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


class FakeSocketForIBKR: ArgusSocket{
    var idx: String = "fake.ibkr"

    let callback: (Any) -> Void

    init(callback: @escaping (Any) -> Void) {
        self.callback = callback
    }

    func sendallObject(_ obj: Any){
        callback(obj)
    }

    func sendall(_ data: Data) throws {
        /*
        Within Python we have the ability to send any type via .sendall but
        within swfit we cannot do this, moreover we cannot change the protocol
        because other modules rely on it and is tightly integrated. Therefore we
        are creating IBKR–Specific fake socket which will be a drop in replacement
        but fix the bug where the AccountProvider data doesnt update because

        1. Swift MKTDispatcher sends raw Protocol 2 Data to ALL clients (including FakeSocket)
        2. FakeSocket.sendall() receives this Data and calls the callback with it
        3. AccountProvider.onMarketData() expects IBKR_CapitalComMKTDataLive but gets Data
        4. The guard let marketData = data as? IBKR_CapitalComMKTDataLive always fails
        5. Function returns early, so position P&L never updates
        The Python difference:
        - Python MKTDispatcher checks client.idx != 'real'
        - For fake sockets: sends the IBKR_CapitalComMKTDataLive object directly
        - For real sockets: sends Protocol 2 packet
        - So Python AccountProvider receives the expected object type
        The Swift constraint:
        ArgusSocket.sendall(_ data: Data) demands Data type, so we can't just send objects directly through the socket interface.
        The real issue is architectural: Swift needs the same distinction Python makes - send different data types to fake vs real clients.
        The socket protocol constrains what goes through the socket, but the dispatcher can choose what to send before calling sendall().

        */
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
