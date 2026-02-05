# Polymarket to Argus-Swift Translation Scope Analysis

**Date:** February 5, 2026  
**Branch:** copilot/define-scope-for-argus-swift  
**Base Commit:** 618124a - "feat: implement P2 encoding for market data and enhance order book update handling"

---

## Executive Summary

This document defines the scope of work required to translate the Polymarket Python implementation from the current branch to the `argus-swift` branch for **FULL PARITY**. The current implementation includes ~3,953 lines of Python code across 12 files in two main modules (`argus.polymarket` and `argus.polymarket_direct`).

**Key Finding:** The current implementation is **NOT COMPLETE**. There are 5 critical order management handlers that are referenced but not implemented, representing approximately 30-40% of the full dispatcher functionality.

---

## 1. Current Implementation Overview

### 1.1 Code Statistics

```
Total Polymarket Code:     3,953 lines
Number of Files:           12 files
Main Modules:              2 (polymarket, polymarket_direct)

Breakdown by Module:
- argus/polymarket/               799 lines (2 files)
  - __init__.py:                  696 lines (PolymarketDispatcher)
  - _classes.py:                  103 lines (Socket wrappers, registry)

- argus/polymarket_direct/      3,154 lines (10 files)
  - __init__.py:                  336 lines (EnhancedPM client)
  - _types.py:                    580 lines (Data models)
  - wss.py:                       609 lines (WebSocket handlers)
  - rest.py:                      384 lines (REST API client)
  - order_types.py:               418 lines (Order data classes)
  - _example.py:                  542 lines (Usage examples)
  - safe.py:                       35 lines (IP safety checks)
  - _examples/:                   250 lines (3 test files)
```

### 1.2 Module Architecture

#### **argus.polymarket (Dispatcher Architecture)**
The dispatcher module implements a server-client architecture:
- **PolymarketDispatcher**: Main server class that:
  - Listens for TCP connections from clients
  - Manages WebSocket connections to Polymarket
  - Routes market data to subscribed clients
  - Implements Protocol 2 (P2) encoding for market data
  - Handles client subscriptions/unsubscriptions
  - Provides market search and data retrieval
  - **INCOMPLETE**: Order placement/management handlers

**Key Components:**
- `RoutingHelper`: Thread-safe routing table for market data distribution
- `P2ConvertClass`: Implements Protocol 2 encoding for order book data
- `SocketsRegistry`: Manages control/market socket pairs (appears unused)
- `ArgsObject`: Parameter wrapper for handler functions

#### **argus.polymarket_direct (Direct Client Library)**
Direct integration bypassing dispatcher architecture:
- **EnhancedPM**: WebSocket client for market data subscriptions
- **PolyRestAPI**: REST API client for order management
- **Account Event WebSocket**: Separate authenticated connection for order updates
- **Type System**: Comprehensive data models for events, markets, orders

**Key Features:**
- Dry mode support (no credentials needed for market data)
- Memory management with rolling mechanism (issue #20 fix)
- Geo-block protection with multiple safety checks
- Proxy support via WireProxy integration
- Fatal error callbacks for critical failures

---

## 2. Incomplete/Stub Implementations

### 2.1 Critical Missing Handlers in PolymarketDispatcher

The following handlers are **REFERENCED** in the dispatcher but **NOT IMPLEMENTED**:

```python
# In argus/polymarket/__init__.py, lines 465-469
'place_order': self._handle_place_order,           # ❌ NOT IMPLEMENTED
'cancel_order': self._handle_cancel_order,         # ❌ NOT IMPLEMENTED
'get_order_status': self._handle_get_order_status, # ❌ NOT IMPLEMENTED
'get_orders': self._handle_get_orders,             # ❌ NOT IMPLEMENTED
'get_balance': self._handle_get_balance,           # ❌ NOT IMPLEMENTED
```

**Impact:** The dispatcher can only serve market data and market information. It cannot handle any trading operations. This represents ~30-40% of the expected full functionality.

### 2.2 NotImplementedError in Base Class

```python
# argus/polymarket/_classes.py, line 154
def subscription_expired(self, clob_id):
    raise NotImplementedError("Subscription expiration handling not implemented.")
```

**Status:** This is actually **IMPLEMENTED** in the subclass (PolymarketDispatcher.subscription_expired at line 514), so this is not a real stub.

### 2.3 Intentional Pass Statements (Not Stubs)

Several `pass` statements exist but are intentional (error handlers, no-ops):
- Exception handling in example files
- Ping implementation (intentionally empty due to Polymarket WS issues)
- Comment placeholders

---

## 3. Key Classes and Data Structures

### 3.1 Core Classes to Translate

| Class | Module | Lines | Complexity | Description |
|-------|--------|-------|------------|-------------|
| **PolymarketDispatcher** | polymarket | ~450 | High | Main dispatcher server with routing, caching, P2 encoding |
| **RoutingHelper** | polymarket | ~90 | Medium | Thread-safe market data routing table |
| **P2ConvertClass** | polymarket | ~30 | Low | Protocol 2 encoding implementation |
| **EnhancedPM** | polymarket_direct | ~200 | High | WebSocket client with reconnection logic |
| **PolyRestAPI** | polymarket_direct | ~280 | High | REST API with order management |
| **PolyMarketAccountEventWss** | polymarket_direct.wss | ~280 | Medium | Authenticated WebSocket for account events |
| **PolyMarketOrderBookWss** | polymarket_direct.wss | ~250 | Medium | Market data WebSocket handler |
| **PolymarketEvent** | polymarket_direct._types | ~100 | Low | Event data model with nested markets |
| **Market** | polymarket_direct._types | ~180 | Low | Market data model (160+ fields!) |
| **PolyMarketOrder** | polymarket_direct.order_types | ~60 | Low | Order data model |
| **OrderEvent** | polymarket_direct.order_types | ~140 | Medium | Account event data model with nested structures |

### 3.2 Data Models

The type system includes comprehensive dataclasses:
- **PolymarketEvent**: Top-level event with markets, series, tags
- **Market**: Individual market with 160+ optional fields
- **Tag**: Market categorization
- **Series**: Recurring market series
- **PolyMarketOrder**: Order state from REST API
- **OrderEvent**: Real-time order updates from WebSocket
- **MakerOrder**: Liquidity provider order details
- **Trade**: Trade execution details

---

## 4. External Dependencies

### 4.1 Python Standard Library
- `socket`, `threading`, `time`, `json`, `logging`
- `dataclasses`, `typing`, `datetime`
- `os`, `uuid`, `traceback`

### 4.2 Third-Party Libraries
- `websocket-client` (WebSocketApp)
- `requests` (HTTP client)
- `tqdm` (progress bars)
- `difflib` (market search)
- `termcolor` (colored output)
- `py_clob_client` (Polymarket CLOB client)
- `httpx` (used by py_clob_client)

### 4.3 Internal Argus Dependencies
- `utils3`: `runAsThread`, `assertTypes`
- `argus._argus_utils`: `Introspective`, `throw_fuss`, `macos_notification_with_custom_sound`
- `argus.cache_sys`: `DomainCache`, `FastCache`
- `argus.protocol`: `encode_packet`, `decode_multiple_packets`, `transmit_mkt_data_with_protocol_2`
- `argus.wireproxy.wrapper`: Proxy configuration
- `utils3.networking.sockets`: `Server`

**Critical Note:** The translation must either:
1. Port all these internal dependencies to Swift, OR
2. Create Swift equivalents with matching APIs, OR
3. Use Swift-Python interop (not recommended for performance)

---

## 5. Scope Definition for Full Parity

### 5.1 Phase 1: Complete the Python Implementation (Prerequisite)

**Before** translating to Swift, the Python implementation must be completed:

#### 5.1.1 Implement Missing Order Handlers (~200-300 lines)
```python
def _handle_place_order(self, args_obj: ArgsObject) -> dict:
    """Place order via REST API and return result"""
    # Extract: token_id, price, size, side, order_type from args
    # Call self.rest_api.place_order(...)
    # Return order result

def _handle_cancel_order(self, args_obj: ArgsObject) -> dict:
    """Cancel order by ID"""
    # Extract: order_id from args
    # Call self.rest_api.cancel_order(order_id)
    # Return cancellation result

def _handle_get_order_status(self, args_obj: ArgsObject) -> dict:
    """Get single order status"""
    # Extract: order_id from args
    # Call self.rest_api.get_order_status(order_id)
    # Return order status as dict

def _handle_get_orders(self, args_obj: ArgsObject) -> list:
    """Get all orders"""
    # Call self.rest_api.get_orders()
    # Return list of orders as dicts

def _handle_get_balance(self, args_obj: ArgsObject) -> float:
    """Get account balance"""
    # Call self.rest_api.get_balance()
    # Return balance
```

**Estimated Effort:** 2-3 days
- Implementation: 4-6 hours
- Testing: 8-12 hours (requires live Polymarket credentials)
- Error handling and edge cases: 4-6 hours

#### 5.1.2 Complete Documentation
- Add order management examples to docs/POLYMARKET_DISPATCHER.md
- Document Protocol 1 (P1) control message format
- Document Protocol 2 (P2) market data format for Polymarket
- Add client examples showing order placement workflow

**Estimated Effort:** 1-2 days

### 5.2 Phase 2: Swift Translation - Core Infrastructure

#### 5.2.1 Port Internal Dependencies (~3,000-5,000 lines Swift)
- Cache system (DomainCache, FastCache)
- Protocol encoding/decoding (P1, P2)
- Socket server infrastructure
- Threading primitives (runAsThread equivalent)
- Utility functions (assertTypes, Introspective)
- Notification system (macOS)
- Proxy integration (WireProxy wrapper)

**Estimated Effort:** 2-3 weeks
- Design Swift architecture: 3-5 days
- Implementation: 10-15 days
- Testing: 3-5 days

#### 5.2.2 Port Python Standard Library Equivalents (~500-1,000 lines Swift)
- WebSocket client (replace websocket-client)
- HTTP client (replace requests)
- Progress indicators (replace tqdm)
- String similarity (replace difflib)

**Estimated Effort:** 1-2 weeks

### 5.3 Phase 3: Swift Translation - Polymarket Core

#### 5.3.1 Type System Translation (~800-1,000 lines Swift)
Translate all data models to Swift structs/classes:
- PolymarketEvent, Market, Tag, Series
- PolyMarketOrder, OrderEvent, Trade, MakerOrder
- Implement Codable for JSON serialization
- Handle 160+ optional fields in Market model

**Complexity:** Medium
**Estimated Effort:** 1 week
- Design: 2 days
- Implementation: 3 days
- Testing: 2 days

#### 5.3.2 REST API Client (~400-500 lines Swift)
Translate PolyRestAPI:
- HTTP request handling
- Authentication and API credentials
- Order management methods
- IP safety checks and geo-blocking
- Fatal error callbacks

**Complexity:** High (crypto signing, proxy support)
**Estimated Effort:** 1.5-2 weeks

#### 5.3.3 WebSocket Clients (~1,000-1,200 lines Swift)
Translate three WebSocket handlers:
- EnhancedPM (market data client)
- PolyMarketAccountEventWss (account events)
- PolyMarketOrderBookWss (order book updates)

**Complexity:** High (reconnection logic, threading, callbacks)
**Estimated Effort:** 2-3 weeks

#### 5.3.4 Dispatcher Implementation (~600-800 lines Swift)
Translate PolymarketDispatcher:
- Server socket handling
- Client connection management
- Routing table (thread-safe)
- P2 encoding
- Market cache with background refresh
- Request/response handling

**Complexity:** High (concurrency, networking, caching)
**Estimated Effort:** 2-3 weeks

### 5.4 Phase 4: Integration and Testing

#### 5.4.1 Integration Testing
- End-to-end dispatcher tests
- Market data subscription flows
- Order placement and management
- Reconnection and error recovery
- Memory leak testing
- Performance benchmarking vs Python

**Estimated Effort:** 2-3 weeks

#### 5.4.2 Documentation
- Swift API documentation
- Migration guide from Python
- Example Swift clients
- Performance comparison

**Estimated Effort:** 1 week

---

## 6. Effort and Time Estimates

### 6.1 Summary Table

| Phase | Component | Lines (Swift) | Complexity | Effort (days) | Effort (weeks) |
|-------|-----------|---------------|------------|---------------|----------------|
| **1** | Complete Python Implementation | ~300 | Medium | 3-5 | 0.6-1.0 |
| **2** | Core Infrastructure | 3,500-6,000 | High | 15-25 | 3-5 |
| **3** | Polymarket Core | 2,800-3,500 | High | 35-55 | 7-11 |
| **4** | Integration & Testing | - | High | 15-25 | 3-5 |
| | **TOTAL** | **6,600-9,800** | | **68-110** | **14-22** |

### 6.2 Conservative Estimate (Single Developer)

**Assumptions:**
- 1 full-time developer
- 5 productive days per week
- Familiarity with both Python and Swift
- Access to Polymarket test accounts
- No major blockers or scope changes

**Timeline:**
- **Minimum:** 14 weeks (3.5 months)
- **Expected:** 18 weeks (4.5 months)
- **Maximum:** 22 weeks (5.5 months)

### 6.3 Risk Factors (Could Add 20-50% Time)

1. **Crypto/Signing Complexity:** Polymarket uses EIP-712 signatures and proxy wallets. Swift crypto libraries may not have feature parity.
   - **Mitigation:** Use existing Swift Ethereum libraries or FFI to C libraries

2. **py_clob_client Dependency:** The Python code relies heavily on `py_clob_client`. This would need to be:
   - Reimplemented in Swift (~500-1000 lines), OR
   - Used via Swift-Python interop (performance penalty), OR
   - Reverse-engineered from API calls (risky)
   - **Mitigation:** Implement from scratch using Polymarket API docs

3. **Thread Safety and Concurrency:** Python's GIL simplifies some threading. Swift requires careful use of actors/locks.
   - **Mitigation:** Use Swift actors (Swift 5.5+) for thread safety

4. **WebSocket Reliability:** Custom reconnection logic, ping/pong handling, memory management
   - **Mitigation:** Use battle-tested Swift WebSocket library

5. **Testing Infrastructure:** Requires live Polymarket account, credentials, and active markets
   - **Mitigation:** Create mock server for offline testing

---

## 7. Architecture Differences: Python vs Swift

### 7.1 Python Advantages (Current Implementation)
- Rapid prototyping and iteration
- Rich ecosystem (websocket-client, requests, py_clob_client)
- Dynamic typing speeds up development
- GIL simplifies some threading scenarios
- Excellent for scripting and data processing

### 7.2 Swift Advantages (Target Implementation)
- **Performance:** 5-10x faster execution for CPU-bound tasks
- **Memory Safety:** Compile-time memory management vs GC
- **Type Safety:** Catch errors at compile time
- **Concurrency:** Modern async/await and actors
- **iOS/macOS Native:** Could enable mobile trading apps
- **Binary Deployment:** Single executable vs Python + dependencies

### 7.3 Key Translation Challenges

| Python Feature | Swift Equivalent | Difficulty |
|----------------|------------------|------------|
| `@dataclass` | `struct` with Codable | Easy |
| `threading.Lock()` | `NSLock` or `actor` | Medium |
| `WebSocketApp` | `URLSessionWebSocketTask` or library | Medium |
| `requests` | `URLSession` | Easy |
| `py_clob_client` | Reimplement or FFI | **Hard** |
| Dynamic attributes | Protocols + generics | Medium |
| `json.dumps/loads` | `JSONEncoder/Decoder` | Easy |
| `tqdm` progress bars | Custom or library | Easy |
| Global cache file | SQLite or custom | Medium |

---

## 8. Recommended Approach

### 8.1 Incremental Translation Strategy

Instead of translating everything at once, consider an **incremental approach**:

**Option A: Bottom-Up (Recommended)**
1. Port data models first (types, orders, events)
2. Port REST API client
3. Port WebSocket clients
4. Port dispatcher last

**Benefits:**
- Can test each layer independently
- REST API can be used standalone
- Easier to validate correctness

**Option B: Top-Down**
1. Port dispatcher framework first
2. Stub out dependencies
3. Fill in implementations gradually

**Benefits:**
- Early end-to-end prototype
- Better understanding of integration points

**Recommendation:** Use **Option A (Bottom-Up)** because:
- Data models are straightforward to translate and test
- REST API can be validated with simple Swift scripts
- WebSocket clients can be tested with mock servers
- Dispatcher is the most complex and benefits from stable dependencies

### 8.2 Development Phases

**Phase 0: Planning (1 week)**
- Set up Swift package structure
- Define Swift coding standards
- Create CI/CD pipeline for Swift
- Set up test Polymarket account

**Phase 1: Complete Python (1 week)**
- Implement missing order handlers
- Test end-to-end in Python
- Document all behaviors

**Phase 2: Foundation (3-5 weeks)**
- Port data models
- Port REST API
- Create test harness

**Phase 3: Real-time (2-3 weeks)**
- Port WebSocket clients
- Test subscriptions and updates

**Phase 4: Dispatcher (2-3 weeks)**
- Port dispatcher
- Implement routing

**Phase 5: Polish (3-5 weeks)**
- Integration testing
- Performance optimization
- Documentation
- Example clients

---

## 9. Success Criteria for Full Parity

The Swift implementation achieves **FULL PARITY** when:

### 9.1 Functional Parity
- ✅ All Python features work identically in Swift
- ✅ Market data subscriptions via WebSocket
- ✅ Real-time order book updates (P2 protocol)
- ✅ Order placement (market, limit, GTC)
- ✅ Order cancellation
- ✅ Account balance retrieval
- ✅ Order status queries
- ✅ Account event WebSocket
- ✅ Market search and filtering
- ✅ Geo-block protection
- ✅ Proxy support (WireProxy)

### 9.2 Performance Parity
- ✅ Message throughput ≥ Python implementation
- ✅ Latency for P2 encoding ≤ Python implementation
- ✅ Memory usage ≤ Python implementation (steady state)
- ✅ CPU usage ≤ Python implementation

### 9.3 API Parity
- ✅ Client protocol identical (P1 control, P2 data)
- ✅ Same configuration via environment variables
- ✅ Same cache file format (or better)
- ✅ Same error messages and codes

### 9.4 Testing Parity
- ✅ All Python test cases pass in Swift
- ✅ Integration tests with live Polymarket
- ✅ Stress tests (1000+ subscriptions)
- ✅ Reconnection tests
- ✅ Memory leak tests (24+ hour run)

---

## 10. Dependencies for Swift Implementation

### 10.1 Required Swift Packages

```swift
// Package.swift
dependencies: [
    // Networking
    .package(url: "https://github.com/daltoniam/Starscream.git", from: "4.0.0"), // WebSocket
    .package(url: "https://github.com/Alamofire/Alamofire.git", from: "5.0.0"), // HTTP client
    
    // Cryptography (for Polymarket signing)
    .package(url: "https://github.com/attaswift/BigInt.git", from: "5.0.0"),
    .package(url: "https://github.com/argentlabs/web3.swift", from: "1.0.0"), // Ethereum
    
    // Utilities
    .package(url: "https://github.com/jpsim/Yams.git", from: "5.0.0"), // YAML config
    .package(url: "https://github.com/apple/swift-log.git", from: "1.0.0"), // Logging
    
    // Possibly needed
    .package(url: "https://github.com/groue/GRDB.swift", from: "6.0.0"), // SQLite for cache
]
```

### 10.2 System Requirements
- macOS 13+ or Linux (Ubuntu 20.04+)
- Swift 5.9+
- Xcode 15+ (for macOS development)

---

## 11. Open Questions

1. **argus-swift Branch Status:**
   - Does the argus-swift branch already exist?
   - What code already exists there?
   - What's the current state of other modules (IB, Capital.com, etc.)?

2. **Polymarket CLOB Client:**
   - Should we reimplement py_clob_client in Swift?
   - Or use the REST API directly?
   - Are there existing Swift Ethereum libraries that work?

3. **Shared Infrastructure:**
   - How much infrastructure exists in argus-swift already?
   - Cache system? Protocol encoding? Socket server?

4. **Testing Strategy:**
   - Mock server vs live testing?
   - CI/CD for Swift?
   - Performance benchmarking framework?

5. **Deployment:**
   - Will argus_swift be a single executable?
   - Package as Swift Package Manager library?
   - Docker container?

---

## 12. Conclusion

Translating the Polymarket implementation from Python to Swift for **full parity** is a **substantial undertaking** requiring approximately:

- **6,600-9,800 lines of Swift code**
- **14-22 weeks of full-time development**
- **68-110 developer-days**

The current Python implementation is **~70% complete** with critical order management handlers missing. These must be implemented and tested in Python first before translation begins.

The translation is **feasible** but requires:
1. Completion of Python implementation
2. Strong Swift and Python expertise
3. Understanding of WebSocket protocols
4. Ethereum/crypto knowledge (EIP-712 signatures)
5. Access to Polymarket test environment
6. Significant testing infrastructure

**Recommendation:** Before committing to full translation, consider:
- **Prototype Approach:** Build a minimal Swift dispatcher with just market data (no orders) to validate architecture and performance gains
- **Hybrid Approach:** Keep Python for prototyping, use Swift for performance-critical production deployments
- **ROI Analysis:** Quantify expected performance gains vs development cost

The Swift translation makes sense if:
- Performance is critical (high-frequency trading)
- iOS/macOS native app is planned
- Team has strong Swift expertise
- Long-term maintenance in Swift ecosystem

---

**Document Version:** 1.0  
**Author:** GitHub Copilot Agent  
**Last Updated:** February 5, 2026
