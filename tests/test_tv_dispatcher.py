"""
Offline tests for the TradingView dispatcher (argus.tv.dispatcher).

Covers, without any network access:
- TVP2ConvertClass P2 wire round-trip (via argus.protocol.Protocol2Parser)
- DispatcherQuoteSession: multi-symbol roster on ONE session, (re)handshake replay,
  symbol-aware qsd dispatch
- TradingViewDispatcher: P1 request/response envelopes (subscribe / unsubscribe /
  get_subscriptions / ping / version), correlation-ID enforcement, and P2 fan-out

Run with: pytest tests/test_tv_dispatcher.py   (or: python -m unittest tests.test_tv_dispatcher)
"""
import json
import unittest
import uuid

from argus.protocol import Protocol2Parser, encode_packet, transmit_mkt_data_with_protocol_2
from argus.tv.dispatcher import (
    TradingViewDispatcher,
    TradingViewQuoteWss,
    DispatcherQuoteSession,
    TVP2ConvertClass,
)


class FakeWs:
    """Stands in for websocket.WebSocketApp: records sent frames, fakes connectivity."""

    def __init__(self, connected=True):
        self.connected = connected
        self.sent = []
        self.keep_running = True
        self.sock = self  # real WebSocketApp exposes the core socket (with .connected) as .sock

    def send(self, msg):
        self.sent.append(msg)

    def methods(self):
        """Decode the ~m~<size>~m~<json> frames back into (method, params) tuples."""
        out = []
        for raw in self.sent:
            parts = raw.split("~m~")
            payload = json.loads(parts[2])
            out.append((payload["m"], payload["p"]))
        return out


class FakeSocket:
    """Stands in for a client TCP socket: records everything sent to it."""

    def __init__(self):
        self.sent = []

    def sendall(self, data):
        self.sent.append(data)


def _p1_request(action, data=None, correlation_id=None):
    payload = json.dumps({
        "action": action,
        "data": data,
        "correlation_id": correlation_id or str(uuid.uuid4()),
    }).encode("utf-8")
    return encode_packet(payload)


def _read_p1_response(sock: FakeSocket) -> dict:
    """Read one P1 frame off a fake socket and decode the envelope."""
    frame = sock.sent.pop(0)
    assert frame.startswith(b"~")
    body = frame.split(b"|", 1)[1]
    return json.loads(body)


class TVP2ConvertTest(unittest.TestCase):
    def test_wire_round_trip_full_quote(self):
        quote = {
            "bid": 232.45, "bid_size": 40, "ask": 232.47, "ask_size": 30,
            "lp": 232.46, "ch": 1.23, "chp": 0.53, "volume": 51234567,
            "lp_time": 1758000000.123,
        }
        packet = transmit_mkt_data_with_protocol_2(
            TVP2ConvertClass(symbol="NASDAQ:AAPL", quote=quote)
        )
        decoded = Protocol2Parser(TVP2ConvertClass.FIELD_ORDER).parse(packet)
        self.assertEqual(decoded["symbol"], "NASDAQ:AAPL")
        self.assertEqual(decoded["bid"], 232.45)
        self.assertEqual(decoded["bid_size"], 40.0)
        self.assertEqual(decoded["ask"], 232.47)
        self.assertEqual(decoded["ask_size"], 30.0)
        self.assertEqual(decoded["last"], 232.46)
        self.assertEqual(decoded["change"], 1.23)
        self.assertEqual(decoded["change_pct"], 0.53)
        self.assertEqual(decoded["volume"], 51234567.0)
        self.assertEqual(decoded["timestamp"], 1758000000.123)
        self.assertGreater(decoded["transmission_time"], 0)

    def test_missing_fields_render_as_zero(self):
        packet = transmit_mkt_data_with_protocol_2(
            TVP2ConvertClass(symbol="BINANCE:BTCUSDT", quote={"lp": 65000})
        )
        decoded = Protocol2Parser(TVP2ConvertClass.FIELD_ORDER).parse(packet)
        self.assertEqual(decoded["last"], 65000.0)
        for field in ("bid", "bid_size", "ask", "ask_size", "change", "change_pct", "volume", "timestamp"):
            self.assertEqual(decoded[field], 0.0, field)

    def test_non_numeric_values_render_as_zero(self):
        conv = TVP2ConvertClass(symbol="X:Y", quote={"lp": "n/a", "ch": None})
        values = conv.transferable_2().decode("ascii").split(",")
        self.assertEqual(values[4], "0.0")  # last
        self.assertEqual(values[5], "0.0")  # change


class DispatcherQuoteSessionTest(unittest.TestCase):
    def _make(self, initial_symbols=()):
        session = DispatcherQuoteSession(initial_symbols=initial_symbols, callback=lambda s, v: None)
        session.ws = FakeWs(connected=True)
        return session

    def test_setup_without_anchor_bootstraps_sessions_only(self):
        session = self._make()
        session.on_open(None)  # as websocket-client would on connect
        session.setup_qs()     # as the first-message handshake would
        methods = [m for m, _ in session.ws.methods()]
        self.assertEqual(methods.count("quote_create_session"), 2)  # main + snapshotter
        self.assertIn("quote_set_fields", methods)
        self.assertNotIn("quote_add_symbols", methods)
        self.assertTrue(session._handshaked.is_set())

    def test_add_and_remove_symbols(self):
        session = self._make()
        session.on_open(None)
        session.setup_qs()
        session.ws.sent.clear()

        session.add_symbols(["NASDAQ:AAPL", "BINANCE:BTCUSDT"])
        pairs = session.ws.methods()
        self.assertEqual(pairs.count(("quote_add_symbols", [session.quote_session_id, "NASDAQ:AAPL", "BINANCE:BTCUSDT"])), 1)
        self.assertEqual(pairs.count(("quote_add_symbols", [session.quote_snapshotter, "NASDAQ:AAPL", "BINANCE:BTCUSDT"])), 1)
        self.assertEqual(session.symbols, ["BINANCE:BTCUSDT", "NASDAQ:AAPL"])

        session.ws.sent.clear()
        session.remove_symbols(["NASDAQ:AAPL"])
        pairs = session.ws.methods()
        self.assertIn(("quote_remove_symbols", [session.quote_session_id, "NASDAQ:AAPL"]), pairs)
        self.assertIn(("quote_remove_symbols", [session.quote_snapshotter, "NASDAQ:AAPL"]), pairs)
        self.assertEqual(session.symbols, ["BINANCE:BTCUSDT"])

    def test_add_before_connect_is_replayed_on_handshake(self):
        session = self._make()
        session.ws = FakeWs(connected=False)
        session.add_symbols(["NASDAQ:AAPL"])  # not connected -> roster only
        self.assertEqual(session.symbols, ["NASDAQ:AAPL"])

        session.ws = FakeWs(connected=True)   # (re)connect
        session.on_open(None)
        session.setup_qs()
        pairs = session.ws.methods()
        self.assertIn(("quote_add_symbols", [session.quote_session_id, "NASDAQ:AAPL"]), pairs)
        self.assertIn(("quote_add_symbols", [session.quote_snapshotter, "NASDAQ:AAPL"]), pairs)

    def test_reconnect_replays_full_roster(self):
        session = self._make(initial_symbols=["NASDAQ:AAPL"])
        session.ws = FakeWs(connected=True)
        session.on_open(None)
        session.setup_qs()  # first connect: anchor added by parent setup_qs
        self.assertIn(("quote_add_symbols", [session.quote_session_id, "NASDAQ:AAPL"]), session.ws.methods())

        session.add_symbols(["BINANCE:BTCUSDT"])
        session.ws.sent.clear()

        # Simulate a drop + websocket-client's built-in reconnect (on_open fires again)
        session.on_open(None)
        session.setup_qs()
        pairs = session.ws.methods()
        self.assertIn(("quote_add_symbols", [session.quote_session_id, "NASDAQ:AAPL"]), pairs)  # anchor via parent
        self.assertIn(("quote_add_symbols", [session.quote_session_id, "BINANCE:BTCUSDT"]), pairs)  # extra replayed

    def test_handle_quote_data_reports_symbol(self):
        seen = []
        session = DispatcherQuoteSession(callback=lambda s, v: seen.append((s, v)))
        session.handle_quote_data(["qs_abc", {"n": "NASDAQ:AAPL", "v": {"lp": 100.5, "ch": 1.0}}])
        self.assertEqual(seen, [("NASDAQ:AAPL", {"lp": 100.5, "ch": 1.0})])

    def test_handle_quote_data_ignores_malformed(self):
        seen = []
        session = DispatcherQuoteSession(callback=lambda s, v: seen.append((s, v)))
        session.handle_quote_data([])
        session.handle_quote_data(["qs_abc", {}])
        session.handle_quote_data(["qs_abc", {"n": None, "v": {"lp": 1}}])
        self.assertEqual(seen, [])


class TradingViewDispatcherTest(unittest.TestCase):
    """P1 routing + P2 fan-out with the upstream WS stubbed out (no network)."""

    def setUp(self):
        import argus.tv.dispatcher as mod
        self._orig_run = TradingViewQuoteWss.run
        TradingViewQuoteWss.run = lambda self, main_thread=False: None  # no upstream connection in tests
        self.disp = TradingViewDispatcher(port=0)  # ephemeral port; server thread not started

    def tearDown(self):
        TradingViewQuoteWss.run = self._orig_run

    def test_subscribe_and_get_subscriptions(self):
        sock = FakeSocket()
        req = _p1_request("subscribe", ["NASDAQ:AAPL", "BADSYMBOL"])
        self.disp._on_recv(sock, ("127.0.0.1", 1), req)
        resp = _read_p1_response(sock)

        self.assertEqual(resp["action"], "response")
        self.assertIsNone(resp["error"])
        self.assertFalse(resp["compressed"])
        self.assertEqual(resp["data"]["subscribed"], ["NASDAQ:AAPL"])
        self.assertEqual(resp["data"]["failed"], ["BADSYMBOL"])
        self.assertEqual(self.disp.market_data_routing_table.get("NASDAQ:AAPL"), [sock])
        self.assertIn("NASDAQ:AAPL", self.disp.market_data.symbols)  # registered on the shared upstream session

        req = _p1_request("get_subscriptions")
        self.disp._on_recv(sock, ("127.0.0.1", 1), req)
        resp = _read_p1_response(sock)
        self.assertEqual(resp["data"]["subscriptions"], ["NASDAQ:AAPL"])

    def test_correlation_id_required(self):
        sock = FakeSocket()
        payload = json.dumps({"action": "ping", "data": None}).encode("utf-8")
        self.disp._on_recv(sock, ("127.0.0.1", 1), encode_packet(payload))
        resp = _read_p1_response(sock)
        self.assertEqual(resp["action"], "error")
        self.assertIn("Correlation ID", resp["error"])

    def test_unknown_action(self):
        sock = FakeSocket()
        self.disp._on_recv(sock, ("127.0.0.1", 1), _p1_request("dance"))
        resp = _read_p1_response(sock)
        self.assertEqual(resp["action"], "error")
        self.assertIn("not valid", resp["error"])

    def test_ping_and_version(self):
        sock = FakeSocket()
        self.disp._on_recv(sock, ("127.0.0.1", 1), _p1_request("ping"))
        self.assertEqual(_read_p1_response(sock)["data"], "pong")

        self.disp._on_recv(sock, ("127.0.0.1", 1), _p1_request("version"))
        resp = _read_p1_response(sock)
        self.assertIn("tradingview_dispatcher", resp["data"])
        self.assertEqual(resp["data"]["tradingview_dispatcher"], [1, 0, 0, 0])

    def test_unsubscribe_drops_upstream_when_last_client(self):
        sock = FakeSocket()
        self.disp._on_recv(sock, ("127.0.0.1", 1), _p1_request("subscribe", ["NASDAQ:AAPL"]))
        _read_p1_response(sock)
        self.assertIn("NASDAQ:AAPL", self.disp.market_data.symbols)

        self.disp._on_recv(sock, ("127.0.0.1", 1), _p1_request("unsubscribe", ["NASDAQ:AAPL"]))
        resp = _read_p1_response(sock)
        self.assertEqual(resp["data"]["unsubscribed"], ["NASDAQ:AAPL"])
        self.assertNotIn("NASDAQ:AAPL", self.disp.market_data_routing_table)
        self.assertNotIn("NASDAQ:AAPL", self.disp.market_data.symbols)  # removed from the shared upstream session

    def test_client_disconnect_drops_its_subscriptions(self):
        sock = FakeSocket()
        self.disp._on_recv(sock, ("127.0.0.1", 1), _p1_request("subscribe", ["NASDAQ:AAPL"]))
        _read_p1_response(sock)

        self.disp._on_disconnect(sock, ("127.0.0.1", 1))
        self.assertNotIn("NASDAQ:AAPL", self.disp.market_data_routing_table)
        self.assertNotIn("NASDAQ:AAPL", self.disp.market_data.symbols)

    def test_p2_fanout_to_subscribed_clients(self):
        sock_a, sock_b = FakeSocket(), FakeSocket()
        self.disp._on_recv(sock_a, ("127.0.0.1", 1), _p1_request("subscribe", ["NASDAQ:AAPL"]))
        _read_p1_response(sock_a)
        self.disp._on_recv(sock_b, ("127.0.0.1", 2), _p1_request("subscribe", ["BINANCE:BTCUSDT"]))
        _read_p1_response(sock_b)

        # A quote for AAPL arrives on the shared upstream session -> only A gets a P2 packet
        self.disp._quote_callback("NASDAQ:AAPL", {"lp": 100.5, "ch": 1.0, "chp": 0.4, "bid": 100.4, "ask": 100.6})
        self.assertEqual(len(sock_a.sent), 1)
        self.assertEqual(len(sock_b.sent), 0)

        decoded = Protocol2Parser(TVP2ConvertClass.FIELD_ORDER).parse(sock_a.sent[0])
        self.assertEqual(decoded["symbol"], "NASDAQ:AAPL")
        self.assertEqual(decoded["last"], 100.5)
        self.assertEqual(decoded["change"], 1.0)
        self.assertEqual(decoded["bid"], 100.4)

    def test_quote_with_no_subscribers_is_dropped(self):
        # No clients subscribed: callback must not blow up or touch the upstream roster
        self.disp._quote_callback("NASDAQ:AAPL", {"lp": 1.0})
        self.assertEqual(self.disp.market_data.symbols, [])


if __name__ == "__main__":
    unittest.main()
