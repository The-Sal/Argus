import os
import json
import time
import logging
import requests
import threading
import traceback
from termcolor import colored
from utils3 import runAsThread
from websocket import WebSocketApp
from argus.cache_sys import DomainCache
from py_clob_client import BalanceAllowanceParams
from argus.wireproxy import wrapper as wp_wrappers
from argus.polymarket_direct import _types as pm_types
from py_clob_client.order_builder.constants import BUY, SELL
from argus._argus_utils import throw_fuss, macos_notification_with_custom_sound
from py_clob_client.client import OrderArgs, OrderType, ClobClient, PartialCreateOrderOptions
from argus.polymarket_direct._order_types import OrderException, PolyMarketOrder, TradeData, OrderEvent

# See docs/perf/rest-wss-orderbook-tuning.md for details on how we tuned the performance of the REST and WebSocket clients in this module.
if os.environ.get('POLYMARKET_ORJSON', 'false').lower() == 'true':
    import orjson as json

# This is how we built perf/rest-wss-orderbook-tuning
# from argus.__build_tools import HowLongDidThisTake
# how_long = HowLongDidThisTake('SOCKET to PRINT')
# debug_handle = open('polymarket_socket_debug.log', 'w')


REST_CACHE = DomainCache('polymarket_direct.rest')
endpoints = {
    'events': "https://gamma-api.polymarket.com/events?order=id&ascending=false&closed=false&limit={}&offset={}",
    'geo_block_test': 'https://polymarket.com/api/geoblock'
}
qw = '[{}]'.format(__name__)


class IPSafety:
    def __init__(self):
        self.KNOWN_BAD_REGIONS = ["US", "GB", "FR", "DE", "IT", "BE", "PL", "AU", "SG", "TW",
                                  "TH", "RU", "BY", "CU", "IR",
                                  "IQ", "KP", "SY", "VE", "MM", "LY", "SD", "SS", "SO",
                                  "YE", "ZW", "LB", "ET", "NI", "BI", "CF", "CD", "UM", "AE"]

        self.session = requests.Session()
        wp_wrappers.update_request_session_proxy(
            idx='POLYMARKET',
            session=self.session,
            verbose=False
        )

    def get_ip_info(self) -> dict:
        """
        Fetch IP information from the ipinfo.io service.
        :return: A dictionary containing IP information.
        """
        response = self.session.get('https://ipinfo.io/json')
        response.raise_for_status()
        return response.json()

    def is_ip_in_bad_region(self, ip_info: dict) -> bool:
        """
        Determine if the IP is located in a known bad region.
        :param ip_info: A dictionary containing IP information.
        :return: True if the IP is in a bad region, False otherwise.
        """
        country = ip_info.get('country', '')
        return country in self.KNOWN_BAD_REGIONS


# MUST be used on methods within PolyRestAPI that are critical
def fatal_decorator(func_idx):
    def decorator(func):
        def wrapper(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except Exception as e:
                traceback.print_exc()
                if self.fatal_callback:
                    self.fatal_callback({
                        'self': self,
                        'function': func_idx,
                        'args': args,
                        'kwargs': kwargs,
                        'exception': e,
                        'traceback': traceback.format_exc()
                    })
                raise

        return wrapper

    return decorator


class PolyRestAPI:
    """
    A REST API client for interacting with Polymarket's CLOB via py_clob_client and other endpoints.
    """

    def __init__(self, private_key, proxy_funder, host='https://clob.polymarket.com',
                 chain_id=137, divisor=1_000_000, fatal_callback=None):
        """
        Initialize the Polymarket REST API client.
        :param private_key: This is the private key of the Polymarket account to use for signing requests.
        :param proxy_funder: This is the address that holds the actual funds
        :param host: the host URL for the Polymarket CLOB API. (leave default)
        :param chain_id: leave default unless Polymarket changes chain
        :param divisor: TL;DR Polymarket uses 6 decimal places for account balances and other things, this
                        divisor helps convert to/from 'real' amounts.
        :param fatal_callback: In the event of an exception that is fatal when using any of the decorated
            methods, this callback will be called. It will be passed a single dictionary as an argument.
            This will update version-version and not guaranteed to be stable. Always use .get() on the dict.
            As of now the dict contains:
                - 'self': The instance of PolyRestAPI
                - 'function': The name of the function where the exception occurred
                - 'args': The positional arguments passed to the function
                - 'kwargs': The keyword arguments passed to the function
                - 'exception': The exception object that was raised
                - 'traceback': The traceback string of the exception

        """
        self.private_key = private_key
        self.proxy_funder = proxy_funder
        self.session = requests.Session()
        wp_wrappers.update_request_session_proxy(idx='POLYMARKET', session=self.session)
        self._make_httpx_clob_client()
        self.safety = IPSafety()

        if os.environ.get('POLYMARKET_NO_SAFETY_CHECK', 'false') != 'true': self.ip_safety_check()
        self.clob = ClobClient(host, key=private_key, chain_id=chain_id, signature_type=1, funder=proxy_funder)
        self.clob.set_api_creds(self._create_or_derive_api_creds())
        self._div = divisor

        self._order_cache = {
            'orders': []
        }

        self.fatal_callback = fatal_callback
        if self.fatal_callback is None:
            def default_fatal_callback(info: dict):
                print(colored(f"[{__name__}] FATAL ERROR in Polymarket REST API client: {info}", 'red',
                              attrs=['bold', 'blink']))

            self.fatal_callback = default_fatal_callback

    ###########################################
    # Utility Methods
    ###########################################

    def ip_safety_check(self):
        print(qw, 'Starting Polymarket REST API client initialization...')
        print(qw, 'Checking IP information against hardcoded geo-blocked regions via ipinfo.io...')
        ip_info = self.safety.get_ip_info()
        ip_info['ip'] = 'REDACTED'  # Redact IP for privacy in logs
        if self.safety.is_ip_in_bad_region(ip_info):
            msg = ("The IP returned from ipinfo.io compared against built-in geo-blocked regions indicates this"
                   " IP address may face issues accessing Polymarket. This maybe wrong, the next check will"
                   " attempt to connect directly to polymarket.com to verify (in the next 5s) to double check. If"
                   "this address should not leak polymarket.com to the ISP TERMINATE NOW. To automatically terminate"
                   " when here set the env `POLYMARKET_PARANOID` to `true` before running.")
            if os.environ.get('POLYMARKET_PARANOID', 'false') == 'true':
                raise RuntimeError(
                    "THIS IP IS GEO-BLOCKED ACCORDING TO HARDCODED REGIONS AND `POLYMARKET_PARANOID` IS SET TO TRUE. "
                    "TERMINATING FOR SAFETY. IP INFO: " + str(ip_info))
            else:
                print(qw, colored(msg, 'yellow', attrs=['bold', 'blink']))

        else:
            ip_info['ip'] = 'REDACTED'
            print(qw, f"IP info: {ip_info}")
            print(qw, "Proceeding to Polymarket geo-block check...")

        if os.environ.get('POLYMARKET_PROTECTION', 'true') == 'true':
            if self.check_geo_blocked():
                raise RuntimeError("The current IP is geo-blocked from accessing Polymarket.")
            else:
                print(qw, "The current IP is NOT geo-blocked from accessing Polymarket. Happy trading!")
        else:
            warning_msg = ("WARNING: YOU HAVE DISABLED POLYMARKET GEO-BLOCK PROTECTION CHECKS VIA THE "
                           "`POLYMARKET_PROTECTION` ENVIRONMENT VARIABLE (default: true). THIS WILL LEAD TO ORDERS NOT BEING REJECTED "
                           "AND POSSIBLE ISSUES WITH A ACTIVELY TRADED ACCOUNT IS LOGGED IN FROM A GEO-BLOCKED IP. "
                           "THE NEXT STEP INVOLVES DERIVING CREDENTIALS WHICH WILL EXPOSE YOUR ACCOUNT TO POLYMARKET"
                           "FROM THIS IP ADDRESS. THE PROGRAM WILL NOW SLEEP FOR 30s DO NOTHING TO PROCEED, TERMINATE OTHERWISE")
            throw_fuss(
                msg=colored(warning_msg, 'red', attrs=['bold', 'blink']),
                notify=False,
                title="WARNING: POLYMARKET GEO-BLOCK PROTECTION DISABLED"
            )
            macos_notification_with_custom_sound(
                title="WARNING: POLYMARKET GEO-BLOCK PROTECTION DISABLED",
                message=warning_msg,
                sound_name="Basso"
            )
            for i in range(30, 0, -1):
                print(qw, colored(f"Continuing in {i} seconds... terminate now to abort.", 'red', attrs=['bold']),
                      end='\r')
                time.sleep(1)
            print()

    def check_geo_blocked(self) -> bool:
        """
        Check if the current IP is geo-blocked from accessing Polymarket.
        :return: True if geo-blocked, False otherwise.
        """
        response = self.session.get(endpoints['geo_block_test'])
        response.raise_for_status()
        data = response.json()
        logging.info('Geo-block check response: %s', data)
        return data.get('blocked', False)

    @REST_CACHE.cache_decorator(
        func_uuid='_create_or_derive_api_creds',
        expiration=60 * 60 * 24,
        should_cache_function=lambda x: x is not None
    )
    def _create_or_derive_api_creds(self):
        response = self.clob.create_or_derive_api_creds()
        return response

    @staticmethod
    def _make_httpx_clob_client():
        """
        PyClobClient uses httpx for its HTTP requests; however, it does not expose a way to set up proxies.
        This function patches the internal httpx Client used by py_clob_client to use a proxy if specified
        via WireProxy.
        """
        from httpx import Client
        from py_clob_client.http_helpers import helpers

        proxy = wp_wrappers.start_proxy_and_return_bind('POLYMARKET')
        if proxy is not None:
            proxy = f'socks5://{proxy}'
        _client = Client(http2=2, proxy=proxy)
        setattr(helpers, '_http_client', _client)

    ###########################################
    # Public API Methods
    ###########################################

    def fetch_events(self, offset=0, limit=20, debug_raw_callback=None) -> list[pm_types.PolymarketEvent]:
        url = endpoints['events'].format(limit, offset)
        response = self.session.get(url)
        response.raise_for_status()
        returns = []
        for event in response.json():
            try:
                if debug_raw_callback:
                    debug_raw_callback(event)
                v = pm_types.PolymarketEvent.from_dict(event)
                returns.append(v)
            except Exception as e:
                print("Error parsing event:", e)

        return returns

    # NOTE: PyClob is the worst library ever made and has this
    # fun little code so the tick size must be a string and only these
    # otherwise raise a key error
    # ROUNDING_CONFIG: dict[TickSize, RoundConfig] = {
    #     "0.1": RoundConfig(price=1, size=2, amount=3),
    #     "0.01": RoundConfig(price=2, size=2, amount=4),
    #     "0.001": RoundConfig(price=3, size=2, amount=5),
    #     "0.0001": RoundConfig(price=4, size=2, amount=6),
    # }

    def get_tick_size(self, token_id: str):
        """
        Get the tick size for a given token ID.
        :param token_id: The ID of the token.
        :return: The tick size as a string.
        """
        tick_size = self.clob.get_tick_size(token_id)
        return tick_size

    @fatal_decorator('place_order')
    def place_order(self, token_id: str, market: pm_types.PolymarketEvent,
                    price: float, size: float, side: str, order_type: OrderType = OrderType.GTC) -> dict:
        """
        Place an order on the Polymarket CLOB.
        :param order_type: The type of the order (default: GTC).
        :param market: The market object associated with the token.
        :param token_id: The ID of the token to trade.
        :param price: The price at which to place the order.
        :param size: The size of the order.
        :param side: The side of the order ('buy' or 'sell').
        :return: A dictionary containing the result of the order placement.
            E.g. {'errorMsg': '', 'orderID': '0xxxxxx', 'takingAmount': '', 'makingAmount': '', 'status': 'live', 'success': True}
        """
        order = self.build_order(
            token_id=token_id,
            market=market,
            price=price,
            size=size,
            side=side
        )
        result = self.clob.post_order(
            order=order,
            orderType=order_type
        )
        logging.info('Order placed: %s', result)
        if result['success']:
            order_id = result['orderID']
            self._order_cache['orders'].append(result)
            self._order_cache[order_id] = {
                'token_id': token_id,
                'market': market,
                'price': price,
                'size': size,
                'side': side,
                'order_type': order_type,
                'result': result
            }
            print(qw, colored(f"Order placed successfully. Order ID: {order_id}", 'green', attrs=['bold']))
        else:
            raise OrderException(f"Failed to place order: {result.get('errorMsg', 'Unknown error')}, response={result}")

        return result

    @fatal_decorator('build_order')
    def build_order(self, token_id: str, market: pm_types.PolymarketEvent, price: float, size: float, side: str):
        tick_size = self.get_tick_size(token_id)
        mapped = {
            'buy': BUY,
            'sell': SELL
        }
        type_side = mapped.get(side.lower())
        if type_side is None:
            raise ValueError("side must be either 'buy' or 'sell'")
        order = self.clob.create_order(
            order_args=OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=type_side,
            ),
            options=PartialCreateOrderOptions(
                tick_size=tick_size,
                neg_risk=market.negRisk
            )
        )

        return order

    @fatal_decorator('cancel_order')
    def cancel_order(self, order_id: str) -> dict:
        """
        Cancel an existing order on the Polymarket CLOB.
        :param order_id: The ID of the order to cancel.
        :return: The result of the cancellation request.
            E.g. {'not_canceled': {}, 'canceled': ['0000000x00000']}
        """
        result = self.clob.cancel(order_id)
        logging.info('Order cancellation result: %s', result)
        if len(result['canceled']) > 0:
            print(qw, colored(f"Order {order_id} canceled successfully.", 'green', attrs=['bold']))
            # Remove from cache
            self._order_cache['orders'] = [
                o for o in self._order_cache['orders'] if o['orderID'] != order_id
            ]
            if order_id in self._order_cache:
                del self._order_cache[order_id]
        else:
            print(qw, colored(f"Failed to cancel order {order_id}.", 'red', attrs=['bold']))
        return result

    @fatal_decorator('get_orders')
    def get_orders(self) -> list[PolyMarketOrder]:
        """
        Get the list of orders.
        :return: A list of orders.
        """
        orders = map(lambda x: PolyMarketOrder(**x), self.clob.get_orders())
        return list(orders)

    @fatal_decorator('get_trades')
    def get_trades(self) -> TradeData:
        """
        Get the list of trades.
        :return: A TradeData object containing the list of trades.
        """
        trades = self.clob.get_trades()
        return TradeData.from_list(trades)

    @fatal_decorator('get_order_status')
    def get_order_status(self, order_id: str) -> PolyMarketOrder:
        """
        Get the status of a specific order.
        :param order_id: The ID of the order.
        :return: A PolyMarketOrder object containing the order status.
        """
        logging.info('Getting order status: %s', order_id)
        order_status = self.clob.get_order(order_id)
        return PolyMarketOrder(**order_status)

    @fatal_decorator('get_balance')
    def get_balance(self) -> float:
        """
        Get the balance of the account.
        :return: The balance as a float.
        """
        balance = float(self.clob.get_balance_allowance(BalanceAllowanceParams(asset_type='COLLATERAL'))['balance'])
        return balance / self._div

    @property
    def order_cache(self):
        """
        Get the order cache.
        :return: The order cache dictionary.
        """
        return self._order_cache

    @property
    def credentials(self):
        """
        Get the API credentials.
        :return: The API credentials dictionary.
        """
        creds = self._create_or_derive_api_creds()
        return {
            "apiKey": creds.api_key,
            "secret": creds.api_secret,
            "passphrase": creds.api_passphrase,
        }


class PolyMarketAccountEventWss:
    """
    A WebSocket that exists just to listen to account events from the Polymarket CLOB.
    This is an authorised WSS connection to Polymarket and CLOB it is SEPARATE from `EnhancedPM`
    and does NOT provide any market data or order placement functionality. It does NOT hold
    any state information.
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
            self.wait_till_first_pong.clear()
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
            logging.warning(
                'Ping thread for Polymarket Account Event WebSocket is already running. Not starting another.')
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
            self.wait_till_first_pong.clear()
            return

        # how_long.start()

        try:
            content = json.loads(message)
            # logging.info('Polymarket Order Book WebSocket message received: %s', content)

            # Handle both list and dict messages
            if isinstance(content, list):
                for msg in content:
                    self._handle_order_book_message(msg)
            else:
                self._handle_order_book_message(content)
        except json.JSONDecodeError as e:
            print('WARNING: Failed to decode Polymarket Order Book WebSocket message: "{}"'.format(message))
            raise
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
                asset_id: self.order_book_for_asset_id(asset_id)
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

    _HIDDEN_ASSET_ID = '70257161748242154417830949164492697213576535524972981809953121043413148169037'


    def ev(x):
        print(x[_HIDDEN_ASSET_ID]['bids'][0])
        print('*' * 50)


    wss = PolyMarketOrderBookWss(ev)
    wss._debug_print_stats_loop()
    wss.run(main_thread=False)
    # wait with threading event to ensure socket is open
    wss.wait_till_socket_open.wait()
    wss.subscribe_to_asset_id(
        _HIDDEN_ASSET_ID
    )
    input('Press Enter to exit...\n')
    wss.print_stats()