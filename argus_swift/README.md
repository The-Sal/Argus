# Argus Swift - Market Data Dispatcher

Pure Swift transcompilation of Argus market data streaming modules.

## Overview

This is an ongoing effort to transcompile the Argus Python codebase to Swift. Currently implemented:

- ✅ **Binance** - Complete market data dispatcher from `argus/binance` (main branch)
- ⬜ **Capital.com** - Pending
- ⬜ **OANDA** - Pending

## Features

- **Protocol 2 streaming** - TCP-based market data transmission in Argus Protocol 2 format
- **Real-time WebSocket** - Direct connection to exchange WebSocket streams
- **Multi-client support** - Multiple TCP clients can subscribe to different symbols
- **Data merging** - Combines order book depth + trade data into unified market data
- **Thread-safe** - Concurrent client handling with proper locking
- **Zero dependencies** - Native Swift/Foundation only (URLSession WebSockets)

## Quick Start

### Build

```bash
cd argus_swift
swift build -c release
```

Executable: `.build/release/argus_server`

### Run Binance Dispatcher

```bash
# Default settings (localhost:9982)
.build/release/argus_server binance

# Custom host/port
.build/release/argus_server binance --host 0.0.0.0 --port 9982
```

### Connect a Client

```python
import socket

sock = socket.socket()
sock.connect(('localhost', 9982))

# Subscribe to symbol
sock.send(b'add=BTCUSDT\n')

# Receive Protocol 2 packets
while True:
    data = sock.recv(4096)
    print(repr(data))
```

## Architecture

### Binance Module

**Components:**

1. **BinanceWebSocket.swift** - Single combined stream WebSocket (`wss://stream.binance.com/stream`)
2. **MKTDispatcher.swift** - TCP server managing client connections and subscriptions
3. **BinanceClasses.swift** - Data structures for depth, trade, kline messages
4. **SocketProtocol.swift** - Socket abstraction (RealSocket + FakeSocket pattern)
5. **Protocol2Utils.swift** - Protocol 2 packet encoding/decoding
6. **MarketData.swift** - Base market data models

**Data Flow:**

```
Binance WebSocket (wss://stream.binance.com/stream)
    ↓
Subscribe: symbol@depth@100ms, symbol@aggTrade, symbol@kline_1s
    ↓
Message Routing (by stream type)
    ↓
Data Merging (depth + trade → unified market data)
    ↓
Protocol 2 Encoding
    ↓
Broadcast to subscribed TCP clients
```

**Key Design Patterns:**

- **Socket abstraction** - `ArgusSocket` protocol allows polymorphic handling of real and fake sockets
- **Data merging** - Caches market data to combine depth (order book) and trade streams
- **Lazy subscription** - Only subscribes to Binance when first client requests a symbol
- **Thread safety** - NSLock protects shared state across multiple async tasks

See **[ARCHITECTURE.md](./ARCHITECTURE.md)** for detailed developer documentation.

## Interactive Mode

After starting, the server enters interactive mode:

```
Options:
1. Show subscribed symbols
2. Show connected clients
3. Toggle packet printing
4. Add symbol manually
5. Remove symbol manually
0. Exit
```

**Example:**
```
Select option: 4
Enter symbol to add: BTCUSDT
Successfully subscribed to BTCUSDT
```

Manual subscriptions use a FakeSocket that receives data internally without TCP overhead.

## Protocol 2 Format

Market data is transmitted using Protocol 2:

```
~<packet-length><symbol-length>|<symbol><csv-data>L
```

**Example packet:**
```
~47|7|BTCUSDT98450.5,10.2,98451.0,8.5,98450.75,0.5,1234567890,1234567890.123L
```

**CSV Fields:** `bid,bidSize,ask,askSize,last,lastSize,timestamp,pythonTimestamp`

### Parsing Protocol 2 (Python example)

```python
def parse_protocol2(packet):
    # Extract header: ~<length><symbol_len>|
    header_end = packet.index(b'|')
    header = packet[1:header_end].decode('ascii')

    parts = header.split('|')
    packet_length = int(parts[0])
    symbol_length = int(parts[1]) if len(parts) > 1 else int(header[len(parts[0]):])

    # Extract symbol
    symbol_start = header_end + 1
    symbol_end = symbol_start + symbol_length
    symbol = packet[symbol_start:symbol_end].decode('ascii')

    # Extract CSV data (up to 'L' terminator)
    data = packet[symbol_end:-1].decode('ascii')
    fields = data.split(',')

    return {
        'symbol': symbol,
        'bid': float(fields[0]),
        'bid_size': float(fields[1]),
        'ask': float(fields[2]),
        'ask_size': float(fields[3]),
        'last': float(fields[4]),
        'last_size': float(fields[5]),
        'timestamp': int(fields[6]),
        'python_timestamp': float(fields[7])
    }
```

## Client Protocol

### Subscribe to Symbol

Send: `add=SYMBOL\n` (e.g., `add=BTCUSDT\n`)

Server subscribes to Binance streams:
- `symbol@depth@100ms` - Order book updates
- `symbol@aggTrade` - Aggregate trades
- `symbol@kline_1s` - 1-second candlesticks

Server begins streaming Protocol 2 packets for that symbol.

### Unsubscribe from Symbol

Send: `remove=SYMBOL\n` (e.g., `remove=BTCUSDT\n`)

Server stops sending data for that symbol to this client. If no other clients are subscribed, server unsubscribes from Binance.

## Platform Support

- ✅ **macOS** - Full support (uses URLSession WebSockets)
- ⚠️ **Linux** - Requires alternative WebSocket library (URLSession WebSockets are macOS-only)

## Development

### For Contributors

See **[ARCHITECTURE.md](./ARCHITECTURE.md)** for:
- Detailed design pattern explanations
- Data structure reference
- Step-by-step guide to adding new exchange modules
- Testing strategies
- Common code patterns

### Project Structure

```
argus_swift/
├── Package.swift                    # Swift package manifest
├── README.md                        # This file
├── ARCHITECTURE.md                  # Developer guide
└── Sources/
    └── ArgusServer/
        ├── main.swift               # Entry point
        ├── MKTDispatcher.swift      # Binance TCP dispatcher
        ├── BinanceWebSocket.swift   # Binance WebSocket manager
        ├── BinanceClasses.swift     # Binance data structures
        ├── SocketProtocol.swift     # Socket abstraction
        ├── Protocol2Utils.swift     # Protocol 2 encoding/decoding
        └── MarketData.swift         # Base market data models
```

### Adding a New Exchange

1. Read `ARCHITECTURE.md` - "Adding New Modules" section
2. Create `<Exchange>Classes.swift` for data structures
3. Create `<Exchange>WebSocket.swift` for WebSocket manager
4. Create `<Exchange>Dispatcher.swift` for TCP dispatcher
5. Update `main.swift` to support new exchange
6. Follow the Binance pattern for consistency

Example: See ARCHITECTURE.md for complete Capital.com transcompilation guide.

## Differences from Python

### Advantages

- **Type safety** - Compile-time checks prevent runtime errors
- **Performance** - Native compiled binary, no interpreter overhead
- **Memory safety** - ARC prevents memory leaks
- **Concurrency** - GCD provides efficient async task management

### Tradeoffs

- **Less dynamic** - Protocol-based architecture needed for Python's duck typing
- **Platform limits** - URLSession WebSockets are macOS-only (for now)
- **Larger binary** - Compiled executable vs Python scripts

### Implementation Notes

- **No python-binance** - Direct WebSocket connection to Binance combined stream
- **No websocket-client** - Native URLSession WebSockets
- **No @runAsThread** - GCD async dispatch queues
- **No external packages** - Pure Swift/Foundation

## Testing

### Manual Test

Terminal 1:
```bash
.build/release/argus_server binance
```

Terminal 2:
```python
import socket
sock = socket.socket()
sock.connect(('localhost', 9982))
sock.send(b'add=BTCUSDT\n')
print(sock.recv(4096))  # Should receive Protocol 2 packets
```

### Expected Output

Server terminal:
```
[BinanceMKTDispatcher] Initialized on localhost:9982
[IMPORTANT] MODE = PROTOCOL_2
[BinanceMKTDispatcher] Client connected from fd=5
[CLIENT] Request: add=BTCUSDT
[SUBSCRIBE] New subscription to BTCUSDT
[TX BTCUSDT] ~47|7|BTCUSDT98450.5,10.2,98451.0,8.5,98450.75,0.5,...L
```

Client terminal:
```
b'~47|7|BTCUSDT98450.5,10.2,98451.0,8.5,98450.75,0.5,1234567890,1234567890.123L'
```

## Troubleshooting

### Port already in use

```bash
# Check what's using the port
lsof -i :9982

# Use different port
.build/release/argus_server binance --port 9983
```

### No data received

1. Check WebSocket connection in server logs
2. Ensure symbol is valid (e.g., `BTCUSDT` not `BTC-USDT`)
3. Toggle packet printing in interactive mode (option 3)
4. Check Binance status: https://www.binance.com/en/support/announcement

### Build errors

```bash
# Clean and rebuild
rm -rf .build
swift build -c release
```

## License

Same as parent Argus project.

## Credits

Transcompiled from Python Argus implementation (main branch).
