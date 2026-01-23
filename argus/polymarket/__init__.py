"""
Refreshed Polymarket Dispatcher based on the polymarket_direct module. For the old version
see https://github.com/The-Sal/Argus/tree/legacy/polymarket-dispatcher

The below code removes the entire old stub with a new implementation based on polymarket_direct.
In a future version this documentation referencing the old dispatcher will be removed.
"""
import os
import socket
from utils3.networking.sockets import Server
from argus._argus_utils import Introspective
from argus.polymarket_direct import rest, EnhancedPM


class PolymarketDispatcher(Server, Introspective):
    def __init__(self, private_key: str = None, proxy_funder: str = None, host="localhost", port=8765):
        if private_key is None:
            private_key = os.environ['POLYMARKET_PRIVATE_KEY']

        if proxy_funder is None:
            proxy_funder = os.environ['POLYMARKET_PROXY_FUNDER']

        super().__init__(
            on_recv=self._on_recv,
            on_disconnect=lambda *args: print("PolymarketDispatcher: Disconnected from client.", args),
            host=host,
            port=port,
        )
        Introspective.__init__(self)

        # All the below are already registered with WireProxy system
        self.epm = EnhancedPM()
        self.rest_api = rest.PolyRestAPI(private_key=private_key, proxy_funder=proxy_funder,
                                         fatal_callback=self._on_fatal_error)
        self.account_updates = rest.PolyMarketAccountEventWss(auth=self.rest_api.credentials)

    #######################################
    # Callbacks
    #######################################
    def _on_recv(self, client_socket: socket.socket, address, data: bytes):
        pass

    def _on_fatal_error(self, error: dict):
        pass
