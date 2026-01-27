import socket
import threading
from utils3 import assertTypes

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
                self.double_sockets.add(
                    DoubleSocketWrapper(control_socket=control_socket, market_socket=market_socket)
                )