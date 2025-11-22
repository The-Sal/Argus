# TradingView (TV) Module

The TradingView module provides real-time quote data and historical chart data from TradingView's WebSocket API.

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Components](#components)
- [Usage Examples](#usage-examples)
- [Limitations](#limitations)

## Overview

The TradingView module **does NOT follow the dispatcher paradigm**. Instead, it uses a **callback-based architecture** similar to the Polymarket module.

**Location:** `/argus/tv/`

**Primary Files:**
- `__init__.py` (448 lines) - QuoteSession, ChartSession, TradingViewConnection
- `multisymbol.py` - Multi-symbol quote streaming

**Key Features:**
- Real-time quote data (bid, ask, last, volume, changes)
- Historical chart data (OHLCV candles)
- Callback-based subscriptions
- No dispatcher / No Protocol 2
- Optional authentication (works without credentials)

## Architecture

### Why Not a Dispatcher?

The TradingView module uses **direct WebSocket connections with callbacks**:

```
[Client Code]
     ↓
[QuoteSession / ChartSession]
     ↓
[TradingView WebSocket]
```

**Reasons:**
1. **Different use case** - Focus on charting and historical data, not real-time trading
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

**Initialization:**
```python
from argus.tv import ChartSession

chart = ChartSession(
    symbol="NASDAQ:AAPL",
    interval="D",  # Daily candles
    range_value=300,  # Number of bars
    sendAuth=False
)
```

**Supported Intervals:**
- `1` - 1 minute
- `5` - 5 minutes
- `15` - 15 minutes
- `60` - 1 hour
- `240` - 4 hours
- `D` - Daily
- `W` - Weekly
- `M` - Monthly

**Get Historical Data:**
```python
chart.setup()
chart.post_setup()

# Wait for data to arrive
time.sleep(5)

# Retrieve as pandas DataFrame
df = chart.get_chart_data()

print(df)
#         time   open   high    low  close    volume
# 0 1609459200  132.0  133.6  130.5  131.0  99310000
# 1 1609545600  131.8  134.5  131.3  133.7 106260000
# ...
```

**DataFrame Columns:**
- `time` - Unix timestamp
- `open` - Opening price
- `high` - Highest price
- `low` - Lowest price
- `close` - Closing price
- `volume` - Trading volume

**Processing:**

The `ChartSession` accumulates chart data messages and converts them to a pandas DataFrame:

```python
def get_chart_data(self) -> pd.DataFrame:
    # Combine multiple data batches
    # Convert to structured format
    # Return as DataFrame
```

### Multi-Symbol Support

**File:** `argus/tv/multisymbol.py`

Allows subscribing to multiple symbols simultaneously:

```python
# Implementation details in multisymbol.py
# Can extend QuoteSession for multiple symbols
```

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
from argus.tv import ChartSession
import time

# Daily candles for AAPL
chart = ChartSession(
    symbol="NASDAQ:AAPL",
    interval="D",
    range_value=30,  # Last 30 days
    sendAuth=False
)

chart.setup()
chart.post_setup()

# Wait for data
time.sleep(5)

# Get DataFrame
df = chart.get_chart_data()

# Calculate statistics
print(f"Average Volume: {df['volume'].mean():,.0f}")
print(f"Highest Price: ${df['high'].max():.2f}")
print(f"Lowest Price: ${df['low'].min():.2f}")
print(f"Price Change: ${df['close'].iloc[-1] - df['close'].iloc[0]:.2f}")

# Plot (requires matplotlib)
# df.plot(x='time', y='close')
```

### Example 3: Intraday Data

```python
from argus.tv import ChartSession
import time

# 5-minute candles
chart = ChartSession(
    symbol="NASDAQ:QQQ",
    interval="5",
    range_value=100,  # Last 100 bars
    sendAuth=False
)

chart.setup()
chart.post_setup()
time.sleep(5)

df = chart.get_chart_data()

# Convert timestamp to datetime
df['datetime'] = pd.to_datetime(df['time'], unit='s')

# Find highest volume bar
max_vol_idx = df['volume'].idxmax()
max_vol_bar = df.loc[max_vol_idx]

print(f"Highest Volume Bar:")
print(f"  Time: {max_vol_bar['datetime']}")
print(f"  Volume: {max_vol_bar['volume']:,.0f}")
print(f"  Price: ${max_vol_bar['close']:.2f}")
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
from argus.tv import ChartSession
import time

chart = ChartSession(symbol="NASDAQ:AAPL", interval="D", range_value=365, sendAuth=False)
chart.setup()
chart.post_setup()
time.sleep(10)  # Wait for full year of data

df = chart.get_chart_data()

# Export to CSV
df.to_csv('aapl_daily_1year.csv', index=False)
print(f"Exported {len(df)} bars to aapl_daily_1year.csv")

# Export to JSON
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

### 1. Not a Dispatcher

**Constraint:** Does not follow Argus dispatcher pattern.

**Impact:**
- No TCP/UDS server
- No Protocol 2 streaming
- No multi-client multiplexing
- Direct Python integration only

**Workaround:**
- Use directly in Python code
- Build custom dispatcher wrapper if needed

### 2. Callback-Based (Not Stream-Based)

**Constraint:** Data delivered via callbacks, not continuous stream.

**Impact:**
- Cannot connect from non-Python clients
- Different API than IB/Capital/Binance modules

**Workaround:**
- Accept callback paradigm
- Bridge to Protocol 2 if needed (custom implementation)

### 3. Single Symbol Per Session

**Constraint:** Each `QuoteSession` handles one symbol.

**Impact:**
- Need multiple sessions for multiple symbols
- Higher overhead (one WebSocket per symbol)

**Workaround:**
- Use `multisymbol.py` utilities
- Manage multiple sessions with threading

### 4. Historical Data Delay

**Constraint:** Chart data arrives asynchronously.

**Impact:**
- Must wait (sleep) before calling `get_chart_data()`
- No explicit "data ready" signal

**Workaround:**
```python
chart.setup()
chart.post_setup()
time.sleep(5)  # Adjust based on range_value
df = chart.get_chart_data()
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
- ❌ Multi-client data distribution (no dispatcher)
- ❌ Non-Python integrations
- ❌ Order execution (TradingView is data-only)

## File Reference

```
argus/tv/
├── __init__.py        # QuoteSession, ChartSession, TradingViewConnection
└── multisymbol.py     # Multi-symbol utilities
```

## Summary

The TradingView module provides convenient access to TradingView's charting and quote data, **diverging from Argus's standard dispatcher architecture**:

**Key Characteristics:**
- ❌ No dispatcher pattern
- ❌ No Protocol 2
- ✅ Callback-based subscriptions
- ✅ Pandas DataFrame output
- ✅ Optional authentication
- ✅ Rich historical data

**Design Philosophy:**

The module is designed for **data analysis and research**, not real-time trading infrastructure. It excels at:
- Fetching historical OHLCV data
- Monitoring real-time quotes
- Supporting backtesting workflows
- Providing multi-exchange coverage

For integration into trading systems requiring Protocol 2 or multi-client support, consider:
1. Building a custom dispatcher wrapper around `QuoteSession`
2. Using TradingView for research, other modules for live trading
3. Exporting data to files and processing separately

**Complementary to Other Modules:**

Use TradingView module alongside other Argus modules:
- **TradingView** → Historical data, charting
- **IB/Capital/Binance** → Real-time trading, order execution

Together, they provide a complete data infrastructure for quantitative trading and research.
