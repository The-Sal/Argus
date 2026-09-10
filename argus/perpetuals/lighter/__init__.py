"""
Lighter (zkLighter) – Phase 1.0 of Argus v2, mirroring the HyperLiquid dispatcher
where Lighter's architecture actually matches it. Track the PR https://github.com/The-Sal/Argus/pull/96

Unlike Hyperliquid, Lighter has no HIP-3-style builder-deployed dexes -- it is a single
unified exchange (one account/margin system) with every market listed flat under that
one venue. Anywhere HyperLiquidDispatcher takes a dex_name / assembles per-dex data
(get_dexs, the dex_name param on get_perpetuals_for_dex, the 4-call perp_info), there is
no Lighter analog, so those are intentionally absent here rather than stubbed out. See
docs/Hyperliquid_and_Lighter_HYPE_Trading_API_Report.md section 7 for details.
"""
from argus._argus_utils import ArgsObject
from argus import __version__ as argus_version
from argus.perpetuals.lighter.rest import LighterRest
from argus.perpetuals.shared import BaseDispatcher, ers as _shared_ers, PrintInterface


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
            common_rest=rest_client
        )
        self.rest = rest_client
        self._all_perps = self.rest.get_all_perpetuals()
        self._refresh_perpetuals()

    ########################################
    # INTERNAL SERVER FUNCTIONS & Callbacks
    ########################################

    def subscription_expired(self, channel_id):
        """
        This function is called when a subscription expires.
        :param channel_id: The ID of the expired subscription
        """
        pass

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

        perpetuals = self._all_perps.perpetuals

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

        funding_rate_sorted = self._all_perps.sorted_by_funding_rate()
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
            perp = self._all_perps.get(symbol)
        else:
            perp = next((p for p in self._all_perps if p.market_id == market_id), None)

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
