"""Users, businesses, memberships and sessions."""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import Conflict, NotFound, Unauthorized, ValidationFailed
from app.core.security import hash_password, issue_token, verify_password
from app.models import Business, BusinessMember, LocalCredential, Profile, Sale
from app.core.config import get_settings
from app.services.common import audit, iso, unique_slug

_DUMMY_HASH = hash_password("dummy-credential-never-used")


@dataclass
class Membership:
    business: Business
    role: str


def register(db: Session, settings: Settings, *, email: str, password: str, full_name: str | None,
             business_name: str, currency: str = "AZN") -> tuple[Profile, str]:
    """Create the account and its business in one transaction: every user starts with an isolated workspace."""
    email = email.strip().lower()
    if db.scalar(select(Profile.id).where(Profile.email == email)):
        raise Conflict("Bu e-poçt artıq qeydiyyatdan keçib", code="email_taken")
    business_name = " ".join(business_name.split())
    if len(business_name) < 2:
        raise ValidationFailed("Biznes adı ən azı 2 simvol olmalıdır", details={"field": "business_name"})
    profile = Profile(email=email, full_name=(full_name or "").strip() or None)
    db.add(profile)
    db.flush()
    db.add(LocalCredential(user_id=profile.id, password_hash=hash_password(password)))
    business = Business(name=business_name, slug=unique_slug(db, Business, business_name), currency=currency)
    db.add(business)
    db.flush()
    db.add(BusinessMember(business_id=business.id, user_id=profile.id, role="owner"))
    audit(db, business_id=business.id, actor_id=profile.id, action="business.create", entity_type="business",
          entity_id=business.id, after={"name": business.name, "currency": currency})
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("Bu e-poçt artıq qeydiyyatdan keçib", code="email_taken") from exc
    return profile, issue_token(settings, profile.id, profile.token_version)


def login(db: Session, settings: Settings, *, email: str, password: str) -> tuple[Profile, str]:
    profile = db.scalar(select(Profile).where(Profile.email == email.strip().lower()))
    cred = db.get(LocalCredential, profile.id) if profile else None
    valid = verify_password(password, cred.password_hash if cred else _DUMMY_HASH)
    if not profile or not cred or not valid:
        raise Unauthorized("E-poçt və ya şifrə yanlışdır", code="invalid_credentials")
    return profile, issue_token(settings, profile.id, profile.token_version)


def change_password(db: Session, settings: Settings, profile: Profile, *, current: str, new: str) -> str:
    cred = db.get(LocalCredential, profile.id)
    if cred is None or not verify_password(current, cred.password_hash):
        raise ValidationFailed("Hazırkı şifrə yanlışdır", code="wrong_password", details={"field": "current_password"})
    cred.password_hash = hash_password(new)
    profile.token_version += 1  # every other session is signed out
    db.commit()
    return issue_token(settings, profile.id, profile.token_version)


def revoke_sessions(db: Session, profile: Profile) -> None:
    profile.token_version += 1
    db.commit()


def get_profile_for_token(db: Session, user_id: uuid.UUID, token_version: int) -> Profile:
    profile = db.get(Profile, user_id)
    if profile is None or profile.token_version != token_version:
        raise Unauthorized("Sessiyanın vaxtı bitib. Yenidən daxil olun.")
    return profile


def memberships(db: Session, user_id: uuid.UUID) -> list[Membership]:
    rows = db.execute(
        select(Business, BusinessMember.role)
        .join(BusinessMember, BusinessMember.business_id == Business.id)
        .where(BusinessMember.user_id == user_id)
        .order_by(BusinessMember.created_at)
    ).all()
    return [Membership(business=b, role=r) for b, r in rows]


def require_member(db: Session, user_id: uuid.UUID, business_id: uuid.UUID, *,
                   roles: tuple[str, ...] | None = None) -> Membership:
    """Central tenant guard. Every business-scoped operation goes through this."""
    row = db.execute(
        select(Business, BusinessMember.role)
        .join(BusinessMember, BusinessMember.business_id == Business.id)
        .where(BusinessMember.user_id == user_id, Business.id == business_id)
    ).first()
    if row is None:
        # Do not reveal whether the business exists.
        raise NotFound("Biznes tapılmadı")
    business, role = row
    if roles and role not in roles:
        from app.core.errors import Forbidden

        raise Forbidden("Bu əməliyyat üçün icazəniz yoxdur")
    return Membership(business=business, role=role)


def update_business(db: Session, actor: Profile, business: Business, data: dict) -> Business:
    if "currency" in data and data["currency"] != business.currency:
        has_sales = db.scalar(select(func.count()).select_from(Sale).where(Sale.business_id == business.id)) or 0
        if has_sales:
            raise Conflict("Satışlar qeydə alındıqdan sonra valyutanı dəyişmək olmaz: köhnə məbləğlər səhv görünərdi.",
                           code="currency_locked")
    if "timezone" in data:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(data["timezone"])
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValidationFailed("Naməlum saat qurşağı", details={"field": "timezone"}) from exc
    if "theme" in data:
        data["theme"] = {k: v for k, v in (data["theme"] or {}).items() if v is not None} or None
    before = {k: getattr(business, k) for k in data}
    for k, v in data.items():
        setattr(business, k, v)
    audit(db, business_id=business.id, actor_id=actor.id, action="business.update", entity_type="business",
          entity_id=business.id, before=before, after=data)
    db.commit()
    return business


# Logos are kept small and inline (data: URL) so no file storage is needed. SVG is refused: it can carry script.
LOGO_MAX_BYTES = 512 * 1024
_LOGO_SIGNATURES = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
}


def _image_type(content: bytes) -> str | None:
    for sig, mime in _LOGO_SIGNATURES.items():
        if content.startswith(sig):
            return mime
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def set_logo(db: Session, actor: Profile, business: Business, content: bytes | None) -> Business:
    if content is None:
        business.logo = None
    else:
        if len(content) > LOGO_MAX_BYTES:
            raise ValidationFailed("Loqo 512 KB-dan böyük ola bilməz", code="logo_too_large")
        mime = _image_type(content)
        if mime is None:
            raise ValidationFailed("Yalnız PNG, JPG, WebP və ya GIF şəkil yükləyin", code="logo_bad_type")
        business.logo = f"data:{mime};base64,{base64.b64encode(content).decode()}"
    audit(db, business_id=business.id, actor_id=actor.id, action="business.logo", entity_type="business",
          entity_id=business.id, after={"logo": "removed" if content is None else f"{len(content)} bytes"})
    db.commit()
    return business


def serialize_business(b: Business) -> dict:
    return {
        "id": str(b.id), "name": b.name, "slug": b.slug, "currency": b.currency, "timezone": b.timezone,
        "phone": b.phone, "address": b.address, "onboarding_completed": b.onboarding_completed,
        "logo": b.logo, "theme": b.theme or {},
        "loyalty_enabled": b.loyalty_enabled, "loyalty_stamps_needed": b.loyalty_stamps_needed,
        "loyalty_reward": b.loyalty_reward,
        "signup_url": signup_url(b),
        "created_at": iso(b.created_at),
    }


def signup_url(b: Business) -> str | None:
    """The business's customer sign-up page on the SadakatKarti card site (its QR goes on the counter)."""
    base = get_settings().sadakat_public_url
    return f"{base.rstrip('/')}/k/{b.slug}" if base else None


def serialize_me(db: Session, profile: Profile) -> dict:
    ms = memberships(db, profile.id)
    return {
        "id": str(profile.id),
        "email": profile.email,
        "full_name": profile.full_name,
        "phone": profile.phone,
        "locale": profile.locale,
        "memberships": [{"role": m.role, "business": serialize_business(m.business)} for m in ms],
    }
