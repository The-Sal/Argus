"""
Hyperliquid – This module will implement the full Dispatcher and Trading API.
This module is still under development and is being built as Phase 1.0 of Argus v2.
Track the PR for hyperliquid [here](https://github.com/The-Sal/Argus/pull/96)
"""
import os
import traceback
from utils3 import runAsThread
from argus.perpetuals.hyper import wss
from argus._argus_utils import ArgsObject
from argus import __version__ as argus_version
from argus.perpetuals.hyper import _errors as _ers
from typing import Any, Dict, List, Optional, Tuple
from argus.perpetuals.hyper import _classes as _cls
from argus.perpetuals.shared import account as _acct
from argus.perpetuals.hyper.rest import HyperLiquidRest
from argus.protocol import transmit_mkt_data_with_protocol_2
from argus.perpetuals.shared.trading import TradingHandlersMixin
from argus.perpetuals.hyper.exchange import HyperLiquidExchange, MAX_BATCH_SIZE
from argus.perpetuals.shared import BaseDispatcher, ers as _shared_ers, PrintInterface, LockedState, NewFundingRate, AccountUpdate

__version__ = [1, 0, 0, 0]
pi = PrintInterface('HyperLiquid')


class HyperLiquidDispatcher(TradingHandlersMixin, BaseDispatcher):
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

    Trading (place_order, place_multiple_orders, cancel_order, cancel_multiple_orders, cancel_all_orders,
    get_leverage, set_leverage) is served by argus/perpetuals/hyper/exchange.py against the signed
    `exchange` endpoint, using HYPERLIQUID_WALLET_ADDRESS + HYPERLIQUID_PRIVATE_KEY (the private key must
    belong to the master address). Order modification is intentionally not part of this surface. Hyperliquid
    has no immediate cancel-all (only the scheduled trigger scheduleCancel), so cancel_all_orders is
    client-side: list open orders, then batch-cancel. place_order takes coin/side/price/size and a REQUIRED
    `leverage` (plus optional margin_mode, order_type GTC|IOC|ALO, reduce_only and cloid): leverage is stored
    per coin by the venue, so the dispatcher reads the coin's current leverage, applies the requested one,
    places the order and ROLLS BACK to the previous leverage if the order is rejected. cancel_order takes an
    order_id (a numeric oid or a 0x-prefixed cloid) and resolves the coin itself via orderStatus when the
    caller omits it. A venue-level rejection of one order (e.g. insufficient margin) is reported in the
    response's 'error' field, not as a packet error.

    Kill switch: HYPERLIQUID_BLOCK_ORDER_EXECUTION (or the interactive "Toggle Block Order Execution") makes
    place_order, place_multiple_orders and set_leverage raise OrderExecutionDisabledError; cancels are never
    blocked. Trading handlers are wrapped in `fatal_decorator`: an error the handler was not ready for runs
    `_on_fatal_error` (console alert + `fatal_error` broadcast; it never touches the kill switch) before propagating.

    Account push: the master wallet's order transitions and fills are pushed to every connected client as
    `account_update` (events "order", "fill", "gap"), one record per message, with no `subscribe` required.
    Served by argus/perpetuals/hyper/wss.py HyperLiquidAccountWss on its own websocket so a failure there
    never affects market data. See docs/perpetuals/hyperliquid/DISPATCHER.md.

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

    #: Errors a trading handler is "ready for" (see `fatal_decorator`): everything else trips the contingency.
    _expected_errors = (_shared_ers.DispatcherError, _ers.HyperLiquidError)

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
            'get_leverage': self._get_leverage,
            'set_leverage': self._set_leverage,
            'place_order': self._place_order,
            'place_multiple_orders': self._place_multiple_orders,
            'cancel_order': self._cancel_order,
            'cancel_multiple_orders': self._cancel_multiple_orders,
            'cancel_all_orders': self._cancel_all_orders,
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
                'distribute_refreshed_perps': True,
                'block_order_execution': os.environ.get('HYPERLIQUID_BLOCK_ORDER_EXECUTION', '').strip().lower()
                                         in ('1', 'true', 'yes'),
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

        # Account push stream (orders + fills for HYPERLIQUID_WALLET_ADDRESS). Isolated from market data:
        # if it cannot start we shout and keep serving everything else, like AccountNotConfiguredError does.
        self.account_updates = None
        try:
            self.account_updates = wss.HyperLiquidAccountWss(
                wallet_address,
                update_callback=self._account_update_callback,
                gap_callback=self._account_gap_callback,
            )
            self.account_updates.run(main_thread=False)
        except Exception as e:
            pi.throw_fuss(
                f"Failed to start the Hyperliquid account update stream: {e}. "
                f"account_update pushes are unavailable; everything else keeps running.",
                title="Account Stream Failure",
                notify=True,
            )
            traceback.print_exc()

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

    def _account_update_callback(self, record) -> None:
        """
        Called on the account websocket thread with one `OrderUpdate` or one `Trade` per call. Wraps it in
        an `AccountUpdate` ("order" / "fill") and pushes it to every connected client; clients do not need
        to `subscribe` first (unlike Polymarket). Never raises: an exception here would kill the websocket
        thread, so failures are logged instead.
        """
        try:
            if isinstance(record, _acct.OrderUpdate):
                update = AccountUpdate.order(record)
            elif isinstance(record, _acct.Trade):
                update = AccountUpdate.fill(record)
            else:
                raise TypeError(f"unsupported account record {type(record).__name__}")
            self._routine_push_account_update(update)
        except Exception as e:
            pi.prt(f"Error pushing account update: {e}")
            traceback.print_exc()

    def _account_gap_callback(self, since_ms: int) -> None:
        """
        Called once after the account websocket reconnected and resubscribed. Events between `since_ms` and
        now may have been missed (the channels do not replay), so tell every client to reconcile with
        `get_orders` / `get_trades`. Never raises, for the same reason as `_account_update_callback`.
        """
        try:
            self._routine_push_account_update(AccountUpdate.gap("reconnected", since_ms))
        except Exception as e:
            pi.prt(f"Error pushing account gap: {e}")
            traceback.print_exc()

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
    def _products_version(self, args: ArgsObject) -> dict:
        """
        Returns the version of the dispatcher and its components, plus whether order execution is currently
        blocked by the kill switch (so a client can tell why placement fails; it cannot change it).
        """
        _ = args
        return {
            'argus': argus_version,
            'hyperliquid_dispatcher': __version__,
            'sidecars': {},
            'order_execution_blocked': self.order_execution_blocked,
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
    # Trading hooks (the venue half of shared.trading.TradingHandlersMixin)
    #
    # The cross-venue control flow (read current -> apply -> place -> roll back) and the seven
    # handlers (get_leverage/set_leverage/place_order/place_multiple_orders/cancel_*) live in
    # `argus.perpetuals.shared.trading`. These hooks are what is actually Hyperliquid-specific:
    # how its leverage is read and written, how orders are signed, and how ids are parsed.
    ########################################

    def _trading_read_leverage(self, coin: str) -> _cls.PositionLeverage:
        """The leverage in force for the account on `coin` ("activeAssetData"), i.e. the roll-back target."""
        return self.rest.get_active_asset_data(coin).leverage

    def _trading_set_leverage(self, coin: str, applied: _cls.PositionLeverage) -> None:
        """Sign and submit the leverage update putting `coin` on `applied`."""
        self.exchange.update_leverage(coin, applied.value, is_cross=applied.type == "cross")

    def _trading_place_order(self, coin, side, price, size, tif, reduce_only, cloid):
        """Sign and submit one limit order on the signed `exchange` endpoint."""
        return self.exchange.place_order(
            coin=coin, side=side, price=price, size=size, tif=tif, reduce_only=reduce_only, cloid=cloid,
        )

    def _trading_place_orders(self, requests: List[_cls.OrderRequest]) -> List[_cls.OrderPlacementResult]:
        """Sign and submit several limit orders in ONE batch action."""
        return self.exchange.place_orders(requests)

    def _trading_cancel(self, coin: str, kind: str, identifier):
        """Cancel one order by venue oid or client cloid."""
        return (
            self.exchange.cancel_by_oid(coin, identifier)
            if kind == 'oid'
            else self.exchange.cancel_by_cloid(coin, identifier)
        )

    def _trading_cancel_many(self, items: List[tuple]) -> _cls.BatchCancelResult:
        """Cancel several orders; `items` are `(coin, kind, identifier)` triples. Hyperliquid's `cancel_many`
        infers the id kind from the identifier's own type, so `kind` is dropped here."""
        return self.exchange.cancel_many([(coin, identifier) for coin, _kind, identifier in items])

    def _trading_cancel_all(self, coin: Optional[str], dex: Optional[str]) -> dict:
        """Hyperliquid has no immediate cancel-all, so this is a point-in-time sweep: read the open orders,
        then batch-cancel them. Orders placed after the read are not affected."""
        orders = self.account_rest.get_open_orders(dex)
        if coin is not None:
            orders = [o for o in orders if o.name == coin]
        if not orders:
            return _cls.BatchCancelResult().to_summary_dict()
        result = self.exchange.cancel_many([(o.name, int(o.order_id)) for o in orders])
        return result.to_summary_dict()

    def _trading_resolve_order_coin(self, kind: str, identifier) -> Optional[str]:
        """Resolve the coin of an order via `orderStatus` -- Hyperliquid's only by-id lookup."""
        status = self.account_rest.get_order_status_detail(identifier)
        return status.order.coin if status.order is not None else None

    def _trading_open_orders_index(self) -> Dict[str, str]:
        """`{str(oid): coin}` and `{cloid: coin}` over the open orders, for batch-cancel coin resolution."""
        index: Dict[str, str] = {}
        for order in self.account_rest.get_open_orders(None):
            index[str(order.order_id)] = order.name
            if order.client_order_id:
                index[order.client_order_id] = order.name
        return index

    @staticmethod
    def _trading_is_definitive_failure(error: Exception) -> bool:
        """True when `error` proves the order was NOT accepted (a venue rejection, or a shared
        DispatcherError raised before submission)."""
        return isinstance(error, (_ers.HyperLiquidError, _shared_ers.DispatcherError))

    @property
    def _trading_max_batch_size(self) -> int:
        """Hyperliquid's cap on orders per signed batch action (our own conservative cap)."""
        return MAX_BATCH_SIZE

    def _trading_leverage_info(self, coin: str) -> dict:
        """
        Hyperliquid's `get_leverage` payload for one coin: `activeAssetData` (the leverage in force, mark
        price and the venue's size limits) plus the asset's max leverage and margin modes.

        :return: {'coin', 'dex', 'leverage': {type, value, rawUsd?}, 'max_leverage', 'allowed_margin_modes'
            (["cross", "isolated"], or ["isolated"] for isolated-only assets), 'mark_price',
            'max_trade_sizes', 'available_to_trade'} -- the two size arrays are passed through in the venue's
            order, which is undocumented (believed [buy, sell]); do not rely on it.
        """
        asset = self.exchange.asset(coin)
        active = self.rest.get_active_asset_data(coin)
        out = active.to_dict()
        out.update({
            'dex': coin.split(':', 1)[0] if ':' in coin else '',
            'max_leverage': asset.maxLeverage,
            'allowed_margin_modes': ['isolated'] if asset.is_isolated_only else ['cross', 'isolated'],
        })
        return out

    @staticmethod
    def _parse_order_id(value: Any) -> Tuple[str, Any]:
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
