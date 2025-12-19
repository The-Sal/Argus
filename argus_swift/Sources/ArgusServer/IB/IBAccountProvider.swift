import Foundation

class ShortableSharesData {
    private var symbolToConidMap: [String: Int] = [:]
    private let lock = NSLock()

    func registerSymbol(_ symbol: String, conid: Int) {
        lock.lock()
        defer { lock.unlock() }
        symbolToConidMap[symbol] = conid
    }

    func translateSymbolToConid(_ symbol: String?) -> Int? {
        guard let symbol = symbol else { return nil }
        lock.lock()
        defer { lock.unlock() }
        return symbolToConidMap[symbol]
    }
}

/// Account Provider - provides live-streaming support for account positions and PnL
/// Transcompiled from argus/ib/__init__.py AccountProvider class
class AccountProvider {
    private let ibWss: IBWss
    private let ibNetworker: IBNetworker
    private var accountPositions: [STKPosition]
    private var accountLedger: [String: Any]

    // Portfolio: conid -> STKPosition
    private var portfolio: [Int: STKPosition] = [:]
    private var accountBalances: AccountBalances?

    private var symbolsToConids: [String: Int] = [:]
    let shortableSharesData = ShortableSharesData()

    private var fakeSocket: FakeSocketForIBKR!

    // Debug socket for streaming position updates (port 9973)
    private var debugSocket: Int32 = -1
    private var debugClients: Set<Int32> = []
    private let debugLock = NSLock()
    private var lastSend = Date()

    init(ibWss: IBWss, ibNetworker: IBNetworker) throws {
        guard ibNetworker.accountId != nil else {
            throw IBError.authenticationError("Trading account ID is not set in IBNetworker")
        }

        self.ibWss = ibWss
        self.ibNetworker = ibNetworker
        self.accountPositions = try ibNetworker.fetchAccountPositions()
        self.accountLedger = try ibNetworker.getAccountLedger()

        print(String(repeating: "*", count: 50))
        print("ACCOUNT POSITIONS:")
        print(accountPositions)
        print(String(repeating: "*", count: 50))

        // Create FakeSocket for receiving market data
        // See FakeSocketForIBKR why this will be of type Any and the prior issues
        self.fakeSocket = FakeSocketForIBKR { [weak self] data in
            self?.onMarketData(data)
        }

        populateConids()
        ibWss.subscribeToPortfolio { [weak self] balances in
            self?.onAccountBalances(balances)
        }

        // Setup debug socket on port 9973
        setupDebugSocket()
        startPropagateThread()
    }

    private func setupDebugSocket() {
        debugSocket = Darwin.socket(AF_INET, SOCK_STREAM, 0)
        guard debugSocket >= 0 else {
            print("Failed to create debug socket")
            return
        }

        var reuseAddr: Int32 = 1
        setsockopt(debugSocket, SOL_SOCKET, SO_REUSEADDR, &reuseAddr, socklen_t(MemoryLayout<Int32>.size))

        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = UInt16(9973).bigEndian
        addr.sin_addr.s_addr = INADDR_ANY.bigEndian

        let bindResult = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(debugSocket, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }

        if bindResult < 0 {
            print("Failed to bind debug socket to port 9973")
            close(debugSocket)
            debugSocket = -1
            return
        }

        print("Debug socket listening on port 9973")
        startDebugListener()
    }

    private func startDebugListener() {
        DispatchQueue.global(qos: .background).async { [weak self] in
            guard let self = self, self.debugSocket >= 0 else { return }

            listen(self.debugSocket, 5)

            while true {
                var clientAddr = sockaddr_in()
                var clientAddrLen = socklen_t(MemoryLayout<sockaddr_in>.size)

                let clientSocket = withUnsafeMutablePointer(to: &clientAddr) {
                    $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                        accept(self.debugSocket, $0, &clientAddrLen)
                    }
                }

                if clientSocket >= 0 {
                    self.debugLock.lock()
                    self.debugClients.insert(clientSocket)
                    self.debugLock.unlock()
                    print("Debug client connected: \(clientSocket)")
                }

                Thread.sleep(forTimeInterval: 0.1)
            }
        }
    }

    private func startPropagateThread() {
        DispatchQueue.global(qos: .background).async { [weak self] in
            while true {
                Thread.sleep(forTimeInterval: 10)
                guard let self = self else { return }

                // Propagate entire portfolio and account balances every 10 seconds
                for position in self.portfolio.values {
                    self.transmit(position: position)
                }
                self.transmit(position: nil)
            }
        }
    }

    private func debugPropagate(_ message: String) {
        debugLock.lock()
        let clients = Array(debugClients)
        debugLock.unlock()

        guard let data = message.data(using: .utf8) else { return }

        for clientSocket in clients {
            let result = data.withUnsafeBytes { (ptr: UnsafeRawBufferPointer) -> Int in
                send(clientSocket, ptr.baseAddress, data.count, 0)
            }

            if result <= 0 {
                debugLock.lock()
                debugClients.remove(clientSocket)
                debugLock.unlock()
                close(clientSocket)
            }
        }
    }

    private func transmit(position: STKPosition?) {
        debugLock.lock()
        lastSend = Date()
        debugLock.unlock()

        let data: [String: Any]
        if let position = position {
            data = [
                "type": "position",
                "data": position.toDict()
            ]
        } else {
            guard let balances = accountBalances else { return }
            data = [
                "type": "account_balances",
                "data": balances.toDict()
            ]
        }

        if let jsonData = try? JSONSerialization.data(withJSONObject: data),
           let jsonStr = String(data: jsonData, encoding: .utf8) {
            debugPropagate("~\(jsonStr)L")
        }
    }

    private func onAccountBalances(_ data: AccountBalances) {
        if data.netLiquidation == nil {
            return
        }
        accountBalances = data
        transmit(position: nil)
    }

    private func onMarketData(_ data: Any) {
        // Handle market data received via FakeSocket
        guard let marketData = data as? IBKR_CapitalComMKTDataLive else {
            // Could be a ping
            if let str = data as? String, str == "$" { return }
            if let bytes = data as? Data, bytes == "$".data(using: .utf8) { return }
            print("WARNING: Unexpected data received within AccountProvider ==> \(data)")
            return
        }

        let symbol = marketData.symbol
        guard let contractId = shortableSharesData.translateSymbolToConid(symbol) else {
            print("Could not translate symbol \(symbol) to contract ID")
            return
        }

        guard let position = portfolio[contractId] else {
            return
        }

        let cost = enforceCurrency(position.avgCost)
        if marketData.last != 0 {
            let pnl = (enforceCurrency(marketData.last) - cost) * position.position
            position.formattedUnrealizedPnl = String(format: "%.2f", pnl)
            position.unrealizedPnl = pnl
            transmit(position: position)
        }
    }

    func requiredAssets() -> [Int] {
        return Array(portfolio.keys)
    }

    private func populateConids() {
        for position in accountPositions {
            portfolio[position.conid] = position
            symbolsToConids[position.contractDesc] = position.conid
            shortableSharesData.registerSymbol(position.contractDesc, conid: position.conid)
        }

        ibWss.writeProtectedAssets(Array(portfolio.keys))
    }

    var socket: FakeSocketForIBKR {
        return fakeSocket
    }

    var accountPositionsList: [STKPosition] {
        return Array(portfolio.values)
    }

    var currentAccountBalances: AccountBalances? {
        return accountBalances
    }
}
