"""Product catalogue of a business.

Mutating functions flush but never commit; the caller owns the transaction
(see services.common.run_idempotent)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.models import Category, Inventory, InventoryEvent, Product, ProductAlias
from app.models.entities import UNITS
from app.services.common import CENT, audit, aware, iso, money, money_str, qty, qty_str
from app.services.lexicon import analyze, build_search_text, fold

EDITABLE_FIELDS = {
    "name", "sku", "category_id", "description", "unit", "purchase_price", "selling_price", "low_stock_threshold",
}


# Products with this many units or fewer always need attention, whatever their own minimum is.
ATTENTION_STOCK = 5


def attention_threshold(threshold: int) -> int:
    return max(threshold, ATTENTION_STOCK)


_needs_attention = or_(Inventory.quantity <= Product.low_stock_threshold, Inventory.quantity <= ATTENTION_STOCK)


def stock_status(quantity: Decimal, threshold: int) -> str:
    if quantity <= 0:
        return "out_of_stock"
    if quantity <= attention_threshold(threshold):
        return "low"
    return "in_stock"


def _qty_of(p: Product) -> Decimal:
    return Decimal(p.inventory.quantity) if p.inventory else Decimal(0)


def cost_value(p: Product) -> Decimal | None:
    """Stock valued at the recorded purchase price. None when no purchase price is recorded:
    a value is never guessed from the selling price."""
    if p.purchase_price is None:
        return None
    return (_qty_of(p) * Decimal(p.purchase_price)).quantize(CENT)


def retail_value(p: Product) -> Decimal | None:
    if p.selling_price is None:
        return None
    return (_qty_of(p) * Decimal(p.selling_price)).quantize(CENT)


def _refresh_search_text(db: Session, p: Product) -> None:
    aliases = db.scalars(select(ProductAlias.code).where(ProductAlias.product_id == p.id)).all() if p.id else []
    p.search_text = build_search_text(p.name, p.description, p.sku, " ".join(aliases))


def _next_sku(db: Session, business_id: uuid.UUID, name: str) -> str:
    letters = "".join(ch for ch in fold(name).upper() if "A" <= ch <= "Z")[:3] or "PRD"
    n = (db.scalar(select(func.count()).select_from(Product).where(Product.business_id == business_id)) or 0) + 1
    while True:
        sku = f"{letters}-{n:04d}"
        if not db.scalar(select(Product.id).where(Product.business_id == business_id, Product.sku == sku)):
            return sku
        n += 1


def _validate_fields(db: Session, business_id: uuid.UUID, data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    if "name" in out:
        out["name"] = " ".join((out["name"] or "").split())
        if len(out["name"]) < 2:
            raise ValidationFailed("Məhsul adı ən azı 2 simvol olmalıdır", details={"field": "name"})
    if "unit" in out and out["unit"] not in UNITS:
        raise ValidationFailed(f"Naməlum ölçü vahidi: {out['unit']}", details={"field": "unit"})
    for f in ("purchase_price", "selling_price"):
        if f in out and out[f] is not None:
            out[f] = money(out[f])
            if out[f] < 0:
                raise ValidationFailed("Qiymət mənfi ola bilməz", details={"field": f})
    if "low_stock_threshold" in out and out["low_stock_threshold"] is not None:
        out["low_stock_threshold"] = int(out["low_stock_threshold"])
        if out["low_stock_threshold"] < 0:
            raise ValidationFailed("Minimum stok həddi mənfi ola bilməz", details={"field": "low_stock_threshold"})
    if "sku" in out and out["sku"] is not None:
        out["sku"] = out["sku"].strip()[:64] or None
    if out.get("category_id") is not None:
        cid = out["category_id"]
        cid = uuid.UUID(cid) if isinstance(cid, str) else cid
        if not db.scalar(select(Category.id).where(Category.id == cid, Category.business_id == business_id)):
            raise ValidationFailed("Kateqoriya tapılmadı", details={"field": "category_id"})
        out["category_id"] = cid
    return out


def get_product(db: Session, business_id: uuid.UUID, product_id: uuid.UUID, *, lock: bool = False) -> Product:
    stmt = (
        select(Product)
        .where(Product.id == product_id, Product.business_id == business_id)
        .options(selectinload(Product.inventory), selectinload(Product.category))
    )
    if lock:
        stmt = stmt.with_for_update()
    p = db.scalar(stmt)
    if p is None:
        raise NotFound("Məhsul tapılmadı")
    return p


def create_product(
    db: Session,
    *,
    actor_id: uuid.UUID,
    business_id: uuid.UUID,
    data: dict[str, Any],
    initial_quantity: Any = 0,
    aliases: list[str] | None = None,
    via: str = "api",
    ai_action_id: uuid.UUID | None = None,
) -> Product:
    data = _validate_fields(db, business_id, {k: v for k, v in data.items() if k in EDITABLE_FIELDS})
    if "name" not in data:
        raise ValidationFailed("Məhsul adı tələb olunur", details={"field": "name"})
    init_qty = qty(initial_quantity or 0)
    if init_qty < 0:
        raise ValidationFailed("Miqdar mənfi ola bilməz", details={"field": "quantity"})
    sku = data.pop("sku", None) or _next_sku(db, business_id, data["name"])
    if db.scalar(select(Product.id).where(Product.business_id == business_id, Product.sku == sku)):
        raise Conflict(f"Bu SKU artıq mövcuddur: {sku}", code="sku_taken")

    product = Product(business_id=business_id, sku=sku, **data)
    db.add(product)
    db.flush()
    for code in aliases or []:
        code = code.strip()[:120]
        if code and not db.scalar(select(ProductAlias.id).where(ProductAlias.business_id == business_id,
                                                               ProductAlias.code == code)):
            db.add(ProductAlias(business_id=business_id, product_id=product.id, code=code))
    inv = Inventory(business_id=business_id, product_id=product.id, quantity=init_qty)
    db.add(inv)
    db.flush()
    db.add(
        InventoryEvent(
            business_id=business_id, product_id=product.id, inventory_id=inv.id, event_type="initial",
            source="ai" if via == "ai" else ("import" if via == "import" else "manual"),
            delta=init_qty, quantity_before=Decimal(0), quantity_after=init_qty, reason="İlkin qalıq",
            actor_id=actor_id, ai_action_id=ai_action_id,
        )
    )
    _refresh_search_text(db, product)
    audit(db, business_id=business_id, actor_id=actor_id, action="product.create", entity_type="product",
          entity_id=product.id, after={"name": product.name, "sku": sku, "quantity": init_qty,
                                       "selling_price": product.selling_price,
                                       "purchase_price": product.purchase_price}, via=via)
    db.flush()
    db.refresh(product)
    return product


def update_product(db: Session, *, actor_id: uuid.UUID, product: Product, data: dict[str, Any], via: str = "api") -> Product:
    data = _validate_fields(db, product.business_id, {k: v for k, v in data.items() if k in EDITABLE_FIELDS})
    if "sku" in data:
        if not data["sku"]:
            data.pop("sku")
        elif data["sku"] != product.sku and db.scalar(
            select(Product.id).where(Product.business_id == product.business_id, Product.sku == data["sku"])
        ):
            raise Conflict(f"Bu SKU artıq mövcuddur: {data['sku']}", code="sku_taken")
    before = {k: getattr(product, k) for k in data}
    changed = {k: v for k, v in data.items() if before.get(k) != v}
    if not changed:
        return product
    for k, v in changed.items():
        setattr(product, k, v)
    _refresh_search_text(db, product)
    action = "product.price_change" if set(changed) <= {"selling_price", "purchase_price"} else "product.update"
    audit(db, business_id=product.business_id, actor_id=actor_id, action=action, entity_type="product",
          entity_id=product.id, before={k: before[k] for k in changed}, after=changed, via=via)
    db.flush()
    return product


def archive_products(db: Session, *, actor_id: uuid.UUID, business_id: uuid.UUID, product_ids: list[uuid.UUID],
                     archived: bool = True) -> int:
    """Products are archived, never deleted: sales and ledger rows keep pointing at them."""
    products = db.scalars(select(Product).where(Product.business_id == business_id, Product.id.in_(product_ids))).all()
    for p in products:
        if p.is_archived == archived:
            continue
        p.is_archived = archived
        audit(db, business_id=business_id, actor_id=actor_id, action="product.archive" if archived else "product.restore",
              entity_type="product", entity_id=p.id)
    db.flush()
    return len(products)


def list_products(
    db: Session,
    business_id: uuid.UUID,
    *,
    q: str | None = None,
    category_id: uuid.UUID | None = None,
    stock: str | None = None,
    include_archived: bool = False,
    archived_only: bool = False,
    sort: str = "-updated_at",
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[Product], int]:
    stmt = select(Product).join(Inventory, Inventory.product_id == Product.id).where(Product.business_id == business_id)
    if archived_only:
        stmt = stmt.where(Product.is_archived.is_(True))
    elif not include_archived:
        stmt = stmt.where(Product.is_archived.is_(False))
    if category_id:
        stmt = stmt.where(Product.category_id == category_id)
    if q and q.strip():
        concepts, rest = analyze(q)
        terms = [f"#{c}" for c in concepts] + rest
        if not terms:
            terms = [fold(q)]
        in_category = select(Category.id).where(Category.business_id == business_id,
                                                Category.name.ilike(f"%{q.strip()}%"))
        stmt = stmt.where(or_(*[Product.search_text.ilike(f"%{t}%") for t in terms],
                              Product.sku.ilike(f"%{q.strip()}%"), Product.name.ilike(f"%{q.strip()}%"),
                              Product.category_id.in_(in_category)))
    if stock == "low":
        stmt = stmt.where(Inventory.quantity > 0, _needs_attention)
    elif stock == "out":
        stmt = stmt.where(Inventory.quantity <= 0)
    elif stock == "in":
        stmt = stmt.where(~_needs_attention)
    elif stock == "attention":
        stmt = stmt.where(_needs_attention)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    sort_map = {
        "name": Product.name, "sku": Product.sku, "quantity": Inventory.quantity, "selling_price": Product.selling_price,
        "purchase_price": Product.purchase_price, "updated_at": Product.updated_at, "created_at": Product.created_at,
    }
    desc = sort.startswith("-")
    col = sort_map.get(sort.lstrip("-"), Product.updated_at)
    stmt = stmt.order_by(col.desc() if desc else col.asc(), Product.id)
    stmt = stmt.options(selectinload(Product.inventory), selectinload(Product.category))
    page_size = max(1, min(page_size, 500))
    items = db.scalars(stmt.offset((max(page, 1) - 1) * page_size).limit(page_size)).all()
    return list(items), total


def find_matching_products(db: Session, business_id: uuid.UUID, text: str, limit: int = 3) -> list[tuple[Product, float]]:
    """Fuzzy match free text (e.g. 'taxta qapı') to the business's existing products by concept/stem overlap."""
    concepts, rest = analyze(text)
    keys = {f"#{c}" for c in concepts} | set(rest)
    if not keys:
        return []
    candidates = db.scalars(
        select(Product)
        .where(Product.business_id == business_id, Product.is_archived.is_(False),
               or_(*[Product.search_text.ilike(f"%{k}%") for k in keys]))
        .options(selectinload(Product.inventory))
        .limit(200)
    ).all()
    scored: list[tuple[Product, float]] = []
    for p in candidates:
        p_tokens = set(p.search_text.split())
        p_concepts = {t for t in p_tokens if t.startswith("#")}
        overlap = len(keys & p_tokens)
        # penalise products that carry extra distinguishing concepts the query did not mention
        extra = len(p_concepts - keys)
        score = overlap / max(len(keys), 1) - 0.15 * extra
        if overlap and score > 0.3:
            scored.append((p, round(score, 3)))
    scored.sort(key=lambda x: -x[1])
    return scored[:limit]


def serialize_product(p: Product) -> dict[str, Any]:
    q = _qty_of(p)
    return {
        "id": str(p.id),
        "sku": p.sku,
        "name": p.name,
        "description": p.description,
        "category": {"id": str(p.category.id), "name": p.category.name} if p.category else None,
        "unit": p.unit,
        "quantity": qty_str(q),
        "purchase_price": money_str(p.purchase_price),
        "selling_price": money_str(p.selling_price),
        "inventory_value": money_str(cost_value(p)),
        "retail_value": money_str(retail_value(p)),
        "low_stock_threshold": p.low_stock_threshold,
        "stock_status": stock_status(q, p.low_stock_threshold),
        "is_archived": p.is_archived,
        "inventory_version": p.inventory.version if p.inventory else 0,
        "created_at": iso(p.created_at),
        "updated_at": iso(max(aware(p.updated_at), aware(p.inventory.updated_at))) if p.inventory else iso(p.updated_at),
    }
