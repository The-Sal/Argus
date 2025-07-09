import random
import socket
import time
import unittest
from argus.ib import MKTDispatcher, IBKRModes, MarketData, IBKRFields

class IBKR_Test(unittest.TestCase):
    def setUp(self):
        """Set up the test class."""
        # Initialize any required resources or configurations here
        self.dispatcher_dry = MKTDispatcher(mode=IBKRModes.PROTOCOL_2, dryRun=True)


    def test_stuffing_system(self):
        dispatcher = self.dispatcher_dry
        # Test the stuffing system
        unique_value_one = random.randint(1, 100)
        unique_value_two = random.randint(1, 100)
        data_objs = [
            MarketData(
                contract_id=123456,
                server_id=1,
                contract_exchange='NASDAQ',
                topic='smd+123456',
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
                contract_id=123456,
                server_id=1,
                contract_exchange='NASDAQ',
                topic='smd+123456',
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
            contract_id=123456,
            server_id=1,
            contract_exchange='NASDAQ',
            topic='smd+123456',
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