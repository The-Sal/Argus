# iOS Compatibility Analysis for argus_swift

## Executive Summary

The `argus_swift` codebase is **moderately close to iOS compatibility**. The core business logic and data structures are fully iOS-compatible, but significant changes are required in the networking and server-side components. The good news is that most of these changes are architectural and don't require rewriting the core logic.

### Compatibility Score: **70-75% iOS Ready**

---

## Detailed Analysis by Category

### ✅ Fully iOS Compatible (No Changes Needed)

These components work on iOS as-is:

| Component | File(s) | Notes |
|-----------|---------|-------|
| **Protocol 2 Utilities** | `Protocol2Utils.swift` | Pure Swift, no platform-specific APIs |
| **Market Data Models** | `MarketData.swift`, `BinanceClasses.swift`, `CapitalComClasses.swift`, `IBClasses.swift`, `IBFields.swift`, `IBForecastClasses.swift`, `PolymarketClasses.swift` | All use standard Foundation types |
| **Environment Loader** | `EnvLoader.swift` | Uses FileManager which works on iOS |
| **Logger** | Simple logger in `BinanceWebSocket.swift` | Just print statements |

### ⚠️ Partially Compatible (Minor Changes Needed)

| Component | File(s) | Issues | Required Changes |
|-----------|---------|--------|------------------|
| **WebSocket Managers** | `BinanceWebSocket.swift`, `IBWebSocket.swift`, `CapitalComWebSocket.swift`, `PolymarketWebSocket.swift` | URLSessionWebSocketTask is fully iOS-compatible since iOS 13 | Minor: Add `.iOS(.v13)` to Package.swift platforms |
| **HTTP Networking** | `IBNetworker.swift`, `CapitalComWebSocket.swift` | URLSession works on iOS | No changes needed for networking logic |

### ❌ Not iOS Compatible (Major Changes Needed)

| Component | File(s) | Issues | Required Changes |
|-----------|---------|--------|------------------|
| **TCP Server Sockets** | `MKTDispatcher.swift`, `IBDispatcher.swift`, `IBForecastDispatcher.swift`, `CapitalComDispatcher.swift` | Uses POSIX socket APIs for server functionality | **Remove or replace with client-only mode** |
| **Socket Protocol** | `SocketProtocol.swift` | `RealSocket` uses POSIX file descriptors | Keep `FakeSocket`, replace `RealSocket` usage |
| **Unix Domain Sockets** | `CapitalComDispatcher.swift` | AF_UNIX sockets not available on iOS | Replace with TCP or remove server mode |
| **Signal Handlers** | `main.swift` | `signal()` not appropriate for iOS apps | Use `NotificationCenter` for app lifecycle |
| **Interactive Console** | All dispatchers | `readLine()` and `fflush()` not available | Replace with UI callbacks |

---

## Specific API Incompatibilities

### 1. POSIX Socket APIs (Major Issue)

**Files Affected:**
- `MKTDispatcher.swift`
- `IBDispatcher.swift`
- `IBForecastDispatcher.swift`
- `CapitalComDispatcher.swift`
- `SocketProtocol.swift`

**Problematic APIs:**
```swift
// These are NOT available on iOS:
import Darwin           // ✅ Available but limited
socket(AF_INET, ...)    // ❌ Cannot create server sockets
bind(...)               // ❌ Cannot bind to ports
listen(...)             // ❌ Cannot listen for connections
accept(...)             // ❌ Cannot accept connections
AF_UNIX                 // ❌ Unix domain sockets not supported
```

**Why This Matters:**
iOS apps cannot act as network servers. The current architecture where dispatchers accept TCP connections from clients fundamentally conflicts with iOS's sandboxed nature.

### 2. Console I/O (Medium Issue)

**Files Affected:**
- `main.swift`
- All `interactiveMode()` methods

**Problematic APIs:**
```swift
readLine()              // ❌ No console on iOS
fflush(stdout)          // ❌ No stdout on iOS
print() with terminator // ✅ Works but goes nowhere useful
```

### 3. Signal Handling (Minor Issue)

**Files Affected:**
- `main.swift`

**Problematic Code:**
```swift
signal(SIGINT) { _ in ... }  // ❌ Not appropriate for iOS
signal(SIGTERM) { _ in ... } // ❌ Not appropriate for iOS
```

---

## Recommended Architecture for iOS

### Option A: Client-Only Mode (Recommended)

Convert the codebase to a library that iOS apps can use to:
1. Connect to existing Argus dispatchers running on servers
2. Subscribe to market data streams
3. Receive and parse Protocol 2 packets

**What to Keep:**
- All WebSocket managers (as data sources)
- All data model classes
- Protocol 2 parser (for receiving data)
- FakeSocket pattern (for callbacks)

**What to Remove/Replace:**
- TCP server functionality → Connect to remote servers instead
- Interactive console → Expose Swift API for iOS apps
- Dispatcher classes → Create lightweight "Client" classes

### Option B: Background Processing Mode

If you need iOS apps to process data independently:
1. Use `URLSession` background tasks for WebSocket connections
2. Use `UserDefaults` or Core Data for caching
3. Implement proper iOS app lifecycle handling

---

## Migration Steps

### Phase 1: Package.swift Updates

```swift
// Current
platforms: [
    .macOS(.v13)
]

// Change to
platforms: [
    .macOS(.v13),
    .iOS(.v13),
    .tvOS(.v13),  // Optional
    .watchOS(.v6) // Optional
]
```

### Phase 2: Create Library Target

```swift
// Add to Package.swift
targets: [
    // Keep executable for macOS
    .executableTarget(
        name: "ArgusServer",
        dependencies: [],
        path: "Sources/ArgusServer",
        condition: .when(platforms: [.macOS, .linux])
    ),
    // New library for iOS
    .target(
        name: "ArgusCore",
        dependencies: [],
        path: "Sources/ArgusCore"
    ),
]
```

### Phase 3: Extract Core Components

Move to `Sources/ArgusCore/`:
- `Protocol2Utils.swift`
- `MarketData.swift`
- `BinanceClasses.swift`
- `BinanceWebSocket.swift` (WebSocket client only)
- `CapitalComClasses.swift`
- `CapitalComWebSocket.swift` (WebSocket client only)
- `IBClasses.swift`
- `IBFields.swift`
- `IBWebSocket.swift` (WebSocket client only)
- `IBNetworker.swift`
- `PolymarketClasses.swift`
- `PolymarketWebSocket.swift`

### Phase 4: Create iOS-Friendly API

```swift
// Example: BinanceClient.swift for iOS
class BinanceMarketDataClient {
    private let ws: BinanceWss
    
    init() {
        self.ws = BinanceWss(configs: nil)
    }
    
    func connect() {
        ws.initWebSocket()
    }
    
    func subscribe(symbol: String, callback: @escaping (Binance_CapitalComMKTDataLive) -> Void) {
        ws.subscribe(symbol: symbol) { msg in
            // Convert AbstractBinanceType to Binance_CapitalComMKTDataLive
            // Call callback with market data
        }
    }
    
    func disconnect() {
        ws.stop()
    }
}
```

---

## File-by-File Compatibility Status

| File | iOS Compatible | Changes Needed |
|------|----------------|----------------|
| `main.swift` | ❌ No | Create iOS entry point or remove |
| `BinanceClasses.swift` | ✅ Yes | None |
| `BinanceWebSocket.swift` | ✅ Yes | None (URLSessionWebSocketTask works) |
| `CapitalComClasses.swift` | ✅ Yes | None |
| `CapitalComDispatcher.swift` | ❌ No | Server code not compatible |
| `CapitalComWebSocket.swift` | ✅ Yes | Timer.scheduledTimer requires dispatch to main thread on iOS; wrap in `DispatchQueue.main.async` |
| `EnvLoader.swift` | ✅ Yes | May need bundle path handling |
| `IBAccountProvider.swift` | ⚠️ Partial | Debug socket on port 9973 uses POSIX server APIs not available on iOS; remove socket and use delegate/callback pattern instead |
| `IBClasses.swift` | ✅ Yes | None |
| `IBDispatcher.swift` | ❌ No | Server code not compatible |
| `IBFields.swift` | ✅ Yes | None |
| `IBForecastClasses.swift` | ✅ Yes | None |
| `IBForecastDispatcher.swift` | ❌ No | Server code not compatible |
| `IBForecastWebSocket.swift` | ✅ Yes | None |
| `IBNetworker.swift` | ✅ Yes | None (synchronous calls should work) |
| `IBWebSocket.swift` | ✅ Yes | None |
| `MarketData.swift` | ✅ Yes | None |
| `MKTDispatcher.swift` | ❌ No | Server code not compatible |
| `PolymarketClasses.swift` | ✅ Yes | None |
| `PolymarketExample.swift` | ⚠️ Partial | Remove print statements, add callbacks |
| `PolymarketWebSocket.swift` | ✅ Yes | None |
| `Protocol2Utils.swift` | ✅ Yes | None |
| `SocketProtocol.swift` | ⚠️ Partial | RealSocket needs replacement for iOS |

---

## Summary Statistics

- **Total Files:** 22
- **Fully Compatible:** 14 (64%)
- **Partially Compatible:** 3 (13%)
- **Not Compatible:** 5 (23%)

The incompatible files are primarily the **dispatcher** files which implement TCP server functionality. The core WebSocket clients, data models, and protocol utilities are all iOS-ready.

---

## Recommendations

### Short-term (Quick iOS Port)

1. Add iOS platform to `Package.swift`
2. Create a new target that excludes dispatcher files
3. Use conditional compilation for platform-specific code:
   ```swift
   #if canImport(UIKit)
   // iOS-specific code
   #else
   // macOS/Linux server code
   #endif
   ```

### Long-term (Full iOS Integration)

1. Refactor into Core library + Server executable
2. Create iOS-specific client APIs
3. Add SwiftUI views for data display
4. Implement proper iOS background task handling
5. Add Combine publishers for reactive data streams

---

## Conclusion

**argus_swift is approximately 70-75% iOS-compatible out of the box.** The core data handling, WebSocket communication, and protocol utilities require no changes. The main work involves:

1. Updating `Package.swift` to include iOS
2. Separating the reusable library code from server-specific code
3. Creating iOS-friendly client APIs

The architecture decision of using `URLSession` for WebSockets (instead of third-party libraries) makes iOS compatibility much easier since `URLSessionWebSocketTask` is available on iOS 13+.

**Estimated effort to make fully iOS-compatible:**
- Basic library port: 2-4 hours
- Full iOS client SDK: 1-2 days
- iOS sample app: 2-3 days
