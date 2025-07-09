import json
import random
import unittest
from argus.capital import (
    encode_packet, decode_packet, MKTDispatcher, Environment
)

class TestCapcom(unittest.TestCase):

    def setUp(self):
        """Set up the test environment."""
        self.dispatcher = MKTDispatcher(
            environment=Environment.LIVE  # for some security, use LIVE environment
        )

    def test_back_and_forth(self):
        """Test encoding and decoding a packet."""
        for _ in range(100):
            random_generated_data = json.dumps({
                "key": random.randint(1, 100),
                "value": random.uniform(1.0, 100.0),
            }).encode('ascii')
            original_data = random_generated_data
            encoded = encode_packet(original_data)
            decoded = decode_packet(encoded)
            self.assertEqual(original_data, decoded)

    def test_mkt_dispatcher_resolve_symbol(self):
        """Test the MKTDispatcher resolution."""
        symbols = ['AAPL', 'GOOGL', 'MSFT', 'QQQ', 'TQQQ', 'YMAX', 'CONY']
        for symbol in symbols:
            result = self.dispatcher.resolve_symbol(symbol, None)
            self.assertIsNotNone(
                result,
                f"Resolution for {symbol} failed."
            )
            print(f"Resolved symbol {result['instrument']['name']} successfully.")


    def test_socket_systems_and_mkt_data(self):
        from tests.test import test_mkt_dispatcher
        self.dispatcher.start_server()
        test_mkt_dispatcher(
            symbols=['BTCUSD', 'ETHUSD']
        )