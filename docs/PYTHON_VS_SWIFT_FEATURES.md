# Python vs Swift Feature Comparison for Argus

## Executive Summary

This document provides a comprehensive comparison of features available in the Python implementation of Argus versus the Swift implementation (on the `argus-swift` branch). The comparison is organized by module, highlighting what features are available, what's missing, and implementation differences.

**Key Finding**: The Swift implementation is currently in development and significantly lags behind the Python implementation in terms of features and completeness. Python remains the primary implementation with full feature support.

---

## Global Infrastructure Features

### Protocol 2 Support

| Feature | Python | Swift | Notes |
|---------|--------|-------|-------|
| Protocol 2 Parser | ✅ Yes | ⚠️ Unknown | Binary protocol for market data transmission |
| Protocol 2 Encoder | ✅ Yes | ⚠️ Unknown | Encoding market data to Protocol 2 format |
| Multi-client multiplexing | ✅ Yes | ⚠️ Unknown | Single data stream to multiple clients |

### Cache System

| Feature | Python | Swift | Notes |
|---------|--------|-------|-------|
| DomainCache | ✅ Yes | ❌ No | Thread-safe caching infrastructure |
| Transparent caching | ✅ Yes | ❌ No | Automatic cache generation with decorators |
| Cache inspection CLI | ✅ Yes | ❌ No | `CacheInspector` for cache manipulation |
| Automatic backups | ✅ Yes | ❌ No | Cache backup on modifications |
| Polymarket separate cache | ✅ Yes | ❌ No | Dedicated cache file to prevent bloat |
| Cache disable env var | ✅ Yes | ❌ No | `ARGUS_CACHES_DISABLED=1` |

### Notifications

| Feature | Python | Swift | Notes |
|---------|--------|-------|-------|
| macOS system notifications | ✅ Yes | ⚠️ Unknown | Via AppleScript |
| iMessage notifications | ✅ Yes | ❌ No | Via imessage-cli |
| Custom notification sounds | ✅ Yes | ❌ No | macOS notification with sound selection |
| Notification disable flag | ✅ Yes | ⚠️ Unknown | `ARGUS_DISABLE_NOTIFICATIONS` |
| Linux notification fallback | ✅ Yes | ❌ No | Console-only notifications on Linux |

### CLI Introspection (Introspective Class)

| Feature | Python | Swift | Notes |
|---------|--------|-------|-------|
| Interactive method calling | ✅ Yes | ❌ **No** | **Major missing feature in Swift** |
| Interactive UI framework | ✅ Yes | ❌ **No** | Menu-driven method selection |
| Runtime method inspection | ✅ Yes | ❌ **No** | Uses Python's `inspect` module |
| Type-aware argument parsing | ✅ Yes | ❌ **No** | Intelligent type casting for method args |
| Method signature display | ✅ Yes | ❌ **No** | Shows method parameters and types |

**Impact**: The `Introspective` class is a Python-specific feature that enables interactive debugging and control of dispatchers at runtime. This is **NOT available in Swift** and represents a significant gap in operational tooling.

---

## Module-by-Module Comparison

### 1. Interactive Brokers (IB) Module

#### Core Components

| Component | Python | Swift | Notes |
|-----------|--------|-------|-------|
| **IBWss** (WebSocket client) | ✅ Full | ⚠️ Unknown | |
| - Cookie-based authentication | ✅ Yes | ⚠️ Unknown | |
| - Session management | ✅ Yes | ⚠️ Unknown | |
| - Heartbeat handling | ✅ Yes | ⚠️ Unknown | |
| - Auto-reconnect | ✅ Yes | ⚠️ Unknown | |
| **IBNetworker** (HTTP API) | ✅ Full | ⚠️ Unknown | |
| - Thread-safe LockedSession | ✅ Yes | ⚠️ Unknown | Uses threading.Lock |
| - Contract search with caching | ✅ Yes | ⚠️ Unknown | DomainCache decorator |
| - Account management | ✅ Yes | ⚠️ Unknown | Multi-account support |
| - Portfolio positions (STK only) | ✅ Yes | ⚠️ Unknown | |
| **MKTDispatcher** | ✅ Full | ⚠️ Unknown | |
| - TCP server for clients | ✅ Yes | ⚠️ Unknown | Port 9972 |
| - Protocol 2 support | ✅ Yes | ⚠️ Unknown | |
| - Multi-client subscriptions | ✅ Yes | ⚠️ Unknown | |
| - Auto-unsubscribe | ✅ Yes | ⚠️ Unknown | When no clients remain |
| - Max 100 contract limit | ✅ Yes | ⚠️ Unknown | IBKR limitation |
| - Interactive config modification | ✅ Yes | ❌ **No** | Via Introspective base |

#### Market Data Features

| Feature | Python | Swift | Notes |
|---------|--------|-------|-------|
| Real-time quotes | ✅ Yes | ⚠️ Unknown | |
| Bid/Ask/Last prices | ✅ Yes | ⚠️ Unknown | |
| Shortable shares data | ✅ Yes | ❌ **No** | macOS Finder-dependent |
| Market data caching | ✅ Yes | ⚠️ Unknown | Last values cached per contract |
| Multiple transmission modes | ✅ Yes | ⚠️ Unknown | ASK, FULL_JSON, PROTOCOL_2, etc. |
| Protected assets | ✅ Yes | ⚠️ Unknown | Prevents accidental unsubscribe |

#### Account Features

| Feature | Python | Swift | Notes |
|---------|--------|-------|-------|
| **AccountProvider** | ✅ Yes | ❌ **No** | Live portfolio tracking |
| - Live P&L tracking | ✅ Yes | ❌ **No** | Per-position unrealized P&L |
| - FakeSocket pattern | ✅ Yes | ❌ **No** | Internal client simulation |
| - Account balances streaming | ✅ Yes | ❌ **No** | WebSocket-based |
| - Position updates | ✅ Yes | ❌ **No** | Real-time position tracking |
| - Debug socket (port 9973) | ✅ Yes | ❌ **No** | JSON-formatted account data |
| Account ledger | ✅ Yes | ⚠️ Unknown | HTTP API call |
| Account summary | ✅ Yes | ⚠️ Unknown | HTTP API call |
| Multi-account selection | ✅ Yes | ⚠️ Unknown | Interactive account picker |

#### Forecasting Contracts (FXC)

| Feature | Python | Swift | Notes |
|---------|--------|-------|-------|
| **FXCWss** | ✅ Yes | ❌ No | Separate WebSocket endpoint |
| **FXCDispatcher** | ✅ Yes | ❌ No | Forecasting contract dispatcher |
| - Market resolution handling | ✅ Yes | ❌ No | YES/NO outcome resolution |
| - Multi-contract markets | ✅ Yes | ❌ No | Markets with multiple contracts |
| - Socket message monitoring | ✅ Yes | ❌ No | Debug pipeline |
| - Realtime logging | ✅ Yes | ❌ No | Configurable message logging |
| AbstractMarket abstraction | ✅ Yes | ❌ No | Market data modeling |

#### Statistics & Debugging

| Feature | Python | Swift | Notes |
|---------|--------|-------|-------|
| WebSocket message counter | ✅ Yes | ⚠️ Unknown | |
| Message statistics | ✅ Yes | ⚠️ Unknown | Time since last data |
| Interactive statistics viewer | ✅ Yes | ❌ **No** | Via interactive_mode() |
| Socket message dumping | ✅ Yes | ⚠️ Unknown | Debug file output |
| Subscription progress bar | ✅ Yes | ⚠️ Unknown | tqdm-based |
| Connection status checking | ✅ Yes | ⚠️ Unknown | test_conn() method |

#### Platform Support

| Feature | Python | Swift | Notes |
|---------|--------|-------|-------|
| macOS full support | ✅ Yes | ⚠️ Unknown | All features |
| Linux support | ⚠️ Partial | ❌ No | IB modules not supported |
| Shortable shares (macOS only) | ✅ Yes | ❌ No | Finder integration |
| Windows support | ❌ No | ❌ No | Not tested |

---

### 2. Capital.com Module

#### Core Components

| Component | Python | Swift | Notes |
|-----------|--------|-------|-------|
| **CapitalComAPI** | ✅ Full | ⚠️ Unknown | REST API wrapper |
| **MKTDispatcher** | ✅ Full | ⚠️ Unknown | |
| - Unix Domain Socket server | ✅ Yes | ⚠️ Unknown | `/tmp/argus_capital.sock` |
| - Protocol 1 for control | ✅ Yes | ⚠️ Unknown | |
| - Protocol 2 for market data | ✅ Yes | ⚠️ Unknown | |
| - Dual-protocol support | ✅ Yes | ❌ **No** | Unique to Capital.com |
| **CapitalComClient** | ✅ Yes | ❌ **No** | Interactive CLI client |

#### Market Data Features

| Feature | Python | Swift | Notes |
|---------|--------|-------|-------|
| WebSocket market data | ✅ Yes | ⚠️ Unknown | |
| Symbol resolution | ✅ Yes | ⚠️ Unknown | EPIC format conversion |
| Symbol resolution caching | ✅ Yes | ⚠️ Unknown | DomainCache |
| Batch symbol resolution | ✅ Yes | ⚠️ Unknown | With progress bar |
| Market search | ✅ Yes | ⚠️ Unknown | |
| CFD data | ✅ Yes | ⚠️ Unknown | |
| Forex data | ✅ Yes | ⚠️ Unknown | |
| Client state tracking | ✅ Yes | ❌ **No** | Per-symbol last tick |

#### Control Features

| Feature | Python | Swift | Notes |
|---------|--------|-------|-------|
| resolve_symbol action | ✅ Yes | ⚠️ Unknown | |
| stream_epic action | ✅ Yes | ⚠️ Unknown | |
| resolve/stream action | ✅ Yes | ⚠️ Unknown | Combined action |
| unsubscribe action | ✅ Yes | ⚠️ Unknown | |
| Batch file streaming | ✅ Yes | ⚠️ Unknown | resolve/stream/batch/file |

#### Environment Support

| Feature | Python | Swift | Notes |
|---------|--------|-------|-------|
| Demo environment | ✅ Yes | ⚠️ Unknown | |
| Live environment | ✅ Yes | ⚠️ Unknown | |
| Environment switching | ✅ Yes | ⚠️ Unknown | Via parameter |

---

### 3. Binance Module

#### Core Components

| Component | Python | Swift | Notes |
|-----------|--------|-------|-------|
| **BinanceWss** | ✅ Full | ⚠️ Unknown | |
| - WebSocket connection | ✅ Yes | ⚠️ Unknown | stream.binance.com |
| - Auto-reconnect | ✅ Yes | ⚠️ Unknown | |
| - Multi-stream support | ✅ Yes | ⚠️ Unknown | aggTrade, depth, kline, bookTicker |
| **BinanceMKTDispatcher** | ✅ Full | ⚠️ Unknown | |
| - TCP server (port 9982) | ✅ Yes | ⚠️ Unknown | |
| - Protocol 2 support | ✅ Yes | ⚠️ Unknown | |
| - Introspective base | ✅ Yes | ❌ **No** | Interactive mode |

#### Market Data Types

| Feature | Python | Swift | Notes |
|---------|--------|-------|-------|
| Depth stream (@100ms) | ✅ Yes | ⚠️ Unknown | Order book updates |
| Aggregate trades | ✅ Yes | ⚠️ Unknown | |
| K-line (1s intervals) | ✅ Yes | ⚠️ Unknown | Candlestick data |
| Book ticker | ✅ Yes | ⚠️ Unknown | Best bid/ask |
| Data class abstractions | ✅ Yes | ⚠️ Unknown | Type-safe message parsing |

#### Features

| Feature | Python | Swift | Notes |
|---------|--------|-------|-------|
| Auto-dump to JSON | ✅ Yes | ⚠️ Unknown | Configurable intervals |
| Message statistics | ✅ Yes | ⚠️ Unknown | msgs/sec tracking |
| Message rollover | ✅ Yes | ⚠️ Unknown | Max 5000 messages per file |
| UUID tracking | ✅ Yes | ⚠️ Unknown | Session identification |
| Symbol data caching | ✅ Yes | ⚠️ Unknown | Last tick per symbol |
| macOS-specific features | ✅ Yes | ❌ No | Chart display disabled on non-macOS |

#### Configuration

| Feature | Python | Swift | Notes |
|---------|--------|-------|-------|
| Configurable dump interval | ✅ Yes | ⚠️ Unknown | Default 30s |
| Statistics interval | ✅ Yes | ⚠️ Unknown | Default 10s |
| Message count limit | ✅ Yes | ⚠️ Unknown | Default 5000 |
| Interactive config modification | ✅ Yes | ❌ **No** | Via interactive_mode() |

---

### 4. TradingView Module

#### Core Components

| Component | Python | Swift | Notes |
|-----------|--------|-------|-------|
| **TradingViewConnection** | ✅ Yes | ⚠️ Unknown | Base WebSocket |
| **QuoteSession** | ✅ Yes | ⚠️ Unknown | Real-time quotes |
| **ChartSession** | ✅ Yes | ⚠️ Unknown | Historical OHLCV |

#### Features

| Feature | Python | Swift | Notes |
|---------|--------|-------|-------|
| Custom WebSocket protocol | ✅ Yes | ⚠️ Unknown | `~m~<size>~m~<JSON>` |
| Callback-based subscriptions | ✅ Yes | ⚠️ Unknown | Not dispatcher pattern |
| Authentication support | ✅ Yes | ⚠️ Unknown | Token-based |
| Unauthorized mode | ✅ Yes | ⚠️ Unknown | Limited functionality |
| Heartbeat handling | ✅ Yes | ⚠️ Unknown | |
| Locale configuration | ✅ Yes | ⚠️ Unknown | en_US default |
| Multi-symbol support | ✅ Yes | ⚠️ Unknown | Via multisymbol.py |
| pandas DataFrame output | ✅ Yes | ❌ **No** | Python-specific |

**Architecture Note**: TradingView module does NOT follow the dispatcher paradigm. It uses callback-based architecture instead.

---

### 5. NASDAQ Module

#### Core Component

| Component | Python | Swift | Notes |
|-----------|--------|-------|-------|
| **NASDAQDataDownloader** | ✅ Yes | ❌ **No** | Selenium-based scraper |

#### Features

| Feature | Python | Swift | Notes |
|---------|--------|-------|-------|
| Selenium WebDriver | ✅ Yes | ❌ No | Firefox/geckodriver |
| Headless mode | ✅ Yes | ❌ No | |
| Cookie handling | ✅ Yes | ❌ No | Automated acceptance |
| 10-year historical data | ✅ Yes | ❌ No | |
| Batch downloading | ✅ Yes | ❌ No | Multiple tickers |
| Progress tracking | ✅ Yes | ❌ No | tqdm-based |
| Temporary directory mgmt | ✅ Yes | ❌ No | Auto-cleanup |
| Context manager support | ✅ Yes | ❌ No | `with` statement |
| Download verification | ✅ Yes | ❌ No | File existence check |

**Note**: This is NOT a real-time data source. It's a utility for historical data collection.

---

### 6. Polymarket Module

#### Legacy Dispatcher (argus.polymarket)

| Feature | Python | Swift | Notes |
|---------|--------|-------|-------|
| Legacy dispatcher | ⚠️ Stub only | ❌ No | See legacy branch |

#### Direct Integration (argus.polymarket_direct)

| Component | Python | Swift | Notes |
|-----------|--------|-------|-------|
| **EnhancedPM** | ✅ Yes | ❌ **No** | Direct API integration |
| - WebSocket subscriptions | ✅ Yes | ❌ No | |
| - Dry mode | ✅ Yes | ❌ No | No credentials needed |
| - Event/Market model | ✅ Yes | ❌ No | Multi-market events |
| - Enhanced endpoints | ✅ Yes | ❌ No | Gamma API |
| - Order book depth | ✅ Yes | ❌ No | Configurable |
| - Socket retry logic | ✅ Yes | ❌ No | Max retries configurable |
| - Separate cache file | ✅ Yes | ❌ No | polymarket_cache.pkl |

#### Data Models

| Feature | Python | Swift | Notes |
|---------|--------|-------|-------|
| PolymarketEvent | ✅ Yes | ❌ No | Event abstraction |
| Market outcomes | ✅ Yes | ❌ No | Multiple outcomes per market |
| CLOB token mapping | ✅ Yes | ❌ No | Outcome to token ID |

**Architecture Note**: Does NOT follow dispatcher paradigm. Direct client library pattern.

---

## Summary of Major Gaps in Swift Implementation

### Critical Missing Features (High Priority)

1. **CLI Introspection (Introspective class)** ❌
   - No interactive method calling
   - No runtime debugging capabilities
   - No interactive configuration modification
   - **Impact**: Major operational tooling gap

2. **Cache System** ❌
   - No DomainCache infrastructure
   - No transparent caching with decorators
   - No cache inspection tools
   - **Impact**: Performance degradation, increased API calls

3. **AccountProvider (IB)** ❌
   - No live P&L tracking
   - No portfolio position streaming
   - No FakeSocket pattern
   - **Impact**: Cannot track account state in real-time

4. **Forecasting Contracts (IB)** ❌
   - No FXCWss or FXCDispatcher
   - No prediction market support on IBKR
   - **Impact**: Missing entire feature set

5. **Shortable Shares (IB)** ❌
   - macOS Finder-dependent feature
   - Critical for short-selling strategies
   - **Impact**: Incomplete market data

### Moderate Missing Features

6. **CapitalComClient** ❌
   - No interactive CLI client
   - **Impact**: Reduced usability for Capital.com

7. **NASDAQ Data Downloader** ❌
   - No historical data collection tool
   - **Impact**: Manual data gathering required

8. **Polymarket Direct Integration** ❌
   - No EnhancedPM class
   - No prediction market support
   - **Impact**: Missing data source

9. **Notification System** ❌
   - No iMessage integration
   - No custom sounds
   - **Impact**: Reduced alerting capabilities

10. **pandas Integration** ❌
    - No DataFrame output (TradingView)
    - **Impact**: Less convenient data analysis

### Platform-Specific Limitations

- **macOS Features**: Swift may have better native macOS support, but currently lacks:
  - Shortable shares integration
  - Notification system parity
  - Finder-based data retrieval

- **Linux Support**: Python has better Linux support despite IB module limitations

---

## Feature Availability Matrix

### Legend
- ✅ **Available**: Fully implemented and tested
- ⚠️ **Unknown**: Implementation status unclear (Swift branch not accessible)
- ❌ **Missing**: Confirmed not available or not applicable
- 🔶 **Partial**: Partially implemented or limited functionality

### Module Summary

| Module | Python Status | Swift Status | Feature Parity |
|--------|---------------|--------------|----------------|
| IB Core | ✅ Full | ⚠️ Unknown | Unknown |
| IB Forecasting | ✅ Full | ❌ None | 0% |
| IB AccountProvider | ✅ Full | ❌ None | 0% |
| Capital.com | ✅ Full | ⚠️ Unknown | Unknown |
| Binance | ✅ Full | ⚠️ Unknown | Unknown |
| TradingView | ✅ Full | ⚠️ Unknown | Unknown |
| NASDAQ | ✅ Full | ❌ None | 0% |
| Polymarket Direct | ✅ Full | ❌ None | 0% |
| Cache System | ✅ Full | ❌ None | 0% |
| CLI Introspection | ✅ Full | ❌ None | 0% |
| Notifications | ✅ Full | 🔶 Partial | ~30% |

---

## Recommendations

### For Python Users
- **Continue using Python** for production workloads
- All features are available and well-tested
- Best platform support (macOS, Linux)
- Active development and maintenance

### For Swift Development
Priority order for feature parity:

1. **High Priority** (Core functionality)
   - Implement cache system (DomainCache)
   - Port Protocol 2 parser/encoder
   - Implement basic dispatchers (IB, Capital.com, Binance)

2. **Medium Priority** (Operational tooling)
   - CLI introspection framework
   - Interactive configuration system
   - Notification system

3. **Lower Priority** (Advanced features)
   - AccountProvider for IB
   - Forecasting contracts
   - Polymarket integration
   - NASDAQ scraper

### For Cross-Platform Projects
- Use Python as the server/dispatcher
- Swift can be used for clients connecting to Python dispatchers
- Protocol 2 is language-agnostic - Swift clients can consume Python server data

---

## Version Information

- **Document Version**: 1.0
- **Python Argus Version**: Current (main branch)
- **Swift Argus Version**: Development (argus-swift branch - not accessible for this analysis)
- **Last Updated**: 2025-12-09

---

## Notes

1. **Swift Branch Inaccessibility**: The `argus-swift` branch mentioned in the README was not accessible during this analysis. Many Swift implementation details are marked as "Unknown" and would require access to that branch for complete comparison.

2. **Python-First Development**: Per the README, "Python remains the primary source code - all patches and updates are applied to Python first, with Swift playing catchup through manual transcompilation."

3. **Architecture Differences**: Some features like CLI introspection are inherently Python-specific due to language capabilities (reflection, dynamic typing). Swift equivalents would require different architectural approaches.

4. **Platform Dependencies**: Features like shortable shares (macOS Finder integration) and notifications (AppleScript) are platform-specific and may need re-implementation in Swift even when the Swift branch is available.

5. **Protocol 2 Compatibility**: Since Protocol 2 is a binary protocol, Swift clients can connect to Python dispatchers today without waiting for full Swift feature parity.

---

## Conclusion

The Python implementation of Argus is significantly more feature-complete than the Swift implementation. The Swift version appears to be in early development stages with several critical features missing, particularly:

- **CLI introspection capabilities** (no interactive debugging)
- **Cache infrastructure** (performance impact)
- **Advanced IB features** (AccountProvider, forecasting contracts)
- **Auxiliary modules** (NASDAQ, Polymarket)

Python remains the recommended implementation for production use. Swift development should focus on achieving core dispatcher parity before tackling advanced features.
