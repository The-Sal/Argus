"""
Offline unit tests for the Hyperliquid `account_update` push: the pure stream parser, the typed records,
the wire budget, the websocket's (re)subscribe + gap behaviour, and dispatcher fan-out.

Payloads in section 3 of docs/perpetuals/hyperliquid/ACCOUNT_UPDATE_PLAN.md are quoted verbatim from the
live websocket. No network is touched.

Run with: env PYTHONPATH=. uv run python -m unittest tests.test_hyper_account_stream
"""
import json
import socket
import unittest
from unittest import mock

from argus import protocol
from argus.perpetuals.hyper import _classes as cls
from argus.perpetuals.hyper.wss import HyperLiquidAccountStream, HyperLiquidAccountWss
from argus.perpetuals.shared import account as acct
from argus.perpetuals.shared import AccountUpdate, BaseDispatcher

ORDER_OPEN = {"order": {"coin": "xyz:SOXL", "side": "B", "limitPx": "161.07", "sz": "95.08", "oid": 562028414333,
                        "timestamp": 1790836086619, "origSz": "95.08", "cloid": "0x000000000000000014e3b3abd2cd9d15"},
              "status": "open", "statusTimestamp": 1790836086619}
ORDER_CANCELED = {"order": {"coin": "xyz:SOXL", "side": "B", "limitPx": "112.7", "sz": "0.1", "oid": 562028156363,
                            "timestamp": 1790836056343, "origSz": "0.1",
                            "cloid": "0x000000000000000014e3b3abd2cd9d15"},
                  "status": "canceled", "statusTimestamp": 1790836086619}
FILL = {"coin": "xyz:XYZ100", "px": "30857.0", "sz": "0.0198", "side": "A", "time": 1790836088664,
        "startPosition": "-68.5688", "dir": "Open Short", "closedPnl": "0.0", "hash": "0x0134385e",
        "oid": 562028105261, "crossed": False, "fee": "-0.001832", "tid": 969238936572089,
        "cloid": "0x000000000000000014e3b3abde689c98", "feeToken": "USDC", "twapId": None}


def frame(channel, data):
    return json.dumps({"channel": channel, "data": data})


def ack(sub_type):
    return frame("subscriptionResponse", {"method": "subscribe", "subscription": {"type": sub_type, "user": "0xabc"}})


class StreamParserTest(unittest.TestCase):
    def setUp(self):
        self.records = []
        self.gaps = []
        self.stream = HyperLiquidAccountStream(self.records.append, self.gaps.append)

    def test_mixed_open_and_canceled_frame_yields_one_record_each_in_order(self):
        self.stream.apply_message(frame("orderUpdates", [ORDER_OPEN, ORDER_CANCELED]))
        self.assertEqual([r.status for r in self.records], ["open", "canceled"])
        self.assertTrue(all(isinstance(r, acct.OrderUpdate) for r in self.records))
        self.assertEqual(self.records[0].order_id, "562028414333")
        self.assertEqual(self.records[0].dex, "xyz")
        self.assertEqual(self.records[0].client_order_id, "0x000000000000000014e3b3abd2cd9d15")

    def test_hip3_fill(self):
        self.stream.apply_message(frame("user", {"fills": [FILL]}))
        (trade,) = self.records
        self.assertIsInstance(trade, acct.Trade)
        self.assertEqual((trade.name, trade.trade_id, trade.is_buy, trade.is_maker),
                         ("xyz:XYZ100", "969238936572089", False, True))

    def test_twap_slice_fills_are_unwrapped(self):
        self.stream.apply_message(frame("user", {"twapSliceFills": [{"fill": FILL, "twapId": 2272276}]}))
        (trade,) = self.records
        self.assertEqual(trade.trade_id, "969238936572089")

    def test_fill_without_cloid(self):
        no_cloid = {k: v for k, v in FILL.items() if k != "cloid"}
        self.stream.apply_message(frame("user", {"fills": [no_cloid]}))
        self.assertEqual(len(self.records), 1)

    def test_unknown_user_event_key_is_ignored_not_fatal(self):
        self.stream.apply_message(frame("user", {"funding": {"coin": "BTC"}, "fills": [FILL]}))
        self.assertEqual(len(self.records), 1)
        self.assertEqual(cls.WsUserEvent.from_dict({"funding": {}, "liquidation": {}}).ignored,
                         ["funding", "liquidation"])

    def test_bad_input_never_raises(self):
        for bad in ("{not json", "[]", json.dumps({"channel": "error", "data": "boom"}),
                    json.dumps({"channel": "somethingElse", "data": 1}),
                    frame("orderUpdates", [{"order": {"coin": "BTC"}}, ORDER_OPEN]),  # first element malformed
                    frame("user", {"fills": [{"coin": "BTC"}]})):
            self.stream.apply_message(bad)
        # Only the well-formed element of the partly-bad orderUpdates frame got through.
        self.assertEqual([r.status for r in self.records], ["open"])

    def test_raising_callback_does_not_stop_the_rest_of_the_frame(self):
        seen = []

        def cb(record):
            seen.append(record)
            raise RuntimeError("boom")

        HyperLiquidAccountStream(cb).apply_message(frame("orderUpdates", [ORDER_OPEN, ORDER_CANCELED]))
        self.assertEqual(len(seen), 2)

    def test_order_update_dex_derivation_and_to_dict(self):
        btc = cls.WsOrderUpdate.from_dict({**ORDER_OPEN, "order": {**ORDER_OPEN["order"], "coin": "BTC", "side": "A"}})
        common = btc.to_common()
        self.assertEqual((common.dex, common.is_buy), ("", False))
        d = common.to_dict()
        self.assertEqual((d["side"], d["price"], d["status"], d["order_id"]), ("sell", "161.07", "open", "562028414333"))
        self.assertEqual(d["venue"]["order"]["oid"], 562028414333)
        self.assertEqual(cls.WsOrderUpdate.from_dict(btc.to_dict()), btc)

    def test_gap_not_fired_on_first_connection(self):
        self.stream.begin_connection()
        self.stream.apply_message(ack("orderUpdates"))
        self.stream.apply_message(ack("userEvents"))
        self.assertEqual(self.gaps, [])

    def test_gap_fires_once_after_both_acks_of_a_reconnect(self):
        self.stream.begin_connection()
        self.stream.apply_message(ack("orderUpdates"))
        self.stream.apply_message(ack("userEvents"))
        self.stream.note_disconnect()
        self.stream.note_disconnect()  # a failed reconnect attempt must not move since_ms forward
        self.stream.begin_connection()
        self.stream.apply_message(ack("orderUpdates"))
        self.assertEqual(self.gaps, [])  # only one of two acks so far
        self.stream.apply_message(ack("userEvents"))
        self.stream.apply_message(ack("userEvents"))
        self.assertEqual(len(self.gaps), 1)
        self.assertGreater(self.gaps[0], 1_700_000_000_000)


class FakeWS:
    def __init__(self):
        self.sent = []

    def send(self, payload):
        self.sent.append(payload)


class AccountWssTest(unittest.TestCase):
    def setUp(self):
        self.records, self.gaps = [], []
        self.wss = HyperLiquidAccountWss("0xabc", self.records.append, self.gaps.append)
        self.fake = FakeWS()
        self.wss._ws = self.fake

    def _subs(self):
        return sorted(json.loads(m)["subscription"]["type"] for m in self.fake.sent
                      if json.loads(m).get("method") == "subscribe")

    def test_framing_is_hyperliquid_json(self):
        self.assertEqual(json.loads(self.wss._ping_frame()), {"method": "ping"})
        self.assertTrue(self.wss._is_pong_frame({"channel": "pong"}))

    def test_open_subscribes_both_channels_for_the_wallet_every_time(self):
        with mock.patch.object(self.wss, "ping"):
            self.wss._on_open_base(None)
            self.assertEqual(self._subs(), ["orderUpdates", "userEvents"])
            self.assertTrue(all(json.loads(m)["subscription"]["user"] == "0xabc"
                                for m in self.fake.sent if "subscribe" in m))
            self.fake.sent.clear()
            self.wss._on_open_base(None)  # reconnect
            self.assertEqual(self._subs(), ["orderUpdates", "userEvents"])

    def test_exactly_one_gap_after_reconnect_and_none_after_first_open(self):
        with mock.patch.object(self.wss, "ping"), mock.patch("argus.perpetuals.shared.wss.time.sleep"), \
                mock.patch.object(self.wss, "_start_ws"), mock.patch("argus.perpetuals.shared.wss.throw_fuss"):
            self.wss._on_open_base(None)
            for t in ("orderUpdates", "userEvents"):
                self.wss._on_message_base(None, ack(t))
            self.assertEqual(self.gaps, [])
            self.wss._on_close_base(None, 1006, "gone")
            self.wss._on_open_base(None)
            for t in ("orderUpdates", "userEvents"):
                self.wss._on_message_base(None, ack(t))
            self.assertEqual(len(self.gaps), 1)

    def test_messages_reach_the_parser(self):
        self.wss._on_message_base(None, frame("orderUpdates", [ORDER_OPEN]))
        self.assertEqual(len(self.records), 1)


class WireBudgetTest(unittest.TestCase):
    def test_each_of_25_fills_fits_protocol_1_separately(self):
        stream_records = []
        stream = HyperLiquidAccountStream(stream_records.append)
        fills = [{**FILL, "tid": FILL["tid"] + i, "hash": "0x" + f"{i:064x}"} for i in range(25)]
        stream.apply_message(frame("user", {"fills": fills}))
        self.assertEqual(len(stream_records), 25)
        for r in stream_records:
            packet = AccountUpdate.fill(r).convert_to_protocol_1()
            self.assertLessEqual(len(packet), 9990 + 100)  # +framing; OutboundMessage itself raises past 9990

    def test_order_and_gap_messages_decode_to_the_documented_shape(self):
        update = cls.WsOrderUpdate.from_dict(ORDER_OPEN).to_common()
        for au, event, key in ((AccountUpdate.order(update), "order", "order"),
                               (AccountUpdate.gap("reconnected", 5), "gap", "reason")):
            (packet,) = protocol.decode_multiple_packets(au.convert_to_protocol_1())
            msg = json.loads(packet)
            self.assertEqual((msg["action"], msg["error"], msg["data"]["event"]), ("account_update", None, event))
            self.assertIn(key, msg["data"])


class _Dispatcher(BaseDispatcher):
    def __init__(self):
        # Skip the real Server (binds a port); BaseDispatcher state is all that fan-out needs.
        with mock.patch("argus.perpetuals.shared.Server"):
            super().__init__(host="localhost", port=0, routing_table={'echo': lambda a: a.args})


class DispatcherFanOutTest(unittest.TestCase):
    def setUp(self):
        self.d = _Dispatcher()

    def _sock(self, broken=False):
        s = mock.Mock(spec=socket.socket)
        if broken:
            s.sendall.side_effect = BrokenPipeError
        return s

    def test_push_reaches_every_known_socket_without_subscribe_and_prunes_dead_ones(self):
        good, dead = self._sock(), self._sock(broken=True)
        for s in (good, dead):
            self.d.add_socket(s)  # what _on_recv does for any client request
        self.d._routine_push_account_update(AccountUpdate.gap("reconnected", 1))
        good.sendall.assert_called_once()
        self.assertEqual(self.d.sockets, [good])

    def test_on_recv_registers_the_client(self):
        client = self._sock()
        packet = protocol.encode_packet(json.dumps({"action": "echo", "data": 1, "correlation_id": "c1"}).encode())
        self.d._on_recv(client, ("127.0.0.1", 1), packet)
        self.assertIn(client, self.d.sockets)


if __name__ == "__main__":
    unittest.main()
