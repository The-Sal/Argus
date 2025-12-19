import Foundation

/// Forecast WebSocket manager (FXCWss)
/// Extends IBWss for forecast-specific functionality
/// Transcompiled from argus/ib/forecast.py FXCWss class
class FXCWss: IBWss {
    private var topicRoutingTable: [String: ([ String: Any]) -> Void] = [:]
    private var connStatistics: [String: Any] = [:]
    private var socketPipeline: [SocketMessage] = []
    private var authenticatedSemaphore = DispatchSemaphore(value: 0)
    private var fxcConfigs: [String: Any] = [:]

    override init(cookie: String) {
        super.init(cookie: cookie)

        // Override URL for forecast endpoint
        setupForecastEndpoint()

        // Setup topic routing
        topicRoutingTable["act"] = actHandler
        topicRoutingTable["smd"] = { [weak self] msg in
            self?.handleMarketDataWrapper(msg)
        }
        topicRoutingTable["system"] = systemHandler
        topicRoutingTable["sts"] = statusHandler
        topicRoutingTable["spl"] = { [weak self] msg in
            self?.handleAccountPnLWrapper(msg)
        }

        // Initialize statistics
        connStatistics["topics_received"] = []
        connStatistics["messages_received"] = 0

        // Initialize configs
        fxcConfigs["Translate Socket Messages"] = true
        fxcConfigs["Realtime Logging"] = false
        fxcConfigs["Realtime Logging Interval"] = 1
        fxcConfigs["Pause realtime logging"] = false
    }

    private func setupForecastEndpoint() {
        // Override the URL to use forecast endpoint
        // This will be used in WebSocket connection setup
    }

    private func handleMarketDataWrapper(_ message: [String: Any]) {
        // Call parent's handleMarketData
        // This is a wrapper to fit the routing table signature
    }

    private func handleAccountPnLWrapper(_ message: [String: Any]) {
        // Call parent's handleAccountPnL
        // This is a wrapper to fit the routing table signature
    }

    private func actHandler(_ message: [String: Any]) {
        // Handle ACT topic messages
    }

    private func systemHandler(_ message: [String: Any]) {
        // Handle system messages
        if let success = message["success"] {
            print("[IMPORTANT] Successfully connected to IBKR FXC WebSocket as \(success)")
            authenticatedSemaphore.signal()
        }
    }

    private func statusHandler(_ message: [String: Any]) {
        // Handle status messages
    }

    /// Wait for authentication
    func waitForAuthentication(timeout: TimeInterval = 30) -> Bool {
        let deadline = DispatchTime.now() + timeout
        return authenticatedSemaphore.wait(timeout: deadline) == .success
    }

    override func streamMarketData(contractId: Int, callback: @escaping (IBMarketData) -> Void,
                                  fields: [Int] = []) {
        // Use full field set for forecast contracts
        let forecastFields = [
            IBKRFields.CHANGE_PERCENT, IBKRFields.CHANGE, IBKRFields.LAST_PRICE,
            IBKRFields.HIGH, IBKRFields.LOW, IBKRFields.OPEN, IBKRFields.CLOSE,
            IBKRFields.PRIOR_CLOSE, IBKRFields.WEEK_52_HIGH, IBKRFields.WEEK_52_LOW,
            IBKRFields.VOLUME, IBKRFields.VOLUME_LONG, IBKRFields.AVERAGE_VOLUME,
            IBKRFields.HISTORICAL_VOLATILITY_PERCENT, IBKRFields.OPTION_IMPLIED_VOL_PERCENT,
            IBKRFields.ASK_PRICE, IBKRFields.ASK_SIZE, IBKRFields.BID_PRICE,
            IBKRFields.BID_SIZE, IBKRFields.OPTION_OPEN_INTEREST, IBKRFields.SYMBOL
        ]

        super.streamMarketData(contractId: contractId, callback: callback, fields: forecastFields)
    }
}

/// Socket message for logging
struct SocketMessage {
    let content: String
    let origin: String
    let timestamp: Date

    func timestampStr() -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return formatter.string(from: timestamp)
    }
}
