"""
Offline regression tests for the TradingView wire protocol (argus.tv).

These lock in the fixes for the upstream drift found in tv_news.har:

- TradingView bundles several ~m~-framed messages into one websocket frame; the old
  decoder only returned the first, dropping most ``qsd`` updates.
- Heartbeats bundled behind a quote update were never echoed, which made upstream stop
  streaming on a long-lived socket.
- ``quote_set_fields`` omitted bid/ask, so the dispatcher's P2 depth columns were zero.
- ``setup_qs`` set fields on the snapshotter using the *main* session id.

Run with: pytest tests/test_tv_protocol.py
"""
import json
import unittest

from argus.tv import ChartSession, QuoteSession, TradingViewConnection, QUOTE_FIELDS


def frame(method, params=None, body=None):
    """Build one ~m~<len>~m~<body> websocket frame component."""
    if body is None:
        body = json.dumps({"m": method, "p": params})
    return f"~m~{len(body)}~m~{body}"


def heartbeat_frame(hb_id):
    body = f"~h~{hb_id}"
    return f"~m~{len(body)}~m~{body}"


def qsd_frame(symbol, values, session="qs_test"):
    return frame("qsd", [session, {"n": symbol, "s": "ok", "v": values}])


class FakeWs:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)


def decode_sent(raw):
    """Decode a wrapped outbound frame back into (method, params)."""
    decoded = TradingViewConnection.decode_message(raw, multiple=True)[0]
    return decoded["m"], decoded["p"]


class DecodeMessageTest(unittest.TestCase):
    def test_bundled_messages_all_decoded(self):
        raw = qsd_frame("NASDAQ:AAPL", {"lp": 1}) + qsd_frame("BINANCE:BTCUSDT", {"lp": 2})
        decoded = TradingViewConnection.decode_message(raw, multiple=True)
        self.assertEqual([m["m"] for m in decoded], ["qsd", "qsd"])
        self.assertEqual(decoded[0]["p"][1]["n"], "NASDAQ:AAPL")
        self.assertEqual(decoded[1]["p"][1]["n"], "BINANCE:BTCUSDT")

    def test_multiple_false_returns_first_message(self):
        raw = qsd_frame("NASDAQ:AAPL", {"lp": 1}) + qsd_frame("BINANCE:BTCUSDT", {"lp": 2})
        self.assertEqual(TradingViewConnection.decode_message(raw)["p"][1]["n"], "NASDAQ:AAPL")

    def test_bundled_heartbeat_is_decoded_in_place(self):
        raw = qsd_frame("NASDAQ:AAPL", {"lp": 1}) + heartbeat_frame(7) + qsd_frame("NASDAQ:AAPL", {"lp": 3})
        decoded = TradingViewConnection.decode_message(raw, multiple=True)
        self.assertEqual([m["m"] for m in decoded], ["qsd", "heartbeat", "qsd"])

    def test_heartbeat_raw_body_is_preserved(self):
        decoded = TradingViewConnection.decode_message(heartbeat_frame(42), multiple=True)
        self.assertEqual(decoded, [{"m": "heartbeat", "raw": "~h~42"}])

    def test_body_containing_tilde_h_is_not_a_heartbeat(self):
        values = {"description": "~h~ not a heartbeat", "lp": 1.5}
        decoded = TradingViewConnection.decode_message(qsd_frame("X:Y", values), multiple=True)
        self.assertEqual(decoded[0]["m"], "qsd")
        self.assertEqual(decoded[0]["p"][1]["v"]["description"], "~h~ not a heartbeat")

    def test_embedded_delimiter_does_not_break_framing(self):
        # A JSON body that itself contains the ~m~ delimiter must not desync the parser.
        values = {"description": "a ~m~ b", "lp": 1.5}
        decoded = TradingViewConnection.decode_message(qsd_frame("X:Y", values), multiple=True)
        self.assertEqual(len(decoded), 1)
        self.assertEqual(decoded[0]["p"][1]["v"]["description"], "a ~m~ b")

    def test_bytes_input_is_decoded(self):
        raw = qsd_frame("NASDAQ:AAPL", {"lp": 1}).encode("utf-8")
        decoded = TradingViewConnection.decode_message(raw, multiple=True)
        self.assertEqual(decoded[0]["p"][1]["n"], "NASDAQ:AAPL")

    def test_empty_frame_yields_nothing(self):
        self.assertEqual(TradingViewConnection.decode_message("", multiple=True), [])
        self.assertIsNone(TradingViewConnection.decode_message(""))

    def test_non_ascii_body_uses_character_length(self):
        # The server's frame size counts characters (~UTF-16 units), not UTF-8 bytes, so a
        # body with CJK text must not desync the component that follows it.
        values = {"description": "\u4e0a\u8bc1\u7efc\u5408\u6307\u6570", "lp": 1.0}
        raw = qsd_frame("SSE:000001", values) + heartbeat_frame(3)
        decoded = TradingViewConnection.decode_message(raw, multiple=True)
        self.assertEqual([m["m"] for m in decoded], ["qsd", "heartbeat"])
        self.assertEqual(decoded[0]["p"][1]["v"]["description"], "\u4e0a\u8bc1\u7efc\u5408\u6307\u6570")

    def test_invalid_frame_size_terminates(self):
        # A negative or non-numeric size used to make the loop spin forever.
        for bad in ("~m~-100~m~junk", "~m~abc~m~junk", "~m~~m~~m~"):
            self.assertEqual(TradingViewConnection.decode_message(bad, multiple=True), [])


class QuoteSessionBundlingTest(unittest.TestCase):
    def _session(self, callback):
        session = QuoteSession(symbol="NASDAQ:AAPL", callback=callback)
        session.ws = FakeWs()
        session.msgs_read = 1  # skip the first-message handshake
        return session

    def test_on_message_processes_every_bundled_qsd(self):
        seen = []
        session = self._session(seen.append)

        raw = (qsd_frame("NASDAQ:AAPL", {"lp": 100, "bid": 99, "ask": 101})
               + qsd_frame("NASDAQ:AAPL", {"lp": 101, "bid": 100, "ask": 102}))
        session.on_message(None, raw)

        self.assertEqual([m.last_price for m in seen], [100, 101])
        self.assertEqual([m.bid_price for m in seen], [99, 100])

    def test_on_message_echoes_heartbeat_behind_quote_data(self):
        session = self._session(lambda md: None)
        session.on_message(None, qsd_frame("NASDAQ:AAPL", {"lp": 1}) + heartbeat_frame(42))
        self.assertEqual(session.ws.sent, ["~m~5~m~~h~42"])

    def test_on_message_echoes_each_heartbeat_once(self):
        session = self._session(lambda md: None)
        session.on_message(None, heartbeat_frame(1) + heartbeat_frame(2))
        self.assertEqual(session.ws.sent, ["~m~4~m~~h~1", "~m~4~m~~h~2"])

    def test_bad_quote_does_not_block_bundle_or_heartbeat(self):
        calls = []

        def callback(md):
            calls.append(md.last_price)
            if len(calls) == 1:
                raise RuntimeError("boom")

        session = self._session(callback)
        raw = (qsd_frame("NASDAQ:AAPL", {"lp": 1})
               + heartbeat_frame(7)
               + qsd_frame("NASDAQ:AAPL", {"lp": 2}))
        session.on_message(None, raw)

        # First quote raised, second still processed, heartbeat still echoed.
        self.assertEqual(calls, [1, 2])
        self.assertEqual(session.ws.sent, ["~m~4~m~~h~7"])


class ChartSessionHeartbeatTest(unittest.TestCase):
    def test_bad_message_does_not_lose_heartbeat_or_later_messages(self):
        session = ChartSession()
        session.ws = FakeWs()
        session.messages = 1  # skip the first-message handshake
        raw = (frame("symbol_resolved", ["cs_x", "sds_sym_1", {}])  # raises: no 'short_description'
               + heartbeat_frame(9)
               + frame("timescale_update", ["cs_x", {}, {"a": 1}]))
        session.on_message(None, raw)  # must not raise
        # heartbeat echoed before the loop; the later timescale_update still processed
        self.assertEqual(session.ws.sent, ["~m~4~m~~h~9"])
        self.assertEqual(len(session.message_history), 1)


class QuoteFieldsTest(unittest.TestCase):
    def test_depth_fields_requested(self):
        for field in ("bid", "bid_size", "ask", "ask_size"):
            self.assertIn(field, QUOTE_FIELDS)

    def test_setup_qs_targets_snapshotter_with_fields(self):
        session = QuoteSession(symbol="NASDAQ:AAPL")
        session.ws = FakeWs()
        session.setup_qs()
        pairs = [decode_sent(raw) for raw in session.ws.sent]

        main_set_fields = ("quote_set_fields", [session.quote_session_id, *QUOTE_FIELDS])
        snap_set_fields = ("quote_set_fields", [session.quote_snapshotter, *QUOTE_FIELDS])
        self.assertIn(main_set_fields, pairs)
        self.assertIn(snap_set_fields, pairs)
        # snapshotter must be created AND configured on its own id
        self.assertIn(("quote_create_session", [session.quote_snapshotter]), pairs)


if __name__ == "__main__":
    unittest.main()
