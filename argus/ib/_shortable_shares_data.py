import os
import time
import logging
import tempfile
import subprocess
import urllib.request
from argus.cache_sys import DomainCache
from typing import Dict, List, Optional


_cache = DomainCache("ib.short")

class ShortableShareEntry:
    def __init__(self, data: str):
        parts = data.split("|")
        if len(parts) != 10:
            raise ValueError(f"Unexpected data format: {data}")
        self.symbol = parts[0].strip()
        self.conid = int(parts[3])
        self.shares = parts[7].strip()
        # Additional fields can be added here if needed


class ShortableShareFastDB:
    def __init__(self, entries: List[ShortableShareEntry]):
        self.entries = entries
        self.symbol_to_entry: Dict[str, int] = {}
        self.conid_to_entry: Dict[int, int] = {}

        for idx, entry in enumerate(entries):
            self.symbol_to_entry[entry.symbol] = idx
            self.conid_to_entry[entry.conid] = idx


class ShortableSharesData:
    def __init__(self):
        # URL of the FTP file containing shortable shares data
        self._ftp_url = "ftp://shortstock:@ftp2.interactivebrokers.com/usa.txt"
        self._server_path = "/Volumes/ftp2.interactivebrokers.com/usa.txt"
        self._fast_db: Optional[ShortableShareFastDB] = None
        self._check_and_connect()

    def _check_and_connect(self, timeout=60):
        """Ensure the shortable shares data is available.
        For cross‑platform support we simply attempt to download the file via HTTP
        and cache it locally. If the download fails we log a warning.
        """
        try:
            self._download_file()
        except Exception as e:
            logging.warning(f"Could not access shortable shares data: {e}")
        # No further action needed; subsequent calls will download as required.

    def _download_file(self):
        """Download the shortable shares file via HTTP and cache it locally.

        Returns the file path to the cached copy.  The function is idempotent – if the
        file already exists and is fresh (less than 48 h old) it will be reused.
        """
        cache_dir = os.path.join(tempfile.gettempdir(), "argus_shortable")
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, "usa.txt")

        # If cached file is recent, use it.
        if os.path.exists(cache_file):
            age = time.time() - os.path.getmtime(cache_file)
            if age < 48 * 3600:  # 48 hours
                return cache_file
        try:
            logging.info("Downloading shortable shares data from FTP…")
            urllib.request.urlretrieve(self._ftp_url, cache_file)
        except Exception as e:
            logging.error(f"Failed to download shortable shares: {e}")
            raise
        return cache_file

    def _raw_get_shortable_shares(self, symbol: str) -> str:
        """Fetches shortable shares data from the local cached file.
        The file is downloaded via HTTP if not already present."""
        # Ensure local cache
        try:
            content = self._download_file()
        except Exception as e:
            raise subprocess.CalledProcessError(1, "download", str(e))
        # Search for the symbol line
        with open(content, "r") as f:
            for line in f:
                if line.startswith(f"{symbol.upper()}|"):
                    return line.strip()
        raise subprocess.CalledProcessError(1, "grep", f"Symbol {symbol} not found")

    @staticmethod
    def _build_fast_db(content: str) -> ShortableShareFastDB:
        """Builds a fast lookup database from the shortable shares data."""
        entries = []
        with open(content, "r") as f:
            for line in f:
                if not line.startswith("#"):  # Skip comments
                    entries.append(ShortableShareEntry(line))
        return ShortableShareFastDB(entries)

    @_cache.cache_decorator(
        "get_shortable_shares", expiration=60 * 60 * 48
    )  # Cache for 48 hours
    def get_shortable_shares(self, symbol: str) -> float:
        """Fetches shortable shares data for a given symbol.
        For symbols >10 Million shares caps at 10 Million. for shares <X returns X-1."""
        if self._fast_db is None:
            content = self._download_file()
            self._fast_db = self._build_fast_db(content)

        try:
            idx = self._fast_db.symbol_to_entry.get(symbol.upper())
            if idx is None:
                raise subprocess.CalledProcessError(
                    1, "grep", f"Symbol {symbol} not found"
                )

            entry = self._fast_db.entries[idx]
            shares = entry.shares
            if ">" in shares:
                return float(shares.split(">")[1].strip())
            elif "<" in shares:
                return float(shares.split("<")[0].strip()) - 1
            else:
                return float(shares)
        except (subprocess.CalledProcessError, ValueError) as e:
            logging.error("No shortable shares data found for symbol: %s", symbol)
            return 0.0

    @_cache.cache_decorator("get_shortable_shares_by_conid", expiration=60 * 60 * 48)
    def get_shortable_shares_by_conid(self, conid: int) -> float:
        """Fetches shortable shares data for a given conid.
        For symbols >10 Million shares caps at 10 Million. for shares <X returns X-1."""
        if self._fast_db is None:
            content = self._download_file()
            self._fast_db = self._build_fast_db(content)

        try:
            idx = self._fast_db.conid_to_entry.get(conid)
            if idx is None:
                raise subprocess.CalledProcessError(
                    1, "grep", f"Conid {conid} not found"
                )

            entry = self._fast_db.entries[idx]
            shares_str = entry.shares
            if ">" in shares_str:
                return float(shares_str.split(">")[1].strip())
            elif "<" in shares_str:
                return float(shares_str.split("<")[0].strip()) - 1
            else:
                return float(shares_str)
        except (subprocess.CalledProcessError, ValueError) as e:
            logging.error("No shortable shares data found for conid: %s", conid)
            return 0.0

    @_cache.cache_decorator(
        "translate_symbol_to_conid", should_cache_function=lambda x: x is not None
    )
    def translate_symbol_to_conid(self, symbol: str) -> Optional[int]:
        """Translates a symbol to its corresponding conid using the shortable shares data."""
        if self._fast_db is None:
            content = self._download_file()
            self._fast_db = self._build_fast_db(content)

        try:
            idx = self._fast_db.symbol_to_entry.get(symbol.upper())
            if idx is None:
                raise subprocess.CalledProcessError(
                    1, "grep", f"Symbol {symbol} not found"
                )

            entry = self._fast_db.entries[idx]
            return entry.conid
        except (subprocess.CalledProcessError, ValueError) as e:
            logging.error("No shortable shares data found for symbol: %s", symbol)
            return None


if __name__ == "__main__":
    ss = ShortableSharesData()
    # _cache.invalidate_key(_cache.generate_key("get_shortable_shares", "AAPL"))
    # _cache.invalidate_key(_cache.generate_key("get_shortable_shares", 72539702))
    # _cache.invalidate_key(_cache.generate_key("translate_symbol_to_conid", "TQQQ"))
    print("AAPL", ss.get_shortable_shares("AAPL"))
    print("TQQQ", ss.get_shortable_shares_by_conid(72539702))
    print(ss.translate_symbol_to_conid("TQQQ"))
