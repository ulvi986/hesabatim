"""Application settings, loaded from environment variables (see .env.example)."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Hesabatım"
    environment: Literal["development", "test", "production"] = "development"
    api_prefix: str = "/api/v1"

    # Database. SQLite is the zero-config development default; production uses PostgreSQL.
    database_url: str = "sqlite:///./hesabatim_dev.db"

    # CORS
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:3000"])

    # --- Authentication (backend-issued HS256 JWTs, argon2id password hashes) ---
    jwt_secret: str = "dev-insecure-change-me-please-32bytes!!"
    jwt_ttl_minutes: int = 60 * 24 * 7

    # --- Uploads (CSV / XLSX import) ---
    max_upload_bytes: int = 5 * 1024 * 1024

    # --- AI ---
    # When llm_api_key is empty the assistant runs the deterministic offline parser
    # (clearly labelled in the UI). Any OpenAI-compatible endpoint can be configured.
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str | None = None
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 60.0
    ai_daily_message_limit: int = 300
    ai_max_tool_iterations: int = 4

    # --- Rate limiting (per user/IP, in-process) ---
    rate_limit_per_minute: int = 120
    auth_rate_limit_per_minute: int = 10
    ai_rate_limit_per_minute: int = 20
    # Shared secret of a trusted server-side proxy (the SadakatKarti card site). Requests carrying it in
    # X-Proxy-Secret are rate limited by the X-Forwarded-For client instead of the proxy's own address.
    proxy_secret: str | None = None
    # Shared secret of this app's own Next.js frontend. Requests carrying it in X-Frontend-Secret are attributed
    # to the X-Client-IP it sets (the frontend strips both headers from incoming browser requests first).
    frontend_proxy_secret: str | None = None
    # Signed card-change webhooks to the SadakatKarti site (app/services/sadakat_webhook.py). Off unless both set.
    sadakat_webhook_url: str | None = None
    sadakat_webhook_secret: str | None = None
    # Public base URL of the SadakatKarti site; each business's sign-up QR points to <url>/k/<slug>.
    sadakat_public_url: str | None = None

    # --- Admin panel (read-only). Disabled unless all four are set. Store only the argon2 hash. ---
    admin_username: str | None = None
    admin_password_hash: str | None = None
    admin_jwt_secret: str | None = None
    # Secret URL segment: the panel lives at /<admin_path> and its API at /api/v1/<admin_path>. Never "admin".
    admin_path: str | None = None
    admin_session_minutes: int = 30
    # Client IPs / CIDR ranges that may reach the admin API at all (e.g. a VPN subnet). Everyone else gets 404.
    admin_allowed_ips: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["127.0.0.1", "::1"])
    admin_max_failures: int = 5
    admin_lockout_minutes: int = 15

    @field_validator("cors_origins", "admin_allowed_ips", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str) and not v.startswith("["):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @field_validator("database_url")
    @classmethod
    def _psycopg_driver(cls, v: str) -> str:
        # Hosting providers (Railway, Heroku...) hand out postgres:// or postgresql:// URLs; use psycopg 3.
        for scheme in ("postgres://", "postgresql://"):
            if v.startswith(scheme):
                return "postgresql+psycopg://" + v[len(scheme):]
        return v

    @field_validator("admin_path")
    @classmethod
    def _admin_path(cls, v: str | None) -> str | None:
        if v and (not re.fullmatch(r"[A-Za-z0-9_-]{12,80}", v) or "admin" in v.lower()):
            raise ValueError("ADMIN_PATH must be 12-80 URL-safe characters and must not contain 'admin'")
        return v or None

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def admin_enabled(self) -> bool:
        return bool(self.admin_username and self.admin_password_hash and self.admin_path and self.admin_jwt_secret
                    and len(self.admin_jwt_secret) >= 32)

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.environment == "production" and ("insecure" in settings.jwt_secret or len(settings.jwt_secret) < 32):
        raise RuntimeError("JWT_SECRET must be set to a strong value (32+ characters) in production")
    if settings.environment == "production" and settings.is_sqlite:
        raise RuntimeError("Production requires PostgreSQL. SQLite is a local development mode only.")
    for name in ("frontend_proxy_secret", "proxy_secret", "sadakat_webhook_secret"):
        value = getattr(settings, name)
        if settings.environment == "production" and value is not None and len(value) < 32:
            raise RuntimeError(f"{name.upper()} must be 32+ characters in production")
    return settings
