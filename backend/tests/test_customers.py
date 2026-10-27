from __future__ import annotations

import base64

from tests.conftest import create_product, sell

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def new_customer(api, **kw):
    r = api.post(api.biz("/customers"), json={"name": "Aysel Məmmədova", "phone": "+994 50 123 45 67"} | kw)
    assert r.status_code == 201, r.text
    return r.json()


def test_customer_crud_and_phone_uniqueness(make_user):
    api = make_user()
    c = new_customer(api, email="Aysel@Example.com", birthday="1995-03-08", tags=["VIP", "VIP", " topdan "])
    assert c["phone"] == "+994501234567"
    assert c["email"] == "aysel@example.com"
    assert c["tags"] == ["VIP", "topdan"]
    assert c["card"]["stamps"] == 0 and c["card"]["stamps_needed"] == 9

    r = api.post(api.biz("/customers"), json={"name": "Başqa", "phone": "+994501234567"})
    assert r.status_code == 409

    r = api.patch(api.biz(f"/customers/{c['id']}"), json={"company": "Bakı Mebel", "note": "Nağd ödəyir"})
    assert r.status_code == 200 and r.json()["company"] == "Bakı Mebel"

    listing = api.get(api.biz("/customers?q=aysel")).json()
    assert listing["total"] == 1 and listing["segments"]["total"] == 1
    assert api.get(api.biz("/customers?q=50123")).json()["total"] == 1


def test_customers_are_isolated_between_businesses(make_user):
    a, b = make_user(), make_user("Digər")
    c = new_customer(a)
    assert b.get(b.biz(f"/customers/{c['id']}")).status_code == 404
    assert b.get(b.biz(f"/customers/by-card/{c['card']['token']}")).status_code == 404


def test_sale_links_customer_and_stats(make_user):
    api = make_user()
    p = create_product(api)
    c = new_customer(api)
    r = sell(api, p["id"], "2", customer_id=c["id"], loyalty_stamp=True)
    assert r.status_code == 201, r.text
    sale = r.json()
    assert sale["customer_id"] == c["id"] and sale["customer_name"] == c["name"]
    sell(api, p["id"], "1", customer_id=c["id"])
    api.post(api.biz(f"/sales/{sale['id']}/returns"),
             json={"items": [{"sale_item_id": sale["items"][0]["id"], "quantity": "1"}]})

    d = api.get(api.biz(f"/customers/{c['id']}")).json()
    assert d["stats"]["orders"] == 2 and d["stats"]["returns"] == 1
    assert d["stats"]["gross_spent"] == "255.00" and d["stats"]["net_spent"] == "170.00"
    assert d["card"]["stamps"] == 1  # only the sale with loyalty_stamp=True
    assert d["top_products"][0]["quantity"] == "2"
    assert len(d["recent_sales"]) == 3
    assert api.get(api.biz(f"/sales?customer_id={c['id']}")).json()["total"] == 3
    row = api.get(api.biz("/customers")).json()["items"][0]
    assert row["net_spent"] == "170.00" and row["orders"] == 2


def test_loyalty_card_cycle(make_user):
    api = make_user()
    api.patch(api.biz(""), json={"loyalty_stamps_needed": 3, "loyalty_reward": "Pulsuz kofe"})
    c = new_customer(api)
    url = api.biz(f"/customers/{c['id']}/loyalty")

    assert api.post(f"{url}/stamp", json={"count": 2}).json()["added"] == 2
    r = api.post(f"{url}/stamp", json={})
    assert r.status_code == 429 and r.json()["error"]["code"] == "loyalty_cooldown"
    r = api.post(f"{url}/stamp", json={"count": 5, "force": True})
    assert r.json()["added"] == 1 and r.json()["customer"]["card"]["reward_ready"] is True
    assert api.post(f"{url}/stamp", json={"force": True}).status_code == 409
    assert api.get(api.biz("/customers?segment=reward_ready")).json()["total"] == 1

    r = api.post(f"{url}/redeem")
    card = r.json()["customer"]["card"]
    assert card["stamps"] == 0 and card["rewards"] == 1 and card["total_stamps"] == 3
    assert api.post(f"{url}/redeem").status_code == 409
    events = r.json()["customer"]["loyalty_events"]
    assert [e["type"] for e in events][:2] == ["redeem", "stamp"]

    # QR scan resolves both a raw token and the full card URL
    token = card["token"]
    assert api.get(api.biz(f"/customers/by-card/{token}")).json()["id"] == c["id"]
    old = token
    new = api.post(f"{url}/new-card").json()["customer"]["card"]["token"]
    assert new != old and api.get(api.biz(f"/customers/by-card/{old}")).status_code == 404


def test_public_card_hides_private_data(make_user, client):
    api = make_user()
    c = new_customer(api)
    r = client.get(f"/api/v1/public/cards/{c['card']['token']}")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Aysel" and "phone" not in body and body["business"]["name"] == "Taxta Dünyası"
    assert client.get("/api/v1/public/cards/notarealtoken").status_code == 404


def test_logo_and_theme(make_user):
    api = make_user()
    r = api.c.put(f"/api/v1{api.biz('/logo')}", headers=api.h, files={"file": ("logo.png", PNG, "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["logo"].startswith("data:image/png;base64,")
    r = api.c.put(f"/api/v1{api.biz('/logo')}", headers=api.h,
                  files={"file": ("x.svg", b"<svg onload=alert(1)>", "image/svg+xml")})
    assert r.status_code == 422
    assert api.delete(api.biz("/logo")).json()["logo"] is None

    r = api.patch(api.biz(""), json={"theme": {"primary": "#7c3aed", "accent": "#f59e0b", "sidebar": "dark"}})
    assert r.status_code == 200 and r.json()["theme"]["primary"] == "#7c3aed"
    assert api.patch(api.biz(""), json={"theme": {"primary": "red"}}).status_code == 422
    assert api.get("/me").json()["memberships"][0]["business"]["theme"]["sidebar"] == "dark"


def test_public_signup_creates_card_the_business_can_read(make_user, client):
    api = make_user()
    slug = api.get(api.biz("")).json()["slug"]
    info = client.get(f"/api/v1/public/businesses/{slug}").json()
    assert info["name"] == "Taxta Dünyası" and info["stamps_needed"] == 9 and info["loyalty_enabled"] is True

    r = client.post(f"/api/v1/public/businesses/{slug}/cards", json={"name": "Nigar Əliyeva", "phone": "050 555 44 33"})
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["name"] == "Nigar" and card["stamps"] == 0 and "phone" not in card

    # same phone again (lost phone) -> same card, no duplicate customer
    again = client.post(f"/api/v1/public/businesses/{slug}/cards", json={"name": "Nigar", "phone": "0505554433"}).json()
    assert again["token"] == card["token"]
    assert api.get(api.biz("/customers")).json()["total"] == 1

    # the business reads the card by raw token and by the SadakatKarti QR URL, and can stamp it
    d = api.get(api.biz(f"/customers/by-card/{card['token']}")).json()
    assert d["name"] == "Nigar Əliyeva" and d["phone"] == "0505554433" and "QR qeydiyyat" in d["tags"]
    from app.services.customers import token_from_text
    assert token_from_text(f"https://vanilla.example/c/{card['token']}") == card["token"]
    assert api.post(api.biz(f"/customers/{d['id']}/loyalty/stamp"), json={}).status_code == 200
    assert client.get(f"/api/v1/public/cards/{card['token']}").json()["stamps"] == 1


def test_public_signup_validation_and_isolation(make_user, client):
    a, b = make_user(), make_user("Digər")
    slug_a = a.get(a.biz("")).json()["slug"]
    slug_b = b.get(b.biz("")).json()["slug"]
    url = f"/api/v1/public/businesses/{slug_a}/cards"
    assert client.post(url, json={"name": "", "phone": "0505554433"}).status_code == 422
    assert client.post(url, json={"name": "X", "phone": "12"}).status_code == 422
    assert client.post(url, json={"name": "X", "phone": "0505554433", "stamps": 9}).status_code == 422
    assert client.post("/api/v1/public/businesses/yoxdur/cards", json={"name": "X", "phone": "0505554433"}).status_code == 404

    ca = client.post(url, json={"name": "X", "phone": "0505554433"}).json()
    cb = client.post(f"/api/v1/public/businesses/{slug_b}/cards", json={"name": "X", "phone": "0505554433"}).json()
    assert ca["token"] != cb["token"]  # same phone, separate cards per business
    assert b.get(b.biz(f"/customers/by-card/{ca['token']}")).status_code == 404

    a.patch(a.biz(""), json={"loyalty_enabled": False})
    r = client.post(url, json={"name": "Y", "phone": "0705554433"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "loyalty_disabled"


def test_public_signup_restores_archived_customer(make_user, client):
    api = make_user()
    slug = api.get(api.biz("")).json()["slug"]
    c = new_customer(api)
    api.patch(api.biz(f"/customers/{c['id']}"), json={"is_archived": True})
    r = client.post(f"/api/v1/public/businesses/{slug}/cards", json={"name": "Aysel", "phone": "+994501234567"})
    assert r.json()["token"] == c["card"]["token"]
    assert api.get(api.biz(f"/customers/{c['id']}")).json()["is_archived"] is False
