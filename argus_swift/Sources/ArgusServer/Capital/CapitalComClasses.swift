import Foundation

// MARK: - Enums

enum Environment: String {
    case demo = "demo"
    case live = "live"

    var baseURL: String {
        switch self {
        case .demo:
            return "https://demo-api-capital.backend-capital.com/api/v1"
        case .live:
            return "https://api-capital.backend-capital.com/api/v1"
        }
    }

    var wsURL: String {
        // Both demo and live use the same WebSocket endpoint
        return "wss://api-streaming-capital.backend-capital.com/connect"
    }
}

enum TradeDirection: String {
    case buy = "BUY"
    case sell = "SELL"
}

enum HistoricalPriceResolution: String {
    case minute = "MINUTE"
    case minute5 = "MINUTE_5"
    case minute10 = "MINUTE_10"
    case minute15 = "MINUTE_15"
    case minute30 = "MINUTE_30"
    case hour = "HOUR"
    case hour2 = "HOUR_2"
    case hour3 = "HOUR_3"
    case hour4 = "HOUR_4"
    case day = "DAY"
    case week = "WEEK"
    case month = "MONTH"
}

enum WebSocketStatus {
    case disconnected
    case connecting
    case connected
    case stopping
}

enum WebsocketDataType {
    case market
    case ohlc
}

enum OhlcBarType: String {
    case classic = "classic"
    case heikinAshi = "heikin-ashi"
}

// MARK: - Errors

enum CapitalComAPIError: Error {
    case invalidResponse
    case authenticationFailed
    case networkError(String)
    case invalidData
    case missingCredentials
    case subscriptionFailed
    case parsingError(String)
}

// MARK: - Market Data Classes

/// Represents live market data from Capital.com
class CapitalCom_CapitalComMKTDataLive: CapitalComMKTDataLive {
    /// Creates market data from Capital.com tick data
    static func fromCapitalComTick(
        epic: String,
        bid: Double,
        bidQty: Double,
        ofr: Double,
        ofrQty: Double,
        last: Double?,
        lastQty: Double?,
        timestamp: Int
    ) -> CapitalCom_CapitalComMKTDataLive {
        return CapitalCom_CapitalComMKTDataLive(
            symbol: epic,
            bid: bid,
            bidSize: bidQty,
            ask: ofr,
            askSize: ofrQty,
            last: last ?? 0.0,
            lastSize: lastQty ?? 0.0,
            timestamp: timestamp
        )
    }

    /// Creates market data from WebSocket message payload
    static func fromWebSocketPayload(_ payload: [String: Any]) throws -> CapitalCom_CapitalComMKTDataLive {
        guard let epic = payload["epic"] as? String,
              let bid = payload["bid"] as? Double,
              let bidQty = payload["bidQty"] as? Double,
              let ofr = payload["ofr"] as? Double,
              let ofrQty = payload["ofrQty"] as? Double,
              let timestamp = payload["timestamp"] as? Int else {
            throw CapitalComAPIError.parsingError("Missing required fields in market data payload")
        }

        let last = payload["last"] as? Double ?? 0.0
        let lastQty = payload["lastQty"] as? Double ?? 0.0

        return fromCapitalComTick(
            epic: epic,
            bid: bid,
            bidQty: bidQty,
            ofr: ofr,
            ofrQty: ofrQty,
            last: last,
            lastQty: lastQty,
            timestamp: timestamp
        )
    }
}

// MARK: - WebSocket Message Types

/// Represents a WebSocket subscription info
struct CapitalComSubscription {
    let epic: String
    let dataType: WebsocketDataType
    let callback: (CapitalCom_CapitalComMKTDataLive) -> Void
    let resolution: HistoricalPriceResolution?
    let barType: OhlcBarType?
    var active: Bool

    init(epic: String, dataType: WebsocketDataType,
         callback: @escaping (CapitalCom_CapitalComMKTDataLive) -> Void,
         resolution: HistoricalPriceResolution? = nil,
         barType: OhlcBarType? = nil) {
        self.epic = epic
        self.dataType = dataType
        self.callback = callback
        self.resolution = resolution
        self.barType = barType
        self.active = true
    }

    /// Returns the stream destination key for this subscription
    var streamDestinationKey: String {
        switch dataType {
        case .market:
            return "/market/\(epic)"
        case .ohlc:
            if let resolution = resolution, let barType = barType {
                return "/ohlc/\(epic)/\(resolution.rawValue)/\(barType.rawValue)"
            }
            return "/ohlc/\(epic)"
        }
    }
}

/// Represents authentication tokens for Capital.com API
struct CapitalComAuthTokens {
    let cst: String
    let xSecurityToken: String
}

/// Represents a market search result
struct MarketSearchResult {
    let epic: String
    let symbol: String
    let name: String
    let type: String
}

/// Represents account information
struct CapitalComAccount {
    let accountId: String
    let accountName: String?
    let balance: Double?
    let currency: String?
}
