"""Customers and their QR loyalty cards (idea from the "sadakatkarti" stamp card: N stamps earn a reward).

* Every customer has a card_token; the QR code on the card encodes the public card URL with that token.
* Stamps and rewards are counters on the customer row; every change is also written to loyalty_events.
* A repeated scan within COOLDOWN_SECONDS is refused unless forced, so one purchase is not stamped twice.
* Purchase statistics are computed from linked sales (returns are subtracted), never stored.

Mutating functions flush but never commit; callers own the transaction.
"""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.models import Business, Customer, LoyaltyEvent, Sale, SaleItem
from app.services.common import audit, aware, iso, money_str, qty_str, utc_now

COOLDOWN_SECONDS = 30
EDITABLE_FIELDS = {"name", "phone", "email", "birthday", "gender", "company", "address", "tags", "note"}


def normalize_phone(value: str | None) -> str | None:
    digits = re.sub(r"[^\d+]", "", value or "")
    return digits or None


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in data.items():
        if k not in EDITABLE_FIELDS:
            continue
        if isinstance(v, str):
            v = v.strip() or None
        if k == "phone":
            v = normalize_phone(v)
            if v is not None and len(v.lstrip("+")) < 7:
                raise ValidationFailed("Telefon nömrəsi düzgün deyil", details={"field": "phone"})
        if k == "email" and v:
            v = v.lower()
        if k == "tags":
            v = list(dict.fromkeys(t.strip()[:40] for t in (v or []) if t and t.strip()))[:20]
        out[k] = v
    if "name" in out and not out["name"]:
        raise ValidationFailed("Müştərinin adı boş ola bilməz", details={"field": "name"})
    return out


def _check_phone(db: Session, business_id: uuid.UUID, phone: str | None, exclude: uuid.UUID | None = None) -> None:
    if not phone:
        return
    stmt = select(Customer.id).where(Customer.business_id == business_id, Customer.phone == phone)
    if exclude:
        stmt = stmt.where(Customer.id != exclude)
    if db.scalar(stmt):
        raise Conflict("Bu telefon nömrəsi ilə müştəri artıq var", code="customer_phone_taken", details={"field": "phone"})


def create_customer(db: Session, *, actor_id: uuid.UUID | None, business_id: uuid.UUID, data: dict[str, Any]) -> Customer:
    data = _clean(data)
    if not data.get("name"):
        raise ValidationFailed("Müştərinin adı tələb olunur", details={"field": "name"})
    _check_phone(db, business_id, data.get("phone"))
    c = Customer(business_id=business_id, card_token=secrets.token_urlsafe(12), tags=data.pop("tags", None) or [], **data)
    db.add(c)
    db.flush()
    audit(db, business_id=business_id, actor_id=actor_id, action="customer.create", entity_type="customer",
          entity_id=c.id, after={"name": c.name, "phone": c.phone})
    return c


def update_customer(db: Session, *, actor_id: uuid.UUID | None, customer: Customer, data: dict[str, Any]) -> Customer:
    archived = data.pop("is_archived", None)
    data = _clean(data)
    if "phone" in data:
        _check_phone(db, customer.business_id, data["phone"], exclude=customer.id)
    before = {k: getattr(customer, k) for k in data}
    for k, v in data.items():
        setattr(customer, k, v if k != "tags" else (v or []))
    if archived is not None:
        before["is_archived"] = customer.is_archived
        customer.is_archived = archived
        data["is_archived"] = archived
    audit(db, business_id=customer.business_id, actor_id=actor_id, action="customer.update", entity_type="customer",
          entity_id=customer.id, before=before, after=data)
    db.flush()
    return customer


def get_customer(db: Session, business_id: uuid.UUID, customer_id: uuid.UUID, *, lock: bool = False) -> Customer:
    stmt = select(Customer).where(Customer.id == customer_id, Customer.business_id == business_id)
    if lock:
        stmt = stmt.with_for_update()
    c = db.scalar(stmt)
    if c is None:
        raise NotFound("Müştəri tapılmadı")
    return c


def get_by_token(db: Session, token: str, business_id: uuid.UUID | None = None) -> Customer:
    stmt = select(Customer).where(Customer.card_token == token.strip())
    if business_id:
        stmt = stmt.where(Customer.business_id == business_id)
    c = db.scalar(stmt)
    if c is None:
        raise NotFound("Kart tapılmadı")
    return c


def token_from_text(text: str) -> str:
    """Accepts the raw token or the full card URL read from a QR code (…/kart/<token> here, …/c/<token> on the
    SadakatKarti customer site)."""
    text = text.strip()
    m = re.search(r"/(?:kart|c)/([A-Za-z0-9_-]{8,40})", text)
    return m.group(1) if m else text


# ---------------------------------------------------------------------------
# Loyalty
# ---------------------------------------------------------------------------
def _event(db: Session, c: Customer, event_type: str, amount: int, *, actor_id, sale_id=None, note=None) -> None:
    db.add(LoyaltyEvent(business_id=c.business_id, customer_id=c.id, event_type=event_type, amount=amount,
                        stamps_after=c.stamps, sale_id=sale_id, actor_id=actor_id, note=note))


def add_stamps(db: Session, *, actor_id: uuid.UUID | None, business: Business, customer: Customer, count: int = 1,
               force: bool = False, sale_id: uuid.UUID | None = None) -> int:
    """Adds up to `count` stamps (never past the reward threshold). Returns the number actually added."""
    if not business.loyalty_enabled:
        raise ValidationFailed("Sadiqlik kartı bu biznes üçün söndürülüb", code="loyalty_disabled")
    needed = business.loyalty_stamps_needed
    if customer.stamps >= needed:
        raise Conflict(f"Hədiyyə hazırdır ({business.loyalty_reward}) — əvvəlcə onu verin", code="reward_ready")
    if not force and sale_id is None and customer.last_stamp_at is not None:
        ago = (utc_now() - aware(customer.last_stamp_at)).total_seconds()
        if ago < COOLDOWN_SECONDS:
            raise Conflict(f"Bu karta {int(ago)} saniyə əvvəl möhür vurulub", code="loyalty_cooldown",
                           details={"seconds_ago": int(ago)}, status_code=429)
    count = max(1, min(int(count or 1), needed - customer.stamps))
    customer.stamps += count
    customer.total_stamps += count
    customer.last_stamp_at = utc_now()
    _event(db, customer, "stamp", count, actor_id=actor_id, sale_id=sale_id)
    db.flush()
    return count


def redeem(db: Session, *, actor_id: uuid.UUID | None, business: Business, customer: Customer) -> None:
    if customer.stamps < business.loyalty_stamps_needed:
        raise Conflict("Hələ kifayət qədər möhür yoxdur", code="reward_not_ready")
    customer.stamps = 0
    customer.rewards += 1
    _event(db, customer, "redeem", 1, actor_id=actor_id, note=business.loyalty_reward)
    db.flush()


def adjust_stamps(db: Session, *, actor_id: uuid.UUID | None, business: Business, customer: Customer, stamps: int,
                  note: str | None) -> None:
    """Manual correction of the current card (e.g. moving a paper card over)."""
    stamps = max(0, min(int(stamps), business.loyalty_stamps_needed))
    delta = stamps - customer.stamps
    if delta == 0:
        return
    customer.stamps = stamps
    if delta > 0:
        customer.total_stamps += delta
    _event(db, customer, "adjust", delta, actor_id=actor_id, note=(note or "").strip()[:300] or None)
    db.flush()


def reissue_card(db: Session, *, actor_id: uuid.UUID | None, customer: Customer) -> None:
    """Replaces the QR token (lost or shared card): stamps stay, the old QR code stops working."""
    customer.card_token = secrets.token_urlsafe(12)
    audit(db, business_id=customer.business_id, actor_id=actor_id, action="customer.card_reissue",
          entity_type="customer", entity_id=customer.id)
    db.flush()


# ---------------------------------------------------------------------------
# Queries & statistics
# ---------------------------------------------------------------------------
def _sales_stats_subquery():
    signed = case((Sale.kind == "sale", Sale.total), else_=-Sale.total)
    return (
        select(
            Sale.customer_id.label("customer_id"),
            func.coalesce(func.sum(signed), 0).label("spent"),
            func.count(case((Sale.kind == "sale", 1))).label("orders"),
            func.max(case((Sale.kind == "sale", Sale.sold_at))).label("last_purchase"),
        )
        .where(Sale.customer_id.is_not(None))
        .group_by(Sale.customer_id)
        .subquery()
    )


def list_customers(db: Session, business_id: uuid.UUID, *, q: str | None = None, archived: bool = False,
                   segment: str | None = None, sort: str = "-last_purchase", page: int = 1,
                   page_size: int = 50) -> tuple[list[dict[str, Any]], int]:
    stats = _sales_stats_subquery()
    stmt = (select(Customer, stats.c.spent, stats.c.orders, stats.c.last_purchase)
            .outerjoin(stats, stats.c.customer_id == Customer.id)
            .where(Customer.business_id == business_id, Customer.is_archived == archived))
    if q and q.strip():
        term = f"%{q.strip()}%"
        phone = normalize_phone(q)
        conds = [Customer.name.ilike(term), Customer.email.ilike(term), Customer.company.ilike(term)]
        if phone:
            conds.append(Customer.phone.like(f"%{phone}%"))
        stmt = stmt.where(or_(*conds))
    if segment == "reward_ready":
        stmt = stmt.where(Customer.stamps >= select(Business.loyalty_stamps_needed)
                          .where(Business.id == business_id).scalar_subquery())
    elif segment == "inactive":
        stmt = stmt.where(or_(stats.c.last_purchase.is_(None), stats.c.last_purchase < utc_now() - timedelta(days=60)))
    elif segment == "birthday":
        today = date.today()
        stmt = stmt.where(Customer.birthday.is_not(None))
        rows = db.execute(stmt).all()
        rows = [r for r in rows if _days_to_birthday(r[0].birthday, today) <= 30]
        rows.sort(key=lambda r: _days_to_birthday(r[0].birthday, today))
        total = len(rows)
        start = (max(page, 1) - 1) * page_size
        return [_row(r) for r in rows[start:start + page_size]], total
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    order = {
        "-last_purchase": [stats.c.last_purchase.desc().nulls_last(), Customer.created_at.desc()],
        "-spent": [func.coalesce(stats.c.spent, 0).desc(), Customer.name],
        "-orders": [func.coalesce(stats.c.orders, 0).desc(), Customer.name],
        "name": [Customer.name],
        "-created_at": [Customer.created_at.desc()],
        "-stamps": [Customer.stamps.desc(), Customer.name],
    }.get(sort, [Customer.created_at.desc()])
    page_size = max(1, min(page_size, 200))
    rows = db.execute(stmt.order_by(*order, Customer.id).offset((max(page, 1) - 1) * page_size).limit(page_size)).all()
    return [_row(r) for r in rows], total


def _row(r) -> dict[str, Any]:
    c, spent, orders, last = r
    return {"customer": c, "spent": Decimal(spent or 0), "orders": int(orders or 0), "last_purchase": last}


def _days_to_birthday(b: date, today: date) -> int:
    try:
        nxt = b.replace(year=today.year)
    except ValueError:  # 29 February
        nxt = date(today.year, 3, 1)
    if nxt < today:
        try:
            nxt = b.replace(year=today.year + 1)
        except ValueError:
            nxt = date(today.year + 1, 3, 1)
    return (nxt - today).days


def segment_counts(db: Session, business: Business) -> dict[str, int]:
    base = select(func.count()).select_from(Customer).where(Customer.business_id == business.id,
                                                            Customer.is_archived.is_(False))
    stats = _sales_stats_subquery()
    active = db.scalar(select(func.count()).select_from(Customer).join(stats, stats.c.customer_id == Customer.id)
                       .where(Customer.business_id == business.id, Customer.is_archived.is_(False),
                              stats.c.last_purchase >= utc_now() - timedelta(days=60))) or 0
    today = date.today()
    birthdays = sum(1 for b in db.scalars(select(Customer.birthday).where(
        Customer.business_id == business.id, Customer.is_archived.is_(False), Customer.birthday.is_not(None)))
        if _days_to_birthday(b, today) <= 30)
    return {
        "total": db.scalar(base) or 0,
        "active": active,
        "reward_ready": db.scalar(base.where(Customer.stamps >= business.loyalty_stamps_needed)) or 0,
        "birthday_soon": birthdays,
        "rewards_given": db.scalar(select(func.count()).select_from(LoyaltyEvent).where(
            LoyaltyEvent.business_id == business.id, LoyaltyEvent.event_type == "redeem")) or 0,
    }


def customer_detail(db: Session, business: Business, c: Customer) -> dict[str, Any]:
    sales = db.scalars(select(Sale).where(Sale.business_id == business.id, Sale.customer_id == c.id)
                       .order_by(Sale.sold_at.desc())).all()
    real = [s for s in sales if s.kind == "sale"]
    returns = [s for s in sales if s.kind == "return"]
    gross = sum((Decimal(s.total) for s in real), Decimal(0))
    returned = sum((Decimal(s.total) for s in returns), Decimal(0))
    known_cost = [s for s in sales if s.cost_total is not None]
    profit = sum(((Decimal(s.total) - Decimal(s.cost_total)) * (1 if s.kind == "sale" else -1) for s in known_cost),
                 Decimal(0))
    first = min((aware(s.sold_at) for s in real), default=None)
    last = max((aware(s.sold_at) for s in real), default=None)
    avg_gap = None
    if len(real) >= 2 and first and last:
        avg_gap = round((last - first).days / (len(real) - 1), 1)

    signed_qty = case((Sale.kind == "sale", SaleItem.quantity), else_=-SaleItem.quantity)
    signed_total = case((Sale.kind == "sale", SaleItem.line_total), else_=-SaleItem.line_total)
    top = db.execute(
        select(SaleItem.product_id, SaleItem.name, SaleItem.unit, func.sum(signed_qty), func.sum(signed_total),
               func.count(func.distinct(Sale.id)))
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(Sale.business_id == business.id, Sale.customer_id == c.id)
        .group_by(SaleItem.product_id, SaleItem.name, SaleItem.unit)
        .order_by(func.sum(signed_total).desc())
        .limit(8)
    ).all()

    # monthly net spending over the last 12 months
    months: dict[str, Decimal] = {}
    today = date.today()
    for i in range(11, -1, -1):
        y, m = today.year, today.month - i
        while m <= 0:
            y, m = y - 1, m + 12
        months[f"{y:04d}-{m:02d}"] = Decimal(0)
    for s in sales:
        key = aware(s.sold_at).strftime("%Y-%m")
        if key in months:
            months[key] += Decimal(s.total) if s.kind == "sale" else -Decimal(s.total)

    events = db.scalars(select(LoyaltyEvent).where(LoyaltyEvent.customer_id == c.id)
                        .order_by(LoyaltyEvent.created_at.desc()).limit(30)).all()
    item_counts = dict(db.execute(select(SaleItem.sale_id, func.count()).where(
        SaleItem.sale_id.in_([s.id for s in sales[:30]] or [uuid.uuid4()])).group_by(SaleItem.sale_id)).all())
    net = gross - returned
    return {
        **serialize_customer(c, business),
        "stats": {
            "orders": len(real), "returns": len(returns), "gross_spent": money_str(gross),
            "returned": money_str(returned), "net_spent": money_str(net),
            "average_order": money_str(net / len(real)) if real else None,
            "gross_profit": money_str(profit) if known_cost else None,
            "profit_complete": len(known_cost) == len(sales),
            "first_purchase": iso(first), "last_purchase": iso(last),
            "days_since_last": (utc_now() - last).days if last else None,
            "average_days_between": avg_gap,
            "days_to_birthday": _days_to_birthday(c.birthday, today) if c.birthday else None,
        },
        "monthly": [{"period": k, "spent": money_str(v)} for k, v in months.items()],
        "top_products": [{"product_id": str(pid), "name": name, "unit": unit, "quantity": qty_str(q),
                          "total": money_str(t), "orders": n} for pid, name, unit, q, t, n in top],
        "recent_sales": [{"id": str(s.id), "number": s.number, "kind": s.kind, "status": s.status,
                          "total": money_str(s.total), "sold_at": iso(s.sold_at),
                          "item_count": item_counts.get(s.id, 0)} for s in sales[:30]],
        "loyalty_events": [{"id": str(e.id), "type": e.event_type, "amount": e.amount, "stamps_after": e.stamps_after,
                            "note": e.note, "sale_id": str(e.sale_id) if e.sale_id else None,
                            "created_at": iso(e.created_at)} for e in events],
    }


def loyalty_card(c: Customer, business: Business) -> dict[str, Any]:
    needed = business.loyalty_stamps_needed
    return {"token": c.card_token, "stamps": c.stamps, "stamps_needed": needed, "reward_ready": c.stamps >= needed,
            "rewards": c.rewards, "total_stamps": c.total_stamps, "last_stamp_at": iso(c.last_stamp_at),
            "reward": business.loyalty_reward, "enabled": business.loyalty_enabled}


def serialize_customer(c: Customer, business: Business, *, spent: Decimal | None = None, orders: int | None = None,
                       last_purchase=None) -> dict[str, Any]:
    out = {
        "id": str(c.id), "name": c.name, "phone": c.phone, "email": c.email,
        "birthday": c.birthday.isoformat() if c.birthday else None, "gender": c.gender, "company": c.company,
        "address": c.address, "tags": c.tags or [], "note": c.note, "is_archived": c.is_archived,
        "created_at": iso(c.created_at), "updated_at": iso(c.updated_at), "card": loyalty_card(c, business),
    }
    if spent is not None:
        out.update({"net_spent": money_str(spent), "orders": orders, "last_purchase": iso(last_purchase)})
    return out


def public_card(c: Customer, business: Business) -> dict[str, Any]:
    """What the customer sees on their phone: no phone number, no purchase history."""
    card = loyalty_card(c, business)
    card.pop("enabled")
    return {**card, "name": c.name.split()[0] if c.name else "", "business": {
        "name": business.name, "logo": business.logo, "theme": business.theme or {}}}
