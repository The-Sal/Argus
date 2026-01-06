import json
from typing import List
from dataclasses import dataclass, field



class PolyMarketException(Exception):
    pass


class OrderException(PolyMarketException):
    pass


@dataclass
class PolyMarketOrder:
    """Represents a single order retrieved from the Polymarket CLOB via REST API (get_orders).
    
    This data class is populated directly from the py_clob_client.clob.get_orders() response
    and represents the current state of an order on the Polymarket CLOB. It is used to
    track order details for risk management, order monitoring, and portfolio tracking purposes.
    
    The PolyRestAPI.get_orders() method returns a list of PolyMarketOrder objects, which are
    typically used to monitor active orders, check fills, and manage trading positions.
    
    Attributes:
        id: Unique identifier for the order on the CLOB.
        status: Current status of the order ('OPEN', 'FILLED', 'CANCELLED', etc).
        owner: Address of the account that owns/created the order.
        maker_address: Address of the market maker (or proxy funder) for order settlement.
        market: Market identifier from Polymarket for the prediction market.
        asset_id: Token ID representing the specific outcome being traded.
        side: Direction of the order ('BUY' or 'SELL').
        original_size: Original number of shares/contracts specified in the order.
        size_matched: Amount of the original_size that has been filled via trades.
        price: Price per share in USDC (0-1 for binary markets).
        outcome: Description of the outcome this order is betting on.
        expiration: Timestamp or expiration policy for order validity.
        order_type: Type of order placement (e.g., 'GTC' = Good-Till-Cancelled).
        associate_trades: List of Trade objects that have filled (or partially filled) this order.
        created_at: Unix timestamp indicating when the order was placed.
    """
    id: str
    status: str
    owner: str
    maker_address: str
    market: str
    asset_id: str
    side: str
    original_size: str
    size_matched: str
    price: str
    outcome: str
    expiration: str
    order_type: str
    associate_trades: List
    created_at: int


    def __repr__(self):
        return (
            f"PolyMarketOrder(id={self.id}, status={self.status}, owner={self.owner}, "
            f"maker_address={self.maker_address}, market={self.market}, asset_id={self.asset_id}, "
            f"side={self.side}, original_size={self.original_size}, size_matched={self.size_matched}, "
            f"price={self.price}, outcome={self.outcome}, expiration={self.expiration}, "
            f"order_type={self.order_type}, created_at={self.created_at})"
        )



@dataclass
class MakerOrder:
    """Represents a liquidity provider's order that was matched in a trade.
    
    This is a nested component within Trade objects and comes from the Polymarket CLOB
    trades endpoint via py_clob_client.clob.get_trades(). Each MakerOrder represents
    the perspective of a market maker whose liquidity was consumed by a taker's order.
    
    MakerOrder objects are used to break down trade composition, track counterparty info,
    understand fee structures, and attribute executed fills to specific liquidity sources.
    A single Trade can have multiple MakerOrder objects if it was filled against multiple
    liquidity pools or makers.
    
    Attributes:
        order_id: Unique identifier for this specific maker order on the CLOB.
        owner: Address of the liquidity provider/maker who placed this order.
        maker_address: Address of the proxy/settlement account for the maker.
        matched_amount: Number of shares/contracts filled from this maker's liquidity.
        price: Price per share that the maker quoted (0-1 for binary markets).
        fee_rate_bps: Fee charged by Polymarket in basis points (100 bps = 1%).
        asset_id: Token ID representing the outcome both taker and maker are trading.
        outcome: Description of the outcome being traded.
        side: Direction from the maker's perspective ('BUY' or 'SELL').
    """
    order_id: str
    owner: str
    maker_address: str
    matched_amount: float
    price: float
    fee_rate_bps: int
    asset_id: str
    outcome: str
    side: str

    def __repr__(self) -> str:
        return (f"MakerOrder(order_id={self.order_id!r}, owner={self.owner!r}, "
                f"maker_address={self.maker_address!r}, matched_amount={self.matched_amount}, "
                f"price={self.price}, fee_rate_bps={self.fee_rate_bps}, asset_id={self.asset_id!r}, "
                f"outcome={self.outcome!r}, side={self.side!r})")

    @classmethod
    def from_dict(cls, data: dict) -> 'MakerOrder':
        return cls(
            order_id=data['order_id'],
            owner=data['owner'],
            maker_address=data['maker_address'],
            matched_amount=float(data['matched_amount']),
            price=float(data['price']),
            fee_rate_bps=int(data['fee_rate_bps']),
            asset_id=data['asset_id'],
            outcome=data['outcome'],
            side=data['side']
        )


@dataclass
class Trade:
    """Represents a single fill/execution from the Polymarket CLOB trades endpoint.
    
    This data class is populated from the py_clob_client.clob.get_trades() REST response
    and represents a matched trade between a taker and one or more makers. The PolyRestAPI.get_trades()
    method returns a TradeData container with a list of Trade objects.
    
    Trade objects are used for:
    - Portfolio P&L calculation (price, size, fees)
    - Trade reconciliation and settlement tracking
    - Counterparty and liquidity source attribution via nested maker_orders
    - Order execution analysis (match_time, status progression)
    
    Note: A single placement of a taker order can result in multiple Trade objects if it was
    filled across different price levels or matched at different times.
    
    Attributes:
        id: Unique identifier for this specific trade execution on the CLOB.
        taker_order_id: ID of the taker's order that triggered this trade.
        market: Market identifier from Polymarket.
        asset_id: Token ID representing the outcome being traded.
        side: Direction from the taker's perspective ('BUY' or 'SELL').
        size: Total number of shares/contracts filled in this trade.
        fee_rate_bps: Polymarket fee rate charged on this trade in basis points.
        price: Weighted average execution price paid per share.
        status: Trade status ('MATCHED', 'SETTLED', etc).
        match_time: Unix timestamp when this trade was executed on the CLOB.
        last_update: Unix timestamp of the most recent update to this trade record.
        outcome: Description of the outcome being traded.
        bucket_index: Internal CLOB index for trade bucketing/organization.
        owner: Address of the account that placed the taker order.
        maker_address: Proxy/settlement address for the taker (usually proxy_funder).
        transaction_hash: Blockchain transaction hash if settled on-chain.
        maker_orders: List of MakerOrder objects showing liquidity sources that filled this trade.
        trader_side: Additional classification of the taker's position/account side.
    """
    id: str
    taker_order_id: str
    market: str
    asset_id: str
    side: str
    size: float
    fee_rate_bps: int
    price: float
    status: str
    match_time: int
    last_update: int
    outcome: str
    bucket_index: int
    owner: str
    maker_address: str
    transaction_hash: str
    maker_orders: List[MakerOrder] = field(default_factory=list)
    trader_side: str = ""

    def __repr__(self) -> str:
        return (f"Trade(id={self.id!r}, taker_order_id={self.taker_order_id!r}, "
                f"market={self.market!r}, asset_id={self.asset_id!r}, side={self.side!r}, "
                f"size={self.size}, fee_rate_bps={self.fee_rate_bps}, price={self.price}, "
                f"status={self.status!r}, match_time={self.match_time}, last_update={self.last_update}, "
                f"outcome={self.outcome!r}, bucket_index={self.bucket_index}, owner={self.owner!r}, "
                f"maker_address={self.maker_address!r}, transaction_hash={self.transaction_hash!r}, "
                f"maker_orders={self.maker_orders!r}, trader_side={self.trader_side!r})")

    @classmethod
    def from_dict(cls, data: dict) -> 'Trade':
        maker_orders = [
            MakerOrder.from_dict(order)
            for order in data.get('maker_orders', [])
        ]

        return cls(
            id=data['id'],
            taker_order_id=data['taker_order_id'],
            market=data['market'],
            asset_id=data['asset_id'],
            side=data['side'],
            size=float(data['size']),
            fee_rate_bps=int(data['fee_rate_bps']),
            price=float(data['price']),
            status=data['status'],
            match_time=int(data['match_time']),
            last_update=int(data['last_update']),
            outcome=data['outcome'],
            bucket_index=int(data['bucket_index']),
            owner=data['owner'],
            maker_address=data['maker_address'],
            transaction_hash=data['transaction_hash'],
            maker_orders=maker_orders,
            trader_side=data.get('trader_side', '')
        )


@dataclass
class TradeData:
    """Container for a list of Trade objects with built-in serialization/deserialization.
    
    This is the top-level return type from PolyRestAPI.get_trades() and wraps the raw
    trades list from py_clob_client.clob.get_trades() with convenience methods for:
    - Loading trades from JSON strings or files
    - Converting back to dict/JSON format for storage or API transmission
    - Indexing and length operations for programmatic trade access
    
    TradeData is typically used when fetching trade history to enable easy serialization
    workflows (e.g., saving to disk, sending via API) while maintaining type safety
    and structured access to individual Trade objects.
    
    Attributes:
        trades: List of Trade objects representing all fetched trades.
    """
    trades: List[Trade] = field(default_factory=list)

    def __repr__(self) -> str:
        return f"TradeData(trades={self.trades!r})"

    @classmethod
    def from_list(cls, data: list) -> 'TradeData':
        trades = [Trade.from_dict(trade_dict) for trade_dict in data]
        return cls(trades=trades)

    @classmethod
    def from_json(cls, json_str: str) -> 'TradeData':
        data = json.loads(json_str)
        return cls.from_list(data)

    @classmethod
    def from_json_file(cls, filepath: str) -> 'TradeData':
        with open(filepath, 'r') as f:
            data = json.load(f)
        return cls.from_list(data)

    def to_dict(self) -> list:
        return [
            {
                'id': trade.id,
                'taker_order_id': trade.taker_order_id,
                'market': trade.market,
                'asset_id': trade.asset_id,
                'side': trade.side,
                'size': str(trade.size),
                'fee_rate_bps': trade.fee_rate_bps,
                'price': str(trade.price),
                'status': trade.status,
                'match_time': str(trade.match_time),
                'last_update': str(trade.last_update),
                'outcome': trade.outcome,
                'bucket_index': trade.bucket_index,
                'owner': trade.owner,
                'maker_address': trade.maker_address,
                'transaction_hash': trade.transaction_hash,
                'maker_orders': [
                    {
                        'order_id': order.order_id,
                        'owner': order.owner,
                        'maker_address': order.maker_address,
                        'matched_amount': str(order.matched_amount),
                        'price': str(order.price),
                        'fee_rate_bps': order.fee_rate_bps,
                        'asset_id': order.asset_id,
                        'outcome': order.outcome,
                        'side': order.side
                    }
                    for order in trade.maker_orders
                ],
                'trader_side': trade.trader_side
            }
            for trade in self.trades
        ]

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def __len__(self) -> int:
        return len(self.trades)

    def __getitem__(self, index: int) -> Trade:
        return self.trades[index]