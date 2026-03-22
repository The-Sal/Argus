import os
import json
import time
import bisect
import logging
import requests
import threading
import traceback
from termcolor import colored
from utils3 import runAsThread
from websocket import WebSocketApp
from py_clob_client.endpoints import GET_TICK_SIZE
from argus.wireproxy import wrapper as wp_wrappers
from argus.polymarket_direct import _types as pm_types
from concurrent.futures.thread import ThreadPoolExecutor
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
                            if ping_delta > 3:
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


class PolyMarketOrderBookWss(PolymarketWSSBase):
    """
    A level 2 order book WebSocket for Polymarket markets.

    Notes:
        This class takes, on average, 132mb of RAM and spawns ~5 threads.
        Tested with if/main on commit c5f0be913721305e937626f0e16c64bc75a3d0d4 (HEAD -> perf/rest-wss-orderbook-tuning) at 2026-02-04 23:05 UTC
        Unlike #59 this class does not have the same memory leak issues again tested on the above commit.
        Commits after this maybe affected. However, considering this is written during the final implementation of
        Polymarket order book WebSocket handling in Argus, it is likely stable and accurate.
    """

    def __init__(self, order_book_update_callback=None):
        super().__init__(name="Polymarket Order Book", url='wss://ws-subscriptions-clob.polymarket.com/ws/market')

        # Where a singular order book is stored as:
        # {
        #   'bids': [(price1, size1), (price2, size
        #   'asks': [(price1, size1), (price2, size2), ...]
        # }
        # asset ID then indexes the above in the main dict below
        self._asset_id_to_order_book = {}
        self._asset_id_to_misc_info = {}  # can be used to store other info about the asset if needed (e.g. tickSize)

        # Note: tickSize is stored as a string

        self._order_book_update_callback = order_book_update_callback
        self._dict_lock = threading.Lock()
        self.session = requests.Session()
        self._thread_pool = ThreadPoolExecutor(max_workers=5, thread_name_prefix="PolyMarketOrderBookWssThreadPool")

        if os.environ.get('POLYMARKET_UNSAFE_RAPID_CONNECTIONS', 'false').lower() == 'true':
            print(colored("[{}] WARNING: UNSAFE RAPID CONNECTIONS IS ENABLED. "
                          "THIS MAY BREAK WEBSOCKET CONNECTIONS.".format(__name__),
                          color='yellow', attrs=['bold', 'blink']))
        else:
            wp_wrappers.update_request_session_proxy(
                session=self.session,
                idx='POLYMARKET',
                verbose=False
            )

        # Stats
        self._updates: list[float] = []  # timestamps of updates received

        # Cache of the most recently received best_bid_ask values per asset.
        # Written only from best_bid_ask events; read by the price_change dedup check.
        # Structure: {asset_id: {'best_bid': str, 'best_ask': str}}
        self._asset_id_to_best_bid_ask: dict = {}

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
        initial_msg = json.dumps({"assets_ids": [], "type": "market"})
        self._ws.send(initial_msg)

    def _on_message_impl(self, message: str):
        """Implementation-specific message handling."""
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
            # Handle both list and dict messages
            if isinstance(content, list):
                for msg in content:
                    self._handle_order_book_message(msg)
            else:
                self._handle_order_book_message(content)
        except Exception as e:
            print('WARNING: Error handling Polymarket Order Book WebSocket message: "{}"'.format(message))
            raise e

    def _on_reconnect_start(self):
        """Called when reconnection starts - restore subscriptions."""
        self._reset_threading_events()
        self._defer_restore_state()

    @runAsThread
    def _defer_restore_state(self):
        """
        Waits for `wait_till_first_pong` to be cleared, then restores WebSocket subscriptions
        to previously subscribed asset IDs. Should only be called internally after a disconnect.
        """
        self.wait_till_first_pong.wait()
        asset_ids = self.asset_ids
        if asset_ids:
            logging.info('Restoring Polymarket Order Book WebSocket subscriptions for asset IDs: %s', asset_ids)
            for asset_id in asset_ids:
                self.subscribe_to_asset_id(asset_id)
        else:
            logging.info('No asset IDs to restore for Polymarket Order Book WebSocket.')

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

    def subscribe_to_asset_id(self, asset_id: str):
        self._ws.send(json.dumps({
            "assets_ids": [asset_id],
            "type": "market",
            "operation": "subscribe",
            "custom_feature_enabled": True
        }))

        with self._dict_lock:
            self._asset_id_to_misc_info[asset_id] = {
                'tick_size': None,
                'future_running': self._future_get_tick_size(asset_id)
            }

    def unsubscribe_from_asset_id(self, asset_id: str):
        """
        Unsubscribe from order book updates for a specific asset ID.
        This will remove the order book from the internal state. It will no
        longer be tracked.

        :param asset_id: The asset ID to unsubscribe from.
        :return:
        """
        self._ws.send(json.dumps({
            "assets_ids": [asset_id],
            "type": "market",
            "operation": "unsubscribe"
        }))

        with self._dict_lock:
            if asset_id in self._asset_id_to_order_book:
                del self._asset_id_to_order_book[asset_id]

    def subscribe_to_market(self, market: pm_types.PolymarketEvent):
        """
        Subscribe to order book updates for all asset IDs in a market.
        :param market: The PolymarketEvent market to subscribe to.
        :return:
        """
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

    def run(self, main_thread=False):
        """
        Run the WebSocket connection.
        :param main_thread: If True, run in the main thread. Otherwise, run in a separate thread.
        :return:
        """
        if main_thread:
            self._start_ws_sync()
        else:
            self._start_ws()

    def print_stats(self):
        """
        Print msgs/sec received in the last 10 seconds.
        :return:
        """
        updates_copy = self._updates.copy()
        now = time.time()
        last_10s = [t for t in updates_copy if now - t <= 10]
        msgs_per_sec = len(last_10s) / 10
        logging.info('Polymarket Order Book WebSocket stats: %.2f msgs/sec in the last 10 seconds.', msgs_per_sec)

        # find the highest 10s msgs/sec in history
        highest_10s = 0
        for i in range(len(updates_copy)):
            start_time = updates_copy[i]
            end_time = start_time + 10
            count = sum(1 for t in updates_copy if start_time <= t < end_time)
            if count > highest_10s:
                highest_10s = count
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
            print(colored('[polymarket wss] Waiting for {} future to complete'.format(asset_id), color='yellow', attrs=['bold', 'blink']))
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
        # impossible since subscribe_to_asset_id initializes the dict entry for the asset ID


if __name__ == '__main__':
    __x = 0

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


    wss = PolyMarketOrderBookWss(ev)

    # noinspection PyProtectedMember
    # wss._debug_print_stats_loop()
    wss.run(main_thread=False)
    # wait with threading event to ensure socket is open
    wss.wait_till_socket_open.wait()
    wss.subscribe_to_asset_id(
        _HIDDEN_ASSET_ID
    )
    input('Press Enter to exit...\n')
    wss.print_stats()
