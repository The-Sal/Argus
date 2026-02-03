The following specification covers the entire Polymarket Dispatcher API surface.

---

# Overview

The Polymarket Dispatcher exposes a **single heterogeneous socket** on **Port 9972** that handles all communication (both control plane and market data) through a unified protocol. Both inbound and outbound messages use packet encoding, with differentiation based on message structure.

**Single Socket Architecture:**
- All communication flows through a single TCP connection
- Both client requests and server responses use the same encoding mechanism
- Protocol differentiation is automatic based on packet structure (see Protocol Details below)


# Protocol Details

## Packet Structure and Differentiation

Packets on the single socket use one of two formats, differentiated by structure:

**Control/Request Packets (Basic Format):**
```
~XXXX|<JSON_DATA>
```
- Prefix: `~XXXX|` where `XXXX` is a 4-digit **decimal** number (zero-padded)
- First byte after `|` is always `{` (JSON object start)
- Content: JSON with `action`, `data`, and `error` fields
- Direction: Client requests and server responses (request-response pairs)
- Example: `~0045|{"action":"subscribe","data":["CLOB_ID_1"]}`

**Market Data Packets (Protocol 2 Format):**
```
~XXXXYYYY|<SYMBOL><DATA>L
```
- Prefix: `~XXXXYYYY|` where:
  - `XXXX` = 4-digit decimal total packet length (after the `~XXXX`)
  - `YYYY` = 4-digit decimal symbol length
  - Packet terminates with `L` byte
- Content: Symbol followed by the dispatcher's P2 format layout (see docs)
- Direction: Server → Client (asynchronous streaming after subscription)
- Example: `~0065<0006|BTCUSD50000.0,50001.0,50000.5,1.0,1.0,1750729519.286L`

**Auto-Detection:** Check the first byte after `|`:
- `{` → Basic format (control message)
- Other and ends with `L` → Protocol 2 format (market data)

# Message Structure (Control Plane)

All inbound messages must be JSON encoded with the following structure:
```json
{
  "action": "<ACTION_NAME>",
  "data": { ... } // request data can be of type Any or null depending on the action
}
```

All outbound messages to client requests will be JSON encoded with the following structure:
```json
{
  "action": "<ACTION_NAME>", // echoed from the request,
  "data": { ... } // response data can be of type Any or null
  "error": "<ERROR_MESSAGE>" // optional, only present if an error occurred either STR or NULL,
}
```

Some important notes about the control plane messages:
The action "notification" is reserved for server-initiated messages. Clients should not send messages with this action. It is used by
the server to update clients of any important events, such as dispatcher fatal errors or other critical notifications.

# Market Data Subscription Management (Control Plane)
### Subscribing to Market Data
To subscribe to market data for a specific token/clob_id, send a JSON message in the following format:

```json

{
  "action": "subscribe",
  "data": ["<CLOB_ID_1>", "<CLOB_ID_2>", "..."]
}
```

The dispatcher will partially fulfil the subscription request if some of the requested clob_ids are invalid.
The response will be in the following format:
```json
{
  "action": "subscribe",
  "data": {
    "subscribed": ["<CLOB_ID_1>", "<CLOB_ID_2>", "..."], 
    "failed": ["<CLOB_ID_3>", "..."] 
  },
  "error": null
}
```

#### Important Notes
* If the token is expired or invalid, it will be included in the "failed" list.
* Subscriptions are tied to the number of connected clients. If all clients disconnect, the subscriptions will be removed.
* If the token/clob_id expires while subscribed, a notification message will be sent to the client with the following format:

```json
{
  "action": "notification",
  "data": {
    "type": "token_expired",
    "clob_id": "<CLOB_ID>"
  },
  "error": null 
}
```

### Unsubscribing from Market Data
To unsubscribe from market data for a specific token/clob_id, send a JSON message in the following format: 
```json
{
  "action": "unsubscribe",
  "data": ["<CLOB_ID_1>", "<CLOB_ID_2>", "..."]
}
```

# Connection Model

The Polymarket Dispatcher uses a **single persistent socket connection** for all communication:

1. **Establish Connection**: Connect to `localhost:9972` (or configured host/port)
2. **Send Requests**: Send JSON control messages on the same socket
3. **Receive Responses**: Responses to your requests arrive on the same socket
4. **Receive Market Data**: Market data updates arrive automatically after subscription on the same socket
5. **Multiplex Messages**: Your client must be able to distinguish between control responses and market data updates based on packet structure

This unified approach simplifies connection management and eliminates the complexity of coordinating multiple ports or sockets.

# Fetching Market Metadata (Control Plane)
The dispatcher maintains a cache of ALL available markets on polymarket. This comes out to ~6000ish markets as of 2026.
This list is updated automatically every five mins to add new markets and remove expired ones. In 99% of cases you will
get a cache hit because markets in Polymarket are often available on the API ahead of time (by sometimes even 51 hours),
especially for markets that continuously roll over (e.g., hourly/15min markets). The dispatcher has the following
actions to fetch market metadata:

### Fetch All Markets
To fetch metadata for all available markets, send a JSON message in the following format:
```json
{
    "action": "fetch_all_markets",
    "data": null
}
```

The response will be in the following format:
```json
{
  "action": "fetch_all_markets",
  "data": [...], // array of dicts representing markets derived from `PolymarketEvent`
  "error": null
}
```

### Fetch All Market Tickers

To fetch only the ticker symbols for all available markets (lightweight alternative to `fetch_all_markets`), send:

```json
{
    "action": "fetch_all_tickers",
    "data": null
}
```

The response will be in the following format:
```json
{
  "action": "fetch_all_tickers",
  "data": ["TICKER_1", "TICKER_2", "..."], // array of ticker strings
  "error": null
}
```

This endpoint is useful when you only need ticker information without the full market metadata, reducing bandwidth consumption.

### Fetch Market by Ticker
To fetch metadata for a specific market by its ticker symbol, send a JSON message in the following format:
```json
{
    "action": "fetch_market_by_ticker",
    "data": "<TICKER>"
}
```

The response will be in the following format:
```json
{
  "action": "fetch_market_by_ticker",
  "data": { ... }, // dict representing market derived from `PolymarketEvent`
  "error": null
}
```

### Search markets by query
To search a market by a query string, send a JSON message in the following format:
```json
{
    "action": "search_markets",
    "data": {
        "query": "<SEARCH_QUERY>",
        "limit": <MAX_RESULTS>
    }
}
```

The response will be in the following format:
```json
{
  "action": "search_markets",
  "data": [...], // array of sorted dicts representing markets derived from `PolymarketEvent`
  "error": null
}
```

Note: The search is case-insensitive and matches against market titles and symbols. Sorted by levenshtein distance.

# Order Execution & Account Data (Control Plane)

The following order execution actions are supported by the dispatcher:
### Place Order
To place an order, send a JSON message in the following format:
```json
{
    "action": "place_order",
    "data": {
        "clob_id": "<CLOB_ID>",
        "side": "<buy|sell>",
        "price": <PRICE>,
        "size": <SIZE>,
        "order_type": "<GTC|FOK|GTD|FAK>", // optional, defaults to GTC
    }
}
```

The response will be in the following format:
```json
{
  "action": "place_order",
  "data": {
    "order_id": "<ORDER_ID>",
    "taking_amount": "<TAKING_AMOUNT>",
    "making_amount": "<MAKING_AMOUNT>",
    "status": "<live|filled|cancelled|rejected>",
    "success": true
  },
  "error": null // or error message if an error occurred
}
```

Note: This action relies on the function `place_order` which is part of the rest/PoltRestAPI and has the requirement
of `market: pm_types.PolymarketEvent` this is automatically filled from the aformentioned market cache. Make sure the
token id will exist in the cache before placing an order using `fetch_market_by_clob_id` or `search_markets` if the token_id
was sourced externally.


### Cancel Order
To cancel an order, send a JSON message in the following format:
```json
{
    "action": "cancel_order",
    "data": "<ORDER_ID>"
}
```

The response will be in the following format:
```json
{
  "action": "cancel_order",
  "data": "<CANCELLED | NOT CANCELLED>", 
  "error": null 
}
```

Note: Canceled or NOT canceled is not because of an error and simply what the API returned.

### Get Order Status
To get the status of an order, send a JSON message in the following format:
```json
{
    "action": "get_order_status",
    "data": "<ORDER_ID>"
}
```

The response will be in the following example format:
```json
{
  "action": "get_order_status",
  "data": {
    "id": "0x1a2b3c4d5e6f7g8h9i0j",
    "status": "OPEN",
    "owner": "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb",
    "maker_address": "0x9A8f92a830A5cB89a3816e3D267CB7791c16b04D",
    "market": "0xabcdef1234567890",
    "asset_id": "21742633143463906290569050155826241533067272736897614950488156847949938836455",
    "side": "BUY",
    "original_size": "100.5",
    "size_matched": "45.25",
    "price": "0.65",
    "outcome": "Yes",
    "expiration": "0",
    "order_type": "GTC",
    "associate_trades": [],
    "created_at": 1706050800
},
  "error": null 
}
```

### Get all Order Statuses
To get the status of all orders for the authenticated user, send a JSON message in the following
format:
```json
{
    "action": "get_orders",
    "data": null
}
```

The response will be in the following example format:
```json
{
  "action": "get_orders",
  "data": [
    {
      "id": "0x1a2b3c4d5e6f7g8h9i0j",
      "status": "OPEN",
      ...
    },
    ...
  ],
  "error": null 
}
```

### Get Balance
Get the balance of the authenticated user by sending a JSON message in the following format:
```json
{
    "action": "get_balance",
    "data": null
}
```

The response will be in the following format:
```json
{
  "action": "get_balance",
  "data": <BALANCE_AMOUNT>,
  "error": null 
}
```

Note: This represents the total cash available for trading or `COLLATERAL` per the clob API.

# Client Implementation Guide

## Handling the Single Socket Connection

Since the dispatcher uses a heterogeneous data stream on a single socket, your client must:

1. **Maintain a single persistent connection** to the dispatcher
2. **Parse incoming packets** to distinguish between two types:
   - **Control responses**: Basic format `~XXXX|{...}` (first byte after `|` is `{`)
   - **Market data**: Protocol 2 format `~XXXX<YYYY>|...|L` (first byte after `|` is symbol, ends with `L`)
3. **Send control messages** (basic format) while receiving both control responses and market data asynchronously
4. **Handle interleaved message types** - control responses and market data arrive on the same socket and may be interleaved

## Packet Detection Logic

```python
def detect_packet_type(packet_bytes):
    # Find the pipe separator
    pipe_idx = packet_bytes.find(b'|')
    if pipe_idx == -1:
        raise ValueError("Invalid packet: no pipe found")

    first_byte_after_pipe = packet_bytes[pipe_idx + 1]

    if first_byte_after_pipe == ord('{'):
        return "control"  # Basic format: ~XXXX|{JSON}
    elif packet_bytes[-1] == ord('L'):
        return "market_data"  # Protocol 2: ~XXXX<YYYY>|SYMBOL...|L
    else:
        raise ValueError("Unknown packet format")
```

## Pseudo-Code Example

```python
import socket
import json
import threading

def listen_loop(sock):
    while True:
        # Read header to determine packet length
        header = sock.recv(6)  # ~XXXX|
        if not header:
            break

        packet_type = detect_packet_type(header)
        length = int(header[1:5])

        if packet_type == "control":
            # Basic format: read JSON
            json_data = sock.recv(length)
            msg = json.loads(json_data)
            handle_control_response(msg)
        elif packet_type == "market_data":
            # Protocol 2: read symbol_length, then symbol, then data
            # Handle market data update
            pass

# Connect and send request
sock = socket.socket()
sock.connect(('localhost', 9972))

# Send control request (basic format)
request = {"action": "subscribe", "data": ["CLOB_ID_1"]}
encoded = encode_packet(json.dumps(request).encode())
sock.sendall(encoded)

# Listen in background thread
thread = threading.Thread(target=listen_loop, args=(sock,))
thread.daemon = True
thread.start()

# Now receive both control responses and market data on the same connection
```

The key advantage is **no connection management overhead** — single socket handles request-response pairs AND asynchronous market data streaming simultaneously.

# Special Actions (Control Plane)
Normally the action field corresponds directly to a specific action. However, there are some special actions that
are sent by the server to notify clients of important events. These are the following special actions:

### notification
These are general notifications from the server. This includes events such as but is not limited to:
* Token expiration
* Dispatcher internal errors (non-fatal)
* System maintenance notifications (i.e., refreshing market cache)
* WebSocket connection issues (generally the dispatcher auto-reconnects with full state restoration)
* Pings (you do not need to respond to pings, they are just to keep the connection alive)

### fatal_error
These are CRITICAL ERRORS that happened inside the `PolyRestAPI` and was result of the `fatal_callback`.
These should be considered as CRITICAL errors that require immediate attention. It maybe moments before the dispatcher
completely crashes. 

### account_update
These are messages sent when an account update has occurred. This includes events such as:
* Order status changes (filled, canceled, etc.)
* Orders placed


# Market Data (Single Socket)

Real-time market data updates for subscribed markets are sent to clients through the same socket connection in **Protocol 2 format** (see Protocol Details above).

**Market Data Format:**
```
~XXXX<YYYY>|<SYMBOL><DATA>L
```
- Identified by: starts with `~XXXX<YYYY>|` and ends with `L`
- Symbol: Asset identifier (e.g., `CLOB_ID_1`)
- Data: CSV-formatted values (prices, sizes, timestamps)

**Market Data Message Types:**
* Top-of-Book Updates: Best bid/ask prices and sizes
* Full Order Book Snapshots: Complete order book state

**Note:** Market data does not use delta updates. All updates are sent as complete snapshots.

**Receiving Market Data:**

After subscribing via `subscribe` action, market data packets stream asynchronously on the same connection. Your client must parse both:
1. **Control responses** (Basic format `~XXXX|{...}`) — responses to your requests
2. **Market data** (Protocol 2 format `~XXXX<YYYY>|...|L`) — asynchronous updates

These arrive interleaved on the same socket and must be handled with proper packet detection (see Client Implementation Guide above).

---

# Summary

The Polymarket Dispatcher provides a **simplified, unified interface** through a single heterogeneous socket:

- **Single Port**: All communication on port 9972 (no dual-port complexity)
- **Heterogeneous Packets**: Control uses Basic format (`~XXXX|{JSON}`), market data uses Protocol 2 (`~XXXX<YYYY>|SYMBOL...L`)
- **Auto-Detection**: Clients distinguish packet types by checking first byte after `|` and looking for `L` terminator
- **Simplified Connection Management**: One persistent socket handles request-response pairs AND market data streaming
- **Stateful Subscriptions**: Subscriptions are tied to connection lifetime; client disconnect triggers cleanup
- **Full Market Cache**: ~6000 active markets cached and refreshed automatically every 5 minutes
- **Async-Ready**: Market data streams asynchronously while you send control requests on the same connection

For questions or issues, refer to the implementation in `argus/polymarket/__init__.py` or the companion `polymarket_direct` module for REST/WebSocket details.

