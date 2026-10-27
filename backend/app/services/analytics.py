"""Business analytics, computed only from recorded sales, returns and inventory.

Definitions (also shown in the UI):
* revenue        = sum of sale lines − sum of return lines in the period (returns count on their own date)
* cost of goods  = same, using the purchase-price snapshot stored on each line
* gross profit   = revenue − cost of goods, over lines that HAVE a recorded cost only. Revenue from lines
                   without a cost is reported separately and is never treated as profit.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ValidationFailed
from app.models import Business, Category, InventoryEvent, Product, Sale, SaleItem
from app.services.common import aware, iso, money_str, qty_str, utc_now
from app.services.inventory import summary as inventory_summary
from app.services.products import attention_threshold, cost_value

MAX_RANGE_DAYS = 731


def business_tz(b: Business) -> ZoneInfo:
    try:
        return ZoneInfo(b.timezone or "Asia/Baku")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


@dataclass
class Period:
    start: date  # inclusive, business-local
    end: date  # inclusive, business-local
    tz: ZoneInfo

    @property
    def start_utc(self) -> datetime:
        return datetime.combine(self.start, time.min, tzinfo=self.tz).astimezone(timezone.utc)

    @property
    def end_utc(self) -> datetime:
        return datetime.combine(self.end + timedelta(days=1), time.min, tzinfo=self.tz).astimezone(timezone.utc)

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def resolve_period(b: Business, *, date_from: date | None = None, date_to: date | None = None,
                   days: int | None = None) -> Period:
    tz = business_tz(b)
    today = utc_now().astimezone(tz).date()
    end = date_to or today
    start = date_from or end - timedelta(days=(days or 30) - 1)
    if start > end:
        raise ValidationFailed("Başlanğıc tarixi son tarixdən sonra ola bilməz", code="bad_period")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise ValidationFailed(f"Dövr ən çox {MAX_RANGE_DAYS} gün ola bilər", code="bad_period")
    return Period(start, end, tz)


@dataclass
class Totals:
    revenue: Decimal = Decimal(0)  # net of returns
    gross_sales: Decimal = Decimal(0)
    returns: Decimal = Decimal(0)
    costed_revenue: Decimal = Decimal(0)  # net revenue of lines that have a cost
    cogs: Decimal = Decimal(0)
    units: Decimal = Decimal(0)  # net units
    sales_count: int = 0
    returns_count: int = 0
    unknown_cost_lines: int = 0
    sale_ids: set = field(default_factory=set)

    def add_line(self, sign: int, item: SaleItem) -> None:
        lt = Decimal(item.line_total)
        self.revenue += sign * lt
        if sign > 0:
            self.gross_sales += lt
        else:
            self.returns += lt
        self.units += sign * Decimal(item.quantity)
        if item.line_cost is not None:
            self.costed_revenue += sign * lt
            self.cogs += sign * Decimal(item.line_cost)
        else:
            self.unknown_cost_lines += 1

    @property
    def gross_profit(self) -> Decimal:
        return self.costed_revenue - self.cogs

    def out(self) -> dict[str, Any]:
        margin = (self.gross_profit / self.costed_revenue * 100) if self.costed_revenue > 0 else None
        return {
            "revenue": money_str(self.revenue),
            "gross_sales": money_str(self.gross_sales),
            "returns": money_str(self.returns),
            "cost_of_goods": money_str(self.cogs),
            "gross_profit": money_str(self.gross_profit) if not self.unknown_cost_lines else None,
            "known_cost_gross_profit": money_str(self.gross_profit),
            "cost_complete": not self.unknown_cost_lines,
            "gross_margin_pct": f"{margin:.1f}" if margin is not None else None,
            "revenue_without_cost": money_str(self.revenue - self.costed_revenue),
            "units_sold": qty_str(self.units),
            "sales_count": self.sales_count,
            "returns_count": self.returns_count,
            "average_sale": money_str(self.gross_sales / self.sales_count) if self.sales_count else None,
        }


def _records(db: Session, business_id: uuid.UUID, start: datetime, end: datetime) -> list[Sale]:
    return list(db.scalars(
        select(Sale).where(Sale.business_id == business_id, Sale.sold_at >= start, Sale.sold_at < end)
        .options(selectinload(Sale.items))
    ).all())


def _bucket(d: date, granularity: str) -> date:
    if granularity == "week":
        return d - timedelta(days=d.weekday())
    if granularity == "month":
        return d.replace(day=1)
    return d


def _buckets(p: Period, granularity: str) -> list[date]:
    out, d = [], _bucket(p.start, granularity)
    while d <= p.end:
        out.append(d)
        if granularity == "week":
            d += timedelta(days=7)
        elif granularity == "month":
            d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        else:
            d += timedelta(days=1)
    return out


def sales_report(db: Session, business: Business, period: Period, *, granularity: str = "day",
                 top_limit: int = 10) -> dict[str, Any]:
    if granularity not in ("day", "week", "month"):
        raise ValidationFailed("granularity: day | week | month")
    records = _records(db, business.id, period.start_utc, period.end_utc)
    totals = Totals()
    series: dict[date, Totals] = defaultdict(Totals)
    by_product: dict[uuid.UUID, dict[str, Any]] = {}
    for s in records:
        sign = 1 if s.kind == "sale" else -1
        b = _bucket(aware(s.sold_at).astimezone(period.tz).date(), granularity)
        if sign > 0:
            totals.sales_count += 1
            series[b].sales_count += 1
        else:
            totals.returns_count += 1
            series[b].returns_count += 1
        for it in s.items:
            totals.add_line(sign, it)
            series[b].add_line(sign, it)
            row = by_product.setdefault(it.product_id, {"product_id": str(it.product_id), "name": it.name,
                                                        "unit": it.unit, "t": Totals()})
            row["t"].add_line(sign, it)
            if sign > 0:
                row["t"].sale_ids.add(s.id)
    products = []
    for row in by_product.values():
        t: Totals = row["t"]
        products.append({"product_id": row["product_id"], "name": row["name"], "unit": row["unit"],
                         "units": qty_str(t.units), "revenue": money_str(t.revenue),
                         "gross_profit": money_str(t.gross_profit) if not t.unknown_cost_lines else None,
                         "sales": len(t.sale_ids), "_u": t.units, "_r": t.revenue})
    top_units = [{k: v for k, v in p.items() if not k.startswith("_")}
                 for p in sorted(products, key=lambda x: (-x["_u"], -x["_r"]))[:top_limit] if p["_u"] > 0]
    top_revenue = [{k: v for k, v in p.items() if not k.startswith("_")}
                   for p in sorted(products, key=lambda x: (-x["_r"], -x["_u"]))[:top_limit] if p["_r"] > 0]
    return {
        "period": {"from": period.start.isoformat(), "to": period.end.isoformat(), "days": period.days,
                   "granularity": granularity, "timezone": str(period.tz)},
        "currency": business.currency,
        "totals": totals.out(),
        "series": [{"period": d.isoformat(), "revenue": money_str(series[d].revenue),
                    "gross_profit": money_str(series[d].gross_profit), "units": qty_str(series[d].units),
                    "sales": series[d].sales_count} for d in _buckets(period, granularity)],
        "top_by_units": top_units,
        "top_by_revenue": top_revenue,
        "has_sales": bool(records),
    }


def overview(db: Session, business: Business, period: Period, *, granularity: str = "day") -> dict[str, Any]:
    inv = inventory_summary(db, business.id)
    report = sales_report(db, business, period, granularity=granularity, top_limit=5)

    # same-length previous period, for a trend arrow based on real numbers only
    prev = Period(period.start - timedelta(days=period.days), period.start - timedelta(days=1), period.tz)
    prev_totals = Totals()
    for s in _records(db, business.id, prev.start_utc, prev.end_utc):
        for it in s.items:
            prev_totals.add_line(1 if s.kind == "sale" else -1, it)

    products = db.scalars(select(Product).where(Product.business_id == business.id, Product.is_archived.is_(False))
                          .options(selectinload(Product.inventory))).all()
    cat_names = {c.id: c.name for c in db.scalars(select(Category).where(Category.business_id == business.id)).all()}
    by_cat: dict[str, dict[str, Any]] = {}
    for p in products:
        name = cat_names.get(p.category_id, "Kateqoriyasız")
        entry = by_cat.setdefault(name, {"category": name, "value": Decimal(0), "products": 0})
        entry["value"] += cost_value(p) or Decimal(0)
        entry["products"] += 1
    categories = sorted(({"category": v["category"], "value": money_str(v["value"]), "products": v["products"]}
                         for v in by_cat.values()), key=lambda x: -Decimal(x["value"]))

    recent_sales = db.scalars(select(Sale).where(Sale.business_id == business.id)
                              .order_by(Sale.sold_at.desc(), Sale.created_at.desc()).limit(6)).all()
    recent_events = db.execute(
        select(InventoryEvent, Product.name, Product.unit).join(Product, Product.id == InventoryEvent.product_id)
        .where(InventoryEvent.business_id == business.id).order_by(InventoryEvent.created_at.desc()).limit(8)
    ).all()
    return {
        **report,
        "inventory": inv,
        "previous_period": {"from": prev.start.isoformat(), "to": prev.end.isoformat(),
                            "revenue": money_str(prev_totals.revenue), "gross_profit": money_str(prev_totals.gross_profit)},
        "categories": categories,
        "low_stock": inv["low_stock"],
        "recent_sales": [{"id": str(s.id), "number": s.number, "kind": s.kind, "status": s.status,
                          "total": money_str(s.total), "customer_name": s.customer_name, "sold_at": iso(s.sold_at)}
                         for s in recent_sales],
        "recent_inventory_events": [
            {"id": str(e.id), "product_name": n, "unit": u, "event_type": e.event_type, "delta": qty_str(e.delta),
             "quantity_after": qty_str(e.quantity_after), "source": e.source, "created_at": iso(e.created_at)}
            for e, n, u in recent_events
        ],
    }


def unsold_products(db: Session, business: Business, period: Period, limit: int = 20) -> list[dict[str, Any]]:
    sold = set(db.scalars(
        select(SaleItem.product_id).join(Sale, Sale.id == SaleItem.sale_id)
        .where(Sale.business_id == business.id, Sale.kind == "sale", Sale.sold_at >= period.start_utc,
               Sale.sold_at < period.end_utc)
    ).all())
    products = db.scalars(select(Product).where(Product.business_id == business.id, Product.is_archived.is_(False))
                          .options(selectinload(Product.inventory))).all()
    rows = [p for p in products if p.id not in sold and p.inventory and Decimal(p.inventory.quantity) > 0]
    rows.sort(key=lambda p: -(cost_value(p) or Decimal(0)))
    return [{"id": str(p.id), "name": p.name, "sku": p.sku, "quantity": qty_str(p.inventory.quantity), "unit": p.unit,
             "inventory_value": money_str(cost_value(p))} for p in rows[:limit]]


def reorder_suggestions(db: Session, business: Business, *, days: int = 30, cover_days: int = 14,
                        target_days: int = 30, limit: int = 50) -> dict[str, Any]:
    """Evidence-based reorder list. For each product:
    velocity  = net units sold in the last `days` / `days`
    cover     = current quantity / velocity (days of stock left at the current pace)
    flagged   if out of stock, at/below its minimum threshold, or cover < `cover_days`
    suggested = ceil(max(velocity × target_days, threshold) − quantity), never negative."""
    days = max(7, min(days, 365))
    period = resolve_period(business, days=days)
    sold: dict[uuid.UUID, Decimal] = defaultdict(Decimal)
    for s in _records(db, business.id, period.start_utc, period.end_utc):
        sign = 1 if s.kind == "sale" else -1
        for it in s.items:
            sold[it.product_id] += sign * Decimal(it.quantity)
    products = db.scalars(select(Product).where(Product.business_id == business.id, Product.is_archived.is_(False))
                          .options(selectinload(Product.inventory))).all()
    items = []
    for p in products:
        q = Decimal(p.inventory.quantity) if p.inventory else Decimal(0)
        units = max(sold.get(p.id, Decimal(0)), Decimal(0))
        velocity = units / Decimal(days)
        cover = (q / velocity) if velocity > 0 else None
        reasons = []
        if q <= 0:
            reasons.append("out_of_stock")
        elif q <= attention_threshold(p.low_stock_threshold):
            reasons.append("below_threshold")
        if cover is not None and cover < cover_days:
            reasons.append("low_cover")
        if not reasons:
            continue
        target = max(velocity * target_days, Decimal(p.low_stock_threshold))
        suggested = max(Decimal(math.ceil(target - q)), Decimal(0)) if target > q else Decimal(0)
        priority = 0 if q <= 0 else (1 if "low_cover" in reasons else 2)
        items.append({
            "product_id": str(p.id), "name": p.name, "sku": p.sku, "unit": p.unit,
            "quantity": qty_str(q), "threshold": p.low_stock_threshold,
            "sold_units": qty_str(units), "daily_velocity": qty_str(velocity.quantize(Decimal("0.001"))),
            "days_of_cover": f"{cover:.1f}" if cover is not None else None,
            "suggested_quantity": qty_str(suggested),
            "estimated_cost": money_str(suggested * Decimal(p.purchase_price)) if p.purchase_price is not None else None,
            "reasons": reasons, "_prio": priority, "_cover": cover if cover is not None else Decimal(10**9),
        })
    items.sort(key=lambda x: (x["_prio"], x["_cover"], x["name"]))
    return {
        "currency": business.currency,
        "basis": {"days": days, "cover_days": cover_days, "target_days": target_days,
                  "from": period.start.isoformat(), "to": period.end.isoformat()},
        "items": [{k: v for k, v in i.items() if not k.startswith("_")} for i in items[:limit]],
    }
