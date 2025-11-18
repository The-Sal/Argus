# Argus Swift - Binance Market Data Dispatcher

Pure transcompilation of the Argus Binance module from Python to Swift.

## Overview

This is a complete Swift transcompilation of `argus/binance` from the **main branch** of the Python codebase. It provides a market data dispatcher for Binance that:

- Streams real-time market data from Binance WebSocket (combined stream)
- Serves data to clients via TCP using Protocol 2 format
- Merges order book depth + trade data for accurate pricing
- Supports multiple stream types: depth, aggTrade, kline
- Provides interactive monitoring mode with statistics

## Architecture

### Core Components

1. **SocketProtocol.swift** - Socket abstraction layer
   - `ArgusSocket` protocol for polymorphic socket handling
   - `RealSocket` for actual TCP connections
   - `FakeSocket` for internal callbacks (preserves Python pattern)

2. **Protocol2Utils.swift** - Protocol 2 packet encoding/decoding
   - Packet format: `~<length><symbol-length>|<symbol><data>L`
   - CSV-based market data serialization
   - Compatible with Python implementation

3. **MarketData.swift** - Base market data models
   - `CapitalComMKTDataLive` - Capital.com compatible format
   - Protocol 2 transmission support

4. **BinanceClasses.swift** - Binance data structures
   - `DepthUpdate` / `DepthStreamMessage` - Order book data
   - `AggTradeData` / `AggTradeMessage` - Trade data
   - `KlineData` / `KlineMessage` - Candlestick data
   - `Binance_CapitalComMKTDataLive` - Merges depth + trade data

5. **BinanceWebSocket.swift** - Binance WebSocket manager
   - **Single combined stream**: `wss://stream.binance.com/stream`
   - Subscribe/unsubscribe via JSON messages
   - Native URLSession WebSocket implementation
   - Automatic reconnection on failure
   - Message statistics showcase

6. **MKTDispatcher.swift** - TCP market data dispatcher (`BinanceMKTDispatcher`)
   - Multi-client connection management
   - Thread-safe symbol subscription tracking
   - Merges depth (order book) + trade data per symbol
   - Caches market data to combine multiple stream types
   - Protocol 2 packet broadcasting

7. **main.swift** - Entry point (equivalent to runtime.py)
   - Command-line argument parsing
   - Interactive mode with manual subscriptions

## Building

```bash
cd argus_swift
swift build -c release
```

This will produce the executable at:
`.build/release/argus_server`

## Usage

### Basic Usage

```bash
# Run with default settings (localhost:9982 by default to avoid conflicts)
./argus_server binance

# Custom host and port
./argus_server binance --host 0.0.0.0 --port 9982
```

**Note:** The main branch implementation uses the public combined stream endpoint and does not require API credentials or support testnet mode.

## Interactive Mode

After starting, the server enters interactive mode with these options:

1. **Show subscribed symbols** - List all active symbol subscriptions
2. **Show connected clients** - Display client connection count
3. **Toggle packet printing** - Enable/disable data packet logging
4. **Add symbol manually** - Subscribe to a symbol (e.g., BTCUSDT)
5. **Remove symbol manually** - Unsubscribe from a symbol
6. **Toggle live stream display** - Show real-time price updates
0. **Exit** - Stop the server

## Protocol 2 Client Integration

Clients can connect via TCP and subscribe to symbols. The server automatically subscribes to:
- `symbol@depth@100ms` - Order book updates
- `symbol@aggTrade` - Aggregate trade data
- `symbol@kline_1s` - 1-second candlestick data

The dispatcher merges depth (bid/ask from order book) with trade data (last price) and transmits using Protocol 2 format:
```
~<packet-length><symbol-length>|<symbol><bid>,<bid_size>,<ask>,<ask_size>,<last>,<last_size>,<timestamp>,<python_timestamp>L
```

## Design Decisions

### Socket Abstraction Protocol

Following user requirements, all socket operations use the `ArgusSocket` protocol instead of concrete types. This allows:

- Passing both real and fake sockets polymorphically
- Testing without actual network connections
- Internal callbacks without TCP overhead

### Threading Model

- **GCD (Grand Central Dispatch)** for all async operations
- Dedicated queues for client listeners and health checks
- NSLock for thread-safe client/subscription management

### WebSocket Implementation

- **Single combined stream connection**: `wss://stream.binance.com/stream`
- Subscribe/unsubscribe via JSON control messages
- Native URLSession WebSocket
- Automatic reconnection on failure
- Message routing based on stream type (depth@100ms, aggTrade, kline_1s)

### Error Handling

- Swift errors for all failure cases
- Graceful degradation on network failures
- Comprehensive logging for debugging

## Differences from Python Version

### Eliminated Dependencies

- **websocket-client** → Native URLSession WebSockets
- **utils3.runAsThread** → GCD async dispatch
- No external Swift packages needed

### Platform Support

- **macOS only** (due to URLSession WebSocket requirements)
- Linux support would require alternative WebSocket library

### Improvements Over Python

- **Type safety** - Strong typing with compile-time checks
- **Memory safety** - ARC instead of GC
- **Single connection** - Combined stream approach is more efficient
- **Performance** - Compiled binary, no interpreter overhead
- **Data merging** - Properly combines depth + trade data per symbol

## Testing

To test the server:

1. Start the server:
   ```bash
   ./argus_server binance --testnet
   ```

2. In interactive mode, add a symbol:
   ```
   Select option: 4
   Enter symbol to add: BTCUSDT
   ```

3. Connect a client and subscribe:
   ```bash
   echo "add=BTCUSDT" | nc localhost 9974
   ```

4. The client will receive Protocol 2 market data packets

## Troubleshooting

### "Cannot reach Binance production endpoint"

**Solution:** Use testnet mode:
```bash
./argus_server binance --testnet
```

### "Connection refused" from client

**Cause:** Server not started or wrong port

**Solution:** Verify server is running and check port:
```bash
lsof -i :9974
```

### No data received after subscription

**Cause:** First-subscription bug or network issues

**Solution:** The server automatically retries up to 5 times. Check logs for connectivity errors.

## License

Same as parent Argus project.

## Credits

Transcompiled from the original Python implementation by Claude.
