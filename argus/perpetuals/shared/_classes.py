import json
import zlib
import base64
from argus import protocol
from typing import Any, Dict, Optional
from argus.perpetuals.shared import _errors as ers


def compress(data: dict) -> str:
    minified = json.dumps(data, separators=(',', ':')).encode()
    return base64.b64encode(zlib.compress(minified, level=9)).decode()



class OutboundMessage:
    """
    This class enforces the following structure for outbound messages:
    {
      "action": "<command_name>",
      "data": { /* response data or null */ },
      "error": "<error message or null>",
      "compressed": <bool>, // true when data is auto-compressed (see polymarket docs for details)
      "correlation_id": "<uuid>" // None if the request errors before the packet was processed, or a pushed response
    }
    """
    def __init__(self, action: str, data: Optional[Dict[str, Any]] = None, error: Optional[str] = None, compressed: bool = False, correlation_id: Optional[str] = None):
        self.action = action
        self.data = data
        self.error = error
        self.compressed = compressed
        self.correlation_id = correlation_id

    def convert_to_protocol_1(self) -> bytes:
        """
        Converts the outbound message into P1 bytes
        :return:
        """
        return protocol.encode_packet(json.dumps(self._compress_and_validate()).encode('utf-8'))

    def _compress_and_validate(self) -> dict:
        """
        Checks if the data requires compression, then compresses it.
        Checks max len of the data <= 9990
        :return:
        """
        size_of_payload = len(json.dumps(self.data))
        if size_of_payload >= 9500:
            print("[auto-compress] Data is being auto-compressed original size: " + str(size_of_payload))
            compressed_data = compress(self.data)
            print("[auto-compress] Compressed size: " + str(len(compressed_data)))
            if len(compressed_data) > 9990:
                raise ers.PacketTooLargeError("Data exceeds max size of 9990 bytes, size of compressed data: " + str(len(compressed_data)) + "")
            return {
                "action": self.action,
                "data": compressed_data,
                "error": self.error,
                "compressed": True,
                "correlation_id": self.correlation_id
            }
        else:
            return {
                "action": self.action,
                "data": self.data,
                "error": self.error,
                "compressed": False,
                "correlation_id": self.correlation_id
            }
