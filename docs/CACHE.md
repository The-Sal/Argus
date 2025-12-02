# Cache System

The Argus cache system provides thread-safe, persistent caching for expensive API calls with automatic backups and a CLI for inspection and manipulation.

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Cache Files](#cache-files)
- [CLI Interface](#cli-interface)
- [Transparent Cache](#transparent-cache)
- [Usage](#usage)
- [Safety and Best Practices](#safety-and-best-practices)

## Overview

Argus uses a **domain-based caching system** to reduce expensive API calls and improve performance.

**Location:** `/argus/cache_utils/__init__.py`

**Cache Storage:** `~/.argus/`

**Key Features:**
- Thread-safe writes
- Automatic backups (`.bak` files)
- Protection against mid-write interruption
- CLI for inspection and manipulation
- Transparent cache generation (human-readable export)
- Separate caches for main and Polymarket modules
- Environment variable to disable all caching

## Architecture

### Domain-Based Structure

The cache is organized by **domains** (modules or API endpoints):

```python
{
    'domain_name': {
        'cache_key_1': value_1,
        'cache_key_2': value_2,
        ...
    },
    'another_domain': {
        ...
    }
}
```

**Example:**
```python
{
    'IBNetworker.search_contract': {
        'AAPL': SearchResult(...),
        'TSLA': SearchResult(...),
    },
    'capital_com.api.resolve_symbol': {
        'BTCUSD': {...},
        'ETHUSD': {...},
    }
}
```

### Cache Decorator

Modules use the `@cache_decorator` to automatically cache function results:

**Interactive Brokers Example:**
```python
@_IB_Cache.cache_decorator('IBNetworker.search_contract')
def search_contract(self, contract_name):
    # Expensive API call
    response = self.session.post(...)
    return response
```

**Capital.com Example:**
```python
@CACHE.cache_decorator('resolve_symbol')
def resolve_symbol(self, symbol: str, market: str = None):
    # Expensive symbol resolution
    resolved = self.api.get_market_details(epic=symbol)
    return resolved
```

**How it works:**
1. Function called with arguments (e.g., `search_contract("AAPL")`)
2. Decorator checks cache for key: `"AAPL"`
3. If found → Return cached value (no API call)
4. If not found → Call function, cache result, return value

### Thread Safety

**Version 0.0.6+ Features:**
- Thread-safe writes with locks
- Warns user if write is in progress
- Blocks program exit during write operations
- Aggressive print statements if interrupted during write
- Prompts to disable cache if interrupted

**Protection Mechanism:**
```python
# Simplified example
while True:
    try:
        # Critical write operation
        with open(cache_file, 'wb') as f:
            pickle.dump(cache, f)
        break
    except KeyboardInterrupt:
        print("⚠️ CACHE WRITE IN PROGRESS! DO NOT INTERRUPT!")
        # Ask user if they want to disable cache
```

### Automatic Backups

Every cache write creates a backup:

```
~/.argus/capital_cache.pkl       # Main cache
~/.argus/capital_cache.pkl.bak   # Automatic backup
```

**Backup Trigger:** Every write operation

**Restoration:** Available via CLI

## Cache Files

### Main Cache

**File:** `~/.argus/capital_cache.pkl`

**Used By:**
- Interactive Brokers module
- Capital.com module
- Binance module (if caching is added)
- General Argus modules

**Domains:**
- `IBNetworker.search_contract`
- `capital_com.api.resolve_symbol`
- Various API call caches

### Polymarket Cache

**File:** `~/.argus/polymarket_cache.pkl`

**Used By:**
- Polymarket module (`argus.polymarket_direct`)

**Reason for Separation:**
- Prevents main cache bloat
- Avoids recursive ImportError issues (< 0.0.6)
- Polymarket has different data models

### Backup Files

**Files:** `*.pkl.bak`

**Creation:** Automatic on every write

**Purpose:** Recovery from corruption

## CLI Interface

### Launching the CLI

```bash
python -m argus.cache_utils
```

**Output:**
```
WARNING: Disabling all Argus caching mechanisms for inspection.
Argus Cache Inspector CLI

Available Commands:
  1: Inspect Cache
  2: Check Cache State
  3: Restore from Backup
  4: Delete Domain from Cache
  5: Generate Transparent Cache
  6: Toggle Cache File (capital <-> polymarket)
  q: Quit
Enter command:
```

### CLI Commands

#### 1. Inspect Cache

View all domains and cached items:

```
Enter command: 1
Cache contents:
Domain: IBNetworker.search_contract
  Key: AAPL | Value Type: <class 'SearchResult'> | Value Preview: SearchResult(conid=265598, symbol='AAPL', de...
  Key: TSLA | Value Type: <class 'SearchResult'> | Value Preview: SearchResult(conid=76792991, symbol='TSLA',...

Domain: capital_com.api.resolve_symbol
  Key: BTCUSD | Value Type: <class 'dict'> | Value Preview: {'instrument': {'epic': 'BTCUSD', 'name': 'Bitco...
```

#### 2. Check Cache State

Validate cache integrity:

```
Enter command: 2
Checking cache state...
Cache is loadable and appears valid.
```

**Possible Outputs:**
- "Cache is loadable and appears valid." ✅
- "Cache is not loadable or is corrupted." ❌
- "No cache file found at ~/.argus/capital_cache.pkl" ⚠️

#### 3. Restore from Backup

Restore cache from `.bak` file:

```
Enter command: 3
Backup file found at ~/.argus/capital_cache.pkl.bak
Restored cache from backup to ~/.argus/capital_cache.pkl
```

**Use Case:** Cache corruption, accidental deletion, bad data

#### 4. Delete Domain from Cache

Remove a specific domain:

```
Enter command: 4
Enter domain to delete: Polymarket
Deleted domain 'Polymarket' from cache.
```

**Use Cases:**
- Remove outdated data
- Fix recursive ImportError (< 0.0.6 Polymarket issue)
- Clear corrupted domain

#### 5. Generate Transparent Cache

Export human-readable cache:

```
Enter command: 5
Domain 'IBNetworker.search_contract' serialized using .to_dict() -> JSON for 47 items.
Domain 'capital_com.api.resolve_symbol' serialized using JSON for 23 items.
Generating transparent cache file at ./transparent_cache.txt
Transparent cache generation complete.
```

**Output:** `transparent_cache.txt` in current directory

(See [Transparent Cache](#transparent-cache) section for details)

#### 6. Toggle Cache File

Switch between main and Polymarket caches:

```
Enter command: 6
Switched cache file to ~/.argus/polymarket_cache.pkl
```

**Useful for:**
- Inspecting Polymarket cache separately
- Comparing main vs. Polymarket caches

## Transparent Cache

### What is Transparent Cache?

A **human-readable export** of the cache for inspection, backup, and recovery.

**File:** `transparent_cache.txt`

**Format:** Text file with domains separated by `=` delimiters

### Serialization Methods

The transparent cache generator tries multiple methods (in order):

#### 1. JSON Serialization (Preferred)

If domain data is JSON-serializable:

```python
try:
    js = json.dumps(domain_cache)
    # Success → Use JSON
except TypeError:
    # Try next method
```

**Example Output:**
```
Domain: capital_com.api.resolve_symbol
{"BTCUSD": {"instrument": {"epic": "BTCUSD", "name": "Bitcoin"}}, "ETHUSD": {...}}
====================================================================================================
```

#### 2. Recursive `.to_dict()` → JSON

If objects have `.to_dict()` methods:

```python
def recursive_to_dict(obj):
    if hasattr(obj, 'to_dict'):
        return obj.to_dict()
    # Recursively convert nested objects
```

**Example Output:**
```
Domain: IBNetworker.search_contract
{"AAPL": {"conid": 265598, "symbol": "AAPL", ...}, "TSLA": {...}}
====================================================================================================
```

#### 3. Pickle → Base64 (Fallback)

If neither works, pickle and encode in base64:

```python
pickled = pickle.dumps(value)
b64 = base64.b64encode(pickled).decode('utf-8')
```

**Example Output:**
```
Domain: some_complex_domain
{"key1": "gASVOgAAAAAAAAB9lCgICklCTmV0...", "key2": "gASVOgAAAAAAAA..."}
====================================================================================================
```

**Note:** Base64 is not human-readable but allows per-object recovery.

### Example Transparent Cache

```
Domain: IBNetworker.search_contract
{"AAPL":{"conid":265598,"description":"APPLE INC-NASDAQ","symbol":"AAPL","secType":"STK","exchange":"NASDAQ"},
"TSLA":{"conid":76792991,"description":"TESLA INC-NASDAQ","symbol":"TSLA","secType":"STK","exchange":"NASDAQ"}}

====================================================================================================
Domain: capital_com.api.resolve_symbol
{"BTCUSD":{"instrument":{"epic":"BTCUSD","name":"Bitcoin vs US Dollar","type":"CRYPTOCURRENCIES"}}}

====================================================================================================
```

### Use Cases

- **Backup:** Safe, human-readable cache backup
- **Inspection:** Audit cached data without loading pickle
- **Recovery:** Manually reconstruct cache if needed
- **Version Control:** Commit transparent cache (not binary `.pkl`)

**Warning:** Transparent cache is **not directly loadable** back into Argus (as of 0.0.6). Manual reconstruction required.

## Usage

### Disable All Caching

**Environment Variable:**
```bash
export ARGUS_CACHES_DISABLED=1
```

**Effect:**
- All cache decorators bypassed
- Functions always execute (no cache lookup)
- No cache writes

**Use Cases:**
- Testing
- Debugging
- Forcing fresh data

### Programmatic Cache Access

**Direct Cache Usage (Not Recommended):**

```python
from argus.capital import DomainCache

cache = DomainCache('my_module')

# Cache decorator
@cache.cache_decorator('expensive_function')
def expensive_function(arg):
    # Expensive operation
    return result
```

**Recommended Approach:**

Use existing module caches instead of creating new ones:
- IB: `_IB_Cache`
- Capital.com: `CACHE`
- Polymarket: `dCache`

### Clear Cache

**Option 1: Delete domain via CLI**
```bash
python -m argus.cache_utils
# Choose: 4. Delete Domain from Cache
```

**Option 2: Delete cache file**
```bash
rm ~/.argus/capital_cache.pkl
rm ~/.argus/polymarket_cache.pkl
```

**Option 3: Disable caching temporarily**
```bash
export ARGUS_CACHES_DISABLED=1
python runtime.py ib.core
```

## Safety and Best Practices

### DO NOT:

1. ❌ **Manually edit cache files** - Use CLI instead
2. ❌ **Load cache outside Argus** - Corrupts pickle format
3. ❌ **Run multiple Argus instances simultaneously** - Cache corruption (< 0.0.6 risk)
4. ❌ **Delete `.bak` files** - Your safety net
5. ❌ **Open `.argus` folder with external tools** - Risk of corruption
6. ❌ **Interrupt during write** - Wait for completion or disable caching

### DO:

1. ✅ **Use CLI for inspection** - Safe, read-only
2. ✅ **Generate transparent cache regularly** - Human-readable backup
3. ✅ **Keep backup files** - Automatic, don't delete
4. ✅ **Use `ARGUS_CACHES_DISABLED=1` for testing** - Clean state
5. ✅ **Restore from backup if corrupted** - CLI option available
6. ✅ **Upgrade to 0.0.6+** - Thread safety improvements

### Cache Corruption Recovery

**Symptoms:**
- `pickle.UnpicklingError`
- `EOFError`
- "Cache is not loadable or is corrupted"

**Recovery Steps:**

1. **Try backup restore:**
   ```bash
   python -m argus.cache_utils
   # Choose: 3. Restore from Backup
   ```

2. **Delete corrupted domain:**
   ```bash
   python -m argus.cache_utils
   # Choose: 4. Delete Domain from Cache
   ```

3. **Delete cache and rebuild:**
   ```bash
   rm ~/.argus/capital_cache.pkl
   # Re-run Argus to rebuild cache
   ```

4. **Use transparent cache for manual recovery:**
   - Generate transparent cache before corruption (if possible)
   - Manually reconstruct critical data
   - Repopulate cache via API calls

### Polymarket ImportError Fix (< 0.0.6)

**Problem:** Recursive ImportError when loading cache with Polymarket data

**Cause:** Polymarket data models conflicting with Capital.com objects

**Solution:**

**Option 1: Upgrade to 0.0.6+** (Recommended)
```bash
git pull
pip install -e .
```

**Option 2: Delete Polymarket domain** (< 0.0.6)
```bash
python -m argus.cache_utils
# Choose: 4. Delete Domain from Cache
# Enter: Polymarket
```

**Option 3: Use separate Polymarket cache** (0.0.6+)
- Automatically separated, no action needed

### Thread Safety Notes

**Version 0.0.6+ Improvements:**
- Write operations are protected
- Multiple instances still risky (avoid)
- Interrupted writes handled gracefully

**Best Practice:**
- Run one Argus instance at a time
- Wait for graceful shutdown
- Don't force-kill during heavy caching

## File Reference

```
argus/cache_utils/
└── __init__.py         # CacheInspector, generate_transparent_cache, cache decorators

~/.argus/
├── capital_cache.pkl     # Main cache
├── capital_cache.pkl.bak # Main backup
├── polymarket_cache.pkl  # Polymarket cache
└── polymarket_cache.pkl.bak # Polymarket backup
```

## Summary

The Argus cache system provides robust, thread-safe caching with the following highlights:

**Key Features:**
- ✅ Domain-based organization
- ✅ Thread-safe writes (0.0.6+)
- ✅ Automatic backups
- ✅ CLI for safe manipulation
- ✅ Transparent cache export
- ✅ Separate Polymarket cache
- ✅ Environment variable disable

**Use Cache For:**
- Reducing expensive API calls
- Improving application startup time
- Symbol/contract resolution
- Account data retrieval

**Avoid Cache For:**
- Real-time market data (always fresh)
- Testing/debugging (use `ARGUS_CACHES_DISABLED=1`)
- First-time runs (cache will be empty)

**Maintenance:**
- Generate transparent cache monthly
- Check cache state after crashes
- Keep backup files
- Upgrade to 0.0.6+ for thread safety

The cache system is a critical infrastructure component that significantly improves Argus performance while maintaining data integrity and recoverability.
