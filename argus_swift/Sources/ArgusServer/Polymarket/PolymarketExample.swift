import Foundation

// MARK: - Polymarket Direct Example
// Simplified transcompilation of argus/polymarket_direct/_example.py
// Demonstrates basic usage of EnhancedPM without full streaming infrastructure


func fetchAllHourlyBtcMarkets(enhancedPM: EnhancedPM) -> [PolymarketEvent] {
    var allBitcoinHourly: [PolymarketEvent] = []
    var totalFetched = 0
    let offsetStep = 150

    for offset in stride(from: 0, to: 5000, by: offsetStep) {
        do {
            let events = try enhancedPM.fetchEvents(offset: offset, limit: offsetStep)
            totalFetched += events.count

            if events.isEmpty {
                break
            }

            // Filter for bitcoin hourly markets
            for event in events {
                if let ticker = event.ticker,
                   ticker.contains("bitcoin-up-or-down") && ticker.contains("-et") {
                    allBitcoinHourly.append(event)
                }
            }

            print("Fetched \(totalFetched) markets, total bitcoin hourly: \(allBitcoinHourly.count)")
            if allBitcoinHourly.count >= 50{
                print("Found \(allBitcoinHourly.count) bitcoin hourly markets, exiting early...")
                return allBitcoinHourly
            }

        } catch {
            print("Error fetching events: \(error)")
            break
        }
    }

    return allBitcoinHourly
}


func polymarketExampleUsage() {
    print("=== Polymarket Direct Example ===\n")

    // Initialize EnhancedPM (no credentials needed for market data)
    let enhancedPM = EnhancedPM(
        privateKey: nil,
        proxyFunder: nil,
        dryMode: true
    )

    // Start market WebSocket
    enhancedPM.startMarketWs()
    print("[Example] Market WebSocket started\n")

    // Wait for connection to establish
    sleep(2)

    // Fetch bitcoin hourly markets
    print("Fetching bitcoin hourly markets...")
    let allBitcoinHourly = fetchAllHourlyBtcMarkets(enhancedPM: enhancedPM)

    print("\nFound \(allBitcoinHourly.count) bitcoin hourly markets")

    if allBitcoinHourly.isEmpty {
        print("No bitcoin hourly markets found. Exiting.")
        return
    }

    // Sort markets by event start time
    let sortedMarkets = allBitcoinHourly.sorted { event1, event2 in
        guard let market1 = event1.markets.first,
              let market2 = event2.markets.first,
              let time1 = market1.eventStartTime,
              let time2 = market2.eventStartTime else {
            return false
        }
        return time1 < time2
    }

    // Print upcoming markets
    print("\n" + String(repeating: "*", count: 100))
    print("Upcoming Bitcoin Hourly Markets:")
    print(String(repeating: "*", count: 100))

    let currentTime = Date()

    for event in sortedMarkets.prefix(10) {
        guard let market = event.markets.first,
              let startTimeStr = market.eventStartTime,
              let endDateStr = market.endDate,
              let startTime = parseISO8601(startTimeStr),
              let endTime = parseISO8601(endDateStr) else {
            continue
        }

        let secondsTillStart = startTime.timeIntervalSince(currentTime)
        let secondsTillEnd = endTime.timeIntervalSince(currentTime)

        print("""
        Market: \(event.ticker ?? "Unknown")
        Starts in: \(String(format: "%.2f", secondsTillStart / 3600)) hours at \(formatDate(startTime))
        Ends in: \(String(format: "%.2f", secondsTillEnd / 3600)) hours at \(formatDate(endTime))
        """)
    }

    // Find currently live markets
    print("\n" + String(repeating: "=", count: 100))
    print("Currently Live Markets:")
    print(String(repeating: "=", count: 100))

    var liveMarkets: [PolymarketEvent] = []
    for event in sortedMarkets {
        guard let market = event.markets.first,
              let startTimeStr = market.eventStartTime,
              let endDateStr = market.endDate,
              let startTime = parseISO8601(startTimeStr),
              let endTime = parseISO8601(endDateStr) else {
            continue
        }

        if startTime < currentTime && currentTime < endTime {
            print("""
            Market: \(event.ticker ?? "Unknown")
            Started at: \(formatDate(startTime))
            Ends at: \(formatDate(endTime))
            """)
            liveMarkets.append(event)
        }
    }

    print("\n" + String(repeating: "=", count: 100) + "\n")

    // Subscribe to a live market if available
    if let liveEvent = liveMarkets.first {
        print("Subscribing to live market: \(liveEvent.ticker ?? "Unknown")")

        guard let market = liveEvent.markets.first else {
            print("No market data available")
            return
        }

        guard let outcomes = market.outcomes,
              let tokenIds = market.clobTokenIds,
              outcomes.count == tokenIds.count else {
            print("Invalid market outcomes or token IDs")
            return
        }

        print("\nOutcome : Token ID")
        for (outcome, tokenId) in zip(outcomes, tokenIds) {
            print("\(outcome) : \(tokenId)")
        }

        // Subscribe to market data
        enhancedPM.subscribeToMarketData(assetIds: tokenIds) { orderBookData in
            let outcome = orderBookData["asset_id"] as? String ?? "Unknown"

            // Parse string values to Double (Polymarket sends numbers as strings)
            let bestAsk = Double(orderBookData["best_ask"] as? String ?? "0") ?? 0.0
            let bestBid = Double(orderBookData["best_bid"] as? String ?? "0") ?? 0.0
            let size = Double(orderBookData["size"] as? String ?? "0") ?? 0.0
            let price = Double(orderBookData["price"] as? String ?? "0") ?? 0.0

            // Determine color based on outcome
            let outcomeIndex = tokenIds.firstIndex(of: outcome) ?? 0
            let outcomeName = outcomeIndex < outcomes.count ? outcomes[outcomeIndex] : "Unknown"

            let color = outcomeName.lowercased() == "up" ? "🟢" : "🔴"

            print("[\(liveEvent.ticker ?? "")] \(color) \(outcomeName) ask=\(bestAsk) bid=\(bestBid) size=\(size) price=\(price)")
        }

        print("\nSubscribed! Listening for market data updates...")
        print("Press Ctrl+C to exit\n")

        // Keep running
        RunLoop.main.run()

    } else {
        print("No live markets found. Example complete.")
    }
}

// MARK: - Helper Functions

private func parseISO8601(_ dateString: String) -> Date? {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]

    if let date = formatter.date(from: dateString) {
        return date
    }

    // Try without fractional seconds
    formatter.formatOptions = [.withInternetDateTime]
    return formatter.date(from: dateString)
}

private func formatDate(_ date: Date) -> String {
    let formatter = DateFormatter()
    formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
    formatter.timeZone = TimeZone(identifier: "UTC")
    return formatter.string(from: date) + " UTC"
}
