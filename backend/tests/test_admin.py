from __future__ import annotations

import pytest

from app.api.routes.admin import lockout
from app.models import AuditLog

BASE = "/api/v1/test-secret-panel-path"
ORIGIN = {"origin": "http://localhost:3000"}
SECRET = "test-frontend-proxy-secret-0123456789"


def via_frontend(ip: str, secret: str = SECRET) -> dict[str, str]:
    return {"x-frontend-secret": secret, "x-client-ip": ip, **ORIGIN}


@pytest.fixture(autouse=True)
def _reset_lockout():
    lockout.reset()
    yield
    lockout.reset()


def login(client, ip="203.0.113.7", password="test-password"):
    return client.post(f"{BASE}/login", json={"username": "test-admin", "password": password}, headers=via_frontend(ip))


def admin_events():
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        return [a.action for a in db.query(AuditLog).filter(AuditLog.entity_type == "admin").all()]


def test_guessable_admin_path_does_not_exist(client):
    assert client.post("/api/v1/admin/login", json={}, headers=via_frontend("203.0.113.7")).status_code == 404


def test_allowed_ip_logs_in_and_reads(client):
    assert login(client).status_code == 200
    h = via_frontend("203.0.113.7")
    assert client.get(f"{BASE}/session", headers=h).json() == {"username": "test-admin"}
    overview = client.get(f"{BASE}/overview", headers=h).json()
    assert "profiles" in overview["tables"]
    assert {"admin_login", "admin_view"} <= set(admin_events())


def test_cidr_range_allowed(client):
    assert login(client, ip="10.8.0.42").status_code == 200


def test_other_ip_gets_404_and_is_logged(client):
    assert login(client, ip="203.0.113.9").status_code == 404
    assert "admin_blocked_ip" in admin_events()


def test_forged_client_ip_without_secret_is_rejected(client):
    # A browser that sets X-Client-IP itself (without the frontend's secret) is an unknown address.
    r = client.post(f"{BASE}/login", json={"username": "test-admin", "password": "test-password"},
                    headers={"x-client-ip": "203.0.113.7", **ORIGIN})
    assert r.status_code == 404
    assert login(client, ip="203.0.113.7").status_code == 200  # sanity: real path works
    r = client.post(f"{BASE}/login", json={"username": "test-admin", "password": "test-password"},
                    headers=via_frontend("203.0.113.7", secret="wrong-secret-wrong-secret-wrong-secret"))
    assert r.status_code == 404


def test_session_cookie_bound_to_ip(client):
    assert login(client).status_code == 200
    # Same cookie, but now the request comes from another allowed address.
    assert client.get(f"{BASE}/session", headers=via_frontend("10.8.0.5")).status_code == 401


def test_wrong_password_locks_out_after_five(client):
    for _ in range(4):
        assert login(client, password="nope").status_code == 401
    assert login(client, password="nope").status_code == 423
    assert login(client).status_code == 423  # even the right password, until the lockout expires
    assert "admin_login_locked" in admin_events()


def test_admin_is_read_only(client):
    assert login(client).status_code == 200
    h = via_frontend("203.0.113.7")
    assert client.post(f"{BASE}/tables/profiles", headers=h).status_code == 405


def test_sensitive_columns_masked(client, make_user):
    make_user()
    assert login(client).status_code == 200
    rows = client.get(f"{BASE}/tables/local_credentials", headers=via_frontend("203.0.113.7")).json()["rows"]
    assert rows and all(r["password_hash"] == "•••• gizli" for r in rows)
