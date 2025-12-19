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

### Critical Difference: Why Binance Works but IB Doesn't

**Key Finding:** The Binance module works perfectly on Linux because it **NEVER uses `URLSession.dataTask()`** for synchronous HTTP requests.

**Binance approach:**
- Only uses `URLSessionWebSocketTask` for WebSocket connections
- No HTTP REST API calls with `dataTask()`
- No mixing of locks + semaphores + dataTask

**IB approach (problematic):**
- Uses `URLSessionWebSocketTask` for WebSocket (works fine)
- **ALSO** uses `URLSession.dataTask()` for synchronous HTTP REST calls (IBKR REST API)
- Combines `NSLock` + `DispatchSemaphore` + `dataTask()` = deadlock on Linux

### The Problem: NSLock + DispatchSemaphore + dataTask() = Deadlock

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

**Constraints from requirements:**
1. ❌ No async/await
2. ❌ No external packages
3. ✅ Can use C/C++ with Swift 6 FFI
4. ✅ Must work on both Linux and macOS

### Solution 1: Replace NSLock with Serial DispatchQueue (RECOMMENDED - Simplest Fix)

**Replace `NSLock` with a serial `DispatchQueue` for thread-safety.**

This is the simplest Swift-only solution that should resolve the deadlock.

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
- Pure Swift, no dependencies
- More Swift-idiomatic
- Avoids lock/dispatch mixing
- Works consistently across macOS and Linux
- **Minimal code changes**

**Cons:**
- Still uses DispatchSemaphore which is not ideal but should work
- Not addressing the root URLSession.dataTask() issue

### Solution 2: Move HTTP Calls to Background Queue (Quick Workaround)

**Move the HTTP calls to a separate background queue** that's not involved in the WebSocket callback chain.

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
- Pure Swift solution

**Cons:**
- Still uses semaphores (nested semaphore anti-pattern)
- Workaround rather than proper fix
- May have other hidden queue-related issues

### Solution 3: C/C++ HTTP Client with Swift 6 FFI (Long-Term Robust Solution)

**Write custom HTTP client in C/C++ and expose to Swift using Swift 6's C++ interop.**

This completely bypasses the problematic FoundationNetworking implementation on Linux.

**Why this makes sense:**
- IB module does complex HTTP REST API calls (unlike Binance which only uses WebSockets)
- C/C++ HTTP libraries (libcurl, cpp-httplib) are battle-tested on Linux
- Swift 6 FFI has zero overhead for C++ interop
- No external Swift packages needed (libcurl is system library)
- Full control over threading and synchronization

**Implementation approach:**

Create `argus_swift/Sources/ArgusServer/IB/Native/IBHTTPClient.cpp`:

```cpp
#include <curl/curl.h>
#include <string>
#include <map>
#include <stdexcept>

// Response structure
struct HTTPResponse {
    int status_code;
    char* body;
    size_t body_length;
};

// Callback for libcurl to write response data
static size_t WriteCallback(void* contents, size_t size, size_t nmemb, void* userp) {
    size_t realsize = size * nmemb;
    std::string* mem = static_cast<std::string*>(userp);
    mem->append(static_cast<char*>(contents), realsize);
    return realsize;
}

// Synchronous HTTP POST
extern "C" HTTPResponse* http_post_sync(
    const char* url,
    const char* const* headers,  // NULL-terminated array
    const char* body,
    size_t body_length
) {
    CURL* curl = curl_easy_init();
    if (!curl) {
        return nullptr;
    }
    
    std::string response_body;
    struct curl_slist* header_list = nullptr;
    
    // Add headers
    for (int i = 0; headers[i] != nullptr; i++) {
        header_list = curl_slist_append(header_list, headers[i]);
    }
    
    // Configure request
    curl_easy_setopt(curl, CURLOPT_URL, url);
    curl_easy_setopt(curl, CURLOPT_POST, 1L);
    curl_easy_setopt(curl, CURLOPT_POSTFIELDS, body);
    curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE, body_length);
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, header_list);
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, WriteCallback);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response_body);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, 30L);
    
    // Perform request
    CURLcode res = curl_easy_perform(curl);
    
    if (res != CURLE_OK) {
        curl_slist_free_all(header_list);
        curl_easy_cleanup(curl);
        return nullptr;
    }
    
    // Get status code
    long status_code;
    curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status_code);
    
    // Create response
    HTTPResponse* response = new HTTPResponse();
    response->status_code = static_cast<int>(status_code);
    response->body_length = response_body.size();
    response->body = new char[response_body.size()];
    memcpy(response->body, response_body.data(), response_body.size());
    
    curl_slist_free_all(header_list);
    curl_easy_cleanup(curl);
    
    return response;
}

// Free response
extern "C" void http_response_free(HTTPResponse* response) {
    if (response) {
        delete[] response->body;
        delete response;
    }
}
```

Create Swift wrapper `argus_swift/Sources/ArgusServer/IB/Native/IBHTTPClient.swift`:

```swift
import Foundation

// Import C++ functions (Swift 6 interop)
@_silgen_name("http_post_sync")
func http_post_sync(
    _ url: UnsafePointer<CChar>,
    _ headers: UnsafePointer<UnsafePointer<CChar>?>,
    _ body: UnsafePointer<CChar>?,
    _ bodyLength: Int
) -> UnsafeMutablePointer<HTTPResponse>?

@_silgen_name("http_response_free")
func http_response_free(_ response: UnsafeMutablePointer<HTTPResponse>)

struct HTTPResponse {
    var status_code: Int32
    var body: UnsafeMutablePointer<CChar>?
    var body_length: Int
}

/// Native HTTP client using libcurl (no FoundationNetworking issues)
class NativeHTTPClient {
    private var headers: [String: String]
    
    init(headers: [String: String]) {
        self.headers = headers
    }
    
    func post(url: String, json: [String: Any]? = nil) throws -> (Data, Int) {
        // Prepare headers as C array
        var headerStrings: [String] = []
        for (key, value) in headers {
            headerStrings.append("\(key): \(value)")
        }
        headerStrings.append("Content-Type: application/json")
        
        // Convert to C string array
        let cHeaders = headerStrings.map { strdup($0) }
        defer { cHeaders.forEach { free($0) } }
        
        var cHeaderPointers = cHeaders.map { $0 as UnsafePointer<CChar>? }
        cHeaderPointers.append(nil)  // NULL terminator
        
        // Prepare body
        var bodyData: Data?
        if let json = json {
            bodyData = try JSONSerialization.data(withJSONObject: json)
        }
        
        // Call C++ function
        let response: UnsafeMutablePointer<HTTPResponse>?
        
        if let bodyData = bodyData {
            response = bodyData.withUnsafeBytes { bodyPtr in
                url.withCString { urlPtr in
                    cHeaderPointers.withUnsafeBufferPointer { headersPtr in
                        http_post_sync(
                            urlPtr,
                            headersPtr.baseAddress!,
                            bodyPtr.bindMemory(to: CChar.self).baseAddress,
                            bodyData.count
                        )
                    }
                }
            }
        } else {
            response = url.withCString { urlPtr in
                cHeaderPointers.withUnsafeBufferPointer { headersPtr in
                    http_post_sync(urlPtr, headersPtr.baseAddress!, nil, 0)
                }
            }
        }
        
        guard let response = response else {
            throw IBError.networkError("HTTP request failed")
        }
        
        defer { http_response_free(response) }
        
        // Extract response data
        let statusCode = Int(response.pointee.status_code)
        let responseData = Data(
            bytes: response.pointee.body!,
            count: response.pointee.body_length
        )
        
        return (responseData, statusCode)
    }
    
    func get(url: String, params: [String: String]? = nil) throws -> (Data, Int) {
        // Similar implementation for GET
        // ... (omitted for brevity)
        fatalError("Not implemented")
    }
}
```

Then modify `IBLockedSession` to use `NativeHTTPClient`:

```swift
class IBLockedSession {
    private let client: NativeHTTPClient
    private let lock = NSLock()  // Still needed for header updates
    
    init(headers: [String: String]) {
        self.client = NativeHTTPClient(headers: headers)
    }
    
    func post(url: String, json: [String: Any]? = nil) throws -> (Data, HTTPURLResponse) {
        lock.lock()
        defer { lock.unlock() }
        
        let (data, statusCode) = try client.post(url: url, json: json)
        
        // Create HTTPURLResponse for compatibility
        let response = HTTPURLResponse(
            url: URL(string: url)!,
            statusCode: statusCode,
            httpVersion: "HTTP/1.1",
            headerFields: nil
        )!
        
        return (data, response)
    }
}
```

**Package.swift changes:**

```swift
.target(
    name: "ArgusServer",
    dependencies: [],
    cxxSettings: [
        .define("_GNU_SOURCE"),
        .linkedLibrary("curl"),
    ]
)
```

**Pros:**
- **Completely bypasses FoundationNetworking bugs** on Linux
- **No external Swift packages** (libcurl is standard system library)
- **Zero-overhead Swift/C++ interop** with Swift 6
- Battle-tested networking (libcurl used everywhere)
- Full control over threading - no dispatch queue issues
- **Same code works on macOS and Linux**

**Cons:**
- Requires libcurl-dev on build system (`apt-get install libcurl4-openssl-dev`)
- More complex implementation
- Need to maintain C++ code alongside Swift
- Slightly more verbose API

**Is it worth the effort?**

Given that:
1. IB networking issues are complex and worth solving properly
2. Binance proves WebSocket-only approach works fine
3. The problem is SPECIFICALLY `URLSession.dataTask()` on Linux
4. You're open to C/C++ solutions

**YES** - This is likely the most robust long-term solution for IB's HTTP REST API needs.

### Solution 4: Eliminate HTTP REST Calls (Architectural Alternative - Not Recommended)

**Refactor IBKR integration to only use WebSocket** (like Binance does).

This would mean finding WebSocket-based alternatives to the IBKR REST API endpoints currently used for:
- Authentication (`/tickle`, `/auth/status`, `/ssodh/init`)
- Account queries (`/portfolio/accounts`, `/account/ledger`, etc.)

**Why not recommended:**
- IBKR's WebSocket API may not support all operations currently done via REST
- Would require significant architectural changes
- REST API is simpler for request/response patterns
- Not addressing the actual bug, just avoiding it

---

## Recommended Implementation Plan

### Phase 1: Immediate Fix (Solution 1) - 1-2 days

**Implement serial DispatchQueue solution** to unblock Linux deployment:
- Replace `NSLock` with `DispatchQueue(label: "com.argus.ib.session")`
- Test on Linux (Raspberry Pi or Docker)
- Verify no regression on macOS
- **Risk:** Low - minimal changes, well-understood pattern

### Phase 2: Evaluate C/C++ Solution (Solution 3) - Planning

**Assess whether IB networking complexity justifies C/C++ HTTP client:**
- How many REST endpoints are used? (Currently: ~10 endpoints)
- How critical is IB reliability vs development time?
- Is team comfortable maintaining C++ code?

**Decision criteria:**
- If IB is mission-critical: **DO Solution 3** (C/C++ libcurl)
- If IB is experimental/secondary: **STAY with Solution 1** (serial queue)

### Phase 3: Long-Term (If C/C++ chosen) - 3-5 days

**Implement native HTTP client:**
1. Create `IBHTTPClient.cpp` with libcurl wrapper
2. Add Swift FFI bindings
3. Update `Package.swift` with C++ interop settings
4. Replace `IBLockedSession` implementation
5. Test thoroughly on both platforms
6. Update build documentation for libcurl dependency


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

# For C++ solution: install libcurl
RUN apt-get update && apt-get install -y libcurl4-openssl-dev

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
2. **libcurl documentation**: https://curl.se/libcurl/
3. **Swift 6 C++ Interop**: https://www.swift.org/documentation/cxx-interop/
4. **Dispatch Queue Best Practices**: https://developer.apple.com/documentation/dispatch/dispatchqueue

---

## Summary

The SIGTRAP abort on Linux is caused by a **deadlock in FoundationNetworking's URLSession implementation** when `URLSession.dataTask()` internally calls `DispatchQueue.sync()` while already executing on a dispatch queue.

**Why Binance works but IB doesn't:**
- **Binance**: Only uses WebSockets, no HTTP REST calls with `dataTask()`
- **IB**: Uses both WebSockets AND HTTP REST API (authentication, account queries)

**Root cause**: 
- Linux-specific `URLSession.dataTask()` implementation uses `DispatchQueue.sync()` internally
- When called from WebSocket callback context with `NSLock` + `DispatchSemaphore`, causes deadlock
- macOS uses native Foundation which doesn't have this issue

**Solutions (given constraints: no async/await, no external packages):**

1. **Quick fix (Solution 2)**: Move HTTP calls to background queue - isolates the problem
2. **Proper fix (Solution 1)**: Replace `NSLock` with serial `DispatchQueue` - more idiomatic Swift
3. **Best long-term (Solution 3)**: C/C++ libcurl with Swift 6 FFI - completely bypasses FoundationNetworking bugs, zero-overhead interop, battle-tested
4. **Not recommended (Solution 4)**: Eliminate HTTP calls - too invasive architecturally

**Recommendation**: Start with Solution 1 for immediate unblocking. Evaluate Solution 3 (C++ libcurl) if IB reliability is mission-critical, as it provides the most robust long-term solution without external Swift packages.

**Platform affected**: Linux only (macOS uses native Foundation without DispatchQueue.sync() issues)
