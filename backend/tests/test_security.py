from tests.conftest import create_product, blocks


def test_registration_login_logout_password(client, make_user):
    client.cookies.clear()
    assert client.get('/api/v1/me').status_code == 401
    a = make_user()
    assert a.me['memberships'][0]['role'] == 'owner'
    email = a.me['email']
    assert client.post('/api/v1/auth/login', json={'email': email, 'password': 'wrong'}).status_code == 401
    login = client.post('/api/v1/auth/login', json={'email': email.upper(), 'password': 'sirr-parol-123'})
    assert login.status_code == 200
    assert 'HttpOnly' in login.headers['set-cookie']
    assert client.get('/api/v1/me').status_code == 200
    assert client.patch('/api/v1/me', json={'full_name': 'CSRF'}, headers={'Origin': 'https://evil.example'}).status_code == 403
    changed = a.post('/auth/password', json={'current_password': 'sirr-parol-123', 'new_password': 'new-secret-123'})
    assert changed.status_code == 200
    assert a.get('/me').status_code == 401
    a.token = changed.json()['access_token']
    assert a.post('/auth/logout-all').status_code == 204
    assert a.get('/me').status_code == 401
    client.cookies.clear()


def test_tenant_isolation_every_boundary(make_user):
    a, b = make_user('Business A'), make_user('Business B')
    p = create_product(a)
    for path in ['/products', f'/products/{p["id"]}', '/sales', '/analytics/overview', '/inventory/events', '/categories']:
        assert b.get(a.biz(path)).status_code == 404
    assert b.get(b.biz(f'/products/{p["id"]}')).status_code == 404
    assert b.post(b.biz(f'/inventory/{p["id"]}/adjust'), json={'delta': '10'}).status_code == 404
    assert b.post(b.biz('/sales'), json={'items': [{'product_id': p['id'], 'quantity': '1'}]}).status_code == 404
    category = a.post(a.biz('/categories'), json={'name': 'Private category'}).json()
    assert b.post(b.biz('/products'), json={'name': 'Wrong tenant', 'category_id': category['id']}).status_code in (404, 422)
    conv = a.new_conversation()
    assert b.get(f'/ai/conversations/{conv}').status_code == 404
    assert b.post('/ai/conversations', json={'business_id': a.business_id}).status_code == 404
    action = blocks(a.chat(conv, 'Məndə 500 qapı var'), 'action')[0]['action']
    assert b.post(f'/ai/actions/{action["id"]}/confirm', json={}).status_code == 404
    assert b.post(f'/ai/actions/{action["id"]}/reject').status_code == 404
    assert a.get(a.biz(f'/products/{p["id"]}')).json()['quantity'] == '100'


def test_unknown_fields_and_invalid_numbers(make_user):
    a = make_user()
    assert a.post(a.biz('/products'), json={'name': 'Test', 'business_id': a.business_id}).status_code == 422
    for value in ('NaN', 'Infinity', '-1'):
        assert a.post(a.biz('/products'), json={'name': 'Test', 'quantity': value}).status_code == 422


def test_proxy_secret_rate_limits_by_forwarded_client(client, monkeypatch):
    from app.api import deps
    from app.core.config import get_settings
    from starlette.requests import Request

    def req(headers):
        return Request({"type": "http", "client": ("10.0.0.1", 1), "headers": [(k.encode(), v.encode()) for k, v in headers.items()]})

    monkeypatch.setattr(get_settings(), "proxy_secret", "s3cret")
    assert deps._client_key(req({"x-proxy-secret": "s3cret", "x-forwarded-for": "1.2.3.4, 10.0.0.1"})) == "proxied:1.2.3.4"
    assert deps._client_key(req({"x-proxy-secret": "wrong", "x-forwarded-for": "1.2.3.4"})) == "10.0.0.1"
    assert deps._client_key(req({"x-forwarded-for": "1.2.3.4"})) == "10.0.0.1"
    monkeypatch.setattr(get_settings(), "proxy_secret", None)
    assert deps._client_key(req({"x-proxy-secret": "s3cret", "x-forwarded-for": "1.2.3.4"})) == "10.0.0.1"


def test_login_response_shape_used_by_sadakatkarti(client, make_user):
    # SadakatKarti's company panel signs in through this endpoint and reads these exact fields.
    api = make_user()
    r = client.post("/api/v1/auth/login", json={"email": api.me["email"], "password": "sirr-parol-123"})
    assert r.status_code == 200, r.text
    m = r.json()["user"]["memberships"][0]
    assert {"role", "business"} <= m.keys()
    assert {"slug", "name", "loyalty_enabled", "loyalty_stamps_needed", "loyalty_reward"} <= m["business"].keys()
