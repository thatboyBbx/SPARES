import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


db_path = Path(tempfile.gettempdir()) / "spop-sales-tests.sqlite3"
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


def _top_up_stock(product_id: str = "part-oil-filter", quantity: int = 500) -> None:
    # See test_transfers.py: the app module (and its engine) is a
    # process-wide singleton, so every test file shares one physical
    # database in a single pytest run.
    response = client.post(
        "/api/v1/receipts",
        headers=_login("owner@sparepilot.local"),
        json={"product_id": product_id, "branch_id": "branch-shop", "supplier_id": "supplier-motovac", "quantity": quantity},
    )
    assert response.status_code == 201


def test_create_customer_and_list_with_search() -> None:
    cashier = _login("cashier@sparepilot.local")
    created = client.post("/api/v1/customers", headers=cashier, json={"name": "Tendai Moyo", "phone": "+263 77 000 1111"})
    assert created.status_code == 201
    customer = created.json()

    listed = client.get("/api/v1/customers", headers=cashier, params={"q": "Tendai"})
    assert listed.status_code == 200
    assert any(row["id"] == customer["id"] for row in listed.json())

    listed_miss = client.get("/api/v1/customers", headers=cashier, params={"q": "Nonexistent"})
    assert listed_miss.status_code == 200
    assert not any(row["id"] == customer["id"] for row in listed_miss.json())


def test_sale_with_customer_records_customer_id() -> None:
    _top_up_stock()
    cashier = _login("cashier@sparepilot.local")
    customer = client.post("/api/v1/customers", headers=cashier, json={"name": "Rudo Banda", "phone": ""}).json()

    response = client.post(
        "/api/v1/sales",
        headers=cashier,
        json={"product_id": "part-oil-filter", "branch_id": "branch-shop", "quantity": 1, "customer_id": customer["id"]},
    )
    assert response.status_code == 201

    bootstrap = client.get("/api/v1/bootstrap", headers=cashier)
    sale = next(s for s in bootstrap.json()["sales"] if s["receipt_number"] == response.json()["receipt_number"])
    assert sale["customer_id"] == customer["id"]


def test_sale_without_customer_is_a_walk_in() -> None:
    _top_up_stock()
    cashier = _login("cashier@sparepilot.local")
    response = client.post(
        "/api/v1/sales",
        headers=cashier,
        json={"product_id": "part-oil-filter", "branch_id": "branch-shop", "quantity": 1},
    )
    assert response.status_code == 201

    bootstrap = client.get("/api/v1/bootstrap", headers=cashier)
    sale = next(s for s in bootstrap.json()["sales"] if s["receipt_number"] == response.json()["receipt_number"])
    assert sale["customer_id"] is None


def test_sale_with_unknown_customer_returns_404() -> None:
    _top_up_stock()
    cashier = _login("cashier@sparepilot.local")
    response = client.post(
        "/api/v1/sales",
        headers=cashier,
        json={"product_id": "part-oil-filter", "branch_id": "branch-shop", "quantity": 1, "customer_id": "missing-customer"},
    )
    assert response.status_code == 404


def test_sale_idempotency_replay_returns_same_receipt() -> None:
    _top_up_stock()
    cashier = _login("cashier@sparepilot.local")
    body = {"product_id": "part-oil-filter", "branch_id": "branch-shop", "quantity": 1, "client_request_id": "idem-sale-1"}
    first = client.post("/api/v1/sales", headers=cashier, json=body)
    assert first.status_code == 201
    second = client.post("/api/v1/sales", headers=cashier, json=body)
    assert second.status_code == 201
    assert first.json()["receipt_number"] == second.json()["receipt_number"]


def test_daily_sales_report_aggregates_by_day() -> None:
    _top_up_stock()
    cashier = _login("cashier@sparepilot.local")
    client.post("/api/v1/sales", headers=cashier, json={"product_id": "part-oil-filter", "branch_id": "branch-shop", "quantity": 1})

    response = client.get("/api/v1/reports/daily-sales", headers=cashier)
    assert response.status_code == 200
    rows = response.json()
    assert rows
    assert all({"day", "sales_count", "sales_total"} <= row.keys() for row in rows)
    assert sum(row["sales_count"] for row in rows) >= 1
