"""
Polymarket API client and dispatcher.


Warnings:
    - Polymarket Cache system is separate from the main Argus cache system.
    - Polymarket cache is stored in ~/.argus/polymarket_cache.pkl by default.
    - .enumerate_all_markets() is cached for 24 hours by default.
    - .enumerate_all_markets() creates MASSIVE AMOUNTS OF CACHE DATA, DO NOT INTERRUPT CACHE FILES ON CREATION EVER
    - .filter_markets_by_close_date calls .enumerate_all_markets() internally.
    - The 24 cache expiration can be extended by modifying the decorator on .enumerate_all_markets().
        remember that .enumerate_all_markets() means literally every single market since the inception
        of Polymarket, so the cache file can grow very large. PolymarketAPI is designed to handle
        this with aggressive caching, separate caches and .filter_markets_by_close_date(). When
        request for market data it's STRONGLY recommended to use .filter_markets_by_close_date()
        to limit the number of markets returned unless you really want everything.
    - All endpoints are already pre-rate-limited internally to avoid hitting Polymarket's rate limits.

Disclaimer:
    - Unlike other Argus modules, PolyDispatcher and FxCDispatcher (in argus.ib.forcast) can be run simultaneously.
        this because they use completely different cache files and backend systems.
    - While TCP-Contract for FxCDispatcher and PolyDispatcher are similar PolyDispatcher data-frame is
        not directly compatible with FxCDispatcher data-frame.
"""
import sys
import tqdm
import time
import json
import socket
import base64
import pandas as pd
from dotenv import load_dotenv
from datetime import datetime, timezone
from utils3 import assertTypes, runAsThread
from argus._argus_utils import Introspective
from utils3.networking.sockets import Server
from py_clob_client.client import ClobClient
from py_clob_client.exceptions import PolyApiException
from argus.polymarket._types import PMarket, PMarketToken
from py_clob_client.clob_types import BookParams, OrderBookSummary


# PolyMarket cache generates LOTs of data, so we separate
# it from the main Argus cache.
from argus.capital import FastCache, DomainCache, NotKey
fCache = FastCache(cache_file='~/.argus/polymarket_cache.pkl')
_POLYCACHE = DomainCache(domain='polymarket', cache=fCache)


class PolymarketAPI:
    def __init__(self, private_key, proxy_funder, host='https://clob.polymarket.com', chain_id=137, order_book_depth=1):
        self.client = ClobClient(
            host,
            key=private_key,
            chain_id=chain_id,
            signature_type=1,
            funder=proxy_funder
        )
        self.client.set_api_creds(self.client.create_or_derive_api_creds())
        self._rate_limit = 0.2  # seconds between requests to avoid rate limiting
        self._order_book_depth = order_book_depth  # Depth of order book to fetch when resolving markets
        if self._order_book_depth < 0:
            raise ValueError("order_book_depth must be at least 1")

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
        tokens = market.tokens
        df_data = []
        for token in tokens:
            try:
                book: OrderBookSummary = self.client.get_order_book(token.token_id)
                asks = book.asks[:self._order_book_depth]
                bids = book.bids[:self._order_book_depth]

                for ask in asks:
                    df_data.append({
                        'side': 'ask',
                        'price': ask.price,
                        'size': ask.size,
                        'outcome': token.outcome
                    })
                for bid in bids:
                    df_data.append({
                        'side': 'bid',
                        'price': bid.price,
                        'size': bid.size,
                        'outcome': token.outcome
                    })
                time.sleep(self._rate_limit)
            except PolyApiException as e:
                print(f"Error fetching order book for token {token.token_id}: {e}")
                raise e

        df_header = ['side', 'price', 'size', 'outcome']
        market.set_df(pd.DataFrame(df_data, columns=df_header))
        return market.df

    def filter_markets_by_close_date(self, max_days=30):
        """Gets ALL markets and filters to those closing within max_days from now. Calls .enumerate_all_markets()."""
        all_markets = self.enumerate_all_markets()
        filtered = []
        now = time.time()
        for market in all_markets:
            if not market.active:
                continue
            end_date = getattr(market, 'end_date_iso', None) or getattr(market, 'end_date', None)
            if end_date:
                if isinstance(end_date, str):
                    end_timestamp = datetime.fromisoformat(end_date.replace('Z', '+00:00')).timestamp()
                else:
                    end_timestamp = end_date
                days_diff = (end_timestamp - now) / 86400
                if 0 <= days_diff <= max_days:
                    filtered.append(market)
        return filtered




class PolyDispatcher(Introspective):
    """
    High-level TCP-based dispatcher for Polymarket API interactions.
    """
    def __init__(self, private_key, proxy_funder, api_host='https://clob.polymarket.com',
                 chain_id=137, order_book_depth=1, listen_host='localhost', listen_port=9962):
        self.api = PolymarketAPI(private_key, proxy_funder, api_host, chain_id, order_book_depth)
        self.server = Server(
            host=listen_host,
            port=listen_port,
            on_disconnect=self.on_disconnect,
            on_recv=self.on_recv
        )
        super().__init__()


    @staticmethod
    def on_disconnect(client, addr):
        print(f"Client {addr} disconnected.")
        _ = client  # Unused

    def on_recv(self, client: socket.socket, addr, data: bytes):
        """
        This is the following client-API interface for PolyDispatcher:
        Output Protocol: ~{JSON}L
        Input Protocol: cmd:arg1,arg2,arg3...

        Commands:
            - enumerate_markets: Enumerates all markets available on Polymarket, cached for 24 hours. Returns list of market slugs (JSON)
            - filter_markets_by_close_date(max_days=30): Filters markets closing within max_days from now. Returns list of market slugs (internal call to enumerate_markets) (JSON)
            - resolve_market(market_slug): Resolves a by market slug, fetches order book data and returns a CSV DataFrame of the order book (base64-encoded CSV)
            - get_market_details(market_slug): Returns detailed information about a specific market may include order book data if resolved. Returns a JSON
            - transpose_df(base64_csv): Transposes a base64-encoded CSV DataFrame into a type more easily workable with FxCDispatcher. Returns base64-encoded CSV of the transposed DataFrame


        All commands are API rate-limited internally to avoid hitting Polymarket's rate limits. Client does not need to handle this.
        There are no authentication requirements, pings, or heartbeats the client must handle.
        """
        cmds = {
            'enumerate_markets': self._client_enumerate_markets,
            'filter_markets_by_close_date': self._client_filter_markets_by_close_date,
            'resolve_market': self._client_resolve_market,
            'get_market_details': self._client_get_market_details,
            'transpose_df': self._client_transpose_df
        }

        raw_cmd = data.decode('utf-8').strip()
        if ':' in raw_cmd:
            cmd, arg_str = raw_cmd.split(':', 1)
            args = [arg.strip() for arg in arg_str.split(',') if arg.strip()]
        else:
            cmd = raw_cmd
            args = []

        print(f"Received command from {addr}: {cmd} with args {args}")
        error = None
        response = None

        if cmd not in cmds:
            response = None
            error = f"Unknown command: {cmd}"
        else:
            try:
                # noinspection all
                response = cmds[cmd](*args)
            except (Exception, PolyApiException) as e:
                error = str(e)

        client.sendall(self.wrap_message({
            'command': cmd,
            'value': response,
            'error': error
        }))
        try:
            print('A response has been sent to {}'.format(client.getpeername()))
        except OSError:
            print('A response has been sent to a disconnected client', client)

    def _client_enumerate_markets(self):
        markets = self.api.enumerate_all_markets()
        slugs = [market.market_slug for market in markets]
        return slugs

    @assertTypes((int,), auto_convert=True, class_method=True)
    def _client_filter_markets_by_close_date(self, max_days=30):
        markets = self.api.filter_markets_by_close_date(max_days)
        slugs = [market.market_slug for market in markets]
        return slugs

    @assertTypes((str,), auto_convert=True, class_method=True)
    def _client_resolve_market(self, market_slug):
        all_markets = self.api.enumerate_all_markets()
        print('Client requested to resolve market:', market_slug)
        target_market = next((m for m in all_markets if m.market_slug == market_slug), None)
        print('Found market:', target_market)
        if not target_market:
            return None
        df = self.api.resolve_market(target_market)
        if df is None:
            return None
        csv_data = df.to_csv(index=False)
        encoded_csv = base64.b64encode(csv_data.encode('utf-8')).decode('utf-8')
        return encoded_csv

    @assertTypes((str,), auto_convert=True, class_method=True)
    def _client_get_market_details(self, market_slug):
        all_markets = self.api.enumerate_all_markets()
        target_market = next((m for m in all_markets if m.market_slug == market_slug), None)
        if not target_market:
            return None
        return target_market.to_dict()

    # noinspection PyMethodMayBeStatic
    @assertTypes((str,), auto_convert=True, class_method=True)
    def _client_transpose_df(self, base64_csv):
        return 'Not implemented yet'

    @staticmethod
    def wrap_message(msg: str | dict) -> bytes:
        if isinstance(msg, dict):
            msg_str = json.dumps(msg)
        else:
            msg_str = msg
        return f"~{msg_str}L".encode('utf-8')

    def interactive_mode(self):
        print('Starting PolyDispatcher on {}:{}'.format(self.server.host, self.server.port))
        self.run_unblock()
        print('PolyDispatcher is running.')
        self._interactive_ui(functions={})
        input('Press Enter to stop the PolyDispatcher...\n')
        self.server.stop()


    @runAsThread
    def run_unblock(self):
        self.server.start()



if __name__ == '__main__':
    load_dotenv()
    from argus.polymarket._ignore import tPoly
    tPoly()