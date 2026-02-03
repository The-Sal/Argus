"""
Refreshed Polymarket Dispatcher based on the polymarket_direct module. For the old version
see https://github.com/The-Sal/Argus/tree/legacy/polymarket-dispatcher

The below code removes the entire old stub with a new implementation based on polymarket_direct.
In a future version this documentation referencing the old dispatcher will be removed.
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
from utils3 import runAsThread
from argus.cache_sys import DomainCache
from utils3.networking.sockets import Server
from argus._argus_utils import Introspective
from argus.polymarket_direct.rest import OrderEvent
from argus.polymarket_direct import rest, PolymarketEvent
from argus.protocol import decode_multiple_packets, encode_packet
from argus.polymarket._classes import PolyMarketDispatcherError, InvalidArgumentError



_CACHE = DomainCache('polymarket_dispatcher_v2')


def print_with_name(*args, **kwargs):
    print("[{}]".format(__name__), *args, **kwargs)


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
    def __init__(self, private_key: str = None, proxy_funder: str = None,
                 host="localhost", port=9972):
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
        self.market_data = rest.PolyMarketOrderBookWss(order_book_update_callback=self._order_book_update_callback)
        self.rest_api = rest.PolyRestAPI(private_key=private_key, proxy_funder=proxy_funder,
                                         fatal_callback=self._on_fatal_error)
        self.account_updates = rest.PolyMarketAccountEventWss(auth=self.rest_api.credentials,
                                                              update_callback=self._account_update_callback)

        self._market_cache_lock = threading.Lock()

        self._routing_helper = RoutingHelper()
        # str is 'ticker' for Polymarket
        self._all_markets_cache: dict[str, PolymarketEvent] = {}

        # Configs
        self._market_cache_refresh_interval = int(os.environ.get('POLYMARKET_FULL_MARKET_CACHE_REFRESH_INTERVAL', 300))
        self._market_api_limit = 150  # Max markets per API call
        self._max_seen_markets = 6000  # Typical polymarket size

        # Make sure we have markets ready to serve
        self._update_markets_cache(invalidate_cache=False)  # load from cache or fetch fresh

        # Start background tasks
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

    #######################################
    # Callbacks
    #######################################
    def _handle_incoming_packets(self, client_socket: socket.socket, address, data: bytes):
        packets = decode_multiple_packets(data)
        for packet in packets:
            content = json.loads(packet.decode('utf-8'))
            logging.debug("Received data from Polymarket client: %s", content)
            try:
                response = self._handle_client_message(address, content)
                msg = {
                    'action': content.get('action', None),
                    'data': response,
                    'error': None
                }
            except Exception as e:
                msg = {
                    'action': content.get('action', None),
                    'data': None,
                    'error': str(e)
                }

            response_bytes = encode_packet(json.dumps(msg).encode('utf-8'))
            client_socket.sendall(response_bytes)

    def _on_fatal_error(self, error: dict):
        pass

    def _order_book_update_callback(self, update):
        pass

    def _handle_client_message(self, sock: socket.socket, address: tuple[str, int], content: dict):
        _ = address
        action = content.get('action', None)
        data = content.get('data', None)
        if action is None:
            raise InvalidArgumentError("Received message without action field.")

        functions_available = {
            # Market Data Subscriptions
            'subscribe': self._handle_subscribe,
            'unsubscribe': self._handle_unsubscribe,

            # Market Data Requests
            'fetch_all_markets': self._handle_fetch_all_markets,
            'fetch_all_tickers': self._handle_fetch_all_markets_ticker,
            'fetch_market_by_ticker': self._handle_fetch_market_by_ticker,
            'search_markets': self._handle_search_markets,

            # Order Management
            'place_order': self._handle_place_order,
            'cancel_order': self._handle_cancel_order,
            'get_order_status': self._handle_get_order_status,
            'get_orders': self._handle_get_orders,
            'get_balance': self._handle_get_balance,

            # Utilities
            'ping': self._handle_ping,
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
            except (ConnectionResetError, BrokenPipeError) as e:
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
            Args is expected to be empty.
        :return:
        """
        _ = args_obj
        markets = self._all_markets_cache
        return list(markets.keys())

    def _handle_fetch_market_by_ticker(self, args_obj: ArgsObject):
        """
        Handle a request to fetch a market by ticker.
        :param args_obj: ArgsObject containing the socket and arguments.
            [0] is expected to be the ticker string.
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




    ########################################
    # Utilities
    ########################################
    @staticmethod
    def _handle_ping(args_obj: ArgsObject):
        _ = args_obj
        response = 'pong'
        return response

    @staticmethod
    def send_with_p1_encoding(dict_data: dict) -> bytes:
        """
        Encodes a dictionary into bytes using JSON and P1 packet encoding.
        :param dict_data: the dictionary to encode.
        :return:
        """
        json_data = json.dumps(dict_data).encode('utf-8')
        packet = encode_packet(json_data)
        return packet

    def run(self):
        self.dispatcher_svr.start()

    def interactive_mode(self):
        self._interactive_ui({})


if __name__ == '__main__':
    dispatcher = PolymarketDispatcher()
    dispatcher.run()
    dispatcher.interactive_mode()
    input("Polymarket Dispatcher running. Press Enter to exit...\n")
