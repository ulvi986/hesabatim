"""Business workspace. Every route is scoped by /businesses/{business_id} and guarded by Member."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, File, Query, Response, UploadFile
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, IdempotencyKey, Member, SettingsDep
from app.core.errors import Forbidden, ValidationFailed
from app.models import AuditLog
from app.schemas.requests import (
    BulkIdsIn,
    BusinessUpdateIn,
    NotificationSettingsIn,
    CancelIn,
    CategoryIn,
    CustomerIn,
    CustomerUpdateIn,
    ProductCreateIn,
    ProductUpdateIn,
    ReturnIn,
    SaleIn,
    StampAdjustIn,
    StampIn,
    StockAdjustIn,
)
from app.services import accounts, analytics, categories, customers, inventory, notifications, products, sales
from app.services.common import iso, run_idempotent

router = APIRouter(prefix="/businesses/{business_id}", tags=["business"])


@router.get("")
def get_business(m: Member):
    return accounts.serialize_business(m.business)


@router.patch("")
def update_business(body: BusinessUpdateIn, db: DB, user: CurrentUser, m: Member):
    if m.role not in ("owner", "admin"):
        raise Forbidden("Yalnız sahibi və ya admin dəyişə bilər")
    b = accounts.update_business(db, user, m.business, body.model_dump(exclude_unset=True))
    return accounts.serialize_business(b)


@router.put("/logo")
async def upload_logo(m: Member, db: DB, user: CurrentUser, file: UploadFile = File(...)):
    if m.role not in ("owner", "admin"):
        raise Forbidden("Yalnız sahibi və ya admin dəyişə bilər")
    content = await file.read(accounts.LOGO_MAX_BYTES + 1)
    return accounts.serialize_business(accounts.set_logo(db, user, m.business, content))


@router.delete("/logo")
def delete_logo(m: Member, db: DB, user: CurrentUser):
    if m.role not in ("owner", "admin"):
        raise Forbidden("Yalnız sahibi və ya admin dəyişə bilər")
    return accounts.serialize_business(accounts.set_logo(db, user, m.business, None))


# ----------------------------------------------------------------- categories
# ----------------------------------------------------------------- notifications
@router.get("/notifications")
def list_notifications(m: Member, db: DB):
    return notifications.listing(db, m.business)


@router.patch("/notifications/settings")
def notification_settings(body: NotificationSettingsIn, m: Member, db: DB):
    if m.role not in ("owner", "admin"):
        raise Forbidden("Yalnız sahibi və ya admin dəyişə bilər")
    data = body.model_dump(exclude_unset=True)
    if data.pop("use_product_thresholds", False):
        data["low_stock_threshold"] = None
    elif data.get("low_stock_threshold", 0) is None:
        data.pop("low_stock_threshold")
    notifications.update_settings(db, m.business, data)
    db.commit()
    return notifications.listing(db, m.business)


@router.post("/notifications/seen", status_code=204)
def notifications_seen(m: Member, db: DB):
    notifications.mark_seen(db, m.business)
    db.commit()


@router.get("/categories")
def list_categories(m: Member, db: DB):
    return {"items": categories.list_categories(db, m.business.id)}


@router.post("/categories", status_code=201)
def create_category(body: CategoryIn, m: Member, db: DB, user: CurrentUser):
    c = categories.create_category(db, actor_id=user.id, business_id=m.business.id, name=body.name)
    db.commit()
    return categories.serialize_category(c, 0)


@router.patch("/categories/{category_id}")
def rename_category(category_id: uuid.UUID, body: CategoryIn, m: Member, db: DB, user: CurrentUser):
    c = categories.rename_category(db, actor_id=user.id, category=categories.get_category(db, m.business.id, category_id),
                                   name=body.name)
    db.commit()
    return categories.serialize_category(c)


@router.delete("/categories/{category_id}", status_code=204)
def delete_category(category_id: uuid.UUID, m: Member, db: DB, user: CurrentUser):
    categories.delete_category(db, actor_id=user.id, category=categories.get_category(db, m.business.id, category_id))
    db.commit()
    return Response(status_code=204)


# ----------------------------------------------------------------- products
@router.get("/products")
def list_products(
    m: Member, db: DB,
    q: str | None = Query(default=None, max_length=200),
    category_id: uuid.UUID | None = None,
    stock: Literal["low", "out", "in", "attention"] | None = None,
    archived: bool = False,
    sort: str = Query(default="-updated_at", max_length=30),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
):
    items, total = products.list_products(db, m.business.id, q=q, category_id=category_id, stock=stock,
                                          archived_only=archived, sort=sort, page=page, page_size=page_size)
    return {"items": [products.serialize_product(p) for p in items], "total": total, "page": page, "page_size": page_size}


@router.post("/products", status_code=201)
def create_product(body: ProductCreateIn, m: Member, db: DB, user: CurrentUser, idem: IdempotencyKey = None):
    def work():
        data = body.model_dump(exclude={"quantity", "aliases"})
        p = products.create_product(db, actor_id=user.id, business_id=m.business.id, data=data,
                                    initial_quantity=body.quantity, aliases=body.aliases)
        return products.serialize_product(products.get_product(db, m.business.id, p.id))

    return run_idempotent(db, user_id=user.id, key=idem, scope=f"product.create:{m.business.id}", fn=work, payload=body.model_dump())


@router.get("/products/{product_id}")
def get_product(product_id: uuid.UUID, m: Member, db: DB):
    return products.serialize_product(products.get_product(db, m.business.id, product_id))


@router.patch("/products/{product_id}")
def update_product(product_id: uuid.UUID, body: ProductUpdateIn, m: Member, db: DB, user: CurrentUser):
    p = products.get_product(db, m.business.id, product_id, lock=True)
    products.update_product(db, actor_id=user.id, product=p, data=body.model_dump(exclude_unset=True))
    db.commit()
    return products.serialize_product(products.get_product(db, m.business.id, product_id))


@router.post("/products/archive")
def archive_products(body: BulkIdsIn, m: Member, db: DB, user: CurrentUser):
    n = products.archive_products(db, actor_id=user.id, business_id=m.business.id, product_ids=body.ids,
                                  archived=body.archived)
    db.commit()
    return {"updated": n}


# ----------------------------------------------------------------- inventory
@router.post("/inventory/{product_id}/adjust")
def adjust(product_id: uuid.UUID, body: StockAdjustIn, m: Member, db: DB, user: CurrentUser,
           idem: IdempotencyKey = None):
    def work():
        ev = inventory.adjust_stock(db, actor_id=user.id, business_id=m.business.id, product_id=product_id,
                                    delta=body.delta, set_to=body.set_quantity, reason=body.reason, source="manual",
                                    expected_version=body.expected_version,
                                    idempotency_key=f"adjust:{idem}" if idem else None)
        p = products.get_product(db, m.business.id, product_id)
        return {"event": inventory.serialize_event(ev, p.name, p.unit), "product": products.serialize_product(p)}

    return run_idempotent(db, user_id=user.id, key=idem, scope=f"inventory.adjust:{product_id}", fn=work, payload=body.model_dump())


@router.get("/inventory/events")
def events(m: Member, db: DB, product_id: uuid.UUID | None = None,
           event_type: Literal["initial", "increase", "decrease", "set", "sale", "return", "import", "undo"] | None = None,
           limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    items, total = inventory.list_events(db, m.business.id, product_id=product_id, event_type=event_type,
                                         limit=limit, offset=offset)
    return {"items": items, "total": total}


@router.post("/inventory/events/{event_id}/undo")
def undo(event_id: uuid.UUID, m: Member, db: DB, user: CurrentUser):
    ev = inventory.undo_event(db, actor_id=user.id, business_id=m.business.id, event_id=event_id)
    db.commit()
    p = products.get_product(db, m.business.id, ev.product_id)
    return {"event": inventory.serialize_event(ev, p.name, p.unit), "product": products.serialize_product(p)}


@router.get("/inventory/summary")
def inv_summary(m: Member, db: DB):
    return inventory.summary(db, m.business.id)


@router.get("/inventory/export")
def export(m: Member, db: DB, format: Literal["csv", "xlsx"] = "csv"):
    if format == "xlsx":
        data = inventory.export_xlsx(db, m.business.id)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        data = inventory.export_csv(db, m.business.id)
        media = "text/csv; charset=utf-8"
    return Response(content=data, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="inventar-{m.business.slug}.{format}"'})


@router.post("/inventory/import")
async def import_file(m: Member, db: DB, user: CurrentUser, settings: SettingsDep, dry_run: bool = True,
                      file: UploadFile = File(...)):
    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise ValidationFailed("Fayl çox böyükdür")
    table = inventory.read_upload(file.filename or "upload.csv", content)
    result = inventory.import_rows(db, actor_id=user.id, business_id=m.business.id, rows=table["rows"], dry_run=dry_run)
    if not dry_run:
        db.commit()
    return {**result, "filename": file.filename, "columns": table["columns"], "ignored": table["ignored"],
            "guessed": table["guessed"]}


# ----------------------------------------------------------------- sales
def _local_range(m, date_from: date | None, date_to: date | None) -> tuple[datetime | None, datetime | None]:
    tz = analytics.business_tz(m.business)
    start = datetime.combine(date_from, time.min, tzinfo=tz).astimezone(timezone.utc) if date_from else None
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc) if date_to else None
    if start and end and start >= end:
        raise ValidationFailed("Başlanğıc tarixi son tarixdən sonra ola bilməz")
    return start, end


@router.get("/sales")
def list_sales(m: Member, db: DB, kind: Literal["sale", "return"] | None = None, date_from: date | None = None,
               date_to: date | None = None, product_id: uuid.UUID | None = None,
               customer_id: uuid.UUID | None = None, q: str | None = Query(default=None, max_length=100),
               page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=200)):
    start, end = _local_range(m, date_from, date_to)
    items, total = sales.list_sales(db, m.business.id, kind=kind, date_from=start, date_to=end, product_id=product_id,
                                    customer_id=customer_id, q=q, page=page, page_size=page_size)
    return {"items": [sales.serialize_sale(s) for s in items], "total": total, "page": page, "page_size": page_size}


@router.post("/sales", status_code=201)
def create_sale(body: SaleIn, m: Member, db: DB, user: CurrentUser, idem: IdempotencyKey = None):
    def work():
        s = sales.record_sale(db, actor_id=user.id, business_id=m.business.id,
                              items=[i.model_dump() for i in body.items], customer_name=body.customer_name,
                              customer_id=body.customer_id, loyalty_stamp=body.loyalty_stamp, note=body.note, sold_at=body.sold_at, idempotency_key=f"sale:{idem}" if idem else None)
        return sales.sale_detail(db, s)

    return run_idempotent(db, user_id=user.id, key=idem, scope=f"sale.create:{m.business.id}", fn=work, payload=body.model_dump())


@router.get("/sales/export")
def export_sales(m: Member, db: DB, date_from: date | None = None, date_to: date | None = None):
    start, end = _local_range(m, date_from, date_to)
    items, page = [], 1
    while True:
        batch, total = sales.list_sales(db, m.business.id, date_from=start, date_to=end, page=page, page_size=200)
        items += batch
        if not batch or len(items) >= total:
            break
        page += 1
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["number", "kind", "status", "sold_at", "customer", "product", "quantity", "unit", "unit_price",
                "line_total", "unit_cost", "line_cost", "currency", "original_sale"])
    for s in items:
        for i in s.items:
            w.writerow([inventory.spreadsheet_safe(v) for v in [s.number, s.kind, s.status, iso(s.sold_at), s.customer_name or "", i.name, i.quantity, i.unit,
                        i.unit_price, i.line_total, i.unit_cost if i.unit_cost is not None else "",
                        i.line_cost if i.line_cost is not None else "", s.currency,
                        s.original.number if s.original else ""]])
    return Response(content=("﻿" + buf.getvalue()).encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="satislar-{m.business.slug}.csv"'})


@router.get("/sales/{sale_id}")
def get_sale(sale_id: uuid.UUID, m: Member, db: DB):
    return sales.sale_detail(db, sales.get_sale(db, m.business.id, sale_id))


@router.post("/sales/{sale_id}/returns", status_code=201)
def return_items(sale_id: uuid.UUID, body: ReturnIn, m: Member, db: DB, user: CurrentUser, idem: IdempotencyKey = None):
    def work():
        r = sales.return_sale(db, actor_id=user.id, business_id=m.business.id, sale_id=sale_id,
                              items=[i.model_dump() for i in body.items], reason=body.reason,
                              idempotency_key=f"return:{idem}" if idem else None)
        return {"return": sales.serialize_sale(r), "sale": sales.sale_detail(db, sales.get_sale(db, m.business.id, sale_id))}

    return run_idempotent(db, user_id=user.id, key=idem, scope=f"sale.return:{sale_id}", fn=work, payload=body.model_dump())


@router.post("/sales/{sale_id}/cancel", status_code=201)
def cancel_sale(sale_id: uuid.UUID, body: CancelIn, m: Member, db: DB, user: CurrentUser, idem: IdempotencyKey = None):
    def work():
        r = sales.return_sale(db, actor_id=user.id, business_id=m.business.id, sale_id=sale_id, items=None,
                              reason=body.reason or "Satış ləğv edildi",
                              idempotency_key=f"cancel:{idem}" if idem else None)
        return {"return": sales.serialize_sale(r), "sale": sales.sale_detail(db, sales.get_sale(db, m.business.id, sale_id))}

    return run_idempotent(db, user_id=user.id, key=idem, scope=f"sale.cancel:{sale_id}", fn=work, payload=body.model_dump())


# ----------------------------------------------------------------- customers & loyalty cards
@router.get("/customers")
def list_customers(m: Member, db: DB, q: str | None = Query(default=None, max_length=100), archived: bool = False,
                   segment: Literal["reward_ready", "inactive", "birthday"] | None = None,
                   sort: str = Query(default="-last_purchase", max_length=30),
                   page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=200)):
    rows, total = customers.list_customers(db, m.business.id, q=q, archived=archived, segment=segment, sort=sort,
                                           page=page, page_size=page_size)
    return {"items": [customers.serialize_customer(r["customer"], m.business, spent=r["spent"], orders=r["orders"],
                                                   last_purchase=r["last_purchase"]) for r in rows],
            "total": total, "page": page, "page_size": page_size,
            "segments": customers.segment_counts(db, m.business)}


@router.post("/customers", status_code=201)
def create_customer(body: CustomerIn, m: Member, db: DB, user: CurrentUser, idem: IdempotencyKey = None):
    def work():
        c = customers.create_customer(db, actor_id=user.id, business_id=m.business.id, data=body.model_dump())
        return customers.serialize_customer(c, m.business)

    return run_idempotent(db, user_id=user.id, key=idem, scope=f"customer.create:{m.business.id}", fn=work,
                          payload=body.model_dump())


@router.get("/customers/by-card/{token}")
def customer_by_card(token: str, m: Member, db: DB):
    c = customers.get_by_token(db, customers.token_from_text(token), m.business.id)
    return customers.customer_detail(db, m.business, c)


@router.get("/customers/{customer_id}")
def get_customer(customer_id: uuid.UUID, m: Member, db: DB):
    return customers.customer_detail(db, m.business, customers.get_customer(db, m.business.id, customer_id))


@router.patch("/customers/{customer_id}")
def update_customer(customer_id: uuid.UUID, body: CustomerUpdateIn, m: Member, db: DB, user: CurrentUser):
    c = customers.get_customer(db, m.business.id, customer_id, lock=True)
    customers.update_customer(db, actor_id=user.id, customer=c, data=body.model_dump(exclude_unset=True))
    db.commit()
    return customers.customer_detail(db, m.business, c)


@router.post("/customers/{customer_id}/loyalty/stamp")
def stamp_card(customer_id: uuid.UUID, body: StampIn, m: Member, db: DB, user: CurrentUser):
    c = customers.get_customer(db, m.business.id, customer_id, lock=True)
    added = customers.add_stamps(db, actor_id=user.id, business=m.business, customer=c, count=body.count,
                                 force=body.force)
    db.commit()
    return {"added": added, "customer": customers.customer_detail(db, m.business, c)}


@router.post("/customers/{customer_id}/loyalty/redeem")
def redeem_card(customer_id: uuid.UUID, m: Member, db: DB, user: CurrentUser):
    c = customers.get_customer(db, m.business.id, customer_id, lock=True)
    customers.redeem(db, actor_id=user.id, business=m.business, customer=c)
    db.commit()
    return {"customer": customers.customer_detail(db, m.business, c)}


@router.post("/customers/{customer_id}/loyalty/adjust")
def adjust_card(customer_id: uuid.UUID, body: StampAdjustIn, m: Member, db: DB, user: CurrentUser):
    if m.role not in ("owner", "admin"):
        raise Forbidden("Yalnız sahibi və ya admin düzəliş edə bilər")
    c = customers.get_customer(db, m.business.id, customer_id, lock=True)
    customers.adjust_stamps(db, actor_id=user.id, business=m.business, customer=c, stamps=body.stamps, note=body.note)
    db.commit()
    return {"customer": customers.customer_detail(db, m.business, c)}


@router.post("/customers/{customer_id}/loyalty/new-card")
def reissue_card(customer_id: uuid.UUID, m: Member, db: DB, user: CurrentUser):
    c = customers.get_customer(db, m.business.id, customer_id, lock=True)
    customers.reissue_card(db, actor_id=user.id, customer=c)
    db.commit()
    return {"customer": customers.customer_detail(db, m.business, c)}


# ----------------------------------------------------------------- analytics & audit
@router.get("/analytics/overview")
def overview(m: Member, db: DB, date_from: date | None = None, date_to: date | None = None,
             days: int = Query(default=30, ge=1, le=731), granularity: Literal["day", "week", "month"] = "day"):
    period = analytics.resolve_period(m.business, date_from=date_from, date_to=date_to, days=days)
    return analytics.overview(db, m.business, period, granularity=granularity)


@router.get("/analytics/sales")
def sales_report(m: Member, db: DB, date_from: date | None = None, date_to: date | None = None,
                 days: int = Query(default=30, ge=1, le=731), granularity: Literal["day", "week", "month"] = "day"):
    period = analytics.resolve_period(m.business, date_from=date_from, date_to=date_to, days=days)
    return analytics.sales_report(db, m.business, period, granularity=granularity)


@router.get("/analytics/reorder")
def reorder(m: Member, db: DB, days: int = Query(default=30, ge=7, le=365), cover_days: int = Query(default=14, ge=1, le=180),
            target_days: int = Query(default=30, ge=1, le=365)):
    return analytics.reorder_suggestions(db, m.business, days=days, cover_days=cover_days, target_days=target_days)


@router.get("/audit-logs")
def audit_logs(m: Member, db: DB, limit: int = Query(default=50, ge=1, le=200)):
    rows = db.scalars(select(AuditLog).where(AuditLog.business_id == m.business.id)
                      .order_by(AuditLog.created_at.desc()).limit(limit)).all()
    return {"items": [{"id": str(a.id), "action": a.action, "entity_type": a.entity_type, "entity_id": a.entity_id,
                       "before": a.before, "after": a.after, "via": a.via, "created_at": iso(a.created_at)} for a in rows]}
