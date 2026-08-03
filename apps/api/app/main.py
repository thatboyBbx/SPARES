"""Database-backed API for the SparePilot operating MVP."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import create_access_token, create_refresh_token, get_current_user, hash_password, require_roles, revoke_refresh_token, rotate_refresh_token, verify_and_upgrade_password
from app.core.config import get_settings
from app.core.permissions import CAN_APPROVE, CAN_FULFIL_TRANSFER, CAN_MANAGE_STOCK_RECEIPTS, CAN_MANAGE_USERS, CAN_RECEIVE_TRANSFER, CAN_RECORD_SALE, CAN_REQUEST_TRANSFER
from app.db.base import Base, SyncStatus
from app.db.session import engine, get_db
from app.models import Approval, Branch, Category, Customer, Device, Expense, Notification, Product, PurchaseOrder, PurchaseOrderLine, PurchaseReceipt, Sale, StockMovement, Supplier, Transfer, User, UserRole
from app.seed import seed_database

# Sentinel for mixin-backed rows written by a plain HTTP request rather than
# replayed from a device's offline outbox (real per-write device attribution
# arrives with the Phase 3 sync plumbing).
SERVER_DEVICE_ID = "server"

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
    client_request_id: str | None = None


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
    return {"users": [serialize(u) for u in db.scalars(select(User)).all()], "products": [serialize(p) for p in products], "categories": [serialize(c) for c in db.scalars(select(Category).order_by(Category.name)).all()], "branches": [serialize(b) for b in branches], "suppliers": [serialize(s) for s in db.scalars(select(Supplier)).all()], "customers": [serialize(c) for c in db.scalars(select(Customer).order_by(Customer.name)).all()], "movements": [serialize(m) for m in movements], "transfers": [serialize(t) for t in db.scalars(select(Transfer).order_by(Transfer.created_at.desc())).all()], "sales": [serialize(s) for s in db.scalars(select(Sale).order_by(Sale.created_at.desc())).all()], "purchase_orders": [serialize(o) for o in db.scalars(select(PurchaseOrder).order_by(PurchaseOrder.requested_at.desc())).all()], "purchase_order_lines": [serialize(l) for l in db.scalars(select(PurchaseOrderLine).order_by(PurchaseOrderLine.created_at.desc())).all()], "approvals": [serialize(a) for a in db.scalars(select(Approval).order_by(Approval.created_at.desc())).all()], "expenses": [serialize(e) for e in db.scalars(select(Expense).order_by(Expense.created_at.desc())).all()], "notifications": [serialize(n) for n in db.scalars(select(Notification).order_by(Notification.created_at.desc())).all()]}

@app.post("/api/v1/auth/login")
def login(payload: LoginIn, db: Session = Depends(get_db)) -> dict:
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
    db.add(Notification(title="Purchase order requested", body=f"{order_reference} was created for {len(payload.items)} items.", kind="purchase_order"))
    db.commit()
    return {"id": order.id, "reference": order_reference, "status": order.status, "version": order.version, "message": "Purchase order requested"}


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

    lines = db.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.purchase_order_id == purchase_order_id)).all()
    if not lines:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Purchase order has no lines")

    for line in lines:
        require(db, Product, line.product_id)
        require(db, Branch, order.branch_id)
        receipt_reference = reference("GRN")
        db.add(PurchaseReceipt(reference=receipt_reference, supplier_id=order.supplier_id, branch_id=order.branch_id, product_id=line.product_id, quantity=line.quantity, idempotency_key=None, device_id=SERVER_DEVICE_ID, created_by_id=current_user.id))
        db.add(StockMovement(product_id=line.product_id, branch_id=order.branch_id, quantity=line.quantity, kind="receipt", reference=receipt_reference, created_by=current_user.full_name, device_id=SERVER_DEVICE_ID, created_by_id=current_user.id))

    order.status = "received"
    order.received_at = datetime.now(timezone.utc)
    db.add(Notification(title="Goods received", body=f"{order.reference} completed and posted to stock.", kind="purchase_order"))
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
    db.add(Notification(title="Transfer requested", body=f"{ref} awaits fulfilment from the source branch.", kind="transfer"))
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
    transfer.status = "in_transit"
    transfer.fulfilled_by_id = current_user.id
    transfer.fulfilled_at = datetime.now(timezone.utc)
    db.add(Notification(title="Transfer in transit", body=f"{transfer.reference} left the source branch.", kind="transfer"))
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
    db.add(Notification(title="Transfer received", body=f"{transfer.reference} is available at the destination branch.", kind="transfer"))
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
    db.add(Sale(receipt_number=ref, product_id=payload.product_id, branch_id=payload.branch_id, customer_id=payload.customer_id, quantity=payload.quantity, total=total, idempotency_key=payload.client_request_id, device_id=SERVER_DEVICE_ID, created_by_id=current_user.id)); db.add(StockMovement(product_id=payload.product_id, branch_id=payload.branch_id, quantity=-payload.quantity, kind="sale", reference=ref, created_by=current_user.full_name, device_id=SERVER_DEVICE_ID, created_by_id=current_user.id)); db.commit()
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
    approvals = db.scalars(select(Approval).order_by(Approval.created_at.desc())).all()
    return [serialize(approval) for approval in approvals]


@app.post("/api/v1/approvals", status_code=201)
def create_approval(
    payload: ApprovalIn,
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(require_roles(*CAN_APPROVE))] = None,
) -> dict:
    if payload.client_request_id:
        existing = db.scalar(select(Approval).where(Approval.idempotency_key == payload.client_request_id))
        if existing: return serialize(existing)
    fields = payload.model_dump(exclude={"client_request_id"})
    approval = Approval(**fields, idempotency_key=payload.client_request_id)
    db.add(approval); db.add(Notification(title="Approval requested", body=payload.subject, kind="approval")); db.commit(); return serialize(approval)


@app.post("/api/v1/approvals/{approval_id}/approve")
def approve(
    approval_id: str,
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(require_roles(*CAN_APPROVE))] = None,
) -> dict:
    approval = require(db, Approval, approval_id); approval.status = "approved"; db.commit(); return serialize(approval)


@app.post("/api/v1/approvals/{approval_id}/reject")
def reject_approval(
    approval_id: str,
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(require_roles(*CAN_APPROVE))] = None,
) -> dict:
    approval = require(db, Approval, approval_id); approval.status = "rejected"; db.commit(); return serialize(approval)


@app.post("/api/v1/expenses", status_code=201)
def create_expense(
    payload: ExpenseIn,
    db: Session = Depends(get_db),
    _: Annotated[User, Depends(require_roles(*CAN_APPROVE))] = None,
) -> dict:
    if payload.client_request_id:
        existing = db.scalar(select(Expense).where(Expense.idempotency_key == payload.client_request_id))
        if existing: return serialize(existing)
    require(db, Branch, payload.branch_id)
    fields = payload.model_dump(exclude={"client_request_id"})
    expense = Expense(**fields, idempotency_key=payload.client_request_id)
    db.add(expense); db.add(Notification(title="Expense awaiting approval", body=payload.description, kind="expense")); db.commit(); return serialize(expense)


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