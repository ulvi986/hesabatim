"""Password hashing and token issuing/verification."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import Settings
from app.core.errors import Unauthorized

_hasher = PasswordHasher()
ISSUER = "hesabatim"
AUDIENCE = "authenticated"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


@dataclass(frozen=True)
class TokenIdentity:
    user_id: uuid.UUID
    token_version: int


def issue_token(settings: Settings, user_id: uuid.UUID, token_version: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "ver": token_version,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_ttl_minutes),
        "iss": ISSUER,
        "aud": AUDIENCE,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def verify_token(settings: Settings, token: str) -> TokenIdentity:
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], audience=AUDIENCE, issuer=ISSUER,
                            options={"require": ["exp", "sub", "iat"]})
        return TokenIdentity(user_id=uuid.UUID(claims["sub"]), token_version=int(claims.get("ver", 0)))
    except (jwt.PyJWTError, KeyError, ValueError, TypeError) as exc:
        raise Unauthorized("Sessiyanın vaxtı bitib və ya etibarsızdır. Yenidən daxil olun.") from exc
