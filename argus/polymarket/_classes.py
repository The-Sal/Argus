import os
import time
import socket
import logging
import threading
from utils3 import assertTypes
from collections import OrderedDict


class PolyMarketDispatcherError(Exception):
    pass

class OrderExecutionError(PolyMarketDispatcherError):
    pass

class OrderExecutionDisabledError(OrderExecutionError):
    pass


class InvalidArgumentError(PolyMarketDispatcherError):
    pass

class CorrelationIDError(PolyMarketDispatcherError):
    pass

class CorrelationIDLengthTooLongError(CorrelationIDError):
    pass

class CorrelationIDAlreadySeenError(CorrelationIDError):
    pass

class UnableToEncodeMarketDataError(PolyMarketDispatcherError):
    pass

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
        try:
            bids = data_obj.get('bids', [])[:self.order_book_depth]
            asks = data_obj.get('asks', [])[:self.order_book_depth]
        except (AttributeError, KeyError, TypeError) as e:
            raise UnableToEncodeMarketDataError(
                f"Market data for asset_id {self.asset_id} is not in the expected format. Cannot encode. Data: {data_obj}, e={e}"
            )

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
        # Per-client sendall lock — prevents byte interleaving when multiple
        # WS shard threads broadcast to the same client socket.  Per-socket
        # granularity means a slow client A never blocks sends to client B.
        # Lazily populated by add_socket and `send_lock_for`; cleaned up by remove_socket.
        self._sendall_locks: dict[socket.socket, threading.Lock] = {}

    def send_lock_for(self, sock: socket.socket) -> threading.Lock:
        """
        Return the per-socket sendall lock, creating it on first access.
        Callers MUST hold this lock around every sock.sendall() into `sock`
        from any thread that may run concurrently with another sender.
        """
        with self._lock:
            lock = self._sendall_locks.get(sock)
            if lock is None:
                lock = threading.Lock()
                self._sendall_locks[sock] = lock
            return lock

    def add_socket(self, sock: socket.socket):
        with self._lock:
            self._sockets.add(sock)
            if sock not in self._sendall_locks:
                self._sendall_locks[sock] = threading.Lock()

    def remove_socket(self, sock: socket.socket):
        """
        Remove a socket and clean up its subscriptions.
        :param sock: The socket to remove.
        :return:
        """
        with self._lock:
            self._sockets.discard(sock)
            self._sendall_locks.pop(sock, None)
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



class CorrelationIDChecker:
    """
    A simple class to check if we've already seen this correlation ID before and raise an error if we have.
    Correlation IDs must be unique for each request and should not be reused. This is to prevent client-side
    matching engines from getting confused. This class is thread-safe and uses a lock to ensure that multiple threads
    can check correlation IDs without running into race conditions. Automatically trims the dict of seen correlation
    IDs if it exceeds a certain size to prevent memory issues.


    WARNING: This class is not going to stay in `argus.polymarket._classes` long-term. It will be moved
    into a shared utility module once other parts of the codebase need similar functionality. DO NOT use this
    class directly. polymarket/__init__.py will be updated automatically with the refactor once the class is moved.
    The point is not to depend on it via `from argus.polymarket._classes import CorrelationIDChecker`
    """

    def __init__(self):
        self.seen_correlation_ids: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()

        self._max_seen_ids = int(os.environ.get('MAX_SEEN_CORRELATION_IDS', 100_000))
        # ^^^^^^^^ ~roughly 7MB if each ID is uuid4

        self._max_id_length = int(os.environ.get('MAX_CORRELATION_ID_LENGTH', 40))
        # ^^^^^^ slightly above uuid4 length, ideally leave alone unless you have a reason to change it
        # uuid4's has 2^122 possible values, so the chance of a collision is astronomically low. Leaving
        # arbitrarily high limits will bloat the memory usage of this class.

    @assertTypes([str], auto_convert=True, class_method=True)
    def check_correlation_id(self, correlation_id: str):
        if len(correlation_id) > self._max_id_length:
            raise CorrelationIDLengthTooLongError(
                f"Correlation ID {correlation_id} is too long. Maximum length is {self._max_id_length} characters."
            )
        with self._lock:
            if correlation_id in self.seen_correlation_ids:  # O(1)
                raise CorrelationIDAlreadySeenError(
                    f"Correlation ID {correlation_id} has already been seen. Correlation IDs must be unique."
                )
            self.seen_correlation_ids[correlation_id] = None

            if len(self.seen_correlation_ids) > self._max_seen_ids:
                self._trim_seen_ids_locked()

    def _trim_seen_ids_locked(self):
        """
        Trims the oldest 50% of seen correlation IDs to free up memory.
        Must be called while self._lock is already held.
        """
        num_to_remove = len(self.seen_correlation_ids) // 2
        for _ in range(num_to_remove):
            self.seen_correlation_ids.popitem(last=False)  # O(1) — pops from front (oldest)

    def clear_seen_ids(self):
        """Clears all seen correlation IDs. Use with caution as this can lead to accepting duplicate IDs."""
        with self._lock:
            self.seen_correlation_ids.clear()