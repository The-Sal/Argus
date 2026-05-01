#!/usr/bin/env python3
"""
Market Data Packet Protocol Implementation

This module implements a custom packet protocol for transmitting financial market data.
It provides two main protocols:

1. Basic Packet Protocol: Generic packet encoding/decoding with length prefixes
2. Protocol 2: Specialized protocol for market data transmission with symbol and field encoding

Protocol Formats:
- Basic: ~<data-length>|{data}
- Protocol 2: ~<packet-length><symbol-length>|<symbol><market-data>L

"""

import json
import zlib
import base64
import time
from typing import List, Dict, Union


def decompress_p1_response(msg: dict) -> dict:
    """
    In-place decompression helper for P1 responses from the Polymarket dispatcher.

    The dispatcher may compress large responses (>= 5000 bytes) by replacing
    ``msg['data']`` with a base64-encoded zlib-compressed JSON string and setting
    ``msg['compressed']`` to ``True``.  This helper detects that flag, decompresses
    the payload, and restores the original Python object in ``msg['data']``.

    :param msg: A decoded P1 JSON response dict.
    :return: The same dict, with ``data`` decompressed when applicable.
    """
    if msg.get("compressed") and isinstance(msg.get("data"), str):
        try:
            raw = base64.b64decode(msg["data"])
            decompressed = zlib.decompress(raw)
            msg["data"] = json.loads(decompressed.decode("utf-8"))
            msg["compressed"] = False
        except Exception:
            # Leave data untouched on failure so the caller fails explicitly.
            pass
    return msg


# =============================================================================
# Basic Packet Protocol
# =============================================================================

def encode_packet(data: bytes) -> bytes:
    """
    Encode a packet with the given data using basic packet protocol.

    Format: ~<data-length>|{data}
    Where:
        - data-length: 4-byte integer representing length of data
        - data: actual data being sent as ASCII bytes

    Args:
        data: The data to encode as bytes

    Returns:
        bytes: Encoded packet

    Raises:
        ValueError: If data length exceeds maximum allowed size (2^32 - 1)

    Example:
        >>> encode_packet(b"hello")
        b'~0005|hello'
    """
    data_length = len(data)
    if data_length > 9999:  # Limiting to 9999 for a 4-digit length header
        raise ValueError("Data length exceeds maximum allowed size. Your data length: {}\nPacket='{}'".format(data_length, data))
    return f"~{data_length:04d}|".encode('ascii') + data


def decode_packet(packet: bytes) -> bytes:
    """
    Decode a packet and return the data using basic packet protocol.

    Args:
        packet: The packet to decode as bytes

    Returns:
        bytes: The decoded data

    Raises:
        ValueError: If packet format is invalid or data length mismatch

    Example:
        >>> decode_packet(b'~0005|hello')
        b'hello'
    """
    if not packet.startswith(b"~"):
        raise ValueError("Invalid packet format: missing start marker '~'")

    if len(packet) < 6:  # Minimum: ~0000|
        raise ValueError("Invalid packet format: packet too short. Packet: {}".format(packet))

    # Parse length from bytes 1-4 (after ~)
    try:
        length_str = int(packet[1:5].decode('ascii'))
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Invalid data length format in packet")

    if length_str < 0 or length_str > 2 ** 32 - 1:
        raise ValueError("Invalid data length in packet")

    # Check for pipe separator
    if packet[5:6] != b"|":
        raise ValueError("Invalid packet format: missing pipe separator")

    # Extract data based on the length
    data = packet[6:6 + length_str]
    if len(data) != length_str:
        raise ValueError("Data length does not match the specified length in packet")

    return data


def decode_multiple_packets(data: bytes) -> List[bytes]:
    """
    Decode multiple packets from a byte stream.

    Args:
        data: Byte stream containing multiple packets

    Returns:
        List[bytes]: List of decoded packet data

    Raises:
        ValueError: If any packet format is invalid

    Example:
        >>> packets = decode_multiple_packets(b'~0005|hello~0005|world')
        >>> packets
        [b'hello', b'world']
    """
    packets = []
    position = 0

    while position < len(data):
        remaining_data = data[position:]

        if not remaining_data.startswith(b"~"):
            raise ValueError(f"Invalid packet format at position {position}")

        if len(remaining_data) < 6:  # Minimum: ~0000|
            raise ValueError(f"Invalid packet format at position {position}: packet too short")

        # Parse length
        try:
            length_str = int(remaining_data[1:5].decode('ascii'))
        except (ValueError, UnicodeDecodeError):
            raise ValueError(f"Invalid data length format at position {position}")

        if length_str < 0 or length_str > 2 ** 32 - 1:
            raise ValueError(f"Invalid data length at position {position}")

        # Check for pipe separator
        if remaining_data[5:6] != b"|":
            raise ValueError(f"Invalid packet format at position {position}: missing pipe separator")

        # Calculate end position
        packet_end = 6 + length_str
        if packet_end > len(remaining_data):
            raise ValueError(f"Data length does not match specified length at position {position}")

        # Extract packet data
        packet_data = remaining_data[6:packet_end]
        packets.append(packet_data)

        # Move to next packet
        position += packet_end

    return packets


# =============================================================================
# Protocol 2: Market Data Protocol
# =============================================================================

def transmit_mkt_data_with_protocol_2(mkt_data) -> bytes:
    """
    Transmit market data using Protocol 2 format.

    Format: ~<packet-length><symbol-length>|<symbol><market-data>L
    Where:
        - packet-length: 4-byte integer for total packet length (excluding header)
        - symbol-length: 4-byte integer for symbol length
        - symbol: ASCII-encoded symbol
        - market-data: CSV-formatted market data from transferable_2() method
        - L: Terminator byte

    Args:
        mkt_data: Market data object with symbol attribute and transferable_2() method

    Returns:
        bytes: Encoded Protocol 2 packet

    Raises:
        TypeError: If mkt_data is not a valid market data object
        AttributeError: If mkt_data lacks required attributes/methods

    Example:
        >>> # Assuming mkt_data is a CapitalComMKTDataLive object
        >>> packet = transmit_mkt_data_with_protocol_2(mkt_data)
    """
    # Validate input (commented out original isinstance check as it requires specific import)
    if not hasattr(mkt_data, 'symbol') or not hasattr(mkt_data, 'transferable_2'):
        raise TypeError("mkt_data must have 'symbol' attribute and 'transferable_2()' method")

    # Get market data and symbol
    packet_data = mkt_data.transferable_2()
    symbol = mkt_data.symbol

    # Build packet without header
    symbol_bytes = symbol.encode('ascii')
    symbol_length_header = f'{len(symbol_bytes):04d}|'.encode('ascii')
    packet_without_heading = symbol_length_header + symbol_bytes + packet_data + b'L'

    length_of_packet = len(packet_without_heading)

    if length_of_packet > 9999:
        raise ValueError("Packet length exceeds maximum allowed size of 9999 bytes")

    # Add main header
    packet_length_header = f"~{length_of_packet:04d}".encode('ascii')
    packet = packet_length_header + packet_without_heading

    return packet


class Protocol2Parser:
    """
    O(n) parser for Protocol 2 market data packets where n = byte length.

    This parser efficiently decodes Protocol 2 packets containing market data
    with symbol information and configurable field ordering.

    Attributes:
        decoding_order (List[str]): Field names in the order they appear in packets
    """

    def __init__(self, decoding_order: List[str]) -> None:
        """
        Initialize parser with field order configuration.

        Args:
            decoding_order: List of field names in order they appear in packet.
                          Example: ['bid', 'bid_size', 'ask', 'ask_size', 'last', 'last_size', 'timestamp', 'transmission_time']
        """
        self.decoding_order = decoding_order

    def multi_parse(self, mixed_packets: bytes) -> List[Dict[str, Union[str, float]]]:
        """
        Parse multiple Protocol 2 packets from a byte stream.
        Wraps the O(n) P2 parser in a loop to handle multiple packets.
        Each packet is read twice (boundary detection + parsing), but
        the overall complexity remains O(n).

        Args:
            mixed_packets: Byte stream containing multiple Protocol 2 packets
        Returns:
            List of parsed packet dictionaries
        """
        # count how many packets are in the mixed_packets
        packets = []
        position = 0
        while position < len(mixed_packets):
            if mixed_packets[position] != ord('~'):
                raise ValueError(f"Invalid packet start at position {position}")

            # Extract packet length
            try:
                packet_length = int(mixed_packets[position + 1:position + 5].decode('ascii'))
            except (ValueError, UnicodeDecodeError):
                raise ValueError(f"Invalid packet length format at position {position}")

            total_packet_length = 5 + packet_length  # 5 bytes for header
            packet_bytes = mixed_packets[position:position + total_packet_length]
            packets.append(self.parse(packet_bytes))
            position += total_packet_length
        return packets

    def parse(self, packet_bytes: bytes) -> Dict[str, Union[str, float]]:
        """
        Parse Protocol 2 packet in O(n) time complexity.

        Packet Format: ~<packet-length><symbol-length>|<symbol><market-data>L

        Args:
            packet_bytes: Raw packet bytes to parse

        Returns:
            Dict containing:
                - 'symbol': String symbol identifier
                - Field values as specified in decoding_order (as floats)

        Raises:
            ValueError: If packet format is invalid, lengths don't match,
                       or values cannot be parsed

        Example:
            >>> parser = Protocol2Parser(['bid', 'ask', 'last'])
            >>> packet = b'~0025|0006|BTCUSD50000.0,50001.0,50000.5L'
            >>> result = parser.parse(packet)
            >>> result
            {'symbol': 'BTCUSD', 'bid': 50000.0, 'ask': 50001.0, 'last': 50000.5}
        """
        # Validate minimum packet size
        if len(packet_bytes) < 11:  # Minimum: ~0000|0000|L
            raise ValueError("Packet too short for Protocol 2 format")

        pos = 0

        # Parse header ~NNNN (5 bytes)
        if packet_bytes[pos] != ord('~'):
            raise ValueError("Invalid header: missing start marker '~'")
        pos += 1

        # Extract and validate packet length
        try:
            packet_length = int(packet_bytes[pos:pos + 4].decode('ascii'))
        except (ValueError, UnicodeDecodeError):
            raise ValueError("Invalid packet length format in header")
        pos += 4

        # Validate total packet size
        expected_total_length = 5 + packet_length
        if len(packet_bytes) != expected_total_length:
            raise ValueError(f"Packet length mismatch: expected {expected_total_length}, got {len(packet_bytes)}, full packet: {packet_bytes}")

        # Parse symbol length NNNN| (5 bytes)
        try:
            symbol_length = int(packet_bytes[pos:pos + 4].decode('ascii'))
        except (ValueError, UnicodeDecodeError):
            raise ValueError("Invalid symbol length format")
        pos += 4

        if packet_bytes[pos] != ord('|'):
            raise ValueError("Missing pipe separator after symbol length")
        pos += 1

        # Parse symbol (variable length)
        if pos + symbol_length > len(packet_bytes):
            raise ValueError("Symbol length exceeds available packet data")

        try:
            symbol = packet_bytes[pos:pos + symbol_length].decode('ascii')
        except UnicodeDecodeError:
            raise ValueError("Invalid ASCII encoding in symbol")
        pos += symbol_length

        # Validate terminator
        if packet_bytes[-1] != ord('L'):
            raise ValueError("Invalid terminator: expected 'L'")

        # Parse market data (everything except last byte 'L')
        market_data_bytes = packet_bytes[pos:-1]
        try:
            market_data_str = market_data_bytes.decode('ascii')
        except UnicodeDecodeError:
            raise ValueError("Invalid ASCII encoding in market data")

        # Parse CSV values in single pass
        values = self._parse_csv_values(market_data_str)

        # Validate value count matches expected fields
        if len(values) != len(self.decoding_order):
            print('VALUES:', values)
            print('Excess Values:', values[len(self.decoding_order):])
            print('FULL DECODING ORDER:', self.decoding_order)
            print('Expected length:', len(self.decoding_order))
            print('RAW PACKET:', packet_bytes)
            print('Total Commas:', market_data_str.count(','))
            raise ValueError(f"Field count mismatch: expected {len(self.decoding_order)} values, got {len(values)}")


        # Build result dictionary
        result = {'symbol': symbol}
        for i, field_name in enumerate(self.decoding_order):
            result[field_name] = values[i]

        return result

    def _parse_csv_values(self, data_str: str) -> List[float]:
        """
        Parse comma-separated values from string.

        Args:
            data_str: String containing comma-separated numeric values

        Returns:
            List[float]: Parsed numeric values

        Raises:
            ValueError: If values cannot be parsed as floats or if empty values found
        """
        if not data_str:
            raise ValueError("Empty market data")

        values = []
        current_value = ""

        for char in data_str:
            if char == ',':
                if not current_value:
                    raise ValueError("Empty value found in market data")
                try:
                    values.append(float(current_value))
                except ValueError:
                    raise ValueError(f"Invalid numeric value: '{current_value}'")
                current_value = ""
            else:
                current_value += char

        # Handle last value (no trailing comma)
        if current_value:
            try:
                values.append(float(current_value))
            except ValueError:
                raise ValueError(f"Invalid numeric value: '{current_value}'")
        elif data_str.endswith(','):
            raise ValueError("Trailing comma in market data")

        return values


# =============================================================================
# Example Usage and Testing
# =============================================================================

def demo_basic_protocol():
    """Demonstrate basic packet protocol functionality."""
    print("=== Basic Packet Protocol Demo ===")

    # Single packet
    original_data = b"Hello, World!"
    encoded = encode_packet(original_data)
    decoded = decode_packet(encoded)

    print(f"Original: {original_data}")
    print(f"Encoded:  {encoded}")
    print(f"Decoded:  {decoded}")
    print(f"Match:    {original_data == decoded}")
    print()

    # Multiple packets
    packets_data = [b"packet1", b"packet2", b"packet3"]
    combined = b"".join(encode_packet(data) for data in packets_data)
    decoded_packets = decode_multiple_packets(combined)

    print(f"Original packets: {packets_data}")
    print(f"Decoded packets:  {decoded_packets}")
    print(f"Match: {packets_data == decoded_packets}")
    print()


def demo_protocol2():
    """Demonstrate Protocol 2 with mock market data."""
    print("=== Protocol 2 Demo ===")

    # Mock market data class for demonstration
    class MockMarketData:
        def __init__(self, symbol: str, **kwargs):
            self.symbol = symbol
            self.data = kwargs

        def transferable_2(self) -> bytes:
            """Mock implementation returning CSV data."""
            values = [
                self.data.get('bid', 50000.0),
                self.data.get('bid_size', 1.0),
                self.data.get('ask', 50001.0),
                self.data.get('ask_size', 1.0),
                self.data.get('last', 50000.5),
                self.data.get('last_size', 1.0),
                self.data.get('timestamp', time.time()),
                self.data.get('python_timestamp', time.time())
            ]
            csv_string = ','.join(map(str, values))
            return csv_string.encode('ascii')

    # Create mock market data
    mkt_data = MockMarketData(
        symbol='BTCUSD',
        bid=50000.0,
        bid_size=1.0,
        ask=50001.0,
        ask_size=1.0,
        last=50000.5,
        last_size=1.0,
        timestamp=1750729519.2856908,
        python_timestamp=1750729519.2862039
    )

    # Encode packet
    packet = transmit_mkt_data_with_protocol_2(mkt_data)
    print(f"Encoded packet: {packet}")
    print(f"Packet length: {len(packet)} bytes")
    print()

    # Parse packet
    decoding_order = ['bid', 'bid_size', 'ask', 'ask_size', 'last', 'last_size', 'shortable_shares', 'timestamp', 'transmission_time']
    parser = Protocol2Parser(decoding_order)

    try:
        result = parser.parse(b'~00710004|None0.0,400,0.0,0,0.0,0.0,0.0,1750853933.618784,1750853961.558223L')
        print("Parsed result:")
        for key, value in result.items():
            print(f"  {key}: {value}")
    except ValueError as e:
        print(f"Parse error: {e}")


if __name__ == "__main__":
    # demo_basic_protocol()
    demo_protocol2()

    # Test the original example from the file
    # print("=== Original Example Test ===")
    # test_packet = b'~0046|{"symbol":"BTCUSD","action":"resolve\\/stream"}'
    # try:
    #     decoded_packets = decode_multiple_packets(test_packet)
    #     print(f"Decoded: {decoded_packets}")
    # except ValueError as e:
    #     print(f"Error: {e}")