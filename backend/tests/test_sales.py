from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from decimal import Decimal
from tests.conftest import create_product, sell


def test_sales_revenue_cost_refund_and_cancellation(make_user):
    a = make_user()
    p = create_product(a, name='Qapı', quantity='500', selling_price='150', purchase_price='100')
    response = sell(a, p['id'], '20')
    assert response.status_code == 201, response.text
    s = response.json()
    assert s['total'] == '3000.00'
    assert a.get(a.biz(f'/products/{p["id"]}')).json()['quantity'] == '480'
    report = a.get(a.biz('/analytics/overview')).json()
    assert report['totals']['revenue'] == '3000.00'
    assert report['totals']['cost_of_goods'] == '2000.00'
    assert report['totals']['gross_profit'] == '1000.00'
    a.patch(a.biz(f'/products/{p["id"]}'), json={'purchase_price': '110'})
    r = a.post(a.biz(f'/sales/{s["id"]}/returns'), json={'items': [{'sale_item_id': s['items'][0]['id'], 'quantity': '5'}]})
    assert r.status_code == 201, r.text
    assert r.json()['sale']['status'] == 'partially_returned'
    cancel = a.post(a.biz(f'/sales/{s["id"]}/cancel'), json={})
    assert cancel.status_code == 201, cancel.text
    assert cancel.json()['sale']['status'] == 'returned'
    assert a.get(a.biz(f'/products/{p["id"]}')).json()['quantity'] == '500'
    totals = a.get(a.biz('/analytics/overview')).json()['totals']
    assert totals['revenue'] == '0.00' and totals['gross_profit'] == '0.00'
    assert a.post(a.biz(f'/sales/{s["id"]}/cancel'), json={}).status_code == 409


def test_unknown_cost_is_explicit(make_user):
    a = make_user()
    p = create_product(a, purchase_price=None)
    assert sell(a, p['id'], '2').status_code == 201
    totals = a.get(a.biz('/analytics/overview')).json()['totals']
    assert totals['revenue_without_cost'] == '170.00'
    assert totals['gross_profit'] is None and totals['cost_complete'] is False


def test_zero_revenue_does_not_imply_known_cost(make_user):
    a = make_user()
    p = create_product(a, purchase_price=None, selling_price='0')
    assert sell(a, p['id'], '1').status_code == 201
    totals = a.get(a.biz('/analytics/overview')).json()['totals']
    assert totals['revenue'] == '0.00' and totals['gross_profit'] is None


def test_business_local_midnight_and_idempotency_conflict(make_user):
    a = make_user()
    p = create_product(a)
    body = {'items': [{'product_id': p['id'], 'quantity': '2'}], 'sold_at': '2020-01-01T20:30:00Z'}
    h = {'Idempotency-Key': 'dated-sale'}
    assert a.post(a.biz('/sales'), json=body, headers=h).status_code == 201
    assert a.post(a.biz('/sales'), json={**body, 'items': [{'product_id': p['id'], 'quantity': '3'}]}, headers=h).status_code == 409
    jan1 = a.get(a.biz('/analytics/overview?date_from=2020-01-01&date_to=2020-01-01')).json()
    jan2 = a.get(a.biz('/analytics/overview?date_from=2020-01-02&date_to=2020-01-02')).json()
    assert jan1['totals']['revenue'] == '0.00'
    assert jan2['totals']['revenue'] == '170.00'
    assert a.get(a.biz('/sales?date_from=2020-01-02&date_to=2020-01-02')).json()['total'] == 1


def test_sale_rollback_and_insufficient_stock(make_user):
    a = make_user()
    p, q = create_product(a, name='Qapı', quantity='10'), create_product(a, name='Masa', quantity='1')
    r = a.post(a.biz('/sales'), json={'items': [{'product_id': p['id'], 'quantity': '5'}, {'product_id': q['id'], 'quantity': '2'}]})
    assert r.status_code == 422
    assert a.get(a.biz(f'/products/{p["id"]}')).json()['quantity'] == '10'
    assert a.get(a.biz('/sales')).json()['total'] == 0
    assert a.get(a.biz('/inventory/events')).json()['total'] == 2


def test_duplicate_sale_and_stock(make_user):
    a = make_user()
    p = create_product(a)
    body = {'items': [{'product_id': p['id'], 'quantity': '10'}]}
    h = {'Idempotency-Key': 'sale-one'}
    first = a.post(a.biz('/sales'), json=body, headers=h)
    again = a.post(a.biz('/sales'), json=body, headers=h)
    assert first.json() == again.json()
    assert a.get(a.biz(f'/products/{p["id"]}')).json()['quantity'] == '90'
    for _ in range(2):
        assert a.post(a.biz(f'/inventory/{p["id"]}/adjust'), json={'delta': '5'}, headers={'Idempotency-Key': 'stock-one'}).status_code == 200
    assert a.get(a.biz(f'/products/{p["id"]}')).json()['quantity'] == '95'


def test_concurrent_sales_cannot_oversell(make_user):
    a = make_user()
    p = create_product(a, quantity='10')
    barrier = Barrier(2)
    def run(_):
        barrier.wait(timeout=10)
        return sell(a, p['id'], '7').status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert sorted(results) == [201, 422]
    assert a.get(a.biz(f'/products/{p["id"]}')).json()['quantity'] == '3'
    assert a.get(a.biz('/sales')).json()['total'] == 1


def test_concurrent_stock_changes_preserve_all_deltas(make_user):
    a = make_user()
    p = create_product(a, quantity='10')
    barrier = Barrier(2)
    def run(_):
        barrier.wait(timeout=10)
        return a.post(a.biz(f'/inventory/{p["id"]}/adjust'), json={'delta': '2'}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(run, range(2))) == [200, 200]
    assert a.get(a.biz(f'/products/{p["id"]}')).json()['quantity'] == '14'


def test_concurrent_duplicate_sale(make_user):
    a = make_user()
    p = create_product(a, quantity='10')
    barrier = Barrier(2)
    def run(_):
        barrier.wait(timeout=10)
        return a.post(a.biz('/sales'), json={'items': [{'product_id': p['id'], 'quantity': '7'}]}, headers={'Idempotency-Key': 'same-key'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert [r.status_code for r in results] == [201, 201]
    assert results[0].json()['id'] == results[1].json()['id']
    assert a.get(a.biz(f'/products/{p["id"]}')).json()['quantity'] == '3'
