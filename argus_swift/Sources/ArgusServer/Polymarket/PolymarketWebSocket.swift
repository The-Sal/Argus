import Foundation
// MARK: - Polymarket Direct WebSocket & API
// Transcompiled from argus/polymarket_direct/__init__.py

/// Enhanced Polymarket integration that fills the gaps left by the official py_clob_client.
/// This is a direct integration without requiring a Dispatcher.
class EnhancedPM {
    private let endpoint = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    private let eventsEndpoint = "https://gamma-api.polymarket.com/events?order=id&ascending=false&closed=false&limit=%d&offset=%d"

    private var ws: URLSessionWebSocketTask?
    private var session: URLSession
    private var idxToCallback: [String: (([String: Any]) -> Void)] = [:]
    private var wsMessages: [[String: Any]] = []
    private let threadLock = NSLock()
    private var wsErrors = 0
    private var internallyClosed = false
    private var marketOpenSemaphore = DispatchSemaphore(value: 0)

    // Constructor parameters (kept for compatibility)
    private let privateKey: String?
    private let proxyFunder: String?
    private let host: String
    private let chainId: Int
    private let orderBookDepth: Int
    private let dryMode: Bool

    init(privateKey: String? = nil,
         proxyFunder: String? = nil,
         host: String = "https://clob.polymarket.com",
         chainId: Int = 137,
         orderBookDepth: Int = 1,
         dryMode: Bool = false) {

        self.privateKey = privateKey
        self.proxyFunder = proxyFunder
        self.host = host
        self.chainId = chainId
        self.orderBookDepth = orderBookDepth
        self.dryMode = dryMode

        // Initialize URLSession
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        config.timeoutIntervalForResource = 300
        self.session = URLSession(configuration: config)

        // Initialize market WebSocket
        initMarketWs()
    }

    // MARK: - WebSocket Management

    private func initMarketWs() {
        guard let url = URL(string: endpoint) else {
            print("[EnhancedPM] Invalid WebSocket URL")
            return
        }

        marketOpenSemaphore = DispatchSemaphore(value: 0)
        ws = session.webSocketTask(with: url)
        ws?.resume()

        // Start receiving messages
        receiveMessage()

        // Simulate on_open callback
        DispatchQueue.global().asyncAfter(deadline: .now() + 1.0) { [weak self] in
            self?.onWsOpen()
        }
    }

    private func onWsOpen() {
        print("[EnhancedPM] Market WebSocket Opened")
        marketOpenSemaphore.signal()
    }

    private func receiveMessage() {
        ws?.receive { [weak self] result in
            guard let self = self else { return }

            switch result {
            case .success(let message):
                switch message {
                case .string(let text):
                    self.onWsMessage(text: text)
                case .data(let data):
                    if let text = String(data: data, encoding: .utf8) {
                        self.onWsMessage(text: text)
                    }
                @unknown default:
                    break
                }

                // Continue receiving
                self.receiveMessage()

            case .failure(let error):
                self.onError(error: error)
            }
        }
    }

    private func onWsMessage(text: String) {
        guard let data = text.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            print("[EnhancedPM] Failed to parse WebSocket message")
            return
        }

        threadLock.lock()
        wsMessages.append(json)
        threadLock.unlock()

        // Route to callbacks based on price_changes
        if let changes = json["price_changes"] as? [[String: Any]] {
            for change in changes {
                if let assetId = change["asset_id"] as? String {
                    threadLock.lock()
                    let callback = idxToCallback[assetId]
                    threadLock.unlock()

                    callback?(change)
                }
            }
        }
    }

    private func onError(error: Error) {
        print("[EnhancedPM] WebSocket Error: \(error)")
    }

    private func onWsClose() {
        if internallyClosed {
            return
        }

        print("[EnhancedPM] Market WebSocket Closed, attempting to reconnect... attempts: \(wsErrors)")
        wsErrors += 1

        if wsErrors > 5 {
            print("[EnhancedPM] Market WebSocket Failed to reconnect after \(wsErrors) attempts, giving up.")
            return
        }

        restartWsConnections()

        // Resubscribe to existing markets
        threadLock.lock()
        let assetIds = Array(idxToCallback.keys)
        threadLock.unlock()

        if !assetIds.isEmpty {
            subscribeToMarketData(assetIds: assetIds, callback: { _ in })
        }
    }

    // MARK: - Public API Methods

    /// Fetch Polymarket events from the Gamma API
    func fetchEvents(offset: Int = 0, limit: Int = 20, debugRawCallback: (([String: Any]) -> Void)? = nil) throws -> [PolymarketEvent] {
        let urlString = String(format: eventsEndpoint, limit, offset)
        guard let url = URL(string: urlString) else {
            throw PolymarketError.invalidURL
        }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 30

        let semaphore = DispatchSemaphore(value: 0)
        var responseData: Data?
        var responseError: Error?

        let task = session.dataTask(with: request) { data, response, error in
            responseData = data
            responseError = error
            semaphore.signal()
        }
        task.resume()

        _ = semaphore.wait(timeout: .now() + 30)

        if let error = responseError {
            throw error
        }

        guard let data = responseData else {
            throw PolymarketError.noData
        }

        guard let jsonArray = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] else {
            throw PolymarketError.invalidResponse
        }

        var events: [PolymarketEvent] = []
        for eventDict in jsonArray {
            debugRawCallback?(eventDict)

            if let event = PolymarketEvent.fromDict(eventDict) {
                events.append(event)
            }
        }

        return events
    }

    /// Restart WebSocket connections
    func restartWsConnections() {
        print("[EnhancedPM] Re-initializing market ws for subscription...")
        internallyClosed = true
        ws?.cancel()
        initMarketWs()
        startMarketWs()

        // Wait for connection to open
        _ = marketOpenSemaphore.wait(timeout: .now() + 5)
        internallyClosed = false
    }

    /// Subscribe to real-time market data via a callback function
    func subscribeToMarketData(assetIds: [String], callback: @escaping ([String: Any]) -> Void) {
        threadLock.lock()
        for assetId in assetIds {
            idxToCallback[assetId] = callback
        }
        threadLock.unlock()

        let message: [String: Any] = [
            "assets_ids": assetIds,
            "type": "market"
        ]

        sendMessage(message)
    }

    /// Unsubscribe from real-time market data
    func unsubscribeFromMarketData(assetIds: [String]) {
        threadLock.lock()
        for assetId in assetIds {
            idxToCallback[assetId] = { _ in }  // No-op callback
        }
        threadLock.unlock()
    }

    /// Start the market WebSocket (call this after initialization)
    func startMarketWs() {
        // WebSocket is already started in initMarketWs via resume()
        // This is kept for API compatibility
    }

    // MARK: - Private Helper Methods

    private func sendMessage(_ message: [String: Any]) {
        guard let jsonData = try? JSONSerialization.data(withJSONObject: message),
              let jsonString = String(data: jsonData, encoding: .utf8) else {
            print("[EnhancedPM] Failed to serialize message")
            return
        }

        let wsMessage = URLSessionWebSocketTask.Message.string(jsonString)
        ws?.send(wsMessage) { error in
            if let error = error {
                print("[EnhancedPM] Failed to send message: \(error)")
            }
        }
    }
}

// MARK: - Errors

enum PolymarketError: Error {
    case invalidURL
    case noData
    case invalidResponse
    case networkError(Error)
}
