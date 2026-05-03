import os
import time
import logging
import requests
import functools
import traceback
from typing import cast
from utils3 import Timer
from termcolor import colored
from argus.cache_sys import DomainCache
from argus.polymarket_direct.safe import IPSafety
from py_clob_client_v2.clob_types import AssetType
from argus.wireproxy import wrapper as wp_wrappers
from py_clob_client_v2 import BalanceAllowanceParams
from argus.polymarket_direct import _types as pm_types
from py_clob_client_v2.order_builder.constants import BUY, SELL
from py_clob_client_v2.order_utils import SignedOrderV2 as SignedOrder
from py_clob_client_v2.clob_types import PostOrdersV2Args, OrderPayload
from argus._argus_utils import throw_fuss, macos_notification_with_custom_sound
from argus.polymarket_direct.order_types import OrderException, PolyMarketOrder, TradeData
from py_clob_client_v2.client import OrderArgsV2, OrderType, ClobClient, PartialCreateOrderOptions

# Line-by-line timing utility
_timer_last = {}


def _tick(label: str, location: str):
    """Print elapsed time since last tick at this location."""
    key = f"{label}:{location}"
    now = time.perf_counter()
    if key in _timer_last:
        elapsed_ms = (now - _timer_last[key]) * 1000
        print(f"[TIMER][{label}] {location}: +{elapsed_ms:.3f}ms")
    else:
        print(f"[TIMER][{label}] {location}: start")
    _timer_last[key] = now


REST_CACHE = DomainCache("polymarket_direct.rest")
endpoints = {
    "events": "https://gamma-api.polymarket.com/events?order=id&ascending=false&closed=false&limit={}&offset={}",
    "geo_block_test": "https://polymarket.com/api/geoblock",
    "page_data": "https://polymarket.com/_next/data/sSKD4bdfi6zzQnEgftBzb/en/event/btc-updown-15m-1770750000.json",
}
qw = "[{}]".format(__name__)


def retry(max_attempts=3, delay=0.35):
    """Retry decorator for methods that may fail due to eventual consistency.
    Retries on TypeError (e.g. None response unpacked as **kwargs) with a delay between attempts."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except TypeError as e:
                    if attempt < max_attempts:
                        logging.warning(
                            "%s failed on attempt %d/%d: %s — retrying in %.2fs...",
                            func.__qualname__,
                            attempt,
                            max_attempts,
                            e,
                            delay,
                        )
                        time.sleep(delay)
                    else:
                        logging.warning(
                            "%s failed on attempt %d/%d: %s — no more retries.",
                            func.__qualname__,
                            attempt,
                            max_attempts,
                            e,
                        )
                        raise
            return None

        return wrapper

    return decorator


# MUST be used on methods within PolyRestAPI that are critical
def fatal_decorator(func_idx):
    def decorator(func):
        def wrapper(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except Exception as e:
                traceback.print_exc()
                if self.fatal_callback:
                    self.fatal_callback(
                        {
                            "self": self,
                            "function": func_idx,
                            "args": args,
                            "kwargs": kwargs,
                            "exception": e,
                            "traceback": traceback.format_exc(),
                        }
                    )
                raise

        return wrapper

    return decorator


class PolyRestAPI:
    """
    A REST API client for interacting with Polymarket's CLOB via py_clob_client_v2 and other endpoints.
    """

    def __init__(
        self,
        private_key,
        proxy_funder,
        host="https://clob.polymarket.com",
        chain_id=137,
        divisor=1_000_000,
        fatal_callback=None,
    ):
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
        self.raw_session: requests.Session = self.session

        wp_wrappers.update_request_session_proxy(idx="POLYMARKET", session=self.session)

        if (
            not os.environ.get("POLYMARKET_UNSAFE_RAPID_CONNECTIONS", "false")
            == "false"
        ):
            # Escape wrapping
            self.raw_session = requests.Session()

        self._make_httpx_clob_client()
        self.safety = IPSafety()

        if os.environ.get("POLYMARKET_NO_SAFETY_CHECK", "false") != "true":
            self.ip_safety_check()

        self.clob = ClobClient(
            host,
            key=private_key,
            chain_id=chain_id,
            signature_type=1,
            funder=proxy_funder,
        )
        self.clob.set_api_creds(self._create_or_derive_api_key())
        self.clob.get_version()  # pre-warm version cache for v2
        self._div = divisor

        self._order_cache = {"orders": []}

        self.fatal_callback = fatal_callback
        if self.fatal_callback is None:

            def default_fatal_callback(info: dict):
                print(
                    colored(
                        f"[{__name__}] FATAL ERROR in Polymarket REST API client: {info}",
                        "red",
                        attrs=["bold", "blink"],
                    )
                )

            self.fatal_callback = default_fatal_callback

    ###########################################
    # Utility Methods
    ###########################################

    def ip_safety_check(self):
        print(qw, "Starting Polymarket REST API client initialization...")
        print(
            qw,
            "Checking IP information against hardcoded geo-blocked regions via ipinfo.io...",
        )
        ip_info = self.safety.get_ip_info()
        ip_info["ip"] = "REDACTED"  # Redact IP for privacy in logs
        if self.safety.is_ip_in_bad_region(ip_info):
            msg = (
                "The IP returned from ipinfo.io compared against built-in geo-blocked regions indicates this"
                " IP address may face issues accessing Polymarket. This maybe wrong, the next check will"
                " attempt to connect directly to polymarket.com to verify (in the next 5s) to double check. If"
                "this address should not leak polymarket.com to the ISP TERMINATE NOW. To automatically terminate"
                " when here set the env `POLYMARKET_PARANOID` to `true` before running."
            )
            if os.environ.get("POLYMARKET_PARANOID", "false") == "true":
                raise RuntimeError(
                    "THIS IP IS GEO-BLOCKED ACCORDING TO HARDCODED REGIONS AND `POLYMARKET_PARANOID` IS SET TO TRUE. "
                    "TERMINATING FOR SAFETY. IP INFO: " + str(ip_info)
                )
            else:
                print(qw, colored(msg, "yellow", attrs=["bold", "blink"]))

        else:
            ip_info["ip"] = "REDACTED"
            print(qw, f"IP info: {ip_info}")
            print(qw, "Proceeding to Polymarket geo-block check...")

        if os.environ.get("POLYMARKET_PROTECTION", "true") == "true":
            if self.check_geo_blocked():
                raise RuntimeError(
                    "The current IP is geo-blocked from accessing Polymarket."
                )
            else:
                print(
                    qw,
                    colored(
                        "The current IP is NOT geo-blocked from accessing Polymarket. Happy trading!",
                        "green",
                        attrs=["bold"],
                    ),
                )
        else:
            warning_msg = (
                "WARNING: YOU HAVE DISABLED POLYMARKET GEO-BLOCK PROTECTION CHECKS VIA THE "
                "`POLYMARKET_PROTECTION` ENVIRONMENT VARIABLE (default: true). THIS WILL LEAD TO ORDERS NOT BEING REJECTED "
                "AND POSSIBLE ISSUES WITH A ACTIVELY TRADED ACCOUNT IS LOGGED IN FROM A GEO-BLOCKED IP. "
                "THE NEXT STEP INVOLVES DERIVING CREDENTIALS WHICH WILL EXPOSE YOUR ACCOUNT TO POLYMARKET"
                "FROM THIS IP ADDRESS. THE PROGRAM WILL NOW SLEEP FOR 30s DO NOTHING TO PROCEED, TERMINATE OTHERWISE"
            )
            throw_fuss(
                msg=colored(warning_msg, "red", attrs=["bold", "blink"]),
                notify=False,
                title="WARNING: POLYMARKET GEO-BLOCK PROTECTION DISABLED",
            )
            macos_notification_with_custom_sound(
                title="WARNING: POLYMARKET GEO-BLOCK PROTECTION DISABLED",
                message=warning_msg,
                sound_name="Basso",
            )
            for i in range(30, 0, -1):
                print(
                    qw,
                    colored(
                        f"Continuing in {i} seconds... terminate now to abort.",
                        "red",
                        attrs=["bold"],
                    ),
                    end="\r",
                )
                time.sleep(1)
            print()

    def check_geo_blocked(self) -> bool:
        """
        Check if the current IP is geo-blocked from accessing Polymarket.
        :return: True if geo-blocked, False otherwise.
        """
        response = self.session.get(endpoints["geo_block_test"])
        response.raise_for_status()
        data = response.json()
        logging.info("Geo-block check response: %s", data)
        return data.get("blocked", False)

    @REST_CACHE.cache_decorator(
        func_uuid="create_or_derive_api_key",
        expiration=60 * 60 * 24,
        should_cache_function=lambda x: x is not None,
    )
    def _create_or_derive_api_key(self):
        response = self.clob.create_or_derive_api_key()
        return response

    @staticmethod
    def _make_httpx_clob_client():
        """
        PyClobClient uses httpx for its HTTP requests; however, it does not expose a way to set up proxies.
        This function patches the internal httpx Client used by py_clob_client_v2 to use a proxy if specified
        via WireProxy.
        """
        from httpx import Client
        from py_clob_client_v2.http_helpers import helpers

        proxy = wp_wrappers.start_proxy_and_return_bind("POLYMARKET")
        if proxy is not None:
            proxy = f"socks5://{proxy}"
        _client = Client(http2=True, proxy=proxy)
        setattr(helpers, "_http_client", _client)

    ###########################################
    # Public API Methods
    ###########################################

    def fetch_events(
        self, offset=0, limit=20, debug_raw_callback=None
    ) -> list[pm_types.PolymarketEvent]:
        url = endpoints["events"].format(limit, offset)
        response = self.raw_session.get(url)
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

    @fatal_decorator("place_order")
    def place_order(
        self,
        token_id: str,
        market: pm_types.PolymarketEvent,
        price: float,
        size: float,
        side: str,
        order_type: OrderType = OrderType.GTC,
        tick_size: float = None,
    ) -> dict:
        """
        Place an order on the Polymarket CLOB.
        :param order_type: The type of the order (default: GTC).
        :param market: The market object associated with the token.
        :param token_id: The ID of the token to trade.
        :param price: The price at which to place the order.
        :param size: The size of the order.
        :param side: The side of the order ('buy' or 'sell').
        :param tick_size: The tick size for the order. If not provided, it will be fetched from the CLOB.
        :return: A dictionary containing the result of the order placement.
            E.g. {'errorMsg': '', 'orderID': '0xxxxxx', 'takingAmount': '', 'makingAmount': '', 'status': 'live', 'success': True}
        """

        time_taken_break_down = []

        with Timer(lambda x: time_taken_break_down.append(x)):
            order = self.build_order(
                token_id=token_id,
                market=market,
                price=price,
                size=size,
                side=side,
                tick_size=tick_size,
            )

        with Timer(lambda x: time_taken_break_down.append(x)):
            result = self.clob.post_order(order=order, order_type=order_type)

        logging.info("Order placed: %s", result)
        if result["errorMsg"] == "" and result["success"]:
            # the success is always true even when there is a failure
            # to submit the order
            order_id = result["orderID"]
            self._order_cache["orders"].append(result)
            self._order_cache[order_id] = {
                "token_id": token_id,
                "market": market,
                "price": price,
                "size": size,
                "side": side,
                "order_type": order_type,
                "result": result,
            }
            total_time_taken = sum(time_taken_break_down)
            print(
                qw,
                colored(
                    f"Order placed successfully. Order ID: {order_id}",
                    "green",
                    attrs=["bold"],
                ),
            )
            msg = f"{qw} Latency breakdown for placing order:\n"
            msg += f"{qw}  ({(time_taken_break_down[0] / total_time_taken) * 100}%) Building order: {time_taken_break_down[0]:.2f} seconds\n"
            msg += f"{qw}  ({(time_taken_break_down[1] / total_time_taken) * 100}%) Posting order: {time_taken_break_down[1]:.2f} seconds\n"
            print(colored(msg, "yellow"))
        else:
            raise OrderException(
                f"Failed to place order: {result.get('errorMsg', 'Unknown error')}, response={result}"
            )

        return result

    @fatal_decorator("place_built_orders")
    def place_built_orders(
        self, orders: list[SignedOrder], order_type: OrderType = OrderType.GTC
    ) -> dict:
        """
        Place already built and signed orders on the Polymarket CLOB.
        :param order_type: The type of the orders (default: GTC).
        :param orders: A list of already built and signed orders to place.
        :return: The result from the CLOB API, containing order placement results.
        """
        _tick("place_built_orders", "start")
        postable_orders = map(
            lambda x: PostOrdersV2Args(order=x, orderType=order_type), orders
        )
        _tick("place_built_orders", "after_postable_orders_map")
        postable_orders_list = list(postable_orders)
        _tick("place_built_orders", "after_list_conversion")
        post: list[dict] = self.clob.post_orders(postable_orders_list)
        _tick("place_built_orders", "after_post_orders_call")
        # Example Response:
        # [
        #    {
        #       "errorMsg":"invalid amount for a marketable BUY order ($0.01), min size: $1",
        #       "orderID":"",
        #       "takingAmount":"",
        #       "makingAmount":"",
        #       "status":"",
        #       "success":true
        #    }
        #    ....
        # ]

        _tick("place_built_orders", "before_success_failed_init")
        success = []
        _tick("place_built_orders", "after_success_init")
        failed = []
        _tick("place_built_orders", "after_failed_init")
        for msg in post:
            _tick("place_built_orders", "loop_start")
            if msg["errorMsg"]:
                _tick("place_built_orders", "before_throw_fuss_error")
                throw_fuss(
                    msg=colored(
                        "Failed to post built and signed order: {}".format(
                            msg["errorMsg"]
                        ),
                        "red",
                        attrs=["bold"],
                    ),
                    title="Failed to Post Built and Signed Order",
                    notify=True,
                )
                _tick("place_built_orders", "after_throw_fuss_error")
                failed.append(msg)
                _tick("place_built_orders", "after_failed_append")
            else:
                _tick("place_built_orders", "else_branch")
                order_id = msg.get("orderID", "Unknown ID")
                _tick("place_built_orders", "after_get_order_id")
                throw_fuss(
                    msg=colored(
                        "Successfully Posted Built and Signed Orders. Order ID: {}".format(
                            order_id
                        ),
                        "green",
                        attrs=["bold"],
                    ),
                    title="Successfully Posted Built and Signed Orders",
                    notify=True,
                )
                _tick("place_built_orders", "after_throw_fuss_success")
                macos_notification_with_custom_sound(
                    title="Successfully Posted Built and Signed Order",
                    message="Successfully Posted Built and Signed Orders. Order ID: {}".format(
                        order_id
                    ),
                    sound_name="Glass",
                )
                _tick("place_built_orders", "after_notification")
                logging.info("Successfully posted built and signed order: %s", msg)
                _tick("place_built_orders", "after_logging")
                success.append(msg)
                _tick("place_built_orders", "after_success_append")
        _tick("place_built_orders", "after_loop")

        result = {"success": success, "failed": failed}
        _tick("place_built_orders", "before_return")
        return result

    @fatal_decorator("build_order")
    def build_order(
        self,
        token_id: str,
        market: pm_types.PolymarketEvent,
        price: float,
        size: float,
        side: str,
        tick_size: float = None,
    ) -> SignedOrder:
        _tick("build_order", "start")
        if tick_size is None:
            _tick("build_order", "before_get_tick_size")
            tick_size = self.get_tick_size(token_id)
            _tick("build_order", "after_get_tick_size")
        _tick("build_order", "after_tick_size_check")

        mapped = {"buy": BUY, "sell": SELL}
        _tick("build_order", "after_mapped_dict")
        type_side = mapped.get(side.lower())
        _tick("build_order", "after_get_type_side")
        if type_side is None:
            raise ValueError("side must be either 'buy' or 'sell'")
        _tick("build_order", "after_type_side_validation")

        _tick("build_order", "before_create_order")
        order = self.clob.create_order(
            order_args=OrderArgsV2(
                token_id=token_id,
                price=price,
                size=size,
                side=type_side,
            ),
            options=PartialCreateOrderOptions(
                tick_size=tick_size, neg_risk=market.negRisk
            ),
        )
        _tick("build_order", "after_create_order")

        return cast(SignedOrder, order)

    @fatal_decorator("cancel_order")
    def cancel_order(self, order_id: str) -> dict:
        """
        Cancel an existing order on the Polymarket CLOB.
        :param order_id: The ID of the order to cancel.
        :return: The result of the cancellation request.
            E.g. {'not_canceled': {}, 'canceled': ['0000000x00000']}
        """
        result = self.clob.cancel_order(OrderPayload(orderID=order_id))
        logging.info("Order cancellation result: %s", result)
        if len(result["canceled"]) > 0:
            print(
                qw,
                colored(
                    f"Order {order_id} canceled successfully.", "green", attrs=["bold"]
                ),
            )
            # Remove from cache
            self._order_cache["orders"] = [
                o for o in self._order_cache["orders"] if o["orderID"] != order_id
            ]
            if order_id in self._order_cache:
                del self._order_cache[order_id]
        else:
            print(
                qw,
                colored(f"Failed to cancel order {order_id}.", "red", attrs=["bold"]),
            )
        return result

    @fatal_decorator("cancel_multiple")
    def cancel_multiple(self, order_ids: list[str]) -> dict:
        """
        Cancel multiple orders on the Polymarket CLOB in a single HTTP request.

        :param order_ids: List of order IDs to cancel.
        :return: The result of the batch cancellation request.
            E.g. {
                'not_canceled': {'order_id_1': 'already canceled or matched'},
                'canceled': ['order_id_2', 'order_id_3']
            }
        """
        result = self.clob.cancel_orders(order_ids)
        logging.info("Batch order cancellation result: %s", result)

        # Log successful cancellations
        if result.get("canceled"):
            canceled_count = len(result["canceled"])
            print(
                qw,
                colored(
                    f"Successfully canceled {canceled_count} orders.",
                    "green",
                    attrs=["bold"],
                ),
            )
            # Remove from cache
            for order_id in result["canceled"]:
                self._order_cache["orders"] = [
                    o for o in self._order_cache["orders"] if o["orderID"] != order_id
                ]
                if order_id in self._order_cache:
                    del self._order_cache[order_id]

        # Log failed cancellations
        if result.get("not_canceled"):
            not_canceled_count = len(result["not_canceled"])
            print(
                qw,
                colored(
                    f"Failed to cancel {not_canceled_count} orders.",
                    "red",
                    attrs=["bold"],
                ),
            )
            for order_id, reason in result["not_canceled"].items():
                logging.warning(f"Order {order_id} not canceled: {reason}")

        return result

    @fatal_decorator("get_orders")
    def get_orders(self) -> list[PolyMarketOrder]:
        """
        Get the list of orders.
        :return: A list of orders.
        """
        orders = map(lambda x: PolyMarketOrder(**x), self.clob.get_open_orders())
        return list(orders)

    @fatal_decorator("get_trades")
    def get_trades(self) -> TradeData:
        """
        Get the list of trades.
        :return: A TradeData object containing the list of trades.
        """
        trades = self.clob.get_trades()
        return TradeData.from_list(trades)

    @fatal_decorator("get_order_status")
    @retry(max_attempts=3, delay=0.35)
    def get_order_status(self, order_id: str) -> PolyMarketOrder:
        """
        Get the status of a specific order.
        :param order_id: The ID of the order.
        :return: A PolyMarketOrder object containing the order status.
        """
        logging.info("Getting order status: %s", order_id)
        order_status = self.clob.get_order(order_id)
        return PolyMarketOrder(**order_status)

    @fatal_decorator("get_balance")
    def get_balance(self) -> float:
        """
        Get the tradeable balance of the account.

        This calculates the available balance by:
        1. Getting the gross on-chain wallet balance
        2. Subtracting reserved collateral from all open BUY orders

        Only BUY orders reserve USDC collateral. The reserved amount is calculated
        as (original_size - size_matched) * price for each open buy order.

        :return: The tradeable balance as a float in USDC.
        """

        # Step 1: Refresh the on-chain balance cache
        self.clob.update_balance_allowance(
            BalanceAllowanceParams(asset_type="COLLATERAL")
        )

        # Step 2: Get gross wallet balance
        raw = self.clob.get_balance_allowance(
            BalanceAllowanceParams(asset_type="COLLATERAL")
        )
        gross_balance = float(raw["balance"]) / self._div

        # Step 3: Subtract all open-order reservations across ALL markets
        open_orders = self.get_orders()  # Returns list of PolyMarketOrder objects
        reserved = 0.0
        for order in open_orders:
            original_size = float(order.original_size)
            size_matched = float(order.size_matched)
            price = float(order.price)
            # Only buy orders reserve USDC collateral
            if order.side.upper() == "BUY":
                reserved += (original_size - size_matched) * price

        return gross_balance - reserved

    @fatal_decorator("cancel_all")
    def cancel_all(self):
        return self.clob.cancel_all()

    @fatal_decorator("get_token_balance")
    def get_token_balance(self, token_id: str) -> float:
        """
        Get the balance of a specific conditional outcome token (YES or NO token).

        Args:
            token_id: The outcome token asset ID (YES or NO token)

        Returns:
            Number of shares as a float (e.g. 150.0)
        """

        params = BalanceAllowanceParams(
            asset_type=AssetType.CONDITIONAL, token_id=token_id
        )

        # Force refresh the cached balance on Polymarket's side first
        self.clob.update_balance_allowance(params)

        # Now fetch the refreshed value
        result = self.clob.get_balance_allowance(params)

        return float(result["balance"]) / self._div

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
        creds = self._create_or_derive_api_key()
        return {
            "apiKey": creds.api_key,
            "secret": creds.api_secret,
            "passphrase": creds.api_passphrase,
        }
