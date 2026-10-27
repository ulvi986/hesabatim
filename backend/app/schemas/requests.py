"""Request bodies (validated at the API boundary)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Unit = Literal["piece", "m2", "m", "m3", "kg", "ton", "l", "pack", "set"]
Currency = Literal["AZN", "USD", "EUR", "TRY", "RUB", "GBP"]
Money = Decimal
MAX_MONEY = Decimal("1e10")


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RegisterIn(Body):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=200)
    business_name: str = Field(min_length=2, max_length=200)
    currency: Currency = "AZN"


class LoginIn(Body):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class PasswordChangeIn(Body):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class ProfileUpdateIn(Body):
    full_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    locale: Literal["az", "en"] | None = None


class ThemeIn(Body):
    primary: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    accent: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    sidebar: Literal["light", "tinted", "dark"] | None = None
    card: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")  # loyalty card colour; default: primary


class BusinessUpdateIn(Body):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    currency: Currency | None = None
    timezone: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=300)
    onboarding_completed: bool | None = None
    theme: ThemeIn | None = None
    loyalty_enabled: bool | None = None
    loyalty_stamps_needed: int | None = Field(default=None, ge=2, le=30)
    loyalty_reward: str | None = Field(default=None, min_length=2, max_length=120)


class NotificationSettingsIn(Body):
    low_stock: bool | None = None
    low_stock_threshold: int | None = Field(default=None, ge=0, le=1_000_000)
    use_product_thresholds: bool | None = None  # true clears low_stock_threshold
    out_of_stock: bool | None = None
    reward_ready: bool | None = None
    birthdays: bool | None = None
    birthday_days: int | None = Field(default=None, ge=0, le=60)
    new_customers: bool | None = None


class CategoryIn(Body):
    name: str = Field(min_length=2, max_length=120)


class ProductCreateIn(Body):
    name: str = Field(min_length=2, max_length=250)
    sku: str | None = Field(default=None, max_length=64)
    category_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=5000)
    unit: Unit = "piece"
    purchase_price: Money | None = Field(default=None, ge=0, le=MAX_MONEY)
    selling_price: Money | None = Field(default=None, ge=0, le=MAX_MONEY)
    low_stock_threshold: int = Field(default=10, ge=0, le=10_000_000)
    quantity: Decimal = Field(default=Decimal(0), ge=0, le=MAX_MONEY)
    aliases: list[str] = Field(default_factory=list, max_length=20)


class ProductUpdateIn(Body):
    name: str | None = Field(default=None, min_length=2, max_length=250)
    sku: str | None = Field(default=None, max_length=64)
    category_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=5000)
    unit: Unit | None = None
    purchase_price: Money | None = Field(default=None, ge=0, le=MAX_MONEY)
    selling_price: Money | None = Field(default=None, ge=0, le=MAX_MONEY)
    low_stock_threshold: int | None = Field(default=None, ge=0, le=10_000_000)


class BulkIdsIn(Body):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    archived: bool = True


class StockAdjustIn(Body):
    delta: Decimal | None = Field(default=None, ge=-MAX_MONEY, le=MAX_MONEY)
    set_quantity: Decimal | None = Field(default=None, ge=0, le=MAX_MONEY)
    reason: str | None = Field(default=None, max_length=500)
    expected_version: int | None = None


class SaleLineIn(Body):
    product_id: uuid.UUID
    quantity: Decimal = Field(gt=0, le=MAX_MONEY)
    unit_price: Money | None = Field(default=None, ge=0, le=MAX_MONEY, description="Defaults to the product's selling price")


class SaleIn(Body):
    items: list[SaleLineIn] = Field(min_length=1, max_length=100)
    customer_name: str | None = Field(default=None, max_length=200)
    customer_id: uuid.UUID | None = None
    loyalty_stamp: bool = False
    note: str | None = Field(default=None, max_length=1000)
    sold_at: datetime | None = None


class CustomerIn(Body):
    name: str = Field(min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    email: EmailStr | None = None
    birthday: date | None = None
    gender: Literal["male", "female", "other"] | None = None
    company: str | None = Field(default=None, max_length=200)
    address: str | None = Field(default=None, max_length=300)
    tags: list[str] = Field(default_factory=list, max_length=20)
    note: str | None = Field(default=None, max_length=5000)


class CustomerUpdateIn(Body):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    email: EmailStr | None = None
    birthday: date | None = None
    gender: Literal["male", "female", "other"] | None = None
    company: str | None = Field(default=None, max_length=200)
    address: str | None = Field(default=None, max_length=300)
    tags: list[str] | None = Field(default=None, max_length=20)
    note: str | None = Field(default=None, max_length=5000)
    is_archived: bool | None = None


class PublicSignupIn(Body):
    """Self-registration from the customer-facing loyalty card site (SadakatKarti)."""
    name: str = Field(min_length=1, max_length=60)
    phone: str = Field(min_length=7, max_length=40)


class StampIn(Body):
    count: int = Field(default=1, ge=1, le=30)
    force: bool = False


class StampAdjustIn(Body):
    stamps: int = Field(ge=0, le=30)
    note: str | None = Field(default=None, max_length=300)


class ReturnLineIn(Body):
    sale_item_id: uuid.UUID
    quantity: Decimal = Field(gt=0, le=MAX_MONEY)


class ReturnIn(Body):
    items: list[ReturnLineIn] = Field(min_length=1, max_length=100)
    reason: str | None = Field(default=None, max_length=1000)


class CancelIn(Body):
    reason: str | None = Field(default=None, max_length=1000)


class ConversationIn(Body):
    business_id: uuid.UUID
    title: str | None = Field(default=None, max_length=200)


class ChatMessageIn(Body):
    content: str = Field(min_length=1, max_length=4000)


class ConfirmActionIn(Body):
    overrides: dict[str, Any] | None = None
