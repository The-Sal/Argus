# Argus

**Argus** aims to be a high-performance financial market data aggregation system built around a server-client architecture. It provides unified access to multiple financial data sources through a custom Protocol 2 (P2) binary protocol for efficient, standardized data transmission.

----

## Overview

Argus connects to major financial data providers (Interactive Brokers, Capital.com, Binance, TradingView, NASDAQ, Polymarket, Hyperliquid, Lighter) and provides a unified interface for consuming real-time and historical market data.

### Core Design Principles

1. **Server-Client Architecture** – Dispatchers run as servers, clients connect via TCP/UDS
2. **Dispatcher Paradigm** – Most modules follow a unified dispatcher pattern for consistency
3. **Protocol 2 Normalization** – Standardized binary protocol across all dispatcher-based modules
4. **Multi-Client Support** – Single data stream multiplexed to multiple consuming processes
5. **Language Agnostic** – A minimal client can be implemented in any language with socket support

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


### Alternative: python3 Module Import

While Argus **can** be imported like any other python3 module, this is **not the primary design pattern**:

```python
# Supported but not recommended for production
from argus.ib import MKTDispatcher, IBWss
from argus.ib.forecast import FXCDispatcher
wss = IBWss(cookie="...")  # defaults to $IB_COOKIE
wss.stream_market_data(contract_id, callback)
```

The server-client architecture is preferred because the internal APIs of each module is not stable. That said
importing modules is useful for testing, debugging, and development. e.g., dataclasses, utility functions, and protocol parsers.

## The Dispatcher Paradigm

**Most modules in Argus follow the dispatcher paradigm** (exception: NASDAQ).
- **NASDAQ** - Historical data scraper (not real-time)
- **Polymarket** - Dispatcher (`polymarket`) built on top of the `polymarket_direct` client library (REST + WebSocket primitives)
- **TradingView** - Has both dispatcher (`tradingview`) and direct callback-based client sessions (`QuoteSession`, `ChartSession`)

### What is a Dispatcher?

A **Dispatcher** is a server that:

1. **Connects** to a financial data source (WebSocket/REST API)
2. **Listens** for client connections (TCP or Unix Domain Sockets)
3. **Subscribes** to market data based on client requests
4. **Converts** incoming data to Protocol 2 format
5. **Multiplexes** data to all subscribed clients
6. **Manages** subscription lifecycle (auto-unsubscribe when no clients remain)
7. **Order Execution** Full Order lifecycle management (if supported by the data source, e.g., Perpetuals)

### Available Dispatchers

| Dispatcher | Data Source | Transport | Port/Socket | Docs |
|------------|-------------|-----------|-------------|------|
| `ib.core` | Interactive Brokers | TCP | 9972 | [IB.md](docs/IB.md) |
| `ib.forecast` | IB Forecasting Contracts | TCP | 9972 | [IB.md](docs/IB.md) |
| `capital.com` | Capital.com | Unix Socket | `/tmp/argus_capital.sock` | [CAPITAL.md](docs/CAPITAL.md) |
| `binance` | Binance | TCP | 9982 | [BINANCE.md](docs/BINANCE.md) |
| `polymarket` | Polymarket | TCP | 9972 | [POLYMARKET.md](docs/POLYMARKET.md) |
| `hyperliquid` | Hyperliquid (perpetuals) | TCP | 9972 | [Hyperliquid DISPATCHER.md](docs/perpetuals/hyperliquid/DISPATCHER.md) |
| `lighter` | Lighter (perpetuals) | TCP | 9974 | [Lighter DISPATCHER.md](docs/perpetuals/lighter/DISPATCHER.md) |
| `tradingview` | TradingView quotes | TCP | 9974 | [TV_DISPATCHER.md](docs/TV_DISPATCHER.md) |

Several dispatchers share a default port, so pass `--port` when running more than one at a time.


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

_This is not consistent with every data source, some may use different ordering or omissions. See each
 dispatcher's documents for details._


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

## Module Documentation

Argus consists of several specialized modules, each providing access to different data sources:

### Core Dispatcher Modules

- **[Interactive Brokers (IB)](docs/IB.md)** - Real-time market data, account positions, P&L tracking
  - `MKTDispatcher` (`ib.core`) - Core market data dispatcher with Protocol 2 support
  - `FXCDispatcher` (`ib.forecast`) - Forecasting contracts for prediction markets on IBKR
  - `IBWss` - WebSocket client with subscription management (max 100 contracts)
  - `AccountProvider` - Live portfolio tracking via FakeSocket pattern
  - Market metadata comes from IBKR's EventTrader `tws.proxy` API ([migration notes](docs/system/FXC_EVENTTRADER_MIGRATION.md))
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

- **Perpetuals** - Perpetual futures venues with market data **and order execution**
  - `HyperLiquidDispatcher` (`hyperliquid`) - [docs](docs/perpetuals/hyperliquid/DISPATCHER.md)
  - `LighterDispatcher` (`lighter`) - [docs](docs/perpetuals/lighter/DISPATCHER.md)
  - Both build on a shared base (`argus.perpetuals.shared`): [protocol](docs/perpetuals/shared/PROTOCOL.md), [account](docs/perpetuals/shared/ACCOUNT.md), [concurrency](docs/perpetuals/shared/CONCURRENCY.md)
  - Full order lifecycle, account updates and funding rates pushed to clients
  - Rust SDKs available via [argus-sdks](https://github.com/the-sal/argus-sdks)

- **[TradingView (TV)](docs/TV_DISPATCHER.md)** - Quotes and chart data
  - `TradingViewDispatcher` (`tradingview`) - one shared upstream WebSocket multiplexed to all clients, P1 control + P2 quote pushes ([dispatcher spec](docs/TV_DISPATCHER.md))
  - `QuoteSession` / `ChartSession` - direct callback-based client for real-time quotes and historical OHLCV DataFrames ([library docs](docs/TV.md))

### Auxiliary Modules

- **[NASDAQ](docs/NASDAQ.md)** - Historical data downloader
  - `NASDAQDataDownloader` - Selenium-based web scraper for 10-year historical CSV data
  - Batch downloading with progress tracking and headless browser support
  - **Not a real-time data source** (utility for historical data collection)

- **[Polymarket](docs/POLYMARKET.md)** - Prediction market data
  - `PolymarketDispatcher` (`polymarket`) - TCP dispatcher with P1/P2 protocol support
  - `polymarket_direct` - Client library the dispatcher is built on: `PolyRestAPI` (REST), `PolyMarketOrderBookConn`/`PolyMarketOrderBookPool` and account/RTDS WebSockets (`wss`), plus typed dataclasses (`Market`, `PolymarketEvent`, `PolyMarketOrder`, `Trade`, ...)
  - Sharded WebSocket order book subscriptions, order management and account updates ([sharding](docs/system/POLYMARKET_SHARDING.md), [protocol v2](docs/system/POLYMARKET_PROTOCOL_V2.md), [REST protections](docs/system/POLYMARKET_REST_PROTECTIONS.md))
  - Requires the APDB sidecar (see Satellite Systems)

### Infrastructure

- **[Cache System](docs/system/CACHE.md)** - Thread-safe caching for API calls
  - All modules share a single cache file (`~/.argus/capital_cache.pkl`) via `DomainCache`
  - Each module has its own domain within the cache (e.g., `IBNetworker.search_contract`, `capital_com.api.resolve_symbol`)
  - Polymarket has a separate cache file (`~/.argus/polymarket_cache.pkl`) to prevent bloat
  - Automatic backups, CLI for inspection/manipulation, transparent cache generation
  - Environment variable to disable: `ARGUS_CACHES_DISABLED=1`
- **[WIREPROXY](docs/system/WIREPROXY.md)** - Proxy Dispatchers through Wireguard tunnels
  - Securely route dispatcher traffic through Wireguard via WireProxy
  - No sudo/root required
  - No changes to networking configuration all user-space
  - Works with command `python3 -m argus.wireproxy`
  - Automatically works with regular Wireguard Configs
  - Downloading/installing WireProxy binary is handled automatically
- **Satellite Systems (`argus.satellite_sys`)** - Install and manage sidecar processes
  - `WpDaemon` - WireProxy process manager (drop-in replacement for the internal `WireProxyServer`)
  - `APDB` - Argus Polymarket Database, a hard dependency of the Polymarket dispatcher
  - Managed with `python3 -m argus.satellite_sys <system> <install|update|start|stop|status>`
- **[Environment Variables & Secure Env](docs/system/ENVIRONMENT_VARIABLES.md)** - Configuration toggles and `SecureEnvLoader`
  - `.env.enc.se` is decrypted just-in-time via [SDist](https://github.com/the-sal/SDist) (macOS only) and the macOS Secure Enclave (see [Encrypted `.env`](#encrypted-env-macos-secure-enclave))
- **[System Docs](docs/system/)** - [Thread model](docs/system/threadmodel.md), Polymarket protocol/sharding/REST protections, [FXC migration](docs/system/FXC_EVENTTRADER_MIGRATION.md), [Raspberry Pi Zero install](docs/system/zero.md)

## Command-Line Tools

All CLIs are run from the repository root with `uv run` (or `python3`).

| Tool | Command | Purpose |
|------|---------|---------|
| **runtime** | `runtime.py <dispatcher>` | Launch a dispatcher server (see [Running a Dispatcher](#running-a-dispatcher)) |
| **cache** | `-m argus.cache_utils` | Interactive inspector for the shared caches: inspect, check state, restore from backup, delete a domain, generate a transparent cache, toggle capital/polymarket cache file |
| **satellite** | `-m argus.satellite_sys <system> <action>` | Manage sidecars. Systems: `WpDaemon`, `APDB`. Actions: `install`, `update`, `start`, `stop`, `status` |
| **wireproxy** | `-m argus.wireproxy [flags]` | Manage WireGuard configs and the WireProxy server (interactive with no flags) |

```bash
uv run python3 -m argus.cache_utils                     # cache inspector
uv run python3 -m argus.satellite_sys APDB install      # install the Polymarket DB sidecar
uv run python3 -m argus.satellite_sys APDB status       # prints "Running: True/False"
uv run python3 -m argus.wireproxy --list-confs          # list WireGuard configs
uv run python3 -m argus.wireproxy --add-conf <path>     # also: --bulk-import <dir>, --remove-conf <name>
uv run python3 -m argus.wireproxy --start-server <conf> # also: --stop-server, --server-status
uv run python3 -m argus.wireproxy --brute-polymarket    # test every VPN against Polymarket's geoblock endpoint
```

_You do not need to install APDB manually, it is automatically installed when you run the Polymarket dispatcher. This applies to all mandatory sidecars._

## Getting Started

### Installation

```bash
git clone https://github.com/The-Sal/Argus.git
cd Argus
# uv automatically setups dependencies
uv run runtime.py --help 
```

### Environment Setup

Create a `.env` file with your API credentials (full list: [ENVIRONMENT_VARIABLES.md](docs/system/ENVIRONMENT_VARIABLES.md)):

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

# Hyperliquid (optional)
HYPERLIQUID_PRIVATE_KEY=your_private_key
HYPERLIQUID_WALLET_ADDRESS=your_wallet_address

# Lighter (optional)
LIGHTER_PRIVATE_KEY=your_private_key
LIGHTER_ACCOUNT_INDEX=your_account_index
LIGHTER_API_INDEX=your_api_key_index

# Perpetuals safety: block order placement (read-only dispatcher)
HYPERLIQUID_BLOCK_ORDER_EXECUTION=0
LIGHTER_BLOCK_ORDER_EXECUTION=0

# Notifications (macOS only)
NOTIFICATION_NUMBER=+1234567890

# Toggles
ARGUS_DISABLE_NOTIFICATIONS=0
ARGUS_CACHES_DISABLED=0
```

#### Encrypted `.env` (macOS Secure Enclave)

Instead of keeping a plaintext `.env` on disk, you can encrypt it with [SDist](https://github.com/the-sal/SDist) (**macOS only**, optional) into `.env.enc.se`:

```bash
sdist -c -p n -f encrypt-se -a .env .env.enc.se ?   # encrypt, then delete the plaintext .env
```

- When `.env.enc.se` is present and the `sdist` CLI is on `PATH`, Argus decrypts it just-in-time on startup (Touch ID prompt), loads it, and immediately deletes the plaintext `.env`. This happens once per process.
- Requires macOS with a Secure Enclave. On Linux, or without `sdist`, Argus warns and falls back to a plaintext `.env`.
- Launching two dispatchers from the same directory at the same moment races on the decrypt/delete cycle, so stagger them (see [CONCURRENCY.md](docs/perpetuals/shared/CONCURRENCY.md)).
- `.env.enc.se` is git-ignored; never commit either file.

Details: [ENVIRONMENT_VARIABLES.md](docs/system/ENVIRONMENT_VARIABLES.md).

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

# Perpetuals
python3 runtime.py hyperliquid --port 9972
python3 runtime.py lighter --port 9974

# TradingView
python3 runtime.py tradingview --port 9974
```

Common flags: `--host`, `--port`, `--log-level`, `-v`, `--exit-on-init`. See `python3 runtime.py --help` for all of them.

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
- **Python**: 3.14+ (3.14t free-threaded build recommended; `runtime.py` forces `PYTHON_GIL=0` on it)
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
- **WpDaemon**: A sidecar daemon that manages WireProxy processes, it works as a drop-in replacement for the internal WireProxyServer. Requires manual startup. Available [here](https://github.com/the-sal/WpDaemon).
- **argus-polymarket**: A Rust SDK for the Polymarket Dispatcher, available [here](https://github.com/the-sal/argus-polymarket).
- **argus-sdks**: Rust SDKs for perpetuals within Argus, available [here](https://github.com/the-sal/argus-sdks).
- **Argus Polymarket Database (APDB)**: A sidecar databases for storing Polymarket data efficiently. It's automatically installed and is a hard dependency of the Polymarket Dispatcher. Available [here](https://github.com/the-sal/argus-polymarket-db).

## Support
For questions, issues, or feature requests:
- **GitHub Issues**: https://github.com/The-Sal/Argus/issues
- **Documentation**: `/docs` directory (docs are branch-specific and differ between `main` and `argus-swift`)

---

Argus provides a unified, efficient infrastructure for consuming financial market data across multiple sources, languages, and use cases.
