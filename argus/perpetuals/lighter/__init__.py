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
from argus.perpetuals.lighter import _classes as _cls
from argus.perpetuals.lighter.rest import LighterRest
from argus.protocol import transmit_mkt_data_with_protocol_2
from argus.perpetuals.shared import BaseDispatcher, ers as _shared_ers, PrintInterface, LockedState, NewFundingRate


__version__ = [1, 0, 0, 0]
pi = PrintInterface('Lighter')


class LighterDispatcher(BaseDispatcher):
    """
    Lighter dispatcher.

    This class orchestrates the read-only market-data surface of the Lighter exchange.
    It is the Lighter analog of HyperLiquidDispatcher (argus/perpetuals/hyper/__init__.py)
    and shares its base class, protocol, and versioning scheme -- see that class's
    docstring for the general Argus v2 perpetuals-dispatcher design (P1 protocol,
    enforced correlation IDs, "products_version" component versioning).

    All endpoints exposed here are backed by Lighter's public REST API (no signing
    required). Account/trading functions are not yet implemented, matching HyperLiquid's
    current state.
    """

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
            # Account Info
            # 'get_account_info': self._get_account_info,
            # 'get_account_balance': self._get_account_balance,
            # 'get_account_positions': self._get_account_positions
            # Trading Functions (TBD)
        }

        rest_client = LighterRest()
        super().__init__(
            host=host,
            port=port,
            routing_table=routing_table,
            pi=pi,
            common_rest=rest_client,
            configurations={
                'distribute_refreshed_perps': True
            }
        )
        self.rest = rest_client
        self._all_perps = LockedState(self.rest.get_all_perpetuals())
        self._refresh_perpetuals()

        self._orderbook_depth = int(os.environ.get("LIGHTER_ORDERBOOK_DEPTH", 10))
        self.market_data = wss.LighterMarketDataWss(
            order_book_update_callback=self._order_book_update_callback
        )
        self.market_data.run(main_thread=False)

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
    @staticmethod
    def _products_version(args: ArgsObject) -> dict:
        """
        Returns the version of the dispatcher and its components.
        """
        _ = args
        return {
            'argus': argus_version,
            'lighter_dispatcher': __version__,
            'sidecars': {}
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
