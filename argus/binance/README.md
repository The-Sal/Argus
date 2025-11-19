# Binance Market Data Dispatcher

A real-time market data dispatcher for Binance, compatible with the Argus trading framework.

## Features

- **Real-time WebSocket Streaming**: Uses `python-binance` ThreadedWebsocketManager for efficient data streaming
- **Protocol 2 Compatible**: Transmits data using Protocol 2 format, compatible with existing Argus clients
- **TCP Server**: Manages multiple client connections via TCP sockets
- **Auto Subscription Management**: Automatically subscribes/unsubscribes based on client connections
- **Progress Checkpointing**: Sends progress notifications to configured endpoint

## Architecture

```
┌─────────────────────────────────────────┐
│         BinanceWss                      │
│  (WebSocket Manager)                    │
│                                         │
│  - Manages Binance WebSocket streams    │
│  - Handles ticker subscriptions         │
│  - Normalizes market data               │
└────────────┬────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────┐
│         MKTDispatcher                   │
│  (TCP Server & Client Manager)          │
│                                         │
│  - Listens for client connections       │
│  - Manages subscriptions per client     │
│  - Broadcasts data via Protocol 2       │
│  - Auto cleanup on disconnect           │
└────────────┬────────────────────────────┘
             │
             ▼
        [Clients via TCP]
```

## Installation

Ensure you have `python-binance` installed:

```bash
pip install python-binance==1.0.32
```

## Configuration

**Note:** API credentials are **optional** for public market data streams (ticker, depth, trades). They are only required for authenticated endpoints (user data, account information).

Set environment variables in your `.env` file (optional):

```bash
# Optional: Only needed for authenticated endpoints
BINANCE_API_KEY=your_api_key_here
BINANCE_API_SECRET=your_api_secret_here
```

The dispatcher works perfectly fine without credentials for streaming public market data.

## Usage

### Using runtime.py

The easiest way to run the Binance dispatcher:

```bash
# Production (default port 9974)
python runtime.py binance

# With custom host/port
python runtime.py binance --host 0.0.0.0 --port 9975

# Using testnet
python runtime.py binance --testnet

# All options
python runtime.py binance --host localhost --port 9974 --testnet
```

### Standalone Usage

```python
from argus.binance import MKTDispatcher

dispatcher = MKTDispatcher(
    host='localhost',
    port=9974,
    api_key=None,  # Optional
    api_secret=None,  # Optional
    testnet=False
)

dispatcher.start()
dispatcher.interactive_mode()
```

## Client Protocol

Clients connect via TCP and send commands:

### Subscribe to Symbol
```
add=BTCUSDT
```

### Unsubscribe from Symbol
```
remove=BTCUSDT
```

### Data Format (Protocol 2)

Market data is transmitted using Protocol 2 format:

```
~<data-length><symbol-length>|{symbol}{data}L
```

Where data contains:
- bid
- bid_size
- ask
- ask_size
- last
- last_size
- timestamp
- python_timestamp

### Example Client (Python)

```python
import socket
import struct

# Connect to dispatcher
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect(('localhost', 9974))

# Subscribe to BTCUSDT
sock.sendall(b'add=BTCUSDT')

# Receive data
while True:
    # Protocol 2 format: ~<4-byte data len><4-byte symbol len>|{symbol}{data}L
    header = sock.recv(1)  # '~'

    data_len_bytes = sock.recv(4)
    data_len = struct.unpack('>I', data_len_bytes)[0]

    symbol_len_bytes = sock.recv(4)
    symbol_len = struct.unpack('>I', symbol_len_bytes)[0]

    pipe = sock.recv(1)  # '|'

    symbol = sock.recv(symbol_len).decode('ascii')
    data = sock.recv(data_len - symbol_len - 1).decode('ascii')

    end_marker = sock.recv(1)  # 'L'

    values = data.split(',')
    print(f"{symbol}: bid={values[0]}, ask={values[2]}, last={values[4]}")
```

## Interactive Mode

The dispatcher includes an interactive mode for monitoring:

```
Options:
1. Show subscribed symbols
2. Show connected clients
3. Toggle packet printing
0. Exit
```

## Classes

### BinanceMarketData

Normalized market data object:

```python
class BinanceMarketData:
    symbol: str
    bid: float
    bid_qty: float
    ask: float
    ask_qty: float
    last: float
    last_qty: float
    timestamp: int
```

### BinanceWss

WebSocket manager:

```python
ws = BinanceWss(api_key=None, api_secret=None, testnet=False)
ws.start()
ws.subscribe_ticker('BTCUSDT', callback=my_callback)
ws.unsubscribe_ticker('BTCUSDT')
ws.stop()
```

### MKTDispatcher

Main dispatcher class:

```python
dispatcher = MKTDispatcher(
    host='localhost',
    port=9974,
    api_key=None,
    api_secret=None,
    testnet=False,
    checkpoint_url="https://your-endpoint/finished"
)

dispatcher.start()  # Starts WebSocket and TCP server
dispatcher.interactive_mode()  # Enter interactive monitoring
```

## Checkpointing

The dispatcher sends progress notifications to a configured endpoint:

```json
POST https://sals-macbook-pro.tail34e8af.ts.net/finished
{
    "task_name": "MKTDispatcher.__init__",
    "status": "complete"
}
```

Checkpoint events:
- `MKTDispatcher.__init__` - Initialization
- `MKTDispatcher.start` - Startup
- `MKTDispatcher.add_symbol({symbol})` - Symbol subscription

## Supported Symbols

All Binance spot trading pairs, e.g.:
- BTCUSDT
- ETHUSDT
- BNBUSDT
- etc.

Symbol format: `{BASE}{QUOTE}` (e.g., BTCUSDT = BTC/USDT)

## Comparison with IB MKTDispatcher

| Feature | Binance | IB |
|---------|---------|-----|
| WebSocket Library | python-binance | Custom |
| Protocol Support | Protocol 2 | Protocol 2, ASK, FULL_PKL, etc. |
| Account Data | No (market data only) | Yes (via AccountProvider) |
| Complexity | Simpler | More complex |
| Data Source | Binance API | Interactive Brokers |

## Limitations

- Market data only (no account/order management)
- Spot markets only (no futures/options yet)
- No historical data (real-time only)
- Rate limits: 10 messages/second per connection, max 1024 streams

## Known Issues

### Production WebSocket Connection Errors

Some users may experience "Connection reset by peer" errors when using `testnet=False` (production) while testnet works fine. This is typically due to:

- **Network/Firewall restrictions**: Production endpoints (`stream.binance.com`) may be blocked
- **Regional access limitations**: Some regions have restricted access to Binance production
- **Rate limiting**: Production has stricter rate limits than testnet

**Workarounds:**
1. Use testnet for development/testing: `python runtime.py binance --testnet`
2. Check firewall settings to allow connections to `stream.binance.com:9443`
3. Try from a different network or use a VPN
4. Contact your network administrator if behind corporate firewall

## Future Enhancements

- [ ] Futures market support
- [ ] Account balance streaming
- [ ] Order book depth streams
- [ ] Kline/candlestick streams
- [ ] Multiple stream aggregation
- [ ] Historical data support

## License

Part of the Argus trading framework.
