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

### `POLYMARKET_MAX_SOCKET_RETRIES`
- **Purpose**: Maximum number of socket connection retries
- **Default**: `50` (for WebSocket connections in `wss.py`), varies by component
- **Required**: No
- **Used in**: `polymarket_direct/wss.py`
- **Note**: Different components may use different defaults for this variable

### `POLYMARKET_ORDERBOOK_DEPTH`
- **Purpose**: Controls the depth of orderbook data (number of bid/ask levels to fetch)
- **Default**: `10`
- **Required**: No
- **Used in**: `polymarket/__init__.py`, `tests/test_poly_dispatcher.py`
- **Example**: Set to `20` for deeper orderbook data, `5` for shallower data


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
- **Used in**: `polymarket_direct/wss.py`

### `POLYMARKET_DISABLE_PING_PONG_LOGS`
- **Purpose**: Disable ping-pong logging to reduce noise
- **Default**: `false`
- **Required**: No
- **Used in**: `polymarket_direct/wss.py`

### `POLYMARKET_KEEPALIVE_INTERVAL`
- **Purpose**: Interval in seconds between CLOB REST keepalive pings
- **Default**: `5.0`
- **Required**: No
- **Used in**: `polymarket_direct/rest.py`
- **Behavior**: Controls how often the background keepalive thread pings `/ok` to keep the shared httpx connection (TCP+TLS+SOCKS5) warm, so hot-path order POSTs avoid a cold handshake

### `POLYMARKET_KEEPALIVE_DISABLE`
- **Purpose**: Disable the CLOB REST keepalive thread
- **Default**: `false`
- **Required**: No
- **Used in**: `polymarket_direct/rest.py`
- **Behavior**: When `true`, the background keepalive pinger is not started and idle connections may need to be re-established on the next order POST

### `POLYMARKET_FULL_MARKET_CACHE_REFRESH_INTERVAL`
- **Purpose**: Refresh interval for full market cache in seconds
- **Default**: `300` (5 minutes)
- **Required**: No
- **Used in**: `polymarket/__init__.py`

### `POLYMARKET_BUILD_POOL_WORKERS`
- **Purpose**: Thread pool size for concurrent order building in `place_multiple_orders`
- **Default**: `10`
- **Required**: No
- **Used in**: `polymarket/__init__.py`
- **Behavior**: Sets the max workers of the persistent `ThreadPoolExecutor` reused across order-building calls, avoiding thread-spawn overhead on each invocation

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
- **Used in**: `polymarket_direct/wss.py`
- **Behavior**: When `true`, triggers fuss notifications and macOS notifications for user account events received via WebSocket




### POLYMARKET_UNSAFE_RAPID_CONNECTIONS
- **Purpose**: For endpoints that do not require authentication, bypass WireProxy to enable maximum performance.
- **Default**: `false`
- **Required**: No
- **Used in**: `polymarket_direct/wss.py`, `polymarket_direct/rest.py`, `polymarket_direct/unsafe_api.py`
- **Behavior**: When `true`, all connections to Polymarket that do not require authenticationa are made directly without routing through WireProxy. These include changes across the rest and websocket layer of the dispatcher. This feature is not stable and its behavior maybe changed with future updates (i.e., supporting more 'usafe' connections. This works in tangent with WIREPROXY integration (and only makes sense if you are geo-blocked from placing orders). It selectively punches holes in the connections.
- **Warning**: Enabling this in a geo-blocked region will result in connection failures. Use with caution and only if you are sure your IP is not blocked for market data access.


### `POLYMARKET_SIGNATURE_TYPE`
- **Purpose**: Signature type for CLOB client authentication
- **Default**: `3`
- **Required**: No
- **Used in**: `polymarket_direct/rest.py`
- **Behavior**: Sets the signature type when creating the CLOB client. Must be `1` or `3`. Value `3` is the newer signature format.
- **Note**: If an invalid value is provided, the application will raise a `ValueError` at startup

### WebSocket Sharding Configuration

### `POLYMARKET_MAX_ASSETS_PER_WS`
- **Purpose**: Maximum number of assets (markets) to assign per WebSocket shard
- **Default**: `4`
- **Required**: No
- **Used in**: `polymarket_direct/wss.py`
- **Behavior**: Controls how many markets are subscribed per WebSocket connection when using the sharded orderbook store

### `POLYMARKET_MIN_SHARDS`
- **Purpose**: Minimum number of WebSocket shards to maintain
- **Default**: `1`
- **Required**: No
- **Used in**: `polymarket_direct/wss.py`
- **Behavior**: Sets the floor for shard count. Will be corrected to `1` if set to `0` or lower

### `POLYMARKET_MAX_SHARDS`
- **Purpose**: Maximum number of WebSocket shards to create
- **Default**: `10`
- **Required**: No
- **Used in**: `polymarket_direct/wss.py`
- **Behavior**: Sets the ceiling for shard count. Automatically adjusted to be at least `_min_shards` if configured lower

### `POLYMARKET_SCALE_DOWN_IDLE_S`
- **Purpose**: Idle time in seconds before a shard is eligible for scale-down
- **Default**: `30`
- **Required**: No
- **Used in**: `polymarket_direct/wss.py`
- **Behavior**: Shards that have been idle (no activity) for this duration enter a grace window before being closed. Uses monotonic time to avoid clock skew issues

### `POLYMARKET_WS_STAT_SAMPLES`
- **Purpose**: Maximum number of WebSocket frame timestamp samples retained for stats
- **Default**: `4096`
- **Required**: No
- **Used in**: `polymarket_direct/wss.py`
- **Behavior**: Bounds the per-shard `deque` of frame timestamps used by `print_stats`. Previously an unbounded list that grew for the life of the process and leaked gigabytes under load

### `POLYMARKET_WS_RESTORE_TIMEOUT`
- **Purpose**: Upper bound in seconds a restore thread waits for the first PONG after a reconnect
- **Default**: `120`
- **Required**: No
- **Used in**: `polymarket_direct/wss.py`
- **Behavior**: Generous on purpose: PING goes out every 10s and the ping/pong failure detector tears the socket down after 3 missed PONGs (~30s). This timeout only fires for a wedged socket, not a slow one

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

### `POLYMARKET_DISPATCHER_LOG_FILE`
- **Purpose**: File path for the Polymarket dispatcher log file
- **Default**: `~/.argus/polymarket_dispatcher.log`
- **Required**: No
- **Used in**: `polymarket/__init__.py`
- **Behavior**: Controls where the Polymarket dispatcher writes its log output

### `POLYMARKET_DISPATCHER_PYTHON_STATE_FILE`
- **Purpose**: File path for the dispatcher's persisted Python state (pickled asset-id/ticker/market caches)
- **Default**: `~/.argus/polymarket_dispatcher_state.pkl`
- **Required**: No
- **Used in**: `polymarket/__init__.py`
- **Behavior**: On startup, if the file exists and is newer than `POLYMARKET_FULL_MARKET_CACHE_REFRESH_INTERVAL`, the dispatcher loads its routing state from it instead of rebuilding the mapping from APDB

## Argus Polymarket Database (APDB)

### `APDB_BIND_ADDRESS`
- **Purpose**: Unix domain socket path the APDB server binds to and the dispatcher/satellite system connect to
- **Default**: `/tmp/argus_polymarket_db.sock`
- **Required**: No
- **Used in**: `polymarket/apdb_client.py`, `satellite_sys/__init__.py` (also read by the `argus-polymarket-db` Rust server as its bind address — a single source of truth for both processes)
- **Behavior**: Must be consistent across the APDB process and every Argus client connecting to it; the dispatcher raises a startup `RuntimeError` if APDB cannot be reached at this path


## Hyperliquid Perpetuals

### `HYPERLIQUID_WALLET_ADDRESS`
- **Purpose**: Wallet address for Hyperliquid authentication and account data
- **Required**: Yes (for the Hyperliquid dispatcher)
- **Used in**: `perpetuals/hyper/__init__.py`, `perpetuals/hyper/rest.py`
- **Behavior**: Read at startup when no `wallet_address` is passed to `HyperLiquidDispatcher`; used to construct `HyperLiquidRest`. It is the `user` on every account read (`get_balance`, `get_positions`, `get_orders`, `get_order_status`, `get_trades`, `get_funding_payments`, `get_account_fees`, `get_rate_limit_usage`), which are unsigned `info` requests. **It must be the master account address**: querying with an API/agent wallet address returns an empty account. See `docs/PERPETUALS_ACCOUNT.md`.

### `HYPERLIQUID_PRIVATE_KEY`
- **Purpose**: Private key for Hyperliquid authentication
- **Required**: Yes (for the Hyperliquid dispatcher)
- **Used in**: `perpetuals/hyper/__init__.py`, `perpetuals/hyper/rest.py`
- **Behavior**: Read at startup when no `private_key` is passed to `HyperLiquidDispatcher`; used to construct `HyperLiquidRest`

### `HYPERLIQUID_ORDERBOOK_DEPTH`
- **Purpose**: Controls the depth of orderbook data (number of bid/ask levels streamed in P2 packets)
- **Default**: `10`
- **Required**: No
- **Used in**: `perpetuals/hyper/__init__.py`, `tests/hyper_cli.py`

### `HYPERLIQUID_MAX_SOCKET_RETRIES`
- **Purpose**: Maximum number of socket connection retries
- **Default**: `50`
- **Required**: No
- **Used in**: `perpetuals/hyper/wss.py`

### `HYPERLIQUID_MAX_PING_PONG_FAILURES`
- **Purpose**: Maximum number of ping-pong failures before reconnect
- **Default**: `3`
- **Required**: No
- **Used in**: `perpetuals/hyper/wss.py`

### `HYPERLIQUID_PING_INTERVAL_S`
- **Purpose**: Interval in seconds between WebSocket pings
- **Default**: `20`
- **Required**: No
- **Used in**: `perpetuals/hyper/wss.py`
- **Behavior**: Hyperliquid closes connections silent for 60s; the default pings comfortably under that

### `HYPERLIQUID_DISABLE_PING_PONG_LOGS`
- **Purpose**: Disable ping-pong logging to reduce noise
- **Default**: `false`
- **Required**: No
- **Used in**: `perpetuals/hyper/wss.py`

### `HYPERLIQUID_WS_RESTORE_TIMEOUT`
- **Purpose**: Upper bound in seconds a restore thread waits for the first PONG after a reconnect
- **Default**: `120`
- **Required**: No
- **Used in**: `perpetuals/hyper/wss.py`

## Lighter Perpetuals

### `LIGHTER_ACCOUNT_INDEX`
- **Purpose**: The integer Lighter account (master or sub-account) the account actions report on
- **Required**: No (without it, and without `LIGHTER_AUTH_TOKEN`, the account actions answer with `AccountNotConfiguredError`; market data is unaffected)
- **Used in**: `perpetuals/lighter/__init__.py`, `perpetuals/lighter/rest.py`
- **Behavior**: `get_balance` / `get_positions` are public reads keyed by this index alone (`GET /api/v1/account`). Find the index for an L1 address with `LighterRest.get_accounts_by_l1_address(...)` or in the Lighter web UI. If omitted but `LIGHTER_AUTH_TOKEN` is set, the index embedded in the token is used.

### `LIGHTER_AUTH_TOKEN`
- **Purpose**: A Lighter **read-only API token** (`ro:<account_index>:<single|all>:<expiry_unix>:<hex>`) for the auth-gated account reads
- **Required**: No (without it, `get_orders` / `get_order_status` / `get_trades` / `get_funding_payments` answer with `AccountNotConfiguredError`; `get_balance` / `get_positions` still work)
- **Used in**: `perpetuals/lighter/__init__.py`, `perpetuals/lighter/rest.py`
- **Behavior**: Sent verbatim in the `authorization` header of auth-gated requests. Mint one in the Lighter web UI (API keys page) or via `POST /api/v1/tokens_create`; read-only tokens live between 1 day and 10 years, so no signing library or API-key private key is needed for reads. The dispatcher refuses to start with a token that is malformed, expired, or scoped (`single`) to a different account than `LIGHTER_ACCOUNT_INDEX`. This is **not** the short-lived signed token the Lighter SDK mints for order execution; that comes with the trading work.

### `LIGHTER_ORDERBOOK_DEPTH`
- **Purpose**: Controls the depth of orderbook data (number of bid/ask levels streamed in P2 packets)
- **Default**: `10`
- **Required**: No
- **Used in**: `perpetuals/lighter/__init__.py`, `tests/lighter_cli.py`

### `LIGHTER_MAX_SOCKET_RETRIES`
- **Purpose**: Maximum number of socket connection retries
- **Default**: `50`
- **Required**: No
- **Used in**: `perpetuals/lighter/wss.py`

### `LIGHTER_MAX_PING_PONG_FAILURES`
- **Purpose**: Maximum number of ping-pong failures before reconnect
- **Default**: `3`
- **Required**: No
- **Used in**: `perpetuals/lighter/wss.py`

### `LIGHTER_PING_INTERVAL_S`
- **Purpose**: Interval in seconds between WebSocket pings
- **Default**: `60`
- **Required**: No
- **Used in**: `perpetuals/lighter/wss.py`
- **Behavior**: Lighter requires a client frame at least every 2 minutes; the default pings comfortably under that (wider margin than Hyperliquid's 20s since Lighter's idle-close window is double)

### `LIGHTER_DISABLE_PING_PONG_LOGS`
- **Purpose**: Disable ping-pong logging to reduce noise
- **Default**: `false`
- **Required**: No
- **Used in**: `perpetuals/lighter/wss.py`

### `LIGHTER_WS_RESTORE_TIMEOUT`
- **Purpose**: Upper bound in seconds a restore thread waits for the first PONG after a reconnect
- **Default**: `120`
- **Required**: No
- **Used in**: `perpetuals/lighter/wss.py`


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

### `ARGUS_PROD`
- **Purpose**: Enable production mode to suppress certain warnings in the cache system
- **Default**: `false`
- **Required**: No
- **Used in**: `cache_sys/__init__.py`
- **Behavior**: When set to `true`, suppresses automatic disabling of cache when warnings are triggered. This is intended for production deployments where cache should remain enabled despite warnings.

## Testing

### `BENCHMARK_ITERATIONS`
- **Purpose**: Number of iterations for benchmark tests
- **Default**: `10000`
- **Required**: No
- **Used in**: `tests/benchmark_p2_encoding.py`
- **Behavior**: Controls how many iterations to run when benchmarking the P2 encoding/decoding performance

## File Structure

Environment variables are primarily managed through:
- `.env` file for local development
- `.env.enc.se` encrypted file, decrypted just-in-time by `SecureEnvLoader` (see Security Notes)
- System environment variables for production
- Some variables are auto-generated during authentication processes (like `IB_COOKIE`)

## Security Notes

- Sensitive variables like private keys, passwords, and API tokens should never be committed to version control
- Use `.env` files for local development and ensure they're in `.gitignore`
- For production, use secure environment variable management systems

### Encrypted `.env` Loading via SDist (`SecureEnvLoader`)

Argus supports **just-in-time encrypted environment loading** through [SDist](https://github.com/The-Sal/SDist) and its macOS Secure Enclave backend:

- `SecureEnvLoader` (`EnvLoader` in `_argus_utils.py`, exposed as the singleton `SECURE_ENV_VAR_LOADER`) is the single `load_dotenv()` used throughout the codebase; every module imports its `load_dotenv()` instead of calling `python-dotenv` directly
- If a `.env.enc.se` file (SDist Secure Enclave format, magic `SDIST.SE`) is present and the `sdist` CLI is on `PATH`, the loader decrypts it to `.env` via `sdist -c -p NONE --args-only -f decrypt-se -a .env.enc.se .env`, loads the result with `python-dotenv`, then immediately deletes the plaintext `.env`. The load only ever happens once per process
- Decryption requires **macOS with a Secure Enclave** (Darwin). On other platforms, or if `sdist` is not installed, the loader prints a warning and falls back to loading a plaintext `.env` (which must be present in that case)
- Encrypt an existing `.env` with `sdist -c -p n -f encrypt-se -a .env .env.enc.se ?`, then delete the plaintext. The `.run/Encrypt Env.run.xml` and `.run/Decrypt Env.run.xml` IDE run configurations wrap these commands
- `ib/set_auth.py` refuses to run while the loader is active (`UnavailableInSecurityContext`) because it would persist the IBKR cookie into a plaintext `.env`
- `.env.enc.se` is ignored by `.gitignore` (`*.enc.se`): the encryption protects credentials at rest on disk, it does **not** make committing them acceptable — never commit either file