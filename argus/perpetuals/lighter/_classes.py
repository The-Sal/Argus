from decimal import Decimal
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional



# --- market metadata (from orderBookDetails) ---------------------------------
#
# `orderBookDetails` returns one flat JSON object per market that mixes static
# metadata (fees, decimals, margin tiers) with live market data (mark price,
# funding params, 24h stats). Market and MarketContext both parse that same
# dict, picking out their own fields, so the two can be composed independently
# -- mirroring how argus/hyper/_classes.py splits Asset from AssetContext.

@dataclass
class MarketConfig:
    """The nested `market_config` object of an orderBookDetails entry."""

    market_margin_mode: int
    insurance_fund_account_index: int
    liquidation_mode: int
    force_reduce_only: bool
    trading_hours: str
    funding_fee_discounts_enabled: bool
    hidden: bool
    rfq_enabled: bool

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MarketConfig":
        return cls(
            market_margin_mode=data["market_margin_mode"],
            insurance_fund_account_index=data["insurance_fund_account_index"],
            liquidation_mode=data["liquidation_mode"],
            force_reduce_only=data["force_reduce_only"],
            trading_hours=data["trading_hours"],
            funding_fee_discounts_enabled=data["funding_fee_discounts_enabled"],
            hidden=data["hidden"],
            rfq_enabled=data["rfq_enabled"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market_margin_mode": self.market_margin_mode,
            "insurance_fund_account_index": self.insurance_fund_account_index,
            "liquidation_mode": self.liquidation_mode,
            "force_reduce_only": self.force_reduce_only,
            "trading_hours": self.trading_hours,
            "funding_fee_discounts_enabled": self.funding_fee_discounts_enabled,
            "hidden": self.hidden,
            "rfq_enabled": self.rfq_enabled,
        }


@dataclass
class Market:
    """Static-ish metadata for one Lighter market: fees, size limits, margin tiers."""

    symbol: str
    market_id: int
    market_type: str  # "perp" | "spot"
    base_asset_id: int
    quote_asset_id: int
    status: str  # "active" | "inactive"
    taker_fee: Decimal
    is_taker_fee_enabled: bool
    maker_fee: Decimal
    is_maker_fee_enabled: bool
    liquidation_fee: Decimal
    min_base_amount: Decimal
    min_quote_amount: Decimal
    order_quote_limit: Decimal
    supported_size_decimals: int
    supported_price_decimals: int
    supported_quote_decimals: int
    created_at_ms: int
    multiplier: Decimal
    size_decimals: int
    price_decimals: int
    quote_multiplier: int
    default_initial_margin_fraction: int
    min_initial_margin_fraction: int
    maintenance_margin_fraction: int
    closeout_margin_fraction: int
    market_config: MarketConfig
    strategy_index: int
    market_flags: int
    funding_premium_multiplier: int
    funding_clamp_small: Decimal
    funding_clamp_big: Decimal
    base_interest_rate: Decimal

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Market":
        return cls(
            symbol=data["symbol"],
            market_id=data["market_id"],
            market_type=data["market_type"],
            base_asset_id=data["base_asset_id"],
            quote_asset_id=data["quote_asset_id"],
            status=data["status"],
            taker_fee=Decimal(data["taker_fee"]),
            is_taker_fee_enabled=data["is_taker_fee_enabled"],
            maker_fee=Decimal(data["maker_fee"]),
            is_maker_fee_enabled=data["is_maker_fee_enabled"],
            liquidation_fee=Decimal(data["liquidation_fee"]),
            min_base_amount=Decimal(data["min_base_amount"]),
            min_quote_amount=Decimal(data["min_quote_amount"]),
            order_quote_limit=Decimal(data["order_quote_limit"]),
            supported_size_decimals=data["supported_size_decimals"],
            supported_price_decimals=data["supported_price_decimals"],
            supported_quote_decimals=data["supported_quote_decimals"],
            created_at_ms=int(data["created_at"]),
            multiplier=Decimal(data["multiplier"]),
            size_decimals=data["size_decimals"],
            price_decimals=data["price_decimals"],
            quote_multiplier=data["quote_multiplier"],
            default_initial_margin_fraction=data["default_initial_margin_fraction"],
            min_initial_margin_fraction=data["min_initial_margin_fraction"],
            maintenance_margin_fraction=data["maintenance_margin_fraction"],
            closeout_margin_fraction=data["closeout_margin_fraction"],
            market_config=MarketConfig.from_dict(data["market_config"]),
            strategy_index=data["strategy_index"],
            market_flags=data["market_flags"],
            funding_premium_multiplier=data["funding_premium_multiplier"],
            funding_clamp_small=Decimal(data["funding_clamp_small"]),
            funding_clamp_big=Decimal(data["funding_clamp_big"]),
            base_interest_rate=Decimal(data["base_interest_rate"]),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "market_id": self.market_id,
            "market_type": self.market_type,
            "base_asset_id": self.base_asset_id,
            "quote_asset_id": self.quote_asset_id,
            "status": self.status,
            "taker_fee": str(self.taker_fee),
            "is_taker_fee_enabled": self.is_taker_fee_enabled,
            "maker_fee": str(self.maker_fee),
            "is_maker_fee_enabled": self.is_maker_fee_enabled,
            "liquidation_fee": str(self.liquidation_fee),
            "min_base_amount": str(self.min_base_amount),
            "min_quote_amount": str(self.min_quote_amount),
            "order_quote_limit": str(self.order_quote_limit),
            "supported_size_decimals": self.supported_size_decimals,
            "supported_price_decimals": self.supported_price_decimals,
            "supported_quote_decimals": self.supported_quote_decimals,
            "created_at": str(self.created_at_ms),
            "multiplier": str(self.multiplier),
            "size_decimals": self.size_decimals,
            "price_decimals": self.price_decimals,
            "quote_multiplier": self.quote_multiplier,
            "default_initial_margin_fraction": self.default_initial_margin_fraction,
            "min_initial_margin_fraction": self.min_initial_margin_fraction,
            "maintenance_margin_fraction": self.maintenance_margin_fraction,
            "closeout_margin_fraction": self.closeout_margin_fraction,
            "market_config": self.market_config.to_dict(),
            "strategy_index": self.strategy_index,
            "market_flags": self.market_flags,
            "funding_premium_multiplier": self.funding_premium_multiplier,
            "funding_clamp_small": str(self.funding_clamp_small),
            "funding_clamp_big": str(self.funding_clamp_big),
            "base_interest_rate": str(self.base_interest_rate),
        }

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def is_perp(self) -> bool:
        return self.market_type == "perp"

    @property
    def created_at(self) -> datetime:
        return datetime.fromtimestamp(self.created_at_ms / 1000, tz=timezone.utc)


@dataclass
class MarketContext:
    """Live market data for one market, as returned alongside Market by orderBookDetails."""

    mark_price: Decimal
    index_price: Decimal
    last_trade_price: Decimal
    daily_trades_count: int
    daily_base_token_volume: Decimal
    daily_quote_token_volume: Decimal
    daily_price_low: Decimal
    daily_price_high: Decimal
    daily_price_change: Decimal  # percentage, e.g. -4.97 == -4.97%, not a fraction
    open_interest: Decimal
    daily_chart: Dict[str, Any] = field(default_factory=dict)  # shape undocumented; always {} observed live

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MarketContext":
        return cls(
            mark_price=Decimal(data["mark_price"]),
            index_price=Decimal(data["index_price"]),
            last_trade_price=Decimal(str(data["last_trade_price"])),
            daily_trades_count=data["daily_trades_count"],
            daily_base_token_volume=Decimal(str(data["daily_base_token_volume"])),
            daily_quote_token_volume=Decimal(str(data["daily_quote_token_volume"])),
            daily_price_low=Decimal(str(data["daily_price_low"])),
            daily_price_high=Decimal(str(data["daily_price_high"])),
            daily_price_change=Decimal(str(data["daily_price_change"])),
            open_interest=Decimal(str(data["open_interest"])),
            daily_chart=data.get("daily_chart", {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mark_price": str(self.mark_price),
            "index_price": str(self.index_price),
            "last_trade_price": str(self.last_trade_price),
            "daily_trades_count": self.daily_trades_count,
            "daily_base_token_volume": str(self.daily_base_token_volume),
            "daily_quote_token_volume": str(self.daily_quote_token_volume),
            "daily_price_low": str(self.daily_price_low),
            "daily_price_high": str(self.daily_price_high),
            "daily_price_change": str(self.daily_price_change),
            "open_interest": str(self.open_interest),
            "daily_chart": self.daily_chart,
        }


@dataclass
class Perpetual:
    """A single tradeable Lighter market: its metadata, live data, and (if attached) funding rate."""

    market: Market
    context: MarketContext
    funding_rate: Optional[Decimal] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Perpetual":
        return cls(market=Market.from_dict(data), context=MarketContext.from_dict(data))

    @property
    def name(self) -> str:
        return self.market.symbol

    @property
    def market_id(self) -> int:
        return self.market.market_id

    @property
    def mark_price(self) -> Decimal:
        return self.context.mark_price

    @property
    def index_price(self) -> Decimal:
        return self.context.index_price

    @property
    def open_interest(self) -> Decimal:
        return self.context.open_interest

    @property
    def open_interest_usd(self) -> Decimal:
        return self.context.open_interest * self.context.mark_price

    @property
    def is_active(self) -> bool:
        return self.market.is_active

    def funding_rate_apr(self, fundings_per_year: int = 24 * 365) -> Optional[Decimal]:
        """Naive annualized funding rate, assuming the current rate holds constant.
        Default assumes hourly settlement (per Lighter's funding docs, not an API
        field) with no compounding."""
        if self.funding_rate is None:
            return None
        return self.funding_rate * fundings_per_year


@dataclass
class PerpetualsIndex:
    """A flat, sortable/filterable collection of Lighter perpetuals."""

    perpetuals: List[Perpetual] = field(default_factory=list)

    def __iter__(self) -> Iterator[Perpetual]:
        return iter(self.perpetuals)

    def __len__(self) -> int:
        return len(self.perpetuals)

    def sorted_by(self, key: Callable[[Perpetual], Any], descending: bool = False) -> List[Perpetual]:
        return sorted(self.perpetuals, key=key, reverse=descending)

    def sorted_by_funding_rate(self, descending: bool = True) -> List[Perpetual]:
        return self.sorted_by(lambda p: (p.funding_rate is not None, p.funding_rate), descending=descending)

    def sorted_by_open_interest(self, descending: bool = True) -> List[Perpetual]:
        return self.sorted_by(lambda p: p.open_interest_usd, descending=descending)

    def sorted_by_volume(self, descending: bool = True) -> List[Perpetual]:
        return self.sorted_by(lambda p: p.context.daily_quote_token_volume, descending=descending)

    def highest_funding(self, n: int = 10) -> List[Perpetual]:
        return [p for p in self.sorted_by_funding_rate(descending=True) if p.funding_rate is not None][:n]

    def lowest_funding(self, n: int = 10) -> List[Perpetual]:
        return [p for p in self.sorted_by_funding_rate(descending=False) if p.funding_rate is not None][:n]

    def filter(self, predicate: Callable[[Perpetual], bool]) -> "PerpetualsIndex":
        return PerpetualsIndex([p for p in self.perpetuals if predicate(p)])

    def excluding_inactive(self) -> "PerpetualsIndex":
        return self.filter(lambda p: p.is_active)

    def get(self, symbol: str) -> Optional[Perpetual]:
        return next((p for p in self.perpetuals if p.name == symbol), None)


# --- funding rates -------------------------------------------------------------

@dataclass(frozen=True)
class FundingRateEntry:
    """One entry of `/funding-rates`: a market's current hourly funding rate on a
    single exchange (Lighter's own rate, or one of the external CEXs it benchmarks
    against). `rate` is a fraction (e.g. Decimal("0.0001") == 0.01%); confirmed live
    against binance/bybit/hyperliquid entries for the same market, which are the
    same order of magnitude."""

    market_id: int
    exchange: str  # "binance" | "bybit" | "hyperliquid" | "lighter"
    symbol: str
    rate: Decimal

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FundingRateEntry":
        return cls(
            market_id=data["market_id"],
            exchange=data["exchange"],
            symbol=data["symbol"],
            rate=Decimal(str(data["rate"])),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market_id": self.market_id,
            "exchange": self.exchange,
            "symbol": self.symbol,
            "rate": str(self.rate),
        }


@dataclass
class CrossExchangeFunding:
    """All known funding rates for one market, across Lighter and external CEXs."""

    market_id: int
    symbol: str
    rates: List[FundingRateEntry] = field(default_factory=list)

    def rate_for(self, exchange: str) -> Optional[Decimal]:
        return next((e.rate for e in self.rates if e.exchange == exchange), None)

    @property
    def lighter_rate(self) -> Optional[Decimal]:
        return self.rate_for("lighter")


@dataclass
class FundingHistoryEntry:
    """One entry of `/fundings` for a single market (market_id is supplied by the
    caller/client, since it isn't echoed back in each entry of that response).

    `rate` here appears to be Lighter's own realized/settled rate for that hour
    (it does move between periods, unlike a static config value), as distinct
    from FundingRateEntry.rate which is a live/current snapshot used for
    cross-exchange comparison. The two were NOT observed to match in magnitude
    for the same market at the same time in testing (this isn't documented
    publicly) -- treat them as two separate quantities rather than assuming one
    derives from the other."""

    market_id: int
    timestamp: int  # seconds
    value: Decimal
    rate: Decimal
    direction: str  # "long" | "short"

    @classmethod
    def from_dict(cls, market_id: int, data: Dict[str, Any]) -> "FundingHistoryEntry":
        return cls(
            market_id=market_id,
            timestamp=data["timestamp"],
            value=Decimal(data["value"]),
            rate=Decimal(data["rate"]),
            direction=data["direction"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "value": str(self.value),
            "rate": str(self.rate),
            "direction": self.direction,
        }

    @property
    def time(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc)
