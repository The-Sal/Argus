"""
The following file contains utility functions for the caching system used in Argus.
The module maybe run standalone as an interactive CLI to inspect and manipulate the cache.

Argus Cache is stored as a PICKLE file at ~/.argus/capital_cache.pkl by default, some modules like Polymarket
use their own separate cache files to avoid bloating the main cache. This utility allows you to inspect, validate, and manipulate these cache files.
It's HIGHLY recommended to use .generate_transparent_cache() to create a human-readable version of the cache for inspection and safekeeping on a regular basis.

Per the version 0.0.6 release, Argus has revamped its caching system with:
- Thread-safe writes
- Prevents program termination during write operations AND warns user if write is in progress
    (by prevent we mean we wrapped .write in a while True/except break block with an aggressive print statement if you try to exit during a write)
    (also the module will get angry and ask if you want to disable cache completely since you tried to exit during a write and this will block the
    program from writing to cache till you respond yes/no)
- Backup files created automatically on each write
- Environment variable ARGUS_CACHES_DISABLED to disable all caching mechanisms for testing or debugging
- CLI interface for inspecting and manipulating the cache
- Transparent human-readable cache generation for inspection and safekeeping
- Polymarket Cache separated from main Argus cache to avoid bloat
- _FastCache is now a public class and can be used by other modules BUT STRONGLY ADVISE AGAINST using it directly, unless storing LARGE amounts of data.

More on .generate_transparent_cache():
This function attempts to serialize each domain in the cache using multiple methods to ensure maximum recoverability and
human-readability:
1. JSON serialization: If the entire domain can be serialized to JSON, it does so.
2. .to_dict() -> JSON serialization: If the objects in the domain have a .
3. pickle.dumps() -> base64 encoding -> string: As a last resort, it pickles each object and encodes it in base64.

This results in a file 'transparent_cache.txt' that contains a human-readable version of the cache, with each domain clearly separated for easy inspection. This file
is not natively loadable back into Argus (per 0.0.6) but can be used to manually recover data if needed. A future version may include a utility to convert this back into a PICKLE file.
Due to the nature of .generate_transparent_cache(), it WILL baloon the resulting file size as it recursively goes to the smallest objects if they are not directly JSON serializable.
On the pickle-serialisation step for each key-value INSIDE the domain per object it does pickle.dumps() meaning it allows per-object recovery.


Recursive ImportError on cache unloading:
<0.0.6 versions if you were on the no_comment, polymarket branches and used PolyMarket API with the .enumerate_market() functions
and then tried to load the cache there is non-zero chance you will get a recursive ImportError because of how the PolyMarket API's
underlying PMarket, PMarketToken classes are defined in argus in relation with capitl.com's objects. This has been fixed in 0.0.6 by
separating the Polymarket cache from the main Argus cache. If you encounter this error, upgrade to 0.0.6. You can remove the domain
'Polymarket' from the cache using the CLI below.


DO NOT EVER TRY TO MANUALLY EDIT, LOAD OUTSIDE ARGUS, RUN MULTIPLE INSTANCE OF ARGUS SIMULTANEOUSLY. DO NOT TOUCH THE CACHE FILES UNLESS YOU KNOW WHAT YOU ARE DOING.
USE THE PROVIDED CLI TO MANIPULATE THE CACHE. CORRUPTION OF THE CACHE FILES CAN LEAD TO DATA LOSS PERMANENTLY. BACKUP file restoration is also
provided in the CLI. DO NOT MANUALLY OPEN/WRITE .argus FOLDER.

Finally, Argus cache <0.0.6 is 100% safe PRE Polymarket merge. Any version of Argus that does not have Polymarket
will not have any issues with the cache. If you're version of Argus has Polymarket, please upgrade to 0.0.6 or later.
There are no thread safety issues before Polymarket merge as the cache was protected. Post Polymarket merge and <0.0.6
there is a non-zero chance of cache corruption if multiple instances of Argus are run simultaneously.

And a last note: Argus/building/_load_symbols_cache.py is a hail-marry script to bulk re-populate the cache
it is NOT part of Argus and should not be used unless you know what you are doing. It is provided as-is
with NO WARRANTY. The file is pre-rate limited and re-stuffing caches can take MULTIPLE HOURS.
"""
import os
import json
import base64
import pickle



cache_path = os.path.join(os.path.expanduser("~"), ".argus", 'capital_cache.pkl')


def generate_transparent_cache(cached_obj: dict):
    """Generate a transparent human-readable version of the cache."""
    transparent_cache = {}

    # We will try the following order of serialization methods
    # 1. JSON serialization
    # 2. .to_dict() -> JSON serialization
    # 4. pickle.dumps() -> base64 encoding -> string (not human-readable but at least recoverable) PER OBJECT inside the domain

    # All keys will be seperated by '=' * 100x for clarity
    separator = '=' * 100
    human_safe = {}
    for domain, domain_cache in cached_obj.items():
        # check JSON serializability

        try:
            js = json.dumps(domain_cache)
            human_safe[domain] = js
            print(f"Domain '{domain}' serialized using JSON for {len(domain_cache)} items.")
            continue
        except TypeError:
            pass


        def recursive_to_dict(obj):
            if isinstance(obj, dict):
                return {k: recursive_to_dict(v) for k, v in obj.items()}
            elif hasattr(obj, 'to_dict') and callable(getattr(obj, 'to_dict')):
                return recursive_to_dict(obj.to_dict())
            elif isinstance(obj, list):
                return [recursive_to_dict(i) for i in obj]
            elif isinstance(obj, tuple):
                return tuple(recursive_to_dict(i) for i in obj)
            elif hasattr(obj, '__dict__'):
                return recursive_to_dict(vars(obj))
            else:
                return obj

        try:
            dict_form = recursive_to_dict(domain_cache)
            js = json.dumps(dict_form)
            human_safe[domain] = js
            print(f"Domain '{domain}' serialized using .to_dict() -> JSON for {len(domain_cache)} items.")
            continue
        except (TypeError, AttributeError):
            pass

        print('Falling back to pickle serialization for domain:', domain)
        # Fallback to pickle serialization
        pickled_items = {}
        for key, value in domain_cache.items():
            try:
                pickled = pickle.dumps(value)
                b64_encoded = base64.b64encode(pickled).decode('utf-8')
                pickled_items[key] = b64_encoded
            except (pickle.PicklingError, TypeError) as e:
                pickled_items[key] = f"<Unserializable: {str(e)}>"
                print(f"Warning: Could not pickle key '{key}' in domain '{domain}': {e}")
        human_safe[domain] = json.dumps(pickled_items)

        print(f"Domain '{domain}' serialized using pickle for {len(pickled_items)} items.")

    print('Generating transparent cache file at ./transparent_cache.txt')
    with open('transparent_cache.txt', 'w') as f:
        for domain, content in human_safe.items():
            f.write(f"Domain: {domain}\n")
            f.write(content)
            f.write(f"\n{separator}\n")
    print('Transparent cache generation complete.')


class CacheInspector:
    def __init__(self, cache_file=cache_path):
        print('WARNING: Disabling all Argus caching mechanisms for inspection.')
        os.environ['ARGUS_CACHES_DISABLED'] = '1'
        self.cache_file = cache_file

    def try_load_cache(self):
        if not os.path.exists(self.cache_file):
            print(f"No cache file found at {self.cache_file}")
            return None
        with open(self.cache_file, 'rb') as f:
            try:
                cache = pickle.load(f)
                return cache
            except (ValueError, pickle.UnpicklingError, EOFError, ) as e:
                print('The cache file is corrupted or not a valid PICKLE.')
                print('Checking for backup file...')
                backup = self.check_for_backup()
                if backup:
                    print('Attempting to load from backup...')
                    with open(backup, 'rb') as bf:
                        try:
                            cache = pickle.load(bf)
                            print('Successfully loaded from backup.')
                            return cache
                        except (pickle.UnpicklingError, EOFError):
                            print('Backup file is also corrupted or not a valid PICKLE.')
            except Exception as e:
                print(f"An unexpected error occurred: {e}")
                return None

    def check_for_backup(self):
        backup_file = self.cache_file + '.bak'
        if os.path.exists(backup_file):
            print(f"Backup file found at {backup_file}")
            return backup_file
        else:
            print("No backup file found.")
            return None

    def restore_from_backup(self):
        backup_file = self.check_for_backup()
        if backup_file:
            with open(backup_file, 'rb') as bf:
                try:
                    cache = pickle.load(bf)
                    with open(self.cache_file, 'wb') as f:
                        pickle.dump(cache, f)
                    print(f"Restored cache from backup to {self.cache_file}")
                except (pickle.UnpicklingError, EOFError):
                    print('Backup file is corrupted or not a valid PICKLE.')
        else:
            print("No backup file to restore from.")

    def inspect_cache(self):
        cache = self.try_load_cache()
        if cache is not None:
            print("Cache contents:")
            for domain, domain_cache in cache.items():
                print(f"Domain: {domain}")
                for key, value in domain_cache.items():
                    print(f"  Key: {key} | Value Type: {type(value)} | Value Preview: {str(value)[:50]}...")

    def check_cache_state(self):
        print("Checking cache state...")
        cache = self.try_load_cache()
        if cache is not None:
            print("Cache is loadable and appears valid.")
        else:
            print("Cache is not loadable or is corrupted.")

    def delete_domain(self, domain):
        cache = self.try_load_cache()
        if cache and domain in cache:
            del cache[domain]
            with open(self.cache_file, 'wb') as f:
                pickle.dump(cache, f)
            print(f"Deleted domain '{domain}' from cache.")
        else:
            print(f"Domain '{domain}' not found in cache.")

    def cli_loop(self):
        print("Argus Cache Inspector CLI")
        cmds = {
            '1': ('Inspect Cache', self.inspect_cache),
            '2': ('Check Cache State', self.check_cache_state),
            '3': ('Restore from Backup', self.restore_from_backup),
            '4': ('Delete Domain from Cache', lambda: self.delete_domain(input("Enter domain to delete: ").strip())),
            '5': ('Generate Transparent Cache', lambda: generate_transparent_cache(self.try_load_cache()) if self.try_load_cache() else print("No valid cache to generate from.")),
            'q': ('Quit', None)
        }
        while True:
            print("\nAvailable Commands:")
            for cmd, (desc, _) in cmds.items():
                print(f"  {cmd}: {desc}")
            choice = input("Enter command: ").strip()
            if choice == 'q':
                print("Exiting Cache Inspector.")
                break
            elif choice in cmds:
                _, action = cmds[choice]
                if action:
                    action()
            else:
                print("Invalid command. Please try again.")


if __name__ == '__main__':
    inspector = CacheInspector()
    inspector.cli_loop()