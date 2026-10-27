"""In-app notifications, computed from live data rather than stored as rows.

Each notification is a condition that is true right now (a product at or below its stock limit, a customer whose
reward is ready, a birthday this week) or a recent event (a customer who signed up with the counter QR). Fixing the
condition removes it. A notification's key changes when its situation changes (e.g. the remaining quantity), so
"seen" is tracked as the set of keys the user had already seen, and a further drop in stock notifies again."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Business, Customer, Inventory, Product
from app.services.common import iso, qty_str, utc_now
from app.services.customers import _days_to_birthday

DEFAULTS: dict[str, Any] = {
    "low_stock": True,
    "low_stock_threshold": None,  # None: each product's own minimum; a number: one limit for every product
    "out_of_stock": True,
    "reward_ready": True,
    "birthdays": True,
    "birthday_days": 7,
    "new_customers": True,
}
PER_TYPE_LIMIT = 50
SEEN_LIMIT = 1000


def settings_of(business: Business) -> dict[str, Any]:
    return {**DEFAULTS, **((business.notifications or {}).get("settings") or {})}


def update_settings(db: Session, business: Business, data: dict[str, Any]) -> dict[str, Any]:
    state = dict(business.notifications or {})
    state["settings"] = {**settings_of(business), **{k: v for k, v in data.items() if k in DEFAULTS}}
    business.notifications = state
    db.flush()
    return state["settings"]


def mark_seen(db: Session, business: Business) -> None:
    keys = [n["key"] for n in compute(db, business)]
    state = dict(business.notifications or {})
    state["seen"] = keys[:SEEN_LIMIT]
    business.notifications = state
    db.flush()


def _stock_items(db: Session, business: Business, s: dict[str, Any]) -> list[dict[str, Any]]:
    rows = db.execute(
        select(Product, Inventory.quantity).join(Inventory, Inventory.product_id == Product.id)
        .where(Product.business_id == business.id, Product.is_archived.is_(False))
    ).all()
    out_items, low_items = [], []
    limit = s["low_stock_threshold"]
    for p, qty in rows:
        threshold = p.low_stock_threshold if limit is None else int(limit)
        if qty <= 0:
            if s["out_of_stock"]:
                out_items.append({"key": f"out:{p.id}", "type": "out_of_stock", "severity": "critical",
                                  "title": f"{p.name} bitib", "body": "Stokda qalmayıb. Yeni mal qəbul edin.",
                                  "target": "inventory", "product_id": str(p.id), "quantity": qty_str(qty)})
        elif qty <= threshold and s["low_stock"]:
            low_items.append({"key": f"low:{p.id}:{qty_str(qty)}", "type": "low_stock", "severity": "warning",
                              "title": f"{p.name} azalır", "body": f"Qalıq {qty_str(qty)} {p.unit}, hədd {threshold}.",
                              "target": "inventory", "product_id": str(p.id), "quantity": qty_str(qty), "_q": qty})
    low_items.sort(key=lambda n: n.pop("_q"))
    return out_items[:PER_TYPE_LIMIT] + low_items[:PER_TYPE_LIMIT]


def _customer_items(db: Session, business: Business, s: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    base = select(Customer).where(Customer.business_id == business.id, Customer.is_archived.is_(False))
    if s["reward_ready"] and business.loyalty_enabled:
        ready = db.scalars(base.where(Customer.stamps >= business.loyalty_stamps_needed)
                           .order_by(Customer.last_stamp_at.desc()).limit(PER_TYPE_LIMIT)).all()
        items += [{"key": f"reward:{c.id}:{c.rewards}", "type": "reward_ready", "severity": "info",
                   "title": f"{c.name}: hədiyyə hazırdır", "body": f"{business.loyalty_reward} verilə bilər.",
                   "target": "customers", "customer_id": str(c.id)} for c in ready]
    if s["birthdays"]:
        today, days = date.today(), int(s["birthday_days"])
        soon = [(c, _days_to_birthday(c.birthday, today))
                for c in db.scalars(base.where(Customer.birthday.is_not(None))).all()]
        soon = sorted([x for x in soon if x[1] <= days], key=lambda x: x[1])[:PER_TYPE_LIMIT]
        items += [{"key": f"bday:{c.id}:{(today + timedelta(days=d)).year}", "type": "birthday", "severity": "info",
                   "title": f"{c.name}: ad günü {'bu gün' if d == 0 else f'{d} gün sonra'}",
                   "body": "Təbrik edin və ya kiçik hədiyyə təklif edin.", "target": "customers",
                   "customer_id": str(c.id)} for c, d in soon]
    if s["new_customers"]:
        since = utc_now() - timedelta(days=7)
        recent = db.scalars(base.where(Customer.created_at >= since).order_by(Customer.created_at.desc())
                            .limit(200)).all()
        items += [{"key": f"cust:{c.id}", "type": "new_customer", "severity": "info",
                   "title": f"Yeni müştəri: {c.name}", "body": "QR kod ilə qeydiyyatdan keçdi.",
                   "target": "customers", "customer_id": str(c.id), "at": iso(c.created_at)}
                  for c in recent if "QR qeydiyyat" in (c.tags or [])][:PER_TYPE_LIMIT]
    return items


def compute(db: Session, business: Business) -> list[dict[str, Any]]:
    s = settings_of(business)
    return _stock_items(db, business, s) + _customer_items(db, business, s)


def listing(db: Session, business: Business) -> dict[str, Any]:
    items = compute(db, business)
    seen = set((business.notifications or {}).get("seen") or [])
    for n in items:
        n["unread"] = n["key"] not in seen
    return {"items": items, "unread": sum(n["unread"] for n in items), "settings": settings_of(business)}
