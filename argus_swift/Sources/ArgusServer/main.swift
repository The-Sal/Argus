import Foundation

/// Argus Server - Swift Edition
/// Entry point for market data dispatchers
/// Transcompiled from runtime.py
///
/// Usage:
///   argus_server <target> [--host HOST] [--port PORT] [OPTIONS]
///
/// Targets:
///   binance        Binance market data dispatcher
///   ib             Interactive Brokers market data dispatcher
///   ib-forecast    Interactive Brokers forecast contracts dispatcher
///
/// Examples:
///   argus_server binance
///   argus_server binance --testnet
///   argus_server ib
///   argus_server ib-forecast

// MARK: - Command Line Argument Parsing

struct Arguments {
    var target: String = ""
    var host: String?
    var port: Int?
    var testnet: Bool = false
    var cookie: String?
    var envFile: String?
    var showHelp: Bool = false
}

func parseArguments(_ args: [String]) -> Arguments {
    var result = Arguments()
    var i = 1  // Skip program name

    while i < args.count {
        let arg = args[i]

        switch arg {
        case "binance", "ib", "ib-forecast", "capitalcom", "polymarket.example":
            result.target = arg

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

        case "--cookie":
            if i + 1 < args.count {
                result.cookie = args[i + 1]
                i += 1
            }

        case "--env-file":
            if i + 1 < args.count {
                result.envFile = args[i + 1]
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
    Argus Server - Market Data Dispatchers (Swift Edition)

    Usage:
      argus_server <target> [OPTIONS]

    Targets:
      binance              Binance market data dispatcher
      ib                   Interactive Brokers market data dispatcher
      ib-forecast          Interactive Brokers forecast contracts dispatcher
      capitalcom           Capital.com market data dispatcher
      polymarket.example   Polymarket direct integration example

    Options:
      --host HOST        Listening host (default: localhost)
      --port PORT        Listening port (default: varies by target)
      --cookie COOKIE    IB cookie for authentication (for ib/ib-forecast)
      --env-file PATH    Path to .env file (default: .env)
      --testnet          Use testnet (binance only)
      --help, -h         Show this help message

    Examples:
      argus_server binance
      argus_server binance --testnet
      argus_server ib --cookie "your-ib-cookie"
      argus_server ib-forecast

    Environment Variables (.env file or system):
      BINANCE_API_KEY              Binance API key (optional for public data)
      BINANCE_API_SECRET           Binance API secret (optional for public data)
      IB_COOKIE                    Interactive Brokers authentication cookie
      CAPITAL_DOTCOM_API_KEY       Capital.com API key
      CAPITAL_DOTCOM_IDENTIFIER    Capital.com identifier/username
      CAPITAL_DOT_CUSTOM_PW        Capital.com password
      CAPITAL_DOTCOM_ENVIRONMENT   Capital.com environment (live or demo, default: live)

    .env File Format:
      IB_COOKIE=your-cookie-value
      BINANCE_API_KEY=your-key
      # Comments are supported

    Default Ports:
      binance: 9974
      ib: 9972
      ib-forecast: 9972
      capitalcom: 9984

    Note:
      - DO NOT pass sensitive credentials via command line arguments
      - Use environment variables or .env file instead
      - The dispatcher provides an interactive mode for monitoring
    """)
}

func getEnvironmentVariable(_ name: String, envVars: [String: String] = [:]) -> String? {
    // Priority: .env file vars > system environment
    return envVars[name] ?? ProcessInfo.processInfo.environment[name]
}

// MARK: - Main Entry Point

func main() {
    print("Argus Server (Swift Edition)")
    print("Platform: \(getSystemInfo())")
    print("Arguments: \(CommandLine.arguments)")
    print("Process ID: \(ProcessInfo.processInfo.processIdentifier)")
    print()

    // Setup signal handlers for graceful shutdown
    signal(SIGINT) { _ in
        print("\n\nReceived SIGINT (Ctrl+C). Shutting down gracefully...")
        exit(0)
    }

    signal(SIGTERM) { _ in
        print("\n\nReceived SIGTERM. Shutting down gracefully...")
        exit(0)
    }

    let args = parseArguments(CommandLine.arguments)

    if args.showHelp {
        printHelp()
        exit(0)
    }

    // Load .env file if specified or use default
    let envFilePath = args.envFile ?? ".env"
    let envVars = EnvLoader.load(path: envFilePath)
    if !envVars.isEmpty {
        print("Loaded \(envVars.count) variables from \(envFilePath)")
    }

    // Set default host
    let host = args.host ?? "localhost"

    switch args.target {
    case "binance":
        print("WARNING: THIS MODULE SHOULD NOT BE USED AND HAS A KNOWN BUG FROM IT'S <0.0.8 PYTHON COUNTERPART.")
        sleep(10)
        runBinanceDispatcher(args: args, host: host, envVars: envVars)

    case "ib":
        runIBDispatcher(args: args, host: host, envVars: envVars)

    case "ib-forecast":
        runIBForecastDispatcher(args: args, host: host, envVars: envVars)

    case "capitalcom":
        runCapitalComDispatcher(args: args, host: host, envVars: envVars)

    case "polymarket.example":
        runPolymarketExample(args: args, host: host, envVars: envVars)

    default:
        print("Error: Unknown target '\(args.target)'")
        print("Currently supported: binance, ib, ib-forecast, capitalcom, polymarket.example")
        print("Use --help for more information")
        exit(1)
    }

    print("\nExiting Argus Server")
}

func runBinanceDispatcher(args: Arguments, host: String, envVars: [String: String]) {
    let apiKey = getEnvironmentVariable("BINANCE_API_KEY", envVars: envVars)
    let apiSecret = getEnvironmentVariable("BINANCE_API_SECRET", envVars: envVars)
    let port = args.port ?? 9974

    print("Starting Binance dispatcher")
    print("Host: \(host)")
    print("Port: \(port)")
    print()

    if args.testnet {
        print("Warning: Testnet not supported in main branch implementation")
        print("Using production endpoint: wss://stream.binance.com/stream")
    }

    if apiKey != nil || apiSecret != nil {
        print("Note: API keys not needed for public market data streams")
    }

    let dispatcher = BinanceMKTDispatcher(host: host, port: port)
    dispatcher.interactiveMode()
}

func runIBDispatcher(args: Arguments, host: String, envVars: [String: String]) {
    let cookie = args.cookie ?? getEnvironmentVariable("IB_COOKIE", envVars: envVars)
    guard let ibCookie = cookie else {
        print("Error: IB_COOKIE environment variable or --cookie argument required")
        print("Please set your Interactive Brokers authentication cookie")
        exit(1)
    }

    let port = Int32(args.port ?? 9972)

    print("Starting Interactive Brokers dispatcher")
    print("Host: \(host)")
    print("Port: \(port)")
    print()

    do {
        let dispatcher = IBMKTDispatcher(cookie: ibCookie, host: host, port: port)
        try dispatcher.selectAccountInteractive()
        dispatcher.interactiveMode()
        print("Dispatcher exited normally")
    } catch {
        print("FATAL ERROR: Dispatcher crashed with error: \(error)")
        print("Stack trace: \(Thread.callStackSymbols.joined(separator: "\n"))")
        exit(1)
    }
}

func runIBForecastDispatcher(args: Arguments, host: String, envVars: [String: String]) {
    let cookie = args.cookie ?? getEnvironmentVariable("IB_COOKIE", envVars: envVars)
    guard let ibCookie = cookie else {
        print("Error: IB_COOKIE environment variable or --cookie argument required")
        print("Please set your Interactive Brokers authentication cookie")
        exit(1)
    }

    let port = Int32(args.port ?? 9972)

    print("Starting Interactive Brokers Forecast dispatcher")
    print("Host: \(host)")
    print("Port: \(port)")
    print()

    let dispatcher = FXCDispatcher(cookie: ibCookie, host: host, port: port)
    dispatcher.selectAccountInteractive()
    dispatcher.interactiveMode()
}

func runCapitalComDispatcher(args: Arguments, host: String, envVars: [String: String]) {
    let apiKey = getEnvironmentVariable("CAPITAL_DOTCOM_API_KEY", envVars: envVars)
    let identifier = getEnvironmentVariable("CAPITAL_DOTCOM_IDENTIFIER", envVars: envVars)
    let password = getEnvironmentVariable("CAPITAL_DOT_CUSTOM_PW", envVars: envVars)
    let envString = getEnvironmentVariable("CAPITAL_DOTCOM_ENVIRONMENT", envVars: envVars)

    guard let capitalApiKey = apiKey,
          let capitalIdentifier = identifier,
          let capitalPassword = password else {
        print("Error: Capital.com credentials missing")
        print("Required environment variables:")
        print("  CAPITAL_DOTCOM_API_KEY")
        print("  CAPITAL_DOTCOM_IDENTIFIER")
        print("  CAPITAL_DOT_CUSTOM_PW")
        print()
        print("Optional environment variables:")
        print("  CAPITAL_DOTCOM_ENVIRONMENT (default: live, options: live, demo)")
        print()
        print("Set these in your .env file or system environment")
        exit(1)
    }

    // Parse environment (default to LIVE to match Python runtime.py behavior)
    let environment: Environment
    if let envString = envString {
        switch envString.lowercased() {
        case "demo":
            environment = .demo
        case "live":
            environment = .live
        default:
            print("Warning: Unknown environment '\(envString)', defaulting to LIVE")
            environment = .live
        }
    } else {
        // Default to LIVE (matching Python runtime.py)
        environment = .live
    }

    print("Starting Capital.com dispatcher")
    print("Environment: \(environment.rawValue.uppercased())")
    print("Note: Capital.com uses Unix domain socket at /tmp/argus_capital.sock")
    print("      (--host and --port arguments are ignored)")
    print()

    let dispatcher = CapitalComMKTDispatcher(
        socketPath: "/tmp/argus_capital.sock",
        apiKey: capitalApiKey,
        identifier: capitalIdentifier,
        password: capitalPassword,
        environment: environment
    )

    dispatcher.interactiveMode()
}

func runPolymarketExample(args: Arguments, host: String, envVars: [String: String]) {
    print("Starting Polymarket Direct Example")
    print("This demonstrates basic usage of the Polymarket API")
    print("No credentials needed for public market data")
    print()

    polymarketExampleUsage()
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
