import difflib
from decimal import Decimal
from datetime import datetime, timezone
from dataclasses import dataclass, field
from argus.perpetuals.shared import account as _acct
from argus.perpetuals.shared import P2OrderBookConvertClass
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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market": self.market.to_dict(),
            "context": self.context.to_dict(),
            "funding_rate": str(self.funding_rate) if self.funding_rate is not None else None,
        }

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

    def search(self, keyword: str, limit: int = 10) -> List[str]:
        """Return the names of the perpetuals most similar to `keyword`, best first.

        Mirrors PolymarketDispatcher's search_markets (a case-insensitive
        difflib.SequenceMatcher ratio over names, sorted descending) so clients
        get the same fuzzy-ticker behaviour across venues. Runs entirely off the
        already-refreshed in-memory index, so there is no network round-trip per
        query.
        """
        if limit <= 0:
            return []
        needle = keyword.lower()
        ranked = sorted(
            self.perpetuals,
            key=lambda p: difflib.SequenceMatcher(None, needle, p.name.lower()).ratio(),
            reverse=True,
        )
        return [p.name for p in ranked[:limit]]


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


# --- account / user state ----------------------------------------------------
#
# Lighter identifies an account by integer `account_index` (an L1 address can
# own several: a master account plus sub-accounts). `GET /api/v1/account` is a
# public read -- balance and positions need no credential -- while the order,
# trade and funding endpoints are auth-gated and take a read-only API token in
# the `authorization` header (see LighterRest). Lighter's JSON is already
# snake_case, so `from_dict` / `to_dict` keep the upstream field names as-is
# (Decimals as strings), and each record that has a venue-agnostic counterpart
# in `argus.perpetuals.shared.account` has a `to_common()` adapter; the
# dispatcher only ever emits the homogenous record, which nests this venue
# record under "venue".
#
# Timestamps: Lighter is not consistent across fields (seconds in some REST
# fields, ms in others, microseconds in `transaction_time`); venue records keep
# the raw integers, `to_common()` normalises through
# `shared.account.normalize_timestamp_ms`.

_dec_or_none = _acct.dec_or_none
_str_or_none = _acct.str_or_none
_decimal_str = _acct.decimal_str


def _id_str(data: Dict[str, Any], *keys: str) -> str:
    """
    The first of `keys` present in `data`, rendered as a string.

    Lighter returns every id twice -- as an integer and as a `*_str` twin -- because
    the integers exceed 2^53 and silently lose precision in JSON consumers. The string
    twin is therefore authoritative and is preferred here, with the integer form only
    as a fallback for payloads that omit it.
    """
    for key in keys:
        value = data.get(key)
        if value is not None:
            return f"{value}"
    return ""


@dataclass
class AccountPosition:
    """One market's position row from `GET /api/v1/account` (`accounts[].positions[]`).

    `sign` is +1 for long, -1 for short (0 when flat) and `position` is the
    unsigned size, so the signed size is `sign * position`. Lighter also lists
    rows for markets the account has traded but is now flat in (with margin
    settings but zero `position`); `LighterRest.get_positions` drops those."""

    market_id: int
    symbol: str
    sign: int
    position: Decimal
    avg_entry_price: Decimal
    position_value: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    liquidation_price: Decimal
    allocated_margin: Decimal
    initial_margin_fraction: Decimal
    margin_mode: int
    open_order_count: int
    pending_order_count: int
    position_tied_order_count: int
    total_funding_paid_out: Optional[Decimal] = None
    total_discount: Optional[str] = None
    margin_set_flag: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AccountPosition":
        return cls(
            market_id=int(data["market_id"]),
            symbol=data["symbol"],
            sign=int(data["sign"]),
            position=Decimal(data["position"]),
            avg_entry_price=Decimal(data["avg_entry_price"]),
            position_value=Decimal(data["position_value"]),
            unrealized_pnl=Decimal(data["unrealized_pnl"]),
            realized_pnl=Decimal(data["realized_pnl"]),
            liquidation_price=Decimal(data["liquidation_price"]),
            allocated_margin=Decimal(data["allocated_margin"]),
            initial_margin_fraction=Decimal(data["initial_margin_fraction"]),
            margin_mode=int(data["margin_mode"]),
            open_order_count=int(data.get("open_order_count", 0)),
            pending_order_count=int(data.get("pending_order_count", 0)),
            position_tied_order_count=int(data.get("position_tied_order_count", 0)),
            total_funding_paid_out=_dec_or_none(data.get("total_funding_paid_out")),
            total_discount=data.get("total_discount"),
            margin_set_flag=data.get("margin_set_flag"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market_id": self.market_id,
            "symbol": self.symbol,
            "sign": self.sign,
            "position": _decimal_str(self.position),
            "avg_entry_price": _decimal_str(self.avg_entry_price),
            "position_value": _decimal_str(self.position_value),
            "unrealized_pnl": _decimal_str(self.unrealized_pnl),
            "realized_pnl": _decimal_str(self.realized_pnl),
            "liquidation_price": _decimal_str(self.liquidation_price),
            "allocated_margin": _decimal_str(self.allocated_margin),
            "initial_margin_fraction": _decimal_str(self.initial_margin_fraction),
            "margin_mode": self.margin_mode,
            "open_order_count": self.open_order_count,
            "pending_order_count": self.pending_order_count,
            "position_tied_order_count": self.position_tied_order_count,
            "total_funding_paid_out": _str_or_none(self.total_funding_paid_out),
            "total_discount": self.total_discount,
            "margin_set_flag": self.margin_set_flag,
        }

    @property
    def signed_size(self) -> Decimal:
        return self.position * self.sign

    @property
    def is_flat(self) -> bool:
        return self.position == 0 or self.sign == 0

    def to_common(self) -> _acct.Position:
        # A liquidation price of 0 means "none" on Lighter (e.g. cross positions well
        # within margin); report None rather than a bogus zero.
        liq = self.liquidation_price if self.liquidation_price != 0 else None
        return _acct.Position(
            name=self.symbol,
            signed_size=self.signed_size,
            notional=self.position_value,
            unrealized_pnl=self.unrealized_pnl,
            entry_price=self.avg_entry_price,
            liquidation_price=liq,
            leverage=None,  # Lighter reports a margin fraction, not a leverage figure; see venue.initial_margin_fraction
            margin_used=self.allocated_margin,
            venue=self,
        )


@dataclass
class AccountSummary:
    """One account from `GET /api/v1/account` (`accounts[0]` when looked up by index).

    Balance semantics: `collateral` is deposited collateral, `available_balance`
    what is free to withdraw / open with, `total_asset_value` the account's total
    equity (collateral + unrealized PnL across positions), `cross_asset_value` the
    part of that in cross margin. `assets` (spot balances) and pool/share data are
    kept verbatim since perps trading does not interpret them."""

    index: int
    l1_address: str
    account_type: int
    status: int
    collateral: Decimal
    available_balance: Decimal
    total_asset_value: Decimal
    cross_asset_value: Decimal
    cross_initial_margin_requirement: Decimal
    cross_maintenance_margin_requirement: Decimal
    total_order_count: int
    pending_order_count: int
    positions: List[AccountPosition] = field(default_factory=list)
    assets: List[Dict[str, Any]] = field(default_factory=list)
    name: Optional[str] = None
    account_trading_mode: Optional[int] = None
    cancel_all_time: Optional[int] = None
    created_at: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AccountSummary":
        return cls(
            index=int(data.get("account_index", data["index"])),
            l1_address=data["l1_address"],
            account_type=int(data["account_type"]),
            status=int(data["status"]),
            collateral=Decimal(data["collateral"]),
            available_balance=Decimal(data["available_balance"]),
            total_asset_value=Decimal(data["total_asset_value"]),
            cross_asset_value=Decimal(data["cross_asset_value"]),
            cross_initial_margin_requirement=Decimal(data.get("cross_initial_margin_requirement", "0")),
            cross_maintenance_margin_requirement=Decimal(data.get("cross_maintenance_margin_requirement", "0")),
            total_order_count=int(data.get("total_order_count", 0)),
            pending_order_count=int(data.get("pending_order_count", 0)),
            positions=[AccountPosition.from_dict(p) for p in data.get("positions", [])],
            assets=list(data.get("assets", [])),
            name=data.get("name"),
            account_trading_mode=data.get("account_trading_mode"),
            cancel_all_time=data.get("cancel_all_time"),
            created_at=data.get("created_at"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "l1_address": self.l1_address,
            "account_type": self.account_type,
            "account_trading_mode": self.account_trading_mode,
            "status": self.status,
            "name": self.name,
            "collateral": _decimal_str(self.collateral),
            "available_balance": _decimal_str(self.available_balance),
            "total_asset_value": _decimal_str(self.total_asset_value),
            "cross_asset_value": _decimal_str(self.cross_asset_value),
            "cross_initial_margin_requirement": _decimal_str(self.cross_initial_margin_requirement),
            "cross_maintenance_margin_requirement": _decimal_str(self.cross_maintenance_margin_requirement),
            "total_order_count": self.total_order_count,
            "pending_order_count": self.pending_order_count,
            "cancel_all_time": self.cancel_all_time,
            "created_at": self.created_at,
            "positions": [p.to_dict() for p in self.positions],
            "assets": list(self.assets),
        }

    @property
    def open_positions(self) -> List[AccountPosition]:
        return [p for p in self.positions if not p.is_flat]

    @property
    def total_margin_used(self) -> Decimal:
        return sum((p.allocated_margin for p in self.open_positions), Decimal(0))

    @property
    def total_position_notional(self) -> Decimal:
        return sum((p.position_value for p in self.open_positions), Decimal(0))

    def to_balance(self) -> _acct.AccountBalance:
        """Positions are nested in the same payload, so `to_balance` is only meaningful
        on a summary fetched *with* positions (the default lookup)."""
        return _acct.AccountBalance(
            account_value=self.total_asset_value,
            available_balance=self.available_balance,
            total_margin_used=self.total_margin_used,
            total_position_notional=self.total_position_notional,
            venue=_AccountSummaryWithoutPositions(self),
        )


class _AccountSummaryWithoutPositions:
    """`to_dict` view of an AccountSummary that omits `positions`, so `get_balance`
    doesn't ship every position (already served, paginated, by `get_positions`)."""

    def __init__(self, summary: AccountSummary):
        self._summary = summary

    def to_dict(self) -> Dict[str, Any]:
        out = self._summary.to_dict()
        out["positions"] = None
        out["open_position_count"] = len(self._summary.open_positions)
        return out


@dataclass
class LighterOrder:
    """One order from `accountActiveOrders` / `accountInactiveOrders` / `accountOrders`.

    Ids: `order_index` / `order_id` identify the order on the venue (the string
    form is authoritative -- ids exceed 2^53); `client_order_index` /
    `client_order_id` are the client-chosen ids. Sizes are in base units as
    decimal strings; the integer `base_size` / `base_price` are the raw
    fixed-point values and are kept for completeness. `status` is Lighter's
    lifecycle string ("open", "filled", "canceled", "canceled-post-only", ...)."""

    order_index: int
    client_order_index: int
    order_id: str
    client_order_id: str
    market_index: int
    owner_account_index: int
    initial_base_amount: Decimal
    price: Decimal
    nonce: int
    remaining_base_amount: Decimal
    is_ask: bool
    base_size: int
    base_price: int
    filled_base_amount: Decimal
    filled_quote_amount: Decimal
    type: str
    time_in_force: str
    reduce_only: bool
    trigger_price: Decimal
    order_expiry: int
    status: str
    trigger_status: str
    timestamp: int
    created_at: int
    updated_at: int
    block_height: int
    order_version: Optional[int] = None
    trigger_time: Optional[int] = None
    parent_order_index: Optional[int] = None
    parent_order_id: Optional[str] = None
    transaction_time: Optional[int] = None
    order_flags: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LighterOrder":
        return cls(
            order_index=int(data["order_index"]),
            client_order_index=int(data.get("client_order_index", 0)),
            order_id=_id_str(data, "order_id"),
            client_order_id=_id_str(data, "client_order_id"),
            market_index=int(data["market_index"]),
            owner_account_index=int(data["owner_account_index"]),
            initial_base_amount=Decimal(data["initial_base_amount"]),
            price=Decimal(data["price"]),
            nonce=int(data.get("nonce", 0)),
            remaining_base_amount=Decimal(data["remaining_base_amount"]),
            is_ask=bool(data["is_ask"]),
            base_size=int(data.get("base_size", 0)),
            base_price=int(data.get("base_price", 0)),
            filled_base_amount=Decimal(data.get("filled_base_amount", "0")),
            filled_quote_amount=Decimal(data.get("filled_quote_amount", "0")),
            type=data["type"],
            time_in_force=data.get("time_in_force", ""),
            reduce_only=bool(data.get("reduce_only", False)),
            trigger_price=Decimal(data.get("trigger_price", "0")),
            order_expiry=int(data.get("order_expiry", 0)),
            status=data["status"],
            trigger_status=data.get("trigger_status", "na"),
            timestamp=int(data["timestamp"]),
            created_at=int(data.get("created_at", data["timestamp"])),
            updated_at=int(data.get("updated_at", data["timestamp"])),
            block_height=int(data.get("block_height", 0)),
            order_version=data.get("order_version"),
            trigger_time=data.get("trigger_time"),
            parent_order_index=data.get("parent_order_index"),
            parent_order_id=data.get("parent_order_id"),
            transaction_time=data.get("transaction_time"),
            order_flags=data.get("order_flags"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_index": self.order_index,
            "client_order_index": self.client_order_index,
            "order_id": self.order_id,
            "client_order_id": self.client_order_id,
            "market_index": self.market_index,
            "owner_account_index": self.owner_account_index,
            "initial_base_amount": _decimal_str(self.initial_base_amount),
            "price": _decimal_str(self.price),
            "nonce": self.nonce,
            "remaining_base_amount": _decimal_str(self.remaining_base_amount),
            "is_ask": self.is_ask,
            "base_size": self.base_size,
            "base_price": self.base_price,
            "filled_base_amount": _decimal_str(self.filled_base_amount),
            "filled_quote_amount": _decimal_str(self.filled_quote_amount),
            "type": self.type,
            "time_in_force": self.time_in_force,
            "reduce_only": self.reduce_only,
            "trigger_price": _decimal_str(self.trigger_price),
            "order_expiry": self.order_expiry,
            "status": self.status,
            "trigger_status": self.trigger_status,
            "trigger_time": self.trigger_time,
            "parent_order_index": self.parent_order_index,
            "parent_order_id": self.parent_order_id,
            "block_height": self.block_height,
            "timestamp": self.timestamp,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "transaction_time": self.transaction_time,
            "order_flags": self.order_flags,
            "order_version": self.order_version,
        }

    @property
    def is_buy(self) -> bool:
        return not self.is_ask

    def to_common(self, symbol: str) -> _acct.Order:
        """`symbol` is resolved from `market_index` by the caller (LighterRest keeps the map)."""
        return _acct.Order(
            order_id=self.order_id,
            client_order_id=self.client_order_id or None,
            name=symbol,
            is_buy=self.is_buy,
            price=self.price,
            original_size=self.initial_base_amount,
            remaining_size=self.remaining_base_amount,
            order_type=self.type,
            status=self.status,
            reduce_only=self.reduce_only,
            timestamp_ms=_acct.normalize_timestamp_ms(self.timestamp),
            venue=self,
        )


@dataclass
class LighterTrade:
    """One trade from `GET /api/v1/trades` filtered to the account.

    A Lighter trade row is symmetric -- it names both the ask and bid order/account
    and the fee each paid -- so which side *we* were on is only known given our
    `account_index` (see `to_common`). `is_maker_ask` says which side was resting.
    Fees are quote-currency amounts as the API returns them (numbers). The
    integrator-fee and allocated-margin bookkeeping fields the API also returns are
    internal to Lighter's margin engine and are intentionally not modelled."""

    trade_id: int
    trade_id_str: str
    tx_hash: str
    type: str
    market_id: int
    size: Decimal
    price: Decimal
    usd_amount: Decimal
    ask_id_str: str
    bid_id_str: str
    ask_client_id_str: str
    bid_client_id_str: str
    ask_account_id: int
    bid_account_id: int
    is_maker_ask: bool
    block_height: int
    timestamp: int
    taker_fee: Decimal
    maker_fee: Decimal
    bid_account_pnl: Optional[Decimal] = None
    ask_account_pnl: Optional[Decimal] = None
    taker_position_size_before: Optional[Decimal] = None
    maker_position_size_before: Optional[Decimal] = None
    taker_position_sign_changed: Optional[bool] = None
    maker_position_sign_changed: Optional[bool] = None
    transaction_time: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LighterTrade":
        return cls(
            trade_id=int(data["trade_id"]),
            trade_id_str=_id_str(data, "trade_id_str", "trade_id"),
            tx_hash=data.get("tx_hash", ""),
            type=data.get("type", "trade"),
            market_id=int(data["market_id"]),
            size=Decimal(data["size"]),
            price=Decimal(data["price"]),
            usd_amount=Decimal(data.get("usd_amount", "0")),
            ask_id_str=_id_str(data, "ask_id_str", "ask_id"),
            bid_id_str=_id_str(data, "bid_id_str", "bid_id"),
            ask_client_id_str=_id_str(data, "ask_client_id_str", "ask_client_id"),
            bid_client_id_str=_id_str(data, "bid_client_id_str", "bid_client_id"),
            ask_account_id=int(data["ask_account_id"]),
            bid_account_id=int(data["bid_account_id"]),
            is_maker_ask=bool(data["is_maker_ask"]),
            block_height=int(data.get("block_height", 0)),
            timestamp=int(data["timestamp"]),
            taker_fee=Decimal(str(data.get("taker_fee", 0))),
            maker_fee=Decimal(str(data.get("maker_fee", 0))),
            bid_account_pnl=_dec_or_none(data.get("bid_account_pnl")),
            ask_account_pnl=_dec_or_none(data.get("ask_account_pnl")),
            taker_position_size_before=_dec_or_none(data.get("taker_position_size_before")),
            maker_position_size_before=_dec_or_none(data.get("maker_position_size_before")),
            taker_position_sign_changed=data.get("taker_position_sign_changed"),
            maker_position_sign_changed=data.get("maker_position_sign_changed"),
            transaction_time=data.get("transaction_time"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "trade_id_str": self.trade_id_str,
            "tx_hash": self.tx_hash,
            "type": self.type,
            "market_id": self.market_id,
            "size": _decimal_str(self.size),
            "price": _decimal_str(self.price),
            "usd_amount": _decimal_str(self.usd_amount),
            "ask_id_str": self.ask_id_str,
            "bid_id_str": self.bid_id_str,
            "ask_client_id_str": self.ask_client_id_str,
            "bid_client_id_str": self.bid_client_id_str,
            "ask_account_id": self.ask_account_id,
            "bid_account_id": self.bid_account_id,
            "is_maker_ask": self.is_maker_ask,
            "block_height": self.block_height,
            "timestamp": self.timestamp,
            "transaction_time": self.transaction_time,
            "taker_fee": _decimal_str(self.taker_fee),
            "maker_fee": _decimal_str(self.maker_fee),
            "bid_account_pnl": _str_or_none(self.bid_account_pnl),
            "ask_account_pnl": _str_or_none(self.ask_account_pnl),
            "taker_position_size_before": _str_or_none(self.taker_position_size_before),
            "maker_position_size_before": _str_or_none(self.maker_position_size_before),
            "taker_position_sign_changed": self.taker_position_sign_changed,
            "maker_position_sign_changed": self.maker_position_sign_changed,
        }

    def side_for(self, account_index: int) -> Optional[bool]:
        """True if `account_index` was the bid (buyer), False if the ask, None if neither."""
        if self.bid_account_id == account_index:
            return True
        if self.ask_account_id == account_index:
            return False
        return None

    def to_common(self, account_index: int, symbol: str) -> _acct.Trade:
        is_buy = self.side_for(account_index)
        if is_buy is None:
            raise ValueError(
                f"LighterTrade {self.trade_id_str}: account {account_index} is neither the bid "
                f"({self.bid_account_id}) nor the ask ({self.ask_account_id})"
            )
        # The maker is whichever side was resting: the ask if is_maker_ask, else the bid.
        is_maker = (not is_buy) if self.is_maker_ask else is_buy
        return _acct.Trade(
            trade_id=self.trade_id_str,
            order_id=self.bid_id_str if is_buy else self.ask_id_str,
            name=symbol,
            is_buy=is_buy,
            price=self.price,
            size=self.size,
            fee=self.maker_fee if is_maker else self.taker_fee,
            is_maker=is_maker,
            realized_pnl=self.bid_account_pnl if is_buy else self.ask_account_pnl,
            timestamp_ms=_acct.normalize_timestamp_ms(self.timestamp),
            venue=self,
        )


@dataclass
class PositionFunding:
    """One funding settlement from `GET /api/v1/positionFunding`.

    `change` is the signed cash flow to the account (negative == paid), `rate` the
    rate applied, `position_size` the unsigned size and `position_side`
    "long" / "short" at settlement."""

    timestamp: int
    market_id: int
    funding_id: int
    change: Decimal
    rate: Decimal
    position_size: Decimal
    position_side: str
    discount: Optional[Decimal] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PositionFunding":
        return cls(
            timestamp=int(data["timestamp"]),
            market_id=int(data["market_id"]),
            funding_id=int(data.get("funding_id", 0)),
            change=Decimal(data["change"]),
            rate=Decimal(data["rate"]),
            position_size=Decimal(data["position_size"]),
            position_side=data["position_side"],
            discount=_dec_or_none(data.get("discount")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "market_id": self.market_id,
            "funding_id": self.funding_id,
            "change": _decimal_str(self.change),
            "discount": _str_or_none(self.discount),
            "rate": _decimal_str(self.rate),
            "position_size": _decimal_str(self.position_size),
            "position_side": self.position_side,
        }

    @property
    def signed_position_size(self) -> Decimal:
        return -self.position_size if self.position_side == "short" else self.position_size

    def to_common(self, symbol: str) -> _acct.FundingPayment:
        return _acct.FundingPayment(
            name=symbol,
            timestamp_ms=_acct.normalize_timestamp_ms(self.timestamp),
            rate=self.rate,
            position_size=self.signed_position_size,
            payment=self.change,
            venue=self,
        )


# --- websocket market-data wire encoding -------------------------------------

class LighterP2ConvertClass(P2OrderBookConvertClass):
    """
    Duck-typed adapter for `argus.protocol.transmit_mkt_data_with_protocol_2`,
    direct port of `argus.perpetuals.hyper._classes.HLP2ConvertClass` -- see
    that class's docstring for the general P2 wire-format contract, which is
    identical here.

    The one structural difference from Hyperliquid: Lighter's wss layer
    (`argus.perpetuals.lighter.wss`) keys order book updates by integer
    `market_id` (the wire-accurate key -- see that module's docstring), not by
    the symbol string used on the wire/by clients. `symbol` and `market_id`
    are therefore passed in separately: `market_id` to look the book up out of
    `market_data`, `symbol` for the P2 packet's wire identity. The dispatcher
    resolves symbol<->market_id once, at the boundary, before constructing this.

    Subclass of `argus.perpetuals.shared._classes.P2OrderBookConvertClass`,
    which enforces the expected market_data shape (same as `HLP2ConvertClass`,
    keyed by market_id):

    {
        1: {
            "bids": [{"price": "97500", "size": "1.5"}, ...],
            "asks": [{"price": "97501", "size": "2.0"}, ...],
        },
        "timestamp": 1770251679393,
    }
    """

    def __init__(self, symbol: str, market_id: int, market_data: Dict[Any, Any], order_book_depth: int):
        self.market_id = market_id
        super().__init__(
            symbol=symbol,
            lookup_key=market_id,
            market_data=market_data,
            order_book_depth=order_book_depth,
        )
