import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from pydantic import ValidationError
from app.ai.tools import TOOLS
from tests.conftest import create_product, blocks


def test_complete_azerbaijani_conversation(make_user):
    a = make_user()
    conv = a.new_conversation()
    events = a.chat(conv, 'Məndə 500 qapı, 300 taxta və 150 masa var.')
    actions = blocks(events, 'action')
    assert len(actions) == 3, events
    assert a.get(a.biz('/products')).json()['total'] == 0
    a.chat(conv, 'Bəli.')
    inventory = a.get(a.biz('/products')).json()['items']
    assert {p['name']: p['quantity'] for p in inventory} == {'Qapı': '500', 'Taxta': '300', 'Masa': '150'}
    a.chat(conv, 'Bəli')  # no repeated writes
    assert a.get(a.biz('/products')).json()['total'] == 3
    events = a.chat(conv, 'Bu gün 20 qapını 150 manatdan satdım.')
    actions = blocks(events, 'action')
    assert len(actions) == 1, events
    assert actions[0]['action']['preview']['total'] == '3000.00'
    assert a.get(a.biz('/sales')).json()['total'] == 0
    a.chat(conv, 'Bəli')
    report = a.get(a.biz('/analytics/overview')).json()
    assert report['totals']['revenue'] == '3000.00'
    qapi = next(p for p in a.get(a.biz('/products')).json()['items'] if p['name'] == 'Qapı')
    assert qapi['quantity'] == '480'
    events = a.chat(conv, 'Bu gün nə qədər satış etmişəm?')
    assert blocks(events, 'analytics')[0]['data']['totals']['revenue'] == '3000.00'
    assert a.get(f'/ai/conversations/{conv}').json()['messages']


def test_tool_schema_cannot_take_tenant_or_sql():
    for args in ({'name': 'Qapı', 'business_id': 'evil'}, {'name': 'Qapı', 'quantity': '-1'}, {'name': 'Qapı', 'sql': 'delete from products'}):
        with pytest.raises(ValidationError):
            TOOLS['create_product'].validate(args)


def test_confirmation_replay_reject_and_missing_price(make_user):
    a = make_user(); conv = a.new_conversation()
    p = create_product(a, name='Masa', selling_price=None)
    action = blocks(a.chat(conv, '10 masa satdım'), 'action')[0]['action']
    assert 'unit_price' in action['preview']['missing']
    url = f'/ai/actions/{action["id"]}/confirm'
    assert a.post(url, json={}).status_code == 422
    r = a.post(url, json={'overrides': {'unit_price': '250'}})
    assert r.status_code == 200, r.text
    assert a.post(url, json={}).json()['result'] == r.json()['result']
    assert a.get(a.biz('/sales')).json()['total'] == 1
    action2 = blocks(a.chat(conv, 'Məndə 5 stul var'), 'action')[0]['action']
    assert a.post(f'/ai/actions/{action2["id"]}/reject').status_code == 200
    assert a.post(f'/ai/actions/{action2["id"]}/confirm', json={}).status_code == 409


def test_concurrent_ai_confirmation(make_user):
    a = make_user(); conv = a.new_conversation()
    action = blocks(a.chat(conv, 'Məndə 500 qapı var'), 'action')[0]['action']
    barrier = Barrier(2)
    def run(_):
        barrier.wait(timeout=10)
        return a.post(f'/ai/actions/{action["id"]}/confirm', json={}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(run, range(2))) == [200, 200]
    assert a.get(a.biz('/products')).json()['total'] == 1
    assert a.get(a.biz('/products')).json()['items'][0]['quantity'] == '500'


def test_preview_price_pinned_and_ambiguous_product(make_user):
    a = make_user(); conv = a.new_conversation()
    p = create_product(a, name='Masa', selling_price='100')
    action = blocks(a.chat(conv, '2 masa satdım'), 'action')[0]['action']
    a.patch(a.biz(f'/products/{p["id"]}'), json={'selling_price': '200'})
    r = a.post(f'/ai/actions/{action["id"]}/confirm', json={})
    assert r.json()['result']['sale']['total'] == '200.00'
    create_product(a, name='Ağ qapı'); create_product(a, name='Qara qapı')
    events = a.chat(conv, '2 qapı satdım')
    assert not blocks(events, 'action'), events


def test_ai_stock_confirmation_detects_changed_stock(make_user):
    a = make_user(); conv = a.new_conversation()
    p = create_product(a, name='Qapı')
    action = blocks(a.chat(conv, 'Qapının stokunu 50 et'), 'action')[0]['action']
    assert a.post(a.biz(f'/inventory/{p["id"]}/adjust'), json={'delta': '5'}).status_code == 200
    r = a.post(f'/ai/actions/{action["id"]}/confirm', json={})
    assert r.status_code == 409
    assert a.get(a.biz(f'/products/{p["id"]}')).json()['quantity'] == '105'


def test_confirmed_card_shows_edited_prices(make_user):
    a = make_user(); conv = a.new_conversation()
    action = blocks(a.chat(conv, 'Məndə 5 stul var'), 'action')[0]['action']
    fields = {f['key']: f['value'] for f in action['preview']['fields']}
    assert fields['selling_price'] is None and fields['purchase_price'] is None
    r = a.post(f'/ai/actions/{action["id"]}/confirm', json={'overrides': {'selling_price': '45', 'purchase_price': '30.5'}})
    assert r.status_code == 200, r.text
    card = next(b['action'] for m in a.get(f'/ai/conversations/{conv}').json()['messages']
                for b in m['blocks'] if b.get('type') == 'action' and b['action']['id'] == action['id'])
    fields = {f['key']: f['value'] for f in card['preview']['fields']}
    assert (fields['selling_price'], fields['purchase_price']) == ('45.00', '30.50')
    product = a.get(a.biz('/products')).json()['items'][0]
    assert (product['selling_price'], product['purchase_price']) == ('45.00', '30.50')
