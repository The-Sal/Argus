# Polymarket Module

The Polymarket module provides access to prediction market data from Polymarket via direct API integration and WebSocket streaming.

## Table of Contents

- [Overview](#overview)
- [Module Status](#module-status)
- [Architecture](#architecture)
- [Components](#components)
- [Data Types](#data-types)
- [Usage Examples](#usage-examples)
- [Migration Notes](#migration-notes)

## Overview

The Polymarket module exists in two forms:

1. **`argus.polymarket`** - Legacy dispatcher-based architecture (stub, see legacy branch)
2. **`argus.polymarket_direct`** - Direct API integration (current implementation)

**Location:** `/argus/polymarket/` and `/argus/polymarket_direct/`

**Primary Files:**
- `polymarket/__init__.py` - Stub pointing to legacy branch
- `polymarket_direct/__init__.py` - EnhancedPM client and API
- `polymarket_direct/_types.py` - Data models (Event, Market, Series, Tag)
- `polymarket_direct/_example.py` - Usage examples

## Module Status

### Current Implementation (polymarket_direct)

**Status:** ✅ Active (non-dispatcher architecture)

**Features:**
- Direct REST API integration
- WebSocket market data subscriptions
- No dispatcher (different paradigm from IB/Capital/Binance)
- Dry mode (no credentials required for read-only)
- Event/Market data models

**Not Supported:**
- Dispatcher pattern
- Protocol 2 streaming
- Multi-client multiplexing
- TCP/UDS server

### Legacy Implementation (polymarket)

**Status:** ⚠️ Stub only

**Message:**
```
THIS IS A STUB IMPLEMENTATION OF POLY DISPATCHER.
IF YOU NEED THE OLD VERSION PLEASE CHECKOUT THE LEGACY BRANCH AT
https://github.com/The-Sal/Argus/tree/legacy/polymarket-dispatcher
```

**Why Deprecated:**
- Official `py_clob_client` library was incomplete
- Multiple conflicting Polymarket APIs
- Markets from `ClobClient.get_markets()` were mostly closed/resolved
- Direct integration provides better data quality and flexibility

## Architecture

### Why Not a Dispatcher?

Unlike IB, Capital.com, and Binance modules, Polymarket **does not follow the dispatcher paradigm**. Instead, it uses a **direct client library** (`EnhancedPM`).

**Reasons:**
1. **Incomplete official client** - `py_clob_client` missing critical functionality
2. **Multiple data sources** - Polymarket has several APIs with different data
3. **Callback-based design** - WebSocket subscriptions use callbacks directly
4. **No normalization needed** - Data is already JSON (no Protocol 2 conversion)
5. **Read-heavy workload** - Most use cases don't require order placement

**Design Pattern:**
```
[Client Code]
     ↓
[EnhancedPM]
     ↓
[Polymarket REST API / WebSocket]
```

vs. traditional dispatcher:
```
[Client Code] → [TCP/UDS] → [Dispatcher] → [WebSocket] → [Data Source]
```

### Dry Mode

The `EnhancedPM` client supports **dry mode** for read-only access:

```python
client = EnhancedPM(
    private_key=None,  # Not required for dry mode
    proxy_funder=None,
    dry_mode=True
)
```

**Dry Mode Features:**
- ✅ Fetch events and markets
- ✅ Subscribe to market data via WebSocket
- ✅ No credentials required
- ❌ Cannot place orders
- ❌ Cannot access user-specific data

## Components

### EnhancedPM

Direct Polymarket integration client.

**Initialization:**
```python
from argus.polymarket_direct import EnhancedPM

# Dry mode (read-only)
client = EnhancedPM(
    private_key=None,
    proxy_funder=None,
    dry_mode=True
)

# Full mode (with trading)
client = EnhancedPM(
    private_key=os.getenv('POLYMARKET_PRIVATE_KEY'),
    proxy_funder=os.getenv('POLYMARKET_PROXY_FUNDER'),
    dry_mode=False
)
```

**Key Methods:**

```python
# Fetch events (markets)
events = client.fetch_events(offset=0, limit=20)

# Start WebSocket connection
client.start_market_ws()
client.market_open_semaphore.acquire()  # Wait for connection

# Subscribe to market data
client.subscribe_to_market_data(
    asset_ids=['asset_id_1', 'asset_id_2'],
    callback=my_callback
)

# Unsubscribe from market data
client.unsubscribe_from_market_data(['asset_id_1'])

# Restart WebSocket (reconnection)
client.restart_ws_connections()
```

### REST API Endpoints

The module uses **reverse-engineered Polymarket API endpoints**:

```python
endpoints = {
    'events': "https://gamma-api.polymarket.com/events?order=id&ascending=false&closed=false&limit={}&offset={}"
}
```

**Why not `py_clob_client`?**
- Official client's `get_markets()` returns mostly closed markets (0.99:1 odds)
- Gamma API provides active, liquid markets
- Better for live trading and data analysis

### WebSocket API

**Endpoint:** `wss://ws-subscriptions-clob.polymarket.com/ws/market`

**Subscription Message:**
```json
{
  "assets_ids": ["asset_id_1", "asset_id_2"],
  "type": "market"
}
```

**Update Message:**
```json
{
  "price_changes": [
    {
      "asset_id": "asset_id_1",
      "price": "0.52",
      "timestamp": 1750217540
    }
  ]
}
```

## Data Types

### PolymarketEvent

Top-level entity representing a collection of markets.

**Key Fields:**
```python
@dataclass
class PolymarketEvent:
    id: str
    title: str
    slug: str
    description: str
    startDate: str
    endDate: str
    markets: List[Market]
    tags: List[Tag]
    series: Optional[Series]
    volume: float
    liquidity: float
    active: bool
    closed: bool
    ...
```

**Terminology:**
- **Event** - Top-level entity (e.g., "2024 US Presidential Election")
- **Market** - Individual prediction within event (e.g., "Will Trump win?")
- **Outcome** - Binary result (Yes/No) with associated CLOB token ID

**Example:**
```
Event: 2024 Presidential Election
├── Market: Will Trump win?
│   ├── Outcome: Yes (asset_id: abc123)
│   └── Outcome: No (asset_id: def456)
└── Market: Will Biden win?
    ├── Outcome: Yes (asset_id: ghi789)
    └── Outcome: No (asset_id: jkl012)
```

### Market

Individual prediction market within an event.

**Key Fields:**
```python
@dataclass
class Market:
    id: str
    question: str
    slug: str
    outcomes: str  # JSON string of outcomes
    clobTokenIds: str  # Comma-separated asset IDs
    volume: str
    liquidity: str
    active: bool
    closed: bool
    endDate: str
    ...
```

**CLOB Token IDs:**

Each market has one or more **CLOB token IDs** (Central Limit Order Book):

```python
market.clobTokenIds  # "123456,789012" (Yes,No)
```

These IDs are used for WebSocket subscriptions and order placement.

### Series

Recurring markets (e.g., weekly/monthly predictions).

```python
@dataclass
class Series:
    id: str
    title: str
    slug: str
    seriesType: str  # "RECURRING"
    recurrence: str  # "weekly", "monthly"
    ...
```

### Tag

Market categorization/labeling.

```python
@dataclass
class Tag:
    id: str
    label: str  # "Politics", "Crypto", "Sports"
    slug: str
    ...
```

## Usage Examples

### Example 1: Fetch Active Markets

```python
from argus.polymarket_direct import EnhancedPM

client = EnhancedPM(None, None, dry_mode=True)

# Fetch first 50 active events
events = client.fetch_events(offset=0, limit=50)

for event in events:
    print(f"Event: {event.title}")
    print(f"  Volume: ${event.volume:,.2f}")
    print(f"  Liquidity: ${event.liquidity:,.2f}")
    print(f"  Markets: {len(event.markets)}")

    for market in event.markets:
        print(f"    - {market.question}")
```

**Output:**
```
Event: 2024 Presidential Election
  Volume: $12,345,678.90
  Liquidity: $567,890.12
  Markets: 3
    - Will Trump win?
    - Will Biden win?
    - Will third party win?
```

### Example 2: Subscribe to Market Data

```python
from argus.polymarket_direct import EnhancedPM

client = EnhancedPM(None, None, dry_mode=True)

# Start WebSocket
client.start_market_ws()
client.market_open_semaphore.acquire()  # Wait for connection
print("WebSocket connected!")

# Fetch an event
events = client.fetch_events(limit=1)
event = events[0]

# Extract CLOB token IDs
market = event.markets[0]
asset_ids = market.clobTokenIds.split(',')

# Define callback
def on_price_change(data):
    asset_id = data['asset_id']
    price = data.get('price', 'N/A')
    print(f"Asset {asset_id}: {price}")

# Subscribe
client.subscribe_to_market_data(asset_ids, on_price_change)

# Keep running
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Shutting down...")
```

### Example 3: Track Specific Market

```python
from argus.polymarket_direct import EnhancedPM

client = EnhancedPM(None, None, dry_mode=True)
client.start_market_ws()
client.market_open_semaphore.acquire()

# Find specific market by keyword
events = client.fetch_events(limit=100)
target_market = None

for event in events:
    for market in event.markets:
        if "Trump" in market.question:
            target_market = market
            break
    if target_market:
        break

if target_market:
    print(f"Tracking: {target_market.question}")
    asset_ids = target_market.clobTokenIds.split(',')

    def log_price(data):
        price = float(data.get('price', 0))
        probability = price * 100
        print(f"Probability: {probability:.2f}%")

    client.subscribe_to_market_data(asset_ids, log_price)

    while True:
        time.sleep(1)
```

### Example 4: Market Statistics

```python
from argus.polymarket_direct import EnhancedPM

client = EnhancedPM(None, None, dry_mode=True)
events = client.fetch_events(limit=100)

# Calculate statistics
total_volume = sum(e.volume for e in events if e.volume)
total_liquidity = sum(e.liquidity for e in events if e.liquidity)
active_markets = sum(1 for e in events if e.active)
closed_markets = sum(1 for e in events if e.closed)

print(f"Total Volume: ${total_volume:,.2f}")
print(f"Total Liquidity: ${total_liquidity:,.2f}")
print(f"Active Markets: {active_markets}")
print(f"Closed Markets: {closed_markets}")

# Top 10 by volume
top_events = sorted(events, key=lambda e: e.volume or 0, reverse=True)[:10]
print("\nTop 10 Events by Volume:")
for i, event in enumerate(top_events, 1):
    print(f"{i}. {event.title} - ${event.volume:,.2f}")
```

### Example 5: WebSocket Auto-Reconnect

```python
from argus.polymarket_direct import EnhancedPM

client = EnhancedPM(None, None, dry_mode=True)
client.start_market_ws()
client.market_open_semaphore.acquire()

# Subscribe to markets
asset_ids = ['asset_1', 'asset_2']
client.subscribe_to_market_data(asset_ids, lambda d: print(d))

# Periodically check connection health
import time
while True:
    time.sleep(60)  # Check every minute
    try:
        # If connection drops, restart will be automatic via on_close handler
        # But you can also manually restart:
        # client.restart_ws_connections()
        pass
    except Exception as e:
        print(f"Error: {e}")
        client.restart_ws_connections()
```

## Configuration

### Environment Variables

```bash
# Optional (only needed for order placement, not dry mode)
POLYMARKET_PRIVATE_KEY=your_private_key
POLYMARKET_PROXY_FUNDER=your_proxy_address
```

### Cache

The module uses `DomainCache('polymarket_direct')`:

**Cache Location:** `~/.argus/polymarket_cache.pkl`

**Cached Data:**
- Event/market metadata (if caching decorator is added)

**Disable Caching:**
```bash
export ARGUS_CACHES_DISABLED=1
```

### WebSocket Message Logging

All WebSocket messages are auto-logged to file:

**File:** `ws_messages.fk`

**Format:** One JSON object per line

**Use Cases:**
- Debugging WebSocket issues
- Replaying market data
- Analyzing message patterns

## Limitations

### 1. Not a Dispatcher

**Constraint:** Does not follow standard Argus dispatcher pattern.

**Impact:**
- No TCP/UDS server
- No Protocol 2 streaming
- No multi-client multiplexing
- Different API than IB/Capital/Binance

**Workaround:**
- Use directly in Python code (cannot connect from C++/Rust/etc.)
- Implement custom dispatcher if needed

### 2. No Protocol 2

**Constraint:** Data is JSON, not Protocol 2 binary format.

**Impact:**
- Larger payload size
- Slower parsing (JSON vs. CSV)
- Not compatible with Protocol 2 clients

**Workaround:**
- Accept JSON format
- Build custom Protocol 2 bridge if needed

### 3. Dry Mode Restrictions

**Constraint:** Dry mode cannot place orders.

**Impact:**
- Read-only access to markets
- Cannot execute trades

**Workaround:**
- Use full mode with credentials for trading

### 4. CLOB Token ID Mapping

**Constraint:** Must manually extract `clobTokenIds` from market data.

**Impact:**
- No automatic symbol→ID resolution
- Must parse comma-separated string

**Workaround:**
```python
asset_ids = market.clobTokenIds.split(',')
yes_token = asset_ids[0]
no_token = asset_ids[1]
```

### 5. No Historical Data

**Constraint:** WebSocket provides real-time updates only.

**Impact:**
- Cannot retrieve historical prices
- No backfill for reconnection gaps

**Workaround:**
- Log all WebSocket messages to file
- Use third-party historical data sources

### 6. WebSocket Unsubscribe Limitation

**Constraint:** Polymarket WebSocket doesn't support true unsubscribe.

**Implementation:**
```python
def unsubscribe_from_market_data(self, asset_id):
    # Set callback to no-op instead of actual unsubscribe
    for idx in asset_id:
        self.idx_to_callback[idx] = lambda x: None
```

**Impact:**
- Still receive messages for "unsubscribed" markets
- Bandwidth waste for unused subscriptions

## Migration Notes

### From Legacy Dispatcher

If migrating from the legacy `polymarket` dispatcher (https://github.com/The-Sal/Argus/tree/legacy/polymarket-dispatcher):

**Key Changes:**

1. **Import Path:**
   ```python
   # Old
   from argus.polymarket import PolymarketDispatcher

   # New
   from argus.polymarket_direct import EnhancedPM
   ```

2. **Initialization:**
   ```python
   # Old
   dispatcher = PolymarketDispatcher()

   # New
   client = EnhancedPM()
   ```

3. **Data Models:**
   ```python
   # New
   from argus.polymarket_direct._types import PolymarketEvent, Market
   ```

4. **Subscription:**
   ```python
   # Old (via dispatcher commands)
   # TCP client sends: "subscribe=market_id"

   # New (direct callback)
   client.subscribe_to_market_data(asset_ids, callback)
   ```

### Terminology Mapping

| Legacy | New | Description |
|--------|-----|-------------|
| Market | Event | Top-level entity |
| - | Market | Individual prediction market |
| - | Outcome | Binary result (Yes/No) |
| - | CLOB Token ID | Asset identifier |

## File Reference

```
argus/polymarket/
└── __init__.py           # Stub pointing to legacy branch

argus/polymarket_direct/
├── __init__.py           # EnhancedPM client
├── _types.py             # Data models (Event, Market, Series, Tag)
└── _example.py           # Usage examples
```

## Summary

The Polymarket module provides direct integration with Polymarket's prediction markets, **diverging from Argus's standard dispatcher architecture**:

**Key Differences:**
- ❌ No dispatcher pattern
- ❌ No Protocol 2
- ❌ No TCP/UDS server
- ✅ Direct Python client library
- ✅ REST API + WebSocket
- ✅ Dry mode for read-only access

**Best For:**
- Python-based prediction market analysis
- Real-time market data subscriptions
- Event/market discovery
- Research and data collection

**Not Suitable For:**
- Multi-language systems (C++, Rust, etc.)
- Integration with other Argus dispatchers
- High-frequency trading (use direct WebSocket)

For traditional dispatcher-based architecture, see the legacy branch. For modern, flexible Polymarket integration, use `polymarket_direct`.
