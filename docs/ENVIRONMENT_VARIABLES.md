# Argus Project Environment Variables

This document lists all environment variables used throughout the Argus project and their purposes.

## Polymarket Direct Integration

### `POLYMARKET_PRIVATE_KEY`
- **Purpose**: Private key for Polymarket authentication
- **Required**: Yes (for trading/order placement)
- **Used in**: `polymarket_direct/_example.py`, `polymarket_direct/_examples/unsub_test.py`
- **Example**: Export your private key for Polymarket API access

### `POLYMARKET_PROXY_FUNDER`
- **Purpose**: Proxy funder address for Polymarket
- **Required**: Yes (for trading/order placement)
- **Used in**: `polymarket_direct/_example.py`, `polymarket_direct/_examples/unsub_test.py`

### `POLYMARKET_ENABLE_CLOB`
- **Purpose**: Enable/disable ClobClient initialization for Polymarket
- **Default**: `false`
- **Required**: No
- **Used in**: `polymarket_direct/__init__.py`

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
- **Default**: `3` (based on code context)
- **Required**: No
- **Used in**: `polymarket_direct/__init__.py`

### `POLYMARKET_MAX_MESSAGE_COUNT`
- **Purpose**: Maximum message count before rolling mechanism triggers
- **Default**: `5000`
- **Required**: No
- **Used in**: `polymarket_direct/__init__.py`

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

### `POLYMARKET_PARANOID`
- **Purpose**: Enable immediate termination if IP is in known geo-blocked regions
- **Default**: `false`
- **Required**: No
- **Used in**: `polymarket_direct/rest.py`
- **Behavior**: When `true`, terminates immediately on Stage 1 IP check failure

### `POLYMARKET_PROTECTION`
- **Purpose**: Enable/disable Polymarket geo-block protection checks
- **Default**: `true`
- **Required**: No
- **Used in**: `polymarket_direct/rest.py`
- **Behavior**: When `false`, skips Stage 2 direct Polymarket geo-block verification (DANGEROUS)

### `POLYMARKET_USER_EVENTS_FUSS`
- **Purpose**: Enable/disable fuss notifications for user account events
- **Default**: `false`
- **Required**: No
- **Used in**: `polymarket_direct/rest.py`
- **Behavior**: When `true`, triggers fuss notifications and macOS notifications for user account events received via WebSocket

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

## System-wide Variables

### `ARGUS_DISABLE_NOTIFICATIONS`
- **Purpose**: Disable system notifications (1) or enable (0)
- **Default**: `0` (enabled)
- **Required**: No
- **Used in**: `_argus_utils.py`

### `ARGUS_CACHES_DISABLED`
- **Purpose**: Disable all caching mechanisms globally
- **Default**: Not set (caching enabled)
- **Required**: No
- **Used in**: `cache_utils/__init__.py`, `capital/_caches.py`

## File Structure

Environment variables are primarily managed through:
- `.env` file for local development
- System environment variables for production
- Some variables are auto-generated during authentication processes (like `IB_COOKIE`)

## Security Notes

- Sensitive variables like private keys, passwords, and API tokens should never be committed to version control
- Use `.env` files for local development and ensure they're in `.gitignore`
- For production, use secure environment variable management systems