import Foundation
#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#endif

/// Protocol that abstracts socket operations
/// This allows for both real sockets and fake sockets (for testing/internal callbacks)
protocol ArgusSocket: AnyObject {
    /// Unique identifier for this socket
    var idx: String { get set }

    /// Send all data through the socket
    /// - Parameter data: The data to send
    /// - Throws: Socket errors if sending fails
    func sendall(_ data: Data) throws

    /// Close the socket
    func close()
}

/// Real socket implementation wrapping POSIX socket
class RealSocket: ArgusSocket {
    var idx: String = "real"
    let fileDescriptor: Int32  // Make public for receive operations
    private var isClosed = false

    init(fileDescriptor: Int32) {
        self.fileDescriptor = fileDescriptor
    }

    func sendall(_ data: Data) throws {
        guard !isClosed else {
            throw SocketError.socketClosed
        }

        var sent = 0
        let totalBytes = data.count

        try data.withUnsafeBytes { (buffer: UnsafeRawBufferPointer) in
            guard let baseAddress = buffer.baseAddress else {
                throw SocketError.invalidData
            }

            while sent < totalBytes {
                let bytesWritten = send(
                    fileDescriptor,
                    baseAddress.advanced(by: sent),
                    totalBytes - sent,
                    0
                )

                if bytesWritten < 0 {
                    if errno == EPIPE || errno == ECONNRESET {
                        throw SocketError.connectionReset
                    }
                    throw SocketError.sendFailed(errno: errno)
                }

                sent += bytesWritten
            }
        }
    }

    /// Receive data from socket
    func receive(bufferSize: Int = 9999) throws -> Data? {
        guard !isClosed else {
            throw SocketError.socketClosed
        }

        var buffer = [UInt8](repeating: 0, count: bufferSize)
        let bytesRead = recv(fileDescriptor, &buffer, bufferSize, 0)

        if bytesRead < 0 {
            throw SocketError.receiveFailed(errno: errno)
        }

        if bytesRead == 0 {
            // Connection closed by peer
            return nil
        }

        return Data(buffer[0..<bytesRead])
    }

    func close() {
        guard !isClosed else { return }
        isClosed = true
        #if canImport(Darwin)
        Darwin.close(fileDescriptor)
        #elseif canImport(Glibc)
        Glibc.close(fileDescriptor)
        #endif
    }

    deinit {
        close()
    }
}

/// Fake socket for internal callbacks (no actual socket connection)
/// Equivalent to Python's FakeSocket class
class FakeSocket: ArgusSocket {
    var idx: String = "fake"

    /// Callback function that receives data instead of sending over network
    private let callback: (Data) -> Void

    init(callback: @escaping (Data) -> Void) {
        self.callback = callback
    }

    func sendall(_ data: Data) throws {
        // Instead of sending over network, call the callback
        callback(data)
    }

    func close() {
        // Nothing to close for fake socket
    }
}

/// Socket-related errors
enum SocketError: Error {
    case socketClosed
    case invalidData
    case connectionReset
    case sendFailed(errno: Int32)
    case receiveFailed(errno: Int32)
    case bindFailed
    case listenFailed
    case acceptFailed
}
