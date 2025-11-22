# Binance Module

The Binance module provides real-time cryptocurrency market data from Binance's WebSocket API with order book depth, aggregate trades, and k-line (candlestick) data.

## Table of Contents

- [Overview](#overview)
- [Dispatcher](#dispatcher)
- [Features](#features)
- [API Surface](#api-surface)
- [Internal Architecture](#internal-architecture)
- [Data Types](#data-types)
- [Auto-Dump Feature](#auto-dump-feature)
- [Usage Examples](#usage-examples)

## Overview

The Binance module follows the standard Argus dispatcher paradigm, providing a TCP server that bridges between Binance's WebSocket API and multiple clients.

**Location:** `/argus/binance/`

**Primary Files:**
- `__init__.py` (538 lines) - BinanceWss and BinanceMKTDispatcher
- `_classes.py` - Data classes for Binance message types

**Key Features:**
- TCP socket server (default port: 9982)
- Protocol 2 for market data streaming
- Auto-dump messages to JSON files
- Statistics tracking (messages per second)
- Order book depth updates (@100ms)
- Aggregate trades
- K-line (candlestick) data
- Interactive configuration

## Dispatcher

### BinanceMKTDispatcher - `binance`

**Purpose:** Real-time cryptocurrency market data from Binance

**Transport:** TCP socket (default port: 9982)

**Caching:** ✅ Yes (last-known values for bid/ask/last)

**Data Protocol:** Protocol 2

**Concurrent Clients:** ✅ Full support

**Launch:**
```bash
python runtime.py binance --port 9982
```

**Client Commands:**

| Command | Description |
|---------|-------------|
| `add=SYMBOL` | Subscribe to a crypto pair (e.g., `BTCUSDT`) |
| `subscribe=SYMBOL` | Alias for `add` |

**Note:** Symbol format must match Binance convention (e.g., `BTCUSDT`, not `BTC/USDT` or `BTC-USDT`)

## Features

### Feature Matrix

| Feature | Support | Notes |
|---------|---------|-------|
| Real-time market data | ✅ | Bid/Ask/Last via WebSocket |
| Order book depth | ✅ | @100ms updates |
| Aggregate trades | ✅ | Combined market/limit orders |
| K-line data | ✅ | 1-second candles |
| Protocol 2 streaming | ✅ | Normalized data format |
| Multi-client support | ✅ | TCP allows multiple connections |
| Auto-dump | ✅ | JSON file exports every 30s |
| Statistics | ✅ | Messages/second tracking |
| Caching | ✅ | Last-known values |
| Interactive mode | ✅ | Runtime configuration |
| Auto-unsubscribe | ✅ | Cleanup on client disconnect |

### Why TCP Instead of UDS?

Unlike the Capital.com module (UDS), Binance uses **TCP sockets**:

**Advantages:**
- **Remote access** - Can connect from other machines
- **Cross-platform** - Native Windows support
- **Standard tooling** - Compatible with any TCP client

**Disadvantages:**
- Slightly higher latency vs. UDS (negligible for crypto markets)
- Requires port management (default: 9982)

## API Surface

### Client-Side Commands

Clients send simple text commands to subscribe:

```python
import socket

s = socket.socket()
s.connect(('localhost', 9982))

# Subscribe to Bitcoin
s.sendall(b'add=BTCUSDT')

# Subscribe to Ethereum
s.sendall(b'subscribe=ETHUSDT')
```

**Command Format:**
- `add=SYMBOL`
- `subscribe=SYMBOL`

Both are equivalent.

### Market Data Stream (Protocol 2)

Once subscribed, market data flows continuously in Protocol 2 format:

```
~<packet-length><symbol-length>|<symbol><market-data>L

Fields (in order):
  1. bid
  2. bid_size
  3. ask
  4. ask_size
  5. last
  6. last_size
  7. timestamp (Binance event timestamp)
  8. transmission_time (Argus transmission timestamp)
```

**Example:**
```
~00730007|BTCUSDT98765.50,1.234,98766.00,0.876,98765.75,0.500,1750217540123,1750217540200L
```

**Parsed:**
- Symbol: `BTCUSDT`
- Bid: $98,765.50 × 1.234 BTC
- Ask: $98,766.00 × 0.876 BTC
- Last: $98,765.75 × 0.500 BTC
- Timestamp: 1750217540123 (Binance event time)
- Transmission: 1750217540200 (Argus time)

**Ping Messages:**

The dispatcher sends `$` (ASCII 36) as ping/health check:

```python
data = s.recv(4096)
if data[0] == 36:  # '$'
    data = data[1:]  # Strip ping byte
```

## Internal Architecture

### Component Stack

```
[Binance WebSocket Server]
    wss://stream.binance.com/stream
           ↓
    [BinanceWss]
           ↓
   [Message Parsing]
           ↓
  [Type Classification]
    ↓         ↓         ↓
[DepthUpdate] [AggTrade] [Kline]
    ↓         ↓         ↓
[Binance_CapitalComMKTDataLive]
           ↓
   [Protocol 2 Encoding]
           ↓
  [BinanceMKTDispatcher]
           ↓
    [TCP Socket Send]
           ↓
  [Multiple TCP Clients]
```

### Class Hierarchy

```
BinanceWss (WebSocket client)
    ├── Auto-dump thread
    └── Statistics thread

BinanceMKTDispatcher (TCP server)
    ├── Client listener thread
    ├── Per-client threads
    └── Health check thread
```

### Threading Model

1. **Main Thread** - WebSocket `run_forever()` loop
2. **Accept Thread** - TCP server `accept()` loop
3. **Client Thread(s)** - One per connected client
4. **Health Check Thread** - Ping clients every 5s
5. **Auto-Dump Thread** - Save messages every 30s
6. **Statistics Thread** - Log stats every 10s

**Thread Safety:**
- `_thread_lock` protects `symbol_to_clients` and `symbol_data_cache`
- WebSocket callbacks are thread-safe
- Client list operations protected by locks

### Data Flow

**Subscription Flow:**

```
Client sends: "add=BTCUSDT"
    ↓
_subscribe_to_symbol(BTCUSDT, client_socket)
    ↓
Check if first client for BTCUSDT?
    ↓ (yes)
BinanceWss.subscribe(BTCUSDT, callback)
    ↓
WebSocket SUBSCRIBE message sent
    ↓
Binance server confirms subscription
    ↓
Market data starts flowing...
```

**Market Data Flow:**

```
Binance WebSocket message arrives
    ↓
_on_message(ws, message)
    ↓
Parse JSON and classify type
    ↓ (depth@100ms)
DepthStreamMessage.from_dict()
    ↓
AbstractBinanceType(idx=DEPTH_STREAM, obj=...)
    ↓
Symbol callback: _binance_callback(symbol, msg)
    ↓
Convert to Binance_CapitalComMKTDataLive
    ↓
Merge with existing data (if available)
    ↓
Update symbol_data_cache
    ↓
Get all clients for symbol
    ↓
transmit_mkt_data_with_protocol_2()
    ↓
client.sendall(packet) for each client
```

**Unsubscription Flow:**

```
_check_clients_live() (every 5s)
    ↓
Send ping '$' to all clients
    ↓
Client disconnected? (OSError)
    ↓
Remove from symbol_to_clients[symbol]
    ↓
No clients left for symbol?
    ↓
BinanceWss.unsubscribe(symbol)
    ↓
WebSocket UNSUBSCRIBE message sent
    ↓
Binance server stops sending data
```

## Data Types

### Binance Message Types

The module handles three primary message types from Binance:

#### 1. Depth Stream (`depth@100ms`)

Order book updates at 100ms intervals.

**Structure:**
```python
@dataclass
class DepthUpdate:
    e: str          # Event type ("depthUpdate")
    E: int          # Event time
    s: str          # Symbol ("BTCUSDT")
    U: int          # First update ID
    u: int          # Final update ID
    b: List[List]   # Bids [[price, qty], ...]
    a: List[List]   # Asks [[price, qty], ...]
```

**Conversion to Protocol 2:**
```python
market_data = Binance_CapitalComMKTDataLive.from_binance_depth(
    symbol, depth_update
)
```

**Protocol 2 Fields:**
- `bid` = `b[0][0]` (top bid price)
- `bid_size` = `b[0][1]` (top bid quantity)
- `ask` = `a[0][0]` (top ask price)
- `ask_size` = `a[0][1]` (top ask quantity)
- `last` = preserved from previous trade (if available)

#### 2. Aggregate Trade (`aggTrade`)

Combined market/limit orders executed at the same price.

**Structure:**
```python
@dataclass
class AggTradeData:
    e: str      # Event type ("aggTrade")
    E: int      # Event time
    s: str      # Symbol
    a: int      # Aggregate trade ID
    p: str      # Price
    q: str      # Quantity
    f: int      # First trade ID
    l: int      # Last trade ID
    T: int      # Trade time
    m: bool     # Is buyer the market maker?
    M: bool     # Ignore
```

**Conversion to Protocol 2:**
```python
market_data = Binance_CapitalComMKTDataLive.from_binance_trade(
    symbol, agg_trade, existing_data
)
```

**Protocol 2 Fields:**
- `last` = `p` (trade price)
- `last_size` = `q` (trade quantity)
- `bid` / `ask` = preserved from previous depth update (if available)

#### 3. K-line / Candlestick (`kline_1s`)

1-second candlestick data.

**Structure:**
```python
@dataclass
class KlineData:
    t: int      # Kline start time
    T: int      # Kline close time
    s: str      # Symbol
    i: str      # Interval ("1s")
    o: str      # Open price
    c: str      # Close price
    h: str      # High price
    l: str      # Low price
    v: str      # Base asset volume
    n: int      # Number of trades
    x: bool     # Is kline closed?
    # ... more fields
```

**Current Status:**
- K-line messages are **received and stored** (auto-dump)
- **Not currently converted to Protocol 2**
- Can be accessed from JSON dump files for historical analysis

### Binance_CapitalComMKTDataLive

Extends `CapitalComMKTDataLive` with Binance-specific factory methods:

```python
class Binance_CapitalComMKTDataLive(CapitalComMKTDataLive):
    @classmethod
    def from_binance_depth(cls, symbol: str, depth: DepthUpdate):
        # Create from order book update
        # Uses top bid/ask from order book
        # Last price = 0.0 (use existing if available)

    @classmethod
    def from_binance_trade(cls, symbol: str, trade: AggTradeData, existing=None):
        # Create from aggregate trade
        # Uses trade price/quantity for last/last_size
        # Preserves bid/ask from existing data if available
```

**Data Merging:**

The dispatcher intelligently merges depth and trade data:

```python
# Depth update arrives
market_data = from_binance_depth(symbol, depth)
if existing_data and existing_data.last > 0:
    market_data.last = existing_data.last          # Preserve last trade
    market_data.last_size = existing_data.last_size

# Trade update arrives
market_data = from_binance_trade(symbol, trade, existing_data)
# Bid/ask preserved from existing_data
```

This ensures clients always receive the most complete market picture.

## Auto-Dump Feature

The `BinanceWss` class automatically saves all received messages to JSON files.

**Configuration:**
```python
configs = {
    BinanceWssConfig.AUTO_DUMP: True,  # Enable/disable auto-dump
    BinanceWssConfig.TOTAL_MESSAGE_STATISTICS: True,  # Enable stats
}

wss = BinanceWss(configs=configs)
```

**Behavior:**
- Dumps messages every **30 seconds** (configurable: `_dump_interval`)
- File naming: `binance_wss_dump_<UUID>-<segment>.json`
- **Rollover** at 5000 messages (configurable: `_max_message_count`)
- Handles `KeyboardInterrupt` gracefully (completes dump before exit)

**File Format:**
```json
[
  {
    "stream": "btcusdt@depth@100ms",
    "data": {
      "e": "depthUpdate",
      "E": 1750217540123,
      "s": "BTCUSDT",
      "b": [["98765.50", "1.234"], ...],
      "a": [["98766.00", "0.876"], ...]
    },
    "received_at": 1750217540.200
  },
  ...
]
```

**Use Cases:**
- **Historical analysis** - Replay market conditions
- **Debugging** - Inspect raw Binance data
- **Research** - Study order book dynamics
- **Compliance** - Audit trail for trades

**Disable Auto-Dump:**
```python
configs[BinanceWssConfig.AUTO_DUMP] = False
```

## Configuration

### Dispatcher Configurations

Accessible via interactive mode:

```python
dispatcher = BinanceMKTDispatcher()
dispatcher.interactive_mode()
```

**Available Configs:**

| Config | Default | Description |
|--------|---------|-------------|
| `Print data packets` | `False` | Log every Protocol 2 packet sent |
| `Show subscription changes` | `True` | Log subscribe/unsubscribe events |
| `Auto-unsubscribe disconnected clients` | `True` | Cleanup on client disconnect |

**Interactive Modification:**
```
Configuration: Print data packets
Enter new value for Print data packets (current: False): true
Updated Print data packets to True
```

### BinanceWss Configurations

```python
configs = {
    BinanceWssConfig.AUTO_DUMP: True,
    BinanceWssConfig.TOTAL_MESSAGE_STATISTICS: True,
    BinanceWssConfig.SHOW_ME_CHARTS: True,  # macOS only
}
```

**Statistics Output:**
```
[STATISTICS] Received 487 messages in the last 10 seconds (avg: 48.70 msgs/sec)
```

## Usage Examples

### Example 1: Basic Crypto Streaming

```python
import socket
from argus.capital._svr_utils import Protocol2Parser

# Connect to Binance dispatcher
s = socket.socket()
s.connect(('localhost', 9982))

# Subscribe to BTC and ETH
s.sendall(b'add=BTCUSDT')
s.sendall(b'add=ETHUSDT')

# Parse Protocol 2 stream
parser = Protocol2Parser([
    'bid', 'bid_size', 'ask', 'ask_size',
    'last', 'last_size', 'timestamp', 'transmission_time'
])

while True:
    data = s.recv(4096)

    # Handle ping
    if data[0] == 36:  # '$'
        data = data[1:]

    result = parser.parse(data)

    # Calculate midpoint
    midpoint = (result['bid'] + result['ask']) / 2
    spread = result['ask'] - result['bid']
    spread_bps = (spread / midpoint) * 10000

    print(f"{result['symbol']}: ${midpoint:,.2f} (spread: {spread_bps:.2f} bps)")
```

### Example 2: Multiple Symbol Tracking

```python
import socket
import time
from argus.capital._svr_utils import Protocol2Parser

s = socket.socket()
s.connect(('localhost', 9982))

# Major crypto pairs
symbols = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'ADAUSDT']
for symbol in symbols:
    s.sendall(f'add={symbol}'.encode())

parser = Protocol2Parser([
    'bid', 'bid_size', 'ask', 'ask_size',
    'last', 'last_size', 'timestamp', 'transmission_time'
])

# Track last update time per symbol
last_update = {}

while True:
    data = s.recv(4096)
    if data[0] == 36:
        data = data[1:]

    result = parser.parse(data)
    symbol = result['symbol']

    # Calculate time since last update
    now = time.time()
    if symbol in last_update:
        latency = now - last_update[symbol]
        print(f"{symbol}: ${result['last']:,.2f} (latency: {latency*1000:.2f}ms)")
    else:
        print(f"{symbol}: ${result['last']:,.2f} (first update)")

    last_update[symbol] = now
```

### Example 3: Spread Alert System

```python
import socket
from argus.capital._svr_utils import Protocol2Parser

s = socket.socket()
s.connect(('localhost', 9982))
s.sendall(b'add=BTCUSDT')

parser = Protocol2Parser([
    'bid', 'bid_size', 'ask', 'ask_size',
    'last', 'last_size', 'timestamp', 'transmission_time'
])

# Alert if spread exceeds threshold
SPREAD_THRESHOLD_BPS = 5  # 5 basis points

while True:
    data = s.recv(4096)
    if data[0] == 36:
        data = data[1:]

    result = parser.parse(data)

    spread = result['ask'] - result['bid']
    midpoint = (result['bid'] + result['ask']) / 2
    spread_bps = (spread / midpoint) * 10000

    if spread_bps > SPREAD_THRESHOLD_BPS:
        print(f"🚨 WIDE SPREAD ALERT: {result['symbol']} spread = {spread_bps:.2f} bps")
        print(f"   Bid: ${result['bid']:,.2f} × {result['bid_size']}")
        print(f"   Ask: ${result['ask']:,.2f} × {result['ask_size']}")
```

### Example 4: Order Book Imbalance

```python
import socket
from argus.capital._svr_utils import Protocol2Parser

s = socket.socket()
s.connect(('localhost', 9982))
s.sendall(b'add=ETHUSDT')

parser = Protocol2Parser([
    'bid', 'bid_size', 'ask', 'ask_size',
    'last', 'last_size', 'timestamp', 'transmission_time'
])

while True:
    data = s.recv(4096)
    if data[0] == 36:
        data = data[1:]

    result = parser.parse(data)

    # Calculate order book imbalance
    bid_volume = result['bid_size']
    ask_volume = result['ask_size']
    total_volume = bid_volume + ask_volume

    if total_volume > 0:
        imbalance = (bid_volume - ask_volume) / total_volume
        direction = "BULLISH" if imbalance > 0 else "BEARISH"
        print(f"{result['symbol']}: {direction} imbalance = {abs(imbalance)*100:.2f}%")
```

### Example 5: Historical Data Replay

```python
import json

# Read auto-dumped data
with open('binance_wss_dump_<UUID>-0.json', 'r') as f:
    messages = json.load(f)

# Replay depth updates
for msg in messages:
    if 'depth@100ms' in msg['stream']:
        symbol = msg['data']['s']
        bids = msg['data']['b']
        asks = msg['data']['a']
        timestamp = msg['received_at']

        if bids and asks:
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            midpoint = (best_bid + best_ask) / 2

            print(f"[{timestamp}] {symbol}: ${midpoint:,.2f}")
```

## Interactive Mode

Launch interactive mode to manage the dispatcher:

```python
from argus.binance import BinanceMKTDispatcher

dispatcher = BinanceMKTDispatcher()
dispatcher.interactive_mode()
```

**Available Commands:**
1. `show_subscriptions` - Display all active symbol subscriptions
2. `show_clients` - Show all connected clients
3. `modify_configs` - Change dispatcher settings

**Example Session:**
```
=== Binance Market Data Dispatcher ===
Choose an action:
1. show_subscriptions
2. show_clients
3. modify_configs

Enter choice: 1

=== Active Subscriptions ===
BTCUSDT: 2 client(s)
ETHUSDT: 1 client(s)
```

## Statistics and Monitoring

The module provides real-time statistics:

**Message Rate:**
```
[STATISTICS] Received 487 messages in the last 10 seconds (avg: 48.70 msgs/sec)
```

**Subscription Changes:**
```
[SUBSCRIBE] New subscription to BTCUSDT
[CLIENT] Added client to BTCUSDT subscription (total: 1)
[CLIENT] Disconnected client removed from BTCUSDT
[UNSUBSCRIBE] No clients for BTCUSDT, cleaned up
```

**Auto-Dump:**
```
[AUTO-DUMP] Dumped 2847 messages to binance_wss_dump_abc123-0.json
```

## Limitations

### 1. Symbol Format

**Constraint:** Must use Binance's exact symbol format.

**Examples:**
- ✅ `BTCUSDT`
- ✅ `ETHUSDT`
- ❌ `BTC/USDT`
- ❌ `BTC-USDT`
- ❌ `btcusdt` (will be auto-converted to uppercase)

### 2. No Historical Data

**Constraint:** Only real-time streaming, no historical candles via dispatcher.

**Workaround:**
- Use auto-dump files for recent history
- Use Binance REST API directly for historical data
- Use TradingView module for historical charts

### 3. K-line Data Not Streamed

**Constraint:** K-line messages received but not converted to Protocol 2.

**Current Behavior:**
- K-line data saved in auto-dump files
- Not transmitted to Protocol 2 clients

**Workaround:**
- Extend `_binance_callback()` to handle `BinanceTypes.KLINE`
- Parse auto-dump files for k-line analysis

### 4. Top-of-Book Only

**Constraint:** Protocol 2 only transmits top bid/ask (Level 1).

**Impact:**
- No full order book depth (Levels 2-N)
- Cannot calculate cumulative volume at price levels

**Workaround:**
- Use raw WebSocket connection for full depth
- Parse auto-dump files for full order book

### 5. Auto-Dump File Size

**Constraint:** JSON files can grow large (30s × 50 msgs/sec = 1500 msgs/file).

**Impact:**
- Disk space usage
- Memory for JSON parsing

**Mitigation:**
- Lower `_max_message_count` threshold
- Increase `_dump_interval`
- Disable auto-dump if not needed
- Implement file rotation/compression

## Troubleshooting

### Cannot Bind to Port 9982

**Symptoms:** `OSError: Address already in use`

**Causes:**
- Port already in use by another process
- Previous dispatcher instance didn't exit cleanly

**Solutions:**
1. Kill existing process: `lsof -i :9982` → `kill <PID>`
2. Use different port: `BinanceMKTDispatcher(port=9983)`
3. Wait 60 seconds for TCP TIME_WAIT

### No Data Received

**Symptoms:** Client connected but no Protocol 2 packets

**Causes:**
- Incorrect symbol format
- Symbol not subscribed
- Binance market closed (rare, 24/7 trading)

**Solutions:**
1. Verify subscription: `dispatcher.show_subscriptions()`
2. Check logs for `[SUBSCRIBE]` messages
3. Test with known-good symbol: `BTCUSDT`

### Ping Byte Confusion

**Symptoms:** Protocol 2 parser errors

**Causes:**
- Ping `$` byte not stripped before parsing

**Solutions:**
```python
data = s.recv(4096)
if data[0] == 36:  # '$'
    data = data[1:]
result = parser.parse(data)
```

### WebSocket Disconnects

**Symptoms:** "WebSocket connection closed" notifications

**Causes:**
- Network issues
- Binance server maintenance
- Rate limiting (too many messages)

**Solutions:**
1. Check network connectivity
2. Review Binance API status page
3. Reduce number of subscriptions
4. Restart dispatcher to reconnect

## File Reference

```
argus/binance/
├── __init__.py      # BinanceWss, BinanceMKTDispatcher
└── _classes.py      # DepthUpdate, AggTradeData, KlineData, Binance_CapitalComMKTDataLive
```

## Summary

The Binance module provides robust, production-grade cryptocurrency market data with the following highlights:

**Strengths:**
- ✅ **TCP transport** for remote access and cross-platform support
- ✅ **Protocol 2** for efficient data streaming
- ✅ **Auto-dump** for historical analysis and debugging
- ✅ **Statistics tracking** for monitoring message rates
- ✅ **Intelligent data merging** (depth + trades)
- ✅ **Multi-client support** with automatic cleanup

**Key Design Choices:**
- **TCP over UDS** - Remote access and Windows compatibility
- **Depth + Trade merging** - Complete market picture
- **Auto-dump by default** - Audit trail and research

**Best For:**
- Cryptocurrency trading systems
- Market microstructure research
- Order book analysis
- Latency-sensitive applications

**Not Suitable For:**
- Full order book depth (Level 2+)
- Historical data retrieval
- K-line analysis via Protocol 2 (use auto-dump files)

For production deployments, consider monitoring disk space usage if auto-dump is enabled and implementing log rotation.
