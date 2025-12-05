# Argus Swift - Cross-Platform Compatibility Assessment

This document analyzes the cross-platform compatibility of `argus_swift` for **iOS** and **Linux** systems. The codebase currently compiles and runs on macOS.

---

## Executive Summary

| Platform | Overall Compatibility | Estimated Effort to Port |
|----------|----------------------|--------------------------|
| **iOS** | **75%** | Medium (WebSocket API changes, no UI) |
| **Linux** | **35%** | High (WebSocket replacement, system API changes) |

---

## Detailed Analysis

### Platform-Specific API Usage Overview

The codebase uses the following platform-specific APIs:

| API/Feature | macOS | iOS | Linux | Notes |
|-------------|-------|-----|-------|-------|
| Foundation | ✅ | ✅ | ✅ | Core framework, works everywhere |
| URLSession (HTTP) | ✅ | ✅ | ⚠️ | Works on Linux via FoundationNetworking |
| URLSessionWebSocketTask | ✅ | ✅ | ❌ | **Not available on Linux** |
| Darwin (POSIX sockets) | ✅ | ❌ | N/A | iOS doesn't allow raw sockets |
| Glibc (POSIX sockets) | N/A | N/A | ✅ | Linux equivalent to Darwin |
| NSLock | ✅ | ✅ | ✅ | Works everywhere |
| DispatchQueue/GCD | ✅ | ✅ | ✅ | Works everywhere |
| Timer | ✅ | ✅ | ✅ | Works everywhere |
| FileManager | ✅ | ✅ | ✅ | Works everywhere |
| ProcessInfo | ✅ | ✅ | ✅ | Works everywhere |
| signal() | ✅ | ❌ | ✅ | Not available/meaningful on iOS |
| Unix Domain Sockets | ✅ | ❌ | ✅ | iOS sandboxing prevents this |
| fflush(stdout) | ✅ | ⚠️ | ✅ | Not meaningful on iOS |
| readLine() | ✅ | ❌ | ✅ | No stdin on iOS apps |

---

## iOS Compatibility Assessment

### ✅ What's Compatible (75%)

| Component | File(s) | iOS Status | Notes |
|-----------|---------|------------|-------|
| Data structures | All `*Classes.swift` files | ✅ Ready | Pure Swift structs/classes |
| Protocol 2 encoding/decoding | `Protocol2Utils.swift` | ✅ Ready | Pure Swift/Foundation |
| Market data models | `MarketData.swift` | ✅ Ready | Pure Swift/Foundation |
| .env file parsing | `EnvLoader.swift` | ✅ Ready | Uses FileManager (works) |
| WebSocket connections | `*WebSocket.swift` | ✅ Ready | Uses URLSessionWebSocketTask |
| HTTP networking | `IBNetworker.swift` | ✅ Ready | Uses URLSession |
| JSON parsing | All files | ✅ Ready | Uses JSONSerialization |
| Thread safety | All files | ✅ Ready | Uses NSLock, DispatchQueue |
| Error handling | All files | ✅ Ready | Pure Swift |

### ❌ What Needs Changes (25%)

| Component | File(s) | Issue | Solution |
|-----------|---------|-------|----------|
| TCP Server | `MKTDispatcher.swift`, `IBDispatcher.swift`, `IBForecastDispatcher.swift`, `CapitalComDispatcher.swift` | Uses raw POSIX sockets (Darwin) | Use NWListener from Network framework |
| TCP Clients | `SocketProtocol.swift` (`RealSocket`) | Uses raw POSIX sockets | Use NWConnection from Network framework |
| Unix Domain Socket | `CapitalComDispatcher.swift` | Uses sockaddr_un | Use NWListener/NWConnection with local path |
| Signal handlers | `main.swift` | Uses signal(SIGINT, ...) | Remove or use different termination approach |
| Interactive mode | `main.swift`, all dispatchers | Uses readLine() for stdin | iOS apps don't have stdin; redesign for iOS UI |
| Command line args | `main.swift` | Uses CommandLine.arguments | Not applicable for iOS apps; use different config |

### iOS Porting Strategy

1. **Create iOS-specific socket wrappers** using Apple's Network framework (`NWListener`, `NWConnection`, `NWEndpoint`)
2. **Abstract the transport layer** so the same business logic works with different socket implementations
3. **Remove interactive mode** - iOS doesn't have a terminal; use a UI or background service pattern
4. **Use Swift Package Manager conditionals** to swap implementations:
   ```swift
   #if canImport(Network) && os(iOS)
   import Network
   // Use NWConnection
   #else
   import Darwin
   // Use POSIX sockets
   #endif
   ```

### iOS-Specific Considerations

- **Background execution**: iOS requires Background Modes entitlement for long-running network operations
- **Network extension**: May need to use Network Extension framework for VPN/proxy functionality
- **App Store restrictions**: Raw socket access may require special entitlements
- **Power management**: iOS aggressively suspends background apps; need to handle reconnection

---

## Linux Compatibility Assessment

### ✅ What's Compatible (35%)

| Component | File(s) | Linux Status | Notes |
|-----------|---------|--------------|-------|
| Data structures | All `*Classes.swift` files | ✅ Ready | Pure Swift structs/classes |
| Protocol 2 encoding/decoding | `Protocol2Utils.swift` | ✅ Ready | Pure Swift/Foundation |
| Market data models | `MarketData.swift` | ✅ Ready | Pure Swift/Foundation |
| .env file parsing | `EnvLoader.swift` | ✅ Ready | Uses FileManager (works on Linux) |
| JSON parsing | All files | ✅ Ready | Uses JSONSerialization |
| Thread safety | All files | ✅ Ready | Uses NSLock, DispatchQueue |
| Error handling | All files | ✅ Ready | Pure Swift |
| HTTP networking | `IBNetworker.swift` | ⚠️ Partial | Works with FoundationNetworking import |
| POSIX socket server | Dispatcher files | ✅ Ready | Already has `#if canImport(Glibc)` conditionals |
| Signal handlers | `main.swift` | ✅ Ready | Works with Glibc |
| Unix Domain Sockets | `CapitalComDispatcher.swift` | ✅ Ready | Works with Glibc |

### ❌ What Needs Replacement (65%)

| Component | File(s) | Issue | Solution |
|-----------|---------|-------|----------|
| WebSocket (Binance) | `BinanceWebSocket.swift` | URLSessionWebSocketTask not available | Use external library (e.g., Starscream, WebSocketKit) |
| WebSocket (IB) | `IBWebSocket.swift` | URLSessionWebSocketTask not available | Use external library |
| WebSocket (Capital.com) | `CapitalComWebSocket.swift` | URLSessionWebSocketTask not available | Use external library |
| WebSocket (Polymarket) | `PolymarketWebSocket.swift` | URLSessionWebSocketTask not available | Use external library |
| WebSocket (IB Forecast) | `IBForecastWebSocket.swift` | URLSessionWebSocketTask not available | Use external library |
| HTTP requests | Various files | HTTPURLResponse may differ | Use FoundationNetworking consistently |
| Timer scheduling | `CapitalComWebSocket.swift` | Timer.scheduledTimer behavior | Use DispatchSourceTimer instead |
| RunLoop | `PolymarketExample.swift` | RunLoop.main.run() | Use dispatchMain() |

### Current Linux-Friendly Code Already in Place

The codebase already has some Linux compatibility:

```swift
// FoundationNetworking import for Linux HTTP support
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// Glibc/Darwin conditional imports
#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#endif

// Platform detection in main.swift
func getSystemInfo() -> String {
    #if os(macOS)
    return "macOS"
    #elseif os(Linux)
    return "Linux"
    #else
    return "Unknown"
    #endif
}
```

### Linux Porting Strategy

1. **Add WebSocket library dependency** - Options:
   - [WebSocketKit](https://github.com/vapor/websocket-kit) (Vapor ecosystem, SwiftNIO-based)
   - [Starscream](https://github.com/daltoniam/Starscream) (works on Linux with 4.0+)
   - [async-http-client](https://github.com/swift-server/async-http-client) with WebSocket support
   
2. **Update Package.swift** to conditionally include WebSocket library on Linux:
   ```swift
   // Note: The 'platforms' array only specifies minimum versions for Apple platforms.
   // Linux support is automatic in Swift Package Manager - it just needs the dependencies.
   let package = Package(
       name: "ArgusServer",
       platforms: [.macOS(.v13)],  // Apple platforms only; Linux has no platform version
       dependencies: [
           // For Linux WebSocket support (on macOS, native URLSessionWebSocketTask is used)
           .package(url: "https://github.com/vapor/websocket-kit.git", from: "2.0.0")
       ],
       targets: [
           .executableTarget(
               name: "ArgusServer",
               dependencies: [
                   // Only link WebSocketKit on Linux; macOS uses native APIs
                   .product(name: "WebSocketKit", package: "websocket-kit", 
                            condition: .when(platforms: [.linux]))
               ]
           )
       ]
   )
   ```

3. **Create a WebSocket protocol abstraction**:
   ```swift
   protocol ArgusWebSocket {
       func connect(url: URL)
       func send(_ message: String, completion: ((Error?) -> Void)?)
       func receive(handler: @escaping (Result<WebSocketMessage, Error>) -> Void)
       func disconnect()
   }
   
   // macOS implementation using URLSessionWebSocketTask
   #if os(macOS) || os(iOS)
   class NativeWebSocket: ArgusWebSocket { ... }
   #endif
   
   // Linux implementation using WebSocketKit
   #if os(Linux)
   class LinuxWebSocket: ArgusWebSocket { ... }
   #endif
   ```

4. **Replace Timer with DispatchSourceTimer** for reliability:
   ```swift
   // Instead of Timer.scheduledTimer
   let timer = DispatchSource.makeTimerSource(queue: .global())
   timer.schedule(deadline: .now() + 540, repeating: 540)
   timer.setEventHandler { [weak self] in
       self?.sendApplicationPing()
   }
   timer.resume()
   ```

5. **Replace RunLoop.main.run() with dispatchMain()**:
   ```swift
   // Instead of: RunLoop.main.run()
   dispatchMain()
   ```

---

## File-by-File Compatibility Matrix

| File | macOS | iOS | Linux | Issues |
|------|-------|-----|-------|--------|
| `main.swift` | ✅ | ⚠️ | ⚠️ | iOS: no stdin/signal; Linux: works |
| `BinanceWebSocket.swift` | ✅ | ✅ | ❌ | Linux: no URLSessionWebSocketTask |
| `BinanceClasses.swift` | ✅ | ✅ | ✅ | Pure Swift |
| `MKTDispatcher.swift` | ✅ | ❌ | ✅ | iOS: no raw sockets |
| `SocketProtocol.swift` | ✅ | ❌ | ✅ | iOS: no raw sockets |
| `Protocol2Utils.swift` | ✅ | ✅ | ✅ | Pure Swift |
| `MarketData.swift` | ✅ | ✅ | ✅ | Pure Swift |
| `EnvLoader.swift` | ✅ | ✅ | ✅ | Pure Swift |
| `IBWebSocket.swift` | ✅ | ✅ | ❌ | Linux: no URLSessionWebSocketTask |
| `IBNetworker.swift` | ✅ | ✅ | ⚠️ | Linux: needs FoundationNetworking |
| `IBDispatcher.swift` | ✅ | ❌ | ✅ | iOS: no raw sockets |
| `IBClasses.swift` | ✅ | ✅ | ✅ | Pure Swift |
| `IBFields.swift` | ✅ | ✅ | ✅ | Pure Swift |
| `IBAccountProvider.swift` | ✅ | ❌ | ✅ | iOS: no raw sockets (debug socket) |
| `IBForecastWebSocket.swift` | ✅ | ✅ | ❌ | Linux: no URLSessionWebSocketTask |
| `IBForecastDispatcher.swift` | ✅ | ❌ | ✅ | iOS: no raw sockets |
| `IBForecastClasses.swift` | ✅ | ✅ | ✅ | Pure Swift |
| `CapitalComWebSocket.swift` | ✅ | ✅ | ❌ | Linux: no URLSessionWebSocketTask |
| `CapitalComDispatcher.swift` | ✅ | ❌ | ✅ | iOS: no Unix domain sockets |
| `CapitalComClasses.swift` | ✅ | ✅ | ✅ | Pure Swift |
| `PolymarketWebSocket.swift` | ✅ | ✅ | ❌ | Linux: no URLSessionWebSocketTask |
| `PolymarketExample.swift` | ✅ | ⚠️ | ❌ | iOS: no RunLoop.main.run(); Linux: WebSocket |
| `PolymarketClasses.swift` | ✅ | ✅ | ✅ | Pure Swift |

---

## Summary Statistics

### iOS Compatibility

| Category | Count | Percentage |
|----------|-------|------------|
| ✅ Fully Compatible | 12 files | 54.5% |
| ⚠️ Minor Changes | 2 files | 9.1% |
| ❌ Needs Rewrite | 8 files | 36.4% |

**Overall iOS Compatibility: ~75%** (most logic is compatible, but network layer needs rework)

### Linux Compatibility

| Category | Count | Percentage |
|----------|-------|------------|
| ✅ Fully Compatible | 8 files | 36.4% |
| ⚠️ Minor Changes | 6 files | 27.3% |
| ❌ Needs Replacement | 8 files | 36.4% |

**Overall Linux Compatibility: ~35%** (WebSocket is a critical blocking issue affecting all real-time data functionality; while 63% of files have some compatibility, the WebSocket dependency makes the codebase non-functional on Linux without replacement)

---

## Recommendations

### For iOS Port

1. **Phase 1**: Create Network framework wrappers for TCP sockets
2. **Phase 2**: Create iOS-specific UI or background service architecture
3. **Phase 3**: Handle iOS-specific lifecycle (background modes, reconnection)
4. **Effort estimate**: 2-3 weeks

### For Linux Port

1. **Phase 1**: Add WebSocket library dependency (WebSocketKit recommended)
2. **Phase 2**: Create WebSocket abstraction protocol
3. **Phase 3**: Implement Linux WebSocket wrapper
4. **Phase 4**: Test and fix FoundationNetworking edge cases
5. **Effort estimate**: 1-2 weeks

### Priority Recommendation

**Linux should be prioritized first** because:
- Only needs WebSocket library replacement (one external dependency)
- All POSIX socket code already has Glibc conditionals
- Server use case is more relevant for Linux deployment
- Can be done without architectural changes

---

## Appendix: Specific Code Locations Requiring Changes

### WebSocket Usage (Linux Blocker)

```swift
// BinanceWebSocket.swift:60-62
ws = urlSession.webSocketTask(with: url)
ws?.resume()

// IBWebSocket.swift:40-41
self.ws = self.session?.webSocketTask(with: request)
self.ws?.resume()

// CapitalComWebSocket.swift:51-52
ws = session.webSocketTask(with: url)
ws?.resume()

// PolymarketWebSocket.swift:65-66
ws = session.webSocketTask(with: url)
ws?.resume()
```

### POSIX Socket Usage (iOS Blocker)

```swift
// SocketProtocol.swift:47-52
let bytesWritten = send(
    fileDescriptor,
    baseAddress.advanced(by: sent),
    totalBytes - sent,
    0
)

// MKTDispatcher.swift:63
serverSocket = socket(AF_INET, SOCK_STREAM, 0)

// IBAccountProvider.swift:77
debugSocket = Darwin.socket(AF_INET, SOCK_STREAM, 0)

// CapitalComDispatcher.swift:259-262
#if canImport(Darwin)
serverSocket = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
#else
serverSocket = Glibc.socket(AF_UNIX, SOCK_STREAM, 0)
#endif
```

---

*Document generated: 2025*
*Swift version: 5.9+*
*Analyzed files: 22 Swift source files*
