
"""
Phase 1.0 of Argus v2.0, track the PR https://github.com/The-Sal/Argus/pull/96
"""

import time
from utils3.networking import Session
from typing import Dict, List, Optional
from argus.perpetuals.lighter import _classes as _cls

_ep = {
    'base': 'https://mainnet.zklighter.elliot.ai',
}


class LighterRest:
    """Public market-data client for the Lighter exchange REST API.

    All endpoints used here are public (no API key / signing needed) -- they
    only cover market metadata, prices, and funding, not order placement.
    """

    def __init__(self, base_url: str = _ep['base']):
        self.base_url = base_url
        self.session = Session()
        self.session.headers = {
            'Content-Type': 'application/json',
        }

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        return self.session.get(url=f'{self.base_url}{path}', params=params).json()

    # --- markets -------------------------------------------------------------

    def get_markets(self) -> List[_cls.Perpetual]:
        """All perpetual markets' metadata + live data (mark price, index price, 24h stats, ...)."""
        response = self._get('/api/v1/orderBookDetails', params={'filter': 'perp'})
        markets = [_cls.Perpetual.from_dict(m) for m in response['order_book_details']]
        assert all(p.market.is_perp for p in markets), 'orderBookDetails?filter=perp returned a non-perp market'
        return markets

    # --- funding rates ---------------------------------------------------------

    def get_funding_rates(self) -> List[_cls.FundingRateEntry]:
        """Current funding rate for every market, on Lighter and the external CEXs
        (binance, bybit, hyperliquid) it benchmarks against."""
        response = self._get('/api/v1/funding-rates')
        return [_cls.FundingRateEntry.from_dict(e) for e in response['funding_rates']]

    def get_cross_exchange_fundings(self) -> List[_cls.CrossExchangeFunding]:
        """`get_funding_rates()`, grouped by market so each market's rates across
        exchanges can be compared side by side."""
        by_market: Dict[int, _cls.CrossExchangeFunding] = {}
        for entry in self.get_funding_rates():
            group = by_market.setdefault(
                entry.market_id, _cls.CrossExchangeFunding(market_id=entry.market_id, symbol=entry.symbol)
            )
            group.rates.append(entry)
        return list(by_market.values())

    def get_funding_history(
        self,
        market_id: int,
        start_timestamp: int,
        end_timestamp: Optional[int] = None,
        resolution: str = '1h',
        count_back: int = 0,
    ) -> List[_cls.FundingHistoryEntry]:
        """Historical funding for one market, in [start_timestamp, end_timestamp] (unix
        seconds). `end_timestamp` defaults to now. `resolution` is "1h" or "1d"; at most
        750 entries are returned per call. `count_back=0` returns everything in range."""
        params = {
            'market_id': market_id,
            'resolution': resolution,
            'start_timestamp': start_timestamp,
            'end_timestamp': end_timestamp if end_timestamp is not None else int(time.time()),
            'count_back': count_back,
        }
        response = self._get('/api/v1/fundings', params=params)
        return [_cls.FundingHistoryEntry.from_dict(market_id, e) for e in response['fundings']]

    # --- combined convenience ---------------------------------------------------

    def get_all_perpetuals(self) -> _cls.PerpetualsIndex:
        """All perpetual markets with Lighter's current funding rate attached,
        as one sortable/filterable index."""
        perpetuals = self.get_markets()
        lighter_rates = {e.market_id: e.rate for e in self.get_funding_rates() if e.exchange == 'lighter'}
        for p in perpetuals:
            p.funding_rate = lighter_rates.get(p.market_id)
        return _cls.PerpetualsIndex(perpetuals)


if __name__ == '__main__':
    rest = LighterRest()

    index = rest.get_all_perpetuals()
    print(f'There are {len(index)} perpetual markets on Lighter ({len(index.excluding_inactive())} active).')

    print('\nTop 5 by funding rate:')
    for p in index.highest_funding(5):
        print(f'  {p.name:<10} funding={p.funding_rate!s:<14} mark={p.mark_price}')

    print('\nBottom 5 by funding rate:')
    for p in index.lowest_funding(5):
        print(f'  {p.name:<10} funding={p.funding_rate!s:<14} mark={p.mark_price}')
