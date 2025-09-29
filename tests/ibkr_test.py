import random
import unittest
from argus.ib import MKTDispatcher, IBKRModes, MarketData, IBKRFields

def _empty_mock_socket():
    """Create a mock socket that does nothing."""
    class MockSocket:
        def __getattr__(self, name):
            if name == 'recv':
                return lambda bufsize: b''
            elif name == 'sendall':
                return lambda data: None
            elif name == 'close':
                return lambda: None
            elif name == 'idx':
                return 'real'
            else:
                raise AttributeError(f"MockSocket has no attribute '{name}'")


    return MockSocket()

class IBKR_Test(unittest.TestCase):
    def setUp(self):
        """Set up the test class."""
        # Initialize any required resources or configurations here
        self.dispatcher_dry = MKTDispatcher(mode=IBKRModes.PROTOCOL_2, dryRun=True)


    def test_stuffing_system(self):
        mock_socket = _empty_mock_socket()
        dispatcher = self.dispatcher_dry
        # Define contract_id as a variable instead of using magic number
        contract_id = 123456
        
        # Add mock_socket to the dispatcher's con_id_to_client dictionary
        dispatcher.con_id_to_client[contract_id] = [mock_socket]
        
        # Test the stuffing system
        unique_value_one = random.randint(1, 100)
        unique_value_two = random.randint(1, 100)
        data_objs = [
            MarketData(
                contract_id=contract_id,
                server_id=1,
                contract_exchange='NASDAQ',
                topic=f'smd+{contract_id}',
                data={
                    str(IBKRFields.SYMBOL): 'AAPL',
                    str(IBKRFields.LAST_PRICE): '150.00',
                    str(IBKRFields.ASK_PRICE): '151.00',
                    str(IBKRFields.ASK_SIZE): '100',
                    str(IBKRFields.BID_PRICE): '149.50',
                    str(IBKRFields.BID_SIZE): '200',
                    str(IBKRFields.SHORTABLE_SHARES): unique_value_one
                },
            ),
            MarketData(
                contract_id=contract_id,
                server_id=1,
                contract_exchange='NASDAQ',
                topic=f'smd+{contract_id}',
                data={
                    str(IBKRFields.LAST_PRICE): '151.00',
                    str(IBKRFields.ASK_PRICE): '151.00',
                    str(IBKRFields.ASK_SIZE): '100',
                    str(IBKRFields.BID_PRICE): '149.50',
                    str(IBKRFields.BID_SIZE): '200',
                    str(IBKRFields.SHORTABLE_SHARES): unique_value_two
                },
            ),
        ]
        for data in data_objs:
            dispatcher.callback(data)

        empty_mkt_data = MarketData(
            contract_id=contract_id,
            server_id=1,
            contract_exchange='NASDAQ',
            topic=f'smd+{contract_id}',
            data={
                str(IBKRFields.SYMBOL): 'AAPL',
            }
        )
        dispatcher.callback(empty_mkt_data)
        data = self.dispatcher_dry._stuff_from_cache(empty_mkt_data, ib_fields=[IBKRFields.SHORTABLE_SHARES])
        last_point = data_objs[-1]
        self.assertEqual(data.get(IBKRFields.SHORTABLE_SHARES), last_point.get(IBKRFields.SHORTABLE_SHARES))



if __name__ == '__main__':
    unittest.main()