# Polymarket to Argus-Swift: Quick Reference Summary

**TL;DR:** The Polymarket Python implementation is ~70% complete with ~3,953 lines across 12 files. Full Swift translation would require **14-22 weeks** of development, producing **6,600-9,800 lines of Swift code**.

---

## Current State at a Glance

### Code Volume
```
Total Python Code:        3,953 lines
Files:                    12 files
Modules:                  2 (dispatcher + direct client)

Main Components:
├── Dispatcher Module:    799 lines (2 files)
│   ├── Server:           696 lines
│   └── Socket Utils:     103 lines
│
└── Direct Client:        3,154 lines (10 files)
    ├── WebSocket Client: 336 lines
    ├── Data Models:      580 lines
    ├── WebSocket Handlers: 609 lines
    ├── REST API:         384 lines
    ├── Order Types:      418 lines
    └── Examples:         827 lines
```

### Completeness Status

| Component | Status | Notes |
|-----------|--------|-------|
| Market Data Streaming | ✅ Complete | WebSocket subscriptions working |
| Order Book Updates | ✅ Complete | P2 protocol encoding implemented |
| Market Search | ✅ Complete | Cache with background refresh |
| Account Events | ✅ Complete | Separate authenticated WebSocket |
| REST API Client | ✅ Complete | With geo-blocking and proxies |
| **Order Placement** | ❌ **Missing** | Handler referenced but not implemented |
| **Order Cancellation** | ❌ **Missing** | Handler referenced but not implemented |
| **Order Status** | ❌ **Missing** | Handler referenced but not implemented |
| **Get Orders** | ❌ **Missing** | Handler referenced but not implemented |
| **Get Balance** | ❌ **Missing** | Handler referenced but not implemented |

**Completion:** ~70% (market data complete, trading incomplete)

---

## Missing Implementations

### 5 Critical Handlers (~200-300 lines to complete)

In `argus/polymarket/__init__.py`, these handlers are **registered but not defined**:

```python
'place_order': self._handle_place_order,           # ❌ Missing
'cancel_order': self._handle_cancel_order,         # ❌ Missing  
'get_order_status': self._handle_get_order_status, # ❌ Missing
'get_orders': self._handle_get_orders,             # ❌ Missing
'get_balance': self._handle_get_balance,           # ❌ Missing
```

**Impact:** Dispatcher can stream market data but cannot place/manage orders.

**Estimated to Complete:** 2-3 days (implementation + testing)

---

## Swift Translation Effort Estimate

### High-Level Breakdown

| Phase | Description | Lines (Swift) | Weeks |
|-------|-------------|---------------|-------|
| **Phase 0** | Complete Python implementation | - | 1 |
| **Phase 1** | Core infrastructure (cache, protocol, sockets) | 3,500-6,000 | 3-5 |
| **Phase 2** | Polymarket-specific code | 2,800-3,500 | 7-11 |
| **Phase 3** | Integration & testing | - | 3-5 |
| **TOTAL** | | **6,600-9,800** | **14-22** |

### Component-by-Component

```
┌─────────────────────────────────────────────────────────┐
│ SWIFT TRANSLATION COMPONENTS                            │
├─────────────────────────────────────────────────────────┤
│                                                          │
│ Foundation Layer (3-5 weeks)                            │
│ ├── Cache System          ~800 lines   ████░░ Medium    │
│ ├── Protocol Encoding     ~600 lines   ██████ High      │
│ ├── Socket Server         ~1,200 lines ██████ High      │
│ ├── Threading/Async       ~400 lines   ████░░ Medium    │
│ ├── Utilities             ~500 lines   ██░░░░ Low       │
│ └── WebSocket/HTTP        ~1,000 lines ████░░ Medium    │
│                                                          │
│ Polymarket Core (7-11 weeks)                            │
│ ├── Data Models           ~1,000 lines ███░░░ Low       │
│ ├── REST API Client       ~500 lines   ██████ High      │
│ ├── WebSocket Clients     ~1,200 lines ██████ High      │
│ └── Dispatcher            ~800 lines   ██████ High      │
│                                                          │
│ Total: 6,600-9,800 lines over 14-22 weeks               │
└─────────────────────────────────────────────────────────┘
```

### Complexity Rating
- ██████ **High**: Crypto signing, WebSocket reconnection, threading
- ████░░ **Medium**: Standard Swift patterns with some complexity
- ██░░░░ **Low**: Straightforward translation of data structures

---

## Key Classes to Translate

### Core Classes (Top 10 by Complexity)

| # | Class | Lines | Complexity | Description |
|---|-------|-------|------------|-------------|
| 1 | **PolymarketDispatcher** | ~450 | ⚠️ High | Main server with routing, caching, P2 encoding |
| 2 | **PolyRestAPI** | ~280 | ⚠️ High | REST API with crypto signing, orders |
| 3 | **PolyMarketAccountEventWss** | ~280 | ⚠️ High | Authenticated WebSocket with reconnection |
| 4 | **PolyMarketOrderBookWss** | ~250 | ⚠️ High | Market data WebSocket handler |
| 5 | **EnhancedPM** | ~200 | ⚠️ High | WebSocket client with memory management |
| 6 | **Market** | ~180 | ⚡ Medium | Data model with 160+ optional fields |
| 7 | **OrderEvent** | ~140 | ⚡ Medium | Account event with nested structures |
| 8 | **PolymarketEvent** | ~100 | ⚡ Medium | Top-level event with markets/tags |
| 9 | **RoutingHelper** | ~90 | ⚡ Medium | Thread-safe routing table |
| 10 | **PolyMarketOrder** | ~60 | ✅ Low | Order data model |

---

## Key Challenges

### 1. **py_clob_client Dependency** 🔴 CRITICAL
The Python code uses `py_clob_client` for order signing and submission. Swift options:
- **Option A:** Reimplement from scratch (~500-1,000 lines) ⏱️ 1-2 weeks
- **Option B:** Use Swift-Python interop (performance penalty)
- **Option C:** Reverse-engineer API (risky, may break)

**Recommendation:** Option A - reimplement in pure Swift

### 2. **Crypto/EIP-712 Signatures** 🟡 HIGH
Polymarket uses Ethereum EIP-712 for order signing. Need Swift crypto library.

**Solution:** Use `web3.swift` or similar Ethereum library

### 3. **Thread Safety** 🟡 HIGH
Python's GIL simplifies threading. Swift requires careful use of actors/locks.

**Solution:** Use Swift actors (Swift 5.5+) for thread safety

### 4. **WebSocket Reconnection** 🟡 HIGH
Complex reconnection logic with ping/pong, memory management, retry limits.

**Solution:** Use battle-tested library like Starscream

### 5. **Cache System** 🟢 MEDIUM
Shared cache file with domain isolation, automatic backups.

**Solution:** Use SQLite or custom Swift implementation

---

## Timeline (Conservative, Single Developer)

```
Month 1: Foundation & Planning
├─ Week 1:  Planning, setup, complete Python
├─ Week 2:  Data models translation
├─ Week 3:  Protocol encoding, cache system
└─ Week 4:  Socket server infrastructure

Month 2: Core Implementation
├─ Week 5:  REST API client (part 1)
├─ Week 6:  REST API client (part 2)  
├─ Week 7:  WebSocket client (EnhancedPM)
└─ Week 8:  Account event WebSocket

Month 3: Dispatcher & Integration
├─ Week 9:   Order book WebSocket
├─ Week 10:  Dispatcher framework
├─ Week 11:  Dispatcher routing & caching
└─ Week 12:  Dispatcher order handlers

Month 4: Testing & Polish
├─ Week 13:  Integration testing
├─ Week 14:  Performance optimization
├─ Week 15:  Stress testing, memory leaks
└─ Week 16:  Documentation & examples

Month 5 (Buffer): 
├─ Week 17-18: Contingency for blockers
└─ Week 19-22: Extended testing & refinement
```

**Minimum:** 14 weeks (3.5 months)  
**Expected:** 18 weeks (4.5 months)  
**Maximum:** 22 weeks (5.5 months)

---

## Dependencies for Swift

### Required Packages
```swift
// Networking
.package(url: "https://github.com/daltoniam/Starscream.git", from: "4.0.0")
.package(url: "https://github.com/Alamofire/Alamofire.git", from: "5.0.0")

// Ethereum/Crypto
.package(url: "https://github.com/argentlabs/web3.swift", from: "1.0.0")
.package(url: "https://github.com/attaswift/BigInt.git", from: "5.0.0")

// Utilities
.package(url: "https://github.com/apple/swift-log.git", from: "1.0.0")
.package(url: "https://github.com/groue/GRDB.swift", from: "6.0.0") // For cache
```

---

## Success Criteria

The Swift implementation achieves **FULL PARITY** when:

### Functional ✅
- All Python features work in Swift
- Market data, orders, account events
- Same client protocol (P1/P2)
- Same configuration (env vars)

### Performance ✅
- Throughput ≥ Python
- Latency ≤ Python  
- Memory ≤ Python
- CPU ≤ Python

### Testing ✅
- All Python tests pass
- Integration tests pass
- Stress tests (1000+ subscriptions)
- 24+ hour stability test

---

## Recommendations

### Before Starting Translation

1. ✅ **Complete Python Implementation** (1 week)
   - Implement 5 missing order handlers
   - Test end-to-end with real Polymarket account
   - Document all edge cases

2. ✅ **Build Prototype** (1 week)
   - Minimal Swift dispatcher (market data only)
   - Validate architecture and performance
   - Measure actual speedup vs Python

3. ✅ **Assess ROI**
   - Quantify performance requirements
   - Evaluate Swift team expertise
   - Consider maintenance burden

### Translation Strategy

**Bottom-Up Approach** (Recommended):
1. Data models → Easy to test
2. REST API → Validate with scripts
3. WebSocket clients → Test with mocks
4. Dispatcher → Benefits from stable deps

**Benefits:**
- Each layer can be tested independently
- Early validation of difficult parts (crypto)
- Can abandon if prototype shows issues

---

## Risk Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Crypto library incompatible | Medium | High | Evaluate early, have backup plan |
| py_clob_client reimplementation | High | High | Allocate 2 weeks for this alone |
| Performance not better than Python | Low | High | Build prototype first |
| Testing infrastructure lacking | Medium | Medium | Create mock server early |
| Swift team expertise | Variable | High | Hire Swift expert or train team |

---

## Cost-Benefit Analysis

### Costs
- **Development:** 14-22 weeks @ developer rate
- **Testing:** Polymarket test account, infrastructure
- **Maintenance:** Two codebases (Python + Swift)
- **Risk:** Potential delays, crypto issues

### Benefits
- **Performance:** 5-10x faster execution
- **Memory:** Lower memory usage (no GC)
- **iOS/macOS:** Enable mobile trading apps
- **Deployment:** Single binary vs Python + deps
- **Type Safety:** Fewer runtime errors

### Break-Even
If performance is critical (HFT, high volume), Swift pays for itself in:
- Reduced infrastructure costs (fewer servers)
- Lower latency → better execution prices
- Mobile capability → new market opportunities

If performance is **not** critical, Python may be sufficient.

---

## Next Steps

1. **Review this scope document** with team
2. **Complete Python implementation** (5 order handlers)
3. **Build Swift prototype** (market data only, 1 week)
4. **Measure performance** gains vs Python
5. **Make go/no-go decision** based on ROI
6. **If go:** Follow recommended bottom-up translation

---

**For detailed analysis, see:** `POLYMARKET_SWIFT_SCOPE.md`

**Document Version:** 1.0  
**Last Updated:** February 5, 2026
