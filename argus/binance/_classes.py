from typing import List, Tuple
from dataclasses import dataclass
from argus.capital import CapitalComMKTDataLive

@dataclass
class DepthUpdate:
    """Represents order book depth update data"""
    e: str  # Event type
    E: int  # Event time (milliseconds)
    s: str  # Symbol
    U: int  # First update ID
    u: int  # Final update ID
    b: List[List[str]]  # Bids [price, quantity]
    a: List[List[str]]  # Asks [price, quantity]

@dataclass
class DepthStreamMessage:
    """Complete WebSocket message wrapper"""
    stream: str
    data: DepthUpdate

    @classmethod
    def from_dict(cls, d: dict):
        return cls(
            stream=d['stream'],
            data=DepthUpdate(**d['data'])
        )


@dataclass
class AggTradeData:
    """Aggregated trade data"""
    e: str          # Event type
    E: int          # Event time (ms)
    s: str          # Symbol
    a: int          # Aggregate trade ID
    p: str          # Price
    q: str          # Quantity
    f: int          # First trade ID
    l: int          # Last trade ID
    T: int          # Trade time (ms)
    m: bool         # Is buyer market maker
    M: bool         # Ignore (best price match)

@dataclass
class AggTradeMessage:
    """WebSocket aggregate trade message"""
    stream: str
    data: AggTradeData

    @classmethod
    def from_dict(cls, d: dict):
        return cls(
            stream=d['stream'],
            data=AggTradeData(**d['data'])
        )



@dataclass
class KlineData:
    """Individual kline/candlestick data"""
    t: int          # Kline start time (ms)
    T: int          # Kline close time (ms)
    s: str          # Symbol
    i: str          # Interval
    f: int          # First trade ID
    L: int          # Last trade ID
    o: str          # Open price
    c: str          # Close price
    h: str          # High price
    l: str          # Low price
    v: str          # Base asset volume
    n: int          # Number of trades
    x: bool         # Is kline closed?
    q: str          # Quote asset volume
    V: str          # Taker buy base asset volume
    Q: str          # Taker buy quote asset volume
    B: str          # Ignore

@dataclass
class KlineMessage:
    """WebSocket kline stream message"""
    stream: str
    data: 'KlineEventData'

    @classmethod
    def from_dict(cls, d: dict):
        data = KlineEventData(
            e=d['data']['e'],
            E=d['data']['E'],
            s=d['data']['s'],
            k=KlineData(**d['data']['k'])
        )
        return cls(stream=d['stream'], data=data)

@dataclass
class KlineEventData:
    """Kline event wrapper"""
    e: str          # Event type
    E: int          # Event time (ms)
    s: str          # Symbol
    k: KlineData    # Kline data


class Binance_CapitalComMKTDataLive(CapitalComMKTDataLive):
    """
    Extension of CapitalComMKTDataLive to support Binance market data.
    This class conforms with the 'transmit_mkt_data_with_protocol_2' function
    and allows Binance data to be transmitted using Protocol 2 format.

    Since Binance doesn't have shortable shares or unrealized P&L like IBKR,
    those fields are set to 0 or can be omitted from Protocol 2 transmission.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @classmethod
    def from_binance_depth(cls, symbol: str, depth_update: DepthUpdate):
        """Create market data from Binance depth update (order book)."""
        # Extract top bid and ask from the order book
        try:
            top_bid = float(depth_update.b[0][0]) if depth_update.b else 0.0
            top_bid_size = float(depth_update.b[0][1]) if depth_update.b else 0.0
        except (IndexError, ValueError):
            top_bid = 0.0
            top_bid_size = 0.0

        try:
            top_ask = float(depth_update.a[0][0]) if depth_update.a else 0.0
            top_ask_size = float(depth_update.a[0][1]) if depth_update.a else 0.0
        except (IndexError, ValueError):
            top_ask = 0.0
            top_ask_size = 0.0

        # Use mid price as last price if no trade data available
        last_price = (top_bid + top_ask) / 2 if (top_bid > 0 and top_ask > 0) else 0.0

        return cls(
            symbol=symbol.upper(),
            bid=top_bid,
            bid_size=top_bid_size,
            ask=top_ask,
            ask_size=top_ask_size,
            last=last_price,
            last_size=0.0,
            timestamp=int(depth_update.E)
        )

    @classmethod
    def from_binance_trade(cls, symbol: str, trade_data: AggTradeData,
                           existing_data: 'Binance_CapitalComMKTDataLive' = None):
        """Create or update market data from Binance aggregate trade."""
        last_price = float(trade_data.p)
        last_size = float(trade_data.q)

        # If we have existing depth data, preserve it and just update the last trade
        if existing_data:
            return cls(
                symbol=symbol.upper(),
                bid=existing_data.bid,
                bid_size=existing_data.bid_size,
                ask=existing_data.ask,
                ask_size=existing_data.ask_size,
                last=last_price,
                last_size=last_size,
                timestamp=int(trade_data.T)
            )
        else:
            # No depth data, use trade price for bid/ask approximation
            return cls(
                symbol=symbol.upper(),
                bid=last_price,
                bid_size=0.0,
                ask=last_price,
                ask_size=0.0,
                last=last_price,
                last_size=last_size,
                timestamp=int(trade_data.T)
            )