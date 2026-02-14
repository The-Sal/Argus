"""
Refreshed Polymarket Dispatcher based on the polymarket_direct module. For the old version
see https://github.com/The-Sal/Argus/tree/legacy/polymarket-dispatcher

The below code removes the entire old stub with a new implementation based on polymarket_direct.
In a future version this documentation referencing the old dispatcher will be removed.

IMPORTANT — Account Update Delivery Requirement:
    Real-time account events (order PLACEMENT, CANCELLATION, MATCH, etc.) are
    only broadcast to client sockets that have an active market data subscription.
    A client that connects and issues order management commands (place_order,
    cancel_order, get_order_status, etc.) WITHOUT first subscribing to at least
    one asset via the 'subscribe' action will NEVER receive account_update pushes,
    even though the dispatcher's internal WebSocket is receiving them from the CLOB.

    This is because the dispatcher tracks connected clients through the
    RoutingHelper's socket set, which is only populated when a client subscribes
    to market data.  If your workflow depends on receiving account lifecycle
    events, you MUST subscribe to at least one asset_id before placing orders.
"""
import os
import json
import time
import tqdm
import socket
import difflib
import logging
import threading
import traceback
import dataclasses
from datetime import datetime
from utils3 import runAsThread, Timer
from argus.polymarket_direct import wss
from utils3.networking.sockets import Server
from argus.wireproxy.wrapper import BIND_ADDRESS
from argus.cache_sys import DomainCache, FastCache
from argus._argus_utils import Introspective, throw_fuss
from argus.polymarket_direct import rest, PolymarketEvent
from argus.polymarket_direct.order_types import OrderEvent
from argus.polymarket.proxy_perf import ProxyPerformanceProfiler
from argus.polymarket._classes import PolyMarketDispatcherError, InvalidArgumentError
from argus.protocol import decode_multiple_packets, encode_packet, transmit_mkt_data_with_protocol_2

# Much like it's predecessor on legacy/ this dispatcher is contained to its own cache file due to bloat.
_poly_cache = FastCache(cache_file='~/.argus/polymarket_cache.pkl')
_CACHE = DomainCache('polymarket_dispatcher_v2', cache=_poly_cache)


def print_with_name(*args, **kwargs):
    print("[{}]".format(__name__), *args, **kwargs)


class P2ConvertClass:
    """
    Implements the methods required for the P2 encoder to encode market data.
    - symbol
    - transferable_2

    Expected input market data:
    {
        '661095475084821930790589425827399710453605787397495798070750303202782280580': {
            'bids': [
                {'price': '0.75', 'size': '65'},
                {'price': '0.74', 'size': '299'},
                {'price': '0.73', 'size': '621.2'},
                {'price': '0.72', 'size': '2472'},
                {'price': '0.37', 'size': '464'},
                {'price': '0.36', 'size': '464'},
                {'price': '0.01', 'size': '2822.47'}
            ],
            'asks': [
                {'price': '0.76', 'size': '227.02'},
                {'price': '0.77', 'size': '1737.48'},
                {'price': '0.78', 'size': '335'},
                {'price': '0.79', 'size': '585'},
                {'price': '0.8', 'size': '746'},
                {'price': '0.81', 'size': '704'},
                {'price': '0.99', 'size': '4998.02'}
                ]
            },
        'timestamp': '1770251679393'
    }

    """

    def __init__(self, ticker: str, market_slug: str,
                 asset_id: str, market_data: dict, order_book_depth: int):
        self.ticker = ticker
        self.market_slug = market_slug
        self.asset_id = asset_id
        self.market_data = market_data
        self.order_book_depth = order_book_depth

    @property
    def symbol(self) -> str:
        return f"{self.ticker}-{self.market_slug}-{self.asset_id}"

    def transferable_2(self) -> bool:
        data_obj = self.market_data.get(self.asset_id, {})
        bids = data_obj.get('bids', [])[:self.order_book_depth]
        asks = data_obj.get('asks', [])[:self.order_book_depth]

        market_packet = str()

        for bid_index in range(self.order_book_depth):
            if bid_index < len(bids):
                bid = bids[bid_index]
                market_packet += f"{bid['price']},{bid['size']},"
            else:
                market_packet += "0,0,"

        for ask_index in range(self.order_book_depth):
            if ask_index < len(asks):
                ask = asks[ask_index]
                market_packet += f"{ask['price']},{ask['size']},"
            else:
                market_packet += "0,0,"

        # add the timestamp at the end and the server timestamp
        market_packet += f"{self.market_data.get('timestamp', '')},{time.time()}"
        return market_packet.encode('ascii')


class RoutingHelper:
    """
    Helper class to manage routing of market data and order subscriptions.
    You must override the subscription_expired method to handle subscription expiration logic.
    Features:
        1. Market Data Routing Table: clob_id -> list of sockets subscribed to market
        2. Order Subscriptions: socket -> list of clob_ids the socket is subscribed to
        3. Thread-safe operations using a lock
        4. Methods to add/remove sockets and manage subscriptions
        5. Properties to access the current state of sockets and subscriptions
        6. Logging for subscription management actions
    """

    def __init__(self):
        self._sockets: set[socket.socket] = set()
        self._market_data_routing_table: dict[str, list[socket.socket]] = {}  # clob_id -> list[socket.socket]
        self._order_subscriptions: dict[socket.socket, list[str]] = {}  # socket.socket -> list[clob_id]
        self._lock = threading.Lock()

    def add_socket(self, sock: socket.socket):
        with self._lock:
            self._sockets.add(sock)

    def remove_socket(self, sock: socket.socket):
        """
        Remove a socket and clean up its subscriptions.
        :param sock: The socket to remove.
        :return:
        """
        with self._lock:
            self._sockets.discard(sock)
            subscribed_clob_ids = self._order_subscriptions.pop(sock, [])
            for clob_id in subscribed_clob_ids:
                if clob_id in self._market_data_routing_table:
                    # Remove the socket from the routing table
                    self._market_data_routing_table[clob_id].remove(sock)
                    # If no more sockets are subscribed to this clob_id, remove the entry
                    if not self._market_data_routing_table[clob_id]:
                        del self._market_data_routing_table[clob_id]
                        self.subscription_expired(clob_id)

    # THIS METHOD TO BE OVERRIDDEN
    def subscription_expired(self, clob_id):
        """
        This method should be implemented to handle subscription expiration logic.
        What happens when a subscription expires? – Probably tell Ws to stop sending updates.
        :param clob_id:
        :return:
        """
        raise NotImplementedError("Subscription expiration handling not implemented.")

    def add_socket_to_subscription(self, sock: socket.socket, clob_id: str):
        """Adds socket to market data and order subscriptions"""
        with self._lock:
            if clob_id not in self._market_data_routing_table:
                self._market_data_routing_table[clob_id] = []
            if sock not in self._market_data_routing_table[clob_id]:
                self._market_data_routing_table[clob_id].append(sock)

            if sock not in self._order_subscriptions:
                self._order_subscriptions[sock] = []
            if clob_id not in self._order_subscriptions[sock]:
                self._order_subscriptions[sock].append(clob_id)

    def remove_socket_from_subscription(self, sock: socket.socket, clob_id: str):
        """Removes socket from market data and order subscriptions"""
        with self._lock:
            if clob_id in self._market_data_routing_table:
                if sock in self._market_data_routing_table[clob_id]:
                    self._market_data_routing_table[clob_id].remove(sock)
                    if not self._market_data_routing_table[clob_id]:
                        del self._market_data_routing_table[clob_id]
                        self.subscription_expired(clob_id)
                        logging.info('Market data subscription for clob_id %s has expired', clob_id)
                    else:
                        logging.info('Removed socket from market data subscription for clob_id %s', clob_id)
                else:
                    logging.warning('Tried to remove socket not subscribed to market data for clob_id %s', clob_id)
            else:
                logging.warning('Tried to remove socket from non-existent market data subscription for clob_id %s',
                                clob_id)

            if sock in self._order_subscriptions:
                if clob_id in self._order_subscriptions[sock]:
                    self._order_subscriptions[sock].remove(clob_id)
                    if not self._order_subscriptions[sock]:
                        del self._order_subscriptions[sock]
                        logging.info('Order subscriptions for socket has expired after removing clob_id %s', clob_id)
                else:
                    logging.warning('Tried to remove clob_id %s from `order_subscriptions` but not found for socket.',
                                    clob_id)
            else:
                logging.warning('Tried to remove socket from `order_subscriptions` but socket not found.')

    @property
    def sockets(self):
        with self._lock:
            return list(self._sockets)

    @property
    def market_data_routing_table(self):
        with self._lock:
            return dict(self._market_data_routing_table)

    @property
    def order_subscriptions(self):
        with self._lock:
            return dict(self._order_subscriptions)


class ArgsObject:
    """
    A simple class to hold arguments for handler functions.
    The order of 'args' is important as handler functions expect
    specific args in a certain order.
    """

    def __init__(self, sock: socket.socket, args):
        """
        The first argument is always the socket.
        The order of 'args' is important as handler functions expect specific args in a certain order.
        :param sock:
        :param args:
        """
        self.sock = sock
        self.args = args


class PolymarketDispatcher(Introspective, RoutingHelper):
    """
    TCP server that exposes Polymarket's CLOB via a P1 (JSON) control protocol
    and a P2 (binary) market data protocol.

    WARNING: Account lifecycle events (PLACEMENT, CANCELLATION, MATCH, etc.)
    are only forwarded to client sockets that have an active market data
    subscription via the 'subscribe' action.  Clients that only use order
    management actions (place_order, cancel_order, get_order_status, ...) will
    NOT receive real-time account_update pushes unless they first subscribe to
    at least one asset_id.  This is a consequence of the RoutingHelper's socket
    tracking — sockets are registered only on subscription.
    """

    def __init__(self, private_key: str = None, proxy_funder: str = None,
                 host="localhost", port=9972, profile_proxy=-1):
        """
        Initializes the PolymarketDispatcher instance to handle incoming market data, account events,
        and routing tasks across relevant components. Configures the REST API and WebSocket
        connections for managing and processing Polymarket events effectively. Ensures proper
        initialization of market caches and spawns background threads for continuous data updates.

        :param private_key: The private key used for authentication with the PolyRestAPI. Defaults
            to the value of the 'POLYMARKET_PRIVATE_KEY' environment variable if not explicitly provided.
        :type private_key: Str, optional

        :param proxy_funder: The address or identifier of the proxy funder for routing transactions
            within the Polymarket system. Defaults to the value of the 'POLYMARKET_PROXY_FUNDER'
            environment variable if not explicitly provided.
        :type proxy_funder: Str, optional

        :param host: The hostname or IP address on which the dispatcher server listens for incoming
            connections. Defaults to 'localhost'.
        :type host: Str, optional

        :param port: The port number on which the dispatcher server listens for incoming connections.
            Defaults to 9972.
        :type port: Int, optional

        :param profile_proxy: -1 (no profile), 0 (profile via proxy only), 1 (profile with proxy and local)
        :type profile_proxy: Int, optional

        """

        super().__init__()
        RoutingHelper.__init__(self)
        if private_key is None:
            private_key = os.environ['POLYMARKET_PRIVATE_KEY']

        if proxy_funder is None:
            proxy_funder = os.environ['POLYMARKET_PROXY_FUNDER']

        self.dispatcher_svr = Server(
            on_recv=self._handle_incoming_packets,
            on_disconnect=lambda *args: print("PolymarketDispatcher: Disconnected from client.", args),
            host=host,
            port=port,
        )

        # All the below are already registered with WireProxy system
        self.market_data = wss.PolyMarketOrderBookWss(order_book_update_callback=self._order_book_update_callback)
        self.rest_api = rest.PolyRestAPI(private_key=private_key, proxy_funder=proxy_funder,
                                         fatal_callback=self._on_fatal_error)
        self.account_updates = wss.PolyMarketAccountEventWss(auth=self.rest_api.credentials,
                                                             update_callback=self._account_update_callback)

        # Proxy Profiling if enabled
        if profile_proxy in [0, 1]:
            profiler = ProxyPerformanceProfiler(print_callback=print_with_name)
            profiler.run_profiling(BIND_ADDRESS, profile_proxy)
            profiler.display_table(BIND_ADDRESS, profile_proxy)

        self._market_cache_lock = threading.Lock()

        self._routing_helper = RoutingHelper()
        # str is 'ticker' for Polymarket
        self._all_markets_cache: dict[str, PolymarketEvent] = {}

        # TL;DR the P2 encoding format's ticker field
        # is formatted like <Event-Ticker><Market-Slug><Asset_id>
        # now we will get asset_id from the market data wss, but we need
        # to match the asset_id to the ticker and market index so we can route the data and also decode the market data correctly.
        self._asset_id_to_ticker = {}
        # ^^^ is locked with '_market_cache_lock' since it is only updated in the market cache refresh function and read in the market data update callback, which are both protected by the same lock.

        self._orderbook_depth = int(os.environ.get('POLYMARKET_ORDERBOOK_DEPTH', 10))

        # Configs
        self._market_cache_refresh_interval = int(os.environ.get('POLYMARKET_FULL_MARKET_CACHE_REFRESH_INTERVAL', 300))
        self._market_api_limit = 150  # Max markets per API call
        self._max_seen_markets = 6000  # Typical polymarket size

        # Make sure we have markets ready to serve
        self._update_markets_cache(invalidate_cache=False)  # load from cache or fetch fresh

        # Start background tasks
        self.market_data.run(main_thread=False)
        self.start_update_markets_cache_thread()

        logging.info("PolymarketDispatcher initialized on %s:%d", host, port)
        logging.info("Market cache refresh interval set to %d seconds", self._market_cache_refresh_interval)
        logging.info("Market API limit set to %d", self._market_api_limit)
        logging.info("Max seen markets initialized to %d", self._max_seen_markets)

    #######################################
    # Worker Threads & Functions
    #######################################

    @runAsThread
    def start_update_markets_cache_thread(self):
        """
        Periodically refresh the cache of all markets.
        :return:
        """
        while True:
            # Sleep first, to defer the first refresh and allow the initial load to complete
            time.sleep(self._market_cache_refresh_interval)
            self._update_markets_cache(invalidate_cache=True)

    # Note: The intended logic is that when the program boots, we already have a cache of markets
    # loaded from disk (if available) or freshly fetched from the API. Subsequent calls to this function with invalidate_cache=True
    # will force a refresh from the API. The invalidation call would be coming from the background thread.
    def _update_markets_cache(self, invalidate_cache: bool = False):
        """Updates markets cache; logs errors"""
        uuid_of_func = '_update_markets_cache.internal'

        if invalidate_cache:
            print_with_name('Invalidating Polymarket markets cache.')
            _CACHE.invalidate_key(_CACHE.generate_key(
                func_uuid=uuid_of_func,
            ))
        else:
            print_with_name('Loading Polymarket markets cache from disk or fetching fresh if not available.')

        @_CACHE.cache_decorator(
            func_uuid=uuid_of_func,
            expiration=60 * 60 * 3,  # 3 hours
            should_cache_function=lambda x: len(x.keys()) > 0
        )
        def fetch_all_markets_cached():
            scoped_all_markets_cache = {}
            progress = tqdm.tqdm(
                total=self._max_seen_markets,
                desc="Refreshing Polymarket markets cache",
                unit="markets",
                dynamic_ncols=True,
            )
            try:
                offset = 0
                while True:
                    markets = self.rest_api.fetch_events(
                        offset=offset,
                        limit=self._market_api_limit
                    )
                    scoped_all_markets_cache.update({market.ticker: market for market in markets})
                    offset += len(markets)
                    progress.update(len(markets))
                    progress.set_postfix({'Total Markets': len(scoped_all_markets_cache), 'Offset': offset})
                    progress.refresh()
                    if len(markets) == 0:
                        break

                progress.close()
                progress.refresh()
                time.sleep(1)  # tqdm refresh
                logging.info("Refreshed all markets cache with %d markets.", len(scoped_all_markets_cache))
                self._max_seen_markets = max(self._max_seen_markets, len(markets))
            except Exception as e:
                logging.error("Error refreshing all markets cache: %s", e)

            return scoped_all_markets_cache

        markets_cached = fetch_all_markets_cached()
        with self._market_cache_lock:
            self._all_markets_cache.update(markets_cached)

        self._build_asset_id_to_ticker_mapping()

    # ALREADY LOCKED WITH `_market_cache_lock` DO NOT use it inside with `_market_cache_lock` block
    def _build_asset_id_to_ticker_mapping(self):
        """
        Build a mapping of asset_id to ticker for a quick lookup when receiving market data updates.
        This should be called after the markets cache is updated.
        :return:
        """
        dict_asset_id_to_ticker = {}
        with self._market_cache_lock:
            for ticker, event in tqdm.tqdm(self._all_markets_cache.items(), desc="Building asset_id to ticker mapping",
                                           unit="markets", dynamic_ncols=True):
                markets = event.markets
                for index in range(len(markets)):
                    clobs: list[str] = markets[index].clobTokenIds
                    if clobs is None:
                        # logging.warning("Market %s has no clobTokenIds, skipping.", markets[index].slug)
                        continue
                    for clob_id in clobs:
                        # Store ticker and market index for later use in market data updates
                        dict_asset_id_to_ticker[clob_id] = (ticker, index)

        with self._market_cache_lock:
            self._asset_id_to_ticker.update(dict_asset_id_to_ticker)

    #######################################
    # Callbacks
    #######################################
    def _handle_incoming_packets(self, client_socket: socket.socket, address, data: bytes):
        packets = decode_multiple_packets(data)
        for packet in packets:
            content = json.loads(packet.decode('utf-8'))
            logging.debug("Received data from Polymarket client: %s", content)
            try:
                response = self._handle_client_message(client_socket, address, content)
                msg = {
                    'action': content.get('action', None),
                    'data': response,
                    'error': None
                }
                # because encoding can fail!
                response_bytes = encode_packet(json.dumps(msg).encode('utf-8'))
            except Exception as e:

                throw_fuss(
                    msg=traceback.format_exc(),
                    notify=False
                )

                msg = {
                    'action': content.get('action', None),
                    'data': None,
                    'error': str(e)
                }
                response_bytes = encode_packet(json.dumps(msg).encode('utf-8'))

            client_socket.sendall(response_bytes)

    def _on_fatal_error(self, error: dict):
        """
        Callback invoked by PolyRestAPI's fatal_decorator when a critical REST operation fails.
        Performs two actions:
            1. Logs the error to stdout via throw_fuss with notifications disabled (notify=False),
               since throw_fuss expects a str, not a dict — we format the error dict into a readable
               multi-line string containing the function name, exception, and traceback.
            2. Broadcasts a P1-encoded fatal_error message to all connected clients following the
               dispatcher's standard response format: {'action': 'fatal_error', 'data': ..., 'error': ...}.
               Dead sockets are cleaned up on send failure.

        :param error: Dict from fatal_decorator with keys:
            'function' (str), 'exception' (Exception), 'traceback' (str), 'args', 'kwargs', 'self'.
        :return:
        """
        func_name = error.get('function', 'unknown')
        exception = error.get('exception', 'unknown')
        tb = error.get('traceback', '')

        fuss_msg = (
            f"POLYMARKET DISPATCHER FATAL ERROR\n"
            f"Function: {func_name}\n"
            f"Exception: {exception}\n"
            f"Traceback:\n{tb}"
        )
        throw_fuss(fuss_msg, notify=False, title="Argus Polymarket Fatal Error")

        # Build a serializable error payload for clients (Exception objects are not JSON serializable)
        client_error_payload = {
            'function': func_name,
            'exception': str(exception),
            'traceback': tb,
        }

        error_packet = self.send_with_p1_encoding({
            'action': 'fatal_error',
            'data': client_error_payload,
            'error': str(exception)
        })

        for sock in self.sockets:
            try:
                sock.sendall(error_packet)
            except (ConnectionResetError, BrokenPipeError) as e:
                self.remove_socket(sock)
                print_with_name('Removed socket due to error while broadcasting fatal error:', e)
            except Exception as e:
                print_with_name('Unexpected error broadcasting fatal error to socket:', e)
                traceback.print_exc()

    def _order_book_update_callback(self, update: dict):
        """
        Callback for market data updates from Polymarket.
        :param update: The market data update. Contains asset_id as the key for the updated market.
        :return:
        """

        # The update dict from the order book WebSocket contains the asset_id as a key
        # alongside a 'timestamp' metadata key, e.g.:
        #   {'<asset_id>': {'bids': [...], 'asks': [...]}, 'timestamp': '177...'}
        # We filter out 'timestamp' to reliably extract the actual asset_id regardless
        # of dict key ordering.
        asset_keys = [k for k in update.keys() if k != 'timestamp']
        if len(asset_keys) != 1:
            logging.warning("Unexpected keys in market data update (expected 1 asset_id + timestamp): %s",
                            list(update.keys()))
            return

        asset_id = asset_keys[0]
        with self._market_cache_lock:
            ticker_market_index = self._asset_id_to_ticker.get(asset_id, None)
            if ticker_market_index is None:
                logging.warning("Received market data update for unknown asset_id: %s", asset_id)
                return
        ticker, market_index = ticker_market_index

        clients_to_send = []
        with self._lock:
            if asset_id in self._market_data_routing_table:
                clients_to_send = self._market_data_routing_table[asset_id]

        # If no clients are subscribed (e.g. last client disconnected between the
        # routing table read and this point), bail out early.  Continuing would
        # attempt to build a P2 packet from the update which can crash if the
        # message type (e.g., last_trade_price) doesn't carry full order book data.
        if not clients_to_send:
            logging.warning("No clients subscribed to market data for asset_id: %s, this should not be possible.",
                            asset_id)
            return

        p2_obj = self.send_market_data_with_p2_encoding(
            market_data=update,
            ticker=ticker,
            market_slug=self._all_markets_cache[ticker].markets[market_index].slug,
            asset_id=asset_id
        )

        # Broadcast P2-encoded market data to all clients subscribed to this asset_id.
        # On sent failure (dead/disconnected client), remove the socket via remove_socket()
        # which cascades cleanup through the routing table and triggers subscription_expired
        # if no clients remain for a given clob_id.
        # NOTE: remove_socket() and friends are thread-safe (all guarded by self._lock),
        # so it is safe to call from this WSS callback thread.
        for sock in clients_to_send:
            try:
                sock.sendall(p2_obj)
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                # OSError [Errno 9] Bad file descriptor occurs when the client
                # has already closed the socket but the routing table still holds
                # a reference to it (race between disconnect and this callback).
                self.remove_socket(sock)
                print_with_name('Removed dead socket while sending market data for asset_id %s: %s', asset_id, e)
            except Exception as e:
                print_with_name('Unexpected error sending market data for asset_id %s to socket: %s', asset_id, e)
                self.remove_socket(sock)
                traceback.print_exc()

    def _handle_client_message(self, sock: socket.socket, address: tuple[str, int], content: dict):
        with Timer(lambda x: print_with_name(f"Handled client message in {x:.4f} seconds: {content}")):
            _ = address
            action = content.get('action', None)
            data = content.get('data', None)
            if action is None:
                raise InvalidArgumentError("Received message without action field.")

            functions_available = {
                # Market Data Subscriptions
                'subscribe': self._handle_subscribe,
                'subscribe_to_market_by_ticker': self._handle_subscribe_to_market_by_ticker,

                'unsubscribe': self._handle_unsubscribe,
                'unsubscribe_from_market_by_ticker': self._handle_unsubscribe_from_market_by_ticker,

                'orderbook_snapshot': self._handle_orderbook_snapshot,

                # Market Data Requests
                'fetch_all_markets': self._handle_fetch_all_markets,
                'fetch_all_tickers': self._handle_fetch_all_markets_ticker,
                'fetch_market_by_ticker': self._handle_fetch_market_by_ticker,
                'search_markets': self._handle_search_markets,

                'fetch_clob_id_information': self._fetch_clob_id_information,

                # Order Management
                'place_order': self._handle_place_order,
                'cancel_order': self._handle_cancel_order,
                'get_order_status': self._handle_get_order_status,
                'get_orders': self._handle_get_orders,
                'get_balance': self._handle_get_balance,

                # Crypto Utilities
                'get_price_to_beat': self._handle_get_price_to_beat,

                # Utilities
                'ping': self._handle_ping,
                'rtt_to_exchange': self._handle_rtt_to_exchange,
            }

            func = functions_available.get(action, None)
            if func is None:
                raise InvalidArgumentError(f"Unknown action '{action}' received from client.")

            args = data if data is not None else {}
            if func is not None:
                # noinspection all
                response = func(args_obj=ArgsObject(sock, args))

            return response

    def _account_update_callback(self, update: OrderEvent):
        """
        Callback for account updates from Polymarket.
        :param update: The order event update.
        :return:
        """
        obj = self.send_with_p1_encoding(
            {
                'action': 'account_update',
                'data': update.to_dict(),
                'error': None
            }
        )

        for sock in self.sockets:
            try:
                sock.sendall(obj)
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                self.remove_socket(sock)
                print_with_name('Removed socket due to error while sending account update:', e)
            except Exception as e:
                print_with_name('Error while sending account update to socket:', e)
                print_with_name('THIS SHOULD NOT HAPPEN, INVESTIGATE!')
                traceback.print_exc()

    ########################################
    # Subscription
    ########################################
    def subscription_expired(self, clob_id):
        """
        Handle subscription expiration logic.
        :param clob_id:
        :return:
        """
        self.market_data.unsubscribe_from_asset_id(clob_id)

    def _handle_subscribe(self, args_obj: ArgsObject):
        """
        Handle subscription request from a client.
        :param args_obj: ArgsObject containing the socket and arguments.
            Args is expected to be a list of clob_ids to subscribe to.
        :return:
        """
        sock = args_obj.sock
        self.add_socket(sock)
        subscribed = []
        failed = []
        for clob_id in args_obj.args:
            try:
                self.add_socket_to_subscription(sock, clob_id)
                self.market_data.subscribe_to_asset_id(clob_id)
                subscribed.append(clob_id)
            except Exception as e:
                failed.append(clob_id)
                print_with_name("Error subscribing to clob_id {}: {}".format(clob_id, e))
                traceback.print_exc()

        return {
            'subscribed': subscribed,
            'failed': failed
        }

    def _handle_unsubscribe(self, args_obj: ArgsObject):
        """
        Handle unsubscription request from a client.
        :param args_obj: ArgsObject containing the socket and arguments.
            Args is expected to be a list of clob_ids to unsubscribe from.
        :return:
        """
        sock = args_obj.sock
        unsubscribed = []
        failed = []
        for clob_id in args_obj.args:
            try:
                self.remove_socket_from_subscription(sock, clob_id)
                unsubscribed.append(clob_id)
            except Exception as e:
                failed.append(clob_id)
                print_with_name("Error unsubscribing from clob_id {}: {}".format(clob_id, e))
                traceback.print_exc()

        return {
            'unsubscribed': unsubscribed,
            'failed': failed
        }

    def _handle_subscribe_to_market_by_ticker(self, args_obj: ArgsObject):
        """
        Subscribe to all clob_ids for a market identified by its ticker.
        Note: This subscribes to all submarkets of the event that may not be desired
        use `_handle_subscribe` with specific clob_ids for more granular control.
        :param args_obj: Where args_obj.args[0] is expected to be the ticker string of the market to subscribe to.
        :return:
        """

        sock = args_obj.sock
        ticker = args_obj.args[0]
        market = self._all_markets_cache.get(ticker, None)
        if market is None:
            raise PolyMarketDispatcherError(f"Market with ticker '{ticker}' not found for subscription.")

        subscribed = []
        failed = []
        for market_index in range(len(market.markets)):
            clobs: list[str] = market.markets[market_index].clobTokenIds
            if clobs is None:
                logging.warning("Market %s has no clobTokenIds, skipping subscription for this submarket.",
                                market.markets[market_index].slug)
                continue
            for clob_id in clobs:
                try:
                    self.add_socket_to_subscription(sock, clob_id)
                    self.market_data.subscribe_to_asset_id(clob_id)
                    subscribed.append(clob_id)
                except Exception as e:
                    failed.append(clob_id)
                    print_with_name("Error subscribing to clob_id {}: {}".format(clob_id, e))
                    traceback.print_exc()

        return {
            'subscribed': subscribed,
            'failed': failed
        }

    def _handle_unsubscribe_from_market_by_ticker(self, args_obj: ArgsObject):
        """
        Unsubscribe from all clob_ids for a market identified by its ticker.
         Note: This unsubscribes from all submarkets of the event that may not be desired
            use `_handle_unsubscribe` with specific clob_ids for more granular control.
        :param args_obj:
        :return:
        """

        sock = args_obj.sock
        ticker = args_obj.args[0]
        market = self._all_markets_cache.get(ticker, None)
        if market is None:
            raise PolyMarketDispatcherError(f"Market with ticker '{ticker}' not found for unsubscription.")

        unsubscribed = []
        failed = []
        for market_index in range(len(market.markets)):
            clobs: list[str] = market.markets[market_index].clobTokenIds
            if clobs is None:
                logging.warning("Market %s has no clobTokenIds, skipping unsubscription for this submarket.",
                                market.markets[market_index].slug)
                continue
            for clob_id in clobs:
                try:
                    self.remove_socket_from_subscription(sock, clob_id)
                    unsubscribed.append(clob_id)
                except Exception as e:
                    failed.append(clob_id)
                    print_with_name("Error unsubscribing from clob_id {}: {}".format(clob_id, e))
                    traceback.print_exc()

        return {
            'unsubscribed': unsubscribed,
            'failed': failed
        }

    ########################################
    # Market Data Requests
    #########################################

    # Warning: This is a chunky method it sends a LOT of data,
    # use `_handle_fetch_all_markets_ticker` instead if possible
    def _handle_fetch_all_markets(self, args_obj: ArgsObject):
        """
        Handle request to fetch all markets.
        :param args_obj: ArgsObject containing the socket and arguments.
            Args is expected to be empty.
        :return:
        """
        _ = args_obj
        markets = self._all_markets_cache
        return [market.to_dict() for market in markets.values()]

    def _handle_fetch_all_markets_ticker(self, args_obj: ArgsObject):
        """
        Handle request to fetch all market tickers.
        :param args_obj: ArgsObject containing the socket and arguments.
            Args[0] is expected to be the limit of tickers to return (optional, default 100).
            Args[1] is expected to be the offset for pagination (optional, default 0).
        :return:
        """
        _ = args_obj
        markets = self._all_markets_cache

        offset = 0
        limit = 100
        if len(args_obj.args) > 1:
            limit = int(args_obj.args[0])
            offset = int(args_obj.args[1])
        elif len(args_obj.args) > 0:
            limit = int(args_obj.args[0])

        items = list(markets.keys())
        max_items = len(items)

        max_limit = min(limit, max_items)
        if offset >= max_items:
            return []

        return items[offset:offset + max_limit]

    def _handle_fetch_market_by_ticker(self, args_obj: ArgsObject):
        """
        Handle a request to fetch a market by ticker.
        :param args_obj: ArgsObject containing the socket and arguments.
            [0] it is expected to be the ticker string.
        :return:
        """
        try:
            ticker = args_obj.args[0]
        except IndexError:
            raise InvalidArgumentError("Ticker argument is required for fetch_market_by_ticker.")
        market = self._all_markets_cache.get(ticker, None)
        if market is None:
            raise PolyMarketDispatcherError(f"Market with ticker '{ticker}' not found.")
        return market.to_dict()

    def _handle_search_markets(self, args_obj: ArgsObject):
        """
        Handle a request to search markets by keyword.
        :param args_obj: ArgsObject containing the socket and arguments.
            [0] is expected to be the search keyword string.
            [1] optional is the limit of results to return (default 10).

        Returns only the tickers of matching markets.
        :return:
        """
        sorted_markets = sorted(
            self._all_markets_cache.keys(),
            key=lambda x: difflib.SequenceMatcher(None, args_obj.args[0], x).ratio(),
            reverse=True
        )
        limit = 10
        if len(args_obj.args) > 1:
            try:
                limit = int(args_obj.args[1])
            except ValueError:
                pass
        return sorted_markets[:limit]

    def _handle_orderbook_snapshot(self, args_obj: ArgsObject):
        """
        Trigger an orderbook snapshot for a given clob_id(s). The data will come over the normal
        P2 channels. This is from a cache, NOT a live request to the CLOB. The endpoint
        is designed for stale markets that are already SUBSCRIBED to get a snapshot on demand.
        The endpoint will trigger a push of the latest order book with timestamp 0, which clients
        can identify as an on-demand snapshot.

        :param args_obj: Arg[0...n] of args_obj is expected to be the clob_id to fetch the snapshot for.
        """

        clobs = args_obj.args
        successful = []
        failed = []
        for clob_id in clobs:
            try:
                self._order_book_update_callback(
                    {
                        clob_id: self.market_data.order_book_for_asset_id(asset_id=clob_id),
                        'timestamp': 0  # Clients can identify this as a snapshot by the timestamp of 0
                    }
                )
                successful.append(clob_id)
            except Exception as e:
                failed.append(clob_id)
                print_with_name("Error triggering orderbook snapshot for clob_id {}: {}".format(clob_id, e))
                traceback.print_exc()

        return {
            'successful': successful,
            'failed': failed
        }

    def _fetch_clob_id_information(self, args_obj: ArgsObject):
        """
        Gets information about a clob_id by querying the internal market cache.
        :param args_obj: [0] of args_obj is expected to be the clob_id to fetch information for.
        :return:
        """

        clob_id = args_obj.args[0]
        event = self._resolve_market_from_token_id(clob_id)
        # Find the market and outcome associated with this clob_id
        for market in event.markets:
            if market.clobTokenIds and clob_id in market.clobTokenIds:
                outcome_index = market.clobTokenIds.index(clob_id)
                if market.outcomes and isinstance(market.outcomes, list):
                    outcome = market.outcomes[outcome_index]
                else:
                    outcome = None
                return {
                    'event_name': event.title,
                    'market_name': market.question,
                    'outcome': outcome,
                    'ticker': event.ticker,
                    'market_slug': market.slug
                }

        raise PolyMarketDispatcherError(
            f"clob_id '{clob_id}' not found in any market outcomes."
        )

    def _handle_get_price_to_beat(self, args_obj: ArgsObject):
        """
        Handle request to get the price to beat for an Up/Down market.

        This method implements a dual-strategy approach to fetch the price to beat, ensuring maximum
        reliability by attempting multiple methods in sequence. Both methods MUST be tried before
        returning an error to the user.

        DUAL METHOD STRATEGY:
        --------------------

        METHOD 1 - Frontend HTML Scraper (Primary):
            Uses UnsafePolyMarket.get_price_to_beat(slug) to scrape the price directly from
            Polymarket's frontend HTML. This method:
            - Makes an HTTP GET request to https://polymarket.com/event/{market_slug}
            - Parses the embedded JSON in the HTML page props to extract 'openPrice'
            - Is more stable as it doesn't require forging API tokens or special headers
            - Benefits from frontend caching via @_unsafe_api_cache.cache_decorator
            - May fail if Polymarket changes their HTML structure or if the market slug is invalid

        METHOD 2 - Crypto Price API (Fallback):
            Uses UnsafePolyMarket.build_crypto_price_url_and_get_price() as a fallback when
            the scraper fails. This method:
            - Extracts metadata from the market (crypto symbol, variant, start/end dates)
            - Builds a direct API URL to Polymarket's crypto price endpoint
            - Returns the 'priceToBeat' field from the JSON response
            - Requires proper parsing of market metadata from the ticker and resolution source
            - Validates all required parameters before making the API call

        METADATA EXTRACTION:
        -------------------
        The fallback method requires extracting the following from market metadata:
        - Symbol: BTC, ETH, SOL (extracted from ticker or resolutionSource URL)
        - Variant: 'fifteen', 'hourly', or 'daily' (parsed from ticker pattern like '15m', 'hour', etc.)
        - Start Date: Event start time from market.eventStartTime or market.startDate
        - End Date: Market end time from market.endDate

        EXECUTION FLOW:
        --------------
        1. Validate ticker argument
        2. Look up market in cache to get metadata
        3. Attempt METHOD 1 (scraper)
        4. If METHOD 1 fails, capture error and proceed to METHOD 2
        5. If METHOD 2 succeeds, return price; otherwise return combined error

        The input expected from the user is the ticker of the market, for example:
        "bitcoin-up-or-down-february-10-4pm-et" or "btc-updown-15m-1769111100"

        :param args_obj: ArgsObject containing the socket and arguments.
            args_obj.args[0] is expected to be the market ticker string.
        :return: float representing the price to beat
        :raises InvalidArgumentError: If ticker argument is missing
        :raises PolyMarketDispatcherError: If market not found or both methods fail
        """
        # Extract the ticker from the arguments
        try:
            ticker = args_obj.args[0]
        except IndexError:
            raise InvalidArgumentError("Ticker argument is required for get_price_to_beat.")

        # Look up the market in the cache to get metadata
        with self._market_cache_lock:
            market_event = self._all_markets_cache.get(ticker, None)

        if market_event is None:
            raise PolyMarketDispatcherError(f"Market with ticker '{ticker}' not found.")

        # Import UnsafePolyMarket here to avoid circular imports
        from argus.polymarket_direct.unsafe_api import UnsafePolyMarket, UnableToReachPolymarket

        unsafe_api = UnsafePolyMarket()

        # Get the market slug from the first market in the event
        # Most Up/Down events have a single market, so we use index 0
        if not market_event.markets or len(market_event.markets) == 0:
            raise PolyMarketDispatcherError(f"Market '{ticker}' has no submarkets.")

        market = market_event.markets[0]
        market_slug = market.slug

        # Check if market_slug is available
        if market_slug is None:
            raise PolyMarketDispatcherError(f"Market '{ticker}' has no slug defined.")

        # METHOD 1: Try the scraper first (get_price_to_beat using market slug)
        scraper_error = None
        try:
            price = unsafe_api.get_price_to_beat(market_slug)
            if price is not None:
                return price
        except UnableToReachPolymarket as e:
            scraper_error = str(e)
            logging.warning(f"Scraper method failed for ticker '{ticker}': {scraper_error}")
        except Exception as e:
            scraper_error = str(e)
            logging.warning(f"Unexpected error in scraper method for ticker '{ticker}': {scraper_error}")

        # METHOD 2: Fall back to crypto price API if scraper failed
        # We need to extract metadata from the market to build the API call
        try:
            # Extract symbol from ticker or resolution source
            symbol = self._extract_crypto_symbol(ticker, market_event)

            # Get start and end dates from market metadata FIRST
            # (needed for variant calculation)
            start_date = self._extract_start_date(market)
            end_date = self._extract_end_date(market)

            # Extract variant (fifteen, hourly, daily) from market duration
            variant = self._extract_variant(start_date, end_date)

            if symbol and variant and start_date and end_date:
                price = unsafe_api.build_crypto_price_url_and_get_price(
                    symbol=symbol,
                    variant=variant,
                    start_date=start_date,
                    end_date=end_date
                )
                if price is not None:
                    return price
            else:
                missing = []
                if not symbol:
                    missing.append("symbol")
                if not variant:
                    missing.append("variant")
                if not start_date:
                    missing.append("start_date")
                if not end_date:
                    missing.append("end_date")
                raise PolyMarketDispatcherError(
                    f"Cannot use crypto price API for ticker '{ticker}': missing {', '.join(missing)}"
                )

        except UnableToReachPolymarket as e:
            # Both methods failed - return comprehensive error
            raise PolyMarketDispatcherError(
                f"Failed to get price to beat for ticker '{ticker}'. "
                f"Scraper error: {scraper_error}. "
                f"Crypto API error: {str(e)}"
            )
        except Exception as e:
            # Unexpected error in fallback method
            raise PolyMarketDispatcherError(
                f"Failed to get price to beat for ticker '{ticker}'. "
                f"Scraper error: {scraper_error}. "
                f"Crypto API error: {str(e)}"
            )

    @staticmethod
    def _extract_crypto_symbol(ticker: str, market_event) -> str:
        """
        Extract the crypto symbol (e.g., 'BTC', 'ETH') from the ticker or resolution source.

        :param ticker: The market ticker string
        :param market_event: The PolymarketEvent object
        :return: Uppercase crypto symbol or None if cannot extract
        """
        # Map of common crypto abbreviations in tickers to symbols
        crypto_map = {
            'btc': 'BTC',
            'bitcoin': 'BTC',
            'eth': 'ETH',
            'ethereum': 'ETH',
            'sol': 'SOL',
            'solana': 'SOL',
        }

        # Try to extract from ticker first (e.g., "btc-updown-15m-1769111100")
        ticker_lower = ticker.lower()
        for key, symbol in crypto_map.items():
            if key in ticker_lower:
                return symbol

        # Try to extract from resolution source (e.g., "https://data.chain.link/streams/btc-usd")
        resolution_source = market_event.resolutionSource or ''
        if 'btc' in resolution_source.lower():
            return 'BTC'
        elif 'eth' in resolution_source.lower():
            return 'ETH'
        elif 'sol' in resolution_source.lower():
            return 'SOL'

        return None

    @staticmethod
    def _extract_variant(start_date, end_date) -> str:
        """
        Calculate the variant type (fifteen, hourly, daily) from the market duration.

        Instead of parsing human-readable slugs like "bitcoin-up-or-down-february-10-5pm-et",
        we calculate the duration between start and end dates to determine the market type.

        :param start_date: The market start datetime
        :param end_date: The market end datetime
        :return: Variant string ('fifteen', 'hourly', 'daily') or None if cannot determine
        """
        if start_date is None or end_date is None:
            return None

        try:
            # Calculate duration
            duration = end_date - start_date
            duration_minutes = duration.total_seconds() / 60

            # Determine variant based on duration
            # 15-minute markets: ~15 minutes
            if 10 <= duration_minutes <= 20:
                return 'fifteen'

            # Hourly markets: ~60 minutes (with some tolerance)
            if 50 <= duration_minutes <= 70:
                return 'hourly'

            # Daily markets: ~24 hours (1440 minutes)
            if 1380 <= duration_minutes <= 1500:
                return 'daily'

            # Log warning for unclassified durations
            logging.warning(f"Could not determine variant for duration of {duration_minutes:.1f} minutes")
            return None

        except (TypeError, AttributeError) as e:
            logging.warning(f"Error calculating variant from dates: {e}")
            return None

    @staticmethod
    def _extract_start_date(market):
        """
        Extract the start datetime from the market metadata.

        :param market: The Market object
        :return: datetime object or None
        """
        # Try eventStartTime first, then startDate, then startDateIso
        date_str = market.eventStartTime or market.startDate or market.startDateIso

        if date_str:
            try:
                # Parse ISO format datetime string
                return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                pass

        return None

    @staticmethod
    def _extract_end_date(market):
        """
        Extract the end datetime from the market metadata.

        :param market: The Market object
        :return: datetime object or None
        """
        # Try endDate first, then endDateIso
        date_str = market.endDate or market.endDateIso

        if date_str:
            try:
                # Parse ISO format datetime string
                return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                pass

        return None

    ########################################
    # Order Management
    ########################################

    def _resolve_market_from_token_id(self, token_id: str) -> PolymarketEvent:
        """
        Resolves a token_id (asset_id / clob_id) to its parent PolymarketEvent using the
        dispatcher's internal caches. The lookup path is:
            token_id -> _asset_id_to_ticker[token_id] -> (ticker, market_index)
                     -> _all_markets_cache[ticker] -> PolymarketEvent

        This is required because the REST API's place_order method needs a full PolymarketEvent
        object (for negRisk and other market metadata), but clients only send a token_id.
        Both caches are protected by _market_cache_lock.

        :param token_id: The asset_id / clob_id identifying a specific market outcome.
        :return: The PolymarketEvent object associated with this token_id.
        :raises InvalidArgumentError: If the token_id is not found in the asset-to-ticker mapping.
        :raises PolyMarketDispatcherError: If the resolved ticker is not found in the markets cache.
        """
        with self._market_cache_lock:
            ticker_market_index = self._asset_id_to_ticker.get(token_id, None)
        if ticker_market_index is None:
            raise InvalidArgumentError(
                f"token_id '{token_id}' not found in asset-to-ticker mapping. "
                f"The market may not exist or the cache may not have refreshed yet."
            )
        ticker, _ = ticker_market_index

        with self._market_cache_lock:
            market = self._all_markets_cache.get(ticker, None)
        if market is None:
            raise PolyMarketDispatcherError(
                f"Market with ticker '{ticker}' resolved from token_id '{token_id}' "
                f"was not found in markets cache."
            )
        return market

    def _handle_place_order(self, args_obj: ArgsObject):
        """
        Handle an order placement request from a client. Resolves the token_id to its parent
        PolymarketEvent from the internal cache, then delegates to the REST API's place_order.

        :param args_obj: ArgsObject containing the socket and arguments.
            Args is expected to be a dict with:
                'token_id' (str): The asset_id / clob_id to trade.
                'price' (float): The price at which to place the order.
                'size' (float): The size (number of contracts) of the order.
                'side' (str): The side of the order ('buy' or 'sell').
                'order_type' (str, optional): The order type, defaults to 'GTC'.
                    Accepted values match py_clob_client.OrderType enum names.
        :return: Dict from the CLOB API, e.g.:
            {'errorMsg': '', 'orderID': '0x...', 'takingAmount': '', 'makingAmount': '',
             'status': 'live', 'success': True}
        """
        args = args_obj.args
        token_id = args.get('token_id', None)
        if token_id is None:
            raise InvalidArgumentError("'token_id' is required for place_order.")

        price = args.get('price', None)
        if price is None:
            raise InvalidArgumentError("'price' is required for place_order.")

        size = args.get('size', None)
        if size is None:
            raise InvalidArgumentError("'size' is required for place_order.")

        side = args.get('side', None)
        if side is None:
            raise InvalidArgumentError("'side' is required for place_order.")

        market = self._resolve_market_from_token_id(token_id)

        result = self.rest_api.place_order(
            token_id=token_id,
            market=market,
            price=float(price),
            size=float(size),
            side=str(side),
        )
        return result

    def _handle_cancel_order(self, args_obj: ArgsObject):
        """
        Handle an order cancellation request from a client. Delegates directly to the
        REST API's cancel_order with the provided order_id.

        :param args_obj: ArgsObject containing the socket and arguments.
            Args is expected to be a dict with:
                'order_id' (str): The ID of the order to cancel.
        :return: Dict from the CLOB API, e.g.:
            {'not_canceled': {}, 'canceled': ['0x...']}
        """
        args = args_obj.args
        order_id = args.get('order_id', None)
        if order_id is None:
            raise InvalidArgumentError("'order_id' is required for cancel_order.")

        result = self.rest_api.cancel_order(order_id=str(order_id))
        return result

    def _handle_get_order_status(self, args_obj: ArgsObject):
        """
        Handle a request to get the status of a specific order. Delegates to the REST API's
        get_order_status and serializes the resulting PolyMarketOrder dataclass to a dict.

        :param args_obj: ArgsObject containing the socket and arguments.
            Args is expected to be a dict with:
                'order_id' (str): The ID of the order to query.
        :return: Dict representation of the PolyMarketOrder, containing fields such as:
            id, status, owner, maker_address, market, asset_id, side, original_size,
            size_matched, price, outcome, expiration, order_type, associate_trades, created_at.
        """
        args = args_obj.args
        order_id = args.get('order_id', None)
        if order_id is None:
            raise InvalidArgumentError("'order_id' is required for get_order_status.")

        order = self.rest_api.get_order_status(order_id=str(order_id))
        return dataclasses.asdict(order)

    def _handle_get_orders(self, args_obj: ArgsObject):
        """
        Handle a request to fetch all open orders for the account. Delegates to the REST API's
        get_orders and serializes each PolyMarketOrder dataclass to a dict.

        :param args_obj: ArgsObject containing the socket and arguments.
            Args is expected to be empty (no arguments required).
        :return: List of dicts, each representing a PolyMarketOrder.
        """
        _ = args_obj
        orders = self.rest_api.get_orders()
        return [dataclasses.asdict(order) for order in orders]

    def _handle_get_balance(self, args_obj: ArgsObject):
        """
        Handle a request to get the account's USDC balance. Delegates to the REST API's
        get_balance which returns the collateral balance divided by the chain divisor.

        :param args_obj: ArgsObject containing the socket and arguments.
            Args is expected to be empty (no arguments required).
        :return: Float representing the account balance in USDC.
        """
        _ = args_obj
        balance = self.rest_api.get_balance()
        return balance

    ########################################
    # Utilities
    ########################################
    @staticmethod
    def _handle_ping(args_obj: ArgsObject):
        _ = args_obj
        response = 'pong'
        return response

    def _handle_rtt_to_exchange(self, args_obj: ArgsObject) -> float:
        """
        Calculates the RTT to the exchange by measuring the time taken to
        check the account balance via the rest api.
        :param args_obj: Expects no arguments, just a trigger to perform the RTT check.
        :return: A float representing the round-trip time in seconds from the dispatcher to the exchange and back.
        """
        time_now = time.time()
        _ = args_obj
        self.rest_api.get_balance()
        time_after = time.time()
        rtt = time_after - time_now
        return rtt

    @staticmethod
    def send_with_p1_encoding(dict_data: dict) -> bytes:
        """
        Encodes a dictionary into bytes using JSON and P1 packet encoding.
        :param dict_data: The dictionary to encode.
        :return:
        """
        json_data = json.dumps(dict_data).encode('utf-8')
        packet = encode_packet(json_data)
        return packet

    def send_market_data_with_p2_encoding(self, market_data: dict, ticker: str, market_slug: str,
                                          asset_id: str) -> bytes:
        """
        Encodes market data into bytes using a custom P2 encoding format.
        The P2 encoding format's ticker field is formatted like <Event-Ticker><Market-Slug><Asset_id>.

        P2 Layout:
        ~<packet-len><ticker-len><[event-ticker][market-slug][asset_id]><[Nx (price, size) for bid]><[Nx (price, size) for bid]>L

        Control N with `POLYMARKET_ORDERBOOK_DEPTH` environment variable (default 10)

        :param market_data: The market data to encode.
        :param ticker: The event ticker for the market data.
        :param market_slug: The market slug for the market data.
        :param asset_id: The asset ID for the market data.
        :return:
        """

        return transmit_mkt_data_with_protocol_2(
            P2ConvertClass(
                ticker=ticker,
                market_slug=market_slug,
                asset_id=asset_id,
                market_data=market_data,
                order_book_depth=self._orderbook_depth
            )
        )

    @runAsThread
    def run(self):
        self.dispatcher_svr.start()

    def interactive_mode(self):
        self._interactive_ui({})


if __name__ == '__main__':
    dispatcher = PolymarketDispatcher()
    dispatcher.run()
    dispatcher.interactive_mode()
    input("Polymarket Dispatcher running. Press Enter to exit...\n")
