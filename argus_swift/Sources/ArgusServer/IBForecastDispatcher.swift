import Foundation

#if canImport(Glibc)
import Glibc
#else
import Darwin
#endif

/// IBKR Forecast Dispatcher (FXCDispatcher)
/// Transcompiled from argus/ib/forecast.py FXCDispatcher class
/// Only supports Protocol 2 / JSON mode
class FXCDispatcher {
    private let ws: FXCWss
    private var serverSocket: Int32 = -1
    private var clients: [ArgusSocket] = []
    private let threadLock = NSLock()
    private let marketLock = NSLock()
    private let marketDataLock = NSLock()

    private let urls: [String: String] = [
        "tree": "https://api.ibkr.com/v1/api/trsrv/event/category-tree",
        "contract": "https://api.ibkr.com/v1/api/trsrv/event/contracts?market={}&exchange=FORECASTX"
    ]

    private var allMarkets: [AbstractMarket]?
    private var activeMarket: FxContractBig?
    private var activeMarketMemory: [Int: FxContractBig] = [:]
    private var configs: [String: Any] = [:]
    private let host: String
    private let port: Int32

    init(cookie: String, host: String = "localhost", port: Int32 = 9972) {
        self.host = host
        self.port = port
        self.ws = FXCWss(cookie: cookie)

        // Default configurations
        configs["Auto-Print Pandas DataFrame on Update"] = true

        setupServerSocket()
        startWebSocket()
    }

    private func setupServerSocket() {
        serverSocket = socket(AF_INET, SOCK_STREAM, 0)
        guard serverSocket >= 0 else {
            print("Failed to create socket")
            return
        }

        var reuseAddr: Int32 = 1
        setsockopt(serverSocket, SOL_SOCKET, SO_REUSEADDR, &reuseAddr, socklen_t(MemoryLayout<Int32>.size))

        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = UInt16(port).bigEndian
        addr.sin_addr.s_addr = inet_addr(host)

        let bindResult = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(serverSocket, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }

        guard bindResult >= 0 else {
            print("Failed to bind socket")
            return
        }

        print("Forecast server socket bound to \(host):\(port)")
    }

    private func startWebSocket() {
        ws.run()
        let authenticated = ws.waitForAuthentication(timeout: 30)

        if authenticated {
            print("Forecast WebSocket authenticated")
        } else {
            print("Forecast WebSocket authentication timeout")
        }

        startClientListener()
    }

    private func startClientListener() {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }

            while true {
                listen(self.serverSocket, 5)

                var clientAddr = sockaddr_in()
                var clientAddrLen = socklen_t(MemoryLayout<sockaddr_in>.size)

                let clientSocket = withUnsafeMutablePointer(to: &clientAddr) {
                    $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                        accept(self.serverSocket, $0, &clientAddrLen)
                    }
                }

                guard clientSocket >= 0 else {
                    continue
                }

                let realSocket = RealSocket(socket: clientSocket)
                self.threadLock.lock()
                self.clients.append(realSocket)
                self.threadLock.unlock()

                self.listenToClient(realSocket)
            }
        }
    }

    private func listenToClient(_ client: ArgusSocket) {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }

            while true {
                guard let realSocket = client as? RealSocket else { break }

                var buffer = [UInt8](repeating: 0, count: 1024)
                let bytesRead = recv(realSocket.socket, &buffer, buffer.count, 0)

                guard bytesRead > 0 else {
                    break
                }

                let data = Data(bytes: buffer, count: bytesRead)
                guard let message = String(data: data, encoding: .ascii)?.trimmingCharacters(in: .whitespacesAndNewlines) else {
                    continue
                }

                self.handleClientCommand(message, client: client)
            }

            try? client.close()
        }
    }

    private func handleClientCommand(_ command: String, client: ArgusSocket) {
        let parts = command.components(separatedBy: ":")
        guard parts.count >= 1 else {
            sendResponse(client: client, command: "unknown", value: nil, error: "Invalid command format")
            return
        }

        let cmd = parts[0].trimmingCharacters(in: .whitespaces)
        let args = parts.count > 1 ? parts[1].components(separatedBy: ",") : []

        switch cmd {
        case "get_active_market":
            let result = getActiveMarket()
            sendResponse(client: client, command: cmd, value: result, error: nil)

        case "activate_market":
            guard let conidStr = args.first, let conid = Int(conidStr) else {
                sendResponse(client: client, command: cmd, value: nil, error: "Invalid conid")
                return
            }
            do {
                try activateMarket(conid: conid)
                sendResponse(client: client, command: cmd, value: "Market activated", error: nil)
            } catch {
                sendResponse(client: client, command: cmd, value: nil, error: error.localizedDescription)
            }

        case "start_market_resolution":
            do {
                try startMarketResolution()
                sendResponse(client: client, command: cmd, value: "Started resolution", error: nil)
            } catch {
                sendResponse(client: client, command: cmd, value: nil, error: error.localizedDescription)
            }

        case "market_fully_resolved":
            let resolved = isMarketFullyResolved()
            sendResponse(client: client, command: cmd, value: resolved, error: nil)

        case "get_all_markets":
            do {
                let markets = try generateAllMarkets()
                let json = try? JSONSerialization.data(withJSONObject: markets.map { ["name": $0.name, "conid": $0.conid] })
                let jsonStr = json != nil ? String(data: json!, encoding: .utf8) : "[]"
                sendResponse(client: client, command: cmd, value: jsonStr, error: nil)
            } catch {
                sendResponse(client: client, command: cmd, value: nil, error: error.localizedDescription)
            }

        default:
            sendResponse(client: client, command: cmd, value: nil, error: "Unknown command")
        }
    }

    private func sendResponse(client: ArgusSocket, command: String, value: Any?, error: String?) {
        let response: [String: Any] = [
            "command": command,
            "value": value as Any,
            "error": error as Any
        ]

        do {
            let jsonData = try JSONSerialization.data(withJSONObject: response)
            let msg = "~\(String(data: jsonData, encoding: .utf8) ?? "{}")L"
            try client.sendall(msg.data(using: .ascii)!)
        } catch {
            print("Failed to send response: \(error)")
        }
    }

    /// Generate all available markets
    func generateAllMarkets() throws -> [AbstractMarket] {
        if let markets = allMarkets {
            return markets
        }

        print("Fetching all markets from IBKR...")

        let (data, _) = try ws.networker.session.get(url: urls["tree"]!)
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw FxCError.abstractionError
        }

        var markets: [AbstractMarket] = []

        for (_, categoryData) in json {
            if let category = categoryData as? [String: Any],
               let marketList = category["markets"] as? [[String: Any]] {
                for marketDict in marketList {
                    if let market = try? AbstractMarket.fromDict(marketDict) {
                        markets.append(market)
                    }
                }
            }
        }

        print("Found \(markets.count) markets")
        allMarkets = markets
        return markets
    }

    /// Activate a market
    func activateMarket(conid: Int) throws {
        marketLock.lock()
        defer { marketLock.unlock() }

        if let market = activeMarket, !isMarketFullyResolved() {
            throw FxCError.marketNotFinishedResolution
        }

        if let cachedMarket = activeMarketMemory[conid] {
            activeMarket = cachedMarket
            print("Market \(cachedMarket.underlyingName) re-activated from memory")
            return
        }

        let contractUrl = urls["contract"]!.replacingOccurrences(of: "{}", with: "\(conid)")
        let (data, _) = try ws.networker.session.get(url: contractUrl)

        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let contracts = json["contracts"] as? [[String: Any]] else {
            throw FxCError.abstractionError
        }

        let big = try FxContractBig.fromJson(contracts)
        activeMarket = big
        print("Activated market: \(big.underlyingName)")
    }

    /// Start market resolution
    func startMarketResolution() throws {
        marketLock.lock()
        defer { marketLock.unlock() }

        guard let market = activeMarket else {
            throw FxCError.abstractionError
        }

        if isMarketFullyResolved() {
            print("Market already fully resolved")
            return
        }

        for conid in market.allConids {
            ws.streamMarketData(contractId: conid) { [weak self] marketData in
                self?.internalCallback(marketData)
            }
        }
    }

    private func internalCallback(_ marketData: IBMarketData) {
        marketDataLock.lock()
        defer { marketDataLock.unlock() }

        guard let market = activeMarket else { return }

        if market.allConids.contains(marketData.contractId) {
            do {
                try market.applyMktDataUpdate(conid: marketData.contractId, mktData: marketData)
            } catch FxCError.noValueMarketData {
                // Ignore
            } catch {
                print("Error applying market data update: \(error)")
            }

            if isMarketFullyResolved() {
                print("Market fully resolved!")
                activeMarketMemory[market.conid] = market
            }
        }
    }

    private func getActiveMarket() -> String? {
        guard let market = activeMarket else {
            return nil
        }

        if let jsonData = try? JSONSerialization.data(withJSONObject: market.toDict()),
           let jsonStr = String(data: jsonData, encoding: .utf8) {
            return jsonStr
        }

        return nil
    }

    private func isMarketFullyResolved() -> Bool {
        guard let market = activeMarket else {
            return true
        }

        let states = market.allConidStates()
        for (_, missing) in states {
            if missing {
                return false
            }
        }

        return true
    }

    /// Interactive mode
    func interactiveMode() {
        print("\nFXC Dispatcher Interactive Mode")
        print("Enter commands (or 'exit' to quit):")

        while true {
            print("> ", terminator: "")
            guard let input = readLine()?.trimmingCharacters(in: .whitespacesAndNewlines) else {
                continue
            }

            if input.lowercased() == "exit" {
                break
            }

            print("Unknown command: \(input)")
        }
    }

    func selectAccountInteractive() {
        do {
            let accounts = try ws.networker.getAllTradingAccountIds()

            print("Available accounts:")
            for (index, account) in accounts.enumerated() {
                print("\(index + 1). \(account.accountId)")
            }

            print("Select an account by number (default is 1): ", terminator: "")
            if let input = readLine()?.trimmingCharacters(in: .whitespacesAndNewlines) {
                let choice = input.isEmpty ? 0 : (Int(input) ?? 1) - 1
                let selectedAccount = accounts[choice]

                try ws.networker.setTradingAccountId(selectedAccount.accountId)
                print("Selected account: \(selectedAccount.accountId)")
            }
        } catch {
            print("Failed to select account: \(error)")
        }
    }
}
