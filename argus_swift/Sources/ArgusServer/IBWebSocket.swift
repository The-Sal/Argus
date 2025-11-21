import Foundation

/// IBKR WebSocket manager
/// Transcompiled from argus/ib/__init__.py IBWss class
class IBWss {
    private let url = "wss://www.interactivebrokers.co.uk/portal.proxy/v1/portal/ws"
    private let cookie: String
    private var ws: URLSessionWebSocketTask?
    private var session: URLSession?
    private var opened = false
    private var ready = false
    private var recv = 0

    let networker: IBNetworker
    private var contractCallbacks: [Int: (IBMarketData) -> Void] = [:]
    private var pnlCallbacks: [(AccountBalances) -> Void] = []
    private let streamLock = NSLock()
    private var protectedAssets = Set<Int>()
    private var subscribeCount = 0
    private let maxSubscriptions = 100

    init(cookie: String) {
        self.cookie = cookie
        self.networker = IBNetworker(cookie: cookie)
    }

    /// Start WebSocket connection
    func run() {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }

            let config = URLSessionConfiguration.default
            self.session = URLSession(configuration: config, delegate: nil, delegateQueue: nil)

            var request = URLRequest(url: URL(string: self.url)!)
            request.setValue(self.cookie, forHTTPHeaderField: "Cookie")
            request.setValue("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                           forHTTPHeaderField: "User-Agent")

            self.ws = self.session?.webSocketTask(with: request)
            self.ws?.resume()

            self.receiveMessage()
            self.startHeartbeat()
        }
    }

    private func receiveMessage() {
        ws?.receive { [weak self] result in
            guard let self = self else { return }

            switch result {
            case .success(let message):
                self.handleMessage(message)
                self.receiveMessage()  // Continue reading

            case .failure(let error):
                print("WebSocket error: \(error)")
                self.onClose()
            }
        }
    }

    private func handleMessage(_ message: URLSessionWebSocketTask.Message) {
        switch message {
        case .string(let text):
            recv += 1

            // Ignore heartbeat
            if text == "ech+hb" {
                return
            }

            guard let data = text.data(using: .utf8),
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                print("Received non-JSON message: \(text)")
                return
            }

            if !opened {
                opened = true
            }

            if recv == 2 {
                boot()
            }

            // Route message based on topic
            if let topic = json["topic"] as? String {
                if topic.contains("smd") {
                    handleMarketData(json)
                } else if topic.contains("spl") {
                    handleAccountPnL(json)
                } else if topic == "system" {
                    if let hb = json["hb"] as? Bool, hb {
                        return  // Heartbeat
                    }
                    if let success = json["success"] {
                        print("[IMPORTANT] Successfully connected to IBKR WebSocket as \(success)")
                        recv = 1
                    }
                }
            }

        case .data(let data):
            // Try to decode binary data as UTF-8 string
            if let text = String(data: data, encoding: .utf8) {
                recv += 1

                // Ignore heartbeat
                if text == "ech+hb" {
                    return
                }

                guard let jsonData = text.data(using: .utf8),
                      let json = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any] else {
                    print("Received non-JSON binary message: \(text)")
                    return
                }

                if !opened {
                    opened = true
                }

                if recv == 2 {
                    boot()
                }

                // Route message based on topic
                if let topic = json["topic"] as? String {
                    if topic.contains("smd") {
                        handleMarketData(json)
                    } else if topic.contains("spl") {
                        handleAccountPnL(json)
                    } else if topic == "system" {
                        if let hb = json["hb"] as? Bool, hb {
                            return  // Heartbeat
                        }
                        if let success = json["success"] {
                            print("[IMPORTANT] Successfully connected to IBKR WebSocket as \(success)")
                            recv = 1
                        }
                    }
                }
            } else {
                print("Received binary data that cannot be decoded as UTF-8: \(data.count) bytes")
            }
        @unknown default:
            break
        }
    }

    private func boot() {
        do {
            try networker.initialize()
        } catch {
            print("Failed to initialize networker: \(error)")
        }

        // Send stream subscription messages
        ws?.send(.string("sor+{}")) { _ in }
        ws?.send(.string("upl+{}")) { _ in }

        ready = true
    }

    /// Wait until WebSocket is ready
    func waitTillReady() {
        let startTime = Date()
        while !ready {
            Thread.sleep(forTimeInterval: 1)
            let elapsed = Date().timeIntervalSince(startTime)
            print("Waiting for WebSocket to be ready.. Time elapsed: \(String(format: "%.2f", elapsed)) seconds")
        }
    }

    /// Stream market data for a contract
    func streamMarketData(contractId: Int, callback: @escaping (IBMarketData) -> Void,
                         fields: [Int] = [IBKRFields.LAST_PRICE, IBKRFields.ASK_PRICE, IBKRFields.ASK_SIZE,
                                         IBKRFields.BID_PRICE, IBKRFields.BID_SIZE, IBKRFields.SHORTABLE_SHARES,
                                         IBKRFields.SYMBOL, IBKRFields.FORMATTED_UNREALIZED_PNL]) {
        streamLock.lock()
        defer { streamLock.unlock() }

        if subscribeCount >= maxSubscriptions {
            print("Maximum number of subscriptions reached: \(maxSubscriptions)")
            return
        }

        contractCallbacks[contractId] = callback

        if !protectedAssets.contains(contractId) {
            subscribeCount += 1
        }

        let fieldsStr = fields.map { String($0) }
        let fieldsJson: [String: Any] = ["fields": fieldsStr, "backout": true]

        if let jsonData = try? JSONSerialization.data(withJSONObject: fieldsJson),
           let jsonStr = String(data: jsonData, encoding: .utf8) {
            let msg = "smd+\(contractId)+\(jsonStr)"
            ws?.send(.string(msg)) { error in
                if let error = error {
                    print("Failed to subscribe to contract \(contractId): \(error)")
                }
            }
        }
    }

    /// Unsubscribe from market data
    func unsubscribeMarketData(contractId: Int) {
        streamLock.lock()
        defer { streamLock.unlock() }

        if protectedAssets.contains(contractId) {
            print("Cannot unsubscribe from protected asset: \(contractId)")
            return
        }

        contractCallbacks.removeValue(forKey: contractId)
        subscribeCount -= 1

        let msg = "umd+\(contractId){}"
        ws?.send(.string(msg)) { _ in }
    }

    /// Write protected assets that cannot be unsubscribed
    func writeProtectedAssets(_ assets: [Int]) {
        streamLock.lock()
        defer { streamLock.unlock() }

        protectedAssets = Set(assets)
    }

    /// Subscribe to portfolio updates
    func subscribeToPortfolio(callback: @escaping (AccountBalances) -> Void) {
        pnlCallbacks.append(callback)
    }

    private func handleMarketData(_ message: [String: Any]) {
        guard let conid = message["conid"] as? Int else {
            print("Market data message missing conid: \(message)")
            return
        }

        let conidEx = message["conidEx"] as? String
        let topic = message["topic"] as? String ?? "smd"
        let serverId = message["server_id"] as? String

        let marketData = IBMarketData(
            contractId: conid,
            serverId: serverId,
            contractExchange: conidEx,
            topic: topic,
            data: message
        )

        if let callback = contractCallbacks[conid] {
            callback(marketData)
        }
    }

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

    private func startHeartbeat() {
        DispatchQueue.global(qos: .background).async { [weak self] in
            while true {
                Thread.sleep(forTimeInterval: 10)
                guard let self = self else { return }

                if self.opened {
                    self.ws?.send(.string("ech+hb")) { _ in }
                } else {
                    Thread.sleep(forTimeInterval: 5)
                }
            }
        }
    }

    private func onClose() {
        print("WebSocket connection closed")
        opened = false
    }

    var isOpened: Bool {
        return opened
    }

    var isReady: Bool {
        return ready
    }

    var privateContracts: [Int] {
        return Array(protectedAssets)
    }

    /// Send a raw WebSocket message
    func sendMessage(_ message: String, completion: ((Error?) -> Void)? = nil) {
        ws?.send(.string(message)) { error in
            completion?(error)
        }
    }
}
