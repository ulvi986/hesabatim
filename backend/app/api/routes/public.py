"""Unauthenticated endpoints. The loyalty card token is the only credential; the response holds no phone
number or purchase history, only what is printed on the card itself.

The customer-facing card site (SadakatKarti) registers customers here by business slug, so every card it issues
is an ordinary row in `customers` that the business reads and stamps in Hesabatım."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DB, auth_rate_limit
from app.core.errors import NotFound, ValidationFailed
from app.models import Business, Customer
from app.schemas.requests import PublicSignupIn
from app.services import customers

router = APIRouter(prefix="/public", tags=["public"])

Slug = Path(min_length=1, max_length=220, pattern=r"^[a-z0-9-]+$")


def _business(db: DB, slug: str) -> Business:
    b = db.scalar(select(Business).where(Business.slug == slug))
    if b is None:
        raise NotFound("Biznes tapılmadı")
    return b


@router.get("/cards/{token}")
def public_card(db: DB, token: str = Path(min_length=8, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")):
    c = customers.get_by_token(db, token)
    return customers.public_card(c, db.get(Business, c.business_id))


@router.get("/businesses/{slug}")
def public_business(db: DB, slug: str = Slug):
    b = _business(db, slug)
    return {"name": b.name, "logo": b.logo, "theme": b.theme or {}, "loyalty_enabled": b.loyalty_enabled,
            "stamps_needed": b.loyalty_stamps_needed, "reward": b.loyalty_reward}


@router.post("/businesses/{slug}/cards", dependencies=[Depends(auth_rate_limit)])
def signup(body: PublicSignupIn, db: DB, slug: str = Slug):
    """Creates the customer's card, or returns the existing one for the same phone (a lost/changed phone gets the
    same card back). Rate limited like login, since a phone number is enough to recover a card."""
    b = _business(db, slug)
    if not b.loyalty_enabled:
        raise ValidationFailed("Sadiqlik kartı bu biznes üçün söndürülüb", code="loyalty_disabled")
    phone = customers.normalize_phone(body.phone)
    existing = db.scalar(select(Customer).where(Customer.business_id == b.id, Customer.phone == phone))
    if existing is None:
        try:
            existing = customers.create_customer(db, actor_id=None, business_id=b.id,
                                                 data={"name": body.name, "phone": body.phone, "tags": ["QR qeydiyyat"]})
            db.commit()
        except IntegrityError:  # the same phone registered concurrently
            db.rollback()
            existing = db.scalar(select(Customer).where(Customer.business_id == b.id, Customer.phone == phone))
            if existing is None:
                raise
    elif existing.is_archived:  # a returning customer the business had archived
        customers.update_customer(db, actor_id=None, customer=existing, data={"is_archived": False})
        db.commit()
    return customers.public_card(existing, b)
