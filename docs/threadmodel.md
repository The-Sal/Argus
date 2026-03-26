# PolymarketDispatcher Threading Model

## Overview

The PolymarketDispatcher employs a **hybrid multi-threaded architecture** combining persistent background threads, per-client connection threads, temporary thread pools for concurrent operations, and WebSocket connection threads. The threading model is designed to handle high-concurrency market data distribution, order management, and real-time account event notifications.

---

## Thread Categories

### 1. Persistent Background Threads (Long-running)

These threads run for the entire lifetime of the dispatcher:

| Thread | File | Spawn Function | Lifespan | Purpose |
|--------|------|----------------|----------|---------|
| **TCP Server Main Loop** | `polymarket/__init__.py:1409-1411` | `@runAsThread run()` | Until process exit | Accepts incoming client connections |
| **Market Cache Refresher** | `polymarket/__init__.py:170-179` | `@runAsThread start_update_markets_cache_thread()` | Infinite loop (5min sleep) | Periodically refreshes market metadata cache |
| **Order Book WebSocket** | `polymarket_direct/wss.py:187-190` | `@runAsThread _start_ws()` | Until reconnect | WebSocket connection to Polymarket order book |
| **Order Book Ping/Pong** | `polymarket_direct/wss.py:130-176` | `@runAsThread ping()` | Infinite loop (10s sleep) | Heartbeat maintenance for order book WS |
| **Account Events WebSocket** | `polymarket_direct/wss.py:187-190` | `@runAsThread _start_ws()` | Until reconnect | Authenticated WS for account events |
| **Account Events Ping/Pong** | `polymarket_direct/wss.py:130-176` | `@runAsThread ping()` | Infinite loop (10s sleep) | Heartbeat for account events WS |

**Thread Creation Count**: ~6 persistent threads minimum

---

### 2. Per-Client Connection Threads (Dynamic)

**Spawned by**: `utils3.networking.sockets.Server._execute_async()`

The TCP server creates **daemon threads** for each client interaction:

```python
# From utils3/networking/sockets.py:13-17
def _execute_async(func, *args, **kwargs):
    thread = threading.Thread(target=func, args=args, kwargs=kwargs, daemon=True)
    thread.start()
    _threads.append(thread)
    return thread
```

#### Thread Lifecycle:
1. **On Connect**: `_execute_async(on_connect, client, address)`
2. **Per Message**: `_execute_async(on_recv, client, address, data)`
3. **On Disconnect**: Thread terminates via exception handling

**Thread Destruction**: Threads exit automatically when:
- `ConnectionResetError` raised
- `ConnectionAbortedError` raised
- `OSError` raised
- `on_disconnect` callback completes

**Important**: The `_threads` list tracks spawned threads but they are daemon threads (exits with main process).

---

### 3. Temporary Thread Pools (Task-scoped)

#### A. Order Building Thread Pool
**Location**: `polymarket/__init__.py:1247`

```python
with ThreadPoolExecutor(max_workers=min(len(order_specs), 10)) as executor:
    future_to_order = {
        executor.submit(self.rest_api.build_order, ...): spec 
        for spec in order_specs
    }
```

**Triggered by**: `place_multiple_orders` action
**Max Workers**: `min(number_of_orders, 10)`
**Lifespan**: Scoped to order building operation only
**Purpose**: Parallelizes order building HTTP calls to minimize latency

#### B. Tick Size Fetching Thread Pool
**Location**: `polymarket_direct/wss.py:291`

```python
self._thread_pool = ThreadPoolExecutor(max_workers=5, thread_name_prefix="PolyMarketOrderBookWssThreadPool")
```

**Triggered by**: `subscribe_to_asset_id()` for async tick size retrieval
**Max Workers**: 5 (fixed)
**Lifespan**: Persistent within `PolyMarketOrderBookWss` instance

#### C. REST API Thread Pool
**Location**: `polymarket_direct/rest.py:132`

```python
self._thread_pool = ThreadPoolExecutor(max_workers=5)
```

**Triggered by**: `_rapid_order_builder()` for concurrent API calls
**Max Workers**: 5 (fixed)
**Lifespan**: Persistent within `PolyRestAPI` instance

---

## Thread Scaling Model

### Per-Asset Scaling

The threading model is **NOT 1:1 threads per asset**. Instead:

1. **Single WebSocket Connection**: One thread handles ALL assets through a multiplexed WebSocket
2. **Shared Order Book State**: Protected by `_dict_lock` (`wss.py:289`)
3. **Callback Broadcasting**: Market data callbacks route to subscribed clients via `RoutingHelper`

**Asset-Thread Relationship**:
- Assets are tracked in internal dictionaries (no dedicated threads)
- Updates received via single WebSocket thread
- Lock-protected state updates
- Broadcast to multiple clients via routing table

**Maximum Assets**: Unlimited (constrained by memory, not threads)

### Per-Client Scaling

**1:1 Thread Model for Client Connections**:

```
Client Connects
    ↓
Server._execute_async(on_connect_handler)
    ↓
New daemon thread spawned
    ↓
Each message: _execute_async(on_recv_handler) → New thread
    ↓
Disconnect → Thread exits
```

**Thread Count Formula**:
```
Total Client Threads = (1 connect thread) + (N message handling threads per client)
```

**Important**: This can lead to thread explosion under high message volume. The threads are daemon threads that clean up automatically.

---

## Synchronization & Locking

### Lock Inventory

| Lock Name | Location | Protects | Type |
|-----------|----------|----------|------|
| `_market_cache_lock` | `__init__.py:132` | `_all_markets_cache`, `_asset_id_to_ticker` | `threading.Lock()` |
| `_lock` (inherited) | `_classes.py:119` | `_sockets`, `_market_data_routing_table`, `_order_subscriptions` | `threading.Lock()` |
| `_dict_lock` | `wss.py:289` | `_asset_id_to_order_book`, `_asset_id_to_misc_info` | `threading.Lock()` |
| `_ping_pong_lock` | `wss.py:38` | `_ping_pongs` counter tuple | `threading.Lock()` |
| `_pinging_lock` | `wss.py:43` | Prevents concurrent ping threads | `threading.Lock()` |
| `_lock` | `CorrelationIDChecker` (`_classes.py:248`) | `seen_correlation_ids` OrderedDict | `threading.Lock()` |

### Threading Events

| Event | Location | Purpose |
|-------|----------|---------|
| `wait_till_socket_open` | `wss.py:50` | Signals WebSocket connection established |
| `wait_till_first_pong` | `wss.py:51` | Signals first PONG received after PING |

---

## Thread Spawning Locations

### Function Decorator Pattern

The `@runAsThread` decorator (from `utils3`) marks methods that spawn threads:

```python
# argus/polymarket/__init__.py
@runAsThread
def start_update_markets_cache_thread(self):  # Line 170

@runAsThread  
def run(self):  # Line 1409

# argus/polymarket_direct/wss.py
@runAsThread
def ping(self):  # Line 130

@runAsThread
def _start_ws(self):  # Line 187

@runAsThread
def _defer_restore_state(self):  # Line 347
```

### Direct Thread Creation

```python
# In ThreadPoolExecutor contexts (no decorator needed):
- __init__.py:1247 - Order building executor
- wss.py:291 - Tick size fetching executor  
- rest.py:132 - Rapid order building executor
```

---

## Thread Destruction & Cleanup

### Normal Thread Termination

1. **Daemon Threads**: Exit automatically when main process exits
2. **ThreadPoolExecutor**: Context manager ensures cleanup (`with` statement)
3. **WebSocket Threads**: 
   - Exits on `_on_close_base()` during reconnection
   - Exits on `max_reconnect_attempts` exceeded
   - Exits on fatal error

### Client Connection Cleanup

**Trigger**: Client disconnect or socket error

**Cleanup Chain**:
```
Connection Error
    ↓
_builtin_on_connect catches exception
    ↓
self.on_disconnect(client, address) called
    ↓
remove_socket(sock) [RoutingHelper._classes.py:125]
    ↓
Remove from _sockets set
    ↓
Remove from _order_subscriptions dict
    ↓
Remove from _market_data_routing_table
    ↓
subscription_expired(clob_id) called
    ↓
market_data.unsubscribe_from_asset_id(asset_id)
```

### WebSocket Reconnection

When WebSocket disconnects (`_on_close_base` in `wss.py:91-111`):

1. Current thread terminates
2. `_on_reconnect_start()` called
3. `_reset_threading_events()` clears events
4. `_defer_restore_state()` spawns new thread
5. `_start_ws()` creates new WebSocket connection thread
6. Subscriptions restored after `wait_till_first_pong`

**Max Reconnect Attempts**: 50 (configurable via `POLYMARKET_MAX_SOCKET_RETRIES`)

---

## Thread Safety Patterns

### 1. Lock-Acquire Pattern

```python
with self._lock:
    # Critical section
    self._sockets.add(sock)
```

### 2. Event Waiting Pattern

```python
# In _defer_restore_state (wss.py:347-360)
self.wait_till_first_pong.wait()  # Blocks until pong received
# ... restore subscriptions
```

### 3. ThreadPoolExecutor with Futures

```python
with ThreadPoolExecutor(...) as executor:
    future_to_order = {executor.submit(func, arg): arg for arg in args}
    for future in as_completed(future_to_order):
        result = future.result()
```

### 4. Lock-Protected State Access

```python
# Order book updates (wss.py:420-443)
with self._dict_lock:
    if asset_id not in self._asset_id_to_order_book:
        return
    # ... modify order book
```

---

## Key Threading Relationships

### Data Flow Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   PolymarketDispatcher                     │
├─────────────────────────────────────────────────────────────┤
│  Main Process Thread                                         │
│  ├── Initializes all components                              │
│  ├── Spawns background threads via @runAsThread             │
│  └── Runs interactive_mode()                                 │
├─────────────────────────────────────────────────────────────┤
│  TCP Server (utils3.networking.sockets.Server)              │
│  ├── Thread #1: Server accept loop (daemon)                  │
│  ├── Thread N: Per-client connection (daemon)                │
│  └── Thread M: Per-message handling (daemon)                 │
├─────────────────────────────────────────────────────────────┤
│  RoutingHelper (Thread-Safe)                                 │
│  ├── _lock protects routing tables                           │
│  ├── add_socket() → Lock acquired                          │
│  └── remove_socket() → Lock acquired + cleanup              │
├─────────────────────────────────────────────────────────────┤
│  Order Book WebSocket (PolyMarketOrderBookWss)              │
│  ├── Thread: WebSocket connection (persistent)              │
│  ├── Thread: Ping/Pong heartbeat (persistent)                │
│  ├── ThreadPool: 5 workers for tick sizes                  │
│  └── _dict_lock: Order book state protection               │
├─────────────────────────────────────────────────────────────┤
│  Account Events WebSocket (PolyMarketAccountEventWss)       │
│  ├── Thread: WebSocket connection (persistent)            │
│  └── Thread: Ping/Pong heartbeat (persistent)              │
├─────────────────────────────────────────────────────────────┤
│  REST API (PolyRestAPI)                                      │
│  └── ThreadPool: 5 workers for rapid order building        │
└─────────────────────────────────────────────────────────────┘
```

### Thread-to-Client Mapping

```
Client A Connects
    └── Spawns Thread-A (connection handler)
        └── Each message → Spawns Thread-A-N (message handler)

Client B Connects  
    └── Spawns Thread-B (connection handler)
        └── Each message → Spawns Thread-B-N (message handler)

Market Data Update (WebSocket Thread)
    └── Reads update from Polymarket
        └── Acquires _dict_lock
            └── Updates _asset_id_to_order_book
                └── Calls _order_book_update_callback
                    └── Routes to all subscribed clients via routing table
```

---

## Configuration & Tuning

### Environment Variables Affecting Threading

| Variable | Default | Description |
|----------|---------|-------------|
| `POLYMARKET_MAX_SOCKET_RETRIES` | 50 | Max WebSocket reconnection attempts |
| `POLYMARKET_MAX_PING_PONG_FAILURES` | 3 | Max ping/pong failures before reconnect |
| `POLYMARKET_DISABLE_PING_PONG_LOGS` | false | Disable ping/pong logging |
| `POLYMARKET_ORDERBOOK_DEPTH` | 10 | Depth for P2 encoding |
| `POLYMARKET_RAPID_ORDER_BUILD` | false | Enable rapid order building thread pool |
| `POLYMARKET_FULL_MARKET_CACHE_REFRESH_INTERVAL` | 300 | Cache refresh interval in seconds |
| `MAX_SEEN_CORRELATION_IDS` | 100,000 | Max correlation IDs to track |
| `MAX_CORRELATION_ID_LENGTH` | 40 | Max correlation ID length |

---

## Potential Threading Issues

### 1. Thread Explosion Risk
**Location**: `utils3/networking/sockets.py:13-17`
**Issue**: Every message spawns a new thread; high message volume = thread explosion
**Mitigation**: Daemon threads exit with process; limited by client connection count

### 2. Lock Contention
**Location**: `_classes.py:119` (RoutingHelper._lock)
**Issue**: Single lock protects all routing operations
**Impact**: Can become bottleneck with many concurrent subscriptions

### 3. WebSocket Reconnection Race
**Location**: `wss.py:91-111` (_on_close_base)
**Issue**: Multiple close events could spawn multiple reconnection threads
**Mitigation**: `_pinging_lock` prevents concurrent ping threads; exponential backoff

### 4. Future Cancellation
**Location**: `wss.py:403-405`
**Issue**: Tick size futures may not cancel cleanly
**Code**:
```python
if possible_future and not possible_future.done():
    possible_future.cancel()
```

---

## Summary Statistics

| Category | Count | Notes |
|----------|-------|-------|
| **Persistent Threads** | 6+ | Background maintenance |
| **ThreadPools** | 3 | Fixed worker pools |
| **Locks** | 6+ | Synchronization |
| **Events** | 2 | State synchronization |
| **Max Workers (Order Building)** | 10 | Temporary pool |
| **Max Workers (Tick Size)** | 5 | Persistent pool |
| **Max Workers (REST API)** | 5 | Persistent pool |
| **Total Thread Count** | 6 + 3*N + 10 (variable) | N = connected clients |

---

## Files Involved in Threading

1. `argus/polymarket/__init__.py` - Main dispatcher, TCP server, order handling
2. `argus/polymarket/_classes.py` - RoutingHelper, CorrelationIDChecker
3. `argus/polymarket_direct/wss.py` - WebSocket connections, ping/pong
4. `argus/polymarket_direct/rest.py` - REST API, order building
5. `utils3/networking/sockets.py` - TCP server implementation

---

*Generated: 2026-02-24*
*PolymarketDispatcher Threading Model Documentation*
