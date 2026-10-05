"""
Lighter (zkLighter) – Phase 1.0 of Argus v2, mirroring the HyperLiquid dispatcher
where Lighter's architecture actually matches it. Track the PR https://github.com/The-Sal/Argus/pull/96

Unlike Hyperliquid, Lighter has no HIP-3-style builder-deployed dexes -- it is a single
unified exchange (one account/margin system) with every market listed flat under that
one venue. Anywhere HyperLiquidDispatcher takes a dex_name / assembles per-dex data
(get_dexs, the dex_name param on get_perpetuals_for_dex, the 4-call perp_info), there is
no Lighter analog, so those are intentionally absent here rather than stubbed out.

Market-data streaming (subscribe/unsubscribe, live order book over P2) mirrors
HyperLiquidDispatcher's wiring -- see `argus/perpetuals/lighter/wss.py` for the
websocket layer and `docs/perf/lighter-market-data-parity-plan.md` for the design
this was built against. One divergence from Hyperliquid worth noting here: Hyperliquid's
`coin` is both the client-facing subscription key and the wire-level channel key, whereas
Lighter's wss channels are keyed by integer `market_id`, not the symbol string clients
subscribe with -- so `subscribe`/`unsubscribe`/`subscription_expired` below translate
symbol -> market_id once, at this dispatcher boundary, and everything below `self.market_data`
stays on `market_id` throughout.
"""
import os
import traceback
from utils3 import runAsThread
from argus.perpetuals.lighter import wss
from argus._argus_utils import ArgsObject
from argus import __version__ as argus_version
from argus.perpetuals.lighter import _errors as _ers
from argus.perpetuals.lighter import _classes as _cls
from argus.perpetuals.lighter.rest import LighterRest
from argus.perpetuals.lighter.exchange import LighterExchange, MAX_BATCH_SIZE
from argus.protocol import transmit_mkt_data_with_protocol_2
from argus.perpetuals.shared import BaseDispatcher, ers as _shared_ers, PrintInterface, LockedState, NewFundingRate, AccountUpdate
from argus.perpetuals.shared import account as _acct
from argus.perpetuals.shared.trading import TradingHandlersMixin


__version__ = [1, 0, 0, 0]
pi = PrintInterface('Lighter')

#: Deadline (seconds from now) for the native auth token minted from the API key when
#: LIGHTER_AUTH_TOKEN is unset. Lighter rejects a signed token whose deadline is much past ~6h for REST
#: reads ("invalid deadline"), so it is refreshed on demand by `LighterRest.set_auth_token_provider`.
_NATIVE_AUTH_TOKEN_DEADLINE_S = 6 * 60 * 60


class LighterDispatcher(TradingHandlersMixin, BaseDispatcher):
    """
    Lighter dispatcher.

    This class orchestrates the read-only market-data surface of the Lighter exchange.
    It is the Lighter analog of HyperLiquidDispatcher (argus/perpetuals/hyper/__init__.py)
    and shares its base class, protocol, and versioning scheme -- see that class's
    docstring for the general Argus v2 perpetuals-dispatcher design (P1 protocol,
    enforced correlation IDs, "products_version" component versioning).

    Market-data endpoints are backed by Lighter's public REST API (no signing required).

    Account data follows PolymarketDispatcher's action names (get_balance, get_positions,
    get_orders, get_order_status, get_trades) plus get_funding_payments, served by the shared
    handlers in argus/perpetuals/shared/account.py. Configuration is by environment:
    LIGHTER_ACCOUNT_INDEX selects the account (balance/positions are public reads) and
    LIGHTER_AUTH_TOKEN, a long-lived read-only API token, unlocks orders/trades/funding
    payments. Both are optional: leaving them *unset* is fine -- market data is unaffected and the
    account actions answer with AccountNotConfiguredError. A LIGHTER_AUTH_TOKEN that is *set* but
    malformed, expired, or scoped (`single`) to a different account than LIGHTER_ACCOUNT_INDEX is
    a configuration error and the dispatcher refuses to start (LighterRest raises ValueError from
    its constructor), market data included. This is deliberate: silently running with a dead token
    would make get_orders / get_trades look like "not configured" long after the real cause
    (an expired credential) has been forgotten. Every list action is paginated
    (offset/limit) because each record carries the full venue payload under "venue".
    Trading (get_leverage, set_leverage, place_order, place_multiple_orders,
    cancel_order, cancel_multiple_orders, cancel_all_orders) is served by
    argus/perpetuals/lighter/exchange.py against the signed `sendTx` endpoint, using the
    API-key credentials LIGHTER_ACC_INDEX + LIGHTER_API_INDEX + LIGHTER_PRIVATE_KEY (leverage
    and orders share the API key and nonce stream; no L1 wallet key is needed). The cross-venue
    control flow -- read current leverage -> apply -> place -> roll back on a definitive
    failure -- comes from argus.perpetuals.shared.trading, exactly as on Hyperliquid. Lighter's
    `sendTx` returns only a tx hash, so place_order answers status "submitted" and confirmation
    arrives through the account stream / get_orders; cancel_all_orders uses Lighter's native
    immediate cancel-all rather than Hyperliquid's client-side sweep. When the trading
    credentials are absent, trading actions raise AccountNotConfiguredError and market data is
    unaffected. LIGHTER_BLOCK_ORDER_EXECUTION is the kill switch.

    Account events are also pushed, exactly as on Hyperliquid: LighterAccountWss (argus/perpetuals/
    lighter/wss.py) runs on its own websocket and every connected client receives `account_update`
    pushes (`event` = "order" / "fill", one record per message) with no `subscribe` first; a reconnect
    emits one `event` = "gap" once both account channels re-ack. The stream is best-effort and isolated:
    if it cannot start, market data, REST and the account reads keep working. `account_all_orders` needs a
    short-lived native token (minted from the API key, refreshed before expiry); without the trading
    credentials the stream still pushes fills (no auth) but not order updates.
    """

    #: Errors a trading handler is "ready for" (see `fatal_decorator`): everything else trips the contingency.
    _expected_errors = (_shared_ers.DispatcherError, _ers.LighterError)

    def __init__(self, host: str = "localhost", port: int = 9974):

        routing_table = {
            # Meta Functions
            'products_version': self._products_version,
            # Information Functions
            'get_markets': self._get_markets,
            'get_funding_rates_for_all_perpetuals': self._get_funding_rates_for_all_perps,
            'market_info': self._market_info,
            'get_funding_history': self._get_funding_history,
            'search_perpetuals': self._search_perpetuals,
            'get_funding_rate': self._get_funding_rate,
            # Market Data Streaming
            'subscribe': self._handle_subscribe,
            'unsubscribe': self._handle_unsubscribe,
            # Account Info (shared handlers -- see argus/perpetuals/shared/account.py):
            #   get_balance, get_positions, get_orders, get_order_status, get_trades, get_funding_payments
            **self.account_routing_table(),
            # Trading Functions (signed `sendTx`; shared handlers via TradingHandlersMixin):
            'get_leverage': self._get_leverage,
            'set_leverage': self._set_leverage,
            'place_order': self._place_order,
            'place_multiple_orders': self._place_multiple_orders,
            'cancel_order': self._cancel_order,
            'cancel_multiple_orders': self._cancel_multiple_orders,
            'cancel_all_orders': self._cancel_all_orders,
        }

        # Account index: prefer the `.env` spelling (LIGHTER_ACC_INDEX); LIGHTER_ACCOUNT_INDEX is
        # accepted as a legacy fallback. LIGHTER_AUTH_TOKEN stays optional and unlocks the
        # auth-gated REST reads (orders/trades/funding payments).
        account_index = os.environ.get("LIGHTER_ACC_INDEX") or os.environ.get("LIGHTER_ACCOUNT_INDEX")
        rest_client = LighterRest(
            account_index=int(account_index) if account_index else None,
            auth_token=os.environ.get("LIGHTER_AUTH_TOKEN"),
        )
        if rest_client.account_index is None:
            pi.prt("No LIGHTER_ACC_INDEX / LIGHTER_AUTH_TOKEN set; account actions will report AccountNotConfiguredError")
        super().__init__(
            host=host,
            port=port,
            routing_table=routing_table,
            pi=pi,
            common_rest=rest_client,
            configurations={
                'distribute_refreshed_perps': True,
                'block_order_execution': os.environ.get('LIGHTER_BLOCK_ORDER_EXECUTION', '').strip().lower()
                                         in ('1', 'true', 'yes'),
            },
            account_rest=rest_client,
        )
        self.rest = rest_client

        # Signed trading client. Optional: without LIGHTER_API_INDEX + LIGHTER_PRIVATE_KEY the
        # dispatcher keeps serving market data and account reads, and trading actions answer with
        # AccountNotConfiguredError (see `_require_exchange`).
        api_key_index = os.environ.get("LIGHTER_API_INDEX")
        api_key_private_key = os.environ.get("LIGHTER_PRIVATE_KEY")
        self.exchange = None
        if rest_client.account_index is not None and api_key_index and api_key_private_key:
            try:
                self.exchange = LighterExchange(
                    account_index=rest_client.account_index,
                    api_key_index=int(api_key_index),
                    api_key_private_key=api_key_private_key,
                    rest=rest_client,
                    leverage_cache_ttl_s=self._leverage_cache_ttl_from_env(),
                )
            except Exception as e:
                pi.throw_fuss(
                    f"Failed to initialise the Lighter trading client: {e}. "
                    f"Trading actions will report AccountNotConfiguredError; everything else keeps running.",
                    title="Lighter Trading Init Failure", notify=True,
                )
                self.exchange = None
        elif rest_client.account_index is not None:
            pi.prt("Lighter account configured without trading credentials (LIGHTER_API_INDEX / "
                   "LIGHTER_PRIVATE_KEY); trading actions will report AccountNotConfiguredError")

        # With the API key we can mint a short-lived native auth token, refreshed on demand, so the
        # auth-gated REST reads (get_orders / get_trades / ...) keep working when LIGHTER_AUTH_TOKEN is unset.
        if self.exchange is not None and self.rest.auth_token is None:
            try:
                self.rest.set_auth_token_provider(
                    lambda: self.exchange.auth_token(_NATIVE_AUTH_TOKEN_DEADLINE_S)
                )
                pi.prt("Minting native Lighter auth tokens from the API key for auth-gated account reads")
            except Exception as e:
                pi.prt(f"Could not mint a native Lighter auth token; auth-gated reads will report "
                       f"AccountNotConfiguredError: {e}")
        self._all_perps = LockedState(self.rest.get_all_perpetuals())
        self._refresh_perpetuals()

        self._orderbook_depth = int(os.environ.get("LIGHTER_ORDERBOOK_DEPTH", 10))
        self.market_data = wss.LighterMarketDataWss(
            order_book_update_callback=self._order_book_update_callback
        )
        self.market_data.run(main_thread=False)

        # Account push stream (order lifecycle + fill events for the configured account), mirroring
        # HyperLiquidDispatcher. Isolated from market data on its own connection: if it cannot start we
        # shout and keep serving market data, REST and account reads, consistent with the account reads'
        # AccountNotConfiguredError behaviour. `account_all_orders` requires a short-lived native token,
        # so with the trading credentials we mint one from the API key and let the stream refresh it before
        # expiry; without them we still stream fills (which need no auth) and log that order updates are
        # unavailable. `account_all_positions` is not subscribed (no position push yet).
        self.account_updates = None
        if rest_client.account_index is not None:
            token_provider = None
            if self.exchange is not None:
                token_provider = lambda: self.exchange.auth_token(_NATIVE_AUTH_TOKEN_DEADLINE_S)
            else:
                pi.prt("Lighter account configured without trading credentials; the account stream will "
                       "push fills but not order updates (account_all_orders needs an auth token)")
            try:
                self.account_updates = wss.LighterAccountWss(
                    account_index=rest_client.account_index,
                    auth_token_provider=token_provider,
                    symbol_for_market_id=self._symbol_for_market_id,
                    update_callback=self._account_update_callback,
                    gap_callback=self._account_gap_callback,
                    token_deadline_s=_NATIVE_AUTH_TOKEN_DEADLINE_S,
                )
                self.account_updates.run(main_thread=False)
            except Exception as e:
                pi.throw_fuss(
                    f"Failed to start the Lighter account update stream: {e}. "
                    f"account_update pushes are unavailable; everything else keeps running.",
                    title="Lighter Account Stream Failure", notify=True,
                )
                traceback.print_exc()

    ########################################
    # INTERNAL SERVER FUNCTIONS & Callbacks
    ########################################

    def subscription_expired(self, channel_id):
        """
        Called by RoutingHelper.remove_socket once the last client socket subscribed
        to `channel_id` (a symbol, e.g. "BTC") disconnects/unsubscribes. Mirrors
        HyperLiquidDispatcher.subscription_expired: tears down the now-unused upstream
        Lighter websocket subscription so we don't keep streaming a book nobody is
        listening to. `channel_id` is the symbol (client-facing key); it is translated
        to the wire-level `market_id` here, at the boundary -- see module docstring.
        :param channel_id: The symbol whose last subscriber just went away.
        """
        perp = self._all_perps.value.get(channel_id)
        if perp is None:
            pi.prt(f"subscription_expired: unknown symbol '{channel_id}', cannot unsubscribe upstream")
            return
        self.market_data.unsubscribe_from_market(perp.market_id)

    def _handle_subscribe(self, args: ArgsObject) -> dict:
        """
        Subscribe the calling client socket to live order book updates for one or
        more symbols. :param args: Expects `args.args` to be a list of symbol strings
        (e.g. ["BTC", "ETH"]).
        """
        sock = args.sock
        self.add_socket(sock)
        subscribed = []
        failed = []
        for symbol in args.args or []:
            perp = self._all_perps.value.get(symbol)
            if perp is None:
                raise _shared_ers.InvalidCoinError(f"Symbol {symbol} is not a valid perpetual on Lighter")

            try:
                self.add_socket_to_subscription(sock, symbol)
                self.market_data.subscribe_to_market(perp.market_id)
                subscribed.append(symbol)
                self._routine_push_funding_rates_for_client(sock, perp)
            except Exception as e:
                failed.append(symbol)
                pi.prt(f"Error subscribing to symbol {symbol}: {e}")
                traceback.print_exc()
        return {"subscribed": subscribed, "failed": failed}

    def _handle_unsubscribe(self, args: ArgsObject) -> dict:
        """
        Unsubscribe the calling client socket from one or more symbols. Does not tear
        down the upstream Lighter subscription directly -- that happens via
        `subscription_expired` once no client socket is left subscribed to the symbol.
        """
        sock = args.sock
        unsubscribed = []
        failed = []
        for symbol in args.args or []:
            try:
                self.remove_socket_from_subscription(sock, symbol)
                unsubscribed.append(symbol)
            except Exception as e:
                failed.append(symbol)
                pi.prt(f"Error unsubscribing from symbol {symbol}: {e}")
                traceback.print_exc()
        return {"unsubscribed": unsubscribed, "failed": failed}

    def _order_book_update_callback(self, update: dict):
        """
        Fan-out callback wired into `wss.LighterMarketDataWss` -- runs on the
        websocket's own callback thread every time a market's book changes. Mirrors
        HyperLiquidDispatcher._order_book_update_callback: look up which client
        sockets are subscribed to this market's symbol, encode the book with the same
        P2 wire format via LighterP2ConvertClass, and push it to each of them.
        `update` is keyed by integer `market_id` (the wss layer's key); it is resolved
        to a symbol here (the routing table's key) before fan-out.
        """
        market_id_keys = [k for k in update.keys() if k != "timestamp"]
        if len(market_id_keys) != 1:
            pi.prt(f"Unexpected order book update shape (expected exactly one market_id key): {update.keys()}")
            return
        market_id = market_id_keys[0]

        perp = next((p for p in self._all_perps.value if p.market_id == market_id), None)
        if perp is None:
            pi.prt(f"Order book update for unknown market_id {market_id}; no symbol mapping, dropping.")
            return
        symbol = perp.name

        clients_to_send = list(self.market_data_routing_table.get(symbol, []))
        if not clients_to_send:
            return

        packet = transmit_mkt_data_with_protocol_2(
            _cls.LighterP2ConvertClass(
                symbol=symbol,
                market_id=market_id,
                market_data=update,
                order_book_depth=self._orderbook_depth,
            )
        )

        self._routine_send_packet_to_clients(clients_to_send, packet, f"order book update for symbol {symbol}")

    def _account_update_callback(self, record) -> None:
        """
        Called on the account websocket thread with one `_acct.OrderUpdate` or one `_acct.Trade` per call.
        Wraps it in an `AccountUpdate` (event "order" / "fill") and pushes it to every connected client;
        no `subscribe` is needed first. Never raises: an exception here would kill the websocket thread, so
        failures are logged instead. Mirrors `HyperLiquidDispatcher._account_update_callback`.
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
            pi.prt(f"Error pushing Lighter account update: {e}")
            traceback.print_exc()

    def _account_gap_callback(self, since_ms: int) -> None:
        """
        Called once after the account websocket reconnected and resubscribed. Events between `since_ms`
        and now may have been missed (neither account channel replays), so tell every client to reconcile
        with `get_orders` / `get_trades`. Never raises, for the same reason as `_account_update_callback`.
        """
        try:
            self._routine_push_account_update(AccountUpdate.gap("reconnected", since_ms))
        except Exception as e:
            pi.prt(f"Error pushing Lighter account gap: {e}")
            traceback.print_exc()

    @runAsThread
    def _distribute_refreshed_perpetuals(self):
        """
        Distributes the refreshed perpetuals currently subscribed to their respective clients as a P1 message.
        Mirrors HyperLiquidDispatcher._distribute_refreshed_perpetuals -- see that method's docstring.
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
                pi.prt(f"Unexpected error building perpetual info payload for symbol {perp.name}: {e}")
                traceback.print_exc()
                continue

            self._routine_send_packet_to_clients(clients_to_send, p1_bytes, f"perpetual info for symbol {perp.name}")

    ########################################
    # Dispatcher Functions
    ########################################
    def _products_version(self, args: ArgsObject) -> dict:
        """
        Returns the version of the dispatcher and its components, plus whether order execution is
        currently blocked by the kill switch (so a client can tell why placement fails).
        """
        _ = args
        return {
            'argus': argus_version,
            'lighter_dispatcher': __version__,
            'sidecars': {},
            'order_execution_blocked': self.order_execution_blocked,
        }

    def _get_markets(self, args: ArgsObject) -> dict:
        """
        Returns a paginated list of all perpetual markets. Unlike HyperLiquid's
        get_perpetuals_for_dex, this takes no venue parameter -- Lighter has a single
        unified market list, not per-dex universes.
        :param args: Expects arguments:
            'offset': int (default: 0)
            'limit': int (default: 100)
        :return:
        """
        DEFAULT_VALUE = 10

        perpetuals = self._all_perps.value.perpetuals

        offset = args.args.get('offset', 0)
        limit = args.args.get('limit', min(DEFAULT_VALUE, len(perpetuals)))

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

    def _market_info(self, args: ArgsObject) -> dict:
        """
        Returns metadata + live data for a single market. Unlike HyperLiquid's
        perpetual_info (which assembles 4 separate annotation/category endpoints),
        Lighter has no per-asset annotation/category system -- this is a direct lookup
        into the market list already fetched via get_all_perpetuals.

        :param args: Expects arguments (exactly one of):
            'symbol': str -- e.g. "BTC"
            'market_id': int
        :return:
        """
        symbol = args.args.get('symbol')
        market_id = args.args.get('market_id')
        if symbol is None and market_id is None:
            raise _shared_ers.MissingArgumentError("Missing argument: 'symbol' or 'market_id'")

        if symbol is not None:
            perp = self._all_perps.value.get(symbol)
        else:
            perp = next((p for p in self._all_perps.value if p.market_id == market_id), None)

        return {'perpetual': perp.to_dict() if perp is not None else None}

    def _get_funding_history(self, args: ArgsObject) -> dict:
        """
        Returns historical funding for a single market.
        :param args: Expects arguments:
            'market_id': int (required)
            'start_timestamp': int (required, unix seconds)
            'end_timestamp': int (default: now)
            'resolution': str (default: "1h")
        :return:
        """
        market_id = args.args.get('market_id')
        start_timestamp = args.args.get('start_timestamp')
        if market_id is None:
            raise _shared_ers.MissingArgumentError("Missing argument: 'market_id'")
        if start_timestamp is None:
            raise _shared_ers.MissingArgumentError("Missing argument: 'start_timestamp'")

        history = self.rest.get_funding_history(
            market_id=market_id,
            start_timestamp=start_timestamp,
            end_timestamp=args.args.get('end_timestamp'),
            resolution=args.args.get('resolution', '1h'),
        )
        return {'funding_history': [entry.to_dict() for entry in history]}

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

    def _get_funding_rate(self, args: ArgsObject) -> dict:
        """
        Returns the live funding rate for a single market.
        :param args: Expects arguments:
            'symbol': str (required) -- e.g. "BTC".
        :return: {'symbol', 'funding_rate', 'funding_rate_apr'}
        """
        symbol = args.args.get('symbol')
        if symbol is None:
            raise _shared_ers.MissingArgumentError("Missing argument: 'symbol'")
        perp = self._all_perps.value.get(symbol)
        if perp is None:
            raise _shared_ers.InvalidCoinError(f"Unknown perpetual symbol: '{symbol}'")
        apr = perp.funding_rate_apr()
        return {
            'symbol': perp.name,
            'funding_rate': str(perp.funding_rate) if perp.funding_rate is not None else None,
            'funding_rate_apr': str(apr) if apr is not None else None,
        }

    ########################################
    # Trading hooks (the venue half of shared.trading.TradingHandlersMixin)
    ########################################

    @staticmethod
    def _leverage_cache_ttl_from_env() -> float:
        """LIGHTER_LEVERAGE_CACHE_TTL_S (seconds, default 0 = always read fresh). A malformed value is
        ignored loudly rather than disabling trading."""
        raw = os.environ.get("LIGHTER_LEVERAGE_CACHE_TTL_S", "").strip()
        if not raw:
            return 0.0
        try:
            return max(0.0, float(raw))
        except ValueError:
            pi.prt(f"Ignoring invalid LIGHTER_LEVERAGE_CACHE_TTL_S={raw!r}; reading leverage fresh every time")
            return 0.0

    def _require_exchange(self) -> LighterExchange:
        """The signed trading client, or AccountNotConfiguredError when the API-key credentials
        were not provided (market data and account reads keep working without it)."""
        if self.exchange is None:
            raise _shared_ers.AccountNotConfiguredError(
                "Lighter trading is not configured: set LIGHTER_ACC_INDEX, LIGHTER_API_INDEX and "
                "LIGHTER_PRIVATE_KEY."
            )
        return self.exchange

    @staticmethod
    def _parse_order_id(value):
        """A Lighter cancel id is a venue `order_index` (numeric), a venue `order_id` string, or a
        client id written `"c:<client_order_index>"`. All are resolved to an `order_index` before
        signing (see `_resolve_order_index`). The `c:` prefix exists because a client id and an
        order_index are both integers and are otherwise indistinguishable on the wire."""
        if isinstance(value, bool) or value is None:
            raise _shared_ers.DispatcherError(f"Invalid order_id {value!r}")
        if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
            return 'order_index', int(value)
        if isinstance(value, str) and value.startswith('c:') and value[2:].isdigit():
            return 'client_order_index', int(value[2:])
        if isinstance(value, str) and value:
            return 'order_id', value
        raise _shared_ers.DispatcherError(
            f"Invalid order_id {value!r}: expected a numeric order_index, a venue order_id string, "
            f"or a client id written 'c:<n>'"
        )

    def _symbol_for_market_id(self, market_id) -> 'str | None':
        perp = next((p for p in self._all_perps.value if p.market_id == market_id), None)
        return perp.name if perp is not None else None

    def _trading_leverage_info(self, coin: str) -> dict:
        """Lighter's `get_leverage` payload: the leverage in force, the market max, and the allowed modes."""
        ex = self._require_exchange()
        return {
            'coin': coin,
            'leverage': ex.read_leverage(coin, fresh=True).to_dict(),
            'max_leverage': ex.max_leverage(coin),
            'allowed_margin_modes': ex.allowed_margin_modes(coin),
        }

    def _trading_read_leverage(self, coin: str):
        return self._require_exchange().read_leverage(coin)

    def _trading_set_leverage(self, coin: str, applied) -> None:
        self._require_exchange().update_leverage(coin, applied.value, applied.type)

    def _trading_place_order(self, coin, side, price, size, tif, reduce_only, cloid):
        return self._require_exchange().place_order(coin, side, price, size, tif, reduce_only, cloid)

    def _trading_place_orders(self, requests):
        return self._require_exchange().place_orders(requests)

    def _trading_cancel(self, coin: str, kind: str, identifier):
        return self._require_exchange().cancel_order(coin, kind, identifier)

    def _trading_cancel_many(self, items):
        """The exchange resolves every id kind from ONE open-orders read, chunks the submission and turns an
        unresolvable item into an error outcome rather than failing the batch."""
        return self._require_exchange().cancel_many(items)

    def _trading_cancel_all(self, coin, dex):
        ex = self._require_exchange()
        if dex:
            raise _shared_ers.InvalidCoinError(
                f"Lighter has no sub-ledgers; 'dex' must be empty, got {dex!r}. Use LIGHTER_ACC_INDEX."
            )
        market_index = None
        if coin is not None:
            perp = self._all_perps.value.get(coin)
            if perp is None:
                raise _shared_ers.InvalidCoinError(f"Unknown perpetual symbol: '{coin}'")
            market_index = perp.market_id
        return ex.cancel_all(market_index)

    def _trading_resolve_order_coin(self, kind: str, identifier):
        wanted = str(identifier)
        for order in self.rest.get_account_active_orders():
            if self._order_key_matches(order, kind, wanted):
                return self._symbol_for_market_id(order.market_index)
        return None

    @staticmethod
    def _order_key_matches(order, kind: str, wanted: str) -> bool:
        if kind == 'order_index':
            return str(order.order_index) == wanted
        if kind == 'client_order_index':
            return str(order.client_order_index) == wanted
        return str(order.order_id) == wanted

    @staticmethod
    def _trading_index_key(kind: str, identifier) -> str:
        # Kind-qualified: an order_index and another order's client_order_index can be the same integer.
        return f"{kind}:{identifier}"

    def _trading_open_orders_index(self) -> dict:
        index = {}
        for order in self.rest.get_account_active_orders():
            symbol = self._symbol_for_market_id(order.market_index)
            if symbol is None:
                continue
            index[self._trading_index_key('order_index', order.order_index)] = symbol
            index[self._trading_index_key('order_id', order.order_id)] = symbol
            index[self._trading_index_key('client_order_index', order.client_order_index)] = symbol
        return index

    @staticmethod
    def _trading_is_definitive_failure(error: Exception) -> bool:
        return isinstance(error, (_ers.LighterError, _shared_ers.DispatcherError))

    @property
    def _trading_max_batch_size(self) -> int:
        return MAX_BATCH_SIZE
