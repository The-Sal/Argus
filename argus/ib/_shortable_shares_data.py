import os
import time
import logging
import subprocess
import urllib.request
import tempfile
from argus.capital import DomainCache

_cache = DomainCache('ib.short')


class ShortableSharesData:
    def __init__(self):
        # URL of the FTP file containing shortable shares data
        self._ftp_url = 'ftp://shortstock:@ftp2.interactivebrokers.com/usa.txt'
        self._server_path = '/Volumes/ftp2.interactivebrokers.com/usa.txt'
        self._check_and_connect()

    def _check_and_connect(self, timeout=60):
        """Ensure the shortable shares data is available.
        For cross‑platform support we simply attempt to download the file via HTTP
        and cache it locally. If the download fails we log a warning.
        """
        try:
            self._download_file()
        except Exception as e:
            logging.warning(f'Could not access shortable shares data: {e}')
        # No further action needed; subsequent calls will download as required.


    def _download_file(self):
        """Download the shortable shares file via HTTP and cache it locally.

        Returns the file path to the cached copy.  The function is idempotent – if the
        file already exists and is fresh (less than 48 h old) it will be reused.
        """
        cache_dir = os.path.join(tempfile.gettempdir(), 'argus_shortable')
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, 'usa.txt')

        # If cached file is recent, use it.
        if os.path.exists(cache_file):
            age = time.time() - os.path.getmtime(cache_file)
            if age < 48 * 3600:  # 48 hours
                return cache_file
        try:
            logging.info('Downloading shortable shares data from FTP…')
            urllib.request.urlretrieve(self._ftp_url, cache_file)
        except Exception as e:
            logging.error(f'Failed to download shortable shares: {e}')
            raise
        return cache_file

    def _raw_get_shortable_shares(self, symbol):
        """Fetches shortable shares data from the local cached file.
        The file is downloaded via HTTP if not already present."""
        # Ensure local cache
        try:
            content = self._download_file()
        except Exception as e:
            raise subprocess.CalledProcessError(1, 'download', str(e))
        # Search for the symbol line
        with open(content, 'r') as f:
            for line in f:
                if line.startswith(f'{symbol.upper()}|'):
                    return line.strip()
        raise subprocess.CalledProcessError(1, 'grep', f'Symbol {symbol} not found')

    @_cache.cache_decorator('get_shortable_shares', expiration=60 * 60 * 48)  # Cache for 48 hours
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
            # Use cached file and search for conid
            content = self._download_file()
            with open(content, 'r') as f:
                for line in f:
                    if f'|{conid}|' in line:
                        raw_data = line.strip()
                        break
                else:
                    raise subprocess.CalledProcessError(1, 'grep', f'Conid {conid} not found')
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

    @_cache.cache_decorator('translate_symbol_to_conid', should_cache_function=lambda x: x is not None)
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
    # _cache.invalidate_key(_cache.generate_key('get_shortable_shares', 'AAPL'))
    # _cache.invalidate_key(_cache.generate_key('get_shortable_shares', 72539702))
    # _cache.invalidate_key(_cache.generate_key('translate_symbol_to_conid', 'TQQQ'))
    print('AAPL', ss.get_shortable_shares('AAPL'))
    print('TQQQ', ss.get_shortable_shares_by_conid(72539702))
    print(ss.translate_symbol_to_conid('TQQQ'))
