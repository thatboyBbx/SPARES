"""Core operational tables for the SPOP MVP.

Stock is never held as an editable balance: every quantity is derived from
the append-only ``stock_movements`` ledger.
"""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, SyncedEntityMixin


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Branch(Base):
    __tablename__ = "branches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(120), unique=True)
    kind: Mapped[str] = mapped_column(String(20))  # warehouse | shop
    address: Mapped[str] = mapped_column(String(255), default="")


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(120), unique=True)
    phone: Mapped[str] = mapped_column(String(50), default="")


class Category(SyncedEntityMixin, Base):
    __tablename__ = "categories"
    name: Mapped[str] = mapped_column(String(120))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("categories.id"), nullable=True)


class Product(SyncedEntityMixin, Base):
    __tablename__ = "products"
    sku: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    brand: Mapped[str] = mapped_column(String(100))
    fitment: Mapped[str] = mapped_column(Text)
    reorder_level: Mapped[int] = mapped_column(Integer, default=0)
    selling_price: Mapped[float] = mapped_column(Float)
    category_id: Mapped[str | None] = mapped_column(ForeignKey("categories.id"), nullable=True, index=True)


class StockMovement(SyncedEntityMixin, Base):
    __tablename__ = "stock_movements"
    # Real-time SUM() over the ledger is used for balances rather than a
    # materialized quantity column, to keep the ledger the single source of
    # truth; this composite index keeps that aggregation cheap per branch.
    __table_args__ = (Index("ix_stock_movements_product_branch", "product_id", "branch_id"),)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(30))  # receipt, sale, transfer_out, transfer_in, adjustment
    reference: Mapped[str] = mapped_column(String(80), index=True)
    created_by: Mapped[str] = mapped_column(String(120))
    # Overrides the mixin's plain created_at to keep the index the ledger's
    # time-ordered reads (bootstrap, reports) already rely on.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class PurchaseReceipt(SyncedEntityMixin, Base):
    __tablename__ = "purchase_receipts"
    reference: Mapped[str] = mapped_column(String(80), unique=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"))
    branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id"))
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PurchaseOrder(SyncedEntityMixin, Base):
    """Three-step workflow: requested -> approved -> received."""
    __tablename__ = "purchase_orders"
    reference: Mapped[str] = mapped_column(String(80), unique=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"), index=True)
    branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="requested")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(120), default="System")
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    approved_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PurchaseOrderLine(SyncedEntityMixin, Base):
    __tablename__ = "purchase_order_lines"
    purchase_order_id: Mapped[str] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    unit_cost: Mapped[float] = mapped_column(Float, default=0.0)


class Transfer(SyncedEntityMixin, Base):
    """Two-step workflow: requested -> in_transit -> received (or cancelled
    before fulfilment). `created_by_id` (from the mixin) is the requester;
    `created_at` is the request time."""
    __tablename__ = "transfers"
    reference: Mapped[str] = mapped_column(String(80), unique=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    from_branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id"))
    to_branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="requested")
    fulfilled_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    received_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Customer(SyncedEntityMixin, Base):
    __tablename__ = "customers"
    name: Mapped[str] = mapped_column(String(160))
    phone: Mapped[str] = mapped_column(String(50), default="")


class Sale(SyncedEntityMixin, Base):
    __tablename__ = "sales"
    receipt_number: Mapped[str] = mapped_column(String(80), unique=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id"))
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    total: Mapped[float] = mapped_column(Float)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)


class Approval(SyncedEntityMixin, Base):
    """`created_by_id` (from the mixin) is the requester; `approver_id` is
    who resolved it. `related_entity_type`/`related_entity_id` link this
    approval to the record it gates (e.g. an Expense or PurchaseOrder) so
    dispatch_approval_decision() can flip both statuses together."""
    __tablename__ = "approvals"
    type: Mapped[str] = mapped_column(String(50))
    subject: Mapped[str] = mapped_column(String(200))
    requester: Mapped[str] = mapped_column(String(120))
    approver: Mapped[str] = mapped_column(String(120))
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    related_entity_type: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    related_entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    approver_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class Expense(SyncedEntityMixin, Base):
    __tablename__ = "expenses"
    branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id"))
    category: Mapped[str] = mapped_column(String(80))
    amount: Mapped[float] = mapped_column(Float)
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(40))
    read: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
