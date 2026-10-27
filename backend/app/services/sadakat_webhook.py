"""Signed, fire-and-forget webhooks to the SadakatKarti card site, so customers' phones and the company panels
update as soon as a card changes. Disabled unless SADAKAT_WEBHOOK_URL and SADAKAT_WEBHOOK_SECRET are both set.

Changes are picked up from the ORM session itself (new loyalty events, new customers, reissued card tokens,
loyalty settings), so every path that touches a card (API, sales, AI, public signup) is covered. Events are
collected at flush, dropped on rollback, and sent only after commit from a small thread pool, using the
committed state. A failed delivery is logged and never affects the user's request; SadakatKarti can always
fall back to GET /public/cards/{token}. No phone numbers are sent.

Headers: X-Sadakat-Timestamp: <unix seconds>, X-Sadakat-Signature: sha256=<hex HMAC-SHA256(secret, "{ts}.{body}")>."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Business, Customer, LoyaltyEvent

log = logging.getLogger("hesabatim.sadakat")
KEY = "sadakat_webhooks"
BUSINESS_FIELDS = ("name", "loyalty_stamps_needed", "loyalty_reward", "loyalty_enabled", "logo", "theme")
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sadakat-webhook")


def enabled() -> bool:
    s = get_settings()
    return bool(s.sadakat_webhook_url and s.sadakat_webhook_secret)


def _old_value(obj: Any, attr: str) -> Any:
    hist = inspect(obj).attrs[attr].history
    return hist.deleted[0] if hist.deleted else None


def _after_flush(session: Session, _ctx) -> None:
    if not enabled():
        return
    queue: list[dict[str, Any]] = session.info.setdefault(KEY, [])
    for obj in session.new:
        if isinstance(obj, LoyaltyEvent):
            queue.append({"event": f"loyalty.{obj.event_type}", "customer_id": obj.customer_id, "id": str(obj.id)})
        elif isinstance(obj, Customer):
            queue.append({"event": "customer.created", "customer_id": obj.id, "id": str(uuid.uuid4())})
        elif isinstance(obj, Business):
            queue.append({"event": "business.created", "business_id": obj.id, "id": str(uuid.uuid4())})
    for obj in session.dirty:
        if isinstance(obj, Customer) and inspect(obj).attrs.card_token.history.has_changes():
            queue.append({"event": "loyalty.new_card", "customer_id": obj.id, "id": str(uuid.uuid4()),
                          "old_token": _old_value(obj, "card_token")})
        elif isinstance(obj, Business) and any(inspect(obj).attrs[f].history.has_changes() for f in BUSINESS_FIELDS):
            # The logo (up to ~700 KB as a data URI) is sent only when it changed; SadakatKarti keeps it otherwise.
            queue.append({"event": "business.updated", "business_id": obj.id, "id": str(uuid.uuid4()),
                          "logo_changed": inspect(obj).attrs.logo.history.has_changes()})


def _after_commit(session: Session) -> None:
    queue = session.info.pop(KEY, None)
    if queue:
        _pool.submit(_deliver_all, queue)


def _after_rollback(session: Session, previous_transaction) -> None:
    if not previous_transaction.nested:  # a rolled-back SAVEPOINT leaves the outer transaction's events
        session.info.pop(KEY, None)


def _payload(db: Session, item: dict[str, Any]) -> dict[str, Any] | None:
    at = datetime.now(timezone.utc).isoformat()
    if item["event"] in ("business.created", "business.updated"):
        b = db.get(Business, item["business_id"])
        out = b and {"event": item["event"], "business_slug": b.slug, "name": b.name,
                     "stamps_needed": b.loyalty_stamps_needed, "reward": b.loyalty_reward,
                     "loyalty_enabled": b.loyalty_enabled, "theme": b.theme or {},
                     "at": at, "id": item["id"]}
        if out and (item["event"] == "business.created" or item.get("logo_changed")):
            out["logo"] = b.logo
        return out
    c = db.get(Customer, item["customer_id"])
    b = c and db.get(Business, c.business_id)
    if c is None or b is None:
        return None
    out = {"event": item["event"], "business_slug": b.slug, "card_token": c.card_token, "name": c.name,
           "stamps": c.stamps, "stamps_needed": b.loyalty_stamps_needed, "rewards": c.rewards,
           "total_stamps": c.total_stamps, "reward_ready": c.stamps >= b.loyalty_stamps_needed, "at": at, "id": item["id"]}
    if item.get("old_token"):
        out["old_token"] = item["old_token"]
    return out


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def _deliver_all(queue: list[dict[str, Any]]) -> None:
    from app.db.session import SessionLocal

    s = get_settings()
    try:
        with SessionLocal() as db:
            payloads = [p for p in (_payload(db, item) for item in queue) if p]
        with httpx.Client(timeout=3.0) as client:
            for p in payloads:
                body = json.dumps(p, ensure_ascii=False, separators=(",", ":")).encode()
                ts = str(int(time.time()))
                try:
                    r = client.post(s.sadakat_webhook_url, content=body, headers={
                        "Content-Type": "application/json", "X-Sadakat-Timestamp": ts,
                        "X-Sadakat-Signature": sign(s.sadakat_webhook_secret, ts, body)})
                    if r.status_code >= 400:
                        log.warning(json.dumps({"event": "sadakat_webhook_rejected", "type": p["event"], "status": r.status_code}))
                except httpx.HTTPError as exc:
                    log.warning(json.dumps({"event": "sadakat_webhook_failed", "type": p["event"], "error": type(exc).__name__}))
    except Exception:  # never let a webhook problem surface anywhere else
        log.exception("sadakat webhook delivery crashed")


def install(session_factory) -> None:
    event.listen(session_factory, "after_flush", _after_flush)
    event.listen(session_factory, "after_commit", _after_commit)
    event.listen(session_factory, "after_soft_rollback", _after_rollback)
