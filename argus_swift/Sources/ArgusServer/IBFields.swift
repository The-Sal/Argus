import Foundation

/// IBKR Field Codes - Maps field names to their respective codes
/// Transcompiled from argus/ib/fields.py
struct IBKRFields {
    // Field Codes as Static Constants
    static let LAST_PRICE = 31
    static let SYMBOL = 55
    static let TEXT = 58
    static let HIGH = 70
    static let LOW = 71
    static let MARKET_VALUE = 73
    static let AVG_PRICE = 74
    static let UNREALIZED_PNL = 75
    static let FORMATTED_POSITION = 76
    static let FORMATTED_UNREALIZED_PNL = 77
    static let DAILY_PNL = 78
    static let REALIZED_PNL = 79
    static let UNREALIZED_PNL_PERCENT = 80
    static let CHANGE = 82
    static let CHANGE_PERCENT = 83
    static let BID_PRICE = 84
    static let ASK_SIZE = 85
    static let ASK_PRICE = 86
    static let VOLUME = 87
    static let BID_SIZE = 88
    static let EXCHANGE = 6004
    static let CONID = 6008
    static let SEC_TYPE = 6070
    static let MONTHS = 6072
    static let REGULAR_EXPIRY = 6073
    static let MARKET_DATA_MARKER = 6119
    static let UNDERLYING_CONID = 6457
    static let SERVICE_PARAMS = 6508
    static let MARKET_DATA_AVAILABILITY = 6509
    static let COMPANY_NAME = 7051
    static let ASK_EXCHANGE = 7057
    static let LAST_EXCHANGE = 7058
    static let LAST_SIZE = 7059
    static let BID_EXCHANGE = 7068
    static let IMPLIED_VOL_HIST_VOL_PERCENT = 7084
    static let PUT_CALL_INTEREST = 7085
    static let PUT_CALL_VOLUME = 7086
    static let HIST_VOL_PERCENT = 7087
    static let HIST_VOL_CLOSE_PERCENT = 7088
    static let OPT_VOLUME = 7089
    static let CONID_EXCHANGE = 7094
    static let CAN_BE_TRADED = 7184
    static let CONTRACT_DESCRIPTION1 = 7219
    static let CONTRACT_DESCRIPTION2 = 7220
    static let LISTING_EXCHANGE = 7221
    static let INDUSTRY = 7280
    static let CATEGORY = 7281
    static let AVERAGE_VOLUME = 7282
    static let OPTION_IMPLIED_VOL_PERCENT = 7283
    static let HISTORICAL_VOLATILITY_PERCENT = 7284
    static let PUT_CALL_RATIO = 7285
    static let DIVIDEND_AMOUNT = 7286
    static let DIVIDEND_YIELD_PERCENT = 7287
    static let DIVIDEND_EX_DATE = 7288
    static let MARKET_CAP = 7289
    static let PE_RATIO = 7290
    static let EPS = 7291
    static let COST_BASIS = 7292
    static let WEEK_52_HIGH = 7293
    static let WEEK_52_LOW = 7294
    static let OPEN = 7295
    static let CLOSE = 7296
    static let DELTA = 7308
    static let GAMMA = 7309
    static let THETA = 7310
    static let VEGA = 7311
    static let OPT_VOLUME_CHANGE_PERCENT = 7607
    static let IMPLIED_VOL_PERCENT = 7633
    static let MARK = 7635
    static let SHORTABLE_SHARES = 7636
    static let FEE_RATE = 7637
    static let OPTION_OPEN_INTEREST = 7638
    static let PERCENT_OF_MARK_VALUE = 7639
    static let SHORTABLE = 7644
    static let MORNINGSTAR_RATING = 7655
    static let DIVIDENDS = 7671
    static let DIVIDENDS_TTM = 7672
    static let EMA_200 = 7674
    static let EMA_100 = 7675
    static let EMA_50 = 7676
    static let EMA_20 = 7677
    static let PRICE_EMA_200 = 7678
    static let PRICE_EMA_100 = 7679
    static let PRICE_EMA_50 = 7724
    static let PRICE_EMA_20 = 7681
    static let CHANGE_SINCE_OPEN = 7682
    static let UPCOMING_EVENT = 7683
    static let UPCOMING_EVENT_DATE = 7684
    static let UPCOMING_ANALYST_MEETING = 7685
    static let UPCOMING_EARNINGS = 7686
    static let UPCOMING_MISC_EVENT = 7687
    static let RECENT_ANALYST_MEETING = 7688
    static let RECENT_EARNINGS = 7689
    static let RECENT_MISC_EVENT = 7690
    static let PROBABILITY_OF_MAX_RETURN1 = 7694
    static let BREAK_EVEN = 7695
    static let SPX_DELTA = 7696
    static let FUTURES_OPEN_INTEREST = 7697
    static let LAST_YIELD = 7698
    static let BID_YIELD = 7699
    static let PROBABILITY_OF_MAX_RETURN2 = 7700
    static let PROBABILITY_OF_MAX_LOSS = 7702
    static let PROFIT_PROBABILITY = 7703
    static let ORGANIZATION_TYPE = 7704
    static let DEBT_CLASS = 7705
    static let RATINGS = 7706
    static let BOND_STATE_CODE = 7707
    static let BOND_TYPE = 7708
    static let LAST_TRADING_DATE = 7714
    static let ISSUE_DATE = 7715
    static let BETA = 7718
    static let ASK_YIELD = 7720
    static let PRIOR_CLOSE = 7741
    static let VOLUME_LONG = 7762
    static let HAS_TRADING_PERMISSIONS = 7768
    static let DAILY_PNL_RAW = 7920
    static let COST_BASIS_RAW = 7921

    /// Map of field codes to their descriptions
    static let descriptions: [Int: String] = [
        31: "Last Price",
        55: "Symbol",
        58: "Text",
        70: "High",
        71: "Low",
        73: "Market Value",
        74: "Avg Price",
        75: "Unrealized PnL",
        76: "Formatted position",
        77: "Formatted Unrealized PnL",
        78: "Daily PnL",
        79: "Realized PnL",
        80: "Unrealized PnL %",
        82: "Change",
        83: "Change %",
        84: "Bid Price",
        85: "Ask Size",
        86: "Ask Price",
        87: "Volume",
        88: "Bid Size",
        7636: "Shortable Shares",
        7637: "Fee Rate"
    ]

    /// Get description for a field code
    static func getDescription(_ fieldCode: Int) -> String {
        return descriptions[fieldCode] ?? "Unknown field code"
    }
}

/// Search result for contract lookups
struct SearchResult {
    let conid: String
    let companyHeader: String
    let companyName: String
    let symbol: String
    let description: String
    let restricted: String?
    let sections: [[String: Any]]

    static func fromDict(_ dict: [String: Any]) throws -> SearchResult {
        guard let conid = dict["conid"] as? String,
              let companyHeader = dict["companyHeader"] as? String,
              let companyName = dict["companyName"] as? String,
              let symbol = dict["symbol"] as? String,
              let description = dict["description"] as? String else {
            throw IBError.invalidResponse
        }

        let restricted = dict["restricted"] as? String
        let sections = dict["sections"] as? [[String: Any]] ?? []

        return SearchResult(
            conid: conid,
            companyHeader: companyHeader,
            companyName: companyName,
            symbol: symbol,
            description: description,
            restricted: restricted,
            sections: sections
        )
    }
}
