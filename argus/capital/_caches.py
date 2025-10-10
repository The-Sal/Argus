import os
import json
import logging
import pickle
import time

logger = logging.getLogger(__name__)

class CacheError(Exception):
    """Custom exception for cache-related errors."""
    pass

class NotKey(CacheError):
    """Exception raised when a key is not found in the cache."""
    def __init__(self, key: str):
        super().__init__(f"Key '{key}' not found in cache.")
        self.key = key


class _FastCache:
    """A super simple cache system that also saves to disk. Used to cache data from the Capital.com API especially
    resolution related data or any non-changing data that's wasteful to fetch repeatedly."""
    def __init__(self, cache_file: str = '~/.argus/capital_cache.pkl'):
        """Initializes the FastCache with a specified cache file."""
        self.cache_file = os.path.expanduser(cache_file)
        self.cache = {}
        self._loaded = False  # Track if we've loaded yet

    def _ensure_loaded(self):
        """Lazy load the cache on first access."""
        if not self._loaded:
            self.load_cache()
            self._loaded = True

    def load_cache(self):
        """Loads the cache from the specified file."""
        # the directory must exist
        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        if os.path.exists(self.cache_file):
            with open(self.cache_file, 'rb') as f:
                try:
                    self.cache = pickle.load(f)
                except (pickle.UnpicklingError, EOFError):
                    raise ValueError(f"Cache file {self.cache_file} is corrupted or not a valid PICKLE.")

    def save_cache(self):
        """Saves the cache to the specified file."""
        self._ensure_loaded()  # Make sure we've loaded before saving
        with open(self.cache_file, 'wb') as f:
            pickle.dump(self.cache, f)

CACHE = _FastCache()

class DomainCache:
    """A cache for domain-specific data, such as symbols and their resolutions."""
    def __init__(self, domain: str):
        """Initializes the DomainCache with a specified domain."""
        self.domain = domain
        self.cache = CACHE
        self.cache._ensure_loaded()  # Lazy load when DomainCache is first used
        if domain not in list(self.cache.cache.keys()):
            self.cache.cache[self.domain] = {}
            self.cache.save_cache()

    def get(self, key: str):
        timenow = time.time()
        # check if there is an expiration key and if it has expired
        exp_key = self.expiration_key(key)
        if exp_key in self.cache.cache[self.domain]:
            expiration = self.cache.cache[self.domain][exp_key]
            if timenow > expiration:
                logger.info(f"Cache expired for key: {key[:20]}... in domain: {self.domain}")
                self.delete(key)
                self.delete(exp_key)
                raise NotKey(key)

        try:
            val = self.cache.cache[self.domain][key]
            # logger.info(f"Cache hit for key: {key[:20]}... in domain: {self.domain}")
            return val
        except KeyError:
            raise NotKey(key)

    @staticmethod
    def expiration_key(key: str):
        return f"internal.{key}.expiration"

    def set(self, key: str, value, expiration: int = None):
        """Sets a value in the cache for the specified key.
        Args:
            key (str): The key to set in the
            value: The value to associate with the key.
            expiration (int, optional): Expiration time in seconds
        """

        self.cache.cache[self.domain][key] = value
        if expiration is not None:
            expiration_timestamp = time.time() + expiration
            self.cache.cache[self.domain][self.expiration_key(key)] = expiration_timestamp


        self.cache.save_cache()

    def delete(self, key: str):
        """Deletes a key from the cache."""
        try:
            del self.cache.cache[self.domain][key]
            self.cache.save_cache()
        except KeyError:
            raise NotKey(key)

    @staticmethod
    def generate_key(func_uuid: str, *args, **kwargs) -> str:
        args_key = tuple(arg for arg in args if 'object at' not in str(arg))
        key = f"{func_uuid}:{args_key}:{kwargs}"
        return key

    def cache_decorator(self, func_uuid: str, expiration: int = None):
        """Decorator to cache the result of a function.
        Args:
            func_uuid (str): A unique identifier for the function being cached.
            expiration (int, optional): Expiration time in seconds for the cached value.
        """
        def decorator(func):
            def wrapper(*args, **kwargs):
                # remove args that have 'object at' in them these are dynamic objects that should not be cached
                key = self.generate_key(func_uuid, *args, **kwargs)
                try:
                    return self.get(key)
                except NotKey:
                    result = func(*args, **kwargs)
                    self.set(key, result, expiration=expiration)
                    return result
            return wrapper
        return decorator

    def invalidate_key(self, key: str):
        """Invalidates a specific key in the cache."""
        try:
            self.delete(key)
        except NotKey:
            logger.warning("Unable to find key: {}".format(key))
            logger.warning("Available keys:")

            for k in self.cache.cache[self.domain].keys():
                logger.warning(k)


if __name__ == '__main__':
    # enumerates all domains and the amount of keys in each domain
    CACHE._ensure_loaded()  # Make sure cache is loaded
    for domain, data in CACHE.cache.items():
        print(f"Domain: {domain}, Keys: {len(data)}")