import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


db_path = Path(tempfile.gettempdir()) / "spop-notifications-tests.sqlite3"
if db_path.exists():
    db_path.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
os.environ["ENVIRONMENT"] = "test"
os.environ["DEBUG"] = "false"
os.environ["JWT_SECRET_KEY"] = "test-secret"
os.environ["ALLOWED_ORIGINS"] = '["http://localhost:5173"]'
# Deliberately unset: no SMTP/Firebase configuration in this test environment,
# so dispatch should fall back to the safe no-op console/push channels.
os.environ.pop("SMTP_HOST", None)
os.environ.pop("FIREBASE_CREDENTIALS_JSON", None)

from app.core.config import get_settings
from app.main import app

get_settings.cache_clear()
client = TestClient(app)


def _login(email: str, password: str = "pilot123") -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _current_stock(headers: dict[str, str], product_id: str, branch_id: str) -> int:
    response = client.get("/api/v1/reports/overview", headers=headers)
    assert response.status_code == 200
    row = next(r for r in response.json()["stock"] if r["product_id"] == product_id and r["branch_id"] == branch_id)
    return int(row["quantity"])


def _low_stock_notification_count(headers: dict[str, str], product_name: str) -> int:
    response = client.get("/api/v1/notifications", headers=headers)
    assert response.status_code == 200
    return sum(1 for n in response.json() if n["kind"] == "low_stock" and product_name in n["body"])


def test_low_stock_notification_fires_once_on_threshold_crossing() -> None:
    owner = _login("owner@sparepilot.local")
    cashier = _login("cashier@sparepilot.local")

    # part-plug (Spark Plug) has reorder_level 12 in the seed data.
    current = _current_stock(owner, "part-plug", "branch-shop")
    top_up = client.post(
        "/api/v1/receipts",
        headers=owner,
        json={"product_id": "part-plug", "branch_id": "branch-shop", "supplier_id": "supplier-motovac", "quantity": 100},
    )
    assert top_up.status_code == 201
    above_threshold_stock = current + 100
    reorder_level = 12

    before_crossing = _low_stock_notification_count(owner, "Spark Plug")

    # Sell exactly enough in one sale to land one unit below the reorder
    # level, so this single sale is the one that crosses the threshold.
    crossing_quantity = above_threshold_stock - reorder_level + 1
    crossing_sale = client.post(
        "/api/v1/sales",
        headers=cashier,
        json={"product_id": "part-plug", "branch_id": "branch-shop", "quantity": crossing_quantity},
    )
    assert crossing_sale.status_code == 201

    after_crossing = _low_stock_notification_count(owner, "Spark Plug")
    assert after_crossing == before_crossing + 1

    # A further sale that stays below the reorder level must not fire again.
    follow_up_sale = client.post(
        "/api/v1/sales",
        headers=cashier,
        json={"product_id": "part-plug", "branch_id": "branch-shop", "quantity": 1},
    )
    assert follow_up_sale.status_code == 201

    after_follow_up = _low_stock_notification_count(owner, "Spark Plug")
    assert after_follow_up == after_crossing


def test_sync_report_failure_creates_notification() -> None:
    cashier = _login("cashier@sparepilot.local")
    response = client.post(
        "/api/v1/sync/report-failure",
        headers=cashier,
        json={"path": "/sales", "error": "Network unreachable", "attempts": 5},
    )
    assert response.status_code == 201
    assert response.json()["kind"] == "sync_failed"

    listed = client.get("/api/v1/notifications", headers=cashier)
    assert any(n["kind"] == "sync_failed" and "/sales" in n["body"] for n in listed.json())


def test_push_token_registration_requires_prior_device_registration() -> None:
    owner = _login("owner@sparepilot.local")
    response = client.post("/api/v1/notifications/push-token", headers=owner, json={"device_id": "unregistered-device", "push_token": "token-123"})
    assert response.status_code == 404


def test_push_token_registration_updates_registered_device() -> None:
    owner = _login("owner@sparepilot.local")
    register = client.post("/api/v1/devices/register", headers=owner, json={"device_id": "push-test-device", "platform": "web"})
    assert register.status_code == 201

    response = client.post("/api/v1/notifications/push-token", headers=owner, json={"device_id": "push-test-device", "push_token": "token-abc"})
    assert response.status_code == 200
    assert response.json()["push_token"] == "token-abc"


def test_console_email_and_noop_push_channels_do_not_raise() -> None:
    from app.core.notifications import ConsoleEmailChannel, NoopPushChannel

    ConsoleEmailChannel().send("Test", "Body", "test")
    NoopPushChannel().send("Test", "Body", "test")


def test_dispatch_notification_with_unconfigured_channels_does_not_break_the_caller() -> None:
    # No SMTP/Firebase env vars are set in this test environment; creating an
    # expense (which auto-dispatches a notification) must still succeed.
    accountant = _login("accounts@sparepilot.local")
    response = client.post(
        "/api/v1/expenses",
        headers=accountant,
        json={"branch_id": "branch-warehouse", "category": "delivery", "amount": 12.0, "description": "Unconfigured-channel smoke test"},
    )
    assert response.status_code == 201
