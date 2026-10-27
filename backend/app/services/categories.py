"""Product categories. Each business defines its own; nothing is seeded."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.models import Category, Product
from app.services.common import audit
from app.services.lexicon import fold


def _key(name: str) -> str:
    return fold(name)[:120]


def _clean(name: str) -> str:
    name = " ".join((name or "").split())
    if len(name) < 2:
        raise ValidationFailed("Kateqoriya adı ən azı 2 simvol olmalıdır", details={"field": "name"})
    return name[:120]


def list_categories(db: Session, business_id: uuid.UUID) -> list[dict]:
    counts = dict(db.execute(
        select(Product.category_id, func.count()).where(Product.business_id == business_id, Product.is_archived.is_(False))
        .group_by(Product.category_id)
    ).all())
    cats = db.scalars(select(Category).where(Category.business_id == business_id).order_by(Category.name)).all()
    return [serialize_category(c, counts.get(c.id, 0)) for c in cats]


def get_category(db: Session, business_id: uuid.UUID, category_id: uuid.UUID) -> Category:
    c = db.scalar(select(Category).where(Category.id == category_id, Category.business_id == business_id))
    if c is None:
        raise NotFound("Kateqoriya tapılmadı")
    return c


def find_by_name(db: Session, business_id: uuid.UUID, name: str) -> Category | None:
    return db.scalar(select(Category).where(Category.business_id == business_id, Category.name_key == _key(name)))


def create_category(db: Session, *, actor_id: uuid.UUID, business_id: uuid.UUID, name: str, via: str = "api") -> Category:
    name = _clean(name)
    if find_by_name(db, business_id, name):
        raise Conflict(f"«{name}» kateqoriyası artıq mövcuddur", code="category_exists")
    c = Category(business_id=business_id, name=name, name_key=_key(name))
    db.add(c)
    db.flush()
    audit(db, business_id=business_id, actor_id=actor_id, action="category.create", entity_type="category",
          entity_id=c.id, after={"name": name}, via=via)
    return c


def get_or_create(db: Session, *, actor_id: uuid.UUID, business_id: uuid.UUID, name: str, via: str = "api") -> Category:
    return find_by_name(db, business_id, _clean(name)) or create_category(
        db, actor_id=actor_id, business_id=business_id, name=name, via=via)


def rename_category(db: Session, *, actor_id: uuid.UUID, category: Category, name: str) -> Category:
    name = _clean(name)
    other = find_by_name(db, category.business_id, name)
    if other and other.id != category.id:
        raise Conflict(f"«{name}» kateqoriyası artıq mövcuddur", code="category_exists")
    before = category.name
    category.name, category.name_key = name, _key(name)
    audit(db, business_id=category.business_id, actor_id=actor_id, action="category.rename", entity_type="category",
          entity_id=category.id, before={"name": before}, after={"name": name})
    db.flush()
    return category


def delete_category(db: Session, *, actor_id: uuid.UUID, category: Category) -> None:
    """Products in the category become uncategorised (FK ON DELETE SET NULL); no product is removed."""
    audit(db, business_id=category.business_id, actor_id=actor_id, action="category.delete", entity_type="category",
          entity_id=category.id, before={"name": category.name})
    for p in db.scalars(select(Product).where(Product.category_id == category.id)).all():
        p.category_id = None
    db.delete(category)
    db.flush()


def serialize_category(c: Category, product_count: int | None = None) -> dict:
    out = {"id": str(c.id), "name": c.name}
    if product_count is not None:
        out["product_count"] = product_count
    return out
