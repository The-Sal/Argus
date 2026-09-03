"""
Hyperliquid – This module will implement the full Dispatcher + Trading API.
This module is still under development and is being built as Phase 1.0 of Argus v2.
Track the PR for hyperliquid [here](https://github.com/The-Sal/Argus/pull/96)
"""
from argus._argus_utils import ArgsObject
from argus import __version__ as argus_version
from argus.perpetuals.hyper import _errors as _ers
from argus.perpetuals.hyper import _classes as _cls
from argus.perpetuals.hyper.rest import HyperLiquidRest
from argus.perpetuals.shared import BaseDispatcher, ers as _shared_ers, PrintInterface

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
      "correlation_id": "<uuid>" // None if the request errors before packet was processed, or a pushed response
    }

    """

    def __init__(self, wallet_address: str, private_key: str, host: str = "localhost", port: int = 9972):

        routing_table = {
            # Meta Functions
            'products_version': self._products_version,
            # Information Functions
            'get_dexs': self._get_dexs,
            'get_perpetuals_for_dex': self._get_perpetual_for_dex,
            'get_funding_rates_for_all_perpetuals': self._get_funding_rates_for_all_perps,
            'perpetual_info': self._perp_info,
            # Account Info      
            # 'get_account_info': self._get_account_info,
            # 'get_account_balance': self._get_account_balance,
            # 'get_account_positions': self._get_account_positions
            # Trading Functions (TBD)
        }
        
        super().__init__(
            host=host,
            port=port,
            routing_table=routing_table
        )
        self.rest = HyperLiquidRest(wallet_address, private_key)
        self._all_perps = self.rest.get_all_perpetuals()

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


if __name__ == '__main__':
    from dotenv import load_dotenv
    import os
    if not load_dotenv():
        print("Error loading .env file")
    dispatcher = HyperLiquidDispatcher(
        wallet_address=os.environ['HYPERLIQUID_WALLET_ADDRESS'],
        private_key=os.environ['HYPERLIQUID_PRIVATE_KEY']
    )
    dispatcher.run_server()

