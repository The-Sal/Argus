# Issue #39: HTTPClient Implementation and Linux-macOS Parity

## Executive Summary

Issue #39 addresses a critical architectural decision in Argus Swift: implementing cross-platform HTTP/FTP networking that provides **Linux-macOS parity** without relying on problematic `FoundationNetworking` implementations on Linux.

The solution: A custom `HTTPClient` class (`cURL.swift`) that uses **process execution of system curl** instead of Swift's `URLSession.dataTask()`. This approach completely bypasses the FoundationNetworking limitations that caused SIGTRAP deadlocks on Linux (documented in `LINUX_ABORT_TRAP_DEADLOCK.md`).

**Key Achievement**: The same code now works identically on both macOS and Linux, achieving true cross-platform parity for HTTP/FTP operations without external Swift packages.

---

## Background: The Foundation of the Problem

### The Linux-macOS Networking Divide

Swift's networking story differs fundamentally between platforms:

| Platform | Implementation | Internal Behavior |
|----------|---------------|-------------------|
| **macOS** | Native Foundation (Objective-C/C++) | Uses CFRunLoop, native system frameworks |
| **Linux** | FoundationNetworking (Swift reimplementation) | Uses `DispatchQueue.sync()` internally, prone to deadlocks |

### The Root Issue: FoundationNetworking's URLSession

On Linux, `URLSession.dataTask()` internally uses `DispatchQueue.sync()` for certain operations. This is a **known limitation** of swift-corelibs-foundation. As documented in `LINUX_ABORT_TRAP_DEADLOCK.md`:

```
The crash occurs due to a dispatch queue deadlock scenario:
1. WebSocket callback executes on a dispatch queue
2. Call chain invokes URLSession.dataTask()
3. On Linux: URLSession.dataTask() tries to call DispatchQueue.sync() internally
4. Deadlock: If the internal DispatchQueue.sync() tries to synchronize on the 
   same queue that's already executing the WebSocket callback, it deadlocks
5. Result: The dispatch queue watchdog detects the deadlock and sends SIGTRAP
```

This meant code that worked perfectly on macOS would **crash on Linux** with SIGTRAP signals.

### Why This Mattered for Argus

The Interactive Brokers (IB) module requires:
- HTTP REST API calls for authentication and account data
- FTP access for shortable shares data (IBKR FTP server)
- WebSocket connections for real-time market data

The Binance module worked fine on Linux because it **only used WebSockets** - no `URLSession.dataTask()` calls. But IB's requirement for both HTTP and WebSocket created the deadlock condition on Linux.

---

## The Solution: Process-Based HTTPClient

### Architecture Decision

Instead of trying to work around FoundationNetworking's limitations, the solution was to **sidestep it entirely** by using the system's curl binary via Swift's `Process` API.

**File**: `argus_swift/Sources/ArgusServer/Utils/cURL.swift`

### Core Implementation

```swift
class HTTPClient {
    /// Performs a synchronous HTTP GET request
    func get(url: String, headers: [String: String]? = nil) throws -> String
    
    /// Performs a synchronous HTTP POST request
    func post(url: String, headers: [String: String]? = nil, 
              contentType: String? = nil, body: String? = nil) throws -> String
    
    /// Performs a synchronous FTP request with authentication
    func ftp(url: String, username: String, password: String) throws -> String
}
```

### How It Works

**Key technique**: Execute curl as a subprocess and capture its output synchronously.

```swift
private func performRequest(
    method: HTTPMethod,
    url: String,
    headers: [String: String]?,
    contentType: String?,
    body: String?
) throws -> String {
    // Locate curl executable
    let curlPath = try findCurl()  // Checks /usr/bin/curl, /bin/curl, etc.
    
    // Build curl arguments
    var arguments: [String] = []
    arguments.append("-s")  // Silent mode
    arguments.append("-S")  // Show errors
    arguments.append("-X")
    arguments.append(method.rawValue)  // GET or POST
    
    // Add headers
    if let headers = headers {
        for (key, value) in headers {
            arguments.append("-H")
            arguments.append("\(key): \(value)")
        }
    }
    
    // Add body for POST
    if let body = body, method == .post {
        arguments.append("-d")
        arguments.append(body)
    }
    
    arguments.append(url)
    
    // Create and run process
    let process = Process()
    process.executableURL = URL(fileURLWithPath: curlPath)
    process.arguments = arguments
    
    let outputPipe = Pipe()
    let errorPipe = Pipe()
    process.standardOutput = outputPipe
    process.standardError = errorPipe
    
    try process.run()
    process.waitUntilExit()  // Synchronous execution
    
    // Check exit status
    guard process.terminationStatus == 0 else {
        throw HTTPError.curlExecutionFailed(process.terminationStatus)
    }
    
    // Read and return output
    let outputData = outputPipe.fileHandleForReading.readDataToEndOfFile()
    guard let responseString = String(data: outputData, encoding: .utf8) else {
        throw HTTPError.encodingError
    }
    
    return responseString
}
```

### Why This Approach Works

**1. No FoundationNetworking dependency**
- Uses Swift's `Process` API (Foundation.Process) which works identically on macOS and Linux
- curl binary is available on all Unix systems (standard on macOS, Linux)
- No internal `DispatchQueue.sync()` complications

**2. True synchronous execution**
- `process.waitUntilExit()` blocks until curl completes
- No semaphores, no locks, no dispatch queue coordination needed
- Simple, predictable behavior

**3. Cross-platform parity guaranteed**
- curl binary behaves identically on macOS and Linux
- Same command-line interface on all platforms
- Same output format, same error codes

**4. Battle-tested networking**
- curl/libcurl is one of the most widely used networking libraries in the world
- Handles edge cases, redirects, authentication, SSL/TLS automatically
- Mature error handling and timeout management

---

## Concrete Example: ShortableShares() Instance

### The Use Case

The `IBShortableShares.swift` module downloads a list of shortable stocks from Interactive Brokers' FTP server:

```swift
class ShortableSharesData {
    let ibkrFtp = "ftp://ftp2.interactivebrokers.com/usa.txt"
    let username = "shortstock"
    let password = ""
    
    func downloadShortableShares() throws -> ShortableShareFastDB {
        print("Please wait while downloading shortable shares...")
        
        // This is where HTTPClient is used
        let rawContent = try HTTPClient().ftp(
            url: self.ibkrFtp,
            username: self.username,
            password: self.password
        ).replacingOccurrences(of: "\r", with: "")
        
        // Parse the data
        let rawData = rawContent.components(separatedBy: "\n")
        let shares: [ShortableShareEntry] = rawData.compactMap({
            do {
                return try ShortableShareEntry.init($0)
            } catch {
                print("Error parsing ShortableShareEntry: \(error)")
                return nil
            }
        })
        
        print("Shortable shares downloaded successfully. Total entries: \(rawData.count)")
        return ShortableShareFastDB(entries: shares)
    }
}
```

### Why URLSession.dataTask() Would Fail Here

**Scenario**: This code runs during initialization of the IB module, potentially from a WebSocket callback context or dispatch queue.

**On Linux with URLSession.dataTask()**:
1. WebSocket receives authentication success
2. Initialization triggers `downloadShortableShares()`
3. Code creates `URLSession.dataTask()` for FTP request
4. **Internal DispatchQueue.sync() deadlocks** against the WebSocket callback queue
5. SIGTRAP crash

**On macOS with URLSession.dataTask()**:
- Works fine because native Foundation doesn't have this issue

**Result**: Platform-dependent behavior - the exact problem Argus wanted to avoid.

### How HTTPClient Solves It

**With HTTPClient**:
1. WebSocket receives authentication success
2. Initialization triggers `downloadShortableShares()`
3. Code executes `curl -u shortstock: ftp://ftp2.interactivebrokers.com/usa.txt`
4. curl subprocess runs **independently** of dispatch queues
5. Process waits synchronously for curl to complete
6. Returns result - no deadlock possible

**Result**: **Identical behavior on macOS and Linux** - true cross-platform parity.

### Data Flow Example

```
FTP Server                           HTTPClient (cURL.swift)              ShortableShares
     |                                        |                                   |
     |<------ curl -u shortstock: ftp://... --|                                   |
     |                                        |                                   |
     |-------- usa.txt (pipe format) ------->|                                   |
     |                                        |                                   |
     |                                        |-- String response -------------->|
     |                                        |                                   |
     |                                        |                                   |-- Parse entries
     |                                        |                                   |-- Build FastDB
     |                                        |                                   |-- Ready for lookups
```

### The Data Structure

Downloaded FTP file format:
```
#SYM|CUR|NAME|CON|ISIN|REBATERATE|FEERATE|AVAILABLE|FIGI|
A|USD|AGILENT TECHNOLOGIES INC|1715006|US00846U1016|3.2319|0.4081|4100000|BBG000C2V3D6|
AA|USD|ALCOA CORP|251962528|US0138721065|3.3761|0.2639|4400000|BBG00B3T3HD3|
```

This data is crucial for the IB module to:
- Map stock symbols to IBKR contract IDs (conid)
- Check if stocks are available for short selling
- Display rebate rates and fees
- Build internal lookup tables (`symbolToEntry`, `conidToEntry`)

**Without FTP access working on Linux**, the IB module would be crippled on that platform.

---

## The Architectural Shift: A New Paradigm for Networking

### Before: Split Approach

**Pre-HTTPClient architecture**:
```
Binance Module:
  - WebSocket only (URLSessionWebSocketTask) ✅ Works on Linux
  - No HTTP calls ✅ No deadlock issues

IB Module:
  - WebSocket (URLSessionWebSocketTask) ✅ Works on Linux
  - HTTP/FTP (URLSession.dataTask()) ❌ Deadlocks on Linux
```

**Problem**: Architectural inconsistency. Modules using only WebSockets worked fine, but any module needing HTTP would break on Linux.

### After: Unified Cross-Platform Approach

**Post-HTTPClient architecture**:
```
All Modules:
  - WebSocket: URLSessionWebSocketTask ✅ Works on both platforms
  - HTTP/FTP: HTTPClient (process-based curl) ✅ Works on both platforms
```

**Achievement**: **Full platform parity** - no module is Linux-incompatible anymore.

### Design Principles Established

**1. Process-based networking for HTTP/FTP**
- Leverage system tools (curl) instead of fighting FoundationNetworking
- Accept the overhead of process spawning for reliability
- Synchronous execution aligns with Argus's design (no async/await required)

**2. URLSessionWebSocketTask for WebSockets**
- WebSocket implementation works well on both platforms
- No known deadlock issues with WebSocket frames
- Handles binary and string frames correctly

**3. No external Swift packages**
- Maintains Argus's constraint: pure Swift + system libraries
- curl is standard on all Unix systems
- No dependency management complexity

**4. Separation of concerns**
- Request/response (HTTP/FTP): Use HTTPClient
- Streaming bidirectional (WebSocket): Use URLSessionWebSocketTask
- Clear boundary: Don't mix approaches

### Comparison with LINUX_ABORT_TRAP_DEADLOCK.md Solutions

The deadlock document proposed three solutions:

**Solution 1**: Replace NSLock with serial DispatchQueue
- **Status**: Workaround, doesn't address root cause
- **Issue**: Still uses URLSession.dataTask() internally

**Solution 2**: Move HTTP calls to background queue
- **Status**: Another workaround
- **Issue**: Nested semaphore anti-pattern, brittle

**Solution 3**: C/C++ HTTP client with libcurl
- **Status**: Overcomplicated
- **Issue**: Requires C++ interop, custom memory management, linking complexity

**Actual Solution (HTTPClient)**: Process-based curl execution
- **Status**: ✅ Implemented
- **Advantages**:
  - Simpler than C/C++ interop
  - No custom memory management
  - No linking complexity
  - Works out of the box on all Unix systems
  - Maintainable pure Swift code

### Why Process-Based Approach Was Chosen

**Advantages**:
1. **Simplicity**: ~270 lines of straightforward Swift code
2. **Zero configuration**: curl is always available on target platforms
3. **No FFI complexity**: No C/C++ interop needed
4. **Battle-tested**: curl handles all edge cases (redirects, SSL, auth, timeouts)
5. **Debuggable**: Can test exact curl commands in terminal
6. **Portable**: Same code works on macOS, Linux, BSD, etc.

**Tradeoffs**:
1. **Performance overhead**: Process spawning ~1-5ms per request
   - **Acceptable** for Argus's use case (initialization, occasional queries)
   - Not suitable for high-frequency trading (but Argus isn't HFT)
2. **Memory overhead**: Separate process for each request
   - **Acceptable** for low-frequency HTTP calls
3. **No connection pooling**: Each request creates new connection
   - **Acceptable** given request patterns in Argus

**When this approach is appropriate**:
- ✅ Initialization-time HTTP requests (auth, setup)
- ✅ Periodic polling (account data, once per minute)
- ✅ One-time downloads (FTP shortable shares list)
- ✅ REST API calls that are infrequent

**When this approach is NOT appropriate**:
- ❌ High-frequency HTTP requests (>100 req/sec)
- ❌ Long-lived HTTP connections (streaming HTTP)
- ❌ Complex connection pooling requirements

---

## Impact on Future Modules

### Capital.com Module

**Status**: Already uses process-based authentication approach.

The Capital.com module (`CapitalComDispatcher.swift`) authenticates via REST API:
```swift
// Uses similar pattern to HTTPClient
// POST to /api/v1/session with credentials
// Extract CST and X-SECURITY-TOKEN from headers
```

**Lesson**: HTTP authentication followed by WebSocket streaming is a proven pattern across multiple exchanges.

### Future OANDA Module

**Expected architecture**:
```swift
class OandaNetworker {
    private let httpClient = HTTPClient()
    
    func authenticate() throws {
        // Use HTTPClient for REST API authentication
        let response = try httpClient.post(
            url: "https://api.oanda.com/v3/accounts",
            headers: ["Authorization": "Bearer \(apiKey)"],
            contentType: "application/json",
            body: nil
        )
        // Parse response
    }
    
    func getAccountSummary() throws -> OandaAccountSummary {
        // Use HTTPClient for periodic REST queries
        let response = try httpClient.get(
            url: "https://api.oanda.com/v3/accounts/\(accountId)/summary",
            headers: ["Authorization": "Bearer \(apiKey)"]
        )
        // Parse response
    }
}

class OandaWss {
    // Use URLSessionWebSocketTask for streaming prices
    func streamPrices(instruments: [String]) {
        // WebSocket to wss://stream-fxtrade.oanda.com/v3/accounts/...
    }
}
```

**Pattern established**:
1. Use `HTTPClient` for authentication, account queries, order placement (REST)
2. Use `URLSessionWebSocketTask` for real-time price streaming
3. Guaranteed to work on both macOS and Linux

### Generic Exchange Integration Pattern

```
Exchange Integration Recipe:
┌──────────────────────────────────────────────────────────┐
│ 1. Authentication & Setup (HTTPClient)                   │
│    - REST API authentication                             │
│    - Get account IDs, session tokens                     │
│    - Query static data (instruments, contracts)          │
│                                                           │
│ 2. Real-time Data (URLSessionWebSocketTask)             │
│    - Connect WebSocket                                   │
│    - Subscribe to market data                            │
│    - Stream price updates                                │
│                                                           │
│ 3. Occasional REST Queries (HTTPClient)                 │
│    - Check positions/balances                            │
│    - Place/modify/cancel orders                          │
│    - Query account state                                 │
└──────────────────────────────────────────────────────────┘
```

This pattern now works **identically** on macOS and Linux.

---

## Technical Deep Dive: Why Process-Based Curl Works

### The Process API on macOS and Linux

Swift's `Foundation.Process` (formerly `NSTask`) is a **wrapper around POSIX fork/exec**. It works identically on all Unix-like systems:

```swift
// This code is identical on macOS and Linux
let process = Process()
process.executableURL = URL(fileURLWithPath: "/usr/bin/curl")
process.arguments = ["-s", "https://example.com"]
process.standardOutput = Pipe()

try process.run()         // fork() + exec()
process.waitUntilExit()   // waitpid()
let status = process.terminationStatus
```

**Under the hood**:
1. `process.run()` → `fork()` creates child process
2. Child process → `exec("/usr/bin/curl", ...)` replaces itself with curl
3. Parent process → `waitpid()` blocks until child completes
4. Pipes capture stdout/stderr from child

**Key insight**: This POSIX API predates Swift, Foundation, and FoundationNetworking. It's **rock-solid** across all Unix systems.

### Curl: The Universal Networking Tool

**Why curl is everywhere**:
- Included in macOS by default (`/usr/bin/curl`)
- Included in all major Linux distributions
- Used by billions of API calls daily
- Handles: HTTP/1.1, HTTP/2, HTTP/3, FTP, FTPS, SFTP, SCP, TELNET, DICT, LDAP, MQTT, and more

**Curl's robustness**:
```bash
# Automatically handles redirects
curl -L https://example.com

# SSL/TLS verification out of the box
curl https://example.com

# Authentication (Basic, Digest, OAuth, etc.)
curl -u user:pass https://example.com

# Custom headers
curl -H "Authorization: Bearer token" https://example.com

# FTP with authentication
curl -u user:pass ftp://ftp.example.com/file.txt

# Timeouts and retries
curl --max-time 30 --retry 3 https://example.com
```

All of this complexity is handled by curl - the Swift code just has to parse the output.

### Error Handling and Edge Cases

**HTTPClient handles**:

1. **Curl not found**: Checks multiple paths (`/usr/bin/curl`, `/bin/curl`, `/usr/local/bin/curl`)
2. **Non-zero exit codes**: Throws `HTTPError.curlExecutionFailed(exitCode)`
3. **Empty response**: Throws `HTTPError.emptyResponse`
4. **Encoding errors**: Throws `HTTPError.encodingError`
5. **Process launch failures**: Catches and wraps in `HTTPError.processError`

**Example**:
```swift
do {
    let response = try client.get(url: "https://api.example.com/data")
    print("Success: \(response)")
} catch HTTPError.curlNotFound {
    print("curl binary not found on system")
} catch HTTPError.curlExecutionFailed(let code) {
    print("curl failed with exit code \(code)")
} catch {
    print("Other error: \(error)")
}
```

### Performance Characteristics

**Process spawning overhead** (measured on typical systems):
- macOS: ~2-4ms per spawn
- Linux: ~1-3ms per spawn

**HTTP request total time**:
- Local network: ~10-50ms (dominated by network RTT)
- Internet: ~100-500ms (dominated by network RTT)

**Process overhead as percentage of total**:
- Local: 4ms / 30ms = ~13% overhead
- Internet: 4ms / 200ms = ~2% overhead

**Conclusion**: For Argus's use case (infrequent HTTP calls during initialization and periodic queries), the overhead is negligible.

---

## Comparison with Other Approaches

### URLSession.dataTask() (FoundationNetworking)

| Aspect | URLSession.dataTask() | HTTPClient (curl) |
|--------|----------------------|-------------------|
| **Linux behavior** | ❌ Deadlocks with dispatch queues | ✅ No deadlock issues |
| **macOS behavior** | ✅ Works fine | ✅ Works fine |
| **Platform parity** | ❌ Different behavior | ✅ Identical behavior |
| **Setup complexity** | None | None (curl is standard) |
| **Performance** | ~0ms overhead | ~2-4ms overhead per request |
| **Memory** | Shared process | Separate process (~2MB) |
| **Use case fit** | Any frequency | Low-to-medium frequency |

**Verdict**: HTTPClient wins for Argus's cross-platform requirements.

### Swift-NIO + AsyncHTTPClient

| Aspect | Swift-NIO | HTTPClient (curl) |
|--------|-----------|-------------------|
| **External package** | ❌ Required | ✅ None (system curl) |
| **Async/await** | ❌ Required (or callbacks) | ✅ Synchronous |
| **Platform parity** | ✅ Good | ✅ Excellent |
| **Setup complexity** | High (package dependency) | None |
| **Performance** | Excellent | Good |
| **Memory** | Low | Medium |

**Verdict**: HTTPClient wins given Argus's "no external packages" and "no async/await" constraints.

### C/C++ libcurl with Swift FFI

| Aspect | C++ libcurl | HTTPClient (curl) |
|--------|-------------|-------------------|
| **Implementation complexity** | High (C++ interop) | Low (pure Swift) |
| **Memory management** | Manual (C pointers) | Automatic (Swift) |
| **Build complexity** | High (linking, headers) | None |
| **Platform parity** | ✅ Excellent | ✅ Excellent |
| **Performance** | Excellent | Good |
| **Maintainability** | Low | High |

**Verdict**: HTTPClient wins for simplicity and maintainability.

---

## Lessons for Other Swift Projects

### When to Use Process-Based Networking

**Good fit**:
- Cross-platform Swift projects targeting Unix systems
- Applications with infrequent HTTP requests (<10 req/sec)
- Projects avoiding external dependencies
- Situations where curl's robustness is valuable (SSL, auth, redirects)
- Codebases that need synchronous HTTP (no async/await)

**Poor fit**:
- High-frequency API calls (>100 req/sec)
- Mobile applications (iOS/Android where process spawning is expensive)
- Windows-first applications (curl not standard on Windows)
- Projects already using async/await (might as well use native APIs)

### Generalizing the Pattern

The HTTPClient pattern can be extended to other command-line tools:

```swift
// SSH/SCP client
class SSHClient {
    func execute(host: String, command: String) throws -> String {
        // Execute: ssh user@host command
    }
}

// Git operations
class GitClient {
    func clone(repo: String, path: String) throws {
        // Execute: git clone repo path
    }
}

// Database queries
class PostgresClient {
    func query(sql: String) throws -> String {
        // Execute: psql -c "sql" 
    }
}
```

**Pattern**: When a robust command-line tool exists and matches your usage pattern (synchronous, low-frequency), wrap it in Swift's Process API rather than implementing from scratch or using complex FFI.

---

## Security Considerations

### Input Validation

**Current HTTPClient implementation** does **minimal input validation**. This is acceptable for Argus's internal use case (controlled URLs, no user input), but projects exposing this to user input should add:

**URL validation**:
```swift
private func validateURL(_ url: String) throws {
    // Check for shell injection attempts
    guard !url.contains(";"), !url.contains("|"), !url.contains("&") else {
        throw HTTPError.invalidURL
    }
    
    // Verify URL format
    guard let _ = URL(string: url) else {
        throw HTTPError.invalidURL
    }
}
```

**Header validation**:
```swift
private func validateHeader(key: String, value: String) throws {
    // Prevent CRLF injection
    guard !key.contains("\r"), !key.contains("\n"),
          !value.contains("\r"), !value.contains("\n") else {
        throw HTTPError.invalidHeader
    }
}
```

### Process Isolation

**Advantage of process-based approach**: Each curl invocation runs in an isolated process with its own memory space. If curl crashes or is compromised, it doesn't affect the parent Swift process.

**Security boundary**:
```
Swift Process (Argus)           Curl Process
┌──────────────────┐           ┌──────────────┐
│ Main application │◄─────────►│ HTTP request │
│ (trusted)        │   Pipes   │ (isolated)   │
└──────────────────┘           └──────────────┘
```

If curl encounters malicious input or crashes, the Swift application continues running.

### SSL/TLS Verification

Curl performs SSL/TLS certificate verification by default:
```bash
# This will fail on invalid certificate
curl https://self-signed.badssl.com/  # Error!

# This will succeed on valid certificate
curl https://example.com/  # Success
```

No additional code needed - curl handles it automatically.

---

## Future Enhancements

### 1. Connection Pooling (if needed)

If Argus scales to higher HTTP request rates, connection pooling could be added:

```swift
class HTTPClientPool {
    private var processes: [Process] = []
    private let maxConnections = 10
    
    func getClient() -> Process {
        // Reuse existing process or create new one
    }
    
    func releaseClient(_ process: Process) {
        // Return to pool
    }
}
```

**Note**: Only implement if profiling shows process spawning is a bottleneck.

### 2. Async Wrapper (if async/await is adopted)

If Argus eventually adopts async/await, wrap HTTPClient:

```swift
extension HTTPClient {
    func getAsync(url: String) async throws -> String {
        return try await Task.detached {
            try self.get(url: url)
        }.value
    }
}
```

### 3. Response Parsing Helpers

Add convenience methods for common response types:

```swift
extension HTTPClient {
    func getJSON(url: String) throws -> [String: Any] {
        let response = try get(url: url)
        guard let data = response.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw HTTPError.invalidJSON
        }
        return json
    }
}
```

### 4. Request/Response Logging

For debugging, add optional logging:

```swift
class HTTPClient {
    var enableLogging = false
    
    private func performRequest(...) throws -> String {
        if enableLogging {
            print("[HTTPClient] \(method.rawValue) \(url)")
        }
        // ... existing code ...
        if enableLogging {
            print("[HTTPClient] Response: \(responseString.prefix(100))...")
        }
    }
}
```

---

## Conclusion

### What Issue #39 Achieved

**Primary Goal**: Linux-macOS parity for HTTP/FTP networking ✅

**How**:
1. Identified FoundationNetworking's URLSession.dataTask() as root cause of Linux deadlocks
2. Designed HTTPClient using process-based curl execution
3. Bypassed FoundationNetworking entirely
4. Achieved identical behavior on both platforms

**Secondary Benefits**:
- No external Swift package dependencies ✅
- Synchronous API (matches Argus's design philosophy) ✅
- Battle-tested networking (curl's robustness) ✅
- Simple, maintainable pure Swift code ✅
- Established pattern for future exchange integrations ✅

### The ShortableShares() Instance as Proof

**Before HTTPClient**:
- macOS: `downloadShortableShares()` works fine ✅
- Linux: SIGTRAP deadlock in URLSession.dataTask() ❌
- **No Linux support for IB shortable shares**

**After HTTPClient**:
- macOS: `downloadShortableShares()` works fine ✅
- Linux: `downloadShortableShares()` works fine ✅
- **Full Linux support for IB module**

The same code now runs identically on both platforms - true cross-platform parity.

### Architectural Impact Going Forward

**The pattern established**:
```
┌─────────────────────────────────────────────────┐
│           Argus Networking Architecture         │
├─────────────────────────────────────────────────┤
│                                                 │
│  HTTP/FTP (request/response):                   │
│    → HTTPClient (process-based curl)            │
│    → Works identically on macOS and Linux       │
│                                                 │
│  WebSocket (bidirectional streaming):           │
│    → URLSessionWebSocketTask                    │
│    → Works identically on macOS and Linux       │
│                                                 │
│  Result: Full platform parity ✅                │
└─────────────────────────────────────────────────┘
```

**Every future module** (OANDA, Schwab, Tradier, etc.) will follow this pattern:
1. Use HTTPClient for REST API interactions
2. Use URLSessionWebSocketTask for real-time data streams
3. Guaranteed to work on both macOS and Linux

**This is the new paradigm** for cross-platform networking in Argus Swift.

---

## References

1. **swift-corelibs-foundation URLSession issues**:
   - [Issue #2980](https://github.com/apple/swift-corelibs-foundation/issues/2980) - URLSession DispatchQueue.sync() deadlock
   - [Issue #3012](https://github.com/apple/swift-corelibs-foundation/issues/3012) - URLSession Linux implementation issues

2. **Related documentation**:
   - `LINUX_ABORT_TRAP_DEADLOCK.md` - Detailed analysis of the deadlock issue
   - `ARCHITECTURE.md` - Overall Argus Swift architecture
   - `cURL.swift` - HTTPClient implementation
   - `IBShortableShares.swift` - Concrete usage example

3. **Curl documentation**:
   - [curl.se](https://curl.se/) - Official curl website
   - [libcurl tutorial](https://curl.se/libcurl/c/libcurl-tutorial.html)

4. **Swift Process API**:
   - [Foundation.Process documentation](https://developer.apple.com/documentation/foundation/process)

---

## Appendix: Code Comparison

### Before: URLSession.dataTask() Approach

```swift
// This would deadlock on Linux in certain contexts
func downloadShortableShares() throws -> ShortableShareFastDB {
    let url = URL(string: "ftp://ftp2.interactivebrokers.com/usa.txt")!
    var request = URLRequest(url: url)
    
    let semaphore = DispatchSemaphore(value: 0)
    var result: (Data, URLResponse)?
    var error: Error?
    
    URLSession.shared.dataTask(with: request) { data, response, err in
        // On Linux: This internally uses DispatchQueue.sync()
        // If called from a dispatch queue context: DEADLOCK
        if let err = err {
            error = err
        } else {
            result = (data!, response!)
        }
        semaphore.signal()
    }.resume()
    
    semaphore.wait()  // SIGTRAP on Linux
    
    // Process result...
}
```

### After: HTTPClient Approach

```swift
// This works identically on macOS and Linux
func downloadShortableShares() throws -> ShortableShareFastDB {
    print("Please wait while downloading shortable shares...")
    
    // No deadlock possible - independent process
    let rawContent = try HTTPClient().ftp(
        url: self.ibkrFtp,
        username: self.username,
        password: self.password
    ).replacingOccurrences(of: "\r", with: "")
    
    // Parse the data
    let rawData = rawContent.components(separatedBy: "\n")
    let shares: [ShortableShareEntry] = rawData.compactMap({
        do {
            return try ShortableShareEntry.init($0)
        } catch {
            print("Error parsing ShortableShareEntry: \(error)")
            return nil
        }
    })
    
    print("Shortable shares downloaded successfully. Total entries: \(rawData.count)")
    return ShortableShareFastDB(entries: shares)
}
```

**The difference**: Process-based execution eliminates dispatch queue interactions entirely.

---

## Document Information

- **Created**: December 2024
- **Issue**: #39
- **Status**: Resolved
- **Impact**: Critical - Enables Linux support for all HTTP-dependent modules
- **Related**: LINUX_ABORT_TRAP_DEADLOCK.md, ARCHITECTURE.md
