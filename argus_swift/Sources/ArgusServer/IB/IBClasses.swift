import Foundation

/// IBKR-specific errors
enum IBError: Error {
    case invalidResponse
    case authenticationTimeout
    case authenticationError(String)
    case marketDataRefused
    case protectedAssetViolation
    case parseError(String)
}

/// Helper function to enforce currency conversion
/// Removes currency codes and symbols, converts to Double
func enforceCurrency(_ value: Any, raiseOnFail: Bool = true, fallback: Double = 0.0) -> Double {
    if let num = value as? Double {
        return num
    }
    if let num = value as? Int {
        return Double(num)
    }
    if var str = value as? String {
        // Remove currency prefix if starts with 'C'
        if str.hasPrefix("C") {
            str = String(str.dropFirst())
        }
        // Remove common currency symbols
        str = str.trimmingCharacters(in: .whitespaces)
            .replacingOccurrences(of: "$", with: "")
            .replacingOccurrences(of: "USD", with: "")
            .replacingOccurrences(of: ",", with: "")
            .trimmingCharacters(in: .whitespaces)

        if let num = Double(str) {
            return num
        }

        if raiseOnFail {
            print("Cannot convert value to Double: \(value)")
            return fallback
        }
        return fallback
    }

    if raiseOnFail {
        print("Unsupported type for currency conversion: \(type(of: value))")
    }
    return fallback
}

/// Market data container for IBKR WebSocket messages
class IBMarketData {
    let contractId: Int
    let serverId: String?
    let contractExchange: String?
    let topic: String
    var data: [String: Any]

    init(contractId: Int, serverId: String?, contractExchange: String?, topic: String, data: [String: Any]) {
        self.contractId = contractId
        self.serverId = serverId
        self.contractExchange = contractExchange
        self.topic = topic
        self.data = data
    }

    /// Get field value with default
    func get(_ field: Int, default defaultValue: Any? = nil, stripCommas: Bool = true, stringValues: Bool = true) -> Any? {
        let key1 = String(field)
        let key2 = field

        var a1 = data[key1]
        var a2 = data[String(key2)]

        // Treat empty strings as nil
        if let str = a1 as? String, str.isEmpty {
            a1 = nil
        }
        if let str = a2 as? String, str.isEmpty {
            a2 = nil
        }

        let finalValue = a1 ?? a2 ?? defaultValue

        guard var value = finalValue else {
            return defaultValue
        }

        if stripCommas, var str = value as? String {
            str = str.replacingOccurrences(of: ",", with: "")
            value = str
        }

        if stringValues {
            return "\(value)"
        }

        return value
    }
}

/// IBKR Account representation
struct IBAccount {
    let accountId: String
    let additionalData: [String: Any]

    static func fromDict(_ dict: [String: Any]) throws -> IBAccount {
        guard let accountId = dict["accountId"] as? String else {
            throw IBError.invalidResponse
        }
        return IBAccount(accountId: accountId, additionalData: dict)
    }
}

/// Account balances from PnL WebSocket messages
struct AccountBalances {
    let accountId: String
    let dailyPnl: Double
    let pnl: Double
    let marketValue: Double
    let netLiquidation: Double?
    let excessLiquidity: Double?
    let unrealizedExcessLiquidity: Double?
    let rowType: Int?

    static func fromDict(_ dict: [String: Any]) throws -> AccountBalances {
        guard let topic = dict["topic"] as? String, topic == "spl" else {
            throw IBError.invalidResponse
        }

        guard let args = dict["args"] as? [String: Any],
              let accountKey = args.keys.first,
              let accountData = args[accountKey] as? [String: Any] else {
            throw IBError.invalidResponse
        }

        let cleanAccountId = accountKey.trimmingCharacters(in: CharacterSet(charactersIn: "."))

        return AccountBalances(
            accountId: cleanAccountId,
            dailyPnl: accountData["dpl"] as? Double ?? 0.0,
            pnl: accountData["upl"] as? Double ?? 0.0,
            marketValue: accountData["mv"] as? Double ?? 0.0,
            netLiquidation: accountData["nl"] as? Double,
            excessLiquidity: accountData["el"] as? Double,
            unrealizedExcessLiquidity: accountData["uel"] as? Double,
            rowType: accountData["rowType"] as? Int
        )
    }

    func toDict() -> [String: Any] {
        return [
            "account_id": accountId,
            "daily_pnl": dailyPnl,
            "pnl": pnl,
            "market_value": marketValue,
            "net_liquidation": netLiquidation as Any,
            "excess_liquidity": excessLiquidity as Any,
            "unrealized_excess_liquidity": unrealizedExcessLiquidity as Any,
            "row_type": rowType as Any
        ]
    }
}

/// Stock position data
class STKPosition {
    let accountId: String
    let conid: Int
    let contractDesc: String
    let position: Double
    var mktPrice: Double
    var mktValue: Double
    let currency: String
    let avgCost: Double
    let avgPrice: Double
    let realizedPnl: Double
    var unrealizedPnl: Double
    let exchs: String
    let expiry: String
    let putOrCall: String
    let multiplier: String
    let strike: Double
    let exerciseStyle: String
    let conExchMap: [[String: Any]]
    let assetClass: String
    let undConid: Int
    var formattedUnrealizedPnl: String = "0.00"

    init(accountId: String, conid: Int, contractDesc: String, position: Double,
         mktPrice: Double, mktValue: Double, currency: String, avgCost: Double,
         avgPrice: Double, realizedPnl: Double, unrealizedPnl: Double,
         exchs: String, expiry: String, putOrCall: String, multiplier: String,
         strike: Double, exerciseStyle: String, conExchMap: [[String: Any]],
         assetClass: String, undConid: Int) {
        self.accountId = accountId
        self.conid = conid
        self.contractDesc = contractDesc
        self.position = position
        self.mktPrice = mktPrice
        self.mktValue = mktValue
        self.currency = currency
        self.avgCost = avgCost
        self.avgPrice = avgPrice
        self.realizedPnl = realizedPnl
        self.unrealizedPnl = unrealizedPnl
        self.exchs = exchs
        self.expiry = expiry
        self.putOrCall = putOrCall
        self.multiplier = multiplier
        self.strike = strike
        self.exerciseStyle = exerciseStyle
        self.conExchMap = conExchMap
        self.assetClass = assetClass
        self.undConid = undConid
    }

    static func fromDict(_ dict: [String: Any]) throws -> STKPosition {
        guard let accountId = dict["acctId"] as? String,
              let conid = dict["conid"] as? Int else {
            throw IBError.invalidResponse
        }

        return STKPosition(
            accountId: accountId,
            conid: conid,
            contractDesc: dict["contractDesc"] as? String ?? "",
            position: dict["position"] as? Double ?? 0.0,
            mktPrice: dict["mktPrice"] as? Double ?? 0.0,
            mktValue: dict["mktValue"] as? Double ?? 0.0,
            currency: dict["currency"] as? String ?? "USD",
            avgCost: dict["avgCost"] as? Double ?? 0.0,
            avgPrice: dict["avgPrice"] as? Double ?? 0.0,
            realizedPnl: dict["realizedPnl"] as? Double ?? 0.0,
            unrealizedPnl: dict["unrealizedPnl"] as? Double ?? 0.0,
            exchs: dict["exchs"] as? String ?? "",
            expiry: dict["expiry"] as? String ?? "",
            putOrCall: dict["putOrCall"] as? String ?? "",
            multiplier: dict["multiplier"] as? String ?? "",
            strike: dict["strike"] as? Double ?? 0.0,
            exerciseStyle: dict["exerciseStyle"] as? String ?? "",
            conExchMap: dict["conExchMap"] as? [[String: Any]] ?? [],
            assetClass: dict["assetClass"] as? String ?? "STK",
            undConid: dict["undConid"] as? Int ?? 0
        )
    }

    func toDict() -> [String: Any] {
        return [
            "account_id": accountId,
            "conid": conid,
            "contract_desc": contractDesc,
            "position": position,
            "mkt_price": mktPrice,
            "mkt_value": mktValue,
            "currency": currency,
            "avg_cost": avgCost,
            "avg_price": avgPrice,
            "realized_pnl": realizedPnl,
            "unrealized_pnl": unrealizedPnl,
            "exchs": exchs,
            "expiry": expiry,
            "put_or_call": putOrCall,
            "multiplier": multiplier,
            "strike": strike,
            "exercise_style": exerciseStyle,
            "con_exch_map": conExchMap,
            "asset_class": assetClass,
            "und_conid": undConid
        ]
    }
}

/// IBKR-specific market data for Protocol 2
/// Extends CapitalComMKTDataLive with shortable shares and unrealized PnL
class IBKR_CapitalComMKTDataLive: CapitalComMKTDataLive {
    let shortableShares: Double
    let unrealizedPnl: Double

    init(symbol: String, bid: Double, bidSize: Double, ask: Double, askSize: Double,
         last: Double, lastSize: Double, shortableShares: Double, unrealizedPnl: Double = 0.0) {
        self.shortableShares = shortableShares
        self.unrealizedPnl = unrealizedPnl
        super.init(symbol: symbol, bid: bid, bidSize: bidSize, ask: ask, askSize: askSize,
                   last: last, lastSize: lastSize)
    }

    override func transferable2() throws -> Data {
        // Get base CSV data from parent
        let baseData = try super.transferable2()
        let baseStr = String(data: baseData, encoding: .ascii) ?? ""

        // Split CSV and insert shortable shares before last two elements (timestamps)
        var components = baseStr.components(separatedBy: ",")

        // Insert shortable shares before the last 2 elements
        if components.count >= 2 {
            components.insert(String(shortableShares), at: components.count - 2)
        }

        let finalStr = components.joined(separator: ",")
        return finalStr.data(using: .ascii) ?? Data()
    }
}
