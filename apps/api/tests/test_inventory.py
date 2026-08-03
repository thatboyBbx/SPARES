import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


db_path = Path(tempfile.gettempdir()) / "spop-inventory-tests.sqlite3"
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


def test_owner_can_create_and_list_categories() -> None:
    headers = _login("owner@sparepilot.local")

    create_response = client.post("/api/v1/categories", headers=headers, json={"name": "Filters"})
    assert create_response.status_code == 201
    category = create_response.json()
    assert category["name"] == "Filters"

    child_response = client.post("/api/v1/categories", headers=headers, json={"name": "Oil Filters", "parent_id": category["id"]})
    assert child_response.status_code == 201
    assert child_response.json()["parent_id"] == category["id"]

    list_response = client.get("/api/v1/categories", headers=headers)
    assert list_response.status_code == 200
    names = {row["name"] for row in list_response.json()}
    assert {"Filters", "Oil Filters"}.issubset(names)


def test_cashier_cannot_create_category() -> None:
    headers = _login("cashier@sparepilot.local")
    response = client.post("/api/v1/categories", headers=headers, json={"name": "Not allowed"})
    assert response.status_code == 403


def test_creating_category_with_unknown_parent_returns_404() -> None:
    headers = _login("owner@sparepilot.local")
    response = client.post("/api/v1/categories", headers=headers, json={"name": "Orphan", "parent_id": "missing-category"})
    assert response.status_code == 404


def test_product_can_be_assigned_and_unassigned_from_category() -> None:
    headers = _login("owner@sparepilot.local")
    category = client.post("/api/v1/categories", headers=headers, json={"name": "Brakes"}).json()

    assign_response = client.patch(
        "/api/v1/products/part-brake-pad/category",
        headers=headers,
        json={"category_id": category["id"]},
    )
    assert assign_response.status_code == 200
    assert assign_response.json()["category_id"] == category["id"]

    bootstrap = client.get("/api/v1/bootstrap", headers=headers)
    assert bootstrap.status_code == 200
    product = next(p for p in bootstrap.json()["products"] if p["id"] == "part-brake-pad")
    assert product["category_id"] == category["id"]

    unassign_response = client.patch(
        "/api/v1/products/part-brake-pad/category",
        headers=headers,
        json={"category_id": None},
    )
    assert unassign_response.status_code == 200
    assert unassign_response.json()["category_id"] is None


def test_cannot_assign_unknown_category_to_product() -> None:
    headers = _login("owner@sparepilot.local")
    response = client.patch(
        "/api/v1/products/part-oil-filter/category",
        headers=headers,
        json={"category_id": "missing-category"},
    )
    assert response.status_code == 404


def test_stock_movement_composite_index_present() -> None:
    from sqlalchemy import inspect

    from app.db.session import engine

    inspector = inspect(engine)
    index_names = {index["name"] for index in inspector.get_indexes("stock_movements")}
    assert "ix_stock_movements_product_branch" in index_names
