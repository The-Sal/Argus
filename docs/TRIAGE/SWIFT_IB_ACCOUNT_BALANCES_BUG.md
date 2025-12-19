# Swift IB AccountProvider Missing Account Balances

## Issue Summary

The Swift `AccountProvider` class is not receiving account balance updates from the IBKR WebSocket, even though all the infrastructure to handle these updates is in place. The Python version works correctly.

**Status:** The issue is caused by a missing WebSocket subscription message in the Swift `IBDispatcher` initialization.

---

## Root Cause Analysis

### What Should Happen (Python)

In `argus/ib/__init__.py` (lines 1176-1178), after initializing `AccountProvider`, the `MKTDispatcher` sends **two** subscription messages:

```python
self.ws.ws.send('upl+{}')      # Subscribe to portfolio updates
time.sleep(1)
self.ws.ws.send('spl+{}')      # Subscribe to PnL/account balance updates
```

### What Actually Happens (Swift)

In `argus_swift/Sources/ArgusServer/IB/IBDispatcher.swift` (lines 378-380), after initializing `AccountProvider`, only **one** subscription message is sent:

```swift
ws.sendMessage("upl+{}")        // Subscribe to portfolio updates
Thread.sleep(forTimeInterval: 1)
// MISSING: ws.sendMessage("spl+{}") ❌
```

---

## Why This Breaks Account Balances

The IBKR WebSocket API uses **topic-based message routing**:

| Topic | Routed To | Purpose |
|-------|-----------|---------|
| `smd` | `handleMarketData()` | Market data updates (prices, quotes) |
| `spl` | `handleAccountPnL()` | Account balance/PnL updates |
| `system` | System handler | Connection messages, heartbeats |

### Message Flow (Python - Working ✓)

```
1. AccountProvider.__init__
   └─> subscribeToPortfolio(callback)  [Registers callback]

2. MKTDispatcher sends: spl+{}
   └─> WebSocket server receives subscription request
       └─> Begins streaming messages with topic='spl'

3. WebSocket receives message with topic='spl'
   └─> handleAccountPnL() invoked
       └─> Calls all registered pnlCallbacks
           └─> AccountProvider._on_account_balances(data) executed
               └─> self._account_balances = data  [✓ Balances stored]
```

### Message Flow (Swift - Broken ❌)

```
1. AccountProvider.init
   └─> subscribeToPortfolio(callback)  [Registers callback]

2. IBDispatcher sends: upl+{} ONLY
   └─> WebSocket server receives subscription request
       └─> Streams messages with topic='upl'
       └─> Does NOT stream topic='spl' (never requested!)

3. WebSocket receives NO messages with topic='spl'
   └─> handleAccountPnL() NEVER called [❌]
       └─> pnlCallbacks NEVER invoked [❌]
           └─> AccountProvider.onAccountBalances() NEVER called [❌]
               └─> accountBalances stays nil [❌ No balances]
```

---

## Evidence: Infrastructure is Complete

The Swift code has **all the correct infrastructure**:

1. **PnL Callbacks Array** (`IBWebSocket.swift:16`)
   ```swift
   private var pnlCallbacks: [(AccountBalances) -> Void] = []
   ```

2. **Subscription Method** (`IBWebSocket.swift:242-244`)
   ```swift
   func subscribeToPortfolio(callback: @escaping (AccountBalances) -> Void) {
       pnlCallbacks.append(callback)
   }
   ```

3. **Message Handler** (`IBWebSocket.swift:269-278`)
   ```swift
   private func handleAccountPnL(_ message: [String: Any]) {
       do {
           let balances = try AccountBalances.fromDict(message)
           for callback in pnlCallbacks {
               callback(balances)
           }
       } catch {
           print("Failed to parse account balances: \(error)")
       }
   }
   ```

4. **Message Routing** (`IBWebSocket.swift:96-97`)
   ```swift
   else if topic.contains("spl") {
       handleAccountPnL(json)
   }
   ```

5. **AccountProvider Registration** (`IBAccountProvider.swift:67-69`)
   ```swift
   ibWss.subscribeToPortfolio { [weak self] balances in
       self?.onAccountBalances(balances)
   }
   ```

**Everything is there—except the subscription request.**

---

## Impact

- **What Breaks:** `currentAccountBalances` property always returns `nil`
- **Who's Affected:** Any client requesting account balances via AccountProvider
- **Debug Symptoms:**
  - `accountBalances` is never set in `onAccountBalances()`
  - `transmit(position: nil)` returns early because `accountBalances` is nil
  - Debug socket (port 9973) never sends account balance updates (only positions)

---

## The Fix

**File:** `argus_swift/Sources/ArgusServer/IB/IBDispatcher.swift`

**Location:** Around line 378 in the `selectAccount()` method

**Change:** Add the missing `spl+{}` subscription message

```swift
// Subscribe to portfolio updates
ws.sendMessage("upl+{}")
Thread.sleep(forTimeInterval: 1)

// MISSING IN CURRENT CODE:
ws.sendMessage("spl+{}")  // Subscribe to account balance/PnL updates
```

---

## Verification Checklist

After applying the fix:

- [ ] Code compiles without errors
- [ ] `AccountProvider` initializes successfully
- [ ] WebSocket message `spl+{}` is sent during `selectAccount()`
- [ ] `handleAccountPnL()` is called when balance updates arrive
- [ ] `AccountProvider.currentAccountBalances` is no longer nil
- [ ] Debug socket (port 9973) sends account balance updates
- [ ] Balance updates appear in debug stream: `~{"type": "account_balances", "data": {...}}L`

---

## Related Code Locations

| File | Lines | Purpose |
|------|-------|---------|
| `argus/ib/__init__.py` | 1176-1178 | Python reference implementation (correct) |
| `argus_swift/Sources/ArgusServer/IB/IBDispatcher.swift` | 360-386 | Swift implementation (missing spl+{}) |
| `argus_swift/Sources/ArgusServer/IB/IBWebSocket.swift` | 92-107 | Message routing logic |
| `argus_swift/Sources/ArgusServer/IB/IBAccountProvider.swift` | 25-75 | AccountProvider initialization |
| `argus/ib/__init__.py` | 556-751 | Python AccountProvider (reference) |

---

## Additional Notes

- This is a **transcompilation error** where the Swift version is an incomplete port of the Python code
- The bug doesn't involve any functional logic—just a missing initialization step
- The subscription message format (`spl+{}`) is defined by IBKR's WebSocket protocol
- Both `upl+{}` and `spl+{}` are needed for complete account streaming functionality
