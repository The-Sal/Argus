# Python vs Swift Feature Comparison for Argus Dispatchers

This document provides a **thorough, in-depth comparison** of dispatcher features available in the Python and Swift implementations of Argus. Only modules with dispatcher support are analyzed.

**Scope:** This comparison focuses exclusively on modules that implement the dispatcher pattern for real-time market data streaming. Non-dispatcher modules (NASDAQ, TradingView, Cache Utils) are excluded as they serve different purposes.

---

## Executive Summary

| Module | Python Status | Swift Status | Feature Parity | Notes |
|--------|--------------|--------------|----------------|-------|
| **Binance** | ✅ Full | ✅ Full | ~90% | Swift has 5 interactive commands vs Python's extensive Introspective framework |
| **Capital.com** | ✅ Full | ⚠️ Limited | ~85% | Swift missing client library, disk caching, batch file loading |
| **Interactive Brokers (IB)** | ✅ Full | ⚠️ Limited | ~55% | **Swift has empty interactive mode with NO commands**, missing 4 of 5 dispatcher modes, no disk caching, no shortable shares |
| **IB Forecast** | ✅ Full | ⚠️ Limited | ~80% | Swift missing disk caching, limited interactive commands |
| **Polymarket** | ⚠️ Stub | ⚠️ Example | N/A | Both implementations are non-functional for dispatcher use |

**Legend:**
- ✅ Full: Feature-complete dispatcher implementation
- ⚠️ Limited/Partial: Major features missing or non-functional
- ❌ Not Available: Module does not exist

---

## 1. Binance Dispatcher

### Python Implementation (`argus/binance/`)
**Files:** `__init__.py` (565 lines), `_classes.py` (232 lines)

#### Dispatcher Features:
- ✅ **BinanceMKTDispatcher:** TCP server on port 9982
- ✅ **Protocol 2 Support:** Normalized CSV-based market data format
- ✅ **WebSocket Streaming:** Combined stream for depth, trades, klines
- ✅ **Multi-client Support:** Unlimited TCP clients
- ✅ **Dynamic Subscription Management:** Add/remove symbols on demand
- ✅ **Data Merging:** Combines depth (100ms), aggTrade, and kline_1s
- ✅ **Thread Safety:** Proper locking for concurrent operations
- ✅ **Testnet Support:** Can connect to testnet.binance.vision
- ✅ **Caching:** Last-known market data cached per symbol
- ✅ **Lazy Subscription:** Only subscribes to Binance when first client requests

#### Interactive Mode (via Introspective base class):
Python's Binance dispatcher extends `Introspective` which provides:

**Interactive Menu Options:**
1. **show_subscriptions** - Display all active symbol subscriptions with client counts
2. **show_clients** - Display all connected TCP clients with addresses
3. **modify_configs** - Interactively modify runtime configurations
4. **call_method** - Dynamically call any public method of the dispatcher
5. **exit** - Exit interactive mode

**Runtime Configurations:**
- `Print data packets` - Toggle packet printing for debugging
- `Use TQDM Progress bar` - Show progress bars for operations
- Any custom config added by the dispatcher

**Key Python Advantage:** The `Introspective` base class provides a **complete framework** for runtime introspection:
- Dynamically discover and call any public method
- Modify configurations without restart
- Extensive debugging capabilities
- Extensible architecture for adding new commands

### Swift Implementation (`argus_swift/Sources/ArgusServer/`)
**Files:** `MKTDispatcher.swift` (426 lines), `BinanceWebSocket.swift` (379 lines), `BinanceClasses.swift` (309 lines)

#### Dispatcher Features:
- ✅ **MKTDispatcher:** TCP server (default port 9982, configurable)
- ✅ **Protocol 2 Support:** Normalized CSV-based market data format  
- ✅ **WebSocket Streaming:** Combined stream using URLSession
- ✅ **Multi-client Support:** Unlimited TCP clients
- ✅ **Dynamic Subscription Management:** Add/remove symbols via TCP commands
- ✅ **Data Merging:** Combines depth, trades, klines
- ✅ **Thread Safety:** NSLock for concurrent operations
- ✅ **Testnet Support:** Can connect to testnet
- ✅ **Caching:** In-memory market data cache per symbol
- ✅ **FakeSocket Pattern:** Elegant pattern for in-memory subscriptions
- ✅ **Zero Dependencies:** Native URLSession WebSockets

#### Interactive Mode:
Swift Binance has a **functional interactive menu** with **5 commands**:

**Interactive Menu:**
```
Options:
1. Show subscribed symbols
2. Show connected clients  
3. Toggle packet printing
4. Add symbol manually
5. Remove symbol manually
0. Exit
```

**Implemented Commands:**
1. **Show subscribed symbols** - Lists all symbols with client counts
2. **Show connected clients** - Displays count of connected clients
3. **Toggle packet printing** - Toggles `configs["Print data packets"]`
4. **Add symbol manually** - Subscribe via FakeSocket (no TCP overhead)
5. **Remove symbol manually** - Unsubscribe from symbol

**Configurations:**
- `Print data packets` (Boolean) - Toggle packet printing

### Missing in Swift:
- ❌ **Introspective Framework:** No base class for extensible introspection
- ❌ **call_method capability:** Cannot dynamically call arbitrary methods
- ❌ **Extensive configurations:** Only 1 config vs Python's framework for multiple configs
- ❌ **Cross-platform:** URLSession WebSockets are macOS-only

### Missing in Python:
- ❌ **FakeSocket elegance:** Swift's protocol-based FakeSocket is more elegant
- ❌ **Compiled performance:** Swift binary is faster than Python interpreter

### Detailed Analysis:
**Interactive Mode Comparison:**
- **Python:** Extensible framework with Introspective base class allowing unlimited commands
- **Swift:** Fixed menu with 5 hardcoded commands, less flexible but fully functional

**Key Difference:** Python's approach is **framework-based** (extensible, abstract), Swift's is **implementation-based** (fixed, concrete). Swift's 5 commands cover the essential operations but lack Python's extensibility.

### Feature Parity: **~90%**

---

## 2. Capital.com Dispatcher

### Python Implementation (`argus/capital/`)
**Files:** `__init__.py` (443 lines), `client.py` (420 lines), `_svr_utils.py` (470 lines), `_lib.py` (1,800+ lines), `_caches.py` (200+ lines)

#### Dispatcher Features:
- ✅ **MKTDispatcher (extends SvrExport):** Unix Domain Socket server (`/tmp/argus_capital.sock`)
- ✅ **Dual Protocol:** Protocol 1 (JSON control messages) + Protocol 2 (CSV market data)
- ✅ **WebSocket Streaming:** Real-time CFD/Forex data from Capital.com
- ✅ **Multi-client Support:** Multiple UDS connections
- ✅ **Symbol Resolution:** Automatic ticker → EPIC resolution
- ✅ **Disk Caching:** `DomainCache` persists EPIC resolutions to `~/.argus/capital_cache.pkl`
- ✅ **Demo + Live Environments:** Switch between `Environment.DEMO` and `Environment.LIVE`
- ✅ **REST API Wrapper:** Full Capital.com API integration (`_lib.py`)
- ✅ **Rate Limiting:** Built-in handling for API rate limits
- ✅ **Batch Subscription:** Load symbols from file via `resolve/stream/batch/file` action

#### Client Actions (Protocol 1):
1. **resolve_symbol** - Resolve ticker to Capital.com EPIC (cached)
2. **stream_epic** - Start streaming market data for EPIC
3. **resolve/stream** - Combined resolve + stream
4. **unsubscribe** - Stop streaming for EPIC
5. **resolve/stream/batch/file** - Bulk subscribe from file

#### Python Client Library:
- ✅ **CapitalComClient (`client.py`):** High-level Python client for UDS
- ✅ **State tracking:** Monitors connection and subscription states
- ✅ **Automatic reconnection:** Handles disconnections
- ✅ **Callback-based API:** Clean callback interface for market data
- ✅ **Interactive CLI mode:** Edit/view mode for managing symbols

#### Interactive Mode:
Python Capital.com dispatcher **does not have interactive mode** in the traditional sense. It's designed as a service that clients connect to via UDS. Configuration is done via environment variables and launch parameters.

**Key Python Advantages:**
- **DomainCache:** Symbol resolutions persist to disk, reducing API load on restarts
- **CapitalComClient:** Full-featured client library for easy integration
- **Batch file loading:** Can subscribe to hundreds of symbols from a text file

### Swift Implementation (`argus_swift/Sources/ArgusServer/`)
**Files:** `CapitalComDispatcher.swift` (781 lines), `CapitalComWebSocket.swift` (457 lines), `CapitalComClasses.swift` (184 lines)

#### Dispatcher Features:
- ✅ **CapitalComMKTDispatcher:** Unix Domain Socket server (`/tmp/argus_capital.sock`)
- ✅ **Dual Protocol:** Protocol 1 (JSON control) + Protocol 2 (CSV data)
- ✅ **WebSocket Streaming:** Real-time CFD/Forex data
- ✅ **Multi-client Support:** Multiple UDS connections
- ✅ **Symbol Resolution:** Automatic ticker → EPIC resolution
- ⚠️ **In-memory Caching:** No disk persistence (lost on restart)
- ✅ **Demo + Live Environments:** Environment switching support
- ⚠️ **Basic REST API:** Subset of Capital.com API implemented
- ⚠️ **No Batch File Loading:** Cannot load symbols from file

#### Client Actions:
1. **resolve_symbol** - Resolve ticker to EPIC (in-memory cache only)
2. **stream_epic** - Start streaming
3. **unsubscribe** - Stop streaming

#### Interactive Mode:
Swift Capital.com has **basic interactive mode**:

**Commands:** (Implementation needs verification - appears to be minimal)
- Start/stop dispatcher
- Monitor connection status

**No Swift Client Library:** Must manually connect to UDS socket.

### Missing in Swift:
- ❌ **DomainCache/Disk Caching:** Symbol resolutions not persisted, must re-resolve on every restart
- ❌ **CapitalComClient Library:** No high-level Swift client, must use raw UDS
- ❌ **Batch File Loading:** Cannot subscribe to symbols from file (`resolve/stream/batch/file` action missing)
- ❌ **Interactive CLI Client:** No edit/view mode for symbol management
- ❌ **Full REST API:** Subset of endpoints implemented
- ❌ **Advanced rate limiting:** Less sophisticated than Python

### Missing in Python:
- (None - Python is more feature-complete)

### Detailed Analysis:
**Caching Impact:** 
- **Python:** Symbol resolutions cached to disk (`~/.argus/capital_cache.pkl`). On restart, previously resolved symbols load instantly.
- **Swift:** In-memory only. Every restart requires re-resolving all symbols via Capital.com API, increasing load time and API usage.

**Client Experience:**
- **Python:** Use `CapitalComClient` for high-level integration. Simple, clean API.
- **Swift:** Must manually construct UDS socket connection and handle Protocol 1/2 parsing.

### Feature Parity: **~85%**

---

## 3. Interactive Brokers (IB) Core Dispatcher

### Python Implementation (`argus/ib/`)
**Files:** `__init__.py` (1,204 lines), `_ib_utils.py` (500+ lines), `fields.py` (300+ lines), `_shortable_shares_data.py` (135 lines)

#### Dispatcher Features:
- ✅ **MKTDispatcher:** TCP server on port 9972
- ✅ **Multiple Modes:** 
  1. **ASK** - Ask price only
  2. **ASK+BID+LAST** - Bid, ask, and last price
  3. **FULL_PKL** - Full market data as pickled Python objects
  4. **FULL_JSON** - Full market data as JSON
  5. **PROTOCOL_2** - Normalized CSV format (recommended)
- ✅ **WebSocket Client (IBWss):** Real-time market data from IBKR
- ✅ **REST API (IBNetworker):** Contract search, account data, authentication
- ✅ **AccountProvider:** Live portfolio tracking + P&L streaming to debug socket (port 9973)
- ✅ **Multi-client Support:** Multiple TCP clients with independent subscriptions
- ✅ **Contract Search:** Cached contract resolution (`@_IB_Cache.cache_decorator`)
- ✅ **Disk Caching:** Contract search results and account data cached to `~/.argus/ib_cache.pkl`
- ✅ **Shortable Shares Data:** Real-time short-selling availability tracking (macOS only)
- ✅ **Protected Assets:** Prevent unsubscription of portfolio holdings
- ✅ **Subscription Limits:** Max 100 contracts (IBKR limitation), tracked with progress bar
- ✅ **FakeSocket Pattern:** Elegant integration for AccountProvider
- ✅ **Authentication Management:** Tickle, heartbeat, session validation threads
- ✅ **Notification System:** macOS notifications for critical events
- ✅ **Thread Safety:** LockedSession for REST API, locks for subscriptions

#### Interactive Mode (IBWss):
Python IB WebSocket has **extensive interactive capabilities**:

**Built-in Interactive Functions:**
1. **Time since last contract data** - Monitor data freshness
2. **Total WebSocket messages received** - Message counter
3. **Write all WebSocket messages to a file** - Debug logging
4. **Unique Contracts subscribed (lifetime)** - Subscription tracking
5. **Socket Still Open** - Connection health check
6. **Modify dispatcher configurations interactively** - Added by MKTDispatcher

**Runtime Configurations (MKTDispatcher):**
1. `Print data packets` (Boolean) - Toggle packet printing
2. `Use TQDM Progress bar for subscription checking` (Boolean) - Progress bars
3. `Use TQDM Progress bar for subscription current load` (Boolean) - Load monitoring
4. `Show search results from quick_add` (Boolean) - Display contract search results
5. `Block New MKT Data` (Boolean) - Block data until account ID is set
6. `Show blocked MKT Data Warning` (Boolean) - Warning messages

**Interactive Configuration Flow:**
```
Select option: 6  # Modify dispatcher configurations
Current configurations:
Print data packets: False
Use TQDM Progress bar for subscription checking: False
...
Configuration: Print data packets
Enter new value for Print data packets (current: False): true
Updated Print data packets to True
```

**Key Python Advantages:**
- **6 runtime configurations** that can be toggled without restart
- **Interactive contract subscription** via `quick_add` with search results
- **Comprehensive debugging tools** (WebSocket message logging, health checks)
- **Account integration** via AccountProvider with automatic portfolio tracking
- **5 dispatcher modes** for different use cases

### Swift Implementation (`argus_swift/Sources/ArgusServer/`)
**Files:** `IBDispatcher.swift` (425 lines), `IBWebSocket.swift` (316 lines), `IBNetworker.swift` (306 lines), `IBAccountProvider.swift` (258 lines), `IBClasses.swift` (303 lines), `IBFields.swift` (189 lines)

#### Dispatcher Features:
- ✅ **IBMKTDispatcher:** TCP server (port 9972)
- ⚠️ **Single Mode ONLY:** Protocol 2 only - **NO ASK, ASK+BID+LAST, FULL_PKL, or FULL_JSON modes**
- ✅ **WebSocket Client (IBWss):** Real-time market data from IBKR
- ✅ **REST API (IBNetworker):** Basic contract search and account selection
- ✅ **AccountProvider:** Portfolio tracking (simplified)
- ✅ **Multi-client Support:** Multiple TCP clients
- ⚠️ **In-memory Caching:** Contract search not cached to disk
- ❌ **No Shortable Shares:** Short-selling availability not tracked
- ✅ **Protected Assets:** Implemented (different API)
- ✅ **Subscription Tracking:** Progress monitoring
- ✅ **FakeSocket Pattern:** Implemented for AccountProvider
- ⚠️ **Basic Authentication:** Tickle and session management (no dedicated threads)
- ❌ **No Notification System:** No macOS notifications
- ✅ **Thread Safety:** NSLock for concurrent operations

#### Interactive Mode:
**CRITICAL ISSUE:** Swift IB has an `interactiveMode()` function, but it is **EMPTY** with **NO commands**:

```swift
func interactiveMode() {
    print("\nIBKR Dispatcher Interactive Mode")
    print("Enter commands (or 'exit' to quit):")
    print("Server is running. Press Ctrl+C to stop.")
    
    while true {
        print("> ", terminator: "")
        guard let input = readLine()?.trimmingCharacters(in: .whitespacesAndNewlines) else {
            Thread.sleep(forTimeInterval: 1.0)
            continue
        }
        
        if input.lowercased() == "exit" {
            print("Shutting down...")
            break
        }
        
        if !input.isEmpty {
            print("Unknown command: \(input)")  // ALL non-empty commands are "unknown"
        }
    }
}
```

**Analysis:** The Swift IB interactive mode:
- ❌ **NO interactive commands implemented** - All non-empty commands print "Unknown command"
- ❌ **NO configuration options** - Cannot modify settings at runtime
- ❌ **NO debugging tools** - No subscription viewer, no health checks
- ❌ **NO account management** - Cannot interact with portfolio tracking
- ✅ **Does detect exit** - Can type "exit" to quit (only working command)

**Configs Available (but not accessible):**
```swift
configs["Print data packets"] = false
configs["Block New MKT Data"] = true
configs["Show blocked MKT Data Warning"] = false
```

These exist in code but **there are no interactive commands to modify them**.

### Missing in Swift:
- ❌ **Interactive Mode is NON-FUNCTIONAL** - No commands implemented despite menu existing
- ❌ **4 of 5 Dispatcher Modes:** Only Protocol 2 supported (no ASK, ASK+BID+LAST, FULL_PKL, FULL_JSON)
- ❌ **Disk Caching:** Contract search and account data not cached
- ❌ **Shortable Shares Data:** Cannot track short-selling availability
- ❌ **Runtime Configuration:** 3 configs exist but no way to modify them interactively
- ❌ **Debugging Tools:** No WebSocket message logging, no health checks, no subscription viewer
- ❌ **Notification System:** No macOS notifications for critical events
- ❌ **LockedSession:** Different threading model, less robust for concurrent API calls
- ❌ **Account Ledger/Summary APIs:** Simplified account data retrieval

### Missing in Python:
- (None - Python is significantly more feature-complete)

### Detailed Analysis:

**Interactive Mode Comparison:**
- **Python IB:** 6+ interactive functions, 6 runtime configurations, full debugging toolkit
- **Swift IB:** **EMPTY interactive mode** - literally no commands except "exit"

This is a **critical gap**. The Swift IB dispatcher says it has interactive mode, but it's non-functional.

**Dispatcher Mode Impact:**
- **Python:** Can switch between 5 modes based on client needs (lightweight ASK, detailed FULL_JSON, normalized PROTOCOL_2)
- **Swift:** Protocol 2 only - cannot serve clients expecting other formats

**Caching Impact:**
- **Python:** Contract searches cached to disk. Searching for "AAPL" is instant on second run.
- **Swift:** Every contract search hits IBKR API, slower and higher API load.

**Shortable Shares:**
- **Python:** Real-time tracking of shares available for short-selling (critical for short sellers)
- **Swift:** Not available

### Feature Parity: **~55%**

**Assessment:** Swift IB implementation has **major gaps**. While it handles basic real-time market data, it lacks:
1. Interactive mode functionality
2. Multiple dispatcher modes
3. Disk caching
4. Shortable shares
5. Advanced debugging tools

---

## 4. Interactive Brokers (IB) Forecast Dispatcher

### Python Implementation (`argus/ib/`)
**Files:** `forecast.py` (758 lines), `_forecast_utils.py` (900+ lines)

#### Dispatcher Features:
- ✅ **FXCDispatcher:** TCP dispatcher for prediction market contracts
- ✅ **FXCWss:** WebSocket client for forecast contracts (separate endpoint)
- ✅ **Big/Mini/Micro Hierarchy:** 3-level contract structure for prediction markets
- ✅ **Market Resolution:** Automatically resolves markets to component contracts
- ✅ **Interactive Account Selection:** Menu-driven account picker at startup
- ✅ **Contract Metadata Caching:** Forecast contract details cached to disk
- ✅ **Protected Assets:** Prevent unsubscription of active positions
- ✅ **Multiple Topic Handlers:** `act`, `system`, `sts` WebSocket topics
- ✅ **Socket Message Monitoring:** Comprehensive WebSocket logging
- ✅ **Custom Data Structures:** `ForecastBig`, `ForecastMini`, `ForecastMicro` classes

#### Interactive Mode:
Python IB Forecast has **account selection interactive mode**:

**Account Selection Menu:**
```
Select an account:
1. Account U1234567 (Individual - IBKR Pro)
2. Account U7654321 (IRA - IBKR Pro)
Enter choice: 
```

**Key Features:**
- Interactive account selection from multiple trading accounts
- Account details displayed (type, ID)
- Can be integrated with Introspective framework for runtime config

### Swift Implementation (`argus_swift/Sources/ArgusServer/`)
**Files:** `IBForecastDispatcher.swift` (384 lines), `IBForecastWebSocket.swift` (106 lines), `IBForecastClasses.swift` (284 lines)

#### Dispatcher Features:
- ✅ **FXCDispatcher:** TCP dispatcher for prediction markets
- ✅ **FXCWss:** WebSocket client for forecast contracts
- ✅ **Big/Mini/Micro Hierarchy:** 3-level contract structure
- ✅ **Market Resolution:** Resolves markets to contracts
- ✅ **Interactive Account Selection:** Implemented
- ⚠️ **In-memory Caching:** Contract metadata not persisted to disk
- ✅ **Basic Topic Handlers:** `act`, `system` topics
- ⚠️ **Limited Logging:** Less comprehensive than Python

#### Interactive Mode:
Swift IB Forecast has **account selection**:

```swift
func selectAccountInteractive() throws {
    // Fetch accounts and present menu
}
```

**Key Features:**
- Interactive account selection menu
- Basic implementation

### Missing in Swift:
- ❌ **Disk Caching:** Forecast contract metadata not cached to disk
- ❌ **Comprehensive WebSocket Logging:** Less detailed than Python
- ❌ **Full Topic Handlers:** Subset of Python's topic handling
- ❌ **Advanced Interactive Mode:** No runtime configuration beyond account selection

### Missing in Python:
- (None - Python is more feature-complete)

### Detailed Analysis:
Swift IB Forecast is **functional** and covers core prediction market features. Main gap is disk caching - on restart, all forecast contract metadata must be re-fetched from IBKR, which is slow for markets with many contracts (10-candidate election = 20 contracts).

### Feature Parity: **~80%**

---

## 5. Polymarket Dispatcher

### Python Implementation (`argus/polymarket/`)
**Files:** `__init__.py` (stub only)

#### Status: ⚠️ **DEPRECATED / STUB**

```python
# THIS IS A STUB IMPLEMENTATION OF POLY DISPATCHER.
# IF YOU NEED THE OLD VERSION PLEASE CHECKOUT THE LEGACY BRANCH
```

The Python polymarket dispatcher is **non-functional** in main branch. A legacy implementation exists in a separate branch but is deprecated due to:
- Incomplete official `py_clob_client` library
- Markets from `ClobClient.get_markets()` being mostly closed/resolved
- Multiple conflicting Polymarket APIs

**Current Approach:** `polymarket_direct` module provides direct API integration without dispatcher pattern.

### Swift Implementation (`argus_swift/Sources/ArgusServer/`)
**Files:** `PolymarketWebSocket.swift` (281 lines), `PolymarketClasses.swift` (444 lines), `PolymarketExample.swift` (197 lines)

#### Status: ⚠️ **EXAMPLE ONLY**

Swift Polymarket is **demonstration code**, not a production dispatcher:
- Basic WebSocket connection examples
- Simplified data structures  
- No dispatcher implementation
- Intended for learning/prototyping

### Analysis:
Both implementations are **non-functional** for dispatcher use. Polymarket module in both languages serves as example/legacy code rather than production-ready dispatcher.

### Feature Parity: **N/A** (Both non-functional)

---

## Key Missing Features in Swift (Detailed)

### Critical - Interactive Mode

**IB Dispatcher Interactive Mode is EMPTY:**
- ❌ Function exists but has **zero commands** implemented
- ❌ Cannot view subscriptions
- ❌ Cannot modify configurations (3 configs exist but unreachable)
- ❌ Cannot add/remove symbols manually
- ❌ No debugging tools
- ❌ Everything prints "Unknown command"

**Impact:** Users cannot troubleshoot or configure IB dispatcher at runtime. Must restart entire dispatcher to change settings.

**Comparison:**
- **Python IB:** 6+ interactive functions, 6 runtime configurations
- **Swift IB:** 0 commands (except "exit")

### Critical - IB Dispatcher Modes

**Only Protocol 2 Supported:**
- ❌ No **ASK mode** (ask price only - lightest weight)
- ❌ No **ASK+BID+LAST mode** (basic trading data)
- ❌ No **FULL_PKL mode** (pickled objects for Python clients)
- ❌ No **FULL_JSON mode** (JSON for cross-language clients)
- ✅ Only **PROTOCOL_2 mode** (CSV format)

**Impact:** Clients expecting ASK, FULL_JSON, or FULL_PKL formats cannot use Swift IB dispatcher. Must use Python.

### Critical - Disk Caching

**No DomainCache Equivalent:**

| Feature | Python | Swift |
|---------|--------|-------|
| **IB Contract Search** | Cached to `~/.argus/ib_cache.pkl` | In-memory only, lost on restart |
| **Capital.com EPIC Resolution** | Cached to `~/.argus/capital_cache.pkl` | In-memory only |
| **IB Forecast Contracts** | Cached to disk | In-memory only |
| **Restart Performance** | Instant (loads from cache) | Slow (refetch from API) |
| **API Load** | Minimal (only new contracts) | High (all contracts on restart) |

**Impact:** 
- Every Swift dispatcher restart requires re-fetching all previously resolved symbols
- Higher API load on exchanges/brokers
- Slower startup times
- Risk of rate limiting from repeated API calls

### High Priority - IB Shortable Shares

**Python Only:**
- ✅ Real-time tracking of shares available for short-selling
- ✅ Field `IBKRFields.SHORTABLE_SHARES` in Protocol 2 packets
- ✅ Integrated with `ShortableSharesData` class
- ✅ Essential for short-selling strategies

**Swift:**
- ❌ Not implemented
- ❌ Field always `0` in Protocol 2 packets

**Impact:** Short sellers cannot determine availability. Must use Python or external data source.

### Medium Priority - Client Libraries

**Capital.com:**
- **Python:** Full `CapitalComClient` library with state tracking, reconnection, callbacks
- **Swift:** Must manually connect to UDS socket, no high-level client

**Impact:** Swift developers must implement UDS connection handling and Protocol 1/2 parsing manually.

### Medium Priority - Batch Operations

**Capital.com Batch File Loading:**
- **Python:** `resolve/stream/batch/file` action to subscribe to hundreds of symbols from text file
- **Swift:** Must send individual resolve/stream requests for each symbol

**Impact:** Subscribing to 100+ symbols is tedious in Swift, instant in Python.

### Low Priority - Notification System

**Python IB Only:**
- macOS system notifications for:
  - WebSocket connection/disconnection
  - Authentication failures
  - Market data errors
  - Account P&L alerts
- Optional iMessage integration

**Swift:** No notification system

**Impact:** Less visibility into dispatcher health and critical events.

---

## Platform Limitations

### Swift
- **macOS-only WebSockets:** URLSession WebSockets require macOS, not cross-platform
- **No Linux/Windows Support:** Native Swift on Linux/Windows has limitations
- **Empty Interactive Mode (IB):** Advertised but non-functional

### Python
- **Cross-platform:** Works on macOS, Linux, Windows
- **Interpreter Overhead:** Slower than compiled Swift binary
- **GIL Limitations:** Threading performance bottlenecks

---

## Client Connection Limits

**Neither Python nor Swift dispatchers implement explicit client connection limits.**

Both implementations support **unlimited TCP/UDS clients** with these natural constraints:

**Python:**
- Limited by system socket limits (`ulimit -n`)
- Threading overhead for many concurrent clients
- No hardcoded max client limit

**Swift:**
- Limited by system socket limits
- GCD handles client threads efficiently
- No hardcoded max client limit

**IBKR Subscription Limit:** Both Python and Swift respect IBKR's **100 concurrent contract subscriptions** limit. This is an IBKR platform limitation, not a dispatcher limitation.

**Recommendation:** For max connection control, implement at the infrastructure level (reverse proxy, load balancer) rather than in dispatcher code.

---

## Recommendations

### For Swift Development Priority

1. **CRITICAL: Implement IB Interactive Commands**
   - Add commands to view subscriptions
   - Add command to modify configurations
   - Add debugging commands (health check, message logging)
   - Study Python's Introspective framework for inspiration

2. **CRITICAL: Implement Disk Caching**
   - Create Swift equivalent of DomainCache
   - Cache IB contract searches to disk
   - Cache Capital.com EPIC resolutions
   - Cache IB Forecast contract metadata
   - Use `FileManager` or Swift-native persistence

3. **HIGH: Add Missing IB Dispatcher Modes**
   - Implement ASK mode
   - Implement ASK+BID+LAST mode
   - Implement FULL_JSON mode (FULL_PKL not needed for Swift)
   - Make mode configurable at startup

4. **HIGH: Implement Shortable Shares Tracking (IB)**
   - Port `ShortableSharesData` class to Swift
   - Integrate with IBWss data flow
   - Add SHORTABLE_SHARES field to Protocol 2 packets

5. **MEDIUM: Improve Interactive Modes**
   - Binance: Good, but could benefit from call_method equivalent
   - Capital.com: Add interactive configuration options
   - IB Forecast: Add runtime configuration beyond account selection

6. **MEDIUM: Create Client Libraries**
   - Swift Capital.com client for UDS connections
   - Swift IB client for TCP connections
   - Protocol 2 parsing utilities

7. **LOW: Add Notification System**
   - macOS notifications via UserNotifications framework
   - Critical event alerting

### For Python Development

1. **Adopt FakeSocket Protocol Pattern:** Make FakeSocket more formal with ABC/Protocol
2. **Performance:** Consider Cython for hot paths in dispatchers
3. **Type Hints:** Add comprehensive type hints for better IDE support
4. **Documentation:** Already excellent, keep updating

### For Users

**When to Use Swift:**
- **Performance-critical** real-time trading on macOS
- **Low-latency** local systems
- **Production dispatchers** for Binance, Capital.com (with caveats)

**When to Use Python:**
- **IB Core dispatcher** (Swift missing too many features)
- **Cross-platform** deployment (Linux, Windows)
- **Advanced features** (shortable shares, multiple modes, disk caching)
- **Development/prototyping** (richer ecosystem, easier debugging)

**Avoid:**
- Swift IB dispatcher for production (too many gaps)
- Either Polymarket dispatcher (both non-functional)

---

## Summary

**Swift Implementation Status:**
- ✅ **Binance:** Excellent, 90% parity, fully production-ready
- ✅ **Capital.com:** Good, 85% parity, lacks caching and client library but usable
- ⚠️ **IB Core:** Limited, 55% parity, **empty interactive mode**, missing modes/features - **NOT production-ready**
- ✅ **IB Forecast:** Good, 80% parity, lacks caching but functional

**Critical Findings:**
1. **IB Interactive Mode is EMPTY** - advertised but non-functional
2. **No disk caching** in any Swift dispatcher - all cache lost on restart
3. **4 of 5 IB modes missing** - only Protocol 2 supported
4. **No shortable shares** - critical data missing for short sellers

**Note:** Neither Python nor Swift implement explicit client connection limits (natural system limits apply).

**Python remains the reference implementation** with full features. Swift offers **performance advantages** for specific dispatchers (Binance, Capital.com) but **should not be used for IB Core** until critical gaps are addressed.

**Best Practice:** 
- **Binance, Capital.com:** Swift on macOS for performance
- **IB Core, IB Forecast:** Python for features and reliability
- **Cross-platform:** Python exclusively
- **Development:** Python for richer tooling
