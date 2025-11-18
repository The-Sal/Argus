# Argus Swift - Binance Market Data Dispatcher

Pure transcompilation of the Argus Binance module from Python to Swift.

## Overview

This is a complete Swift transcompilation of `argus/binance` from the original Python codebase. It provides a market data dispatcher for Binance that:

- Streams real-time market data from Binance WebSockets
- Serves data to clients via TCP using Protocol 2 format
- Supports both production and testnet endpoints
- Provides interactive monitoring mode

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

3. **MarketData.swift** - Market data models
   - `CapitalComMKTDataLive` - Capital.com compatible format
   - `BinanceMarketData` - Binance ticker data structure

4. **BinanceWebSocket.swift** - Binance WebSocket manager
   - Native URLSession WebSocket implementation
   - Automatic reconnection and retry logic
   - Sacrificial subscription to work around first-message bug

5. **MKTDispatcher.swift** - TCP market data dispatcher
   - Multi-client connection management
   - Thread-safe symbol subscription tracking
   - Protocol 2 packet broadcasting

6. **main.swift** - Entry point (equivalent to runtime.py)
   - Command-line argument parsing
   - Environment variable configuration
   - Interactive mode

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
# Run with default settings (localhost:9974)
./argus_server binance

# Use Binance testnet
./argus_server binance --testnet

# Custom host and port
./argus_server binance --host 0.0.0.0 --port 9974
```

### With API Credentials (Optional)

```bash
export BINANCE_API_KEY="your_api_key"
export BINANCE_API_SECRET="your_api_secret"
./argus_server binance
```

**Note:** API credentials are only needed for private endpoints. Public market data works without authentication.

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

Clients can connect via TCP and send commands:

```
add=BTCUSDT       # Subscribe to BTCUSDT ticker
remove=BTCUSDT    # Unsubscribe from BTCUSDT
```

The server will stream market data using Protocol 2 format:
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

- **Native URLSession** instead of external dependencies
- Automatic retry with exponential backoff
- Sacrificial first subscription to work around Binance API quirk

### Error Handling

- Swift errors for all failure cases
- Graceful degradation on network failures
- Comprehensive logging for debugging

## Differences from Python Version

### Eliminated Dependencies

- **python-binance** → Native URLSession WebSockets
- **utils3.runAsThread** → GCD async dispatch
- **dotenv** → Direct ProcessInfo environment access

### Platform Support

- **macOS only** (due to URLSession WebSocket requirements)
- Linux support possible with different WebSocket library

### Improvements

- **Type safety** - Strong typing throughout
- **Memory safety** - ARC instead of GC
- **Concurrency** - Modern Swift concurrency patterns
- **Performance** - Compiled binary, no interpreter overhead

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
