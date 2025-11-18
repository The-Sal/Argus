import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// WebSocket manager for Binance using native URLSession
/// Transcompiled from argus/binance/__init__.py: BinanceWss
class BinanceWss {
    private let apiKey: String?
    private let apiSecret: String?
    private let testnet: Bool

    // Track subscriptions: symbol -> callback
    private var subscriptions: [String: (BinanceMarketData) -> Void] = [:]

    // Track active WebSocket connections: symbol -> task
    private var activeStreams: [String: URLSessionWebSocketTask] = [:]

    // Lock for thread-safe operations
    private let lock = NSLock()

    // Running flag
    private var running = false

    // URLSession for WebSocket connections
    private var urlSession: URLSession!

    // Sacrificial subscription to work around first-subscription bug
    private var dummySubscriptionTask: URLSessionWebSocketTask?

    // Logger
    private let logger = Logger(subsystem: "BinanceWss")

    init(apiKey: String? = nil, apiSecret: String? = nil, testnet: Bool = false) {
        self.apiKey = apiKey
        self.apiSecret = apiSecret
        self.testnet = testnet

        // Configure URLSession
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 10
        config.timeoutIntervalForResource = 300
        self.urlSession = URLSession(configuration: config)

        // Check connectivity to Binance if not testnet
        if !testnet {
            do {
                try checkBinanceConnectivity()
            } catch {
                logger.error("Connectivity check failed: \(error)")
            }
        }

        logger.info("Initialized BinanceWss (testnet=\(testnet))")
    }

    /// Check connectivity to Binance production endpoints
    private func checkBinanceConnectivity() throws {
        let host = "stream.binance.com"
        let port: UInt16 = 9443

        logger.info("Checking connectivity to \(host):\(port)...")

        // Create socket for connectivity test
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else {
            throw BinanceError.connectivityFailed("Failed to create socket")
        }

        defer {
            #if canImport(Darwin)
            Darwin.close(fd)
            #elseif canImport(Glibc)
            Glibc.close(fd)
            #endif
        }

        // Set timeout
        var timeout = timeval(tv_sec: 5, tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))

        // Resolve hostname
        guard let hostInfo = gethostbyname(host) else {
            throw BinanceError.connectivityFailed("Cannot resolve hostname: \(host)")
        }

        var serverAddr = sockaddr_in()
        serverAddr.sin_family = sa_family_t(AF_INET)
        serverAddr.sin_port = port.bigEndian

        let addrList = hostInfo.pointee.h_addr_list
        guard let firstAddr = addrList?[0] else {
            throw BinanceError.connectivityFailed("No address found for \(host)")
        }

        memcpy(&serverAddr.sin_addr, firstAddr, Int(hostInfo.pointee.h_length))

        // Try to connect
        let connectResult = withUnsafePointer(to: &serverAddr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }

        if connectResult < 0 {
            let errorMessage = """
            Connection to \(host):\(port) failed.
            Binance production endpoint is unreachable from your network.

            Possible causes:
              - Firewall blocking cryptocurrency exchanges
              - ISP blocking Binance
              - Regional restrictions
              - Network connectivity issues

            Solutions:
              1. Use testnet: ./argus_server binance --testnet
              2. Try a different network (mobile hotspot, VPN)
              3. Check firewall settings
              4. Contact your network administrator
            """
            throw BinanceError.connectivityFailed(errorMessage)
        }

        logger.info("Successfully connected to \(host):\(port)")
    }

    /// Start the WebSocket manager
    func start() {
        lock.lock()
        defer { lock.unlock() }

        guard !running else { return }

        running = true
        logger.info("BinanceWss started")

        // Wait for initialization
        Thread.sleep(forTimeInterval: 0.5)

        // Create sacrificial subscription to work around python-binance bug
        // (First subscription never receives data)
        logger.info("Creating sacrificial first subscription to work around bug...")
        createSacrificialSubscription()

        logger.info("BinanceWss ready for subscriptions")
    }

    /// Create a dummy subscription that absorbs the first-subscription bug
    private func createSacrificialSubscription() {
        let symbol = "BNBUSDT"
        let streamURL = getStreamURL(for: symbol, stream: "ticker")

        guard let url = URL(string: streamURL) else {
            logger.warning("Failed to create sacrificial subscription URL")
            return
        }

        let task = urlSession.webSocketTask(with: url)
        task.resume()

        dummySubscriptionTask = task

        // Start receiving messages (but ignore them)
        receiveMessage(task: task, symbol: symbol, callback: { _ in
            // Dummy callback - ignore data
        })

        logger.info("Sacrificial subscription created")
    }

    /// Stop the WebSocket manager
    func stop() {
        lock.lock()
        defer { lock.unlock() }

        guard running else { return }

        // Stop sacrificial subscription
        dummySubscriptionTask?.cancel(with: .goingAway, reason: nil)
        dummySubscriptionTask = nil

        // Stop all active streams
        for (_, task) in activeStreams {
            task.cancel(with: .goingAway, reason: nil)
        }
        activeStreams.removeAll()
        subscriptions.removeAll()

        running = false
        logger.info("BinanceWss stopped")
    }

    /// Subscribe to real-time ticker data for a symbol
    func subscribeTicker(symbol: String, callback: @escaping (BinanceMarketData) -> Void) {
        lock.lock()

        let upperSymbol = symbol.uppercased()

        if subscriptions[upperSymbol] != nil {
            logger.warning("Already subscribed to \(upperSymbol)")
            lock.unlock()
            return
        }

        // Store subscription info first (before WebSocket connects)
        subscriptions[upperSymbol] = callback
        lock.unlock()

        // Attempt subscription outside of lock
        attemptSubscription(symbol: upperSymbol, callback: callback)
    }

    /// Attempt to subscribe with retry logic
    private func attemptSubscription(
        symbol: String,
        callback: @escaping (BinanceMarketData) -> Void,
        retryCount: Int = 0
    ) {
        let maxRetries = 5
        let retryWait: TimeInterval = 3.0

        logger.info("Starting ticker socket for \(symbol) (attempt \(retryCount + 1))...")

        let streamURL = getStreamURL(for: symbol, stream: "ticker")

        guard let url = URL(string: streamURL) else {
            logger.error("Invalid URL for \(symbol): \(streamURL)")
            return
        }

        let task = urlSession.webSocketTask(with: url)
        task.resume()

        lock.lock()
        activeStreams[symbol] = task
        lock.unlock()

        logger.info("Ticker socket started for \(symbol)")

        // Monitor if data is received
        var firstMessageReceived = false

        // Wrapper callback to track first message
        let wrappedCallback: (BinanceMarketData) -> Void = { marketData in
            if !firstMessageReceived {
                self.logger.info("First message received for \(symbol)")
                firstMessageReceived = true
            }
            callback(marketData)
        }

        // Start receiving messages
        receiveMessage(task: task, symbol: symbol, callback: wrappedCallback)

        // Monitor subscription health
        DispatchQueue.global().asyncAfter(deadline: .now() + retryWait) { [weak self] in
            guard let self = self else { return }

            if !firstMessageReceived {
                self.lock.lock()
                let stillSubscribed = self.subscriptions[symbol] != nil
                self.lock.unlock()

                if !stillSubscribed {
                    self.logger.info("Subscription to \(symbol) was cancelled, aborting retry")
                    return
                }

                self.logger.warning("No data received for \(symbol) after \(retryWait)s, retrying...")

                // Unsubscribe the failed attempt
                task.cancel(with: .goingAway, reason: nil)

                self.lock.lock()
                self.activeStreams.removeValue(forKey: symbol)
                self.lock.unlock()

                // Retry if under max retries
                if retryCount < maxRetries {
                    self.logger.info("Retrying subscription to \(symbol)...")
                    self.attemptSubscription(symbol: symbol, callback: callback, retryCount: retryCount + 1)
                } else {
                    self.logger.error("Failed to subscribe to \(symbol) after \(maxRetries) attempts")
                }
            } else {
                self.logger.info("Monitor for \(symbol): data received successfully")
            }
        }
    }

    /// Receive WebSocket messages
    private func receiveMessage(
        task: URLSessionWebSocketTask,
        symbol: String,
        callback: @escaping (BinanceMarketData) -> Void
    ) {
        task.receive { [weak self] result in
            guard let self = self else { return }

            switch result {
            case .success(let message):
                switch message {
                case .string(let text):
                    self.handleTickerMessage(text: text, symbol: symbol, callback: callback)
                case .data(let data):
                    if let text = String(data: data, encoding: .utf8) {
                        self.handleTickerMessage(text: text, symbol: symbol, callback: callback)
                    }
                @unknown default:
                    break
                }

                // Continue receiving
                self.receiveMessage(task: task, symbol: symbol, callback: callback)

            case .failure(let error):
                self.logger.error("WebSocket error for \(symbol): \(error)")
                // Try to reconnect
                self.lock.lock()
                let stillSubscribed = self.subscriptions[symbol] != nil
                self.lock.unlock()

                if stillSubscribed {
                    self.logger.info("Attempting to reconnect \(symbol)...")
                    DispatchQueue.global().asyncAfter(deadline: .now() + 2.0) {
                        self.attemptSubscription(symbol: symbol, callback: callback)
                    }
                }
            }
        }
    }

    /// Handle ticker message from Binance
    private func handleTickerMessage(text: String, symbol: String, callback: @escaping (BinanceMarketData) -> Void) {
        guard let jsonData = text.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any] else {
            logger.warning("Failed to parse ticker message for \(symbol)")
            return
        }

        // Check for error
        if let error = json["e"] as? String, error == "error" {
            logger.error("WebSocket error for \(symbol): \(json)")
            return
        }

        // Parse Binance ticker data
        guard let msgSymbol = json["s"] as? String,
              let bidStr = json["b"] as? String, let bid = Double(bidStr),
              let bidQtyStr = json["B"] as? String, let bidQty = Double(bidQtyStr),
              let askStr = json["a"] as? String, let ask = Double(askStr),
              let askQtyStr = json["A"] as? String, let askQty = Double(askQtyStr),
              let lastStr = json["c"] as? String, let last = Double(lastStr),
              let lastQtyStr = json["Q"] as? String, let lastQty = Double(lastQtyStr),
              let timestamp = json["E"] as? Int else {
            logger.warning("Incomplete ticker data for \(symbol)")
            return
        }

        let marketData = BinanceMarketData(
            symbol: msgSymbol,
            bid: bid,
            bidQty: bidQty,
            ask: ask,
            askQty: askQty,
            last: last,
            lastQty: lastQty,
            timestamp: timestamp
        )

        callback(marketData)
    }

    /// Unsubscribe from ticker data
    func unsubscribeTicker(symbol: String) {
        lock.lock()
        defer { lock.unlock() }

        let upperSymbol = symbol.uppercased()

        guard subscriptions[upperSymbol] != nil else {
            logger.warning("Not subscribed to \(upperSymbol)")
            return
        }

        if let task = activeStreams[upperSymbol] {
            task.cancel(with: .goingAway, reason: nil)
            activeStreams.removeValue(forKey: upperSymbol)
        }

        subscriptions.removeValue(forKey: upperSymbol)
        logger.info("Unsubscribed from \(upperSymbol)")
    }

    /// Get list of currently subscribed symbols
    func getSubscribedSymbols() -> [String] {
        lock.lock()
        defer { lock.unlock() }
        return Array(subscriptions.keys)
    }

    /// Get WebSocket stream URL for a symbol
    private func getStreamURL(for symbol: String, stream: String) -> String {
        let lowercaseSymbol = symbol.lowercased()
        if testnet {
            return "wss://testnet.binance.vision/ws/\(lowercaseSymbol)@\(stream)"
        } else {
            return "wss://stream.binance.com:9443/ws/\(lowercaseSymbol)@\(stream)"
        }
    }

    deinit {
        stop()
    }
}

/// Simple logger for BinanceWss
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
