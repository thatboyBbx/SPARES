import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

db_path = Path(tempfile.gettempdir()) / "spop-users-admin-tests.sqlite3"
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


def test_owner_can_list_and_create_users() -> None:
    headers = _login("owner@sparepilot.local")

    list_response = client.get("/api/v1/users", headers=headers)
    assert list_response.status_code == 200
    assert len(list_response.json()) >= 6  # seeded pilot roster

    create_response = client.post(
        "/api/v1/users",
        headers=headers,
        json={"email": "new-cashier@sparepilot.local", "full_name": "New Cashier", "password": "s3cret123", "role": "cashier", "branch_id": "branch-shop"},
    )
    assert create_response.status_code == 201
    body = create_response.json()
    assert body["role"] == "cashier"
    assert "hashed_password" not in body

    login_response = client.post("/api/v1/auth/login", json={"email": "new-cashier@sparepilot.local", "password": "s3cret123"})
    assert login_response.status_code == 200


def test_non_admin_cannot_manage_users() -> None:
    headers = _login("cashier@sparepilot.local")

    list_response = client.get("/api/v1/users", headers=headers)
    assert list_response.status_code == 403

    create_response = client.post(
        "/api/v1/users",
        headers=headers,
        json={"email": "x@sparepilot.local", "full_name": "X", "password": "irrelevant", "role": "cashier"},
    )
    assert create_response.status_code == 403


def test_bootstrap_limits_operator_data_to_role_and_branch() -> None:
    cashier = _login("cashier@sparepilot.local")
    payload = client.get("/api/v1/bootstrap", headers=cashier).json()

    assert payload["users"] == []
    assert payload["approvals"] == []
    assert payload["expenses"] == []
    assert payload["journal_entries"] == []
    assert all(movement["branch_id"] == "branch-shop" for movement in payload["movements"])
    assert all(sale["branch_id"] == "branch-shop" for sale in payload["sales"])


def test_owner_can_update_user_role_and_branch() -> None:
    headers = _login("owner@sparepilot.local")

    create_response = client.post(
        "/api/v1/users",
        headers=headers,
        json={"email": "promote-me@sparepilot.local", "full_name": "Promote Me", "password": "s3cret123", "role": "cashier"},
    )
    user_id = create_response.json()["id"]

    patch_response = client.patch(f"/api/v1/users/{user_id}", headers=headers, json={"role": "shop_manager", "branch_id": "branch-shop"})
    assert patch_response.status_code == 200
    body = patch_response.json()
    assert body["role"] == "shop_manager"
    assert body["branch_id"] == "branch-shop"


def test_owner_can_manage_branches() -> None:
    headers = _login("owner@sparepilot.local")

    create_response = client.post("/api/v1/branches", headers=headers, json={"name": "Msasa Shop", "kind": "shop", "address": "Msasa, Harare"})
    assert create_response.status_code == 201

    list_response = client.get("/api/v1/branches", headers=headers)
    assert list_response.status_code == 200
    assert any(branch["name"] == "Msasa Shop" for branch in list_response.json())
