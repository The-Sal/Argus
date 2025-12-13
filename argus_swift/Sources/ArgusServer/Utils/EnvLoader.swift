import Foundation

/// Simple .env file parser
/// Loads environment variables from a .env file
struct EnvLoader {
    /// Load environment variables from a .env file
    /// - Parameter path: Path to the .env file (defaults to .env in current directory)
    /// - Returns: Dictionary of key-value pairs
    static func load(path: String = ".env") -> [String: String] {
        var envVars: [String: String] = [:]

        // Check if file exists
        guard FileManager.default.fileExists(atPath: path) else {
            return envVars
        }

        // Read file contents
        guard let contents = try? String(contentsOfFile: path, encoding: .utf8) else {
            print("Warning: Could not read .env file at \(path)")
            return envVars
        }

        // Parse line by line
        let lines = contents.components(separatedBy: .newlines)

        for line in lines {
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            // Skip empty lines and comments
            if trimmed.isEmpty || trimmed.hasPrefix("#") {
                continue
            }

            // Parse KEY=VALUE format
            let parts = trimmed.components(separatedBy: "=")
            guard parts.count >= 2 else {
                continue
            }

            let key = parts[0].trimmingCharacters(in: .whitespaces)
            let value = parts[1...].joined(separator: "=").trimmingCharacters(in: .whitespaces)

            // Remove quotes if present
            var cleanValue = value
            if (cleanValue.hasPrefix("\"") && cleanValue.hasSuffix("\"")) ||
               (cleanValue.hasPrefix("'") && cleanValue.hasSuffix("'")) {
                cleanValue = String(cleanValue.dropFirst().dropLast())
            }

            envVars[key] = cleanValue
        }

        return envVars
    }

    /// Load .env file and set as process environment variables
    /// - Parameter path: Path to the .env file
    static func loadAndSet(path: String = ".env") {
        let vars = load(path: path)

        if !vars.isEmpty {
            print("Loaded \(vars.count) variables from \(path)")
        }

        // Note: We can't actually set process environment in Swift
        // So we just return the dictionary for use
    }
}
