import time

# RoutingHelper, ArgsObject, and CorrelationIDChecker used to live here. They were
# exchange-agnostic dispatcher plumbing, so they were moved to argus._argus_utils
# once the HyperLiquid and Lighter dispatchers needed the same functionality.
# Import them (and CorrelationIDError/CorrelationIDLengthTooLongError/
# CorrelationIDAlreadySeenError) from argus._argus_utils, not from here.


class PolyMarketDispatcherError(Exception):
    pass

class OrderExecutionError(PolyMarketDispatcherError):
    pass

class OrderExecutionDisabledError(OrderExecutionError):
    pass


class InvalidArgumentError(PolyMarketDispatcherError):
    pass

class UnableToEncodeMarketDataError(PolyMarketDispatcherError):
    pass

def print_with_name(*args, **kwargs):
    print("[{}]".format(__name__), *args, **kwargs)


class P2ConvertClass:
    """
    Implements the methods required for the P2 encoder to encode market data.
    - symbol
    - transferable_2

    Expected input market data:
    {
        '661095475084821930790589425827399710453605787397495798070750303202782280580': {
            'bids': [
                {'price': '0.75', 'size': '65'},
                {'price': '0.74', 'size': '299'},
                {'price': '0.73', 'size': '621.2'},
                {'price': '0.72', 'size': '2472'},
                {'price': '0.37', 'size': '464'},
                {'price': '0.36', 'size': '464'},
                {'price': '0.01', 'size': '2822.47'}
            ],
            'asks': [
                {'price': '0.76', 'size': '227.02'},
                {'price': '0.77', 'size': '1737.48'},
                {'price': '0.78', 'size': '335'},
                {'price': '0.79', 'size': '585'},
                {'price': '0.8', 'size': '746'},
                {'price': '0.81', 'size': '704'},
                {'price': '0.99', 'size': '4998.02'}
                ]
            },
        'timestamp': '1770251679393'
    }

    """

    def __init__(self, ticker: str, market_slug: str,
                 asset_id: str, market_data: dict, order_book_depth: int, forced_symbol: str = ""):
        self.ticker = ticker
        self.market_slug = market_slug
        self.asset_id = asset_id
        self.market_data = market_data
        self.order_book_depth = order_book_depth
        self.forced_symbol = forced_symbol

    @property
    def symbol(self) -> str:
        if self.forced_symbol != "":
            return self.forced_symbol
        else:
            return f"{self.ticker}-{self.market_slug}-{self.asset_id}"

    def transferable_2(self) -> bool:
        data_obj = self.market_data.get(self.asset_id, {})
        try:
            bids = data_obj.get('bids', [])[:self.order_book_depth]
            asks = data_obj.get('asks', [])[:self.order_book_depth]
        except (AttributeError, KeyError, TypeError) as e:
            raise UnableToEncodeMarketDataError(
                f"Market data for asset_id {self.asset_id} is not in the expected format. Cannot encode. Data: {data_obj}, e={e}"
            )

        market_packet = str()

        for bid_index in range(self.order_book_depth):
            if bid_index < len(bids):
                bid = bids[bid_index]
                market_packet += f"{bid['price']},{bid['size']},"
            else:
                market_packet += "0,0,"

        for ask_index in range(self.order_book_depth):
            if ask_index < len(asks):
                ask = asks[ask_index]
                market_packet += f"{ask['price']},{ask['size']},"
            else:
                market_packet += "0,0,"

        # add the timestamp at the end and the server timestamp
        market_packet += f"{self.market_data.get('timestamp', '')},{time.time()}"
        return market_packet.encode('ascii')

