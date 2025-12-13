import Foundation
#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#endif

/// Market Data Dispatcher for Binance
/// Transcompiled from argus/binance/__init__.py: BinanceMKTDispatcher (main branch)
/// Manages client connections via TCP and streams Binance market data using Protocol 2
class BinanceMKTDispatcher {
    private let host: String
    private let port: Int

    // TCP server socket
    private var serverSocket: Int32 = -1

    // Client management
    private var clients: [ArgusSocket] = []
    private var symbolToClients: [String: [ArgusSocket]] = [:]
    private var symbolDataCache: [String: Binance_CapitalComMKTDataLive] = [:]

    // Thread lock for client operations
    private let threadLock = NSLock()

    // Binance WebSocket
    private let ws: BinanceWss

    // Configuration
    private var configs: [String: Bool] = [
        "Print data packets": false,
        "Show subscription changes": true,
        "Show client messages": true,
        "Auto-unsubscribe disconnected clients": true
    ]

    init(host: String = "localhost", port: Int = 9982) {
        self.host = host
        self.port = port

        // Initialize Binance WebSocket
        self.ws = BinanceWss(configs: nil)
        self.ws.initWebSocket()

        // Create TCP server socket
        do {
            try createServerSocket()
        } catch {
            print("[ERROR] Failed to create server socket: \(error)")
            fatalError("Cannot start dispatcher")
        }

        // Start client listener and health check
        startClientListener()
        startClientHealthChecker()

        print("[BinanceMKTDispatcher] Initialized on \(host):\(port)")
        print("[IMPORTANT] MODE = PROTOCOL_2")
    }

    /// Create and bind TCP server socket
    private func createServerSocket() throws {
        serverSocket = socket(AF_INET, SOCK_STREAM, 0)
        guard serverSocket >= 0 else {
            throw SocketError.bindFailed
        }

        var reuseAddr: Int32 = 1
        setsockopt(serverSocket, SOL_SOCKET, SO_REUSEADDR, &reuseAddr, socklen_t(MemoryLayout<Int32>.size))

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

        print("[BinanceMKTDispatcher] Server socket bound to \(host):\(port)")
    }

    /// Start listening for incoming client connections
    private func startClientListener() {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }

            while true {
                let listenResult = listen(self.serverSocket, 5)
                guard listenResult >= 0 else {
                    print("[ERROR] Listen failed")
                    break
                }

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

                print("[BinanceMKTDispatcher] Client connected from fd=\(clientFD)")

                let clientSocket = RealSocket(fileDescriptor: clientFD)

                self.threadLock.lock()
                self.clients.append(clientSocket)
                self.threadLock.unlock()

                self.listenToClient(clientSocket)
            }
        }
    }

    /// Listen to client requests
    /// Transcompiled from Python: _listen_to_client
    private func listenToClient(_ client: ArgusSocket) {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }

            // FakeSockets don't need to receive data
            guard let realSocket = client as? RealSocket else {
                return
            }

            while true {
                do {
                    guard let data = try realSocket.receive() else {
                        // Connection closed
                        break
                    }

                    guard let message = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines),
                          !message.isEmpty else {
                        continue
                    }

                    if self.configs["Show client messages"] == true {
                        print("[CLIENT] Request: \(message)")
                    }

                    // Parse client commands
                    if message.hasPrefix("add=") {
                        let symbol = String(message.dropFirst(4)).uppercased()
                        self.subscribeToSymbol(symbol: symbol, client: client)
                    } else if message.hasPrefix("remove=") {
                        let symbol = String(message.dropFirst(7)).uppercased()
                        self.unsubscribeSymbol(symbol: symbol, client: client)
                    }

                } catch {
                    // Error reading from client, connection likely closed
                    break
                }
            }

            // Client disconnected
            self.cleanupClient(client)
        }
    }

    /// Unsubscribe a client from a symbol
    private func unsubscribeSymbol(symbol: String, client: ArgusSocket) {
        threadLock.lock()
        defer { threadLock.unlock() }

        guard var clients = symbolToClients[symbol] else {
            return
        }

        clients.removeAll { $0 === client }

        if clients.isEmpty {
            // No more clients for this symbol, unsubscribe from WebSocket
            ws.unsubscribe(symbol: symbol)
            symbolToClients.removeValue(forKey: symbol)
            if configs["Show subscription changes"] == true {
                print("[UNSUBSCRIBE] No more clients for \(symbol)")
            }
        } else {
            symbolToClients[symbol] = clients
        }
    }

    /// Subscribe to a symbol for a client
    private func subscribeToSymbol(symbol: String, client: ArgusSocket) {
        let upperSymbol = symbol.uppercased()

        threadLock.lock()
        let needsSubscription = symbolToClients[upperSymbol] == nil

        if needsSubscription {
            if configs["Show subscription changes"] == true {
                print("[SUBSCRIBE] New subscription to \(upperSymbol)")
            }
            symbolToClients[upperSymbol] = [client]
        } else {
            if !symbolToClients[upperSymbol]!.contains(where: { $0 === client }) {
                symbolToClients[upperSymbol]?.append(client)
                if configs["Show subscription changes"] == true {
                    let count = symbolToClients[upperSymbol]?.count ?? 0
                    print("[CLIENT] Added client to \(upperSymbol) subscription (total: \(count))")
                }
            }
        }
        threadLock.unlock()

        if needsSubscription {
            ws.subscribe(symbol: upperSymbol) { [weak self] msg in
                self?.binanceCallback(symbol: upperSymbol, msg: msg)
            }
        }
    }

    /// Callback for Binance market data
    private func binanceCallback(symbol: String, msg: AbstractBinanceType) {
        // CRITICAL: Only process bookTicker messages
        guard msg.idx == BinanceTypes.BOOK_TICKER else {
            // Ignore other message types (depth, aggTrade, kline)
            return
        }
        
        guard let bookTickerMsg = msg.obj as? BookTickerMessage else {
            return
        }

        // Get or create market data cache for this symbol
        threadLock.lock()
        let existingData = symbolDataCache[symbol]
        threadLock.unlock()

        // Create market data from book ticker
        let marketData = Binance_CapitalComMKTDataLive.fromBinanceBookTicker(
            symbol: symbol,
            bookTicker: bookTickerMsg.data,
            existingData: existingData
        )

        // Update cache
        threadLock.lock()
        symbolDataCache[symbol] = marketData
        let clients = symbolToClients[symbol] ?? []
        threadLock.unlock()

        guard !clients.isEmpty else {
            return
        }

        // Transmit to all clients using Protocol 2
        do {
            let packet = try transmitMarketDataWithProtocol2(marketData)

            if configs["Print data packets"] == true {
                print("[TX \(symbol)] \(String(data: packet, encoding: .ascii) ?? "")")
            }

            for client in clients {
                do {
                    try client.sendall(packet)
                } catch {
                    if configs["Show subscription changes"] == true {
                        print("[CLIENT] Error sending to client: \(error)")
                    }
                }
            }
        } catch {
            print("[ERROR] Error encoding market data for \(symbol): \(error)")
        }
    }

    /// Clean up a disconnected client
    private func cleanupClient(_ client: ArgusSocket) {
        threadLock.lock()
        defer { threadLock.unlock() }

        clients.removeAll { $0 === client }

        // Remove from all symbol subscriptions
        for symbol in Array(symbolToClients.keys) {
            symbolToClients[symbol]?.removeAll { $0 === client }

            // Unsubscribe if no more clients
            if symbolToClients[symbol]?.isEmpty == true {
                ws.unsubscribe(symbol: symbol)
                symbolToClients.removeValue(forKey: symbol)
                if configs["Show subscription changes"] == true {
                    print("[UNSUBSCRIBE] No more clients for \(symbol)")
                }
            }
        }

        client.close()
    }

    /// Periodically check if clients are still connected
    private func startClientHealthChecker() {
        DispatchQueue.global(qos: .utility).async { [weak self] in
            while true {
                Thread.sleep(forTimeInterval: 5)

                guard let self = self else { break }

                self.threadLock.lock()
                let clientsCopy = self.clients
                self.threadLock.unlock()

                for client in clientsCopy {
                    do {
                        let pingData = Data([0x24])  // '$'
                        try client.sendall(pingData)
                    } catch {
                        if self.configs["Show subscription changes"] == true {
                            print("[CLIENT] Detected dead client, cleaning up")
                        }
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
            // Ignore data - already broadcast
        }

        let manualSocket = FakeSocket(callback: manualCallback)
        manualSocket.idx = "manual"

        while true {
            print("\nOptions:")
            print("1. Show subscribed symbols")
            print("2. Show connected clients")
            print("3. Toggle packet printing")
            print("4. Add symbol manually")
            print("5. Remove symbol manually")
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
                threadLock.lock()
                let clientCount = clients.count
                threadLock.unlock()
                print("\nConnected clients: \(clientCount)")

            case "3":
                configs["Print data packets"]?.toggle()
                print("Packet printing: \(configs["Print data packets"] ?? false)")

            case "4":
                print("Enter symbol to add (e.g., BTCUSDT): ", terminator: "")
                fflush(stdout)
                if let symbol = readLine()?.trimmingCharacters(in: .whitespacesAndNewlines).uppercased(), !symbol.isEmpty {
                    subscribeToSymbol(symbol: symbol, client: manualSocket)
                    print("Successfully subscribed to \(symbol)")
                }

            case "5":
                print("Enter symbol to remove (e.g., BTCUSDT): ", terminator: "")
                fflush(stdout)
                if let symbol = readLine()?.trimmingCharacters(in: .whitespacesAndNewlines).uppercased(), !symbol.isEmpty {
                    ws.unsubscribe(symbol: symbol)
                    threadLock.lock()
                    symbolToClients.removeValue(forKey: symbol)
                    threadLock.unlock()
                    print("Successfully unsubscribed from \(symbol)")
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
