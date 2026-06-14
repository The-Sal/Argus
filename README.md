# Argus

**Argus** aims to be a high-performance financial market data aggregation system built around a server-client architecture. It provides unified access to multiple financial data sources through a custom Protocol 2 (P2) binary protocol for efficient, standardized data transmission.

----

## Overview

Argus connects to major financial data providers (Interactive Brokers, Capital.com, Binance, TradingView, NASDAQ, Polymarket) and provides a unified interface for consuming real-time and historical market data.

### Core Design Principles

1. **Server-Client Architecture** - Dispatchers run as servers, clients connect via TCP/UDS
2. **Dispatcher Paradigm** – Most modules follow a unified dispatcher pattern for consistency
3. **Protocol 2 Normalization** – Standardized binary protocol across all dispatcher-based modules
4. **Multi-Client Support** - Single data stream multiplexed to multiple consuming processes
5. **Language Agnostic** – Clients can be written in any language with socket support

## Architecture

### Server-Client Design

Argus is designed as a **server-client architecture** centered around `runtime.py`. The workflow:

1. **Spin up Argus**: Launch a dispatcher server for your chosen data source
2. **Connect via TCP/UDS**: Connect trading algorithms, data pipelines, or analysis tools
3. **Stream real-time data**: Receive normalized market data in Protocol 2 format

```bash
# Start a dispatcher server
python3 runtime.py ib.core --port 9972

# Connect from any client (Python, C++, Rust, etc.)
import socket
s = socket.socket()
s.connect(('localhost', 9972))
s.sendall(b'add=AAPL')  # Subscribe to AAPL
```

**Benefits:**
- **Multi-client support** - Multiple processes consume the same data stream
- **Language agnostic** - Clients can be any language with socket support
- **Resource efficiency** - Single WebSocket connection shared across clients
- **Fault isolation** - Client crashes don't affect dispatcher or other clients

### Alternative: python3 Module Import

While Argus **can** be imported like any other python3 module, this is **not the primary design pattern**:

```python
# Supported but not recommended for production
from argus.ib import MKTDispatcher
from argus.ib.forecast import FXCDispatcher
wss = IBWss(token="...")
wss.subscribe_to_contracts([...])
```

The server-client architecture is preferred because:
- Better separation of concerns
- Enables polyglot systems (python3 server, C++ trading engine)
- Simplifies deployment and monitoring
- Reduces memory overhead (shared data streams)

## The Dispatcher Paradigm

**Most modules in Argus follow the dispatcher paradigm** (exceptions: TradingView and NASDAQ modules use different architectures).
- **TradingView** - Direct callback-based client library
- **NASDAQ** - Historical data scraper (not real-time)
- **Polymarket** - Has both dispatcher (`polymarket`) and direct client (`polymarket_direct`) modes

### What is a Dispatcher?

A **Dispatcher** is a server that:

1. **Connects** to a financial data source (WebSocket/REST API)
2. **Listens** for client connections (TCP or Unix Domain Sockets)
3. **Subscribes** to market data based on client requests
4. **Converts** incoming data to Protocol 2 format
5. **Multiplexes** data to all subscribed clients
6. **Manages** subscription lifecycle (auto-unsubscribe when no clients remain)

### Available Dispatchers

| Dispatcher | Data Source | Transport | Port/Socket | Docs |
|------------|-------------|-----------|-------------|------|
| `ib.core` | Interactive Brokers | TCP | 9972 | [IB.md](docs/IB.md) |
| `ib.forecast` | IB Forecasting Contracts | TCP | 9972 | [IB.md](docs/IB.md) |
| `capital.com` | Capital.com | Unix Socket | `/tmp/argus_capital.sock` | [CAPITAL.md](docs/CAPITAL.md) |
| `binance` | Binance | TCP | 9982 | [BINANCE.md](docs/BINANCE.md) |
| `polymarket` | Polymarket | TCP | 9972 | [POLYMARKET.md](docs/POLYMARKET.md) |


## Protocol 2 (P2): The Universal Data Format

### Protocol 2 Packet Format

```
~<packet-length><symbol-length>|<symbol><market-data>L

Components:
  ~                  Start marker (1 byte)
  <packet-length>    4-byte ASCII integer (total packet size excluding header)
  <symbol-length>    4-byte ASCII integer (symbol/ticker length)
  |                  Delimiter (1 byte)
  <symbol>           ASCII-encoded ticker symbol (variable length)
  <market-data>      CSV format with 9 fields (see below)
  L                  Terminator (1 byte)
```

### Market Data Fields (in order)

1. `bid` - Best bid price
2. `bid_size` - Bid size (shares/contracts)
3. `ask` - Best ask price
4. `ask_size` - Ask size (shares/contracts)
5. `last` - Last traded price
6. `last_size` - Last trade size
7. `shortable_shares` - Available shares to short (IB only, 0.0 for others)
8. `timestamp` - Data source timestamp (Unix epoch)
9. `transmission_time` - Argus transmission timestamp (Unix epoch)

_This is not consistent with every data source, some may use different ordering or omissions. See each
 dispatcher's documents for details._

### Example Packet

```
~00710004|AAPL150.25,1000,150.30,800,150.28,100,50000,1732275600.123,1732275600.456L
```

**Decoded:**
- Symbol length: 4 bytes
- Symbol: `AAPL`
- Bid: $150.25 × 1000
- Ask: $150.30 × 800
- Last: $150.28 × 100
- Shortable shares: 50,000
- Source timestamp: 1732275600.123
- Transmission timestamp: 1732275600.456

### Parsing Protocol 2

Use the built-in `Protocol2Parser`:

```python
from argus.protocol import Protocol2Parser

parser = Protocol2Parser([
    'bid', 'bid_size', 'ask', 'ask_size',
    'last', 'last_size', 'shortable_shares',
    'timestamp', 'transmission_time'
])

# Parse a packet
result = parser.parse(data)
# Returns: {'symbol': 'AAPL', 'bid': 150.25, 'bid_size': 1000, ...}
# The array [...] defines the expected fields and their order should be
# consistent with the dispatcher's output
```

### Why Not JSON?

| Aspect | Protocol 2 | JSON |
|--------|-----------|------|
| **Size** | ~70 bytes | ~200 bytes |
| **Parse time** | O(n) single-pass | O(n) with object allocation |
| **Type safety** | Enforced by parser | Client-side validation needed |
| **Extensibility** | Backward compatible | Versioning required |
| **Bandwidth** | Minimal | 3x larger |


## Module Documentation

Argus consists of several specialized modules, each providing access to different data sources:

### Core Dispatcher Modules

- **[Interactive Brokers (IB)](docs/IB.md)** - Real-time market data, account positions, P&L tracking
  - `MKTDispatcher` (`ib.core`) - Core market data dispatcher with Protocol 2 support
  - `FXCDispatcher` (`ib.forecast`) - Forecasting contracts for prediction markets on IBKR
  - `IBWss` - WebSocket client with subscription management (max 100 contracts)
  - `AccountProvider` - Live portfolio tracking via FakeSocket pattern
  - **Note:** FXCDispatcher has limited concurrent client support due to IBKR's 100-contract ceiling and multi-contract market resolution requirements

- **[Capital.com](docs/CAPITAL.md)** - CFD and forex market data
  - `MKTDispatcher` - Unix Domain Socket server with dual-protocol support
  - Protocol 1 for control messages, Protocol 2 for market data
  - `CapitalComClient` - Interactive CLI client with state tracking
  - Symbol resolution caching for EPIC format conversion

- **[Binance](docs/BINANCE.md)** - Cryptocurrency market data
  - `BinanceMKTDispatcher` - WebSocket-based crypto data with Protocol 2
  - Top of book data (bid/ask/last) for multiple trading pairs
  - Auto-dump to JSON files for historical analysis
  - Statistics tracking (messages per second)

### Auxiliary Modules

- **[TradingView (TV)](docs/TV.md)** - Chart data and quotes (**non-dispatcher architecture**)
  - `QuoteSession` - Real-time quote data with callback subscriptions
  - `ChartSession` - Historical OHLCV data as pandas DataFrames
  - Custom WebSocket protocol (`~m~<size>~m~<JSON>`)
  - **Does NOT follow dispatcher paradigm** (callback-based)

- **[NASDAQ](docs/NASDAQ.md)** - Historical data downloader
  - `NASDAQDataDownloader` - Selenium-based web scraper for 10-year historical CSV data
  - Batch downloading with progress tracking and headless browser support
  - **Not a real-time data source** (utility for historical data collection)

- **[Polymarket](docs/POLYMARKET.md)** - Prediction market data
  - `PolymarketDispatcher` (`polymarket`) - TCP dispatcher with P1/P2 protocol support
  - `EnhancedPM` (`polymarket_direct`) - Direct API integration (no dispatcher)
  - WebSocket subscriptions for market data
  - Supports both dispatcher and direct client library modes

### Infrastructure

- **[Cache System](docs/CACHE.md)** - Thread-safe caching for API calls
  - All modules share a single cache file (`~/.argus/capital_cache.pkl`) via `DomainCache`
  - Each module has its own domain within the cache (e.g., `IBNetworker.search_contract`, `capital_com.api.resolve_symbol`)
  - Polymarket has a separate cache file (`~/.argus/polymarket_cache.pkl`) to prevent bloat
  - Automatic backups, CLI for inspection/manipulation, transparent cache generation
  - Environment variable to disable: `ARGUS_CACHES_DISABLED=1`
- **[WIREPROXY](docs/WIREPROXY.md)** - Proxy Dispatchers through Wireguard tunnels
  - Securely route dispatcher traffic through Wireguard via WireProxy
  - No sudo/root required
  - No changes to networking configuration all user-space
  - Works with command `python3 -m argus.wireproxy`
  - Automatically works with regular Wireguard Configs
  - Downloading/installing WireProxy binary is handled automatically
  

## Getting Started

### Installation

```bash
git clone https://github.com/The-Sal/Argus.git
cd Argus
pip install -e .
```

### Environment Setup

Create a `.env` file with your API credentials:

```bash
# Interactive Brokers
IB_COOKIE=your_ib_session_cookie

# Capital.com
CAPITAL_DOTCOM_API_KEY=your_api_key
CAPITAL_DOT_CUSTOM_PW=your_password
CAPITAL_DOTCOM_IDENTIFIER=your_identifier

# Polymarket (optional)
POLYMARKET_PRIVATE_KEY=your_private_key
POLYMARKET_PROXY_FUNDER=your_proxy_address

# TradingView (optional)
TOKEN=your_tv_token

# Notifications (macOS only)
NOTIFICATION_NUMBER=+1234567890

# Toggles
ARGUS_DISABLE_NOTIFICATIONS=0
ARGUS_CACHES_DISABLED=0
```

### Running a Dispatcher

```bash
# Interactive Brokers
python3 runtime.py ib.core --port 9972

# Capital.com (Unix Domain Socket)
python3 runtime.py capital.com --capital-env demo

# Binance
python3 runtime.py binance --port 9982

# Polymarket
python3 runtime.py polymarket --port 9972
```

### Connecting a Client

```python
import socket
from argus.protocol import Protocol2Parser

# Connect to dispatcher
s = socket.socket()
s.connect(('localhost', 9972))

# Subscribe to symbols
s.sendall(b'add=AAPL')
s.sendall(b'add=TSLA')

# Parse Protocol 2 stream
parser = Protocol2Parser([
    'bid', 'bid_size', 'ask', 'ask_size',
    'last', 'last_size', 'shortable_shares',
    'timestamp', 'transmission_time'
])

while True:
    data = s.recv(4096)

    # Handle ping
    if data[0] == 36:  # '$'
        data = data[1:]

    result = parser.parse(data)
    print(f"{result['symbol']}: bid={result['bid']}, ask={result['ask']}")
```

## Quick Start Examples

### Example 1: Real-Time Stock Quotes (IB)

```python
import socket
from argus.protocol import Protocol2Parser

# Connect to IB dispatcher
s = socket.socket()
s.connect(('localhost', 9972))
s.sendall(b'add=AAPL')

parser = Protocol2Parser([
    'bid', 'bid_size', 'ask', 'ask_size',
    'last', 'last_size', 'shortable_shares',
    'timestamp', 'transmission_time'
])

while True:
    data = s.recv(4096)
    if data[0] == 36: data = data[1:]  # Strip ping
    result = parser.parse(data)

    midpoint = (result['bid'] + result['ask']) / 2
    spread = result['ask'] - result['bid']
    print(f"AAPL: ${midpoint:.2f} (spread: ${spread:.4f})")
```

### Example 2: Crypto Streaming (Binance)

```python
import socket
from argus.protocol import Protocol2Parser

s = socket.socket()
s.connect(('localhost', 9982))
s.sendall(b'add=BTCUSDT')

parser = Protocol2Parser([
    'bid', 'bid_size', 'ask', 'ask_size',
    'last', 'last_size', 'timestamp', 'transmission_time'
])

while True:
    data = s.recv(4096)
    if data[0] == 36: data = data[1:]
    result = parser.parse(data)
    print(f"BTC: ${result['last']:,.2f}")
```

### Example 3: Historical Data (NASDAQ)

```python
from argus.nasdaq import NASDAQDataDownloader

with NASDAQDataDownloader(headless=True) as downloader:
    result = downloader.download_tickers(["AAPL", "MSFT", "GOOGL"])

    print(f"Downloaded {len(result['succeeded'])} files")
    print(f"Location: {result['temp_dir']}")
```

## System Requirements
- **Python**: 3.14t (should work with lower versions)
- **Operating Systems**:
  - **macOS**: Full support (all modules)
  - **Linux**: 
    - No desktop notifications (powered by AppleScript)
    - All other modules work fully
  - **Windows**: Not tested, not a target platform
- **Dependencies**: See `requirements.txt` (or uv, this project uses pyproject.toml)
- **Optional**: Firefox + geckodriver (for NASDAQ module)

## Development Ecosystem
- **argus-swift**: An experimental fork of Argus that's written in Swift (macOS/Linux only). It is far behind the main branch. Available [here](https://github.com/The-Sal/Argus/tree/argus-swift).
- **WpDaemon**: A sidecar daemon that manages WireProxy processes, it works as a drop-in replacement for the internal WireProxyServer. Available [here](https://github.com/the-sal/WpDaemon).
- **argus-polymarket**: A Rust SDK for the Polymarket Dispatcher, available [here](https://github.com/the-sal/argus-polymarket).

## Support
For questions, issues, or feature requests:
- **GitHub Issues**: https://github.com/The-Sal/Argus/issues
- **Documentation**: `/docs` directory (docs are branch-specific and differes between `main` and `argus-swift`)

---

Argus provides a unified, efficient infrastructure for consuming financial market data across multiple sources, languages, and use cases.
