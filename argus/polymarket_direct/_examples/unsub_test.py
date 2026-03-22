#####################################
# DEPRECIATION NOTICE:
# The following code relied on classes no longer supported.
# The last cut of Argus that supports this was '4fee5993b4658d7f89d010e638740aa80a3e8e2c'
#####################################

# import os
# import time
# import pickle
# from tqdm import tqdm
# from datetime import datetime, UTC
# from argus.polymarket_direct import EnhancedPM, PolymarketEvent
#
# class VeryUglyCache:
#     def __init__(self):
#         self.store = {}
#         self.load_cache()
#
#     def get(self, key: str):
#         value = self.store.get(key, None)
#         expiration = self.store.get(f"{key}_expiration", 0)
#         if value is not None and time.time() < expiration:
#             return value
#         return None
#
#     def set(self, key: str, value, expiration: int = 3600):
#         self.store[key] = value
#         self.store[f"{key}_expiration"] = time.time() + expiration
#         self.save_cache()
#
#
#     def save_cache(self):
#         with open('very_ugly_cache.pkl', 'wb') as f:
#             pickle.dump(self.store, f)
#
#     def load_cache(self):
#         if os.path.exists('very_ugly_cache.pkl'):
#             with open('very_ugly_cache.pkl', 'rb') as f:
#                 self.store = pickle.load(f)
#
# ugly_cache = VeryUglyCache()
#
# pm = EnhancedPM(private_key=os.environ['POLYMARKET_PRIVATE_KEY'],
#                 proxy_funder=os.environ['POLYMARKET_PROXY_FUNDER'])
#
# at_least_two_events: list[PolymarketEvent] = []
# offset = 0
# dt_now = datetime.now(UTC)
# current_time = dt_now
#
#
# def wrapped_btc_fetch():
#     def _inner_all_btc_hourly_events():
#         return all_btc_hourly_events()
#
#     cached_result = ugly_cache.get('all_btc_hourly_events')
#     if cached_result:
#         print("Using cached all_btc_hourly_events")
#         return cached_result
#     result = _inner_all_btc_hourly_events()
#     ugly_cache.set('all_btc_hourly_events', result)
#     return _inner_all_btc_hourly_events()
#
# def all_btc_hourly_events():
#     all_bitcoin_hourly = []
#     raw_data_dump = []
#     offset_val = 0
#     offset_step = 150
#     total_fetched = 0
#     while True:
#         rq = pm.fetch_events(limit=offset_step, offset=offset_val,
#                              debug_raw_callback=lambda x: raw_data_dump.append(x))
#         total_fetched += len(rq)
#         if len(rq) == 0:
#             break
#
#         # noinspection all
#         for market in rq:
#             try:
#                 if 'bitcoin-up-or-down' in market.ticker and '-et' in market.ticker:
#                     all_bitcoin_hourly.append(market)
#             except TypeError:
#                 pass
#
#         print(f"\rFetched {total_fetched} markets, total bitcoin hourly markets: {len(all_bitcoin_hourly)}", end='')
#         offset_val += offset_step
#     print('' * 100)
#
#     cleaned_raw_dump = []
#     for raw in raw_data_dump:
#         if 'bitcoin-up-or-down' in raw['ticker']:
#             cleaned_raw_dump.append(raw)
#
#     def _sorted_by_time_key(sorting_event: PolymarketEvent):
#         [x.convert_to_datetime() for x in sorting_event.markets]
#         return sorting_event.markets[0].eventStartTime
#
#     all_bitcoin_hourly.sort(key=_sorted_by_time_key)
#
#     return all_bitcoin_hourly
#
# def get_all_btc_live_events():
#     all_btcoin_hourly = wrapped_btc_fetch()
#
#     print('*' * 100)
#     for market in all_btcoin_hourly:
#         startTime = market.markets[0].eventStartTime
#         endTime = market.markets[0].endDate
#         diff = startTime - current_time
#         seconds_till_start = diff.total_seconds()
#         print(
#             f"Market: {market.ticker}, Starts in: {seconds_till_start / 3600:.2f} hours at "
#             f"{datetime.strftime(startTime, '%Y-%m-%d %H:%M:%S UTC')}",
#             f"Ends in: {(endTime - current_time).total_seconds() / 3600:.2f} hours at {datetime.strftime(endTime, '%Y-%m-%d %H:%M:%S UTC')} "
#         )
#
#     # find everything that is currently live
#     print('\n' + '=' * 100 + '\n')
#     print("Currently Live Events:")
#     live_markets = []
#     for market in all_btcoin_hourly:
#         startTime = market.markets[0].eventStartTime
#         endTime = market.markets[0].endDate
#         if startTime < current_time < endTime:
#             print(
#                 f"Market: {market.ticker}, Started at "
#                 f"{datetime.strftime(startTime, '%Y-%m-%d %H:%M:%S UTC')}, "
#                 f"Ends at {datetime.strftime(endTime, '%Y-%m-%d %H:%M:%S UTC')} "
#             )
#             live_markets.append(market)
#     print('\n' + '=' * 100 + '\n')
#
#     return live_markets
#
# def main():
#
#     live_markets = get_all_btc_live_events()
#     last_map = {}
#
#     def print_market_update(data):
#         print("Market Update:", data)
#
#     all_tokens = []
#     pm.start_market_ws()
#     pm.market_open_semaphore.acquire()
#     for event in live_markets:
#         clob_tokens = event.markets[0].clobTokenIds
#         if clob_tokens:
#             print('Adding clob tokens for event:', event.title, clob_tokens)
#             all_tokens.extend(clob_tokens)
#         else:
#             print("No clob tokens for event:", event.title)
#
#     for token in tqdm(all_tokens, desc="Subscribing to market data"):
#         print("Subscribing to token:", token)
#         pm.subscribe_to_market_data([token], print_market_update)
#         time.sleep(1)
#
#     time.sleep(60)
#
#     print('\n' + '=' * 100 + '\n')
#     print('ATTEMPTING TO UNSUBSCRIBE FROM ALL TOKENS')
#     for token in tqdm(all_tokens, desc="Unsubscribing from market data"):
#         print("Unsubscribing from token:", token)
#         pm.unsubscribe_from_market_data([token])
#         time.sleep(1)
#
#     print('YOU SHOULD NO LONGER SEE UPDATES')
#     time.sleep(60)
#     print('\n' + '=' * 100 + '\n')
#
#
# if __name__ == '__main__':
#     main()