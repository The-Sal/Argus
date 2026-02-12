# Binance Module

The Binance module provides real-time cryptocurrency market data through Binance's WebSocket API with automatic message collection, filtering, and multi-client distribution.

## Table of Contents

- [Overview](#overview)
- [Dispatcher](#dispatcher)
- [Features](#features)
- [API Surface](#api-surface)
- [Internal Architecture](#internal-architecture)
- [Client Usage](#client-usage)
- [Protocols](#protocols)
- [Usage Examples](#usage-examples)

## Overview

The Binance module follows the Argus dispatcher paradigm, providing a TCP socket server that bridges between Binance's WebSocket API and multiple clients. Unlike the IB module (stocks) or Capital.com (CFDs), Binance specializes in cryptocurrency trading with support for multiple message types (book tickers, aggregated trades, klines, and depth updates).

**Location:** `/argus/binance/`

**Primary Files:**
- `__init__.py` (536 lines) - BinanceWss WebSocket client and BinanceMKTDispatcher
- `_classes.py` - Data classes for market data messages (DepthUpdate, BookTicker, KlineMessage, etc.)

**Key Features:**
- TCP socket for remote client connections
- Multiple WebSocket message types (4 streams per symbol)
- Automatic message collection and statistics
- Protocol 2 normalization for market data
- Interactive CLI dispatcher management
- Health checking and auto-cleanup of disconnected clients

## Dispatcher

### BinanceMKTDispatcher - `binance.core`

**Purpose:** Real-time cryptocurrency market data from Binance

**Transport:** TCP socket (default port: 9982)

**Multiplexing:** ✅ Yes (one WebSocket shared across multiple clients)

**Data Protocol:** Protocol 2

**Concurrent Clients:** ✅ Full support

**Auto-subscription:** ✅ Yes - subscribes to Binance on first client request, unsubscribes when no clients remain

**Launch:**
```bash
python3 runtime.py binance.core
```

## Features

### Feature Matrix

| Feature | Support | Notes |
|---------|---------|-------|
| Real-time market data | ✅ | Book ticker (best bid/ask) via WebSocket |
| Multi-stream per symbol | ✅ | aggTrade, depth@100ms, kline_1s, bookTicker |
| Multi-client support | ✅ | TCP allows multiple concurrent connections |
| Protocol 2 streaming | ✅ | Market data normalization |
| Automatic subscription | ✅ | Subscribe on client request, unsubscribe on disconnect |
| Message statistics | ✅ | Real-time throughput monitoring |
| Auto-dump to file | ✅ | Periodic JSON dumps of raw WebSocket messages |
| Health checking | ✅ | Periodic ping/cleanup of disconnected clients |
| Interactive CLI | ✅ | Runtime configuration and monitoring |
| Automatic reconnection | ✅ | Reconnects to Binance on WebSocket close |

### WebSocket Streams

Each subscribed symbol receives **4 concurrent streams** from Binance:

| Stream | Data | Update Rate |
|--------|------|-------------|
| `@aggTrade` | Price, quantity, trade ID | Real-time |
| `@depth@100ms` | Bids/asks (up to 100 levels) | Every 100ms |
| `@kline_1s` | OHLCV + trade count | Every 1 second |
| `@bookTicker` | Best bid/ask prices and sizes | Every 100ms |

**Currently Used:** Only `@bookTicker` is actively processed for Protocol 2 transmission. Other streams are collected but not forwarded to clients.

## API Surface

### Subscription Commands

Clients send simple text commands to subscribe:

```
add=SYMBOL
```

Example: `add=BTCUSDT`

Symbols are case-insensitive and automatically converted to lowercase for Binance.

### Market Data Reception

Once subscribed, market data flows continuously in Protocol 2 format (see [Protocols](#protocols) section).

## Internal Architecture

### Component Stack

```
[Binance WebSocket Server]
         ↓
    [BinanceWss]
         ↓
[Message Routing]
    ↓              ↓              ↓              ↓
[aggTrade]  [depth@100ms]  [kline_1s]  [bookTicker]
                                              ↓
                                 [Binance_CapitalComMKTDataLive]
                                              ↓
                                    [Protocol 2 Encoding]
                                              ↓
                                      [BinanceMKTDispatcher]
                                              ↓
                                          [TCP Socket]
                                              ↓
                                        [Multiple Clients]
```

### Class Hierarchy

```
BinanceWss (WebSocket handler)
    ├── Manages WebSocket connection to Binance
    ├── Parses incoming JSON messages
    ├── Maintains callbacks per symbol
    └── Handles reconnection logic

BinanceMKTDispatcher (TCP server)
    ├── Manages client connections
    ├── Handles subscription requests
    ├── Converts market data to Protocol 2
    ├── Auto-cleans disconnected clients
    └── Provides interactive configuration

Binance_CapitalComMKTDataLive (Data class)
    ├── Extends CapitalComMKTDataLive
    ├── Stores bid/ask/last price data
    └── Converts to Protocol 2 format
```

### Threading Model

1. **Main Thread** - TCP server accept loop
2. **WebSocket Thread** - Binance WebSocket `run_forever()` loop
3. **Client Handler Thread(s)** - One per connected client (listens for commands)
4. **Client Health Check Thread** - Periodically pings clients
5. **Auto-Dump Thread** - Periodically writes messages to JSON files
6. **Statistics Thread** - Periodic throughput reporting

**Thread Safety:**
- Client list protected by `_thread_lock`
- Symbol-to-clients mapping protected by `_thread_lock`
- Callbacks are atomic operations
- WebSocket message processing is single-threaded (per symbol)

### Data Classes

#### BookTicker

Best bid/ask from Binance:

```python
@dataclass
class BookTicker:
    u: int          # Order book updateId
    s: str          # Symbol (e.g., "BTCUSDT")
    b: Decimal      # Best bid price
    B: Decimal      # Best bid quantity
    a: Decimal      # Best ask price
    A: Decimal      # Best ask quantity
```

#### DepthUpdate

Order book depth snapshot:

```python
@dataclass
class DepthUpdate:
    e: str          # Event type ("depthUpdate")
    E: int          # Event time (milliseconds)
    s: str          # Symbol
    U: int          # First update ID
    u: int          # Final update ID
    b: list         # Bids [price, qty]
    a: list         # Asks [price, qty]
```

**Note:** This is NOT the full order book snapshot, only the incremental update.

#### KlineMessage

1-second candlestick data:

```python
@dataclass
class KlineMessage:
    stream: str          # Stream identifier
    data: KlineEventData # Kline data with OHLCV
    received_at: float   # Reception timestamp
```

#### AggTradeMessage

Aggregated trades:

```python
@dataclass
class AggTradeMessage:
    stream: str        # Stream identifier
    data: AggTradeData # Trade details
    received_at: float # Reception timestamp
```

#### Binance_CapitalComMKTDataLive

Market data object compatible with Protocol 2:

```python
class Binance_CapitalComMKTDataLive(CapitalComMKTDataLive):
    symbol: str      # Trading pair
    bid: float       # Best bid price
    bid_size: float  # Best bid quantity
    ask: float       # Best ask price
    ask_size: float  # Best ask quantity
    last: float      # Last traded price
    last_size: float # Last trade quantity
    timestamp: int   # Server timestamp (ms)
```

### Symbol Data State

The dispatcher maintains current market data for each subscribed symbol:

```python
self.symbol_data_cache = {}  # Maps symbol -> Binance_CapitalComMKTDataLive
```

Each subscribed symbol holds:
- Latest bid/ask from book ticker
- Most recent trade price
- Timestamp information

**Lifecycle:**
1. First client subscribes → Create entry
2. Market data arrives → Update with full packets (not deltas)
3. Last client unsubscribes → Remove entry
4. Unsubscribe from Binance WebSocket

## Client Usage

### Raw TCP Socket (Recommended)

Direct TCP connection to dispatcher:

```python
import socket
from argus.protocol import Protocol2Parser

# Connect to dispatcher
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect(('localhost', 9982))

# Subscribe to symbols
s.sendall(b'add=BTCUSDT')
s.sendall(b'add=ETHUSDT')

# Parse Protocol 2 stream
parser = Protocol2Parser([
    'bid', 'bid_size', 'ask', 'ask_size',
    'last', 'last_size', 'timestamp', 'transmission_time'
])

while True:
    data = s.recv(4096)
    result = parser.parse(data)
    print(f"{result['symbol']}: bid={result['bid']}, ask={result['ask']}")

s.close()
```

### Interactive CLI

The dispatcher can be configured interactively:

```bash
# Start dispatcher (press 'i' for interactive mode)
python3 runtime.py binance.core

# Available commands:
# show_subscriptions - View all active symbol subscriptions
# show_clients - View all connected clients
# modify_configs - Change runtime settings
```

**Runtime Configurations:**
- `Print data packets` - Enable/disable Protocol 2 packet logging
- `Show subscription changes` - Log when clients subscribe/unsubscribe
- `Auto-unsubscribe disconnected clients` - Auto-cleanup (default: True)

## Protocols

### Subscription Commands

**Format:**
```
add=SYMBOL
```

Where `SYMBOL` is a Binance trading pair (case-insensitive).

**Examples:**
```
add=BTCUSDT
add=ETHUSDT
add=bnbusdt
```

**Processing:**
- Symbol is converted to uppercase for Binance API
- Duplicate subscriptions are ignored (client added once)
- Invalid symbols cause silent rejection with warning log

### Protocol 2: Market Data Stream

Real-time market updates are transmitted in Protocol 2 format:

```
~<packet-length><symbol-length>|<symbol><csv-data>L

Where:
  ~: Start marker
  <packet-length>: 4-byte ASCII integer (total packet size excluding header)
  <symbol-length>: 4-byte ASCII integer
  |: Delimiter
  <symbol>: Trading pair (uppercase ASCII)
  <csv-data>: 8 comma-separated fields
  L: Terminator
```

**CSV Fields (in order):**
1. `bid` (float) - Best bid price
2. `bid_size` (float) - Best bid quantity
3. `ask` (float) - Best ask price
4. `ask_size` (float) - Best ask quantity
5. `last` (float) - Last traded price (from prior trade data)
6. `last_size` (float) - Last trade quantity
7. `timestamp` (int) - Binance server timestamp (milliseconds)
8. `transmission_time` (int) - Dispatcher transmission timestamp (milliseconds)

**Example:**
```
~01050007|BTCUSDT95124.50,0.150,95125.00,0.200,94567.89,0.500,1702345678900,1702345678950L
```

**Parsing:**
```python
from argus.protocol import Protocol2Parser

parser = Protocol2Parser([
    'bid', 'bid_size', 'ask', 'ask_size',
    'last', 'last_size', 'timestamp', 'transmission_time'
])

result = parser.parse(packet)
# {
#   'symbol': 'BTCUSDT',
#   'bid': 95124.50,
#   'bid_size': 0.150,
#   'ask': 95125.00,
#   'ask_size': 0.200,
#   'last': 94567.89,
#   'last_size': 0.500,
#   'timestamp': 1702345678900,
#   'transmission_time': 1702345678950
# }
```

## Usage Examples

### Basic Cryptocurrency Streaming

```python
import socket
from argus.protocol import Protocol2Parser

# Connect to dispatcher
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect(('localhost', 9982))

# Subscribe to symbols
symbols = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT']
for symbol in symbols:
    s.sendall(f'add={symbol}'.encode())

# Parse incoming data
parser = Protocol2Parser([
    'bid', 'bid_size', 'ask', 'ask_size',
    'last', 'last_size', 'timestamp', 'transmission_time'
])

while True:
    try:
        data = s.recv(4096)
        if not data:
            break

        result = parser.parse(data)
        spread = result['ask'] - result['bid']
        midpoint = (result['ask'] + result['bid']) / 2

        print(f"{result['symbol']}: ${midpoint:.2f} (spread: ${spread:.4f})")

    except KeyboardInterrupt:
        break

s.close()
```

## Environment Variables

```bash
# None required for basic operation
# The dispatcher uses Binance public WebSocket (wss://stream.binance.com/stream)
# No API keys needed for read-only market data
```

## Troubleshooting

### Cannot Connect to Dispatcher

**Symptoms:** `Connection refused` when connecting to port 9982

**Solutions:**
1. Start dispatcher: `python3 runtime.py binance.core`
2. Verify dispatcher is listening: `lsof -i :9982`
3. Check firewall: `sudo iptables -L` (Linux)

### WebSocket Connection Drops

**Symptoms:** Logs show `WebSocket connection closed: 1000`

**Solutions:**
1. Check network: `ping -c 5 stream.binance.com`
2. Dispatcher auto-reconnects (see logs)
3. Re-subscribe clients after reconnection
4. Monitor `[STATISTICS]` messages for throughput

### Market Data Not Received

**Symptoms:** Connected but no Protocol 2 packets received

**Solutions:**
1. Send subscribe command: `s.sendall(b'add=BTCUSDT')`
2. Check symbol format in Binance (e.g., `BTCUSDT` not `BTC/USDT`)
3. Verify port: `netstat -an | grep 9982`
4. Check logs: `[CLIENT] Subscribing to SYMBOL`

### Protocol 2 Parsing Errors

**Symptoms:** `ValueError: Invalid data length for protocol 2`

**Solutions:**
1. Implement retry logic with packet validation
2. Check for `~` start marker and `L` terminator
3. Enable `Print data packets` in interactive mode for debugging

## File Reference

```
argus/binance/
├── __init__.py           # BinanceWss, BinanceMKTDispatcher
└── _classes.py           # Data classes for market data types
```
