# TradingView (TV) Module

The TradingView module provides real-time quote data and historical chart data from TradingView's WebSocket API.

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Components](#components)
- [Usage Examples](#usage-examples)
- [Limitations](#limitations)

## Overview

The TradingView module provides **two layers**:

1. **Low-level sessions** (`QuoteSession`, `ChartSession`) — callback-based, for direct Python integration and historical data.
2. **`TradingViewDispatcher`** (`dispatcher.py`) — a full Argus dispatcher (Protocol 1 request/response + Protocol 2 streaming) built on top of a single shared multi-symbol quote session. See **[TV_DISPATCHER.md](TV_DISPATCHER.md)** for its API spec.

**Location:** `/argus/tv/`

**Primary Files:**
- `__init__.py` - QuoteSession, ChartSession, TradingViewConnection (low-level, callback-based)
- `dispatcher.py` - TradingViewDispatcher, DispatcherQuoteSession, TVP2ConvertClass (dispatcher layer)
- `multisymbol.py` - legacy standalone asyncio `Ticker` (low-level)

**Key Features:**
- Real-time quote data (bid, ask, last, volume, changes)
- Historical chart data (OHLCV candles)
- Callback-based subscriptions (low-level) **and** full dispatcher with Protocol 2 (multi-client, non-Python clients)
- Optional authentication (works without credentials)

## Architecture

### Layering: Callback Sessions + Dispatcher

The low-level sessions use **direct WebSocket connections with callbacks**, and the dispatcher wraps a single multi-symbol session for network clients:

```
[Python code]            [TCP clients]
     ↓                        ↓
[QuoteSession / ChartSession]   [TradingViewDispatcher]
     \                        /
      \                      /
          [DispatcherQuoteSession]  (ONE shared TradingView WS, multi-symbol)
                       ↓
              [TradingView WebSocket]
```

**History:** the module originally shipped without a dispatcher (1. **Different use case** - focus on charting and historical data, not real-time trading),
2. **TradingView protocol** - Custom message encoding (`~m~<size>~m~<JSON>`)
3. **Callback-driven** - Natural fit for per-symbol callbacks
4. **No normalization needed** - Returns pandas DataFrames and MarketData objects

### Message Encoding

TradingView uses a custom WebSocket protocol:

**Format:**
```
~m~<size>~m~<JSON>

Where:
  ~m~: Delimiter
  <size>: Message size (ASCII integer)
  <JSON>: JSON-encoded message
```

**Example:**
```
~m~45~m~{"m":"quote_create_session","p":["qs_abc123"]}
```

**Heartbeat:**
```
~m~~h~<heartbeat_id>
```

## Components

### TradingViewConnection (Base Class)

Abstract base class for TradingView WebSocket connections.

**Key Methods:**

```python
class TradingViewConnection:
    def __init__(self, send_auth=True)

    def setup(self)
        # Send authentication and locale setup

    def craft_message(method, params) -> dict
        # Create message: {"m": method, "p": params}

    def send_msg(self, msg: dict)
        # Encode and send message

    @staticmethod
    def decode_message(raw_msg: str) -> dict
        # Decode TradingView message format

    def heartbeat_reply(self, heartbeat_msg)
        # Echo heartbeat back to server
```

### QuoteSession

Real-time quote data streaming for a single symbol.

**Initialization:**
```python
from argus.tv import QuoteSession

def my_callback(market_data):
    print(market_data)

quote = QuoteSession(symbol="NASDAQ:AAPL", callback=my_callback, sendAuth=False)
quote.setup()
quote.post_setup()
quote.ws.run_forever()  # Blocking
```

**MarketData Object:**
```python
class MarketData:
    last_price: float
    bid_price: float
    bid_size: float
    ask_price: float
    ask_size: float
    change_percentage: float
    change_value: float
```

**Quote Fields:**

The quote session subscribes to extensive market data fields:
- `lp` - Last price
- `ch` - Change value
- `chp` - Change percentage
- `ask` / `bid` - Ask/Bid prices
- `ask_size` / `bid_size` - Sizes
- `volume` - Trading volume
- `description` - Instrument description
- `exchange` - Exchange name
- `currency_code` - Currency
- And many more...

**Session Lifecycle:**
```
Create quote session (quote_create_session)
    ↓
Set fields (quote_set_fields)
    ↓
Add symbol (quote_add_symbols)
    ↓
Receive updates (qsd message)
    ↓
Callback invoked with MarketData
```

### ChartSession

Historical OHLCV (Open, High, Low, Close, Volume) data retrieval.

**Method Signature:**
```python
import threading

chart = ChartSession()
# get_symbol_data() blocks on the server response, so the socket must be running.
threading.Thread(target=lambda: chart.ws.run_forever(), daemon=True).start()

df = chart.get_symbol_data(
    symbol="NASDAQ:AAPL",
    interval="15",
    total_ticks=20
)
chart.ws.close()
```

**Parameters:**
- `symbol` (str) - TradingView symbol (e.g., `"NASDAQ:AAPL"`, `"BINANCE:BTCUSDT"`)
- `interval` (str) - Timeframe to retrieve. Pass as string:
- `total_ticks` (int) - Number of candles to retrieve

**Interval Values - What to Pass:**

Pass the interval as a **string** to the `interval` parameter:

| Timeframe | Pass | Description |
|-----------|------|-------------|
| 1 minute | `"1"` | 1-minute candles |
| 5 minutes | `"5"` | 5-minute candles |
| **15 minutes** | **`"15"`** | 15-minute candles |
| **1 hour** | **`"60"`** | 60-minute (1-hour) candles |
| 4 hours | `"240"` | 240-minute (4-hour) candles |
| **Daily** | **`"D"`** | Daily candles |
| Weekly | `"W"` | Weekly candles |
| Monthly | `"M"` | Monthly candles |

**Return Value:**

Returns a pandas DataFrame with columns:
- `Date` - datetime object of the candle timestamp
- `Open` - Opening price
- `High` - Highest price in the period
- `Low` - Lowest price in the period
- `Close` - Closing price
- `Volume` - Trading volume

**Notes:**

- `get_symbol_data()` sends the chart/series requests, blocks until the server's `timescale_update`, and converts the bars to a DataFrame — but the WebSocket must already be running (`ws.run_forever()`).
- Data is returned as a pandas DataFrame with datetime objects instead of Unix timestamps
- Works with both traditional stock exchanges and 24/7 cryptocurrency markets
- Bitcoin and other crypto assets have consistent intervals without market gaps (no weekends/holidays)

### Multi-Symbol Support

Production multi-symbol streaming lives in the dispatcher layer. `DispatcherQuoteSession`
(`argus/tv/dispatcher.py`) holds **many symbols on ONE upstream connection**, and
`TradingViewDispatcher` multiplexes that stream to TCP clients — see
[TV_DISPATCHER.md](TV_DISPATCHER.md). For direct use, `TradingViewQuoteWss` exposes
`subscribe_to_symbol()` / `unsubscribe_from_symbol()` / `symbols`.

`argus/tv/multisymbol.py` (`Ticker`) is an older standalone asyncio client kept for
backwards compatibility; the dispatcher does not use it.

## Usage Examples

### Example 1: Real-Time Quote Streaming

```python
from argus.tv import QuoteSession

def print_quote(data):
    print(f"Last: ${data.last_price:.2f}")
    print(f"Bid: ${data.bid_price:.2f} x {data.bid_size}")
    print(f"Ask: ${data.ask_price:.2f} x {data.ask_size}")
    print(f"Change: {data.change_percentage:.2f}%")
    print("-" * 40)

quote = QuoteSession(symbol="NASDAQ:TSLA", callback=print_quote, sendAuth=False)
quote.setup()
quote.post_setup()
quote.ws.run_forever()
```

**Output:**
```
Last: $242.50
Bid: $242.45 x 100
Ask: $242.55 x 150
Change: +1.23%
----------------------------------------
```

### Example 2: Historical Chart Data

```python
import threading
from argus.tv import ChartSession

chart = ChartSession()
threading.Thread(target=lambda: chart.ws.run_forever(), daemon=True).start()

# Last 30 daily candles for AAPL (blocks until the server responds)
df = chart.get_symbol_data(symbol="NASDAQ:AAPL", interval="D", total_ticks=30)
chart.ws.close()

print(f"Average Volume: {df['Volume'].mean():,.0f}")
print(f"Highest Price: ${df['High'].max():.2f}")
print(f"Lowest Price: ${df['Low'].min():.2f}")
print(f"Price Change: ${df['Close'].iloc[-1] - df['Close'].iloc[0]:.2f}")
```

### Example 3: Intraday Data

```python
import threading
from argus.tv import ChartSession

chart = ChartSession()
threading.Thread(target=lambda: chart.ws.run_forever(), daemon=True).start()

# Last 100 5-minute candles
df = chart.get_symbol_data(symbol="NASDAQ:QQQ", interval="5", total_ticks=100)
chart.ws.close()

# 'Date' is already a datetime; find the highest-volume bar
max_vol_bar = df.loc[df['Volume'].idxmax()]
print(f"Highest Volume Bar:")
print(f"  Time: {max_vol_bar['Date']}")
print(f"  Volume: {max_vol_bar['Volume']:,.0f}")
print(f"  Price: ${max_vol_bar['Close']:.2f}")
```

### Example 4: Multiple Symbols (Sequential)

```python
from argus.tv import QuoteSession
import threading
import time

symbols = ["NASDAQ:AAPL", "NASDAQ:GOOGL", "NASDAQ:MSFT"]

def create_quote_session(symbol):
    def callback(data):
        print(f"{symbol}: ${data.last_price:.2f} ({data.change_percentage:+.2f}%)")

    quote = QuoteSession(symbol=symbol, callback=callback, sendAuth=False)
    quote.setup()
    quote.post_setup()
    quote.ws.run_forever()

# Start each in a thread
threads = []
for symbol in symbols:
    t = threading.Thread(target=create_quote_session, args=(symbol,))
    t.start()
    threads.append(t)
    time.sleep(1)  # Stagger connections

# Wait for all
for t in threads:
    t.join()
```

### Example 5: Authenticated Access

```python
import os
from dotenv import load_dotenv
from argus.tv import QuoteSession

load_dotenv()

# With authentication (TOKEN env variable)
quote = QuoteSession(
    symbol="NASDAQ:AAPL",
    callback=lambda d: print(d.last_price),
    sendAuth=True  # Requires TOKEN in .env
)

quote.setup()
quote.post_setup()
quote.ws.run_forever()
```

**.env file:**
```
TOKEN=your_tradingview_auth_token
```

**Note:** Authentication is **optional** for most use cases. Unauthenticated access works for public market data.

### Example 6: Chart Data Export

```python
import threading
from argus.tv import ChartSession

chart = ChartSession()
threading.Thread(target=lambda: chart.ws.run_forever(), daemon=True).start()
df = chart.get_symbol_data(symbol="NASDAQ:AAPL", interval="D", total_ticks=365)
chart.ws.close()

df.to_csv('aapl_daily_1year.csv', index=False)
print(f"Exported {len(df)} bars to aapl_daily_1year.csv")
df.to_json('aapl_daily_1year.json', orient='records')
```

## Configuration

### Environment Variables

```bash
# Optional (only needed for authenticated access)
TOKEN=your_tradingview_auth_token
```

**How to get TOKEN:**
1. Log in to TradingView website
2. Open browser Developer Tools (F12)
3. Go to Network tab
4. Find WebSocket connection to `data.tradingview.com`
5. Look for `set_auth_token` message in frames
6. Copy the token value

**Note:** Most features work without authentication.

### Symbol Format

TradingView symbols follow the format: `EXCHANGE:SYMBOL`

**Examples:**
- `NASDAQ:AAPL` - Apple Inc.
- `NYSE:BA` - Boeing
- `BINANCE:BTCUSDT` - Bitcoin/USDT
- `FX:EURUSD` - Euro/USD Forex
- `CRYPTO:ETHUSD` - Ethereum/USD

**Invalid formats:**
- ❌ `AAPL` (missing exchange)
- ❌ `NASDAQ-AAPL` (wrong delimiter)

## Limitations

### 1. ~~Not a Dispatcher~~ — RESOLVED

`TradingViewDispatcher` (`argus/tv/dispatcher.py`, spec in [TV_DISPATCHER.md](TV_DISPATCHER.md)) provides the TCP server, Protocol 1 request/response and Protocol 2 streaming with multi-client multiplexing over ONE shared upstream session. The low-level `QuoteSession`/`ChartSession` classes remain callback-based for direct Python use.

### 2. Low-Level Sessions Are Callback-Based (Not Stream-Based)

**Constraint:** The raw `QuoteSession`/`ChartSession` deliver data via callbacks, not continuous streams.

**Impact:**
- Direct-session use cannot connect from non-Python clients
- Different API than the dispatcher layer

**Workaround:**
- Use `TradingViewDispatcher` for network clients (it bridges callbacks → Protocol 2)

### 3. Low-Level `QuoteSession` is Single-Symbol

**Constraint:** A low-level `QuoteSession` handles one symbol; `ChartSession` fetches one series at a time.

**Impact:**
- Multiple low-level sessions mean one WebSocket per symbol

**Workaround:**
- Use `DispatcherQuoteSession` / `TradingViewDispatcher` (ONE shared upstream socket, many symbols)

### 4. Historical Data Delay & No Pagination

**Constraint:** `get_symbol_data()` blocks until the server's initial `timescale_update`; there is no incremental/paginated fetch, so very large `total_ticks` values are not paged automatically.

**Workaround:**
```python
import threading
chart = ChartSession()
threading.Thread(target=lambda: chart.ws.run_forever(), daemon=True).start()
df = chart.get_symbol_data(symbol="NASDAQ:AAPL", interval="D", total_ticks=300)
chart.ws.close()
```

### 5. No Real-Time Trade Data

**Constraint:** Provides quote data (bid/ask/last), not individual trades.

**Impact:**
- Cannot analyze trade-by-trade flow
- No tape/time & sales

**Workaround:**
- Use volume data as proxy
- Connect to exchange APIs directly for trade data

### 6. Pandas Dependency

**Constraint:** `ChartSession` returns pandas DataFrame.

**Impact:**
- Requires pandas installation
- Memory overhead for large datasets

**Workaround:**
- Convert DataFrame to dict/list if needed
- Use NumPy directly if pandas is overkill

### 7. WebSocket Blocking

**Constraint:** `ws.run_forever()` is blocking.

**Impact:**
- Blocks main thread
- Must use threading for multiple sessions

**Workaround:**
```python
import threading

def run_quote():
    quote = QuoteSession(...)
    quote.setup()
    quote.post_setup()
    quote.ws.run_forever()

thread = threading.Thread(target=run_quote)
thread.start()
```

## Use Cases

### Best For:
- ✅ Historical data analysis
- ✅ Chart data visualization
- ✅ Backtesting strategies
- ✅ Multi-asset research
- ✅ Real-time quote monitoring

### Not Suitable For:
- ❌ High-frequency trading (use exchange APIs)
- ❌ Order execution (TradingView is data-only)

## File Reference

```
argus/tv/
├── __init__.py        # QuoteSession, ChartSession, TradingViewConnection (low-level)
├── dispatcher.py      # TradingViewDispatcher, DispatcherQuoteSession, TVP2ConvertClass
└── multisymbol.py     # legacy standalone Ticker (not used by the dispatcher)
```

## Summary

The TradingView module provides convenient access to TradingView's charting and quote data, with a full dispatcher layer on top of the callback sessions:

**Key Characteristics:**
- ✅ Dispatcher pattern (`TradingViewDispatcher`, Protocol 1 + Protocol 2 — see [TV_DISPATCHER.md](TV_DISPATCHER.md))
- ✅ Callback-based subscriptions (low-level sessions)
- ✅ Pandas DataFrame output
- ✅ Optional authentication
- ✅ Rich historical data

**Design Philosophy:**

The module is designed for **data analysis and research**, not real-time trading infrastructure. It excels at:
- Fetching historical OHLCV data
- Monitoring real-time quotes
- Supporting backtesting workflows
- Providing multi-exchange coverage

For integration into trading systems requiring Protocol 2 or multi-client support, use `TradingViewDispatcher` (`python runtime.py tradingview`) — it multiplexes all client subscriptions over a single shared TradingView quote session.

**Complementary to Other Modules:**

Use TradingView module alongside other Argus modules:
- **TradingView** → Historical data, charting
- **IB/Capital/Binance** → Real-time trading, order execution

Together, they provide a complete data infrastructure for quantitative trading and research.
