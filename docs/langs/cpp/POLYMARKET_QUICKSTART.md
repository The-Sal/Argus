# Polymarket Dispatcher C++ Conversion - Quick Start Guide

**Estimated Effort:** 2-4 weeks  
**Complexity:** Low-Medium  
**Status:** Recommended as first C++ conversion project

---

## TL;DR

Converting the polymarket dispatcher to C++ is:
- ✅ **Feasible**: All dependencies have mature C++ alternatives
- ✅ **Low Risk**: Isolated module, clean API, no complex dependencies
- ✅ **Good ROI**: Performance improvement + validation of C++ architecture
- ⏱️ **2-4 weeks**: With buffer for testing and documentation

---

## What Needs to be Converted

### Files to Port

| Python File | LOC | C++ Equivalent | Est. LOC |
|-------------|-----|----------------|----------|
| `polymarket_direct/__init__.py` | 304 | `enhanced_pm.cpp` + `.hpp` | 600 |
| `polymarket_direct/_types.py` | 538 | `polymarket_types.cpp` + `.hpp` | 600 |
| `polymarket_direct/_example.py` | 544 | `examples/example_usage.cpp` | 100 |

**Total:** ~1,400 LOC Python → ~2,250 LOC C++ (includes tests, CMake)

---

## Dependencies

### Current Python Dependencies
```python
requests              # HTTP client
websocket-client      # WebSocket
utils3                # Threading decorator (custom, needs port)
json                  # JSON parsing
threading             # Concurrency
uuid                  # Unique IDs
time                  # Sleep, timestamps
```

### C++ Replacements
```cpp
cpr                   // HTTP client (or cpp-httplib, libcurl)
websocketpp           // WebSocket (or Boost.Beast)
nlohmann/json         // JSON parsing
std::thread           // Concurrency
std::mutex            // Thread safety
boost::uuids          // Unique IDs
std::chrono           // Time operations
```

**All available via Conan/vcpkg** ✅

---

## Architecture Overview

### Current Python Architecture

```
EnhancedPM
├── HTTP Session (requests.Session)
│   └── fetch_events() → REST API call
├── WebSocket (WebSocketApp)
│   ├── on_open → Release semaphore
│   ├── on_message → Parse JSON, call callbacks
│   ├── on_close → Auto-reconnect logic
│   └── on_error → Notification
├── Callback System (dict)
│   └── asset_id → callback function
└── Message Logging (rolling file mechanism)
    ├── ws_messages list (in-memory buffer)
    ├── Periodic file write (30s interval)
    └── Rollover when > 5000 messages
```

### Proposed C++ Architecture

```cpp
class EnhancedPM {
private:
    // HTTP client
    cpr::Session http_session_;
    
    // WebSocket client (option 1: websocketpp)
    websocketpp::client<websocketpp::config::asio_tls_client> ws_client_;
    
    // Or (option 2: Boost.Beast)
    boost::asio::io_context io_context_;
    boost::beast::websocket::stream<boost::beast::ssl_stream<boost::asio::ip::tcp::socket>> ws_stream_;
    
    // Callback system
    std::unordered_map<std::string, std::function<void(const json&)>> callbacks_;
    std::mutex callbacks_mutex_;
    
    // Message buffer
    std::vector<json> ws_messages_;
    std::mutex messages_mutex_;
    
    // Rolling mechanism
    std::atomic<size_t> message_seg_id_{0};
    boost::uuids::uuid uuid_;
    std::thread rolling_thread_;
    std::atomic<bool> running_{false};
    
public:
    std::vector<PolymarketEvent> fetch_events(int offset = 0, int limit = 20);
    void subscribe_to_market_data(const std::vector<std::string>& asset_ids,
                                   std::function<void(const json&)> callback);
    void run();  // Blocking event loop
    void stop(); // Graceful shutdown
};
```

---

## Implementation Checklist

### Week 1: Setup & HTTP
- [ ] **Day 1-2:** CMake setup
  - [ ] Create `CMakeLists.txt`
  - [ ] Add Conan/vcpkg dependencies
  - [ ] Test build system
- [ ] **Day 3-4:** HTTP client
  - [ ] Implement `fetch_events()` with `cpr`
  - [ ] Test against live API: `https://gamma-api.polymarket.com/events`
  - [ ] Verify JSON parsing with `nlohmann::json`
- [ ] **Day 5:** Type definitions
  - [ ] Port `Market` struct (100+ fields, all `std::optional`)
  - [ ] Port `PolymarketEvent` struct
  - [ ] Implement `from_json()` / `to_json()` helpers

### Week 2: WebSocket
- [ ] **Day 1-2:** WebSocket setup
  - [ ] Initialize `websocketpp::client` (or Boost.Beast)
  - [ ] Connect to `wss://ws-subscriptions-clob.polymarket.com/ws/market`
  - [ ] Test SSL/TLS connection
- [ ] **Day 3:** Message handling
  - [ ] Implement `on_message` handler
  - [ ] Parse JSON and route to callbacks
  - [ ] Test with live market data
- [ ] **Day 4-5:** Reconnection logic
  - [ ] Implement `on_close` handler
  - [ ] Auto-reconnect with exponential backoff
  - [ ] Resubscribe to previous asset_ids after reconnect

### Week 3: Features & Testing
- [ ] **Day 1-2:** Rolling file mechanism
  - [ ] Implement background thread with 30s interval
  - [ ] Write messages to `ws_messages_{uuid}-{seg_id}.fk`
  - [ ] Rollover when buffer exceeds 5000 messages
- [ ] **Day 3:** Threading & callbacks
  - [ ] Thread-safe callback registration
  - [ ] Mutex protection for `callbacks_` and `ws_messages_`
  - [ ] Test with multiple concurrent subscriptions
- [ ] **Day 4-5:** Integration tests
  - [ ] End-to-end test: fetch events + subscribe + receive data
  - [ ] Test reconnection after forced disconnect
  - [ ] Test with multiple asset_ids

### Week 4: Polish & Documentation
- [ ] **Day 1-2:** Error handling
  - [ ] HTTP errors (network failures, 4xx/5xx responses)
  - [ ] WebSocket errors (SSL failures, parse errors)
  - [ ] Graceful shutdown (`stop()` method)
- [ ] **Day 3:** Performance testing
  - [ ] Benchmark latency vs Python version
  - [ ] Memory leak testing with Valgrind
  - [ ] CPU profiling (perf, gprof)
- [ ] **Day 4-5:** Documentation
  - [ ] API documentation (Doxygen or inline comments)
  - [ ] README with examples
  - [ ] Build instructions

---

## CMake Template

```cmake
cmake_minimum_required(VERSION 3.20)
project(argus_polymarket_cpp VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)  # For IDE integration

# Option: Use Conan
include(${CMAKE_BINARY_DIR}/conanbuildinfo.cmake)
conan_basic_setup(TARGETS)

# Or option: Use vcpkg
# set(CMAKE_TOOLCHAIN_FILE "${VCPKG_ROOT}/scripts/buildsystems/vcpkg.cmake")

# Dependencies
find_package(Boost 1.75 REQUIRED COMPONENTS system thread)
find_package(OpenSSL REQUIRED)
find_package(websocketpp REQUIRED)
find_package(nlohmann_json 3.10 REQUIRED)
find_package(cpr REQUIRED)

# Library
add_library(polymarket_dispatcher SHARED
    src/polymarket_types.cpp
    src/enhanced_pm.cpp
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

# Compiler warnings
target_compile_options(polymarket_dispatcher PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-Wall -Wextra -Wpedantic>
    $<$<CXX_COMPILER_ID:MSVC>:/W4>
)

# Example executable
add_executable(polymarket_example examples/example_usage.cpp)
target_link_libraries(polymarket_example PRIVATE polymarket_dispatcher)

# Tests
enable_testing()
add_subdirectory(tests)

# Install rules
install(TARGETS polymarket_dispatcher
    LIBRARY DESTINATION lib
    ARCHIVE DESTINATION lib
)

install(DIRECTORY include/ DESTINATION include)
```

---

## Conan Dependencies

```ini
# conanfile.txt
[requires]
boost/1.82.0
openssl/3.1.0
websocketpp/0.8.2
nlohmann_json/3.11.2
cpr/1.10.5

[generators]
cmake

[options]
boost:shared=True
openssl:shared=True
```

```bash
# Install dependencies
mkdir build && cd build
conan install .. --build=missing
cmake .. -DCMAKE_BUILD_TYPE=Release
make -j$(nproc)
```

---

## Code Samples

### Fetch Events (C++)

```cpp
std::vector<PolymarketEvent> EnhancedPM::fetch_events(int offset, int limit) {
    std::string url = std::format(
        "https://gamma-api.polymarket.com/events?limit={}&offset={}&closed=false",
        limit, offset
    );
    
    cpr::Response response = cpr::Get(
        cpr::Url{url},
        cpr::Header{{"User-Agent", "argus-cpp/1.0"}}
    );
    
    if (response.status_code != 200) {
        throw std::runtime_error("HTTP error: " + std::to_string(response.status_code));
    }
    
    json data = json::parse(response.text);
    std::vector<PolymarketEvent> events;
    
    for (const auto& event_json : data) {
        try {
            events.push_back(PolymarketEvent::from_json(event_json));
        } catch (const std::exception& e) {
            std::cerr << "Error parsing event: " << e.what() << "\n";
        }
    }
    
    return events;
}
```

### Subscribe to Market Data (C++)

```cpp
void EnhancedPM::subscribe_to_market_data(
    const std::vector<std::string>& asset_ids,
    std::function<void(const json&)> callback
) {
    std::lock_guard<std::mutex> lock(callbacks_mutex_);
    
    for (const auto& asset_id : asset_ids) {
        callbacks_[asset_id] = callback;
    }
    
    json subscribe_msg = {
        {"assets_ids", asset_ids},
        {"type", "market"}
    };
    
    ws_client_.send(
        connection_hdl_,
        subscribe_msg.dump(),
        websocketpp::frame::opcode::text
    );
}
```

### WebSocket Message Handler (C++)

```cpp
void EnhancedPM::on_ws_message(
    websocketpp::connection_hdl hdl,
    websocketpp::client<...>::message_ptr msg
) {
    try {
        json data = json::parse(msg->get_payload());
        
        // Store message for rolling file mechanism
        {
            std::lock_guard<std::mutex> lock(messages_mutex_);
            ws_messages_.push_back(data);
        }
        
        // Route price changes to callbacks
        if (data.contains("price_changes")) {
            for (const auto& change : data["price_changes"]) {
                std::string asset_id = change["asset_id"];
                
                std::lock_guard<std::mutex> lock(callbacks_mutex_);
                if (auto it = callbacks_.find(asset_id); it != callbacks_.end()) {
                    it->second(change);  // Call registered callback
                }
            }
        }
    } catch (const std::exception& e) {
        std::cerr << "Error parsing WebSocket message: " << e.what() << "\n";
    }
}
```

### Type Definitions (C++)

```cpp
// polymarket_types.hpp
struct Market {
    std::optional<std::string> id;
    std::optional<std::string> question;
    std::optional<double> volume;
    std::optional<bool> active;
    // ... 100+ more optional fields
    
    static Market from_json(const json& j);
    json to_json() const;
};

struct PolymarketEvent {
    std::optional<std::string> id;
    std::optional<std::string> title;
    std::optional<std::string> description;
    std::optional<bool> active;
    std::vector<Market> markets;
    // ... more fields
    
    static PolymarketEvent from_json(const json& j);
    json to_json() const;
};

// polymarket_types.cpp
Market Market::from_json(const json& j) {
    Market m;
    if (j.contains("id")) m.id = j["id"];
    if (j.contains("question")) m.question = j["question"];
    if (j.contains("volume")) m.volume = j["volume"];
    if (j.contains("active")) m.active = j["active"];
    // ... handle all 100+ fields
    return m;
}
```

---

## Testing Strategy

### Unit Tests (Google Test)

```cpp
TEST(EnhancedPMTest, FetchEvents) {
    EnhancedPM client;
    auto events = client.fetch_events(0, 5);
    
    ASSERT_FALSE(events.empty());
    EXPECT_TRUE(events[0].id.has_value());
    EXPECT_TRUE(events[0].title.has_value());
}

TEST(EnhancedPMTest, WebSocketConnection) {
    EnhancedPM client;
    
    bool received_data = false;
    client.subscribe_to_market_data({"test_asset_id"}, [&](const json& data) {
        received_data = true;
    });
    
    // Run for 10 seconds
    std::thread run_thread([&client]() { client.run(); });
    std::this_thread::sleep_for(std::chrono::seconds(10));
    client.stop();
    run_thread.join();
    
    EXPECT_TRUE(received_data);
}
```

### Integration Tests

```bash
# Test build
mkdir build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Debug
make -j$(nproc)

# Run tests
ctest --output-on-failure

# Memory leak test
valgrind --leak-check=full ./polymarket_example

# Performance test
perf stat ./polymarket_example
```

---

## Performance Expectations

### Latency Comparison (Estimated)

| Operation | Python | C++ | Improvement |
|-----------|--------|-----|-------------|
| HTTP request (fetch_events) | 50-100ms | 40-80ms | 10-20% |
| JSON parsing (single event) | 0.5-1ms | 0.1-0.2ms | 5-10x |
| WebSocket message handling | 0.2-0.5ms | 0.05-0.1ms | 4-5x |
| Callback dispatch | 0.01ms | 0.002ms | 5x |

**Overall WebSocket pipeline:** 2-5x faster (Python: ~1ms, C++: ~0.2ms)

### Memory Usage (Estimated)

| Metric | Python | C++ | Improvement |
|--------|--------|-----|-------------|
| Base memory (idle) | ~50MB | ~5MB | 10x |
| Per message overhead | ~500 bytes | ~200 bytes | 2.5x |
| 10,000 messages buffered | ~55MB | ~7MB | 8x |

---

## Common Pitfalls & Solutions

### 1. WebSocket SSL/TLS Issues

**Problem:** Certificate verification failures
```
Error: certificate verify failed: unable to get local issuer certificate
```

**Solution:** Use system CA bundle
```cpp
boost::asio::ssl::context ctx{boost::asio::ssl::context::tlsv12_client};
ctx.set_default_verify_paths();  // Use system certs
ctx.set_verify_mode(boost::asio::ssl::verify_peer);
```

### 2. JSON Parsing Unknown Fields

**Problem:** C++ throws exception on unknown JSON fields
```cpp
json::parse(data);  // Throws if data has unexpected fields
```

**Solution:** Use `.contains()` checks
```cpp
if (j.contains("id") && !j["id"].is_null()) {
    market.id = j["id"];
}
```

### 3. Thread Safety

**Problem:** Data races on `callbacks_` map
```
Thread 1: callbacks_["asset_1"] = callback1;
Thread 2: auto cb = callbacks_["asset_1"];  // RACE CONDITION
```

**Solution:** Mutex protection
```cpp
std::lock_guard<std::mutex> lock(callbacks_mutex_);
callbacks_[asset_id] = callback;
```

### 4. Graceful Shutdown

**Problem:** WebSocket loop blocks forever
```cpp
ws_client_.run();  // Blocks indefinitely
```

**Solution:** Use `run_one()` with timeout or separate thread
```cpp
while (running_) {
    ws_client_.run_one_for(std::chrono::milliseconds(100));
}
```

---

## Next Steps After Completion

### Validation
1. ✅ Performance benchmarking (latency, throughput, memory)
2. ✅ Stability testing (24h+ continuous run)
3. ✅ Comparison with Python version (feature parity)

### Decision Points
1. ❓ Was C++ worth the effort? (2-4 weeks investment)
2. ❓ Continue with other modules? (Binance, IB, Capital.com)
3. ❓ Deprecate Python version or maintain both?

### If Successful
- Convert **Binance dispatcher** next (simplest, validates architecture)
- Build **common C++ infrastructure** (Protocol 2 parser, socket utils)
- Consider **full Argus-CPP** roadmap (6-12 months)

### If Not Worth It
- Keep Python as primary (faster iteration)
- Use C++ only for performance-critical bottlenecks
- Consider **Swift instead** (safer, similar performance)

---

## Resources

### Libraries
- websocketpp: https://github.com/zaphoyd/websocketpp
- Boost.Beast: https://www.boost.org/doc/libs/1_84_0/libs/beast/
- nlohmann/json: https://json.nlohmann.me/
- cpr: https://docs.libcpr.org/

### Tools
- Conan: https://conan.io/
- vcpkg: https://vcpkg.io/
- Google Test: https://github.com/google/googletest
- Valgrind: https://valgrind.org/

### Tutorials
- Boost.Beast WebSocket Client: https://www.boost.org/doc/libs/1_84_0/libs/beast/example/websocket/client/sync-ssl/
- nlohmann::json Tutorial: https://json.nlohmann.me/features/parsing/parse/
- Modern CMake Tutorial: https://cliutils.gitlab.io/modern-cmake/

---

**Summary:** Converting the polymarket dispatcher to C++ is a low-risk, high-value project that can be completed in 2-4 weeks. It's an excellent way to validate the C++ architecture before committing to a full Argus-CPP conversion.
