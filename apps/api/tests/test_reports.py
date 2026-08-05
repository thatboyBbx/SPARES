import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


db_path = Path(tempfile.gettempdir()) / "spop-reports-tests.sqlite3"
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


def _top_up_shop_stock(product_id: str = "part-oil-filter", quantity: int = 500) -> None:
    response = client.post(
        "/api/v1/receipts",
        headers=_login("owner@sparepilot.local"),
        json={"product_id": product_id, "branch_id": "branch-shop", "supplier_id": "supplier-motovac", "quantity": quantity},
    )
    assert response.status_code == 201


def test_profit_loss_reflects_sale_expense_and_purchase_cost() -> None:
    _top_up_shop_stock()
    accountant = _login("accounts@sparepilot.local")
    cashier = _login("cashier@sparepilot.local")
    store_keeper = _login("warehouse@sparepilot.local")

    before = client.get("/api/v1/reports/profit-loss", headers=accountant).json()

    sale = client.post("/api/v1/sales", headers=cashier, json={"product_id": "part-oil-filter", "branch_id": "branch-shop", "quantity": 3}).json()

    expense = client.post("/api/v1/expenses", headers=accountant, json={"branch_id": "branch-shop", "category": "utilities", "amount": 10.0, "description": "Water"}).json()
    client.post(f"/api/v1/expenses/{expense['id']}/approve", headers=accountant)

    order = client.post(
        "/api/v1/purchase-orders",
        headers=store_keeper,
        json={"supplier_id": "supplier-motovac", "branch_id": "branch-warehouse", "items": [{"product_id": "part-oil-filter", "quantity": 2, "unit_cost": 3.0}]},
    ).json()
    approved = client.post(f"/api/v1/purchase-orders/{order['id']}/approve", headers=accountant).json()
    client.post(f"/api/v1/purchase-orders/{order['id']}/receive", headers=store_keeper, json={"expected_version": approved["version"]})

    after = client.get("/api/v1/reports/profit-loss", headers=accountant).json()

    assert after["revenue"] - before["revenue"] == sale["total"]
    assert after["expenses"] - before["expenses"] == 10.0
    assert after["purchase_cost"] - before["purchase_cost"] == 6.0
    expected_net_delta = sale["total"] - 10.0 - 6.0
    assert round(after["net_profit"] - before["net_profit"], 6) == round(expected_net_delta, 6)


def test_branch_performance_lists_every_branch() -> None:
    shop_manager = _login("shop@sparepilot.local")
    response = client.get("/api/v1/reports/branch-performance", headers=shop_manager)
    assert response.status_code == 200
    rows = response.json()
    branch_ids = {row["branch_id"] for row in rows}
    assert {"branch-warehouse", "branch-shop"} <= branch_ids
    for row in rows:
        assert {"sales_total", "sales_count", "stock_value", "low_stock_count"} <= row.keys()


def test_expenses_summary_groups_by_category_and_status() -> None:
    accountant = _login("accounts@sparepilot.local")
    created = client.post("/api/v1/expenses", headers=accountant, json={"branch_id": "branch-shop", "category": "maintenance", "amount": 15.0, "description": "Generator service"}).json()

    pending_summary = client.get("/api/v1/reports/expenses-summary", headers=accountant).json()
    assert any(row["category"] == "maintenance" and row["status"] == "pending" for row in pending_summary["by_category"])

    client.post(f"/api/v1/expenses/{created['id']}/approve", headers=accountant)
    approved_summary = client.get("/api/v1/reports/expenses-summary", headers=accountant).json()
    assert any(row["category"] == "maintenance" and row["status"] == "approved" for row in approved_summary["by_category"])
    assert approved_summary["total_approved"] >= 15.0


def test_approvals_summary_counts_pending_and_resolves() -> None:
    accountant = _login("accounts@sparepilot.local")
    store_keeper = _login("warehouse@sparepilot.local")

    before = client.get("/api/v1/reports/approvals-summary", headers=accountant).json()

    order = client.post(
        "/api/v1/purchase-orders",
        headers=store_keeper,
        json={"supplier_id": "supplier-motovac", "branch_id": "branch-warehouse", "items": [{"product_id": "part-oil-filter", "quantity": 1, "unit_cost": 1.0}]},
    ).json()

    mid = client.get("/api/v1/reports/approvals-summary", headers=accountant).json()
    assert mid["pending_count"] == before["pending_count"] + 1
    assert mid["pending_by_type"].get("purchase_order", 0) >= 1

    client.post(f"/api/v1/purchase-orders/{order['id']}/approve", headers=accountant)
    after = client.get("/api/v1/reports/approvals-summary", headers=accountant).json()
    assert after["pending_count"] == before["pending_count"]
    assert after["avg_resolution_hours"] is not None


def test_profit_loss_and_expenses_summary_forbidden_for_shop_manager() -> None:
    shop_manager = _login("shop@sparepilot.local")
    assert client.get("/api/v1/reports/profit-loss", headers=shop_manager).status_code == 403
    assert client.get("/api/v1/reports/expenses-summary", headers=shop_manager).status_code == 403
