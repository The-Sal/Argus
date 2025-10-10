"""
Polymarket API
"""
# import sys
# import tqdm
# import time
# import base64
# import pandas as pd
# from dotenv import load_dotenv
# from datetime import datetime, timezone
# from py_clob_client.client import ClobClient
# from argus.capital import DomainCache, NotKey
# from py_clob_client.exceptions import PolyApiException
# from argus.polymarket._types import PMarket, PMarketToken
# from py_clob_client.clob_types import BookParams, OrderBookSummary
#
# assert load_dotenv()
# _POLYCACHE = DomainCache('Polymarket')
#
#
# # HOST = "https://clob.polymarket.com"
# # CHAIN_ID = 137
# # PRIVATE_KEY = os.environ['POLYMARKET_PRIVATE_KEY']
# # PROXY_FUNDER = os.environ['POLYMARKET_PROXY_FUNDER']
# #
# # client = ClobClient(
# #     HOST,  # The CLOB API endpoint
# #     key=PRIVATE_KEY,  # Your wallet's private key
# #     chain_id=CHAIN_ID,  # Polygon chain ID (137)
# #     signature_type=1,  # 1 for email/Magic wallet signatures
# #     funder=PROXY_FUNDER  # Address that holds your funds
# # )
# # client.set_api_creds(client.create_or_derive_api_creds())
#
# class PolymarketAPI:
#     def __init__(self, private_key, proxy_funder, host='https://clob.polymarket.com', chain_id=137, order_book_depth=1):
#         self.client = ClobClient(
#             host,
#             key=private_key,
#             chain_id=chain_id,
#             signature_type=1,
#             funder=proxy_funder
#         )
#         self.client.set_api_creds(self.client.create_or_derive_api_creds())
#         self._rate_limit = 0.2  # seconds between requests to avoid rate limiting
#         self._order_book_depth = order_book_depth  # Depth of order book to fetch when resolving markets
#         if self._order_book_depth < 0:
#             raise ValueError("order_book_depth must be at least 1")
#
#     def get_markets(self, next_cursor=None):
#         if next_cursor is None:
#             return self.client.get_markets()
#         else:
#             return self.client.get_markets(next_cursor=next_cursor)
#
#     @_POLYCACHE.cache_decorator(func_uuid='PolymarketAPI.enumerate_all_markets', expiration=60 * 60 * 24)
#     def enumerate_all_markets(self) -> list[PMarket]:
#         all_markets = []
#         next_cursor = None
#         total_markets = 0
#         try:
#             total_cached = _POLYCACHE.get('total_markets_length')
#         except NotKey:
#             total_cached = 0
#
#         iterator = tqdm.tqdm(total=total_cached)
#
#         def _write_progress(x):
#             if total_cached <= 0:
#                 sys.stdout.write(f"\r{x}")
#                 sys.stdout.flush()
#             else:
#                 iterator.set_description(x)
#
#         try:
#             while True:
#                 response = self.get_markets(next_cursor=next_cursor)
#                 all_markets.extend(response['data'])
#                 if total_cached > 0:
#                     iterator.update(len(response['data']))
#                 total_markets += len(response['data'])
#                 next_cursor = response.get('next_cursor')
#                 decoded_cursor = float(base64.b64decode(next_cursor).decode('utf-8')) if next_cursor else 'None'
#                 if decoded_cursor == -1:
#                     _write_progress(
#                         'Polymarket API indicates no more markets to fetch. Found {} markets.'.format(total_markets))
#                     break
#                 msg = f"Fetched {len(response['data'])} markets, total so far: {total_markets}. Next cursor: {next_cursor}"
#                 _write_progress(msg)
#                 time.sleep(self._rate_limit)  # Rate limiting
#                 if not next_cursor:
#                     _write_progress('\n')
#                     break
#         except KeyboardInterrupt:
#             _write_progress('\n')
#             print('Interrupted by user, stopping...')
#             print('Returning markets fetched so far...')
#         except PolyApiException as e:
#             _write_progress('\n')
#             print(f'Polymarket API error: {e}')
#             print('Returning markets fetched so far...')
#
#         if len(all_markets) > total_cached:
#             _POLYCACHE.set('total_markets_length', len(all_markets))
#         iterator.close()
#         return list(map(lambda x: PMarket(x), all_markets))
#
#     def resolve_market(self, market: PMarket):
#         """Resolves the market and fills the PMarket's .df attribute with the order book data."""
#         tokens = market.tokens
#         df_data = []
#         for token in tokens:
#             try:
#                 book: OrderBookSummary = self.client.get_order_book(token.token_id)
#                 asks = book.asks[:self._order_book_depth]
#                 bids = book.bids[:self._order_book_depth]
#
#                 for ask in asks:
#                     df_data.append({
#                         'side': 'ask',
#                         'price': ask.price,
#                         'size': ask.size,
#                         'outcome': token.outcome
#                     })
#                 for bid in bids:
#                     df_data.append({
#                         'side': 'bid',
#                         'price': bid.price,
#                         'size': bid.size,
#                         'outcome': token.outcome
#                     })
#                 time.sleep(self._rate_limit)
#             except PolyApiException as e:
#                 print(f"Error fetching order book for token {token.token_id}: {e}")
#                 return None
#
#         df_header = ['side', 'price', 'size', 'outcome']
#         market.set_df(pd.DataFrame(df_data, columns=df_header))
#         return market.df
#
#     def filter_markets_by_close_date(self, max_days=30):
#         """Gets ALL markets and filters to those closing within max_days from now. Calls .enumerate_all_markets()."""
#         all_markets = self.enumerate_all_markets()
#         filtered = []
#         now = time.time()
#         for market in all_markets:
#             if not market.active:
#                 continue
#             end_date = getattr(market, 'end_date_iso', None) or getattr(market, 'end_date', None)
#             if end_date:
#                 if isinstance(end_date, str):
#                     end_timestamp = datetime.fromisoformat(end_date.replace('Z', '+00:00')).timestamp()
#                 else:
#                     end_timestamp = end_date
#                 days_diff = (end_timestamp - now) / 86400
#                 if 0 <= days_diff <= max_days:
#                     filtered.append(market)
#         return filtered
#
#
#
#
# class PolyDispatcher:
#     """
#     High-level TCP-based dispatcher for Polymarket API interactions.
#
#     This is the following client-API interface for PolyDispatcher:
#     Output Protocol: ~{JSON}L
#     Input Protocol: cmd:arg1,arg2,arg3...
#
#     Commands:
#         - enumerate_markets: Enumerates all markets available on Polymarket, cached for 24 hours. Returns list of market slugs
#         - filter_markets_by_close_date(max_days=30): Filters markets closing within max_days from now. Returns list of market slugs (internal call to enumerate_markets)
#         - resolve_market(market_id): Resolves a market by its ID and returns a DataFrame of the order book data. Returns base64-encoded CSV of the DataFrame
#         - get_market_details(market_id): Returns detailed information about a specific market may include order book data if resolved. Returns a JSON
#         - transpose_df(base64_csv): Transposes a base64-encoded CSV DataFrame into a type more easily workable with FxCDispatcher. Returns base64-encoded CSV of the transposed DataFrame
#
#
#     All commands are API rate-limited internally to avoid hitting Polymarket's rate limits. Client does not need to handle this.
#     There are no authentication requirements, pings, or heartbeats the client must handle.
#
#     """
#     def __init__(self, private_key, proxy_funder, host='https://clob.polymarket.com', chain_id=137, order_book_depth=1):
#         self.api = PolymarketAPI(private_key, proxy_funder, host, chain_id, order_book_depth)
#
#
#     # TBD.
#
