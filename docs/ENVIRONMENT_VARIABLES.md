# Argus Project Environment Variables

This document lists all environment variables used throughout the Argus project and their purposes.

## Polymarket 

### `POLYMARKET_PRIVATE_KEY`
- **Purpose**: Private key for Polymarket authentication
- **Required**: Yes (for trading/order placement)
- **Used in**: `polymarket_direct/_example.py`, `polymarket_direct/_examples/unsub_test.py`
- **Example**: Export your private key for Polymarket API access

### `POLYMARKET_PROXY_FUNDER`
- **Purpose**: Proxy funder address for Polymarket
- **Required**: Yes (for trading/order placement)
- **Used in**: `polymarket_direct/_example.py`, `polymarket_direct/_examples/unsub_test.py`

### `POLYMARKET_STREAM_DIR`
- **Purpose**: Directory to save Polymarket stream data
- **Default**: `./polymarket_data`
- **Required**: No
- **Used in**: `polymarket_direct/_example.py`

### `POLYMARKET_STREAM_PREFIX`
- **Purpose**: File prefix for Polymarket stream data files
- **Default**: `polymarket_stream`
- **Required**: No
- **Used in**: `polymarket_direct/_example.py`

### `POLYMARKET_STREAM_SAVE_INTERVAL`
- **Purpose**: Save interval for stream data (seconds)
- **Default**: `60`
- **Required**: No
- **Used in**: `polymarket_direct/_example.py`

### `POLYMARKET_MAX_SOCKET_RETRIES`
- **Purpose**: Maximum number of socket connection retries
- **Default**: `50` (for WebSocket connections in `wss.py`), varies by component
- **Required**: No
- **Used in**: `polymarket_direct/__init__.py`, `polymarket_direct/wss.py`
- **Note**: Different components may use different defaults for this variable

### `POLYMARKET_MAX_MESSAGE_COUNT`
- **Purpose**: Maximum message count before rolling mechanism triggers
- **Default**: `5000`
- **Required**: No
- **Used in**: `polymarket_direct/__init__.py`

### `POLYMARKET_ORDERBOOK_DEPTH`
- **Purpose**: Controls the depth of orderbook data (number of bid/ask levels to fetch)
- **Default**: `10`
- **Required**: No
- **Used in**: `polymarket/__init__.py`, `tests/test_poly_dispatcher.py`
- **Example**: Set to `20` for deeper orderbook data, `5` for shallower data

### `POLYMARKET_ENABLE_ROLLING`
- **Purpose**: Enable/disable rolling mechanism for message handling
- **Default**: `true`
- **Required**: No
- **Used in**: `polymarket_direct/__init__.py`

### `POLYMARKET_WRITE_INTERVAL`
- **Purpose**: Write interval for data persistence (seconds)
- **Default**: `30`
- **Required**: No
- **Used in**: `polymarket_direct/__init__.py`

### `POLYMARKET_ENABLE_UNSUB_PATCH`
- **Purpose**: Enable/disable unsubscription patch
- **Default**: `false`
- **Required**: No
- **Used in**: `polymarket_direct/__init__.py`

### `POLYMARKET_NO_SAFETY_CHECK`
- **Purpose**: Disable **Stage 1** pre-connection IP safety check (ipinfo.io)
- **Default**: `false`
- **Required**: No
- **Used in**: `polymarket_direct/rest.py`, `polymarket_direct/_examples/send_order_cancel_order_with_wss.py`
- **Behavior**: When `true`, skips early warning IP check against hardcoded geo-blocked region list
- **IP Exposure**: Only exposes IP to ipinfo.io service (not to Polymarket)
- **Distinction**: This is the **early warning system**. Disabling it removes a preliminary check but does NOT skip the actual Polymarket geo-block verification (see `POLYMARKET_PROTECTION` for that)

### `POLYMARKET_MAX_PING_PONG_FAILURES`
- **Purpose**: Maximum number of ping-pong failures before reconnect
- **Default**: `3`
- **Required**: No
- **Used in**: `polymarket_direct/rest.py`

### `POLYMARKET_DISABLE_PING_PONG_LOGS`
- **Purpose**: Disable ping-pong logging to reduce noise
- **Default**: `false`
- **Required**: No
- **Used in**: `polymarket_direct/rest.py`

### `POLYMARKET_FULL_MARKET_CACHE_REFRESH_INTERVAL`
- **Purpose**: Refresh interval for full market cache in seconds
- **Default**: `300` (5 minutes)
- **Required**: No
- **Used in**: `polymarket/__init__.py`

### `POLYMARKET_PARANOID`
- **Purpose**: Enable immediate termination if IP is in known geo-blocked regions
- **Default**: `false`
- **Required**: No
- **Used in**: `polymarket_direct/rest.py`
- **Behavior**: When `true`, terminates immediately on Stage 1 IP check failure

### `POLYMARKET_PROTECTION`
- **Purpose**: Enable/disable **Stage 2** direct Polymarket geo-block verification
- **Default**: `true`
- **Required**: No
- **Used in**: `polymarket_direct/rest.py`
- **Behavior**: When `false`, skips the actual Polymarket `/api/geoblock` check and shows 30-second countdown warning
- **IP Exposure**: **DANGEROUS** - Your IP is exposed directly to Polymarket's servers
- **Distinction**: This is the **definitive check from Polymarket's own systems**. Disabling it means orders may be placed but rejected by Polymarket if your IP is blocked. See `POLYMARKET_NO_SAFETY_CHECK` for the early warning system check

### Polymarket IP Protection: `POLYMARKET_NO_SAFETY_CHECK` vs `POLYMARKET_PROTECTION`

**Quick Comparison:**

| Aspect | `POLYMARKET_NO_SAFETY_CHECK` | `POLYMARKET_PROTECTION` |
|--------|-----|---------|
| **Stage** | Stage 1 (Pre-connection) | Stage 2 (Direct Polymarket) |
| **Default** | `false` (check enabled) | `true` (check enabled) |
| **IP Exposed To** | ipinfo.io only | Polymarket's servers |
| **Purpose** | Early warning using hardcoded region list | Definitive check from Polymarket's own system |
| **Consequence if disabled** | Skips preliminary warning, but Stage 2 still runs | Allows bypass of Polymarket's geo-block - orders may be placed but rejected |
| **Danger Level** | Low (less intrusive) | High (exposes IP to Polymarket) |

**When to disable:**
- `POLYMARKET_NO_SAFETY_CHECK=true`: Only if you're certain your IP is not in a blocked region and want to skip the ipinfo.io check
- `POLYMARKET_PROTECTION=false`: **Only for testing market data access in blocked regions** (order placement will fail anyway)

**Recommended configuration:** Keep both at defaults (`false` and `true` respectively) for maximum safety.

### `POLYMARKET_USER_EVENTS_FUSS`
- **Purpose**: Enable/disable fuss notifications for user account events
- **Default**: `false`
- **Required**: No
- **Used in**: `polymarket_direct/rest.py`
- **Behavior**: When `true`, triggers fuss notifications and macOS notifications for user account events received via WebSocket

### `POLYMARKET_RAPID_ORDER_BUILD`
- **Purpose**: Enable rapid order builder that reduces order placement latency by ~50%
- **Default**: `false`
- **Required**: No
- **Used in**: `polymarket_direct/rest.py`
- **Behavior**: When `true`, uses parallel API calls and bypasses SDK overhead for faster order building. Fetches tick_size and fee_rate concurrently via thread pool instead of sequentially
- **Performance**: Reduces "building order" phase from ~0.23s to ~0.11s (~52% faster)
- **Warning**: A warning is logged when enabled to alert users of potential risks if assumptions about tick size/fee rate are violated


### POLYMARKET_UNSAFE_RAPID_CONNECTIONS
- **Purpose**: For endpoints that do not require authentication, bypass WireProxy to enable maximum performance.
- **Default**: `false`
- **Required**: No
- **Used in**: `polymarket_direct/wss.py`
- **Behavior**: When `true`, all connections to Polymarket that do not require authenticationa are made directly without routing through WireProxy. These include changes across the rest and websocket layer of the dispatcher. This feature is not stable and its behavior maybe changed with future updates (i.e., supporting more 'usafe' connections. This works in tangent with WIREPROXY integration (and only makes sense if you are geo-blocked from placing orders). It selectively punches holes in the connections.
- **Warning**: Enabling this in a geo-blocked region will result in connection failures. Use with caution and only if you are sure your IP is not blocked for market data access.


### `MAX_SEEN_CORRELATION_IDS`
- **Purpose**: Maximum number of seen correlation IDs to track for duplicate detection
- **Default**: `100000`
- **Required**: No
- **Used in**: `polymarket/_classes.py`
- **Behavior**: Limits the size of the correlation ID tracking set to prevent memory growth

### `MAX_CORRELATION_ID_LENGTH`
- **Purpose**: Maximum length of correlation IDs to store
- **Default**: `40`
- **Required**: No
- **Used in**: `polymarket/_classes.py`
- **Behavior**: Truncates correlation IDs to this length before storage

## Interactive Brokers Integration

### `IB_COOKIE`
- **Purpose**: Authentication cookie for IBKR web API
- **Required**: Yes (for IB integration)
- **Used in**: `ib/__init__.py`, `ib/forecast.py`, `ib/set_auth.py`
- **Generated**: Automatically by `ib/set_auth.py` authentication script

### `USERNAME`
- **Purpose**: IBKR username for authentication
- **Required**: Yes (for IB authentication)
- **Used in**: `ib/set_auth.py`

### `PASSWORD`
- **Purpose**: IBKR password for authentication
- **Required**: Yes (for IB authentication)
- **Used in**: `ib/set_auth.py`

### `PAPER_ACCOUNT`
- **Purpose**: Flag to use paper trading account (1) vs live account (0)
- **Default**: `0` (live account)
- **Required**: No
- **Used in**: `ib/set_auth.py`

### `NOTIFICATION_NUMBER`
- **Purpose**: Phone number for notifications
- **Required**: No
- **Used in**: `ib/_ib_utils.py`

## Capital.com Integration

### `CAPITAL_DOTCOM_API_KEY`
- **Purpose**: API key for Capital.com authentication
- **Required**: Yes (for Capital.com integration)
- **Used in**: `capital/__init__.py`

### `CAPITAL_DOT_CUSTOM_PW`
- **Purpose**: Custom password for Capital.com API
- **Required**: Yes (for Capital.com integration)
- **Used in**: `capital/__init__.py`

### `CAPITAL_DOTCOM_IDENTIFIER`
- **Purpose**: Identifier for Capital.com API
- **Required**: Yes (for Capital.com integration)
- **Used in**: `capital/__init__.py`

## TradingView Integration

### `TOKEN`
- **Purpose**: Authentication token for TradingView
- **Required**: Yes (for TV integration)
- **Used in**: `tv/__init__.py`

## WireProxy Integration

### `WIREPROXY_BIND_ADDRESS`
- **Purpose**: Bind address for WireProxy server
- **Default**: `127.0.0.1:25344`
- **Required**: No
- **Used in**: `wireproxy/wrapper.py`

### `WIREPROXY_MAPPING_<DISPATCHER_NAME>`
- **Purpose**: Maps dispatcher names to WireGuard configuration names
- **Format**: `WIREPROXY_MAPPING_<DISPATCHER_NAME>=<CONFIG_NAME>`
- **Required**: No (variable based on setup)
- **Used in**: `wireproxy/wrapper.py`
- **Example**: `WIREPROXY_MAPPING_IB_NY=my_ny_config`

### `WIREPROXY_BLIND_BIND`
- **Purpose**: Skip daemon state checks and blindly bind to the WireProxy address
- **Default**: `false`
- **Required**: No
- **Used in**: `wireproxy/wrapper.py`
- **Behavior**: When `true`, skips checking if the WireProxy daemon is running and blindly binds to the configured address. Useful when the daemon is managed externally or for testing purposes.

## IP Safety Check

### `IPINFO_TOKEN`
- **Purpose**: Authentication token for ipinfo.io API service used in IP safety checks
- **Required**: No (optional, requests work without it but may be rate-limited)
- **Used in**: `polymarket_direct/safe.py`
- **Behavior**: When provided, adds Bearer token authentication to requests to ipinfo.io for IP geolocation lookups. Without a token, the service may have stricter rate limits.

## System-wide Variables

### `ARGUS_DISABLE_NOTIFICATIONS`
- **Purpose**: Disable system notifications (1) or enable (0)
- **Default**: `0` (enabled)
- **Required**: No
- **Used in**: `_argus_utils.py`

### `ARGUS_CACHES_DISABLED`
- **Purpose**: Disable **ALL** caching mechanisms globally across all modules
- **Default**: Not set (caching **enabled**)
- **Required**: No
- **Used in**: `cache_utils/__init__.py`, `capital/_caches.py`, `ib/__init__.py`, `polymarket/__init__.py`
- **Values**: `1`, `true`, `True`, `TRUE` to disable

#### What Gets Cached (Impact When Disabled)

When caching is **enabled** (default), the following API calls are cached and will NOT repeat if called with same parameters:

**Interactive Brokers (IB):**
- `IBNetworker.search_contract()` - Contract symbol searches (e.g., searching "AAPL" returns cached SearchResult)
- Contract metadata and descriptions

**Capital.com:**
- `resolve_symbol()` - Symbol resolution to EPIC format (e.g., "BTCUSD" → Capital.com market details)
- Market metadata and instrument details
- These calls can take 1-5 seconds each, and repeated lookups for the same symbol happen frequently

**Polymarket:**
- Market enumeration results (list of all markets)
- Market ticker data and clob client prices
- Account data and order history

#### Performance Impact When Disabled

When `ARGUS_CACHES_DISABLED=1`, **every function call re-executes the full API request**, even for identical parameters:

**Example - IB Contract Search:**
```python
# Without cache (ARGUS_CACHES_DISABLED=1):
search_contract("AAPL")  # 2-3 seconds, hits IB API
search_contract("AAPL")  # 2-3 seconds AGAIN, hits IB API again
search_contract("AAPL")  # 2-3 seconds AGAIN, hits IB API again

# With cache (default):
search_contract("AAPL")  # 2-3 seconds, hits IB API
search_contract("AAPL")  # <1ms, returns cached result
search_contract("AAPL")  # <1ms, returns cached result
```

**Example - Capital.com Symbol Resolution:**
```python
# Without cache:
resolve_symbol("BTCUSD")  # API call + search fallback, 1-5 seconds
resolve_symbol("BTCUSD")  # API call + search fallback AGAIN, 1-5 seconds
# Startup time for strategies with 100+ symbols: 100-500 seconds!

# With cache:
resolve_symbol("BTCUSD")  # 1-5 seconds first time
resolve_symbol("BTCUSD")  # <1ms cached
# Startup time: 5-10 seconds for 100+ symbols
```

#### When to Disable

**Use `ARGUS_CACHES_DISABLED=1` when:**
- Testing/debugging (clean slate for each test run)
- Writing tests (CI/CD pipeline - see `run_tests.py`)
- Troubleshooting stale data issues
- Forcing fresh API data (market metadata changes)

**Do NOT disable in production** - caching is critical for performance:
- API rate limit compliance
- Startup time reduction
- Network latency reduction
- Cost optimization (fewer API calls)

## File Structure

Environment variables are primarily managed through:
- `.env` file for local development
- System environment variables for production
- Some variables are auto-generated during authentication processes (like `IB_COOKIE`)

## Security Notes

- Sensitive variables like private keys, passwords, and API tokens should never be committed to version control
- Use `.env` files for local development and ensure they're in `.gitignore`
- For production, use secure environment variable management systems