import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


db_path = Path(tempfile.gettempdir()) / "spop-purchasing-tests.sqlite3"
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


def _create_order(headers: dict[str, str], product_id: str = "part-oil-filter", quantity: int = 3) -> dict:
    response = client.post(
        "/api/v1/purchase-orders",
        headers=headers,
        json={"supplier_id": "supplier-motovac", "branch_id": "branch-warehouse", "items": [{"product_id": product_id, "quantity": quantity, "unit_cost": 5.0}]},
    )
    assert response.status_code == 201
    return response.json()


def test_full_requested_approved_received_flow() -> None:
    store_keeper = _login("warehouse@sparepilot.local")
    accountant = _login("accounts@sparepilot.local")

    order = _create_order(store_keeper)
    assert order["status"] == "requested"

    approve_response = client.post(f"/api/v1/purchase-orders/{order['id']}/approve", headers=accountant)
    assert approve_response.status_code == 201
    approved = approve_response.json()
    assert approved["status"] == "approved"

    receive_response = client.post(f"/api/v1/purchase-orders/{order['id']}/receive", headers=store_keeper, json={"expected_version": approved["version"]})
    assert receive_response.status_code == 201
    assert receive_response.json()["message"] == "Purchase order received"


def test_requester_cannot_approve_own_order() -> None:
    store_keeper = _login("warehouse@sparepilot.local")
    order = _create_order(store_keeper)

    response = client.post(f"/api/v1/purchase-orders/{order['id']}/approve", headers=store_keeper)
    assert response.status_code == 403


def test_receive_before_approve_returns_422() -> None:
    store_keeper = _login("warehouse@sparepilot.local")
    order = _create_order(store_keeper)

    response = client.post(f"/api/v1/purchase-orders/{order['id']}/receive", headers=store_keeper, json={"expected_version": order["version"]})
    assert response.status_code == 422


def test_cannot_approve_an_already_approved_order_twice() -> None:
    store_keeper = _login("warehouse@sparepilot.local")
    accountant = _login("accounts@sparepilot.local")
    order = _create_order(store_keeper)

    first_approve = client.post(f"/api/v1/purchase-orders/{order['id']}/approve", headers=accountant)
    assert first_approve.status_code == 201

    second_approve = client.post(f"/api/v1/purchase-orders/{order['id']}/approve", headers=accountant)
    assert second_approve.status_code == 422


def test_receipts_from_receive_are_idempotent_per_line() -> None:
    store_keeper = _login("warehouse@sparepilot.local")
    accountant = _login("accounts@sparepilot.local")
    order = _create_order(store_keeper, quantity=4)

    approved = client.post(f"/api/v1/purchase-orders/{order['id']}/approve", headers=accountant).json()
    received = client.post(f"/api/v1/purchase-orders/{order['id']}/receive", headers=store_keeper, json={"expected_version": approved["version"]})
    assert received.status_code == 201

    # Receiving again against an already-received order is a no-op
    # short-circuit, not a second set of stock movements/receipts.
    replay = client.post(f"/api/v1/purchase-orders/{order['id']}/receive", headers=store_keeper, json={"expected_version": approved["version"]})
    assert replay.status_code == 201
    assert replay.json()["message"] == "Purchase order already received"
