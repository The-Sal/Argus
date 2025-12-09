# Swift Binance Module Triage

## Overview

This document provides a detailed triage of the Swift Binance implementation (in `argus-swift` branch) compared to the canonical Python implementation (in `main` branch, version 0.0.8+). It addresses Issue #19: "Binance – Argus Swift is Deprecated" and outlines the necessary changes to bring the Swift code up to parity.

---

## Background

### The Pre-0.0.8 Bug

Prior to version 0.0.8, the Argus Binance module had a critical architectural flaw:

**Multiple data types were being mixed within a single P2 (Protocol 2) message without clear distinction.**

The old implementation subscribed to and processed four different Binance WebSocket streams simultaneously:
1. `@depth@100ms` - Order book depth updates
2. `@aggTrade` - Aggregated trade data
3. `@kline_1s` - 1-second candlestick data
4. `@bookTicker` - Best bid/ask prices

All four data types were being converted to `Binance_CapitalComMKTDataLive` objects and transmitted as P2 packets. The problem was that **clients had no way to distinguish which data type they were receiving**, since P2 format doesn't include a data type identifier.

This caused issues such as:
- Depth updates (which don't include last trade prices) being mixed with trade data
- Inconsistent `timestamp` values (different streams use different timestamp semantics)
- Clients parsing data incorrectly because they assumed all packets were the same type

### The Fix in Python (0.0.8+)

The Python implementation in `main` branch (version 0.0.8+) resolved this by:

1. **Using ONLY the `bookTicker` stream** for P2 transmission to clients
2. **Keeping other streams for internal use** (logging, statistics) but not forwarding them
3. **Introducing the `BookTicker` dataclass** with proper parsing from Binance data
4. **Updating `BinanceMKTDispatcher._binance_callback()`** to only process `BinanceTypes.BOOK_TICKER` messages

This provides a clean, single-data-type stream:
- Best bid price and size
- Best ask price and size
- Last traded price (approximated from mid-price when no trade data exists)

---

## Current State Comparison

### Python Implementation (main branch, v0.0.8+)

**File: `argus/binance/__init__.py`**

Key characteristics:
- `BinanceTypes` enum includes: `DEPTH_STREAM`, `AGG_TRADE`, `KLINE`, `BOOK_TICKER`
- `BinanceWss._craft_msg()` subscribes to all four streams:
  ```python
  "params": [
      symbol+"@aggTrade",
      symbol+"@depth@100ms", 
      symbol+"@kline_1s",
      symbol+"@bookTicker"
  ]
  ```
- `BinanceWss._on_message()` parses all message types and wraps them in `AbstractBinanceType`
- **CRITICAL**: `BinanceMKTDispatcher._binance_callback()` only processes `BOOK_TICKER`:
  ```python
  if msg.idx == BinanceTypes.BOOK_TICKER:
      book_ticker: BookTicker = msg.obj
      market_data = Binance_CapitalComMKTDataLive.from_binance_book_ticker(
          symbol, book_ticker, existing_data
      )
  else:
      # Other message types - skip for now
      return
  ```

**File: `argus/binance/_classes.py`**

- Contains `BookTicker` dataclass with fields: `u`, `s`, `b`, `B`, `a`, `A`
- `BookTicker.from_dict()` properly parses Binance WebSocket response
- `Binance_CapitalComMKTDataLive.from_binance_book_ticker()` creates P2-compatible market data
- Old methods `from_binance_depth()` and `from_binance_trade()` are commented out

### Swift Implementation (argus-swift branch)

**File: `argus_swift/Sources/ArgusServer/BinanceWebSocket.swift`**

Based on the deprecation warning added in commit `edbfd9f`, the Swift implementation:
- Still subscribes to multiple streams
- Still processes and forwards all message types as P2
- **Has the same bug that was present in Python pre-0.0.8**

**File: `argus_swift/Sources/ArgusServer/BinanceClasses.swift`**

- Contains data classes for all Binance message types
- May not have the `BookTicker` class properly implemented
- May be missing `from_binance_book_ticker()` conversion method

**File: `argus_swift/Sources/ArgusServer/MKTDispatcher.swift`**

- The Binance callback likely processes all message types
- Needs to be updated to filter for `bookTicker` only

---

## Required Changes

### 1. Add/Update `BookTicker` Struct

**Location**: `BinanceClasses.swift`

The Swift code needs a `BookTicker` struct equivalent to the Python dataclass:

```swift
struct BookTicker {
    let u: Int           // order book updateId
    let s: String        // symbol
    let b: Decimal       // best bid price
    let B: Decimal       // best bid quantity
    let a: Decimal       // best ask price
    let A: Decimal       // best ask quantity
    
    static func fromDict(_ data: [String: Any]) throws -> BookTicker {
        guard let innerData = data["data"] as? [String: Any] else {
            throw BinanceError.parseError("Missing 'data' field")
        }
        
        guard let u = innerData["u"] as? Int,
              let s = innerData["s"] as? String,
              let bStr = innerData["b"] as? String,
              let BStr = innerData["B"] as? String,
              let aStr = innerData["a"] as? String,
              let AStr = innerData["A"] as? String,
              let b = Decimal(string: bStr),
              let B = Decimal(string: BStr),
              let a = Decimal(string: aStr),
              let A = Decimal(string: AStr) else {
            throw BinanceError.parseError("Failed to parse BookTicker fields")
        }
        
        return BookTicker(u: u, s: s, b: b, B: B, a: a, A: A)
    }
}
```

### 2. Add `fromBinanceBookTicker()` Method

**Location**: `BinanceClasses.swift` or market data conversion class

Add a method to convert `BookTicker` to P2-compatible market data:

```swift
extension MarketData {
    static func fromBinanceBookTicker(
        symbol: String,
        bookTicker: BookTicker,
        existingData: MarketData? = nil
    ) -> MarketData {
        let bidPrice = Double(truncating: bookTicker.b as NSDecimalNumber)
        let bidSize = Double(truncating: bookTicker.B as NSDecimalNumber)
        let askPrice = Double(truncating: bookTicker.a as NSDecimalNumber)
        let askSize = Double(truncating: bookTicker.A as NSDecimalNumber)
        
        // Use existing trade data if available, otherwise approximate with mid price
        let (lastPrice, lastSize, timestamp): (Double, Double, Int)
        if let existing = existingData, existing.last > 0 {
            lastPrice = existing.last
            lastSize = existing.lastSize
            timestamp = existing.timestamp
        } else {
            lastPrice = (bidPrice + askPrice) / 2.0
            lastSize = 0.0
            timestamp = 0
        }
        
        return MarketData(
            symbol: symbol.uppercased(),
            bid: bidPrice,
            bidSize: bidSize,
            ask: askPrice,
            askSize: askSize,
            last: lastPrice,
            lastSize: lastSize,
            timestamp: timestamp
        )
    }
}
```

### 3. Update Dispatcher Callback

**Location**: `MKTDispatcher.swift` (or equivalent Binance dispatcher file)

The Binance callback must be updated to ONLY forward `bookTicker` data:

```swift
func binanceCallback(symbol: String, msg: AbstractBinanceType) {
    // CRITICAL: Only process bookTicker messages
    guard msg.idx == .bookTicker else {
        // Ignore other message types (depth, aggTrade, kline)
        return
    }
    
    guard let bookTicker = msg.obj as? BookTicker else {
        return
    }
    
    // Get or create market data cache for this symbol
    let existingData = symbolDataCache[symbol]
    let marketData = MarketData.fromBinanceBookTicker(
        symbol: symbol,
        bookTicker: bookTicker,
        existingData: existingData
    )
    
    // Update cache
    symbolDataCache[symbol] = marketData
    
    // Transmit to clients using Protocol 2
    let packet = transmitMarketDataWithProtocol2(marketData)
    sendToSubscribedClients(symbol: symbol, packet: packet)
}
```

### 4. Ensure Proper Message Type Enum

**Location**: `BinanceClasses.swift` or `BinanceWebSocket.swift`

Verify that `BinanceTypes` enum exists and includes `bookTicker`:

```swift
enum BinanceTypes: String {
    case depthStream = "depth_stream"
    case aggTrade = "agg_trade"
    case kline = "kline"
    case bookTicker = "book_ticker"
}
```

### 5. Update WebSocket Message Parser

**Location**: `BinanceWebSocket.swift`

Ensure the `bookTicker` stream type is properly detected:

```swift
func onMessage(_ message: String) {
    guard let data = message.data(using: .utf8),
          let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let stream = json["stream"] as? String else {
        return
    }
    
    let components = stream.split(separator: "@", maxSplits: 1)
    guard components.count == 2 else { return }
    
    let symbol = String(components[0])
    let streamType = String(components[1])
    
    let abstractType: AbstractBinanceType
    do {
        switch streamType {
        case "bookTicker":
            let bookTicker = try BookTicker.fromDict(json)
            abstractType = AbstractBinanceType(idx: .bookTicker, obj: bookTicker)
            
        case "depth@100ms":
            // Parse but don't forward to clients
            let depthUpdate = try DepthStreamMessage.fromDict(json)
            abstractType = AbstractBinanceType(idx: .depthStream, obj: depthUpdate)
            
        case "aggTrade":
            // Parse but don't forward to clients
            let aggTrade = try AggTradeMessage.fromDict(json)
            abstractType = AbstractBinanceType(idx: .aggTrade, obj: aggTrade)
            
        case "kline_1s":
            // Parse but don't forward to clients
            let kline = try KlineMessage.fromDict(json)
            abstractType = AbstractBinanceType(idx: .kline, obj: kline)
            
        default:
            return
        }
    } catch {
        print("[ERROR] Failed to parse \(streamType) message: \(error)")
        return
    }
    
    // Invoke callback - callback itself filters for bookTicker only
    if let callback = callbacks[symbol.lowercased()] {
        callback(abstractType)
    }
}
```

### 6. Remove Deprecation Warning

**Location**: `main.swift`

Once the changes are implemented, remove the deprecation warning that was added in commit `edbfd9f`:

```swift
// Remove this warning after implementing the fix
print("[WARNING] Binance module is deprecated...")
```

---

## Testing Requirements

After implementing the changes, verify:

1. **Only `bookTicker` data is transmitted**
   - Subscribe to BTCUSDT
   - Verify P2 packets only contain bid/ask data from `bookTicker`
   - Verify no depth updates or trade data is mixed in

2. **P2 format integrity**
   - Use `test_binance_proc_2.py` client to connect
   - Verify packets parse correctly
   - Verify field order matches: `bid`, `bid_size`, `ask`, `ask_size`, `last`, `last_size`, `timestamp`, `transmission_time`

3. **Timestamp behavior**
   - `timestamp` field will be `0` (as noted in README warning)
   - `transmission_time` should reflect Argus server time

4. **Multi-symbol subscriptions**
   - Subscribe to multiple symbols (BTCUSDT, ETHUSDT)
   - Verify each symbol's data is correctly isolated

---

## Implementation Priority

| Priority | Task | Complexity |
|----------|------|------------|
| 1 | Add `BookTicker` struct with parsing | Low |
| 2 | Add `fromBinanceBookTicker()` conversion | Medium |
| 3 | Update dispatcher callback to filter for `bookTicker` only | Medium |
| 4 | Verify WebSocket parser handles `bookTicker` correctly | Low |
| 5 | Remove deprecation warning | Low |
| 6 | Test end-to-end with Python client | Medium |

---

## Summary

The core issue is that the Swift implementation mixes multiple Binance data types in P2 messages. The fix requires:

1. **Ensure `BookTicker` is properly implemented** as a Swift struct
2. **Add conversion method** `fromBinanceBookTicker()` to create P2-compatible market data
3. **Update the dispatcher callback** to ONLY forward `bookTicker` data, ignoring all other stream types
4. **Test thoroughly** to verify single-data-type P2 streams

These changes will bring the Swift Binance module to parity with Python 0.0.8+ and resolve Issue #19.

---

## References

- Issue #19: [Binance – Argus Swift is Deprecated](https://github.com/The-Sal/Argus/issues/19)
- Python fix commit: `9b0116946e137b25326d82e3cf1aa78312abcb78` (Add BookTicker support and update README warnings)
- Python dispatcher fix: `c1bbf3a15ea74cfbc702618d5e814bb652500e0a` (Add BookTicker forwarding to BinanceMKTDispatcher)
- Swift deprecation commit: `edbfd9f7404f987650655bff06316ed0ceab5936`
