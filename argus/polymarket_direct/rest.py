import os
import time
import logging
import requests
from termcolor import colored
from argus.capital import DomainCache
from py_clob_client.client import ClobClient
from argus.wireproxy import wrapper as wp_wrappers
from argus.polymarket_direct import _types as pm_types
from argus._argus_utils import throw_fuss, macos_notification_with_custom_sound

REST_CACHE = DomainCache('polymarket_direct.rest')
endpoints = {
    'events': "https://gamma-api.polymarket.com/events?order=id&ascending=false&closed=false&limit={}&offset={}",
    'geo_block_test': 'https://polymarket.com/api/geoblock'
}
qw = '[{}]'.format(__name__)


class IPSafety:
    def __init__(self):
        self.KNOWN_BAD_REGIONS = ["US", "GB", "FR", "DE", "IT", "BE", "PL", "AU", "SG", "TW",
                                  "TH", "RU", "BY", "CU", "IR",
                                  "IQ", "KP", "SY", "VE", "MM", "LY", "SD", "SS", "SO",
                                  "YE", "ZW", "LB", "ET", "NI", "BI", "CF", "CD", "UM", "AE"]

        self.session = requests.Session()
        wp_wrappers.update_request_session_proxy(
            idx='POLYMARKET',
            session=self.session,
            verbose=False
        )

    def get_ip_info(self) -> dict:
        """
        Fetch IP information from the ipinfo.io service.
        :return: A dictionary containing IP information.
        """
        response = self.session.get('https://ipinfo.io/json')
        response.raise_for_status()
        return response.json()

    def is_ip_in_bad_region(self, ip_info: dict) -> bool:
        """
        Determine if the IP is located in a known bad region.
        :param ip_info: A dictionary containing IP information.
        :return: True if the IP is in a bad region, False otherwise.
        """
        country = ip_info.get('country', '')
        return country in self.KNOWN_BAD_REGIONS


class PolyRestAPI:
    """
    A REST API client for interacting with Polymarket's CLOB via py_clob_client and other endpoints.

    Available Endpoints and Features:
    – Fetch Events: Retrieve a list of events from Polymarket.
    – Execute Orders
    – Get Order Status
    – Cancel Orders
    – Order history

    Note: All orders routed through `PolyRestAPI` are stored and persistently cached
    within this class using the `DomainCache` system. Ergo, the 'return' from the orders(s)
    function can be specified to only use 'cached' orders by default they fetch live data and
    sync with the cache.


    """

    def __init__(self, private_key, proxy_funder, host='https://clob.polymarket.com', chain_id=137):
        self.private_key = private_key
        self.proxy_funder = proxy_funder
        self.session = requests.Session()
        wp_wrappers.update_request_session_proxy(
            idx='POLYMARKET',
            session=self.session
        )
        self._make_httpx_clob_client()
        self.safety = IPSafety()


        print(qw, 'Starting Polymarket REST API client initialization...')
        print(qw, 'Checking IP information against hardcoded geo-blocked regions via ipinfo.io...')
        ip_info = self.safety.get_ip_info()
        ip_info['ip'] = 'REDACTED'  # Redact IP for privacy in logs
        if self.safety.is_ip_in_bad_region(ip_info):
            msg = ("The IP returned from ipinfo.io compared against built-in geo-blocked regions indicates this"
                   " IP address may face issues accessing Polymarket. This maybe wrong, the next check will"
                   " attempt to connect directly to polymarket.com to verify (in the next 5s) to double check. If"
                   "this address should not leak polymarket.com to the ISP TERMINATE NOW. To automatically terminate"
                   " when here set the env `POLYMARKET_PARANOID` to `true` before running.")
            if os.environ.get('POLYMARKET_PARANOID', 'false') == 'true':
                raise RuntimeError("THIS IP IS GEO-BLOCKED ACCORDING TO HARDCODED REGIONS AND `POLYMARKET_PARANOID` IS SET TO TRUE. "
                                   "TERMINATING FOR SAFETY. IP INFO: " + str(ip_info))
            else:
                print(qw, colored(msg, 'yellow', attrs=['bold', 'blink']))

        else:
            ip_info['ip'] = 'REDACTED'
            print(qw, f"IP info: {ip_info}")
            print(qw, "Proceeding to Polymarket geo-block check...")

        if os.environ.get('POLYMARKET_PROTECTION', 'true') == 'true':
            if self.check_geo_blocked():
                raise RuntimeError("The current IP is geo-blocked from accessing Polymarket.")
            else:
                print(qw, "The current IP is NOT geo-blocked from accessing Polymarket. Happy trading!")
        else:
            warning_msg = ("WARNING: YOU HAVE DISABLED POLYMARKET GEO-BLOCK PROTECTION CHECKS VIA THE "
                           "`POLYMARKET_PROTECTION` ENVIRONMENT VARIABLE (default: true). THIS WILL LEAD TO ORDERS NOT BEING REJECTED "
                           "AND POSSIBLE ISSUES WITH A ACTIVELY TRADED ACCOUNT IS LOGGED IN FROM A GEO-BLOCKED IP. "
                           "THE NEXT STEP INVOLVES DERIVING CREDENTIALS WHICH WILL EXPOSE YOUR ACCOUNT TO POLYMARKET"
                           "FROM THIS IP ADDRESS. THE PROGRAM WILL NOW SLEEP FOR 30s DO NOTHING TO PROCEED, TERMINATE OTHERWISE")
            throw_fuss(
                msg=colored(warning_msg, 'red', attrs=['bold', 'blink']),
                notify=False,
                title="WARNING: POLYMARKET GEO-BLOCK PROTECTION DISABLED"
            )
            macos_notification_with_custom_sound(
                title="WARNING: POLYMARKET GEO-BLOCK PROTECTION DISABLED",
                message=warning_msg,
                sound_name="Basso"
            )
            for i in range(30, 0, -1):
                print(qw, colored(f"Continuing in {i} seconds... terminate now to abort.", 'red', attrs=['bold']),
                      end='\r')
                time.sleep(1)
            print()

        self.clob = ClobClient(
            host,
            key=private_key,
            chain_id=chain_id,
            signature_type=1,
            funder=proxy_funder,
        )
        self._create_or_derive_api_creds()

    ###########################################
    # Utility Methods
    ###########################################

    def check_geo_blocked(self) -> bool:
        """
        Check if the current IP is geo-blocked from accessing Polymarket.
        :return: True if geo-blocked, False otherwise.
        """
        response = self.session.get(endpoints['geo_block_test'])
        response.raise_for_status()
        data = response.json()
        logging.info('Geo-block check response: %s', data)
        return data.get('blocked', False)

    @REST_CACHE.cache_decorator(
        func_uuid='_create_or_derive_api_creds',
        expiration=60 * 60,
        should_cache_function=lambda x: x is not None
    )
    def _create_or_derive_api_creds(self):
        response = self.clob.create_or_derive_api_creds()
        return response

    @staticmethod
    def _make_httpx_clob_client():
        """
        PyClobClient uses httpx for its HTTP requests; however, it does not expose a way to set up proxies.
        This function patches the internal httpx Client used by py_clob_client to use a proxy if specified
        via WireProxy.
        """
        from httpx import Client
        from py_clob_client.http_helpers import helpers

        proxy = wp_wrappers.start_proxy_and_return_bind('POLYMARKET')
        if proxy is not None:
            proxy = f'socks5://{proxy}'
        _client = Client(http2=2, proxy=proxy)
        setattr(helpers, '_http_client', _client)

    ###########################################
    # Public API Methods
    ###########################################

    def fetch_events(self, offset=0, limit=20, debug_raw_callback=None) -> list[pm_types.PolymarketEvent]:
        url = endpoints['events'].format(limit, offset)
        response = self.session.get(url)
        response.raise_for_status()
        returns = []
        for event in response.json():
            try:
                if debug_raw_callback:
                    debug_raw_callback(event)
                v = pm_types.PolymarketEvent.from_dict(event)
                returns.append(v)
            except Exception as e:
                print("Error parsing event:", e)

        return returns

    def place_order(self, token_id: str, price: float, size: float, side: str):
        """
        Place an order on the Polymarket CLOB.
        :param token_id: The ID of the token to trade.
        :param price: The price at which to place the order.
        :param size: The size of the order.
        :param side: The side of the order ('buy' or 'sell').
        :return:
        """
        pass


if __name__ == '__main__':
    from dotenv import load_dotenv

    load_dotenv()
    rest = PolyRestAPI(
        private_key=os.environ['POLYMARKET_PRIVATE_KEY'],
        proxy_funder=os.environ['POLYMARKET_PROXY_FUNDER']
    )

    pass
