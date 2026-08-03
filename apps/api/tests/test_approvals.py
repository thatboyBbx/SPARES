import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient


db_path = Path(tempfile.gettempdir()) / "spop-approvals-tests.sqlite3"
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


def test_expense_auto_creates_linked_approval() -> None:
    accountant = _login("accounts@sparepilot.local")
    created = client.post(
        "/api/v1/expenses",
        headers=accountant,
        json={"branch_id": "branch-warehouse", "category": "delivery", "amount": 25.0, "description": "Fuel run"},
    )
    assert created.status_code == 201
    expense = created.json()

    approvals = client.get("/api/v1/approvals", headers=accountant).json()
    linked = next(a for a in approvals if a["related_entity_type"] == "expense" and a["related_entity_id"] == expense["id"])
    assert linked["status"] == "pending"
    assert linked["deadline"] is not None


def test_approving_expense_flips_both_statuses() -> None:
    accountant = _login("accounts@sparepilot.local")
    expense = client.post(
        "/api/v1/expenses",
        headers=accountant,
        json={"branch_id": "branch-warehouse", "category": "utilities", "amount": 40.0, "description": "Power bill"},
    ).json()

    approve_response = client.post(f"/api/v1/expenses/{expense['id']}/approve", headers=accountant)
    assert approve_response.status_code == 200
    assert approve_response.json()["status"] == "approved"

    approvals = client.get("/api/v1/approvals", headers=accountant).json()
    linked = next(a for a in approvals if a["related_entity_type"] == "expense" and a["related_entity_id"] == expense["id"])
    assert linked["status"] == "approved"
    assert linked["approver_id"] is not None


def test_rejecting_expense_flips_both_statuses() -> None:
    accountant = _login("accounts@sparepilot.local")
    expense = client.post(
        "/api/v1/expenses",
        headers=accountant,
        json={"branch_id": "branch-warehouse", "category": "maintenance", "amount": 15.0, "description": "Repairs"},
    ).json()

    reject_response = client.post(f"/api/v1/expenses/{expense['id']}/reject", headers=accountant)
    assert reject_response.status_code == 200
    assert reject_response.json()["status"] == "rejected"

    approvals = client.get("/api/v1/approvals", headers=accountant).json()
    linked = next(a for a in approvals if a["related_entity_type"] == "expense" and a["related_entity_id"] == expense["id"])
    assert linked["status"] == "rejected"


def test_approving_via_generic_approval_endpoint_also_flips_expense() -> None:
    accountant = _login("accounts@sparepilot.local")
    expense = client.post(
        "/api/v1/expenses",
        headers=accountant,
        json={"branch_id": "branch-warehouse", "category": "delivery", "amount": 5.0, "description": "Courier"},
    ).json()
    approvals = client.get("/api/v1/approvals", headers=accountant).json()
    linked = next(a for a in approvals if a["related_entity_type"] == "expense" and a["related_entity_id"] == expense["id"])

    approve_response = client.post(f"/api/v1/approvals/{linked['id']}/approve", headers=accountant)
    assert approve_response.status_code == 200

    expenses = client.get("/api/v1/bootstrap", headers=accountant).json()["expenses"]
    matching_expense = next(e for e in expenses if e["id"] == expense["id"])
    assert matching_expense["status"] == "approved"


def test_purchase_order_request_auto_creates_and_resolves_linked_approval() -> None:
    store_keeper = _login("warehouse@sparepilot.local")
    accountant = _login("accounts@sparepilot.local")
    order = client.post(
        "/api/v1/purchase-orders",
        headers=store_keeper,
        json={"supplier_id": "supplier-motovac", "branch_id": "branch-warehouse", "items": [{"product_id": "part-oil-filter", "quantity": 1, "unit_cost": 5.0}]},
    ).json()

    approvals = client.get("/api/v1/approvals", headers=accountant).json()
    linked = next(a for a in approvals if a["related_entity_type"] == "purchase_order" and a["related_entity_id"] == order["id"])
    assert linked["status"] == "pending"

    approve_response = client.post(f"/api/v1/purchase-orders/{order['id']}/approve", headers=accountant)
    assert approve_response.status_code == 201

    approvals_after = client.get("/api/v1/approvals", headers=accountant).json()
    linked_after = next(a for a in approvals_after if a["id"] == linked["id"])
    assert linked_after["status"] == "approved"


def test_price_change_by_owner_applies_immediately() -> None:
    owner = _login("owner@sparepilot.local")
    response = client.patch("/api/v1/products/part-plug/price", headers=owner, json={"selling_price": 9.5})
    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "Price updated"
    assert body["product"]["selling_price"] == 9.5


def test_price_change_by_cashier_creates_pending_approval_instead() -> None:
    cashier = _login("cashier@sparepilot.local")
    original = client.get("/api/v1/bootstrap", headers=cashier).json()
    original_price = next(p for p in original["products"] if p["id"] == "part-brake-pad")["selling_price"]

    response = client.patch("/api/v1/products/part-brake-pad/price", headers=cashier, json={"selling_price": 999.0})
    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "Price change submitted for approval"
    assert body["approval"]["type"] == "price_change"
    assert body["approval"]["status"] == "pending"

    after = client.get("/api/v1/bootstrap", headers=cashier).json()
    unchanged_price = next(p for p in after["products"] if p["id"] == "part-brake-pad")["selling_price"]
    assert unchanged_price == original_price


def test_overdue_approval_escalates_priority_to_high() -> None:
    accountant = _login("accounts@sparepilot.local")
    created = client.post(
        "/api/v1/approvals",
        headers=accountant,
        json={
            "type": "stock adjustment",
            "subject": "Recount shelf 3",
            "requester": "Store Keeper",
            "approver": "Accountant",
            "priority": "normal",
            "deadline": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        },
    )
    assert created.status_code == 201
    assert created.json()["priority"] == "normal"
    approval_id = created.json()["id"]

    listed = client.get("/api/v1/approvals", headers=accountant).json()
    escalated = next(a for a in listed if a["id"] == approval_id)
    assert escalated["priority"] == "high"
    assert escalated["is_overdue"] is True
