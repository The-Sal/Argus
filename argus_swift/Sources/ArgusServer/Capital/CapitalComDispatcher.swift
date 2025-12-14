import Foundation

/// Unix Domain Socket server dispatcher for Capital.com market data streaming
class CapitalComMKTDispatcher {
    private let socketPath: String
    private var serverSocket: Int32 = -1

    // WebSocket connection
    private let ws: CapitalComWss
    private var authTokens: CapitalComAuthTokens?

    // REST API credentials
    private let apiKey: String
    private let identifier: String
    private let password: String
    private let environment: Environment

    // Client management
    private var clients: [ArgusSocket] = []
    private var symbolToClients: [String: [ArgusSocket]] = [:]
    private var symbolDataCache: [String: CapitalCom_CapitalComMKTDataLive] = [:]

    // Symbol resolution cache
    private var epicStreams: [String: Bool] = [:]

    private let threadLock = NSLock()
    private var isRunning = false

    init(socketPath: String = "/tmp/argus_capital.sock",
         apiKey: String, identifier: String, password: String,
         environment: Environment = .demo) {
        self.socketPath = socketPath
        self.apiKey = apiKey
        self.identifier = identifier
        self.password = password
        self.environment = environment
        self.ws = CapitalComWss()
    }

    // MARK: - Server Lifecycle

    /// Start the dispatcher in interactive mode
    func interactiveMode() {
        print("Capital.com Market Data Dispatcher")
        print("===================================")
        print("Environment: \(environment.rawValue)")
        print("Socket: \(socketPath)")
        print()

        // Login to Capital.com API
        guard login() else {
            print("Failed to login to Capital.com API. Check credentials.")
            return
        }

        print("Successfully logged in to Capital.com API")
        print()

        // Start token refresh timer (every 8 minutes to stay ahead of expiration)
        startTokenRefreshTimer()

        // Start TCP server
        do {
            try startServer()
        } catch {
            print("Failed to start server: \(error)")
            return
        }

        print("Server started successfully")
        print()
        print("Commands:")
        print("  add <epic>     - Subscribe to market data for an epic")
        print("  remove <epic>  - Unsubscribe from market data")
        print("  list           - List subscribed epics")
        print("  quit           - Stop server and exit")
        print()

        isRunning = true

        // Start client listener thread
        startClientListener()

        // Start health checker thread
        startHealthChecker()

        // Interactive command loop
        commandLoop()
    }

    private func startTokenRefreshTimer() {
        // Refresh tokens every 8 minutes (session typically expires after 10 min)
        // Note: We DON'T disconnect/reconnect the WebSocket! The application pings
        // (every 9 min) keep the connection alive, and we just update the tokens
        // that will be used in future subscription/ping messages.
        DispatchQueue.global(qos: .background).async { [weak self] in
            while self?.isRunning == true {
                Thread.sleep(forTimeInterval: 8 * 60) // 8 minutes

                guard let self = self, self.isRunning else { break }

                print("Refreshing authentication tokens...")
                if self.login() {
                    print("Tokens refreshed successfully")
                    // Update the WebSocket's auth tokens without disconnecting
                    if let tokens = self.authTokens {
                        self.ws.updateAuthTokens(tokens)
                    }
                } else {
                    print("Token refresh failed")
                }
            }
        }
    }

    private func commandLoop() {
        while isRunning {
            print("> ", terminator: "")
            fflush(stdout)

            guard let input = readLine() else {
                // readLine() returned nil - stdin closed or EOF
                print("\nStdin closed, shutting down...")
                isRunning = false
                cleanup()
                return
            }

            let trimmedInput = input.trimmingCharacters(in: .whitespaces)
            guard !trimmedInput.isEmpty else {
                continue
            }

            let parts = trimmedInput.split(separator: " ", maxSplits: 1).map(String.init)
            guard let command = parts.first else { continue }

            switch command.lowercased() {
            case "add":
                if parts.count >= 2 {
                    let epic = parts[1]
                    manualAdd(epic: epic)
                } else {
                    print("Usage: add <epic>")
                }

            case "remove":
                if parts.count >= 2 {
                    let epic = parts[1]
                    manualRemove(epic: epic)
                } else {
                    print("Usage: remove <epic>")
                }

            case "list":
                listSubscriptions()

            case "quit", "exit":
                print("Shutting down...")
                isRunning = false
                cleanup()
                return

            default:
                print("Unknown command: \(command)")
            }
        }

        // If we exit the loop for any reason, cleanup
        print("Command loop exited unexpectedly")
        cleanup()
    }

    // MARK: - Authentication

    private func login() -> Bool {
        let loginURL = "\(environment.baseURL)/session"

        guard let url = URL(string: loginURL) else {
            print("Invalid login URL")
            return false
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue(apiKey, forHTTPHeaderField: "X-CAP-API-KEY")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let loginPayload: [String: Any] = [
            "identifier": identifier,
            "password": password,
            "encryptedPassword": false
        ]

        guard let jsonData = try? JSONSerialization.data(withJSONObject: loginPayload) else {
            print("Failed to serialize login payload")
            return false
        }

        request.httpBody = jsonData

        let semaphore = DispatchSemaphore(value: 0)
        var success = false

        let task = URLSession.shared.dataTask(with: request) { [weak self] data, response, error in
            defer { semaphore.signal() }

            if let error = error {
                print("Login request error: \(error)")
                return
            }

            guard let httpResponse = response as? HTTPURLResponse else {
                print("Invalid response type")
                return
            }

            if httpResponse.statusCode == 200 {
                // Extract auth tokens from headers
                if let cst = httpResponse.value(forHTTPHeaderField: "CST"),
                   let xst = httpResponse.value(forHTTPHeaderField: "X-SECURITY-TOKEN") {
                    self?.authTokens = CapitalComAuthTokens(cst: cst, xSecurityToken: xst)

                    // Connect WebSocket
                    self?.ws.connect(authTokens: CapitalComAuthTokens(cst: cst, xSecurityToken: xst))

                    success = true
                } else {
                    print("Auth tokens not found in response headers")
                }
            } else {
                print("Login failed with status code: \(httpResponse.statusCode)")
                if let data = data, let responseText = String(data: data, encoding: .utf8) {
                    print("Response: \(responseText)")
                }
            }
        }

        task.resume()
        semaphore.wait()

        return success
    }

    // MARK: - TCP Server

    private func startServer() throws {
        // Remove existing socket file if it exists
        let fileManager = FileManager.default
        if fileManager.fileExists(atPath: socketPath) {
            try? fileManager.removeItem(atPath: socketPath)
        }

        // Create Unix domain socket
        serverSocket = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)


        guard serverSocket >= 0 else {
            throw CapitalComAPIError.networkError("Failed to create Unix domain socket")
        }

        // Bind socket to path
        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)

        let pathBytes = socketPath.utf8CString
        guard pathBytes.count <= MemoryLayout.size(ofValue: addr.sun_path) else {
            throw CapitalComAPIError.networkError("Socket path too long: \(socketPath)")
        }

        withUnsafeMutablePointer(to: &addr.sun_path) { ptr in
            ptr.withMemoryRebound(to: Int8.self, capacity: pathBytes.count) { dest in
                for (i, byte) in pathBytes.enumerated() {
                    dest[i] = byte
                }
            }
        }

        let bindResult = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                #if canImport(Darwin)
                Darwin.bind(serverSocket, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
                #else
                Glibc.bind(serverSocket, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
                #endif
            }
        }

        guard bindResult == 0 else {
            throw CapitalComAPIError.networkError("Failed to bind socket to \(socketPath)")
        }

        // Listen
        #if canImport(Darwin)
        guard Darwin.listen(serverSocket, 5) == 0 else {
            throw CapitalComAPIError.networkError("Failed to listen on socket")
        }
        #else
        guard Glibc.listen(serverSocket, 5) == 0 else {
            throw CapitalComAPIError.networkError("Failed to listen on socket")
        }
        #endif
    }

    private func startClientListener() {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            self?.clientListenerLoop()
        }
    }

    private func clientListenerLoop() {
        print("Client listener started on \(socketPath)")

        while isRunning {
            var clientAddr = sockaddr_un()
            var clientAddrLen = socklen_t(MemoryLayout<sockaddr_un>.size)

            let clientSocket = withUnsafeMutablePointer(to: &clientAddr) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    Darwin.accept(serverSocket, $0, &clientAddrLen)
                }
            }

            guard clientSocket >= 0 else {
                if isRunning {
                    let error = errno
                    print("Accept failed with errno: \(error)")
                    if error == EBADF || error == EINVAL {
                        print("Server socket invalid, stopping client listener")
                        break
                    }
                }
                continue
            }

            print("Client connected: \(clientSocket)")

            let client = RealSocket(fileDescriptor: clientSocket)
            client.idx = String(clientSocket)

            threadLock.lock()
            clients.append(client)
            threadLock.unlock()

            // Start client handler thread
            DispatchQueue.global(qos: .userInitiated).async { [weak self] in
                self?.handleClient(client)
            }
        }

        print("Client listener loop exited")
    }

    private func handleClient(_ client: ArgusSocket) {
        let bufferSize = 4096
        var buffer = [UInt8](repeating: 0, count: bufferSize)

        while isRunning {
            let bytesRead = recv((client as! RealSocket).fileDescriptor, &buffer, bufferSize, 0)

            if bytesRead <= 0 {
                // Client disconnected
                removeClient(client)
                break
            }

            let data = Data(buffer.prefix(bytesRead))
            handleClientData(data, from: client)
        }
    }

    private func handleClientData(_ data: Data, from client: ArgusSocket) {
        do {
            // Decode packets (may be multiple)
            let packets = try decodeMultiplePackets(data)

            for packetData in packets {
                // Parse JSON request
                guard let json = try? JSONSerialization.jsonObject(with: packetData) as? [String: Any],
                      let action = json["action"] as? String else {
                    sendErrorResponse(to: client, message: "Invalid request format")
                    continue
                }

                handleClientRequest(action: action, request: json, client: client)
            }
        } catch {
            print("Error decoding packet: \(error)")
            sendErrorResponse(to: client, message: "Invalid packet format")
        }
    }

    private func handleClientRequest(action: String, request: [String: Any], client: ArgusSocket) {
        switch action {
        case "resolve_symbol":
            guard let symbol = request["symbol"] as? String else {
                sendErrorResponse(to: client, message: "Missing 'symbol' parameter")
                return
            }
            resolveSymbol(symbol: symbol, client: client)

        case "stream_epic":
            guard let epic = request["epic"] as? String else {
                sendErrorResponse(to: client, message: "Missing 'epic' parameter")
                return
            }
            subscribeToEpic(epic: epic, client: client)
            sendSuccessResponse(to: client, message: "Subscribed to \(epic)")

        case "resolve/stream":
            guard let symbol = request["symbol"] as? String else {
                sendErrorResponse(to: client, message: "Missing 'symbol' parameter")
                return
            }
            resolveAndStreamSymbol(symbol: symbol, client: client)

        case "unsubscribe":
            guard let epic = request["epic"] as? String else {
                sendErrorResponse(to: client, message: "Missing 'epic' parameter")
                return
            }
            unsubscribeFromEpic(epic: epic, client: client)
            sendSuccessResponse(to: client, message: "Unsubscribed from \(epic)")

        default:
            sendErrorResponse(to: client, message: "Unknown action: \(action)")
        }
    }

    private func sendResponse(to client: ArgusSocket, response: [String: Any]) {
        do {
            let jsonData = try JSONSerialization.data(withJSONObject: response)
            let packet = try encodePacket(jsonData)
            try client.sendall(packet)
        } catch {
            print("Failed to send response: \(error)")
        }
    }

    private func sendSuccessResponse(to client: ArgusSocket, message: String, data: [String: Any]? = nil) {
        var response: [String: Any] = [
            "status": "success",
            "message": message
        ]
        if let data = data {
            response["data"] = data
        }
        sendResponse(to: client, response: response)
    }

    private func sendErrorResponse(to client: ArgusSocket, message: String) {
        let response: [String: Any] = [
            "status": "error",
            "message": message
        ]
        sendResponse(to: client, response: response)
    }

    // MARK: - Symbol Resolution

    private func resolveSymbol(symbol: String, client: ArgusSocket) {
        guard let tokens = authTokens else {
            sendErrorResponse(to: client, message: "Not authenticated")
            return
        }

        let searchURL = "\(environment.baseURL)/markets?searchTerm=\(symbol)"

        guard let url = URL(string: searchURL) else {
            sendErrorResponse(to: client, message: "Invalid URL")
            return
        }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue(tokens.cst, forHTTPHeaderField: "CST")
        request.setValue(tokens.xSecurityToken, forHTTPHeaderField: "X-SECURITY-TOKEN")

        let task = URLSession.shared.dataTask(with: request) { [weak self] data, response, error in
            guard let self = self else { return }

            if let error = error {
                self.sendErrorResponse(to: client, message: "Search failed: \(error.localizedDescription)")
                return
            }

            guard let httpResponse = response as? HTTPURLResponse,
                  httpResponse.statusCode == 200,
                  let data = data,
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let markets = json["markets"] as? [[String: Any]],
                  let firstMarket = markets.first else {
                self.sendErrorResponse(to: client, message: "Symbol not found")
                return
            }

            // Extract instrument data
            let instrumentData: [String: Any] = [
                "epic": firstMarket["epic"] as? String ?? "",
                "instrumentName": firstMarket["instrumentName"] as? String ?? "",
                "marketStatus": firstMarket["marketStatus"] as? String ?? ""
            ]

            self.sendSuccessResponse(to: client, message: "Symbol resolved", data: ["instrument": instrumentData])
        }

        task.resume()
    }

    private func resolveAndStreamSymbol(symbol: String, client: ArgusSocket) {
        guard let tokens = authTokens else {
            sendErrorResponse(to: client, message: "Not authenticated")
            return
        }

        let searchURL = "\(environment.baseURL)/markets?searchTerm=\(symbol)"

        guard let url = URL(string: searchURL) else {
            sendErrorResponse(to: client, message: "Invalid URL")
            return
        }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue(tokens.cst, forHTTPHeaderField: "CST")
        request.setValue(tokens.xSecurityToken, forHTTPHeaderField: "X-SECURITY-TOKEN")

        let task = URLSession.shared.dataTask(with: request) { [weak self] data, response, error in
            guard let self = self else { return }

            if let error = error {
                self.sendErrorResponse(to: client, message: "Search failed: \(error.localizedDescription)")
                return
            }

            guard let httpResponse = response as? HTTPURLResponse,
                  httpResponse.statusCode == 200,
                  let data = data,
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let markets = json["markets"] as? [[String: Any]],
                  let firstMarket = markets.first,
                  let epic = firstMarket["epic"] as? String else {
                self.sendErrorResponse(to: client, message: "Symbol not found")
                return
            }

            // Extract instrument data
            let instrumentData: [String: Any] = [
                "epic": epic,
                "instrumentName": firstMarket["instrumentName"] as? String ?? "",
                "marketStatus": firstMarket["marketStatus"] as? String ?? ""
            ]

            // Subscribe to the epic
            self.subscribeToEpic(epic: epic, client: client)

            // Send success response with instrument data
            self.sendSuccessResponse(to: client, message: "Subscribed to \(epic)", data: ["instrument": instrumentData])
        }

        task.resume()
    }

    // MARK: - Subscription Management

    private func subscribeToEpic(epic: String, client: ArgusSocket) {
        threadLock.lock()

        let needsSubscription = symbolToClients[epic] == nil

        if needsSubscription {
            symbolToClients[epic] = [client]
        } else {
            if !symbolToClients[epic]!.contains(where: { $0.idx == client.idx }) {
                symbolToClients[epic]?.append(client)
            }
        }

        threadLock.unlock()

        if needsSubscription {
            // Subscribe to WebSocket
            ws.subscribeToMarketData(epic: epic) { [weak self] marketData in
                self?.capitalComCallback(epic: epic, marketData: marketData)
            }

            epicStreams[epic] = true
            print("Subscribed to \(epic) for client \(client.idx)")
        }
    }

    private func unsubscribeFromEpic(epic: String, client: ArgusSocket) {
        threadLock.lock()
        defer { threadLock.unlock() }

        if var clientList = symbolToClients[epic] {
            clientList.removeAll { $0.idx == client.idx }

            if clientList.isEmpty {
                symbolToClients.removeValue(forKey: epic)
                ws.unsubscribeFromMarketData(epic: epic)
                epicStreams[epic] = false
                print("Unsubscribed from \(epic)")
            } else {
                symbolToClients[epic] = clientList
            }
        }
    }

    private func manualAdd(epic: String) {
        let fakeSocket = FakeSocket { data in
            print("Manual subscription data: \(data.count) bytes")
        }
        fakeSocket.idx = "manual-\(epic)"

        subscribeToEpic(epic: epic, client: fakeSocket)
    }

    private func manualRemove(epic: String) {
        ws.unsubscribeFromMarketData(epic: epic)
        epicStreams[epic] = false

        threadLock.lock()
        symbolToClients.removeValue(forKey: epic)
        threadLock.unlock()

        print("Unsubscribed from \(epic)")
    }

    private func listSubscriptions() {
        threadLock.lock()
        let epics = Array(symbolToClients.keys)
        threadLock.unlock()

        if epics.isEmpty {
            print("No active subscriptions")
        } else {
            print("Active subscriptions (\(epics.count)):")
            for epic in epics.sorted() {
                print("  - \(epic)")
            }
        }
    }

    // MARK: - Data Handling

    private func capitalComCallback(epic: String, marketData: CapitalCom_CapitalComMKTDataLive) {
        threadLock.lock()

        // Update cache
        symbolDataCache[epic] = marketData

        // Get clients subscribed to this epic
        guard let clients = symbolToClients[epic] else {
            threadLock.unlock()
            return
        }

        threadLock.unlock()

        // Convert market data to JSON format
        let marketDataDict: [String: Any] = [
            "symbol": marketData.symbol,
            "bid": marketData.bid,
            "bid_size": marketData.bidSize,
            "ask": marketData.ask,
            "ask_size": marketData.askSize,
            "last": marketData.last,
            "last_size": marketData.lastSize,
            "timestamp": marketData.timestamp
        ]

        // Send as JSON packet to all clients
        do {
            let jsonData = try JSONSerialization.data(withJSONObject: marketDataDict)
            let packet = try encodePacket(jsonData)

            for client in clients {
                do {
                    try client.sendall(packet)
                } catch {
                    print("Failed to send to client \(client.idx): \(error)")
                    removeClient(client)
                }
            }
        } catch {
            print("Failed to encode market data: \(error)")
        }
    }

    private func removeClient(_ client: ArgusSocket) {
        threadLock.lock()
        defer { threadLock.unlock() }

        clients.removeAll { $0.idx == client.idx }

        // Remove client from all symbol subscriptions
        for (epic, clientList) in symbolToClients {
            let filtered = clientList.filter { $0.idx != client.idx }

            if filtered.isEmpty {
                symbolToClients.removeValue(forKey: epic)
                ws.unsubscribeFromMarketData(epic: epic)
                epicStreams[epic] = false
            } else {
                symbolToClients[epic] = filtered
            }
        }

        client.close()
        print("Client \(client.idx) removed")
    }

    // MARK: - Health Checker

    private func startHealthChecker() {
        DispatchQueue.global(qos: .background).async { [weak self] in
            while self?.isRunning == true {
                Thread.sleep(forTimeInterval: 30)
                self?.pingClients()
            }
        }
    }

    private func pingClients() {
        threadLock.lock()
        let currentClients = clients
        threadLock.unlock()

        let pingByte = Data([0x24])  // '$' byte

        for client in currentClients {
            do {
                try client.sendall(pingByte)
            } catch {
                print("Client \(client.idx) failed ping, removing")
                removeClient(client)
            }
        }
    }

    // MARK: - Cleanup

    private func cleanup() {
        isRunning = false

        // Disconnect WebSocket
        ws.disconnect()

        // Close all client connections
        threadLock.lock()
        for client in clients {
            client.close()
        }
        clients.removeAll()
        threadLock.unlock()

        // Close server socket
        if serverSocket >= 0 {
            #if canImport(Darwin)
            Darwin.close(serverSocket)
            #else
            Glibc.close(serverSocket)
            #endif
            serverSocket = -1
        }

        print("Server stopped")
    }
}
