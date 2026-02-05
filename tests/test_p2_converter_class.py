import time
import random
import unittest
from argus.polymarket import P2ConvertClass
from argus.protocol import Protocol2Parser, transmit_mkt_data_with_protocol_2


def create_fake_data(num_tranches=None):
    """
    Create mock order book data with randomized values.

    Args:
        num_tranches: Number of price levels (5-100). If None, randomly chosen.

    Returns:
        Dictionary with market ID, bids, asks, and timestamp
    """
    # Generate random market ID (similar length to the original)
    market_id = str(random.randint(10 ** 77, 10 ** 78 - 1))

    # Random number of tranches if not specified
    if num_tranches is None:
        num_tranches = random.randint(5, 100)
    else:
        num_tranches = max(5, min(100, num_tranches))  # Clamp between 5-100

    # Generate a random mid-price around which to build the order book
    mid_price = round(random.uniform(0.01, 100.0), 2)

    # Generate bids (sorted descending by price)
    bids = []
    current_price = mid_price * 0.99  # Start just below mid-price
    for i in range(num_tranches):
        price = round(current_price - (i * random.uniform(0.001, 0.05)), 2)
        price = max(0.01, price)  # Ensure the price doesn't go below 0.01
        size = round(random.uniform(10, 5000), 2)
        bids.append({
            'price': str(price),
            'size': str(size)
        })

    # Generate asks (sorted ascending by price)
    asks = []
    current_price = mid_price * 1.01  # Start just above mid-price
    for i in range(num_tranches):
        price = round(current_price + (i * random.uniform(0.001, 0.05)), 2)
        size = round(random.uniform(10, 5000), 2)
        asks.append({
            'price': str(price),
            'size': str(size)
        })

    # Generate timestamp (milliseconds since epoch)
    timestamp = str(int(time.time() * 1000) + random.randint(-86400000, 86400000))

    return {
        market_id: {
            'bids': bids,
            'asks': asks
        },
        'timestamp': timestamp
    }

def make_decoder(tranches: int) -> Protocol2Parser:
    decoder_arg = []
    for i in range(tranches):
        decoder_arg.append('bid_{}'.format(i + 1))
        decoder_arg.append('bid_size_{}'.format(i + 1))

    for i in range(tranches):
        decoder_arg.append('ask_{}'.format(i + 1))
        decoder_arg.append('ask_size_{}'.format(i + 1))

    decoder_arg.append('timestamp')
    decoder_arg.append('transmission_time')
    return Protocol2Parser(decoder_arg)


class MyTestCase(unittest.TestCase):
    def test_basic_test_p2_encoder(self):
        fake_data = create_fake_data(num_tranches=50)
        p2_converter = P2ConvertClass(
            asset_id=list(fake_data.keys())[0],
            ticker='example_ticker',
            market_slug='example_market_slug',
            market_data=fake_data,
            order_book_depth=10
        )
        data = transmit_mkt_data_with_protocol_2(
            mkt_data=p2_converter
        )

        decoder = make_decoder(tranches=10)
        decoded_data = decoder.parse(data)

        # check every single field
        for i in range(10):
            self.assertEqual(
                decoded_data['bid_{}'.format(i + 1)],
                float(fake_data[list(fake_data.keys())[0]]['bids'][i]['price'])
            )
            self.assertEqual(
                decoded_data['bid_size_{}'.format(i + 1)],
                float(fake_data[list(fake_data.keys())[0]]['bids'][i]['size'])
            )
            self.assertEqual(
                decoded_data['ask_{}'.format(i + 1)],
                float(fake_data[list(fake_data.keys())[0]]['asks'][i]['price'])
            )
            self.assertEqual(
                decoded_data['ask_size_{}'.format(i + 1)],
                float(fake_data[list(fake_data.keys())[0]]['asks'][i]['size'])
            )

    def test_if_padding_works(self):
        """
        Test that checks to make sure the padding is addding 0s and not making
        up data.
        :return:
        """

        fake_data = create_fake_data(num_tranches=5)
        p2_converter = P2ConvertClass(
            asset_id=list(fake_data.keys())[0],
            ticker='example_ticker',
            market_slug='example_market_slug',
            market_data=fake_data,
            order_book_depth=10
        )
        data = transmit_mkt_data_with_protocol_2(
            mkt_data=p2_converter
        )

        decoder = make_decoder(tranches=10)
        decoded_data = decoder.parse(data)

        # check every single field
        for i in range(5):
            self.assertEqual(
                decoded_data['bid_{}'.format(i + 1)],
                float(fake_data[list(fake_data.keys())[0]]['bids'][i]['price'])
            )
            self.assertEqual(
                decoded_data['bid_size_{}'.format(i + 1)],
                float(fake_data[list(fake_data.keys())[0]]['bids'][i]['size'])
            )
            self.assertEqual(
                decoded_data['ask_{}'.format(i + 1)],
                float(fake_data[list(fake_data.keys())[0]]['asks'][i]['price'])
            )
            self.assertEqual(
                decoded_data['ask_size_{}'.format(i + 1)],
                float(fake_data[list(fake_data.keys())[0]]['asks'][i]['size'])
            )

        # check padding
        for i in range(5, 10):
            self.assertEqual(
                decoded_data['bid_{}'.format(i + 1)],
                0.0
            )
            self.assertEqual(
                decoded_data['bid_size_{}'.format(i + 1)],
                0.0
            )
            self.assertEqual(
                decoded_data['ask_{}'.format(i + 1)],
                0.0
            )
            self.assertEqual(
                decoded_data['ask_size_{}'.format(i + 1)],
                0.0
            )





if __name__ == '__main__':
    unittest.main()
