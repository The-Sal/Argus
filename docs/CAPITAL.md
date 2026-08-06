# Capital.com Module

The Capital.com module provides real-time CFD and forex market data through the Capital.com API with both demo and live trading environments.

## Table of Contents

- [Overview](#overview)
- [Dispatcher](#dispatcher)
- [Features](#features)
- [API Surface](#api-surface)
- [Internal Architecture](#internal-architecture)
- [Client Usage](#client-usage)
- [Protocols](#protocols)
- [Limitations](#limitations)
- [Usage Examples](#usage-examples)

## Overview

The Capital.com module follows the standard Argus dispatcher paradigm, providing a Unix Domain Socket (UDS) server that bridges between Capital.com's WebSocket API and multiple clients.

**Location:** `/argus/capital/`

**Primary Files:**
- `__init__.py` (443 lines) - MKTDispatcher and core server logic
- `client.py` (420 lines) - CapitalComClient implementation
- `_svr_utils.py` (470 lines) - Protocol 2 parser and packet encoding
- `_lib.py` - Capital.com API wrapper and WebSocket client
- `_caches.py` - Domain-specific caching utilities

**Key Features:**
- Unix Domain Socket (UDS) for local IPC
- Dual-protocol support (Protocol 1 for control, Protocol 2 for data)
- Symbol resolution caching
- Demo and Live environments
- Interactive CLI client

## Dispatcher

### MKTDispatcher - `capital.com`

**Purpose:** Real-time CFD/Forex market data from Capital.com

**Transport:** Unix Domain Socket (UDS)

**Socket Path:** `/tmp/argus_capital.sock`

**Caching:** ✅ Yes (symbol resolution)

**Data Protocol:** Protocol 2 (market data) + Protocol 1 (control messages)

**Concurrent Clients:** ✅ Full support

**Environments:**
- `Environment.DEMO` - Demo trading environment (default)
- `Environment.LIVE` - Live trading environment

**Launch:**
```bash
# Demo environment
python runtime.py capital.com --capital-env demo

# Live environment
python runtime.py capital.com --capital-env live
```

## Features

### Feature Matrix

| Feature                   | Support | Notes                           |
|---------------------------|---------|---------------------------------|
| Real-time market data     | ✅      | Bid/Ask/Last via WebSocket      |
| Symbol resolution         | ✅      | Cached lookups for EPIC format  |
| Multi-client support      | ✅      | UDS allows multiple connections |
| Protocol 2 streaming      | ✅      | Normalized data format          |
| Batch symbol subscription | ✅      | Load from file                  |
| Unsubscribe               | ✅      | Clean disconnection             |
| Caching                   | ✅      | Symbol resolution cached        |
| Demo environment          | ✅      | Paper trading                   |
| Live environment          | ✅      | Real trading                    |

### Why Unix Domain Socket?

Unlike the IB module (TCP), Capital.com dispatcher uses **Unix Domain Socket** (UDS):

**Advantages:**
- **Lower latency** - No TCP/IP stack overhead
- **Higher throughput** - Direct kernel IPC
- **Security** - Filesystem permissions, no network exposure
- **Reliability** - No port conflicts

**Disadvantages:**
- **Local only** - Cannot connect from remote machines
- **Platform-specific** - Linux/macOS (not native Windows)

For remote access, consider SSH tunneling or switching to TCP (requires code modification).

## API Surface

### Client-Side Actions (Protocol 1)

Clients send JSON-encoded actions to the dispatcher:

| Action                      | Parameters | Description                          |
|-----------------------------|------------|--------------------------------------|
| `resolve_symbol`            | `symbol`   | Resolve ticker to Capital.com EPIC   |
| `stream_epic`               | `epic`     | Start streaming market data for EPIC |
| `resolve/stream`            | `symbol`   | Resolve and stream in one call       |
| `unsubscribe`               | `epic`     | Stop streaming for EPIC              |
| `resolve/stream/batch/file` | `file`     | Bulk subscribe from file             |

#### Action Examples

**1. Resolve Symbol:**
```json
{
  "action": "resolve_symbol",
  "symbol": "BTCUSD"
}
```

**Response:**
```json
{
  "object": "Response",
  "status": "success",
  "data": {
    "instrument": {
      "epic": "BTCUSD",
      "name": "Bitcoin vs US Dollar",
      "type": "CRYPTOCURRENCIES",
      ...
    }
  }
}
```

**2. Stream EPIC:**
```json
{
  "action": "stream_epic",
  "epic": "BTCUSD"
}
```

**3. Resolve and Stream (Recommended):**
```json
{
  "action": "resolve/stream",
  "symbol": "ETHUSD"
}
```

**4. Unsubscribe:**
```json
{
  "action": "unsubscribe",
  "epic": "BTCUSD"
}
```

**5. Batch Subscribe from File:**
```json
{
  "action": "resolve/stream/batch/file",
  "file": "/path/to/symbols.txt"
}
```

**File format** (`symbols.txt`):
```
BTCUSD
ETHUSD
GBPUSD
EURUSD
```

### Market Data Stream (Protocol 2)

Once subscribed, market data flows continuously in Protocol 2 format:

```
~<packet-length><symbol-length>|<symbol><market-data>L

Fields (in order):
  1. bid
  2. bid_size
  3. ask
  4. ask_size
  5. last
  6. last_size
  7. timestamp (Capital.com server timestamp)
  8. transmission_time (Argus transmission timestamp)
```

**Example:**
```
~00710006|BTCUSD105099.85,0.2,105149.85,0.2,0.0,0.0,1750217540462,1750217540500L
```

**Parsed:**
- Symbol: `BTCUSD`
- Bid: $105,099.85 × 0.2
- Ask: $105,149.85 × 0.2
- Last: $0.00 (not provided by Capital.com)
- Timestamp: 1750217540462

## Internal Architecture

### Component Stack

```
[Capital.com WebSocket Server]
           ↓
    [CapitalComAPI]
           ↓
   [WebSocket Handler]
           ↓
  [Market Data Callback]
           ↓
   [CapitalComMKTDataLive]
           ↓
   [Protocol 2 Encoding]
           ↓
    [MKTDispatcher]
           ↓
   [Unix Domain Socket]
           ↓
  [Multiple UDS Clients]
```

### Class Hierarchy

```
SvrExport (Base UDS Server)
    ↓
MKTDispatcher (Capital.com-specific)
    ├── CapitalComAPI (WebSocket + REST)
    ├── DomainCache (Symbol resolution caching)
    └── TransferPROTOCOL (Dual protocol support)
```

### Threading Model

1. **Main Thread** - UDS server accept loop
2. **Client Thread(s)** - One per connected client
3. **WebSocket Thread** - Capital.com WebSocket `run_forever()` loop
4. **Callback Thread** - Market data processing

**Thread Safety:**
- Client list protected by implicit socket locks
- Epic streams tracked in thread-safe dict
- Cache operations are thread-safe

### Data Classes

#### CapitalComMKTDataLive

The core market data object:

```python
class CapitalComMKTDataLive:
    symbol: str
    bid: float
    bid_size: float
    ask: float
    ask_size: float
    last: float
    last_size: float
    timestamp: int

    def transferable(self) -> dict
        # Protocol 1 JSON representation

    def transferable_2(self, encode=True) -> bytes | list[str]
        # Protocol 2 CSV representation

    @classmethod
    def from_protocol_2(cls, data: bytes)
        # Parse Protocol 2 packet
```

**Type Enforcement:**
All fields are type-checked and auto-converted using `@assertTypes` decorator.

#### TransferPROTOCOL

Protocol version constants:

```python
class TransferPROTOCOL:
    VERSION_1 = 1  # Control messages (JSON)
    VERSION_2 = 2  # Market data (CSV)
```

### Symbol Resolution and Caching

Capital.com uses **EPIC** identifiers instead of traditional ticker symbols. The dispatcher automatically resolves tickers to EPICs:

**Resolution Flow:**

```
Client sends: "BTCUSD"
    ↓
Check cache: ~/.argus/capital_cache.pkl
    ↓
Cache miss → API call: get_market_details("BTCUSD")
    ↓
Success? → Cache result, return EPIC
    ↓
Failure? → Search API: search_market_for_security("BTCUSD")
    ↓
Match found? → get_market_details(epic) → Cache result
    ↓
No match? → Return error to client
```

**Cache Decorator:**
```python
@CACHE.cache_decorator('resolve_symbol')
def resolve_symbol(self, symbol: str, market: str = None):
    # Expensive API call cached automatically
```

**Cache Location:** `~/.argus/capital_cache.pkl`

**Disable Caching:**
```bash
export ARGUS_CACHES_DISABLED=1
```

## Client Usage

### Option 1: CapitalComClient (Recommended)

Use the built-in client library:

```python
from argus.capital.client import CapitalComClient

client = CapitalComClient()
client.connect()

def my_callback(symbol, data):
    print(f"{symbol}: bid={data.bid}, ask={data.ask}")

client.stream_symbols(['BTCUSD', 'ETHUSD'], callback=my_callback)

# Keep running
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    client.close()
```

**Features:**
- Automatic reconnection
- State tracking (`client.states`)
- Protocol 1 and 2 handling
- Callback-based API

**Concurrency Limit:**
- Maximum **30 symbols** concurrently to avoid rate limiting
- Raises exception if exceeded

### Option 2: Raw Socket (Advanced)

Direct UDS connection:

```python
import socket
import json
from argus.capital import encode_packet
from argus.protocol import Protocol2Parser

# Connect to UDS
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect('/tmp/argus_capital.sock')

# Send resolve/stream request
request = json.dumps({'action': 'resolve/stream', 'symbol': 'BTCUSD'})
packet = encode_packet(request.encode('ascii'))
s.sendall(packet)

# Parse Protocol 2 stream
parser = Protocol2Parser([
    'bid', 'bid_size', 'ask', 'ask_size',
    'last', 'last_size', 'timestamp', 'transmission_time'
])

while True:
    data = s.recv(4096)
    # Handle both Protocol 1 and Protocol 2
    if data[5:6] == b'|':
        # Protocol 1 (control message)
        payload = data[6:]
        print(json.loads(payload))
    else:
        # Protocol 2 (market data)
        result = parser.parse(data)
        print(result)
```

### Option 3: Interactive CLI

The module includes an interactive CLI client:

```bash
python -m argus.capital.client
```

**Features:**
- **Edit Mode** - Add/remove symbols interactively
- **View Mode** - Real-time display of market data
- Auto-reconnection on disconnect

*Note: The interactive CLI is referenced in the codebase but requires additional implementation for full functionality. Use `CapitalComClient` programmatically for production.*

## Protocols

### Protocol 1: Control Messages

**Purpose:** Client → Server commands and Server → Client responses

**Format:**
```
~<data-length>|<json-data>

Where:
  ~: Start marker
  <data-length>: 4-byte ASCII integer
  |: Delimiter
  <json-data>: JSON-encoded message
```

**Example Request:**
```
~0038|{"action": "resolve_symbol", "symbol": "BTCUSD"}
```

**Example Response:**
```
~0123|{"object": "Response", "status": "success", "data": {...}}
```

**Encoding Helper:**
```python
from argus.capital import encode_packet
packet = encode_packet(b'{"action": "stream_epic", "epic": "BTCUSD"}')
```

**Decoding Helper:**
```python
from argus.protocol import decode_packet
data = decode_packet(packet)  # Returns bytes
```

### Protocol 2: Market Data Stream

**Purpose:** Server → Client real-time market updates

**Format:**
```
~<packet-length><symbol-length>|<symbol><csv-data>L

Where:
  ~: Start marker
  <packet-length>: 4-byte ASCII integer (total packet size excluding header)
  <symbol-length>: 4-byte ASCII integer
  |: Delimiter
  <symbol>: Ticker symbol (ASCII)
  <csv-data>: 8 comma-separated fields
  L: Terminator
```

**CSV Fields (in order):**
1. `bid`
2. `bid_size`
3. `ask`
4. `ask_size`
5. `last`
6. `last_size`
7. `timestamp` (Capital.com server timestamp in milliseconds)
8. `transmission_time` (Argus transmission timestamp)

**Example:**
```
~00710006|BTCUSD105099.85,0.2,105149.85,0.2,0.0,0.0,1750217540462,1750217540500L
```

**Parsing:**
```python
from argus.protocol import Protocol2Parser

parser = Protocol2Parser([
    'bid', 'bid_size', 'ask', 'ask_size',
    'last', 'last_size', 'timestamp', 'transmission_time'
])

result = parser.parse(packet)
# {'symbol': 'BTCUSD', 'bid': 105099.85, 'bid_size': 0.2, ...}
```

### Why Dual Protocols?

| Aspect | Protocol 1 | Protocol 2 |
|--------|-----------|-----------|
| **Purpose** | Control/commands | Market data |
| **Format** | JSON | CSV |
| **Size** | ~100-500 bytes | ~70 bytes |
| **Frequency** | Low (on-demand) | High (continuous) |
| **Parsing** | `json.loads()` | O(n) single-pass |
| **Flexibility** | High (arbitrary fields) | Low (fixed schema) |

**Design Rationale:**
- **Protocol 1** provides flexibility for control messages (new actions can be added)
- **Protocol 2** provides efficiency for high-frequency market data
- Clients automatically differentiate by inspecting byte position 5 (`|` = P1, else P2)

## Limitations

### 1. Local-Only Access (UDS)

**Constraint:** Unix Domain Sockets only work on the same machine.

**Impact:**
- Cannot connect from remote servers
- Not suitable for distributed systems

**Workarounds:**
- SSH tunneling (not native)
- Modify dispatcher to use TCP (requires code changes)
- Run dispatcher on same machine as trading algorithms

### 2. Capital.com Rate Limiting

**Constraint:** Capital.com API has rate limits and concurrency restrictions.

**Impact:**
- Maximum **~30 concurrent symbol subscriptions** recommended
- Bulk operations (`resolve/stream/batch/file`) include 0.1s sleep per symbol
- Excessive subscriptions may trigger API throttling

**Mitigation:**
- Track active subscriptions
- Unsubscribe from unused symbols
- Use caching to reduce API calls

### 3. Missing "Last" Price

**Constraint:** Capital.com WebSocket provides `bid`/`ask` but not always `last` traded price.

**Impact:**
- `last` and `last_size` fields often `0.0` in Protocol 2 packets
- Cannot calculate volume-weighted average price (VWAP)

**Workaround:**
- Use midpoint: `(bid + ask) / 2`
- Fetch historical trades via REST API (not implemented in dispatcher)

### 4. Environment Switching

**Constraint:** Cannot run both DEMO and LIVE environments simultaneously.

**Impact:**
- Must stop dispatcher to switch environments
- No parallel testing against demo while running live

**Workaround:**
- Run two separate dispatcher instances with different UDS paths (requires code modification)
- Use multiple Capital.com accounts

### 5. Platform Support

**Constraint:** UDS is POSIX-only (Linux, macOS).

**Impact:**
- Windows requires WSL or Windows 10+ AF_UNIX support
- Native Windows not tested

## Usage Examples

### Example 1: Basic Crypto Streaming

```python
from argus.capital.client import CapitalComClient
import time

client = CapitalComClient()
client.connect()

def print_tick(symbol, data):
    spread = data.ask - data.bid
    print(f"{symbol}: ${data.bid:.2f} / ${data.ask:.2f} (spread: ${spread:.2f})")

symbols = ['BTCUSD', 'ETHUSD', 'SOLUSD']
client.stream_symbols(symbols, callback=print_tick)

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    client.close()
    print("Disconnected.")
```

## Environment Variables

```bash
# Required
CAPITAL_DOTCOM_API_KEY="your_api_key"
CAPITAL_DOT_CUSTOM_PW="your_password"
CAPITAL_DOTCOM_IDENTIFIER="your_identifier"

# Optional
ARGUS_CACHES_DISABLED=0  # Set to 1 to disable caching
```

## Troubleshooting

### Cannot Connect to Socket

**Symptoms:** `socket.error: [Errno 2] No such file or directory`

**Causes:**
- Dispatcher not running
- Wrong socket path
- Permissions issue

**Solutions:**
1. Start dispatcher: `python runtime.py capital.com`
2. Verify socket exists: `ls -la /tmp/argus_capital.sock`
3. Check permissions: `chmod 666 /tmp/argus_capital.sock`

### Symbol Not Resolving

**Symptoms:** Server response: `"Symbol 'XYZ' could not be resolved"`

**Causes:**
- Symbol not available on Capital.com
- Incorrect symbol format
- Market closed (some markets)

**Solutions:**
1. Search manually on Capital.com web platform
2. Check symbol format (e.g., `BTC/USD` vs `BTCUSD`)
3. Use correct EPIC directly: `{'action': 'stream_epic', 'epic': 'BTCUSD'}`

### Rate Limiting / Throttling

**Symptoms:** WebSocket disconnects, error messages

**Causes:**
- Too many concurrent subscriptions
- Bulk operations too fast

**Solutions:**
1. Limit to 30 symbols max
2. Increase sleep time in batch operations
3. Unsubscribe from unused symbols

### Protocol 2 Parsing Errors

**Symptoms:** `ValueError: Invalid data length for protocol 2`

**Causes:**
- Partial packet received
- Buffer desync

**Solutions:**
1. Use `CapitalComClient` (handles buffering automatically)
2. If raw socket, implement proper packet framing
3. Check for `~` start marker and `L` terminator

## File Reference

```
argus/capital/
├── __init__.py           # MKTDispatcher, SvrExport, CapitalComMKTDataLive
├── client.py             # CapitalComClient (UDS client implementation)
├── _svr_utils.py         # Protocol 2 parser, packet encoding/decoding
├── _lib.py               # CapitalComAPI (WebSocket + REST wrapper)
└── _caches.py            # DomainCache, FastCache utilities
```

## Summary

The Capital.com module provides a robust, high-performance dispatcher for CFD and forex market data with the following highlights:

**Strengths:**
- ✅ **Unix Domain Socket** for ultra-low latency local IPC
- ✅ **Dual-protocol design** (flexible control + efficient data)
- ✅ **Comprehensive caching** to minimize API calls
- ✅ **Demo environment** for safe testing
- ✅ **Built-in client library** with state tracking

**Key Design Choices:**
- **UDS over TCP** - Better performance for local deployments
- **Protocol 1 + Protocol 2** - Optimal balance of flexibility and efficiency
- **Automatic symbol resolution** - Abstracts Capital.com EPIC complexity

**Best For:**
- Low-latency local trading systems
- Multi-client market data distribution
- Forex and crypto CFD trading
- Research and backtesting (demo mode)

**Not Suitable For:**
- Distributed systems (UDS limitation)
- High-volume subscriptions (>30 symbols)
- Remote data access without SSH tunneling

For production deployments requiring remote access, consider modifying the dispatcher to support TCP in addition to UDS.
