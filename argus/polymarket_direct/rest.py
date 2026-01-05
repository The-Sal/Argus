import requests
from argus.capital import DomainCache
from py_clob_client.client import ClobClient
from argus.wireproxy import wrapper as wp_wrappers
from argus.polymarket_direct import _types as pm_types

REST_CACHE = DomainCache('polymarket_direct.rest')
endpoints = {
    'events': "https://gamma-api.polymarket.com/events?order=id&ascending=false&closed=false&limit={}&offset={}",
}



class PolyRestAPI:
    """
    A REST API client for interacting with Polymarket's CLOB via py_clob_client and other endpoints.
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

    @REST_CACHE.cache_decorator(
        func_uuid='_create_or_derive_api_creds',
        expiration=60*60,
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

