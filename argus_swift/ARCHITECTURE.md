# Argus Swift Architecture Guide

**For developers continuing the Swift transcompilation effort**

## Table of Contents

1. [Overview](#overview)
2. [Core Design Patterns](#core-design-patterns)
3. [Key Data Structures](#key-data-structures)
4. [Protocol 2 Implementation](#protocol-2-implementation)
5. [Module Architecture](#module-architecture)
6. [Transcompilation Progress](#transcompilation-progress)
7. [Adding New Modules](#adding-new-modules)
8. [Testing Strategy](#testing-strategy)

---

## Overview

This is an ongoing effort to transcompile the Argus Python codebase to Swift. The goal is **pure transcompilation** - maintaining the same architecture, data flow, and behavior as the Python implementation while leveraging Swift's type safety and performance.

### Design Philosophy

1. **Protocol-oriented architecture** - Use Swift protocols to replicate Python's duck typing
2. **Type safety without rigidity** - Leverage Swift's strong typing while preserving Python's flexibility
3. **Zero external dependencies** - Use native Swift/Foundation APIs wherever possible
4. **Cross-platform compatibility** - Support both macOS and Linux (where feasible)

### What's Been Transcompiled

- ✅ **argus/binance** - Complete Binance market data dispatcher
- ✅ **argus/ib** - Complete Interactive Brokers market data dispatcher with AccountProvider
- ✅ **argus/ib/forecast** - Complete Interactive Brokers forecast contracts dispatcher
- ⬜ **argus/capitalcom** - Capital.com integration (pending)
- ⬜ **argus/oanda** - OANDA integration (pending)
- ⬜ **argus/core** - Core utilities and helpers (pending)

---

## Core Design Patterns

### 1. Socket Abstraction Pattern

**Problem**: Python's socket flexibility (passing both real sockets and fake objects) doesn't translate directly to Swift.

**Solution**: Protocol-based socket abstraction defined in `SocketProtocol.swift`.

```swift
protocol ArgusSocket: AnyObject {
    var idx: String { get set }
    func sendall(_ data: Data) throws
    func close()
}
```

**Implementations**:

- **RealSocket** - Wraps POSIX file descriptors for actual TCP connections
- **FakeSocket** - Accepts a callback closure instead of network I/O

**Why this matters**: Allows polymorphic handling of sockets throughout the codebase, enabling both real client connections and internal callbacks without type casting hell.

**Example usage**:
```swift
// Can accept either RealSocket or FakeSocket
func subscribeToSymbol(symbol: String, client: ArgusSocket) {
    // Works with both types transparently
    clients.append(client)
}

// Create fake socket for manual subscriptions
let manualSocket = FakeSocket { data in
    // Handle data internally without network
}
```

---

### 2. Message Type System

**Problem**: Python uses dynamic typing for Binance message types (depth, trade, kline, etc.).

**Solution**: Type-safe enum + protocol pattern in `BinanceWebSocket.swift`.

```swift
enum BinanceTypes: Int {
    case DEPTH_STREAM = 0
    case AGG_TRADE = 1
    case KLINE = 2
}

struct AbstractBinanceType {
    let idx: BinanceTypes  // Message type
    let obj: Any          // Actual data (DepthStreamMessage, AggTradeMessage, etc.)
}
```

**Why this matters**: Preserves Python's message routing flexibility while adding compile-time type safety. You can switch on message types and safely cast to the appropriate struct.

**Example usage**:
```swift
func handleMessage(_ msg: AbstractBinanceType) {
    switch msg.idx {
    case .DEPTH_STREAM:
        guard let depthMsg = msg.obj as? DepthStreamMessage else { return }
        // Process depth update

    case .AGG_TRADE:
        guard let tradeMsg = msg.obj as? AggTradeMessage else { return }
        // Process trade
    }
}
```

---

### 3. Data Merging Pattern

**Problem**: Binance sends depth (order book) and trade data separately, but clients need unified market data.

**Solution**: Cache-based merging in `MKTDispatcher.swift`.

```swift
private var symbolDataCache: [String: Binance_CapitalComMKTDataLive] = [:]

func binanceCallback(symbol: String, msg: AbstractBinanceType) {
    let existingData = symbolDataCache[symbol]  // Get cached data

    let marketData: Binance_CapitalComMKTDataLive
    if msg.idx == .DEPTH_STREAM {
        // Update order book, preserve trade data from cache
        marketData = Binance_CapitalComMKTDataLive.fromBinanceDepth(
            symbol: symbol,
            depthUpdate: depthMsg.data,
            existingData: existingData  // Merge with cached trade data
        )
    } else if msg.idx == .AGG_TRADE {
        // Update trade data, preserve order book from cache
        marketData = Binance_CapitalComMKTDataLive.fromBinanceTrade(
            symbol: symbol,
            tradeData: tradeMsg.data,
            existingData: existingData  // Merge with cached depth data
        )
    }

    symbolDataCache[symbol] = marketData  // Update cache
}
```

**Why this matters**: Critical pattern for any exchange integration. Most exchanges send order book and trade data separately, so this caching/merging pattern will be reused in Capital.com, OANDA, etc.

---

### 4. Thread Safety Pattern

**Problem**: Multiple async tasks (client listener, health checker, WebSocket callbacks) access shared state.

**Solution**: NSLock with defer-based unlocking.

```swift
private let threadLock = NSLock()

func subscribeToSymbol(symbol: String, client: ArgusSocket) {
    threadLock.lock()
    defer { threadLock.unlock() }  // Guaranteed unlock even if function throws

    // Modify shared state safely
    symbolToClients[symbol]?.append(client)
}
```

**Why this matters**: Swift's value semantics help, but reference types (classes, arrays of classes) need explicit locking. Always use `defer` to ensure unlock happens.

---

## Key Data Structures

### Market Data Hierarchy

```
MarketDataTransferable (protocol)
    ↓
CapitalComMKTDataLive (base class)
    ↓
Binance_CapitalComMKTDataLive (Binance-specific extension)
```

**MarketDataTransferable** (`MarketData.swift`)
```swift
protocol MarketDataTransferable {
    var symbol: String { get }
    func transferable2() throws -> Data  // Protocol 2 encoding
}
```

**CapitalComMKTDataLive** (`MarketData.swift`)
- Base market data format compatible with Capital.com
- Fields: `bid`, `bidSize`, `ask`, `askSize`, `last`, `lastSize`, `timestamp`
- Implements Protocol 2 CSV encoding

**Binance_CapitalComMKTDataLive** (`BinanceClasses.swift`)
- Factory methods for creating from Binance messages
- `fromBinanceDepth()` - Extract top bid/ask from order book
- `fromBinanceTrade()` - Extract last price/size from trade

**When to extend**:
- For Capital.com: Create `CapitalCom_CapitalComMKTDataLive` with factory methods for Capital.com message types
- For OANDA: Create `OANDA_CapitalComMKTDataLive` with factory methods for OANDA message types

---

### Binance Message Structures

All in `BinanceClasses.swift`, directly transcompiled from `argus/binance/_classes.py`:

```swift
// Order book update
struct DepthUpdate {
    let e: String      // Event type
    let E: Int         // Event time (ms)
    let s: String      // Symbol
    let U, u: Int      // Update IDs
    let b: [[String]]  // Bids [price, quantity]
    let a: [[String]]  // Asks [price, quantity]
}

struct DepthStreamMessage {
    let stream: String
    let data: DepthUpdate
    let receivedAt: Double?
}

// Aggregate trade
struct AggTradeData {
    let e: String  // Event type
    let E: Int     // Event time
    let s: String  // Symbol
    let a: Int     // Trade ID
    let p: String  // Price
    let q: String  // Quantity
    let T: Int     // Trade time
    let m: Bool    // Is buyer market maker
}

struct AggTradeMessage {
    let stream: String
    let data: AggTradeData
    let receivedAt: Double?
}

// Kline (candlestick)
struct KlineData {
    let t, T: Int  // Start/end time
    let s: String  // Symbol
    let i: String  // Interval
    let o, c, h, l: String  // OHLC prices
    let v: String  // Volume
    let n: Int     // Trade count
    let x: Bool    // Is closed
    // ... more fields
}

struct KlineEventData {
    let e: String
    let E: Int
    let s: String
    let k: KlineData
}

struct KlineMessage {
    let stream: String
    let data: KlineEventData
    let receivedAt: Double?
}
```

**Parsing pattern**:
```swift
static func fromDict(_ dict: [String: Any]) throws -> DepthUpdate {
    guard let e = dict["e"] as? String,
          let E = dict["E"] as? Int,
          // ... validate all fields
    else {
        throw BinanceError.invalidResponse
    }
    return DepthUpdate(e: e, E: E, ...)
}
```

**Why this structure**: Preserves Python's dict-based JSON parsing while adding type safety. The `fromDict` pattern is reusable for any exchange's JSON messages.

---

## Protocol 2 Implementation

Protocol 2 is Argus's custom TCP streaming format for market data. **This is exchange-agnostic and will be reused for all integrations.**

### Packet Format

```
~<packet-length><symbol-length>|<symbol><data>L
```

Example: `~42|7|BTCUSDT98450.5,10.2,98451.0,8.5,98450.75,0.5,1234567890,1234567890.123L`

### Encoding (`Protocol2Utils.swift`)

```swift
func transmitMarketDataWithProtocol2<T: MarketDataTransferable>(_ marketData: T) throws -> Data {
    let dataBytes = try marketData.transferable2()  // CSV: "bid,bidSize,ask,..."
    let symbol = marketData.symbol
    let symbolBytes = symbol.data(using: .ascii)!

    let symbolLength = symbolBytes.count
    let dataLength = dataBytes.count
    let packetLength = 1 + String(symbolLength).count + 1 + symbolLength + dataLength + 1

    var packet = Data()
    packet.append("~".data(using: .ascii)!)
    packet.append(String(packetLength).data(using: .ascii)!)
    packet.append(String(symbolLength).data(using: .ascii)!)
    packet.append("|".data(using: .ascii)!)
    packet.append(symbolBytes)
    packet.append(dataBytes)
    packet.append("L".data(using: .ascii)!)

    return packet
}
```

### Decoding (`Protocol2Utils.swift`)

```swift
class Protocol2Parser {
    let decodingOrder: [String]

    func parse(_ packetBytes: Data) throws -> [String: Any] {
        // Extract symbol and data
        // Parse CSV data according to decodingOrder
        // Return dictionary: ["symbol": "BTCUSDT", "bid": 98450.5, ...]
    }
}
```

**Usage**:
```swift
// Encoding (server -> client)
let packet = try transmitMarketDataWithProtocol2(marketData)
try client.sendall(packet)

// Decoding (client-side)
let parser = Protocol2Parser(decodingOrder: ["bid", "bidSize", "ask", ...])
let parsed = try parser.parse(receivedPacket)
print(parsed["bid"])  // 98450.5
```

---

## Module Architecture

### BinanceWebSocket.swift

**Purpose**: Manages single WebSocket connection to Binance combined stream.

**Key methods**:

```swift
class BinanceWss {
    private let endpoint = "wss://stream.binance.com/stream"
    private var ws: URLSessionWebSocketTask?
    private var callbacks: [String: (AbstractBinanceType) -> Void] = [:]

    // Subscribe to symbol with callback
    func subscribe(symbol: String, callback: @escaping (AbstractBinanceType) -> Void)

    // Unsubscribe from symbol
    func unsubscribe(symbol: String)

    // Get currently subscribed symbols
    func getSubscribedSymbols() -> [String]

    // Internal: Handle incoming WebSocket messages
    private func handleMessage(_ text: String)
}
```

**Subscription flow**:
1. `subscribe("BTCUSDT", callback)` is called
2. Sends JSON to WebSocket: `{"method": "SUBSCRIBE", "params": ["btcusdt@depth@100ms", "btcusdt@aggTrade", "btcusdt@kline_1s"]}`
3. Stores callback in `callbacks["BTCUSDT"]`
4. When message arrives, routes to callback based on symbol

**Important**: Binance uses lowercase symbols in stream names but uppercase in message data. Normalize to uppercase for consistency.

---

### MKTDispatcher.swift

**Purpose**: TCP server that accepts client connections and broadcasts market data.

**Architecture**:
```
┌─────────────────────────────────────────────────────────┐
│                  BinanceMKTDispatcher                   │
├─────────────────────────────────────────────────────────┤
│  - serverSocket: Int32  (TCP listener)                  │
│  - clients: [ArgusSocket]  (connected clients)          │
│  - symbolToClients: [String: [ArgusSocket]]             │
│  - symbolDataCache: [String: MarketData]                │
│  - ws: BinanceWss  (WebSocket connection)               │
└─────────────────────────────────────────────────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
  Client Listener      Health Checker      Binance Callback
  (accept conns)       (ping clients)      (handle data)
```

**Threading model**:
- **Main thread**: Initialization, interactive mode
- **Client listener thread**: Accepts incoming TCP connections
- **Client handler threads**: One per client, reads `add=`/`remove=` commands
- **Health checker thread**: Periodically pings clients with `$` byte
- **WebSocket thread**: URLSession handles internally

**Client communication**:
```
Client -> Server: "add=BTCUSDT\n"
Server -> Client: [Protocol 2 packets for BTCUSDT]

Client -> Server: "remove=BTCUSDT\n"
Server stops sending BTCUSDT data to this client
```

**Subscription management**:
```swift
private func subscribeToSymbol(symbol: String, client: ArgusSocket) {
    threadLock.lock()
    let needsSubscription = symbolToClients[symbol] == nil

    if needsSubscription {
        symbolToClients[symbol] = [client]
    } else {
        symbolToClients[symbol]?.append(client)
    }
    threadLock.unlock()

    if needsSubscription {
        // First client for this symbol - subscribe to Binance
        ws.subscribe(symbol: symbol) { [weak self] msg in
            self?.binanceCallback(symbol: symbol, msg: msg)
        }
    }
}
```

**Why it matters**: This pattern (lazy subscription on first client) reduces WebSocket bandwidth. Only subscribe to symbols that have active clients.

---

### 5. AccountProvider Pattern (IB-Specific)

**Problem**: Need to track live portfolio positions and calculate real-time PnL from market data updates.

**Solution**: AccountProvider class that combines REST API portfolio data with WebSocket market data streaming.

```swift
class AccountProvider {
    private let ibWss: IBWss
    private let ibNetworker: IBNetworker
    private var portfolio: [Int: STKPosition] = [:]  // conid -> position
    private var fakeSocket: FakeSocket  // Receives market data internally
    private var debugSocket: Int32  // Port 9973 for streaming updates

    init(ibWss: IBWss, ibNetworker: IBNetworker) throws {
        // Fetch initial positions via REST API
        self.accountPositions = try ibNetworker.fetchAccountPositions()

        // Setup FakeSocket to receive market data
        self.fakeSocket = FakeSocket { [weak self] data in
            self?.onMarketData(data)
        }

        // Start debug socket on port 9973
        setupDebugSocket()
    }
}
```

**Architecture**:
```
[IBWss] <---- [IBNetworker]
   |______________|_____________|
   |              |             |
   V              V             V
  [AccountProvider] <-----> [IBMKTDispatcher] <-----> [Clients]
       |
       V
  [Debug Socket:9973] -----> [Debug Clients]
```

**Why this matters**:
- Separates concerns: REST API for positions, WebSocket for real-time prices
- Uses FakeSocket pattern to receive market data without network overhead
- Provides debug socket for monitoring portfolio in real-time
- Automatically protects portfolio assets from unsubscription

**Debug Socket Format** (~JSON~L):
```json
~{"type": "position", "data": {"conid": 123, "unrealized_pnl": 150.25, ...}}L
~{"type": "account_balances", "data": {"net_liquidation": 10000.0, ...}}L
```

---

### 6. Binary WebSocket Message Handling (IB-Specific)

**Problem**: IBKR WebSocket sends both string and binary messages. Binary messages need special handling.

**Solution**: Dual message type handling with UTF-8 decoding fallback.

```swift
private func handleMessage(_ message: URLSessionWebSocketTask.Message) {
    switch message {
    case .string(let text):
        // Handle JSON string messages
        processJSONMessage(text)

    case .data(let data):
        // Try to decode binary as UTF-8 string
        if let text = String(data: data, encoding: .utf8) {
            processJSONMessage(text)
        } else {
            print("Received non-UTF8 binary data")
        }
    }
}
```

**Why this matters**: URLSession WebSocket distinguishes between string and binary frames. IBKR sends JSON as both types, requiring flexible handling.

---

### 7. Environment Configuration Pattern

**Problem**: Need secure way to pass credentials without command-line arguments.

**Solution**: .env file parser with priority chain: CLI args > .env file > system environment.

```swift
// EnvLoader.swift
class EnvLoader {
    static func load(path: String = ".env") -> [String: String] {
        // Parse KEY=VALUE format
        // Support comments (#)
        // Strip quotes from values
    }
}

// Priority chain in main.swift
let envVars = EnvLoader.load(path: ".env")
let cookie = args.cookie ?? envVars["IB_COOKIE"] ?? ProcessInfo.processInfo.environment["IB_COOKIE"]
```

**Why this matters**:
- Security: Credentials never in shell history
- Flexibility: Override .env with CLI args for testing
- Compatibility: Falls back to system environment variables

---

## Transcompilation Progress

### Completed: argus/binance

| Python File | Swift File | Status | Notes |
|-------------|-----------|--------|-------|
| `__init__.py` | `MKTDispatcher.swift` | ✅ Complete | Main dispatcher class |
| `_classes.py` | `BinanceClasses.swift` | ✅ Complete | Data structures |
| `_binance_websocket.py` | `BinanceWebSocket.swift` | ✅ Complete | WebSocket manager |
| `runtime.py` | `main.swift` | ✅ Complete | Entry point |

**Shared components**:
- `SocketProtocol.swift` - Socket abstraction (new for Swift)
- `Protocol2Utils.swift` - Protocol 2 encoding/decoding
- `MarketData.swift` - Base market data classes

---

### Completed: argus/ib (Interactive Brokers)

| Python File | Swift File | Status | Notes |
|-------------|-----------|--------|-------|
| `__init__.py` (MKTDispatcher) | `IBDispatcher.swift` | ✅ Complete | Main IB dispatcher (Protocol 2 only) |
| `__init__.py` (IBWss) | `IBWebSocket.swift` | ✅ Complete | IBKR WebSocket manager with binary support |
| `__init__.py` (IBNetworker) | `IBNetworker.swift` | ✅ Complete | REST API client for IBKR |
| `__init__.py` (AccountProvider) | `IBAccountProvider.swift` | ✅ Complete | Portfolio tracking & PnL streaming |
| `_ib_utils.py` | `IBClasses.swift` | ✅ Complete | IBKR data structures & field codes |
| `fields.py` | `IBFields.swift` | ✅ Complete | 100+ IBKR field code constants |

**IB-specific features**:
- REST API integration for authentication & account data
- Binary WebSocket message handling
- AccountProvider with debug socket (port 9973)
- Protected assets (portfolio positions never unsubscribed)
- .env file support for credentials
- Thread-safe session management

**Default port**: 9972

---

### Completed: argus/ib/forecast (IB Forecast Contracts)

| Python File | Swift File | Status | Notes |
|-------------|-----------|--------|-------|
| `forecast.py` (FXCDispatcher) | `IBForecastDispatcher.swift` | ✅ Complete | Forecast contracts dispatcher |
| `forecast.py` (FXCWss) | `IBForecastWebSocket.swift` | ✅ Complete | Forecast WebSocket (extends IBWss) |
| `forecast.py` (classes) | `IBForecastClasses.swift` | ✅ Complete | Forecast contract hierarchy |

**Forecast-specific features**:
- Three-tier contract hierarchy (Big/Mini/Micro)
- Market resolution detection
- Batch contract activation
- Special field set for forecast contracts

**Default port**: 9972

---

## IB Module Architecture

### IBNetworker.swift - REST API Client

**Purpose**: Thread-safe HTTP client for IBKR Client Portal API.

**Key components**:
```swift
class IBLockedSession {
    private let session: URLSession
    private let lock = NSLock()  // Thread-safe requests

    func get(url: String, params: [String: String]?) throws -> (Data, HTTPURLResponse)
    func post(url: String, json: [String: Any]?) throws -> (Data, HTTPURLResponse)
}

class IBNetworker {
    let session: IBLockedSession
    private var authenticated = false
    private var tradingAccountId: String?

    // Authentication flow
    func initialize() throws  // Sends tickle, auth status, ssodh/init
    func setTradingAccountId(_ accountId: String) throws

    // Data fetching
    func searchContract(contractName: String) throws -> [SearchResult]
    func getAllTradingAccountIds() throws -> [IBAccount]
    func fetchAccountPositions() throws -> [STKPosition]
    func getAccountLedger() throws -> [String: Any]

    // Background maintenance
    private func startAuthenticationChecker()  // Every 2 minutes
    private func startHeartbeat()  // Every 2 seconds (tickle)
}
```

**IBKR API endpoints**:
- `tickle` - Keep session alive
- `auth/status` - Check authentication state
- `auth/ssodh/init` - Initialize session
- `iserver/secdef/search` - Search contracts
- `portfolio/{accountId}/positions` - Get positions
- `portfolio/{accountId}/ledger` - Get account ledger

---

### IBWebSocket.swift - WebSocket Manager

**Purpose**: Manages WebSocket connection to IBKR for market data and PnL streaming.

**Key features**:
```swift
class IBWss {
    private let url = "wss://www.interactivebrokers.co.uk/portal.proxy/v1/portal/ws"
    private var ws: URLSessionWebSocketTask?
    private var ready = false
    let networker: IBNetworker

    // Market data subscriptions
    func streamMarketData(contractId: Int, callback: @escaping (IBMarketData) -> Void)
    func unsubscribeMarketData(contractId: Int)

    // Protected assets (portfolio positions)
    func writeProtectedAssets(_ assets: [Int])  // Never unsubscribe these

    // Portfolio PnL
    func subscribeToPortfolio(callback: @escaping (AccountBalances) -> Void)

    // WebSocket messaging
    func sendMessage(_ message: String, completion: ((Error?) -> Void)?)

    // Binary message handling
    private func handleMessage(_ message: URLSessionWebSocketTask.Message) {
        switch message {
        case .string(let text): // JSON string messages
        case .data(let data):   // Binary messages (decode as UTF-8)
        }
    }
}
```

**Message protocol**:
- Subscribe: `smd+{conid}+{"fields": [...], "backout": true}`
- Unsubscribe: `umd+{conid}{}`
- Portfolio PnL: `upl+{}`
- Heartbeat: `ech+hb`

**Topics**:
- `smd` - Streaming market data
- `spl` - Streaming portfolio PnL
- `system` - System messages (auth success, errors)

---

### IBAccountProvider.swift - Portfolio Tracking

**Purpose**: Combines REST API positions with WebSocket market data for real-time PnL.

**Architecture flow**:
```
1. Fetch positions via REST API (fetchAccountPositions)
2. Create FakeSocket to receive market data internally
3. Subscribe to portfolio PnL updates (upl+{})
4. For each position, subscribe to market data via IBMKTDispatcher
5. Calculate PnL: (current_price - avg_cost) × quantity
6. Stream updates to debug socket (port 9973)
```

**Key methods**:
```swift
class AccountProvider {
    init(ibWss: IBWss, ibNetworker: IBNetworker) throws {
        // Fetch initial positions from REST API
        self.accountPositions = try ibNetworker.fetchAccountPositions()

        // Create internal callback for market data
        self.fakeSocket = FakeSocket { [weak self] data in
            self?.onMarketData(data)
        }

        // Protect portfolio assets from unsubscription
        ibWss.writeProtectedAssets(Array(portfolio.keys))

        // Subscribe to portfolio PnL
        ibWss.subscribeToPortfolio { [weak self] balances in
            self?.onAccountBalances(balances)
        }

        // Start debug socket (port 9973)
        setupDebugSocket()
    }

    func requiredAssets() -> [Int]  // Returns portfolio contract IDs
    var socket: FakeSocket  // For IBMKTDispatcher to send data
}
```

**Debug socket output** (~JSON~L format):
```
~{"type": "position", "data": {"conid": 123, "symbol": "AAPL", "position": 100, "unrealized_pnl": 150.25}}L
~{"type": "account_balances", "data": {"net_liquidation": 50000.0, "daily_pnl": 250.50}}L
```

**Usage**:
```bash
# Monitor portfolio in real-time
python tests/debug_socket.py
# Connects to localhost:9973 and displays position/PnL updates
```

---

### IBDispatcher.swift - Main Dispatcher

**Purpose**: TCP server that accepts clients and streams IBKR market data (Protocol 2 only).

**Integration with AccountProvider**:
```swift
func selectAccountInteractive() {
    let accounts = try ws.networker.getAllTradingAccountIds()
    let selectedAccount = accounts[userChoice]

    try ws.networker.setTradingAccountId(selectedAccount.accountId)

    // Initialize AccountProvider after account is set
    accountProvider = try AccountProvider(ibWss: ws, ibNetworker: ws.networker)

    // Subscribe to all portfolio positions
    for conid in accountProvider.requiredAssets() {
        quickAdd(symbol: nil, conid: conid, client: accountProvider.socket)
    }

    // Subscribe to portfolio PnL updates
    ws.sendMessage("upl+{}")
}
```

**Why this integration matters**:
- Portfolio positions are automatically subscribed as "protected assets"
- Market data updates flow to AccountProvider via FakeSocket
- Real-time PnL calculations without additional network overhead
- Debug socket provides visibility into portfolio performance

---

### IBForecastDispatcher.swift - Forecast Contracts

**Purpose**: Specialized dispatcher for IBKR forecast contracts (prediction markets).

**Contract hierarchy**:
```swift
class FxContractBig {           // Top-level event (e.g., "Fed Rate Decision")
    let conid: Int
    var minis: [FxContractMini]
}

class FxContractMini {          // Outcome category (e.g., "Rate increase")
    var yes: FxContractMicro
    var no: FxContractMicro
}

class FxContractMicro {         // Individual contract (yes/no)
    var conid: Int?
    var strikeLabel: String?
    var marketData: IBMarketData?
}
```

**Market resolution flow**:
```swift
func activateMarket(conid: Int) throws {
    let big = try generateBigContract(conid: conid)

    // Resolve contract details
    for mini in big.minis {
        try resolveMini(mini.yes)
        try resolveMini(mini.no)
    }

    // Subscribe to market data for all micro contracts
    for mini in big.minis {
        if let yesConid = mini.yes.conid {
            ws.streamMarketData(contractId: yesConid, ...)
        }
        if let noConid = mini.no.conid {
            ws.streamMarketData(contractId: noConid, ...)
        }
    }
}
```

---

## Adding New Modules

### Example: Transcompiling argus/capitalcom

**Step 1**: Understand the Python structure
```
argus/capitalcom/
├── __init__.py          # Main dispatcher
├── _classes.py          # Capital.com data structures
├── _websocket.py        # Capital.com WebSocket
└── runtime.py           # Entry point
```

**Step 2**: Create Swift files in `Sources/ArgusServer/`
```
Sources/ArgusServer/
├── CapitalComClasses.swift      # Data structures
├── CapitalComWebSocket.swift    # WebSocket manager
├── CapitalComDispatcher.swift   # Dispatcher
└── main.swift                   # Update to support "argus_server capitalcom"
```

**Step 3**: Define message structures

```swift
// CapitalComClasses.swift

struct CapitalComTickData {
    let symbol: String
    let bid: String
    let ask: String
    let timestamp: Int

    static func fromDict(_ dict: [String: Any]) throws -> CapitalComTickData {
        // Parse Capital.com JSON
    }
}

struct CapitalComTickMessage {
    let data: CapitalComTickData
    let receivedAt: Double?
}

class CapitalCom_CapitalComMKTDataLive: CapitalComMKTDataLive {
    static func fromCapitalComTick(
        symbol: String,
        tickData: CapitalComTickData
    ) -> CapitalCom_CapitalComMKTDataLive {
        return CapitalCom_CapitalComMKTDataLive(
            symbol: symbol,
            bid: Double(tickData.bid) ?? 0.0,
            // ... map fields
        )
    }
}
```

**Step 4**: Implement WebSocket manager

```swift
// CapitalComWebSocket.swift

class CapitalComWss {
    private let endpoint: String  // Capital.com WebSocket URL
    private var ws: URLSessionWebSocketTask?
    private var callbacks: [String: (CapitalComTickMessage) -> Void] = [:]

    func subscribe(symbol: String, callback: @escaping (CapitalComTickMessage) -> Void) {
        // Send Capital.com-specific subscription message
    }

    private func handleMessage(_ text: String) {
        // Parse Capital.com JSON
        // Route to callbacks
    }
}
```

**Step 5**: Implement dispatcher (copy pattern from BinanceMKTDispatcher)

```swift
// CapitalComDispatcher.swift

class CapitalComMKTDispatcher {
    private var clients: [ArgusSocket] = []
    private var symbolToClients: [String: [ArgusSocket]] = [:]
    private var symbolDataCache: [String: CapitalCom_CapitalComMKTDataLive] = [:]
    private let ws: CapitalComWss

    // Copy patterns from BinanceMKTDispatcher:
    // - TCP server setup
    // - Client connection handling
    // - Subscription management
    // - Data merging and caching
    // - Protocol 2 transmission
}
```

**Step 6**: Update main.swift

```swift
func main() {
    let args = parseArguments(CommandLine.arguments)

    switch args.target {
    case "binance":
        let dispatcher = BinanceMKTDispatcher(host: host, port: port)
        dispatcher.interactiveMode()

    case "capitalcom":
        let dispatcher = CapitalComMKTDispatcher(host: host, port: port)
        dispatcher.interactiveMode()

    default:
        print("Unknown target")
    }
}
```

---

## Testing Strategy

### Unit Testing Pattern

Create `Tests/ArgusServerTests/` directory:

```swift
// Protocol2Tests.swift
import XCTest
@testable import ArgusServer

final class Protocol2Tests: XCTestCase {
    func testEncodingDecoding() throws {
        let marketData = CapitalComMKTDataLive(
            symbol: "BTCUSDT",
            bid: 98450.5,
            bidSize: 10.2,
            // ...
        )

        let packet = try transmitMarketDataWithProtocol2(marketData)

        let parser = Protocol2Parser(decodingOrder: ["bid", "bidSize", ...])
        let parsed = try parser.parse(packet)

        XCTAssertEqual(parsed["symbol"] as? String, "BTCUSDT")
        XCTAssertEqual(parsed["bid"] as? Double, 98450.5)
    }
}
```

### Integration Testing Pattern

Use FakeSocket for testing without network:

```swift
func testDispatcherWithFakeSocket() {
    var receivedData: [Data] = []

    let fakeSocket = FakeSocket { data in
        receivedData.append(data)
    }

    let dispatcher = BinanceMKTDispatcher(host: "localhost", port: 9999)
    dispatcher.subscribeToSymbol(symbol: "BTCUSDT", client: fakeSocket)

    // Simulate Binance message
    let mockMessage = AbstractBinanceType(idx: .DEPTH_STREAM, obj: mockDepthMessage)
    dispatcher.binanceCallback(symbol: "BTCUSDT", msg: mockMessage)

    XCTAssertEqual(receivedData.count, 1)
    // Verify Protocol 2 packet
}
```

### Manual Testing

```bash
# Terminal 1: Start server
cd argus_swift
swift build -c release
.build/release/argus_server binance

# Terminal 2: Test client (Python)
python3 -c "
import socket
sock = socket.socket()
sock.connect(('localhost', 9982))
sock.send(b'add=BTCUSDT\n')
while True:
    data = sock.recv(4096)
    print(repr(data))
"
```

---

## Common Patterns Reference

### Error Handling
```swift
enum CustomError: Error {
    case invalidData
    case connectionFailed(errno: Int32)
}

func riskyOperation() throws -> Data {
    guard condition else {
        throw CustomError.invalidData
    }
    return data
}
```

### Async Dispatch
```swift
DispatchQueue.global(qos: .userInitiated).async { [weak self] in
    guard let self = self else { return }
    // Background work
}
```

### Thread-Safe Access
```swift
private let lock = NSLock()

func safeModify() {
    lock.lock()
    defer { lock.unlock() }
    // Modify shared state
}
```

### JSON Parsing
```swift
guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
      let field = json["field"] as? String else {
    throw ParsingError.invalidJSON
}
```

### WebSocket Reading
```swift
func receiveMessage() {
    ws?.receive { [weak self] result in
        switch result {
        case .success(let message):
            if case .string(let text) = message {
                self?.handleMessage(text)
            }
            self?.receiveMessage()  // Continue reading

        case .failure(let error):
            print("WebSocket error: \(error)")
        }
    }
}
```

---

## Key Takeaways for Next Developer

1. **Don't fight Swift's type system** - Use protocols to replicate Python's flexibility
2. **Socket abstraction is critical** - Always use `ArgusSocket`, never raw file descriptors
3. **Cache and merge data** - Most exchanges send fragmented data that needs combining
4. **Thread safety matters** - Always lock when accessing shared collections
5. **Protocol 2 is reusable** - Don't reimplement, just extend `MarketDataTransferable`
6. **Follow the Binance pattern** - Dispatcher, WebSocket, Classes structure works well
7. **REST + WebSocket combo** - IB pattern shows how to combine REST API (positions) with WebSocket (live data)
8. **AccountProvider pattern** - Reusable for any broker requiring portfolio tracking
9. **Binary WebSocket handling** - Handle both string and binary frames with UTF-8 fallback
10. **Environment configuration** - .env files with priority chain (CLI > .env > system)

**IB-specific learnings**:
- URLSession WebSocket distinguishes string vs binary frames - handle both
- Use `Darwin.socket()` to disambiguate from instance properties
- IBKR requires active session maintenance (tickle every 2s, auth check every 2min)
- Protected assets prevent important subscriptions from being removed
- Debug sockets (port 9973) are invaluable for monitoring live systems

**Questions?** Check git history for detailed commit messages explaining design decisions.

**Next priorities**:
1. Transcompile argus/capitalcom
2. Add unit tests for Protocol 2
3. Add Linux compatibility (replace URLSession WebSocket)
4. Implement connection pooling for high-throughput scenarios
5. Add reconnection logic for WebSocket disconnections

---

## File Reference Guide

### Core Infrastructure
- `SocketProtocol.swift` - Socket abstraction (RealSocket, FakeSocket)
- `Protocol2Utils.swift` - Protocol 2 encoding/decoding
- `MarketData.swift` - Base market data classes
- `EnvLoader.swift` - .env file parser
- `main.swift` - Entry point with target routing

### Binance Module
- `BinanceClasses.swift` - Binance message structures
- `BinanceWebSocket.swift` - Binance WebSocket manager
- `MKTDispatcher.swift` - Binance dispatcher

### IB Module (Interactive Brokers)
- `IBFields.swift` - 100+ IBKR field code constants
- `IBClasses.swift` - IBKR data structures (IBMarketData, AccountBalances, STKPosition)
- `IBNetworker.swift` - IBKR REST API client (thread-safe HTTP)
- `IBWebSocket.swift` - IBKR WebSocket manager (binary message support)
- `IBAccountProvider.swift` - Portfolio tracking & PnL streaming (debug socket port 9973)
- `IBDispatcher.swift` - Main IB dispatcher (Protocol 2, AccountProvider integration)

### IB Forecast Module
- `IBForecastClasses.swift` - Forecast contract hierarchy (Big/Mini/Micro)
- `IBForecastWebSocket.swift` - Forecast WebSocket (extends IBWss)
- `IBForecastDispatcher.swift` - Forecast dispatcher

### Usage Examples

**Run Binance dispatcher**:
```bash
argus_server binance --port 9974
```

**Run IB dispatcher with .env**:
```bash
# Create .env file
echo "IB_COOKIE=your-cookie-here" > .env

# Run dispatcher
argus_server ib
# Select account interactively
# AccountProvider starts on port 9973
```

**Run IB Forecast dispatcher**:
```bash
argus_server ib-forecast --env-file /path/to/.env
```

**Monitor IB portfolio**:
```bash
python tests/debug_socket.py
# Connects to localhost:9973
# Shows real-time position and PnL updates
```
