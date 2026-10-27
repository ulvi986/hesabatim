from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.ai import orchestrator
from app.api.deps import DB, CurrentUser, SettingsDep
from app.core.errors import AppError
from app.core.rate_limit import limiter
from app.db.session import SessionLocal
from app.models import Conversation, Profile
from app.schemas.requests import ChatMessageIn, ConfirmActionIn, ConversationIn
from app.services.accounts import require_member
from app.services.common import iso

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/status")
def status(settings: SettingsDep):
    return orchestrator.provider_status(settings)


def _conv_out(c: Conversation) -> dict:
    return {"id": str(c.id), "title": c.title, "business_id": str(c.business_id),
            "created_at": iso(c.created_at), "updated_at": iso(c.updated_at)}


@router.get("/conversations")
def list_conversations(db: DB, user: CurrentUser, business_id: uuid.UUID):
    require_member(db, user.id, business_id)
    stmt = select(Conversation).where(Conversation.user_id == user.id, Conversation.business_id == business_id)
    return {"items": [_conv_out(c) for c in db.scalars(stmt.order_by(Conversation.updated_at.desc()).limit(100)).all()]}


@router.post("/conversations", status_code=201)
def create_conversation(body: ConversationIn, db: DB, user: CurrentUser):
    business_id = require_member(db, user.id, body.business_id).business.id
    conv = Conversation(user_id=user.id, business_id=business_id, title=body.title or "Yeni söhbət")
    db.add(conv)
    db.commit()
    return _conv_out(conv)


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: uuid.UUID, db: DB, user: CurrentUser):
    conv = orchestrator.get_conversation(db, user.id, conversation_id)
    return {**_conv_out(conv), "messages": orchestrator.serialize_messages(db, conv)}


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: uuid.UUID, db: DB, user: CurrentUser):
    conv = orchestrator.get_conversation(db, user.id, conversation_id)
    db.delete(conv)
    db.commit()
    return Response(status_code=204)


@router.post("/conversations/{conversation_id}/messages")
def send_message(conversation_id: uuid.UUID, body: ChatMessageIn, db: DB, user: CurrentUser, settings: SettingsDep):
    """Server-Sent Events stream: user_message, token, tool_start, block, tool_end, error, done."""
    limiter.hit(f"ai:{user.id}", settings.ai_rate_limit_per_minute)
    orchestrator.get_conversation(db, user.id, conversation_id)  # ownership check before streaming starts
    user_id = user.id
    db.rollback()  # release the request session's transaction; the stream below uses its own session

    def events():
        session = SessionLocal()
        try:
            profile = session.get(Profile, user_id)
            conv = orchestrator.get_conversation(session, user_id, conversation_id)
            for ev in orchestrator.run_turn(session, settings, user=profile, conv=conv, text=body.content):
                yield f"event: {ev['type']}\ndata: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
        except AppError as exc:
            session.rollback()
            payload = {"type": "error", "message": exc.message, "code": exc.code, "retryable": exc.status_code >= 500}
            yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except Exception:  # never leak internals into the stream
            session.rollback()
            orchestrator.log.exception("assistant turn failed")
            payload = {"type": "error", "message": "Gözlənilməz xəta baş verdi", "retryable": True}
            yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        finally:
            session.close()

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/actions/{action_id}/confirm")
def confirm(action_id: uuid.UUID, body: ConfirmActionIn, db: DB, user: CurrentUser, settings: SettingsDep):
    a = orchestrator.confirm_action(db, settings, user=user, action_id=action_id, overrides=body.overrides)
    return orchestrator.serialize_action(a)


@router.post("/actions/{action_id}/reject")
def reject(action_id: uuid.UUID, db: DB, user: CurrentUser):
    return orchestrator.serialize_action(orchestrator.reject_action(db, user=user, action_id=action_id))
