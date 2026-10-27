"""Read-only admin panel API.

Defence in depth:
* it is mounted only at the secret /api/v1/<ADMIN_PATH>, never at a guessable path;
* the whole router answers 404 unless the admin is configured and the client IP is in ADMIN_ALLOWED_IPS;
* every request, allowed or not, is written to the audit log and the application log;
* the password is checked against an argon2id hash only; failures lock the client IP out;
* sessions are short-lived JWTs signed with a separate secret, in an HttpOnly SameSite=Strict cookie;
* there are no write endpoints, and the admin DB session refuses to flush any change."""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import secrets
import threading
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import String, cast, event, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import DB, SettingsDep, trusted_client_ip
from app.core.errors import AppError, Forbidden, NotFound, Unauthorized
from app.core.security import hash_password, verify_password
from app.db.session import SessionLocal
from app.models import AuditLog, Base

log = logging.getLogger("hesabatim.admin")
# Mounted by main.py under the secret ADMIN_PATH.
router = APIRouter(tags=["admin"], include_in_schema=False)

COOKIE = "hesabatim_admin"
AUDIENCE = "admin"
SENSITIVE = ("password", "hash", "secret", "token")
# A real hash, so unknown usernames cost the same time as wrong passwords.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


class Locked(AppError):
    status_code = 423
    code = "admin_locked"


class _Lockout:
    def __init__(self):
        self._failures: dict[str, list[float]] = {}
        self._locked: dict[str, float] = {}
        self._lock = threading.Lock()

    def locked(self, ip: str) -> bool:
        with self._lock:
            return self._locked.get(ip, 0) > time.time()

    def fail(self, ip: str, max_failures: int, minutes: int) -> bool:
        now = time.time()
        with self._lock:
            recent = [t for t in self._failures.get(ip, []) if now - t < minutes * 60] + [now]
            self._failures[ip] = recent
            if len(recent) >= max_failures:
                self._locked[ip] = now + minutes * 60
                return True
            return False

    def success(self, ip: str) -> None:
        with self._lock:
            self._failures.pop(ip, None)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
            self._locked.clear()


lockout = _Lockout()


def _ip_allowed(ip: str, allowed: list[str]) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
        if getattr(addr, "ipv4_mapped", None):
            addr = addr.ipv4_mapped
        return any(addr in ipaddress.ip_network(net, strict=False) for net in allowed)
    except ValueError:
        return False


def _record(action: str, ip: str, request: Request | None = None, extra: dict[str, Any] | None = None) -> None:
    if request is not None:
        extra = {"method": request.method, "path": request.url.path,
                 "user_agent": request.headers.get("user-agent", "")[:200], **(extra or {})}
    log.warning(json.dumps({"event": action, "ip": ip, **(extra or {})}, ensure_ascii=False))
    with SessionLocal() as db:
        db.add(AuditLog(action=action, entity_type="admin", entity_id=ip[:64], after=extra, via="system"))
        db.commit()


def gate(request: Request, settings: SettingsDep) -> str:
    ip = trusted_client_ip(request)
    if not settings.admin_enabled or ip is None or not _ip_allowed(ip, settings.admin_allowed_ips):
        _record("admin_blocked_ip", ip or "unknown", request)
        raise NotFound("Səhifə tapılmadı")
    if lockout.locked(ip):
        _record("admin_locked_request", ip, request)
        raise Locked("Giriş müvəqqəti bloklanıb")
    return ip


ClientIP = Annotated[str, Depends(gate)]


def admin_session(request: Request, settings: SettingsDep, ip: ClientIP) -> str:
    token = request.cookies.get(COOKIE)
    if not token:
        raise Unauthorized("Admin girişi tələb olunur")
    try:
        claims = jwt.decode(token, settings.admin_jwt_secret, algorithms=["HS256"], audience=AUDIENCE,
                            issuer="hesabatim", options={"require": ["exp", "sub", "ip"]})
    except jwt.PyJWTError as exc:
        raise Unauthorized("Admin sessiyası etibarsızdır") from exc
    if claims["ip"] != ip:  # a stolen cookie is useless from another address
        _record("admin_session_ip_mismatch", ip, request)
        raise Unauthorized("Admin sessiyası etibarsızdır")
    if request.method == "GET" and not request.url.path.endswith("/session"):
        _record("admin_view", ip, request, {"query": str(request.url.query)[:200]})
    return claims["sub"]


Admin = Annotated[str, Depends(admin_session)]


def read_only_db(db: DB, _: Admin) -> Session:
    def refuse(*_args):
        raise Forbidden("Admin paneli yalnız oxumaq üçündür")
    event.listen(db, "before_flush", refuse)
    return db


ReadDB = Annotated[Session, Depends(read_only_db)]


def _origin_ok(request: Request, settings) -> None:
    if request.headers.get("origin") not in settings.cors_origins:
        raise Forbidden("Sorğunun mənbəyi təsdiqlənmədi")


def _cookie_path(settings) -> str:
    return f"{settings.api_prefix}/{settings.admin_path}"


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


@router.post("/login")
def login(body: dict[str, str], request: Request, response: Response, settings: SettingsDep, ip: ClientIP):
    _origin_ok(request, settings)
    username, password = str(body.get("username", ""))[:200], str(body.get("password", ""))[:200]
    name_ok = hmac.compare_digest(username.encode(), (settings.admin_username or "").encode())
    pass_ok = verify_password(password, settings.admin_password_hash if name_ok else _DUMMY_HASH)
    if not (name_ok and pass_ok):
        locked = lockout.fail(ip, settings.admin_max_failures, settings.admin_lockout_minutes)
        _record("admin_login_locked" if locked else "admin_login_failed", ip, request,
                {"username_ok": name_ok})  # never log the typed name: it may be a mistyped password
        if locked:
            raise Locked("Giriş müvəqqəti bloklanıb")
        raise Unauthorized("İstifadəçi adı və ya şifrə yanlışdır")
    lockout.success(ip)
    _record("admin_login", ip, request)
    now = datetime.now(timezone.utc)
    token = jwt.encode({"sub": username, "ip": ip, "iat": now, "exp": now + timedelta(minutes=settings.admin_session_minutes),
                        "iss": "hesabatim", "aud": AUDIENCE}, settings.admin_jwt_secret, algorithm="HS256")
    response.set_cookie(COOKIE, token, httponly=True, secure=settings.environment == "production", samesite="strict",
                        max_age=settings.admin_session_minutes * 60, path=_cookie_path(settings))
    _no_store(response)
    return {"username": username}


@router.post("/logout", status_code=204)
def logout(request: Request, settings: SettingsDep, ip: ClientIP):
    _origin_ok(request, settings)
    _record("admin_logout", ip, request)
    response = Response(status_code=204)
    response.delete_cookie(COOKIE, path=_cookie_path(settings))
    return response


@router.get("/session")
def session(response: Response, admin: Admin):
    _no_store(response)
    return {"username": admin}


def _tables():
    return {t.name: t for t in Base.metadata.sorted_tables}


@router.get("/overview")
def overview(response: Response, db: ReadDB):
    _no_store(response)
    counts = {name: db.scalar(select(func.count()).select_from(t)) for name, t in _tables().items()}
    attempts = db.scalars(select(AuditLog).where(AuditLog.entity_type == "admin")
                          .order_by(AuditLog.created_at.desc()).limit(50)).all()
    return {"tables": counts, "security_events": [
        {"action": a.action, "ip": a.entity_id, "at": a.created_at.isoformat(), "details": a.after} for a in attempts]}


def _value(column: str, value: Any) -> Any:
    if value is None:
        return None
    if any(s in column.lower() for s in SENSITIVE) and column != "token_version":
        return "•••• gizli"
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, bytes):
        return f"<{len(value)} bayt>"
    return value


@router.get("/tables/{name}")
def table_rows(name: str, response: Response, db: ReadDB, page: int = Query(1, ge=1, le=100000),
               size: int = Query(50, ge=1, le=200), q: str = Query("", max_length=100)):
    _no_store(response)
    table = _tables().get(name)
    if table is None:
        raise NotFound("Cədvəl tapılmadı")
    stmt = select(table)
    if q:
        searchable = [c for c in table.columns if not any(s in c.name.lower() for s in SENSITIVE)]
        stmt = stmt.where(or_(*[cast(c, String).ilike(f"%{q}%") for c in searchable]))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    order = table.c.created_at.desc() if "created_at" in table.c else list(table.primary_key.columns)[0]
    rows = db.execute(stmt.order_by(order).offset((page - 1) * size).limit(size)).mappings().all()
    columns = [c.name for c in table.columns]
    return {"name": name, "columns": columns, "total": total, "page": page, "size": size,
            "rows": [{c: _value(c, r[c]) for c in columns} for r in rows]}
