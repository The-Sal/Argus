# Packet Encoding Rules
# Start packet
# ~<data-length>|{data}
# Where:
#   data-length is the length of the data in bytes and is a 4-byte integer
#   data is the actual data being sent, encoded as ascii bytes

def encode_packet(data: bytes) -> bytes:
    """Encode a packet with the given data."""
    data_length = len(data)
    if data_length > 2**32 - 1:
        raise ValueError("Data length exceeds maximum allowed size.")
    return f"~{data_length:04d}|".encode('ascii') + data

def decode_packet(packet: bytes) -> bytes:
    """Decode a packet and return the data."""
    if not packet.startswith(b"~"):
        raise ValueError("Invalid packet format.")

    # read the first 4 bytes after the start marker
    length_str = int(packet[1:5].decode('ascii'))
    if length_str < 0 or length_str > 2**32 - 1:
        raise ValueError("Invalid data length in packet.")

    # extract the data based on the length
    data = packet[6:6 + length_str]
    if len(data) != length_str:
        raise ValueError("Data length does not match the specified length in packet.")
    return data

