
"""
Hyperliquid – This module will implement the full Dispatcher + Trading API.
This module is still under development and is being built as Phase 1.0 of Argus v2.
Track the PR for hyperliquid [here](https://github.com/The-Sal/Argus/pull/96)
"""

import os
from tqdm import tqdm
from typing import List, Optional
from utils3.networking import Session
from argus.perpetuals.hyper import _classes as _cls

_ep = {
    'info': 'https://api.hyperliquid.xyz/info'
}


class HyperLiquidRest:
    def __init__(self, wallet_address: str, private_key: str):
        self.wallet_address = wallet_address
        self.private_key = private_key
        self.session = Session()
        self.session.headers = {
            'Content-Type': 'application/json',
        }

    def _post(self, body: dict):
        return self.session.post(url=_ep['info'], json=body).json()

    # --- dexes -----------------------------------------------------------

    def get_dexs(self) -> List[_cls.PerpDexConfig]:
        """All builder-deployed (HIP-3) perp dexes. Does not include the default dex,
        which is always represented by dex="" and has no PerpDexConfig of its own."""
        response: list = self._post({'type': 'perpDexs'})
        return [_cls.PerpDexConfig.from_dict(dex) for dex in response if dex is not None]

    # --- universe / metadata ----------------------------------------------

    def get_meta(self, dex: str = "") -> _cls.UniverseConfig:
        """Perpetuals metadata (universe + margin tables) for a single dex, without market data."""
        body = {'type': 'meta', 'dex': dex}
        return _cls.UniverseConfig.from_dict(self._post(body))

    def get_perpetuals_for_dex(self, dex: str = "") -> _cls.PerpDexSnapshot:
        """Universe + live market data (funding, mark price, open interest, ...) for one dex."""
        body = {'type': 'metaAndAssetCtxs', 'dex': dex}
        response: list = self._post(body)
        return _cls.PerpDexSnapshot.from_response(dex, response)

    def get_all_perpetuals(self) -> _cls.PerpetualsIndex:
        """All perpetuals across the default dex and every HIP-3 dex, as one sortable/filterable index."""
        dex_names = [""] + [dex.name for dex in self.get_dexs()]
        snapshots = [
            self.get_perpetuals_for_dex(dex_name)
            for dex_name in tqdm(dex_names, desc='Fetching perpetuals for each dex')
        ]
        return _cls.PerpetualsIndex.from_snapshots(snapshots)

    # --- funding rates -----------------------------------------------------

    def get_funding_history(
        self, coin: str, start_time_ms: int, end_time_ms: Optional[int] = None
    ) -> List[_cls.FundingHistoryEntry]:
        body = {'type': 'fundingHistory', 'coin': coin, 'startTime': start_time_ms}
        if end_time_ms is not None:
            body['endTime'] = end_time_ms
        response: list = self._post(body)
        return [_cls.FundingHistoryEntry.from_dict(entry) for entry in response]

    def get_predicted_fundings(self) -> List[_cls.PredictedFunding]:
        """Predicted next funding rates for each coin, across Hyperliquid and external CEXs.
        Only supported for the default (first) perp dex."""
        response: list = self._post({'type': 'predictedFundings'})
        return [_cls.PredictedFunding.from_pair(pair) for pair in response]

    # --- misc dex / coin info ----------------------------------------------

    def get_perps_at_open_interest_cap(self, dex: str = "") -> List[str]:
        return self._post({'type': 'perpsAtOpenInterestCap', 'dex': dex})

    def get_perp_dex_limits(self, dex: str) -> _cls.PerpDexLimits:
        """`dex` must be a non-empty, builder-deployed (HIP-3) dex name."""
        return _cls.PerpDexLimits.from_dict(self._post({'type': 'perpDexLimits', 'dex': dex}))

    def get_perp_dex_status(self, dex: str = "") -> _cls.PerpDexStatus:
        return _cls.PerpDexStatus.from_dict(self._post({'type': 'perpDexStatus', 'dex': dex}))

    def get_perp_deploy_auction_status(self) -> _cls.PerpDeployAuctionStatus:
        return _cls.PerpDeployAuctionStatus.from_dict(self._post({'type': 'perpDeployAuctionStatus'}))

    def get_perp_annotation(self, coin: str) -> Optional[_cls.PerpAnnotation]:
        """Returns None for coins with no annotation (e.g. most default-dex coins)."""
        return _cls.PerpAnnotation.from_dict(self._post({'type': 'perpAnnotation', 'coin': coin}))

    def get_perp_categories(self) -> List[_cls.PerpCategory]:
        response: list = self._post({'type': 'perpCategories'})
        return [_cls.PerpCategory.from_pair(pair) for pair in response]

    def get_perp_concise_annotations(self) -> List[_cls.PerpConciseAnnotation]:
        response: list = self._post({'type': 'perpConciseAnnotations'})
        return [_cls.PerpConciseAnnotation.from_pair(pair) for pair in response]


if __name__ == '__main__':
    from dotenv import load_dotenv
    load_dotenv("/Users/Salman/Projects/Imperium/Argus/.env")
    rest = HyperLiquidRest(wallet_address=os.environ['HYPERLIQUID_WALLET_ADDRESS'], private_key=os.environ['HYPERLIQUID_PRIVATE_KEY'])

    index = rest.get_all_perpetuals()
    print('There are', len(index), 'perpetuals available across', len({p.dex for p in index}), 'dex(es).')

    print('\nTop 5 by funding rate:')
    for p in index.highest_funding(5):
        print(f'  {p.dex or "hyperliquid":<12} {p.name:<12} funding={p.funding_rate!s:<14} mark={p.mark_price}')

    print('\nBottom 5 by funding rate:')
    for p in index.lowest_funding(5):
        print(f'  {p.dex or "hyperliquid":<12} {p.name:<12} funding={p.funding_rate!s:<14} mark={p.mark_price}')
