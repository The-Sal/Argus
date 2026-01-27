"""
Refreshed Polymarket Dispatcher based on the polymarket_direct module. For the old version
see https://github.com/The-Sal/Argus/tree/legacy/polymarket-dispatcher

The below code removes the entire old stub with a new implementation based on polymarket_direct.
In a future version this documentation referencing the old dispatcher will be removed.
"""
import os
import json
import socket
import logging
import threading
from argus.polymarket_direct import rest
from utils3 import runAsThread, assertTypes
from utils3.networking.sockets import Server
from argus._argus_utils import Introspective
from argus.capital import decode_multiple_packets, encode_packet


class PolyMarketDispatcherError(Exception):
    pass


class InvalidArgumentError(PolyMarketDispatcherError):
    pass


class UnRegisteredClientError(PolyMarketDispatcherError):
    pass


class SocketWrapper:
    """
    A simple wrapper around a socket that adds an identifier.
    """

    def __init__(self, sock: socket.socket, idx: str):
        self.socket = sock
        self.id = idx

    def sendall(self, data: bytes):
        self.socket.sendall(data)

    def recv(self, bufsize: int) -> bytes:
        return self.socket.recv(bufsize)


class DoubleSocketWrapper:
    """
    A wrapper around a pair of sockets: control and market data.
    """

    @assertTypes([SocketWrapper, SocketWrapper], class_method=True, auto_convert=False)
    def __init__(self, control_socket: SocketWrapper, market_socket: SocketWrapper):
        self.control_socket = control_socket
        self.market_socket = market_socket
        self.idx = control_socket.id

    def __eq__(self, other):
        if not isinstance(other, DoubleSocketWrapper):
            return False
        return self.idx == other.idx

    def __hash__(self):
        return hash(self.idx)

    def send_control(self, data: bytes):
        self.control_socket.sendall(data)

    def recv_control(self, bufsize: int) -> bytes:
        return self.control_socket.recv(bufsize)

    def send_market(self, data: bytes):
        self.market_socket.sendall(data)


class SocketsRegistry:
    """
    This class maintains a mapping between control sockets and market data sockets.
    It allows registering pairs of sockets so that given a control socket, one can
    retrieve the corresponding market data socket.

    """

    def __init__(self):
        self._thread_lock = threading.Lock()

        self._orphaned_ids = {}
        self.double_sockets: set[DoubleSocketWrapper] = set()

    def register_pair(self, control_socket: SocketWrapper, market_socket: SocketWrapper):
        with self._thread_lock:
            if control_socket is None and market_socket is not None:
                # orphaned market socket
                market_socket_id = market_socket.id
                if market_socket_id not in self._orphaned_ids:
                    self._orphaned_ids[market_socket_id] = market_socket
                else:
                    # this id already exists, meaning the control socket must have already been registered
                    existing_market_socket = self._orphaned_ids[market_socket_id]
                    self.double_sockets.add(
                        DoubleSocketWrapper(control_socket=existing_market_socket, market_socket=market_socket))
                    del self._orphaned_ids[market_socket_id]
            elif market_socket is None and control_socket is not None:
                # orphaned control socket
                control_socket_id = control_socket.id
                if control_socket_id not in self._orphaned_ids:
                    self._orphaned_ids[control_socket_id] = control_socket
                else:
                    # this id already exists, meaning the market socket must have already been registered
                    existing_control_socket = self._orphaned_ids[control_socket_id]
                    self.double_sockets.add(
                        DoubleSocketWrapper(control_socket=control_socket, market_socket=existing_control_socket))
                    del self._orphaned_ids[control_socket_id]
            else:
                # both sockets are provided
                self.double_sockets[control_socket] = market_socket


class PolymarketDispatcher(Introspective):
    def __init__(self, private_key: str = None, proxy_funder: str = None,
                 host="localhost", control_port=9972, market_data_port=9973):
        super().__init__()
        if private_key is None:
            private_key = os.environ['POLYMARKET_PRIVATE_KEY']

        if proxy_funder is None:
            proxy_funder = os.environ['POLYMARKET_PROXY_FUNDER']

        self.clob_ids_to_clients = {}
        self.clients_to_idxs = {}

        self.control_server = Server(
            on_recv=self._on_recv,
            on_disconnect=lambda *args: print("PolymarketDispatcher: Disconnected from client.", args),
            host=host,
            port=control_port,
        )

        # does not respond to anything.
        self.market_data_server = Server(
            on_connect=self._mkt_sock_on_connect,
            on_disconnect=lambda *args: logging.info("Market data socket disconnected from client: %s", args),
            host=host,
            port=market_data_port,
        )

        self.sockets_registry = SocketsRegistry()

        # All the below are already registered with WireProxy system
        self.market_data = rest.PolyMarketOrderBookWss(order_book_update_callback=self._order_book_update_callback)
        self.rest_api = rest.PolyRestAPI(private_key=private_key, proxy_funder=proxy_funder,
                                         fatal_callback=self._on_fatal_error)
        self.account_updates = rest.PolyMarketAccountEventWss(auth=self.rest_api.credentials)

    #######################################
    # Callbacks
    #######################################
    def _on_recv(self, client_socket: socket.socket, address, data: bytes):
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

    # This happens only once per market data socket connection without connecting
    # and sending an identification message, this socket will never be paired or get used.
    @runAsThread
    def _mkt_sock_on_connect(self, client_socket: socket.socket, address):
        """
        The first message must be an identification message that pairs
        the market data socket and the control socket. The identification message
        can be anything as long as it's unique per client and at least
        10 bytes long and can be represented as utf-8.

        :param client_socket: The client socket
        :param address: The client address
        :return:
        """
        read = client_socket.recv(9999)
        mkt_socket_id = read.decode('utf-8')
        print(f"PolymarketDispatcher: Market data socket connected with id {mkt_socket_id} from {address}")
        self.sockets_registry.register_pair(control_socket=None,
                                            market_socket=SocketWrapper(client_socket, mkt_socket_id))

    def run_all(self):
        runAsThread(self.control_server.start)()
        runAsThread(self.market_data_server.start)()

    def _handle_client_message(self, client_socket: socket.socket, address: tuple[str, int], content: dict):
        _ = address
        action = content.get('action', None)
        data = content.get('data', None)
        if action is None:
            raise InvalidArgumentError("Received message without action field.")

        # check if they are registered
        if client_socket not in self.clients_to_idxs:
            # the first message must be registration
            if action != 'register':
                raise UnRegisteredClientError("First message from client must be 'register' action.")
            client_idx = data.get('client_idx', None)
            if client_idx is None:
                raise InvalidArgumentError(
                    "PolymarketDispatcher: Registration message must contain 'client_idx' field in data.")

            print('[handle_client_message] Registering client with idx:', client_idx)
            self.clients_to_idxs[client_socket] = client_idx
            self.sockets_registry.register_pair(
                control_socket=SocketWrapper(client_socket, client_idx),
                market_socket=None
            )

        functions_available = {
            # Market Data Subscriptions
            'subscribe': self._handle_subscribe,
            'unsubscribe': self._handle_unsubscribe,

            # Market Data Requests
            'fetch_all_markets': self._handle_fetch_all_markets,
            'fetch_market_by_clob_id': self._handle_fetch_market_by_clob_id,
            'search_markets': self._handle_search_markets,

            # Order Management
            'place_order': self._handle_place_order,
            'cancel_order': self._handle_cancel_order,
            'get_order_status': self._handle_get_order_status,
            'get_orders': self._handle_get_orders,
            'get_balance': self._handle_get_balance,

            # Utilities
            'ping': self._handle_ping,
            'am_i_paired': self._handle_am_i_paired,
        }

        func = functions_available.get(action, None)
        if func is None:
            raise InvalidArgumentError(f"Unknown action '{action}' received from client.")

        args = data if data is not None else {}
        if len(args) > 0:
            response = func(**args)
        else:
            response = func()
        
        return response

    ########################################
    # Utilities Handlers
    ########################################
    @staticmethod
    def _handle_ping():
        response = 'pong'
        return response

    def _handle_am_i_paired(self, client_idx: str):
        for double_socket in self.sockets_registry.double_sockets:
            if double_socket.idx == client_idx:
                return True
        return False


if __name__ == '__main__':
    dispatcher = PolymarketDispatcher()
    dispatcher.run_all()
    input("Polymarket Dispatcher running. Press Enter to exit...\n")
