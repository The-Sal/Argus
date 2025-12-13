import Foundation

/// WebSocket manager for Binance using native URLSession
/// Transcompiled from argus/binance/__init__.py: BinanceWss (main branch)
/// Uses single combined stream endpoint with subscribe/unsubscribe messages
class BinanceWss {
    // Single WebSocket endpoint for all streams
    private let endpoint = "wss://stream.binance.com/stream"

    // WebSocket connection
    private var ws: URLSessionWebSocketTask?
    private var urlSession: URLSession!

    // Callbacks: symbol -> callback
    private var callbacks: [String: (AbstractBinanceType) -> Void] = [:]

    // Thread lock
    private let lock = NSLock()

    // Running flag
    private var running = false

    // Logger
    private let logger = Logger(subsystem: "BinanceWss")

    // Configuration
    private var configs: [String: Bool]

    // Message statistics
    private var statsStamps: [Double] = []

    init(configs: [String: Bool]? = nil) {
        self.configs =
            configs ?? [
                "auto_dump": true,
                "total_message_statistics": true,
                "show_me_charts": false,  // Disabled for Swift
            ]

        // Configure URLSession
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        config.timeoutIntervalForResource = 600
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.urlCache = nil

        self.urlSession = URLSession(configuration: config, delegate: nil, delegateQueue: nil)

        logger.info("BinanceWss initialized")
    }

    /// Initialize WebSocket connection
    func initWebSocket() {
        guard let url = URL(string: endpoint) else {
            logger.error("Invalid WebSocket endpoint: \(endpoint)")
            return
        }

        ws = urlSession.webSocketTask(with: url)
        ws?.resume()

        logger.info("WebSocket connection opened to \(endpoint)")

        // Start receiving messages
        receiveMessage()

        // Start statistics showcase
        if configs["total_message_statistics"] == true {
            startStatisticsShowcase()
        }
    }

    /// Receive WebSocket messages
    private func receiveMessage() {
        ws?.receive { [weak self] result in
            guard let self = self else { return }

            switch result {
            case .success(let message):
                switch message {
                case .string(let text):
                    self.handleMessage(text)
                case .data(let data):
                    if let text = String(data: data, encoding: .utf8) {
                        self.handleMessage(text)
                    }
                @unknown default:
                    break
                }

                // Continue receiving
                self.receiveMessage()

            case .failure(let error):
                self.logger.error("WebSocket error: \(error)")
                // Try to reconnect
                DispatchQueue.global().asyncAfter(deadline: .now() + 2.0) {
                    self.logger.info("Attempting to reconnect...")
                    self.initWebSocket()
                }
            }
        }
    }

    /// Handle incoming WebSocket message
    private func handleMessage(_ text: String) {
        // Update statistics
        statsStamps.append(Date().timeIntervalSince1970)

        guard let jsonData = text.data(using: .utf8),
            let json = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any]
        else {
            logger.warning("Failed to parse message")
            return
        }

        // Get stream type
        guard let messageType = json["stream"] as? String else {
            logger.warning("Malformed message received: no stream field")
            return
        }

        // Parse stream type: "symbol@streamType"
        let components = messageType.split(separator: "@")
        guard components.count >= 2 else {
            logger.warning("Malformed stream type: \(messageType)")
            return
        }

        let symbol = String(components[0])
        let streamType = components.dropFirst().joined(separator: "@")

        // Ignore miniTicker messages
        if streamType.contains("miniTicker") || streamType.contains("arr@1000ms") {
            return
        }

        // Parse based on stream type
        let abstractMsg: AbstractBinanceType?

        if streamType == "depth@100ms" {
            abstractMsg = parseDepthMessage(json, symbol: symbol)
        } else if streamType == "aggTrade" {
            abstractMsg = parseAggTradeMessage(json, symbol: symbol)
        } else if streamType == "kline_1s" {
            abstractMsg = parseKlineMessage(json, symbol: symbol)
        } else if streamType == "bookTicker" {
            abstractMsg = parseBookTickerMessage(json, symbol: symbol)
        } else {
            logger.warning("Unknown stream type: \(streamType)")
            return
        }

        // Call callback if registered
        if let msg = abstractMsg {
            lock.lock()
            let callback = callbacks[symbol]
            lock.unlock()

            if let callback = callback {
                callback(msg)
            } else {
                logger.warning("No callback registered for symbol: \(symbol)")
            }
        }
    }

    /// Parse depth (order book) message
    private func parseDepthMessage(_ json: [String: Any], symbol: String) -> AbstractBinanceType? {
        guard let data = json["data"] as? [String: Any],
            let depthUpdate = try? DepthUpdate.fromDict(data)
        else {
            return nil
        }

        let message = DepthStreamMessage(
            stream: json["stream"] as? String ?? "",
            data: depthUpdate,
            receivedAt: json["received_at"] as? Double
        )

        return AbstractBinanceType(idx: BinanceTypes.DEPTH_STREAM, obj: message)
    }

    /// Parse aggregate trade message
    private func parseAggTradeMessage(_ json: [String: Any], symbol: String) -> AbstractBinanceType?
    {
        guard let data = json["data"] as? [String: Any],
            let tradeData = try? AggTradeData.fromDict(data)
        else {
            return nil
        }

        let message = AggTradeMessage(
            stream: json["stream"] as? String ?? "",
            data: tradeData,
            receivedAt: json["received_at"] as? Double
        )

        return AbstractBinanceType(idx: BinanceTypes.AGG_TRADE, obj: message)
    }

    /// Parse kline message
    private func parseKlineMessage(_ json: [String: Any], symbol: String) -> AbstractBinanceType? {
        guard let data = json["data"] as? [String: Any],
            let klineData = try? KlineEventData.fromDict(data)
        else {
            return nil
        }

        let message = KlineMessage(
            stream: json["stream"] as? String ?? "",
            data: klineData,
            receivedAt: json["received_at"] as? Double
        )

        return AbstractBinanceType(idx: BinanceTypes.KLINE, obj: message)
    }

    /// Parse book ticker message
    private func parseBookTickerMessage(_ json: [String: Any], symbol: String)
        -> AbstractBinanceType?
    {
        guard let bookTicker = try? BookTicker.fromDict(json) else {
            return nil
        }

        let message = BookTickerMessage(
            stream: json["stream"] as? String ?? "",
            data: bookTicker,
            receivedAt: json["received_at"] as? Double
        )

        return AbstractBinanceType(idx: BinanceTypes.BOOK_TICKER, obj: message)
    }

    /// Subscribe to a symbol with callback
    func subscribe(symbol: String, callback: @escaping (AbstractBinanceType) -> Void) {
        let lowercaseSymbol = symbol.lowercased()

        lock.lock()
        callbacks[lowercaseSymbol] = callback
        lock.unlock()

        // Craft subscription message
        let subscribeMsg: [String: Any] = [
            "method": "SUBSCRIBE",
            "params": [
                "\(lowercaseSymbol)@aggTrade",
                "\(lowercaseSymbol)@depth@100ms",
                "\(lowercaseSymbol)@kline_1s",
                "\(lowercaseSymbol)@bookTicker",
            ],
            "id": 1,
        ]

        guard let jsonData = try? JSONSerialization.data(withJSONObject: subscribeMsg),
            let jsonString = String(data: jsonData, encoding: .utf8)
        else {
            logger.error("Failed to create subscription message")
            return
        }

        // Send subscription message
        let message = URLSessionWebSocketTask.Message.string(jsonString)
        ws?.send(message) { [weak self] error in
            if let error = error {
                self?.logger.error("Failed to send subscription: \(error)")
            } else {
                self?.logger.info("Subscribed to \(symbol)")
            }
        }
    }

    /// Unsubscribe from a symbol
    func unsubscribe(symbol: String) {
        let lowercaseSymbol = symbol.lowercased()

        lock.lock()
        callbacks.removeValue(forKey: lowercaseSymbol)
        lock.unlock()

        // Craft unsubscribe message
        let unsubscribeMsg: [String: Any] = [
            "method": "UNSUBSCRIBE",
            "params": [
                "\(lowercaseSymbol)@aggTrade",
                "\(lowercaseSymbol)@depth@100ms",
                "\(lowercaseSymbol)@kline_1s",
                "\(lowercaseSymbol)@bookTicker",
            ],
            "id": 1,
        ]

        guard let jsonData = try? JSONSerialization.data(withJSONObject: unsubscribeMsg),
            let jsonString = String(data: jsonData, encoding: .utf8)
        else {
            logger.error("Failed to create unsubscribe message")
            return
        }

        let message = URLSessionWebSocketTask.Message.string(jsonString)
        ws?.send(message) { [weak self] error in
            if let error = error {
                self?.logger.error("Failed to send unsubscribe: \(error)")
            } else {
                self?.logger.info("Unsubscribed from \(symbol)")
            }
        }
    }

    /// Start statistics showcase
    private func startStatisticsShowcase() {
        let statisticsInterval: TimeInterval = 10

        DispatchQueue.global(qos: .utility).async { [weak self] in
            while true {
                Thread.sleep(forTimeInterval: statisticsInterval)

                guard let self = self else { break }

                if self.configs["total_message_statistics"] == true {
                    let now = Date().timeIntervalSince1970
                    let cutoff = now - statisticsInterval

                    self.lock.lock()
                    let count = self.statsStamps.filter { $0 >= cutoff }.count
                    self.statsStamps = self.statsStamps.filter { $0 >= cutoff }
                    self.lock.unlock()

                    let avgPerSec = Double(count) / statisticsInterval
                    print(
                        "[STATISTICS] Received \(count) messages in the last \(Int(statisticsInterval)) seconds (avg: \(String(format: "%.2f", avgPerSec)) msgs/sec)"
                    )
                }
            }
        }
    }

    /// Get list of subscribed symbols
    func getSubscribedSymbols() -> [String] {
        lock.lock()
        defer { lock.unlock() }
        return Array(callbacks.keys)
    }

    /// Stop WebSocket
    func stop() {
        ws?.cancel(with: .goingAway, reason: nil)
        ws = nil
        logger.info("WebSocket stopped")
    }

    deinit {
        stop()
    }
}

/// Binance message types
struct BinanceTypes {
    static let DEPTH_STREAM = "depth_stream"
    static let AGG_TRADE = "agg_trade"
    static let KLINE = "kline"
    static let BOOK_TICKER = "book_ticker"
}

/// Abstract wrapper for Binance message types
struct AbstractBinanceType {
    let idx: String
    let obj: Any
}

/// Simple logger
struct Logger {
    let subsystem: String

    func info(_ message: String) {
        print("[\(subsystem)] INFO: \(message)")
    }

    func warning(_ message: String) {
        print("[\(subsystem)] WARNING: \(message)")
    }

    func error(_ message: String) {
        print("[\(subsystem)] ERROR: \(message)")
    }
}
