import os
import json
import logging
import pickle

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
        self.load_cache()

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
        with open(self.cache_file, 'wb') as f:
            pickle.dump(self.cache, f)

CACHE = _FastCache()

class DomainCache:
    """A cache for domain-specific data, such as symbols and their resolutions."""
    def __init__(self, domain: str):
        """Initializes the DomainCache with a specified domain."""
        self.domain = domain
        self.cache = CACHE
        if domain not in list(self.cache.cache.keys()):
            self.cache.cache[self.domain] = {}
            self.cache.save_cache()

    def get(self, key: str):
        try:
            val = self.cache.cache[self.domain][key]
            logger.info(f"Cache hit for key: {key[:20]}... in domain: {self.domain}")
            return val
        except KeyError:
            raise NotKey(key)

    def set(self, key: str, value):
        """Sets a value in the cache for the specified key."""
        self.cache.cache[self.domain][key] = value
        self.cache.save_cache()

    def delete(self, key: str):
        """Deletes a key from the cache."""
        if key in self.cache.cache[self.domain]:
            del self.cache.cache[self.domain][key]
            self.cache.save_cache()
        else:
            raise NotKey(key)

    def cache_decorator(self, func_uuid: str):
        """Decorator to cache the result of a function."""
        def decorator(func):
            def wrapper(*args, **kwargs):
                # remove args that have 'object at' in them these are dynamic objects that should not be cached
                args_key = tuple(arg for arg in args if 'object at' not in str(arg))
                key = f"{func_uuid}:{args_key}:{kwargs}"
                try:
                    return self.get(key)
                except NotKey:
                    result = func(*args, **kwargs)
                    self.set(key, result)
                    return result
            return wrapper
        return decorator