import Foundation
#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#endif

/// Market Data Dispatcher for Binance
/// Manages client connections via TCP and streams Binance market data using Protocol 2
/// Transcompiled from argus/binance/__init__.py: MKTDispatcher
class MKTDispatcher {
    private let host: String
    private let port: Int
    private let checkpointURL: String?

    // WebSocket manager
    private let ws: BinanceWss

    // TCP server socket
    private var serverSocket: Int32 = -1

    // Client management
    private var clients: [ArgusSocket] = []
    private var symbolToClients: [String: [ArgusSocket]] = [:]

    // Thread lock for client operations
    private let clientLock = NSLock()

    // Configuration
    private var configs: [String: Bool] = [
        "Print data packets": false,
        "Show client messages": true,
        "Show live stream": false
    ]

    // Live stream monitoring
    private var liveStreamSymbol: String?

    // Logger
    private let logger = Logger(subsystem: "MKTDispatcher")

    // Running flag
    private var running = false

    init(
        host: String = "localhost",
        port: Int = 9974,
        apiKey: String? = nil,
        apiSecret: String? = nil,
        testnet: Bool = false,
        checkpointURL: String? = nil
    ) {
        self.host = host
        self.port = port
        self.checkpointURL = checkpointURL

        // Initialize WebSocket manager
        self.ws = BinanceWss(apiKey: apiKey, apiSecret: apiSecret, testnet: testnet)

        logger.info("MKTDispatcher initialized on \(host):\(port)")
        checkpoint(taskName: "MKTDispatcher.__init__", status: "complete")
    }

    /// Send checkpoint notification
    private func checkpoint(taskName: String, status: String) {
        guard let urlString = checkpointURL,
              let url = URL(string: urlString) else {
            return
        }

        let payload: [String: String] = [
            "task_name": taskName,
            "status": status
        ]

        guard let jsonData = try? JSONSerialization.data(withJSONObject: payload) else {
            return
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.httpBody = jsonData
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 5

        let task = URLSession.shared.dataTask(with: request) { _, _, error in
            if let error = error {
                print("Checkpoint notification failed: \(error)")
            }
        }
        task.resume()
    }

    /// Start the dispatcher: WebSocket manager and client listener
    func start() {
        checkpoint(taskName: "MKTDispatcher.start", status: "start")

        // Start WebSocket manager
        ws.start()

        // Create TCP server socket
        do {
            try createServerSocket()
        } catch {
            logger.error("Failed to create server socket: \(error)")
            return
        }

        // Start client listener
        startClientListener()

        // Start client health checker
        startClientHealthChecker()

        logger.info("MKTDispatcher running on \(host):\(port)")
        checkpoint(taskName: "MKTDispatcher.start", status: "complete")
    }

    /// Create and bind TCP server socket
    private func createServerSocket() throws {
        // Create socket
        serverSocket = socket(AF_INET, SOCK_STREAM, 0)
        guard serverSocket >= 0 else {
            throw SocketError.bindFailed
        }

        // Set SO_REUSEADDR
        var reuseAddr: Int32 = 1
        setsockopt(serverSocket, SOL_SOCKET, SO_REUSEADDR, &reuseAddr, socklen_t(MemoryLayout<Int32>.size))

        // Bind socket
        var serverAddr = sockaddr_in()
        serverAddr.sin_family = sa_family_t(AF_INET)
        serverAddr.sin_port = UInt16(port).bigEndian

        if host == "localhost" || host == "127.0.0.1" {
            serverAddr.sin_addr.s_addr = INADDR_LOOPBACK.bigEndian
        } else {
            serverAddr.sin_addr.s_addr = INADDR_ANY.bigEndian
        }

        let bindResult = withUnsafePointer(to: &serverAddr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(serverSocket, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }

        guard bindResult >= 0 else {
            throw SocketError.bindFailed
        }

        logger.info("Server socket bound to \(host):\(port)")
    }

    /// Start listening for incoming client connections
    private func startClientListener() {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }

            // CRITICAL: Wait to ensure WebSocket is fully ready before accepting clients
            Thread.sleep(forTimeInterval: 1.0)
            self.logger.info("Client listener ready to accept connections")

            while true {
                // Listen
                let listenResult = listen(self.serverSocket, 5)
                guard listenResult >= 0 else {
                    self.logger.error("Listen failed")
                    break
                }

                // Accept client connection
                var clientAddr = sockaddr_in()
                var addrLen = socklen_t(MemoryLayout<sockaddr_in>.size)

                let clientFD = withUnsafeMutablePointer(to: &clientAddr) {
                    $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                        accept(self.serverSocket, $0, &addrLen)
                    }
                }

                guard clientFD >= 0 else {
                    continue
                }

                self.logger.info("Client connected from fd=\(clientFD)")

                let clientSocket = RealSocket(fileDescriptor: clientFD)

                self.clientLock.lock()
                self.clients.append(clientSocket)
                self.clientLock.unlock()

                // Start listening to this client
                self.listenToClient(clientSocket)
            }
        }
    }

    /// Listen to client requests and handle subscriptions
    private func listenToClient(_ client: ArgusSocket) {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }

            // Buffer for receiving data
            var buffer = [UInt8](repeating: 0, count: 4096)

            while true {
                guard let realSocket = client as? RealSocket else {
                    // FakeSocket doesn't receive data
                    break
                }

                // Receive data
                let bytesRead = recv(
                    realSocket.idx == "real" ? 0 : 0,  // Will be fixed with proper fd access
                    &buffer,
                    buffer.count,
                    0
                )

                if bytesRead <= 0 {
                    break
                }

                let data = Data(buffer[0..<bytesRead])
                guard let message = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) else {
                    continue
                }

                if self.configs["Show client messages"] == true {
                    self.logger.info("Client request: \(message)")
                }

                // Parse client request
                if message.hasPrefix("add=") {
                    let symbol = String(message.dropFirst(4)).uppercased()
                    self.addSymbol(symbol: symbol, client: client)
                } else if message.hasPrefix("remove=") {
                    let symbol = String(message.dropFirst(7)).uppercased()
                    self.removeSymbol(symbol: symbol, client: client)
                }
            }

            // Client disconnected
            self.cleanupClient(client)
        }
    }

    /// Add a symbol subscription for a client
    private func addSymbol(symbol: String, client: ArgusSocket) {
        checkpoint(taskName: "MKTDispatcher.add_symbol(\(symbol))", status: "start")

        var needsSubscription = false

        clientLock.lock()
        if symbolToClients[symbol] == nil {
            // First client for this symbol - add client FIRST, then subscribe to WebSocket
            symbolToClients[symbol] = [client]
            needsSubscription = true
        } else {
            // Already subscribed, just add client to list
            if !symbolToClients[symbol]!.contains(where: { $0 === client }) {
                symbolToClients[symbol]?.append(client)
                logger.info("Added client to existing \(symbol) subscription")
            }
        }
        clientLock.unlock()

        // Subscribe to WebSocket OUTSIDE of client_lock to avoid deadlock
        if needsSubscription {
            let callback: (BinanceMarketData) -> Void = { [weak self] marketData in
                self?.broadcastMarketData(symbol: symbol, marketData: marketData)
            }

            ws.subscribeTicker(symbol: symbol, callback: callback)
            logger.info("Subscribed to \(symbol) for client")
        }

        checkpoint(taskName: "MKTDispatcher.add_symbol(\(symbol))", status: "complete")
    }

    /// Remove a symbol subscription for a client
    private func removeSymbol(symbol: String, client: ArgusSocket) {
        clientLock.lock()
        defer { clientLock.unlock() }

        guard var clients = symbolToClients[symbol] else {
            return
        }

        clients.removeAll { $0 === client }

        if clients.isEmpty {
            // No more clients, unsubscribe from WebSocket
            ws.unsubscribeTicker(symbol: symbol)
            symbolToClients.removeValue(forKey: symbol)
            logger.info("Unsubscribed from \(symbol) (no more clients)")
        } else {
            symbolToClients[symbol] = clients
        }
    }

    /// Broadcast market data to all subscribed clients using Protocol 2
    private func broadcastMarketData(symbol: String, marketData: BinanceMarketData) {
        clientLock.lock()
        defer { clientLock.unlock() }

        guard let clients = symbolToClients[symbol], !clients.isEmpty else {
            return
        }

        // Convert to Protocol 2 format
        let capitalData = marketData.toCapitalComFormat()

        do {
            let packet = try transmitMarketDataWithProtocol2(capitalData)

            if configs["Print data packets"] == true {
                logger.info("Broadcasting \(symbol): \(marketData.description)")
            }

            // Live stream display
            if configs["Show live stream"] == true && symbol == liveStreamSymbol {
                print("\r[LIVE] \(symbol): Bid=\(marketData.bid) Ask=\(marketData.ask) Last=\(marketData.last)", terminator: "")
                fflush(stdout)
            }

            // Send to all clients
            for client in clients {
                do {
                    try client.sendall(packet)
                } catch {
                    logger.warning("Client disconnected during broadcast: \(error)")
                    cleanupClient(client)
                }
            }
        } catch {
            logger.error("Failed to encode market data: \(error)")
        }
    }

    /// Remove a client and clean up its subscriptions
    private func cleanupClient(_ client: ArgusSocket) {
        clientLock.lock()
        defer { clientLock.unlock() }

        clients.removeAll { $0 === client }

        // Remove from all symbol subscriptions
        for symbol in Array(symbolToClients.keys) {
            symbolToClients[symbol]?.removeAll { $0 === client }

            // Unsubscribe if no more clients
            if symbolToClients[symbol]?.isEmpty == true {
                ws.unsubscribeTicker(symbol: symbol)
                symbolToClients.removeValue(forKey: symbol)
            }
        }

        client.close()
    }

    /// Periodically check if clients are still connected
    private func startClientHealthChecker() {
        DispatchQueue.global(qos: .utility).async { [weak self] in
            while true {
                Thread.sleep(forTimeInterval: 5)

                guard let self = self else { return }

                self.clientLock.lock()
                let clientsCopy = self.clients
                self.clientLock.unlock()

                for client in clientsCopy {
                    do {
                        // Send ping
                        let pingData = Data([0x24])  // '$'
                        try client.sendall(pingData)
                    } catch {
                        self.logger.info("Detected dead client, cleaning up")
                        self.cleanupClient(client)
                    }
                }
            }
        }
    }

    /// Interactive mode for debugging and monitoring
    func interactiveMode() {
        print("\nBinance MKTDispatcher Interactive Mode")
        print(String(repeating: "=", count: 50))

        // Create a fake socket for manual subscriptions
        let manualCallback: (Data) -> Void = { _ in
            // Ignore data - already broadcast by _broadcastMarketData
        }

        let manualSocket = FakeSocket(callback: manualCallback)
        manualSocket.idx = "manual"

        while true {
            if configs["Show live stream"] == true {
                print()  // New line after live stream display
            }

            print("\nOptions:")
            print("1. Show subscribed symbols")
            print("2. Show connected clients")
            print("3. Toggle packet printing")
            print("4. Add symbol manually")
            print("5. Remove symbol manually")
            print("6. Toggle live stream display")
            print("0. Exit")

            print("\nSelect option: ", terminator: "")
            fflush(stdout)

            guard let choice = readLine()?.trimmingCharacters(in: .whitespacesAndNewlines) else {
                continue
            }

            switch choice {
            case "1":
                let symbols = ws.getSubscribedSymbols()
                print("\nSubscribed symbols (\(symbols.count)):")
                for symbol in symbols {
                    let numClients = symbolToClients[symbol]?.count ?? 0
                    print("  - \(symbol) (\(numClients) clients)")
                }

            case "2":
                clientLock.lock()
                let clientCount = clients.count
                clientLock.unlock()
                print("\nConnected clients: \(clientCount)")

            case "3":
                configs["Print data packets"]?.toggle()
                print("Packet printing: \(configs["Print data packets"] ?? false)")

            case "4":
                print("Enter symbol to add (e.g., BTCUSDT): ", terminator: "")
                fflush(stdout)
                if let symbol = readLine()?.trimmingCharacters(in: .whitespacesAndNewlines).uppercased(), !symbol.isEmpty {
                    addSymbol(symbol: symbol, client: manualSocket)
                    print("Successfully subscribed to \(symbol)")
                }

            case "5":
                print("Enter symbol to remove (e.g., BTCUSDT): ", terminator: "")
                fflush(stdout)
                if let symbol = readLine()?.trimmingCharacters(in: .whitespacesAndNewlines).uppercased(), !symbol.isEmpty {
                    removeSymbol(symbol: symbol, client: manualSocket)
                    print("Successfully unsubscribed from \(symbol)")
                }

            case "6":
                if configs["Show live stream"] == true {
                    // Turn off
                    configs["Show live stream"] = false
                    liveStreamSymbol = nil
                    print("Live stream display: OFF")
                } else {
                    // Turn on - ask for symbol
                    print("Enter symbol to display (e.g., BTCUSDT): ", terminator: "")
                    fflush(stdout)
                    if let symbol = readLine()?.trimmingCharacters(in: .whitespacesAndNewlines).uppercased(), !symbol.isEmpty {
                        // Subscribe if not already subscribed
                        if symbolToClients[symbol] == nil {
                            addSymbol(symbol: symbol, client: manualSocket)
                        }

                        liveStreamSymbol = symbol
                        configs["Show live stream"] = true
                        print("Live stream display: ON for \(symbol)")
                        print("(Press Enter to stop live stream)")
                    }
                }

            case "0":
                print("Exiting...")
                return

            default:
                print("Invalid option")
            }
        }
    }

    deinit {
        ws.stop()
        if serverSocket >= 0 {
            #if canImport(Darwin)
            Darwin.close(serverSocket)
            #elseif canImport(Glibc)
            Glibc.close(serverSocket)
            #endif
        }
    }
}
