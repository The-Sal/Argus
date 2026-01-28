# C++ Conversion Feasibility Analysis for Argus

**Date:** January 28, 2026  
**Analysis of:** Converting Argus Python codebase to C++  
**Current Codebase:** ~12,000 lines of Python across 11 modules

---

## Executive Summary

Converting Argus to C++ is **technically feasible** but represents a **significant engineering effort** (estimated 6-12 person-months for full conversion, 2-4 weeks for polymarket dispatcher only). The modular architecture and dependency on standard libraries make it more tractable than typical Python projects, but several challenges exist around WebSocket handling, dynamic typing, and third-party integrations.

**Key Findings:**
- **Full Argus-CPP:** 6-12 person-months (medium-high complexity)
- **Polymarket Dispatcher Only:** 2-4 weeks (low-medium complexity)
- **Recommended Approach:** Incremental module-by-module conversion starting with polymarket dispatcher
- **Main Challenges:** WebSocket client libraries, py_clob (ignore per requirements), Protocol 2 parser implementation

---

## 1. Repository Overview

### 1.1 Current Architecture

Argus is a financial market data aggregation system with:
- **11 modules** across ~12,000 lines of Python
- **Server-client architecture** using TCP/Unix Domain Sockets
- **Dispatcher paradigm** for most modules (IB, Capital.com, Binance)
- **Protocol 2 (P2)** binary protocol for efficient data transmission
- **WebSocket-based** real-time data streaming
- **Minimal external dependencies** (primarily stdlib + websocket-client + requests)

### 1.2 Module Breakdown

| Module | LOC | Complexity | C++ Difficulty | Notes |
|--------|-----|------------|----------------|-------|
| `ib` (Interactive Brokers) | ~3,700 | High | High | Complex API, authentication flows, forecasting |
| `capital` (Capital.com) | ~2,200 | Medium | Medium | REST + WebSocket, UDS server, Protocol 1 & 2 |
| `binance` | ~800 | Low | Low | Simple WebSocket streams, minimal API |
| `polymarket_direct` | ~1,400 | Medium | **Low-Medium** | **Target module**, REST + WebSocket, clean API |
| `polymarket` (legacy stub) | ~30 | Minimal | Minimal | Stub only, ignore |
| `tv` (TradingView) | ~450 | Medium | Medium | Custom WebSocket protocol |
| `nasdaq` | ~250 | Low | Medium | Selenium-based scraper (not real-time) |
| `wireproxy` | ~1,200 | High | High | Wireguard integration, binary protocol |
| `cache_utils` | ~270 | Low | Medium | Pickle-based caching, thread-safe |
| `_argus_utils` | ~100 | Low | Low | Utility functions, notifications |

**Total Effective LOC:** ~12,000 (excluding tests, docs, examples)

---

## 2. Dependency Analysis

### 2.1 Python Dependencies

```python
# requirements.txt
tqdm              # Progress bars -> C++: indicators library
websocket-client  # WebSocket -> C++: libwebsockets, Boost.Beast, websocketpp
python-dotenv     # Env vars -> C++: stdlib getenv() or dotenv-cpp
selenium          # Browser automation -> C++: selenium-cpp (or skip NASDAQ module)
numpy             # Numerical -> C++: Eigen, Armadillo (or skip if unused)
pandas            # DataFrames -> C++: skip or custom (rarely used in core)
websockets        # Async WebSocket -> C++: same as websocket-client
requests          # HTTP -> C++: libcurl, cpp-httplib, cpr
utils3            # Custom utilities -> C++: reimplement or port
termcolor         # Terminal colors -> C++: termcolor-cpp, rang
python-socks      # SOCKS proxy -> C++: libproxychains, curl SOCKS support
```

### 2.2 C++ Alternatives Assessment

| Python Dependency | C++ Alternative | Availability | Difficulty |
|-------------------|-----------------|--------------|------------|
| `websocket-client` | `websocketpp`, `Boost.Beast` | ✅ Excellent | Low-Medium |
| `requests` | `libcurl`, `cpp-httplib`, `cpr` | ✅ Excellent | Low |
| `python-dotenv` | `dotenv-cpp` or stdlib | ✅ Good | Trivial |
| `tqdm` | `indicators` | ✅ Good | Low |
| `termcolor` | `termcolor-cpp`, `rang` | ✅ Good | Trivial |
| `numpy/pandas` | `Eigen`, `Armadillo` | ⚠️ Partial | Medium (mostly unused in Argus) |
| `selenium` | `selenium-cpp` | ⚠️ Limited | High (only for NASDAQ module) |
| `utils3` | Custom port | ⚠️ N/A | Medium (needs analysis) |
| `py_clob_client` | **IGNORE** per requirements | ❌ N/A | N/A |
| `python-socks` | `libcurl` SOCKS support | ✅ Good | Low |

**Verdict:** All critical dependencies have mature C++ equivalents except `py_clob_client` (which you specified to ignore).

---

## 3. Architecture & Patterns

### 3.1 Dispatcher Pattern (Core Architecture)

The dispatcher pattern is central to Argus:

```python
# Python Pattern (simplified)
class MKTDispatcher:
    def __init__(self, port):
        self.server = socket.socket()
        self.server.bind(('0.0.0.0', port))
        self.clients = []
        self.subscriptions = {}  # symbol -> [client_sockets]
        
    def handle_client(self, client):
        while True:
            msg = client.recv(1024)  # e.g., b'add=AAPL'
            if msg.startswith(b'add='):
                symbol = msg[4:].decode()
                self.subscribe_client(client, symbol)
                
    def broadcast_market_data(self, symbol, data):
        packet = Protocol2.encode(symbol, data)
        for client in self.subscriptions.get(symbol, []):
            client.send(packet)
```

**C++ Translation:** Straightforward using `boost::asio` or raw POSIX sockets
- Similar socket API semantics
- `std::unordered_map<string, vector<int>>` for subscriptions
- Threading with `std::thread` or `std::async`
- Mutex protection with `std::mutex`, `std::lock_guard`

**Complexity:** Low-Medium (standard network programming)

### 3.2 Protocol 2 Binary Format

```
~<packet-length><symbol-length>|<symbol><market-data>L

Example:
~00710004|AAPL150.25,1000,150.30,800,150.28,100,50000,1732275600.123,1732275600.456L
```

**C++ Implementation:**

```cpp
struct Protocol2Packet {
    std::string symbol;
    double bid, ask, last;
    int bid_size, ask_size, last_size;
    double timestamp, transmission_time;
    
    static Protocol2Packet parse(const std::vector<char>& data);
    std::vector<char> encode() const;
};
```

**Complexity:** Trivial (basic string parsing with `std::stringstream`)

### 3.3 WebSocket Handling

Python uses `websocket-client` (blocking) and `websockets` (async). C++ equivalents:

**Option 1: Boost.Beast (Recommended)**
- Modern C++17+ design
- Integrates with `boost::asio` for async I/O
- Well-documented, mature
- Example: https://www.boost.org/doc/libs/1_84_0/libs/beast/example/websocket/client/sync-ssl/websocket_client_sync_ssl.cpp

**Option 2: websocketpp**
- Header-only, easier to integrate
- Less modern but stable
- Good for simpler use cases

**Complexity:** Medium (async I/O requires careful design)

---

## 4. Full Argus-CPP Conversion

### 4.1 Engineering Effort Breakdown

| Phase | Modules | Estimated Effort | Priority |
|-------|---------|------------------|----------|
| **Phase 1: Infrastructure** | Core utils, Protocol 2 parser, socket abstraction | 3-4 weeks | Critical |
| **Phase 2: Simple Modules** | Binance, polymarket_direct | 3-5 weeks | High |
| **Phase 3: Medium Modules** | Capital.com, TradingView | 6-8 weeks | Medium |
| **Phase 4: Complex Modules** | Interactive Brokers (IB), Wireproxy | 10-14 weeks | Low-Medium |
| **Phase 5: Auxiliary** | NASDAQ (Selenium), cache system | 2-4 weeks | Low |
| **Phase 6: Testing & Integration** | End-to-end tests, performance tuning | 4-6 weeks | Critical |

**Total: 28-41 weeks (6-10 months) for a single engineer**  
**Realistic with 2-3 engineers: 3-6 months**

### 4.2 Risk Factors

| Risk | Impact | Mitigation |
|------|--------|------------|
| WebSocket library compatibility issues | High | Prototype early with Boost.Beast |
| Dynamic typing -> static typing friction | Medium | Use `std::variant`, `std::optional` liberally |
| Third-party API changes (IB, Capital.com) | Medium | Maintain feature parity testing |
| Threading/concurrency bugs | High | Extensive testing, use RAII, smart pointers |
| Build system complexity (CMake, deps) | Medium | Use Conan or vcpkg for dependency management |
| Cross-platform issues (macOS/Linux) | Low-Medium | Test early on both platforms |

### 4.3 Benefits of Full Conversion

1. **Performance:** 5-10x latency improvement (critical for HFT)
2. **Memory Efficiency:** 50-70% reduction in memory footprint
3. **Deployment:** Single binary, no Python interpreter needed
4. **Integration:** Easier to embed in C++ trading systems
5. **Type Safety:** Catch errors at compile-time vs runtime

### 4.4 Recommended Approach

**Incremental Module-by-Module Conversion:**

1. **Start with polymarket dispatcher** (2-4 weeks, see Section 5)
2. **Build common infrastructure** (Protocol 2 parser, socket utils)
3. **Convert Binance** (simplest dispatcher, validate architecture)
4. **Tackle Capital.com** (tests UDS + dual protocol support)
5. **Convert IB** (most complex, highest ROI for performance)
6. **Optional modules last** (NASDAQ, Wireproxy, TradingView)

**Maintain Python version in parallel** until C++ version reaches parity (3-6 months)

---

## 5. Polymarket Dispatcher Conversion (Focused Analysis)

### 5.1 Module Overview

**Location:** `argus/polymarket_direct/__init__.py` (~300 LOC core logic)  
**Current Status:** Actively maintained, no legacy dependencies  
**Dependencies:**
- `requests` (HTTP API calls) ✅
- `websocket-client` (WebSocket subscriptions) ✅
- `utils3` (threading decorator) ⚠️ (needs custom port)
- `py_clob_client` ❌ **IGNORED per requirements**

**Key Components:**
1. **EnhancedPM class** - Main API client
2. **Event fetching** - REST API endpoint (`https://gamma-api.polymarket.com/events`)
3. **WebSocket subscriptions** - Real-time market data (`wss://ws-subscriptions-clob.polymarket.com/ws/market`)
4. **Message logging** - Rolling file mechanism (`.fk` files)
5. **Type definitions** - `_types.py` with dataclasses (~540 LOC)

### 5.2 Architecture Analysis

```python
# Simplified flow
class EnhancedPM:
    def __init__(self):
        self.session = requests.Session()  # HTTP client
        self.market_ws = WebSocketApp(...)  # WebSocket
        self.idx_to_callback = {}  # asset_id -> callback
        self.ws_messages = []  # Message buffer
        
    def fetch_events(self, limit=20):
        response = self.session.get(gamma_api_url)
        return [PolymarketEvent.from_dict(e) for e in response.json()]
        
    def subscribe_to_market_data(self, asset_ids, callback):
        self.idx_to_callback[asset_id] = callback
        self.market_ws.send(json.dumps({'assets_ids': asset_ids}))
        
    def _on_ws_message(self, ws, message):
        data = json.loads(message)
        for change in data['price_changes']:
            callback = self.idx_to_callback[change['asset_id']]
            callback(change)
```

**C++ Translation Strategy:**

```cpp
class EnhancedPM {
private:
    cpr::Session http_session_;  // or libcurl
    websocketpp::client<...> ws_client_;
    std::unordered_map<std::string, std::function<void(json)>> callbacks_;
    std::vector<json> ws_messages_;
    std::mutex callbacks_mutex_;
    
public:
    EnhancedPM();
    std::vector<PolymarketEvent> fetch_events(int limit = 20);
    void subscribe_to_market_data(const std::vector<std::string>& asset_ids,
                                   std::function<void(json)> callback);
private:
    void on_ws_message(const std::string& message);
};
```

### 5.3 Type System Conversion

Python uses `@dataclass` for `PolymarketEvent`, `Market`, `Series`, `Tag`:

```python
@dataclass
class Market:
    id: Optional[str] = None
    question: Optional[str] = None
    volume: Optional[float] = None
    # ... 100+ optional fields
```

**C++ Approach:**

```cpp
struct Market {
    std::optional<std::string> id;
    std::optional<std::string> question;
    std::optional<double> volume;
    // ... using std::optional for all nullable fields
    
    static Market from_json(const nlohmann::json& j);
    nlohmann::json to_json() const;
};
```

**Library:** `nlohmann/json` (header-only, excellent std::optional support)

### 5.4 Engineering Effort (Polymarket Only)

| Task | Estimated Time | Difficulty |
|------|----------------|------------|
| **Setup build system** (CMake, dependencies) | 1-2 days | Low |
| **HTTP client wrapper** (`cpr` or `cpp-httplib`) | 2-3 days | Low |
| **WebSocket client** (`websocketpp` or Boost.Beast) | 4-5 days | Medium |
| **Type definitions** (`_types.cpp`, 540 LOC) | 3-4 days | Low-Medium |
| **EnhancedPM class** (core logic, 300 LOC) | 5-7 days | Medium |
| **Rolling message mechanism** | 2-3 days | Low |
| **Threading & callbacks** | 2-3 days | Low-Medium |
| **Testing & integration** | 3-5 days | Medium |
| **Documentation** | 2-3 days | Low |

**Total: 24-35 days (3.5-5 weeks) for single engineer**  
**Realistic with testing: 2-4 weeks with aggressive timeline, 4-6 weeks with buffer**

### 5.5 Specific Challenges (Polymarket)

1. **WebSocket Reconnection Logic**
   - Python: `on_close` callback with manual reconnect
   - C++: Requires explicit state machine for reconnection
   - **Mitigation:** Use Boost.Beast with `steady_timer` for retries

2. **Rolling File Mechanism**
   - Python: Threading with `time.sleep()` and `uuid` naming
   - C++: `std::thread` + `std::this_thread::sleep_for()` + `boost::uuids`
   - **Complexity:** Low (straightforward translation)

3. **Dynamic JSON Parsing**
   - Python: Flexible dict-based parsing, unknown fields ignored
   - C++: `nlohmann::json` supports this with `.contains()` checks
   - **Complexity:** Low (library handles it well)

4. **Callback System**
   - Python: Dict of lambdas (`self.idx_to_callback[asset_id] = callback`)
   - C++: `std::unordered_map<string, std::function<void(json)>>`
   - **Complexity:** Low (standard C++ pattern)

5. **Wireproxy Integration**
   - Python: `start_proxy_aware_ws('POLYMARKET', self.market_ws)`
   - C++: Need to port wireproxy wrapper or use libcurl SOCKS
   - **Complexity:** Medium (may require custom SOCKS proxy code)

### 5.6 Dependencies for Polymarket C++

```cmake
# CMakeLists.txt (example)
find_package(Boost REQUIRED COMPONENTS system thread)
find_package(OpenSSL REQUIRED)
find_package(websocketpp REQUIRED)
find_package(nlohmann_json REQUIRED)
find_package(cpr REQUIRED)  # or cpp-httplib

target_link_libraries(polymarket_dispatcher
    Boost::system
    Boost::thread
    OpenSSL::SSL
    websocketpp::websocketpp
    nlohmann_json::nlohmann_json
    cpr::cpr
)
```

All dependencies available via Conan/vcpkg:
```bash
# Conan
conan install . --build=missing

# vcpkg
vcpkg install boost-asio boost-beast websocketpp nlohmann-json cpr
```

### 5.7 Recommended Implementation Plan (Polymarket)

**Week 1: Setup & HTTP**
- Day 1-2: CMake setup, dependency management (Conan/vcpkg)
- Day 3-4: HTTP client wrapper, test event fetching
- Day 5: Type definitions (Market, PolymarketEvent structs)

**Week 2: WebSocket Core**
- Day 1-3: WebSocket client setup (websocketpp)
- Day 4: Message parsing and callback system
- Day 5: Reconnection logic

**Week 3: Features & Testing**
- Day 1-2: Rolling file mechanism
- Day 3: Wireproxy integration (if needed)
- Day 4-5: Unit tests, integration tests

**Week 4: Polish & Documentation**
- Day 1-2: Error handling, logging
- Day 3-4: Performance testing, memory leak checks (Valgrind)
- Day 5: Documentation, code review

**Deliverable:** Production-ready `polymarket_dispatcher` C++ library

### 5.8 Code Size Estimate (Polymarket C++)

| File | Estimated LOC | Notes |
|------|---------------|-------|
| `polymarket_types.hpp` | 200 | Struct definitions, std::optional |
| `polymarket_types.cpp` | 400 | JSON serialization/deserialization |
| `enhanced_pm.hpp` | 100 | EnhancedPM class declaration |
| `enhanced_pm.cpp` | 500 | Core logic, WebSocket, HTTP |
| `websocket_client.hpp/.cpp` | 300 | WebSocket abstraction layer |
| `http_client.hpp/.cpp` | 150 | HTTP wrapper (if needed) |
| `examples/example_usage.cpp` | 100 | Usage examples |
| `tests/` | 400 | Unit and integration tests |
| `CMakeLists.txt` | 100 | Build configuration |

**Total: ~2,250 LOC C++ (vs ~1,400 LOC Python = 1.6x increase)**

The increase is due to:
- Explicit type declarations (no duck typing)
- Manual memory management (though mostly RAII)
- More verbose error handling
- Separate header/implementation files

---

## 6. Swift vs C++ Comparison

Given that `argus-swift` branch exists:

| Aspect | C++ | Swift |
|--------|-----|-------|
| **Performance** | Excellent (native, zero-cost abstractions) | Excellent (LLVM, ARC) |
| **Memory Safety** | Manual (smart pointers help) | Automatic (ARC) |
| **Ecosystem** | Mature (Boost, vcpkg, Conan) | Growing (SwiftPM) |
| **WebSocket Support** | Excellent (Boost.Beast, websocketpp) | Good (NIO, Starscream) |
| **Cross-platform** | Excellent (Linux/macOS/Windows) | Limited (macOS/Linux, no Windows) |
| **HFT Suitability** | Excellent (deterministic latency) | Good (GC-free, but ARC overhead) |
| **Learning Curve** | High (complex, footguns) | Medium (safer, modern) |
| **Interop with Python** | Good (pybind11) | Poor (requires bridging) |

**Recommendation:** 
- **C++ for performance-critical, cross-platform deployment** (production HFT systems, Linux servers)
- **Swift for rapid development on Apple ecosystem** (iOS apps, macOS analysis tools)
- **Python remains the reference implementation** (easiest to maintain, test, iterate)

---

## 7. Recommendations

### 7.1 For Full Argus-CPP

**IF** you want full C++ conversion:
1. ✅ **Start with polymarket dispatcher** (lowest risk, 2-4 weeks)
2. ✅ **Build reusable infrastructure** (Protocol 2 parser, socket abstraction)
3. ✅ **Convert Binance next** (validates architecture with minimal complexity)
4. ✅ **Parallelize Python/C++** for 3-6 months (gradual migration)
5. ⚠️ **Budget 6-12 person-months** (realistic with testing, docs, maintenance)
6. ⚠️ **Requires strong C++ expertise** (async I/O, threading, WebSocket)

**Pros:**
- Significant performance gains (5-10x latency reduction)
- Single binary deployment (no Python runtime)
- Better for embedded/HFT systems

**Cons:**
- High upfront cost (6-12 months)
- Slower iteration vs Python
- More bugs initially (memory safety, concurrency)

### 7.2 For Polymarket Dispatcher Only

**IF** you want just polymarket dispatcher in C++:
1. ✅ **Highly feasible** (2-4 weeks, low risk)
2. ✅ **Good learning exercise** (validates C++ architecture without full commitment)
3. ✅ **Immediate value** (if polymarket is performance bottleneck)
4. ⚠️ **Plan for 4-6 weeks with buffer** (includes testing, docs)
5. ⚠️ **Use Conan/vcpkg** for dependency management
6. ⚠️ **Start with Boost.Beast** for WebSocket (mature, well-documented)

**Pros:**
- Low risk (isolated module)
- Fast iteration (single module)
- Validates C++ approach before full conversion

**Cons:**
- Limited ROI if polymarket isn't bottleneck
- Still need Python for other modules
- Interop overhead (if calling from Python)

### 7.3 Hybrid Approach (Recommended)

**Best of both worlds:**

1. **Keep Python as primary** for rapid development, iteration
2. **Convert performance-critical modules to C++**:
   - Start with polymarket dispatcher (2-4 weeks)
   - Add Binance if needed (2-3 weeks)
   - Convert IB only if HFT latency is critical (8-12 weeks)
3. **Use pybind11** to expose C++ modules as Python packages
4. **Maintain Python dispatchers** for less critical modules (Capital.com, TradingView, NASDAQ)

**Example:**
```python
# Python code can import C++ modules seamlessly
from argus_cpp.polymarket import EnhancedPM  # C++ implementation
from argus.capital import CapitalComAPI      # Python implementation

# Mixed usage
pm_client = EnhancedPM()  # Fast C++ WebSocket
cap_client = CapitalComAPI()  # Flexible Python REST
```

**Pros:**
- Incremental effort (2-4 weeks initially)
- Performance where it matters
- Flexibility where it doesn't
- Python remains primary development language

**Cons:**
- Complexity of maintaining two languages
- Build system overhead (CMake + setuptools)

---

## 8. Action Items

### 8.1 Immediate Next Steps (Polymarket Dispatcher)

1. **Week 1: Prototype** (De-risk WebSocket + HTTP)
   - [ ] Create minimal CMake project
   - [ ] Test Boost.Beast WebSocket connection to `wss://ws-subscriptions-clob.polymarket.com`
   - [ ] Test `cpr` HTTP GET to `https://gamma-api.polymarket.com/events`
   - [ ] Validate JSON parsing with `nlohmann::json`

2. **Week 2-3: Implementation**
   - [ ] Port `_types.py` to `polymarket_types.hpp/.cpp`
   - [ ] Implement `EnhancedPM` class with WebSocket subscriptions
   - [ ] Implement rolling file mechanism
   - [ ] Add unit tests (Google Test or Catch2)

3. **Week 4: Integration & Testing**
   - [ ] End-to-end integration test (fetch events, subscribe, receive data)
   - [ ] Memory leak testing (Valgrind)
   - [ ] Performance benchmarking vs Python
   - [ ] Documentation (README, API docs)

### 8.2 Decision Points

**Before starting:**
1. ❓ **Is polymarket a performance bottleneck?** (Profile Python version first)
2. ❓ **Do you need C++ for production deployment?** (Binary vs Python runtime)
3. ❓ **Can you commit 2-4 weeks?** (Plus 1-2 weeks buffer for testing)

**After polymarket dispatcher:**
1. ❓ **Was C++ worth it?** (Latency improvement, development velocity)
2. ❓ **Continue with Binance/IB?** (Expand or stop)
3. ❓ **Maintain Python version?** (Parallel or deprecate)

---

## 9. Technical Specifications (Polymarket C++)

### 9.1 Build System

```cmake
# CMakeLists.txt
cmake_minimum_required(VERSION 3.20)
project(argus_polymarket_cpp VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Dependencies
find_package(Boost 1.75 REQUIRED COMPONENTS system thread)
find_package(OpenSSL REQUIRED)
find_package(websocketpp REQUIRED)
find_package(nlohmann_json 3.10 REQUIRED)
find_package(cpr REQUIRED)

add_library(polymarket_dispatcher SHARED
    src/polymarket_types.cpp
    src/enhanced_pm.cpp
    src/websocket_client.cpp
)

target_include_directories(polymarket_dispatcher PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:include>
)

target_link_libraries(polymarket_dispatcher PUBLIC
    Boost::system
    Boost::thread
    OpenSSL::SSL
    websocketpp::websocketpp
    nlohmann_json::nlohmann_json
    cpr::cpr
)

# Example executable
add_executable(polymarket_example examples/example_usage.cpp)
target_link_libraries(polymarket_example PRIVATE polymarket_dispatcher)

# Tests
enable_testing()
add_subdirectory(tests)
```

### 9.2 Directory Structure

```
argus-cpp/
├── CMakeLists.txt
├── conanfile.txt (or vcpkg.json)
├── README.md
├── include/
│   └── argus/
│       ├── polymarket_types.hpp
│       ├── enhanced_pm.hpp
│       └── websocket_client.hpp
├── src/
│   ├── polymarket_types.cpp
│   ├── enhanced_pm.cpp
│   └── websocket_client.cpp
├── examples/
│   └── example_usage.cpp
└── tests/
    ├── test_types.cpp
    ├── test_websocket.cpp
    └── test_enhanced_pm.cpp
```

### 9.3 API Example (C++)

```cpp
#include <argus/enhanced_pm.hpp>
#include <iostream>

int main() {
    // Initialize client (dry mode, no credentials needed)
    argus::EnhancedPM client;
    
    // Fetch events
    auto events = client.fetch_events(20);
    for (const auto& event : events) {
        std::cout << "Event: " << event.title << "\n";
        std::cout << "  Active: " << (event.active ? "Yes" : "No") << "\n";
    }
    
    // Subscribe to market data
    std::vector<std::string> asset_ids = {"asset_123", "asset_456"};
    client.subscribe_to_market_data(asset_ids, [](const nlohmann::json& data) {
        std::cout << "Market update: " << data.dump() << "\n";
    });
    
    // Run WebSocket loop (blocks)
    client.run();
    
    return 0;
}
```

---

## 10. Conclusion

### Summary Table

| Conversion Scope | Effort | Risk | ROI | Recommendation |
|------------------|--------|------|-----|----------------|
| **Full Argus-CPP** | 6-12 months | High | High* | ⚠️ Only if HFT performance critical |
| **Polymarket Only** | 2-4 weeks | Low | Medium | ✅ Good learning exercise, low risk |
| **Incremental (3-4 modules)** | 3-6 months | Medium | High | ✅ **Recommended** (best balance) |
| **Hybrid (C++ + Python)** | 2-4 weeks initial | Low | High | ✅ **Highly Recommended** (pragmatic) |

\* ROI high **only if** latency is critical (HFT, algo trading). For data analysis/backtesting, Python is sufficient.

### Final Recommendation

**For most use cases:**
1. ✅ **Start with polymarket dispatcher C++ conversion** (2-4 weeks)
2. ✅ **Use pybind11 to call from Python** (preserve ecosystem)
3. ✅ **Measure performance improvement** (latency, throughput)
4. ✅ **Decide based on data** (continue or stop)

**For high-frequency trading:**
1. ✅ **Commit to full argus-cpp** (6-12 months, 2-3 engineers)
2. ✅ **Start with polymarket + binance** (validate architecture)
3. ✅ **Convert IB dispatcher** (highest value)
4. ⚠️ **Maintain Python for prototyping** (new strategies, modules)

### Key Takeaways

- ✅ **Technically feasible** - All dependencies have C++ equivalents
- ⚠️ **Significant effort** - 6-12 months for full conversion
- ✅ **Polymarket is easy** - 2-4 weeks, good starting point
- ✅ **Incremental approach** - Best risk/reward profile
- ⚠️ **Profile first** - Ensure Python is actually bottleneck
- ✅ **Hybrid is pragmatic** - C++ where needed, Python elsewhere

---

## Appendix A: Polymarket Dispatcher Code Samples

### A.1 Python (Current)

```python
class EnhancedPM:
    def __init__(self, private_key, proxy_funder, dry_mode=False):
        self.session = requests.Session()
        self.market_ws = WebSocketApp('wss://...')
        self.idx_to_callback = {}
        
    def fetch_events(self, offset=0, limit=20):
        url = f'https://gamma-api.polymarket.com/events?limit={limit}&offset={offset}'
        response = self.session.get(url)
        return [PolymarketEvent.from_dict(e) for e in response.json()]
        
    def subscribe_to_market_data(self, asset_ids, callback):
        for idx in asset_ids:
            self.idx_to_callback[idx] = callback
        self.market_ws.send(json.dumps({'assets_ids': asset_ids, 'type': 'market'}))
```

### A.2 C++ (Proposed)

```cpp
class EnhancedPM {
public:
    EnhancedPM(const std::string& private_key = "", 
               const std::string& proxy_funder = "",
               bool dry_mode = true);
    
    std::vector<PolymarketEvent> fetch_events(int offset = 0, int limit = 20);
    
    void subscribe_to_market_data(
        const std::vector<std::string>& asset_ids,
        std::function<void(const json&)> callback
    );
    
    void run();  // Blocking WebSocket event loop
    void stop(); // Graceful shutdown
    
private:
    void on_ws_open(websocketpp::connection_hdl hdl);
    void on_ws_message(websocketpp::connection_hdl hdl, message_ptr msg);
    void on_ws_close(websocketpp::connection_hdl hdl);
    
    cpr::Session http_session_;
    websocketpp::client<websocketpp::config::asio_tls_client> ws_client_;
    std::unordered_map<std::string, std::function<void(const json&)>> callbacks_;
    std::mutex callbacks_mutex_;
    std::atomic<bool> running_;
};
```

---

## Appendix B: Resources

### Documentation
- Boost.Beast: https://www.boost.org/doc/libs/1_84_0/libs/beast/doc/html/index.html
- websocketpp: https://github.com/zaphoyd/websocketpp
- nlohmann/json: https://json.nlohmann.me/
- cpr: https://docs.libcpr.org/

### Build Tools
- Conan: https://conan.io/
- vcpkg: https://vcpkg.io/

### Testing
- Google Test: https://github.com/google/googletest
- Catch2: https://github.com/catchorg/Catch2

---

**End of Analysis**
