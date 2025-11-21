import Foundation

#if canImport(Glibc)
import Glibc
#else
import Darwin
#endif

/// IBKR Market Data Dispatcher
/// Transcompiled from argus/ib/__init__.py MKTDispatcher class
/// Only supports Protocol 2 mode
class IBMKTDispatcher {
    private var serverSocket: Int32 = -1
    private var clients: [ArgusSocket] = []
    private var conidToClients: [Int: [ArgusSocket]] = [:]
    private let ws: IBWss
    private let threadLock = NSLock()
    private var caches: [Int: [Int: Any]] = [:]  // contractId -> [field -> value]
    private let cacheFields = [IBKRFields.SYMBOL, IBKRFields.LAST_PRICE, IBKRFields.SHORTABLE_SHARES]
    private var configs: [String: Any] = [:]
    private let host: String
    private let port: Int32
    private var accountProvider: AccountProvider?

    init(cookie: String, host: String = "localhost", port: Int32 = 9972) {
        print("[IB Dispatcher] Initializing...")
        self.host = host
        self.port = port
        self.ws = IBWss(cookie: cookie)

        // Default configurations
        configs["Print data packets"] = false
        configs["Block New MKT Data"] = true  // Wait until account is set
        configs["Show blocked MKT Data Warning"] = false

        print("[IB Dispatcher] Setting up server socket on \(host):\(port)")
        setupServerSocket()

        print("[IB Dispatcher] Starting WebSocket connection...")
        startWebSocket()

        print("[IB Dispatcher] Initialization complete")
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
        // Use INADDR_ANY to bind to all interfaces (works with localhost)
        addr.sin_addr.s_addr = INADDR_ANY.bigEndian

        let bindResult = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(serverSocket, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }

        guard bindResult >= 0 else {
            print("Failed to bind socket")
            return
        }

        print("Server socket bound to \(host):\(port)")
    }

    private func startWebSocket() {
        ws.run()
        ws.waitTillReady()

        // Wait for authentication
        var waitTime = 0
        while !ws.networker.isAuthenticated && waitTime < 60 {
            print("Waiting for authentication... \(waitTime)/60s")
            Thread.sleep(forTimeInterval: 1)
            waitTime += 1
        }

        if ws.networker.isAuthenticated {
            print("Authenticated successfully")
        } else {
            print("ERROR: Authentication timeout after 60 seconds")
            print("Please check your IB_COOKIE is valid and IBKR Client Portal is running")
            exit(1)
        }

        startClientListener()
        startHealthChecker()
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

                let realSocket = RealSocket(fileDescriptor: clientSocket)
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

                let data: Data
                do {
                    guard let receivedData = try realSocket.receive(bufferSize: 9999) else {
                        break  // Connection closed
                    }
                    data = receivedData
                } catch {
                    break  // Error receiving
                }
                guard let message = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) else {
                    continue
                }

                if message.hasPrefix("add=") {
                    let symbol = String(message.dropFirst(4)).trimmingCharacters(in: .whitespacesAndNewlines)
                    self.quickAdd(symbol: symbol, client: client)
                } else if message.hasPrefix("conid=") {
                    let conidStr = String(message.dropFirst(6)).trimmingCharacters(in: .whitespacesAndNewlines)
                    if let conid = Int(conidStr) {
                        self.quickAdd(conid: conid, client: client)
                    }
                }
            }

            // Client disconnected
            try? client.close()
        }
    }

    private func quickAdd(symbol: String? = nil, conid: Int? = nil, client: ArgusSocket) {
        threadLock.lock()
        let blockNewData = configs["Block New MKT Data"] as? Bool ?? false
        threadLock.unlock()

        if blockNewData {
            print("New market data subscriptions are blocked")
            return
        }

        var finalConid = conid

        // If symbol provided, search for contract
        if let symbol = symbol {
            do {
                let results = try ws.networker.searchContract(contractName: symbol)
                guard let topHit = results.first(where: { $0.symbol.lowercased() == symbol.lowercased() }) else {
                    print("No contract found for symbol: \(symbol)")
                    return
                }
                finalConid = Int(topHit.conid)
            } catch {
                print("Failed to search contract: \(error)")
                return
            }
        }

        guard let contractId = finalConid else {
            print("No contract ID available")
            return
        }

        threadLock.lock()
        let needsSubscription = conidToClients[contractId] == nil

        if needsSubscription {
            conidToClients[contractId] = [client]
        } else {
            conidToClients[contractId]?.append(client)
        }
        threadLock.unlock()

        if needsSubscription {
            ws.streamMarketData(contractId: contractId) { [weak self] marketData in
                self?.callback(marketData: marketData)
            }
        }
    }

    private func callback(marketData: IBMarketData) {
        threadLock.lock()
        guard let clients = conidToClients[marketData.contractId] else {
            threadLock.unlock()
            return
        }
        threadLock.unlock()

        // Update cache
        updateCache(marketData)

        // Stuff from cache
        let stuffedData = stuffFromCache(marketData)

        // Convert to IBKR_CapitalComMKTDataLive and send with Protocol 2
        let ibkrData = IBKR_CapitalComMKTDataLive(
            symbol: (stuffedData.get(IBKRFields.SYMBOL, default: "UNKNOWN") as? String) ?? "UNKNOWN",
            bid: enforceCurrency(stuffedData.get(IBKRFields.BID_PRICE, default: 0.0) ?? 0.0),
            bidSize: enforceCurrency(stuffedData.get(IBKRFields.BID_SIZE, default: 0) ?? 0),
            ask: enforceCurrency(stuffedData.get(IBKRFields.ASK_PRICE, default: 0.0) ?? 0.0),
            askSize: enforceCurrency(stuffedData.get(IBKRFields.ASK_SIZE, default: 0) ?? 0),
            last: enforceCurrency(stuffedData.get(IBKRFields.LAST_PRICE, default: 0.0) ?? 0.0),
            lastSize: 0.0,  // Not available
            shortableShares: enforceCurrency(stuffedData.get(IBKRFields.SHORTABLE_SHARES, default: 0.0) ?? 0.0),
            unrealizedPnl: enforceCurrency(stuffedData.get(IBKRFields.FORMATTED_UNREALIZED_PNL, default: 0.0) ?? 0.0)
        )

        for client in clients {
            do {
                // Send Protocol 2 packet
                let packet = try transmitMarketDataWithProtocol2(ibkrData)

                if let printPackets = configs["Print data packets"] as? Bool, printPackets {
                    print("Sending packet: \(String(data: packet, encoding: .ascii) ?? "")")
                }

                try client.sendall(packet)
            } catch {
                print("Failed to send data to client: \(error)")

                threadLock.lock()
                if let index = clients.firstIndex(where: { $0 === client }) {
                    conidToClients[marketData.contractId]?.remove(at: index)
                }

                // Unsubscribe if no clients left
                if conidToClients[marketData.contractId]?.isEmpty == true {
                    conidToClients.removeValue(forKey: marketData.contractId)
                    ws.unsubscribeMarketData(contractId: marketData.contractId)
                }
                threadLock.unlock()
            }
        }
    }

    private func updateCache(_ marketData: IBMarketData) {
        threadLock.lock()
        defer { threadLock.unlock() }

        if caches[marketData.contractId] == nil {
            caches[marketData.contractId] = [:]
        }

        for field in cacheFields {
            if let value = marketData.get(field, default: nil, stripCommas: false, stringValues: false),
               !(value is NSNull) {
                caches[marketData.contractId]?[field] = value
            }
        }
    }

    private func stuffFromCache(_ marketData: IBMarketData) -> IBMarketData {
        threadLock.lock()
        defer { threadLock.unlock() }

        guard let cache = caches[marketData.contractId] else {
            return marketData
        }

        for field in cacheFields {
            let currentValue = marketData.get(field, default: nil, stripCommas: false, stringValues: false)
            if currentValue == nil || "\(currentValue ?? "")" == "None" {
                if let cachedValue = cache[field] {
                    marketData.data[String(field)] = cachedValue
                }
            }
        }

        return marketData
    }

    private func startHealthChecker() {
        DispatchQueue.global(qos: .background).async { [weak self] in
            while true {
                Thread.sleep(forTimeInterval: 5)
                guard let self = self else { return }

                self.threadLock.lock()
                let clientsCopy = self.conidToClients
                self.threadLock.unlock()

                for (contractId, clients) in clientsCopy {
                    for client in clients {
                        do {
                            try client.sendall("$".data(using: .ascii)!)
                        } catch {
                            // Client disconnected, remove it
                            self.threadLock.lock()
                            if let index = self.conidToClients[contractId]?.firstIndex(where: { $0 === client }) {
                                self.conidToClients[contractId]?.remove(at: index)
                            }

                            if self.conidToClients[contractId]?.isEmpty == true {
                                self.conidToClients.removeValue(forKey: contractId)
                                self.ws.unsubscribeMarketData(contractId: contractId)
                            }
                            self.threadLock.unlock()
                        }
                    }
                }
            }
        }
    }

    /// Select trading account interactively
    func selectAccountInteractive() throws {
        do {
            print("[Account Selection] Fetching available accounts...")
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

                // Initialize AccountProvider (for Protocol 2 mode)
                print("Initializing AccountProvider...")
                accountProvider = try AccountProvider(ibWss: ws, ibNetworker: ws.networker)

                // Unblock new market data
                threadLock.lock()
                configs["Block New MKT Data"] = false
                threadLock.unlock()

                print("New market data subscriptions are now unblocked.")

                // Add AccountProvider's required assets as protected subscriptions
                if let provider = accountProvider {
                    let requiredAssets = provider.requiredAssets()
                    print("Adding \(requiredAssets.count) protected assets from AccountProvider...")

                    for conid in requiredAssets {
                        quickAdd(symbol: nil, conid: conid, client: provider.socket)
                    }

                    print("Protected assets added from AccountProvider")

                    // Subscribe to portfolio updates
                    ws.sendMessage("upl+{}")
                    Thread.sleep(forTimeInterval: 1)
                }
            }
        } catch {
            print("FATAL: Failed to select account: \(error)")
            throw error
        }
    }

    /// Interactive mode
    func interactiveMode() {
        print("\nIBKR Dispatcher Interactive Mode")
        print("Enter commands (or 'exit' to quit):")
        print("Server is running. Press Ctrl+C to stop.")

        var eofDetected = false
        while true {
            if !eofDetected {
                print("> ", terminator: "")
                fflush(stdout)
            }

            guard let input = readLine()?.trimmingCharacters(in: .whitespacesAndNewlines) else {
                // EOF detected (stdin closed)
                if !eofDetected {
                    print("\nEOF detected. Server will continue running in background.")
                    print("Press Ctrl+C to stop.")
                    eofDetected = true
                }
                // Sleep to avoid tight loop
                Thread.sleep(forTimeInterval: 1.0)
                continue
            }

            if input.lowercased() == "exit" {
                print("Shutting down...")
                break
            }

            if !input.isEmpty {
                print("Unknown command: \(input)")
            }
        }
    }
}
