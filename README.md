# Argus
.
![CI/CD Status](https://github.com/The-Sal/Argus/workflows/Manual%20CI%2FCD%20Trigger/badge.svg)

**Argus** aims to be a high-performance financial market data aggregation system built around a server-client architecture. It provides unified access to multiple financial data sources through a custom Protocol 2 (P2) binary protocol for efficient, standardized data transmission.

----

## Overview

Argus connects to major financial data providers (Interactive Brokers, Capital.com, Binance, TradingView, NASDAQ, Polymarket) and provides a unified interface for consuming real-time and historical market data.

### Core Design Principles

1. **Server-Client Architecture** - Dispatchers run as servers, clients connect via TCP/UDS
2. **Dispatcher Paradigm** - Most modules follow a unified dispatcher pattern for consistency
3. **Protocol 2 Normalization** - Standardized binary protocol across all dispatcher-based modules
4. **Multi-Client Support** - Single data stream multiplexed to multiple consuming processes
5. **Language Agnostic** - Clients can be written in any language with socket support

## Architecture

### Server-Client Design

Argus is designed as a **server-client architecture** centered around `runtime.py`. The workflow:

1. **Spin up Argus**: Launch a dispatcher server for your chosen data source
2. **Connect via TCP/UDS**: Connect trading algorithms, data pipelines, or analysis tools
3. **Stream real-time data**: Receive normalized market data in Protocol 2 format

```bash
# Start a dispatcher server
python runtime.py ib.core --port 9972

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

### Alternative: Python Module Import

While Argus **can** be imported like any other Python module, this is **not the primary design pattern**:

```python
# Supported but not recommended for production
from argus.ib import IBWss, MKTDispatcher
wss = IBWss(token="...")
wss.subscribe_to_contracts([...])
```

The server-client architecture is preferred because:
- Better separation of concerns
- Enables polyglot systems (Python server, C++ trading engine)
- Simplifies deployment and monitoring
- Reduces memory overhead (shared data streams)

## The Dispatcher Paradigm

**Most modules in Argus follow the dispatcher paradigm** (exceptions: TradingView, NASDAQ, Polymarket modules use different architectures).

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

### Dispatcher Lifecycle

```
┌─────────────────┐
│ Client connects │
└────────┬────────┘
         │
         ▼
┌─────────────────────────┐
│ Client: add=AAPL        │
└────────┬────────────────┘
         │
         ▼
┌──────────────────────────────────┐
│ Dispatcher subscribes to AAPL    │
│ (if not already subscribed)      │
└────────┬─────────────────────────┘
         │
         ▼
┌──────────────────────────────────┐
│ Market data flows to client      │
│ in Protocol 2 format             │
└────────┬─────────────────────────┘
         │
         ▼
┌──────────────────────────────────┐
│ Client disconnects               │
└────────┬─────────────────────────┘
         │
         ▼
┌──────────────────────────────────┐
│ Dispatcher auto-unsubscribes     │
│ (if no other clients need AAPL)  │
└──────────────────────────────────┘
```

## Protocol 2 (P2): The Universal Data Format

### Why Protocol 2?

Protocol 2 is a **custom binary protocol** designed for efficient, standardized market data transmission. It solves a critical problem: each financial data source provides data in different formats (JSON, CSV, binary), making it difficult to build unified systems.

**P2 provides:**
- **Unified format** across all dispatcher-based sources (IB, Binance, Capital.com)
- **Minimal overhead** (binary encoding, single-pass parsing)
- **Extensibility** (add new fields without breaking clients)
- **Type safety** (well-defined field ordering and validation)
- **Performance** (O(n) time complexity, no JSON parsing)

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

### Example Packet

```
~00710004|AAPL150.25,1000,150.30,800,150.28,100,50000,1732275600.123,1732275600.456L
```

**Decoded:**
- Packet length: 71 bytes
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
from argus.capital._svr_utils import Protocol2Parser

parser = Protocol2Parser([
    'bid', 'bid_size', 'ask', 'ask_size',
    'last', 'last_size', 'shortable_shares',
    'timestamp', 'transmission_time'
])

# Parse a packet
result = parser.parse(data)
# Returns: {'symbol': 'AAPL', 'bid': 150.25, 'bid_size': 1000, ...}
```

### Why Not JSON?

| Aspect | Protocol 2 | JSON |
|--------|-----------|------|
| **Size** | ~70 bytes | ~200 bytes |
| **Parse time** | O(n) single-pass | O(n) with object allocation |
| **Type safety** | Enforced by parser | Client-side validation needed |
| **Extensibility** | Backward compatible | Versioning required |
| **Bandwidth** | Minimal | 3x larger |

For high-frequency trading and real-time analytics, Protocol 2's efficiency is critical.

### P2 Design Philosophy

Protocol 2 was designed with three goals:

1. **Efficiency** - Minimal bandwidth and parsing overhead for high-frequency data
2. **Standardization** - Unified format across disparate data sources
3. **Simplicity** - Easy to implement in any programming language

The result is a protocol that's:
- **Fast** - Single-pass O(n) parsing
- **Compact** - ~70 bytes vs. ~200 bytes for JSON
- **Extensible** - Can add fields without breaking existing clients
- **Universal** - Same format for stocks (IB), crypto (Binance), forex (Capital.com)

This standardization enables:
- Writing once, consuming from multiple sources
- Building polyglot systems (Python server, C++ client)
- Efficient network utilization for real-time trading
- Simplified client implementation (no source-specific parsing)

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
  - Order book depth (@100ms), aggregate trades, k-line data, BookTrade, etc...
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
  - Legacy dispatcher (stub, see legacy branch)
  - `EnhancedPM` (polymarket_direct) - Direct API integration with dry mode
  - WebSocket subscriptions for market data
  - **Does NOT follow dispatcher paradigm** (direct client library)

### Infrastructure

- **[Cache System](docs/CACHE.md)** - Thread-safe caching for API calls
  - All modules share a single cache file (`~/.argus/capital_cache.pkl`) via `DomainCache`
  - Each module has its own domain within the cache (e.g., `IBNetworker.search_contract`, `capital_com.api.resolve_symbol`)
  - Polymarket has a separate cache file (`~/.argus/polymarket_cache.pkl`) to prevent bloat
  - Automatic backups, CLI for inspection/manipulation, transparent cache generation
  - Environment variable to disable: `ARGUS_CACHES_DISABLED=1`

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
python runtime.py ib.core --port 9972

# Capital.com (Unix Domain Socket)
python runtime.py capital.com --capital-env demo

# Binance
python runtime.py binance --port 9982
```

### Connecting a Client

```python
import socket
from argus.capital._svr_utils import Protocol2Parser

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
from argus.capital._svr_utils import Protocol2Parser

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
from argus.capital._svr_utils import Protocol2Parser

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

- **Python**: 3.10+
- **Operating Systems**:
  - **macOS**: Full support (all modules)
  - **Linux**: Partial support
    - IB modules currently not supported (ShortableShares requires macOS Finder)
    - No desktop notifications (powered by AppleScript)
    - All other modules work fully
    - IB module Linux support is in the pipeline
  - **Windows**: Not tested, not a target platform
- **Dependencies**: See `requirements.txt`
- **Optional**: Firefox + geckodriver (for NASDAQ module)

## Project Structure

```
Argus/
├── argus/                      # Main package directory
│   ├── ib/                     # Interactive Brokers module
│   │   ├── __init__.py         # IBWss, MKTDispatcher
│   │   ├── forecast.py         # FXCDispatcher for forecasting contracts
│   │   ├── _ib_utils.py        # Utilities, FakeSocket pattern
│   │   └── fields.py           # IBKR field definitions
│   ├── capital/                # Capital.com module
│   │   ├── __init__.py         # MKTDispatcher (UDS)
│   │   ├── client.py           # CapitalComClient
│   │   └── _svr_utils.py       # Protocol 2 parser
│   ├── binance/                # Binance cryptocurrency module
│   │   ├── __init__.py         # BinanceWss, BinanceMKTDispatcher
│   │   └── _classes.py         # Data classes
│   ├── polymarket/             # Polymarket (stub)
│   ├── polymarket_direct/      # Direct Polymarket integration
│   │   ├── __init__.py         # EnhancedPM client
│   │   └── _types.py           # Event, Market data models
│   ├── nasdaq/                 # NASDAQ data downloader
│   │   └── __init__.py         # NASDAQDataDownloader (Selenium)
│   ├── tv/                     # TradingView integration
│   │   ├── __init__.py         # QuoteSession, ChartSession
│   │   └── multisymbol.py      # Multi-symbol support
│   ├── cache_utils/            # Caching infrastructure
│   │   └── __init__.py         # CacheInspector, transparent cache
│   └── _argus_utils.py         # Notifications, Introspective base
├── tests/                      # Test files
├── docs/                       # Module documentation
│   ├── IB.md                   # Interactive Brokers docs
│   ├── CAPITAL.md              # Capital.com docs
│   ├── BINANCE.md              # Binance docs
│   ├── POLYMARKET.md           # Polymarket docs
│   ├── TV.md                   # TradingView docs
│   ├── NASDAQ.md               # NASDAQ docs
│   └── CACHE.md                # Cache system docs
├── runtime.py                  # Main dispatcher launcher
├── setup.py                    # Package setup
└── requirements.txt            # Dependencies
```

## Contributing

Argus is under active development. Current efforts:

**Swift Transcompilation**: There is an ongoing transcompilation effort from Python to Swift in the `argus-swift` branch. Python remains the primary source code - all patches and updates are applied to Python first, with Swift playing catchup through manual transcompilation. Python is not going anywhere.

## License

See `LICENSE` file for details.

## Support

For questions, issues, or feature requests:
- **GitHub Issues**: https://github.com/The-Sal/Argus/issues
- **Documentation**: `/docs` directory

---

Argus provides a unified, efficient infrastructure for consuming financial market data across multiple sources, languages, and use cases.
