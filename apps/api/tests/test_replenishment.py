import math
import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


db_path = Path(tempfile.gettempdir()) / "spop-replenishment-tests.sqlite3"
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


def _receive(product_id: str, branch_id: str, quantity: int) -> None:
    response = client.post(
        "/api/v1/receipts",
        headers=_login("owner@sparepilot.local"),
        json={"product_id": product_id, "branch_id": branch_id, "supplier_id": "supplier-motovac", "quantity": quantity},
    )
    assert response.status_code == 201


def _sell(product_id: str, branch_id: str, quantity: int) -> None:
    response = client.post(
        "/api/v1/sales",
        headers=_login("cashier@sparepilot.local"),
        json={"product_id": product_id, "branch_id": branch_id, "quantity": quantity},
    )
    assert response.status_code == 201


def _find(rows: list[dict], product_id: str, branch_id: str) -> dict:
    return next(row for row in rows if row["product_id"] == product_id and row["branch_id"] == branch_id)


# NOTE: per test_sales.py / test_transfers.py, the app module (and its DB
# engine) is a process-wide singleton, so every test file in a single
# pytest run shares one physical database — these tests cannot assume a
# pristine starting stock/sales history for a given product/branch pair.
# They instead check the heuristic's documented formula holds (an
# environment-independent invariant) and check deltas from actions taken
# within the test itself, never hardcoded absolute stock levels.


def test_suggestions_are_internally_consistent_with_documented_heuristic() -> None:
    store_keeper = _login("warehouse@sparepilot.local")
    rows = client.get("/api/v1/reports/replenishment-suggestions", headers=store_keeper).json()
    assert rows

    for row in rows:
        assert row["needs_reorder"] == (row["current_stock"] <= row["reorder_level"])

        # Recompute from sales_last_30_days (exact) rather than the
        # API's already-rounded velocity_per_day, to avoid compounding
        # rounding error against the server's own unrounded intermediate.
        velocity = row["sales_last_30_days"] / 30
        assert row["velocity_per_day"] == round(velocity, 3)

        if velocity > 0:
            assert row["days_of_cover"] == round(row["current_stock"] / velocity, 1)
        else:
            assert row["days_of_cover"] is None

        expected_urgent = row["needs_reorder"] or (row["days_of_cover"] is not None and row["days_of_cover"] < 7)
        assert row["urgent"] == expected_urgent

        if velocity > 0:
            expected_suggested = max(0, math.ceil(velocity * 30 - row["current_stock"]))
        elif row["needs_reorder"]:
            expected_suggested = max(0, row["reorder_level"] * 2 - row["current_stock"])
        else:
            expected_suggested = 0
        assert row["suggested_reorder_quantity"] == expected_suggested


def test_receiving_and_selling_moves_stock_and_velocity_by_the_expected_delta() -> None:
    store_keeper = _login("warehouse@sparepilot.local")
    before = _find(client.get("/api/v1/reports/replenishment-suggestions", headers=store_keeper).json(), "part-oil-filter", "branch-shop")

    _receive("part-oil-filter", "branch-shop", 100)
    _sell("part-oil-filter", "branch-shop", 30)

    after = _find(client.get("/api/v1/reports/replenishment-suggestions", headers=store_keeper).json(), "part-oil-filter", "branch-shop")

    assert after["current_stock"] == before["current_stock"] + 100 - 30
    assert after["sales_last_30_days"] == before["sales_last_30_days"] + 30
    assert round(after["velocity_per_day"] - before["velocity_per_day"], 3) == 1.0


def test_depleting_stock_below_reorder_level_flags_urgent() -> None:
    store_keeper = _login("warehouse@sparepilot.local")
    before = _find(client.get("/api/v1/reports/replenishment-suggestions", headers=store_keeper).json(), "part-plug", "branch-warehouse")

    # Sell down to exactly the reorder level from the warehouse (which
    # started this test file with 48 seeded units, comfortably above the
    # reorder level of 12) so the "at/below threshold" branch is exercised
    # regardless of what other test files already did to this row.
    deficit = max(0, before["current_stock"] - before["reorder_level"] + 1)
    if deficit:
        _sell("part-plug", "branch-warehouse", deficit)

    after = _find(client.get("/api/v1/reports/replenishment-suggestions", headers=store_keeper).json(), "part-plug", "branch-warehouse")
    assert after["current_stock"] <= after["reorder_level"]
    assert after["needs_reorder"] is True
    assert after["urgent"] is True
    assert after["suggested_reorder_quantity"] > 0


def test_cashier_cannot_view_replenishment_suggestions() -> None:
    cashier = _login("cashier@sparepilot.local")
    response = client.get("/api/v1/reports/replenishment-suggestions", headers=cashier)
    assert response.status_code == 403
