from typing import List, Tuple
from dataclasses import dataclass

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
