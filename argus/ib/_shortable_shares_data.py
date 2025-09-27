import os
import time
import logging
import subprocess
from argus.capital import DomainCache

_cache = DomainCache('ib.short')


class ShortableSharesData:
    def __init__(self):
        self._server_addr = 'ftp://shortstock:@ftp2.interactivebrokers.com'
        self._server_path = '/Volumes/ftp2.interactivebrokers.com/usa.txt'
        self._check_and_connect()

    def _check_and_connect(self, timeout=60):
        if 'ftp2.interactivebrokers.com' in os.listdir('/Volumes'):
            logging.info('Shortable shares FTP server is already mounted.')
            return
        logging.info('Mounting shortable shares FTP server...')
        subprocess.check_call(['open', '-a', 'Finder', self._server_addr])
        for _ in range(timeout * 100):
            time.sleep(1 / 100)
            if 'ftp2.interactivebrokers.com' in os.listdir('/Volumes'):
                try:
                    _ = self._raw_get_shortable_shares('AAPL')
                except subprocess.CalledProcessError:
                    continue
                break

    def _raw_get_shortable_shares(self, symbol):
        """Fetches shortable shares data directly from the FTP server.
        Does not parse the data. INTERNAL USE ONLY."""
        logging.debug(f'Fetching shortable shares for {symbol.upper()} from {self._server_path}')
        return subprocess.check_output([
            'grep',
            f'^{symbol.upper()}|',
            self._server_path
        ], stderr=subprocess.DEVNULL).decode('utf-8').strip()

    @_cache.cache_decorator('get_shortable_shares')
    def get_shortable_shares(self, symbol):
        """Fetches shortable shares data for a given symbol.
        For symbols >10 Million shares caps at 10 Million. for shares <X returns X-1."""
        try:
            raw_data = self._raw_get_shortable_shares(symbol)
            parts = raw_data.split('|')
            if len(parts) != 10:
                raise ValueError(f'Unexpected data format: {raw_data}')
            found_symbol = parts[0].strip()
            shares = parts[7]
            if '>' in shares:
                shares = float(shares.split('>')[1].strip())
            elif '<' in shares:
                shares = float(shares.split('<')[0].strip()) - 1

            if found_symbol != symbol.upper():
                raise ValueError(f'Symbol mismatch: {found_symbol} != {symbol.upper()}')

            return shares
        except subprocess.CalledProcessError:
            logging.error('No shortable shares data found for symbol: %s', symbol)
            return 0.0


    @_cache.cache_decorator('get_shortable_shares')
    def get_shortable_shares_by_conid(self, conid):
        """Fetches shortable shares data for a given conid.
        For symbols >10 Million shares caps at 10 Million. for shares <X returns X-1."""
        try:
            raw_data = subprocess.check_output([
                'grep',
                f'|{conid}|',
                self._server_path
            ], stderr=subprocess.DEVNULL).decode('utf-8').strip()
            parts = raw_data.split('|')
            if len(parts) != 10:
                raise ValueError(f'Unexpected data format: {raw_data}')
            found_conid = parts[3].strip()
            shares = parts[7]
            if '>' in shares:
                shares = float(shares.split('>')[1].strip())
            elif '<' in shares:
                shares = float(shares.split('<')[0].strip()) - 1

            if found_conid != str(conid):
                raise ValueError(f'Conid mismatch: {found_conid} != {conid}')

            return shares
        except subprocess.CalledProcessError:
            logging.error('No shortable shares data found for conid: %s', conid)
            return 0.0


    @_cache.cache_decorator('translate_symbol_to_conid')
    def translate_symbol_to_conid(self, symbol):
        """Translates a symbol to its corresponding conid using the shortable shares data."""
        try:
            raw_data = self._raw_get_shortable_shares(symbol)
            parts = raw_data.split('|')
            if len(parts) != 10:
                raise ValueError(f'Unexpected data format: {raw_data}')
            found_symbol = parts[0].strip()
            conid = parts[3].strip()

            if found_symbol != symbol.upper():
                raise ValueError(f'Symbol mismatch: {found_symbol} != {symbol.upper()}')

            return int(conid)
        except subprocess.CalledProcessError:
            logging.error('No shortable shares data found for symbol: %s', symbol)
            return None


if __name__ == '__main__':
    ss = ShortableSharesData()
    _cache.invalidate_key(_cache.generate_key('get_shortable_shares', 'AAPL'))
    _cache.invalidate_key(_cache.generate_key('get_shortable_shares', 72539702))
    print('AAPL', ss.get_shortable_shares('AAPL'))
    print('TQQQ', ss.get_shortable_shares_by_conid(72539702))