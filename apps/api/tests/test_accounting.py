import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


db_path = Path(tempfile.gettempdir()) / f"spop-accounting-tests-{os.getpid()}.sqlite3"
if db_path.exists():
    db_path.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
os.environ["ENVIRONMENT"] = "test"
os.environ["DEBUG"] = "false"
os.environ["JWT_SECRET_KEY"] = "test-secret"
os.environ["ALLOWED_ORIGINS"] = '["http://localhost:5173"]'

from app.core.config import get_settings
from app.main import app

get_settings.cache_clear()
client = TestClient(app)


def _login(email: str, password: str = "pilot123") -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _journal(headers: dict[str, str], **params) -> list[dict]:
    response = client.get("/api/v1/journal", headers=headers, params=params)
    assert response.status_code == 200
    return response.json()


def _top_up_shop_stock(product_id: str = "part-oil-filter", quantity: int = 500) -> None:
    # See test_transfers.py / test_sales.py: the app module (and its engine)
    # is a process-wide singleton, so every test file shares one physical
    # database in a single pytest run — the seeded receipts only stock the
    # warehouse, so shop-branch sales need an explicit top-up first.
    response = client.post(
        "/api/v1/receipts",
        headers=_login("owner@sparepilot.local"),
        json={"product_id": product_id, "branch_id": "branch-shop", "supplier_id": "supplier-motovac", "quantity": quantity},
    )
    assert response.status_code == 201


def test_sale_posts_revenue_journal_entry() -> None:
    _top_up_shop_stock()
    accountant = _login("accounts@sparepilot.local")
    cashier = _login("cashier@sparepilot.local")

    sale = client.post("/api/v1/sales", headers=cashier, json={"product_id": "part-oil-filter", "branch_id": "branch-shop", "quantity": 2})
    assert sale.status_code == 201

    entries = _journal(accountant, entry_type="sale_revenue")
    matching = [e for e in entries if e["reference"] == sale.json()["receipt_number"]]
    assert len(matching) == 1
    assert matching[0]["amount"] == sale.json()["total"]


def test_expense_posts_journal_entry_only_on_approval() -> None:
    accountant = _login("accounts@sparepilot.local")

    created = client.post("/api/v1/expenses", headers=accountant, json={"branch_id": "branch-shop", "category": "utilities", "amount": 25.0, "description": "Electricity"})
    assert created.status_code == 201
    expense_id = created.json()["id"]

    before = _journal(accountant, entry_type="expense")
    assert not any(e["related_entity_id"] == expense_id for e in before)

    approved = client.post(f"/api/v1/expenses/{expense_id}/approve", headers=accountant)
    assert approved.status_code == 200

    after = _journal(accountant, entry_type="expense")
    matching = [e for e in after if e["related_entity_id"] == expense_id]
    assert len(matching) == 1
    assert matching[0]["amount"] == -25.0


def test_purchase_order_receive_posts_negative_cost_entry() -> None:
    store_keeper = _login("warehouse@sparepilot.local")
    accountant = _login("accounts@sparepilot.local")

    order = client.post(
        "/api/v1/purchase-orders",
        headers=store_keeper,
        json={"supplier_id": "supplier-motovac", "branch_id": "branch-warehouse", "items": [{"product_id": "part-oil-filter", "quantity": 5, "unit_cost": 4.0}]},
    ).json()

    approved = client.post(f"/api/v1/purchase-orders/{order['id']}/approve", headers=accountant).json()
    received = client.post(f"/api/v1/purchase-orders/{order['id']}/receive", headers=store_keeper, json={"expected_version": approved["version"]})
    assert received.status_code == 201

    entries = _journal(accountant, entry_type="purchase_cost")
    matching = [e for e in entries if e["reference"] == order["reference"]]
    assert len(matching) == 1
    assert matching[0]["amount"] == -20.0


def test_supplier_payment_creates_journal_entry_and_is_idempotent() -> None:
    accountant = _login("accounts@sparepilot.local")

    payload = {"supplier_id": "supplier-motovac", "amount": 50.0, "method": "bank", "client_request_id": "pay-req-1"}
    first = client.post("/api/v1/supplier-payments", headers=accountant, json=payload)
    assert first.status_code == 201
    reference = first.json()["reference"]

    replay = client.post("/api/v1/supplier-payments", headers=accountant, json=payload)
    assert replay.status_code == 201
    assert replay.json()["reference"] == reference

    payments = client.get("/api/v1/supplier-payments", headers=accountant)
    assert payments.status_code == 200
    assert sum(1 for p in payments.json() if p["reference"] == reference) == 1

    entries = _journal(accountant, entry_type="supplier_payment")
    matching = [e for e in entries if e["reference"] == reference]
    assert len(matching) == 1
    assert matching[0]["amount"] == -50.0


def test_supplier_payment_rejects_purchase_order_from_other_supplier() -> None:
    store_keeper = _login("warehouse@sparepilot.local")
    accountant = _login("accounts@sparepilot.local")

    order = client.post(
        "/api/v1/purchase-orders",
        headers=store_keeper,
        json={"supplier_id": "supplier-autopro", "branch_id": "branch-warehouse", "items": [{"product_id": "part-brake-pad", "quantity": 1, "unit_cost": 10.0}]},
    ).json()

    response = client.post(
        "/api/v1/supplier-payments",
        headers=accountant,
        json={"supplier_id": "supplier-motovac", "purchase_order_id": order["id"], "amount": 10.0},
    )
    assert response.status_code == 422


def test_cashier_cannot_manage_accounting() -> None:
    cashier = _login("cashier@sparepilot.local")

    response = client.post("/api/v1/supplier-payments", headers=cashier, json={"supplier_id": "supplier-motovac", "amount": 5.0})
    assert response.status_code == 403

    response = client.get("/api/v1/journal", headers=cashier)
    assert response.status_code == 403
