from __future__ import annotations

import hmac
import uuid
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import Unauthorized
from app.core.rate_limit import limiter
from app.core.security import verify_token
from app.db.session import get_db
from app.models import Profile
from app.services.accounts import Membership, get_profile_for_token, require_member

DB = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def trusted_client_ip(request: Request) -> str | None:
    """The browser's address, or None when it cannot be known.

    Behind our own Next.js frontend the address comes from X-Client-IP, trusted only together with the
    frontend's shared secret (the frontend overwrites both headers on every request). A direct connection
    with no forwarding headers (local development) is its socket address. Anything else is unknown."""
    secret = get_settings().frontend_proxy_secret
    supplied = request.headers.get("x-frontend-secret")
    if secret and supplied:
        if not hmac.compare_digest(supplied.encode(), secret.encode()):
            return None
        return request.headers.get("x-client-ip", "").strip()[:64] or None
    if any(h.startswith("x-forwarded-") or h in ("forwarded", "x-real-ip", "x-client-ip") for h in request.headers.keys()):
        return None
    return request.client.host if request.client else None


def _client_key(request: Request) -> str:
    # The card site may reach us through our own frontend, so its end-user address takes precedence.
    secret = get_settings().proxy_secret
    supplied = request.headers.get("x-proxy-secret")
    if secret and supplied and hmac.compare_digest(supplied.encode(), secret.encode()):
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded:
            return f"proxied:{forwarded[:64]}"
    if (ip := trusted_client_ip(request)) and request.headers.get("x-frontend-secret"):
        return f"client:{ip}"
    return request.client.host if request.client else "unknown"


def rate_limit(request: Request, settings: SettingsDep) -> None:
    limiter.hit(f"ip:{_client_key(request)}", settings.rate_limit_per_minute)


def auth_rate_limit(request: Request, settings: SettingsDep) -> None:
    """Brute-force protection for login/registration."""
    limiter.hit(f"auth:{_client_key(request)}", settings.auth_rate_limit_per_minute)


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return None


def current_user(request: Request, db: DB, settings: SettingsDep, authorization: Annotated[str | None, Header()] = None) -> Profile:
    token = _bearer(authorization)
    if not token:
        token = request.cookies.get("hesabatim_session")
        if token and request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("origin") not in settings.cors_origins:
                from app.core.errors import Forbidden
                raise Forbidden("Sorğunun mənbəyi təsdiqlənmədi")
    if not token:
        raise Unauthorized("Daxil olmaq tələb olunur")
    ident = verify_token(settings, token)
    return get_profile_for_token(db, ident.user_id, ident.token_version)


CurrentUser = Annotated[Profile, Depends(current_user)]


def business_membership(business_id: uuid.UUID, db: DB, user: CurrentUser) -> Membership:
    return require_member(db, user.id, business_id)


Member = Annotated[Membership, Depends(business_membership)]
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key", max_length=120)]
