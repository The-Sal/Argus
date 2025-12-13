import Foundation

/// Forecast contract errors
enum FxCError: Error {
    case marketNotFinishedResolution
    case noValueMarketData
    case abstractionError
}

/// Abstract market representation
struct AbstractMarket {
    let name: String
    let symbol: String
    let exchange: String
    let conid: Int

    static func fromDict(_ dict: [String: Any]) throws -> AbstractMarket {
        guard let name = dict["name"] as? String,
              let symbol = dict["symbol"] as? String,
              let exchange = dict["exchange"] as? String,
              let conid = dict["conid"] as? Int else {
            throw FxCError.abstractionError
        }

        return AbstractMarket(name: name, symbol: symbol, exchange: exchange, conid: conid)
    }
}

/// Micro forecast contract (smallest unit)
class FxContractMicro {
    var market: String?
    var popularityRank: Int?
    var name: String?
    var longDescription: String?
    var putOrCall: String?  // Put=No, Call=Yes
    var expiration: Int?
    var currency: String?
    var lastTradeMillis: Int?
    var lastTradeDate: String?
    var lastTradeTime: String?
    var timezone: String?
    var commodityCode: String?
    var eventAuthorityURL: String?
    var eventFixedPayout: Double?
    var sourceAgency: String?
    var marketRulesLink: String?
    var underlyingName: String?
    var categories: [String] = []
    var expectedResolutionTime: Date?
    var expectedPayoutTime: Date?
    var timespecifierParam: String?
    var exchange: String?
    var priceIncrement: Double?
    var tradingHours: [String: Any] = [:]
    var conid: Int?
    var underlyingConid: Int?
    var underlyingSymbol: String?
    var shortDescription: String?
    var strike: Double?
    var strikeLabel: String?

    private let requiredFields = [IBKRFields.BID_PRICE, IBKRFields.BID_SIZE,
                                 IBKRFields.ASK_PRICE, IBKRFields.ASK_SIZE]
    var marketData: IBMarketData?

    init(_ dict: [String: Any]) throws {
        market = dict["market"] as? String
        popularityRank = dict["popularityRank"] as? Int
        name = dict["name"] as? String
        longDescription = dict["longDescription"] as? String
        putOrCall = dict["putOrCall"] as? String
        expiration = dict["expiration"] as? Int
        currency = dict["currency"] as? String
        lastTradeMillis = dict["lastTradeMillis"] as? Int
        lastTradeDate = dict["lastTradeDate"] as? String
        lastTradeTime = dict["lastTradeTime"] as? String
        timezone = dict["timezone"] as? String
        commodityCode = dict["commodityCode"] as? String
        eventAuthorityURL = dict["eventAuthorityURL"] as? String
        eventFixedPayout = dict["eventFixedPayout"] as? Double
        sourceAgency = dict["sourceAgency"] as? String
        marketRulesLink = dict["marketRulesLink"] as? String
        underlyingName = dict["underlyingName"] as? String
        categories = dict["categories"] as? [String] ?? []

        // Parse timestamps
        if let timeStr = dict["expectedResolutionTime"] as? String {
            expectedResolutionTime = parseTimestamp(timeStr)
        }
        if let timeStr = dict["expectedPayoutTime"] as? String {
            expectedPayoutTime = parseTimestamp(timeStr)
        }

        timespecifierParam = dict["timespecifierParam"] as? String
        exchange = dict["exchange"] as? String
        priceIncrement = dict["priceIncrement"] as? Double
        tradingHours = dict["tradingHours"] as? [String: Any] ?? [:]
        conid = dict["conid"] as? Int
        underlyingConid = dict["underlyingConid"] as? Int
        underlyingSymbol = dict["underlyingSymbol"] as? String
        shortDescription = dict["shortDescription"] as? String
        strike = dict["strike"] as? Double
        strikeLabel = dict["strikeLabel"] as? String

        guard strikeLabel != nil else {
            throw FxCError.abstractionError
        }
    }

    func updateMarketData(_ data: IBMarketData) {
        marketData = data
    }

    func deltaUpdateMarketData(_ data: IBMarketData) throws {
        if marketData == nil {
            var hasValidValue = false
            for field in requiredFields {
                let newValue = data.get(field, default: nil)
                if newValue != nil && "\(newValue!)" != "None" {
                    hasValidValue = true
                    break
                }
            }

            guard hasValidValue else {
                throw FxCError.noValueMarketData
            }

            marketData = data
        } else {
            for field in requiredFields {
                if let newValue = data.get(field, default: nil), "\(newValue)" != "None" {
                    marketData?.data[String(field)] = newValue
                }
            }
        }
    }

    func dataAvailable() -> Bool {
        return marketData != nil
    }

    func buyData() -> (Double, Double) {
        guard let data = marketData else { return (0.0, 0.0) }
        let price = Double("\(data.get(IBKRFields.BID_PRICE, default: 0.0) ?? 0.0)") ?? 0.0
        let size = Double("\(data.get(IBKRFields.BID_SIZE, default: 0.0) ?? 0.0)") ?? 0.0
        return (price, size)
    }

    func sellData() -> (Double, Double) {
        guard let data = marketData else { return (0.0, 0.0) }
        let price = Double("\(data.get(IBKRFields.ASK_PRICE, default: 0.0) ?? 0.0)") ?? 0.0
        let size = Double("\(data.get(IBKRFields.ASK_SIZE, default: 0.0) ?? 0.0)") ?? 0.0
        return (price, size)
    }
}

/// Mini forecast contract (contains 2 micros: Yes and No)
class FxContractMini {
    var yes: FxContractMicro
    var no: FxContractMicro

    init(yes: FxContractMicro, no: FxContractMicro) {
        self.yes = yes
        self.no = no
    }

    var allConids: [Int] {
        var conids: [Int] = []
        if let yesConid = yes.conid {
            conids.append(yesConid)
        }
        if let noConid = no.conid {
            conids.append(noConid)
        }
        return conids
    }

    func applyMktDataUpdate(conid: Int, mktData: IBMarketData) throws {
        if yes.conid == conid {
            try yes.deltaUpdateMarketData(mktData)
        } else if no.conid == conid {
            try no.deltaUpdateMarketData(mktData)
        }
    }

    func allConidStates() -> [Int: Bool] {
        var states: [Int: Bool] = [:]
        if let yesConid = yes.conid {
            states[yesConid] = !yes.dataAvailable()
        }
        if let noConid = no.conid {
            states[noConid] = !no.dataAvailable()
        }
        return states
    }
}

/// Big forecast contract (contains multiple minis)
class FxContractBig {
    let conid: Int
    let underlyingName: String
    var minis: [FxContractMini] = []

    init(conid: Int, underlyingName: String, contracts: [[String: Any]]) throws {
        self.conid = conid
        self.underlyingName = underlyingName

        // Group contracts by strikeLabel to create mini contracts
        var grouped: [String: [FxContractMicro]] = [:]

        for contractDict in contracts {
            let micro = try FxContractMicro(contractDict)

            guard let label = micro.strikeLabel else {
                continue
            }

            if grouped[label] == nil {
                grouped[label] = []
            }
            grouped[label]?.append(micro)
        }

        // Create minis (each should have exactly 2 micros: Yes and No)
        for (_, micros) in grouped {
            guard micros.count == 2 else {
                continue
            }

            let yesMicro = micros.first { $0.putOrCall == "C" }  // Call = Yes
            let noMicro = micros.first { $0.putOrCall == "P" }   // Put = No

            if let yes = yesMicro, let no = noMicro {
                minis.append(FxContractMini(yes: yes, no: no))
            }
        }
    }

    static func fromJson(_ contracts: [[String: Any]]) throws -> FxContractBig {
        guard let first = contracts.first,
              let conid = first["underlyingConid"] as? Int,
              let underlyingName = first["underlyingName"] as? String else {
            throw FxCError.abstractionError
        }

        return try FxContractBig(conid: conid, underlyingName: underlyingName, contracts: contracts)
    }

    var allConids: [Int] {
        return minis.flatMap { $0.allConids }
    }

    func applyMktDataUpdate(conid: Int, mktData: IBMarketData) throws {
        for mini in minis {
            try mini.applyMktDataUpdate(conid: conid, mktData: mktData)
        }
    }

    func allConidStates() -> [Int: Bool] {
        var states: [Int: Bool] = [:]
        for mini in minis {
            for (conid, missing) in mini.allConidStates() {
                states[conid] = missing
            }
        }
        return states
    }

    func toDict() -> [String: Any] {
        return [
            "conid": conid,
            "underlyingName": underlyingName,
            "mini_count": minis.count
        ]
    }
}

/// Helper function to parse timestamp in YYYYMMDDHHMMSS format
func parseTimestamp(_ str: String) -> Date? {
    let formatter = DateFormatter()
    formatter.dateFormat = "yyyyMMddHHmmss"
    return formatter.date(from: str)
}
