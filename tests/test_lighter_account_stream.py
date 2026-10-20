"""
Offline unit tests for the Lighter `account_update` push: the pure stream parser, the typed
records, the wire budget, the websocket's (re)subscribe + gap behaviour, and the dispatcher
callbacks.

Payloads follow the verbatim shapes of the live feed (the REST/WS `Order`/`Trade` records share
their field names). No network is touched.

Run with: env PYTHONPATH=. uv run python -m unittest tests.test_lighter_account_stream
"""
import json
import os
import importlib.util
import unittest
from decimal import Decimal
from unittest import mock

from argus import protocol
from argus.perpetuals.lighter import LighterDispatcher
from argus.perpetuals.lighter import _classes as cls
from argus.perpetuals.lighter.wss import LighterAccountStream, LighterAccountWss
from argus.perpetuals.shared import AccountUpdate
from argus.perpetuals.shared import account as acct


# --- fixtures (mirroring tests/test_perpetuals_account.py) -------------------

LIGHTER_ORDER = {
    "order_index": 1, "client_order_index": 234, "order_id": "1", "client_order_id": "234", "market_index": 1,
    "owner_account_index": 6, "initial_base_amount": "0.1", "price": "3024.66", "nonce": 722,
    "remaining_base_amount": "0.05", "is_ask": True, "base_size": 12354, "base_price": 3024,
    "filled_base_amount": "0.05", "filled_quote_amount": "151.23", "side": "sell", "type": "limit",
    "time_in_force": "good-till-time", "reduce_only": True, "trigger_price": "0", "order_expiry": 1640995200,
    "status": "open", "trigger_status": "na", "block_height": 45434, "timestamp": 1640995200,
    "created_at": 1640995200, "updated_at": 1640995200, "transaction_time": 1640995200000000, "order_version": 1,
}

LIGHTER_TRADE = {
    "trade_id": 145, "trade_id_str": "145", "tx_hash": "0xabc", "type": "trade", "market_id": 1, "size": "0.1",
    "price": "3024.66", "usd_amount": "302.466", "ask_id": 145, "ask_id_str": "145", "bid_id": 245, "bid_id_str": "245",
    "ask_client_id_str": "145", "bid_client_id_str": "245", "ask_account_id": 6, "bid_account_id": 3,
    "is_maker_ask": True, "block_height": 45434, "timestamp": 1640995200123, "taker_fee": 0.3, "maker_fee": 0.1,
    "bid_account_pnl": "-0.022890", "ask_account_pnl": "1.989696", "transaction_time": 1771943742851429,
}

#: One record verbatim from the live account_all_trades probe (2026-10-05), which lacks the
#: REST-only `*_account_pnl` fields and adds `market_kind`/`*_order_version`/`*_client_id`.
WS_TRADE = {
    "trade_id": 33315598436, "trade_id_str": "33315598436",
    "tx_hash": "0000001e9bf6e776000001a10b8c8fc2000000000000000000000000000000000000000000000000",
    "type": "trade", "market_id": 0, "market_kind": "perps", "size": "0.0025", "price": "2715.73",
    "usd_amount": "6.789325", "ask_id": 281477954085049, "ask_id_str": "281477954085049",
    "bid_id": 562946868371510, "bid_id_str": "562946868371510", "ask_client_id": 252261215612024,
    "ask_client_id_str": "252261215612024", "bid_client_id": 0, "bid_client_id_str": "0",
    "ask_account_id": 702389, "bid_account_id": 1976, "is_maker_ask": True, "block_height": 348807618,
    "timestamp": 1791195123650, "taker_fee": 100, "taker_position_size_before": "107.1082",
}

ACCOUNT = 6
SYMBOL_BY_MARKET = {1: "ETH", 0: "BTC"}


def order_frame(msg_type, orders, channel="account_all_orders:6"):
    return json.dumps({"type": msg_type, "channel": channel, "orders": orders})


def trade_frame(msg_type, trades, channel="account_all_trades:6"):
    return json.dumps({"type": msg_type, "channel": channel, "trades": trades})


def order(**changes):
    return {**LIGHTER_ORDER, **changes}


def trade(**changes):
    return {**LIGHTER_TRADE, **changes}


class StreamParserTest(unittest.TestCase):
    def setUp(self):
        self.records = []
        self.gaps = []
        self.stream = LighterAccountStream(
            account_index=ACCOUNT,
            symbol_for_market_id=SYMBOL_BY_MARKET.get,
            update_callback=self.records.append,
            gap_callback=self.gaps.append,
        )

    # --- orders --------------------------------------------------------------

    def test_order_snapshot_is_a_silent_baseline(self):
        self.stream.apply_message(order_frame("subscribed/account_all_orders", {"1": [LIGHTER_ORDER]}))
        self.assertEqual(self.records, [])

    def test_order_update_emits_one_order_update_with_resolved_symbol(self):
        self.stream.apply_message(order_frame("update/account_all_orders", {"1": [order(status="canceled")]}))
        (record,) = self.records
        self.assertIsInstance(record, acct.OrderUpdate)
        self.assertEqual((record.name, record.status, record.order_id), ("ETH", "canceled", "1"))

    def test_unchanged_order_in_a_repeated_full_set_update_is_suppressed(self):
        self.stream.apply_message(order_frame("subscribed/account_all_orders", {"1": [LIGHTER_ORDER]}))
        self.stream.apply_message(order_frame("update/account_all_orders", {"1": [LIGHTER_ORDER]}))
        self.stream.apply_message(order_frame("update/account_all_orders", {"1": [LIGHTER_ORDER]}))
        self.assertEqual(self.records, [])

    def test_a_later_size_change_is_emitted(self):
        self.stream.apply_message(order_frame("subscribed/account_all_orders", {"1": [LIGHTER_ORDER]}))
        self.stream.apply_message(order_frame("update/account_all_orders", {"1": [order(remaining_base_amount="0.02")]}))
        self.assertEqual([r.remaining_size for r in self.records], [Decimal("0.02")])

    def test_malformed_order_does_not_swallow_the_rest_of_the_frame(self):
        self.stream.apply_message(order_frame(
            "update/account_all_orders", {"1": [{"garbage": True}, order(status="filled")]}
        ))
        self.assertEqual([r.status for r in self.records], ["filled"])

    def test_unknown_market_id_is_skipped_loudly(self):
        self.stream.apply_message(order_frame("update/account_all_orders", {"999": [LIGHTER_ORDER]}))
        self.assertEqual(self.records, [])

    # --- trades --------------------------------------------------------------

    def test_trade_snapshot_seeds_but_emits_nothing(self):
        self.stream.apply_message(trade_frame("subscribed/account_all_trades", {"1": [LIGHTER_TRADE]}))
        self.assertEqual(self.records, [])

    def test_trade_update_emits_one_per_new_fill_and_dedups_within_and_across_frames(self):
        self.stream.apply_message(trade_frame("subscribed/account_all_trades", {"1": [LIGHTER_TRADE]}))
        self.stream.apply_message(trade_frame("update/account_all_trades", {"1": [LIGHTER_TRADE]}))
        self.assertEqual(self.records, [])  # already seen in the baseline

        fresh = trade(trade_id=146, trade_id_str="146")
        self.stream.apply_message(trade_frame("update/account_all_trades", {"1": [fresh, fresh]}))
        self.stream.apply_message(trade_frame("update/account_all_trades", {"1": [fresh]}))
        self.assertEqual(len(self.records), 1)
        self.assertIsInstance(self.records[0], acct.Trade)
        self.assertEqual(self.records[0].trade_id, "146")

    def test_trade_update_without_a_snapshot_is_emitted(self):
        self.stream.apply_message(trade_frame("update/account_all_trades", {"1": [LIGHTER_TRADE]}))
        self.assertEqual(len(self.records), 1)

    def test_trade_for_another_account_is_skipped(self):
        other = trade(ask_account_id=99, bid_account_id=98)
        self.stream.apply_message(trade_frame("update/account_all_trades", {"1": [other]}))
        self.assertEqual(self.records, [])

    # --- robustness ----------------------------------------------------------

    def test_bad_input_never_raises(self):
        for bad in (
            "{not json",
            "[]",
            json.dumps({"type": "somethingElse", "data": 1}),
            json.dumps({"type": "update/account_all_orders", "orders": "not-a-mapping"}),
            json.dumps({"type": "update/account_all_orders", "orders": {"not-an-int": [LIGHTER_ORDER]}}),
            trade_frame("update/account_all_trades", {"1": [{"trade_id": 1}]}),
        ):
            self.stream.apply_message(bad)
        self.assertEqual(self.records, [])

    def test_error_frame_marks_orders_unavailable_and_a_trades_only_reconnect_still_gaps(self):
        self.assertTrue(self.stream._require_orders)
        self.stream.apply_message(json.dumps({
            "type": "error",
            "error": {"code": 20001, "message": "invalid param : auth field is required: account_all_orders:0"},
        }))
        self.assertFalse(self.stream._require_orders)
        self.stream.note_disconnect()
        self.stream.begin_connection()
        self.stream.apply_message(trade_frame("subscribed/account_all_trades", {}))
        self.assertEqual(len(self.gaps), 1)

    def test_an_auth_shaped_error_also_marks_orders_unavailable(self):
        # Lighter's real auth failures (e.g. "invalid auth: invalid deadline") do not name the channel.
        self.stream.apply_message(json.dumps({"error": {"message": "invalid auth: invalid deadline"}}))
        self.assertFalse(self.stream._require_orders)

    def test_a_benign_already_subscribed_error_does_not_drop_orders(self):
        self.stream.apply_message(json.dumps({
            "type": "error", "error": {"message": "account_all_orders is already subscribed"},
        }))
        self.assertTrue(self.stream._require_orders)

    def test_gap_timeout_backstop_fires_when_orders_never_acks(self):
        stream = LighterAccountStream(ACCOUNT, SYMBOL_BY_MARKET.get, self.records.append, self.gaps.append,
                                      gap_timeout_s=0.05)
        stream.begin_connection()
        stream.apply_message(order_frame("subscribed/account_all_orders", {}))
        stream.apply_message(trade_frame("subscribed/account_all_trades", {}))
        stream.note_disconnect()
        stream.begin_connection()
        stream.apply_message(trade_frame("subscribed/account_all_trades", {}))
        self.assertEqual(self.gaps, [])  # orders ack is still required

        import time
        deadline = time.time() + 1.5
        while not self.gaps and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(self.gaps), 1)

    def test_conversion_failure_does_not_swallow_the_rest_of_a_frame(self):
        records = []
        stream = LighterAccountStream(ACCOUNT, SYMBOL_BY_MARKET.get, records.append)
        original = cls.LighterOrder.to_order_update_common
        calls = {"n": 0}

        def flaky(order_self, symbol):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return original(order_self, symbol)

        with mock.patch.object(cls.LighterOrder, "to_order_update_common", flaky):
            stream.apply_message(order_frame("update/account_all_orders", {"1": [
                order(status="open"), order(order_index=2, order_id="2", status="canceled"),
            ]}))
        self.assertEqual([r.status for r in records], ["canceled"])

    def test_an_unresolved_symbol_is_baselined_so_a_later_update_is_not_new(self):
        state = {"symbol": None}
        records = []
        stream = LighterAccountStream(ACCOUNT, lambda mid: state["symbol"], records.append)
        stream.apply_message(order_frame("subscribed/account_all_orders", {"1": [LIGHTER_ORDER]}))
        state["symbol"] = "ETH"
        stream.apply_message(order_frame("update/account_all_orders", {"1": [LIGHTER_ORDER]}))
        self.assertEqual(records, [])

    def test_raising_callback_does_not_stop_the_rest_of_the_frame(self):
        seen = []

        def callback(record):
            seen.append(record)
            raise RuntimeError("boom")

        stream = LighterAccountStream(ACCOUNT, SYMBOL_BY_MARKET.get, callback)
        stream.apply_message(order_frame("update/account_all_orders", {"1": [
            order(status="open"), order(order_index=2, order_id="2", status="canceled"),
        ]}))
        self.assertEqual(len(seen), 2)

    # --- gap -----------------------------------------------------------------

    def test_gap_not_fired_on_first_connection(self):
        self.stream.begin_connection()
        self.stream.apply_message(order_frame("subscribed/account_all_orders", {}))
        self.stream.apply_message(trade_frame("subscribed/account_all_trades", {}))
        self.assertEqual(self.gaps, [])

    def test_gap_fires_once_after_both_acks_of_a_reconnect(self):
        self.stream.begin_connection()
        self.stream.apply_message(order_frame("subscribed/account_all_orders", {}))
        self.stream.apply_message(trade_frame("subscribed/account_all_trades", {}))
        self.stream.note_disconnect()
        self.stream.note_disconnect()  # a failed reconnect attempt must not move since_ms forward
        self.stream.begin_connection()
        self.stream.apply_message(order_frame("subscribed/account_all_orders", {}))
        self.assertEqual(self.gaps, [])  # only one of two acks so far
        self.stream.apply_message(trade_frame("subscribed/account_all_trades", {}))
        self.stream.apply_message(trade_frame("subscribed/account_all_trades", {}))
        self.assertEqual(len(self.gaps), 1)
        self.assertGreater(self.gaps[0], 1_700_000_000_000)


class OrderUpdateConverterTest(unittest.TestCase):
    def test_to_order_update_common(self):
        order = cls.LighterOrder.from_dict(LIGHTER_ORDER)
        common = order.to_order_update_common("ETH")
        self.assertEqual(common.order_id, "1")
        self.assertEqual(common.client_order_id, "234")
        self.assertFalse(common.is_buy)
        self.assertEqual(common.price, Decimal("3024.66"))
        self.assertEqual(common.original_size, Decimal("0.1"))
        self.assertEqual(common.remaining_size, Decimal("0.05"))
        self.assertEqual(common.status, "open")
        self.assertEqual(common.dex, "")
        self.assertEqual(common.status_timestamp_ms, 1640995200000)
        self.assertEqual(common.timestamp_ms, 1640995200000)
        self.assertIs(common.venue, order)
        d = common.to_dict()
        self.assertEqual((d["side"], d["status"], d["dex"]), ("sell", "open", ""))

    def test_status_timestamp_falls_back_to_timestamp(self):
        raw = {k: v for k, v in LIGHTER_ORDER.items() if k != "updated_at"}
        raw["timestamp"] = 1_700_000_000
        common = cls.LighterOrder.from_dict(raw).to_order_update_common("ETH")
        self.assertEqual(common.status_timestamp_ms, 1_700_000_000_000)

    def test_empty_client_order_id_is_none(self):
        common = cls.LighterOrder.from_dict({**LIGHTER_ORDER, "client_order_id": ""}).to_order_update_common("ETH")
        self.assertIsNone(common.client_order_id)

    def test_a_real_ws_trade_record_is_parsed_and_converted(self):
        # The live account_all_trades shape (no *_account_pnl, extra market_kind/order_version fields).
        common = cls.LighterTrade.from_dict(WS_TRADE).to_common(1976, "BTC")
        self.assertEqual(common.trade_id, "33315598436")
        self.assertTrue(common.is_buy)  # account 1976 was the bid
        self.assertFalse(common.is_maker)  # is_maker_ask=true -> the bid was the taker
        self.assertEqual(common.price, Decimal("2715.73"))
        self.assertEqual(common.size, Decimal("0.0025"))
        self.assertEqual(common.fee, Decimal("100"))


class FakeWS:
    def __init__(self):
        self.sent = []

    def send(self, payload):
        self.sent.append(payload)


class AccountWssTest(unittest.TestCase):
    def setUp(self):
        self.records, self.gaps = [], []
        self.token = "1700000000:6:0:abcdef"
        self.wss = LighterAccountWss(
            account_index=ACCOUNT,
            auth_token_provider=lambda: self.token,
            symbol_for_market_id=SYMBOL_BY_MARKET.get,
            update_callback=self.records.append,
            gap_callback=self.gaps.append,
            token_deadline_s=3600,
        )
        self.fake = FakeWS()
        self.wss._ws = self.fake

    def tearDown(self):
        self.wss._cancel_token_refresh()

    def _sent(self):
        return [json.loads(m) for m in self.fake.sent]

    def test_framing_is_lighter_json(self):
        self.assertEqual(json.loads(self.wss._ping_frame()), {"type": "ping"})
        self.assertTrue(self.wss._is_pong_frame({"type": "pong"}))

    def test_open_subscribes_orders_with_auth_and_trades_without_every_time(self):
        with mock.patch.object(self.wss, "ping"):
            self.wss._on_open_base(None)
            orders = next(s for s in self._sent() if "account_all_orders" in s["channel"])
            self.assertEqual(orders["channel"], "account_all_orders/6")
            self.assertEqual(orders["auth"], self.token)
            trades = next(s for s in self._sent() if "account_all_trades" in s["channel"])
            self.assertEqual(trades["channel"], "account_all_trades/6")
            self.assertNotIn("auth", trades)

            self.fake.sent.clear()
            self.wss._on_open_base(None)  # reconnect
            self.assertEqual([s["channel"] for s in self._sent()],
                             ["account_all_orders/6", "account_all_trades/6"])

    def test_open_without_a_token_provider_is_trades_only(self):
        wss = LighterAccountWss(ACCOUNT, None, SYMBOL_BY_MARKET.get, self.records.append)
        fake = FakeWS()
        wss._ws = fake
        try:
            with mock.patch.object(wss, "ping"):
                wss._on_open_base(None)
            channels = [json.loads(m)["channel"] for m in fake.sent]
            self.assertEqual(channels, ["account_all_trades/6"])
            self.assertFalse(wss._stream._require_orders)
        finally:
            wss._cancel_token_refresh()

    def test_refresh_resends_the_orders_subscribe_with_a_fresh_token(self):
        tokens = iter(["old-token", "new-token"])
        self.wss._auth_token_provider = lambda: next(tokens)
        with mock.patch.object(self.wss, "ping"):
            self.wss._on_open_base(None)
        self.fake.sent.clear()
        self.wss._on_token_timer(self.wss._token_generation)
        (frame,) = self._sent()
        self.assertIn("account_all_orders", frame["channel"])
        self.assertEqual(frame["auth"], "new-token")

    def test_a_failed_refresh_rearms_a_retry_timer(self):
        with mock.patch.object(self.wss, "ping"):
            self.wss._on_open_base(None)
        with mock.patch.object(self.wss._ws, "send", side_effect=RuntimeError("no socket")):
            self.wss._on_token_timer(self.wss._token_generation)
        self.assertIsNotNone(self.wss._token_timer)

    def test_close_cancels_the_token_refresh_timer(self):
        with mock.patch.object(self.wss, "ping"):
            self.wss._on_open_base(None)
        self.assertIsNotNone(self.wss._token_timer)
        with mock.patch("argus.perpetuals.shared.wss.time.sleep"), \
                mock.patch.object(self.wss, "_start_ws"), \
                mock.patch("argus.perpetuals.shared.wss.throw_fuss"):
            self.wss._on_close_base(None, 1006, "gone")
        self.assertIsNone(self.wss._token_timer)

    def test_exactly_one_gap_after_reconnect_and_none_after_first_open(self):
        with mock.patch.object(self.wss, "ping"), mock.patch("argus.perpetuals.shared.wss.time.sleep"), \
                mock.patch.object(self.wss, "_start_ws"), mock.patch("argus.perpetuals.shared.wss.throw_fuss"):
            self.wss._on_open_base(None)
            for t in ("account_all_orders", "account_all_trades"):
                self.wss._on_message_base(None, json.dumps({"type": "subscribed/" + t, "channel": f"{t}:6"}))
            self.assertEqual(self.gaps, [])

            self.wss._on_close_base(None, 1006, "gone")
            self.wss._on_open_base(None)
            for t in ("account_all_orders", "account_all_trades"):
                self.wss._on_message_base(None, json.dumps({"type": "subscribed/" + t, "channel": f"{t}:6"}))
            self.assertEqual(len(self.gaps), 1)

    def test_messages_reach_the_parser(self):
        self.wss._on_message_base(None, order_frame("update/account_all_orders", {"1": [LIGHTER_ORDER]}))
        self.assertEqual(len(self.records), 1)


class WireBudgetTest(unittest.TestCase):
    def test_each_of_25_fills_fits_protocol_1_separately(self):
        records = []
        stream = LighterAccountStream(ACCOUNT, SYMBOL_BY_MARKET.get, records.append)
        fills = [trade(trade_id=145 + i, trade_id_str=str(145 + i), tx_hash="0x" + f"{i:064x}") for i in range(25)]
        stream.apply_message(trade_frame("update/account_all_trades", {"1": fills}))
        self.assertEqual(len(records), 25)
        for record in records:
            packet = AccountUpdate.fill(record).convert_to_protocol_1()
            self.assertLessEqual(len(packet), 9990 + 100)  # + framing; OutboundMessage itself raises past 9990

    def test_order_and_gap_messages_decode_to_the_documented_shape(self):
        update = cls.LighterOrder.from_dict(LIGHTER_ORDER).to_order_update_common("ETH")
        for account_update, event, key in ((AccountUpdate.order(update), "order", "order"),
                                          (AccountUpdate.gap("reconnected", 5), "gap", "reason")):
            (packet,) = protocol.decode_multiple_packets(account_update.convert_to_protocol_1())
            msg = json.loads(packet)
            self.assertEqual((msg["action"], msg["error"], msg["data"]["event"]),
                             ("account_update", None, event))
            self.assertIn(key, msg["data"])


class _CallbackHost:
    def __init__(self):
        self.pushes = []

    def _routine_push_account_update(self, update):
        self.pushes.append(update)


class DispatcherCallbackTest(unittest.TestCase):
    """The dispatcher's account callbacks are pure record -> `AccountUpdate` mappers; the shared
    `_routine_push_account_update` fan-out itself is covered by tests/test_hyper_account_stream.py."""

    def test_order_and_fill_and_gap_are_wrapped(self):
        host = _CallbackHost()
        order_update = cls.LighterOrder.from_dict(LIGHTER_ORDER).to_order_update_common("ETH")
        LighterDispatcher._account_update_callback(host, order_update)
        self.assertEqual((host.pushes[-1].event, host.pushes[-1].payload["order"]["order_id"]), ("order", "1"))

        common_trade = cls.LighterTrade.from_dict(LIGHTER_TRADE).to_common(ACCOUNT, "ETH")
        LighterDispatcher._account_update_callback(host, common_trade)
        self.assertEqual((host.pushes[-1].event, host.pushes[-1].payload["trade"]["trade_id"]), ("fill", "145"))

        LighterDispatcher._account_gap_callback(host, 123)
        self.assertEqual((host.pushes[-1].event, host.pushes[-1].payload["since_ms"]), ("gap", 123))

    def test_an_unsupported_record_is_swallowed_not_raised(self):
        host = _CallbackHost()
        LighterDispatcher._account_update_callback(host, object())
        self.assertEqual(host.pushes, [])


class DispatcherFailureIsolationTest(unittest.TestCase):
    def test_a_failing_account_stream_leaves_market_data_running(self):
        index = cls.PerpetualsIndex([])
        with mock.patch("argus.perpetuals.shared.Server"), \
                mock.patch("argus.perpetuals.lighter.LighterRest") as MockRest, \
                mock.patch("argus.perpetuals.lighter.LighterExchange"), \
                mock.patch("argus.perpetuals.lighter.pi.throw_fuss") as throw_fuss, \
                mock.patch("argus.perpetuals.lighter.wss.LighterMarketDataWss") as MockMarketData, \
                mock.patch("argus.perpetuals.lighter.wss.LighterAccountWss",
                           side_effect=RuntimeError("boom")):
            rest = MockRest.return_value
            rest.account_index = ACCOUNT
            rest.auth_token = None
            rest.get_all_perpetuals.return_value = index

            dispatcher = LighterDispatcher(port=0)

            self.assertIsNone(dispatcher.account_updates)
            MockMarketData.return_value.run.assert_called_once_with(main_thread=False)
            throw_fuss.assert_called_once()


class _FakeCLISocket:
    """Feeds pre-built P1 frames to `LighterArgusClient`, capturing the request's correlation id."""

    def __init__(self, frames):
        self._frames = list(frames)
        self._timeout = None
        self.request = None

    def sendall(self, data):
        self.request = json.loads(protocol.decode_packet(data))

    def recv(self, _nbytes):
        if not self._frames:
            raise AssertionError("no more frames queued")
        frame = self._frames.pop(0)
        if callable(frame):
            frame = frame(self.request["correlation_id"])
        return frame

    def settimeout(self, timeout):
        self._timeout = timeout

    def gettimeout(self):
        return self._timeout


class LighterCliPushSkippingTest(unittest.TestCase):
    """tests/lighter_cli.py must not let an unsolicited account_update push satisfy a request.

    Regression for the Phase 4 failure where the CLI read the `account_update` frame that interleaves
    ahead of a reply and returned it as the response (its correlation_id is null but the old code only
    *warned* on a mismatch, and never checked `action`).
    """

    @staticmethod
    def _client_class():
        path = os.path.join(os.path.dirname(__file__), "lighter_cli.py")
        spec = importlib.util.spec_from_file_location("lighter_cli_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.LighterArgusClient

    def test_send_request_skips_a_push_and_returns_the_matching_reply(self):
        LighterArgusClient = self._client_class()

        push = AccountUpdate.gap("reconnected", 5).convert_to_protocol_1()

        def reply(correlation_id):
            return protocol.encode_packet(json.dumps({
                "action": "response", "data": {"ok": True}, "error": None,
                "compressed": False, "correlation_id": correlation_id,
            }).encode())

        client = LighterArgusClient()
        client.socket = _FakeCLISocket([push, reply])
        response, _ = client.send_request("products_version")
        self.assertEqual(response["data"], {"ok": True})
        self.assertEqual([p["action"] for p in client.pushes], ["account_update"])

    def test_send_request_returns_an_early_error_with_no_correlation_id(self):
        LighterArgusClient = self._client_class()

        early_error = protocol.encode_packet(json.dumps({
            "action": "error", "data": None, "error": "Unable to decode message",
            "compressed": False, "correlation_id": None,
        }).encode())
        client = LighterArgusClient()
        client.socket = _FakeCLISocket([early_error])
        response, _ = client.send_request("products_version")
        self.assertEqual(response["error"], "Unable to decode message")
        self.assertEqual(client.pushes, [])


if __name__ == "__main__":
    unittest.main()