from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from app.core.config import get_settings
from app.services import sadakat_webhook
from tests.test_customers import new_customer

SECRET = "sadakat-test-secret-0123456789abcdef"


@pytest.fixture
def sent(monkeypatch):
    """Delivers synchronously and records each signed request instead of sending it."""
    calls: list[dict] = []

    class FakeClient:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def post(self, url, content, headers):
            calls.append({"url": url, "body": content, "headers": headers})

            class R:
                status_code = 200
            return R()

    monkeypatch.setattr(get_settings(), "sadakat_webhook_url", "https://sadakat.test/hook")
    monkeypatch.setattr(get_settings(), "sadakat_webhook_secret", SECRET)
    monkeypatch.setattr(sadakat_webhook.httpx, "Client", FakeClient)
    monkeypatch.setattr(sadakat_webhook._pool, "submit", lambda fn, *a: fn(*a))
    return calls


def events(calls):
    return [json.loads(c["body"]) for c in calls]


def test_card_changes_are_signed_and_sent_after_commit(make_user, sent):
    api = make_user()
    c = new_customer(api)
    cid, old = c["id"], c["card"]["token"]
    assert api.post(api.biz(f"/customers/{cid}/loyalty/stamp"), json={"count": 2}).status_code == 200
    assert api.post(api.biz(f"/customers/{cid}/loyalty/new-card")).status_code == 200
    assert api.patch(api.biz(""), json={"loyalty_reward": "Pulsuz qəhvə"}).status_code == 200

    got = events(sent)
    got = [e for e in got if e["event"] != "business.created"]
    assert [e["event"] for e in got] == ["customer.created", "loyalty.stamp", "loyalty.new_card", "business.updated"]
    stamp = got[1]
    assert stamp["stamps"] == 2 and stamp["stamps_needed"] == 9 and stamp["reward_ready"] is False
    assert got[2]["old_token"] == old and got[2]["card_token"] != old
    assert got[3]["reward"] == "Pulsuz qəhvə" and "card_token" not in got[3]
    assert all("phone" not in e for e in got) and len({e["id"] for e in got}) == 4

    for call in sent:
        ts = call["headers"]["X-Sadakat-Timestamp"]
        expected = hmac.new(SECRET.encode(), f"{ts}.".encode() + call["body"], hashlib.sha256).hexdigest()
        assert call["headers"]["X-Sadakat-Signature"] == f"sha256={expected}"


def test_failed_request_sends_nothing(make_user, sent):
    api = make_user()
    c = new_customer(api)
    sent.clear()
    assert api.post(api.biz(f"/customers/{c['id']}/loyalty/redeem")).status_code == 409  # reward not ready
    assert sent == []


def test_registration_announces_business(client, sent):
    from tests.conftest import register
    api = register(client, "Qəhvə Evi")
    created = [e for e in events(sent) if e["event"] == "business.created"]
    assert len(created) == 1 and created[0]["business_slug"] == api.me["memberships"][0]["business"]["slug"]
    assert created[0]["stamps_needed"] == 9 and "logo" in created[0]


def test_signup_url_follows_slug(make_user, monkeypatch):
    api = make_user()
    monkeypatch.setattr(get_settings(), "sadakat_public_url", None)
    assert api.get(api.biz("")).json()["signup_url"] is None
    monkeypatch.setattr(get_settings(), "sadakat_public_url", "https://kart.example/")
    b = api.get(api.biz("")).json()
    assert b["signup_url"] == f"https://kart.example/k/{b['slug']}"


def test_branding_change_is_sent(make_user, sent):
    api = make_user()
    sent.clear()
    r = api.patch(api.biz(""), json={"theme": {"primary": "#1d5fa8", "card": "#b93a6b"}})
    assert r.status_code == 200, r.text
    (e,) = events(sent)
    assert e["event"] == "business.updated" and e["theme"]["card"] == "#b93a6b" and "logo" not in e

    sent.clear()
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    r = api.c.put(f"/api/v1{api.biz('/logo')}", headers=api.h, files={"file": ("l.png", png, "image/png")})
    assert r.status_code == 200, r.text
    (e,) = events(sent)
    assert e["logo"].startswith("data:image/png;base64,")

    sent.clear()
    assert api.delete(api.biz("/logo")).status_code == 200
    (e,) = events(sent)
    assert "logo" in e and e["logo"] is None


def test_disabled_without_settings(make_user, sent, monkeypatch):
    monkeypatch.setattr(get_settings(), "sadakat_webhook_secret", None)
    new_customer(make_user())
    assert sent == []
