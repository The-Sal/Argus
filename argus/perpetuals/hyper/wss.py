"""
Hyperliquid market-data (order book) websocket streaming.

Structurally this mirrors `argus/polymarket_direct/wss.py` (reconnect/ping-pong/
threading-event skeleton in a base class, a pure socket-agnostic book-state store,
and a dispatcher-facing class that owns the WS connection) -- see that file's
docstrings for the pattern this was modeled on.

Key protocol differences from Polymarket, confirmed against Hyperliquid's docs
(hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket):
  - Ping/pong is JSON, not a literal "PING"/"PONG" string: client sends
    {"method": "ping"} and the server replies {"channel": "pong"}. The server
    closes any connection that hasn't seen a message in 60s, so we ping well
    under that (default 20s).
  - Subscribe/unsubscribe: {"method": "subscribe"|"unsubscribe",
    "subscription": {"type": "l2Book", "coin": "<coin>"}}.
  - l2Book push: {"channel": "l2Book", "data": {"coin": "BTC",
    "levels": [[{"px","sz","n"}, ...bids], [{"px","sz","n"}, ...asks]], "time": ms}}.
    Bids/asks arrive pre-sorted (best first) directly from Hyperliquid -- there is
    no incremental price_change/best_bid_ask delta stream like Polymarket's; every
    l2Book push is a full snapshot for that coin, which makes the store far
    simpler than Polymarket's OrderBookStore (no bisect-insert delta application,
    no tick-size fetching, no best-bid-ask dedup path).
  - No sharding: Hyperliquid's documented per-IP limits are 10 connections but up
    to 1000 subscriptions and 2000 inbound msgs/minute -- the opposite shape of
    Polymarket (which caps out around 4 assets/connection in practice). Since
    Hyperliquid has on the order of a few hundred perpetuals total, one connection
    comfortably holds every coin, so this file does not implement Polymarket's
    shard-pool (PolyMarketOrderBookPool); `HyperLiquidMarketDataWss` below *is*
    the whole thing. If Hyperliquid's limits change, or if a future dispatcher
    wants to subscribe user-specific channels per end-client (which does count
    against the 10-unique-user cap), revisit this.
"""
import os
import json
import time
import logging
import threading
import traceback
from utils3 import runAsThread
from websocket import WebSocketApp
from argus.wireproxy import wrapper as wp_wrappers
from argus._argus_utils import throw_fuss, macos_notification_with_custom_sound


class HyperLiquidWSSBase:
    """
    Base class for Hyperliquid WebSocket connections. Handles common boilerplate:
    reconnection, ping/pong, threading events. Subclasses must provide:
    _create_ws_app(), _on_open_impl(), _on_message_impl().

    This is a near-verbatim port of PolymarketWSSBase (argus/polymarket_direct/wss.py)
    with the ping/pong framing swapped for Hyperliquid's JSON method/channel shape
    instead of Polymarket's bare "PING"/"PONG" strings.
    """

    def __init__(self, name: str, url: str):
        self._name = name
        self._url = url
        self._ws: WebSocketApp = None  # type: ignore

        self._max_reconnect_attempts = int(os.environ.get('HYPERLIQUID_MAX_SOCKET_RETRIES', '50'))
        self._reconnect_attempts = 0
        self._internally_closed = False
        self._allow_ping = True

        self._ping_pong_lock = threading.Lock()
        self._ping_pongs = (0, 0)  # (sent, received)
        self._max_ping_pong_failures = int(os.environ.get('HYPERLIQUID_MAX_PING_PONG_FAILURES', '3'))
        # Hyperliquid closes connections silent for 60s -- ping comfortably under that.
        self._ping_interval_s = float(os.environ.get('HYPERLIQUID_PING_INTERVAL_S', '20'))

        self._pinging_lock = threading.Lock()
        self._last_msg_recv_ts: float = 0.0

        self._reset_threading_events()

    def _reset_threading_events(self):
        self.wait_till_socket_open = threading.Event()
        self.wait_till_first_pong = threading.Event()

    def _init_ws(self):
        with self._ping_pong_lock:
            self._ping_pongs = (0, 0)
        self._create_ws_app()

    def _create_ws_app(self):
        raise NotImplementedError("Subclasses must implement _create_ws_app()")

    def _on_open_base(self, ws):
        _ = ws
        self._reconnect_attempts = 0
        logging.info('%s WebSocket opened.', self._name)
        self._on_open_impl()
        self.ping()
        self.wait_till_socket_open.set()

    def _on_open_impl(self):
        raise NotImplementedError("Subclasses must implement _on_open_impl()")

    def _on_message_base(self, ws, message):
        _ = ws
        # Hyperliquid's pong is a JSON frame, not a bare string, so it has to be
        # decoded before we can recognize it -- unlike Polymarket's literal "PONG".
        try:
            parsed = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            parsed = None

        if isinstance(parsed, dict) and parsed.get('channel') == 'pong':
            logging.debug('%s WebSocket received pong.', self._name)
            with self._ping_pong_lock:
                self._ping_pongs = (self._ping_pongs[0], self._ping_pongs[1] + 1)
            self.wait_till_first_pong.set()
            return

        self._last_msg_recv_ts = time.perf_counter()
        self._on_message_impl(message)

    def _on_message_impl(self, message: str):
        raise NotImplementedError("Subclasses must implement _on_message_impl()")

    def _on_close_base(self, ws, close_status_code, close_msg):
        self._allow_ping = False
        _ = ws
        logging.warning('%s WebSocket closed. Code: %s, Message: %s', self._name, close_status_code, close_msg)
        print(
            f"Attempting to reconnect {self._name} WebSocket... {self._reconnect_attempts + 1}/{self._max_reconnect_attempts}")

        if not self._internally_closed:
            self._on_reconnect_start()
            self._reconnect_attempts += 1
            if self._reconnect_attempts > self._max_reconnect_attempts:
                logging.error('Maximum reconnect attempts reached for %s WebSocket. Giving up.', self._name)
                throw_fuss(
                    msg=f"{self._name.upper()} WEBSOCKET RECONNECTION FAILURE: Maximum reconnect attempts reached.",
                    notify=True
                )
                return
            time.sleep(1)
            self._start_ws()
            self._allow_ping = True

    def _on_reconnect_start(self):
        """Called when reconnection starts. Subclasses can override to restore state."""
        pass

    def _on_error_base(self, ws, error):
        _ = ws
        throw_fuss(
            msg=f"{self._name.upper()} WEBSOCKET ERROR:\n{traceback.format_exc()}",
            notify=False
        )
        macos_notification_with_custom_sound(
            title=f"{self._name.upper()} WEBSOCKET ERROR",
            message=str(error),
            sound_name="Basso"
        )

    @runAsThread
    def ping(self):
        """Send periodic {"method": "ping"} messages and monitor pong responses."""
        if self._pinging_lock.locked():
            logging.warning('Ping thread for %s WebSocket is already running. Not starting another.', self._name)
            return

        with self._pinging_lock:
            while True:
                if self._internally_closed:
                    logging.info('%s ping thread exiting (internally closed).', self._name)
                    return
                try:
                    if self._allow_ping:
                        self._ws.send(json.dumps({"method": "ping"}))
                        with self._ping_pong_lock:
                            self._ping_pongs = (self._ping_pongs[0] + 1, self._ping_pongs[1])
                            pings = self._ping_pongs[0]
                            pongs = self._ping_pongs[1]

                            if os.environ.get('HYPERLIQUID_DISABLE_PING_PONG_LOGS', 'false').lower() != 'true':
                                logging.info(
                                    'Sending ping to %s WebSocket. Total pings: %d, Total pongs: %d',
                                    self._name, pings, pongs
                                )

                            ping_delta = abs(pings - pongs)
                            if ping_delta >= self._max_ping_pong_failures:
                                logging.error(
                                    'Maximum ping-pong failures reached. Reconnecting %s WebSocket...', self._name
                                )
                                throw_fuss(
                                    msg=f"{self._name.upper()} WEBSOCKET PING-PONG FAILURE: No pong received for {ping_delta} pings.",
                                    notify=True
                                )
                                self._ws.close()
                    else:
                        logging.info('Ping to %s WebSocket is currently disabled.', self._name)
                except Exception as e:
                    logging.error("%s WebSocket ping failed: %s", self._name, e)
                    with self._ping_pong_lock:
                        self._ping_pongs = (self._ping_pongs[0] + 1, self._ping_pongs[1])
                time.sleep(self._ping_interval_s)

    def _start_ws_sync(self):
        logging.info('Starting %s WebSocket...', self._name)
        self._init_ws()
        wp_wrappers.start_proxy_aware_ws(
            idx='HYPERLIQUID',
            websocket=self._ws,
        )

    @runAsThread
    def _start_ws(self):
        self._start_ws_sync()


class HyperLiquidOrderBookStore:
    """
    Pure book-state container, keyed by coin symbol (e.g. "BTC", "xyz:AAPL").

    Unlike Polymarket's OrderBookStore, Hyperliquid's l2Book push is always a full
    snapshot (not a price_change delta stream), so there is no incremental-update
    logic, no bisect-insert, no tick-size REST fetch, and no best-bid-ask dedup
    path to port -- this store just replaces the book wholesale on every message
    and fires the callback.
    """

    def __init__(self, order_book_update_callback=None):
        self._coin_to_order_book: dict = {}
        self._order_book_update_callback = order_book_update_callback
        self._dict_lock = threading.Lock()
        self._last_msg_recv_ts: float = 0.0

    def forget(self, coin: str) -> None:
        with self._dict_lock:
            self._coin_to_order_book.pop(coin, None)

    def apply_message(self, message: str) -> None:
        self._last_msg_recv_ts = time.perf_counter()

        try:
            content = json.loads(message)
        except json.JSONDecodeError:
            logging.debug('Non-JSON message from Hyperliquid Order Book WebSocket (ignored): "%s"', message)
            return

        channel = content.get('channel')
        if channel == 'l2Book':
            self._handle_l2_book(content.get('data') or {})
        elif channel == 'subscriptionResponse':
            logging.info('Hyperliquid WebSocket subscription ack: %s', content.get('data'))
        elif channel == 'error':
            logging.warning('Hyperliquid WebSocket error frame: %s', content)
        # Other channels (allMids, trades, ...) aren't subscribed by this store and are ignored.

    def _handle_l2_book(self, data: dict) -> None:
        coin = data.get('coin')
        levels = data.get('levels')
        if not coin or not levels or len(levels) != 2:
            logging.warning('Malformed l2Book payload (missing coin/levels): %s', data)
            return

        raw_bids, raw_asks = levels
        book = {
            'bids': [{'price': lvl['px'], 'size': lvl['sz']} for lvl in raw_bids],
            'asks': [{'price': lvl['px'], 'size': lvl['sz']} for lvl in raw_asks],
        }

        with self._dict_lock:
            self._coin_to_order_book[coin] = book

        if self._order_book_update_callback:
            self._order_book_update_callback({
                coin: book,
                'timestamp': data.get('time', int(time.time() * 1000)),
            })

    def order_book_for_coin(self, coin: str):
        return self._coin_to_order_book.get(coin, None)

    @property
    def order_books(self):
        return self._coin_to_order_book

    @property
    def coins(self):
        return list(self._coin_to_order_book.keys())


class HyperLiquidMarketDataWss(HyperLiquidWSSBase):
    """
    Single-connection order book streamer for Hyperliquid.

    Exposes the same dispatcher-facing surface Polymarket's PolyMarketOrderBookPool
    does (`run`, `subscribe_to_coin`/`unsubscribe_from_coin` in place of
    `subscribe_to_asset_id`/`unsubscribe_from_asset_id`, `order_book_for_coin`,
    `order_books`) so wiring into HyperLiquidDispatcher mirrors PolymarketDispatcher's
    wiring of `self.market_data`, minus the shard/pool indirection -- see this
    module's docstring for why sharding isn't needed here.
    """

    def __init__(self, order_book_update_callback=None):
        super().__init__(name="Hyperliquid Order Book", url='wss://api.hyperliquid.xyz/ws')
        self._store = HyperLiquidOrderBookStore(order_book_update_callback=order_book_update_callback)
        self._roster: set[str] = set()
        self._roster_lock = threading.Lock()
        self._restore_state_timeout = float(
            os.environ.get('HYPERLIQUID_WS_RESTORE_TIMEOUT', '120')
        )

    def _create_ws_app(self):
        self._ws = WebSocketApp(
            url=self._url,
            on_open=self._on_open_base,
            on_close=self._on_close_base,
            on_error=self._on_error_base,
            on_message=self._on_message_base,
        )

    def _on_open_impl(self):
        # Nothing to send on open -- unlike Polymarket, Hyperliquid needs no
        # handshake frame before real subscriptions can be sent.
        pass

    def _on_message_impl(self, message: str):
        self._store.apply_message(message)

    def _on_reconnect_start(self):
        self._reset_threading_events()
        self._defer_restore_state(self.wait_till_first_pong)

    @runAsThread
    def _defer_restore_state(self, pong_event: threading.Event):
        """After the new socket comes back up, replay every coin in our roster.

        Ported from PolyMarketOrderBookConn._defer_restore_state: `pong_event` is
        bound at spawn time (rather than read off `self` inside the thread) and the
        wait is bounded, so a second reconnect landing before the first pong arrives
        can't strand this thread on an Event nothing will ever `.set()` again.
        """
        if not pong_event.wait(timeout=self._restore_state_timeout):
            logging.warning(
                '%s: no pong within %.0fs of reconnect; abandoning subscription restore '
                '(a later reconnect will retry).',
                self._name, self._restore_state_timeout,
            )
            return

        if self._internally_closed:
            logging.info('%s: closed while awaiting pong; skipping restore.', self._name)
            return

        if pong_event is not self.wait_till_first_pong:
            logging.info('%s: superseded by a newer reconnect; leaving restore to it.', self._name)
            return

        with self._roster_lock:
            coins = list(self._roster)
        if coins:
            logging.info('Restoring %s subscriptions: %s', self._name, coins)
            for coin in coins:
                self._send_subscribe_op(coin)
        else:
            logging.info('No coins to restore for %s.', self._name)

    def _send_subscribe_op(self, coin: str) -> None:
        self._ws.send(json.dumps({
            "method": "subscribe",
            "subscription": {"type": "l2Book", "coin": coin},
        }))

    def _send_unsubscribe_op(self, coin: str) -> None:
        self._ws.send(json.dumps({
            "method": "unsubscribe",
            "subscription": {"type": "l2Book", "coin": coin},
        }))

    def run(self, main_thread=False):
        """Bring the connection online. `main_thread` accepted for API parity with
        Polymarket's pool `.run()` but ignored -- always runs in a background thread."""
        _ = main_thread
        self._start_ws()

    def subscribe_to_coin(self, coin: str) -> None:
        with self._roster_lock:
            if coin in self._roster:
                return
            self._roster.add(coin)

        self.wait_till_socket_open.wait()
        self._send_subscribe_op(coin)

    def unsubscribe_from_coin(self, coin: str) -> None:
        with self._roster_lock:
            self._roster.discard(coin)

        try:
            self._send_unsubscribe_op(coin)
        except Exception as e:
            logging.warning('HyperLiquidMarketDataWss: error sending unsubscribe for %s: %s', coin, e)

        self._store.forget(coin)

    def order_book_for_coin(self, coin: str):
        return self._store.order_book_for_coin(coin)

    @property
    def order_books(self):
        return self._store.order_books

    @property
    def coins(self):
        return self._store.coins

    # `_last_msg_recv_ts` (read by the dispatcher for WS-arrival -> sendall latency,
    # the same way it reads `PolyMarketOrderBookPool._last_msg_recv_ts`) needs no
    # forwarding property here the way Polymarket's pool needs one: Polymarket's
    # pool isn't itself a WSSBase and has to reach into its shards' store, whereas
    # this class *is* the WSSBase subclass holding the connection, so the plain
    # attribute `HyperLiquidWSSBase._on_message_base` already sets on `self` is
    # accurate as-is.
