import json
import time
import zlib
import base64
from argus import protocol
from collections.abc import Mapping
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


class P2OrderBookConvertClass:
    """
    Shared base for the duck-typed adapter consumed by
    `argus.protocol.transmit_mkt_data_with_protocol_2` (`.symbol` /
    `.transferable_2()`). Hyperliquid's `HLP2ConvertClass` and Lighter's
    `LighterP2ConvertClass` both subclass this, differing only in the key under
    which the order book is stored in `market_data`.

    Enforces the exact market-data shape both venues produce (the same shape
    `argus.polymarket._classes.P2ConvertClass` uses):

        {
            <lookup_key>: {
                "bids": [{"price": "97500", "size": "1.5"}, ...],
                "asks": [{"price": "97501", "size": "2.0"}, ...],
            },
            "timestamp": 1770251679393,
        }

    `symbol` is the P2 wire identity; `lookup_key` is the key the book is stored
    under in `market_data` (the coin string for Hyperliquid, the integer
    `market_id` for Lighter). `timestamp` is optional and rendered as an empty
    field when absent, matching the previous behavior.

    A malformed book raises from the constructor (TypeError/ValueError) rather
    than silently emitting a packet full of zeros.
    """

    def __init__(self, symbol: str, lookup_key, market_data: Mapping, order_book_depth: int):
        if not isinstance(order_book_depth, int) or isinstance(order_book_depth, bool) or order_book_depth < 0:
            raise ValueError(
                f"{type(self).__name__}: order_book_depth must be a non-negative int, got {order_book_depth!r}"
            )
        self._symbol = symbol
        self._lookup_key = lookup_key
        self._order_book_depth = order_book_depth
        self._validate_market_data(market_data)
        self.market_data = market_data

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def order_book_depth(self) -> int:
        return self._order_book_depth

    def _validate_market_data(self, market_data: Mapping) -> None:
        if not isinstance(market_data, Mapping):
            raise TypeError(
                f"{type(self).__name__}: market_data must be a mapping, got {type(market_data).__name__}"
            )

        book = market_data.get(self._lookup_key)
        if not isinstance(book, Mapping):
            present = [key for key in market_data if key != 'timestamp']
            raise ValueError(
                f"{type(self).__name__}: market_data has no order book for key {self._lookup_key!r} "
                f"(present keys: {present!r})"
            )

        for side in ('bids', 'asks'):
            levels = book.get(side)
            if not isinstance(levels, list):
                raise ValueError(
                    f"{type(self).__name__}: {side!r} for key {self._lookup_key!r} must be a list, "
                    f"got {type(levels).__name__}"
                )
            for i, level in enumerate(levels):
                if not isinstance(level, Mapping) or 'price' not in level or 'size' not in level:
                    raise ValueError(
                        f"{type(self).__name__}: {side}[{i}] must be a mapping with 'price' and 'size', "
                        f"got {level!r}"
                    )

    def transferable_2(self) -> bytes:
        data_obj = self.market_data.get(self._lookup_key, {})
        bids = data_obj.get('bids', [])[:self._order_book_depth]
        asks = data_obj.get('asks', [])[:self._order_book_depth]

        market_packet = ""
        for i in range(self._order_book_depth):
            if i < len(bids):
                market_packet += f"{bids[i]['price']},{bids[i]['size']},"
            else:
                market_packet += "0,0,"

        for i in range(self._order_book_depth):
            if i < len(asks):
                market_packet += f"{asks[i]['price']},{asks[i]['size']},"
            else:
                market_packet += "0,0,"

        market_packet += f"{self.market_data.get('timestamp', '')},{time.time()}"
        return market_packet.encode('ascii')
