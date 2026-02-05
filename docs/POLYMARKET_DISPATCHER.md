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

**Control/Request Packets (P1 Format):**
```
~NNNN|<JSON_DATA>
```
- Prefix: `~NNNN|` where `NNNN` is a **variable-width** decimal number (zero-padded to a minimum of 4 digits).
  For payloads <= 9999 bytes the field is exactly 4 digits (e.g. `~0045|`). For larger payloads the field
  grows to however many digits are needed (e.g. `~12345|`). **Clients must locate the `|` dynamically**
  rather than assuming it is always at byte offset 5. See [#68](https://github.com/The-Sal/Argus/issues/68).
- First byte after `|` is always `{` (JSON object start)
- Content: JSON with `action`, `data`, and `error` fields
- Direction: Client requests and server responses (request-response pairs)
- Example: `~0045|{"action":"subscribe","data":["CLOB_ID_1"]}`
- **Important:** Clients must also wrap their outbound JSON requests in P1 encoding before sending.
  Raw JSON sent without the `~NNNN|` framing will not be understood by the dispatcher.

**Market Data Packets (P2 Format):**
```
~NNNNYYYY|<SYMBOL><DATA>L
```
- Prefix: `~NNNNYYYY|` where:
  - `NNNN` = 4-digit decimal total packet length (everything after the `~NNNN` prefix)
  - `YYYY` = 4-digit decimal symbol length
  - `|` separator immediately after `YYYY`
  - Packet terminates with `L` byte
- Content: Symbol followed by CSV market data (see P2 Data Layout below)
- Direction: Server -> Client (asynchronous streaming after subscription)
- Example: `~00650006|BTCUSD50000.0,50001.0,50000.5,1.0,1.0,1750729519.286L`

**Auto-Detection:** Check the first byte after `|`:
- `{` -> P1 (control message)
- Other and ends with `L` -> P2 (market data)

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

The response will be in the following format:
```json
{
  "action": "unsubscribe",
  "data": {
    "unsubscribed": ["<CLOB_ID_1>", "<CLOB_ID_2>", "..."],
    "failed": ["<CLOB_ID_3>", "..."]
  },
  "error": null
}
```

# Connection Model

The Polymarket Dispatcher uses a **single persistent socket connection** for all communication:

1. **Establish Connection**: Connect to `localhost:9972` (or configured host/port)
2. **Send Requests**: Send P1-encoded JSON control messages on the socket (see Protocol Details for framing)
3. **Receive Responses**: Responses to your requests arrive on the same socket as P1 packets
4. **Receive Market Data**: Market data updates arrive automatically after subscription as P2 packets
5. **Multiplex Messages**: Your client must be able to distinguish between P1 (control) and P2 (market data) packets based on structure

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
    "data": ["<TICKER>"]
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
    "data": ["<SEARCH_QUERY>", <MAX_RESULTS>]
}
```

The `data` field is a list where:
- Index 0 (required): The search query string
- Index 1 (optional): Maximum number of results to return (default: 10)

The response will be in the following format:
```json
{
  "action": "search_markets",
  "data": ["TICKER_1", "TICKER_2", "..."], // array of ticker strings sorted by similarity
  "error": null
}
```

Note: The search matches against market ticker strings. Results are sorted by `difflib.SequenceMatcher` similarity ratio (longest common subsequence based), not Levenshtein distance.

# Order Execution & Account Data (Control Plane)

The following order execution actions are supported by the dispatcher:
### Place Order
To place an order, send a JSON message in the following format:
```json
{
    "action": "place_order",
    "data": {
        "token_id": "<TOKEN_ID>",
        "side": "<buy|sell>",
        "price": <PRICE>,
        "size": <SIZE>
    }
}
```

The response will be in the following format (note: field names use camelCase as returned by the CLOB API):
```json
{
  "action": "place_order",
  "data": {
    "errorMsg": "",
    "orderID": "<ORDER_ID>",
    "takingAmount": "<TAKING_AMOUNT>",
    "makingAmount": "<MAKING_AMOUNT>",
    "status": "<live|filled|cancelled|rejected>",
    "success": true
  },
  "error": null // or error message if an error occurred
}
```

Note: This action relies on the function `place_order` which is part of the rest/PolyRestAPI and has the requirement
of `market: pm_types.PolymarketEvent` this is automatically filled from the aforementioned market cache. Make sure the
token id will exist in the cache before placing an order using `search_markets` or `fetch_all_tickers` if the token_id
was sourced externally.


### Cancel Order
To cancel an order, send a JSON message in the following format:
```json
{
    "action": "cancel_order",
    "data": {
        "order_id": "<ORDER_ID>"
    }
}
```

The response will be in the following format:
```json
{
  "action": "cancel_order",
  "data": {
    "canceled": ["<ORDER_ID>"],
    "not_canceled": {}
  },
  "error": null 
}
```

Note: An order appearing in `not_canceled` rather than `canceled` is not an error condition; it reflects
what the CLOB API returned (e.g. the order may have already been filled or canceled).

### Get Order Status
To get the status of an order, send a JSON message in the following format:
```json
{
    "action": "get_order_status",
    "data": {
        "order_id": "<ORDER_ID>"
    }
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

# Utilities (Control Plane)

### Ping
To check if the dispatcher is alive and responsive, send:
```json
{
    "action": "ping",
    "data": null
}
```

The response will be in the following format:
```json
{
  "action": "ping",
  "data": "pong",
  "error": null
}
```

# Client Implementation Guide

## Handling the Single Socket Connection

Since the dispatcher uses a heterogeneous data stream on a single socket, your client must:

1. **Maintain a single persistent connection** to the dispatcher
2. **Parse incoming packets** to distinguish between two types:
   - **Control responses**: P1 format `~NNNN|{...}` (first byte after `|` is `{`)
   - **Market data**: P2 format `~NNNNYYYY|...L` (first byte after `|` is part of the symbol, ends with `L`)
3. **Send control messages** (P1 format) while receiving both control responses and market data asynchronously
4. **Handle interleaved message types** - control responses and market data arrive on the same socket and may be interleaved

## Packet Detection Logic

P2 packets always have a 4-digit packet length at bytes 1-4, a 4-digit symbol length at bytes 5-8,
and a pipe at byte 9. P1 packets have a variable-width length field (minimum 4 digits) followed
immediately by `|`. To distinguish them, find the pipe position: if `|` is at byte 9 and the
packet ends with `L`, it is P2; otherwise it is P1.

```python
def detect_and_parse_packet(raw_bytes):
    """Detect whether a complete packet starting at raw_bytes[0] is P1 or P2."""
    pipe_idx = raw_bytes.find(b'|')
    if pipe_idx == -1:
        raise ValueError("Invalid packet: no pipe found")

    # P2: pipe is always at index 9 (~NNNNYYYY|), ends with L
    if pipe_idx == 9:
        pkt_len = int(raw_bytes[1:5].decode('ascii'))
        total = 5 + pkt_len
        if raw_bytes[total - 1:total] == b'L':
            return "market_data", total

    # P1: pipe is right after the variable-width length field
    payload_len = int(raw_bytes[1:pipe_idx].decode('ascii'))
    total = pipe_idx + 1 + payload_len
    return "control", total
```

## Pseudo-Code Example

```python
import socket
import json
import threading

def encode_packet(data: bytes) -> bytes:
    """Wrap data in P1 framing: ~NNNN|<data>"""
    return f"~{len(data):04d}|".encode('ascii') + data

def listen_loop(sock):
    buf = b''
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf += chunk

        while buf and buf[0:1] == b'~':
            pipe_idx = buf.find(b'|')
            if pipe_idx == -1:
                break  # incomplete header

            pkt_type, total_len = detect_and_parse_packet(buf)
            if len(buf) < total_len:
                break  # incomplete packet

            packet = buf[:total_len]
            buf = buf[total_len:]

            if pkt_type == "control":
                payload = packet[pipe_idx + 1:]
                msg = json.loads(payload.decode('utf-8'))
                handle_control_response(msg)
            else:
                # P2 market data — parse symbol and CSV fields
                handle_market_data(packet)

# Connect and send request
sock = socket.socket()
sock.connect(('localhost', 9972))

# Send control request (must be P1-encoded)
request = {"action": "subscribe", "data": ["CLOB_ID_1"]}
encoded = encode_packet(json.dumps(request).encode('utf-8'))
sock.sendall(encoded)

# Listen in background thread
thread = threading.Thread(target=listen_loop, args=(sock,))
thread.daemon = True
thread.start()

# Now receive both control responses and market data on the same connection
```

The key advantage is **no connection management overhead** -- single socket handles request-response pairs AND asynchronous market data streaming simultaneously.

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

**IMPORTANT — Account Update Delivery Requirement:**
Real-time account events (order PLACEMENT, CANCELLATION, MATCH, etc.) are only broadcast to client
sockets that have an active market data subscription. A client that connects and issues order management
commands (`place_order`, `cancel_order`, `get_order_status`, etc.) **without** first subscribing to at
least one asset via the `subscribe` action will **never** receive `account_update` pushes, even though
the dispatcher's internal WebSocket is receiving them from the CLOB.

This is because the dispatcher tracks connected clients through the routing table's socket set, which is
only populated when a client subscribes to market data. If your workflow depends on receiving account
lifecycle events, you **must** subscribe to at least one asset_id before placing orders.


# Market Data (Single Socket)

Real-time market data updates for subscribed markets are sent to clients through the same socket connection in **P2 format** (see Protocol Details above).

**Market Data Format:**
```
~NNNNYYYY|<SYMBOL><DATA>L
```
- Identified by: first byte after `|` is NOT `{`, and packet ends with `L`
- Symbol: A concatenation of `<event_ticker><market_slug><asset_id>` (e.g., `bitcoin-up-or-down-1hrbtc-hourly-up-or-down-jan-31-2026-2pm-et21742633143463...`)
- Data: CSV-formatted order book values (see layout below)

**P2 Symbol Structure:**

The P2 symbol is NOT just the clob_id/asset_id. It is constructed as:
```
<event_ticker> + <market_slug> + <asset_id>
```
For example, if the event ticker is `bitcoin-up-or-down-1hr`, the market slug is
`btc-hourly-up-or-down-jan-31-2026-2pm-et`, and the asset_id is `217426331434639...`,
the symbol will be the concatenation of all three.

**P2 Data Layout:**

After the symbol, the CSV data contains the following fields in order:

| Fields | Count | Description |
|--------|-------|-------------|
| `bid_price_N, bid_size_N` | N pairs | Bid levels (best to worst) |
| `ask_price_N, ask_size_N` | N pairs | Ask levels (best to worst) |
| `exchange_timestamp` | 1 | Timestamp from the Polymarket WebSocket |
| `server_timestamp` | 1 | Timestamp when the dispatcher encoded the packet |

Where N = `POLYMARKET_ORDERBOOK_DEPTH` (default 10, configurable via environment variable).
At default depth this yields `10*2 + 10*2 + 2 = 42` comma-separated float values per packet.
If the order book has fewer than N levels on a side, the missing levels are padded with `0,0`.

**Note:** Market data does not use delta updates. All updates are sent as complete snapshots.

**Receiving Market Data:**

After subscribing via `subscribe` action, market data packets stream asynchronously on the same connection. Your client must parse both:
1. **Control responses** (P1 format `~NNNN|{...}`) -- responses to your requests
2. **Market data** (P2 format `~NNNNYYYY|...L`) -- asynchronous updates

These arrive interleaved on the same socket and must be handled with proper packet detection (see Client Implementation Guide above).

---

# Summary

The Polymarket Dispatcher provides a **simplified, unified interface** through a single heterogeneous socket:

- **Single Port**: All communication on port 9972 (no dual-port complexity)
- **Heterogeneous Packets**: Control uses P1 format (`~NNNN|{JSON}`), market data uses P2 (`~NNNNYYYY|SYMBOL...L`)
- **Auto-Detection**: Clients distinguish packet types by pipe position (byte 9 = P2, otherwise P1) and `L` terminator
- **Simplified Connection Management**: One persistent socket handles request-response pairs AND market data streaming
- **Stateful Subscriptions**: Subscriptions are tied to connection lifetime; client disconnect triggers cleanup
- **Subscription Required for Account Updates**: Clients must subscribe to at least one asset before placing orders if they need real-time account_update pushes
- **Full Market Cache**: ~6000 active markets cached and refreshed automatically every 5 minutes
- **Async-Ready**: Market data streams asynchronously while you send control requests on the same connection

For questions or issues, refer to the implementation in `argus/polymarket/__init__.py` or the companion `polymarket_direct` module for REST/WebSocket details.

