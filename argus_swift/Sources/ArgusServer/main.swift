import Foundation

/// Argus Server - Swift Edition
/// Entry point for the Binance market data dispatcher
/// Transcompiled from runtime.py
///
/// Usage:
///   argus_server binance [--host HOST] [--port PORT] [--testnet]
///
/// Examples:
///   argus_server binance
///   argus_server binance --testnet
///   argus_server binance --host 0.0.0.0 --port 9974

// MARK: - Command Line Argument Parsing

struct Arguments {
    var target: String = ""
    var host: String?
    var port: Int?
    var testnet: Bool = false
    var showHelp: Bool = false
}

func parseArguments(_ args: [String]) -> Arguments {
    var result = Arguments()
    var i = 1  // Skip program name

    while i < args.count {
        let arg = args[i]

        switch arg {
        case "binance":
            result.target = "binance"

        case "--host":
            if i + 1 < args.count {
                result.host = args[i + 1]
                i += 1
            }

        case "--port":
            if i + 1 < args.count {
                result.port = Int(args[i + 1])
                i += 1
            }

        case "--testnet":
            result.testnet = true

        case "--help", "-h":
            result.showHelp = true

        default:
            if result.target.isEmpty {
                result.target = arg
            }
        }

        i += 1
    }

    return result
}

func printHelp() {
    print("""
    Argus Server - Binance Market Data Dispatcher (Swift Edition)

    Usage:
      argus_server binance [OPTIONS]

    Options:
      --host HOST      Listening host (default: localhost)
      --port PORT      Listening port (default: 9974)
      --testnet        Use Binance testnet instead of production
      --help, -h       Show this help message

    Examples:
      argus_server binance
      argus_server binance --testnet
      argus_server binance --host 0.0.0.0 --port 9974

    Environment Variables:
      BINANCE_API_KEY     Binance API key (optional for public data)
      BINANCE_API_SECRET  Binance API secret (optional for public data)

    Note:
      - DO NOT pass API credentials via command line arguments
      - Use environment variables or .env file instead
      - The dispatcher provides an interactive mode for monitoring
    """)
}

func getEnvironmentVariable(_ name: String) -> String? {
    return ProcessInfo.processInfo.environment[name]
}

// MARK: - Main Entry Point

func main() {
    print("Argus Server (Swift Edition)")
    print("Platform: \(getSystemInfo())")
    print("Arguments: \(CommandLine.arguments)")
    print()

    let args = parseArguments(CommandLine.arguments)

    if args.showHelp {
        printHelp()
        exit(0)
    }

    guard args.target == "binance" else {
        print("Error: Unknown target '\(args.target)'")
        print("Currently supported: binance")
        print("Use --help for more information")
        exit(1)
    }

    // Get API credentials from environment
    let apiKey = getEnvironmentVariable("BINANCE_API_KEY")
    let apiSecret = getEnvironmentVariable("BINANCE_API_SECRET")

    // Set default host and port if not provided
    let host = args.host ?? "localhost"
    let port = args.port ?? 9974

    print("Starting Binance dispatcher (testnet=\(args.testnet))")
    print("Host: \(host)")
    print("Port: \(port)")
    print()

    // Create dispatcher
    let dispatcher = MKTDispatcher(
        host: host,
        port: port,
        apiKey: apiKey,
        apiSecret: apiSecret,
        testnet: args.testnet,
        checkpointURL: nil  // Can be configured via environment variable
    )

    // Start dispatcher
    dispatcher.start()

    // Enter interactive mode
    dispatcher.interactiveMode()

    print("\nExiting Argus Server")
}

func getSystemInfo() -> String {
    #if os(macOS)
    return "macOS"
    #elseif os(Linux)
    return "Linux"
    #elseif os(Windows)
    return "Windows"
    #else
    return "Unknown"
    #endif
}

// Run main
main()
