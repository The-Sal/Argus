"""Offline tests for Polymarket CLOB V2 robustness (no network)."""
import os
import json

os.environ.setdefault('POLYMARKET_UNSAFE_RAPID_CONNECTIONS', 'true')  # skip proxy wiring

import pytest

from argus.polymarket_direct import wss
from argus.polymarket_direct.order_types import OrderEvent, TradeEvent, Trade
from argus.polymarket_direct._types import Market, PolymarketEvent
from argus.polymarket._classes import P2ConvertClass
from argus.polymarket import _mem_slim

V2_ASSET = '495099235152725588201991226912420825532590817323357329480139627250245959680'
V1_ASSET = '661095475084821930790589425827399710453605787397495798070750303202782280580'
MARKET = '0xbd31dc8a20211944f6b70f31557f1001557b59905b7738480ca09bd4532f84af'


def book(asset=V1_ASSET, version=False, tick='0.01', ts='1770251679393'):
    f = {'market': MARKET, 'asset_id': asset, 'timestamp': ts, 'hash': 'abc',
         'bids': [{'price': '0.74', 'size': '10'}, {'price': '0.75', 'size': '65'}],
         'asks': [{'price': '0.77', 'size': '5'}, {'price': '0.76', 'size': '227.02'}],
         'tick_size': tick, 'event_type': 'book', 'last_trade_price': '0.75'}
    if version:
        f['version'] = 2
    return f


@pytest.fixture
def store():
    got = []
    s = wss.OrderBookStore(order_book_update_callback=got.append)
    s.got = got
    return s


def feed(s, frame):
    s.apply_message(json.dumps(frame))


@pytest.mark.parametrize('asset,version,tick', [
    (V1_ASSET, False, '0.01'), (V2_ASSET, True, '0.005'), (V2_ASSET, True, '0.0025'),
])
def test_book_variants(store, asset, version, tick):
    feed(store, book(asset, version, tick))
    b = store.order_book_for_asset_id(asset)
    assert b['bids'][0]['price'] == '0.75' and b['asks'][0]['price'] == '0.76'
    assert store.got[-1][asset] is b
    assert store.got[-1]['timestamp'] == '1770251679393'


def test_book_missing_timestamp_falls_back(store):
    f = book(V2_ASSET, True)
    del f['timestamp']
    feed(store, f)
    assert store.got[-1]['timestamp'] not in ('', None)


def test_price_change_multi_and_zero_size_numeric(store):
    feed(store, book(V2_ASSET, True))
    pc = {'market': MARKET, 'timestamp': '1770251679999', 'event_type': 'price_change',
          'price_changes': [
              {'asset_id': V2_ASSET, 'price': '0.75', 'size': '0.00', 'side': 'BUY', 'hash': 'h',
               'best_bid': '0.74', 'best_ask': '0.76'},
              {'asset_id': V2_ASSET, 'price': '0.78', 'size': '3', 'side': 'SELL', 'hash': 'h'},
              {'price': '0.1', 'size': '1', 'side': 'BUY'},  # no asset_id: skipped
          ]}
    feed(store, pc)
    b = store.order_book_for_asset_id(V2_ASSET)
    assert [l['price'] for l in b['bids']] == ['0.74']
    assert [l['price'] for l in b['asks']] == ['0.76', '0.77', '0.78']


def test_price_change_missing_timestamp(store):
    feed(store, book(V2_ASSET, True))
    feed(store, {'market': MARKET, 'event_type': 'price_change', 'price_changes': [
        {'asset_id': V2_ASSET, 'price': '0.9', 'size': '1', 'side': 'SELL'}]})
    assert store.got[-1]['timestamp']


def test_last_trade_price_and_unknown_events(store):
    feed(store, book(V2_ASSET, True))
    n = len(store.got)
    feed(store, {'event_type': 'last_trade_price', 'asset_id': V2_ASSET, 'market': MARKET,
                 'price': '0.5', 'size': '3', 'side': 'BUY', 'fee_rate_bps': '0'})
    assert len(store.got) == n + 1 and store.got[-1]['timestamp']
    for weird in ({'event_type': 'market_resolved', 'market': MARKET},
                  {'event_type': 'brand_new', 'asset_id': V2_ASSET},
                  {'foo': 'bar'}, {'error': 'x'}):
        feed(store, weird)  # must not raise
    store.apply_message('NO NEW ASSETS')


def test_tick_size_change_small_ticks(store):
    feed(store, book(V2_ASSET, True))
    for new in ('0.005', '0.0025'):
        feed(store, {'event_type': 'tick_size_change', 'asset_id': V2_ASSET, 'market': MARKET,
                     'old_tick_size': '0.01', 'new_tick_size': new, 'timestamp': '1'})
        assert store._asset_id_to_misc_info[V2_ASSET]['tick_size'] == new


def test_best_bid_ask_missing_ts(store):
    feed(store, book(V2_ASSET, True))
    feed(store, {'event_type': 'best_bid_ask', 'asset_id': V2_ASSET, 'market': MARKET,
                 'best_bid': '0.75', 'best_ask': '0.76'})
    assert store.got[-1]['timestamp']


# ---- user channel ---------------------------------------------------------

def _user_cls():
    for name in dir(wss):
        c = getattr(wss, name)
        if isinstance(c, type) and hasattr(c, '_dispatch_user_frame'):
            return c
    raise AssertionError('user channel class not found')


def user_ws():
    got = []
    ws = object.__new__(_user_cls())
    ws._update_callback = got.append
    ws._throw_fuss_on_user_events = False
    return ws, got


ORDER = {'id': '0xo', 'owner': 'k', 'market': MARKET, 'asset_id': V2_ASSET, 'side': 'BUY',
         'order_owner': 'k', 'original_size': '10', 'size_matched': '0', 'price': '0.5',
         'associate_trades': None, 'outcome': 'Yes', 'type': 'PLACEMENT', 'created_at': '1770251679',
         'expiration': '0', 'order_type': 'GTC', 'status': 'LIVE', 'maker_address': '0xm',
         'timestamp': '1770251679393', 'event_type': 'order'}
TRADE = {'id': 't', 'taker_order_id': '0xo', 'market': MARKET, 'asset_id': V2_ASSET, 'side': 'BUY',
         'size': '5', 'price': '0.5', 'status': 'MATCHED', 'match_time': '1770251679',
         'last_update': '1770251679', 'outcome': 'Yes', 'owner': 'k', 'trade_owner': 'k',
         'maker_address': '0xm', 'transaction_hash': '0xh', 'bucket_index': 0, 'trader_side': 'TAKER',
         'maker_orders': [{'order_id': 'x', 'owner': 'k', 'maker_address': '0xm', 'matched_amount': '5',
                           'price': '0.5', 'asset_id': V2_ASSET, 'outcome': 'Yes', 'outcome_index': 0,
                           'side': 'SELL'}],
         'timestamp': '1770251679394', 'event_type': 'trade', 'type': 'TRADE'}


def test_user_routes_order_and_trade():
    ws, got = user_ws()
    ws._on_message_impl(json.dumps(ORDER))
    ws._on_message_impl(json.dumps(TRADE))  # no fee_rate_bps (V2)
    assert isinstance(got[0], OrderEvent) and isinstance(got[1], TradeEvent)
    d = got[1].to_dict()
    assert d['fee_rate_bps'] == '0' and isinstance(d['bucket_index'], int)
    assert d['maker_orders'][0]['fee_rate_bps'] == '0' and d['maker_orders'][0]['outcome_index'] == 0
    od = got[0].to_dict()
    assert od['associate_trades'] == [] and od['type'] == 'PLACEMENT'


def test_user_routes_on_type_fallback_and_extra_keys():
    ws, got = user_ws()
    t = {k: v for k, v in TRADE.items() if k != 'event_type'}
    t['brand_new_v2_key'] = 1
    ws._on_message_impl(json.dumps(t))
    assert isinstance(got[0], TradeEvent)


@pytest.mark.parametrize('frame', [
    {'event_type': 'something_new', 'x': 1}, {'error': 'bad auth'}, {'message': 'ack'},
    {'event_type': 'trade', 'id': 'only-id'}, {}, [], 'PONG',
])
def test_user_skips_garbage(frame):
    ws, got = user_ws()
    ws._on_message_impl(frame if isinstance(frame, str) else json.dumps(frame))
    assert got == []


def test_user_missing_timestamp_never_empty():
    ws, got = user_ws()
    o = {k: v for k, v in ORDER.items() if k != 'timestamp'}
    ws._on_message_impl(json.dumps(o))
    assert got[0].timestamp != ''
    o['timestamp'] = ''
    ws._on_message_impl(json.dumps(o))
    assert got[1].timestamp != ''


def test_rest_trade_without_fee():
    t = Trade.from_dict({'id': 'a', 'taker_order_id': 'b', 'market': MARKET, 'asset_id': V2_ASSET,
                         'side': 'BUY', 'size': '1', 'price': '0.5', 'status': 'MINED',
                         'match_time': '1', 'maker_orders': [
                             {'order_id': 'x', 'matched_amount': '1', 'price': '0.5', 'asset_id': V2_ASSET}],
                         'extra': 1})
    assert t.fee_rate_bps == 0 and t.maker_orders[0].fee_rate_bps == 0


# ---- P2 / types -------------------------------------------------------------

def test_p2_never_empty_timestamp():
    for md in ({V2_ASSET: {'bids': [], 'asks': []}},
               {V2_ASSET: {'bids': [], 'asks': []}, 'timestamp': ''}):
        pkt = P2ConvertClass('t', 's', V2_ASSET, md, 2).transferable_2().decode()
        ts = pkt.split(',')[-2]
        assert ts.isdigit() and int(ts) > 0


def test_market_v2_fields_and_to_dict_unchanged():
    m = Market.from_dict({'slug': 's', 'positionIds': json.dumps([V2_ASSET, '2']), 'version': 2,
                          'resolutionStatus': 'x'})
    assert m.positionIds == [V2_ASSET, '2'] and m.outcome_token_ids() == [V2_ASSET, '2']
    m2 = Market.from_dict({'clobTokenIds': '["a","b"]', 'positionIds': ['c']})
    assert m2.outcome_token_ids() == ['a', 'b']
    assert Market.from_dict({}).outcome_token_ids() is None
    ev = PolymarketEvent.from_dict({'id': '1', 'markets': [
        {'slug': 's', 'positionIds': ['c'], 'version': 2, 'resolutionStatus': 'x'}]})
    mk = ev.to_dict()['markets'][0]
    assert not ({'version', 'positionIds', 'resolutionStatus'} & set(mk))


def test_mem_slim_output_unchanged():
    ev = PolymarketEvent.from_dict({'id': '1', 'ticker': 't', 'markets': [
        {'slug': 's', 'clobTokenIds': '["a"]', 'positionIds': ['a'], 'version': 2}]})
    slim = _mem_slim.traverse_and_slim(ev)
    assert slim.markets[0].positionIds == ['a']
    out = slim.to_dict()
    assert 'version' not in out['markets'][0] and 'positionIds' not in out['markets'][0]


def test_subscribe_to_market_plain_string_ids():
    pool = object.__new__(wss.PolyMarketOrderBookPool)
    seen = []
    pool.subscribe_to_asset_id = seen.append
    ev = PolymarketEvent.from_dict({'id': '1', 'markets': [{'clobTokenIds': '["a","b"]'}]})
    pool.subscribe_to_market(ev)
    assert seen == ['a', 'b']
