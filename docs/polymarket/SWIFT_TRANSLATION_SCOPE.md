# Polymarket Python to Swift Translation Scope

**Analysis Date:** February 5, 2026  
**Python Branch:** feature/polymarket-dispatcher (commit 618124a)  
**Swift Branch:** argus-swift (commit 70831e79)

---

## Executive Summary

This document defines the scope for translating the Polymarket Python implementation to Swift for full feature parity with the argus-swift branch.

**Key Findings:**
- Python implementation: ~3,953 lines across 12 files (~70% complete)
- argus-swift infrastructure: ~72.5KB already exists
- Additional Swift code needed: ~2,730 lines
- Estimated timeline: **4-6 weeks**

---

## Current Python Implementation

### Code Statistics

```
Total Polymarket Code:     3,953 lines (12 files)

argus/polymarket/             799 lines (2 files)
  - __init__.py               696 lines (PolymarketDispatcher)
  - _classes.py               103 lines (Socket utilities)

argus/polymarket_direct/    3,154 lines (10 files)
  - __init__.py               336 lines (EnhancedPM client)
  - _types.py                 580 lines (Data models)
  - wss.py                    609 lines (WebSocket handlers)
  - rest.py                   384 lines (REST API client)
  - order_types.py            418 lines (Order data classes)
  - _example.py               542 lines (Usage examples)
  - safe.py                    35 lines (IP safety checks)
  - _examples/                250 lines (3 test files)
```

### Architecture

**Dispatcher Module (argus.polymarket):**
- Server-client architecture with TCP connections
- Protocol 2 (P2) encoding for market data
- WebSocket connections to Polymarket
- Market data routing to subscribed clients
- Market search and caching with background refresh

**Direct Client Module (argus.polymarket_direct):**
- Direct WebSocket client for market data
- REST API client for order management
- Authenticated WebSocket for account events
- Comprehensive data models with 160+ optional fields
- Dry mode support (no credentials for market data)

### Completeness Status

| Feature | Status | Notes |
|---------|--------|-------|
| Market data streaming | ✅ Complete | WebSocket subscriptions working |
| Order book updates (P2) | ✅ Complete | Protocol 2 encoding implemented |
| Market search & cache | ✅ Complete | Background refresh working |
| Account events | ✅ Complete | Authenticated WebSocket |
| REST API client | ✅ Complete | With geo-blocking protection |
| **Order placement** | ❌ Missing | Handler stub exists |
| **Order cancellation** | ❌ Missing | Handler stub exists |
| **Order status query** | ❌ Missing | Handler stub exists |
| **Get orders list** | ❌ Missing | Handler stub exists |
| **Get balance** | ❌ Missing | Handler stub exists |

**Current Completion:** ~70% (market data complete, order management incomplete)

---

## Existing argus-swift Infrastructure

### Core Infrastructure (~40KB)

argus-swift already has comprehensive infrastructure that can be reused:

| Component | File | Status | Description |
|-----------|------|--------|-------------|
| **Cache System** | Cache/cache.swift | ✅ Complete | Thread-safe, native Swift implementation |
| **Protocol 2** | Utils/Protocol2Utils.swift | ✅ Complete | P2 encoding/decoding for market data |
| **Socket Server** | Utils/SocketProtocol.swift | ✅ Complete | TCP server infrastructure |
| **HTTP Client** | Utils/cURL.swift | ✅ Complete | cURL wrapper (no external dependencies) |
| **WebSocket** | Native URLSession | ✅ Complete | Native Swift WebSocket support |
| **Market Data** | Utils/MarketData.swift | ✅ Complete | Data structure utilities |
| **Env Loader** | Utils/EnvLoader.swift | ✅ Complete | Environment variable handling |
| **Sync API** | Utils/sync_api.swift | ✅ Complete | Synchronous wrappers |

### Existing Polymarket Module (~32.5KB)

The argus-swift branch already has a partial Polymarket implementation:

| File | Size | Status | Content |
|------|------|--------|---------|
| **PolymarketClasses.swift** | ~17KB | ⚠️ Partial | Basic data models and structures |
| **PolymarketWebSocket.swift** | ~8.5KB | ⚠️ Partial | WebSocket client implementation |
| **PolymarketExample.swift** | ~7KB | ✅ Complete | Usage examples |

### Zero Dependencies Paradigm

```swift
// Package.swift from argus-swift
dependencies: [
    // No external dependencies - using native Swift URLSession WebSockets
],
```

**Key Principle:** argus-swift uses ZERO external dependencies. All functionality is implemented using:
- Native Swift frameworks (Foundation, CryptoKit, etc.)
- System libraries (cURL via cURL.swift wrapper)
- Custom implementations

---

## Gap Analysis

### What Needs to be Added to Swift

| Component | Python LoC | Swift LoC Needed | Complexity | Days |
|-----------|-----------|------------------|------------|------|
| **Data Models Extension** | 580 | ~580 | Low-Medium | 2-3 |
| **REST API Client** | 384 | ~500 | High | 3-5 |
| **EIP-712 Signing** | N/A | ~300 | High | 2-3 |
| **Account Event WS** | 280 | ~350 | Medium | 2-3 |
| **Dispatcher** | 696 | ~800 | High | 5-7 |
| **TOTAL** | ~2,140 | **~2,730** | | **19-29** |

### Detailed Component Breakdown

#### 1. Data Models Extension (~580 lines, 2-3 days)

Extend `PolymarketClasses.swift` with:
- Full Market struct with 160+ optional fields
- PolyMarketOrder struct
- OrderEvent struct with nested structures
- MakerOrder and Trade structs
- Tag and Series structs

All models must conform to `Codable` for JSON serialization.

#### 2. REST API Client (~500 lines, 3-5 days)

Create new `PolymarketREST.swift`:
- Order placement and cancellation
- Balance queries
- Order status retrieval
- IP safety checks and geo-blocking
- Fatal error callbacks
- Uses existing cURL.swift wrapper

#### 3. EIP-712 Signing (~300 lines, 2-3 days)

Implement native Swift solution for Ethereum EIP-712 signatures:
- Cannot use external libraries (zero dependency policy)
- Must use CryptoKit + custom implementation
- Required for order signing
- This is the most challenging technical component

#### 4. Account Event WebSocket (~350 lines, 2-3 days)

Create `PolymarketAccountWS.swift`:
- Authenticated WebSocket connection
- Ping/pong handling
- Reconnection logic with retry limits
- OrderEvent parsing
- Reuse patterns from existing PolymarketWebSocket.swift

#### 5. Dispatcher (~800 lines, 5-7 days)

Create `PolymarketDispatcher.swift`:
- TCP server (use existing SocketProtocol.swift)
- Client connection management
- Thread-safe routing table (use Swift actors)
- P2 encoding (use existing Protocol2Utils.swift)
- Market cache with background refresh
- Request/response handling
- Implement 5 order management handlers
- Follow patterns from IB/Binance/Capital dispatchers

---

## Implementation Strategy

### Phase 1: Complete Python Implementation (2-3 days)

Before starting Swift translation, complete the Python implementation:

1. Implement 5 missing order handlers in `argus/polymarket/__init__.py`:
   - `_handle_place_order`
   - `_handle_cancel_order`
   - `_handle_get_order_status`
   - `_handle_get_orders`
   - `_handle_get_balance`

2. Test end-to-end with live Polymarket account
3. Document exact behavior for Swift transcompilation

### Phase 2: Swift Transcompilation (3-4 weeks)

#### Week 1: Data Models & REST API Foundation
- Day 1-2: Audit existing PolymarketClasses.swift
- Day 3-5: Extend data models with missing structures
- Day 6-7: Begin PolymarketREST.swift implementation

#### Week 2: REST API & EIP-712
- Day 8-10: Complete REST API client
- Day 11-13: Implement EIP-712 signing in pure Swift

#### Week 3: WebSocket & Dispatcher Foundation
- Day 14-16: Create/extend Account Event WebSocket
- Day 17-19: Begin PolymarketDispatcher.swift

#### Week 4: Dispatcher Completion & Integration
- Day 20-24: Complete dispatcher implementation
- Day 25-26: Wire all components together
- Day 27-29: Integration testing

### Phase 3: Testing & Validation (3-5 days)

- Integration testing with all components
- Live Polymarket testing with real account
- Performance comparison with Python implementation
- Memory leak testing
- Stress testing (multiple concurrent subscriptions)

---

## Technical Challenges

### 1. EIP-712 Signing (Critical)

**Challenge:** Python uses `py_clob_client` for Ethereum EIP-712 signatures. Swift must implement this natively.

**Solution:**
- Use CryptoKit for cryptographic primitives
- Implement EIP-712 typed data hashing
- Extract signing logic from py_clob_client source
- Estimated: ~300 lines, 2-3 days of research + implementation

### 2. Market Model Complexity (Medium)

**Challenge:** Market data model has 160+ optional fields with various types.

**Solution:**
- Use Swift structs with optional properties
- Leverage Codable for automatic JSON parsing
- Filter unknown fields during parsing
- Estimated: ~200 lines of model definitions

### 3. Thread Safety (Medium)

**Challenge:** Python's GIL simplifies some threading scenarios.

**Solution:**
- Use Swift actors for thread-safe state management
- Use established patterns from IB/Binance/Capital modules
- Lock-free data structures where possible

### 4. WebSocket Reconnection (Medium)

**Challenge:** Complex reconnection logic with ping/pong, retry limits, memory management.

**Solution:**
- Follow patterns from existing PolymarketWebSocket.swift
- Use URLSession native WebSocket capabilities
- Implement exponential backoff for retries

---

## Development Guidelines

### Code Organization

Follow established argus-swift patterns:

```
argus_swift/Sources/ArgusServer/
└── Polymarket/
    ├── PolymarketClasses.swift      # Data models (extend)
    ├── PolymarketWebSocket.swift    # Market WS (extend)
    ├── PolymarketAccountWS.swift    # Account WS (new)
    ├── PolymarketREST.swift         # REST API (new)
    ├── PolymarketDispatcher.swift   # Dispatcher (new)
    └── PolymarketExample.swift      # Examples (exists)
```

### Paradigms to Follow

1. **Zero External Dependencies**
   - Use native Swift frameworks only
   - No SPM packages (Alamofire, Starscream, web3.swift, etc.)
   - System libraries via wrappers (cURL.swift)

2. **Direct Transcompilation**
   - Maintain similar structure to Python code
   - Class names match Python when possible
   - Keep same logical flow

3. **Performance-First**
   - Struct over class when appropriate
   - Actor-based concurrency for thread safety
   - Avoid reflection/dynamic dispatch

4. **Pattern Consistency**
   - Follow IB/Binance/Capital module patterns
   - Use existing utility functions
   - Consistent error handling

---

## Timeline Estimate

### Conservative Estimate (Single Developer)

**Assumptions:**
- Full-time developer (5 days/week)
- Familiarity with both Python and Swift
- Access to Polymarket test account
- No major blockers

**Timeline:**

| Phase | Duration | Calendar Time |
|-------|----------|---------------|
| Complete Python | 2-3 days | Week 1 |
| Data Models & REST Foundation | 5-7 days | Week 1-2 |
| REST API & EIP-712 | 6-8 days | Week 2-3 |
| WebSocket & Dispatcher Start | 5-7 days | Week 3-4 |
| Dispatcher Completion | 5-7 days | Week 4-5 |
| Integration & Testing | 3-5 days | Week 5-6 |
| **TOTAL** | **26-37 days** | **4-6 weeks** |

### Risk Factors

Potential delays (add 1-2 weeks if encountered):

1. **EIP-712 Implementation Complexity** - If native Swift crypto proves difficult
2. **Testing Infrastructure** - Setting up reliable test environment
3. **Unexpected API Differences** - Between Python libs and Swift implementation
4. **Performance Issues** - Requiring optimization iterations

---

## Success Criteria

The Swift implementation achieves full parity when:

### Functional Parity
- ✅ All Python features work identically in Swift
- ✅ Market data subscriptions via WebSocket
- ✅ Real-time order book updates with P2 encoding
- ✅ Order placement, cancellation, status queries
- ✅ Account balance retrieval
- ✅ Account event WebSocket
- ✅ Market search and filtering
- ✅ Geo-block protection
- ✅ Cache with background refresh

### Performance Parity
- ✅ Message throughput ≥ Python implementation
- ✅ Latency for P2 encoding ≤ Python
- ✅ Memory usage ≤ Python (likely better, no GC)
- ✅ CPU usage ≤ Python

### API Parity
- ✅ Same client protocol (P1 control, P2 data)
- ✅ Same environment variables
- ✅ Same cache behavior
- ✅ Same error messages and codes

---

## Next Steps

1. **Audit Existing Swift Code** (1 day)
   - Check out argus-swift branch locally
   - Review PolymarketClasses.swift in detail
   - Review PolymarketWebSocket.swift in detail
   - Identify exact gaps

2. **Complete Python Implementation** (2-3 days)
   - Implement 5 missing order handlers
   - Test with live Polymarket account
   - Document behavior

3. **Begin Swift Transcompilation** (3-4 weeks)
   - Follow phase-by-phase plan above
   - Commit incrementally
   - Test continuously

4. **Final Validation** (3-5 days)
   - Integration testing
   - Performance benchmarking
   - Documentation updates

---

## Appendix: Python Dependencies

The Python implementation uses these key libraries that have Swift equivalents:

| Python Library | Swift Equivalent | Notes |
|---------------|------------------|-------|
| `websocket-client` | Native URLSession | WebSocket support |
| `requests` | cURL.swift | HTTP client |
| `threading` | Swift Concurrency | Actors, async/await |
| `json` | JSONEncoder/Decoder | Built-in |
| `dataclasses` | Struct + Codable | Type-safe |
| `py_clob_client` | Custom implementation | EIP-712 signing |

**Critical:** The py_clob_client dependency requires custom implementation in Swift as external crypto libraries violate the zero-dependency paradigm.

---

**Document Version:** 1.0  
**Last Updated:** February 5, 2026  
**Status:** Active scope document
