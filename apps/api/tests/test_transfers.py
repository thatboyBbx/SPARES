import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


db_path = Path(tempfile.gettempdir()) / "spop-transfers-tests.sqlite3"
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




def _available_stock(headers: dict[str, str], product_id: str = "part-oil-filter", branch_id: str = "branch-warehouse") -> int:
    response = client.get("/api/v1/reports/overview", headers=headers)
    assert response.status_code == 200
    row = next(r for r in response.json()["stock"] if r["product_id"] == product_id and r["branch_id"] == branch_id)
    return int(row["quantity"])


def _top_up_stock(quantity: int = 500) -> None:
    """The app module (and its SQLAlchemy engine) is a process-wide
    singleton, so every test file in a single pytest run shares one physical
    database regardless of each file's own DATABASE_URL setup above. Top up
    stock before any stock-sensitive test rather than relying on the exact
    seeded quantity, so these tests aren't order-dependent on how much other
    test files/tests have already consumed."""
    response = client.post(
        "/api/v1/receipts",
        headers=_login("owner@sparepilot.local"),
        json={"product_id": "part-oil-filter", "branch_id": "branch-warehouse", "supplier_id": "supplier-motovac", "quantity": quantity},
    )
    assert response.status_code == 201


def _request_transfer(headers: dict[str, str], quantity: int = 2) -> dict:
    response = client.post(
        "/api/v1/transfers",
        headers=headers,
        json={
            "product_id": "part-oil-filter",
            "from_branch_id": "branch-warehouse",
            "to_branch_id": "branch-shop",
            "quantity": quantity,
        },
    )
    assert response.status_code == 201
    return response.json()


def _fulfil(headers: dict[str, str], transfer: dict):
    return client.post(f"/api/v1/transfers/{transfer['id']}/fulfil", headers=headers, json={"expected_version": transfer["version"]})


def _receive(headers: dict[str, str], transfer: dict):
    return client.post(f"/api/v1/transfers/{transfer['id']}/receive", headers=headers, json={"expected_version": transfer["version"]})


def test_full_request_fulfil_receive_flow() -> None:
    _top_up_stock()
    store_keeper = _login("warehouse@sparepilot.local")
    shop_manager = _login("shop@sparepilot.local")

    created = _request_transfer(store_keeper)
    assert created["status"] == "requested"
    transfer_id = created["id"]

    fulfil_response = _fulfil(store_keeper, created)
    assert fulfil_response.status_code == 201
    fulfilled = fulfil_response.json()
    assert fulfilled["status"] == "in_transit"

    receive_response = _receive(shop_manager, {"id": transfer_id, "version": fulfilled["version"]})
    assert receive_response.status_code == 201
    assert receive_response.json()["status"] == "received"

    listed = client.get("/api/v1/transfers", headers=shop_manager, params={"status": "received"})
    assert listed.status_code == 200
    assert any(item["id"] == transfer_id for item in listed.json())


def test_fulfil_rejects_stock_depleted_by_another_transfer_since_request() -> None:
    # Two requests for the full available quantity are both valid at request
    # time (neither has moved stock yet), but only the first can be fulfilled
    # once the ledger reflects that move.
    _top_up_stock()
    store_keeper = _login("warehouse@sparepilot.local")
    available = _available_stock(store_keeper)
    first = _request_transfer(store_keeper, quantity=available)
    second = _request_transfer(store_keeper, quantity=available)

    first_fulfil = _fulfil(store_keeper, first)
    assert first_fulfil.status_code == 201

    second_fulfil = _fulfil(store_keeper, second)
    assert second_fulfil.status_code == 422

    # This test intentionally drains the branch to zero; restore headroom for
    # any tests that run after it in this file.
    _top_up_stock()


def test_receive_before_fulfil_is_rejected() -> None:
    _top_up_stock()
    store_keeper = _login("warehouse@sparepilot.local")
    shop_manager = _login("shop@sparepilot.local")
    created = _request_transfer(store_keeper)
    transfer_id = created["id"]

    response = _receive(shop_manager, created)
    assert response.status_code == 422


def test_cashier_cannot_request_transfer() -> None:
    cashier = _login("cashier@sparepilot.local")
    response = client.post(
        "/api/v1/transfers",
        headers=cashier,
        json={"product_id": "part-oil-filter", "from_branch_id": "branch-warehouse", "to_branch_id": "branch-shop", "quantity": 1},
    )
    assert response.status_code == 403


def test_shop_manager_cannot_fulfil_transfer() -> None:
    _top_up_stock()
    store_keeper = _login("warehouse@sparepilot.local")
    shop_manager = _login("shop@sparepilot.local")
    created = _request_transfer(store_keeper)

    response = _fulfil(shop_manager, created)
    assert response.status_code == 403


def test_cancel_only_allowed_before_fulfilment() -> None:
    _top_up_stock()
    store_keeper = _login("warehouse@sparepilot.local")
    created = _request_transfer(store_keeper)
    transfer_id = created["id"]

    cancel_response = client.post(f"/api/v1/transfers/{transfer_id}/cancel", headers=store_keeper)
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"

    second_request = _request_transfer(store_keeper)
    fulfil_response = _fulfil(store_keeper, second_request)
    assert fulfil_response.status_code == 201

    late_cancel = client.post(f"/api/v1/transfers/{second_request['id']}/cancel", headers=store_keeper)
    assert late_cancel.status_code == 422
