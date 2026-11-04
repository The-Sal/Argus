"""
Offline tests for the PolyBolt TWAP stream (client + dispatcher wiring + legacy RTDS switch).

No network, no Polymarket, no fixed ports: the fake server binds 127.0.0.1:0 (ephemeral) and the client
runner is injected (plain ``run_forever``, no WireProxy). Frames come from the sanitized Phase 0 capture in
tests/fixtures/polybolt_twap_frames.json.

Run:  python -m pytest tests/test_polybolt_twap.py -q
"""
import os
import json
import time
import random
import socket
import threading
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('POLYMARKET_UNSAFE_RAPID_CONNECTIONS', 'true')  # skip proxy wiring

import pytest
from websockets.sync.server import serve

from argus.polymarket_direct import wss
from argus.polymarket_direct.wss import PolyBoltTwapWss

FIXTURE = json.loads((Path(__file__).parent / 'fixtures' / 'polybolt_twap_frames.json').read_text())
CREDS = {'apiKey': 'k', 'secret': 's', 'passphrase': 'p'}
ALL_FRAMES = sorted(FIXTURE['snapshots'] + FIXTURE['live'], key=lambda f: f['seq'])
SYMBOLS = ('btcusd', 'ethusd', 'solusd', 'xrpusd', 'dogeusd')


class FakeWS:
    """Stands in for WebSocketApp in protocol-level tests."""

    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(json.loads(text))


def make_client(ticks=None, snaps=None, **kw):
    ticks = [] if ticks is None else ticks
    kw.setdefault('symbols', SYMBOLS)
    c = PolyBoltTwapWss(on_tick=lambda *a: ticks.append(a), creds_provider=lambda: dict(CREDS),
                        on_snapshot=(lambda s, p: snaps.append((s, p))) if snaps is not None else None, **kw)
    c.ticks = ticks
    return c


def wait_for(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------------------- env parsing
def test_symbols_from_env(monkeypatch):
    monkeypatch.delenv('POLYMARKET_TWAP_SYMBOLS', raising=False)
    assert wss.polybolt_symbols_from_env() == SYMBOLS
    monkeypatch.setenv('POLYMARKET_TWAP_SYMBOLS', ' BTCUSD, ethusd ,btcusd,,')
    assert wss.polybolt_symbols_from_env() == ('btcusd', 'ethusd')
    monkeypatch.setenv('POLYMARKET_TWAP_SYMBOLS', ' , ')
    assert wss.polybolt_symbols_from_env() == SYMBOLS


def test_url_default_and_env(monkeypatch):
    monkeypatch.delenv('POLYMARKET_POLYBOLT_URL', raising=False)
    assert make_client()._url == 'wss://ws-live-v2.polymarket.com/ws'
    monkeypatch.setenv('POLYMARKET_POLYBOLT_URL', 'ws://example/x')
    assert make_client()._url == 'ws://example/x'


# ---------------------------------------------------------------------------- protocol (fake ws)
def test_fixture_is_contiguous_seq_1_to_20():
    assert [f['seq'] for f in ALL_FRAMES] == list(range(1, 21))


def test_on_open_sends_auth_once_with_creds():
    c = make_client()
    c._creds = dict(CREDS)
    ws = FakeWS()
    c._on_open(ws)
    assert ws.sent == [{'op': 'auth', 'rid': 'auth', 'auth': CREDS}]


def test_authed_triggers_one_subscribe_frame_with_all_symbols():
    c = make_client()
    ws = FakeWS()
    c.handle_frame(ws, json.dumps(FIXTURE['authed']))
    assert c.connected.is_set() and c.stats['authed'] == 1
    assert len(ws.sent) == 1
    f = ws.sent[0]
    assert f['op'] == 'subscribe'
    assert f['subscriptions'] == [
        {'channel': 'price.crypto.twap', 'filter': {'symbol': s, 'window_seconds': 60}} for s in SYMBOLS]
    assert c.subscribed_symbols == set(SYMBOLS)


def test_subscribe_respects_per_frame_and_connection_limits():
    c = make_client(symbols=[f's{i}usd' for i in range(40)])
    ws = FakeWS()
    c.handle_frame(ws, json.dumps(FIXTURE['authed']))
    assert [len(f['subscriptions']) for f in ws.sent] == [16, 16, 8]
    c = make_client(symbols=[f's{i}usd' for i in range(100)])
    assert len(c.symbols) == 64


def test_snapshots_not_forwarded_by_default_but_stored():
    snaps = []
    c = make_client(snaps=snaps)
    for f in ALL_FRAMES[:5]:
        c.handle_frame(FakeWS(), json.dumps(f))
    assert c.ticks == []
    assert c.stats['snapshots'] == 5 and c.stats['live'] == 0
    assert [s for s, _ in snaps] == list(SYMBOLS)
    assert len(snaps[0][1]) == 6
    sym0 = FIXTURE['snapshots'][0]['payload']
    assert c.latest['btcusd'] == (sym0['data'][-1]['value'], sym0['data'][-1]['timestamp'])


def test_snapshots_forwarded_when_configured():
    c = make_client(forward_snapshots=True)
    for f in ALL_FRAMES[:5]:
        c.handle_frame(FakeWS(), json.dumps(f))
    assert len(c.ticks) == 5 * 6
    assert all(t[4] is True for t in c.ticks)
    assert [t[2] for t in c.ticks[:6]] == sorted(t[2] for t in c.ticks[:6])  # oldest first


def test_live_frames_forwarded_and_no_gap_on_contiguous_seq():
    c = make_client()
    for f in ALL_FRAMES:
        c.handle_frame(FakeWS(), json.dumps(f))
    assert c.stats['live'] == 15 and len(c.ticks) == 15
    assert c.stats['seq_gaps'] == 0 and c.stats['dropped_frames'] == 0 and c.stats['seq_out_of_order'] == 0
    sym, value, ts, source, is_snap = c.ticks[0]
    first = FIXTURE['live'][0]['payload']
    assert (sym, value, ts, source, is_snap) == (first['symbol'], first['value'], first['timestamp'],
                                                   'chainlink', False)
    assert isinstance(ts, int) and ts % 1000 == 0


def test_seq_gap_detected_and_counted():
    c = make_client()
    frames = [f for f in ALL_FRAMES if f['seq'] not in (7, 8, 12)]
    for f in frames:
        c.handle_frame(FakeWS(), json.dumps(f))
    assert c.stats['seq_gaps'] == 2          # 6->9 and 11->13
    assert c.stats['seq_missing'] == 3
    assert c.stats['frames'] == 17


def test_out_of_order_seq_counted_and_last_seq_not_rewound():
    c = make_client()
    for seq in (1, 2, 3):
        c.handle_frame(FakeWS(), json.dumps(ALL_FRAMES[seq - 1]))
    c.handle_frame(FakeWS(), json.dumps(ALL_FRAMES[1]))  # seq 2 again
    c.handle_frame(FakeWS(), json.dumps(ALL_FRAMES[3]))  # seq 4: contiguous with 3
    assert c.stats['seq_out_of_order'] == 1 and c.stats['seq_gaps'] == 0


def test_dropped_flag_counted():
    c = make_client()
    f = dict(FIXTURE['live'][0], dropped=True)
    c.handle_frame(FakeWS(), json.dumps(f))
    g = dict(FIXTURE['live'][1], dropped=3)
    c.handle_frame(FakeWS(), json.dumps(g))
    c.handle_frame(FakeWS(), json.dumps(dict(FIXTURE['live'][2], dropped=False)))
    assert c.stats['dropped_frames'] == 4
    assert len(c.ticks) == 3  # dropped frames still deliver their own payload


def test_seq_reset_on_new_connection_is_not_a_gap():
    c = make_client()
    c.handle_frame(FakeWS(), json.dumps(ALL_FRAMES[10]))   # seq 11
    c._reset_connection_state()
    c.handle_frame(FakeWS(), json.dumps(ALL_FRAMES[0]))    # seq 1 on new connection
    assert c.stats['seq_gaps'] == 0 and c.stats['seq_out_of_order'] == 0


def test_op_ping_answered_with_pong():
    c = make_client()
    ws = FakeWS()
    c.handle_frame(ws, json.dumps({'op': 'ping'}))
    assert ws.sent == [{'op': 'pong'}] and c.stats['server_pings'] == 1


def test_garbage_and_unknown_frames_do_not_raise():
    c = make_client()
    ws = FakeWS()
    c.handle_frame(ws, 'not json')
    c.handle_frame(ws, '[1,2]')
    c.handle_frame(ws, json.dumps({'op': 'weird'}))
    c.handle_frame(ws, json.dumps({'channel': 'other', 'payload': {}}))
    c.handle_frame(ws, b'{"op":"subscribed","channel":"price.crypto.twap"}')
    assert c.stats['decode_errors'] == 2 and c.stats['unknown_ops'] == 2 and c.ticks == []


def test_none_close_code_treated_as_1006():
    c = make_client()
    c._on_close(None, None, '')
    assert c._last_close_code == 1006
    c._on_close(None, 4003, 'draining')
    assert c._last_close_code == 4003


@pytest.mark.parametrize('code,lo,hi', [
    (4002, 1.0, 1.0), (4003, 0.0, 10.0), (4001, 5.0, 5.0), (4008, 5.0, 5.0),
    (1006, 1.0, 1.0), (1011, 1.0, 1.0),
])
def test_backoff_first_attempt(code, lo, hi):
    c = make_client(rng=random.Random(1))
    for _ in range(50):
        assert lo <= c._backoff_for(code, 1) <= hi


def test_backoff_grows_and_caps():
    c = make_client(rng=random.Random(2))
    assert max(c._backoff_for(1006, 20) for _ in range(100)) <= 30.0
    assert min(c._backoff_for(1006, 20) for _ in range(100)) >= 15.0
    assert c._backoff_for(4001, 3) == 20.0 and c._backoff_for(4001, 10) == 60.0
    assert all(c._backoff_for(1006, 1) >= 1.0 for _ in range(50))


# ---------------------------------------------------------------------------- fake server (real sockets, ephemeral port)
class FakeServer:
    """
    websockets sync server on 127.0.0.1:<ephemeral>. ``script[i]`` decides what connection i does; the last
    entry repeats. Every inbound frame is recorded per connection in ``self.conns``.
    """

    def __init__(self, script):
        self.script = script
        self.conns = []
        self._lock = threading.Lock()
        self.server = serve(self._handler, '127.0.0.1', 0, close_timeout=0.5)
        self.port = self.server.socket.getsockname()[1]
        self.url = f'ws://127.0.0.1:{self.port}/ws'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _handler(self, ws):
        with self._lock:
            idx = len(self.conns)
            rec = {'in': [], 'idx': idx}
            self.conns.append(rec)
        behaviour = self.script[min(idx, len(self.script) - 1)]
        behaviour(ws, rec)

    def close(self):
        self.server.shutdown()


def recv_json(ws, rec, timeout=5):
    msg = json.loads(ws.recv(timeout=timeout))
    rec['in'].append(msg)
    return msg


def serve_frames(ws, rec, then=None, ping=False):
    """Auth handshake, subscribe handshake, send snapshots + live fixture frames, then `then`."""
    auth = recv_json(ws, rec)
    assert auth['op'] == 'auth'
    ws.send(json.dumps(FIXTURE['authed']))
    sub = recv_json(ws, rec)
    assert sub['op'] == 'subscribe'
    ws.send(json.dumps(FIXTURE['subscribed']))
    if ping:
        ws.send(json.dumps({'op': 'ping'}))
        rec['pong'] = recv_json(ws, rec)
    for f in ALL_FRAMES:
        ws.send(json.dumps(f))
    if then:
        then(ws, rec)
    else:
        try:
            while True:
                ws.recv(timeout=30)
        except Exception:
            pass


def close_with(code):
    def _c(ws, rec):
        ws.close(code, 'bye')
    return _c


def drop_tcp(ws, rec):
    ws.socket.shutdown(socket.SHUT_RDWR)


def run_client(server, **kw):
    sleeps = []
    stop_holder = {}

    def sleep(s):
        sleeps.append(s)
        return stop_holder['c']._stop_event.is_set()

    ticks = []
    c = PolyBoltTwapWss(on_tick=lambda *a: ticks.append(a), creds_provider=lambda: dict(CREDS), symbols=SYMBOLS,
                        url=server.url, runner=lambda ws: ws.run_forever(), sleep=sleep, rng=random.Random(3), **kw)
    stop_holder['c'] = c
    c.sleeps, c.ticks = sleeps, ticks
    c.start()
    return c


@pytest.fixture
def cleanup():
    items = []
    yield items
    for i in reversed(items):  # clients before servers
        try:
            i.stop() if hasattr(i, 'stop') else i.close()
        except Exception:
            pass


def test_end_to_end_auth_subscribe_snapshot_live(cleanup):
    srv = FakeServer([lambda ws, rec: serve_frames(ws, rec, ping=True)])
    cleanup.append(srv)
    c = run_client(srv)
    cleanup.append(c)
    assert wait_for(lambda: c.stats['live'] == 15)
    rec = srv.conns[0]
    assert rec['in'][0] == {'op': 'auth', 'rid': 'auth', 'auth': CREDS}
    sub = rec['in'][1]
    assert [s['filter']['symbol'] for s in sub['subscriptions']] == list(SYMBOLS)
    assert rec['pong'] == {'op': 'pong'}
    assert c.stats['snapshots'] == 5 and c.stats['seq_gaps'] == 0 and c.stats['disconnects'] == 0
    assert len(c.ticks) == 15


def test_websocket_level_ping_is_answered(cleanup):
    result = {}

    def behaviour(ws, rec):
        serve_frames(ws, rec, then=lambda w, r: result.update(pong=w.ping().wait(3)) or time.sleep(0.2))

    srv = FakeServer([behaviour])
    cleanup.append(srv)
    c = run_client(srv)
    cleanup.append(c)
    assert wait_for(lambda: 'pong' in result)
    assert result['pong'] is True


@pytest.mark.parametrize('code,expected_range', [
    (4002, (1.0, 1.0)), (4003, (0.0, 10.0)), (1006, (1.0, 1.0)), (4001, (5.0, 5.0)), (4008, (5.0, 5.0)),
])
def test_close_code_policy_then_reconnect_reauth_resubscribe(cleanup, code, expected_range):
    first = drop_tcp if code == 1006 else close_with(code)
    if code in (4001, 4008):
        # rejected before authed: server closes right after reading auth
        def first_conn(ws, rec):
            recv_json(ws, rec)
            ws.close(code, 'no')
    else:
        def first_conn(ws, rec):
            serve_frames(ws, rec, then=first)
    srv = FakeServer([first_conn, lambda ws, rec: serve_frames(ws, rec)])
    cleanup.append(srv)
    c = run_client(srv)
    cleanup.append(c)
    assert wait_for(lambda: len(srv.conns) >= 2 and c.stats['live'] >= 15 and (code in (4001, 4008) or c.stats['live'] >= 30)
                    or (code in (4001, 4008) and c.stats['live'] >= 15))
    assert expected_range[0] <= c.sleeps[0] <= expected_range[1]
    assert c.stats['close_codes'].get(code) == 1
    # second connection re-authenticated and resubscribed
    ops = [m['op'] for m in srv.conns[1]['in']]
    assert ops[:2] == ['auth', 'subscribe']
    assert srv.conns[1]['in'][0]['auth'] == CREDS
    # server restarted seq at 1 on the new connection -> never counted as a gap
    assert c.stats['seq_gaps'] == 0 and c.stats['seq_out_of_order'] == 0


@pytest.mark.parametrize('code', [4001, 4008])
def test_auth_and_policy_closes_give_up_after_repeated_failures(cleanup, code):
    def reject(ws, rec):
        recv_json(ws, rec)
        ws.close(code, 'no')

    srv = FakeServer([reject])
    cleanup.append(srv)
    c = run_client(srv, max_consecutive_failures=3)
    cleanup.append(c)
    assert wait_for(lambda: c.gave_up)
    assert len(srv.conns) == 3
    assert c.sleeps == [5.0, 10.0]          # no sleep after the final failure
    assert c.stats['close_codes'] == {code: 3}


def test_successful_auth_resets_failure_streak(cleanup):
    def reject(ws, rec):
        recv_json(ws, rec)
        ws.close(4001, 'no')

    ok_then_4003 = lambda ws, rec: serve_frames(ws, rec, then=close_with(4003))
    srv = FakeServer([reject, ok_then_4003, reject, reject, lambda ws, rec: serve_frames(ws, rec)])
    cleanup.append(srv)
    c = run_client(srv, max_consecutive_failures=4)
    cleanup.append(c)
    assert wait_for(lambda: len(srv.conns) >= 5 and c.stats['live'] >= 30)
    assert not c.gave_up


def test_creds_provider_called_on_every_connect(cleanup):
    calls = []

    def provider():
        calls.append(1)
        return dict(CREDS)

    srv = FakeServer([lambda ws, rec: serve_frames(ws, rec, then=close_with(4002)),
                      lambda ws, rec: serve_frames(ws, rec)])
    cleanup.append(srv)
    c = PolyBoltTwapWss(on_tick=lambda *a: None, creds_provider=provider, symbols=SYMBOLS, url=srv.url,
                        runner=lambda ws: ws.run_forever(), sleep=lambda s: False)
    cleanup.append(c)
    c.start()
    assert wait_for(lambda: len(srv.conns) >= 2 and c.stats['authed'] >= 2)
    assert len(calls) >= 2


def test_creds_provider_failure_is_retried_not_fatal(cleanup):
    attempts = []

    def provider():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError('creds endpoint down')
        return dict(CREDS)

    srv = FakeServer([lambda ws, rec: serve_frames(ws, rec)])
    cleanup.append(srv)
    c = PolyBoltTwapWss(on_tick=lambda *a: None, creds_provider=provider, symbols=SYMBOLS, url=srv.url,
                        runner=lambda ws: ws.run_forever(), sleep=lambda s: False, rng=random.Random(0))
    cleanup.append(c)
    c.start()
    assert wait_for(lambda: c.stats['live'] >= 15)


def test_server_dropped_and_gap_over_real_socket(cleanup):
    def behaviour(ws, rec):
        recv_json(ws, rec)
        ws.send(json.dumps(FIXTURE['authed']))
        recv_json(ws, rec)
        for f in ALL_FRAMES:
            if f['seq'] == 9:
                continue
            ws.send(json.dumps(dict(f, dropped=True) if f['seq'] == 12 else f))
        time.sleep(0.5)

    srv = FakeServer([behaviour])
    cleanup.append(srv)
    c = run_client(srv)
    cleanup.append(c)
    assert wait_for(lambda: c.stats['frames'] == 19)
    assert c.stats['seq_gaps'] == 1 and c.stats['seq_missing'] == 1 and c.stats['dropped_frames'] == 1


def test_stop_ends_supervisor(cleanup):
    srv = FakeServer([lambda ws, rec: serve_frames(ws, rec)])
    cleanup.append(srv)
    c = run_client(srv)
    assert wait_for(lambda: c.stats['live'] == 15)
    c.stop()
    assert wait_for(lambda: not c._thread.is_alive())
    c.start()  # restartable
    assert wait_for(lambda: c.stats['connections'] == 2)
    cleanup.append(c)


# ---------------------------------------------------------------------------- dispatcher wiring
from argus.polymarket import PolymarketDispatcher, env_flag, LEGACY_RTDS_DISABLED_MSG  # noqa: E402
from argus._argus_utils import RoutingHelper  # noqa: E402


class FakeSock:
    def __init__(self):
        self.data = b''

    def sendall(self, b):
        self.data += b


def make_dispatcher(legacy=True, monkeypatch=None):
    d = object.__new__(PolymarketDispatcher)
    RoutingHelper.__init__(d)
    d._configs = {'Show P1 Packets': False, 'Print P2 packets': False, 'Show packet timestamps': True,
                  'Block Order Execution': False, 'show response times': False, 'P2 Packets for RTDS': False}
    d._orderbook_depth = 10
    d.rtds_magic_asset_id = 'RTDS_MAGIC_ID'
    d.polybolt_magic_asset_id = 'POLYBOLT_MAGIC_ID'
    d._rtds_legacy_enabled = legacy
    d.real_time_data = None
    d.polybolt = None
    d._polybolt_lock = threading.Lock()
    d.rest_api = SimpleNamespace(credentials=dict(CREDS))
    d.market_data = SimpleNamespace(unsubscribe_from_asset_id=lambda cid: pytest.fail(f'pool touched for {cid}'))
    return d


class StubTwap:
    instances = []

    def __init__(self, on_tick, creds_provider, **kw):
        self.on_tick, self.creds_provider = on_tick, creds_provider
        self.symbols = SYMBOLS
        self.window_seconds = 60
        self.starts = 0
        StubTwap.instances.append(self)

    def start(self):
        self.starts += 1


@pytest.fixture
def stub_twap(monkeypatch):
    StubTwap.instances = []
    monkeypatch.setattr(wss, 'PolyBoltTwapWss', StubTwap)
    return StubTwap


def call(d, action, sock):
    return d._handle_client_message(sock, ('127.0.0.1', 0), {'action': action, 'data': None})


def test_env_flag_parsing(monkeypatch):
    monkeypatch.delenv('X_FLAG', raising=False)
    assert env_flag('X_FLAG', True) is True and env_flag('X_FLAG', False) is False
    for v in ('0', 'false', 'OFF', ' no '):
        monkeypatch.setenv('X_FLAG', v)
        assert env_flag('X_FLAG', True) is False
    for v in ('1', 'true', 'On', 'YES'):
        monkeypatch.setenv('X_FLAG', v)
        assert env_flag('X_FLAG', False) is True
    monkeypatch.setenv('X_FLAG', 'garbage')
    assert env_flag('X_FLAG', True) is True
    monkeypatch.setenv('X_FLAG', '')
    assert env_flag('X_FLAG', False) is False


def test_twap_actions_registered_and_lazy_start_once(stub_twap):
    d = make_dispatcher()
    assert stub_twap.instances == []                      # nothing started at construction
    s1, s2 = FakeSock(), FakeSock()
    r1 = call(d, 'twap_subscribe', s1)
    assert r1 == {'subscribed': ['POLYBOLT_MAGIC_ID'], 'failed': [], 'symbols': list(SYMBOLS)}
    call(d, 'twap_subscribe', s2)
    assert len(stub_twap.instances) == 1                  # one client
    assert stub_twap.instances[0].creds_provider() == CREDS
    assert d._market_data_routing_table['POLYBOLT_MAGIC_ID'] == [s1, s2]


def test_twap_unsubscribe_keeps_connection_and_never_touches_clob_pool(stub_twap):
    d = make_dispatcher()
    s1 = FakeSock()
    call(d, 'twap_subscribe', s1)
    assert call(d, 'twap_unsubscribe', s1) == {'unsubscribed': ['POLYBOLT_MAGIC_ID'], 'failed': []}
    assert 'POLYBOLT_MAGIC_ID' not in d._market_data_routing_table
    d.remove_socket(s1)  # path through subscription_expired must not call the order-book pool


def test_twap_subscribe_start_failure_reported(monkeypatch):
    def boom(**kw):
        raise RuntimeError('no creds')
    monkeypatch.setattr(wss, 'PolyBoltTwapWss', boom)
    d = make_dispatcher()
    r = call(d, 'twap_subscribe', FakeSock())
    assert r['subscribed'] == [] and 'no creds' in r['failed'][0]


def test_tick_callback_routes_p2_packet_only_to_twap_subscribers(stub_twap):
    d = make_dispatcher()
    twap_sock, rtds_sock = FakeSock(), FakeSock()
    call(d, 'twap_subscribe', twap_sock)
    call(d, 'rtds_subscribe', rtds_sock)
    d._polybolt_tick_callback('btcusd', 77107.5, 1793704117000, 'chainlink', False)
    assert b'chainlink-twap60-btcusd' in twap_sock.data
    assert b'77107.5' in twap_sock.data and b'1793704117000' in twap_sock.data
    assert rtds_sock.data == b''


def test_dead_socket_removed_on_send_failure(stub_twap):
    d = make_dispatcher()

    class Dead(FakeSock):
        def sendall(self, b):
            raise BrokenPipeError()

    dead, ok = Dead(), FakeSock()
    call(d, 'twap_subscribe', dead)
    call(d, 'twap_subscribe', ok)
    d._polybolt_tick_callback('ethusd', 2500.5, 1793704117000, 'chainlink', False)
    assert ok.data and d._market_data_routing_table['POLYBOLT_MAGIC_ID'] == [ok]


def test_legacy_on_rtds_actions_work_unchanged(stub_twap):
    d = make_dispatcher(legacy=True)
    s = FakeSock()
    assert call(d, 'rtds_subscribe', s) == {'subscribed': ['RTDS_MAGIC_ID'], 'failed': []}
    assert call(d, 'rtds_unsubscribe', s) == {'unsubscribed': ['RTDS_MAGIC_ID'], 'failed': []}


def test_legacy_off_rtds_actions_return_clear_error(stub_twap):
    d = make_dispatcher(legacy=False)
    s = FakeSock()
    r = call(d, 'rtds_subscribe', s)
    assert r == {'subscribed': [], 'failed': [LEGACY_RTDS_DISABLED_MSG]}
    assert 'legacy RTDS stream disabled' in r['failed'][0]
    assert 'RTDS_MAGIC_ID' not in d._market_data_routing_table
    assert call(d, 'rtds_unsubscribe', s) == {'unsubscribed': [], 'failed': [LEGACY_RTDS_DISABLED_MSG]}


def test_legacy_off_does_not_disable_twap(stub_twap):
    d = make_dispatcher(legacy=False)
    assert call(d, 'twap_subscribe', FakeSock())['subscribed'] == ['POLYBOLT_MAGIC_ID']
