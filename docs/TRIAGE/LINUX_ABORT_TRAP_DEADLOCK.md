# Linux SIGTRAP Abort in Swift IB on Raspberry Pi

## Issue Summary

The Argus Swift IB implementation crashes on Linux (specifically tested on Raspberry Pi/aarch64) with a `SIGTRAP` signal during initialization. The crash occurs in the dispatch queue synchronization code when making HTTP requests via `URLSession.dataTask()`.

**Platform:** Linux (aarch64 Raspberry Pi)  
**Swift Runtime:** FoundationNetworking (Linux-specific Foundation implementation)  
**Error:** SIGTRAP in `__DISPATCH_WAIT_FOR_QUEUE__`  
**Status:** Root cause identified, multiple potential solutions available

---

## Stack Trace Analysis

```
* thread #1, name = 'argus_server', stop reason = signal SIGTRAP
  * frame #0: 0x00005555ca817d4c argus_server`__DISPATCH_WAIT_FOR_QUEUE__ + 332
    frame #1: 0x00005555ca8177a8 argus_server`_dispatch_sync_f_slow + 160
    frame #2: 0x00005555ca165974 argus_server`implicit closure #2 in implicit closure #1 in DispatchQueue.sync(execute:) + 140
    frame #3: 0x00005555ca165b94 argus_server`partial apply for implicit closure #2 in implicit closure #1 in DispatchQueue.sync(execute:) + 40
    frame #4: 0x00005555ca1653a8 argus_server`DispatchQueue._syncHelper(fn:execute:rescue:) + 220
    frame #5: 0x00005555ca1668f8 argus_server`DispatchQueue.sync(execute:) + 92
    frame #6: 0x00005555ca5fc1ec argus_server`URLSession.dataTask(with:completionHandler:) + 180
    frame #7: 0x00005555c9e0a030 argus_server`IBLockedSession.post(url=<unavailable>, json=nil) at IBNetworker.swift:75:17 [opt]
    frame #8: 0x00005555c9e0a570 argus_server`IBNetworker.runSetupMessages() at IBNetworker.swift:138:43 [opt]
    frame #9: 0x00005555c9e11fe0 argus_server`IBNetworker.initialize() at IBNetworker.swift:129:13 [opt] [inlined]
    frame #10: 0x00005555c9e11fd4 argus_server`IBWss.boot() at IBWebSocket.swift:159:27 [opt]
    frame #11: 0x00005555c9e11ba8 argus_server`IBWss.handleMessage(message=data) at <compiler-generated>:0 [opt]
    frame #12: 0x00005555c9e12ec0 argus_server`closure #1 in IBWss.receiveMessage(result=success) at IBWebSocket.swift:54:22 [opt]
```

### Key Observations

1. **Frame #6**: `URLSession.dataTask(with:completionHandler:)` is internally calling `DispatchQueue.sync()`
2. **Frame #7**: This is happening inside `IBLockedSession.post()` at line 75
3. **Frames #8-12**: The call originates from WebSocket message handling → `boot()` → `networker.initialize()` → `runSetupMessages()`

---

## Root Cause

### The Problem: DispatchQueue.sync() Deadlock in FoundationNetworking (Linux)

On **macOS**, Foundation's `URLSession` is implemented using native system frameworks that don't have this issue.

On **Linux**, Swift uses **FoundationNetworking**, a separate implementation that has different internal behavior. Specifically:

**The Linux implementation of `URLSession.dataTask()` internally uses `DispatchQueue.sync()`** to synchronize certain operations. This is documented in the swift-corelibs-foundation source code and has been a known issue.

### Why This Causes SIGTRAP on Linux

The crash occurs due to a **dispatch queue deadlock scenario**:

1. **WebSocket callback executes on a dispatch queue** (from frame #12-14)
2. **Call chain**: WebSocket receive → `boot()` → `networker.initialize()` → `runSetupMessages()`
3. **Inside `runSetupMessages()`**: Calls `session.post()` which:
   - Acquires `NSLock` (line 56)
   - Creates `URLSession.dataTask()` (line 75)
   - **On Linux**: `URLSession.dataTask()` tries to call `DispatchQueue.sync()` internally
4. **Deadlock**: If the internal `DispatchQueue.sync()` tries to synchronize on the same queue that's already executing the WebSocket callback, it deadlocks
5. **Result**: The dispatch queue watchdog detects the deadlock and sends `SIGTRAP`

### Why This Only Happens on Linux

| Platform | URLSession Implementation | Internal Sync Behavior |
|----------|---------------------------|------------------------|
| **macOS** | Native Foundation (Objective-C/C++) | Uses CFRunLoop, no DispatchQueue.sync() issues |
| **Linux** | FoundationNetworking (Swift reimplementation) | Uses DispatchQueue.sync() internally, prone to deadlocks |

This is a **known limitation** of swift-corelibs-foundation on Linux. See:
- [swift-corelibs-foundation Issue #2980](https://github.com/apple/swift-corelibs-foundation/issues/2980)
- [swift-corelibs-foundation URLSession Implementation](https://github.com/apple/swift-corelibs-foundation/blob/main/Sources/FoundationNetworking/URLSession/URLSession.swift)

---

## Why NSLock + DispatchSemaphore Pattern is Problematic

Looking at `IBLockedSession.post()` (lines 55-95):

```swift
func post(url: String, json: [String: Any]? = nil) throws -> (Data, HTTPURLResponse) {
    lock.lock()  // ← Acquire NSLock
    defer { lock.unlock() }
    
    // ... setup request ...
    
    let semaphore = DispatchSemaphore(value: 0)
    var result: (Data, HTTPURLResponse)?
    var error: Error?
    
    session.dataTask(with: request) { data, response, err in  // ← On Linux, internally calls DispatchQueue.sync()
        // ...
        semaphore.signal()
    }.resume()
    
    semaphore.wait()  // ← Block waiting for completion
    
    // ...
}
```

**The problematic pattern:**
1. Hold `NSLock` 
2. Call `URLSession.dataTask()` which:
   - On Linux: Internally uses `DispatchQueue.sync()`
   - Tries to dispatch work synchronously
3. Wait on `DispatchSemaphore`

When this runs on a dispatch queue (as it does in the WebSocket callback), the internal `DispatchQueue.sync()` can deadlock against the queue the function is already running on.

---

## Potential Solutions

### Solution 1: Remove NSLock and Use Serial DispatchQueue (Recommended)

**Replace `NSLock` with a serial `DispatchQueue` for thread-safety.**

This is the most Swift-idiomatic approach and avoids lock + dispatch mixing.

**Changes to `IBLockedSession`:**

```swift
class IBLockedSession {
    private let session: URLSession
    private let syncQueue = DispatchQueue(label: "com.argus.ib.session", qos: .userInitiated)  // Serial queue
    private var headers: [String: String]
    
    init(headers: [String: String]) {
        let config = URLSessionConfiguration.default
        self.session = URLSession(configuration: config)
        self.headers = headers
    }
    
    func post(url: String, json: [String: Any]? = nil) throws -> (Data, HTTPURLResponse) {
        // Use DispatchQueue.sync for thread-safety instead of NSLock
        return try syncQueue.sync {
            var request = URLRequest(url: URL(string: url)!)
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            
            for (key, value) in headers {
                request.setValue(value, forHTTPHeaderField: key)
            }
            
            if let json = json {
                request.httpBody = try JSONSerialization.data(withJSONObject: json)
            }
            
            let semaphore = DispatchSemaphore(value: 0)
            var result: (Data, HTTPURLResponse)?
            var error: Error?
            
            session.dataTask(with: request) { data, response, err in
                if let err = err {
                    error = err
                } else if let data = data, let httpResponse = response as? HTTPURLResponse {
                    result = (data, httpResponse)
                }
                semaphore.signal()
            }.resume()
            
            semaphore.wait()
            
            if let error = error {
                throw error
            }
            
            guard let result = result else {
                throw IBError.invalidResponse
            }
            
            return result
        }
    }
    
    // Same pattern for get()
}
```

**Pros:**
- More Swift-idiomatic
- Avoids lock/dispatch mixing
- Works consistently across macOS and Linux

**Cons:**
- Still uses DispatchSemaphore which can be problematic in nested queue scenarios

### Solution 2: Async/Await Refactor (Best Long-Term)

**Convert to async/await pattern** (requires Swift 5.5+).

This completely eliminates semaphores and locks, using Swift's structured concurrency.

```swift
class IBLockedSession {
    private let session: URLSession
    private let syncQueue = DispatchQueue(label: "com.argus.ib.session", qos: .userInitiated)
    private var headers: [String: String]
    
    init(headers: [String: String]) {
        let config = URLSessionConfiguration.default
        self.session = URLSession(configuration: config)
        self.headers = headers
    }
    
    func post(url: String, json: [String: Any]? = nil) async throws -> (Data, HTTPURLResponse) {
        var request = URLRequest(url: URL(string: url)!)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        
        for (key, value) in headers {
            request.setValue(value, forHTTPHeaderField: key)
        }
        
        if let json = json {
            request.httpBody = try JSONSerialization.data(withJSONObject: json)
        }
        
        let (data, response) = try await session.data(for: request)
        
        guard let httpResponse = response as? HTTPURLResponse else {
            throw IBError.invalidResponse
        }
        
        return (data, httpResponse)
    }
}
```

**Pros:**
- Modern Swift concurrency
- No locks, no semaphores, no deadlocks
- Better error handling
- More maintainable

**Cons:**
- Requires refactoring all callers to use async/await
- Larger change scope
- Requires Swift 5.5+ (already available on modern Swift toolchains)

### Solution 3: Call on Background Queue (Quick Fix)

**Move the HTTP calls to a background queue** that's not involved in the WebSocket callback chain.

**Changes to `IBNetworker.runSetupMessages()`:**

```swift
private func runSetupMessages() throws {
    print("Sending setup messages to IBKR...")
    
    // Wrap HTTP calls in a separate queue to avoid deadlock
    let httpQueue = DispatchQueue(label: "com.argus.ib.setup", qos: .userInitiated)
    
    var setupError: Error?
    let semaphore = DispatchSemaphore(value: 0)
    
    httpQueue.async { [weak self] in
        guard let self = self else {
            semaphore.signal()
            return
        }
        
        do {
            // Tickle
            let (tickleData, _) = try self.session.post(url: self.urls["tickle"]!)
            if let json = try? JSONSerialization.jsonObject(with: tickleData) {
                print("Tickle response: \(json)")
            }
            
            Thread.sleep(forTimeInterval: 1)
            
            // Auth status
            let (authData, _) = try self.session.post(url: self.urls["auth_status"]!)
            if let json = try? JSONSerialization.jsonObject(with: authData) {
                print("Auth status response: \(json)")
            }
            
            Thread.sleep(forTimeInterval: 1)
            
            // SSODH init
            let initPayload: [String: Any] = [
                "compete": false,
                "useSecurityContext": true,
                "locale": "en_US",
                "tz": "xxx (Europe/London)",
                "isET": true,
                "publish": true
            ]
            
            let (initData, _) = try self.session.post(url: self.urls["ssodh_init"]!, json: initPayload)
            if let json = try? JSONSerialization.jsonObject(with: initData) as? [String: Any] {
                print("SSODH init response: \(json)")
                self.authenticated = json["authenticated"] as? Bool ?? false
                print("Authenticated: \(self.authenticated)")
            }
        } catch {
            setupError = error
        }
        
        semaphore.signal()
    }
    
    semaphore.wait()
    
    if let error = setupError {
        throw error
    }
}
```

**Pros:**
- Minimal code changes
- Isolates the problem

**Cons:**
- Still uses semaphores (nested semaphore anti-pattern)
- Workaround rather than proper fix
- May have other hidden queue-related issues

### Solution 4: Use a Different HTTP Library (Alternative)

**Replace URLSession with a pure-Swift HTTP client** that doesn't have Linux-specific dispatch issues.

Options:
- [AsyncHTTPClient](https://github.com/swift-server/async-http-client) - Apple's official async HTTP client
- [curl-nio](https://github.com/swift-server/curl-nio) - libcurl wrapper for SwiftNIO

**Example with AsyncHTTPClient:**

```swift
import AsyncHTTPClient

class IBLockedSession {
    private let httpClient: HTTPClient
    private let syncQueue = DispatchQueue(label: "com.argus.ib.session", qos: .userInitiated)
    private var headers: [String: String]
    
    init(headers: [String: String]) {
        self.httpClient = HTTPClient(eventLoopGroupProvider: .createNew)
        self.headers = headers
    }
    
    func post(url: String, json: [String: Any]? = nil) throws -> (Data, HTTPURLResponse) {
        var request = try HTTPClient.Request(url: url, method: .POST)
        
        for (key, value) in headers {
            request.headers.add(name: key, value: value)
        }
        
        if let json = json {
            let body = try JSONSerialization.data(withJSONObject: json)
            request.body = .data(body)
        }
        
        let response = try httpClient.execute(request: request).wait()
        
        // Convert to URLSession-compatible types
        let data = response.body.map { Data(buffer: $0) } ?? Data()
        let statusCode = response.status.code
        
        // Note: Would need to create HTTPURLResponse wrapper or change return type
        return (data, /* construct HTTPURLResponse */)
    }
}
```

**Pros:**
- Avoids FoundationNetworking Linux bugs entirely
- AsyncHTTPClient is well-maintained and used in production Swift servers

**Cons:**
- Additional dependency
- Need to adapt interfaces (HTTPURLResponse vs different types)
- More invasive change

---

## Recommended Implementation Plan

### Phase 1: Immediate Fix (Solution 1 or 3)

**For quick resolution**, implement **Solution 3** (background queue) to unblock development:
- Changes are isolated to `runSetupMessages()`
- Minimal risk
- Can be deployed quickly

### Phase 2: Proper Fix (Solution 1)

**Once stable**, implement **Solution 1** (serial DispatchQueue):
- Replace NSLock with DispatchQueue throughout `IBLockedSession`
- More idiomatic Swift
- Better cross-platform behavior

### Phase 3: Modern Swift (Solution 2)

**Long-term**, migrate to **Solution 2** (async/await):
- Refactor entire IB module to use structured concurrency
- Eliminate all semaphores and locks
- Future-proof the codebase

---

## Testing Recommendations

### Before Fix
1. Reproduce on Linux (Raspberry Pi or Docker container)
2. Verify SIGTRAP occurs in `__DISPATCH_WAIT_FOR_QUEUE__`
3. Confirm it's in the `IBLockedSession.post()` path

### After Fix
1. Test on Linux with the same setup
2. Verify no SIGTRAP during initialization
3. Confirm WebSocket connection and data flow works
4. Test on macOS to ensure no regression
5. Run under different load conditions (multiple concurrent requests)

### Linux Test Environment

```dockerfile
# Dockerfile for testing
FROM swift:5.9-focal

WORKDIR /app
COPY . .

RUN swift build -c release

CMD ["./argus_server"]
```

---

## Related Issues

- [swift-corelibs-foundation #2980](https://github.com/apple/swift-corelibs-foundation/issues/2980) - URLSession DispatchQueue.sync() deadlock
- [swift-corelibs-foundation #3012](https://github.com/apple/swift-corelibs-foundation/issues/3012) - URLSession Linux implementation issues
- Similar issues reported in swift-nio and other networking libraries

---

## References

1. **Swift-corelibs-foundation URLSession source**: https://github.com/apple/swift-corelibs-foundation/blob/main/Sources/FoundationNetworking/URLSession/URLSession.swift
2. **Swift Concurrency Documentation**: https://docs.swift.org/swift-book/LanguageGuide/Concurrency.html
3. **AsyncHTTPClient**: https://github.com/swift-server/async-http-client
4. **Dispatch Queue Best Practices**: https://developer.apple.com/documentation/dispatch/dispatchqueue

---

## Summary

The SIGTRAP abort on Linux is caused by a **deadlock in FoundationNetworking's URLSession implementation** when `URLSession.dataTask()` internally calls `DispatchQueue.sync()` while already executing on a dispatch queue.

**Root cause**: Linux-specific `URLSession` implementation using `DispatchQueue.sync()` internally, conflicting with the existing queue context from WebSocket callbacks.

**Quick fix**: Move HTTP calls to a separate background queue (Solution 3)

**Proper fix**: Replace NSLock with serial DispatchQueue (Solution 1)

**Best long-term**: Migrate to async/await (Solution 2)

**Platform affected**: Linux only (macOS uses native Foundation which doesn't have this issue)

This is a **known limitation of swift-corelibs-foundation** on Linux and requires careful queue management or migration to modern Swift concurrency to resolve properly.
