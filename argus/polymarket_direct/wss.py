import os
import json
import time
import bisect
import logging
import requests
import threading
import traceback
from collections import deque
from termcolor import colored
from utils3 import runAsThread
from websocket import WebSocketApp
from argus.wireproxy import wrapper as wp_wrappers
from py_clob_client_v2.endpoints import GET_TICK_SIZE
from argus.polymarket_direct import _types as pm_types
from concurrent.futures.thread import ThreadPoolExecutor
from argus.polymarket._classes import print_with_name, P2ConvertClass
from argus.polymarket_direct.order_types import OrderEvent, TradeEvent
from argus._argus_utils import throw_fuss, macos_notification_with_custom_sound


class PolymarketWSSBase:
    """
    Base class for Polymarket WebSocket connections.
    Handles common boilerplate: reconnection, ping/pong, threading events.
    Subclasses must provide: _name, _url, _create_ws_app(), _on_open_impl(), _on_message_impl()
    """

    def __init__(self, name: str, url: str):
        self._name = name
        self._url = url
        self._ws: WebSocketApp = None  # type: ignore

        # Reconnection state
        self._max_reconnect_attempts = int(os.environ.get('POLYMARKET_MAX_SOCKET_RETRIES', '50'))
        self._reconnect_attempts = 0
        self._internally_closed = False
        self._allow_ping = True

        # Ping/pong tracking
        self._ping_pong_lock = threading.Lock()
        self._ping_pongs = (0, 0)  # (sent, received)
        self._max_ping_pong_failures = int(os.environ.get('POLYMARKET_MAX_PING_PONG_FAILURES', '3'))

        # Prevent concurrent ping threads
        self._pinging_lock = threading.Lock()

        # Latency measurement: timestamp (perf_counter) of the most recently received non-PONG message
        self._last_msg_recv_ts: float = 0.0

        # Threading events
        self._reset_threading_events()

    def _reset_threading_events(self):
        """Reset threading events to their initial cleared state."""
        self.wait_till_socket_open = threading.Event()
        self.wait_till_first_pong = threading.Event()

    def _init_ws(self):
        """Initialize the WebSocket connection. Subclasses must set self._ws."""
        with self._ping_pong_lock:
            self._ping_pongs = (0, 0)
        self._create_ws_app()

    def _create_ws_app(self):
        """Create the WebSocketApp instance. Must be implemented by subclasses."""
        raise NotImplementedError("Subclasses must implement _create_ws_app()")

    def _on_open_base(self, ws):
        """Handle WebSocket open event."""
        _ = ws
        self._reconnect_attempts = 0
        logging.info('%s WebSocket opened.', self._name)
        self._on_open_impl()
        self.ping()
        self.wait_till_socket_open.set()

    def _on_open_impl(self):
        """Implementation-specific open logic. Must be implemented by subclasses."""
        raise NotImplementedError("Subclasses must implement _on_open_impl()")

    def _on_message_base(self, ws, message):
        """Handle WebSocket message."""
        _ = ws
        if message == "PONG":
            logging.debug('%s WebSocket received PONG.', self._name)
            with self._ping_pong_lock:
                self._ping_pongs = (self._ping_pongs[0], self._ping_pongs[1] + 1)
            self.wait_till_first_pong.set()
            return
        self._last_msg_recv_ts = time.perf_counter()
        self._on_message_impl(message)

    def _on_message_impl(self, message: str):
        """Implementation-specific message handling. Must be implemented by subclasses."""
        raise NotImplementedError("Subclasses must implement _on_message_impl()")

    def _on_close_base(self, ws, close_status_code, close_msg):
        """Handle WebSocket close event."""
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
            # Only re-enable pings on a true reconnect.  When the WS was closed
            # internally (e.g. pool sweeper draining a shard), leaving this False
            # — combined with the _internally_closed check at the top of the ping
            # loop — lets the ping thread exit cleanly instead of zombie-spamming
            # "Connection is already closed" forever.
            self._allow_ping = True

    def _on_reconnect_start(self):
        """Called when reconnection starts. Subclasses can override to restore state."""
        pass

    def _on_error_base(self, ws, error):
        """Handle WebSocket error."""
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
        """Send periodic PING messages and monitor PONG responses."""
        if self._pinging_lock.locked():
            logging.warning('Ping thread for %s WebSocket is already running. Not starting another.', self._name)
            return

        with self._pinging_lock:
            while True:
                # Exit cleanly when the WS has been closed for good (e.g. pool
                # sweeper draining an idle shard).  Without this the thread loops
                # forever, spamming "Connection is already closed" on a dead WS.
                if self._internally_closed:
                    logging.info('%s ping thread exiting (internally closed).', self._name)
                    return
                try:
                    if self._allow_ping:
                        self._ws.send("PING")
                        with self._ping_pong_lock:
                            self._ping_pongs = (self._ping_pongs[0] + 1, self._ping_pongs[1])
                            pings = self._ping_pongs[0]
                            pongs = self._ping_pongs[1]

                            if os.environ.get('POLYMARKET_DISABLE_PING_PONG_LOGS', 'false').lower() != 'true':
                                logging.info(
                                    'Sending PING to %s WebSocket. Total PINGs: %d, Total PONGs: %d',
                                    self._name, pings, pongs
                                )

                            ping_delta = abs(pings - pongs)
                            pct_of_max = ping_delta / self._max_ping_pong_failures * 100
                            pct_rounded = round(pct_of_max, 2)
                            if pct_rounded >= 50:
                                logging.warning(
                                    'No PONG received for last 3 PINGs on %s WebSocket. Maximum delta=%d Current delta=%d',
                                    self._name, self._max_ping_pong_failures, ping_delta)

                            if ping_delta >= self._max_ping_pong_failures:
                                logging.error(
                                    'Maximum PING-PONG failures reached. Reconnecting %s WebSocket...', self._name
                                )
                                throw_fuss(
                                    msg=f"{self._name.upper()} WEBSOCKET PING-PONG FAILURE: No PONG received for {ping_delta} PINGs.",
                                    notify=True
                                )
                                self._ws.close()
                    else:
                        logging.info('Ping to %s WebSocket is currently disabled.', self._name)
                except Exception as e:
                    logging.error("%s WebSocket ping failed: %s", self._name, e)
                    with self._ping_pong_lock:
                        self._ping_pongs = (self._ping_pongs[0] + 1, self._ping_pongs[1])
                        logging.info("Incrementing PING count despite error. Total PINGs: %d, Total PONGs: %d",
                                     self._ping_pongs[0], self._ping_pongs[1])
                time.sleep(10)

    def _start_ws_sync(self):
        """Start the WebSocket connection synchronously (blocks current thread)."""
        logging.info('Starting %s WebSocket...', self._name)
        self._init_ws()
        wp_wrappers.start_proxy_aware_ws(
            idx='POLYMARKET',
            websocket=self._ws,
        )

    @runAsThread
    def _start_ws(self):
        """Start the WebSocket connection in a new thread."""
        self._start_ws_sync()


class PolyMarketAccountEventWss(PolymarketWSSBase):
    """
    A WebSocket that exists just to listen to account events from the Polymarket CLOB.
    This is an authorised WSS connection to Polymarket and CLOB it is SEPARATE from `EnhancedPM`
    and does NOT provide any market data or order placement functionality. It does NOT hold
    any state information. WARNING: THIS CLASS DOES NOT IMPLEMENT IP Safety measures like the REST
    API it's assumed everything is clear. It does, however, respect WireProxy settings. This is because
    this class does not interact with the REST API or handle credential DERIVATION in any way,
    it simply takes the auth dict as-is and passes it to the WebSocket for authentication.
    """

    def __init__(self, auth: dict, update_callback=None):
        """
        Initialize the Polymarket Account Event WebSocket.
        :param auth: {"apiKey": api_key, "secret": api_secret, "passphrase": api_passphrase}
            Can be obtained from CLOB API.

        :param update_callback: A callback function that will be called with each OrderEvent received.
        """
        # auth dict validation
        keys_needed = ["apiKey", "secret", "passphrase"]
        for key in keys_needed:
            if key not in auth:
                raise ValueError(f"Auth dictionary must contain the key: {key}")

        super().__init__(name="Polymarket Account Event", url='wss://ws-subscriptions-clob.polymarket.com/ws/user')

        self._auth = auth
        self._update_callback = update_callback
        self._throw_fuss_on_user_events = os.environ.get('POLYMARKET_USER_EVENTS_FUSS', 'false').lower() == 'true'

        self._start_ws()

    def _create_ws_app(self):
        """Create the WebSocketApp instance."""
        self._ws = WebSocketApp(
            url=self._url,
            on_open=self._on_open_base,
            on_close=self._on_close_base,
            on_error=self._on_error_base,
            on_message=self._on_message_base
        )

    def _on_open_impl(self):
        """Implementation-specific open logic."""
        logging.info('Authenticating Polymarket Account Event WebSocket...')
        self._ws.send(json.dumps({
            "auth": self._auth,
            "markets": [],
            "type": "user",
        }))

    def _on_message_impl(self, message: str):
        """Implementation-specific message handling."""
        content = json.loads(message)

        # Handle different message types
        msg_type = content.get('type', '').upper()

        if msg_type == 'TRADE':
            # Parse TRADE messages into TradeEvent
            try:
                update = TradeEvent.from_dict(content)
            except KeyError as e:
                print('WARNING: Received unexpected TRADE message format on Polymarket '
                      'Account Event WebSocket: {}'.format(content))
                raise
        else:
            try:
                update = OrderEvent.from_dict(content)
            except KeyError as e:
                print('WARNING: Received unexpected message format on '
                      'Polymarket Account Event WebSocket: {}'.format(content))
                raise

        if self._throw_fuss_on_user_events:
            throw_fuss(update.__repr__(), notify=False)
            macos_notification_with_custom_sound(
                title="POLYMARKET USER ACCOUNT EVENT",
                message="A new account event occurred."
            )

        logging.info('Polymarket Account Event WebSocket message received: %s', content)
        if self._update_callback:
            self._update_callback(update)


class OrderBookStore:
    """
    Pure book-state container shared across N PolyMarketOrderBookConn shards.

    No socket awareness — `apply_message` is called by each shard's WS callback thread
    with the raw frame, and this class owns all dedup, dict-mutation, and user-callback
    fanout.  Per-asset uniqueness is enforced by the owning Pool, so two shards never
    write to the same asset_id concurrently; cross-asset writes are serialized only
    while the (briefly held) `_dict_lock` mutates the top-level dicts.
    """

    def __init__(self, order_book_update_callback=None):
        # Book state, formerly on PolyMarketOrderBookWss.
        self._asset_id_to_order_book: dict = {}
        self._asset_id_to_misc_info: dict = {}  # tickSize + future_running per asset
        self._asset_id_to_best_bid_ask: dict = {}

        self._order_book_update_callback = order_book_update_callback
        self._dict_lock = threading.Lock()

        # Latency tracking — read by the dispatcher (via the pool) to compute
        # WS-arrival → sendall propagation latency.  Updated inside `apply_message`
        # so it's accurate regardless of which shard delivered the message.
        self._last_msg_recv_ts: float = 0.0

        # REST session for tick-size fetches.  Lives on the store (not per-shard)
        # so that re-subscribe-after-reconnect doesn't double-fetch.
        self.session = requests.Session()
        self._thread_pool = ThreadPoolExecutor(
            max_workers=5,
            thread_name_prefix="OrderBookStoreTickPool",
        )

        if os.environ.get('POLYMARKET_UNSAFE_RAPID_CONNECTIONS', 'false').lower() == 'true':
            print(colored("[{}] WARNING: UNSAFE RAPID CONNECTIONS IS ENABLED. "
                          "THIS MAY BREAK WEBSOCKET CONNECTIONS.".format(__name__),
                          color='yellow', attrs=['bold', 'blink']))
        else:
            wp_wrappers.update_request_session_proxy(
                session=self.session,
                idx='POLYMARKET',
                verbose=False,
            )

        # Stats — arrival timestamps of recent WS frames, newest last.
        #
        # This was an unbounded `list` appended once per frame in `apply_message` and
        # never trimmed, so it grew for the life of the process: at ~380 frames/s
        # (157 assets in prod) each entry costs ~67 bytes on free-threaded 3.14
        # (a 40-byte float plus its list slot), i.e. ~2.2 GB/day of immortal objects.
        # Because frames are applied on every shard thread, those floats landed in
        # every thread's mimalloc arena and pinned all of them — which is what a
        # 5 GB RSS / 4.7 GB-across-six-arenas prod process turned out to be made of.
        #
        # Nothing in production reads these samples (`print_stats` is only reachable
        # from the __main__ demo below), so a bounded window is strictly sufficient.
        self._updates: deque[float] = deque(
            maxlen=int(os.environ.get('POLYMARKET_WS_STAT_SAMPLES', '4096'))
        )

    ##############################################
    # Pool-facing entry points
    ##############################################

    def on_subscribe(self, asset_id: str) -> None:
        """
        Called by the pool the first time an asset is subscribed.  Idempotent —
        if a misc_info entry already exists (e.g. survived a forget/re-subscribe race),
        we leave it alone rather than firing a duplicate REST tick-size fetch.
        """
        with self._dict_lock:
            existing = self._asset_id_to_misc_info.get(asset_id)
            if existing and (existing.get('tick_size') or existing.get('future_running')):
                return
            self._asset_id_to_misc_info[asset_id] = {
                'tick_size': None,
                'future_running': self._future_get_tick_size(asset_id),
            }

    def forget(self, asset_id: str) -> None:
        """Drop all state for an asset (called by pool on full unsubscribe)."""
        with self._dict_lock:
            self._asset_id_to_order_book.pop(asset_id, None)
            self._asset_id_to_misc_info.pop(asset_id, None)
            self._asset_id_to_best_bid_ask.pop(asset_id, None)

    def apply_message(self, message: str) -> None:
        """
        Parse a raw WS frame from a shard and dispatch to the per-event handler.

        Called concurrently from N shard threads — but per-asset there is still
        only ever one writer (Pool guarantees one shard per asset_id), so the
        dedup/update logic remains race-free for any given asset.
        """
        self._last_msg_recv_ts = time.perf_counter()
        self._updates.append(time.time())

        try:
            content = json.loads(message)
        except json.JSONDecodeError:
            # Polymarket's WebSocket occasionally sends plain-text control strings
            # (e.g. "NO NEW ASSETS") that are not valid JSON.  These are informational
            # and safe to ignore — re-raising would crash the websocket-client callback
            # loop and tear down the connection.
            logging.debug('Non-JSON message from Polymarket Order Book WebSocket (ignored): "%s"', message)
            return

        try:
            if isinstance(content, list):
                for msg in content:
                    self._handle_order_book_message(msg)
            else:
                self._handle_order_book_message(content)
        except Exception as e:
            print('WARNING: Error handling Polymarket Order Book WebSocket message: "{}"'.format(message))
            raise e

    ##############################################
    # Message Handlers & Logic
    ##############################################

    def _handle_order_book_message(self, message: dict) -> None:
        event_type = message.get('event_type')
        asset_id = message.get('asset_id')

        # Controls whether the bottom-of-function callback fires.
        # Set to True when we've already fired it (best_bid_ask fast path) or
        # determined it's a dedup (price_change that matches cached best_bid_ask state).
        _skip_bottom_callback = False

        if not asset_id:
            # Multi-asset price_change: each change in the list is processed individually.
            if event_type == 'price_change' and 'price_changes' in message:
                for change in message['price_changes']:
                    asset_id = change['asset_id']
                    if asset_id is None:
                        return

                    # Only pay the dedup-check cost if this change touches the top of book —
                    # deep-book changes can never be redundant with a best_bid_ask event.
                    is_top = self._is_top_of_book_change(asset_id, change)
                    self._update_order_book(asset_id, change)

                    # If best_bid_ask already fired a callback reflecting this exact
                    # top-of-book state, skip to avoid double-firing for this asset.
                    if is_top and self._matches_best_bid_ask_cache(asset_id):
                        continue

                    order_book = self.order_book_for_asset_id(asset_id)
                    if self._order_book_update_callback and order_book is not None:
                        self._order_book_update_callback({
                            asset_id: order_book,
                            'timestamp': message['timestamp']
                        })

        elif event_type == 'book':
            # Snapshot: bids descending, asks ascending
            bid_sorted = sorted(message['bids'], key=lambda x: float(x['price']), reverse=True)
            ask_sorted = sorted(message['asks'], key=lambda x: float(x['price']))

            with self._dict_lock:
                self._asset_id_to_order_book[asset_id] = {
                    'bids': bid_sorted,
                    'asks': ask_sorted
                }

        elif event_type == 'price_change':
            # Single-asset delta.
            # Check whether this change is at the top of book BEFORE updating so we
            # compare against the pre-update best level (the level that best_bid_ask
            # would have been tracking).
            is_top = self._is_top_of_book_change(asset_id, message)
            self._update_order_book(asset_id, message)

            # After update: if the change was at the top and the resulting best bid/ask
            # matches what best_bid_ask already broadcast, suppress the bottom callback.
            if is_top and self._matches_best_bid_ask_cache(asset_id):
                _skip_bottom_callback = True

        elif event_type == 'best_bid_ask':
            # Fast path: Polymarket sends this dedicated event whenever the best bid or
            # ask PRICE shifts (not merely size).  It arrives over the same TCP stream as
            # price_change so ordering is preserved from source — best_bid_ask may arrive
            # before or after the corresponding price_change, but we fire immediately
            # either way and let the price_change handler dedup against this cache entry.
            #
            # Payload: {"event_type": "best_bid_ask", "asset_id": "...", "market": "0x...",
            #           "best_bid": "0.73", "best_ask": "0.77", "spread": "0.04",
            #           "timestamp": "1766789469958"}
            with self._dict_lock:
                self._asset_id_to_best_bid_ask[asset_id] = {
                    'best_bid': message['best_bid'],
                    'best_ask': message['best_ask'],
                }

            # Fire callback immediately with the current full L2 book.  The book may be
            # very slightly stale if the corresponding price_change hasn't arrived yet,
            # but this is the fastest possible notification that top-of-book has moved.
            if self._order_book_update_callback:
                book = self.order_book_for_asset_id(asset_id)
                if book is not None:
                    self._order_book_update_callback({
                        asset_id: book,
                        'timestamp': message['timestamp']
                    })
            return  # callback already fired; skip bottom

        elif event_type == 'tick_size_change':
            # Tick size change - store in misc info dict for now, as it doesn't affect the order book structure
            # {
            #     "event_type": "tick_size_change",
            #     "asset_id": "65818619657568813474341868652308942079804919287380422192892211131408793125422",
            #     "market": "0xbd31dc8a20211944f6b70f31557f1001557b59905b7738480ca09bd4532f84af",
            #     "old_tick_size": "0.01",
            #     "new_tick_size": "0.001",
            #     "timestamp": "100000000"
            # }
            with self._dict_lock:
                possible_future = self._asset_id_to_misc_info.get(asset_id, {}).get('future_running')
                if possible_future and not possible_future.done():
                    possible_future.cancel()

                self._asset_id_to_misc_info[asset_id] = {
                    'tick_size': message.get('new_tick_size'),
                    'future_running': None
                }

        # Bottom callback: fires for book snapshots, non-deduped price_changes, and
        # tick_size_change events.  Skipped when best_bid_ask returned early or when
        # a top-of-book price_change was already covered by a best_bid_ask callback.
        if not _skip_bottom_callback and self._order_book_update_callback:
            book = self.order_book_for_asset_id(asset_id)
            if book is not None:
                self._order_book_update_callback({
                    asset_id: book,
                    'timestamp': message['timestamp']
                })

    # Warning: This method is already thread-locked. Do not call inside another lock or you will cause a deadlock.
    def _update_order_book(self, asset_id: str, change: dict) -> None:
        with self._dict_lock:
            if asset_id not in self._asset_id_to_order_book:
                return

            book = self._asset_id_to_order_book[asset_id]
            price = change['price']
            size = float(change['size']) if change['size'] != '0' else 0
            side = 'bids' if change['side'] == 'BUY' else 'asks'
            levels = book[side]

            # Find existing level with this price
            idx = next((i for i, l in enumerate(levels) if l['price'] == price), None)

            if size == 0:
                if idx is not None:
                    levels.pop(idx)
            else:
                if idx is not None:
                    levels[idx]['size'] = str(size)  # update in place
                else:
                    # Insert in sorted position
                    new_level = {'price': price, 'size': str(size)}
                    insert_idx = bisect.bisect_left(
                        [float(l['price']) for l in levels],
                        float(price)
                    )
                    if side == 'bids':
                        insert_idx = len(levels) - insert_idx  # bids are descending
                    levels.insert(insert_idx, new_level)

    def _is_top_of_book_change(self, asset_id: str, change: dict) -> bool:
        """
        Returns True if this price_change event touches the current best bid or best ask level.

        This is called BEFORE _update_order_book so the comparison is against the pre-update
        top of book. A change at the top is the only case where a best_bid_ask event could
        arrive for the same state, making the subsequent price_change callback redundant.

        We use string comparison here (same as the price values in the book) — no float
        conversion needed since both sides come from Polymarket as strings.
        """
        with self._dict_lock:
            book = self._asset_id_to_order_book.get(asset_id)
            if not book:
                return False
            side = 'bids' if change.get('side') == 'BUY' else 'asks'
            levels = book[side]
            if not levels:
                return False
            # Change is at the top iff its price equals the current best level's price.
            return change.get('price') == levels[0]['price']

    def _matches_best_bid_ask_cache(self, asset_id: str) -> bool:
        """
        Returns True if the book's current best bid/ask matches the cached values from the
        most recent best_bid_ask event.

        Called AFTER _update_order_book so we compare the post-update book state against the
        cache. If they match, best_bid_ask has already fired a callback reflecting this exact
        top-of-book state, so the price_change callback can be safely suppressed (dedup).

        If no best_bid_ask has been received yet for this asset, returns False — we never
        suppress a callback without prior confirmation from the server.
        """
        with self._dict_lock:
            cached = self._asset_id_to_best_bid_ask.get(asset_id)
            if not cached:
                # No best_bid_ask received yet for this asset; can't dedup.
                return False
            book = self._asset_id_to_order_book.get(asset_id)
            if not book:
                return False
            bids = book.get('bids', [])
            asks = book.get('asks', [])
            current_best_bid = bids[0]['price'] if bids else None
            current_best_ask = asks[0]['price'] if asks else None
            return current_best_bid == cached['best_bid'] and current_best_ask == cached['best_ask']

    def order_book_for_asset_id(self, asset_id: str):
        """
        Get the order book for a specific asset ID.
        :param asset_id: Asset ID to get an order book for.
        :return:
        """
        return self._asset_id_to_order_book.get(asset_id, None)

    @property
    def order_books(self):
        return self._asset_id_to_order_book

    @property
    def asset_ids(self):
        return list(self._asset_id_to_order_book.keys())

    def print_stats(self):
        """
        Print msgs/sec received in the last 10 seconds.
        :return:
        """
        updates_copy = list(self._updates)
        now = time.time()
        last_10s = [t for t in updates_copy if now - t <= 10]
        msgs_per_sec = len(last_10s) / 10
        logging.info('Polymarket Order Book WebSocket stats: %.2f msgs/sec in the last 10 seconds.', msgs_per_sec)

        # Highest 10s msgs/sec within the retained window, via a two-pointer sweep — the
        # previous nested-loop version was O(n^2) over a list that grew without bound,
        # which made this unrunnable in production long before the memory became a problem.
        #
        # Sort first: `apply_message` runs concurrently on every shard thread and there is
        # no lock around "read time.time() -> append", so two threads can interleave and
        # leave the deque very slightly out of order.  The sweep assumes ascending input
        # and silently overcounts without this; the old brute force was order-independent.
        # O(n log n) on a bounded window is still far cheaper than the O(n^2) it replaced.
        updates_copy.sort()
        highest_10s = 0
        left = 0
        for right, start_time in enumerate(updates_copy):
            while updates_copy[left] <= start_time - 10:
                left += 1
            span = right - left + 1
            if span > highest_10s:
                highest_10s = span
        highest_msgs_per_sec = highest_10s / 10
        logging.info('Polymarket Order Book WebSocket highest recorded: %.2f msgs/sec in any 10 second window.',
                     highest_msgs_per_sec)

    @runAsThread
    def _debug_print_stats_loop(self):
        while True:
            self.print_stats()
            time.sleep(10)

    def _future_get_tick_size(self, asset_id: str):
        """
        Returns a future that will get the tick size for a specific asset id.
        :return:
        """

        def _inner():
            url = "{}{}?token_id={}".format("https://clob.polymarket.com", GET_TICK_SIZE, asset_id)
            response = self.session.get(url)
            response.raise_for_status()
            data = response.json()
            return str(data['minimum_tick_size'])  # this is exactly how py_clob does it

        return self._thread_pool.submit(_inner)

    def get_tick_size(self, asset_id: str, timeout=10):
        """
        Get the tick size for a specific asset ID, waiting for the future to complete if necessary.
        There are 2 paths either the future is still running and the WSS has not pushed a new tickSize
        in which case we will wait for the future, otherwise return the latest tickSize from the WSS update.

        :param asset_id: The asset ID to get the tick size for.
        :param timeout: How long to wait for the tick size future to complete before giving up and returning None.
        :return: The tick size as a string, or None if it could not be retrieved in time.
        """

        with self._dict_lock:
            misc_info = self._asset_id_to_misc_info.get(asset_id, {})
            future = misc_info.get('future_running')

        # if the future is still running and there is no tick size update from the WSS,
        # wait for the future to complete and update the tick size in the misc info dict
        if future and not future.done() and (not misc_info.get('tick_size')):
            print(colored('[polymarket wss] Waiting for {} future to complete'.format(asset_id), color='yellow',
                          attrs=['bold', 'blink']))
            try:
                tick_size = future.result(timeout=timeout)
                with self._dict_lock:
                    # one last check to see if the tick size was updated.
                    if self._asset_id_to_misc_info[asset_id].get('tick_size'):
                        # we don't need to set None because the WSS will update it when it gets the update, we just need to return the latest tick size
                        return self._asset_id_to_misc_info[asset_id]['tick_size']

                    self._asset_id_to_misc_info[asset_id]['future_running'] = None
                    self._asset_id_to_misc_info[asset_id]['tick_size'] = tick_size

            except Exception as e:
                logging.error('Error retrieving tick size for asset ID %s: %s', asset_id, e)
                return None

        # if the future is done but there is still no tick size in the misc info dict,
        # it means the WSS has not pushed a tick size update yet, so we return what the future got us
        if future and future.result() and (not misc_info.get('tick_size')):
            # ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ Is `technically` a local copy, but this is
            # microseconds from when we got it since there's no blocking that occurred
            with self._dict_lock:
                self._asset_id_to_misc_info[asset_id]['future_running'] = None
                self._asset_id_to_misc_info[asset_id]['tick_size'] = future.result()

        # Finally, if the future is none, that means tick_size was updated.
        with self._dict_lock:
            return self._asset_id_to_misc_info[asset_id]['tick_size']

        # just throw if the key doesn't exit, it should be
        # impossible since on_subscribe initializes the dict entry for the asset ID


class PolyMarketOrderBookConn(PolymarketWSSBase):
    """
    A single Polymarket order book WebSocket *shard*.

    Owns only the WS lifecycle (open / close / ping / pong / reconnect — inherited
    from `PolymarketWSSBase`) and a `_roster` of the asset_ids this shard is
    currently subscribed to.  All book state lives on the shared `OrderBookStore`
    that the owning `PolyMarketOrderBookPool` injects on construction.

    Reconnect behavior: `_roster` is populated at subscribe-time (not derived from
    the book dict the way the old single-conn class was), so even assets that
    never received a snapshot before the disconnect are correctly re-subscribed.
    """

    def __init__(self, store: 'OrderBookStore', shard_index: int = 0):
        super().__init__(
            name=f"Polymarket Order Book Shard {shard_index}",
            url='wss://ws-subscriptions-clob.polymarket.com/ws/market',
        )
        self._store = store
        self._shard_index = shard_index
        self._roster: set[str] = set()
        self._roster_lock = threading.Lock()
        # Upper bound on how long a restore thread will wait for the first PONG after a
        # reconnect before giving up.  Generous on purpose: PING goes out every 10s and
        # the ping/pong failure detector tears the socket down after 3 missed PONGs
        # (~30s), which spawns a fresh restore thread anyway — so this only ever fires
        # for a socket that is wedged rather than merely slow.
        self._restore_state_timeout = float(
            os.environ.get('POLYMARKET_WS_RESTORE_TIMEOUT', '120')
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
        # Polymarket requires an initial subscribe frame (even an empty one) to
        # complete the protocol handshake.  Real subscriptions are sent later by
        # the pool via `subscribe()`.
        self._ws.send(json.dumps({"assets_ids": [], "type": "market"}))

    def _on_message_impl(self, message: str):
        self._store.apply_message(message)

    def _on_reconnect_start(self):
        self._reset_threading_events()
        # Hand the restore thread the Event it was spawned for.  See _defer_restore_state.
        self._defer_restore_state(self.wait_till_first_pong)

    @runAsThread
    def _defer_restore_state(self, pong_event: threading.Event):
        """After the new socket comes back up, replay every asset_id in our roster.

        `pong_event` is bound at spawn time rather than read off `self` inside the
        thread, and the wait is bounded.  Both matter:

        `_reset_threading_events()` *replaces* `self.wait_till_first_pong` with a brand
        new Event on every reconnect.  The old code re-read that attribute here, so if a
        second reconnect landed before the first PONG arrived (trivially easy — PING is
        only sent every 10s), this thread was left parked on an Event that nothing held
        a reference to any more and that nothing would ever `.set()`.  With no timeout on
        the wait, that thread never woke up again: one permanently parked thread per
        rapid-reconnect pair, each keeping a live per-thread mimalloc arena resident on
        free-threaded 3.14.  A prod dispatcher accumulated 721 of them in 29.5 hours.

        Binding the Event makes the wait immune to the swap; the timeout guarantees the
        thread exits even if the socket is wedged; and the identity check below drops the
        restore if a newer reconnect has already superseded us, so only one thread ever
        replays the roster.
        """
        if not pong_event.wait(timeout=self._restore_state_timeout):
            logging.warning(
                '%s: no PONG within %.0fs of reconnect; abandoning subscription restore '
                '(a later reconnect will retry).',
                self._name, self._restore_state_timeout,
            )
            return

        if self._internally_closed:
            logging.info('%s: shard closed while awaiting PONG; skipping restore.', self._name)
            return

        if pong_event is not self.wait_till_first_pong:
            logging.info(
                '%s: superseded by a newer reconnect; leaving restore to it.', self._name
            )
            return

        with self._roster_lock:
            assets = list(self._roster)
        if assets:
            logging.info('Restoring %s subscriptions: %s', self._name, assets)
            for asset_id in assets:
                self._send_subscribe_op(asset_id)
        else:
            logging.info('No asset IDs to restore for %s.', self._name)

    def _send_subscribe_op(self, asset_id: str) -> None:
        self._ws.send(json.dumps({
            "assets_ids": [asset_id],
            "type": "market",
            "operation": "subscribe",
            "custom_feature_enabled": True,
        }))

    def _send_unsubscribe_op(self, asset_id: str) -> None:
        self._ws.send(json.dumps({
            "assets_ids": [asset_id],
            "type": "market",
            "operation": "unsubscribe",
        }))

    def subscribe(self, asset_id: str) -> None:
        """Add to roster + send the subscribe op.  Caller must have waited on socket open."""
        with self._roster_lock:
            self._roster.add(asset_id)
        self._send_subscribe_op(asset_id)

    def unsubscribe(self, asset_id: str) -> None:
        with self._roster_lock:
            self._roster.discard(asset_id)
        self._send_unsubscribe_op(asset_id)

    @property
    def roster_size(self) -> int:
        with self._roster_lock:
            return len(self._roster)

    @property
    def roster_snapshot(self) -> list[str]:
        with self._roster_lock:
            return list(self._roster)

    def close(self) -> None:
        """Permanently close this shard.  Suppresses the auto-reconnect path."""
        self._internally_closed = True
        self._allow_ping = False
        try:
            self._ws.close()
        except Exception as e:
            logging.warning('Error closing %s: %s', self._name, e)

    def start(self) -> None:
        """Start the WS in a background thread (non-blocking)."""
        self._start_ws()


class PolymarketRTDSWss(PolymarketWSSBase):
    """
    A class that sends all the supported crypto prices from polymarket to the clients
    that request subscription to RDTS. There are only 3-4 assets aavailable on polymarkets
    both binance and chainlink stream hence why this class just subscribes to all and the
    value passed into 

    ask[0] = value returned from RTDS
    ask_size[0-N] = 0
    bids[0-N] = 0
    ....

    only ask[0] will have a value and ofc the timestamp. And the symbol will be
    <source>-<asset> i.e. binance-btusdt or chainlink-btcusdt. Any special values sent
    by polymarket like eth/usdt will auto convert -> ethust. The same padding guaraentees
    can be accepted assumed since thats done in P2ConverterClass not here

    """

    def __init__(self, on_msg_callback, book_depth: int):
        super().__init__(
            name="Polymarket Real-Time Data Stream",
            url="wss://ws-live-data.polymarket.com"
        )
        self.callback = on_msg_callback
        self.subscription_msg = {
            "action": "subscribe",
            "subscriptions": [
                {
                    "topic": "crypto_prices",
                    "type": "update",
                },
                {
                    "topic": "crypto_prices_chainlink",
                    "type": "*",
                    "filters": ""
                }
            ]
        }
        self.depth = book_depth

        # don't know why, but this connection does not pong
        # but asks for ping IDK
        self._max_ping_pong_failures = 100000000

    def _create_ws_app(self):
        self._ws = WebSocketApp(
            url=self._url,
            on_open=self._on_open_base,
            on_close=self._on_close_base,
            on_error=self._on_error_base,
            on_message=self._on_message_base
        )

    def _on_open_impl(self):
        self._ws.send_text(json.dumps(self.subscription_msg))

    # even if the ws dies after it re-opens this should be called again
    # restoring all things
    def _on_message_impl(self, message: str):
        try:
            js_msg = json.loads(message)
        except json.JSONDecodeError:
            print_with_name("Unable to decode JSON (RDTS), msg=", message, "size=", len(message))
            return

        try:
            topic = str(js_msg["topic"])
        except KeyError as e:
            print("Unable to find topic in JSON payload. Msg=", js_msg)
            raise e
        payload = js_msg.get("payload", None)

        if payload is None:
            return

        # below is based on polymarket/_classes.py P2ConverterClass

        market_data = {
            payload["symbol"]: {
                "bids": [],
                "asks": [
                    {"price": payload["value"], "size": 0.0}
                ]
            },
            "timestamp": payload["timestamp"]
        }

        if "crypto_prices_chainlink" in topic:
            forced_symbol = "chainlink-" + payload["symbol"].replace("/", "")
        else:
            forced_symbol = "binance-" + payload["symbol"].replace("/", "")

        # we set asset_id = payload[symbol] because `transferable_2` uses it to key out
        # the market data from the market_data dict which we keyd with payload[symbol]
        # The others are over-ridden with forced_symbol
        p2_class = P2ConvertClass(ticker="", market_slug="", asset_id=payload["symbol"], market_data=market_data,
                                  order_book_depth=self.depth, forced_symbol=forced_symbol)

        self.callback(p2_class)

    def start(self):
        """Start the RTDS (non-blocking)"""
        self._start_ws()


class PolyMarketOrderBookPool:
    """
    Façade over N PolyMarketOrderBookConn shards backed by a single OrderBookStore.

    Exposes the same surface the dispatcher used to call on `PolyMarketOrderBookWss`
    (`subscribe_to_asset_id`, `unsubscribe_from_asset_id`, `order_book_for_asset_id`,
    `get_tick_size`, `run`) so the swap is a one-liner at the call site.

    Sharding strategy:
        - Each shard caps at `max_assets_per_shard` (default 4).
        - Subscriptions go to the smallest non-draining shard with room.
        - At full capacity, a new shard is spawned up to `max_shards`.
        - When a shard's roster empties (and we're above `min_shards`), it goes
          into a `_draining` grace window of `scale_down_idle_seconds`; if no new
          subscribe lands within that window, the sweeper closes it.
        - A pending re-subscribe arriving during the grace window un-drains the
          shard rather than spawning a fresh connection (avoids churn).

    All env-tunable from day one — see the table in plan.md.
    """

    def __init__(self, order_book_update_callback=None):
        self._max_assets_per_shard = int(os.environ.get('POLYMARKET_MAX_ASSETS_PER_WS', '4'))
        self._min_shards = int(os.environ.get('POLYMARKET_MIN_SHARDS', '1'))
        self._max_shards = int(os.environ.get('POLYMARKET_MAX_SHARDS', '10'))
        self._scale_down_idle_seconds = float(os.environ.get('POLYMARKET_SCALE_DOWN_IDLE_S', '30'))

        if self._min_shards < 1:
            self._min_shards = 1
        if self._max_shards < self._min_shards:
            self._max_shards = self._min_shards

        self._store = OrderBookStore(order_book_update_callback=order_book_update_callback)
        self._shards: list[PolyMarketOrderBookConn] = []
        self._asset_to_shard: dict[str, PolyMarketOrderBookConn] = {}
        # shard -> drain_start_time (monotonic).  Membership = "this shard is in
        # the grace window awaiting close."
        self._draining: dict[PolyMarketOrderBookConn, float] = {}
        self._lock = threading.RLock()
        self._next_shard_index = 0
        self._sweeper_started = False

    # ---- dispatcher-facing latency probe ---------------------------------
    @property
    def _last_msg_recv_ts(self) -> float:
        # The dispatcher reads this via `getattr(self.market_data, '_last_msg_recv_ts', 0.0)`
        # at __init__.py:497.  Forward to the store so latency is measured per-message
        # regardless of which shard delivered it.
        return self._store._last_msg_recv_ts

    # ---- internal helpers -------------------------------------------------
    def _spawn_shard_locked(self) -> PolyMarketOrderBookConn:
        """Caller must hold self._lock."""
        idx = self._next_shard_index
        self._next_shard_index += 1
        shard = PolyMarketOrderBookConn(store=self._store, shard_index=idx)
        self._shards.append(shard)
        shard.start()
        logging.info('PolyMarketOrderBookPool: spawned shard %d (total=%d)', idx, len(self._shards))
        return shard

    def _pick_or_spawn_shard_locked(self, asset_id: str) -> PolyMarketOrderBookConn:
        """
        Pick an existing shard with room, un-drain a draining one if needed,
        or spawn a new shard.  Raises if all shards are full.
        Caller must hold self._lock.
        """
        # 1. Smallest non-draining shard with room.
        candidates = [
            s for s in self._shards
            if s not in self._draining and s.roster_size < self._max_assets_per_shard
        ]
        if candidates:
            return min(candidates, key=lambda s: s.roster_size)

        # 2. Resurrect a draining shard with room (avoids churn during the grace window).
        for s in list(self._draining.keys()):
            if s.roster_size < self._max_assets_per_shard:
                del self._draining[s]
                logging.info(
                    'PolyMarketOrderBookPool: un-draining shard %d for %s',
                    s._shard_index, asset_id,
                )
                return s

        # 3. Spawn a new shard if we have headroom.
        if len(self._shards) < self._max_shards:
            return self._spawn_shard_locked()

        # 4. Out of capacity — propagate so the dispatcher's _handle_subscribe surfaces it.
        raise RuntimeError(
            f"PolyMarketOrderBookPool: cannot subscribe {asset_id}; "
            f"all {self._max_shards} shards are full at "
            f"{self._max_assets_per_shard} assets each."
        )

    # ---- dispatcher-facing API -------------------------------------------
    def run(self, main_thread=False):
        """
        Bring the pool online: spawn `min_shards` connections and start the sweeper.

        `main_thread` is accepted for API compatibility with the old single-conn
        class but ignored — the pool always runs all shards in background threads.
        """
        _ = main_thread
        with self._lock:
            while len(self._shards) < self._min_shards:
                self._spawn_shard_locked()
            if not self._sweeper_started:
                self._sweeper_started = True
                self._scale_down_sweeper()

    def subscribe_to_asset_id(self, asset_id: str):
        # Pool owns idempotency — _handle_subscribe in the dispatcher fires this
        # unconditionally on every client subscribe; we no-op duplicates here.
        with self._lock:
            if asset_id in self._asset_to_shard:
                return
            shard = self._pick_or_spawn_shard_locked(asset_id)
            self._asset_to_shard[asset_id] = shard

        # Wait for the shard's WS to be open *outside* the pool lock so other
        # subscribe/unsubscribe ops can proceed in parallel.
        shard.wait_till_socket_open.wait()
        shard.subscribe(asset_id)
        self._store.on_subscribe(asset_id)

    def unsubscribe_from_asset_id(self, asset_id: str):
        with self._lock:
            shard = self._asset_to_shard.pop(asset_id, None)
        if shard is None:
            return

        try:
            shard.unsubscribe(asset_id)
        except Exception as e:
            logging.warning(
                'PolyMarketOrderBookPool: error sending unsubscribe for %s: %s',
                asset_id, e,
            )

        self._store.forget(asset_id)

        with self._lock:
            if shard.roster_size == 0 and len(self._shards) > self._min_shards and shard not in self._draining:
                self._draining[shard] = time.monotonic()
                logging.info(
                    'PolyMarketOrderBookPool: shard %d entered drain window',
                    shard._shard_index,
                )

    def order_book_for_asset_id(self, asset_id: str):
        return self._store.order_book_for_asset_id(asset_id)

    def get_tick_size(self, asset_id: str, timeout=10):
        return self._store.get_tick_size(asset_id, timeout=timeout)

    def subscribe_to_market(self, market: pm_types.PolymarketEvent):
        """Convenience helper retained from the old API — subscribe to every clob token in a market."""
        if not market.markets:
            logging.warning('Market has no sub-markets; cannot subscribe.')
            return

        if len(market.markets) > 1:
            logging.warning('Market has multiple sub-markets; This is unexpected behavior.')
            for m in market.markets:
                logging.warning('Sub-market: %s', m)

        first_market = market.markets[0]
        if first_market.clobTokenIds:
            for asset in first_market.clobTokenIds:
                self.subscribe_to_asset_id(asset.id)

    @property
    def order_books(self):
        return self._store.order_books

    @property
    def asset_ids(self):
        return self._store.asset_ids

    def print_stats(self):
        self._store.print_stats()
        with self._lock:
            shard_summary = ', '.join(
                f"shard{s._shard_index}={s.roster_size}" for s in self._shards
            )
            draining_count = len(self._draining)
        logging.info(
            'PolyMarketOrderBookPool: %d shards (%s), %d draining',
            len(self._shards), shard_summary, draining_count,
        )

    @runAsThread
    def _scale_down_sweeper(self):
        """Background thread that closes idle drained shards after the grace window."""
        sweep_interval = max(1.0, self._scale_down_idle_seconds / 5)
        while True:
            time.sleep(sweep_interval)
            now = time.monotonic()
            to_close: list[PolyMarketOrderBookConn] = []
            with self._lock:
                for shard, drain_start in list(self._draining.items()):
                    if shard.roster_size > 0:
                        # Defensive: subscribe path should have already un-drained.
                        del self._draining[shard]
                        continue
                    if now - drain_start >= self._scale_down_idle_seconds and len(self._shards) > self._min_shards:
                        to_close.append(shard)
                        del self._draining[shard]
                        self._shards.remove(shard)

            for shard in to_close:
                logging.info(
                    'PolyMarketOrderBookPool: closing drained shard %d',
                    shard._shard_index,
                )
                try:
                    shard.close()
                except Exception as e:
                    logging.warning('Error closing drained shard: %s', e)


if __name__ == '__main__':
    _HIDDEN_ASSET_ID = '661095475084821930790589425827399710453605787397495798070750303202782280580'


    def ev(x):
        print('---ORDER BOOK UPDATE---')
        print(x)
        print('---END UPDATE---')
        print('Number of bid levels: {}, Number of ask levels: {}'.format(
            len(x[_HIDDEN_ASSET_ID]['bids']) if x.get(_HIDDEN_ASSET_ID) else 'N/A',
            len(x[_HIDDEN_ASSET_ID]['asks']) if x.get(_HIDDEN_ASSET_ID) else 'N/A'
        ))
        print('-' * 100)


    pool = PolyMarketOrderBookPool(order_book_update_callback=ev)
    pool.run(main_thread=False)
    pool.subscribe_to_asset_id(_HIDDEN_ASSET_ID)
    input('Press Enter to exit...\n')
    pool.print_stats()
