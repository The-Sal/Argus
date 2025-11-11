"""
A direct integration with Polymarket without requiring a Dispatcher, for dispatcher-based architecture see (argus.polymarket).
This module will have a different API than the dispatcher-based one as it uses 'EnhancedPM' class, which is an extension of the
py_clob_client's ClobClient to provide critical functionality missing from the base class. This module also
uses different data models unlike the dispatcher-based one, which works through PMarket class. The reason for this
is that the original dispatcher assumed the python-client provided by Polymarket 'py_clob_client' was complete
and sufficient, which is not the case. This module aims to fill in those gaps by integrating reverse engineered
API endpoints as well as synthesizing the various Polymarket 'official' sources.

The terminology between the two modules differs heavily, in this module we refer to 'events' as the top-level
entity, which contains one or more 'markets' (usually just one). Each market has multiple 'outcomes', each outcome
has a corresponding CLOB token ID. The events in this module are also sourced from a different API than the dispatcher-based
one, which is called ClobClients 'get_markets' method to retrieve events/markets these markets were almost
always closed and or 0.99:1 which is not useful for live trading or data analysis. Moreover, Polymarket have multiple
APIs for the same data, with different levels of completeness and latency. This module uses a mixture of these APIs
to provide results. This module also supports a 'dry' mode where you do NOT need to provide credentials and
supports real-time market data subscriptions via WebSocket. The keys are usually only for order placement.

"""
import copy
import json
import time
import requests
import threading
from argus import throw_fuss
from utils3 import runAsThread
from websocket import WebSocketApp
from py_clob_client.client import ClobClient
from argus.polymarket import fCache, DomainCache
from argus.polymarket_direct._types import PolymarketEvent

dCache = DomainCache(domain='polymarket.direct', cache=fCache)

#############################################
# ENHANCED ENDPOINTS
#############################################
endpoints = {
    'events': "https://gamma-api.polymarket.com/events?order=id&ascending=false&closed=false&limit={}&offset={}",
}


class EnhancedPM(ClobClient):
    """
    An enhanced version of the ClobClient from py_clob_client with additional functionality.
    This class adds methods to fetch market data, user positions, and other critical information
    that is not available in the base ClobClient.
    """

    def __init__(self, private_key, proxy_funder,
                 host='https://clob.polymarket.com',
                 chain_id=137,
                 order_book_depth=1, dry_mode=False):

        if not dry_mode:
            super().__init__(
                host,
                key=private_key,
                chain_id=chain_id,
                signature_type=1,
                funder=proxy_funder,
            )
            self.set_api_creds(self.create_or_derive_api_creds())

        self.bd = order_book_depth
        self.user_ws = WebSocketApp('wss://ws-subscriptions-clob.polymarket.com/ws/user')
        self.session = requests.Session()
        self.idx_to_callback = {}
        self.ws_messages = []
        self._write_messages_to_file()
        self.ws_errors = 0
        self.market_ws: WebSocketApp = None
        self.init_market_ws()
        self._internally_closed = False


        # This should be reset everytime after use. It will be blocked
        # when you call init_market_ws until the ws connection is open.
        self.market_open_semaphore = threading.Semaphore(0)

    ############################################
    # NON-PUBLIC METHODS
    ############################################

    def init_market_ws(self):
        self.market_open_semaphore = threading.Semaphore(0)
        self.market_ws = WebSocketApp('wss://ws-subscriptions-clob.polymarket.com/ws/market',
                                      on_open=self._on_ws_open,
                                      on_message=self._on_ws_message,
                                      on_error=self.on_error,
                                      on_close=self._on_ws_close)

    ############################################
    # WSS METHODS
    ############################################
    def _on_ws_open(self, ws):
        print("Market WebSocket Opened")
        self.market_open_semaphore.release()

    def on_error(self, ws, error):
        throw_fuss(
            msg=f"WebSocket Error: {error}",
            title="Polymarket WebSocket Error"
        )

    def _on_ws_close(self, ws, close_status_code, close_msg):
        if self._internally_closed:
            return
        throw_fuss(
            msg=f"Market WebSocket Closed, attempting to reconnect... and resubscribe to markets. attempts: {self.ws_errors}",
            title="Polymarket WebSocket Closed"
        )
        self.ws_errors += 1
        if self.ws_errors > 5:
            throw_fuss(
                msg=f"Market WebSocket Failed to reconnect after {self.ws_errors} attempts, giving up.",
                title="Polymarket WebSocket Failed"
            )
            return

        self.restart_ws_connections()
        time.sleep(2)
        if self.idx_to_callback:
            self.market_ws.send(json.dumps({
                'assets_ids': list(self.idx_to_callback.keys()),
                'type': 'market'
            }))

    def _on_ws_message(self, ws, message):
        try:
            data = json.loads(message)
            self.ws_messages.append(data)
        except Exception as e:
            print("Error parsing WebSocket message:", e)
            data = message
            self.ws_messages.append(data)
            print(data, len(data), type(data))
            return

        if not isinstance(data, dict):
            print("Error parsing WebSocket message:", data)
            return

        changes = data.get('price_changes', [])
        for change in changes:
            asset_id = change.get('asset_id')
            callback = self.idx_to_callback.get(asset_id)
            if callback is None:
                raise ValueError(f"No callback found for asset_id: {asset_id}")
            callback(change)

    @runAsThread
    def _write_messages_to_file(self, filename='ws_messages.fk'):
        while True:
            time.sleep(1)
            with open(filename, 'w') as f:
                for msg in self.ws_messages:
                    f.write(str(msg) + '\n')

    ############################################
    # PUBLIC METHODS
    ############################################

    def fetch_events(self, offset=0, limit=20, debug_raw_callback=None) -> list[PolymarketEvent]:
        url = endpoints['events'].format(limit, offset)
        response = self.session.get(url)
        response.raise_for_status()
        returns = []
        for event in response.json():
            try:
                if debug_raw_callback:
                    debug_raw_callback(event)
                v = PolymarketEvent.from_dict(event)
                returns.append(v)
            except Exception as e:
                print("Error parsing event:", e)

        return returns


    def restart_ws_connections(self):
        """
        You should call this function often to ensure the ws connections are alive.
        :return:
        """
        print('Re-initializing market ws for subscription...')
        self._internally_closed = True
        self.market_ws.close()
        self.init_market_ws()
        self.start_market_ws()
        self.market_open_semaphore.acquire()
        self._internally_closed = False

    # Subscribe to real-time market data via a callback function
    def subscribe_to_market_data(self, asset_ids, callback):
        """
        :param asset_ids: A list of asset IDs to subscribe to.
        :param callback: A callback function that takes a single argument (the market data update).
        :return:
        """
        for idx in asset_ids:
            self.idx_to_callback[idx] = callback
        self.market_ws.send(json.dumps({
            'assets_ids': asset_ids,
            'type': 'market'
        }))

    def unsubscribe_from_market_data(self, asset_id):
        """
        Unsubscribe from real-time market data for the given market IDs.
        Due to how polymarket's ws works, there is no actual unsubscribing, instead
        we set the callback to a no-op lambda function.

        :param asset_id: The asset IDs to unsubscribe from.
        :return:
        """
        for idx in asset_id:
            if idx in self.idx_to_callback:
                self.idx_to_callback[idx] = lambda x: None

    @runAsThread
    def start_market_ws(self):
        self.market_ws.run_forever()


if __name__ == '__main__':
    from argus.polymarket_direct._example import example_usage

    example_usage()
