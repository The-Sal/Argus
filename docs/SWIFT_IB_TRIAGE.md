# Swift Interactive Brokers (IB) Module Triage

## Overview

This document provides a detailed roadmap for bringing the Swift IB implementation up to feature parity with the Python implementation. It addresses the significant gaps identified in the feature comparison analysis and provides actionable implementation steps.

**Current Status:** Swift IB is at **~55% feature parity** and is **NOT production-ready**.

**Goal:** Achieve **95%+ feature parity** and production-ready status.

---

## Executive Summary

### Critical Gaps (Must Fix)

1. **Empty Interactive Mode** - Function exists but has NO commands
2. **Missing 4 of 5 Dispatcher Modes** - Only Protocol 2 supported
3. **No Disk Caching** - All cache lost on restart
4. **No Shortable Shares** - Critical for short sellers

### High Priority Gaps

5. Runtime Configuration System
6. Debugging Tools
7. WebSocket Message Logging
8. Account Ledger/Summary APIs

### Medium Priority Gaps

9. Notification System
10. Enhanced AccountProvider
11. LockedSession Pattern

---

## Phase 1: Fix Interactive Mode (Critical)

**Priority:** URGENT  
**Estimated Effort:** 2-3 days  
**Impact:** Makes dispatcher usable for development and debugging

### Current State

```swift
func interactiveMode() {
    print("\nIBKR Dispatcher Interactive Mode")
    print("Enter commands (or 'exit' to quit):")
    print("Server is running. Press Ctrl+C to stop.")
    
    while true {
        print("> ", terminator: "")
        guard let input = readLine()?.trimmingCharacters(in: .whitespacesAndNewlines) else {
            Thread.sleep(forTimeInterval: 1.0)
            continue
        }
        
        if input.lowercased() == "exit" {
            print("Shutting down...")
            break
        }
        
        if !input.isEmpty {
            print("Unknown command: \(input)")  // ALL commands print this!
        }
    }
}
```

**Problem:** No commands implemented - everything prints "Unknown command".

### Implementation Plan

#### Step 1: Add Menu System

Create a menu-driven interface similar to Binance Swift:

```swift
func interactiveMode() {
    print("\nIBKR Dispatcher Interactive Mode")
    print(String(repeating: "=", count: 50))
    
    while true {
        print("\nOptions:")
        print("1. Show subscribed contracts")
        print("2. Show connected clients")
        print("3. Show configurations")
        print("4. Modify configuration")
        print("5. Show subscription stats")
        print("6. Show WebSocket health")
        print("7. Write WebSocket messages to file")
        print("8. Show account info")
        print("9. Search contract")
        print("0. Exit")
        
        print("\nSelect option: ", terminator: "")
        // Note: fflush is available in Swift but not strictly necessary
        // as print() flushes automatically with terminator parameter
        
        guard let choice = readLine()?.trimmingCharacters(in: .whitespacesAndNewlines) else {
            continue
        }
        
        switch choice {
        case "1": showSubscribedContracts()
        case "2": showConnectedClients()
        case "3": showConfigurations()
        case "4": modifyConfiguration()
        case "5": showSubscriptionStats()
        case "6": showWebSocketHealth()
        case "7": writeWebSocketMessagesToFile()
        case "8": showAccountInfo()
        case "9": searchContract()
        case "0": 
            print("Shutting down...")
            return
        default:
            print("Invalid option. Please try again.")
        }
    }
}
```

#### Step 2: Implement Core Commands

**Command 1: Show Subscribed Contracts**

```swift
private func showSubscribedContracts() {
    threadLock.lock()
    defer { threadLock.unlock() }
    
    print("\n=== Subscribed Contracts ===")
    if conidToClients.isEmpty {
        print("No active subscriptions")
    } else {
        for (conid, clients) in conidToClients.sorted(by: { $0.key < $1.key }) {
            let symbol = caches[conid]?[IBKRFields.SYMBOL] as? String ?? "Unknown"
            print("  Contract \(conid) (\(symbol)): \(clients.count) client(s)")
        }
        print("Total: \(conidToClients.count) contracts")
    }
}
```

**Command 2: Show Connected Clients**

```swift
private func showConnectedClients() {
    threadLock.lock()
    let clientCount = clients.count
    threadLock.unlock()
    
    print("\n=== Connected Clients ===")
    print("Total clients: \(clientCount)")
    
    // Optionally show client IDs or addresses if tracked
}
```

**Command 3: Show Configurations**

```swift
private func showConfigurations() {
    print("\n=== Current Configurations ===")
    for (key, value) in configs.sorted(by: { $0.key < $1.key }) {
        print("  \(key): \(value)")
    }
}
```

**Command 4: Modify Configuration**

```swift
private func modifyConfiguration() {
    print("\n=== Modify Configuration ===")
    print("Available configurations:")
    let configKeys = Array(configs.keys).sorted()
    for (index, key) in configKeys.enumerated() {
        print("\(index + 1). \(key) (current: \(configs[key] ?? "nil"))")
    }
    
    print("\nSelect configuration number (0 to cancel): ", terminator: "")
    fflush(stdout)
    
    guard let input = readLine(),
          let choice = Int(input),
          choice > 0, choice <= configKeys.count else {
        print("Cancelled.")
        return
    }
    
    let key = configKeys[choice - 1]
    print("Enter new value for '\(key)' (current: \(configs[key] ?? "nil")): ", terminator: "")
    fflush(stdout)
    
    guard let newValue = readLine()?.trimmingCharacters(in: .whitespacesAndNewlines) else {
        print("Cancelled.")
        return
    }
    
    // Parse boolean values
    if newValue.lowercased() == "true" {
        configs[key] = true
    } else if newValue.lowercased() == "false" {
        configs[key] = false
    } else {
        configs[key] = newValue
    }
    
    print("Updated '\(key)' to '\(configs[key] ?? "nil")'")
}
```

**Command 5: Show Subscription Stats**

```swift
private func showSubscriptionStats() {
    threadLock.lock()
    let contractCount = conidToClients.count
    let clientCount = clients.count
    threadLock.unlock()
    
    print("\n=== Subscription Statistics ===")
    print("Active contracts: \(contractCount)/100")
    print("Connected clients: \(clientCount)")
    print("WebSocket status: \(ws.isConnected() ? "Connected" : "Disconnected")")
}
```

**Command 6: Show WebSocket Health**

```swift
private func showWebSocketHealth() {
    print("\n=== WebSocket Health ===")
    print("Connected: \(ws.isConnected())")
    print("Messages received: \(ws.messageCount)")
    print("Last message: \(ws.lastMessageTime)")
    // Add more health metrics from IBWss
}
```

**Command 7: Write WebSocket Messages to File**

```swift
private func writeWebSocketMessagesToFile() {
    let messages = ws.getStoredMessages()
    if messages.isEmpty {
        print("No WebSocket messages to write.")
        return
    }
    
    let filename = "ibkr_websocket_messages_\(Date().timeIntervalSince1970).txt"
    let url = FileManager.default.temporaryDirectory.appendingPathComponent(filename)
    
    do {
        try messages.joined(separator: "\n").write(to: url, atomically: true, encoding: .utf8)
        print("Wrote \(messages.count) messages to \(url.path)")
    } catch {
        print("Error writing file: \(error)")
    }
}
```

**Command 8: Show Account Info**

```swift
private func showAccountInfo() {
    guard let provider = accountProvider else {
        print("Account provider not initialized")
        return
    }
    
    print("\n=== Account Information ===")
    print("Account ID: \(provider.accountId ?? "Not set")")
    print("Positions: \(provider.positions.count)")
    // Add more account details
}
```

**Command 9: Search Contract**

```swift
private func searchContract() {
    print("\nEnter symbol to search: ", terminator: "")
    fflush(stdout)
    
    guard let symbol = readLine()?.trimmingCharacters(in: .whitespacesAndNewlines), !symbol.isEmpty else {
        print("Cancelled.")
        return
    }
    
    print("Searching for '\(symbol)'...")
    
    // Call IBNetworker to search
    // This requires making searchContract async or running on background thread
    DispatchQueue.global().async {
        do {
            let results = try self.ws.networker.searchContract(symbol: symbol)
            DispatchQueue.main.async {
                print("\n=== Search Results ===")
                if results.isEmpty {
                    print("No results found for '\(symbol)'")
                } else {
                    for (index, result) in results.enumerated() {
                        print("\(index + 1). [\(result.conid)] \(result.description ?? result.symbol)")
                    }
                }
            }
        } catch {
            DispatchQueue.main.async {
                print("Error searching: \(error)")
            }
        }
    }
}
```

#### Step 3: Add Supporting Infrastructure

**In IBWss.swift:**

```swift
class IBWss {
    // Add message tracking
    private var storedMessages: [String] = []
    private var _messageCount: Int = 0
    private var _lastMessageTime: Date?
    
    var messageCount: Int {
        return _messageCount
    }
    
    var lastMessageTime: String {
        guard let time = _lastMessageTime else { return "Never" }
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return formatter.string(from: time)
    }
    
    func isConnected() -> Bool {
        // Check actual WebSocket connection state
        // Assuming webSocket has a readyState or similar property
        guard let ws = webSocket else { return false }
        return ws.state == .open  // Adjust based on actual WebSocket API
    }
    
    func getStoredMessages() -> [String] {
        return storedMessages
    }
    
    // In message handler:
    func onMessage(_ message: String) {
        _messageCount += 1
        _lastMessageTime = Date()
        storedMessages.append(message)
        
        // Limit stored messages to prevent memory issues
        if storedMessages.count > 1000 {
            storedMessages.removeFirst()
        }
        
        // ... rest of message handling
    }
}
```

### Testing Plan

1. Run Swift IB dispatcher
2. Enter interactive mode
3. Test each command:
   - Subscribe to a contract via TCP client
   - Use command 1 to verify it shows up
   - Use command 3 to see configurations
   - Use command 4 to toggle "Print data packets"
   - Verify packet printing changes
   - Use command 6 to check WebSocket health
   - Use command 9 to search for "AAPL"

### Success Criteria

- ✅ All 9 commands functional
- ✅ Configuration changes take effect immediately
- ✅ No "Unknown command" messages for valid inputs
- ✅ Interactive mode is useful for debugging

---

## Phase 2: Implement Disk Caching (Critical)

**Priority:** CRITICAL  
**Estimated Effort:** 3-4 days  
**Impact:** Dramatically improves startup time and reduces API load

### Current State

Swift has no disk persistence. All caches (contract searches, account data) are in-memory and lost on restart.

**Impact:**
- Every restart must re-fetch all contract searches from IBKR API
- Slow startup (10-30 seconds for 20 contracts)
- Higher API load, risk of rate limiting

### Implementation Plan

#### Step 1: Create Cache Manager

**File:** `argus_swift/Sources/ArgusServer/CacheManager.swift`

```swift
import Foundation

/// Generic disk-based cache manager inspired by Python's DomainCache
class CacheManager {
    private let cacheDirectory: URL
    private let cacheFilename: String
    private var cache: [String: Any] = [:]
    private let lock = NSLock()
    
    init(domain: String) {
        // Use ~/.argus/ directory like Python
        let homeDir = FileManager.default.homeDirectoryForCurrentUser
        self.cacheDirectory = homeDir.appendingPathComponent(".argus")
        self.cacheFilename = "\(domain)_cache.json"
        
        // Create directory if needed
        try? FileManager.default.createDirectory(at: cacheDirectory, withIntermediateDirectories: true)
        
        // Load existing cache
        loadCache()
    }
    
    private func cacheFilePath() -> URL {
        return cacheDirectory.appendingPathComponent(cacheFilename)
    }
    
    private func loadCache() {
        lock.lock()
        defer { lock.unlock() }
        
        let path = cacheFilePath()
        guard FileManager.default.fileExists(atPath: path.path) else {
            print("[Cache] No existing cache file at \(path.path)")
            return
        }
        
        do {
            let data = try Data(contentsOf: path)
            if let decoded = try JSONSerialization.jsonObject(with: data) as? [String: Any] {
                cache = decoded
                print("[Cache] Loaded \(cache.count) entries from \(cacheFilename)")
            }
        } catch {
            print("[Cache] Error loading cache: \(error)")
        }
    }
    
    func saveCache() {
        lock.lock()
        defer { lock.unlock() }
        
        do {
            let data = try JSONSerialization.data(withJSONObject: cache, options: .prettyPrinted)
            try data.write(to: cacheFilePath())
            print("[Cache] Saved \(cache.count) entries to \(cacheFilename)")
        } catch {
            print("[Cache] Error saving cache: \(error)")
        }
    }
    
    func get(_ key: String) -> Any? {
        lock.lock()
        defer { lock.unlock() }
        return cache[key]
    }
    
    func set(_ key: String, value: Any) {
        lock.lock()
        cache[key] = value
        lock.unlock()
        
        // Save to disk asynchronously
        DispatchQueue.global().async {
            self.saveCache()
        }
    }
    
    func clear() {
        lock.lock()
        cache.removeAll()
        lock.unlock()
        saveCache()
    }
}
```

#### Step 2: Integrate with IBNetworker

**In IBNetworker.swift:**

```swift
class IBNetworker {
    private let cache: CacheManager
    
    init(cookie: String) {
        self.cache = CacheManager(domain: "ib")
        // ... rest of init
    }
    
    func searchContract(symbol: String) throws -> [SearchResult] {
        // Check cache first
        let cacheKey = "contract_search:\(symbol)"
        if let cached = cache.get(cacheKey) as? [[String: Any]] {
            print("[IBNetworker] Using cached results for '\(symbol)'")
            return cached.compactMap { SearchResult.from(dict: $0) }
        }
        
        print("[IBNetworker] Searching IBKR API for '\(symbol)'")
        let results = try performSearch(symbol: symbol)
        
        // Cache results
        let serialized = results.map { $0.toDictionary() }
        cache.set(cacheKey, value: serialized)
        
        return results
    }
}
```

#### Step 3: Make SearchResult Serializable

```swift
extension SearchResult {
    func toDictionary() -> [String: Any] {
        return [
            "conid": conid,
            "symbol": symbol,
            "description": description ?? "",
            "secType": secType ?? "",
            // ... all fields
        ]
    }
    
    static func from(dict: [String: Any]) -> SearchResult? {
        guard let conid = dict["conid"] as? Int,
              let symbol = dict["symbol"] as? String else {
            return nil
        }
        
        return SearchResult(
            conid: conid,
            symbol: symbol,
            description: dict["description"] as? String,
            secType: dict["secType"] as? String
            // ... all fields
        )
    }
}
```

#### Step 4: Add Cache Management Commands

Add to interactive mode:

```swift
case "10": clearCache()
case "11": showCacheStats()

private func clearCache() {
    print("Are you sure you want to clear the cache? (yes/no): ", terminator: "")
    fflush(stdout)
    
    guard let confirm = readLine()?.lowercased(), confirm == "yes" else {
        print("Cancelled.")
        return
    }
    
    ws.networker.clearCache()
    print("Cache cleared.")
}

private func showCacheStats() {
    let stats = ws.networker.getCacheStats()
    print("\n=== Cache Statistics ===")
    print("Cache file: \(stats.filename)")
    print("Entries: \(stats.entryCount)")
    print("Size: \(stats.sizeInBytes) bytes")
}
```

### Testing Plan

1. Start Swift IB dispatcher
2. Search for "AAPL" - should hit API (slow)
3. Restart dispatcher
4. Search for "AAPL" again - should use cache (instant)
5. Verify cache file exists at `~/.argus/ib_cache.json`
6. Verify file contains contract data

### Success Criteria

- ✅ Contract searches cached to disk
- ✅ Cache persists across restarts
- ✅ Second search for same symbol is instant
- ✅ Cache file location matches Python (`~/.argus/`)
- ✅ Cache can be cleared via interactive command

---

## Phase 3: Implement Multiple Dispatcher Modes (Critical)

**Priority:** CRITICAL  
**Estimated Effort:** 4-5 days  
**Impact:** Enables compatibility with various client types

### Current State

Swift only supports Protocol 2 mode. Python supports 5 modes:
1. ASK - Ask price only (lightest)
2. ASK+BID+LAST - Basic trading data
3. FULL_PKL - Pickled Python objects (not needed for Swift)
4. FULL_JSON - JSON format (cross-language)
5. PROTOCOL_2 - CSV format (current Swift mode)

### Implementation Plan

#### Step 1: Define Mode Enum

```swift
enum DispatcherMode: String {
    case ask = "ASK"
    case askBidLast = "ASK+BID+LAST"
    case fullJson = "FULL_JSON"
    case protocol2 = "PROTOCOL_2"
}
```

#### Step 2: Add Mode Selection

**In IBMKTDispatcher:**

```swift
class IBMKTDispatcher {
    private let mode: DispatcherMode
    
    init(cookie: String, host: String = "localhost", port: Int32 = 9972, mode: DispatcherMode = .protocol2) {
        self.mode = mode
        print("[IB Dispatcher] Mode: \(mode.rawValue)")
        // ... rest of init
    }
}
```

**In main.swift:**

```swift
func runIBDispatcher(args: Arguments, host: String, envVars: [String: String]) {
    // Parse mode from args
    let modeString = args.mode ?? "PROTOCOL_2"
    let mode = DispatcherMode(rawValue: modeString) ?? .protocol2
    
    let dispatcher = IBMKTDispatcher(cookie: cookie, host: host, port: port, mode: mode)
    // ...
}
```

#### Step 3: Implement ASK Mode

Simplest mode - only send ask price:

```swift
private func formatDataForMode(_ marketData: IBMarketData) -> Data? {
    switch mode {
    case .ask:
        return formatAsAsk(marketData)
    case .askBidLast:
        return formatAsAskBidLast(marketData)
    case .fullJson:
        return formatAsFullJson(marketData)
    case .protocol2:
        return formatAsProtocol2(marketData)
    }
}

private func formatAsAsk(_ data: IBMarketData) -> Data? {
    guard let ask = data.fields[IBKRFields.ASK_PRICE] as? Double else {
        return nil
    }
    
    let string = String(format: "%.2f\n", ask)
    return string.data(using: .utf8)
}
```

#### Step 4: Implement ASK+BID+LAST Mode

```swift
private func formatAsAskBidLast(_ data: IBMarketData) -> Data? {
    guard let ask = data.fields[IBKRFields.ASK_PRICE] as? Double,
          let bid = data.fields[IBKRFields.BID_PRICE] as? Double,
          let last = data.fields[IBKRFields.LAST_PRICE] as? Double else {
        return nil
    }
    
    let string = String(format: "%.2f,%.2f,%.2f\n", bid, ask, last)
    return string.data(using: .utf8)
}
```

#### Step 5: Implement FULL_JSON Mode

```swift
private func formatAsFullJson(_ data: IBMarketData) -> Data? {
    var json: [String: Any] = [:]
    
    // Convert all fields to JSON
    for (field, value) in data.fields {
        let fieldName = IBKRFields.fieldName(for: field) ?? "\(field)"
        json[fieldName] = value
    }
    
    json["symbol"] = data.symbol
    json["conid"] = data.conid
    json["timestamp"] = Date().timeIntervalSince1970
    
    guard let jsonData = try? JSONSerialization.data(withJSONObject: json, options: []) else {
        return nil
    }
    
    // Add newline delimiter
    var result = jsonData
    result.append(contentsOf: "\n".utf8)
    return result
}
```

#### Step 6: Keep Protocol 2 Implementation

Already implemented, no changes needed.

### Testing Plan

1. Test ASK mode:
   ```bash
   argus_server ib --mode ASK
   # Client connects, should receive only ask prices
   ```

2. Test ASK+BID+LAST mode:
   ```bash
   argus_server ib --mode ASK+BID+LAST
   # Client connects, should receive CSV with 3 fields
   ```

3. Test FULL_JSON mode:
   ```bash
   argus_server ib --mode FULL_JSON
   # Client connects, should receive JSON objects
   ```

4. Test Protocol 2 (default):
   ```bash
   argus_server ib
   # Should work as before
   ```

### Success Criteria

- ✅ All 4 modes implemented (ASK, ASK+BID+LAST, FULL_JSON, PROTOCOL_2)
- ✅ Mode selectable via command-line argument
- ✅ Each mode sends correct format
- ✅ Clients can parse data correctly
- ✅ Mode displayed at startup

---

## Phase 4: Implement Shortable Shares (High Priority)

**Priority:** HIGH  
**Estimated Effort:** 2-3 days  
**Impact:** Critical for short-selling strategies

### Current State

Python tracks shares available for short-selling via `ShortableSharesData` class. Swift has no equivalent.

**Impact:** Short sellers cannot determine availability before attempting to short.

### Implementation Plan

#### Step 1: Create ShortableSharesData Class

**File:** `argus_swift/Sources/ArgusServer/ShortableSharesData.swift`

```swift
import Foundation

/// Tracks shares available for short-selling for each contract
class ShortableSharesData {
    private var data: [Int: Int] = [:]  // conid -> shares
    private let lock = NSLock()
    
    func update(conid: Int, shares: Int) {
        lock.lock()
        defer { lock.unlock() }
        data[conid] = shares
    }
    
    func get(conid: Int) -> Int {
        lock.lock()
        defer { lock.unlock() }
        return data[conid] ?? 0
    }
    
    func getAll() -> [Int: Int] {
        lock.lock()
        defer { lock.unlock() }
        return data
    }
}
```

#### Step 2: Integrate with IBWss

```swift
class IBWss {
    private let shortableShares = ShortableSharesData()
    
    func handleMarketData(message: [String: Any]) {
        // ... existing parsing ...
        
        // Check for shortable shares field (field 588)
        if let shares = message["588"] as? Int {
            shortableShares.update(conid: conid, shares: shares)
        }
    }
    
    func getShortableShares(conid: Int) -> Int {
        return shortableShares.get(conid: conid)
    }
}
```

#### Step 3: Add to Protocol 2 Output

```swift
private func formatAsProtocol2(_ data: IBMarketData) -> Data? {
    // Get shortable shares for this contract
    let shortableShares = ws.getShortableShares(conid: data.conid)
    
    // Include in CSV fields
    let csvData = "\(bid),\(bidSize),\(ask),\(askSize),\(last),\(lastSize),\(shortableShares),\(timestamp),\(transmissionTime)"
    
    // ... rest of Protocol 2 formatting
}
```

#### Step 4: Add Interactive Command

```swift
case "12": showShortableShares()

private func showShortableShares() {
    let shares = ws.getAllShortableShares()
    
    print("\n=== Shortable Shares ===")
    if shares.isEmpty {
        print("No data available")
    } else {
        for (conid, count) in shares.sorted(by: { $0.key < $1.key }) {
            let symbol = caches[conid]?[IBKRFields.SYMBOL] as? String ?? "Unknown"
            print("  [\(conid)] \(symbol): \(count) shares")
        }
    }
}
```

### Testing Plan

1. Subscribe to a contract known to have shortable shares
2. Use interactive command 12 to view shortable shares data
3. Verify Protocol 2 packets include shortable shares field
4. Test with contracts that have 0 shortable shares (hard to borrow)

### Success Criteria

- ✅ Shortable shares tracked per contract
- ✅ Data included in Protocol 2 output
- ✅ Interactive command shows shortable shares
- ✅ Correctly handles 0 shares (hard to borrow)

---

## Phase 5: Enhanced Features (Medium Priority)

### 5.1 Notification System

**Effort:** 2-3 days

Implement macOS notifications for critical events using UserNotifications framework:

```swift
import UserNotifications

class NotificationManager {
    static let shared = NotificationManager()
    
    func requestPermission() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { granted, _ in
            print("[Notifications] Permission granted: \(granted)")
        }
    }
    
    func send(title: String, body: String) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        
        let request = UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request)
    }
}

// Use in IBWss:
NotificationManager.shared.send(title: "IBKR WebSocket", body: "Connection lost")
```

### 5.2 Account Ledger/Summary APIs

**Effort:** 2-3 days

Add comprehensive account data retrieval to IBNetworker:

```swift
func getAccountLedger(accountId: String) throws -> AccountLedger {
    let url = "https://api.ibkr.com/v1/api/portfolio/\(accountId)/ledger"
    // ... implement
}

func getAccountSummary(accountId: String) throws -> AccountSummary {
    let url = "https://api.ibkr.com/v1/api/portfolio/\(accountId)/summary"
    // ... implement
}
```

### 5.3 LockedSession Pattern

**Effort:** 1-2 days

Implement thread-safe HTTP session manager:

```swift
class LockedSession {
    private let session: URLSession
    private let lock = NSLock()
    
    func get(url: String) throws -> Data {
        lock.lock()
        defer { lock.unlock() }
        
        guard let requestURL = URL(string: url) else {
            throw NSError(domain: "LockedSession", code: -1, userInfo: [NSLocalizedDescriptionKey: "Invalid URL: \(url)"])
        }
        
        // Synchronous request using semaphore
        var result: Data?
        var error: Error?
        let semaphore = DispatchSemaphore(value: 0)
        
        let task = session.dataTask(with: requestURL) { data, _, err in
            result = data
            error = err
            semaphore.signal()
        }
        task.resume()
        semaphore.wait()
        
        if let error = error {
            throw error
        }
        
        guard let data = result else {
            throw NSError(domain: "LockedSession", code: -2, userInfo: [NSLocalizedDescriptionKey: "No data received"])
        }
        
        return data
    }
}
```

---

## Implementation Timeline

### Week 1: Critical Fixes
- **Days 1-3:** Implement interactive mode commands (Phase 1)
- **Days 4-5:** Begin disk caching implementation (Phase 2)

### Week 2: Core Features
- **Days 1-2:** Complete disk caching (Phase 2)
- **Days 3-5:** Implement dispatcher modes (Phase 3)

### Week 3: High Priority
- **Days 1-3:** Implement shortable shares (Phase 4)
- **Days 4-5:** Testing and bug fixes

### Week 4: Medium Priority & Polish
- **Days 1-2:** Notification system (Phase 5.1)
- **Days 3-4:** Account APIs (Phase 5.2)
- **Day 5:** Final testing and documentation

**Total Estimated Time:** 4 weeks for one developer

---

## Testing Strategy

### Unit Tests

Create test suite for each phase:

```swift
import XCTest

class IBDispatcherTests: XCTestCase {
    func testInteractiveCommands() {
        // Test each interactive command
    }
    
    func testCacheManager() {
        // Test cache save/load
    }
    
    func testDispatcherModes() {
        // Test each mode format
    }
    
    func testShortableShares() {
        // Test shortable shares tracking
    }
}
```

### Integration Tests

1. **Interactive Mode Test:** Run dispatcher, execute all commands, verify outputs
2. **Cache Persistence Test:** Restart dispatcher, verify cache loaded
3. **Mode Switching Test:** Test each dispatcher mode with real client
4. **Shortable Shares Test:** Verify field in Protocol 2 output

### Performance Tests

1. **Startup Time:** Measure with cold cache vs warm cache
2. **Memory Usage:** Monitor over 24 hours with active subscriptions
3. **API Load:** Count API calls before/after caching

---

## Success Criteria

### Minimum Viable Product (MVP)

To consider Swift IB **production-ready**:

- ✅ Interactive mode with 9+ functional commands
- ✅ Disk caching for contract searches
- ✅ Support for ASK, ASK+BID+LAST, FULL_JSON, PROTOCOL_2 modes
- ✅ Shortable shares tracking and output
- ✅ Feature parity reaches **85%+**
- ✅ No critical bugs in 1-week testing period

### Full Feature Parity (95%+)

For complete parity with Python:

- ✅ All MVP features
- ✅ Notification system
- ✅ Complete account ledger/summary APIs
- ✅ LockedSession pattern
- ✅ WebSocket message logging to file
- ✅ Comprehensive test coverage (80%+)
- ✅ Documentation updated

---

## Risk Mitigation

### Risk 1: IBKR API Changes

**Mitigation:** 
- Version check on startup
- Graceful degradation if API unavailable
- Comprehensive error logging

### Risk 2: Cache Corruption

**Mitigation:**
- JSON format (human-readable, debuggable)
- Validation on load
- Automatic cache clear if invalid
- Backup previous cache before overwrite

### Risk 3: Memory Leaks

**Mitigation:**
- Use Instruments to profile memory
- Limit stored WebSocket messages (1000 max)
- Regular testing with long-running instances
- ARC should handle most issues automatically

### Risk 4: Threading Issues

**Mitigation:**
- Use NSLock consistently
- Document all thread access points
- Test with Thread Sanitizer
- Follow Swift concurrency best practices

---

## Maintenance Plan

### Post-Implementation

1. **Weekly:** Monitor GitHub issues for Swift IB bugs
2. **Monthly:** Review Python changes for new features
3. **Quarterly:** Performance testing and optimization
4. **Yearly:** Major version sync with Python

### Version Compatibility

- Maintain compatibility with IBKR API version used by Python
- Document any divergence from Python implementation
- Keep feature comparison document updated

---

## Conclusion

Bringing Swift IB up to parity is **achievable in 4 weeks** for a dedicated developer. The critical path is:

1. Fix interactive mode (unlock debugging capabilities)
2. Implement disk caching (improve performance and reduce API load)
3. Add dispatcher modes (enable diverse clients)
4. Add shortable shares (enable short-selling strategies)

Once these 4 phases are complete, Swift IB will be **production-ready** at **85%+ feature parity**.

The remaining medium-priority features (notifications, enhanced account APIs, LockedSession) can be added incrementally based on user feedback and requirements.

**Key Takeaway:** The current 55% parity is primarily due to **incomplete implementation** rather than fundamental architectural issues. All gaps are addressable with focused development effort.
