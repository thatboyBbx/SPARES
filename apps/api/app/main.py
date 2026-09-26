"""Database-backed API for the SparePilot operating MVP."""
import math
from collections import Counter
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.auth import create_access_token, create_refresh_token, get_current_user, hash_password, require_roles, revoke_refresh_token, rotate_refresh_token, verify_and_upgrade_password
from app.core.config import PILOT_PASSWORD, get_settings
from app.core.notifications import dispatch_notification
from app.core.permissions import CAN_APPROVE, CAN_FULFIL_TRANSFER, CAN_MANAGE_ACCOUNTING, CAN_MANAGE_STOCK_RECEIPTS, CAN_MANAGE_USERS, CAN_RECEIVE_TRANSFER, CAN_RECORD_SALE, CAN_REQUEST_PURCHASE, CAN_REQUEST_TRANSFER, CAN_VIEW_ANALYTICS
from app.db.base import Base, SyncStatus, as_aware_utc
from app.db.session import engine, get_db
from app.models import Approval, Branch, Category, Customer, Device, Expense, JournalEntry, Notification, Product, PurchaseOrder, PurchaseOrderLine, PurchaseReceipt, Sale, StockMovement, Supplier, SupplierPayment, Transfer, User, UserRole
from app.seed import seed_database

# Sentinel for mixin-backed rows written by a plain HTTP request rather than
# replayed from a device's offline outbox (real per-write device attribution
# arrives with the Phase 3 sync plumbing).
SERVER_DEVICE_ID = "server"

settings = get_settings()


def initialize_database() -> None:
    settings.assert_deployable()
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


class CategoryIn(BaseModel):
    name: str
    parent_id: str | None = None


class ProductCategoryIn(BaseModel):
    category_id: str | None = None

class SaleIn(BaseModel):
    product_id: str
    branch_id: str
    quantity: int = Field(gt=0)
    customer_id: str | None = None
    client_request_id: str | None = None


class CustomerIn(BaseModel):
    name: str
    phone: str = ""

class ApprovalIn(BaseModel):
    type: str
    subject: str
    requester: str
    approver: str
    priority: str = "normal"
    deadline: datetime | None = None
    client_request_id: str | None = None


class ProductPriceIn(BaseModel):
    selling_price: float = Field(gt=0)


class PurchaseOrderLineIn(BaseModel):
    product_id: str
    quantity: int = Field(gt=0)
    unit_cost: float = Field(default=0.0)


class PurchaseOrderIn(BaseModel):
    supplier_id: str
    branch_id: str
    notes: str = ""
    items: list[PurchaseOrderLineIn] = Field(default_factory=list)
    client_request_id: str | None = None


class ExpenseIn(BaseModel):
    branch_id: str
    category: str
    amount: float = Field(gt=0)
    description: str
    client_request_id: str | None = None


class SupplierPaymentIn(BaseModel):
    supplier_id: str
    purchase_order_id: str | None = None
    amount: float = Field(gt=0)
    method: str = "bank"
    notes: str = ""
    client_request_id: str | None = None


class VersionedActionIn(BaseModel):
    expected_version: int

class LoginIn(BaseModel):
    email: str
    password: str
    device_id: str | None = None


class RefreshIn(BaseModel):
    refresh_token: str


class DeviceRegisterIn(BaseModel):
    device_id: str
    platform: str = "web"
    push_token: str | None = None


class PushTokenIn(BaseModel):
    device_id: str
    push_token: str


class SyncFailureIn(BaseModel):
    path: str
    error: str = ""
    attempts: int = 0


class UserCreateIn(BaseModel):
    email: str
    full_name: str
    password: str
    role: UserRole
    branch_id: str | None = None


class UserUpdateIn(BaseModel):
    role: UserRole | None = None
    branch_id: str | None = None
    is_active: bool | None = None


class BranchIn(BaseModel):
    name: str
    kind: str
    address: str = ""

def reference(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc):%y%m%d%H%M%S}-{str(uuid4())[:4].upper()}"

def branch_stock(db: Session, product_id: str, branch_id: str) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(StockMovement.quantity), 0)).where(StockMovement.product_id == product_id, StockMovement.branch_id == branch_id)) or 0)


def notify_if_crossing_low_stock(db: Session, product: Product, branch_id: str, movement_quantity: int) -> None:
    """Fire once, on the specific movement that pushes stock from at/above
    the reorder level to below it — not on every subsequent sale while it
    stays low."""
    if movement_quantity >= 0:
        return
    db.flush()
    current = branch_stock(db, product.id, branch_id)
    previous = current - movement_quantity
    if previous >= product.reorder_level and current < product.reorder_level:
        dispatch_notification(db, title=f"Low stock: {product.name}", body=f"{product.name} is at {current} units at this branch (reorder level {product.reorder_level}).", kind="low_stock")

def post_journal_entry(
    db: Session,
    *,
    entry_type: str,
    amount: float,
    reference: str,
    actor: str,
    description: str = "",
    branch_id: str | None = None,
    related_entity_type: str | None = None,
    related_entity_id: str | None = None,
) -> JournalEntry:
    """Single write path for the accounting ledger — sale revenue, approved
    expenses, purchase cost on receive, and supplier payments all post here
    so financial reports stay a derived SUM() over signed entries, the same
    discipline branch_stock() already applies to StockMovement."""
    entry = JournalEntry(entry_type=entry_type, amount=amount, reference=reference, description=description, branch_id=branch_id, related_entity_type=related_entity_type, related_entity_id=related_entity_id, created_by=actor)
    db.add(entry)
    return entry


def require(db: Session, model: type, identity: str):
    record = db.get(model, identity)
    if not record:
        raise HTTPException(404, f"{model.__name__} not found")
    return record

# Entities an Approval can gate; approving/rejecting the Approval flips the
# entity's own status through the same code path so the two can never drift
# apart, regardless of whether the operator acts from the Approval side
# (/approvals/{id}/approve) or the entity side (/expenses/{id}/approve).
APPROVABLE_ENTITY_MODELS: dict[str, type] = {"expense": Expense, "purchase_order": PurchaseOrder}
DEFAULT_APPROVAL_WINDOW = timedelta(hours=72)


def default_deadline() -> datetime:
    return datetime.now(timezone.utc) + DEFAULT_APPROVAL_WINDOW


def create_linked_approval(
    db: Session,
    *,
    type_: str,
    subject: str,
    requester: User,
    approver_label: str = "",
    related_entity_type: str | None = None,
    related_entity_id: str | None = None,
    priority: str = "normal",
    deadline: datetime | None = None,
) -> Approval:
    approval = Approval(
        type=type_,
        subject=subject,
        requester=requester.full_name,
        approver=approver_label,
        priority=priority,
        related_entity_type=related_entity_type,
        related_entity_id=related_entity_id,
        deadline=deadline or default_deadline(),
        device_id=SERVER_DEVICE_ID,
        created_by_id=requester.id,
    )
    db.add(approval)
    return approval


def dispatch_approval_decision(db: Session, approval: Approval, decision: str, actor: User) -> None:
    approval.status = decision
    approval.approver_id = actor.id
    if approval.related_entity_type and approval.related_entity_id:
        model = APPROVABLE_ENTITY_MODELS.get(approval.related_entity_type)
        entity = db.get(model, approval.related_entity_id) if model else None
        if entity is not None:
            entity.status = decision
            if decision == "approved" and hasattr(entity, "approved_by_id"):
                entity.approved_by_id = actor.id
                entity.approved_at = datetime.now(timezone.utc)


def find_linked_approval(db: Session, related_entity_type: str, related_entity_id: str) -> Approval:
    approval = db.scalar(
        select(Approval).where(
            Approval.related_entity_type == related_entity_type,
            Approval.related_entity_id == related_entity_id,
            Approval.status == "pending",
        )
    )
    if not approval:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No pending approval linked to this record")
    return approval


def escalate_overdue_approvals(db: Session) -> None:
    """Lazy escalation: bump overdue pending approvals to high priority
    whenever an approval endpoint is touched, rather than running a
    background job for a table this size."""
    now = datetime.now(timezone.utc)
    overdue = db.scalars(
        select(Approval).where(Approval.status == "pending", Approval.deadline.is_not(None), Approval.deadline < now, Approval.priority != "high")
    ).all()
    for approval in overdue:
        approval.priority = "high"
    if overdue:
        db.commit()


def serialize_approval(approval: Approval) -> dict:
    result = serialize(approval)
    result["is_overdue"] = bool(approval.deadline and approval.status == "pending" and as_aware_utc(approval.deadline) < datetime.now(timezone.utc))
    return result


@app.get("/health", tags=["system"])
def health_check() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}


def readiness_checks() -> dict[str, dict[str, str]]:
    """Return dependency state without exposing connection strings or secrets."""
    checks: dict[str, dict[str, str]] = {}
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        checks["database"] = {"status": "ok"}
    except Exception:
        checks["database"] = {"status": "unavailable"}
    try:
        from redis import Redis
        Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1).ping()
        checks["redis"] = {"status": "ok"}
    except Exception:
        checks["redis"] = {"status": "unavailable"}
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar_one()
        checks["migrations"] = {"status": "ok"}
    except Exception:
        checks["migrations"] = {"status": "unavailable"}
    return checks


@app.get("/ready", tags=["system"])
def readiness_check() -> JSONResponse:
    checks = readiness_checks()
    ready = all(check["status"] == "ok" for check in checks.values())
    return JSONResponse(
        status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )

@app.get("/api/v1/bootstrap")
def bootstrap(
    db: Session = Depends(get_db),
    current_user: Annotated[User, Depends(get_current_user)] = None,
) -> dict:
    products = db.scalars(select(Product).order_by(Product.name)).all()
    branches = db.scalars(select(Branch).order_by(Branch.kind)).all()
    movements = db.scalars(select(StockMovement).order_by(StockMovement.created_at.desc())).all()
    return {"users": [serialize(u) for u in db.scalars(select(User)).all()], "products": [serialize(p) for p in products], "categories": [serialize(c) for c in db.scalars(select(Category).order_by(Category.name)).all()], "branches": [serialize(b) for b in branches], "suppliers": [serialize(s) for s in db.scalars(select(Supplier)).all()], "customers": [serialize(c) for c in db.scalars(select(Customer).order_by(Customer.name)).all()], "movements": [serialize(m) for m in movements], "transfers": [serialize(t) for t in db.scalars(select(Transfer).order_by(Transfer.created_at.desc())).all()], "sales": [serialize(s) for s in db.scalars(select(Sale).order_by(Sale.created_at.desc())).all()], "purchase_orders": [serialize(o) for o in db.scalars(select(PurchaseOrder).order_by(PurchaseOrder.requested_at.desc())).all()], "purchase_order_lines": [serialize(l) for l in db.scalars(select(PurchaseOrderLine).order_by(PurchaseOrderLine.created_at.desc())).all()], "approvals": [serialize_approval(a) for a in db.scalars(select(Approval).order_by(Approval.created_at.desc())).all()], "expenses": [serialize(e) for e in db.scalars(select(Expense).order_by(Expense.created_at.desc())).all()], "notifications": [serialize(n) for n in db.scalars(select(Notification).order_by(Notification.created_at.desc())).all()], "supplier_payments": [serialize(p) for p in db.scalars(select(SupplierPayment).order_by(SupplierPayment.created_at.desc())).all()] if current_user.role in CAN_MANAGE_ACCOUNTING else [], "journal_entries": [serialize(j) for j in db.scalars(select(JournalEntry).order_by(JournalEntry.created_at.desc())).all()] if current_user.role in CAN_MANAGE_ACCOUNTING else []}

@app.post("/api/v1/auth/login")
def login(payload: LoginIn, db: Session = Depends(get_db)) -> dict:
    if settings.environment == "production" and payload.password == PILOT_PASSWORD:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    user = db.scalar(select(User).where(User.email == payload.email))
    if not user or not verify_and_upgrade_password(user, payload.password, db):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    access_token = create_access_token(user.id, user.role.value)
    refresh_token = create_refresh_token(db, user.id, payload.device_id)
    return {"user": serialize(user), "access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"}


@app.post("/api/v1/auth/refresh")
def refresh_tokens(payload: RefreshIn, db: Session = Depends(get_db)) -> dict:
    access_token, refresh_token, _ = rotate_refresh_token(db, payload.refresh_token)
    return {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"}


@app.post("/api/v1/auth/logout", status_code=204)
def logout(payload: RefreshIn, db: Session = Depends(get_db)) -> None:
    revoke_refresh_token(db, payload.refresh_token)


@app.get("/api/v1/auth/me")
def current_user(user: Annotated[User, Depends(get_current_user)]) -> dict:
    return {"user": serialize(user)}


@app.post("/api/v1/devices/register", status_code=201)
def register_device(
    payload: DeviceRegisterIn,
    db: Session = Depends(get_db),
    user: Annotated[User, Depends(get_current_user)] = None,
) -> dict:
    device = db.scalar(select(Device).where(Device.user_id == user.id, Device.device_id == payload.device_id))
    if device:
        device.platform = payload.platform
        if payload.push_token is not None:
            device.push_token = payload.push_token
        device.last_seen_at = datetime.now(timezone.utc)
    else:
        device = Device(user_id=user.id, device_id=payload.device_id, platform=payload.platform, push_token=payload.push_token, created_by_id=user.id)
        db.add(device)
    db.commit()
    return serialize(device)


@app.get("/api/v1/devices")
def list_devices(
    db: Session = Depends(get_db),
    user: Annotated[User, Depends(get_current_user)] = None,
) -> list[dict]:
    devices = db.scalars(select(Device).where(Device.user_id == user.id)).all()
    return [serialize(device) for device in devices]


@app.get("/api/v1/users")
def list_users(
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(require_roles(*CAN_MANAGE_USERS))] = None,
) -> list[dict]:
    return [serialize(user) for user in db.scalars(select(User).order_by(User.full_name)).all()]


@app.post("/api/v1/users", status_code=201)
def create_user(
    payload: UserCreateIn,
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(require_roles(*CAN_MANAGE_USERS))] = None,
) -> dict:
    if db.scalar(select(User).where(User.email == payload.email)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Email already registered")
    if payload.branch_id:
        require(db, Branch, payload.branch_id)
    user = User(id=str(uuid4()), email=payload.email, full_name=payload.full_name, hashed_password=hash_password(payload.password), role=payload.role, branch_id=payload.branch_id)
    db.add(user)
    db.commit()
    return serialize(user)


@app.patch("/api/v1/users/{user_id}")
def update_user(
    user_id: str,
    payload: UserUpdateIn,
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(require_roles(*CAN_MANAGE_USERS))] = None,
) -> dict:
    user = require(db, User, user_id)
    if payload.branch_id is not None:
        require(db, Branch, payload.branch_id)
        user.branch_id = payload.branch_id
    if payload.role is not None:
        user.role = payload.role
    if payload.is_active is not None:
        user.is_active = payload.is_active
    db.commit()
    return serialize(user)


@app.get("/api/v1/branches")
def list_branches(
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(get_current_user)] = None,
) -> list[dict]:
    return [serialize(item) for item in db.scalars(select(Branch).order_by(Branch.kind)).all()]


@app.post("/api/v1/branches", status_code=201)
def create_branch(
    payload: BranchIn,
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(require_roles(*CAN_MANAGE_USERS))] = None,
) -> dict:
    branch = Branch(name=payload.name, kind=payload.kind, address=payload.address)
    db.add(branch)
    db.commit()
    return serialize(branch)

@app.post("/api/v1/receipts", status_code=201)
def create_receipt(
    payload: ReceiptIn,
    db: Session = Depends(get_db),
    current_user: Annotated[User, Depends(require_roles(*CAN_MANAGE_STOCK_RECEIPTS))] = None,
) -> dict:
    if payload.client_request_id:
        existing = db.scalar(select(PurchaseReceipt).where(PurchaseReceipt.idempotency_key == payload.client_request_id))
        if existing: return {"reference": existing.reference, "message": "Goods receipt already posted"}
    require(db, Product, payload.product_id); require(db, Branch, payload.branch_id); require(db, Supplier, payload.supplier_id)
    ref = reference("GRN")
    receipt = PurchaseReceipt(reference=ref, product_id=payload.product_id, branch_id=payload.branch_id, supplier_id=payload.supplier_id, quantity=payload.quantity, idempotency_key=payload.client_request_id, device_id=SERVER_DEVICE_ID, created_by_id=current_user.id)
    db.add(receipt); db.add(StockMovement(product_id=payload.product_id, branch_id=payload.branch_id, quantity=payload.quantity, kind="receipt", reference=ref, created_by=current_user.full_name, device_id=SERVER_DEVICE_ID, created_by_id=current_user.id)); db.commit()
    return {"reference": ref, "message": "Goods receipt posted"}

@app.post("/api/v1/purchase-orders", status_code=201)
def create_purchase_order(
    payload: PurchaseOrderIn,
    db: Session = Depends(get_db),
    current_user: Annotated[User, Depends(require_roles(*CAN_MANAGE_STOCK_RECEIPTS))] = None,
) -> dict:
    if payload.client_request_id:
        existing = db.scalar(select(PurchaseOrder).where(PurchaseOrder.idempotency_key == payload.client_request_id))
        if existing: return {"id": existing.id, "reference": existing.reference, "status": existing.status, "version": existing.version, "message": "Purchase order already requested"}
    if not payload.items:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "At least one item is required")
    require(db, Supplier, payload.supplier_id)
    require(db, Branch, payload.branch_id)
    for item in payload.items:
        require(db, Product, item.product_id)

    order_reference = reference("PO")
    order = PurchaseOrder(reference=order_reference, supplier_id=payload.supplier_id, branch_id=payload.branch_id, notes=payload.notes, created_by=current_user.full_name, idempotency_key=payload.client_request_id, device_id=SERVER_DEVICE_ID, created_by_id=current_user.id)
    db.add(order)
    db.flush()
    db.add_all([
        PurchaseOrderLine(purchase_order_id=order.id, product_id=item.product_id, quantity=item.quantity, unit_cost=item.unit_cost, device_id=SERVER_DEVICE_ID, created_by_id=current_user.id)
        for item in payload.items
    ])
    create_linked_approval(db, type_="purchase_order", subject=f"Purchase order {order_reference} ({len(payload.items)} items)", requester=current_user, related_entity_type="purchase_order", related_entity_id=order.id)
    dispatch_notification(db, title="Purchase order requested", body=f"{order_reference} was created for {len(payload.items)} items.", kind="purchase_order")
    db.commit()
    return {"id": order.id, "reference": order_reference, "status": order.status, "version": order.version, "message": "Purchase order requested"}


@app.get("/api/v1/purchase-orders")
def list_purchase_orders(
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(get_current_user)] = None,
) -> list[dict]:
    orders = db.scalars(select(PurchaseOrder).order_by(PurchaseOrder.requested_at.desc())).all()
    return [serialize(order) for order in orders]


@app.post("/api/v1/purchase-orders/{purchase_order_id}/approve", status_code=201)
def approve_purchase_order(
    purchase_order_id: str,
    db: Session = Depends(get_db),
    current_user: Annotated[User, Depends(require_roles(*CAN_APPROVE))] = None,
) -> dict:
    escalate_overdue_approvals(db)
    order = require(db, PurchaseOrder, purchase_order_id)
    if order.status != "requested":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Purchase order is {order.status}, expected requested")
    approval = find_linked_approval(db, "purchase_order", order.id)
    dispatch_approval_decision(db, approval, "approved", current_user)
    dispatch_notification(db, title="Purchase order approved", body=f"{order.reference} is approved and ready to receive.", kind="purchase_order")
    db.commit()
    return {"reference": order.reference, "status": order.status, "version": order.version, "message": "Purchase order approved"}


@app.post("/api/v1/purchase-orders/{purchase_order_id}/receive", status_code=201)
def receive_purchase_order(
    purchase_order_id: str,
    payload: VersionedActionIn,
    db: Session = Depends(get_db),
    current_user: Annotated[User, Depends(require_roles(*CAN_MANAGE_STOCK_RECEIPTS))] = None,
) -> dict:
    order = require(db, PurchaseOrder, purchase_order_id)
    if order.status == "received":
        return {"reference": order.reference, "message": "Purchase order already received"}
    if order.version != payload.expected_version:
        order.sync_status = SyncStatus.CONFLICT
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, f"Purchase order changed since it was loaded (expected version {payload.expected_version}, currently {order.version})")
    if order.status != "approved":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Purchase order is {order.status}, expected approved")

    lines = db.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.purchase_order_id == purchase_order_id)).all()
    if not lines:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Purchase order has no lines")

    total_cost = 0.0
    for line in lines:
        require(db, Product, line.product_id)
        require(db, Branch, order.branch_id)
        receipt_reference = reference("GRN")
        # Deterministic per-line key: a retried receive call is provably
        # idempotent at the DB layer, not solely via the order-status
        # short-circuit above.
        receipt_idempotency_key = f"po-receive-{order.id}-{line.id}"
        db.add(PurchaseReceipt(reference=receipt_reference, supplier_id=order.supplier_id, branch_id=order.branch_id, product_id=line.product_id, quantity=line.quantity, idempotency_key=receipt_idempotency_key, device_id=SERVER_DEVICE_ID, created_by_id=current_user.id))
        db.add(StockMovement(product_id=line.product_id, branch_id=order.branch_id, quantity=line.quantity, kind="receipt", reference=receipt_reference, created_by=current_user.full_name, device_id=SERVER_DEVICE_ID, created_by_id=current_user.id))
        total_cost += line.quantity * line.unit_cost

    order.status = "received"
    order.received_at = datetime.now(timezone.utc)
    if total_cost:
        post_journal_entry(db, entry_type="purchase_cost", amount=-total_cost, reference=order.reference, actor=current_user.full_name, description=f"Goods received for {order.reference}", branch_id=order.branch_id, related_entity_type="purchase_order", related_entity_id=order.id)
    dispatch_notification(db, title="Goods received", body=f"{order.reference} completed and posted to stock.", kind="purchase_order")
    db.commit()
    return {"reference": order.reference, "received_lines": len(lines), "version": order.version, "message": "Purchase order received"}


@app.post("/api/v1/transfers", status_code=201)
def create_transfer(
    payload: TransferIn,
    db: Session = Depends(get_db),
    current_user: Annotated[User, Depends(require_roles(*CAN_REQUEST_TRANSFER))] = None,
) -> dict:
    if payload.client_request_id:
        existing = db.scalar(select(Transfer).where(Transfer.idempotency_key == payload.client_request_id))
        if existing: return {"id": existing.id, "reference": existing.reference, "status": existing.status, "version": existing.version, "message": "Transfer already requested"}
    require(db, Product, payload.product_id); require(db, Branch, payload.from_branch_id); require(db, Branch, payload.to_branch_id)
    if payload.from_branch_id == payload.to_branch_id: raise HTTPException(422, "Origin and destination must differ")
    if branch_stock(db, payload.product_id, payload.from_branch_id) < payload.quantity: raise HTTPException(422, "Insufficient origin stock")
    ref = reference("TRF")
    transfer = Transfer(reference=ref, product_id=payload.product_id, from_branch_id=payload.from_branch_id, to_branch_id=payload.to_branch_id, quantity=payload.quantity, idempotency_key=payload.client_request_id, status="requested", device_id=SERVER_DEVICE_ID, created_by_id=current_user.id)
    db.add(transfer)
    dispatch_notification(db, title="Transfer requested", body=f"{ref} awaits fulfilment from the source branch.", kind="transfer")
    db.commit()
    return {"id": transfer.id, "reference": ref, "status": transfer.status, "version": transfer.version, "message": "Transfer requested"}


@app.get("/api/v1/transfers")
def list_transfers(
    status_query: str | None = Query(default=None, alias="status"),
    branch_id: str | None = None,
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(get_current_user)] = None,
) -> list[dict]:
    query = select(Transfer)
    if status_query:
        query = query.where(Transfer.status == status_query)
    if branch_id:
        query = query.where((Transfer.from_branch_id == branch_id) | (Transfer.to_branch_id == branch_id))
    transfers = db.scalars(query.order_by(Transfer.created_at.desc())).all()
    return [serialize(t) for t in transfers]


@app.post("/api/v1/transfers/{transfer_id}/fulfil", status_code=201)
def fulfil_transfer(
    transfer_id: str,
    payload: VersionedActionIn,
    db: Session = Depends(get_db),
    current_user: Annotated[User, Depends(require_roles(*CAN_FULFIL_TRANSFER))] = None,
) -> dict:
    transfer = require(db, Transfer, transfer_id)
    if transfer.version != payload.expected_version:
        transfer.sync_status = SyncStatus.CONFLICT
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, f"Transfer changed since it was loaded (expected version {payload.expected_version}, currently {transfer.version})")
    if transfer.status != "requested":
        raise HTTPException(422, f"Transfer is {transfer.status}, expected requested")
    if branch_stock(db, transfer.product_id, transfer.from_branch_id) < transfer.quantity:
        raise HTTPException(422, "Insufficient origin stock")
    db.add(StockMovement(product_id=transfer.product_id, branch_id=transfer.from_branch_id, quantity=-transfer.quantity, kind="transfer_out", reference=transfer.reference, created_by=current_user.full_name, device_id=SERVER_DEVICE_ID, created_by_id=current_user.id))
    notify_if_crossing_low_stock(db, require(db, Product, transfer.product_id), transfer.from_branch_id, -transfer.quantity)
    transfer.status = "in_transit"
    transfer.fulfilled_by_id = current_user.id
    transfer.fulfilled_at = datetime.now(timezone.utc)
    dispatch_notification(db, title="Transfer in transit", body=f"{transfer.reference} left the source branch.", kind="transfer")
    db.commit()
    return {"reference": transfer.reference, "status": transfer.status, "version": transfer.version, "message": "Transfer fulfilled"}


@app.post("/api/v1/transfers/{transfer_id}/receive", status_code=201)
def receive_transfer(
    transfer_id: str,
    payload: VersionedActionIn,
    db: Session = Depends(get_db),
    current_user: Annotated[User, Depends(require_roles(*CAN_RECEIVE_TRANSFER))] = None,
) -> dict:
    transfer = require(db, Transfer, transfer_id)
    if transfer.version != payload.expected_version:
        transfer.sync_status = SyncStatus.CONFLICT
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, f"Transfer changed since it was loaded (expected version {payload.expected_version}, currently {transfer.version})")
    if transfer.status != "in_transit":
        raise HTTPException(422, f"Transfer is {transfer.status}, expected in_transit")
    db.add(StockMovement(product_id=transfer.product_id, branch_id=transfer.to_branch_id, quantity=transfer.quantity, kind="transfer_in", reference=transfer.reference, created_by=current_user.full_name, device_id=SERVER_DEVICE_ID, created_by_id=current_user.id))
    transfer.status = "received"
    transfer.received_by_id = current_user.id
    transfer.received_at = datetime.now(timezone.utc)
    dispatch_notification(db, title="Transfer received", body=f"{transfer.reference} is available at the destination branch.", kind="transfer")
    db.commit()
    return {"reference": transfer.reference, "status": transfer.status, "version": transfer.version, "message": "Transfer received"}


@app.post("/api/v1/transfers/{transfer_id}/cancel")
def cancel_transfer(
    transfer_id: str,
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(require_roles(*CAN_REQUEST_TRANSFER))] = None,
) -> dict:
    transfer = require(db, Transfer, transfer_id)
    if transfer.status != "requested":
        raise HTTPException(422, "Only a requested transfer can be cancelled before fulfilment")
    transfer.status = "cancelled"
    db.commit()
    return {"reference": transfer.reference, "status": transfer.status, "message": "Transfer cancelled"}


@app.get("/api/v1/categories")
def list_categories(
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(get_current_user)] = None,
) -> list[dict]:
    return [serialize(category) for category in db.scalars(select(Category).order_by(Category.name)).all()]


@app.post("/api/v1/categories", status_code=201)
def create_category(
    payload: CategoryIn,
    db: Session = Depends(get_db),
    current_user: Annotated[User, Depends(require_roles(*CAN_MANAGE_STOCK_RECEIPTS))] = None,
) -> dict:
    if payload.parent_id:
        require(db, Category, payload.parent_id)
    category = Category(name=payload.name, parent_id=payload.parent_id, device_id=SERVER_DEVICE_ID, created_by_id=current_user.id)
    db.add(category)
    db.commit()
    return serialize(category)


@app.patch("/api/v1/products/{product_id}/category")
def assign_product_category(
    product_id: str,
    payload: ProductCategoryIn,
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(require_roles(*CAN_MANAGE_STOCK_RECEIPTS))] = None,
) -> dict:
    product = require(db, Product, product_id)
    if payload.category_id:
        require(db, Category, payload.category_id)
    product.category_id = payload.category_id
    db.commit()
    return serialize(product)


@app.patch("/api/v1/products/{product_id}/price")
def update_product_price(
    product_id: str,
    payload: ProductPriceIn,
    db: Session = Depends(get_db),
    current_user: Annotated[User, Depends(get_current_user)] = None,
) -> dict:
    product = require(db, Product, product_id)
    if current_user.role in (UserRole.OWNER, UserRole.ACCOUNTANT, UserRole.SUPER_ADMIN):
        product.selling_price = payload.selling_price
        db.commit()
        return {"product": serialize(product), "message": "Price updated"}
    approval = create_linked_approval(
        db,
        type_="price_change",
        subject=f"Change {product.name} selling price from {product.selling_price} to {payload.selling_price}",
        requester=current_user,
        related_entity_type="product",
        related_entity_id=product.id,
    )
    dispatch_notification(db, title="Price change requested", body=approval.subject, kind="approval")
    db.commit()
    return {"approval": serialize_approval(approval), "message": "Price change submitted for approval"}

@app.post("/api/v1/sales", status_code=201)
def create_sale(
    payload: SaleIn,
    db: Session = Depends(get_db),
    current_user: Annotated[User, Depends(require_roles(*CAN_RECORD_SALE))] = None,
) -> dict:
    if payload.client_request_id:
        existing = db.scalar(select(Sale).where(Sale.idempotency_key == payload.client_request_id))
        if existing: return {"receipt_number": existing.receipt_number, "total": existing.total, "message": "Sale already posted"}
    product = require(db, Product, payload.product_id); require(db, Branch, payload.branch_id)
    if payload.customer_id:
        require(db, Customer, payload.customer_id)
    if branch_stock(db, payload.product_id, payload.branch_id) < payload.quantity: raise HTTPException(422, "Insufficient branch stock")
    ref = reference("S")
    total = product.selling_price * payload.quantity
    sale = Sale(receipt_number=ref, product_id=payload.product_id, branch_id=payload.branch_id, customer_id=payload.customer_id, quantity=payload.quantity, total=total, idempotency_key=payload.client_request_id, device_id=SERVER_DEVICE_ID, created_by_id=current_user.id)
    db.add(sale)
    db.add(StockMovement(product_id=payload.product_id, branch_id=payload.branch_id, quantity=-payload.quantity, kind="sale", reference=ref, created_by=current_user.full_name, device_id=SERVER_DEVICE_ID, created_by_id=current_user.id))
    db.flush()
    post_journal_entry(db, entry_type="sale_revenue", amount=total, reference=ref, actor=current_user.full_name, description=f"Sale {ref} ({payload.quantity}x {product.name})", branch_id=payload.branch_id, related_entity_type="sale", related_entity_id=sale.id)
    notify_if_crossing_low_stock(db, product, payload.branch_id, -payload.quantity)
    db.commit()
    return {"receipt_number": ref, "total": total, "message": "Sale recorded"}


@app.get("/api/v1/customers")
def list_customers(
    q: str | None = None,
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(get_current_user)] = None,
) -> list[dict]:
    query = select(Customer)
    if q:
        query = query.where(Customer.name.ilike(f"%{q}%"))
    return [serialize(customer) for customer in db.scalars(query.order_by(Customer.name)).all()]


@app.post("/api/v1/customers", status_code=201)
def create_customer(
    payload: CustomerIn,
    db: Session = Depends(get_db),
    current_user: Annotated[User, Depends(require_roles(*CAN_RECORD_SALE))] = None,
) -> dict:
    customer = Customer(name=payload.name, phone=payload.phone, device_id=SERVER_DEVICE_ID, created_by_id=current_user.id)
    db.add(customer)
    db.commit()
    return serialize(customer)

@app.get("/api/v1/approvals")
def list_approvals(
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(get_current_user)] = None,
) -> list[dict]:
    escalate_overdue_approvals(db)
    approvals = db.scalars(select(Approval).order_by(Approval.created_at.desc())).all()
    return [serialize_approval(approval) for approval in approvals]


@app.post("/api/v1/approvals", status_code=201)
def create_approval(
    payload: ApprovalIn,
    db: Session = Depends(get_db),
    current_user: Annotated[User, Depends(require_roles(*CAN_APPROVE))] = None,
) -> dict:
    escalate_overdue_approvals(db)
    if payload.client_request_id:
        existing = db.scalar(select(Approval).where(Approval.idempotency_key == payload.client_request_id))
        if existing: return serialize_approval(existing)
    fields = payload.model_dump(exclude={"client_request_id", "deadline"})
    approval = Approval(**fields, idempotency_key=payload.client_request_id, deadline=payload.deadline or default_deadline(), device_id=SERVER_DEVICE_ID, created_by_id=current_user.id)
    db.add(approval); dispatch_notification(db, title="Approval requested", body=payload.subject, kind="approval"); db.commit(); return serialize_approval(approval)


@app.post("/api/v1/approvals/{approval_id}/approve")
def approve(
    approval_id: str,
    db: Session = Depends(get_db),
    current_user: Annotated[User, Depends(require_roles(*CAN_APPROVE))] = None,
) -> dict:
    escalate_overdue_approvals(db)
    approval = require(db, Approval, approval_id)
    dispatch_approval_decision(db, approval, "approved", current_user)
    db.commit()
    return serialize_approval(approval)


@app.post("/api/v1/approvals/{approval_id}/reject")
def reject_approval(
    approval_id: str,
    db: Session = Depends(get_db),
    current_user: Annotated[User, Depends(require_roles(*CAN_APPROVE))] = None,
) -> dict:
    escalate_overdue_approvals(db)
    approval = require(db, Approval, approval_id)
    dispatch_approval_decision(db, approval, "rejected", current_user)
    db.commit()
    return serialize_approval(approval)


@app.post("/api/v1/expenses", status_code=201)
def create_expense(
    payload: ExpenseIn,
    db: Session = Depends(get_db),
    current_user: Annotated[User, Depends(require_roles(*CAN_APPROVE))] = None,
) -> dict:
    if payload.client_request_id:
        existing = db.scalar(select(Expense).where(Expense.idempotency_key == payload.client_request_id))
        if existing: return serialize(existing)
    require(db, Branch, payload.branch_id)
    fields = payload.model_dump(exclude={"client_request_id"})
    expense = Expense(**fields, idempotency_key=payload.client_request_id, device_id=SERVER_DEVICE_ID, created_by_id=current_user.id)
    db.add(expense)
    db.flush()
    create_linked_approval(db, type_="expense", subject=f"Expense: {payload.description}", requester=current_user, related_entity_type="expense", related_entity_id=expense.id)
    dispatch_notification(db, title="Expense awaiting approval", body=payload.description, kind="expense"); db.commit(); return serialize(expense)


@app.post("/api/v1/expenses/{expense_id}/approve")
def approve_expense(
    expense_id: str,
    db: Session = Depends(get_db),
    current_user: Annotated[User, Depends(require_roles(*CAN_APPROVE))] = None,
) -> dict:
    escalate_overdue_approvals(db)
    expense = require(db, Expense, expense_id)
    approval = find_linked_approval(db, "expense", expense_id)
    dispatch_approval_decision(db, approval, "approved", current_user)
    post_journal_entry(db, entry_type="expense", amount=-expense.amount, reference=f"EXP-{expense.id[:8]}", actor=current_user.full_name, description=expense.description, branch_id=expense.branch_id, related_entity_type="expense", related_entity_id=expense.id)
    db.commit()
    return serialize(expense)


@app.post("/api/v1/expenses/{expense_id}/reject")
def reject_expense(
    expense_id: str,
    db: Session = Depends(get_db),
    current_user: Annotated[User, Depends(require_roles(*CAN_APPROVE))] = None,
) -> dict:
    escalate_overdue_approvals(db)
    expense = require(db, Expense, expense_id)
    approval = find_linked_approval(db, "expense", expense_id)
    dispatch_approval_decision(db, approval, "rejected", current_user)
    db.commit()
    return serialize(expense)


@app.post("/api/v1/supplier-payments", status_code=201)
def create_supplier_payment(
    payload: SupplierPaymentIn,
    db: Session = Depends(get_db),
    current_user: Annotated[User, Depends(require_roles(*CAN_MANAGE_ACCOUNTING))] = None,
) -> dict:
    if payload.client_request_id:
        existing = db.scalar(select(SupplierPayment).where(SupplierPayment.idempotency_key == payload.client_request_id))
        if existing: return {"reference": existing.reference, "message": "Supplier payment already recorded"}
    supplier = require(db, Supplier, payload.supplier_id)
    order = require(db, PurchaseOrder, payload.purchase_order_id) if payload.purchase_order_id else None
    if order and order.supplier_id != supplier.id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Purchase order does not belong to this supplier")
    ref = reference("PAY")
    payment = SupplierPayment(reference=ref, supplier_id=supplier.id, purchase_order_id=order.id if order else None, amount=payload.amount, method=payload.method, notes=payload.notes, idempotency_key=payload.client_request_id, device_id=SERVER_DEVICE_ID, created_by_id=current_user.id)
    db.add(payment)
    db.flush()
    post_journal_entry(db, entry_type="supplier_payment", amount=-payload.amount, reference=ref, actor=current_user.full_name, description=f"Payment to {supplier.name}" + (f" for {order.reference}" if order else ""), branch_id=order.branch_id if order else None, related_entity_type="supplier_payment", related_entity_id=payment.id)
    db.commit()
    return {"reference": ref, "message": "Supplier payment recorded"}


@app.get("/api/v1/supplier-payments")
def list_supplier_payments(
    supplier_id: str | None = None,
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(require_roles(*CAN_MANAGE_ACCOUNTING))] = None,
) -> list[dict]:
    query = select(SupplierPayment)
    if supplier_id:
        query = query.where(SupplierPayment.supplier_id == supplier_id)
    return [serialize(payment) for payment in db.scalars(query.order_by(SupplierPayment.created_at.desc())).all()]


@app.get("/api/v1/journal")
def list_journal_entries(
    entry_type: str | None = None,
    branch_id: str | None = None,
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(require_roles(*CAN_MANAGE_ACCOUNTING))] = None,
) -> list[dict]:
    query = select(JournalEntry)
    if entry_type:
        query = query.where(JournalEntry.entry_type == entry_type)
    if branch_id:
        query = query.where(JournalEntry.branch_id == branch_id)
    return [serialize(entry) for entry in db.scalars(query.order_by(JournalEntry.created_at.desc())).all()]


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


@app.post("/api/v1/notifications/push-token")
def register_push_token(
    payload: PushTokenIn,
    db: Session = Depends(get_db),
    user: Annotated[User, Depends(get_current_user)] = None,
) -> dict:
    device = db.scalar(select(Device).where(Device.user_id == user.id, Device.device_id == payload.device_id))
    if not device:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Device not registered; call /devices/register first")
    device.push_token = payload.push_token
    db.commit()
    return serialize(device)


@app.post("/api/v1/sync/report-failure", status_code=201)
def report_sync_failure(
    payload: SyncFailureIn,
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(get_current_user)] = None,
) -> dict:
    notification = dispatch_notification(
        db,
        title="Sync failed",
        body=f"{payload.path} failed to sync after {payload.attempts} attempts: {payload.error or 'unknown error'}",
        kind="sync_failed",
    )
    db.commit()
    return serialize(notification)


@app.get("/api/v1/reports/overview")
def report_overview(
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(get_current_user)] = None,
) -> dict:
    products = db.scalars(select(Product)).all(); branches = db.scalars(select(Branch)).all()
    stock = [{"product_id": p.id, "branch_id": b.id, "quantity": branch_stock(db, p.id, b.id)} for p in products for b in branches]
    low = [row for row in stock if row["quantity"] <= next(p.reorder_level for p in products if p.id == row["product_id"])]
    return {"sales_total": float(db.scalar(select(func.coalesce(func.sum(Sale.total), 0))) or 0), "sales_count": int(db.scalar(select(func.count(Sale.id))) or 0), "pending_approvals": int(db.scalar(select(func.count(Approval.id)).where(Approval.status == "pending")) or 0), "pending_purchase_orders": int(db.scalar(select(func.count(PurchaseOrder.id)).where(PurchaseOrder.status != "received")) or 0), "stock": stock, "low_stock": low}

@app.get("/api/v1/reports/daily-sales")
def daily_sales(
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(get_current_user)] = None,
) -> list[dict]:
    rows = db.execute(
        select(func.date(Sale.created_at), func.count(Sale.id), func.coalesce(func.sum(Sale.total), 0.0)).group_by(func.date(Sale.created_at)).order_by(func.date(Sale.created_at))
    ).all()
    return [{"day": day, "sales_count": int(count), "sales_total": float(total)} for day, count, total in rows]


@app.get("/api/v1/reports/profit-loss")
def profit_loss(
    start: date | None = None,
    end: date | None = None,
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(require_roles(*CAN_MANAGE_ACCOUNTING))] = None,
) -> dict:
    """Accrual-basis P&L derived from the journal: revenue is recognised at
    sale, purchase_cost at goods-received (not at cash payment) — so
    supplier_payments is reported separately as a cash-flow figure and is
    deliberately NOT subtracted again here, to avoid double-counting the
    same spend on both a cash and an accrual basis."""
    query = select(JournalEntry.entry_type, func.coalesce(func.sum(JournalEntry.amount), 0.0)).group_by(JournalEntry.entry_type)
    if start:
        query = query.where(JournalEntry.created_at >= datetime.combine(start, time.min, tzinfo=timezone.utc))
    if end:
        query = query.where(JournalEntry.created_at <= datetime.combine(end, time.max, tzinfo=timezone.utc))
    totals = {entry_type: float(total) for entry_type, total in db.execute(query).all()}
    revenue = totals.get("sale_revenue", 0.0)
    purchase_cost = -totals.get("purchase_cost", 0.0)
    expenses = -totals.get("expense", 0.0)
    supplier_payments_cash_out = -totals.get("supplier_payment", 0.0)
    return {
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "revenue": revenue,
        "purchase_cost": purchase_cost,
        "expenses": expenses,
        "net_profit": revenue - purchase_cost - expenses,
        "supplier_payments_cash_out": supplier_payments_cash_out,
    }


@app.get("/api/v1/reports/branch-performance")
def branch_performance(
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(require_roles(*CAN_VIEW_ANALYTICS))] = None,
) -> list[dict]:
    branches = db.scalars(select(Branch).order_by(Branch.kind)).all()
    products = db.scalars(select(Product)).all()
    rows = []
    for b in branches:
        sales_total, sales_count = db.execute(select(func.coalesce(func.sum(Sale.total), 0.0), func.count(Sale.id)).where(Sale.branch_id == b.id)).one()
        stock_levels = {p.id: branch_stock(db, p.id, b.id) for p in products}
        stock_value = sum(stock_levels[p.id] * p.selling_price for p in products)
        low_stock_count = sum(1 for p in products if stock_levels[p.id] <= p.reorder_level)
        rows.append({
            "branch_id": b.id,
            "branch_name": b.name,
            "sales_total": float(sales_total),
            "sales_count": int(sales_count),
            "stock_value": stock_value,
            "low_stock_count": low_stock_count,
        })
    return rows


@app.get("/api/v1/reports/expenses-summary")
def expenses_summary(
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(require_roles(*CAN_MANAGE_ACCOUNTING))] = None,
) -> dict:
    rows = db.execute(
        select(Expense.category, Expense.status, func.count(Expense.id), func.coalesce(func.sum(Expense.amount), 0.0))
        .group_by(Expense.category, Expense.status)
        .order_by(Expense.category)
    ).all()
    by_category = [{"category": category, "status": status_, "count": int(count), "total": float(total)} for category, status_, count, total in rows]
    return {
        "by_category": by_category,
        "total_approved": sum(row["total"] for row in by_category if row["status"] == "approved"),
        "total_pending": sum(row["total"] for row in by_category if row["status"] == "pending"),
    }


@app.get("/api/v1/reports/approvals-summary")
def approvals_summary(
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(require_roles(*CAN_VIEW_ANALYTICS))] = None,
) -> dict:
    escalate_overdue_approvals(db)
    approvals = db.scalars(select(Approval)).all()
    pending = [a for a in approvals if a.status == "pending"]
    resolved = [a for a in approvals if a.status in ("approved", "rejected")]
    overdue = [a for a in pending if a.deadline and as_aware_utc(a.deadline) < datetime.now(timezone.utc)]
    resolution_hours = [(as_aware_utc(a.updated_at) - as_aware_utc(a.created_at)).total_seconds() / 3600 for a in resolved]
    return {
        "pending_count": len(pending),
        "overdue_count": len(overdue),
        "pending_by_type": dict(Counter(a.type for a in pending)),
        "avg_resolution_hours": (sum(resolution_hours) / len(resolution_hours)) if resolution_hours else None,
    }


REPLENISHMENT_WINDOW_DAYS = 30
REPLENISHMENT_TARGET_COVER_DAYS = 30
REPLENISHMENT_URGENT_COVER_DAYS = 7


@app.get("/api/v1/reports/replenishment-suggestions")
def replenishment_suggestions(
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(require_roles(*CAN_REQUEST_PURCHASE))] = None,
) -> list[dict]:
    """Phase 10 foundation: a deterministic, fully-explainable heuristic —
    trailing sales velocity projected against current stock — not machine
    learning. PHASED_PLAN.md deliberately defers real demand
    forecasting/ML until Phases 1-9 have produced enough real production
    data to validate a model against; this gives Owner/Store Keeper/Shop
    Manager an actionable reorder signal in the meantime, upgradeable to a
    real model later without changing the response shape callers depend on.
    """
    window_start = datetime.now(timezone.utc) - timedelta(days=REPLENISHMENT_WINDOW_DAYS)
    products = db.scalars(select(Product).order_by(Product.name)).all()
    branches = db.scalars(select(Branch).order_by(Branch.kind)).all()
    suggestions = []
    for product in products:
        for branch in branches:
            current_stock = branch_stock(db, product.id, branch.id)
            sold = int(db.scalar(select(func.coalesce(func.sum(Sale.quantity), 0)).where(Sale.product_id == product.id, Sale.branch_id == branch.id, Sale.created_at >= window_start)) or 0)
            velocity_per_day = sold / REPLENISHMENT_WINDOW_DAYS
            days_of_cover = (current_stock / velocity_per_day) if velocity_per_day > 0 else None
            needs_reorder = current_stock <= product.reorder_level
            urgent = needs_reorder or (days_of_cover is not None and days_of_cover < REPLENISHMENT_URGENT_COVER_DAYS)
            if velocity_per_day > 0:
                suggested_quantity = max(0, math.ceil(velocity_per_day * REPLENISHMENT_TARGET_COVER_DAYS - current_stock))
            elif needs_reorder:
                suggested_quantity = max(0, product.reorder_level * 2 - current_stock)
            else:
                suggested_quantity = 0
            suggestions.append({
                "product_id": product.id,
                "product_name": product.name,
                "branch_id": branch.id,
                "branch_name": branch.name,
                "current_stock": current_stock,
                "reorder_level": product.reorder_level,
                "sales_last_30_days": sold,
                "velocity_per_day": round(velocity_per_day, 3),
                "days_of_cover": round(days_of_cover, 1) if days_of_cover is not None else None,
                "needs_reorder": needs_reorder,
                "urgent": urgent,
                "suggested_reorder_quantity": suggested_quantity,
            })
    return suggestions


def serialize(record: object) -> dict:
    result = {column.name: getattr(record, column.name) for column in record.__table__.columns if column.name != "hashed_password"}
    for key, value in result.items():
        if isinstance(value, datetime): result[key] = value.isoformat()
    return result
