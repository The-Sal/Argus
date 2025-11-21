import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// WebSocket manager for Capital.com streaming API
class CapitalComWss {
    private let baseURL = "wss://api-streaming-capital.backend-capital.com/connect"
    private var ws: URLSessionWebSocketTask?
    private let session: URLSession

    private var authTokens: CapitalComAuthTokens?
    private var wsStatus: WebSocketStatus = .disconnected

    private var subscriptions: [String: CapitalComSubscription] = [:]
    private let subscriptionLock = NSLock()

    // Application ping for keeping session alive (every 9 minutes)
    private let appPingInterval: TimeInterval = 9 * 60
    private var pingTimer: Timer?

    private var stopEvent = false
    private var reconnectAttempts = 0
    private let maxReconnectAttempts = 10
    private let initialReconnectDelay: TimeInterval = 5
    private let maxReconnectDelay: TimeInterval = 60

    init() {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        config.timeoutIntervalForResource = 300
        self.session = URLSession(configuration: config)
    }

    deinit {
        disconnect()
    }

    // MARK: - Connection Management

    /// Connect to Capital.com WebSocket with authentication tokens
    func connect(authTokens: CapitalComAuthTokens) {
        self.authTokens = authTokens
        wsStatus = .connecting

        guard let url = URL(string: "\(baseURL)?cst=\(authTokens.cst)&x-security-token=\(authTokens.xSecurityToken)") else {
            print("Invalid WebSocket URL")
            return
        }

        ws = session.webSocketTask(with: url)
        ws?.resume()

        wsStatus = .connected
        print("Capital.com WebSocket connected")

        // Start receiving messages
        receiveMessage()

        // Start application ping timer
        startPingTimer()

        // Resubscribe to all active subscriptions
        resubscribeAll()
    }

    /// Disconnect from WebSocket
    func disconnect() {
        wsStatus = .stopping
        stopEvent = true

        pingTimer?.invalidate()
        pingTimer = nil

        ws?.cancel(with: .goingAway, reason: nil)
        ws = nil

        wsStatus = .disconnected
        print("Capital.com WebSocket disconnected")
    }

    // MARK: - Subscription Management

    /// Subscribe to market data for an epic
    func subscribeToMarketData(
        epic: String,
        callback: @escaping (CapitalCom_CapitalComMKTDataLive) -> Void
    ) {
        subscriptionLock.lock()
        defer { subscriptionLock.unlock() }

        let streamKey = "/market/\(epic)"
        let subscription = CapitalComSubscription(
            epic: epic,
            dataType: .market,
            callback: callback
        )

        subscriptions[streamKey] = subscription

        // Send subscription message if connected
        if wsStatus == .connected {
            sendSubscriptionMessage(epic: epic, dataType: .market)
        }
    }

    /// Subscribe to OHLC data for an epic
    func subscribeToOHLC(
        epic: String,
        resolution: HistoricalPriceResolution,
        barType: OhlcBarType = .classic,
        callback: @escaping (CapitalCom_CapitalComMKTDataLive) -> Void
    ) {
        subscriptionLock.lock()
        defer { subscriptionLock.unlock() }

        let streamKey = "/ohlc/\(epic)/\(resolution.rawValue)/\(barType.rawValue)"
        let subscription = CapitalComSubscription(
            epic: epic,
            dataType: .ohlc,
            callback: callback,
            resolution: resolution,
            barType: barType
        )

        subscriptions[streamKey] = subscription

        // Send subscription message if connected
        if wsStatus == .connected {
            sendOHLCSubscriptionMessage(epic: epic, resolution: resolution, barType: barType)
        }
    }

    /// Unsubscribe from market data for an epic
    func unsubscribeFromMarketData(epic: String) {
        subscriptionLock.lock()
        defer { subscriptionLock.unlock() }

        let streamKey = "/market/\(epic)"
        subscriptions.removeValue(forKey: streamKey)

        if wsStatus == .connected {
            sendUnsubscriptionMessage(epic: epic, dataType: .market)
        }
    }

    /// Unsubscribe from OHLC data
    func unsubscribeFromOHLC(epic: String, resolution: HistoricalPriceResolution, barType: OhlcBarType = .classic) {
        subscriptionLock.lock()
        defer { subscriptionLock.unlock() }

        let streamKey = "/ohlc/\(epic)/\(resolution.rawValue)/\(barType.rawValue)"
        subscriptions.removeValue(forKey: streamKey)

        if wsStatus == .connected {
            sendOHLCUnsubscriptionMessage(epic: epic, resolution: resolution, barType: barType)
        }
    }

    /// Get list of currently subscribed epics
    func getSubscribedEpics() -> [String] {
        subscriptionLock.lock()
        defer { subscriptionLock.unlock() }

        return Array(Set(subscriptions.values.map { $0.epic }))
    }

    // MARK: - WebSocket Communication

    private func sendSubscriptionMessage(epic: String, dataType: WebsocketDataType) {
        guard let tokens = authTokens else { return }

        let destination: String
        let payload: [String: Any]

        switch dataType {
        case .market:
            destination = "marketData.subscribe"
            payload = ["epics": [epic]]
        case .ohlc:
            // Will be handled by sendOHLCSubscriptionMessage
            return
        }

        let message: [String: Any] = [
            "destination": destination,
            "correlationId": "sub-\(epic)-\(Int(Date().timeIntervalSince1970))",
            "cst": tokens.cst,
            "securityToken": tokens.xSecurityToken,
            "payload": payload
        ]

        sendJSON(message)
    }

    private func sendOHLCSubscriptionMessage(epic: String, resolution: HistoricalPriceResolution, barType: OhlcBarType) {
        guard let tokens = authTokens else { return }

        let message: [String: Any] = [
            "destination": "OHLCMarketData.subscribe",
            "correlationId": "sub-ohlc-\(epic)-\(Int(Date().timeIntervalSince1970))",
            "cst": tokens.cst,
            "securityToken": tokens.xSecurityToken,
            "payload": [
                "epics": [epic],
                "resolutions": [resolution.rawValue],
                "type": barType.rawValue
            ]
        ]

        sendJSON(message)
    }

    private func sendUnsubscriptionMessage(epic: String, dataType: WebsocketDataType) {
        guard let tokens = authTokens else { return }

        let destination = "marketData.unsubscribe"
        let message: [String: Any] = [
            "destination": destination,
            "correlationId": "unsub-\(epic)-\(Int(Date().timeIntervalSince1970))",
            "cst": tokens.cst,
            "securityToken": tokens.xSecurityToken,
            "payload": ["epics": [epic]]
        ]

        sendJSON(message)
    }

    private func sendOHLCUnsubscriptionMessage(epic: String, resolution: HistoricalPriceResolution, barType: OhlcBarType) {
        guard let tokens = authTokens else { return }

        let message: [String: Any] = [
            "destination": "OHLCMarketData.unsubscribe",
            "correlationId": "unsub-ohlc-\(epic)-\(Int(Date().timeIntervalSince1970))",
            "cst": tokens.cst,
            "securityToken": tokens.xSecurityToken,
            "payload": [
                "epics": [epic],
                "resolutions": [resolution.rawValue],
                "types": [barType.rawValue]
            ]
        ]

        sendJSON(message)
    }

    private func sendJSON(_ message: [String: Any]) {
        guard let jsonData = try? JSONSerialization.data(withJSONObject: message),
              let jsonString = String(data: jsonData, encoding: .utf8) else {
            print("Failed to serialize JSON message")
            return
        }

        let wsMessage = URLSessionWebSocketTask.Message.string(jsonString)
        ws?.send(wsMessage) { error in
            if let error = error {
                print("WebSocket send error: \(error)")
            }
        }
    }

    // MARK: - Application Ping

    private func startPingTimer() {
        DispatchQueue.main.async { [weak self] in
            self?.pingTimer = Timer.scheduledTimer(withTimeInterval: self?.appPingInterval ?? 540, repeats: true) { [weak self] _ in
                self?.sendApplicationPing()
            }
        }
    }

    private func sendApplicationPing() {
        guard let tokens = authTokens else { return }

        let pingMessage: [String: Any] = [
            "destination": "ping",
            "correlationId": "app-ping-\(Int(Date().timeIntervalSince1970))",
            "cst": tokens.cst,
            "securityToken": tokens.xSecurityToken
        ]

        sendJSON(pingMessage)
        print("Sent application-level WebSocket PING")
    }

    // MARK: - Message Handling

    private func receiveMessage() {
        ws?.receive { [weak self] result in
            switch result {
            case .success(let message):
                self?.handleMessage(message)
                self?.receiveMessage()  // Continue receiving

            case .failure(let error):
                print("WebSocket receive error: \(error)")
                self?.handleDisconnection()
            }
        }
    }

    private func handleMessage(_ message: URLSessionWebSocketTask.Message) {
        switch message {
        case .string(let text):
            handleTextMessage(text)
        case .data(let data):
            // Try to decode binary as UTF-8 string
            if let text = String(data: data, encoding: .utf8) {
                handleTextMessage(text)
            } else {
                print("Received non-UTF8 binary data")
            }
        @unknown default:
            print("Unknown WebSocket message type")
        }
    }

    private func handleTextMessage(_ text: String) {
        guard let data = text.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            print("Failed to parse WebSocket message as JSON")
            return
        }

        // Check for errors
        if let errorCode = json["errorCode"] as? String {
            let errorMessage = json["errorMessage"] as? String ?? "Unknown error"
            print("WebSocket error: \(errorMessage) (Code: \(errorCode))")

            if errorCode == "exceptions.security.authentication-failure" {
                print("Authentication failure, disconnecting")
                disconnect()
            }
            return
        }

        // Handle subscription confirmations
        if let status = json["status"] as? String,
           status == "OK",
           let payload = json["payload"] as? [String: Any],
           let subscriptions = payload["subscriptions"] as? [String: String] {
            print("Subscription confirmation: \(subscriptions)")
            return
        }

        // Handle ping responses
        if let destination = json["destination"] as? String,
           destination == "ping",
           let status = json["status"] as? String,
           status == "OK" {
            print("Application-level WebSocket PONG received")
            return
        }

        // Handle market data
        if let payload = json["payload"] as? [String: Any],
           let epic = payload["epic"] as? String {

            let serverDestination = json["destination"] as? String
            var streamKey: String?

            if serverDestination == "quote" {
                streamKey = "/market/\(epic)"
            } else if serverDestination == "ohlc.event" {
                if let resolution = payload["resolution"] as? String,
                   let barType = payload["type"] as? String {
                    streamKey = "/ohlc/\(epic)/\(resolution)/\(barType)"
                }
            }

            if let streamKey = streamKey {
                subscriptionLock.lock()
                let subscription = subscriptions[streamKey]
                subscriptionLock.unlock()

                if let subscription = subscription, subscription.active {
                    do {
                        let marketData = try CapitalCom_CapitalComMKTDataLive.fromWebSocketPayload(payload)
                        subscription.callback(marketData)
                    } catch {
                        print("Error parsing market data: \(error)")
                    }
                }
            }
        }
    }

    private func handleDisconnection() {
        if wsStatus == .stopping || stopEvent {
            return
        }

        wsStatus = .disconnected

        // Attempt reconnection with token refresh
        if reconnectAttempts < maxReconnectAttempts {
            let delay = min(initialReconnectDelay * pow(2.0, Double(reconnectAttempts)), maxReconnectDelay)
            print("WebSocket disconnected. Will refresh tokens and reconnect in \(delay) seconds...")

            DispatchQueue.global().asyncAfter(deadline: .now() + delay) { [weak self] in
                guard let self = self, !self.stopEvent else { return }
                self.reconnectAttempts += 1

                // Request token refresh from dispatcher before reconnecting
                print("Attempting to refresh authentication tokens...")
                // Note: Token refresh should be handled by dispatcher re-calling connect()
                // For now, just try reconnecting with existing tokens
                // TODO: Add callback to request fresh tokens from dispatcher

                if let tokens = self.authTokens {
                    self.connect(authTokens: tokens)
                } else {
                    print("No auth tokens available for reconnection")
                }
            }
        } else {
            print("Max reconnect attempts reached. WebSocket stopped.")
        }
    }

    private func resubscribeAll() {
        subscriptionLock.lock()
        let subs = Array(subscriptions.values)
        subscriptionLock.unlock()

        print("Resubscribing to \(subs.count) streams...")

        for sub in subs {
            if sub.active {
                switch sub.dataType {
                case .market:
                    sendSubscriptionMessage(epic: sub.epic, dataType: .market)
                case .ohlc:
                    if let resolution = sub.resolution, let barType = sub.barType {
                        sendOHLCSubscriptionMessage(epic: sub.epic, resolution: resolution, barType: barType)
                    }
                }
            }
        }
    }
}
