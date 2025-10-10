import os
import time

from argus.polymarket import *


def tPoly(resolve_max: int = 2):
    api = PolymarketAPI(
        private_key=os.environ['POLYMARKET_PRIVATE_KEY'],
        proxy_funder=os.environ['POLYMARKET_PROXY_FUNDER']
    )
    print('Filtering for active markets...')
    real_markets = api.filter_markets_by_close_date(max_days=7)
    print('Active markets:', len(real_markets))
    resolved = 0
    pos = 0
    while True:
        market = real_markets[pos]
        pos += 1
        df = api.resolve_market(market)
        if df is not None:
            print(f"\nMarket: {market.question}")
            # pretty print the DataFrame
            print(df)
            resolved += 1
        else:
            print(f"Failed to resolve market: {market.question}")
        if resolved >= resolve_max:
            break
        time.sleep(0.2)



if __name__ == '__main__':
    tPoly(resolve_max=5)