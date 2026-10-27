from __future__ import annotations

import logging
import json
import re
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from app.api.deps import rate_limit, trusted_client_ip
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.api.routes import admin, ai, auth, business, public
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import SessionLocal, engine
from app.services import sadakat_webhook
from app.models import Base

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("hesabatim")


def _init_monitoring() -> None:
    """Monitoring hook: enable Sentry when SENTRY_DSN is set and sentry-sdk is installed."""
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return
    try:
        import sentry_sdk  # type: ignore

        sentry_sdk.init(dsn=dsn, traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")))
        log.info("Sentry monitoring enabled")
    except ImportError:
        log.warning("SENTRY_DSN set but sentry-sdk is not installed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    _init_monitoring()
    if settings.is_sqlite:
        # Zero-config local development: create tables directly. Postgres uses Alembic migrations.
        Base.metadata.create_all(engine)
    log.info("Hesabatım API ready (db=%s, llm=%s)", "sqlite" if settings.is_sqlite else "postgresql",
             "configured" if settings.llm_enabled else "offline-parser")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    sadakat_webhook.install(SessionLocal)
    app = FastAPI(title="Hesabatım API", version="0.1.0", lifespan=lifespan, dependencies=[Depends(rate_limit)],
                  docs_url="/docs" if settings.environment != "production" else None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "Content-Disposition"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        supplied = request.headers.get("x-request-id", "")
        rid = supplied if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", supplied) else uuid.uuid4().hex[:16]
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        log.info(json.dumps({"event": "http_request", "method": request.method, "path": request.url.path,
                             "status": response.status_code, "duration_ms": round((time.perf_counter() - start) * 1000),
                             "ip": trusted_client_ip(request) or "unknown", "request_id": rid}))
        return response

    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code,
                            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"error": {
            "code": "validation_failed", "message": "Daxil edilən məlumat yanlışdır", "details": details}})

    @app.exception_handler(IntegrityError)
    async def integrity_error(_: Request, exc: IntegrityError):
        log.warning("integrity error: %s", exc.orig)
        return JSONResponse(status_code=409, content={"error": {
            "code": "conflict", "message": "Əməliyyat məlumat bütövlüyü qaydalarını pozur", "details": None}})

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse(status_code=500, content={"error": {
            "code": "internal_error", "message": "Gözlənilməz xəta baş verdi", "details": None}})

    prefix = settings.api_prefix
    for r in (auth.router, business.router, ai.router, public.router):
        app.include_router(r, prefix=prefix)
    if settings.admin_path:  # the admin API exists only under its secret path
        app.include_router(admin.router, prefix=f"{prefix}/{settings.admin_path}")

    @app.get("/health")
    def health():
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return {"status": "ok"}

    return app


app = create_app()
