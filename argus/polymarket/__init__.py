"""
Polymarket API
"""
import os
import random
import sys
import json
import tqdm
import time
import base64
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from argus.capital import DomainCache, NotKey
from py_clob_client.exceptions import PolyApiException
from argus.polymarket._types import PMarket, PMarketToken
from py_clob_client.clob_types import BookParams, OrderBookSummary

assert load_dotenv()
_POLYCACHE = DomainCache('Polymarket')


# HOST = "https://clob.polymarket.com"
# CHAIN_ID = 137
# PRIVATE_KEY = os.environ['POLYMARKET_PRIVATE_KEY']
# PROXY_FUNDER = os.environ['POLYMARKET_PROXY_FUNDER']
#
# client = ClobClient(
#     HOST,  # The CLOB API endpoint
#     key=PRIVATE_KEY,  # Your wallet's private key
#     chain_id=CHAIN_ID,  # Polygon chain ID (137)
#     signature_type=1,  # 1 for email/Magic wallet signatures
#     funder=PROXY_FUNDER  # Address that holds your funds
# )
# client.set_api_creds(client.create_or_derive_api_creds())

class PolymarketAPI:
    def __init__(self, private_key, proxy_funder, host='https://clob.polymarket.com', chain_id=137):
        self.client = ClobClient(
            host,
            key=private_key,
            chain_id=chain_id,
            signature_type=1,
            funder=proxy_funder
        )
        self.client.set_api_creds(self.client.create_or_derive_api_creds())
        self._rate_limit = 0.2  # seconds between requests to avoid rate limiting

    def get_markets(self, next_cursor=None):
        if next_cursor is None:
            return self.client.get_markets()
        else:
            return self.client.get_markets(next_cursor=next_cursor)

    @_POLYCACHE.cache_decorator(func_uuid='PolymarketAPI.enumerate_all_markets', expiration=60 * 60 * 24)
    def enumerate_all_markets(self) -> list[PMarket]:
        all_markets = []
        next_cursor = None
        total_markets = 0
        try:
            total_cached = _POLYCACHE.get('total_markets_length')
        except NotKey:
            total_cached = 0

        iterator = tqdm.tqdm(total=total_cached)

        def _write_progress(x):
            if total_cached <= 0:
                sys.stdout.write(f"\r{x}")
                sys.stdout.flush()
            else:
                iterator.set_description(x)

        try:
            while True:
                response = self.get_markets(next_cursor=next_cursor)
                all_markets.extend(response['data'])
                if total_cached > 0:
                    iterator.update(len(response['data']))
                total_markets += len(response['data'])
                next_cursor = response.get('next_cursor')
                decoded_cursor = float(base64.b64decode(next_cursor).decode('utf-8')) if next_cursor else 'None'
                if decoded_cursor == -1:
                    _write_progress(
                        'Polymarket API indicates no more markets to fetch. Found {} markets.'.format(total_markets))
                    break
                msg = f"Fetched {len(response['data'])} markets, total so far: {total_markets}. Next cursor: {next_cursor}"
                _write_progress(msg)
                time.sleep(self._rate_limit)  # Rate limiting
                if not next_cursor:
                    _write_progress('\n')
                    break
        except KeyboardInterrupt:
            _write_progress('\n')
            print('Interrupted by user, stopping...')
            print('Returning markets fetched so far...')
        except PolyApiException as e:
            _write_progress('\n')
            print(f'Polymarket API error: {e}')
            print('Returning markets fetched so far...')

        if len(all_markets) > total_cached:
            _POLYCACHE.set('total_markets_length', len(all_markets))
        iterator.close()
        return list(map(lambda x: PMarket(x), all_markets))


    def resolve_market(self, market: PMarket):
        """Resolves the market and fills the PMarket's .df attribute with the order book data."""
        if not market.active:
            # print('Market is not active, cannot resolve.')
            return

        tokens = market.tokens
        order_data = {}
        for token in tokens:
            try:
                book = self.client.get_order_book(token.token_id)
                print(book)
            except Exception as e:
                # print('Failed tor resolve market:', e, 'market_slug:', market.market_slug)
                continue


if __name__ == '__main__':
    PRIVATE_KEY = os.environ['POLYMARKET_PRIVATE_KEY']
    PROXY_FUNDER = os.environ['POLYMARKET_PROXY_FUNDER']

    # _POLYCACHE.invalidate_key(_POLYCACHE.generate_key('PolymarketAPI.enumerate_all_markets'))

    api = PolymarketAPI(PRIVATE_KEY, PROXY_FUNDER)
    all_markets = api.enumerate_all_markets()
    print(f"Total markets fetched: {len(all_markets)}")
    # json.dump(all_markets, open('all_polymarket_markets.json', 'w'), indent=4)
    books: list[BookParams] = []
    for market in all_markets:
        if market.active:
            for token in market.tokens:
                books.append(BookParams(token_id=token.token_id))


    get_all_books = api.client.get_order_books(books[:50])
    for book in get_all_books:
        print(book)
    print(get_all_books)
