import os
import time
import logging
import requests
import traceback
from termcolor import colored
from argus.cache_sys import DomainCache
from py_clob_client import BalanceAllowanceParams
from argus.polymarket_direct.safe import IPSafety
from argus.wireproxy import wrapper as wp_wrappers
from argus.polymarket_direct import _types as pm_types
from py_clob_client.order_builder.constants import BUY, SELL
from argus._argus_utils import throw_fuss, macos_notification_with_custom_sound
from argus.polymarket_direct.order_types import OrderException, PolyMarketOrder, TradeData
from py_clob_client.client import OrderArgs, OrderType, ClobClient, PartialCreateOrderOptions


REST_CACHE = DomainCache('polymarket_direct.rest')
endpoints = {
    'events': "https://gamma-api.polymarket.com/events?order=id&ascending=false&closed=false&limit={}&offset={}",
    'geo_block_test': 'https://polymarket.com/api/geoblock'
}
qw = '[{}]'.format(__name__)





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



