import json
from argus import protocol
from typing import Any, Dict, Optional



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
        return protocol.encode_packet(json.dumps({
            "action": self.action,
            "data": self.data,
            "error": self.error,
            "compressed": self.compressed,
            "correlation_id": self.correlation_id
        }))