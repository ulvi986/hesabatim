"""Shared helpers: decimal handling, auditing, idempotency, slugs."""

from __future__ import annotations

import re
import hashlib
import json
import secrets
import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Callable, TypeVar

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, ValidationFailed
from app.db.base import utcnow as utc_now  # noqa: F401  (re-exported)
from app.models import AuditLog, IdempotencyRecord, Profile
from app.services.lexicon import fold

CENT = Decimal("0.01")
MILLI = Decimal("0.001")


def to_decimal(value: Any, field: str = "value") -> Decimal:
    if isinstance(value, Decimal):
        d = value
    else:
        try:
            d = Decimal(str(value).replace(",", ".").strip())
        except (InvalidOperation, ValueError) as exc:
            raise ValidationFailed(f"{field}: düzgün rəqəm deyil", details={"field": field}) from exc
    if not d.is_finite():
        raise ValidationFailed(f"{field}: düzgün rəqəm deyil", details={"field": field})
    return d


def money(value: Any) -> Decimal:
    return to_decimal(value, "price").quantize(CENT, rounding=ROUND_HALF_UP)


def qty(value: Any) -> Decimal:
    return to_decimal(value, "quantity").quantize(MILLI, rounding=ROUND_HALF_UP)


def money_str(value: Decimal | None) -> str | None:
    return None if value is None else str(Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP))


def qty_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    d = Decimal(value).quantize(MILLI, rounding=ROUND_HALF_UP).normalize()
    # avoid scientific notation for round numbers (e.g. 5E+2)
    return format(d, "f")


def aware(dt):
    """SQLite returns naive datetimes even for timezone=True columns; treat them as UTC."""
    from datetime import timezone

    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt) -> str | None:
    return aware(dt).isoformat() if dt else None


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")
    return s[:60] or "biznes"


def unique_slug(db: Session, model: Any, text: str) -> str:
    base = slugify(text)
    slug = base
    while db.scalar(select(model.id).where(model.slug == slug)) is not None:
        slug = f"{base}-{secrets.token_hex(2)}"
    return slug


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def audit(
    db: Session,
    *,
    business_id: uuid.UUID | None,
    actor_id: uuid.UUID | None,
    action: str,
    entity_type: str,
    entity_id: Any = None,
    before: dict | None = None,
    after: dict | None = None,
    via: str = "api",
) -> None:
    db.add(
        AuditLog(
            business_id=business_id,
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            before=to_jsonable(before) if before else None,
            after=to_jsonable(after) if after else None,
            via=via,
        )
    )


T = TypeVar("T")


def run_idempotent(db: Session, *, user_id: uuid.UUID, key: str | None, scope: str, fn: Callable[[], dict], payload: Any = None) -> dict:
    """Execute `fn` once per (user, key). Replays return the stored response.

    `fn` must not commit; this helper commits the work and the idempotency record atomically.
    """
    if not key:
        result = fn()
        db.commit()
        return result
    key = key[:120]
    if payload is not None:
        digest = hashlib.sha256(json.dumps(to_jsonable(payload), sort_keys=True).encode()).hexdigest()[:32]
        scope = f"{scope}:{digest}"
    db.scalar(select(Profile).where(Profile.id == user_id).with_for_update())
    existing = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.user_id == user_id, IdempotencyRecord.key == key))
    if existing:
        if existing.scope != scope:
            raise Conflict("Idempotency-Key başqa əməliyyat üçün istifadə olunub")
        return existing.response
    result = fn()
    db.add(IdempotencyRecord(user_id=user_id, key=key, scope=scope, response=to_jsonable(result)))
    try:
        db.commit()
    except IntegrityError:
        # A concurrent request with the same key won the race: discard our work, replay theirs.
        db.rollback()
        existing = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.user_id == user_id, IdempotencyRecord.key == key))
        if existing is None:
            raise
        if existing.scope != scope:
            raise Conflict("Idempotency-Key başqa əməliyyat üçün istifadə olunub")
        return existing.response
    return to_jsonable(result)
