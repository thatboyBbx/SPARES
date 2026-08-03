import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


db_path = Path(tempfile.gettempdir()) / "spop-sync-tests.sqlite3"
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


def _top_up_stock(quantity: int = 500) -> None:
    # See test_transfers.py: the app module (and its engine) is a
    # process-wide singleton, so every test file shares one physical
    # database in a single pytest run.
    response = client.post(
        "/api/v1/receipts",
        headers=_login("owner@sparepilot.local"),
        json={"product_id": "part-oil-filter", "branch_id": "branch-warehouse", "supplier_id": "supplier-motovac", "quantity": quantity},
    )
    assert response.status_code == 201


def _request_transfer(headers: dict[str, str], quantity: int = 1) -> dict:
    response = client.post(
        "/api/v1/transfers",
        headers=headers,
        json={"product_id": "part-oil-filter", "from_branch_id": "branch-warehouse", "to_branch_id": "branch-shop", "quantity": quantity},
    )
    assert response.status_code == 201
    return response.json()


def test_transfer_version_starts_at_one_and_bumps_on_each_update() -> None:
    _top_up_stock()
    store_keeper = _login("warehouse@sparepilot.local")
    shop_manager = _login("shop@sparepilot.local")

    created = _request_transfer(store_keeper)
    assert created["version"] == 1

    fulfilled = client.post(f"/api/v1/transfers/{created['id']}/fulfil", headers=store_keeper, json={"expected_version": 1})
    assert fulfilled.status_code == 201
    assert fulfilled.json()["version"] == 2

    received = client.post(f"/api/v1/transfers/{created['id']}/receive", headers=shop_manager, json={"expected_version": 2})
    assert received.status_code == 201
    assert received.json()["version"] == 3


def test_stale_expected_version_returns_409_and_does_not_apply() -> None:
    _top_up_stock()
    store_keeper = _login("warehouse@sparepilot.local")
    created = _request_transfer(store_keeper)

    stale_response = client.post(f"/api/v1/transfers/{created['id']}/fulfil", headers=store_keeper, json={"expected_version": created["version"] + 1})
    assert stale_response.status_code == 409

    # The stale attempt never applied the fulfil mutation (status is still
    # "requested"), but flagging sync_status=conflict is itself a write that
    # bumps version, so the client must re-fetch before retrying rather than
    # reusing its original expected_version.
    listed = client.get("/api/v1/transfers", headers=store_keeper, params={"status": "requested"})
    current = next(item for item in listed.json() if item["id"] == created["id"])

    retry_response = client.post(f"/api/v1/transfers/{created['id']}/fulfil", headers=store_keeper, json={"expected_version": current["version"]})
    assert retry_response.status_code == 201
    assert retry_response.json()["status"] == "in_transit"


def test_conflict_marks_sync_status_on_server_record() -> None:
    _top_up_stock()
    store_keeper = _login("warehouse@sparepilot.local")
    created = _request_transfer(store_keeper)

    conflict_response = client.post(f"/api/v1/transfers/{created['id']}/fulfil", headers=store_keeper, json={"expected_version": 999})
    assert conflict_response.status_code == 409

    listed = client.get("/api/v1/transfers", headers=store_keeper, params={"status": "requested"})
    record = next(item for item in listed.json() if item["id"] == created["id"])
    assert record["sync_status"] == "conflict"


def test_purchase_order_receive_is_conflict_checked() -> None:
    owner = _login("owner@sparepilot.local")
    create_response = client.post(
        "/api/v1/purchase-orders",
        headers=owner,
        json={"supplier_id": "supplier-motovac", "branch_id": "branch-warehouse", "items": [{"product_id": "part-oil-filter", "quantity": 2, "unit_cost": 5.0}]},
    )
    assert create_response.status_code == 201
    order = create_response.json()
    assert order["version"] == 1

    stale = client.post(f"/api/v1/purchase-orders/{order['id']}/receive", headers=owner, json={"expected_version": 2})
    assert stale.status_code == 409

    # A receive attempt against a not-yet-approved order is also correctly
    # rejected (422, not the version-conflict 409) once expected_version is
    # right - approve first, matching Phase 5's requested->approved->received
    # workflow.
    approve_response = client.post(f"/api/v1/purchase-orders/{order['id']}/approve", headers=owner)
    assert approve_response.status_code == 201

    correct = client.post(f"/api/v1/purchase-orders/{order['id']}/receive", headers=owner, json={"expected_version": approve_response.json()["version"]})
    assert correct.status_code == 201


def test_purchase_order_create_idempotency_replay_returns_same_order() -> None:
    owner = _login("owner@sparepilot.local")
    body = {
        "supplier_id": "supplier-motovac",
        "branch_id": "branch-warehouse",
        "items": [{"product_id": "part-brake-pad", "quantity": 1, "unit_cost": 20.0}],
        "client_request_id": "idem-po-1",
    }
    first = client.post("/api/v1/purchase-orders", headers=owner, json=body)
    assert first.status_code == 201
    second = client.post("/api/v1/purchase-orders", headers=owner, json=body)
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_approval_create_idempotency_replay_returns_same_approval() -> None:
    owner = _login("owner@sparepilot.local")
    body = {"type": "stock adjustment", "subject": "Recount", "requester": "Store Keeper", "approver": "Owner", "client_request_id": "idem-approval-1"}
    first = client.post("/api/v1/approvals", headers=owner, json=body)
    assert first.status_code == 201
    second = client.post("/api/v1/approvals", headers=owner, json=body)
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_expense_create_idempotency_replay_returns_same_expense() -> None:
    owner = _login("owner@sparepilot.local")
    body = {"branch_id": "branch-warehouse", "category": "delivery", "amount": 15.5, "description": "Fuel", "client_request_id": "idem-expense-1"}
    first = client.post("/api/v1/expenses", headers=owner, json=body)
    assert first.status_code == 201
    second = client.post("/api/v1/expenses", headers=owner, json=body)
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
