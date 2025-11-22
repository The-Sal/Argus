# Interactive Brokers (IB) Module

The IB module provides real-time market data, account positions, and P&L tracking from Interactive Brokers through WebSocket connections and REST API calls.

## Table of Contents

- [Overview](#overview)
- [Components](#components)
- [Dispatchers](#dispatchers)
- [Features](#features)
- [API Surface](#api-surface)
- [Internal Architecture](#internal-architecture)
- [Limitations & Constraints](#limitations--constraints)
- [Usage Examples](#usage-examples)

## Overview

The IB module is one of the most feature-rich modules in Argus, providing:

- **Real-time market data** via WebSocket streaming
- **Account positions** and live P&L tracking
- **Contract search** and resolution
- **Shortable shares data** for short-selling analysis
- **Protocol 2 normalization** for unified data consumption
- **Caching** for expensive API calls (contract search, account data)

**Location:** `/argus/ib/`

**Primary Files:**
- `__init__.py` (1,204 lines) - Core IBWss and MKTDispatcher
- `forecast.py` (758 lines) - FXCWss and FXCDispatcher for forecasting contracts
- `_ib_utils.py` - Shared utilities, data classes, and helpers
- `fields.py` - IBKR field definitions and mappings
- `_shortable_shares_data.py` - Shortable shares tracking

## Components

### 1. IBNetworker

HTTP session manager for IBKR REST API.

**Features:**
- Thread-safe `LockedSession` for concurrent access
- Authentication management (tickle, heartbeat, session validation)
- Account data retrieval (positions, ledger, summary)
- Contract search and resolution

**Key Methods:**

```python
class IBNetworker:
    def initialize(self)
        # Run setup messages and start heartbeat threads

    def search_contract(self, contract_name) -> list[SearchResult]
        # Search for stock contracts (cached)

    def get_all_trading_accounts_ids(self) -> list[Account]
        # Retrieve all trading account IDs

    def set_trading_account_id(self, account_id)
        # Set active trading account (one-time only)

    def get_account_ledger(self)
        # Get account ledger data (balances, cash, etc.)
```

**Caching:**
- `search_contract()` results are cached to reduce API load
- Cache location: `~/.argus/ib_cache.pkl`

### 2. IBWss

WebSocket client for streaming real-time market data.

**Features:**
- Subscription management (max 100 concurrent contracts)
- Protected assets (prevents accidental unsubscription)
- Progress tracking for subscriptions
- Automatic reconnection and heartbeat
- Callback-based market data delivery

**Key Methods:**

```python
class IBWss:
    def __init__(self, cookie=os.getenv('IB_COOKIE'))
        # Initialize WebSocket connection

    def subscribe_to_contracts(self, contracts: list)
        # Subscribe to market data for list of contracts

    def subscribe_to_portfolio(self, callback)
        # Subscribe to account P&L updates

    def unsubscribe(self, conid)
        # Unsubscribe from a contract (respects protected assets)

    def wait_till_read(self)
        # Block until WebSocket is ready
```

**Protected Assets:**
- Contracts marked as "protected" cannot be unsubscribed
- Used by `AccountProvider` to prevent unsubscribing portfolio holdings
- Raises `ProtectedAssetViolation` if unsubscribe attempted

**Subscription Limits:**
- **Maximum:** 100 contracts per WebSocket connection (IBKR limitation)
- Progress bar shows current subscription count
- Auto-cleanup when clients disconnect

### 3. MKTDispatcher (Core)

The main dispatcher for Interactive Brokers market data.

**Transport:** TCP socket (default port: 9972)

**Supported Modes:**
1. `ASK` - Ask price only
2. `ASK+BID+LAST` - Bid, ask, and last price
3. `FULL_PKL` - Full market data as pickled objects
4. `FULL_JSON` - Full market data as JSON
5. `PROTOCOL_2` - Normalized Protocol 2 format (recommended)

**Features:**
- ✅ **Caching** - Last-known values cached for immediate client response
- ✅ **Multi-client** - Multiple clients can consume same data stream
- ✅ **Auto-subscription** - Subscribe on client request, unsubscribe when no clients remain
- ✅ **Interactive mode** - Runtime configuration via `Introspective` base class

**Client Commands:**

| Command | Description |
|---------|-------------|
| `add=SYMBOL` | Subscribe to a ticker |
| `remove=SYMBOL` | Unsubscribe from a ticker |
| `ping` | Check connection status |

**Protocol 2 Format:**

```
~<packet-length><symbol-length>|<symbol><market-data>L

Fields: bid, bid_size, ask, ask_size, last, last_size, shortable_shares, timestamp, transmission_time
```

### 4. AccountProvider

**Critical Component** for live portfolio tracking.

**Features:**
- Streams account positions to debug socket (port 9973)
- Uses `FakeSocket` pattern to integrate with `MKTDispatcher` without TCP overhead
- Provides P&L data in `~{JSON}L` format
- Protects portfolio tickers from unsubscription

**How it works:**

1. `AccountProvider` subscribes to all portfolio holdings via `FakeSocket`
2. Market data flows from `MKTDispatcher` to `FakeSocket` callbacks
3. P&L calculated in real-time and streamed to port 9973
4. Portfolio tickers marked as "protected assets"

**FakeSocket Pattern:**

The `FakeSocket` is a brilliant design pattern that allows in-memory callbacks while preserving the socket-based architecture of `MKTDispatcher`. Instead of refactoring the entire dispatcher to support both sockets and callbacks, `FakeSocket` provides a socket-like interface that triggers callbacks internally.

```python
class FakeSocket:
    def send(self, data):
        # Trigger callback instead of actual socket send
        self.callback(data)

    def recv(self, size):
        # Not used for outgoing-only fake sockets
        pass
```

This pattern enables:
- Code reuse (no dispatcher refactoring)
- Performance (no actual socket overhead)
- Modularity (account provider can be disabled without affecting dispatcher)

### 5. FXCWss and FXCDispatcher

WebSocket client and dispatcher for **Forecasting Contracts** (prediction markets on IBKR).

**WebSocket URL:** `wss://forecasttrader.interactivebrokers.ie/portal.proxy/v1/etp/ws`

**Key Differences from IBWss:**
- Different WebSocket endpoint (forecasttrader subdomain)
- Additional topic handlers: `act`, `system`, `sts`
- Socket message monitoring and logging
- **Cannot coexist with IBWss** (only one IBKR WebSocket at a time)

**Contract Structure:**

Forecasting contracts have a hierarchical structure:

- **Big Contract** (e.g., "NYC Mayor Election")
  - **Mini Contracts** (one per outcome/candidate)
    - **Micro Contracts** (2 per mini: YES and NO)

Example: 3-candidate election
```
Big: NYC Mayor Election
├── Mini: Candidate A
│   ├── Micro: A-YES (Call)
│   └── Micro: A-NO (Put)
├── Mini: Candidate B
│   ├── Micro: B-YES (Call)
│   └── Micro: B-NO (Put)
└── Mini: Candidate C
    ├── Micro: C-YES (Call)
    └── Micro: C-NO (Put)
```

**Each micro contract requires its own subscription**, so a 10-candidate election requires **20 subscriptions**.

## Dispatchers

### MKTDispatcher (Core) - `ib.core`

**Purpose:** Real-time market data for stocks

**Caching:** ✅ Yes (last-known values)

**Protocol:** Protocol 2

**Concurrent Clients:** ✅ Full support (no limitations)

**Launch:**
```bash
python runtime.py ib.core --port 9972
```

**Client Example:**
```python
import socket
from argus.capital._svr_utils import Protocol2Parser

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
    if data[0] == 36:  # Handle ping '$'
        data = data[1:]
    result = parser.parse(data)
    print(f"{result['symbol']}: bid={result['bid']}, ask={result['ask']}")
```

### FXCDispatcher (Forecast) - `ib.forecast`

**Purpose:** Forecasting/prediction market contracts

**Caching:** ✅ Yes (market resolution, contract metadata)

**Protocol:** Custom (Big/Mini/Micro contract structures)

**Concurrent Clients:** ⚠️ **LIMITED** (see constraints below)

**Launch:**
```bash
python runtime.py ib.forecast --port 9972
```

**Interactive Mode:**
- Account selection UI on startup
- Runtime configuration of logging and monitoring

## Features

### Feature Matrix

| Feature | MKTDispatcher | FXCDispatcher |
|---------|--------------|---------------|
| Real-time market data | ✅ | ✅ |
| Protocol 2 support | ✅ | ⚠️ (custom) |
| Caching | ✅ | ✅ |
| Multi-client support | ✅ | ⚠️ (limited) |
| Account integration | ✅ (via AccountProvider) | ✅ |
| Protected assets | ✅ | ✅ |
| Interactive configuration | ✅ | ✅ |
| Shortable shares | ✅ | ❌ |

### Caching

Both dispatchers use the IB Cache system (`~/.argus/ib_cache.pkl`):

**Cached Operations:**
- `IBNetworker.search_contract()` - Contract search results
- Account ledger and summary data
- Forecasting contract metadata

**Cache Decorator:**
```python
@_IB_Cache.cache_decorator('IBNetworker.search_contract')
def search_contract(self, contract_name):
    # Expensive API call cached automatically
```

**Disable Caching:**
```bash
export ARGUS_CACHES_DISABLED=1
```

### Notifications

The IB module uses the Argus notification system for critical events:

- WebSocket connection/disconnection
- Authentication failures
- Market data errors
- Account P&L alerts

**Platforms:**
- macOS: osascript system notifications + iMessage (optional)
- Linux: Console logging
- Windows: Console logging

**Disable Notifications:**
```bash
export ARGUS_DISABLE_NOTIFICATIONS=1
```

## API Surface

### Client-Side API (How to Interact)

#### 1. Direct Socket Connection (Recommended)

```python
import socket

s = socket.socket()
s.connect(('localhost', 9972))

# Subscribe
s.sendall(b'add=AAPL')
s.sendall(b'add=TSLA')

# Unsubscribe
s.sendall(b'remove=AAPL')

# Receive Protocol 2 data
while True:
    data = s.recv(4096)
    # Parse with Protocol2Parser
```

#### 2. Python Module Import (Not Recommended)

```python
from argus.ib import IBWss

wss = IBWss(cookie="your_ib_cookie")
wss.run()
wss.wait_till_read()

def callback(market_data):
    print(market_data.data)

wss.subscribe_to_contracts([conid1, conid2], callback=callback)
```

**Why not recommended?**
- Bypasses dispatcher architecture
- No multi-client support
- No Protocol 2 normalization
- Harder to integrate with non-Python systems

### Server-Side API (Internal)

Developers extending the IB module should understand:

**IBWss Callbacks:**
```python
def handle_market_data(self, message):
    # Called for every market data update
    conid = message['conid']
    if conid in self.contract_callbacks:
        self.contract_callbacks[conid](MarketData(...))
```

**MKTDispatcher Client Management:**
```python
def handle_client(self, conn, addr):
    # New client connected
    # Parse commands: add=SYMBOL, remove=SYMBOL
    # Stream Protocol 2 data
```

## Internal Architecture

### Threading Model

The IB module uses extensive threading for concurrency:

1. **Main Thread** - WebSocket `run_forever()` loop
2. **Heartbeat Thread** - Keep-alive messages every 10s
3. **Authentication Thread** - Check auth status every 2 minutes
4. **HTTP Session Thread** - Tickle endpoint every 2s
5. **Dispatcher Thread(s)** - One per client connection

**Thread Safety:**
- `LockedSession` - Thread-safe HTTP requests
- `threading.Lock` on subscription management
- Atomic operations for contract callbacks

### WebSocket Message Flow

```
[IBKR WebSocket Server]
         ↓
   [on_message handler]
         ↓
   [JSON parsing]
         ↓
   [Topic routing]
    ↓         ↓
[smd]      [spl]
    ↓         ↓
[handle_market_data] [handle_account_pnl]
    ↓         ↓
[contract_callbacks] [_pnl_subscriptions]
    ↓         ↓
[MKTDispatcher] [AccountProvider]
    ↓         ↓
[Protocol 2 encoding] [JSON encoding]
    ↓         ↓
[TCP socket send] [Debug socket send :9973]
    ↓         ↓
[Clients]  [P&L consumers]
```

### Data Classes

**MarketData:**
```python
@dataclass
class MarketData:
    contract_id: int
    server_id: str
    contract_exchange: str
    topic: str
    data: dict  # Raw IBKR fields
```

**SearchResult:**
```python
@dataclass
class SearchResult:
    conid: int
    description: str
    symbol: str
    # ... other contract metadata
```

**Account:**
```python
@dataclass
class Account:
    accountId: str
    accountVan: str
    accountTitle: str
    type: str
```

**STK_Position:**
```python
@dataclass
class STK_Position:
    acctId: str
    conid: int
    ticker: str
    position: float
    mktPrice: float
    mktValue: float
    # ... P&L fields
```

## Limitations & Constraints

### 1. WebSocket Subscription Limit

**Constraint:** IBKR allows maximum **100 concurrent contract subscriptions** per WebSocket.

**Impact:**
- `MKTDispatcher` shows progress bar: `[XX/100]`
- Attempting 101st subscription will fail
- Must manage subscriptions carefully

**Mitigation:**
- Auto-unsubscribe when no clients need a symbol
- Protected assets don't count toward cleanup
- Use multiple IBKR accounts (not supported currently)

### 2. FXCDispatcher Concurrent Client Limitation

**Constraint:** FXC market resolution requires **multiple contracts per market** (2 per outcome).

**Example:** 10-candidate election = 20 contract subscriptions

**Problem:**
- Client A requests 5-candidate market → 10 subscriptions
- Client B requests 8-candidate market → 16 subscriptions
- **Total: 26 subscriptions** for just 2 markets
- With 100-subscription ceiling, only ~4-5 markets can be active simultaneously

**Impact:**
- **Multiple concurrent clients resolving different markets can quickly exhaust the 100-contract ceiling**
- Client A's markets might force Client B's markets to be unsubscribed

**Current Behavior:**
- No explicit queue or lock
- First-come, first-served subscription model
- Protected assets take priority

**Recommended Approach:**
- **Single client per FXCDispatcher instance**
- Coordinate market resolution to avoid conflicts
- Use multiple IBKR accounts for parallel FXC research (requires code changes)

### 3. One WebSocket Per Account

**Constraint:** IBKR allows only **one WebSocket connection** per account.

**Impact:**
- Cannot run `IBWss` and `FXCWss` simultaneously
- Cannot run multiple `MKTDispatcher` instances with same credentials
- Must choose: Core market data OR forecasting contracts

**Workaround:**
- Use multiple IBKR accounts
- Time-multiplex: Run core during market hours, FXC after hours

### 4. Platform-Specific Features

**macOS Only:**
- `ShortableSharesData` - Requires macOS file paths
- iMessage notifications

**Linux Support:**
- All features except ShortableSharesData and iMessage
- Console-only notifications

**Windows:**
- Limited testing
- No IB forecasting support
- No notifications

### 5. Authentication and Session Management

**Constraint:** IB cookie expires after inactivity.

**Impact:**
- Must manually retrieve new cookie from browser
- No programmatic login (IBKR security policy)

**Mitigation:**
- Heartbeat keeps session alive
- Authentication checker re-authenticates if needed
- Notifications alert on auth failure

## Usage Examples

### Example 1: Basic Market Data Subscription

```python
import socket
from argus.capital._svr_utils import Protocol2Parser

# Connect to dispatcher
s = socket.socket()
s.connect(('localhost', 9972))

# Subscribe to symbols
symbols = ['AAPL', 'GOOGL', 'MSFT', 'TSLA']
for symbol in symbols:
    s.sendall(f'add={symbol}'.encode())

# Parse incoming data
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

    # Calculate spread
    spread = result['ask'] - result['bid']
    midpoint = (result['ask'] + result['bid']) / 2

    print(f"{result['symbol']}: ${midpoint:.2f} (spread: ${spread:.4f})")
```

### Example 2: Live P&L Monitoring

```python
import socket
import json

# Connect to debug socket for account data
s = socket.socket()
s.connect(('localhost', 9973))

while True:
    data = s.recv(8192).decode()

    # Protocol: ~{JSON}L
    if data.startswith('~') and data.endswith('L'):
        json_str = data[1:-1]
        positions = json.loads(json_str)

        total_pnl = sum(pos['unrealizedPnl'] for pos in positions)
        print(f"Total Unrealized P&L: ${total_pnl:,.2f}")

        for pos in positions:
            print(f"  {pos['ticker']}: {pos['position']} shares @ ${pos['mktPrice']:.2f} "
                  f"(P&L: ${pos['unrealizedPnl']:,.2f})")
```

### Example 3: Shortable Shares Tracking

```python
import socket
from argus.capital._svr_utils import Protocol2Parser

s = socket.socket()
s.connect(('localhost', 9972))
s.sendall(b'add=GME')  # Meme stock

parser = Protocol2Parser([
    'bid', 'bid_size', 'ask', 'ask_size',
    'last', 'last_size', 'shortable_shares',
    'timestamp', 'transmission_time'
])

while True:
    data = s.recv(4096)
    if data[0] == 36:
        data = data[1:]

    result = parser.parse(data)

    # Check if shares available to short
    shortable = result['shortable_shares']
    if shortable == 0:
        print(f"⚠️ {result['symbol']}: HARD TO BORROW (0 shares available)")
    else:
        print(f"{result['symbol']}: {shortable:,} shares available to short")
```

### Example 4: Interactive Account Selection (FXC)

```bash
# Launch FXCDispatcher
python runtime.py ib.forecast

# Interactive prompt appears:
# Select an account:
# 1. Account U1234567 (Individual - IBKR Pro)
# 2. Account U7654321 (IRA - IBKR Pro)
# Enter choice:
```

## Environment Variables

```bash
# Required
IB_COOKIE="your_ibkr_session_cookie"

# Optional
ARGUS_DISABLE_NOTIFICATIONS=0  # Set to 1 to disable
ARGUS_CACHES_DISABLED=0        # Set to 1 to disable caching
NOTIFICATION_NUMBER="+1234567890"  # For iMessage alerts (macOS)
```

## Troubleshooting

### WebSocket Won't Connect

**Symptoms:** "WebSocket connection closed" immediately after startup

**Causes:**
- Expired IB cookie
- Another WebSocket already connected
- Network/firewall issues

**Solutions:**
1. Get fresh cookie from browser (inspect network requests on IBKR web portal)
2. Kill any other Argus processes using same account
3. Check firewall settings for `wss://` connections

### Subscription Limit Reached

**Symptoms:** "Cannot subscribe to more contracts (100/100)"

**Solutions:**
1. Unsubscribe from unused symbols: `s.sendall(b'remove=SYMBOL')`
2. Check for leaked subscriptions (clients disconnected without cleanup)
3. Restart dispatcher to clear all subscriptions

### FXC Markets Not Resolving

**Symptoms:** FXCDispatcher can't find contracts for a market

**Causes:**
- Market not available on IBKR
- Incorrect market name
- Contract metadata not cached

**Solutions:**
1. Search manually on IBKR web portal first
2. Check cache: `~/.argus/ib_cache.pkl`
3. Clear cache and retry: `rm ~/.argus/ib_cache.pkl`

### Account Data Not Streaming

**Symptoms:** Port 9973 not responding

**Causes:**
- `AccountProvider` not initialized
- No trading account ID set
- Portfolio is empty

**Solutions:**
1. Verify trading account ID in logs
2. Check that account has positions
3. Ensure `ib.forecast` mode (not `ib.core`)

## File Reference

```
argus/ib/
├── __init__.py                    # IBWss, IBNetworker, MKTDispatcher
├── forecast.py                    # FXCWss, FXCDispatcher
├── fields.py                      # IBKR field definitions (31=LAST_PRICE, etc.)
├── _ib_utils.py                   # Data classes, LockedSession, FakeSocket
├── _forcast_utils.py              # Big/Mini/Micro contract classes
└── _shortable_shares_data.py      # Shortable shares tracking (macOS only)
```

## Summary

The IB module is the most sophisticated module in Argus, providing comprehensive access to Interactive Brokers' market data and account information. Its standout features include:

- **Dual dispatcher model** (Core + Forecast)
- **FakeSocket pattern** for elegant account integration
- **Protected assets** to prevent critical unsubscriptions
- **Thread-safe design** with extensive concurrency
- **Protocol 2 normalization** for unified consumption

**Key Constraint:** FXCDispatcher's concurrent client limitation due to IBKR's 100-contract ceiling and multi-contract market resolution requirements. For production systems requiring multiple simultaneous FXC markets, consider:
- Single-client architecture
- Request queuing/coordination
- Multiple IBKR accounts (requires code modification)

For most use cases (stock market data, account tracking), the IB module provides robust, production-ready functionality with excellent multi-client support.
