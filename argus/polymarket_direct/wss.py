import os
import json
import time
import logging
import threading
import traceback
from utils3 import runAsThread
from websocket import WebSocketApp
from argus.wireproxy import wrapper as wp_wrappers
from argus.polymarket_direct import _types as pm_types
from argus.polymarket_direct.order_types import OrderEvent
from argus._argus_utils import throw_fuss, macos_notification_with_custom_sound




class PolyMarketAccountEventWss:
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
        self._auth = auth
        self._update_callback = update_callback

        # auth dict validation
        keys_needed = ["apiKey", "secret", "passphrase"]
        for key in keys_needed:
            if key not in self._auth:
                raise ValueError(f"Auth dictionary must contain the key: {key}")

        self.user_ws: WebSocketApp = None
        self._max_reconnect_attempts = int(os.environ.get('POLYMARKET_MAX_SOCKET_RETRIES', '50'))
        self._reconnect_attempts = 0
        self._internally_closed = False
        self._allow_ping = True
        self._reset_threading_events()

        self._ping_pong_lock = threading.Lock()
        self._ping_pongs = (0, 0)  # (sent, received)
        self._max_ping_pong_failures = int(os.environ.get('POLYMARKET_MAX_PING_PONG_FAILURES', '3'))

        # In some cases we've seen concurrent pings causing issues
        # that is since the ping function is a thread that runs forever;
        # it should not be called twice ever while it's already running
        self._pinging_lock = threading.Lock()

        self._throw_fuss_on_user_events = os.environ.get('POLYMARKET_USER_EVENTS_FUSS', 'false').lower() == 'true'

        self._start_ws()

    def _reset_threading_events(self):
        """
        Reset threading events for socket open and first pong.
        The 'default' state is 'clear' meaning the socket/ping is not ready.
        :return:
        """
        self.wait_till_socket_open = threading.Event()
        self.wait_till_first_pong = threading.Event()

        # Don't set these - they should start cleared (not ready)

    def _init_ws(self):
        """
        Initialize the WebSocket connection to Polymarket account events.
        :return:
        """
        with self._ping_pong_lock:
            self._ping_pongs = (0, 0)
        self.user_ws = WebSocketApp(
            url='wss://ws-subscriptions-clob.polymarket.com/ws/user',
            on_open=self._on_open,
            on_close=self._on_close,
            on_error=self._on_error,
            on_message=self._on_message
        )

    ############################################
    # WebSocket Event Handlers  & Utilities
    ############################################

    def _on_message(self, ws, message):
        _ = ws
        if message == "PONG":
            logging.debug('Polymarket Account Event WebSocket received PONG.')
            with self._ping_pong_lock:
                self._ping_pongs = (self._ping_pongs[0], self._ping_pongs[1] + 1)
            self.wait_till_first_pong.set()
            return

        content = json.loads(message)
        update = OrderEvent.from_dict(content)

        if self._throw_fuss_on_user_events:
            throw_fuss(update.__repr__(), notify=False)
            macos_notification_with_custom_sound(
                title="POLYMARKET USER ACCOUNT EVENT",
                message="A new account event occurred."
            )

        logging.info('Polymarket Account Event WebSocket message received: %s', content)
        if self._update_callback:
            self._update_callback(update)

    def _on_close(self, ws, close_status_code, close_msg):
        self._allow_ping = False
        _ = ws
        logging.warning('Polymarket Account Event WebSocket closed. Code: %s, Message: %s', close_status_code,
                        close_msg)
        print("Attempting to reconnect Polymarket Account Event WebSocket...")
        if not self._internally_closed:
            self._reconnect_attempts += 1
            if self._reconnect_attempts > self._max_reconnect_attempts:
                logging.error('Maximum reconnect attempts reached for Polymarket Account Event WebSocket. Giving up.')
                return
            time.sleep(1)
            self._start_ws()
        self._allow_ping = True

    @staticmethod
    def _on_error(ws, error):
        _ = ws
        throw_fuss(
            msg="POLYMARKET USER ACCOUNT WEBSOCKET ERROR:\n{}".format(traceback.format_exc()),
            notify=False
        )
        macos_notification_with_custom_sound(
            title="POLYMARKET USER ACCOUNT WEBSOCKET ERROR",
            message=str(error),
            sound_name="Basso"
        )

    @runAsThread
    def ping(self):

        # check if already pinging
        if self._pinging_lock.locked():
            logging.warning('Ping thread for Polymarket Account Event WebSocket is already running. Not starting another.')
            return

        with self._pinging_lock:
            while True:
                try:
                    if self._allow_ping:
                        self.user_ws.send("PING")
                        with self._ping_pong_lock:
                            self._ping_pongs = (self._ping_pongs[0] + 1, self._ping_pongs[1])
                            pings = self._ping_pongs[0]
                            pongs = self._ping_pongs[1]

                            if os.environ.get('POLYMARKET_DISABLE_PING_PONG_LOGS', 'false').lower() != 'true':
                                logging.info(
                                    'Sending PING to Polymarket Account Event WebSocket. Total PINGs: %d, Total PONGs: %d',
                                    pings, pongs
                                )

                            ping_delta = abs(pings - pongs)
                            if ping_delta > 3:
                                logging.warning('No PONG received for last 3 PINGs.... Maximum delta={}'.format(
                                    self._max_ping_pong_failures))

                            if ping_delta >= self._max_ping_pong_failures:
                                logging.error(
                                    'Maximum PING-PONG failures reached. Reconnecting Polymarket Account Event WebSocket...'
                                )
                                self.user_ws.close()
                    else:
                        logging.info('Ping to Polymarket Account Event WebSocket is currently disabled.')
                except Exception as e:
                    logging.error("User WebSocket ping failed: %s", e)
                    with self._ping_pong_lock:
                        self._ping_pongs = (self._ping_pongs[0] + 1, self._ping_pongs[1])
                        logging.info("Incrementing PING count despite error. Total PINGs: %d, Total PONGs: %d", )
                    pass
                time.sleep(10)

    def _on_open(self, ws):
        _ = ws
        logging.info('Polymarket Account Event WebSocket opened.')
        self.authenticate_ws_for_asset_ids()
        self.ping()
        self.wait_till_socket_open.set()

    def authenticate_ws_for_asset_ids(self):
        """
        Authenticate the WebSocket connection.
        :return:
        """
        logging.info('Authenticating Polymarket Account Event WebSocket...')
        self.user_ws.send(json.dumps({
            "auth": self._auth,
            "markets": [],
            "type": "user",
        }))

    @runAsThread
    def _start_ws(self):
        """
        Start the WebSocket connection.
        :return:
        """
        logging.info('Starting Polymarket Account Event WebSocket...')
        self._init_ws()
        wp_wrappers.start_proxy_aware_ws(
            idx='POLYMARKET',
            websocket=self.user_ws,
        )


class PolyMarketOrderBookWss:
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

        # Where a singular order book is stored as:
        # {
        #   'bids': [(price1, size1), (price2, size
        #   'asks': [(price1, size1), (price2, size2), ...]
        # }
        # asset ID then indexes the above in the main dict below
        self._asset_id_to_order_book = {}

        self._pinging_lock = threading.Lock()

        self._market_ws: WebSocketApp = None
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = int(os.environ.get('POLYMARKET_MAX_SOCKET_RETRIES', '50'))
        self._ping_pong_lock = threading.Lock()
        self._ping_pongs = (0, 0)  # (sent, received)
        self._max_ping_pong_failures = int(os.environ.get('POLYMARKET_MAX_PING_PONG_FAILURES', '3'))
        self._internally_closed = False
        self._allow_ping = True
        self._reset_threading_events()
        self._order_book_update_callback = order_book_update_callback

        # Stats
        self._updates: list[float] = []  # timestamps of updates received

    #############################################
    # WebSocket Event Handlers  & Utilities
    #############################################

    def _reset_threading_events(self):
        """
        Reset threading events for socket open and first pong.
        The 'default' state is 'clear' meaning the socket/ping is not ready.
        :return:
        """
        self.wait_till_socket_open = threading.Event()
        self.wait_till_first_pong = threading.Event()

        # Don't set these - they should start cleared (not ready)

    def _init_ws(self):
        """
        Initialize the WebSocket connection to Polymarket order book events.
        :return:
        """
        with self._ping_pong_lock:
            self._ping_pongs = (0, 0)
        self._market_ws = WebSocketApp(
            url='wss://ws-subscriptions-clob.polymarket.com/ws/market',
            on_open=self._on_open,
            on_close=self._on_close,
            on_error=self._on_error,
            on_message=self._on_message
        )

    @runAsThread
    def ping(self):

        # check if already pinging
        if self._pinging_lock.locked():
            logging.warning(
                'Ping thread for Polymarket Account Event WebSocket is already running. Not starting another.')
            return

        with self._pinging_lock:
            while True:
                try:
                    if self._allow_ping:
                        self._market_ws.send("PING")
                        with self._ping_pong_lock:
                            self._ping_pongs = (self._ping_pongs[0] + 1, self._ping_pongs[1])
                            pings = self._ping_pongs[0]
                            pongs = self._ping_pongs[1]

                            if os.environ.get('POLYMARKET_DISABLE_PING_PONG_LOGS', 'false').lower() != 'true':
                                logging.info(
                                    'Sending PING to Polymarket Orderbook WebSocket. Total PINGs: %d, Total PONGs: %d',
                                    pings, pongs
                                )

                            ping_delta = abs(pings - pongs)
                            if ping_delta > 3:
                                logging.warning('No PONG received for last 3 PINGs.... Maximum delta={}'.format(
                                    self._max_ping_pong_failures))

                            if ping_delta >= self._max_ping_pong_failures:
                                logging.error(
                                    'Maximum PING-PONG failures reached. Reconnecting Polymarket Account Event WebSocket...'
                                )
                                self._market_ws.close()
                    else:
                        logging.info('Ping to Polymarket Account Event WebSocket is currently disabled.')
                except Exception as e:
                    logging.error("User WebSocket ping failed: %s", e)
                    with self._ping_pong_lock:
                        self._ping_pongs = (self._ping_pongs[0] + 1, self._ping_pongs[1])
                        logging.info("Incrementing PING count despite error. Total PINGs: %d, Total PONGs: %d", )
                    pass
                time.sleep(10)

    def _on_message(self, ws, message):
        self._updates.append(time.time())
        _ = ws
        if message == "PONG":
            logging.debug('Polymarket Order Book WebSocket received PONG.')
            with self._ping_pong_lock:
                self._ping_pongs = (self._ping_pongs[0], self._ping_pongs[1] + 1)
            self.wait_till_first_pong.set()
            return

        # how_long.start()

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

        # how_long.stop()

    def _on_close(self, ws, close_status_code, close_msg):
        self._defer_restore_state()
        self._allow_ping = False
        _ = ws
        logging.warning('Polymarket Order Book WebSocket closed. Code: %s, Message: %s', close_status_code,
                        close_msg)
        print("Attempting to reconnect Polymarket Order Book WebSocket...")
        if not self._internally_closed:
            self._reconnect_attempts += 1
            if self._reconnect_attempts > self._max_reconnect_attempts:
                logging.error('Maximum reconnect attempts reached for Polymarket Order Book WebSocket. Giving up.')
                return
            time.sleep(1)
            self._start_ws()
        self._allow_ping = True

    def _on_open(self, ws):
        _ = ws
        logging.info('Polymarket Order Book WebSocket opened.')
        initial_msg = json.dumps({"assets_ids": [], "type": "market"})
        self._market_ws.send(initial_msg)
        self.ping()
        self.wait_till_socket_open.set()

    @staticmethod
    def _on_error(ws, error):
        _ = ws
        throw_fuss(
            msg="POLYMARKET ORDER BOOK WEBSOCKET ERROR:\n{}".format(traceback.format_exc()),
            notify=False
        )
        macos_notification_with_custom_sound(
            title="POLYMARKET ORDER BOOK WEBSOCKET ERROR",
            message=str(error),
            sound_name="Basso"
        )

    @runAsThread
    def _start_ws(self) -> threading.Thread:
        """
        Start the WebSocket connection.
        :return:
        """
        logging.info('Starting Polymarket Order Book WebSocket...')
        self._init_ws()
        wp_wrappers.start_proxy_aware_ws(
            idx='POLYMARKET',
            websocket=self._market_ws,
        )

        # the return of threading.Thread comes from @runAsThread ==> allows .join() if needed
        # returns here will be ignored

    ##############################################
    # Message Handlers & Logic
    ##############################################

    def _handle_order_book_message(self, message: dict) -> None:
        event_type = message.get('event_type')
        asset_id = message.get('asset_id')

        if not asset_id:
            # Handle price_change multi-asset
            if event_type == 'price_change' and 'price_changes' in message:
                for change in message['price_changes']:
                    self._update_order_book(change['asset_id'], change)
            return

        if event_type == 'book':
            # Snapshot: bids descending, asks ascending
            self._asset_id_to_order_book[asset_id] = {
                'bids': sorted(
                    message['bids'], key=lambda x: float(x['price']), reverse=True
                ),
                'asks': sorted(
                    message['asks'], key=lambda x: float(x['price'])
                )
            }

        elif event_type == 'price_change':
            # Single-asset delta
            self._update_order_book(asset_id, message)

        # Callback with a full book
        if self._order_book_update_callback:
            self._order_book_update_callback({
                asset_id: self.order_book_for_asset_id(asset_id),
                'timestamp': message['timestamp']
            })

    def _update_order_book(self, asset_id: str, change: dict) -> None:
        """Apply delta: add/update size at price, or delete if size=0."""
        if asset_id not in self._asset_id_to_order_book:
            return

        book = self._asset_id_to_order_book[asset_id]
        price = change['price']
        size = float(change['size']) if change['size'] != '0' else 0
        side = 'bids' if change['side'] == 'BUY' else 'asks'

        # Build price->size dict from the current list of dicts
        price_to_size = {level['price']: float(level['size']) for level in book[side]}

        if size == 0:
            price_to_size.pop(price, None)
        else:
            price_to_size[price] = size

        # Rebuild sorted list of dicts
        book[side] = sorted(
            [{'price': p, 'size': str(s)} for p, s in price_to_size.items()],
            key=lambda x: float(x['price']), reverse=(side == 'bids')
        )

    def subscribe_to_asset_id(self, asset_id: str):
        self._market_ws.send(json.dumps({
            "assets_ids": [asset_id],
            "type": "market",
            "operation": "subscribe"
        }))

    def unsubscribe_from_asset_id(self, asset_id: str):
        """
        Unsubscribe from order book updates for a specific asset ID.
        This will remove the order book from the internal state. It will no
        longer be tracked.

        :param asset_id: The asset ID to unsubscribe from.
        :return:
        """
        self._market_ws.send(json.dumps({
            "assets_ids": [asset_id],
            "type": "market",
            "operation": "unsubscribe"
        }))
        if asset_id in self._asset_id_to_order_book:
            del self._asset_id_to_order_book[asset_id]

    def subscribe_to_market(self, market: pm_types.PolymarketEvent):
        """
        Subscribe to order book updates for all asset IDs in a market.
        :param market: The PolymarketEvent market to subscribe to.
        :return:
        """
        if len(market.markets) > 1:
            logging.warning('Market has multiple sub-markets; This is unexpected behavior.')
            for m in market.markets:
                logging.warning('Sub-market: %s', m)

        for asset in market.markets[0].clobTokenIds:
            self.subscribe_to_asset_id(asset.id)

    @runAsThread
    def _defer_restore_state(self):
        """
        Waits for `wait_till_first_pong` to be cleared, then restores WebSocket subscriptions
        to previously subscribed asset IDs. Should only be called internally after a disconnect.
        :return:
        """
        pass

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
            self._start_ws().join()
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
        logging.info('Polymarket Order Book WebSocket highest recorded: %.2f msgs/sec in any 10 second window.', highest_msgs_per_sec)


    @runAsThread
    def _debug_print_stats_loop(self):
        while True:
            self.print_stats()
            time.sleep(10)

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
        print('-'*100)


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