import Foundation

// MARK: - Polymarket Direct Classes
// Transcompiled from argus/polymarket_direct/_types.py

/// Represents a tag/label for categorizing markets
struct Tag: Codable {
    let id: String?
    let label: String?
    let slug: String?
    let createdAt: String?
    let forceShow: Bool?
    let updatedAt: String?
    let isCarousel: Bool?
    let publishedAt: String?
    let createdBy: Int?
    let updatedBy: Int?
    let forceHide: Bool?

    static func fromDict(_ dict: [String: Any]) -> Tag? {
        return Tag(
            id: dict["id"] as? String,
            label: dict["label"] as? String,
            slug: dict["slug"] as? String,
            createdAt: dict["createdAt"] as? String,
            forceShow: dict["forceShow"] as? Bool,
            updatedAt: dict["updatedAt"] as? String,
            isCarousel: dict["isCarousel"] as? Bool,
            publishedAt: dict["publishedAt"] as? String,
            createdBy: dict["createdBy"] as? Int,
            updatedBy: dict["updatedBy"] as? Int,
            forceHide: dict["forceHide"] as? Bool
        )
    }
}

/// Represents a series of recurring markets
struct Series: Codable {
    let id: String?
    let ticker: String?
    let slug: String?
    let title: String?
    let active: Bool?
    let closed: Bool?
    let archived: Bool?
    let createdAt: String?
    let updatedAt: String?
    let seriesType: String?
    let recurrence: String?
    let volume: Double?
    let liquidity: Double?
    let commentCount: Int?
    let image: String?
    let icon: String?
    let featured: Bool?
    let restricted: Bool?

    static func fromDict(_ dict: [String: Any]) -> Series? {
        return Series(
            id: dict["id"] as? String,
            ticker: dict["ticker"] as? String,
            slug: dict["slug"] as? String,
            title: dict["title"] as? String,
            active: dict["active"] as? Bool,
            closed: dict["closed"] as? Bool,
            archived: dict["archived"] as? Bool,
            createdAt: dict["createdAt"] as? String,
            updatedAt: dict["updatedAt"] as? String,
            seriesType: dict["seriesType"] as? String,
            recurrence: dict["recurrence"] as? String,
            volume: dict["volume"] as? Double,
            liquidity: dict["liquidity"] as? Double,
            commentCount: dict["commentCount"] as? Int,
            image: dict["image"] as? String,
            icon: dict["icon"] as? String,
            featured: dict["featured"] as? Bool,
            restricted: dict["restricted"] as? Bool
        )
    }
}

/// Represents an individual prediction market within a Polymarket event
struct Market: Codable {
    let id: String?
    let question: String?
    let slug: String?
    let active: Bool?
    let closed: Bool?
    let conditionId: String?
    let resolutionSource: String?
    let endDate: String?
    let liquidity: String?
    let startDate: String?
    let image: String?
    let icon: String?
    let description: String?
    var outcomes: [String]?
    let volume: String?
    let marketMakerAddress: String?
    let createdAt: String?
    let updatedAt: String?
    let new: Bool?
    let featured: Bool?
    let archived: Bool?
    let restricted: Bool?
    let groupItemThreshold: String?
    let questionID: String?
    let enableOrderBook: Bool?
    let orderPriceMinTickSize: Double?
    let orderMinSize: Int?
    let volumeNum: Double?
    let liquidityNum: Double?
    let endDateIso: String?
    let hasReviewedDates: Bool?
    let volume24hr: Double?
    let volume1wk: Double?
    let volume1mo: Double?
    let volume1yr: Double?
    var clobTokenIds: [String]?
    let volume24hrAmm: Double?
    let volume1wkAmm: Double?
    let volume1moAmm: Double?
    let volume1yrAmm: Double?
    let volume24hrClob: Double?
    let volume1wkClob: Double?
    let volume1moClob: Double?
    let volume1yrClob: Double?
    let volumeAmm: Double?
    let volumeClob: Double?
    let liquidityAmm: Double?
    let liquidityClob: Double?
    let acceptingOrders: Bool?
    let negRisk: Bool?
    let ready: Bool?
    let funded: Bool?
    let acceptingOrdersTimestamp: String?
    let cyom: Bool?
    let competitive: Int?
    let pagerDutyNotificationEnabled: Bool?
    let approved: Bool?
    let rewardsMinSize: Double?
    let rewardsMaxSpread: Double?
    let spread: Double?
    let oneDayPriceChange: Double?
    let oneHourPriceChange: Double?
    let oneWeekPriceChange: Double?
    let oneMonthPriceChange: Double?
    let oneYearPriceChange: Double?
    let lastTradePrice: Double?
    let bestBid: Double?
    let bestAsk: Double?
    let automaticallyActive: Bool?
    let clearBookOnStart: Bool?
    let showGmpSeries: Bool?
    let showGmpOutcome: Bool?
    let manualActivation: Bool?
    let negRiskOther: Bool?
    let umaResolutionStatuses: String?
    let pendingDeployment: Bool?
    let deploying: Bool?
    let rfqEnabled: Bool?
    let eventStartTime: String?
    let holdingRewardsEnabled: Bool?
    let feesEnabled: Bool?
    let outcomePrices: String?
    let startDateIso: String?
    let submitted_by: String?
    let resolvedBy: String?
    let gameStartTime: String?
    let secondsDelay: Int?
    let umaBond: String?
    let umaReward: String?
    let negRiskRequestID: String?
    let customLiveness: Int?
    let sportsMarketType: String?
    let deployingTimestamp: String?

    static func fromDict(_ dict: [String: Any]) -> Market? {
        var outcomes: [String]? = nil
        var clobTokenIds: [String]? = nil

        // Parse outcomes JSON string or array
        if let outcomesStr = dict["outcomes"] as? String,
           let data = outcomesStr.data(using: .utf8),
           let parsed = try? JSONSerialization.jsonObject(with: data) as? [String] {
            outcomes = parsed
        } else if let outcomesArray = dict["outcomes"] as? [String] {
            outcomes = outcomesArray
        }

        // Parse clobTokenIds JSON string or array
        if let clobStr = dict["clobTokenIds"] as? String,
           let data = clobStr.data(using: .utf8),
           let parsed = try? JSONSerialization.jsonObject(with: data) as? [String] {
            clobTokenIds = parsed
        } else if let clobArray = dict["clobTokenIds"] as? [String] {
            clobTokenIds = clobArray
        }

        return Market(
            id: dict["id"] as? String,
            question: dict["question"] as? String,
            slug: dict["slug"] as? String,
            active: dict["active"] as? Bool,
            closed: dict["closed"] as? Bool,
            conditionId: dict["conditionId"] as? String,
            resolutionSource: dict["resolutionSource"] as? String,
            endDate: dict["endDate"] as? String,
            liquidity: dict["liquidity"] as? String,
            startDate: dict["startDate"] as? String,
            image: dict["image"] as? String,
            icon: dict["icon"] as? String,
            description: dict["description"] as? String,
            outcomes: outcomes,
            volume: dict["volume"] as? String,
            marketMakerAddress: dict["marketMakerAddress"] as? String,
            createdAt: dict["createdAt"] as? String,
            updatedAt: dict["updatedAt"] as? String,
            new: dict["new"] as? Bool,
            featured: dict["featured"] as? Bool,
            archived: dict["archived"] as? Bool,
            restricted: dict["restricted"] as? Bool,
            groupItemThreshold: dict["groupItemThreshold"] as? String,
            questionID: dict["questionID"] as? String,
            enableOrderBook: dict["enableOrderBook"] as? Bool,
            orderPriceMinTickSize: dict["orderPriceMinTickSize"] as? Double,
            orderMinSize: dict["orderMinSize"] as? Int,
            volumeNum: dict["volumeNum"] as? Double,
            liquidityNum: dict["liquidityNum"] as? Double,
            endDateIso: dict["endDateIso"] as? String,
            hasReviewedDates: dict["hasReviewedDates"] as? Bool,
            volume24hr: dict["volume24hr"] as? Double,
            volume1wk: dict["volume1wk"] as? Double,
            volume1mo: dict["volume1mo"] as? Double,
            volume1yr: dict["volume1yr"] as? Double,
            clobTokenIds: clobTokenIds,
            volume24hrAmm: dict["volume24hrAmm"] as? Double,
            volume1wkAmm: dict["volume1wkAmm"] as? Double,
            volume1moAmm: dict["volume1moAmm"] as? Double,
            volume1yrAmm: dict["volume1yrAmm"] as? Double,
            volume24hrClob: dict["volume24hrClob"] as? Double,
            volume1wkClob: dict["volume1wkClob"] as? Double,
            volume1moClob: dict["volume1moClob"] as? Double,
            volume1yrClob: dict["volume1yrClob"] as? Double,
            volumeAmm: dict["volumeAmm"] as? Double,
            volumeClob: dict["volumeClob"] as? Double,
            liquidityAmm: dict["liquidityAmm"] as? Double,
            liquidityClob: dict["liquidityClob"] as? Double,
            acceptingOrders: dict["acceptingOrders"] as? Bool,
            negRisk: dict["negRisk"] as? Bool,
            ready: dict["ready"] as? Bool,
            funded: dict["funded"] as? Bool,
            acceptingOrdersTimestamp: dict["acceptingOrdersTimestamp"] as? String,
            cyom: dict["cyom"] as? Bool,
            competitive: dict["competitive"] as? Int,
            pagerDutyNotificationEnabled: dict["pagerDutyNotificationEnabled"] as? Bool,
            approved: dict["approved"] as? Bool,
            rewardsMinSize: dict["rewardsMinSize"] as? Double,
            rewardsMaxSpread: dict["rewardsMaxSpread"] as? Double,
            spread: dict["spread"] as? Double,
            oneDayPriceChange: dict["oneDayPriceChange"] as? Double,
            oneHourPriceChange: dict["oneHourPriceChange"] as? Double,
            oneWeekPriceChange: dict["oneWeekPriceChange"] as? Double,
            oneMonthPriceChange: dict["oneMonthPriceChange"] as? Double,
            oneYearPriceChange: dict["oneYearPriceChange"] as? Double,
            lastTradePrice: dict["lastTradePrice"] as? Double,
            bestBid: dict["bestBid"] as? Double,
            bestAsk: dict["bestAsk"] as? Double,
            automaticallyActive: dict["automaticallyActive"] as? Bool,
            clearBookOnStart: dict["clearBookOnStart"] as? Bool,
            showGmpSeries: dict["showGmpSeries"] as? Bool,
            showGmpOutcome: dict["showGmpOutcome"] as? Bool,
            manualActivation: dict["manualActivation"] as? Bool,
            negRiskOther: dict["negRiskOther"] as? Bool,
            umaResolutionStatuses: dict["umaResolutionStatuses"] as? String,
            pendingDeployment: dict["pendingDeployment"] as? Bool,
            deploying: dict["deploying"] as? Bool,
            rfqEnabled: dict["rfqEnabled"] as? Bool,
            eventStartTime: dict["eventStartTime"] as? String,
            holdingRewardsEnabled: dict["holdingRewardsEnabled"] as? Bool,
            feesEnabled: dict["feesEnabled"] as? Bool,
            outcomePrices: dict["outcomePrices"] as? String,
            startDateIso: dict["startDateIso"] as? String,
            submitted_by: dict["submitted_by"] as? String,
            resolvedBy: dict["resolvedBy"] as? String,
            gameStartTime: dict["gameStartTime"] as? String,
            secondsDelay: dict["secondsDelay"] as? Int,
            umaBond: dict["umaBond"] as? String,
            umaReward: dict["umaReward"] as? String,
            negRiskRequestID: dict["negRiskRequestID"] as? String,
            customLiveness: dict["customLiveness"] as? Int,
            sportsMarketType: dict["sportsMarketType"] as? String,
            deployingTimestamp: dict["deployingTimestamp"] as? String
        )
    }

    /// Converts all non-nil date strings into Date objects
    mutating func convertToDatetime() {
        // Note: In Swift, we'd typically work with Date objects
        // This would require converting the struct to use Date? instead of String?
        // For now, we keep string dates for compatibility
    }
}

/// Represents a top-level Polymarket prediction event
struct PolymarketEvent: Codable {
    let id: String?
    let ticker: String?
    let slug: String?
    let title: String?
    let description: String?
    let resolutionSource: String?
    let endDate: String?
    let image: String?
    let icon: String?
    let active: Bool?
    let closed: Bool?
    let archived: Bool?
    let new: Bool?
    let featured: Bool?
    let restricted: Bool?
    let createdAt: String?
    let updatedAt: String?
    let enableOrderBook: Bool?
    let negRisk: Bool?
    let commentCount: Int?
    let cyom: Bool?
    let showAllOutcomes: Bool?
    let showMarketImages: Bool?
    let enableNegRisk: Bool?
    let automaticallyActive: Bool?
    let seriesSlug: String?
    let negRiskAugmented: Bool?
    let pendingDeployment: Bool?
    let deploying: Bool?
    let startDate: String?
    let creationDate: String?
    var markets: [Market]
    var series: [Series]
    var tags: [Tag]
    let openInterest: Double?
    let startTime: String?
    let volume: Double?
    let volume24hr: Double?
    let volume1wk: Double?
    let volume1mo: Double?
    let volume1yr: Double?
    let liquidity: Double?
    let liquidityClob: Double?
    let liquidityAmm: Double?
    let competitive: Int?
    let volumeNum: Double?
    let liquidityNum: Double?
    let eventDate: String?
    let eventWeek: Int?
    let gameId: Int?
    let homeTeamName: String?
    let awayTeamName: String?

    static func fromDict(_ dict: [String: Any]) -> PolymarketEvent? {
        // Parse nested tags
        var tags: [Tag] = []
        if let tagsArray = dict["tags"] as? [[String: Any]] {
            tags = tagsArray.compactMap { Tag.fromDict($0) }
        }

        // Parse nested series
        var series: [Series] = []
        if let seriesArray = dict["series"] as? [[String: Any]] {
            series = seriesArray.compactMap { Series.fromDict($0) }
        }

        // Parse nested markets
        var markets: [Market] = []
        if let marketsArray = dict["markets"] as? [[String: Any]] {
            markets = marketsArray.compactMap { Market.fromDict($0) }
        }

        return PolymarketEvent(
            id: dict["id"] as? String,
            ticker: dict["ticker"] as? String,
            slug: dict["slug"] as? String,
            title: dict["title"] as? String,
            description: dict["description"] as? String,
            resolutionSource: dict["resolutionSource"] as? String,
            endDate: dict["endDate"] as? String,
            image: dict["image"] as? String,
            icon: dict["icon"] as? String,
            active: dict["active"] as? Bool,
            closed: dict["closed"] as? Bool,
            archived: dict["archived"] as? Bool,
            new: dict["new"] as? Bool,
            featured: dict["featured"] as? Bool,
            restricted: dict["restricted"] as? Bool,
            createdAt: dict["createdAt"] as? String,
            updatedAt: dict["updatedAt"] as? String,
            enableOrderBook: dict["enableOrderBook"] as? Bool,
            negRisk: dict["negRisk"] as? Bool,
            commentCount: dict["commentCount"] as? Int,
            cyom: dict["cyom"] as? Bool,
            showAllOutcomes: dict["showAllOutcomes"] as? Bool,
            showMarketImages: dict["showMarketImages"] as? Bool,
            enableNegRisk: dict["enableNegRisk"] as? Bool,
            automaticallyActive: dict["automaticallyActive"] as? Bool,
            seriesSlug: dict["seriesSlug"] as? String,
            negRiskAugmented: dict["negRiskAugmented"] as? Bool,
            pendingDeployment: dict["pendingDeployment"] as? Bool,
            deploying: dict["deploying"] as? Bool,
            startDate: dict["startDate"] as? String ?? dict["startTime"] as? String,
            creationDate: dict["creationDate"] as? String ?? dict["createdAt"] as? String,
            markets: markets,
            series: series,
            tags: tags,
            openInterest: dict["openInterest"] as? Double,
            startTime: dict["startTime"] as? String,
            volume: dict["volume"] as? Double,
            volume24hr: dict["volume24hr"] as? Double,
            volume1wk: dict["volume1wk"] as? Double,
            volume1mo: dict["volume1mo"] as? Double,
            volume1yr: dict["volume1yr"] as? Double,
            liquidity: dict["liquidity"] as? Double,
            liquidityClob: dict["liquidityClob"] as? Double,
            liquidityAmm: dict["liquidityAmm"] as? Double,
            competitive: dict["competitive"] as? Int,
            volumeNum: dict["volumeNum"] as? Double,
            liquidityNum: dict["liquidityNum"] as? Double,
            eventDate: dict["eventDate"] as? String,
            eventWeek: dict["eventWeek"] as? Int,
            gameId: dict["gameId"] as? Int,
            homeTeamName: dict["homeTeamName"] as? String,
            awayTeamName: dict["awayTeamName"] as? String
        )
    }

    /// Converts all non-nil date strings into Date objects
    mutating func convertToDatetime() {
        // Note: In Swift, we'd typically work with Date objects
        // This would require converting the struct to use Date? instead of String?
        // For now, we keep string dates for compatibility
        for i in 0..<markets.count {
            markets[i].convertToDatetime()
        }
    }
}
