"""
Shared WebSocket skeleton for perpetuals venue market-data streams.

`VenueWSSBase` / `MarketDataWssBase` were extracted from the near-verbatim
`HyperLiquidWSSBase` / `HyperLiquidMarketDataWss` (argus/perpetuals/hyper/wss.py)
and `LighterWSSBase` / `LighterMarketDataWss` (argus/perpetuals/lighter/wss.py)
copies, themselves both ported from `PolymarketWSSBase`
(argus/polymarket_direct/wss.py). The reconnect/ping-pong/threading-event
skeleton and the market-data roster/restore-on-reconnect machinery were
identical apart from four small, mechanical seams, which are now the only
things a venue subclass has to supply:

  - `env_prefix` / `proxy_idx` (constructor args) -- env var namespacing
    (`HYPERLIQUID_*`, `LIGHTER_*`) and the WireProxy dispatcher index.
  - `_ping_frame()` -- the outbound keepalive frame the venue expects.
  - `_is_pong_frame(parsed)` -- how to recognize the venue's pong reply.
  - `_handle_server_ping(parsed)` -- optional; venues whose servers also send
    pings (Lighter) override this to reply, returning True to consume the frame.

`MarketDataWssBase` adds the subscription roster and replay-after-reconnect
scaffolding; venue subclasses supply `_send_subscribe_op(key)` /
`_send_unsubscribe_op(key)` and their own `order_book_*` accessors.

Timeouts and env-var behavior are preserved exactly as they were in the two
originals. The venue-specific book stores remain in their own modules -- they
are semantically different (Hyperliquid snapshots vs Lighter snapshot+delta)
and deliberately not unified here.
"""
import os
import json
import time
import logging
import threading
import traceback
from typing import Any
from utils3 import runAsThread
from websocket import WebSocketApp
from argus.wireproxy import wrapper as wp_wrappers
from argus._argus_utils import macos_notification_with_custom_sound, throw_fuss




class VenueWSSBase:
    """
    Base class for a venue WebSocket connection. Handles common boilerplate:
    reconnection, ping/pong keepalive, and threading events. Subclasses must
    provide `_create_ws_app()`, `_on_open_impl()`, `_on_message_impl()` and the
    framing hooks `_ping_frame()` / `_is_pong_frame()`.
    """

    def __init__(self, name: str, url: str, *,
                 env_prefix: str,
                 proxy_idx: str,
                 default_ping_interval_s: float):
        self._name = name
        self._url = url
        self._env_prefix = env_prefix
        self._proxy_idx = proxy_idx
        self._ws: WebSocketApp = None  # type: ignore

        self._max_reconnect_attempts = int(os.environ.get(f'{env_prefix}_MAX_SOCKET_RETRIES', '50'))
        self._reconnect_attempts = 0
        self._internally_closed = False
        self._allow_ping = True

        self._ping_pong_lock = threading.Lock()
        self._ping_pongs = (0, 0)  # (sent, received)
        self._max_ping_pong_failures = int(os.environ.get(f'{env_prefix}_MAX_PING_PONG_FAILURES', '3'))
        self._ping_interval_s = float(
            os.environ.get(f'{env_prefix}_PING_INTERVAL_S', str(default_ping_interval_s))
        )

        self._pinging_lock = threading.Lock()
        self._last_msg_recv_ts: float = 0.0

        self._reset_threading_events()

    ########################################
    # Framing hooks (venue-specific)
    ########################################

    def _ping_frame(self) -> str:
        """The exact outbound keepalive frame for this venue."""
        raise NotImplementedError("Subclasses must implement _ping_frame()")

    def _is_pong_frame(self, parsed) -> bool:
        """True if `parsed` (a decoded JSON dict) is this venue's pong reply."""
        raise NotImplementedError("Subclasses must implement _is_pong_frame()")

    def _handle_server_ping(self, parsed) -> bool:
        """
        Handle a server-initiated ping, if the venue sends them. Return True to
        consume the frame (it is not a market-data message); default is False.
        """
        _ = parsed
        return False

    ########################################
    # Connection lifecycle
    ########################################

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
        # Venue pongs are JSON frames, so they have to be decoded before we can
        # recognize them -- unlike Polymarket's literal "PONG".
        try:
            parsed = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            parsed = None

        if isinstance(parsed, dict):
            if self._is_pong_frame(parsed):
                logging.debug('%s WebSocket received pong.', self._name)
                with self._ping_pong_lock:
                    self._ping_pongs = (self._ping_pongs[0], self._ping_pongs[1] + 1)
                self.wait_till_first_pong.set()
                return

            if self._handle_server_ping(parsed):
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
        """Send periodic keepalive frames and monitor pong responses."""
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
                        self._ws.send(self._ping_frame())
                        with self._ping_pong_lock:
                            self._ping_pongs = (self._ping_pongs[0] + 1, self._ping_pongs[1])
                            pings = self._ping_pongs[0]
                            pongs = self._ping_pongs[1]

                            if os.environ.get(f'{self._env_prefix}_DISABLE_PING_PONG_LOGS', 'false').lower() != 'true':
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
            idx=self._proxy_idx,
            websocket=self._ws,
        )

    @runAsThread
    def _start_ws(self):
        self._start_ws_sync()


class MarketDataWssBase(VenueWSSBase):
    """
    Shared scaffolding for a single-connection market-data streamer: owns the
    subscription roster and replays it after a reconnect (bounded wait on the
    first pong, so a second reconnect landing before the first pong arrives
    can't strand the restore thread on an Event nothing will ever `.set()`).

    Subclasses must set `self._store` (after `super().__init__`) and implement
    `_send_subscribe_op(key)` / `_send_unsubscribe_op(key)`. Public accessor
    names (`subscribe_to_coin` vs `subscribe_to_market`, ...) stay in the
    subclasses and delegate to the generic `_subscribe_to_key` /
    `_unsubscribe_from_key` here.
    """

    _store: Any = None  # set by subclasses after super().__init__

    def __init__(self, name: str, url: str, *,
                 env_prefix: str,
                 proxy_idx: str,
                 default_ping_interval_s: float):
        super().__init__(
            name=name,
            url=url,
            env_prefix=env_prefix,
            proxy_idx=proxy_idx,
            default_ping_interval_s=default_ping_interval_s,
        )
        self._restore_state_timeout = float(
            os.environ.get(f'{env_prefix}_WS_RESTORE_TIMEOUT', '120')
        )
        self._roster: set = set()
        self._roster_lock = threading.Lock()

    ########################################
    # Subscription ops (venue-specific)
    ########################################

    def _send_subscribe_op(self, key) -> None:
        raise NotImplementedError("Subclasses must implement _send_subscribe_op()")

    def _send_unsubscribe_op(self, key) -> None:
        raise NotImplementedError("Subclasses must implement _send_unsubscribe_op()")

    ########################################
    # Connection lifecycle
    ########################################

    def _create_ws_app(self):
        self._ws = WebSocketApp(
            url=self._url,
            on_open=self._on_open_base,
            on_close=self._on_close_base,
            on_error=self._on_error_base,
            on_message=self._on_message_base,
        )

    def _on_open_impl(self):
        # Nothing to send on open -- no handshake frame is needed before real
        # subscriptions can be sent for the venues this was extracted from.
        pass

    def _on_message_impl(self, message: str):
        self._store.apply_message(message)

    def _on_reconnect_start(self):
        self._reset_threading_events()
        self._defer_restore_state(self.wait_till_first_pong)

    @runAsThread
    def _defer_restore_state(self, pong_event: threading.Event):
        """After the new socket comes back up, replay every key in our roster."""
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
            keys = list(self._roster)
        if keys:
            logging.info('Restoring %s subscriptions: %s', self._name, keys)
            for key in keys:
                self._send_subscribe_op(key)
        else:
            logging.info('No subscriptions to restore for %s.', self._name)

    ########################################
    # Public subscription surface
    ########################################

    def run(self, main_thread=False):
        """Bring the connection online. `main_thread` is accepted for API parity
        with Polymarket's pool `.run()` but ignored -- always runs in a background
        thread."""
        _ = main_thread
        self._start_ws()

    def _subscribe_to_key(self, key) -> None:
        with self._roster_lock:
            if key in self._roster:
                return
            self._roster.add(key)

        self.wait_till_socket_open.wait()
        self._send_subscribe_op(key)

    def _unsubscribe_from_key(self, key) -> None:
        with self._roster_lock:
            self._roster.discard(key)

        try:
            self._send_unsubscribe_op(key)
        except Exception as e:
            logging.warning('%s: error sending unsubscribe for %s: %s', self._name, key, e)

        self._store.forget(key)

    @property
    def order_books(self):
        return self._store.order_books
