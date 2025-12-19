import Foundation
import Foundation

enum HTTPError: Error {
    case invalidURL
    case curlNotFound
    case curlExecutionFailed(Int32)
    case emptyResponse
    case encodingError
    case processError(String)
}

enum HTTPMethod: String {
    case get = "GET"
    case post = "POST"
}

class HTTPClient {

    /// Performs a synchronous HTTP GET request
    /// - Parameters:
    ///   - url: The URL string to request
    ///   - headers: Optional dictionary of custom headers
    /// - Returns: Response body as String
    /// - Throws: HTTPError if the request fails
    func get(url: String, headers: [String: String]? = nil) throws -> String {
        return try performRequest(
            method: .get,
            url: url,
            headers: headers,
            contentType: nil,
            body: nil
        )
    }

    /// Performs a synchronous HTTP POST request
    /// - Parameters:
    ///   - url: The URL string to request
    ///   - headers: Optional dictionary of custom headers
    ///   - contentType: Content-Type header value
    ///   - body: Request body as String
    /// - Returns: Response body as String
    /// - Throws: HTTPError if the request fails
    func post(
        url: String,
        headers: [String: String]? = nil,
        contentType: String? = nil,
        body: String? = nil
    ) throws -> String {
        return try performRequest(
            method: .post,
            url: url,
            headers: headers,
            contentType: contentType,
            body: body
        )
    }

    /// Performs a synchronous FTP request with authentication
    /// - Parameters:
    ///   - url: The FTP URL string to request
    ///   - username: Username for authentication
    ///   - password: Password for authentication
    /// - Returns: Response body as String
    /// - Throws: HTTPError if the request fails
    func ftp(url: String, username: String, password: String) throws -> String {
        return try performFTPRequest(
            url: url,
            username: username,
            password: password
        )
    }

    // MARK: - Private Methods

    private func performRequest(
        method: HTTPMethod,
        url: String,
        headers: [String: String]?,
        contentType: String?,
        body: String?
    ) throws -> String {
        guard !url.isEmpty else {
            throw HTTPError.invalidURL
        }

        // Locate curl executable
        let curlPath = try findCurl()

        // Build curl arguments
        var arguments: [String] = []

        // Silent mode but show errors
        arguments.append("-s")
        arguments.append("-S")

        // Set HTTP method
        arguments.append("-X")
        arguments.append(method.rawValue)

        // Add custom headers
        if let headers = headers {
            for (key, value) in headers {
                arguments.append("-H")
                arguments.append("\(key): \(value)")
            }
        }

        // Add Content-Type if specified
        if let contentType = contentType {
            arguments.append("-H")
            arguments.append("Content-Type: \(contentType)")
        }

        // Add body for POST requests
        if let body = body, method == .post {
            arguments.append("-d")
            arguments.append(body)
        }

        // Add URL
        arguments.append(url)

        // Create and configure Process
        let process = Process()
        process.executableURL = URL(fileURLWithPath: curlPath)
        process.arguments = arguments

        // Setup pipes for stdout and stderr
        let outputPipe = Pipe()
        let errorPipe = Pipe()
        process.standardOutput = outputPipe
        process.standardError = errorPipe

        // Launch process
        do {
            try process.run()
        } catch {
            throw HTTPError.processError("Failed to launch curl: \(error.localizedDescription)")
        }

        // Wait synchronously for completion
        process.waitUntilExit()

        // Check exit status
        let exitCode = process.terminationStatus
        guard exitCode == 0 else {
            let errorData = errorPipe.fileHandleForReading.readDataToEndOfFile()
            let errorMessage = String(data: errorData, encoding: .utf8) ?? "Unknown error"
            print("ERROR: \(errorMessage)")
            throw HTTPError.curlExecutionFailed(exitCode)
        }

        // Read output
        let outputData = outputPipe.fileHandleForReading.readDataToEndOfFile()

        guard let responseString = String(data: outputData, encoding: .utf8) else {
            throw HTTPError.encodingError
        }

        return responseString
    }

    private func performFTPRequest(
        url: String,
        username: String,
        password: String
    ) throws -> String {
        guard !url.isEmpty else {
            throw HTTPError.invalidURL
        }

        // Locate curl executable
        let curlPath = try findCurl()

        // Build curl arguments for FTP
        var arguments: [String] = []

        // Silent mode but show errors
        arguments.append("-s")
        arguments.append("-S")

        // Add authentication
        let credentials = "\(username):\(password)"
        arguments.append("-u")
        arguments.append(credentials)

        // Follow redirects (useful for FTP servers)
        arguments.append("-L")

        // Handle large files - don't limit output
        arguments.append("--max-filesize")
        arguments.append("0")

        // Add URL
        arguments.append(url)

        // Create and configure Process
        let process = Process()
        process.executableURL = URL(fileURLWithPath: curlPath)
        process.arguments = arguments

        // Setup pipes for stdout and stderr
        let outputPipe = Pipe()
        let errorPipe = Pipe()
        process.standardOutput = outputPipe
        process.standardError = errorPipe

        // Launch process
        do {
            try process.run()
        } catch {
            throw HTTPError.processError("Failed to launch curl: \(error.localizedDescription)")
        }

        // Wait synchronously for completion
        process.waitUntilExit()

        // Check exit status
        let exitCode = process.terminationStatus
        guard exitCode == 0 else {
            let errorData = errorPipe.fileHandleForReading.readDataToEndOfFile()
            let errorMessage = String(data: errorData, encoding: .utf8) ?? "Unknown error"
            print("ERROR: \(errorMessage)")
            throw HTTPError.curlExecutionFailed(exitCode)
        }

        // Read output
        let outputData = outputPipe.fileHandleForReading.readDataToEndOfFile()

        guard let responseString = String(data: outputData, encoding: .utf8) else {
            throw HTTPError.encodingError
        }

        return responseString
    }

    private func findCurl() throws -> String {
        // Common curl locations on UNIX systems
        let possiblePaths = [
            "/usr/bin/curl",
            "/bin/curl",
            "/usr/local/bin/curl"
        ]

        for path in possiblePaths {
            if FileManager.default.fileExists(atPath: path) {
                return path
            }
        }

        throw HTTPError.curlNotFound
    }
}

// MARK: - Usage Example
 /*
 let client = HTTPClient()

 // GET request
 do {
     let response = try client.get(
         url: "https://api.example.com/data",
         headers: [
             "Authorization": "Bearer token123",
             "Accept": "application/json"
         ]
     )
     print("GET Response:", response)
 } catch {
     print("GET Error:", error)
 }

 // POST request
 do {
     let jsonBody = "{\"name\":\"John\",\"age\":30}"
     let response = try client.post(
         url: "https://api.example.com/users",
         headers: [
             "Authorization": "Bearer token123"
         ],
         contentType: "application/json",
         body: jsonBody
     )
     print("POST Response:", response)
 } catch {
     print("POST Error:", error)
 }

 // FTP request
 do {
     let response = try client.ftp(
         url: "ftp://ftp2.interactivebrokers.com/usa.txt",
         username: "shortstock",
         password: "your_password"
     )
     print("FTP Response:", response)
 } catch {
     print("FTP Error:", error)
 }
 */
