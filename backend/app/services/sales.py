"""Sales, returns and cancellations.

* A sale deducts stock through the inventory ledger (event_type "sale") inside the caller's transaction,
  so a failed line (e.g. insufficient stock) rolls back the whole sale.
* History is never deleted. A return or a cancellation is a new Sale row of kind "return" that points at
  the original sale and puts the stock back (event_type "return"). The original sale's status becomes
  "partially_returned" or "returned".
* Unit cost is snapshotted from the product's purchase price at the time of sale, so later price edits do
  not rewrite past profit. Lines without a recorded purchase price have no cost (NULL), never a guess.

Mutating functions flush but never commit; callers own the transaction.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.models import Business, Customer, Product, Sale, SaleItem
from app.services import inventory as inventory_svc
from app.services.common import CENT, audit, aware, iso, money, money_str, qty, qty_str, utc_now

MAX_LINES = 100


def _lock_business(db: Session, business_id: uuid.UUID) -> Business:
    """Serialises sale numbering per business (row lock on Postgres; SQLite already serialises writers)."""
    b = db.scalar(select(Business).where(Business.id == business_id).with_for_update())
    if b is None:
        raise NotFound("Biznes tapılmadı")
    return b


def _next_number(db: Session, business_id: uuid.UUID, kind: str) -> str:
    prefix = "S" if kind == "sale" else "R"
    n = (db.scalar(select(func.count()).select_from(Sale).where(Sale.business_id == business_id, Sale.kind == kind)) or 0) + 1
    while True:
        number = f"{prefix}-{n:06d}"
        if not db.scalar(select(Sale.id).where(Sale.business_id == business_id, Sale.number == number)):
            return number
        n += 1


def _by_idempotency(db: Session, business_id: uuid.UUID, key: str | None) -> Sale | None:
    if not key:
        return None
    return db.scalar(select(Sale).where(Sale.business_id == business_id, Sale.idempotency_key == key))


def _sold_at(value: datetime | None) -> datetime:
    now = utc_now()
    if value is None:
        return now
    value = aware(value)
    if value > now + timedelta(minutes=5):
        raise ValidationFailed("Satış tarixi gələcəkdə ola bilməz", details={"field": "sold_at"})
    return value


def record_sale(
    db: Session,
    *,
    actor_id: uuid.UUID | None,
    business_id: uuid.UUID,
    items: list[dict[str, Any]],
    customer_name: str | None = None,
    customer_id: uuid.UUID | None = None,
    loyalty_stamp: bool = False,
    note: str | None = None,
    sold_at: datetime | None = None,
    source: str = "manual",
    ai_action_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
) -> Sale:
    """items: [{"product_id": UUID, "quantity": Decimal, "unit_price": Decimal | None}]. A missing unit price
    falls back to the product's recorded selling price; if there is none the sale is refused."""
    if not items:
        raise ValidationFailed("Satışda ən azı bir məhsul olmalıdır")
    if len(items) > MAX_LINES:
        raise ValidationFailed(f"Bir satışda ən çox {MAX_LINES} sətir ola bilər")
    if (prior := _by_idempotency(db, business_id, idempotency_key)) is not None:
        return prior
    business = _lock_business(db, business_id)
    if (prior := _by_idempotency(db, business_id, idempotency_key)) is not None:  # a concurrent retry won
        return prior

    customer = None
    if customer_id is not None:
        customer = db.scalar(select(Customer).where(Customer.id == customer_id, Customer.business_id == business_id)
                             .with_for_update())
        if customer is None:
            raise NotFound("Müştəri tapılmadı", details={"field": "customer_id"})
        customer_name = customer_name or customer.name

    lines: list[tuple[int, Product, Decimal, Decimal]] = []
    for pos, raw in enumerate(items):
        pid = raw["product_id"]
        pid = uuid.UUID(str(pid)) if not isinstance(pid, uuid.UUID) else pid
        p = db.scalar(select(Product).where(Product.id == pid, Product.business_id == business_id)
                      .options(selectinload(Product.inventory)))
        if p is None:
            raise NotFound("Məhsul tapılmadı", details={"line": pos + 1})
        if p.is_archived:
            raise ValidationFailed(f"«{p.name}» arxivdədir, satıla bilməz", code="product_archived")
        q = qty(raw["quantity"])
        if q <= 0:
            raise ValidationFailed("Satış miqdarı müsbət olmalıdır", details={"line": pos + 1, "field": "quantity"})
        price = raw.get("unit_price")
        if price is None or price == "":
            if p.selling_price is None:
                raise ValidationFailed(f"«{p.name}» üçün satış qiyməti yoxdur. Qiyməti göstərin.",
                                       code="price_required", details={"line": pos + 1, "product_id": str(p.id)})
            price = p.selling_price
        price = money(price)
        if price < 0:
            raise ValidationFailed("Qiymət mənfi ola bilməz", details={"line": pos + 1, "field": "unit_price"})
        lines.append((pos, p, q, price))

    sale = Sale(business_id=business_id, number=_next_number(db, business_id, "sale"), kind="sale", status="completed",
                customer_id=customer.id if customer else None, customer_name=(customer_name or "").strip()[:200] or None, note=(note or "").strip()[:1000] or None,
                currency=business.currency, total=Decimal(0), source=source, actor_id=actor_id,
                ai_action_id=ai_action_id, idempotency_key=idempotency_key, sold_at=_sold_at(sold_at))
    db.add(sale)
    db.flush()

    total = Decimal(0)
    cost_total: Decimal | None = Decimal(0)
    # lock inventory rows in a stable order so two multi-line sales cannot deadlock each other
    for pos, p, q, price in sorted(lines, key=lambda x: str(x[1].id)):
        inventory_svc.adjust_stock(db, actor_id=actor_id, business_id=business_id, product_id=p.id, delta=-q,
                                   event_type="sale", source="sale", sale_id=sale.id, ai_action_id=ai_action_id,
                                   reason=f"Satış {sale.number}")
        unit_cost = Decimal(p.purchase_price) if p.purchase_price is not None else None
        line_total = (q * price).quantize(CENT)
        line_cost = (q * unit_cost).quantize(CENT) if unit_cost is not None else None
        db.add(SaleItem(sale_id=sale.id, product_id=p.id, position=pos, name=p.name, unit=p.unit, quantity=q,
                        unit_price=price, unit_cost=unit_cost, line_total=line_total, line_cost=line_cost))
        total += line_total
        cost_total = cost_total + line_cost if (cost_total is not None and line_cost is not None) else None
    sale.total = total
    sale.cost_total = cost_total
    if customer is not None and loyalty_stamp and business.loyalty_enabled             and customer.stamps < business.loyalty_stamps_needed:
        from app.services import customers as customers_svc

        customers_svc.add_stamps(db, actor_id=actor_id, business=business, customer=customer, sale_id=sale.id)
    audit(db, business_id=business_id, actor_id=actor_id, action="sale.create", entity_type="sale", entity_id=sale.id,
          after={"number": sale.number, "total": total, "lines": len(lines)}, via="ai" if source == "ai" else "api")
    db.flush()
    db.refresh(sale)
    return sale


def returned_quantities(db: Session, sale: Sale) -> dict[uuid.UUID, Decimal]:
    rows = db.execute(
        select(SaleItem.original_item_id, func.sum(SaleItem.quantity))
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(Sale.original_sale_id == sale.id, Sale.kind == "return")
        .group_by(SaleItem.original_item_id)
    ).all()
    return {oid: Decimal(q) for oid, q in rows if oid is not None}


def return_sale(
    db: Session,
    *,
    actor_id: uuid.UUID | None,
    business_id: uuid.UUID,
    sale_id: uuid.UUID,
    items: list[dict[str, Any]] | None = None,
    reason: str | None = None,
    source: str = "manual",
    ai_action_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
) -> Sale:
    """Return part of a sale (items = [{"sale_item_id", "quantity"}]) or, with items=None, everything not yet
    returned (= cancellation). Creates a linked return record and puts the stock back."""
    if (prior := _by_idempotency(db, business_id, idempotency_key)) is not None:
        return prior
    _lock_business(db, business_id)
    if (prior := _by_idempotency(db, business_id, idempotency_key)) is not None:
        return prior
    original = db.scalar(select(Sale).where(Sale.id == sale_id, Sale.business_id == business_id)
                         .options(selectinload(Sale.items)))
    if original is None:
        raise NotFound("Satış tapılmadı")
    if original.kind != "sale":
        raise ValidationFailed("Qaytarma yalnız satış üçün edilə bilər", code="not_a_sale")
    already = returned_quantities(db, original)
    remaining = {it.id: Decimal(it.quantity) - already.get(it.id, Decimal(0)) for it in original.items}

    wanted: list[tuple[SaleItem, Decimal]] = []
    if items is None:
        wanted = [(it, remaining[it.id]) for it in original.items if remaining[it.id] > 0]
        if not wanted:
            raise Conflict("Bu satış artıq tam qaytarılıb", code="already_returned")
        remaining = {key: Decimal(0) for key in remaining}
    else:
        if not items:
            raise ValidationFailed("Qaytarılacaq məhsul seçilməyib")
        by_id = {it.id: it for it in original.items}
        for raw in items:
            iid = raw["sale_item_id"]
            iid = uuid.UUID(str(iid)) if not isinstance(iid, uuid.UUID) else iid
            it = by_id.get(iid)
            if it is None:
                raise NotFound("Satış sətri tapılmadı")
            q = qty(raw["quantity"])
            if q <= 0:
                raise ValidationFailed("Qaytarma miqdarı müsbət olmalıdır")
            if q > remaining[it.id]:
                raise ValidationFailed(
                    f"«{it.name}» üçün ən çox {qty_str(remaining[it.id])} qaytarıla bilər",
                    code="return_exceeds_sold", details={"sale_item_id": str(it.id), "remaining": qty_str(remaining[it.id])})
            remaining[it.id] -= q
            wanted.append((it, q))

    ret = Sale(business_id=business_id, number=_next_number(db, business_id, "return"), kind="return",
               status="completed", original_sale_id=original.id, customer_id=original.customer_id,
               customer_name=original.customer_name,
               note=(reason or "").strip()[:1000] or None, currency=original.currency, total=Decimal(0),
               source=source, actor_id=actor_id, ai_action_id=ai_action_id, idempotency_key=idempotency_key,
               sold_at=utc_now())
    db.add(ret)
    db.flush()
    total = Decimal(0)
    cost_total: Decimal | None = Decimal(0)
    for it, q in sorted(wanted, key=lambda x: str(x[0].product_id)):
        inventory_svc.adjust_stock(db, actor_id=actor_id, business_id=business_id, product_id=it.product_id, delta=q,
                                   event_type="return", source="sale", sale_id=ret.id, ai_action_id=ai_action_id,
                                   reason=f"Qaytarma {ret.number} ({original.number})")
        line_total = (q * Decimal(it.unit_price)).quantize(CENT)
        line_cost = (q * Decimal(it.unit_cost)).quantize(CENT) if it.unit_cost is not None else None
        db.add(SaleItem(sale_id=ret.id, product_id=it.product_id, original_item_id=it.id, position=it.position,
                        name=it.name, unit=it.unit, quantity=q, unit_price=it.unit_price, unit_cost=it.unit_cost,
                        line_total=line_total, line_cost=line_cost))
        total += line_total
        cost_total = cost_total + line_cost if (cost_total is not None and line_cost is not None) else None
    ret.total = total
    ret.cost_total = cost_total
    fully = all(v <= 0 for v in remaining.values())
    before_status = original.status
    original.status = "returned" if fully else "partially_returned"
    audit(db, business_id=business_id, actor_id=actor_id, action="sale.cancel" if items is None else "sale.return",
          entity_type="sale", entity_id=original.id, before={"status": before_status},
          after={"status": original.status, "return_number": ret.number, "amount": total},
          via="ai" if source == "ai" else "api")
    db.flush()
    db.refresh(ret)
    return ret


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def get_sale(db: Session, business_id: uuid.UUID, sale_id: uuid.UUID) -> Sale:
    s = db.scalar(select(Sale).where(Sale.id == sale_id, Sale.business_id == business_id)
                  .options(selectinload(Sale.items), selectinload(Sale.original)))
    if s is None:
        raise NotFound("Satış tapılmadı")
    return s


def list_sales(
    db: Session,
    business_id: uuid.UUID,
    *,
    kind: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    product_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[Sale], int]:
    stmt = select(Sale).where(Sale.business_id == business_id)
    if kind:
        stmt = stmt.where(Sale.kind == kind)
    if date_from:
        stmt = stmt.where(Sale.sold_at >= date_from)
    if date_to:
        stmt = stmt.where(Sale.sold_at < date_to)
    if product_id:
        stmt = stmt.where(Sale.id.in_(select(SaleItem.sale_id).where(SaleItem.product_id == product_id)))
    if customer_id:
        stmt = stmt.where(Sale.customer_id == customer_id)
    if q and q.strip():
        term = f"%{q.strip()}%"
        stmt = stmt.where(or_(Sale.number.ilike(term), Sale.customer_name.ilike(term),
                              Sale.id.in_(select(SaleItem.sale_id).where(SaleItem.name.ilike(term)))))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page_size = max(1, min(page_size, 200))
    items = db.scalars(stmt.order_by(Sale.sold_at.desc(), Sale.created_at.desc(), Sale.id)
                       .options(selectinload(Sale.items), selectinload(Sale.original))
                       .offset((max(page, 1) - 1) * page_size).limit(page_size)).all()
    return list(items), total


def serialize_sale(s: Sale, *, returned: dict[uuid.UUID, Decimal] | None = None,
                   returns: list[Sale] | None = None) -> dict[str, Any]:
    profit = (Decimal(s.total) - Decimal(s.cost_total)) if s.cost_total is not None else None
    out: dict[str, Any] = {
        "id": str(s.id), "number": s.number, "kind": s.kind, "status": s.status,
        "original_sale": {"id": str(s.original.id), "number": s.original.number} if s.original else None,
        "customer_id": str(s.customer_id) if s.customer_id else None,
        "customer_name": s.customer_name, "note": s.note, "currency": s.currency,
        "total": money_str(s.total), "cost_total": money_str(s.cost_total), "gross_profit": money_str(profit),
        "source": s.source, "sold_at": iso(s.sold_at), "created_at": iso(s.created_at),
        "items": [{
            "id": str(i.id), "product_id": str(i.product_id), "name": i.name, "unit": i.unit,
            "quantity": qty_str(i.quantity), "unit_price": money_str(i.unit_price), "unit_cost": money_str(i.unit_cost),
            "line_total": money_str(i.line_total), "line_cost": money_str(i.line_cost),
            **({"returned_quantity": qty_str(returned.get(i.id, Decimal(0)))} if returned is not None else {}),
        } for i in s.items],
    }
    if returns is not None:
        out["returns"] = [{"id": str(r.id), "number": r.number, "total": money_str(r.total), "note": r.note,
                           "sold_at": iso(r.sold_at)} for r in returns]
    return out


def sale_detail(db: Session, s: Sale) -> dict[str, Any]:
    if s.kind != "sale":
        return serialize_sale(s)
    returns = db.scalars(select(Sale).where(Sale.original_sale_id == s.id).order_by(Sale.created_at)).all()
    return serialize_sale(s, returned=returned_quantities(db, s), returns=list(returns))
