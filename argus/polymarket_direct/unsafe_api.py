"""
The following module provides an interface to interact with scraped/reverse-engineered Polymarket data. This can
be here for various reasons. For example, for the btc15min markets the resolution source is data.chain.link that is
not publicly available OHLCV data, nor does the polymarket api provide the to beat prices since these are
set JIT depending on the prior market. But for the frontend UI they do have to deliver all this data ergo we
are going to reverse that frontend to get access to the underlying data that is important for making trading
decisions on these markets. 'Most' of these endpoints aim to be cached and fail with joy to avoid providing wrong
data. In the specific instance of the `get_price_to_beat` method during HAR analysis, the underlying endpoint was
exposed. However, the frontend also embeds it within the HTML of the page, which is more stable and doesn't require forging
any special tokens outside the regular url users go to.

Note: Technically we can and have the HAR files to reverse engineer and break down the chain link endpoints and get
exactly the data we want, but then we would be tying

"""
import json
import logging
import requests
from datetime import datetime
from urllib.parse import urlencode
from argus.cache_sys import DomainCache, CACHE
from argus.wireproxy.wrapper import update_request_session_proxy


class UnsafeException(Exception):
    pass

class UnableToReachPolymarket(UnsafeException):
    pass


_unsafe_api_cache = DomainCache(domain='POLYMARKET_UNSAFE_API', cache=CACHE)

class UnsafePolyMarket:
    """
    A class to interact with scraped polymarket data not provided by the official api.
    """

    def __init__(self):
        self.session = requests.Session()
        update_request_session_proxy(
            idx='POLYMARKET',
            session=self.session,
            verbose=False
        )

    @_unsafe_api_cache.cache_decorator(
        func_uuid='get_price_to_beat',
        should_cache_function=lambda price: isinstance(price, float)
    )
    def get_price_to_beat(self, slug: str,) -> float:
        """
        Scrapes from the polymarket frontend the price to beat for any Up/Down market.
        If the market is not an up/down market may result in undefined behavior; moreover, in the event
        the market is already expired, a warning will be raised.


        :param slug: The slug of the market, e.g. "btc-updown-15m-1769111100"
        :return:
        """
        response = self.session.get(f"https://polymarket.com/event/{slug}")
        if response.status_code != 200:
            raise UnableToReachPolymarket(f"Unable to reach Polymarket for slug {slug}. Status code: {response.status_code}")

        # As of Feb 2026 Polymarket embeds the price to beat within the HTML at the bottom of the page
        # it will start with '{"props":{"pageProps":' and then the end of the script will be ended with '</script></body></html>'
        try:
            start_index = response.text.index('{"props":{"pageProps":')
            end_index = response.text.index('</script></body></html>')
            json_str = response.text[start_index:end_index]
            json_data = json.loads(json_str)
        except Exception as e:
            raise UnableToReachPolymarket(f"Unable to parse price to beat for slug {slug}. Error: {str(e)}")

        # There are many nested pointless fields within this JSON and even duplicates of our key so we are
        # going to rely on the structure of the JSON to get what we want.
        # This is roughly what we are looking for with `openPrice` being the price to beat.
        # {
        #     "dehydratedAt": 1770757719937,
        #     "state": {
        #         "data": {
        #             "openPrice": 68703.7235947401,
        #             "closePrice": null
        #         },
        #         "dataUpdateCount": 1,
        #         "dataUpdatedAt": 1770757719725,
        #         "error": null,
        #         "errorUpdateCount": 0,
        #         "errorUpdatedAt": 0,
        #         "fetchFailureCount": 0,
        #         "fetchFailureReason": null,
        #         "fetchMeta": null,
        #         "isInvalidated": false,
        #         "status": "success",
        #         "fetchStatus": "idle"
        #     },
        #     "queryKey": [
        #         "crypto-prices",
        #         "price",
        #         "BTC",
        #         "2026-02-10T21:00:00Z",
        #         "fifteen",
        #         "2026-02-10T21:15:00Z"
        #     ],
        #     "queryHash": "[\"crypto-prices\",\"price\",\"BTC\",\"2026-02-10T21:00:00Z\",\"fifteen\",\"2026-02-10T21:15:00Z\"]"
        # }


        props = json_data.get('props', {})
        page_props = props.get('pageProps', {})
        dehydrated_state = page_props.get('dehydratedState', {})
        queries = dehydrated_state.get('queries', [])
        if len(queries) == 0:
            raise UnableToReachPolymarket(f"No queries found in the JSON data for slug {slug}. "
                                          f"This may be because polymarket has not updated yet, "
                                          f"trying again later")

        for query in queries:
            data = query.get('state', {}).get('data', {})
            try:
                open_price = data.get('openPrice', None)
                close_price = data.get('closePrice', None)
                if close_price is not None:
                    logging.warning(
                        f"Close price is not None for slug {slug}. This may indicate that the market has already expired and you are using the wrong data. ".format(slug=slug)
                    )
                if open_price is not None:
                    return open_price
            except AttributeError:
                pass
        return None

    @staticmethod
    def _format_utc_iso(dt: datetime) -> str:
        """Format datetime as ISO 8601 with 'Z' suffix for UTC."""
        if dt.tzinfo:
            return dt.isoformat().replace('+00:00', 'Z')
        logging.warning(
            "Timezone-naive datetime passed to _format_utc_iso. Assuming UTC. "
            "Consider using timezone-aware datetimes for clarity."
        )
        return dt.isoformat() + 'Z'

    @_unsafe_api_cache.cache_decorator(
        func_uuid='build_crypto_price_url_and_get_price',
        should_cache_function=lambda price: isinstance(price, float)
    )
    def build_crypto_price_url_and_get_price(self, symbol, variant, start_date: datetime, end_date: datetime) -> float:
        """
        Build the crypto price URL and get the price to beat from the Polymarket API.

        Args:
            symbol (str): The crypto symbol, e.g. 'BTC'
            variant (str): The variant type, e.g. 'hourly' or 'daily'
            start_date (datetime): The start date and time for the event. (UTC)
            end_date (datetime): The end date and time for the event. (UTC)
        """

        event_start_time = self._format_utc_iso(start_date)
        end_date_iso = self._format_utc_iso(end_date)
        url = self.build_crypto_price_url(symbol, event_start_time, variant, end_date_iso)
        logging.info(f"Built crypto price URL: {url}")
        response = self.session.get(url)
        if response.status_code != 200:
            raise UnableToReachPolymarket(f"Unable to reach Polymarket for URL {url}. Status code: {response.status_code}")

        try:
            data = response.json()
            price_to_beat = data.get('priceToBeat', None)
            open_price = data.get('openPrice', None)
            if price_to_beat is not None:
                return price_to_beat
            elif open_price is not None:
                logging.warning(
                    f"Price to beat not found in the response for URL {url}, but open price is available. "
                    f"Using open price as a fallback. Response: {data}"
                )
                return open_price
            else:
                raise UnableToReachPolymarket(f"Price to beat not found in the response for URL {url}. Response: {data}")
        except Exception as e:
            raise UnableToReachPolymarket(f"Unable to parse the response for URL {url}. Error: {str(e)}")




    @staticmethod
    def build_crypto_price_url(symbol, event_start_time, variant, end_date):
        """
        Construct a Polymarket crypto price API URL with the given parameters.

        Args:
            symbol (str): The crypto symbol, e.g. 'BTC'
            event_start_time (str): The ISO 8601 start time, e.g. '2026-02-10T21:00:00Z'
            variant (str): The variant type, e.g. 'hourly' or 'daily'
            end_date (str): The ISO 8601 end time, e.g. '2026-02-10T22:00:00Z'

        Returns:
            str: Fully constructed API URL.
        """
        base_url = "https://polymarket.com/api/crypto/crypto-price"

        params = {
            "symbol": symbol,
            "eventStartTime": event_start_time,
            "variant": variant,
            "endDate": end_date
        }

        url = f"{base_url}?{urlencode(params)}"
        return url


if __name__ == '__main__':
    updown = UnsafePolyMarket()
    # https://polymarket.com/event/btc-updown-15m-1770757200
    # https://polymarket.com/event/btc-updown-15m-1770758100
    # https://polymarket.com/event/bitcoin-up-or-down-february-10-4pm-et
    # print(updown.get_price_to_beat("bitcoin-up-or-down-february-10-4pm-et"))
    # https://polymarket.com/event/
    print(updown.get_price_to_beat('btc-updown-5m-1771299600'))
    print(updown.build_crypto_price_url(
        symbol='btc',
        event_start_time='2026-02-17T03:25:00Z',
        end_date='2026-02-17T03:30:00Z',
        variant='fiveminute'
    ))