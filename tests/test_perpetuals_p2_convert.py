"""
Offline tests for the shared perpetuals P2 order-book converter
(`argus.perpetuals.shared._classes.P2OrderBookConvertClass`) and its Hyperliquid
/ Lighter subclasses, including the market-data shape enforcement added when the
two converters were unified.

Run with: pytest tests/test_perpetuals_p2_convert.py
"""
import unittest

from argus.perpetuals.hyper._classes import HLP2ConvertClass
from argus.perpetuals.lighter._classes import LighterP2ConvertClass
from argus.protocol import Protocol2Parser, transmit_mkt_data_with_protocol_2


def _decoder(depth: int) -> Protocol2Parser:
    fields = []
    for i in range(depth):
        fields += [f"bid_{i + 1}", f"bid_size_{i + 1}"]
    for i in range(depth):
        fields += [f"ask_{i + 1}", f"ask_size_{i + 1}"]
    fields += ["timestamp", "transmission_time"]
    return Protocol2Parser(fields)


def _book():
    return {
        "bids": [{"price": "100", "size": "1"}, {"price": "99", "size": "2"}],
        "asks": [{"price": "101", "size": "3"}, {"price": "102", "size": "4"}],
    }


class HyperConverterTest(unittest.TestCase):
    def test_wire_round_trip(self):
        market_data = {"BTC": _book(), "timestamp": 111}
        packet = transmit_mkt_data_with_protocol_2(
            HLP2ConvertClass(coin="BTC", market_data=market_data, order_book_depth=2)
        )
        decoded = _decoder(2).parse(packet)
        self.assertEqual(decoded["symbol"], "BTC")
        self.assertEqual(decoded["bid_1"], 100.0)
        self.assertEqual(decoded["ask_2"], 102.0)

    def test_padding_when_book_thinner_than_depth(self):
        market_data = {"BTC": {"bids": [{"price": "100", "size": "1"}], "asks": []}, "timestamp": 111}
        packet = transmit_mkt_data_with_protocol_2(
            HLP2ConvertClass(coin="BTC", market_data=market_data, order_book_depth=3)
        )
        decoded = _decoder(3).parse(packet)
        self.assertEqual(decoded["bid_1"], 100.0)
        for name in ("bid_2", "bid_3", "ask_1", "ask_2", "ask_3"):
            self.assertEqual(decoded[name], 0.0)


class LighterConverterTest(unittest.TestCase):
    def test_wire_round_trip_uses_market_id_for_lookup_symbol_for_identity(self):
        market_data = {7: _book(), "timestamp": 222}
        packet = transmit_mkt_data_with_protocol_2(
            LighterP2ConvertClass(symbol="ETH", market_id=7, market_data=market_data, order_book_depth=2)
        )
        decoded = _decoder(2).parse(packet)
        self.assertEqual(decoded["symbol"], "ETH")
        self.assertEqual(decoded["bid_1"], 100.0)


class ShapeEnforcementTest(unittest.TestCase):
    def test_market_data_must_be_mapping(self):
        with self.assertRaises(TypeError):
            HLP2ConvertClass(coin="BTC", market_data=None, order_book_depth=2)  # type: ignore[arg-type]

    def test_missing_book_for_key(self):
        with self.assertRaises(ValueError):
            HLP2ConvertClass(coin="BTC", market_data={"ETH": _book()}, order_book_depth=2)

    def test_levels_must_be_lists(self):
        with self.assertRaises(ValueError):
            HLP2ConvertClass(coin="BTC", market_data={"BTC": {"bids": None, "asks": []}}, order_book_depth=2)

    def test_level_requires_price_and_size(self):
        with self.assertRaises(ValueError):
            HLP2ConvertClass(
                coin="BTC", market_data={"BTC": {"bids": [{"price": "1"}], "asks": []}}, order_book_depth=2
            )

    def test_lighter_missing_market_id_key(self):
        with self.assertRaises(ValueError):
            LighterP2ConvertClass(
                symbol="ETH", market_id=7, market_data={8: _book()}, order_book_depth=2
            )

    def test_negative_depth_rejected(self):
        with self.assertRaises(ValueError):
            HLP2ConvertClass(coin="BTC", market_data={"BTC": _book()}, order_book_depth=-1)


if __name__ == "__main__":
    unittest.main()
