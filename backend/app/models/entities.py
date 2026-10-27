"""Database schema. Money uses NUMERIC(14,2), quantities NUMERIC(14,3).

Every business record (products, inventory, sales, events, conversations) is scoped by
business_id. Stock is never overwritten silently: each change is a row in inventory_events, and
sales are never deleted: a cancellation or return is a separate, linked sale record of kind "return".
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPk, utcnow

MONEY = Numeric(14, 2)
QTY = Numeric(14, 3)


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


MEMBER_ROLES = ("owner", "admin", "staff")
CURRENCIES = ("AZN", "USD", "EUR", "TRY", "RUB", "GBP")
UNITS = ("piece", "m2", "m", "m3", "kg", "ton", "l", "pack", "set")
EVENT_TYPES = ("initial", "increase", "decrease", "set", "sale", "return", "import", "undo")
EVENT_SOURCES = ("manual", "ai", "import", "sale", "undo", "system")
AI_ACTION_STATUSES = ("pending", "executed", "rejected", "failed")
SALE_KINDS = ("sale", "return")
SALE_STATUSES = ("completed", "partially_returned", "returned")
SALE_SOURCES = ("manual", "ai", "import")
LOYALTY_EVENT_TYPES = ("stamp", "redeem", "adjust")


# ---------------------------------------------------------------------------
# Identity & tenancy
# ---------------------------------------------------------------------------
class Profile(Base, Timestamps):
    """One row per user account."""

    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(40))
    locale: Mapped[str] = mapped_column(String(8), default="az")
    # Bumped on password change / "sign out everywhere"; tokens carrying an older value are rejected.
    token_version: Mapped[int] = mapped_column(Integer, default=1)

    memberships: Mapped[list[BusinessMember]] = relationship(back_populates="user", cascade="all, delete-orphan")


class LocalCredential(Base):
    __tablename__ = "local_credentials"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Business(Base, UUIDPk, Timestamps):
    __tablename__ = "businesses"
    __table_args__ = (CheckConstraint(_in("currency", CURRENCIES), name="currency"),)

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(220), unique=True, index=True)
    currency: Mapped[str] = mapped_column(String(3), default="AZN")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Baku")  # day boundaries for reports
    phone: Mapped[str | None] = mapped_column(String(40))
    address: Mapped[str | None] = mapped_column(String(300))
    # Set once the user has finished (or skipped) the first conversation with the assistant.
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    # Branding: the logo is a small data: URL (PNG/JPEG/WebP), theme holds the chosen colours.
    logo: Mapped[str | None] = mapped_column(Text)
    theme: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # {"settings": {...}, "seen": [notification keys]} -- see app/services/notifications.py
    notifications: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # Loyalty card: N stamps earn one reward ("9 coffees, the 10th is free").
    loyalty_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    loyalty_stamps_needed: Mapped[int] = mapped_column(Integer, default=9, server_default="9")
    loyalty_reward: Mapped[str] = mapped_column(String(120), default="Pulsuz məhsul", server_default="Pulsuz məhsul")

    members: Mapped[list[BusinessMember]] = relationship(back_populates="business", cascade="all, delete-orphan")


class BusinessMember(Base, UUIDPk):
    __tablename__ = "business_members"
    __table_args__ = (
        UniqueConstraint("business_id", "user_id", name="business_user"),
        CheckConstraint(_in("role", MEMBER_ROLES), name="role"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="owner")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    business: Mapped[Business] = relationship(back_populates="members")
    user: Mapped[Profile] = relationship(back_populates="memberships")


# ---------------------------------------------------------------------------
# Catalogue & inventory
# ---------------------------------------------------------------------------
class Category(Base, UUIDPk):
    """User-defined product category, private to one business."""

    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("business_id", "name_key", name="business_name"),)

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    name_key: Mapped[str] = mapped_column(String(120))  # folded name, for case/diacritic-insensitive uniqueness
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Product(Base, UUIDPk, Timestamps):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("business_id", "sku", name="business_sku"),
        CheckConstraint(_in("unit", UNITS), name="unit"),
        CheckConstraint("purchase_price IS NULL OR purchase_price >= 0", name="purchase_price_nonneg"),
        CheckConstraint("selling_price IS NULL OR selling_price >= 0", name="selling_price_nonneg"),
        CheckConstraint("low_stock_threshold >= 0", name="threshold_nonneg"),
        Index("ix_products_business_archived", "business_id", "is_archived"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("categories.id", ondelete="SET NULL"), index=True)
    sku: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(250))
    description: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(10), default="piece")
    purchase_price: Mapped[Decimal | None] = mapped_column(MONEY)
    selling_price: Mapped[Decimal | None] = mapped_column(MONEY)
    low_stock_threshold: Mapped[int] = mapped_column(Integer, default=10)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    search_text: Mapped[str] = mapped_column(Text, default="")

    category: Mapped[Category | None] = relationship()
    inventory: Mapped[Inventory | None] = relationship(back_populates="product", uselist=False)


class ProductAlias(Base, UUIDPk):
    """Alternative names / barcodes used to match free-text messages to existing products."""

    __tablename__ = "product_aliases"
    __table_args__ = (UniqueConstraint("business_id", "code", name="business_code"),)

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Inventory(Base, UUIDPk):
    """Current stock of one product. Changed only through services.inventory.adjust_stock."""

    __tablename__ = "inventory"
    __table_args__ = (
        UniqueConstraint("product_id", name="product"),
        CheckConstraint("quantity >= 0", name="quantity_nonneg"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id", ondelete="CASCADE"))
    quantity: Mapped[Decimal] = mapped_column(QTY, default=Decimal("0"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    product: Mapped[Product] = relationship(back_populates="inventory")


class InventoryEvent(Base, UUIDPk):
    """Append-only ledger of every stock change."""

    __tablename__ = "inventory_events"
    __table_args__ = (
        CheckConstraint(_in("event_type", EVENT_TYPES), name="event_type"),
        CheckConstraint(_in("source", EVENT_SOURCES), name="source"),
        CheckConstraint("quantity_after >= 0", name="after_nonneg"),
        UniqueConstraint("business_id", "idempotency_key", name="inventory_events_business_idem"),
        Index("ix_inventory_events_product_created", "product_id", "created_at"),
        Index("ix_inventory_events_business_created", "business_id", "created_at"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="CASCADE"))
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id", ondelete="CASCADE"))
    inventory_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("inventory.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(20), default="manual")
    delta: Mapped[Decimal] = mapped_column(QTY)
    quantity_before: Mapped[Decimal] = mapped_column(QTY)
    quantity_after: Mapped[Decimal] = mapped_column(QTY)
    reason: Mapped[str | None] = mapped_column(String(500))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("profiles.id", ondelete="SET NULL"))
    ai_action_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("ai_actions.id", ondelete="SET NULL"))
    sale_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("sales.id", ondelete="SET NULL"), index=True)
    reverts_event_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("inventory_events.id"))
    reverted: Mapped[bool] = mapped_column(Boolean, default=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------------------
# Customers & loyalty
# ---------------------------------------------------------------------------
class Customer(Base, UUIDPk, Timestamps):
    """A customer of one business, with a QR loyalty card identified by card_token."""

    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("business_id", "phone", name="customers_business_phone"),
        UniqueConstraint("card_token", name="customers_card_token"),
        CheckConstraint("stamps >= 0", name="stamps_nonneg"),
        Index("ix_customers_business_name", "business_id", "name"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(320))
    birthday: Mapped[date | None] = mapped_column(Date)
    gender: Mapped[str | None] = mapped_column(String(10))
    company: Mapped[str | None] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(String(300))
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    note: Mapped[str | None] = mapped_column(Text)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    card_token: Mapped[str] = mapped_column(String(40))
    stamps: Mapped[int] = mapped_column(Integer, default=0)  # stamps on the current card
    total_stamps: Mapped[int] = mapped_column(Integer, default=0)
    rewards: Mapped[int] = mapped_column(Integer, default=0)
    last_stamp_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoyaltyEvent(Base, UUIDPk):
    """Append-only history of a customer's loyalty card."""

    __tablename__ = "loyalty_events"
    __table_args__ = (
        CheckConstraint(_in("event_type", LOYALTY_EVENT_TYPES), name="event_type"),
        Index("ix_loyalty_events_customer_created", "customer_id", "created_at"),
        Index("ix_loyalty_events_business_created", "business_id", "created_at"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="CASCADE"))
    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("customers.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(10))
    amount: Mapped[int] = mapped_column(Integer, default=1)
    stamps_after: Mapped[int] = mapped_column(Integer, default=0)
    sale_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("sales.id", ondelete="SET NULL"))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("profiles.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------------
class Sale(Base, UUIDPk):
    """A sale, or a return that reverses (part of) an earlier sale.

    Totals are stored positive; `kind` gives the sign for analytics. Cost totals are a snapshot of
    the purchase price at the time of sale and are NULL when no purchase price was recorded."""

    __tablename__ = "sales"
    __table_args__ = (
        CheckConstraint(_in("kind", SALE_KINDS), name="kind"),
        CheckConstraint(_in("status", SALE_STATUSES), name="status"),
        CheckConstraint(_in("source", SALE_SOURCES), name="source"),
        CheckConstraint("total >= 0", name="total_nonneg"),
        CheckConstraint("kind = 'sale' OR original_sale_id IS NOT NULL", name="return_has_original"),
        UniqueConstraint("business_id", "number", name="business_number"),
        UniqueConstraint("business_id", "idempotency_key", name="sales_business_idem"),
        Index("ix_sales_business_sold_at", "business_id", "sold_at"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="CASCADE"))
    number: Mapped[str] = mapped_column(String(30))
    kind: Mapped[str] = mapped_column(String(10), default="sale")
    status: Mapped[str] = mapped_column(String(20), default="completed")
    original_sale_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("sales.id", ondelete="RESTRICT"), index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("customers.id", ondelete="SET NULL"), index=True)
    customer_name: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(String(1000))
    currency: Mapped[str] = mapped_column(String(3), default="AZN")
    total: Mapped[Decimal] = mapped_column(MONEY)
    cost_total: Mapped[Decimal | None] = mapped_column(MONEY)  # NULL if any line lacks a recorded cost
    source: Mapped[str] = mapped_column(String(10), default="manual")
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("profiles.id", ondelete="SET NULL"))
    ai_action_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("ai_actions.id", ondelete="SET NULL"))
    idempotency_key: Mapped[str | None] = mapped_column(String(120))
    sold_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    items: Mapped[list[SaleItem]] = relationship(back_populates="sale", cascade="all, delete-orphan",
                                                 order_by="SaleItem.position")
    original: Mapped[Sale | None] = relationship(remote_side="Sale.id")


class SaleItem(Base, UUIDPk):
    __tablename__ = "sale_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_nonneg"),
        CheckConstraint("unit_cost IS NULL OR unit_cost >= 0", name="unit_cost_nonneg"),
        Index("ix_sale_items_product", "product_id"),
    )

    sale_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("sales.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id", ondelete="RESTRICT"))
    original_item_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("sale_items.id", ondelete="RESTRICT"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(250))  # snapshot, so renaming a product does not rewrite history
    unit: Mapped[str] = mapped_column(String(10))
    quantity: Mapped[Decimal] = mapped_column(QTY)
    unit_price: Mapped[Decimal] = mapped_column(MONEY)
    unit_cost: Mapped[Decimal | None] = mapped_column(MONEY)
    line_total: Mapped[Decimal] = mapped_column(MONEY)
    line_cost: Mapped[Decimal | None] = mapped_column(MONEY)

    sale: Mapped[Sale] = relationship(back_populates="items")


# ---------------------------------------------------------------------------
# AI
# ---------------------------------------------------------------------------
class Conversation(Base, UUIDPk, Timestamps):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_user_updated", "user_id", "updated_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("profiles.id", ondelete="CASCADE"))
    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="Yeni söhbət")


class Message(Base, UUIDPk):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(_in("role", ("user", "assistant", "tool")), name="role"),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
        Index("ix_messages_user_created", "user_id", "created_at"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("conversations.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("profiles.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(10))
    content: Mapped[str] = mapped_column(Text, default="")
    blocks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)  # structured UI blocks
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AIAction(Base, UUIDPk):
    """A write operation proposed by the assistant. Executed only after explicit confirmation."""

    __tablename__ = "ai_actions"
    __table_args__ = (CheckConstraint(_in("status", AI_ACTION_STATUSES), name="status"),)

    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("messages.id", ondelete="SET NULL"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("profiles.id", ondelete="CASCADE"))
    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    tool_name: Mapped[str] = mapped_column(String(60))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON)
    preview: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(10), default="pending")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# Audit & idempotency
# ---------------------------------------------------------------------------
class AuditLog(Base, UUIDPk):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_business_created", "business_id", "created_at"),)

    business_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="CASCADE"))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("profiles.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(60))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    via: Mapped[str] = mapped_column(String(20), default="api")  # api | ai | import | system
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IdempotencyRecord(Base, UUIDPk):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("user_id", "key", name="user_key"),)

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("profiles.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(120))
    scope: Mapped[str] = mapped_column(String(120))
    response: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
