"""Database-backed API for the SparePilot operating MVP."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import create_access_token, get_current_user, hash_password, require_roles, verify_password
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import engine, get_db
from app.models import Approval, Branch, Expense, Notification, Product, PurchaseOrder, PurchaseOrderLine, PurchaseReceipt, Sale, StockMovement, Supplier, Transfer, User
from app.seed import seed_database

settings = get_settings()


def initialize_database() -> None:
    Base.metadata.create_all(bind=engine)
    with Session(engine) as db:
        seed_database(db)


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


initialize_database()
app = FastAPI(title=settings.app_name, description="Spare parts operations MVP", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


class ReceiptIn(BaseModel):
    product_id: str
    branch_id: str
    supplier_id: str
    quantity: int = Field(gt=0)
    client_request_id: str | None = None

class TransferIn(BaseModel):
    product_id: str
    from_branch_id: str
    to_branch_id: str
    quantity: int = Field(gt=0)
    client_request_id: str | None = None

class SaleIn(BaseModel):
    product_id: str
    branch_id: str
    quantity: int = Field(gt=0)
    client_request_id: str | None = None

class ApprovalIn(BaseModel):
    type: str
    subject: str
    requester: str
    approver: str
    priority: str = "normal"


class PurchaseOrderLineIn(BaseModel):
    product_id: str
    quantity: int = Field(gt=0)
    unit_cost: float = Field(default=0.0)


class PurchaseOrderIn(BaseModel):
    supplier_id: str
    branch_id: str
    notes: str = ""
    items: list[PurchaseOrderLineIn] = Field(default_factory=list)


class ExpenseIn(BaseModel):
    branch_id: str
    category: str
    amount: float = Field(gt=0)
    description: str

class LoginIn(BaseModel):
    email: str
    password: str

def reference(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc):%y%m%d%H%M%S}-{str(uuid4())[:4].upper()}"

def branch_stock(db: Session, product_id: str, branch_id: str) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(StockMovement.quantity), 0)).where(StockMovement.product_id == product_id, StockMovement.branch_id == branch_id)) or 0)

def require(db: Session, model: type, identity: str):
    record = db.get(model, identity)
    if not record:
        raise HTTPException(404, f"{model.__name__} not found")
    return record

@app.get("/health", tags=["system"])
def health_check() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}

@app.get("/api/v1/bootstrap")
def bootstrap(
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(get_current_user)] = None,
) -> dict:
    products = db.scalars(select(Product).order_by(Product.name)).all()
    branches = db.scalars(select(Branch).order_by(Branch.kind)).all()
    movements = db.scalars(select(StockMovement).order_by(StockMovement.created_at.desc())).all()
    return {"users": [serialize(u) for u in db.scalars(select(User)).all()], "products": [serialize(p) for p in products], "branches": [serialize(b) for b in branches], "suppliers": [serialize(s) for s in db.scalars(select(Supplier)).all()], "movements": [serialize(m) for m in movements], "transfers": [serialize(t) for t in db.scalars(select(Transfer).order_by(Transfer.requested_at.desc())).all()], "sales": [serialize(s) for s in db.scalars(select(Sale).order_by(Sale.created_at.desc())).all()], "purchase_orders": [serialize(o) for o in db.scalars(select(PurchaseOrder).order_by(PurchaseOrder.requested_at.desc())).all()], "purchase_order_lines": [serialize(l) for l in db.scalars(select(PurchaseOrderLine).order_by(PurchaseOrderLine.created_at.desc())).all()], "approvals": [serialize(a) for a in db.scalars(select(Approval).order_by(Approval.created_at.desc())).all()], "expenses": [serialize(e) for e in db.scalars(select(Expense).order_by(Expense.created_at.desc())).all()], "notifications": [serialize(n) for n in db.scalars(select(Notification).order_by(Notification.created_at.desc())).all()]}

@app.post("/api/v1/auth/login")
def login(payload: LoginIn, db: Session = Depends(get_db)) -> dict:
    user = db.scalar(select(User).where(User.email == payload.email))
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    return {"user": serialize(user), "access_token": create_access_token(str(user.id), user.role), "token_type": "bearer"}

@app.get("/api/v1/auth/me")
def current_user(user: Annotated[User, Depends(get_current_user)]) -> dict:
    return {"user": serialize(user)}

@app.post("/api/v1/receipts", status_code=201)
def create_receipt(
    payload: ReceiptIn,
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(require_roles("owner", "store_keeper"))] = None,
) -> dict:
    if payload.client_request_id:
        existing = db.scalar(select(PurchaseReceipt).where(PurchaseReceipt.idempotency_key == payload.client_request_id))
        if existing: return {"reference": existing.reference, "message": "Goods receipt already posted"}
    require(db, Product, payload.product_id); require(db, Branch, payload.branch_id); require(db, Supplier, payload.supplier_id)
    ref = reference("GRN")
    receipt = PurchaseReceipt(reference=ref, product_id=payload.product_id, branch_id=payload.branch_id, supplier_id=payload.supplier_id, quantity=payload.quantity, idempotency_key=payload.client_request_id)
    db.add(receipt); db.add(StockMovement(product_id=payload.product_id, branch_id=payload.branch_id, quantity=payload.quantity, kind="receipt", reference=ref, created_by="Store keeper")); db.commit()
    return {"reference": ref, "message": "Goods receipt posted"}

@app.post("/api/v1/purchase-orders", status_code=201)
def create_purchase_order(
    payload: PurchaseOrderIn,
    db: Session = Depends(get_db),
    current_user: Annotated[User, Depends(require_roles("owner", "store_keeper"))] = None,
) -> dict:
    if not payload.items:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "At least one item is required")
    require(db, Supplier, payload.supplier_id)
    require(db, Branch, payload.branch_id)
    for item in payload.items:
        require(db, Product, item.product_id)

    order_reference = reference("PO")
    order = PurchaseOrder(reference=order_reference, supplier_id=payload.supplier_id, branch_id=payload.branch_id, notes=payload.notes, created_by=current_user.full_name)
    db.add(order)
    db.flush()
    db.add_all([
        PurchaseOrderLine(purchase_order_id=order.id, product_id=item.product_id, quantity=item.quantity, unit_cost=item.unit_cost)
        for item in payload.items
    ])
    db.add(Notification(title="Purchase order requested", body=f"{order_reference} was created for {len(payload.items)} items.", kind="purchase_order"))
    db.commit()
    return {"id": order.id, "reference": order_reference, "status": order.status, "message": "Purchase order requested"}


@app.get("/api/v1/purchase-orders")
def list_purchase_orders(
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(get_current_user)] = None,
) -> list[dict]:
    orders = db.scalars(select(PurchaseOrder).order_by(PurchaseOrder.requested_at.desc())).all()
    return [serialize(order) for order in orders]


@app.post("/api/v1/purchase-orders/{purchase_order_id}/receive", status_code=201)
def receive_purchase_order(
    purchase_order_id: str,
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(require_roles("owner", "store_keeper"))] = None,
) -> dict:
    order = require(db, PurchaseOrder, purchase_order_id)
    if order.status == "received":
        return {"reference": order.reference, "message": "Purchase order already received"}

    lines = db.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.purchase_order_id == purchase_order_id)).all()
    if not lines:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Purchase order has no lines")

    for line in lines:
        require(db, Product, line.product_id)
        require(db, Branch, order.branch_id)
        receipt_reference = reference("GRN")
        db.add(PurchaseReceipt(reference=receipt_reference, supplier_id=order.supplier_id, branch_id=order.branch_id, product_id=line.product_id, quantity=line.quantity, idempotency_key=None))
        db.add(StockMovement(product_id=line.product_id, branch_id=order.branch_id, quantity=line.quantity, kind="receipt", reference=receipt_reference, created_by="Store keeper"))

    order.status = "received"
    order.received_at = datetime.now(timezone.utc)
    db.add(Notification(title="Goods received", body=f"{order.reference} completed and posted to stock.", kind="purchase_order"))
    db.commit()
    return {"reference": order.reference, "received_lines": len(lines), "message": "Purchase order received"}


@app.post("/api/v1/transfers", status_code=201)
def create_transfer(
    payload: TransferIn,
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(require_roles("owner", "store_keeper", "shop_manager"))] = None,
) -> dict:
    if payload.client_request_id:
        existing = db.scalar(select(Transfer).where(Transfer.idempotency_key == payload.client_request_id))
        if existing: return {"reference": existing.reference, "message": "Transfer already posted"}
    require(db, Product, payload.product_id); require(db, Branch, payload.from_branch_id); require(db, Branch, payload.to_branch_id)
    if payload.from_branch_id == payload.to_branch_id: raise HTTPException(422, "Origin and destination must differ")
    if branch_stock(db, payload.product_id, payload.from_branch_id) < payload.quantity: raise HTTPException(422, "Insufficient origin stock")
    ref = reference("TRF")
    transfer = Transfer(reference=ref, product_id=payload.product_id, from_branch_id=payload.from_branch_id, to_branch_id=payload.to_branch_id, quantity=payload.quantity, idempotency_key=payload.client_request_id, status="received", received_at=datetime.now(timezone.utc))
    db.add(transfer)
    db.add_all([StockMovement(product_id=payload.product_id, branch_id=payload.from_branch_id, quantity=-payload.quantity, kind="transfer_out", reference=ref, created_by="Warehouse keeper"), StockMovement(product_id=payload.product_id, branch_id=payload.to_branch_id, quantity=payload.quantity, kind="transfer_in", reference=ref, created_by="Shop manager")])
    db.add(Notification(title="Transfer received", body=f"{ref} is available at the destination branch.", kind="transfer")); db.commit()
    return {"reference": ref, "message": "Transfer posted with matched ledger events"}

@app.post("/api/v1/sales", status_code=201)
def create_sale(
    payload: SaleIn,
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(require_roles("owner", "cashier", "shop_manager"))] = None,
) -> dict:
    if payload.client_request_id:
        existing = db.scalar(select(Sale).where(Sale.idempotency_key == payload.client_request_id))
        if existing: return {"receipt_number": existing.receipt_number, "total": existing.total, "message": "Sale already posted"}
    product = require(db, Product, payload.product_id); require(db, Branch, payload.branch_id)
    if branch_stock(db, payload.product_id, payload.branch_id) < payload.quantity: raise HTTPException(422, "Insufficient branch stock")
    ref = reference("S")
    total = product.selling_price * payload.quantity
    db.add(Sale(receipt_number=ref, product_id=payload.product_id, branch_id=payload.branch_id, quantity=payload.quantity, total=total, idempotency_key=payload.client_request_id)); db.add(StockMovement(product_id=payload.product_id, branch_id=payload.branch_id, quantity=-payload.quantity, kind="sale", reference=ref, created_by="Cashier")); db.commit()
    return {"receipt_number": ref, "total": total, "message": "Sale recorded"}

@app.get("/api/v1/approvals")
def list_approvals(
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(get_current_user)] = None,
) -> list[dict]:
    approvals = db.scalars(select(Approval).order_by(Approval.created_at.desc())).all()
    return [serialize(approval) for approval in approvals]


@app.post("/api/v1/approvals", status_code=201)
def create_approval(
    payload: ApprovalIn,
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(require_roles("owner", "accountant"))] = None,
) -> dict:
    approval = Approval(**payload.model_dump()); db.add(approval); db.add(Notification(title="Approval requested", body=payload.subject, kind="approval")); db.commit(); return serialize(approval)


@app.post("/api/v1/approvals/{approval_id}/approve")
def approve(
    approval_id: str,
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(require_roles("owner", "accountant"))] = None,
) -> dict:
    approval = require(db, Approval, approval_id); approval.status = "approved"; db.commit(); return serialize(approval)


@app.post("/api/v1/approvals/{approval_id}/reject")
def reject_approval(
    approval_id: str,
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(require_roles("owner", "accountant"))] = None,
) -> dict:
    approval = require(db, Approval, approval_id); approval.status = "rejected"; db.commit(); return serialize(approval)


@app.post("/api/v1/expenses", status_code=201)
def create_expense(
    payload: ExpenseIn,
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(require_roles("owner", "accountant"))] = None,
) -> dict:
    require(db, Branch, payload.branch_id); expense = Expense(**payload.model_dump()); db.add(expense); db.add(Notification(title="Expense awaiting approval", body=payload.description, kind="expense")); db.commit(); return serialize(expense)


@app.get("/api/v1/notifications")
def list_notifications(
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(get_current_user)] = None,
) -> list[dict]:
    notifications = db.scalars(select(Notification).order_by(Notification.created_at.desc())).all()
    return [serialize(notification) for notification in notifications]


@app.post("/api/v1/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: str,
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(get_current_user)] = None,
) -> dict:
    notification = require(db, Notification, notification_id)
    notification.read = True
    db.commit()
    return serialize(notification)


@app.get("/api/v1/notifications/unread-count")
def unread_notification_count(
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(get_current_user)] = None,
) -> dict:
    count = int(db.scalar(select(func.count(Notification.id)).where(Notification.read.is_(False))) or 0)
    return {"unread_count": count}


@app.get("/api/v1/reports/overview")
def report_overview(
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(get_current_user)] = None,
) -> dict:
    products = db.scalars(select(Product)).all(); branches = db.scalars(select(Branch)).all()
    stock = [{"product_id": p.id, "branch_id": b.id, "quantity": branch_stock(db, p.id, b.id)} for p in products for b in branches]
    low = [row for row in stock if row["quantity"] <= next(p.reorder_level for p in products if p.id == row["product_id"])]
    return {"sales_total": float(db.scalar(select(func.coalesce(func.sum(Sale.total), 0))) or 0), "sales_count": int(db.scalar(select(func.count(Sale.id))) or 0), "pending_approvals": int(db.scalar(select(func.count(Approval.id)).where(Approval.status == "pending")) or 0), "pending_purchase_orders": int(db.scalar(select(func.count(PurchaseOrder.id)).where(PurchaseOrder.status == "pending")) or 0), "stock": stock, "low_stock": low}

@app.get("/api/v1/reports/daily-sales")
def daily_sales(
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(get_current_user)] = None,
) -> list[dict]:
    rows = db.execute(
        select(func.date(Sale.created_at), func.count(Sale.id), func.coalesce(func.sum(Sale.total), 0.0)).group_by(func.date(Sale.created_at)).order_by(func.date(Sale.created_at))
    ).all()
    return [{"day": day, "sales_count": int(count), "sales_total": float(total)} for day, count, total in rows]


def serialize(record: object) -> dict:
    result = {column.name: getattr(record, column.name) for column in record.__table__.columns if column.name != "hashed_password"}
    for key, value in result.items():
        if isinstance(value, datetime): result[key] = value.isoformat()
    return result