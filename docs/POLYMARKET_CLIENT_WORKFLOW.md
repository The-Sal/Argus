# PolyMarket Dispatcher - Client Workflow Guide

## Overview

This guide explains how clients connect to and interact with the PolyMarket Dispatcher to subscribe to prediction market data.

## Architecture

```
┌─────────────┐         ┌──────────────────┐         ┌─────────────────┐
│   Client    │ ◄─────► │ PolyDispatcher   │ ◄─────► │  Polymarket API │
│ Application │   UDS   │ (Unix Socket)    │   WSS   │   + WebSocket   │
└─────────────┘         └──────────────────┘         └─────────────────┘
                              │
                              ▼
                        Protocol 1 (JSON)
                        Protocol 2 (Binary)
```

## Connection Details

- **Socket Type**: Unix Domain Socket (UDS)
- **Default Path**: `/tmp/argus_polymarket.sock`
- **Protocols Supported**:
  - Protocol 1: JSON-based (commands and responses)
  - Protocol 2: Binary-based (market data streaming)

---

## Workflow 1: Subscribe to Specific Asset ID

**Use Case**: You already know the CLOB token ID (asset ID) you want to subscribe to.

### Steps

```
1. Connect to socket
   └─> client.connect()

2. Send subscription command
   └─> {
         "action": "stream_asset",
         "asset_id": "21742633143..."
       }

3. Receive confirmation
   └─> {
         "status": "success",
         "message": "Started streaming asset '21742633143...'"
       }

4. Listen for Protocol 2 data
   └─> Receive real-time price updates
```

### Code Example

```python
from examples.polymarket_client_example import PolymarketClient

client = PolymarketClient()
client.connect()

# Subscribe to asset
asset_id = "21742633143463906290569050155826241533067272736897614950488156847949938836455"
client.stream_asset(asset_id)

# Receive updates
while True:
    data = client.receive_market_data()
    print(f"Price: ${data['price']:.4f}")
```

---

## Workflow 2: Search and Subscribe by Keyword

**Use Case**: You want to find markets related to a topic (e.g., "Bitcoin", "Trump", "Election").

### Steps

```
1. Connect to socket
   └─> client.connect()

2. Send keyword search command
   └─> {
         "action": "stream_market_by_keyword",
         "keyword": "Bitcoin"
       }

3. Dispatcher searches markets
   └─> Fetches events from Polymarket
   └─> Finds first matching market
   └─> Auto-subscribes to all asset IDs in that market

4. Receive confirmation
   └─> {
         "status": "success",
         "message": "Started streaming market: Will Bitcoin reach $100k?"
       }

5. Listen for Protocol 2 data
   └─> Receive real-time updates for all outcomes
```

### Code Example

```python
client = PolymarketClient()
client.connect()

# Search and subscribe
client.stream_market_by_keyword("Bitcoin")

# Receive updates
for i in range(10):
    data = client.receive_market_data()
    print(f"Probability: {data['price']*100:.2f}%")
```

---

## Workflow 3: Browse Events, Then Subscribe

**Use Case**: You want to see what's available before subscribing.

### Steps

```
1. Connect to socket
   └─> client.connect()

2. Fetch available events
   └─> {
         "action": "fetch_events",
         "offset": 0,
         "limit": 20
       }

3. Receive event list
   └─> {
         "status": "success",
         "data": [
           {
             "id": "67413",
             "title": "2024 Presidential Election",
             "markets": 5
           },
           ...
         ]
       }

4. (Client parses data to find asset IDs)
   └─> Note: Full event data with clobTokenIds is returned
   └─> Extract asset IDs from market data

5. Subscribe to selected asset
   └─> {
         "action": "stream_asset",
         "asset_id": "extracted_asset_id"
       }

6. Listen for Protocol 2 data
```

### Code Example

```python
client = PolymarketClient()
client.connect()

# Browse events
events = client.fetch_events(limit=10)
for event in events:
    print(f"{event['title']} - {event['markets']} markets")

# After selecting an event, subscribe by keyword or asset ID
client.stream_market_by_keyword("Trump")
```

---

## Workflow 4: Multi-Asset Monitoring

**Use Case**: Monitor multiple markets simultaneously.

### Steps

```
1. Connect to socket
   └─> client.connect()

2. Subscribe to multiple assets
   └─> client.stream_market_by_keyword("Bitcoin")
   └─> client.stream_market_by_keyword("Trump")
   └─> client.stream_market_by_keyword("Ethereum")

3. Dispatcher subscribes to all assets
   └─> Each market may have multiple asset IDs (Yes/No outcomes)

4. Listen for Protocol 2 data
   └─> Receive interleaved updates from all subscribed assets
   └─> data['asset_id'] identifies which asset the update is for
```

### Code Example

```python
client = PolymarketClient()
client.connect()

# Subscribe to multiple markets
keywords = ["Bitcoin", "Trump", "Ethereum"]
for keyword in keywords:
    client.stream_market_by_keyword(keyword)

# Monitor all
while True:
    data = client.receive_market_data()
    print(f"{data['asset_id'][:20]}... | Price: ${data['price']:.4f}")
```

---

## Protocol Details

### Protocol 1: JSON Commands (Client → Dispatcher)

**Packet Format:**
```
~<data-length>|{json_data}
```

**Available Commands:**

#### 1. Fetch Events
```json
{
  "action": "fetch_events",
  "offset": 0,
  "limit": 20
}
```

**Response:**
```json
{
  "status": "success",
  "data": [
    {
      "id": "67413",
      "title": "Event Title",
      "markets": 3
    }
  ]
}
```

#### 2. Stream Asset
```json
{
  "action": "stream_asset",
  "asset_id": "21742633143463906290569050155826241533067272736897614950488156847949938836455"
}
```

**Response:**
```json
{
  "status": "success",
  "message": "Started streaming asset '21742633143...'"
}
```

#### 3. Stream Market by Keyword
```json
{
  "action": "stream_market_by_keyword",
  "keyword": "Bitcoin"
}
```

**Response (Success):**
```json
{
  "status": "success",
  "message": "Started streaming market: Will Bitcoin reach $100k?"
}
```

**Response (Not Found):**
```json
{
  "status": "error",
  "message": "No market found for keyword 'Bitcoin'"
}
```

#### 4. Unsubscribe from Asset
```json
{
  "action": "unsubscribe_asset",
  "asset_id": "21742633143463906290569050155826241533067272736897614950488156847949938836455"
}
```

**Response:**
```json
{
  "status": "success",
  "message": "Unsubscribed from asset '21742633143...'"
}
```

---

### Protocol 2: Binary Market Data (Dispatcher → Client)

**Packet Format:**
```
~<data-length><symbol-length>|{asset_id}{csv_data}L
```

**Fields (CSV format):**
1. `best_bid` - Best bid price
2. `liquidity` - Market liquidity
3. `best_ask` - Best ask price
4. `volume` - Total volume
5. `price` - Current price (last trade or mid)
6. `price_change` - 24h price change
7. `timestamp` - Unix timestamp (milliseconds)
8. `python_timestamp` - Server timestamp

**Example Parsed Data:**
```python
{
  'asset_id': '21742633143463906290569050155826241533067272736897614950488156847949938836455',
  'best_bid': 0.52,
  'liquidity': 15000.0,
  'best_ask': 0.54,
  'volume': 125000.0,
  'price': 0.53,
  'price_change': 0.05,  # +5%
  'timestamp': 1750217540000,
  'python_timestamp': 1750217540.123
}
```

---

## Quick Reference

### How to Get Asset IDs

Asset IDs (CLOB token IDs) can be obtained in three ways:

#### Method 1: Search by Keyword (Easiest)
```python
client.stream_market_by_keyword("Bitcoin")
# Dispatcher finds market and subscribes automatically
```

#### Method 2: Browse Events
```python
events = client.fetch_events(limit=50)
# Parse events to find markets
# Extract clobTokenIds from market data
```

#### Method 3: Direct from Polymarket API
```python
# Use polymarket_direct module directly
from argus.polymarket_direct import EnhancedPM

pm = EnhancedPM(None, None, dry_mode=True)
events = pm.fetch_events(limit=20)

for event in events:
    for market in event.markets:
        print(f"Market: {market.question}")
        print(f"Asset IDs: {market.clobTokenIds}")
```

### Error Handling

```python
try:
    response = client.stream_asset(asset_id)
    if response['status'] != 'success':
        print(f"Error: {response['message']}")
except socket.error:
    print("Connection lost - dispatcher may be down")
except socket.timeout:
    print("No data received - check subscription")
```

### Timeouts

```python
# Set timeout for receiving data
data = client.receive_market_data(timeout=30.0)

# None = blocking (wait forever)
data = client.receive_market_data(timeout=None)

# 0 = non-blocking (return immediately)
data = client.receive_market_data(timeout=0.0)
```

---

## Complete Example

Here's a full end-to-end example:

```python
from examples.polymarket_client_example import PolymarketClient
import time

def monitor_bitcoin_market():
    """Monitor Bitcoin prediction markets in real-time."""
    client = PolymarketClient()

    try:
        # Step 1: Connect
        client.connect()

        # Step 2: Search and subscribe
        response = client.stream_market_by_keyword("Bitcoin")

        if response['status'] != 'success':
            print(f"Failed to subscribe: {response.get('message')}")
            return

        # Step 3: Monitor in real-time
        print("\n📊 Monitoring Bitcoin market (Ctrl+C to stop)...")
        print("-" * 60)

        while True:
            data = client.receive_market_data(timeout=30.0)

            if data:
                # Calculate probability from price
                probability = data['price'] * 100

                # Format timestamp
                ts = time.strftime('%Y-%m-%d %H:%M:%S',
                                   time.localtime(data['timestamp']/1000))

                # Display update
                print(f"[{ts}] Price: ${data['price']:.4f} | "
                      f"Probability: {probability:.2f}% | "
                      f"Volume: ${data['volume']:,.0f}")

                # Alert on significant price changes
                if abs(data['price_change']) > 0.05:  # >5% change
                    print(f"  ⚠️  Significant move: {data['price_change']:+.2%}")

    except KeyboardInterrupt:
        print("\n\n⏹  Stopped by user")
    except Exception as e:
        print(f"\n⚠️  Error: {e}")
    finally:
        client.disconnect()

if __name__ == '__main__':
    monitor_bitcoin_market()
```

---

## Summary

### Client Workflow at a Glance

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Connect to Unix Socket                                    │
│    client.connect()                                           │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. Choose Subscription Method:                               │
│                                                               │
│    A) By Asset ID                                             │
│       └─> client.stream_asset(asset_id)                       │
│                                                               │
│    B) By Keyword (Recommended)                                │
│       └─> client.stream_market_by_keyword("Bitcoin")          │
│                                                               │
│    C) Browse First                                            │
│       └─> events = client.fetch_events()                      │
│       └─> (select and subscribe)                              │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. Receive Real-Time Updates                                 │
│    while True:                                                │
│        data = client.receive_market_data()                    │
│        print(data['price'])                                   │
└─────────────────────────────────────────────────────────────┘
```

### Key Points

1. **Easiest Method**: Use `stream_market_by_keyword()` - no need to know asset IDs
2. **Protocol 1** (JSON) for commands and responses
3. **Protocol 2** (Binary) for efficient market data streaming
4. **Multiple Assets**: Subscribe to as many as you want, updates are interleaved
5. **Asset ID**: Identifies which market/outcome the data belongs to
6. **Price = Probability**: In prediction markets, price represents probability (0.0-1.0)

---

## Troubleshooting

### Dispatcher Not Running
```
Error: Connection refused
Solution: Start dispatcher in another terminal:
  python -m argus.polymarket
```

### No Data Received
```
Error: Timeout after 30 seconds
Possible causes:
  1. Market has no active trading
  2. Subscription failed (check response status)
  3. Invalid asset ID
  4. WebSocket connection lost (check dispatcher logs)
```

### Invalid Asset ID
```
Error: No callback found for asset_id
Solution:
  1. Use stream_market_by_keyword() instead
  2. Verify asset ID is correct
  3. Check that market is active
```

---

## Next Steps

1. **Try the Examples**: Run `python examples/polymarket_client_example.py`
2. **Read the Code**: Check the example client implementation
3. **Build Your Client**: Use the `PolymarketClient` class as a reference
4. **Integrate**: Add prediction market data to your application

For more information, see:
- `docs/POLYMARKET.md` - Full module documentation
- `argus/polymarket/__init__.py` - Dispatcher implementation
- `examples/polymarket_client_example.py` - Working client examples
