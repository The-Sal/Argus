"""
LIVE end-to-end test for the TradingView dispatcher (argus.tv.dispatcher).

Unlike tests/test_tv_dispatcher.py (fully offline), this connects to the real
TradingView WebSocket and asserts that a P1 subscribe produces live P2 quote
packets with merged state, and that unsubscribe tears the symbol down upstream.

Skipped automatically when data.tradingview.com is unreachable.

Run with: pytest tests/test_tv_dispatcher_live.py
"""
import json
import socket
import time
import unittest
import uuid

from argus.protocol import Protocol2Parser


def _network_available(timeout=5) -> bool:
    try:
        with socket.create_connection(("data.tradingview.com", 443), timeout=timeout):
            return True
    except OSError:
        return False


@unittest.skipUnless(_network_available(), "TradingView unreachable -- skipping live test")
class TradingViewDispatcherLiveTest(unittest.TestCase):
    def setUp(self):
        from argus.tv.dispatcher import TradingViewDispatcher, TVP2ConvertClass
        self.TVP2ConvertClass = TVP2ConvertClass
        self.port = 9974 + (int(time.time()) % 100)  # avoid clashing with a running dispatcher
        self.disp = TradingViewDispatcher(port=self.port)
        self.disp.run()
        time.sleep(0.3)

    def tearDown(self):
        self.disp.market_data.close()

    def _connect(self):
        s = socket.create_connection(("localhost", self.port), timeout=30)
        return s

    def _p1(self, sock, action, data=None):
        req = {"action": action, "data": data, "correlation_id": str(uuid.uuid4())}
        payload = json.dumps(req).encode("utf-8")
        sock.sendall(f"~{len(payload):04d}|".encode("ascii") + payload)
        header = b""
        while not header.endswith(b"|"):
            header += sock.recv(1)
        n = int(header[1:-1])
        body = b""
        while len(body) < n:
            body += sock.recv(n - len(body))
        return json.loads(body)

    def _read_p2(self, sock, timeout=45):
        """Read P2 packets until we have a fully-merged quote (last > 0 and bid > 0)."""
        from argus.tv.dispatcher import TVP2ConvertClass
        parser = Protocol2Parser(TVP2ConvertClass.FIELD_ORDER)
        buf = b""
        deadline = time.time() + timeout
        packets = []
        while time.time() < deadline:
            sock.settimeout(max(0.5, deadline - time.time()))
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            while True:
                i = buf.find(b"~")
                if i == -1:
                    break
                n = int(buf[i + 1:i + 5])
                end = i + 5 + n
                if len(buf) < end:
                    break
                packets.append(parser.parse(buf[i:end]))
                buf = buf[end:]
            merged = packets[-1] if packets else None
            if merged and merged["last"] > 0 and merged["bid"] > 0:
                return packets
        return packets

    def test_subscribe_streams_merged_p2_then_unsubscribe_tears_down(self):
        sock = self._connect()
        resp = self._p1(sock, "subscribe", ["BINANCE:BTCUSDT"])
        self.assertEqual(resp["action"], "response")
        self.assertEqual(resp["data"]["subscribed"], ["BINANCE:BTCUSDT"])
        self.assertIn("BINANCE:BTCUSDT", self.disp.market_data.symbols)

        packets = self._read_p2(sock)
        self.assertGreaterEqual(len(packets), 1, "no P2 data received from TradingView")
        last = packets[-1]
        self.assertEqual(last["symbol"], "BINANCE:BTCUSDT")
        # Merged state: both the main-session (last) and snapshotter (bid/ask) fields populated
        self.assertGreater(last["last"], 0)
        self.assertGreater(last["bid"], 0)
        self.assertGreater(last["ask"], 0)
        self.assertGreaterEqual(last["volume"], 0)

        # Unsubscribe -> symbol dropped from the shared upstream session
        resp = self._p1(sock, "unsubscribe", ["BINANCE:BTCUSDT"])
        self.assertEqual(resp["data"]["unsubscribed"], ["BINANCE:BTCUSDT"])
        self.assertNotIn("BINANCE:BTCUSDT", self.disp.market_data.symbols)

        sock.close()


if __name__ == "__main__":
    unittest.main()
