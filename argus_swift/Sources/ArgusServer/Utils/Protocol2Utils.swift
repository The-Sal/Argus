import Foundation

/// Protocol 2 packet encoding and decoding utilities
/// Transcompiled from argus/capital/_svr_utils.py
///
/// Protocol 2 Format:
/// ~<packet-length><symbol-length>|<symbol><market-data>L
/// Where:
///   - packet-length: 4-byte integer for total packet length (excluding header)
///   - symbol-length: 4-byte integer for symbol length
///   - symbol: ASCII-encoded symbol
///   - market-data: CSV-formatted market data
///   - L: Terminator byte

enum Protocol2Error: Error {
    case invalidPacketFormat
    case packetTooShort
    case missingStartMarker
    case missingPipeSeparator
    case missingTerminator
    case invalidLength
    case dataLengthMismatch
    case invalidSymbol
    case invalidMarketData
    case fieldCountMismatch
    case encodingError
}

/// Encode a basic packet with length prefix
/// Format: ~<data-length>|{data}
func encodePacket(_ data: Data) throws -> Data {
    let length = data.count
    guard length <= UInt32.max else {
        throw Protocol2Error.invalidLength
    }

    let header = String(format: "~%04d|", length)
    guard let headerData = header.data(using: .ascii) else {
        throw Protocol2Error.encodingError
    }

    var packet = Data()
    packet.append(headerData)
    packet.append(data)
    return packet
}

/// Decode a basic packet
/// Returns the data portion (without header)
func decodePacket(_ packet: Data) throws -> Data {
    guard packet.count >= 6 else {  // Minimum: ~0000|
        throw Protocol2Error.packetTooShort
    }

    // Check start marker
    guard packet[0] == 0x7E else {  // '~' = 0x7E
        throw Protocol2Error.missingStartMarker
    }

    // Parse length (bytes 1-4)
    let lengthBytes = packet[1..<5]
    guard let lengthStr = String(data: lengthBytes, encoding: .ascii),
          let length = Int(lengthStr) else {
        throw Protocol2Error.invalidLength
    }

    // Check pipe separator
    guard packet[5] == 0x7C else {  // '|' = 0x7C
        throw Protocol2Error.missingPipeSeparator
    }

    // Extract data
    let dataStart = 6
    let dataEnd = dataStart + length

    guard dataEnd <= packet.count else {
        throw Protocol2Error.dataLengthMismatch
    }

    return packet[dataStart..<dataEnd]
}

/// Decode multiple packets from a byte stream
/// Returns array of decoded packet data
func decodeMultiplePackets(_ data: Data) throws -> [Data] {
    var packets: [Data] = []
    var position = 0

    while position < data.count {
        guard position + 6 <= data.count else {
            throw Protocol2Error.packetTooShort
        }

        let remaining = data[position...]

        // Check start marker
        guard remaining.first == 0x7E else {  // '~'
            throw Protocol2Error.missingStartMarker
        }

        // Parse length
        let lengthStart = position + 1
        let lengthEnd = lengthStart + 4
        let lengthBytes = data[lengthStart..<lengthEnd]

        guard let lengthStr = String(data: lengthBytes, encoding: .ascii),
              let length = Int(lengthStr) else {
            throw Protocol2Error.invalidLength
        }

        // Check pipe
        guard data[lengthEnd] == 0x7C else {  // '|'
            throw Protocol2Error.missingPipeSeparator
        }

        // Extract packet data
        let dataStart = lengthEnd + 1
        let dataEnd = dataStart + length

        guard dataEnd <= data.count else {
            throw Protocol2Error.dataLengthMismatch
        }

        let packetData = data[dataStart..<dataEnd]
        packets.append(packetData)

        // Move to next packet
        position = dataEnd
    }

    return packets
}

/// Transmit market data with Protocol 2 format
/// - Parameter marketData: Market data object conforming to MarketDataTransferable
/// - Returns: Encoded packet as Data
func transmitMarketDataWithProtocol2<T: MarketDataTransferable>(_ marketData: T) throws -> Data {
    // Get market data CSV bytes
    let dataBytes = try marketData.transferable2()
    let symbol = marketData.symbol

    // Build packet without header
    guard let symbolBytes = symbol.data(using: .ascii) else {
        throw Protocol2Error.invalidSymbol
    }

    let symbolLength = symbolBytes.count
    let symbolLengthHeader = String(format: "%04d|", symbolLength)

    guard let symbolLengthData = symbolLengthHeader.data(using: .ascii) else {
        throw Protocol2Error.encodingError
    }

    var packetWithoutHeading = Data()
    packetWithoutHeading.append(symbolLengthData)
    packetWithoutHeading.append(symbolBytes)
    packetWithoutHeading.append(dataBytes)
    packetWithoutHeading.append(0x4C)  // 'L' terminator

    // Add main header
    let packetLength = packetWithoutHeading.count
    let packetLengthHeader = String(format: "~%04d", packetLength)

    guard let packetLengthData = packetLengthHeader.data(using: .ascii) else {
        throw Protocol2Error.encodingError
    }

    var packet = Data()
    packet.append(packetLengthData)
    packet.append(packetWithoutHeading)

    return packet
}

/// Parser for Protocol 2 packets
class Protocol2Parser {
    /// Field names in the order they appear in packets
    let decodingOrder: [String]

    init(decodingOrder: [String]) {
        self.decodingOrder = decodingOrder
    }

    /// Parse a Protocol 2 packet
    /// - Parameter packetBytes: Raw packet data
    /// - Returns: Dictionary containing symbol and field values
    func parse(_ packetBytes: Data) throws -> [String: Any] {
        // Validate minimum packet size
        guard packetBytes.count >= 11 else {  // Minimum: ~0000|0000|L
            throw Protocol2Error.packetTooShort
        }

        var pos = 0

        // Parse header ~NNNN (5 bytes)
        guard packetBytes[pos] == 0x7E else {  // '~'
            throw Protocol2Error.missingStartMarker
        }
        pos += 1

        // Extract packet length
        let packetLengthData = packetBytes[pos..<(pos + 4)]
        guard let packetLengthStr = String(data: packetLengthData, encoding: .ascii),
              let packetLength = Int(packetLengthStr) else {
            throw Protocol2Error.invalidLength
        }
        pos += 4

        // Validate total packet size
        let expectedTotalLength = 5 + packetLength
        guard packetBytes.count == expectedTotalLength else {
            throw Protocol2Error.dataLengthMismatch
        }

        // Parse symbol length NNNN| (5 bytes)
        let symbolLengthData = packetBytes[pos..<(pos + 4)]
        guard let symbolLengthStr = String(data: symbolLengthData, encoding: .ascii),
              let symbolLength = Int(symbolLengthStr) else {
            throw Protocol2Error.invalidLength
        }
        pos += 4

        guard packetBytes[pos] == 0x7C else {  // '|'
            throw Protocol2Error.missingPipeSeparator
        }
        pos += 1

        // Parse symbol
        guard pos + symbolLength <= packetBytes.count else {
            throw Protocol2Error.dataLengthMismatch
        }

        let symbolData = packetBytes[pos..<(pos + symbolLength)]
        guard let symbol = String(data: symbolData, encoding: .ascii) else {
            throw Protocol2Error.invalidSymbol
        }
        pos += symbolLength

        // Validate terminator
        guard packetBytes[packetBytes.count - 1] == 0x4C else {  // 'L'
            throw Protocol2Error.missingTerminator
        }

        // Parse market data (everything except last byte 'L')
        let marketDataBytes = packetBytes[pos..<(packetBytes.count - 1)]
        guard let marketDataStr = String(data: marketDataBytes, encoding: .ascii) else {
            throw Protocol2Error.invalidMarketData
        }

        // Parse CSV values
        let values = try parseCSVValues(marketDataStr)

        // Validate value count
        guard values.count == decodingOrder.count else {
            throw Protocol2Error.fieldCountMismatch
        }

        // Build result dictionary
        var result: [String: Any] = ["symbol": symbol]
        for (index, fieldName) in decodingOrder.enumerated() {
            result[fieldName] = values[index]
        }

        return result
    }

    /// Parse comma-separated values from string
    private func parseCSVValues(_ dataStr: String) throws -> [Double] {
        guard !dataStr.isEmpty else {
            throw Protocol2Error.invalidMarketData
        }

        var values: [Double] = []
        var currentValue = ""

        for char in dataStr {
            if char == "," {
                guard !currentValue.isEmpty else {
                    throw Protocol2Error.invalidMarketData
                }
                guard let value = Double(currentValue) else {
                    throw Protocol2Error.invalidMarketData
                }
                values.append(value)
                currentValue = ""
            } else {
                currentValue.append(char)
            }
        }

        // Handle last value (no trailing comma)
        if !currentValue.isEmpty {
            guard let value = Double(currentValue) else {
                throw Protocol2Error.invalidMarketData
            }
            values.append(value)
        } else if dataStr.hasSuffix(",") {
            throw Protocol2Error.invalidMarketData
        }

        return values
    }
}

/// Protocol that market data objects must conform to for Protocol 2 transmission
protocol MarketDataTransferable {
    var symbol: String { get }

    /// Returns CSV-formatted market data as Data
    func transferable2() throws -> Data
}
