# Polymarket WebSocket Sharding System

## Overview

The Polymarket WebSocket Sharding System replaces the legacy single-connection `PolyMarketOrderBookWss` with a horizontally scalable `PolyMarketOrderBookPool`. 
This enables the Polymarket dispatcher to handle large numbers of concurrent market data subscriptions by distributing them across multiple WebSocket connections (shards).
More importantly, it reduces the latencies for each asset in large portfolios relative to a naive websocket with 20-30 assets.

### Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      PolymarketDispatcher                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                  PolyMarketOrderBookPool                            │    │
│  │                                                                     │    │
│  │   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────┐    │    │
│  │   │   Shard 0   │  │   Shard 1   │  │   Shard N   │  │Draining │    │    │
│  │   │  (4 assets) │  │  (4 assets) │  │  (2 assets) │  │  Idle   │    │    │
│  │   │             │  │             │  │             │  │  (30s)  │    │    │
│  │   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └────┬────┘    │    │
│  │          │                │                │              │         │    │
│  │          └────────────────┴────────────────┘              │         │    │
│  │                           │                               │         │    │
│  │                           ▼                               ▼         │    │
│  │                 ┌──────────────────────┐              ┌──────────┐  │    │
│  │                 │    OrderBookStore    │              │ Sweeper  │  │    │
│  │                 │  (Shared State)      │              │ (Scale   │  │    │
│  │                 │                      │              │  down)   │  │    │
│  │                 └──────────────────────┘              └──────────┘  │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │              Polymarket CLOB WebSocket Endpoints                    │    │
│  │         wss://ws-subscriptions-clob.polymarket.com/ws/market        │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Key Components

### 1. OrderBookStore

Centralized state container shared across all shards. All order book state lives here, decoupled from individual WebSocket connections.

**Responsibilities:**
- Maintains `_asset_id_to_order_book` (L2 book state)
- Maintains `_asset_id_to_misc_info` (tick sizes, futures)
- Maintains `_asset_id_to_best_bid_ask` (top-of-book cache for deduplication)
- Thread-safe message processing via `_dict_lock`
- REST API session for tick-size fetching
- Latency tracking (`_last_msg_recv_ts`)

**Thread Safety Model:**
- Per-asset writes are serialized by the Pool (one shard per asset_id)
- Cross-asset writes protected by `_dict_lock`
- Deduplication logic prevents redundant callbacks

### 2. PolyMarketOrderBookConn

A single WebSocket shard. Manages the WebSocket lifecycle (connect, ping/pong, reconnect) for a subset of assets.

**Key Features:**
- Inherits from `PolymarketWSSBase` (reconnection, heartbeat)
- Maintains a `_roster` of subscribed asset_ids
- Re-subscribes to all roster assets on reconnect
- Can be "closed" (drained) without affecting other shards

### 3. PolyMarketOrderBookPool

Facade that manages N shards, exposing the same API as the legacy single-connection class.

**Sharding Strategy:**
| Parameter | Default | Description |
|-----------|---------|-------------|
| `POLYMARKET_MAX_ASSETS_PER_WS` | 4 | Maximum assets per shard |
| `POLYMARKET_MIN_SHARDS` | 1 | Minimum shards to keep alive |
| `POLYMARKET_MAX_SHARDS` | 10 | Maximum shards allowed |
| `POLYMARKET_SCALE_DOWN_IDLE_S` | 30 | Grace window before closing idle shards |

**Subscription Logic:**
1. Pick the smallest non-draining shard with room
2. If all shards full and below `max_shards`, spawn new shard
3. If at capacity, raise `RuntimeError`

**Scale-Down (Draining) Logic:**
- When a shard's roster empties (and above `min_shards`), enter "draining" state
- Grace window of `scale_down_idle_seconds` (default 30s)
- New subscription during grace window "un-drains" the shard (avoids churn)
- Sweeper thread closes drained shards after timeout

## Environment Variables

All sharding behavior is tunable via environment variables:

```bash
# Core sharding parameters
export POLYMARKET_MAX_ASSETS_PER_WS=4        # Assets per WebSocket connection
export POLYMARKET_MIN_SHARDS=1               # Minimum shards to maintain
export POLYMARKET_MAX_SHARDS=10              # Maximum shards allowed
export POLYMARKET_SCALE_DOWN_IDLE_S=30       # Idle grace period (seconds)

# WebSocket resilience
export POLYMARKET_MAX_SOCKET_RETRIES=50      # Max reconnect attempts
export POLYMARKET_MAX_PING_PONG_FAILURES=3   # Max missed heartbeats
export POLYMARKET_DISABLE_PING_PONG_LOGS=false  # Set to true to reduce noise

# Order book depth
export POLYMARKET_ORDERBOOK_DEPTH=10         # Levels in P2 packets
```

## Migration from Legacy (Single Connection)

The `PolyMarketOrderBookPool` exposes the same surface as the old `PolyMarketOrderBookWss`:

| Old API (PolyMarketOrderBookWss) | New API (PolyMarketOrderBookPool) |
|----------------------------------|-----------------------------------|
| `run(main_thread=True/False)` | `run(main_thread=False)` (ignored) |
| `subscribe_to_asset_id(id)` | `subscribe_to_asset_id(id)` |
| `unsubscribe_from_asset_id(id)` | `unsubscribe_from_asset_id(id)` |
| `order_book_for_asset_id(id)` | `order_book_for_asset_id(id)` |
| `get_tick_size(id, timeout)` | `get_tick_size(id, timeout)` |
| `order_books` (property) | `order_books` (property) |
| `asset_ids` (property) | `asset_ids` (property) |

**Dispatcher-side change** (in `argus/polymarket/__init__.py`):
```python
# Old (single connection)
# self.market_data = wss.PolyMarketOrderBookWss(...)

# New (sharded pool)
self.market_data = wss.PolyMarketOrderBookPool(...)
```

## Performance Characteristics

### Latency

The sharding system introduces no additional latency for message processing:
- Messages arrive on shard threads and are immediately forwarded to `OrderBookStore.apply_message()`
- Latency tracking (`_last_msg_recv_ts`) is updated per-message regardless of shard
- Dispatcher latency measurement (WS arrival → P2 transmission) remains accurate

### Throughput

| Metric | Single Conn | Sharded (4 assets/shard) | Improvement |
|--------|-------------|--------------------------|-------------|
| Max concurrent assets | ~20 | 40 (10 shards × 4) | **2x** |
| Messages/sec capacity | Limited by single WS | Distributed across shards | Linear scaling |
| Reconnect impact | All assets affected | Only shard's assets affected | Isolated |

### Memory

- Each shard: ~baseline WebSocket overhead
- OrderBookStore: Shared state (no duplication)
- Overall increase: ~100KB per additional shard

## Visualization

The pool provides a visualization method to inspect shard distribution:

```python
from argus.polymarket_direct.wss import PolyMarketOrderBookPool

pool = PolyMarketOrderBookPool(...)
pool.run()

# Print ASCII visualization (available in PolymarketDispatcher CLI as 'Visualise Shards')
pool.visualise_shards()  # Note: British spelling 'visualise' in the code
```

**Example Output:**
```
=================================================================
                    SHARD VISUALIZATION
=================================================================

POOL CONFIGURATION
  Min Shards: 1
  Max Shards: 10
  Max Assets Per Shard: 4

-----------------------------------------------------------------

SHARD #0 [ACTIVE] [FULL]
  Load: 4/4 assets
  Assets:
    • 32820219079047008931678545500601547...
    • 43630090304992053861911786701136314...
    • 11009250665775568085083413298300064...
    • 94500987710044090265110195005820324...
  Health:
    Ping/Pong delta: 0 (sent: 21, recv: 21)
    Last message: 0.05s ago
SHARD #1 [ACTIVE] [FULL]
  Load: 4/4 assets
  Assets:
    • 10813631635898693199229240256464515...
    • 20465377781331342569893552457730324...
    • 68546536892598319906894132612577868...
    • 73677535990417713024934905806023672...
  Health:
    Ping/Pong delta: 0 (sent: 20, recv: 20)
    Last message: 0.01s ago
.....
```