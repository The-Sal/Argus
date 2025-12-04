# Linux Compatibility Analysis for argus_swift

## Executive Summary

The `argus_swift` codebase is **very close to Linux compatibility**. The codebase has been **intentionally designed** with Linux support in mind, using conditional compilation throughout. The main blocker is **URLSessionWebSocketTask**, which is not available in swift-corelibs-foundation on Linux.

### Compatibility Score: **85-90% Linux Ready**

---

## What's Already There (Linux-Ready)

### ✅ Conditional Compilation Already Implemented

The codebase already includes proper platform detection in multiple files:

**1. Socket/Low-Level Code (`SocketProtocol.swift`, `MKTDispatcher.swift`, `IBDispatcher.swift`, `CapitalComDispatcher.swift`):**
```swift
#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#endif
```

**2. Networking (`BinanceWebSocket.swift`, `CapitalComWebSocket.swift`):**
```swift
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
```

**3. Platform Detection (`main.swift`):**
```swift
func getSystemInfo() -> String {
    #if os(macOS)
    return "macOS"
    #elseif os(Linux)
    return "Linux"
    #elseif os(Windows)
    return "Windows"
    #else
    return "Unknown"
    #endif
}
```

### ✅ POSIX Socket APIs (100% Compatible)

All TCP/Unix socket code uses POSIX APIs that work identically on Linux:
- `socket()`, `bind()`, `listen()`, `accept()`
- `send()`, `recv()`, `close()`
- `sockaddr_in`, `sockaddr_un`, `AF_INET`, `AF_UNIX`
- `setsockopt()` with `SO_REUSEADDR`

**Files using POSIX sockets:**
- `SocketProtocol.swift` - RealSocket class
- `MKTDispatcher.swift` - Binance TCP server
- `IBDispatcher.swift` - IB TCP server
- `CapitalComDispatcher.swift` - Unix domain socket server

### ✅ Foundation Types (100% Compatible)

All these work on Linux via swift-corelibs-foundation:
- `Data`, `Date`, `URL`, `URLRequest`
- `URLSession`, `URLSessionConfiguration`
- `JSONSerialization`
- `FileManager`
- `ProcessInfo`
- `DispatchQueue`, `DispatchSemaphore`
- `NSLock`
- `Thread`

---

## What's NOT There (Linux Blockers)

### ❌ URLSessionWebSocketTask (Critical Blocker)

**Files Affected:**
- `BinanceWebSocket.swift`
- `IBWebSocket.swift`
- `CapitalComWebSocket.swift`
- `PolymarketWebSocket.swift`
- `IBForecastWebSocket.swift`

**The Problem:**
`URLSessionWebSocketTask` is part of Apple's Foundation framework but is **NOT implemented** in swift-corelibs-foundation for Linux.

```swift
// This code will NOT compile on Linux:
ws = urlSession.webSocketTask(with: url)
ws?.resume()
ws?.receive { result in ... }
ws?.send(.string(message)) { error in ... }
```

**Error on Linux:**
```
error: value of type 'URLSession' has no member 'webSocketTask'
```

### ⚠️ Timer.scheduledTimer on Background Thread

**File Affected:**
- `CapitalComWebSocket.swift`

**The Code:**
```swift
private func startPingTimer() {
    DispatchQueue.main.async { [weak self] in
        self?.pingTimer = Timer.scheduledTimer(withTimeInterval: ...) { ... }
    }
}
```

**The Problem:**
On Linux, there's no automatic RunLoop on background threads. While `Timer` exists, `scheduledTimer` on `DispatchQueue.main` may not work as expected without a run loop.

---

## Detailed File-by-File Analysis

| File | Linux Ready | Issue | Solution |
|------|-------------|-------|----------|
| `main.swift` | ✅ Yes | None | Already has Linux detection |
| `BinanceClasses.swift` | ✅ Yes | None | Pure Swift structs |
| `BinanceWebSocket.swift` | ❌ **No** | URLSessionWebSocketTask | Use third-party WebSocket library |
| `CapitalComClasses.swift` | ✅ Yes | None | Pure Swift structs |
| `CapitalComDispatcher.swift` | ✅ Yes | None | Uses Glibc conditionally |
| `CapitalComWebSocket.swift` | ❌ **No** | URLSessionWebSocketTask, Timer | Replace WebSocket + use DispatchSourceTimer |
| `EnvLoader.swift` | ✅ Yes | None | FileManager works on Linux |
| `IBAccountProvider.swift` | ✅ Yes | None | Uses compatible APIs |
| `IBClasses.swift` | ✅ Yes | None | Pure Swift structs |
| `IBDispatcher.swift` | ✅ Yes | None | Uses Glibc conditionally |
| `IBFields.swift` | ✅ Yes | None | Pure Swift constants |
| `IBForecastClasses.swift` | ✅ Yes | None | Pure Swift structs |
| `IBForecastDispatcher.swift` | ✅ Yes | None | Uses Glibc conditionally |
| `IBForecastWebSocket.swift` | ❌ **No** | URLSessionWebSocketTask | Use third-party WebSocket library |
| `IBNetworker.swift` | ✅ Yes | None | URLSession HTTP works on Linux |
| `IBWebSocket.swift` | ❌ **No** | URLSessionWebSocketTask | Use third-party WebSocket library |
| `MarketData.swift` | ✅ Yes | None | Pure Swift classes |
| `MKTDispatcher.swift` | ✅ Yes | None | Uses Glibc conditionally |
| `PolymarketClasses.swift` | ✅ Yes | None | Pure Swift structs |
| `PolymarketExample.swift` | ⚠️ Partial | Uses PolymarketWebSocket | Depends on WebSocket fix |
| `PolymarketWebSocket.swift` | ❌ **No** | URLSessionWebSocketTask | Use third-party WebSocket library |
| `Protocol2Utils.swift` | ✅ Yes | None | Pure Swift |
| `SocketProtocol.swift` | ✅ Yes | None | Uses Glibc conditionally |

---

## Solution: Linux WebSocket Libraries

To make the codebase compile on Linux, replace `URLSessionWebSocketTask` with one of these:

### Option 1: SwiftNIO + WebSocketKit (Recommended)

```swift
// Package.swift
dependencies: [
    .package(url: "https://github.com/vapor/websocket-kit.git", from: "2.0.0"),
]

// Usage
import WebSocketKit
import NIOCore
import NIOPosix

// Create event loop group first
let eventLoopGroup = MultiThreadedEventLoopGroup(numberOfThreads: 1)

// Connect to WebSocket
WebSocket.connect(to: url, on: eventLoopGroup) { ws in
    ws.onText { text in
        // Handle message
    }
    ws.send("message")
}
```

**Pros:**
- Production-ready, used by Vapor framework
- Async/await support
- Works on both macOS and Linux

**Cons:**
- Adds dependencies (SwiftNIO)
- Requires code changes to async/await pattern

### Option 2: Starscream

```swift
// Package.swift
dependencies: [
    .package(url: "https://github.com/daltoniam/Starscream.git", from: "4.0.0"),
]
```

**Note:** Starscream is primarily designed for Apple platforms (iOS/macOS). It has limited, unofficial Linux support and is **not recommended** for Linux deployments. Use SwiftNIO/WebSocketKit instead for Linux.

### Option 3: Conditional Compilation

Keep `URLSessionWebSocketTask` for macOS, use alternative for Linux:

```swift
#if canImport(Darwin)
// Use URLSessionWebSocketTask (current code)
private var ws: URLSessionWebSocketTask?
#else
// Use SwiftNIO/WebSocketKit for Linux
import WebSocketKit
private var ws: WebSocket?
#endif
```

---

## Package.swift Changes for Linux

Current `Package.swift` specifies macOS only:
```swift
platforms: [
    .macOS(.v13)
]
```

For Linux, you have two options:

**Option A: Remove platforms array entirely (simplest)**
```swift
let package = Package(
    name: "ArgusServer",
    // No platforms array - builds on any platform
    products: [
        // ...
    ],
    // ...
)
```

**Option B: Keep macOS minimum but still build on Linux**

Linux ignores the `platforms` array, so the current Package.swift will still compile on Linux. The `platforms` constraint only applies to Apple platforms. However, removing it makes the intent clearer.

---

## Summary Statistics

| Category | Count | Percentage |
|----------|-------|------------|
| **Fully Linux Compatible** | 17 | 77% |
| **Partially Compatible** | 1 | 5% |
| **Not Compatible (WebSocket)** | 5 | 18% |

---

## What's Already Great for Linux

1. **Conditional Imports** - `#if canImport(Darwin)` / `#elseif canImport(Glibc)` already in place
2. **POSIX Socket Code** - All TCP server code uses standard POSIX APIs
3. **Platform Detection** - `#if os(Linux)` already used in `main.swift`
4. **FoundationNetworking Import** - Already imported where needed for Linux URLSession
5. **No Apple-Only Frameworks** - No UIKit, AppKit, or other Apple-exclusive imports

---

## Migration Path to Linux

### Phase 1: Add WebSocket Dependency

```swift
// Package.swift
dependencies: [
    .package(url: "https://github.com/vapor/websocket-kit.git", from: "2.0.0"),
],
targets: [
    .executableTarget(
        name: "ArgusServer",
        dependencies: [
            .product(name: "WebSocketKit", package: "websocket-kit"),
        ],
        // ...
    ),
]
```

### Phase 2: Create WebSocket Abstraction

```swift
// WebSocketClient.swift
protocol WebSocketClient {
    func connect(to url: URL) async throws
    func send(_ text: String) async throws
    func receive() async throws -> String
    func close()
}

#if canImport(Darwin)
class URLSessionWebSocketClient: WebSocketClient {
    // Use URLSessionWebSocketTask
}
#else
class NIOWebSocketClient: WebSocketClient {
    // Use WebSocketKit
}
#endif
```

### Phase 3: Update WebSocket Managers

Replace direct `URLSessionWebSocketTask` usage with the abstraction layer.

### Phase 4: Fix Timer Issue

```swift
// Replace Timer.scheduledTimer with DispatchSourceTimer
private var pingTimer: DispatchSourceTimer?

private func startPingTimer() {
    pingTimer = DispatchSource.makeTimerSource(queue: .global())
    pingTimer?.schedule(deadline: .now(), repeating: appPingInterval)
    pingTimer?.setEventHandler { [weak self] in
        self?.sendApplicationPing()
    }
    pingTimer?.resume()
}
```

---

## Conclusion

**argus_swift is 85-90% ready for Linux.** The developers have already done excellent work with:
- Conditional imports for Glibc vs Darwin
- Standard POSIX socket APIs
- Platform detection

The **only major blocker** is `URLSessionWebSocketTask`, which requires adding a third-party WebSocket library for Linux support.

### Estimated Effort:
- **Add WebSocket library + abstraction:** 4-8 hours
- **Update all WebSocket managers:** 2-4 hours  
- **Testing on Linux:** 2-4 hours
- **Total:** 1-2 days of development

The codebase is well-prepared for Linux—it just needs the WebSocket piece completed.
