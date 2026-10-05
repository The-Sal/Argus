"""
Offline unit tests for the shared perpetuals websocket skeleton
(`argus.perpetuals.shared.wss`), exercised through the concrete Hyperliquid and
Lighter subclasses.

These cover the parts of the extracted base that used to be duplicated and are
easy to regress silently:

  - ping/pong framing per venue (Hyperliquid `method`/`channel`, Lighter
    bidirectional `type`), including Lighter's required reply to server pings
  - the `_last_msg_recv_ts` latency timestamp only advancing on non-keepalive
    frames
  - reconnect: `_on_close_base` -> `_on_reconnect_start` -> roster replay, the
    bounded/`superseded` restore guards, give-up after max attempts, and the
    no-reconnect-when-internally-closed path

No network is touched: the underlying `_ws` is replaced with a fake, and
`_start_ws`/`time.sleep`/`throw_fuss` are stubbed.

Run with: pytest tests/test_perpetuals_wss_shared.py
"""
import json
import os
import threading
import time
import unittest
from unittest import mock

import argus.perpetuals.shared.wss as shared_wss
from argus.perpetuals.hyper.wss import HyperLiquidMarketDataWss
from argus.perpetuals.lighter.wss import LighterMarketDataWss


class FakeWS:
    """Minimal stand-in for WebSocketApp: records sends, records close()."""

    def __init__(self):
        self.sent = []
        self.closed = False

    def send(self, payload):
        self.sent.append(payload)

    def close(self):
        self.closed = True


class _WssTestBase(unittest.TestCase):
    make_wss = None  # set by subclasses
    key = None
    subscribe = None
    unsubscribe = None
    ping_frame = None

    def setUp(self):
        self.wss = self.make_wss()  # type: ignore[misc]  # set by concrete subclasses
        self.fake = FakeWS()
        self.wss._ws = self.fake
        # Pretend the socket is already open so subscribe() doesn't block.
        self.wss.wait_till_socket_open.set()

    def _store_calls(self):
        calls = []
        self.wss._store.apply_message = lambda msg: calls.append(msg)
        return calls


class HyperFramingTest(_WssTestBase):
    make_wss = staticmethod(lambda: HyperLiquidMarketDataWss())
    key = "BTC"
    subscribe = lambda self, k: self.wss.subscribe_to_coin(k)
    unsubscribe = lambda self, k: self.wss.unsubscribe_from_coin(k)

    def test_ping_frame_is_json_method_ping(self):
        self.assertEqual(json.loads(self.wss._ping_frame()), {"method": "ping"})

    def test_pong_channel_recognized(self):
        calls = self._store_calls()
        self.wss._on_message_base(None, json.dumps({"channel": "pong"}))
        self.assertEqual(self.wss._ping_pongs, (0, 1))
        self.assertTrue(self.wss.wait_till_first_pong.is_set())
        self.assertEqual(calls, [])
        # Pongs must NOT move the latency timestamp.
        self.assertEqual(self.wss._last_msg_recv_ts, 0.0)

    def test_market_message_forwarded_and_timestamps(self):
        calls = self._store_calls()
        self.wss._on_message_base(None, json.dumps({"channel": "l2Book", "data": {}}))
        self.assertEqual(len(calls), 1)
        self.assertGreater(self.wss._last_msg_recv_ts, 0.0)

    def test_server_ping_is_not_consumed(self):
        # Hyperliquid servers don't send client pings; such a frame is data.
        calls = self._store_calls()
        self.wss._on_message_base(None, json.dumps({"type": "ping"}))
        self.assertEqual(len(calls), 1)


class LighterFramingTest(_WssTestBase):
    make_wss = staticmethod(lambda: LighterMarketDataWss())
    key = 0
    subscribe = lambda self, k: self.wss.subscribe_to_market(k)
    unsubscribe = lambda self, k: self.wss.unsubscribe_from_market(k)

    def test_ping_frame_is_json_type_ping(self):
        self.assertEqual(json.loads(self.wss._ping_frame()), {"type": "ping"})

    def test_pong_type_recognized(self):
        calls = self._store_calls()
        self.wss._on_message_base(None, json.dumps({"type": "pong"}))
        self.assertEqual(self.wss._ping_pongs, (0, 1))
        self.assertTrue(self.wss.wait_till_first_pong.is_set())
        self.assertEqual(calls, [])
        self.assertEqual(self.wss._last_msg_recv_ts, 0.0)

    def test_server_ping_replies_pong_and_is_consumed(self):
        calls = self._store_calls()
        self.wss._on_message_base(None, json.dumps({"type": "ping"}))
        self.assertEqual([json.loads(p) for p in self.fake.sent], [{"type": "pong"}])
        self.assertEqual(calls, [])
        self.assertEqual(self.wss._last_msg_recv_ts, 0.0)

    def test_market_message_forwarded(self):
        calls = self._store_calls()
        self.wss._on_message_base(None, json.dumps({"type": "update/order_book", "channel": "order_book:0"}))
        self.assertEqual(len(calls), 1)
        self.assertGreater(self.wss._last_msg_recv_ts, 0.0)


class EnvConfigTest(unittest.TestCase):
    def test_env_overrides_are_honored_with_venue_prefix(self):
        with mock.patch.dict(os.environ, {
            "HYPERLIQUID_PING_INTERVAL_S": "7",
            "HYPERLIQUID_MAX_SOCKET_RETRIES": "9",
            "LIGHTER_PING_INTERVAL_S": "11",
            "LIGHTER_MAX_SOCKET_RETRIES": "13",
        }):
            hyper = HyperLiquidMarketDataWss()
            lighter = LighterMarketDataWss()
        self.assertEqual(hyper._ping_interval_s, 7.0)
        self.assertEqual(hyper._max_reconnect_attempts, 9)
        self.assertEqual(lighter._ping_interval_s, 11.0)
        self.assertEqual(lighter._max_reconnect_attempts, 13)

    def test_proxy_idx_and_env_prefix(self):
        hyper = HyperLiquidMarketDataWss()
        lighter = LighterMarketDataWss()
        self.assertEqual((hyper._env_prefix, hyper._proxy_idx), ("HYPERLIQUID", "HYPERLIQUID"))
        self.assertEqual((lighter._env_prefix, lighter._proxy_idx), ("LIGHTER", "LIGHTER"))


class PingLoopTest(_WssTestBase):
    make_wss = staticmethod(lambda: HyperLiquidMarketDataWss())

    def test_ping_loop_sends_and_closes_after_max_failures(self):
        self.wss._ping_interval_s = 0.01
        self.wss._max_ping_pong_failures = 2
        with mock.patch.object(shared_wss, "throw_fuss", lambda **_: None):
            thread = self.wss.ping()
            deadline = time.time() + 2
            while not self.fake.closed and time.time() < deadline:
                time.sleep(0.01)
            self.wss._internally_closed = True
            thread.join(timeout=2)

        self.assertTrue(self.fake.closed, "ping loop should close the socket after max ping-pong failures")
        self.assertGreaterEqual(len(self.fake.sent), 2)
        self.assertTrue(all(json.loads(p) == {"method": "ping"} for p in self.fake.sent))

    def test_ping_thread_exits_when_internally_closed(self):
        self.wss._ping_interval_s = 0.01
        self.wss._internally_closed = True
        thread = self.wss.ping()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.fake.sent, [])


class ReconnectTest(_WssTestBase):
    make_wss = staticmethod(lambda: HyperLiquidMarketDataWss())
    key = "BTC"
    subscribe = lambda self, k: self.wss.subscribe_to_coin(k)
    unsubscribe = lambda self, k: self.wss.unsubscribe_from_coin(k)

    def _patch_close_side_effects(self, stack):
        stack.enter_context(mock.patch.object(shared_wss.time, "sleep", lambda *_: None))
        stack.enter_context(mock.patch.object(shared_wss, "throw_fuss", lambda **_: None))
        stack.enter_context(mock.patch.object(shared_wss, "macos_notification_with_custom_sound", lambda **_: None))

    def _wait_until(self, predicate, timeout=2.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return predicate()

    def test_close_replays_roster_on_reconnect(self):
        self.subscribe(self.key)
        sent_before = len(self.fake.sent)
        starts = []
        self.wss._start_ws = lambda: starts.append(1)

        import contextlib
        with contextlib.ExitStack() as stack:
            self._patch_close_side_effects(stack)
            self.wss._on_close_base(None, 1000, "test")

            # _on_reconnect_start reset the events; the restore thread is now
            # waiting on the *new* pong event.
            self.wss.wait_till_first_pong.set()
            self.assertTrue(self._wait_until(lambda: len(self.fake.sent) > sent_before))

        self.assertEqual(starts, [1], "reconnect should call _start_ws once")
        self.assertEqual(self.wss._roster, {self.key})
        replayed = [json.loads(p) for p in self.fake.sent[sent_before:]]
        self.assertTrue(any(f.get("method") == "subscribe" or f.get("type") == "subscribe" for f in replayed))

    def test_close_when_internally_closed_does_not_reconnect(self):
        starts = []
        self.wss._start_ws = lambda: starts.append(1)
        self.wss._internally_closed = True
        with mock.patch.object(shared_wss.time, "sleep", lambda *_: None):
            self.wss._on_close_base(None, 1000, "test")
        self.assertEqual(starts, [])
        self.assertEqual(self.wss._reconnect_attempts, 0)

    def test_gives_up_after_max_attempts(self):
        starts = []
        fusses = []
        self.wss._start_ws = lambda: starts.append(1)
        self.wss._max_reconnect_attempts = 0
        with mock.patch.object(shared_wss.time, "sleep", lambda *_: None), \
                mock.patch.object(shared_wss, "throw_fuss", lambda **kwargs: fusses.append(kwargs)):
            self.wss._on_close_base(None, 1000, "test")
        self.assertEqual(starts, [], "should not reconnect after giving up")
        self.assertEqual(len(fusses), 1)
        self.assertTrue(fusses[0]["notify"])

    def test_defer_restore_superseded_by_newer_reconnect(self):
        self.wss._roster.add(self.key)
        stale = threading.Event()
        stale.set()
        self.wss._defer_restore_state(stale)
        time.sleep(0.1)
        self.assertEqual(self.fake.sent, [], "superseded restore must not replay")

    def test_defer_restore_times_out_without_replaying(self):
        self.wss._roster.add(self.key)
        self.wss._restore_state_timeout = 0.05
        never = threading.Event()
        self.wss._defer_restore_state(never)
        time.sleep(0.2)
        self.assertEqual(self.fake.sent, [], "timed-out restore must not replay")

    def test_defer_restore_skips_when_internally_closed(self):
        self.wss._roster.add(self.key)
        self.wss._internally_closed = True
        event = threading.Event()
        event.set()
        self.wss._defer_restore_state(event)
        time.sleep(0.1)
        self.assertEqual(self.fake.sent, [])

    def test_unsubscribe_removes_from_roster_and_store(self):
        self.subscribe(self.key)
        self.assertIn(self.key, self.wss._roster)
        self.wss._store.forget = mock.Mock()
        self.unsubscribe(self.key)
        self.assertNotIn(self.key, self.wss._roster)
        self.wss._store.forget.assert_called_once_with(self.key)
        self.assertTrue(any(json.loads(p).get("method") == "unsubscribe" for p in self.fake.sent))


class LighterReconnectTest(ReconnectTest):
    make_wss = staticmethod(lambda: LighterMarketDataWss())
    key = 0
    subscribe = lambda self, k: self.wss.subscribe_to_market(k)
    unsubscribe = lambda self, k: self.wss.unsubscribe_from_market(k)

    def test_unsubscribe_removes_from_roster_and_store(self):
        self.subscribe(self.key)
        self.wss._store.forget = mock.Mock()
        self.unsubscribe(self.key)
        self.assertNotIn(self.key, self.wss._roster)
        self.wss._store.forget.assert_called_once_with(self.key)
        self.assertTrue(any(json.loads(p).get("type") == "unsubscribe" for p in self.fake.sent))


if __name__ == "__main__":
    unittest.main()
