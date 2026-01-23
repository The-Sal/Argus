The following specification covers the entire Polymarket Dispatcher API surface.

---

# Overview
There are two ports that expose different parts of the API:
- **Port 9972**: Market Data 
- **Port 9973**: Control Plane

The market data port only sends out data. It does not accept any incoming data. Everything from the market data port is
encoded with Protocol 2. To interact with the market data port, you will need to send messages via the control plane port.


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

# Fetching Market Metadata (Control Plane)
The dispatcher maintains a cache of ALL available markets on polymarket. This comes out to ~6000ish markets as of 2026.
This list is updated automatically every five mins to add new markets and remove expired ones. In 99% of cases you will 
get a cache hit because markets in Polymarket are often available on the API ahead of time (by sometimes even 51 hours),
especially for markets that continuously roll over (e.g., hourly/15min markets). The dispatcher allows has the following
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

### Fetch Market by CLOB ID
To fetch metadata for a specific market by its clob_id, send a JSON message in the following format:
```json
{
    "action": "fetch_market_by_clob_id",
    "data": "<CLOB_ID>"
}
```

The response will be in the following format:
```json
{
  "action": "fetch_market_by_clob_id",
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

# Special Actions (Control Plane)
Normally the action field corresponds directly to a specific action. However, there are some special actions that
are sent by the server to notify clients of important events. These are the following special actions:

### Notification
These are general notifications from the server. This includes events such as but is not limited to:
* Token expiration
* Dispatcher internal errors (non-fatal)
* System maintenance notifications (i.e., refreshing market cache)
* WebSocket connection issues (generally the dispatcher auto-reconnects with full state restoration)
* Pings (you do not need to respond to pings, they are just to keep the connection alive)

### Fatal-Error
These are CRITICAL ERRORS that happened inside the `PolyRestAPI` and was result of the `fatal_callback`.
These should be considered as CRITICAL errors that require immediate attention. It maybe moments before the dispatcher
completely crashes. 

### Acc-Update
These are messages sent when an account update has occurred. This includes events such as:
* Order status changes (filled, canceled, etc.)
* Orders placed


# Market Data (Market Data Port)
The market data port (9972) sends out real-time market data updates for subscribed markets.
All messages are encoded using Protocol 2. There are only 2 types of messages sent out on this port:
* Top-of-Book Updates
* Full Order Book Snapshots

Note: The market data port does not do delta updates. It sends out full updates for the top-of-book and full order book snapshots.

