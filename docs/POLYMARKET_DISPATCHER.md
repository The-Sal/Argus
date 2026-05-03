# PolymarketDispatcher TCP Protocol Specification


# ⚠️ WARNINGS
* You cannot place orders on an asset that is not currently subscribed to. Either you (or someone else) must have subscribed to that asset and be alive. (this maybe later revised)
* You will not get push-based updates if you are not subscribed to an asset (this maybe later revised)


## Overview

The `PolymarketDispatcher` is a TCP server that exposes Polymarket's CLOB (Central Limit Order Book) via two protocols:

- **P1 (Protocol 1)**: JSON-based control protocol for commands and responses
- **P2 (Protocol 2)**: Binary market data protocol for real-time order book updates

**Default Connection**: `localhost:9972`

---

## Protocol 1 (P1): Control Protocol

### Packet Format

```
~<length:04d>|<json-data>
```

- `~`: Start marker (1 byte)
- `<length:04d>`: 4-digit zero-padded length of JSON data (4 bytes)
- `|`: Separator (1 byte)
- `<json-data>`: UTF-8 encoded JSON payload (variable length, max 9999 bytes)

### Request Structure

```json
{
  "action": "<command_name>",
  "data": { /* command-specific arguments */ },
  "correlation_id": "<optional-uuid>"  // Optional: for request tracking
}
```

### Response Structure

```json
{
  "action": "<command_name>",
  "data": { /* response data or null */ },
  "error": "<error message or null>",
  "compressed": <bool>,       // true when data is auto-compressed (see below)
  "correlation_id": "<uuid>"  // Included if provided in request
}
```

### Error Handling

- Invalid JSON or malformed packets return an error response with `action: null`
- Unknown actions return `InvalidArgumentError`
- Missing required fields return `InvalidArgumentError`
- Duplicate `correlation_id` values return `CorrelationIDAlreadySeenError`
- **Packet size exceeded**: Responses ≥ **9500 bytes** are automatically compressed with `zlib` (level 9) and base64-encoded. In this case `data` will be a compressed string and `compressed` will be `true`. If the payload is still ≥ 9500 bytes after compression, an error is raised. Use pagination for large data (e.g., `fetch_all_tickers`, `get_trades`)

---

## Protocol 2 (P2): Market Data Protocol

### Packet Format

```
~<packet-length:04d><symbol-length:04d>|<symbol><csv-data>L
```

- `~`: Start marker (1 byte)
- `<packet-length:04d>`: Total packet length excluding header (4 bytes)
- `<symbol-length:04d>`: Symbol string length (4 bytes)
- `|`: Separator (1 byte)
- `<symbol>`: ASCII-encoded symbol (variable length)
- `<csv-data>`: Comma-separated market data values (variable length)
- `L`: Terminator byte (1 byte)

### Symbol Format

```
<Event-Ticker>-<Market-Slug>-<Asset-ID>
```

Example: `bitcoin-up-or-down-march-1-bitcoin-up-or-down-march-1-661095475084821930790589425827399710453605787397495798070750303202782280580`

### CSV Data Fields

The CSV data contains order book depth levels (controlled by `POLYMARKET_ORDERBOOK_DEPTH`, default 10):

```
<bid1_price>,<bid1_size>,<bid2_price>,<bid2_size>,...,<ask1_price>,<ask1_size>,<ask2_price>,<ask2_size>,...,<timestamp>,<server_timestamp>
```

**Field Order:**
1. Bid prices and sizes (N levels)
2. Ask prices and sizes (N levels)
3. Exchange timestamp (from Polymarket)
4. Server timestamp (dispatcher local time)

**Note:** Missing levels are padded with `0,0`

---

## Available Functions

### Market Data Subscriptions

#### `subscribe`
Subscribe to real-time market data for specific clob_token_ids.

**Input:**
```json
{
  "action": "subscribe",
  "data": ["<clob_id_1>", "<clob_id_2>", ...]
}
```

**Output:**
```json
{
  "action": "subscribe",
  "data": {
    "subscribed": ["<clob_id_1>", ...],
    "failed": ["<clob_id_x>", ...]
  },
  "error": null,
  "compressed": false
}
```

---

#### `subscribe_to_market_by_ticker`
Subscribe to all clob_ids for a market identified by its ticker.

**Input:**
```json
{
  "action": "subscribe_to_market_by_ticker",
  "data": ["<ticker>"]
}
```

**Output:** Same as `subscribe`

---

#### `unsubscribe`
Unsubscribe from specific clob_ids.

**Input:**
```json
{
  "action": "unsubscribe",
  "data": ["<clob_id_1>", "<clob_id_2>", ...]
}
```

**Output:**
```json
{
  "action": "unsubscribe",
  "data": {
    "unsubscribed": ["<clob_id_1>", ...],
    "failed": ["<clob_id_x>", ...]
  },
  "error": null,
  "compressed": false
}
```

---

#### `unsubscribe_from_market_by_ticker`
Unsubscribe from all clob_ids for a market by ticker.

**Input:**
```json
{
  "action": "unsubscribe_from_market_by_ticker",
  "data": ["<ticker>"]
}
```

**Output:** Same as `unsubscribe`

---

#### `orderbook_snapshot`
Trigger an on-demand orderbook snapshot for subscribed clob_ids. Data arrives via P2 with timestamp=0.

**Input:**
```json
{
  "action": "orderbook_snapshot",
  "data": ["<clob_id_1>", "<clob_id_2>", ...]
}
```

**Output:**
```json
{
  "action": "orderbook_snapshot",
  "data": {
    "successful": ["<clob_id_1>", ...],
    "failed": ["<clob_id_x>", ...]
  },
  "error": null,
  "compressed": false
}
```

---

### Market Data Requests

#### `fetch_all_markets`
Fetch all markets (heavy response, use `fetch_all_tickers` if possible).

**Input:**
```json
{
  "action": "fetch_all_markets",
  "data": {}
}
```

**Output:** Array of market objects (full PolymarketEvent data)

---

#### `fetch_all_tickers`
Fetch all market tickers with pagination.

**Input:**
```json
{
  "action": "fetch_all_tickers",
  "data": [<limit>, <offset>]
}
```
- `limit`: Maximum number of tickers to return (default: 100)
- `offset`: Pagination offset (default: 0)

**Output:** Array of ticker strings

---

#### `fetch_market_by_ticker`
Fetch detailed market data by ticker.

**Input:**
```json
{
  "action": "fetch_market_by_ticker",
  "data": ["<ticker>"]
}
```

**Output:** PolymarketEvent object as dictionary

---

#### `search_markets`
Search markets by keyword (fuzzy matching).

**Input:**
```json
{
  "action": "search_markets",
  "data": ["<keyword>", <limit>]
}
```
- `keyword`: Search term
- `limit`: Maximum results (default: 10)

**Output:** Array of matching ticker strings

---

#### `fetch_clob_id_information`
Get detailed information about a clob_id.

**Input:**
```json
{
  "action": "fetch_clob_id_information",
  "data": ["<clob_id>"]
}
```

**Output:**
```json
{
  "event_name": "<event title>",
  "market_name": "<market question>",
  "outcome": "<outcome name>",
  "ticker": "<ticker>",
  "market_slug": "<slug>",
  "aot_p2_symbol": "<full-p2-symbol>"
}
```

---

### Order Management

#### `place_order`
Place a single order on the CLOB.

**Input:**
```json
{
  "action": "place_order",
  "data": {
    "token_id": "<clob_id>",
    "price": <float>,
    "size": <float>,
    "side": "buy|sell",
    "order_type": "GTC|FOK|IOC"  // Optional, default: GTC
  }
}
```

**Output:** Order result from CLOB API
```json
{
  "errorMsg": "",
  "orderID": "0x...",
  "takingAmount": "",
  "makingAmount": "",
  "status": "live",
  "success": true
}
```

---

#### `place_multiple_orders`
Place multiple orders in a single batch request.

**Input:**
```json
{
  "action": "place_multiple_orders",
  "data": {
    "orders": [
      {
        "token_id": "<clob_id_1>",
        "price": <float>,
        "size": <float>,
        "side": "buy|sell"
      },
      // ... more orders
    ]
  }
}
```

**Output:** Batch order result from CLOB API

---

#### `cancel_order`
Cancel an existing order.

**Input:**
```json
{
  "action": "cancel_order",
  "data": {
    "order_id": "<order_id>"
  }
}
```

**Output:**
```json
{
  "not_canceled": {},
  "canceled": ["<order_id>"]
}
```

---

#### `cancel_multiple_orders`
Cancel multiple orders in a single batch request (one HTTP POST).

**Input:**
```json
{
  "action": "cancel_multiple_orders",
  "data": {
    "order_ids": ["<order_id_1>", "<order_id_2>", "<order_id_3>"]
  }
}
```

**Output:**
```json
{
  "not_canceled": {
    "<order_id_1>": "order can't be found - already canceled or matched"
  },
  "canceled": ["<order_id_2>", "<order_id_3>"]
}
```

**Notes:**
- All cancellations are sent in a single HTTP POST request to the Polymarket CLOB API
- The `order_ids` field must be a non-empty list of strings
- Each order ID in the response will appear either in `canceled` or `not_canceled`, but never both

---

#### `get_order_status`
Get detailed status of a specific order.

**Input:**
```json
{
  "action": "get_order_status",
  "data": {
    "order_id": "<order_id>"
  }
}
```

**Output:** PolyMarketOrder as dictionary (includes id, status, price, size, side, etc.)

---

#### `get_orders`
Get all open orders for the account.

**Input:**
```json
{
  "action": "get_orders",
  "data": {}
}
```

**Output:** Array of PolyMarketOrder dictionaries

---

#### `get_trades`
Get all trades for the account with pagination support. Due to packet size limits (9999 bytes), use pagination for large trade histories.

**Input:**
```json
{
  "action": "get_trades",
  "data": [<limit>, <offset>]
}
```
- `limit`: Maximum number of trades to return (default: 50, recommended max: 100)
- `offset`: Pagination offset (default: 0)

**Output:** Array of Trade dictionaries

**Example - Fetch all trades with pagination:**
```python
all_trades = []
offset = 0
limit = 50

while True:
    # Request: {"action": "get_trades", "data": [50, 0]}
    trades = send_request('get_trades', [limit, offset])
    if not trades:
        break
    all_trades.extend(trades)
    if len(trades) < limit:
        break  # Last page
    offset += limit
```

**Note:** Trade objects are large (contain ~20 fields including nested `maker_orders`). Exceeding the packet size limit will cause a `ValueError: Data length exceeds maximum allowed size` error.

---

#### `get_balance`
Get the account's USDC balance.

**Input:**
```json
{
  "action": "get_balance",
  "data": {}
}
```

**Output:** Float representing USDC balance

---

### Crypto Utilities

#### `get_price_to_beat`
Get the reference price for Up/Down crypto markets.

Uses a dual-strategy approach:
1. **Method 1**: Frontend HTML scraper (primary)
2. **Method 2**: Crypto price API fallback

**Input:**
```json
{
  "action": "get_price_to_beat",
  "data": ["<ticker>"]
}
```

**Output:** Float representing the price to beat

---

### Utilities

#### `ping`
Simple ping/pong for connection testing.

**Input:**
```json
{
  "action": "ping",
  "data": {}
}
```

**Output:** `"pong"`

---

#### `rtt_to_exchange`
Measure round-trip time to the Polymarket exchange.

**Input:**
```json
{
  "action": "rtt_to_exchange",
  "data": {}
}
```

**Output:** Float (seconds)

---

## Push System (Server-Initiated Messages)

The dispatcher pushes real-time updates to connected clients without requiring explicit requests.

### Account Updates (`account_update`)

Real-time account lifecycle events are pushed to all connected clients that have at least one active market data subscription.

**⚠️ CRITICAL REQUIREMENT**: To receive `account_update` pushes, you MUST first subscribe to at least one asset via the `subscribe` action. Clients without subscriptions will NOT receive account events even though the dispatcher receives them from the CLOB.

**Pushed Message Format:**
```json
{
  "action": "account_update",
  "data": {
    "order_id": "0x...",
    "status": "PLACED|CANCELLED|MATCHED|...",
    "side": "BUY|SELL",
    "price": "0.75",
    "size": "100",
    "asset_id": "<clob_id>",
    "timestamp": "1770251679393",
    // ... additional order event fields
  },
  "error": null,
  "compressed": false
}
```

**Event Types:**
- `PLACED`: New order placed
- `CANCELLED`: Order cancelled
- `MATCHED`: Order filled/matched
- And other CLOB lifecycle events

---

### Fatal Errors (`fatal_error`)

Critical errors from the REST API are broadcast to all connected clients.

**Pushed Message Format:**
```json
{
  "action": "fatal_error",
  "data": {
    "function": "<function_name>",
    "exception": "<error_message>",
    "traceback": "<stack_trace>"
  },
  "error": "<error_message>",
  "compressed": false
}
```

---

## Important Implementation Notes

### Subscription Requirements for Account Updates

The dispatcher tracks connected clients through the `RoutingHelper`'s socket set, which is **only populated when a client subscribes to market data**. This means:

1. **Client connects** → Socket not tracked for pushes
2. **Client subscribes to at least one asset** → Socket added to tracking
3. **Client receives account_update pushes** ✓

**Workflow for order management with account updates:**
1. Connect to dispatcher
2. Subscribe to at least one asset_id (even if you don't care about its market data)
3. Place orders
4. Receive real-time account_update pushes

### Correlation IDs

The dispatcher supports optional `correlation_id` fields for request/response tracking:

- Must be unique per request (UUID recommended)
- Maximum length: 40 characters (configurable via `MAX_CORRELATION_ID_LENGTH`)
- Duplicate IDs will be rejected with `CorrelationIDAlreadySeenError`
- Included in response if provided in request

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `POLYMARKET_PRIVATE_KEY` | Required | Private key for authentication |
| `POLYMARKET_PROXY_FUNDER` | Required | Proxy funder address |
| `POLYMARKET_ORDERBOOK_DEPTH` | 10 | Order book levels in P2 packets |
| `POLYMARKET_FULL_MARKET_CACHE_REFRESH_INTERVAL` | 300 | Market cache refresh (seconds) |
| `MAX_SEEN_CORRELATION_IDS` | 100000 | Max correlation IDs to track |
| `MAX_CORRELATION_ID_LENGTH` | 40 | Max correlation ID length |

### Connection Lifecycle

1. **Connect**: TCP connection established
2. **Subscribe**: Client must subscribe to receive market data (P2) and account updates
3. **Receive Data**: P2 packets pushed for subscribed assets
4. **Disconnect**: Socket cleaned up, subscriptions removed, CLOB unsubscribed

### Error Codes

| Error | Description |
|-------|-------------|
| `InvalidArgumentError` | Missing or invalid arguments |
| `PolyMarketDispatcherError` | General dispatcher errors |
| `CorrelationIDLengthTooLongError` | Correlation ID exceeds max length |
| `CorrelationIDAlreadySeenError` | Duplicate correlation ID |

---

## Example Client Workflow

```python
import socket
import json

def encode_packet(data: bytes) -> bytes:
    return f"~{len(data):04d}|".encode('ascii') + data

def decode_packet(packet: bytes) -> bytes:
    length = int(packet[1:5])
    return packet[6:6+length]

# Connect
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect(('localhost', 9972))

# Subscribe to market data (REQUIRED for account updates)
subscribe_req = {
    "action": "subscribe",
    "data": ["661095475084821930790589425827399710453605787397495798070750303202782280580"],
    "correlation_id": "uuid-1234-5678"
}
sock.sendall(encode_packet(json.dumps(subscribe_req).encode()))

# Receive P2 market data packets (binary)
# Format: ~<packet-len:04d><symbol-len:04d>|<symbol><csv-data>L

# Place an order
order_req = {
    "action": "place_order",
    "data": {
        "token_id": "661095475084821930790589425827399710453605787397495798070750303202782280580",
        "price": 0.75,
        "size": 100,
        "side": "buy"
    }
}
sock.sendall(encode_packet(json.dumps(order_req).encode()))

# Receive account_update push when order is placed/filled
# {"action": "account_update", "data": {...}, "error": null, "compressed": false}
```

---

## See Also

- `argus/polymarket/__init__.py`: Main dispatcher implementation
- `argus/protocol.py`: P1 and P2 protocol implementations
- `argus/polymarket/_classes.py`: Supporting classes (RoutingHelper, ArgsObject, etc.)
