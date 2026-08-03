"""Load pilot records from a data file into an empty database."""
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import hash_password
from app.models import Branch, Product, PurchaseReceipt, StockMovement, Supplier, User, UserRole


def seed_database(db: Session) -> None:
    if db.scalar(select(Product.id).limit(1)):
        return
    data_path = Path(__file__).with_name("data") / "sample_data.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))
    for row in data["users"]:
        user = row.copy()
        password = user.pop("password")
        role = UserRole(user.pop("role"))
        db.add(User(**user, role=role, hashed_password=hash_password(password)))
    owner_id = next(row["id"] for row in data["users"] if row["role"] == "owner")
    db.add_all([Branch(**row) for row in data["branches"]])
    db.add_all([Supplier(**row) for row in data["suppliers"]])
    db.add_all([Product(**row, device_id="system-import", created_by_id=owner_id) for row in data["products"]])
    db.flush()
    for row in data["receipts"]:
        receipt = PurchaseReceipt(**row)
        db.add(receipt)
        db.add(StockMovement(product_id=row["product_id"], branch_id=row["branch_id"], quantity=row["quantity"], kind="receipt", reference=row["reference"], created_by="System import"))
    db.commit()
