import Foundation

/// Binance data structures
/// Transcompiled from argus/binance/_classes.py

// MARK: - Depth Update (Order Book)

struct DepthUpdate {
    let e: String  // Event type
    let E: Int  // Event time (milliseconds)
    let s: String  // Symbol
    let U: Int  // First update ID
    let u: Int  // Final update ID
    let b: [[String]]  // Bids [price, quantity]
    let a: [[String]]  // Asks [price, quantity]

    static func fromDict(_ dict: [String: Any]) throws -> DepthUpdate {
        guard let e = dict["e"] as? String,
              let E = dict["E"] as? Int,
              let s = dict["s"] as? String,
              let U = dict["U"] as? Int,
              let u = dict["u"] as? Int,
              let b = dict["b"] as? [[String]],
              let a = dict["a"] as? [[String]] else {
            throw BinanceError.invalidResponse
        }

        return DepthUpdate(e: e, E: E, s: s, U: U, u: u, b: b, a: a)
    }
}

struct DepthStreamMessage {
    let stream: String
    let data: DepthUpdate
    let receivedAt: Double?
}

// MARK: - Aggregate Trade

struct AggTradeData {
    let e: String  // Event type
    let E: Int  // Event time (ms)
    let s: String  // Symbol
    let a: Int  // Aggregate trade ID
    let p: String  // Price
    let q: String  // Quantity
    let f: Int  // First trade ID
    let l: Int  // Last trade ID
    let T: Int  // Trade time (ms)
    let m: Bool  // Is buyer market maker
    let M: Bool  // Ignore (best price match)

    static func fromDict(_ dict: [String: Any]) throws -> AggTradeData {
        guard let e = dict["e"] as? String,
              let E = dict["E"] as? Int,
              let s = dict["s"] as? String,
              let a = dict["a"] as? Int,
              let p = dict["p"] as? String,
              let q = dict["q"] as? String,
              let f = dict["f"] as? Int,
              let l = dict["l"] as? Int,
              let T = dict["T"] as? Int,
              let m = dict["m"] as? Bool,
              let M = dict["M"] as? Bool else {
            throw BinanceError.invalidResponse
        }

        return AggTradeData(e: e, E: E, s: s, a: a, p: p, q: q, f: f, l: l, T: T, m: m, M: M)
    }
}

struct AggTradeMessage {
    let stream: String
    let data: AggTradeData
    let receivedAt: Double?
}

// MARK: - Kline (Candlestick)

struct KlineData {
    let t: Int  // Kline start time (ms)
    let T: Int  // Kline close time (ms)
    let s: String  // Symbol
    let i: String  // Interval
    let f: Int  // First trade ID
    let L: Int  // Last trade ID
    let o: String  // Open price
    let c: String  // Close price
    let h: String  // High price
    let l: String  // Low price
    let v: String  // Base asset volume
    let n: Int  // Number of trades
    let x: Bool  // Is kline closed?
    let q: String  // Quote asset volume
    let V: String  // Taker buy base asset volume
    let Q: String  // Taker buy quote asset volume
    let B: String  // Ignore

    static func fromDict(_ dict: [String: Any]) throws -> KlineData {
        guard let t = dict["t"] as? Int,
              let T = dict["T"] as? Int,
              let s = dict["s"] as? String,
              let i = dict["i"] as? String,
              let f = dict["f"] as? Int,
              let L = dict["L"] as? Int,
              let o = dict["o"] as? String,
              let c = dict["c"] as? String,
              let h = dict["h"] as? String,
              let l = dict["l"] as? String,
              let v = dict["v"] as? String,
              let n = dict["n"] as? Int,
              let x = dict["x"] as? Bool,
              let q = dict["q"] as? String,
              let V = dict["V"] as? String,
              let Q = dict["Q"] as? String,
              let B = dict["B"] as? String else {
            throw BinanceError.invalidResponse
        }

        return KlineData(t: t, T: T, s: s, i: i, f: f, L: L, o: o, c: c, h: h, l: l, v: v, n: n, x: x, q: q, V: V, Q: Q, B: B)
    }
}

struct KlineEventData {
    let e: String  // Event type
    let E: Int  // Event time (ms)
    let s: String  // Symbol
    let k: KlineData  // Kline data

    static func fromDict(_ dict: [String: Any]) throws -> KlineEventData {
        guard let e = dict["e"] as? String,
              let E = dict["E"] as? Int,
              let s = dict["s"] as? String,
              let kDict = dict["k"] as? [String: Any],
              let k = try? KlineData.fromDict(kDict) else {
            throw BinanceError.invalidResponse
        }

        return KlineEventData(e: e, E: E, s: s, k: k)
    }
}

struct KlineMessage {
    let stream: String
    let data: KlineEventData
    let receivedAt: Double?
}

// MARK: - Binance Market Data for Protocol 2

class Binance_CapitalComMKTDataLive: CapitalComMKTDataLive {
    /// Extension of CapitalComMKTDataLive to support Binance market data
    /// Conforms with transmit_mkt_data_with_protocol_2 function

    /// Create market data from Binance depth update (order book)
    static func fromBinanceDepth(
        symbol: String,
        depthUpdate: DepthUpdate,
        existingData: Binance_CapitalComMKTDataLive? = nil
    ) -> Binance_CapitalComMKTDataLive {
        // Extract top bid and ask from the order book
        var topBid: Double = 0.0
        var topBidSize: Double = 0.0
        var topAsk: Double = 0.0
        var topAskSize: Double = 0.0

        if !depthUpdate.b.isEmpty {
            topBid = Double(depthUpdate.b[0][0]) ?? 0.0
            topBidSize = Double(depthUpdate.b[0][1]) ?? 0.0
        }

        if !depthUpdate.a.isEmpty {
            topAsk = Double(depthUpdate.a[0][0]) ?? 0.0
            topAskSize = Double(depthUpdate.a[0][1]) ?? 0.0
        }

        // If we have existing trade data, use it; otherwise use mid price
        let lastPrice: Double
        let lastSize: Double

        if let existing = existingData, existing.last > 0 {
            lastPrice = existing.last
            lastSize = existing.lastSize
        } else {
            lastPrice = (topBid > 0 && topAsk > 0) ? (topBid + topAsk) / 2 : 0.0
            lastSize = 0.0
        }

        return Binance_CapitalComMKTDataLive(
            symbol: symbol.uppercased(),
            bid: topBid,
            bidSize: topBidSize,
            ask: topAsk,
            askSize: topAskSize,
            last: lastPrice,
            lastSize: lastSize,
            timestamp: depthUpdate.E
        )
    }

    /// Create or update market data from Binance aggregate trade
    static func fromBinanceTrade(
        symbol: String,
        tradeData: AggTradeData,
        existingData: Binance_CapitalComMKTDataLive? = nil
    ) -> Binance_CapitalComMKTDataLive {
        let lastPrice = Double(tradeData.p) ?? 0.0
        let lastSize = Double(tradeData.q) ?? 0.0

        // If we have existing depth data, preserve it and just update the last trade
        if let existing = existingData {
            return Binance_CapitalComMKTDataLive(
                symbol: symbol.uppercased(),
                bid: existing.bid,
                bidSize: existing.bidSize,
                ask: existing.ask,
                askSize: existing.askSize,
                last: lastPrice,
                lastSize: lastSize,
                timestamp: tradeData.T
            )
        } else {
            // No depth data, use trade price for bid/ask approximation
            return Binance_CapitalComMKTDataLive(
                symbol: symbol.uppercased(),
                bid: lastPrice,
                bidSize: 0.0,
                ask: lastPrice,
                askSize: 0.0,
                last: lastPrice,
                lastSize: lastSize,
                timestamp: tradeData.T
            )
        }
    }
}
