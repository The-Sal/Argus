"""
Lighter market-data (order book) websocket streaming.

Structural skeleton (reconnect/ping-pong/threading-event base class, a pure
socket-agnostic book-state store, and a dispatcher-facing class that owns the
WS connection) is a near-verbatim port of `argus/perpetuals/hyper/wss.py`. The
store logic, however, is NOT ported from Hyperliquid's -- see
`docs/perf/lighter-market-data-parity-plan.md` (the design doc this module
implements) for the full reasoning. Short version:

  - Hyperliquid's `l2Book` push is always a full snapshot -> its store just
    replaces the book wholesale on every message.
  - Lighter's `order_book/{market_id}` channel is snapshot+incremental-delta
    (first push is a full snapshot, every push after that is a delta: price-
    keyed upsert, or removal when size=="0"). This is structurally the same
    shape as Polymarket's `price_change` delta stream, so
    `LighterOrderBookStore._handle_order_book_delta` ports
    `argus/polymarket_direct/wss.py`'s `OrderBookStore._update_order_book`
    (bisect-insert to keep levels sorted), not Hyperliquid's snapshot-replace.

Two channels matter for order-book parity, keyed by integer `market_id` (NOT
the symbol string -- see LighterDispatcher for the symbol<->market_id
translation, done once at the dispatcher boundary):

  - `order_book/{market_id}` -- depth baseline, delta-maintained as above.
    Server-side batched at ~50ms (per docs) -> ~20 updates/s out of the box.
  - `ticker/{market_id}` -- BBO fast path (best ask `a` / best bid `b`, each
    `{price, size}`). This is Lighter's analog of Hyperliquid's `bbo` and
    Polymarket's `best_bid_ask`: it is overlaid onto the book's top level
    (Hyperliquid-shaped overlay) AND fired immediately with a dedup check
    against the order_book delta path (Polymarket-shaped short-circuit) --
    see `LighterOrderBookStore._handle_ticker` / `_matches_ticker_cache`.

Exact wire shape was VERIFIED LIVE against `wss://mainnet.zklighter.elliot.ai/stream`
while building this module (see `tests/lighter_wss_latency.py`, which is the repeatable
version of that check) -- and differs from the design doc's Section 3 assumptions (based
on docs.lighter.xyz / the reference lighter-python SDK) in three ways this module
codes around explicitly:
  - The channel field on inbound frames uses a COLON, e.g. `"order_book:0"`, not the
    slash used when sending the subscribe/unsubscribe request itself (both are accepted
    on the request side; only the colon form has been observed on responses) --
    `_market_id_from_channel` accepts either.
  - The first order_book/ticker frame after subscribing arrives with type
    `"subscribed/order_book"` / `"subscribed/ticker"` (not `"update/order_book"`) and
    IS the full payload (snapshot for order_book), not a bare ack -- dispatch is by
    exact `type`, not by "do we already have local state" guessing.
  - The payload's own `last_updated_at` field is in MICROSECONDS; the message's
    top-level `timestamp` field is in milliseconds (matching Hyperliquid's/Polymarket's
    convention) -- callbacks use the top-level field. Using the nested one silently
    produced 1000x-inflated "latency" numbers during testing.

Reconnect-gap handling is new machinery neither Hyperliquid's nor Polymarket's
store needs: because `order_book` is snapshot+delta (not snapshot-only), a
dropped/out-of-order delta would silently desync the local book forever with
no self-correction. Lighter's payload carries `offset`/`nonce`/`begin_nonce`
specifically so a consumer can detect this -- `_handle_order_book_delta`
verifies each delta's `begin_nonce` chains from the last recorded `nonce`, and
on mismatch drops local state and forces an unsubscribe+resubscribe of that
market's `order_book` channel (via the `resync_callback` wired in by
`LighterMarketDataWss`) to force a fresh snapshot, rather than applying an
out-of-order delta.

Ping/pong direction is reversed from Hyperliquid (and from Polymarket), and is
handled in BOTH directions here per the design doc:
  - Lighter's docs say clients must send a frame at least every 2 minutes; we
    send our own `{"type": "ping"}` on a timer (default well under that -- see
    LIGHTER_PING_INTERVAL_S) and track `{"type": "pong"}` replies exactly like
    HyperLiquidWSSBase.ping() does.
  - The reference SDK's actual behavior is that the SERVER also sends
    `{"type": "ping"}`, and the client must reply `{"type": "pong"}`
    immediately -- this is what actually keeps the server-side connection
    alive. `_on_message_base` answers this unconditionally, independent of our
    own ping timer/lock state, since it is a protocol requirement rather than
    a liveness probe we control the cadence of.
"""
import os
import json
import time
import bisect
import logging
import threading
import traceback
from utils3 import runAsThread
from websocket import WebSocketApp
from argus.wireproxy import wrapper as wp_wrappers
from argus._argus_utils import throw_fuss, macos_notification_with_custom_sound


class LighterWSSBase:
    """
    Base class for Lighter WebSocket connections. Handles common boilerplate:
    reconnection, ping/pong (both directions -- see module docstring), threading
    events. Subclasses must provide: _create_ws_app(), _on_open_impl(),
    _on_message_impl().

    Near-verbatim port of HyperLiquidWSSBase (argus/perpetuals/hyper/wss.py),
    itself ported from PolymarketWSSBase, with the ping/pong framing swapped
    for Lighter's bidirectional JSON {"type": "ping"|"pong"} shape.
    """

    def __init__(self, name: str, url: str):
        self._name = name
        self._url = url
        self._ws: WebSocketApp = None  # type: ignore

        self._max_reconnect_attempts = int(os.environ.get('LIGHTER_MAX_SOCKET_RETRIES', '50'))
        self._reconnect_attempts = 0
        self._internally_closed = False
        self._allow_ping = True

        self._ping_pong_lock = threading.Lock()
        self._ping_pongs = (0, 0)  # (sent, received)
        self._max_ping_pong_failures = int(os.environ.get('LIGHTER_MAX_PING_PONG_FAILURES', '3'))
        # Lighter's docs require a client frame at least every 2 minutes; ping
        # comfortably under that. Wider margin than Hyperliquid's 20s default
        # since Lighter's idle-close window (2min) is double Hyperliquid's (60s).
        self._ping_interval_s = float(os.environ.get('LIGHTER_PING_INTERVAL_S', '60'))

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
        try:
            parsed = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            parsed = None

        if isinstance(parsed, dict):
            msg_type = parsed.get('type')

            if msg_type == 'pong':
                logging.debug('%s WebSocket received pong.', self._name)
                with self._ping_pong_lock:
                    self._ping_pongs = (self._ping_pongs[0], self._ping_pongs[1] + 1)
                self.wait_till_first_pong.set()
                return

            if msg_type == 'ping':
                # Server-initiated keepalive -- required response, independent of our
                # own ping timer/lock. See module docstring: this is the direction
                # that actually keeps the connection alive per the reference SDK.
                logging.debug('%s WebSocket received server ping; replying pong.', self._name)
                try:
                    self._ws.send(json.dumps({"type": "pong"}))
                except Exception as e:
                    logging.warning('%s: failed to reply to server ping: %s', self._name, e)
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
        """Send periodic {"type": "ping"} messages and monitor pong responses."""
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
                        self._ws.send(json.dumps({"type": "ping"}))
                        with self._ping_pong_lock:
                            self._ping_pongs = (self._ping_pongs[0] + 1, self._ping_pongs[1])
                            pings = self._ping_pongs[0]
                            pongs = self._ping_pongs[1]

                            if os.environ.get('LIGHTER_DISABLE_PING_PONG_LOGS', 'false').lower() != 'true':
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
            idx='LIGHTER',
            websocket=self._ws,
        )

    @runAsThread
    def _start_ws(self):
        self._start_ws_sync()


class LighterOrderBookStore:
    """
    Pure book-state container, keyed by integer `market_id`.

    Unlike HyperLiquidOrderBookStore (snapshot-replace only), Lighter's
    `order_book` channel is snapshot+delta, so this store carries real delta-
    application logic (bisect-insert, price-keyed upsert/remove) ported from
    `argus.polymarket_direct.wss.OrderBookStore._update_order_book`, plus
    nonce-chain gap detection that neither Hyperliquid's nor Polymarket's store
    needs (see module docstring). The `ticker` channel is then overlaid onto
    the book's top level (Hyperliquid-shaped overlay) with a dedup short-
    circuit against the order_book delta path (Polymarket-shaped), so a
    top-of-book change already announced by `ticker` doesn't fire a second,
    redundant callback when the corresponding `order_book` delta lands.
    """

    def __init__(self, order_book_update_callback=None, resync_callback=None):
        self._market_id_to_order_book: dict = {}
        self._market_id_to_nonce: dict = {}  # market_id -> {'offset':..., 'nonce':...}
        self._market_id_to_ticker_cache: dict = {}  # market_id -> {'best_bid':..., 'best_ask':...}
        self._order_book_update_callback = order_book_update_callback
        # Called (from inside apply_message, without _dict_lock held) with a market_id
        # whose order_book delta failed the nonce-chain check. Wired by
        # LighterMarketDataWss to unsubscribe+resubscribe that market's order_book
        # channel and force a fresh snapshot -- see module docstring.
        self._resync_callback = resync_callback
        self._dict_lock = threading.Lock()
        self._last_msg_recv_ts: float = 0.0

    def forget(self, market_id: int) -> None:
        with self._dict_lock:
            self._market_id_to_order_book.pop(market_id, None)
            self._market_id_to_nonce.pop(market_id, None)
            self._market_id_to_ticker_cache.pop(market_id, None)

    def apply_message(self, message: str) -> None:
        self._last_msg_recv_ts = time.perf_counter()

        try:
            content = json.loads(message)
        except json.JSONDecodeError:
            logging.debug('Non-JSON message from Lighter Order Book WebSocket (ignored): "%s"', message)
            return

        if not isinstance(content, dict):
            return

        msg_type = content.get('type')
        # Confirmed live against wss://mainnet.zklighter.elliot.ai/stream (see
        # tests/lighter_wss_latency.py and docs/perf/lighter-market-data-parity-plan.md
        # Section 8): the FIRST order_book/ticker frame after subscribing arrives as
        # "subscribed/order_book" / "subscribed/ticker" and already carries the full
        # payload (snapshot for order_book) -- it is not a bare ack. Only frames after
        # that use "update/order_book" / "update/ticker". Both are dispatched by exact
        # type (not by "do we already have state" guessing) so a post-resync snapshot
        # is always handled as a snapshot regardless of stale local state.
        if msg_type in ('subscribed/order_book', 'update/order_book'):
            self._handle_order_book_update(content, is_snapshot=(msg_type == 'subscribed/order_book'))
        elif msg_type in ('subscribed/ticker', 'update/ticker'):
            self._handle_ticker(content)
        elif msg_type == 'connected':
            logging.info('Lighter WebSocket session established: %s', content.get('session_id'))
        elif msg_type == 'error':
            logging.warning('Lighter WebSocket error frame: %s', content)
        elif msg_type == 'unsubscribed' or (isinstance(msg_type, str) and msg_type.startswith('unsubscribed')):
            logging.info('Lighter WebSocket unsubscribe ack: %s', content)
        else:
            logging.debug('Unhandled Lighter WebSocket message type %r: %s', msg_type, content)
        # Other channels (trade, market_stats, candle, ...) aren't subscribed by
        # this store and are ignored -- see design doc Section 3 on scope discipline.

    ##############################################
    # order_book: snapshot + delta
    ##############################################

    @staticmethod
    def _market_id_from_channel(channel) -> "int | None":
        """Channel is e.g. "order_book:0" on inbound frames (colon -- confirmed live),
        though the subscribe/unsubscribe request itself uses a slash (both accepted by
        the server; see _send_subscribe_op). Handle either separator defensively."""
        if not isinstance(channel, str):
            return None
        for sep in (':', '/'):
            if sep in channel:
                try:
                    return int(channel.rsplit(sep, 1)[1])
                except ValueError:
                    return None
        return None

    def _handle_order_book_update(self, content: dict, is_snapshot: bool) -> None:
        market_id = self._market_id_from_channel(content.get('channel'))
        if market_id is None:
            logging.warning('Lighter order_book frame missing/unparseable channel: %s', content)
            return
        payload = content.get('order_book') if isinstance(content.get('order_book'), dict) else content
        # Top-level "timestamp" is milliseconds since epoch (matches Hyperliquid's/
        # Polymarket's convention); the nested payload's "last_updated_at" is
        # MICROSECONDS -- confirmed live -- so it must not be used as the callback
        # timestamp or downstream latency math (ms-based, e.g. tests/lighter_wss_latency.py)
        # would be off by 1000x.
        msg_timestamp = content.get('timestamp')

        if is_snapshot:
            self._handle_order_book_snapshot(market_id, payload, msg_timestamp)
        else:
            self._handle_order_book_delta(market_id, payload, msg_timestamp)

    def _handle_order_book_snapshot(self, market_id: int, payload: dict, msg_timestamp=None) -> None:
        """Full replace, sorted (bids desc, asks asc). Used on first sight of a market,
        and again after a forced resubscribe (Section 3/4 of the design doc)."""
        raw_bids = payload.get('bids') or []
        raw_asks = payload.get('asks') or []
        bid_sorted = sorted(raw_bids, key=lambda lvl: float(lvl['price']), reverse=True)
        ask_sorted = sorted(raw_asks, key=lambda lvl: float(lvl['price']))

        with self._dict_lock:
            self._market_id_to_order_book[market_id] = {'bids': bid_sorted, 'asks': ask_sorted}
            self._market_id_to_nonce[market_id] = {
                'offset': payload.get('offset'),
                'nonce': payload.get('nonce'),
            }

        self._fire_callback(market_id, msg_timestamp)

    def _handle_order_book_delta(self, market_id: int, payload: dict, msg_timestamp=None) -> None:
        begin_nonce = payload.get('begin_nonce')
        with self._dict_lock:
            nonce_state = self._market_id_to_nonce.get(market_id)
        last_nonce = nonce_state.get('nonce') if nonce_state else None

        if begin_nonce is not None and last_nonce is not None and begin_nonce != last_nonce:
            logging.warning(
                'Lighter order book desync for market_id %s: delta begin_nonce=%s does not chain from '
                'last nonce=%s -- dropping local book and forcing resubscribe.',
                market_id, begin_nonce, last_nonce,
            )
            self.forget(market_id)
            if self._resync_callback:
                try:
                    self._resync_callback(market_id)
                except Exception as e:
                    logging.warning('Lighter order book resync callback failed for market_id %s: %s', market_id, e)
            return

        bid_changes = payload.get('bids') or []
        ask_changes = payload.get('asks') or []
        # Check top-of-book-touch BEFORE applying the delta, against the pre-update
        # best level -- same ordering Polymarket's _is_top_of_book_change relies on.
        is_top_change = (
            self._is_top_of_book_change(market_id, bid_changes, is_bid=True)
            or self._is_top_of_book_change(market_id, ask_changes, is_bid=False)
        )

        with self._dict_lock:
            book = self._market_id_to_order_book.get(market_id)
            if book is None:
                # Raced with a forget()/resubscribe (e.g. a concurrent desync on this
                # market); drop the now-orphaned delta rather than resurrect a book.
                return
            for level in bid_changes:
                self._apply_level(book['bids'], level, is_bid=True)
            for level in ask_changes:
                self._apply_level(book['asks'], level, is_bid=False)
            self._market_id_to_nonce[market_id] = {
                'offset': payload.get('offset'),
                'nonce': payload.get('nonce'),
            }

        # After update: if this delta touched the top of book and the resulting best
        # bid/ask now matches what `ticker` already broadcast, suppress the callback --
        # same dedup as Polymarket's price_change/best_bid_ask short-circuit.
        if is_top_change and self._matches_ticker_cache(market_id):
            return

        self._fire_callback(market_id, msg_timestamp)

    @staticmethod
    def _apply_level(levels: list, level: dict, is_bid: bool) -> None:
        """Price-keyed upsert (or removal when size=="0"), bisect-inserting new price
        levels to keep sort order. Caller must hold `_dict_lock`.

        NOTE: this deliberately does NOT copy `OrderBookStore._update_order_book`'s
        insertion arithmetic verbatim (argus/polymarket_direct/wss.py) for the bid
        side. That code calls `bisect.bisect_left` directly on `levels`'s prices even
        though bids are stored descending, then "corrects" with `len(levels) - insert_idx`
        -- but `bisect` assumes an ASCENDING sequence, so bisecting a descending one
        does not produce a meaningful index (confirmed live while building this store:
        it silently corrupted bid ordering, caught by tests/lighter_wss_latency.py's
        book-invariant check). Bisecting the NEGATED prices (which is ascending when
        the source is descending) gives the correct insertion index directly, with no
        reflection needed."""
        price = level.get('price')
        size = level.get('size')
        if price is None or size is None:
            logging.warning('Malformed Lighter order_book level (missing price/size): %s', level)
            return

        idx = next((i for i, lvl in enumerate(levels) if lvl['price'] == price), None)

        if float(size) == 0:
            if idx is not None:
                levels.pop(idx)
            return

        if idx is not None:
            levels[idx]['size'] = size
            return

        new_level = {'price': price, 'size': size}
        price_float = float(price)
        if is_bid:
            insert_idx = bisect.bisect_left([-float(lvl['price']) for lvl in levels], -price_float)
        else:
            insert_idx = bisect.bisect_left([float(lvl['price']) for lvl in levels], price_float)
        levels.insert(insert_idx, new_level)

    def _is_top_of_book_change(self, market_id: int, changed_levels: list, is_bid: bool) -> bool:
        """True if any of `changed_levels` touches the current best level on this side.
        Called BEFORE the delta is applied, so this compares against the pre-update
        top of book -- the only case a `ticker` push could already cover."""
        if not changed_levels:
            return False
        with self._dict_lock:
            book = self._market_id_to_order_book.get(market_id)
            if not book:
                return False
            side = book['bids'] if is_bid else book['asks']
            if not side:
                return False
            best_price = side[0]['price']
        touched_prices = {lvl.get('price') for lvl in changed_levels}
        return best_price in touched_prices

    ##############################################
    # ticker: BBO fast path
    ##############################################

    def _handle_ticker(self, content: dict) -> None:
        market_id = self._market_id_from_channel(content.get('channel'))
        if market_id is None:
            logging.warning('Lighter ticker frame missing/unparseable channel: %s', content)
            return
        payload = content.get('ticker') if isinstance(content.get('ticker'), dict) else content
        # See _handle_order_book_update: top-level "timestamp" is ms, payload's
        # "last_updated_at" is microseconds -- use the top-level field for the callback.
        msg_timestamp = content.get('timestamp')

        best_ask = payload.get('a')
        best_bid = payload.get('b')

        with self._dict_lock:
            self._market_id_to_ticker_cache[market_id] = {
                'best_bid': best_bid.get('price') if isinstance(best_bid, dict) else None,
                'best_ask': best_ask.get('price') if isinstance(best_ask, dict) else None,
            }
            book = self._market_id_to_order_book.get(market_id)
            if book is None:
                book = {'bids': [], 'asks': []}
                self._market_id_to_order_book[market_id] = book
            book['bids'] = self._overlay_side(book.get('bids', []), best_bid, is_bid=True)
            book['asks'] = self._overlay_side(book.get('asks', []), best_ask, is_bid=False)

        # Fast path: fire immediately, same as HyperLiquidOrderBookStore._handle_bbo
        # and Polymarket's best_bid_ask handler returning early after firing.
        self._fire_callback(market_id, msg_timestamp)

    @staticmethod
    def _overlay_side(levels: list, best, is_bid: bool) -> list:
        if not isinstance(best, dict):
            return levels

        best_px = best.get('price')
        best_sz = best.get('size')
        if best_px is None or best_sz is None:
            logging.warning('Malformed Lighter ticker level (missing price/size): %s', best)
            return levels

        best_px_float = float(best_px)
        if is_bid:
            kept = [lvl for lvl in levels if float(lvl['price']) < best_px_float]
        else:
            kept = [lvl for lvl in levels if float(lvl['price']) > best_px_float]
        return [{'price': best_px, 'size': best_sz}] + kept

    def _matches_ticker_cache(self, market_id: int) -> bool:
        """True if the book's current best bid/ask matches the last `ticker` cache --
        i.e. `ticker` already broadcast a callback reflecting this exact top-of-book
        state, so the order_book-delta callback can be safely suppressed."""
        with self._dict_lock:
            cached = self._market_id_to_ticker_cache.get(market_id)
            if not cached:
                return False
            book = self._market_id_to_order_book.get(market_id)
            if not book:
                return False
            bids = book.get('bids') or []
            asks = book.get('asks') or []
            current_best_bid = bids[0]['price'] if bids else None
            current_best_ask = asks[0]['price'] if asks else None
            return current_best_bid == cached['best_bid'] and current_best_ask == cached['best_ask']

    ##############################################
    # Shared
    ##############################################

    def _fire_callback(self, market_id: int, timestamp) -> None:
        if not self._order_book_update_callback:
            return
        book = self._market_id_to_order_book.get(market_id)
        if book is None:
            return
        self._order_book_update_callback({
            market_id: book,
            'timestamp': timestamp if timestamp is not None else int(time.time() * 1000),
        })

    def order_book_for_market(self, market_id: int):
        return self._market_id_to_order_book.get(market_id, None)

    @property
    def order_books(self):
        return self._market_id_to_order_book

    @property
    def market_ids(self):
        return list(self._market_id_to_order_book.keys())


class LighterMarketDataWss(LighterWSSBase):
    """
    Single-connection order book streamer for Lighter.

    Exposes the dispatcher-facing surface `LighterDispatcher` wires up
    (`subscribe_to_market`/`unsubscribe_from_market` -- keyed by integer
    `market_id`, not symbol; translation happens once at the dispatcher
    boundary -- `order_book_for_market`, `order_books`, `run`). Mirrors
    HyperLiquidMarketDataWss's shape (no shard pool -- Lighter has on the
    order of ~100 markets and no documented per-connection subscription cap,
    per the design doc's Section 3, so one connection is assumed sufficient
    until live testing shows otherwise).
    """

    def __init__(self, order_book_update_callback=None):
        super().__init__(name="Lighter Order Book", url='wss://mainnet.zklighter.elliot.ai/stream')
        self._store = LighterOrderBookStore(
            order_book_update_callback=order_book_update_callback,
            resync_callback=self._resubscribe_order_book,
        )
        self._roster: set = set()
        self._roster_lock = threading.Lock()
        self._restore_state_timeout = float(
            os.environ.get('LIGHTER_WS_RESTORE_TIMEOUT', '120')
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
        # No handshake frame needed before real subscriptions can be sent.
        pass

    def _on_message_impl(self, message: str):
        self._store.apply_message(message)

    def _on_reconnect_start(self):
        self._reset_threading_events()
        self._defer_restore_state(self.wait_till_first_pong)

    @runAsThread
    def _defer_restore_state(self, pong_event: threading.Event):
        """After the new socket comes back up, replay every market_id in our roster.
        Ported from HyperLiquidMarketDataWss._defer_restore_state -- see that
        docstring for why `pong_event` is bound at spawn time and the wait is bounded."""
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
            market_ids = list(self._roster)
        if market_ids:
            logging.info('Restoring %s subscriptions: %s', self._name, market_ids)
            for market_id in market_ids:
                self._send_subscribe_op(market_id)
        else:
            logging.info('No markets to restore for %s.', self._name)

    def _send_subscribe_op(self, market_id: int) -> None:
        self._ws.send(json.dumps({"type": "subscribe", "channel": f"order_book/{market_id}"}))
        self._ws.send(json.dumps({"type": "subscribe", "channel": f"ticker/{market_id}"}))

    def _send_unsubscribe_op(self, market_id: int) -> None:
        self._ws.send(json.dumps({"type": "unsubscribe", "channel": f"order_book/{market_id}"}))
        self._ws.send(json.dumps({"type": "unsubscribe", "channel": f"ticker/{market_id}"}))

    def _resubscribe_order_book(self, market_id: int) -> None:
        """Force a fresh order_book snapshot after a nonce-chain gap (design doc
        Section 3/4). Store state for this market was already dropped by the caller
        (LighterOrderBookStore._handle_order_book_delta) before invoking this --
        `ticker` stays subscribed throughout, only `order_book` is cycled."""
        try:
            self._ws.send(json.dumps({"type": "unsubscribe", "channel": f"order_book/{market_id}"}))
            self._ws.send(json.dumps({"type": "subscribe", "channel": f"order_book/{market_id}"}))
        except Exception as e:
            logging.warning('%s: error resubscribing order_book for market_id %s: %s', self._name, market_id, e)

    def run(self, main_thread=False):
        """Bring the connection online. `main_thread` accepted for API parity with
        HyperLiquidMarketDataWss.run() but ignored -- always runs in a background thread."""
        _ = main_thread
        self._start_ws()

    def subscribe_to_market(self, market_id: int) -> None:
        with self._roster_lock:
            if market_id in self._roster:
                return
            self._roster.add(market_id)

        self.wait_till_socket_open.wait()
        self._send_subscribe_op(market_id)

    def unsubscribe_from_market(self, market_id: int) -> None:
        with self._roster_lock:
            self._roster.discard(market_id)

        try:
            self._send_unsubscribe_op(market_id)
        except Exception as e:
            logging.warning('LighterMarketDataWss: error sending unsubscribe for market_id %s: %s', market_id, e)

        self._store.forget(market_id)

    def order_book_for_market(self, market_id: int):
        return self._store.order_book_for_market(market_id)

    @property
    def order_books(self):
        return self._store.order_books

    @property
    def market_ids(self):
        return self._store.market_ids
