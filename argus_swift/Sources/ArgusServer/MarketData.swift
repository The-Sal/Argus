import Foundation

/// Market data object compatible with Capital.com format
/// Transcompiled from argus/capital/__init__.py: CapitalComMKTDataLive
class CapitalComMKTDataLive: MarketDataTransferable {
    let symbol: String
    let bid: Double
    let bidSize: Double
    let ask: Double
    let askSize: Double
    let last: Double
    let lastSize: Double
    let timestamp: Int

    init(
        symbol: String,
        bid: Double,
        bidSize: Double,
        ask: Double,
        askSize: Double,
        last: Double,
        lastSize: Double,
        timestamp: Int? = nil
    ) {
        self.symbol = symbol
        self.bid = bid
        self.bidSize = bidSize
        self.ask = ask
        self.askSize = askSize
        self.last = last
        self.lastSize = lastSize
        self.timestamp = timestamp ?? Int(Date().timeIntervalSince1970 * 1000)
    }

    /// Returns CSV-formatted market data as Data for Protocol 2
    /// Format: bid,bid_size,ask,ask_size,last,last_size,timestamp,python_timestamp
    func transferable2() throws -> Data {
        let pythonTimestamp = Date().timeIntervalSince1970

        let values = [
            String(bid),
            String(bidSize),
            String(ask),
            String(askSize),
            String(last),
            String(lastSize),
            String(timestamp),
            String(pythonTimestamp)
        ]

        let csvString = values.joined(separator: ",")

        guard let data = csvString.data(using: .ascii) else {
            throw Protocol2Error.encodingError
        }

        return data
    }

    /// Returns dictionary representation for debugging
    func transferable() -> [String: Any] {
        return [
            "object": "MKTDataLive",
            "symbol": symbol,
            "bid": bid,
            "bid_size": bidSize,
            "ask": ask,
            "ask_size": askSize,
            "last": last,
            "last_size": lastSize,
            "timestamp": timestamp,
            "python_timestamp": Date().timeIntervalSince1970
        ]
    }
}

/// Market data object for Binance
/// Transcompiled from argus/binance/__init__.py: BinanceMarketData
class BinanceMarketData {
    let symbol: String
    let bid: Double
    let bidQty: Double
    let ask: Double
    let askQty: Double
    let last: Double
    let lastQty: Double
    let timestamp: Int

    init(
        symbol: String,
        bid: Double,
        bidQty: Double,
        ask: Double,
        askQty: Double,
        last: Double,
        lastQty: Double,
        timestamp: Int? = nil
    ) {
        self.symbol = symbol
        self.bid = bid
        self.bidQty = bidQty
        self.ask = ask
        self.askQty = askQty
        self.last = last
        self.lastQty = lastQty
        self.timestamp = timestamp ?? Int(Date().timeIntervalSince1970 * 1000)
    }

    /// Convert to CapitalComMKTDataLive for Protocol 2 transmission
    func toCapitalComFormat() -> CapitalComMKTDataLive {
        return CapitalComMKTDataLive(
            symbol: symbol,
            bid: bid,
            bidSize: bidQty,
            ask: ask,
            askSize: askQty,
            last: last,
            lastSize: lastQty,
            timestamp: timestamp
        )
    }

    var description: String {
        return "BinanceMarketData(symbol=\(symbol), bid=\(bid), ask=\(ask), last=\(last))"
    }
}

/// Binance-specific errors
enum BinanceError: Error {
    case connectivityFailed(String)
    case subscriptionFailed(String)
    case websocketError(String)
    case invalidResponse
}
