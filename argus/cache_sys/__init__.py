import os
import time
import pickle
import logging
import threading
import traceback

logger = logging.getLogger(__name__)


class CacheError(Exception):
    """Custom exception for cache-related errors."""
    pass


class NotKey(CacheError):
    """Exception raised when a key is not found in the cache."""

    def __init__(self, key: str):
        super().__init__(f"Key '{key}' not found in cache.")
        self.key = key


class FastCache:
    """A super simple cache system that also saves to disk. Used to cache data from the Capital.com API,
    especially resolution-related data or any non-changing data that's wasteful to fetch repeatedly."""

    def __init__(self, cache_file: str = '~/.argus/capital_cache.pkl'):
        """Initializes the FastCache with a specified cache file."""
        self.cache_file = os.path.expanduser(cache_file)
        self.cache = {}
        self._loaded = False  # Track if we've loaded yet
        self._write_lock = threading.Lock()
        self._backup_file = self.cache_file + '.bak'
        self._disabled = os.getenv('ARGUS_CACHES_DISABLED', False) in ['1', 'true', 'True', 'TRUE']

    def ensure_loaded(self):
        """Lazy load the cache on first access."""
        if not self._loaded:
            self.load_cache()
            self._loaded = True

    def unload_cache(self):
        """Unloads the cache from memory. Useful for long-running processes that want to free up RAM."""
        self.cache = {}
        self._loaded = False

    def load_cache(self):
        """Loads the cache from the specified file."""
        # the directory must exist
        if self._disabled:
            return {}
        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        if os.path.exists(self.cache_file):
            with open(self.cache_file, 'rb') as f:
                try:
                    self.cache = pickle.load(f)
                except (pickle.UnpicklingError, EOFError):
                    raise ValueError(f"Cache file {self.cache_file} is corrupted or not a valid PICKLE. "
                                     f"There maybe a backup file at {self._backup_file} you can try to restore from.")

        return None

    def save_cache(self):
        """Saves the cache to the specified file."""
        if self._disabled:
            return

        with self._write_lock:
            self.ensure_loaded()  # Make sure we've loaded before saving
            os.remove(self._backup_file) if os.path.exists(self._backup_file) else None
            if os.path.exists(self.cache_file):
                os.rename(self.cache_file, self._backup_file)

            attempts = 0
            with open(self.cache_file, 'wb') as f:
                warning_given = False
                while True:

                    if attempts >= 5:
                        print('!' * 50)
                        print('ERROR: CACHE WRITE FAILED MULTIPLE TIMES, CHECK TRACEBACKS ABOVE FOR DETAILS.')
                        print('ABORTING CACHE WRITE')
                        print('!' * 50)
                        break

                    try:
                        attempts += 1
                        pickle.dump(self.cache, f)
                        if warning_given and (os.environ.get('ARGUS_PROD', 'false') == 'false'):
                            print('CACHE WRITE COMPLETED, WOULD YOU LIKE TO DISABLE CACHES TO AVOID THIS IN THE FUTURE?')
                            inp = input('Disable caches? (y/n): ')
                            if inp.lower() == 'y':
                                os.environ['ARGUS_CACHES_DISABLED'] = '1'
                                print(
                                    'Caches disabled for future runs. You can re-enable by removing ARGUS_CACHES_DISABLED from your environment.')
                        break
                    except Exception as e:
                        traceback.print_exc()
                        print(f"Cache write failed with error: {e}.")
                        print('!' * 50)
                        print('WARNING: STOP WHAT YOU ARE DOING! CACHE WRITE FAILED PLEASE WAIT FOR IT TO COMPLETE!')
                        print('!' * 50)
                        warning_given = True


CACHE = FastCache()


class DomainCache:
    """A cache for domain-specific data, such as symbols and their resolutions."""

    def __init__(self, domain: str, cache: FastCache = CACHE):
        """Initializes the DomainCache with a specified domain."""
        self.domain = domain
        self.cache = cache
        self._checked = False  # Track if we've checked for domain existence

    def _check_domain(self):
        self.cache.ensure_loaded()

        if self._checked:
            return

        if self.domain not in list(self.cache.cache.keys()):
            self.cache.cache[self.domain] = {}
            self.cache.save_cache()

        self._checked = True

    def get(self, key: str):
        self._check_domain()
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
        self._check_domain()
        self.cache.cache[self.domain][key] = value
        if expiration is not None:
            expiration_timestamp = time.time() + expiration
            self.cache.cache[self.domain][self.expiration_key(key)] = expiration_timestamp

        self.cache.save_cache()

    def delete(self, key: str):
        """Deletes a key from the cache."""
        self._check_domain()
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

    def cache_decorator(self, func_uuid: str, expiration: int = None, should_cache_function=None):
        """Decorator to cache the result of a function.
        Args:
            func_uuid (str): A unique identifier for the function being cached.
            expiration (int, optional): Expiration time in seconds for the cached value.
            should_cache_function (callable, optional): A function that takes the result and returns True if it should be cached, False otherwise.
        """

        def decorator(func):
            def wrapper(*args, **kwargs):
                # remove args that have 'object at' in them these are dynamic objects that should not be cached
                key = self.generate_key(func_uuid, *args, **kwargs)
                try:
                    # logging.info("Cache hit for key: {}".format(key))
                    return self.get(key)
                except NotKey:
                    logger.info("Cache miss for key: {}".format(key))
                    result = func(*args, **kwargs)
                    if should_cache_function is not None:
                        if should_cache_function(result):
                            self.set(key, result, expiration=expiration)
                    else:
                        self.set(key, result, expiration=expiration)

                    return result

            return wrapper

        return decorator

    # does not require `self._check_domain()` because it uses `self.delete()` which does the check
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
    CACHE.ensure_loaded()  # Make sure cache is loaded
    for _domain, data in CACHE.cache.items():
        print(f"Domain: {_domain}, Keys: {len(data)}")
