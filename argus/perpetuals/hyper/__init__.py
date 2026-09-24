"""
Hyperliquid – This module will implement the full Dispatcher and Trading API.
This module is still under development and is being built as Phase 1.0 of Argus v2.
Track the PR for hyperliquid [here](https://github.com/The-Sal/Argus/pull/96)
"""
import os
import traceback
from typing import Any
from utils3 import runAsThread
from argus.perpetuals.hyper import wss
from argus._argus_utils import ArgsObject
from argus import __version__ as argus_version
from argus.perpetuals.hyper import _errors as _ers
from argus.perpetuals.hyper import _classes as _cls
from argus.perpetuals.hyper.rest import HyperLiquidRest
from argus.protocol import transmit_mkt_data_with_protocol_2
from argus.perpetuals.hyper.exchange import HyperLiquidExchange
from argus.perpetuals.shared import BaseDispatcher, ers as _shared_ers, PrintInterface, LockedState, NewFundingRate




__version__ = [1, 0, 0, 0]
pi = PrintInterface('HyperLiquid')


class HyperLiquidDispatcher(BaseDispatcher):
    """
    Hyperliquid (Hl) dispatcher.

    This class orchestrates the entire surface of the Hyperliquid API.
    Important notes of how this dispatcher differs from PolymarketDispatcher (its closest analog pre-v2)
    This class enforces correlation IDs for all requests. A request without a correlation ID will be rejected;
    moreover, the correlation IDs are checked with _corr_checker to ensure they are unique. This is enforced
    from the base class BaseDispatcher. See BaseDispatcher for more details.

    This dispatcher is part of the Argus v2 architecture and is designed to work with the Phase 1.2 "Homogenous Trading API Specification".
    You can either run this dispatcher as a standalone or use it as an exchange within the upcoming Perpetuals Multi-Exchange
    Dispatcher.

    This dispatcher is independently versioned on top of Argus's own internal versioning system. The "products_version"
    function returns the version of the components of the dispatcher. This includes the following:
    – Argus Core version (argus/__init__.py; __version__)
    – Every sidecar version (relevant to the dispatcher, e.g., you will not get an APDB version here)
    – The Hyperliquid Dispatcher Version (hyper/__init__.py; __version__)

    For compatibility systems should pin the hyperliquid dispatcher version rather than the argus version. Versioning
    within Hyperliquid works as so:
    Version is defined as 4 integers: [INT, INT, INT, INT]
    [0] = API Breaking Change
    [1] = New Functionality
    [2] = Behavioral Changes
    [3] = Bug Fixes

    The general paradigm of how data flows will be identical to PolymarketDispatcher as well as the protocols and their
    quirks. Hl Dispatcher will use the same P1+P2 protocols as PolymarketDispatcher with system messages, request-response,
    and market data all over one stream. P1 of Hl will also inherit the auto-compress and 9999 max byte limits. Unlike
    the PolymarketDispatcher, which had some non-paginated functions (for large data sets), Hl will only expose
    paginated functions for large data sets.

    Account data follows PolymarketDispatcher's action names (get_balance, get_positions, get_orders,
    get_order_status, get_trades) plus get_funding_payments, all served by the shared handlers in
    argus/perpetuals/shared/account.py against the read-only, unsigned `info` endpoint keyed by
    HYPERLIQUID_WALLET_ADDRESS (which must be the master account address, not an API wallet). The
    positions/orders actions read every dex by default (each record is tagged with its 'dex'; pass 'dex' to
    narrow), because Hyperliquid keeps one clearinghouse per dex. get_balance resolves the account mode
    (userAbstraction): unified / portfolio-margin accounts keep their collateral in the spot ledger, so it
    is derived from spot + every dex and 'dex' is ignored. Every list action is paginated (offset/limit)
    because each record carries the full venue payload under "venue" -- see shared/account.py.

    Trading (place_order, cancel_order) is served by argus/perpetuals/hyper/exchange.py against the
    signed `exchange` endpoint, using HYPERLIQUID_WALLET_ADDRESS + HYPERLIQUID_PRIVATE_KEY (the private
    key must belong to the master address). Order modification is intentionally not part of this surface,
    and neither is a cancel-all: Hyperliquid's only bulk cancel is a scheduled trigger (scheduleCancel),
    not an immediate action. place_order takes coin/side/price/size plus optional order_type
    (GTC|IOC|ALO), reduce_only and cloid; cancel_order takes an order_id (a numeric oid or a 0x-prefixed
    cloid) and resolves the coin itself via orderStatus when the caller omits it. A venue-level rejection
    of one order (e.g. insufficient margin) is reported in the response's 'error' field, not as a packet
    error.

    The inbound underlying JSON structure of Hl follows polymarket:
    {
        "action": "<command_name>",
        "data": { /* command-specific arguments */ },
        "correlation_id": "<uuid>" // enforced.
    }
    The outbound JSON structure of Hl follows polymarket:
    {
      "action": "<command_name>",
      "data": { /* response data or null */ },
      "error": "<error message or null>",
      "compressed": <bool>, // true when data is auto-compressed (see polymarket docs for details)
      "correlation_id": "<uuid>" // None if the request errors before a packet was processed, or a pushed response
    }

    """

    def __init__(self, wallet_address=None, private_key=None, host: str = "localhost", port: int = 9972):

        routing_table = {
            # Meta Functions
            'products_version': self._products_version,
            # Information Functions
            'get_dexs': self._get_dexs,
            'get_perpetuals_for_dex': self._get_perpetual_for_dex,
            'get_funding_rates_for_all_perpetuals': self._get_funding_rates_for_all_perps,
            'perpetual_info': self._perp_info,
            'search_perpetuals': self._search_perpetuals,
            'get_funding_rate': self._get_funding_rate,
            # Market Data Streaming
            'subscribe': self._handle_subscribe,
            'unsubscribe': self._handle_unsubscribe,
            # Account Info (shared handlers -- see argus/perpetuals/shared/account.py):
            #   get_balance, get_positions, get_orders, get_order_status, get_trades, get_funding_payments
            **self.account_routing_table(),
            # Account Info (Hyperliquid-only)
            'get_account_fees': self._get_account_fees,
            'get_rate_limit_usage': self._get_rate_limit_usage,
            # Trading Functions (signed `exchange` endpoint -- see argus/perpetuals/hyper/exchange.py):
            'place_order': self._place_order,
            'cancel_order': self._cancel_order,
        }

        if wallet_address is None:
            wallet_address = os.environ["HYPERLIQUID_WALLET_ADDRESS"]
        if private_key is None:
            private_key = os.environ["HYPERLIQUID_PRIVATE_KEY"]

        rest_client = HyperLiquidRest(wallet_address, private_key)
        exchange_client = HyperLiquidExchange(wallet_address, private_key)
        super().__init__(
            host=host,
            port=port,
            routing_table=routing_table,
            pi=pi,
            common_rest=rest_client,
            configurations={
                'distribute_refreshed_perps': True
            },
            account_rest=rest_client,
        )
        self.rest = rest_client
        self.exchange = exchange_client
        self._all_perps = LockedState(self.rest.get_all_perpetuals())
        self._refresh_perpetuals()

        self._orderbook_depth = int(os.environ.get("HYPERLIQUID_ORDERBOOK_DEPTH", 10))
        if self._orderbook_depth > 20:
            raise ValueError("HYPERLIQUID_ORDERBOOK_DEPTH cannot exceed 20")
        self.market_data = wss.HyperLiquidMarketDataWss(
            order_book_update_callback=self._order_book_update_callback
        )
        self.market_data.run(main_thread=False)

    ########################################
    # INTERNAL SERVER FUNCTIONS & Callbacks
    ########################################

    def subscription_expired(self, channel_id):
        """
        Called by RoutingHelper.remove_socket once the last client socket subscribed
        to `channel_id` (a coin, e.g. "BTC") disconnects/unsubscribes. Mirrors
        PolymarketDispatcher.subscription_expired: tears down the now-unused upstream
        Hyperliquid websocket subscription so we don't keep streaming a book nobody
        is listening to.
        :param channel_id: The coin whose last subscriber just went away.
        """
        self.market_data.unsubscribe_from_coin(channel_id)

    def _handle_subscribe(self, args: ArgsObject) -> dict:
        """
        Subscribe the calling client socket to live order book updates for one or
        more coins. :param args: Expects `args.args` to be a list of coin strings
        (e.g. ["BTC", "xyz:AAPL"]).
        """
        sock = args.sock
        self.add_socket(sock)
        subscribed = []
        failed = []
        for coin in args.args or []:

            # check if this coin is value within self.all_perps it should exist
            maybe_dex = coin.split(":")
            if len(maybe_dex) == 2:
                dex = maybe_dex[0]
            else:
                dex = ""

            perp = self._all_perps.value.get(coin, dex=dex)
            if perp is None:
                raise _shared_ers.InvalidCoinError(f"Coin {coin} is not a valid perpetual on Hyperliquid")

            try:
                self.add_socket_to_subscription(sock, coin)
                self.market_data.subscribe_to_coin(coin)
                subscribed.append(coin)
                self._routine_push_funding_rates_for_client(sock, perp)
            except Exception as e:
                failed.append(coin)
                pi.prt(f"Error subscribing to coin {coin}: {e}")
                traceback.print_exc()
        return {"subscribed": subscribed, "failed": failed}

    def _handle_unsubscribe(self, args: ArgsObject) -> dict:
        """
        Unsubscribe the calling client socket from one or more coins. Does not tear
        down the upstream Hyperliquid subscription directly -- that happens via
        `subscription_expired` once no client socket is left subscribed to the coin.
        """
        sock = args.sock
        unsubscribed = []
        failed = []
        for coin in args.args or []:
            try:
                self.remove_socket_from_subscription(sock, coin)
                unsubscribed.append(coin)
            except Exception as e:
                failed.append(coin)
                pi.prt(f"Error unsubscribing from coin {coin}: {e}")
                traceback.print_exc()
        return {"unsubscribed": unsubscribed, "failed": failed}

    def _order_book_update_callback(self, update: dict):
        """
        Fan-out callback wired into `wss.HyperLiquidMarketDataWss` -- runs on the
        websocket's own callback thread every time a coin's book changes. Mirrors
        PolymarketDispatcher._order_book_update_callback: look up which client
        sockets are subscribed to this coin, encode the book with the same P2 wire
        format Polymarket uses (via HLP2ConvertClass), and push it to each of them.
        """
        asset_keys = [k for k in update.keys() if k != "timestamp"]
        if len(asset_keys) != 1:
            pi.prt(f"Unexpected order book update shape (expected exactly one coin key): {update.keys()}")
            return
        coin = asset_keys[0]

        clients_to_send = list(self.market_data_routing_table.get(coin, []))
        if not clients_to_send:
            return

        packet = transmit_mkt_data_with_protocol_2(
            _cls.HLP2ConvertClass(
                coin=coin,
                market_data=update,
                order_book_depth=self._orderbook_depth,
            )
        )

        self._routine_send_packet_to_clients(clients_to_send, packet, f"order book update for coin {coin}")

    @runAsThread
    def _distribute_refreshed_perpetuals(self):
        """
        Distributes the refreshed perpetuals currently subscribed to their respective clients as a P1 message
        :return:
        """
        for perp in self._all_perps.value:
            try:
                clients_to_send = list(self.market_data_routing_table.get(perp.name, []))
                if not clients_to_send:
                    continue
                p1_bytes = NewFundingRate(
                    perp_name=perp.name,
                    funding_rate=perp.funding_rate
                ).convert_to_protocol_1()
            except Exception as e:
                pi.prt(f"Unexpected error building perpetual info payload for coin {perp.name}: {e}")
                traceback.print_exc()
                continue

            self._routine_send_packet_to_clients(clients_to_send, p1_bytes, f"perpetual info for coin {perp.name}")

    ########################################
    # Dispatcher Functions
    ########################################
    @staticmethod
    def _products_version(args: ArgsObject) -> dict:
        """
        Returns the version of the dispatcher and its components.
        """
        _ = args
        return {
            'argus': argus_version,
            'hyperliquid_dispatcher': __version__,
            'sidecars': {}
        }

    def _get_dexs(self, args: ArgsObject) -> dict:
        _ = args
        all_dexes = self.rest.get_dexs()
        dexes_as_dicts = map(lambda dex: dex.to_dict(), all_dexes)
        return {'dexes': list(dexes_as_dicts)}

    def _get_perpetual_for_dex(self, args: ArgsObject) -> dict:
        """
        Returns a paginated list of perpetuals for a given dex.
        :param args: Expects arguments:
            'dex_name': str (required)
            'offset': int (default: 0)
            'limit': int (default: 100)
        :return:
        """
        dex_id = args.args.get('dex_name')
        if dex_id is None:
            raise _shared_ers.MissingArgumentError("Missing argument: 'dex_name'")
        perpetuals = self.rest.get_perpetuals_for_dex(dex_id).perpetuals

        offset = args.args.get('offset', 0)
        limit = args.args.get('limit', min(100, len(perpetuals)))

        if offset >= len(perpetuals):
            return {'perpetuals': []}

        max_index = offset + limit
        max_reachable = min(len(perpetuals), max_index)
        return {'perpetuals': [perp.to_dict() for perp in perpetuals[offset: max_reachable]]}

    def _get_funding_rates_for_all_perps(self, args: ArgsObject) -> dict:
        """
        Returns a sorted list of funding rates for all perps.
        :param args: Expects arguments:
            'offset': int (default: 0)
            'limit': int (default: DEFAULT_VALUE)
        :return:
        """

        DEFAULT_VALUE = 20

        funding_rate_sorted = self._all_perps.value.sorted_by_funding_rate()
        offset = args.args.get('offset', 0)
        limit = args.args.get('limit', min(DEFAULT_VALUE, len(funding_rate_sorted)))
        if limit > DEFAULT_VALUE:
            pi.prt(f"Limit increased from {DEFAULT_VALUE} to {limit}")

        if offset >= len(funding_rate_sorted):
            return {'funding_rates': []}

        max_index = offset + limit
        max_reachable = min(len(funding_rate_sorted), max_index)
        return {'funding_rates': [perp.to_dict() for perp in funding_rate_sorted[offset: max_reachable]]}

    def _perp_info(self, args: ArgsObject) -> dict:
        """
        Returns aggregated informational metadata for a single coin/perpetual. Hyperliquid has no
        single combined endpoint for this, so it is assembled from four separate info requests:
        `perpAnnotation` (per-coin), and `perpCategories` / `perpConciseAnnotations` / `predictedFundings`
        (bulk, all-coins, filtered down to the requested coin here). Each section is independently
        optional and is returned as null when the coin has no data for it -- in practice, annotation/
        category/concise_annotation are only populated for HIP-3 (builder-deployed) dex coins (e.g.
        "xyz:AAPL"), while predicted_funding is only populated for default-dex coins (e.g. "BTC"), per
        Hyperliquid's docs.

        This does NOT return live market data (mark price, funding rate, open interest, ...); use
        'get_perpetuals_for_dex' or 'get_funding_rates_for_all_perpetuals' for that. This does NOT
        return account/position data.

        :param args: Expects arguments:
            'coin': str (required) -- e.g. "BTC" for the default dex, or "xyz:AAPL" for a HIP-3 dex asset.
        :return:
        """
        coin = args.args.get('coin')
        if coin is None:
            raise _shared_ers.MissingArgumentError("Missing argument: 'coin'")

        annotation = self.rest.get_perp_annotation(coin)
        category_entry = next((c for c in self.rest.get_perp_categories() if c.coin == coin), None)
        concise_entry = next((c for c in self.rest.get_perp_concise_annotations() if c.coin == coin), None)
        predicted_entry = next((p for p in self.rest.get_predicted_fundings() if p.coin == coin), None)

        return {
            'coin': coin,
            'annotation': annotation.to_dict() if annotation is not None else None,
            'category': category_entry.category if category_entry is not None else None,
            'concise_annotation': concise_entry.to_pair()[1] if concise_entry is not None else None,
            'predicted_funding': [v.to_pair() for v in predicted_entry.venues] if predicted_entry is not None else None,
        }

    def _search_perpetuals(self, args: ArgsObject) -> dict:
        """
        Fuzzy-search perpetual symbols, mirroring Polymarket's `search_markets`.
        Runs against the in-memory perpetual index (refreshed hourly by the base
        dispatcher), so it is a cheap, network-free lookup.
        :param args: Expects arguments:
            'keyword': str (required) -- the ticker/name fragment to search for.
            'limit': int (default: 10)
        :return: {'perpetuals': [<symbol>, ...]}, best match first.
        """
        keyword = args.args.get('keyword')
        if keyword is None:
            raise _shared_ers.MissingArgumentError("Missing argument: 'keyword'")
        limit = args.args.get('limit', 10)
        return {'perpetuals': self._all_perps.value.search(keyword, limit)}

    ########################################
    # Trading (signed `exchange` endpoint)
    ########################################

    def _place_order(self, args: ArgsObject) -> dict:
        """
        Place a single limit order on the account's master wallet.

        :param args: Accepts 'coin' (required, e.g. "BTC" or "xyz:AAPL"), 'side' (required,
            "buy" or "sell"), 'price' (required, limit price), 'size' (required, size in coins),
            'order_type' (optional, "GTC" | "IOC" | "ALO", default "GTC"), 'reduce_only'
            (optional bool, default False), 'cloid' (optional client order id: 0x + 32 hex chars).
        :return: {'coin', 'oid', 'status', 'avgPx', 'error'} -- `status` is "resting" or
            "filled" and `oid` the venue order id; a venue-level rejection of the order (e.g.
            insufficient margin) comes back with `error` set and `oid` null instead of a packet error.
        """
        data = self._read_args(
            args, 'coin', 'side', 'price', 'size', 'order_type', 'reduce_only', 'cloid'
        )
        for required in ('coin', 'side', 'price', 'size'):
            if data.get(required) is None:
                raise _shared_ers.MissingArgumentError(f"Missing required argument {required!r}")
        coin = data['coin']
        order_type = str(data.get('order_type') or 'GTC').upper()
        tif = {'GTC': 'Gtc', 'IOC': 'Ioc', 'ALO': 'Alo'}.get(order_type)
        if tif is None:
            raise _shared_ers.MissingArgumentError(
                f"Invalid order_type {order_type!r}: expected GTC, IOC or ALO"
            )
        result = self.exchange.place_order(
            coin=coin,
            side=str(data['side']),
            price=data['price'],
            size=data['size'],
            tif=tif,
            reduce_only=bool(data.get('reduce_only') or False),
            cloid=data.get('cloid'),
        )
        return result.to_dict()

    @staticmethod
    def _parse_order_id(value: Any) -> tuple:
        """
        Split an order id into ('oid', int) or ('cloid', str). Oids are venue-assigned
        integers; cloids are client ids (0x + 32 hex chars, 16 bytes). Anything else is
        rejected here rather than surfaced later as an opaque venue error.
        """
        if isinstance(value, bool):
            raise _shared_ers.DispatcherError(f"Invalid order_id {value!r}")
        if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
            return 'oid', int(value)
        if isinstance(value, str) and value.startswith('0x') and len(value) == 34:
            try:
                int(value[2:], 16)
            except ValueError:
                raise _shared_ers.DispatcherError(f"Invalid cloid {value!r}: not hex")
            return 'cloid', value
        raise _shared_ers.DispatcherError(
            f"Invalid order_id {value!r}: expected a numeric oid or a 0x-prefixed 16-byte cloid"
        )

    def _cancel_order(self, args: ArgsObject) -> dict:
        """
        Cancel one order by its venue oid or client cloid.

        :param args: Accepts 'order_id' (required: a numeric oid or a 0x-prefixed 16-byte
            cloid) and an optional 'coin'. When 'coin' is omitted it is resolved from the
            order itself via orderStatus -- which also fails cleanly if the order no longer
            exists. Note the resolution costs one extra info call per cancel.
        :return: {'coin', 'canceledOids', 'errors'} -- the venue reports per-id statuses,
            so an id it could not cancel (already filled/canceled, or unknown) appears in
            'errors' and `canceledOids` stays empty; callers must check for that rather
            than assume a submitted cancel worked.
        """
        data = self._read_args(args, 'order_id', 'coin')
        if data.get('order_id') is None:
            raise _shared_ers.MissingArgumentError("Missing required argument 'order_id'")
        kind, identifier = self._parse_order_id(data['order_id'])
        coin = data.get('coin')
        if coin is None:
            status = self.account_rest.get_order_status_detail(identifier)
            if status.order is None:
                raise _shared_ers.DispatcherError(
                    f"Order {identifier} not found (and no 'coin' given to skip the lookup)"
                )
            coin = status.order.coin
        result = (
            self.exchange.cancel_by_oid(coin, identifier)
            if kind == 'oid'
            else self.exchange.cancel_by_cloid(coin, identifier)
        )
        return result.to_dict()

    def _get_account_fees(self, args: ArgsObject) -> dict:
        """
        Hyperliquid-only: the account's current maker/taker fee rates, fee schedule and rolling
        daily volume (`userFees`). No Lighter analog (Lighter's tier/fee data lives behind an
        auth-gated `accountLimits` endpoint with a different shape), so this is not a shared action.
        :param args: No arguments.
        :return: The `userFees` payload with the typed rate fields normalised (see _classes.UserFees).
        """
        _ = args
        return self.rest.get_user_fees().to_dict()

    def _get_rate_limit_usage(self, args: ArgsObject) -> dict:
        """
        Hyperliquid-only: the address-based rate-limit budget (`userRateLimit`). Signed actions
        (order placement, once implemented) draw down `nRequestsCap`, which grows with traded
        volume, so clients can watch it here before trading lands.
        :param args: No arguments.
        :return: {'cumVlm', 'nRequestsUsed', 'nRequestsCap', 'nRequestsSurplus'}
        """
        _ = args
        return self.rest.get_user_rate_limit().to_dict()

    def _get_funding_rate(self, args: ArgsObject) -> dict:
        """
        Returns the live funding rate for a single perpetual.
        :param args: Expects arguments:
            'symbol': str (required) -- e.g. "BTC" for the default dex, or
                "xyz:AAPL" for a HIP-3 dex asset.
        :return: {'symbol', 'funding_rate', 'funding_rate_apr'}
        """
        symbol = args.args.get('symbol')
        if symbol is None:
            raise _shared_ers.MissingArgumentError("Missing argument: 'symbol'")
        perp = next((p for p in self._all_perps.value if p.name == symbol), None)
        if perp is None:
            raise _shared_ers.InvalidCoinError(f"Unknown perpetual symbol: '{symbol}'")
        return {
            'symbol': perp.name,
            'funding_rate': str(perp.funding_rate),
            'funding_rate_apr': str(perp.funding_rate_apr()),
        }
