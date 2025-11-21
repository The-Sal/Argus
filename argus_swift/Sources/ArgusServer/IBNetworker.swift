import Foundation

/// Thread-safe HTTP session for IBKR REST API
class IBLockedSession {
    private let session: URLSession
    private let lock = NSLock()
    private var headers: [String: String]

    init(headers: [String: String]) {
        let config = URLSessionConfiguration.default
        self.session = URLSession(configuration: config)
        self.headers = headers
    }

    func get(url: String, params: [String: String]? = nil) throws -> (Data, HTTPURLResponse) {
        lock.lock()
        defer { lock.unlock() }

        var urlComponents = URLComponents(string: url)!
        if let params = params {
            urlComponents.queryItems = params.map { URLQueryItem(name: $0.key, value: $0.value) }
        }

        var request = URLRequest(url: urlComponents.url!)
        for (key, value) in headers {
            request.setValue(value, forHTTPHeaderField: key)
        }

        let semaphore = DispatchSemaphore(value: 0)
        var result: (Data, HTTPURLResponse)?
        var error: Error?

        session.dataTask(with: request) { data, response, err in
            if let err = err {
                error = err
            } else if let data = data, let httpResponse = response as? HTTPURLResponse {
                result = (data, httpResponse)
            }
            semaphore.signal()
        }.resume()

        semaphore.wait()

        if let error = error {
            throw error
        }

        guard let result = result else {
            throw IBError.invalidResponse
        }

        return result
    }

    func post(url: String, json: [String: Any]? = nil) throws -> (Data, HTTPURLResponse) {
        lock.lock()
        defer { lock.unlock() }

        var request = URLRequest(url: URL(string: url)!)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        for (key, value) in headers {
            request.setValue(value, forHTTPHeaderField: key)
        }

        if let json = json {
            request.httpBody = try JSONSerialization.data(withJSONObject: json)
        }

        let semaphore = DispatchSemaphore(value: 0)
        var result: (Data, HTTPURLResponse)?
        var error: Error?

        session.dataTask(with: request) { data, response, err in
            if let err = err {
                error = err
            } else if let data = data, let httpResponse = response as? HTTPURLResponse {
                result = (data, httpResponse)
            }
            semaphore.signal()
        }.resume()

        semaphore.wait()

        if let error = error {
            throw error
        }

        guard let result = result else {
            throw IBError.invalidResponse
        }

        return result
    }
}

/// IBKR REST API client
class IBNetworker {
    private let cookie: String
    private let session: IBLockedSession
    private var authenticated = false
    private var tradingAccountId: String?

    private let urls: [String: String] = [
        "search": "https://www.interactivebrokers.co.uk/portal.proxy/v1/portal/iserver/secdef/search",
        "query_equities_contracts": "https://www.interactivebrokers.co.uk/portal.proxy/v1/portal/iserver/trsrv/stocks",
        "portfolio_accounts": "https://api.ibkr.com/v1/api/portfolio/accounts",
        "account_ledger": "https://api.ibkr.com/v1/api/portfolio/{}/ledger",
        "account_summary": "https://api.ibkr.com/v1/api/portfolio/{}/summary",
        "account_positions": "https://api.ibkr.com/v1/api/portfolio/{}/positions",
        "tickle": "https://www.interactivebrokers.co.uk/portal.proxy/v1/portal/tickle",
        "auth_status": "https://www.interactivebrokers.co.uk/portal.proxy/v1/portal/iserver/auth/status",
        "ssodh_init": "https://www.interactivebrokers.co.uk/portal.proxy/v1/portal/iserver/auth/ssodh/init",
        "set_account": "https://www.interactivebrokers.co.uk/portal.proxy/v1/portal/iserver/account"
    ]

    init(cookie: String) {
        self.cookie = cookie
        let headers = [
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        ]
        self.session = IBLockedSession(headers: headers)
    }

    /// Initialize connection with IBKR
    func initialize() throws {
        try runSetupMessages()
        startAuthenticationChecker()
        startHeartbeat()
    }

    private func runSetupMessages() throws {
        print("Sending setup messages to IBKR...")

        // Tickle
        let (tickleData, _) = try session.post(url: urls["tickle"]!)
        if let json = try? JSONSerialization.jsonObject(with: tickleData) {
            print("Tickle response: \(json)")
        }

        Thread.sleep(forTimeInterval: 1)

        // Auth status
        let (authData, _) = try session.post(url: urls["auth_status"]!)
        if let json = try? JSONSerialization.jsonObject(with: authData) {
            print("Auth status response: \(json)")
        }

        Thread.sleep(forTimeInterval: 1)

        // SSODH init
        let initPayload: [String: Any] = [
            "compete": false,
            "useSecurityContext": true,
            "locale": "en_US",
            "tz": "xxx (Europe/London)",
            "isET": true,
            "publish": true
        ]

        let (initData, _) = try session.post(url: urls["ssodh_init"]!, json: initPayload)
        if let json = try? JSONSerialization.jsonObject(with: initData) as? [String: Any] {
            print("SSODH init response: \(json)")
            authenticated = json["authenticated"] as? Bool ?? false
            print("Authenticated: \(authenticated)")
        }
    }

    private func startAuthenticationChecker() {
        DispatchQueue.global(qos: .background).async { [weak self] in
            while true {
                Thread.sleep(forTimeInterval: 120) // Check every 2 minutes
                guard let self = self else { return }

                do {
                    let (data, _) = try self.session.post(url: self.urls["auth_status"]!)
                    if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                        self.authenticated = json["authenticated"] as? Bool ?? false
                        if !self.authenticated {
                            print("Authentication lost, re-running setup...")
                            try self.runSetupMessages()
                        }
                    }
                } catch {
                    print("Authentication check failed: \(error)")
                }
            }
        }
    }

    private func startHeartbeat() {
        DispatchQueue.global(qos: .background).async { [weak self] in
            while true {
                Thread.sleep(forTimeInterval: 2)
                guard let self = self else { return }

                do {
                    _ = try self.session.post(url: self.urls["tickle"]!)
                } catch {
                    print("Heartbeat failed: \(error)")
                }
            }
        }
    }

    /// Search for a contract by symbol
    func searchContract(contractName: String) throws -> [SearchResult] {
        let payload: [String: Any] = [
            "symbol": contractName,
            "secType": "STK",
            "referrer": "onebar"
        ]

        let (data, _) = try session.post(url: urls["search"]!, json: payload)

        guard let jsonArray = try JSONSerialization.jsonObject(with: data) as? [[String: Any]] else {
            throw IBError.invalidResponse
        }

        return try jsonArray.map { try SearchResult.fromDict($0) }
    }

    /// Get all trading account IDs
    func getAllTradingAccountIds() throws -> [IBAccount] {
        let (data, _) = try session.get(url: urls["portfolio_accounts"]!)

        guard let jsonArray = try JSONSerialization.jsonObject(with: data) as? [[String: Any]] else {
            throw IBError.invalidResponse
        }

        return try jsonArray.map { try IBAccount.fromDict($0) }
    }

    /// Set trading account ID
    func setTradingAccountId(_ accountId: String) throws {
        guard tradingAccountId == nil else {
            throw IBError.invalidResponse // Cannot change once set
        }

        tradingAccountId = accountId

        // Fetch positions
        let positionsUrl = urls["account_positions"]!.replacingOccurrences(of: "{}", with: accountId)
        let (posData, _) = try session.get(url: positionsUrl)
        if let json = try? JSONSerialization.jsonObject(with: posData) {
            print("Portfolio Positions: \(json)")
        }

        // Fetch account summary
        let summaryUrl = urls["account_summary"]!.replacingOccurrences(of: "{}", with: accountId)
        let (summaryData, _) = try session.get(url: summaryUrl)
        if let json = try? JSONSerialization.jsonObject(with: summaryData) {
            print("Account Summary: \(json)")
        }

        // Set active account
        let setAccountPayload = ["acctId": accountId]
        let (_, _) = try session.post(url: urls["set_account"]!, json: setAccountPayload)
        print("Set trading account to \(accountId)")
    }

    /// Fetch account positions
    func fetchAccountPositions() throws -> [STKPosition] {
        guard let accountId = tradingAccountId else {
            throw IBError.invalidResponse
        }

        let positionsUrl = urls["account_positions"]!.replacingOccurrences(of: "{}", with: accountId)
        let (data, _) = try session.get(url: positionsUrl)

        guard let jsonArray = try JSONSerialization.jsonObject(with: data) as? [[String: Any]] else {
            throw IBError.invalidResponse
        }

        // Filter for STK only
        return try jsonArray
            .filter { ($0["assetClass"] as? String) == "STK" }
            .map { try STKPosition.fromDict($0) }
    }

    var isAuthenticated: Bool {
        return authenticated
    }

    var accountId: String? {
        return tradingAccountId
    }
}
