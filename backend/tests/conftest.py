from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="hesabatim-test-"))
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["ENVIRONMENT"] = "test"
os.environ["LLM_API_KEY"] = ""
os.environ["RATE_LIMIT_PER_MINUTE"] = "100000"
os.environ["AUTH_RATE_LIMIT_PER_MINUTE"] = "100000"
os.environ["AI_RATE_LIMIT_PER_MINUTE"] = "100000"
os.environ["FRONTEND_PROXY_SECRET"] = "test-frontend-proxy-secret-0123456789"
os.environ["CORS_ORIGINS"] = "http://localhost:3000"
try:  # optional local-only suite, not in git
    import tests.admin_private.env  # noqa: F401,E402
except ModuleNotFoundError:
    pass

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.rate_limit import limiter  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    from app.core.config import get_settings
    from app.db.session import engine
    from app.models import Base

    if not get_settings().is_sqlite:  # Postgres test run: schema comes from the Alembic migration
        if not (engine.url.database or '').endswith('_test'):
            raise RuntimeError('TEST_DATABASE_URL must point to a disposable database ending in _test')
        from alembic import command
        from alembic.config import Config

        Base.metadata.drop_all(engine)
        with engine.begin() as conn:
            conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
        command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_state(client):
    """Each test starts with an empty database."""
    from app.db.session import engine
    from app.models import Base

    limiter.reset()
    client.cookies.clear()
    with engine.begin() as conn:
        # Clear RESTRICT self-references only in this disposable test database.
        from app.models import Sale, SaleItem, InventoryEvent
        conn.execute(SaleItem.__table__.update().values(original_item_id=None))
        conn.execute(Sale.__table__.update().values(original_sale_id=None, kind='sale'))
        conn.execute(InventoryEvent.__table__.update().values(reverts_event_id=None))
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


class Api:
    def __init__(self, client: TestClient, token: str, me: dict):
        self.c = client
        self.token = token
        self.me = me

    @property
    def h(self):
        return {"Authorization": f"Bearer {self.token}"}

    def get(self, url, **kw):
        return self.c.get(f"/api/v1{url}", headers={**self.h, **kw.pop("headers", {})}, **kw)

    def post(self, url, json=None, **kw):
        return self.c.post(f"/api/v1{url}", json=json, headers={**self.h, **kw.pop("headers", {})}, **kw)

    def patch(self, url, json=None, **kw):
        return self.c.patch(f"/api/v1{url}", json=json, headers={**self.h, **kw.pop("headers", {})}, **kw)

    def delete(self, url, **kw):
        return self.c.delete(f"/api/v1{url}", headers={**self.h, **kw.pop("headers", {})}, **kw)

    @property
    def business_id(self) -> str:
        return self.me["memberships"][0]["business"]["id"]

    def biz(self, path: str) -> str:
        return f"/businesses/{self.business_id}{path}"

    def new_conversation(self) -> str:
        r = self.post("/ai/conversations", json={"business_id": self.business_id})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def chat(self, conversation_id: str, text: str) -> list[dict]:
        import json as _json

        r = self.post(f"/ai/conversations/{conversation_id}/messages", json={"content": text})
        assert r.status_code == 200, r.text
        events = []
        for chunk in r.text.split("\n\n"):
            for line in chunk.splitlines():
                if line.startswith("data: "):
                    events.append(_json.loads(line[6:]))
        return events


def register(client: TestClient, business_name: str = "Taxta Dünyası", currency: str = "AZN") -> Api:
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/v1/auth/register", json={"email": email, "password": "sirr-parol-123", "full_name": "Test",
                                                   "business_name": business_name, "currency": currency})
    assert r.status_code == 201, r.text
    data = r.json()
    return Api(client, data["access_token"], data["user"])


@pytest.fixture
def make_user(client):
    def _make(business_name: str = "Taxta Dünyası", currency: str = "AZN") -> Api:
        return register(client, business_name, currency)

    return _make


def create_product(api: Api, **kw) -> dict:
    body = {"name": "Ağ MDF qapı", "unit": "piece", "selling_price": "85", "purchase_price": "60", "quantity": "100"} | kw
    r = api.post(api.biz("/products"), json=body)
    assert r.status_code == 201, r.text
    return r.json()


def sell(api: Api, product_id: str, quantity: str, unit_price: str | None = None, **kw):
    line = {"product_id": product_id, "quantity": quantity} | ({"unit_price": unit_price} if unit_price else {})
    return api.post(api.biz("/sales"), json={"items": [line], **kw})


def blocks(events: list[dict], kind: str | None = None) -> list[dict]:
    return [e["block"] for e in events if e["type"] == "block" and (kind is None or e["block"]["type"] == kind)]
