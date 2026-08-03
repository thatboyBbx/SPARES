import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


db_path = Path(tempfile.gettempdir()) / "spop-auth-tests.sqlite3"
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


def test_login_and_me_endpoint_work() -> None:
    response = client.post("/api/v1/auth/login", json={"email": "owner@sparepilot.local", "password": "pilot123"})
    assert response.status_code == 200

    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["user"]["email"] == "owner@sparepilot.local"

    me_response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me_response.status_code == 200
    assert me_response.json()["user"]["role"] == "owner"


def test_refresh_token_rotates_and_old_token_becomes_invalid() -> None:
    login_response = client.post("/api/v1/auth/login", json={"email": "owner@sparepilot.local", "password": "pilot123"})
    old_refresh_token = login_response.json()["refresh_token"]

    refresh_response = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh_token})
    assert refresh_response.status_code == 200
    body = refresh_response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["refresh_token"] != old_refresh_token

    reuse_response = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh_token})
    assert reuse_response.status_code == 401


def test_logout_revokes_refresh_token() -> None:
    login_response = client.post("/api/v1/auth/login", json={"email": "owner@sparepilot.local", "password": "pilot123"})
    refresh_token = login_response.json()["refresh_token"]

    logout_response = client.post("/api/v1/auth/logout", json={"refresh_token": refresh_token})
    assert logout_response.status_code == 204

    refresh_response = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert refresh_response.status_code == 401


def test_role_mismatch_returns_403() -> None:
    login_response = client.post("/api/v1/auth/login", json={"email": "cashier@sparepilot.local", "password": "pilot123"})
    token = login_response.json()["access_token"]

    response = client.post(
        "/api/v1/receipts",
        headers={"Authorization": f"Bearer {token}"},
        json={"product_id": "part-oil-filter", "branch_id": "branch-warehouse", "supplier_id": "supplier-motovac", "quantity": 2},
    )
    assert response.status_code == 403


def test_device_registration_round_trip() -> None:
    login_response = client.post("/api/v1/auth/login", json={"email": "owner@sparepilot.local", "password": "pilot123", "device_id": "device-abc"})
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    register_response = client.post("/api/v1/devices/register", headers=headers, json={"device_id": "device-abc", "platform": "web"})
    assert register_response.status_code == 201
    assert register_response.json()["device_id"] == "device-abc"

    list_response = client.get("/api/v1/devices", headers=headers)
    assert list_response.status_code == 200
    assert any(device["device_id"] == "device-abc" for device in list_response.json())


def test_protected_receipt_endpoint_requires_auth() -> None:
    response = client.post(
        "/api/v1/receipts",
        json={"product_id": "part-oil-filter", "branch_id": "branch-warehouse", "supplier_id": "supplier-motovac", "quantity": 2},
    )
    assert response.status_code == 401


def test_purchase_order_receiving_and_notifications_work() -> None:
    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@sparepilot.local", "password": "pilot123"},
    )
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    create_order_response = client.post(
        "/api/v1/purchase-orders",
        headers=headers,
        json={
            "supplier_id": "supplier-motovac",
            "branch_id": "branch-warehouse",
            "notes": "Pilot replenishment",
            "items": [{"product_id": "part-oil-filter", "quantity": 6, "unit_cost": 5.5}],
        },
    )
    assert create_order_response.status_code == 201
    body = create_order_response.json()
    assert body["message"] == "Purchase order requested"

    purchase_order_id = body["id"]
    receive_response = client.post(f"/api/v1/purchase-orders/{purchase_order_id}/receive", headers=headers)
    assert receive_response.status_code == 201
    assert receive_response.json()["message"] == "Purchase order received"

    approvals_response = client.get("/api/v1/approvals", headers=headers)
    assert approvals_response.status_code == 200

    notifications_response = client.get("/api/v1/notifications", headers=headers)
    assert notifications_response.status_code == 200
    assert notifications_response.json()
