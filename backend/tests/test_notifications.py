from __future__ import annotations

from tests.conftest import create_product
from tests.test_customers import new_customer


def notes(api):
    r = api.get(api.biz("/notifications"))
    assert r.status_code == 200, r.text
    return r.json()


def test_low_and_out_of_stock_follow_product_minimum(make_user):
    api = make_user()
    create_product(api, name="Qapı", quantity="3", low_stock_threshold=10)
    create_product(api, name="Masa", quantity="0", low_stock_threshold=2)
    create_product(api, name="Taxta", quantity="50", low_stock_threshold=10)
    data = notes(api)
    kinds = {n["title"]: n["type"] for n in data["items"]}
    assert kinds == {"Masa bitib": "out_of_stock", "Qapı azalır": "low_stock"}
    assert data["unread"] == 2


def test_one_threshold_for_all_products(make_user):
    api = make_user()
    create_product(api, name="Qapı", quantity="7", low_stock_threshold=1)
    create_product(api, name="Masa", quantity="9", low_stock_threshold=100)
    r = api.patch(api.biz("/notifications/settings"), json={"low_stock_threshold": 8})
    assert r.status_code == 200, r.text
    assert [n["title"] for n in r.json()["items"]] == ["Qapı azalır"]  # 7 <= 8; Masa's own 100 is ignored
    r = api.patch(api.biz("/notifications/settings"), json={"use_product_thresholds": True})
    assert r.json()["settings"]["low_stock_threshold"] is None
    assert [n["title"] for n in r.json()["items"]] == ["Masa azalır"]


def test_five_or_fewer_always_needs_attention(make_user):
    api = make_user()
    create_product(api, name="Qapı", quantity="5", low_stock_threshold=0)
    create_product(api, name="Masa", quantity="6", low_stock_threshold=0)
    assert [n["title"] for n in notes(api)["items"]] == ["Qapı azalır"]
    r = api.get(api.biz("/products"), params={"stock": "attention"})
    assert [p["name"] for p in r.json()["items"]] == ["Qapı"]
    assert r.json()["items"][0]["stock_status"] == "low"


def test_seen_and_renotify_on_further_drop(make_user):
    api = make_user()
    p = create_product(api, name="Qapı", quantity="5", low_stock_threshold=10)
    assert notes(api)["unread"] == 1
    assert api.post(api.biz("/notifications/seen")).status_code == 204
    assert notes(api)["unread"] == 0
    r = api.post(api.biz(f"/inventory/{p['id']}/adjust"), json={"delta": "-2", "reason": "test"})
    assert r.status_code == 200, r.text
    assert notes(api)["unread"] == 1  # 3 left: a new situation


def test_disabled_types_and_customer_alerts(make_user):
    api = make_user()
    c = new_customer(api)
    api.post(api.biz(f"/customers/{c['id']}/loyalty/adjust"), json={"stamps": 9})
    assert any(n["type"] == "reward_ready" for n in notes(api)["items"])
    api.patch(api.biz("/notifications/settings"), json={"reward_ready": False})
    assert not any(n["type"] == "reward_ready" for n in notes(api)["items"])


def test_category_name_search(make_user):
    api = make_user()
    cat = api.post(api.biz("/categories"), json={"name": "Mebel"}).json()
    create_product(api, name="Stul", quantity="5", category_id=cat["id"])
    create_product(api, name="Mismar", quantity="5")
    items = api.get(api.biz("/products?q=mebel")).json()["items"]
    assert [p["name"] for p in items] == ["Stul"]
