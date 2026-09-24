import json
import difflib
from enum import Enum
from decimal import Decimal
from datetime import datetime, timezone
from dataclasses import dataclass, field
from argus.perpetuals.hyper import _errors as _ers
from argus.perpetuals.shared import account as _acct
from argus.perpetuals.shared import P2OrderBookConvertClass
from typing import Any, Callable, Dict, Iterable, Iterator, List, Literal, Optional, Sequence, Tuple


# --- simple type aliases for readability -----------------------------------

Address = str   # 0x-prefixed hex address, kept as str rather than validated/checksummed
AssetId = str   # e.g. "xyz:AAPL", "xyz:GOLD"


class SubDeployerAction(str, Enum):
    """The set of privileged actions a sub-deployer address can be granted."""

    REGISTER_ASSET = "registerAsset"
    SET_ORACLE = "setOracle"
    SET_FEE_RECIPIENT = "setFeeRecipient"
    HALT_TRADING = "haltTrading"
    SET_MARGIN_TABLE_IDS = "setMarginTableIds"
    INSERT_MARGIN_TABLE = "insertMarginTable"
    SET_OPEN_INTEREST_CAPS = "setOpenInterestCaps"
    SET_FUNDING_MULTIPLIERS = "setFundingMultipliers"
    SET_MARGIN_MODES = "setMarginModes"
    SET_DEPLOYER_FEES = "setDeployerFees"
    SET_FUNDING_INTEREST_RATES = "setFundingInterestRates"
    SET_PERP_ANNOTATION = "setPerpAnnotation"


# --- pair-shaped entries (JSON encodes these as 2-element lists) -----------

@dataclass(frozen=True)
class AssetStreamingOiCap:
    """One entry of assetToStreamingOiCap: an asset and its streaming OI cap."""

    asset: AssetId
    cap: Decimal

    @classmethod
    def from_pair(cls, pair: Sequence[Any]) -> "AssetStreamingOiCap":
        asset, cap = pair
        return cls(asset=asset, cap=Decimal(cap))

    def to_pair(self) -> List[Any]:
        return [self.asset, str(self.cap)]


@dataclass(frozen=True)
class AssetFundingMultiplier:
    """One entry of assetToFundingMultiplier: an asset and its funding multiplier."""

    asset: AssetId
    multiplier: Decimal

    @classmethod
    def from_pair(cls, pair: Sequence[Any]) -> "AssetFundingMultiplier":
        asset, multiplier = pair
        return cls(asset=asset, multiplier=Decimal(multiplier))

    def to_pair(self) -> List[Any]:
        return [self.asset, str(self.multiplier)]


@dataclass(frozen=True)
class AssetFundingInterestRate:
    """One entry of assetToFundingInterestRate: an asset and its funding interest rate."""

    asset: AssetId
    rate: Decimal

    @classmethod
    def from_pair(cls, pair: Sequence[Any]) -> "AssetFundingInterestRate":
        asset, rate = pair
        return cls(asset=asset, rate=Decimal(rate))

    def to_pair(self) -> List[Any]:
        return [self.asset, str(self.rate)]


@dataclass(frozen=True)
class SubDeployerPermission:
    """One entry of subDeployers: an action and the addresses allowed to perform it."""

    action: SubDeployerAction
    addresses: List[Address]

    @classmethod
    def from_pair(cls, pair: Sequence[Any]) -> "SubDeployerPermission":
        action_str, addresses = pair
        return cls(action=SubDeployerAction(action_str), addresses=list(addresses))

    def to_pair(self) -> List[Any]:
        return [self.action.value, list(self.addresses)]


# --- top-level config --------------------------------------------------------

@dataclass
class PerpDexConfig:
    """Top-level deployer configuration for a perp DEX."""

    name: str
    full_name: str
    deployer: Address
    fee_recipient: Address
    asset_to_streaming_oi_cap: List[AssetStreamingOiCap]
    sub_deployers: List[SubDeployerPermission]
    asset_to_funding_multiplier: List[AssetFundingMultiplier]
    asset_to_funding_interest_rate: List[AssetFundingInterestRate]
    oracle_updater: Optional[Address] = None

    # -- (de)serialization, preserving the original JSON key names/shape ----

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PerpDexConfig":
        return cls(
            name=data["name"],
            full_name=data["fullName"],
            deployer=data["deployer"],
            oracle_updater=data.get("oracleUpdater"),
            fee_recipient=data["feeRecipient"],
            asset_to_streaming_oi_cap=[
                AssetStreamingOiCap.from_pair(p) for p in data["assetToStreamingOiCap"]
            ],
            sub_deployers=[
                SubDeployerPermission.from_pair(p) for p in data["subDeployers"]
            ],
            asset_to_funding_multiplier=[
                AssetFundingMultiplier.from_pair(p) for p in data["assetToFundingMultiplier"]
            ],
            asset_to_funding_interest_rate=[
                AssetFundingInterestRate.from_pair(p) for p in data["assetToFundingInterestRate"]
            ],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "fullName": self.full_name,
            "deployer": self.deployer,
            "oracleUpdater": self.oracle_updater,
            "feeRecipient": self.fee_recipient,
            "assetToStreamingOiCap": [a.to_pair() for a in self.asset_to_streaming_oi_cap],
            "subDeployers": [s.to_pair() for s in self.sub_deployers],
            "assetToFundingMultiplier": [a.to_pair() for a in self.asset_to_funding_multiplier],
            "assetToFundingInterestRate": [a.to_pair() for a in self.asset_to_funding_interest_rate],
        }

    # -- convenience lookups --------------------------------------------------

    def streaming_oi_cap_for(self, asset: AssetId) -> Optional[Decimal]:
        for entry in self.asset_to_streaming_oi_cap:
            if entry.asset == asset:
                return entry.cap
        return None

    def funding_multiplier_for(self, asset: AssetId) -> Optional[Decimal]:
        for entry in self.asset_to_funding_multiplier:
            if entry.asset == asset:
                return entry.multiplier
        return None

    def funding_interest_rate_for(self, asset: AssetId) -> Optional[Decimal]:
        for entry in self.asset_to_funding_interest_rate:
            if entry.asset == asset:
                return entry.rate
        return None

    def addresses_for_action(self, action: SubDeployerAction) -> List[Address]:
        for perm in self.sub_deployers:
            if perm.action == action:
                return perm.addresses
        return []

    def actions_for_address(self, address: Address) -> List[SubDeployerAction]:
        return [perm.action for perm in self.sub_deployers if address in perm.addresses]

    @property
    def assets(self) -> List[AssetId]:
        return [entry.asset for entry in self.asset_to_streaming_oi_cap]


# --- "universe" config -------------------------------------------------------
#
# Typed data model for a perp exchange "universe" config, e.g.:
#
# {
#     "universe": [ {...asset...}, ... ],
#     "marginTables": [ [tableId, {...table...}], ... ]
# }

# "strictIsolated": margin cannot be removed from the position.
# "noCross":        only isolated margin is allowed (cross margin disabled).
MarginMode = Literal["strictIsolated", "noCross"]


@dataclass
class Asset:
    name: str
    szDecimals: int
    maxLeverage: int

    # Only present on delisted / isolated-only assets.
    onlyIsolated: Optional[bool] = None
    isDelisted: Optional[bool] = None

    # Newer replacement for `onlyIsolated`. If set, it implies onlyIsolated
    # semantics ("strictIsolated" or "noCross").
    marginMode: Optional[MarginMode] = None

    # Only present on HIP-3 (builder-deployed) dex assets.
    marginTableId: Optional[int] = None
    growthMode: Optional[str] = None
    lastGrowthModeChangeTime: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Asset":
        return cls(
            name=data["name"],
            szDecimals=data["szDecimals"],
            maxLeverage=data["maxLeverage"],
            onlyIsolated=data.get("onlyIsolated"),
            isDelisted=data.get("isDelisted"),
            marginMode=data.get("marginMode"),
            marginTableId=data.get("marginTableId"),
            growthMode=data.get("growthMode"),
            lastGrowthModeChangeTime=data.get("lastGrowthModeChangeTime"),
        )

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "name": self.name,
            "szDecimals": self.szDecimals,
            "maxLeverage": self.maxLeverage,
        }
        if self.onlyIsolated is not None:
            out["onlyIsolated"] = self.onlyIsolated
        if self.isDelisted is not None:
            out["isDelisted"] = self.isDelisted
        if self.marginMode is not None:
            out["marginMode"] = self.marginMode
        if self.marginTableId is not None:
            out["marginTableId"] = self.marginTableId
        if self.growthMode is not None:
            out["growthMode"] = self.growthMode
        if self.lastGrowthModeChangeTime is not None:
            out["lastGrowthModeChangeTime"] = self.lastGrowthModeChangeTime
        return out

    @property
    def is_hip3(self) -> bool:
        """True for builder-deployed (HIP-3) dex assets, whose names are namespaced as 'dex:COIN'."""
        return ":" in self.name

    @property
    def is_isolated_only(self) -> bool:
        """True if either the deprecated flag or the new marginMode enforces isolated-only."""
        return bool(self.onlyIsolated) or self.marginMode in ("strictIsolated", "noCross")


@dataclass
class MarginTier:
    lowerBound: str  # kept as str to preserve exact decimal formatting from the API
    maxLeverage: int

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MarginTier":
        return cls(lowerBound=data["lowerBound"], maxLeverage=data["maxLeverage"])

    def to_dict(self) -> Dict[str, Any]:
        return {"lowerBound": self.lowerBound, "maxLeverage": self.maxLeverage}

    @property
    def lower_bound_float(self) -> float:
        return float(self.lowerBound)


@dataclass
class MarginTable:
    description: str = ""
    marginTiers: List[MarginTier] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MarginTable":
        return cls(
            description=data.get("description", ""),
            marginTiers=[MarginTier.from_dict(t) for t in data.get("marginTiers", [])],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "description": self.description,
            "marginTiers": [t.to_dict() for t in self.marginTiers],
        }

    def max_leverage_for_notional(self, notional: float) -> Optional[int]:
        """Return the tier's maxLeverage applicable at a given position notional."""
        applicable: Optional[MarginTier] = None
        for tier in sorted(self.marginTiers, key=lambda t: t.lower_bound_float):
            if notional >= tier.lower_bound_float:
                applicable = tier
            else:
                break
        return applicable.maxLeverage if applicable else None


@dataclass
class MarginTableEntry:
    """Represents one [id, MarginTable] pair from the `marginTables` array."""

    id: int
    table: MarginTable

    @classmethod
    def from_pair(cls, pair: Tuple[int, Dict[str, Any]]) -> "MarginTableEntry":
        table_id, table_data = pair
        return cls(id=table_id, table=MarginTable.from_dict(table_data))

    def to_pair(self) -> List[Any]:
        # A list, not a tuple, so JSON round-trips as [id, {...}] rather than (id, {...}).
        return [self.id, self.table.to_dict()]


@dataclass
class UniverseConfig:
    universe: List[Asset] = field(default_factory=list)
    marginTables: List[MarginTableEntry] = field(default_factory=list)

    # Only present when this config is the first element of a `metaAndAssetCtxs`
    # / `allPerpMetas` response, not on a plain `meta` response.
    collateralToken: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UniverseConfig":
        return cls(
            universe=[Asset.from_dict(a) for a in data.get("universe", [])],
            marginTables=[
                MarginTableEntry.from_pair((tid, table)) for tid, table in data.get("marginTables", [])
            ],
            collateralToken=data.get("collateralToken"),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "UniverseConfig":
        return cls.from_dict(json.loads(json_str))

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "universe": [a.to_dict() for a in self.universe],
            "marginTables": [e.to_pair() for e in self.marginTables],
        }
        if self.collateralToken is not None:
            out["collateralToken"] = self.collateralToken
        return out

    def to_json(self, **kwargs: Any) -> str:
        return json.dumps(self.to_dict(), **kwargs)

    def get_asset(self, name: str) -> Optional[Asset]:
        return next((a for a in self.universe if a.name == name), None)

    def get_margin_table(self, table_id: int) -> Optional[MarginTable]:
        return next((e.table for e in self.marginTables if e.id == table_id), None)


# --- asset contexts (mark price, funding, open interest, ...) ----------------
#
# Returned alongside a UniverseConfig by `metaAndAssetCtxs` / `allPerpMetas`,
# as a list positionally aligned with `UniverseConfig.universe` (same index
# in both lists refers to the same asset).

@dataclass
class AssetContext:
    """Live market data for one perp asset, as returned by `metaAndAssetCtxs`."""

    day_ntl_vlm: Decimal
    funding: Decimal
    mark_px: Decimal
    open_interest: Decimal
    oracle_px: Decimal
    prev_day_px: Decimal
    impact_pxs: Optional[Tuple[Decimal, Decimal]] = None
    mid_px: Optional[Decimal] = None
    premium: Optional[Decimal] = None
    # Only present on some HIP-3 dex assets.
    day_base_vlm: Optional[Decimal] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AssetContext":
        impact_pxs = data.get("impactPxs")
        return cls(
            day_ntl_vlm=Decimal(data["dayNtlVlm"]),
            funding=Decimal(data["funding"]),
            mark_px=Decimal(data["markPx"]),
            open_interest=Decimal(data["openInterest"]),
            oracle_px=Decimal(data["oraclePx"]),
            prev_day_px=Decimal(data["prevDayPx"]),
            impact_pxs=tuple(Decimal(p) for p in impact_pxs) if impact_pxs else None,
            mid_px=Decimal(data["midPx"]) if data.get("midPx") is not None else None,
            premium=Decimal(data["premium"]) if data.get("premium") is not None else None,
            day_base_vlm=Decimal(data["dayBaseVlm"]) if data.get("dayBaseVlm") is not None else None,
        )

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "dayNtlVlm": str(self.day_ntl_vlm),
            "funding": str(self.funding),
            "impactPxs": [str(p) for p in self.impact_pxs] if self.impact_pxs else None,
            "markPx": str(self.mark_px),
            "midPx": str(self.mid_px) if self.mid_px is not None else None,
            "openInterest": str(self.open_interest),
            "oraclePx": str(self.oracle_px),
            "premium": str(self.premium) if self.premium is not None else None,
            "prevDayPx": str(self.prev_day_px),
        }
        if self.day_base_vlm is not None:
            out["dayBaseVlm"] = str(self.day_base_vlm)
        return out

    @property
    def price_change_24h(self) -> Decimal:
        """Fractional change of mark price vs. 24h-ago price, e.g. 0.05 == +5%."""
        if self.prev_day_px == 0:
            return Decimal(0)
        return (self.mark_px - self.prev_day_px) / self.prev_day_px


@dataclass
class Perpetual:
    """A single tradeable perpetual: its static metadata plus its live market data."""

    dex: str
    asset: Asset
    context: AssetContext

    @property
    def name(self) -> str:
        return self.asset.name

    @property
    def funding_rate(self) -> Decimal:
        """The current (hourly) funding rate, e.g. Decimal("0.0000125")."""
        return self.context.funding

    def funding_rate_apr(self, fundings_per_year: int = 24 * 365) -> Decimal:
        """Naive annualized funding rate, assuming the current rate holds constant."""
        return self.context.funding * fundings_per_year

    @property
    def mark_price(self) -> Decimal:
        return self.context.mark_px

    @property
    def open_interest(self) -> Decimal:
        return self.context.open_interest

    @property
    def open_interest_usd(self) -> Decimal:
        return self.context.open_interest * self.context.mark_px

    @property
    def is_delisted(self) -> bool:
        return bool(self.asset.isDelisted)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dex": self.dex,
            "asset": self.asset.to_dict(),
            "context": self.context.to_dict(),
        }


@dataclass
class PerpDexSnapshot:
    """One dex's full perpetuals universe plus market data, from `metaAndAssetCtxs`."""

    dex: str
    universe_config: UniverseConfig
    perpetuals: List[Perpetual] = field(default_factory=list)

    @classmethod
    def from_response(cls, dex: str, data: Sequence[Any]) -> "PerpDexSnapshot":
        meta_data, asset_ctxs_data = data
        universe_config = UniverseConfig.from_dict(meta_data)
        contexts = [AssetContext.from_dict(c) for c in asset_ctxs_data]
        perpetuals = [
            Perpetual(dex=dex, asset=asset, context=ctx)
            for asset, ctx in zip(universe_config.universe, contexts)
        ]
        return cls(dex=dex, universe_config=universe_config, perpetuals=perpetuals)


@dataclass
class PerpetualsIndex:
    """A flat, sortable/filterable collection of perpetuals, possibly spanning multiple dexes."""

    perpetuals: List[Perpetual] = field(default_factory=list)

    @classmethod
    def from_snapshots(cls, snapshots: Iterable[PerpDexSnapshot]) -> "PerpetualsIndex":
        return cls([p for snapshot in snapshots for p in snapshot.perpetuals])

    def __iter__(self) -> Iterator[Perpetual]:
        return iter(self.perpetuals)

    def __len__(self) -> int:
        return len(self.perpetuals)

    def sorted_by(self, key: Callable[[Perpetual], Any], descending: bool = False) -> List[Perpetual]:
        return sorted(self.perpetuals, key=key, reverse=descending)

    def sorted_by_funding_rate(self, descending: bool = True) -> List[Perpetual]:
        return self.sorted_by(lambda p: p.funding_rate, descending=descending)

    def sorted_by_open_interest(self, descending: bool = True) -> List[Perpetual]:
        return self.sorted_by(lambda p: p.open_interest_usd, descending=descending)

    def sorted_by_volume(self, descending: bool = True) -> List[Perpetual]:
        return self.sorted_by(lambda p: p.context.day_ntl_vlm, descending=descending)

    def highest_funding(self, n: int = 10) -> List[Perpetual]:
        return self.sorted_by_funding_rate(descending=True)[:n]

    def lowest_funding(self, n: int = 10) -> List[Perpetual]:
        return self.sorted_by_funding_rate(descending=False)[:n]

    def filter(self, predicate: Callable[[Perpetual], bool]) -> "PerpetualsIndex":
        return PerpetualsIndex([p for p in self.perpetuals if predicate(p)])

    def excluding_delisted(self) -> "PerpetualsIndex":
        return self.filter(lambda p: not p.is_delisted)

    def for_dex(self, dex: str) -> "PerpetualsIndex":
        return self.filter(lambda p: p.dex == dex)

    def get(self, name: str, dex: str = "") -> Optional[Perpetual]:
        return next((p for p in self.perpetuals if p.name == name and p.dex == dex), None)

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


# --- funding rate history / predictions ---------------------------------------

@dataclass
class FundingHistoryEntry:
    coin: str
    funding_rate: Decimal
    premium: Decimal
    time_ms: int

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FundingHistoryEntry":
        return cls(
            coin=data["coin"],
            funding_rate=Decimal(data["fundingRate"]),
            premium=Decimal(data["premium"]),
            time_ms=data["time"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "coin": self.coin,
            "fundingRate": str(self.funding_rate),
            "premium": str(self.premium),
            "time": self.time_ms,
        }

    @property
    def time(self) -> datetime:
        return datetime.fromtimestamp(self.time_ms / 1000, tz=timezone.utc)


@dataclass
class PredictedFundingVenue:
    """A single venue's predicted next funding rate for one coin."""

    venue: str
    funding_rate: Optional[Decimal] = None
    next_funding_time_ms: Optional[int] = None

    @classmethod
    def from_pair(cls, pair: Sequence[Any]) -> "PredictedFundingVenue":
        venue, data = pair
        if data is None:
            return cls(venue=venue)
        return cls(
            venue=venue,
            funding_rate=Decimal(data["fundingRate"]) if data.get("fundingRate") is not None else None,
            next_funding_time_ms=data.get("nextFundingTime"),
        )

    def to_pair(self) -> List[Any]:
        if self.funding_rate is None and self.next_funding_time_ms is None:
            return [self.venue, None]
        return [
            self.venue,
            {
                "fundingRate": str(self.funding_rate) if self.funding_rate is not None else None,
                "nextFundingTime": self.next_funding_time_ms,
            },
        ]


@dataclass
class PredictedFunding:
    """Predicted funding rates across venues (Hyperliquid + external CEXs) for one coin."""

    coin: str
    venues: List[PredictedFundingVenue] = field(default_factory=list)

    @classmethod
    def from_pair(cls, pair: Sequence[Any]) -> "PredictedFunding":
        coin, venue_pairs = pair
        return cls(coin=coin, venues=[PredictedFundingVenue.from_pair(v) for v in venue_pairs])

    def to_pair(self) -> List[Any]:
        return [self.coin, [v.to_pair() for v in self.venues]]

    def rate_for(self, venue: str) -> Optional[Decimal]:
        return next((v.funding_rate for v in self.venues if v.venue == venue), None)

    @property
    def hyperliquid_rate(self) -> Optional[Decimal]:
        return self.rate_for("HlPerp")


# --- misc per-dex / per-coin info ---------------------------------------------

@dataclass
class CoinOiCap:
    """One entry of `perpDexLimits.coinToOiCap`: a coin and its open-interest cap."""

    coin: str
    cap: Decimal

    @classmethod
    def from_pair(cls, pair: Sequence[Any]) -> "CoinOiCap":
        coin, cap = pair
        return cls(coin=coin, cap=Decimal(cap))

    def to_pair(self) -> List[Any]:
        return [self.coin, str(self.cap)]


@dataclass
class PerpDexLimits:
    """Response of `perpDexLimits` for a builder-deployed (HIP-3) dex."""

    total_oi_cap: Decimal
    oi_sz_cap_per_perp: Decimal
    max_transfer_ntl: Decimal
    coin_to_oi_cap: List[CoinOiCap] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PerpDexLimits":
        return cls(
            total_oi_cap=Decimal(data["totalOiCap"]),
            oi_sz_cap_per_perp=Decimal(data["oiSzCapPerPerp"]),
            max_transfer_ntl=Decimal(data["maxTransferNtl"]),
            coin_to_oi_cap=[CoinOiCap.from_pair(p) for p in data.get("coinToOiCap", [])],
        )


@dataclass
class PerpDexStatus:
    total_net_deposit: Decimal

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PerpDexStatus":
        return cls(total_net_deposit=Decimal(data["totalNetDeposit"]))


@dataclass
class PerpDeployAuctionStatus:
    start_time_seconds: int
    duration_seconds: int
    start_gas: Optional[Decimal] = None
    current_gas: Optional[Decimal] = None
    end_gas: Optional[Decimal] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PerpDeployAuctionStatus":
        return cls(
            start_time_seconds=data["startTimeSeconds"],
            duration_seconds=data["durationSeconds"],
            start_gas=Decimal(data["startGas"]) if data.get("startGas") is not None else None,
            current_gas=Decimal(data["currentGas"]) if data.get("currentGas") is not None else None,
            end_gas=Decimal(data["endGas"]) if data.get("endGas") is not None else None,
        )


@dataclass
class PerpAnnotation:
    category: str
    description: str

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["PerpAnnotation"]:
        if data is None:
            return None
        return cls(category=data["category"], description=data["description"])

    def to_dict(self) -> Dict[str, Any]:
        return {"category": self.category, "description": self.description}


@dataclass
class PerpCategory:
    """One entry of `perpCategories`: a coin and the category it belongs to."""

    coin: str
    category: str

    @classmethod
    def from_pair(cls, pair: Sequence[Any]) -> "PerpCategory":
        coin, category = pair
        return cls(coin=coin, category=category)

    def to_pair(self) -> List[Any]:
        return [self.coin, self.category]


@dataclass
class PerpConciseAnnotation:
    coin: str
    category: str
    keywords: List[str] = field(default_factory=list)

    @classmethod
    def from_pair(cls, pair: Sequence[Any]) -> "PerpConciseAnnotation":
        coin, data = pair
        return cls(coin=coin, category=data["category"], keywords=list(data.get("keywords", [])))

    def to_pair(self) -> List[Any]:
        return [self.coin, {"category": self.category, "keywords": list(self.keywords)}]


# --- account / user state (read-only, `user`-keyed info requests) -----------
#
# Everything below is returned by info requests that take the account's
# *master* wallet address as `user` (`clearinghouseState`, `frontendOpenOrders`,
# `orderStatus`, `userFillsByTime`, `userFunding`, `userFees`, `userRateLimit`).
# None of them need a signature -- they are public reads keyed by address -- so
# they only depend on HYPERLIQUID_WALLET_ADDRESS, never on the private key.
# Order placement/cancellation (which does need signing) is out of scope here.
#
# As with the market-data classes above, `from_dict` parses Hyperliquid's
# camelCase payloads into Decimal-typed dataclasses and `to_dict` renders them
# back in the same camelCase shape (Decimals as strings). Each record that has a
# venue-agnostic counterpart in `argus.perpetuals.shared.account` also has a
# `to_common()` adapter; the dispatcher only ever emits the homogenous record,
# which nests this venue record under "venue" (see shared/account.py).


_dec_or_none = _acct.dec_or_none
_str_or_none = _acct.str_or_none
_decimal_str = _acct.decimal_str


@dataclass
class MarginSummary:
    """Account-level margin totals (both `marginSummary` and `crossMarginSummary` use this shape)."""

    account_value: Decimal
    total_margin_used: Decimal
    total_ntl_pos: Decimal
    total_raw_usd: Decimal

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MarginSummary":
        return cls(
            account_value=Decimal(data["accountValue"]),
            total_margin_used=Decimal(data["totalMarginUsed"]),
            total_ntl_pos=Decimal(data["totalNtlPos"]),
            total_raw_usd=Decimal(data["totalRawUsd"]),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accountValue": _decimal_str(self.account_value),
            "totalMarginUsed": _decimal_str(self.total_margin_used),
            "totalNtlPos": _decimal_str(self.total_ntl_pos),
            "totalRawUsd": _decimal_str(self.total_raw_usd),
        }


@dataclass
class PositionLeverage:
    """Leverage applied to one position. `raw_usd` is only present for isolated margin."""

    type: Literal["cross", "isolated"]
    value: int
    raw_usd: Optional[Decimal] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PositionLeverage":
        return cls(
            type=data["type"],
            value=int(data["value"]),
            raw_usd=_dec_or_none(data.get("rawUsd")),
        )

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"type": self.type, "value": self.value}
        if self.raw_usd is not None:
            out["rawUsd"] = _decimal_str(self.raw_usd)
        return out


@dataclass
class CumulativeFunding:
    """Cumulative funding paid/received on one position."""

    all_time: Decimal
    since_change: Decimal
    since_open: Decimal

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CumulativeFunding":
        return cls(
            all_time=Decimal(data["allTime"]),
            since_change=Decimal(data["sinceChange"]),
            since_open=Decimal(data["sinceOpen"]),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allTime": _decimal_str(self.all_time),
            "sinceChange": _decimal_str(self.since_change),
            "sinceOpen": _decimal_str(self.since_open),
        }


@dataclass
class Position:
    """One open perp position from `clearinghouseState.assetPositions[].position`.

    `szi` is the signed size: positive == long, negative == short. `entry_px` and
    `liquidation_px` are null for positions Hyperliquid reports without them
    (e.g. cross positions with no liquidation price)."""

    coin: str
    szi: Decimal
    position_value: Decimal
    unrealized_pnl: Decimal
    return_on_equity: Decimal
    margin_used: Decimal
    max_leverage: int
    leverage: PositionLeverage
    cum_funding: CumulativeFunding
    entry_px: Optional[Decimal] = None
    liquidation_px: Optional[Decimal] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Position":
        return cls(
            coin=data["coin"],
            szi=Decimal(data["szi"]),
            position_value=Decimal(data["positionValue"]),
            unrealized_pnl=Decimal(data["unrealizedPnl"]),
            return_on_equity=Decimal(data["returnOnEquity"]),
            margin_used=Decimal(data["marginUsed"]),
            max_leverage=int(data["maxLeverage"]),
            leverage=PositionLeverage.from_dict(data["leverage"]),
            cum_funding=CumulativeFunding.from_dict(data["cumFunding"]),
            entry_px=_dec_or_none(data.get("entryPx")),
            liquidation_px=_dec_or_none(data.get("liquidationPx")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "coin": self.coin,
            "szi": _decimal_str(self.szi),
            "entryPx": _str_or_none(self.entry_px),
            "positionValue": _decimal_str(self.position_value),
            "unrealizedPnl": _decimal_str(self.unrealized_pnl),
            "returnOnEquity": _decimal_str(self.return_on_equity),
            "liquidationPx": _str_or_none(self.liquidation_px),
            "marginUsed": _decimal_str(self.margin_used),
            "maxLeverage": self.max_leverage,
            "leverage": self.leverage.to_dict(),
            "cumFunding": self.cum_funding.to_dict(),
        }

    @property
    def is_long(self) -> bool:
        return self.szi > 0

    @property
    def is_short(self) -> bool:
        return self.szi < 0

    @property
    def size(self) -> Decimal:
        """Unsigned position size."""
        return abs(self.szi)

    def to_common(self, dex: str = "") -> _acct.Position:
        return _acct.Position(
            name=self.coin,
            signed_size=self.szi,
            notional=self.position_value,
            unrealized_pnl=self.unrealized_pnl,
            entry_price=self.entry_px,
            liquidation_price=self.liquidation_px,
            leverage=Decimal(self.leverage.value),
            margin_used=self.margin_used,
            dex=dex,
            venue=self,
        )


@dataclass
class AssetPosition:
    """Wrapper Hyperliquid puts around each position (`type` is currently always "oneWay")."""

    type: str
    position: Position

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AssetPosition":
        return cls(type=data["type"], position=Position.from_dict(data["position"]))

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "position": self.position.to_dict()}


@dataclass
class ClearinghouseState:
    """A user's perp account state on one dex, from `clearinghouseState`.

    Hyperliquid keeps a separate clearinghouse per dex: the default dex (`dex=""`)
    and each HIP-3 dex have their own margin summary and positions. `dex` records
    which one this snapshot is for."""

    dex: str
    margin_summary: MarginSummary
    cross_margin_summary: MarginSummary
    cross_maintenance_margin_used: Decimal
    withdrawable: Decimal
    time: int
    asset_positions: List[AssetPosition] = field(default_factory=list)

    @classmethod
    def from_dict(cls, dex: str, data: Dict[str, Any]) -> "ClearinghouseState":
        return cls(
            dex=dex,
            margin_summary=MarginSummary.from_dict(data["marginSummary"]),
            cross_margin_summary=MarginSummary.from_dict(data["crossMarginSummary"]),
            cross_maintenance_margin_used=Decimal(data["crossMaintenanceMarginUsed"]),
            withdrawable=Decimal(data["withdrawable"]),
            time=int(data["time"]),
            asset_positions=[AssetPosition.from_dict(p) for p in data.get("assetPositions", [])],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dex": self.dex,
            "marginSummary": self.margin_summary.to_dict(),
            "crossMarginSummary": self.cross_margin_summary.to_dict(),
            "crossMaintenanceMarginUsed": _decimal_str(self.cross_maintenance_margin_used),
            "withdrawable": _decimal_str(self.withdrawable),
            "time": self.time,
            "assetPositions": [p.to_dict() for p in self.asset_positions],
        }

    @property
    def positions(self) -> List[Position]:
        return [ap.position for ap in self.asset_positions]

    @property
    def account_value(self) -> Decimal:
        return self.margin_summary.account_value

    def to_balance(self, account_mode: Optional[str] = None) -> _acct.AccountBalance:
        """`marginSummary` is the whole account (cross + isolated); `withdrawable` is what is free.
        Only meaningful for accounts that keep perp collateral in the perps ledger; see
        `AccountMode.uses_spot_collateral` and `unified_balance` for the others."""
        return _acct.AccountBalance(
            account_value=self.margin_summary.account_value,
            available_balance=self.withdrawable,
            total_margin_used=self.margin_summary.total_margin_used,
            total_position_notional=self.margin_summary.total_ntl_pos,
            venue=self,
            account_mode=account_mode,
        )


# --- account mode / spot ledger ------------------------------------------------

class AccountMode(str, Enum):
    """How the account keeps its books, from the `userAbstraction` info request (the
    values are the wire strings). It decides where collateral lives:

      - `DEFAULT` / `DISABLED`: standard accounts with separate perp and spot balances.
      - `DEX_ABSTRACTION`: discontinued mode; USDC in perps, other collateral in spot.
      - `UNIFIED` / `PORTFOLIO_MARGIN`: one collateral pool in the *spot* ledger backs spot
        and every perp dex. Hyperliquid's docs say the perps `accountValue`/`withdrawable`
        are "not meaningful" for these accounts (they read 0) and spot is the source of truth.
    """

    DEFAULT = "default"
    DISABLED = "disabled"
    DEX_ABSTRACTION = "dexAbstraction"
    UNIFIED = "unifiedAccount"
    PORTFOLIO_MARGIN = "portfolioMargin"

    @classmethod
    def from_wire(cls, value: Any) -> "AccountMode":
        try:
            return cls(value)
        except ValueError:
            raise _ers.UnsupportedAccountModeError(
                f"Unrecognised Hyperliquid account mode {value!r} from `userAbstraction`; "
                f"known modes: {[m.value for m in cls]}."
            ) from None

    @property
    def uses_spot_collateral(self) -> bool:
        return self in (AccountMode.UNIFIED, AccountMode.PORTFOLIO_MARGIN)


@dataclass
class SpotBalance:
    """One token's line in `spotClearinghouseState.balances`. `hold` is the part locked
    (e.g. by resting spot orders); `token` is the spot token index (USDC is 0)."""

    coin: str
    token: int
    total: Decimal
    hold: Decimal
    entry_ntl: Decimal

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SpotBalance":
        return cls(
            coin=data["coin"],
            token=int(data["token"]),
            total=Decimal(data["total"]),
            hold=Decimal(data["hold"]),
            entry_ntl=Decimal(data["entryNtl"]),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "coin": self.coin,
            "token": self.token,
            "total": _decimal_str(self.total),
            "hold": _decimal_str(self.hold),
            "entryNtl": _decimal_str(self.entry_ntl),
        }


USDC_TOKEN = 0


@dataclass
class SpotClearinghouseState:
    """A user's spot balances, from `spotClearinghouseState`. For unified / portfolio-margin
    accounts this is the account's collateral pool (see `AccountMode`).
    `available_after_maintenance` maps token index -> amount free after maintenance margin."""

    balances: List[SpotBalance]
    available_after_maintenance: Dict[int, Decimal] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SpotClearinghouseState":
        return cls(
            balances=[SpotBalance.from_dict(b) for b in data.get("balances", [])],
            available_after_maintenance={
                int(token): Decimal(amount)
                for token, amount in data.get("tokenToAvailableAfterMaintenance", [])
            },
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "balances": [b.to_dict() for b in self.balances],
            "tokenToAvailableAfterMaintenance": [
                [token, _decimal_str(amount)] for token, amount in self.available_after_maintenance.items()
            ],
        }

    @property
    def usdc(self) -> Optional[SpotBalance]:
        return next((b for b in self.balances if b.token == USDC_TOKEN), None)


@dataclass
class UnifiedAccountState:
    """Everything a unified / portfolio-margin balance is derived from: the spot ledger
    (collateral) plus each perp dex's clearinghouse (positions, PnL, margin). Stored as
    the balance's `venue` record so no ledger read is lost."""

    mode: AccountMode
    spot: SpotClearinghouseState
    perps: List[ClearinghouseState]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode.value,
            "spot": self.spot.to_dict(),
            "perps": [state.to_dict() for state in self.perps],
        }

    def to_balance(self) -> _acct.AccountBalance:
        """
        Equity = spot USDC + unrealized PnL across every perp dex; available = spot's own
        `tokenToAvailableAfterMaintenance` for USDC (else USDC total minus hold); margin and
        notional are summed over the perp dexes. Per Hyperliquid's docs the per-dex
        `accountValue` / `withdrawable` are not meaningful here, so they are never added in.

        The docs give no equity formula, so "USDC + unrealized PnL" is inferred; it matched a live
        account holding a small open ETH position (tests/hyper_order_lifecycle.py). Other collateral tokens (USDT0, ...) are
        listed in `assets` but not priced into `account_value`, and portfolio-margin borrowing
        is not modelled.
        """
        usdc = self.spot.usdc
        usdc_total = usdc.total if usdc else Decimal(0)
        usdc_hold = usdc.hold if usdc else Decimal(0)
        unrealized_pnl = sum((p.unrealized_pnl for s in self.perps for p in s.positions), Decimal(0))
        available = self.spot.available_after_maintenance.get(USDC_TOKEN, usdc_total - usdc_hold)
        assets = tuple(
            _acct.AssetBalance(
                asset=b.coin,
                total=b.total,
                available=b.total - b.hold,
                usd_value=b.total if b.token == USDC_TOKEN else None,
            )
            for b in self.spot.balances
            if b.total != 0
        )
        return _acct.AccountBalance(
            account_value=usdc_total + unrealized_pnl,
            available_balance=available,
            total_margin_used=sum((s.margin_summary.total_margin_used for s in self.perps), Decimal(0)),
            total_position_notional=sum((s.margin_summary.total_ntl_pos for s in self.perps), Decimal(0)),
            venue=self,
            account_mode=self.mode.value,
            assets=assets,
        )


@dataclass
class OpenOrder:
    """One resting order, in the richer `frontendOpenOrders` shape (which is also
    the shape nested inside `orderStatus` responses). `side` is "B" (bid/buy) or
    "A" (ask/sell), as on the wire. `sz` is the remaining size, `orig_sz` the
    size at placement. `cloid` is the client order id, if one was set."""

    coin: str
    side: Literal["A", "B"]
    limit_px: Decimal
    sz: Decimal
    oid: int
    timestamp: int
    orig_sz: Decimal
    order_type: str
    trigger_condition: str
    is_trigger: bool
    trigger_px: Decimal
    reduce_only: bool
    is_position_tpsl: bool
    cloid: Optional[str] = None
    tif: Optional[str] = None
    children: List[Any] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OpenOrder":
        return cls(
            coin=data["coin"],
            side=data["side"],
            limit_px=Decimal(data["limitPx"]),
            sz=Decimal(data["sz"]),
            oid=int(data["oid"]),
            timestamp=int(data["timestamp"]),
            # `openOrders` (the slim variant) omits these; default them so both parse.
            orig_sz=Decimal(data.get("origSz", data["sz"])),
            order_type=data.get("orderType", "Limit"),
            trigger_condition=data.get("triggerCondition", "N/A"),
            is_trigger=bool(data.get("isTrigger", False)),
            trigger_px=Decimal(data.get("triggerPx", "0.0")),
            reduce_only=bool(data.get("reduceOnly", False)),
            is_position_tpsl=bool(data.get("isPositionTpsl", False)),
            cloid=data.get("cloid"),
            tif=data.get("tif"),
            children=list(data.get("children", [])),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "coin": self.coin,
            "side": self.side,
            "limitPx": _decimal_str(self.limit_px),
            "sz": _decimal_str(self.sz),
            "oid": self.oid,
            "timestamp": self.timestamp,
            "origSz": _decimal_str(self.orig_sz),
            "orderType": self.order_type,
            "triggerCondition": self.trigger_condition,
            "isTrigger": self.is_trigger,
            "triggerPx": _decimal_str(self.trigger_px),
            "reduceOnly": self.reduce_only,
            "isPositionTpsl": self.is_position_tpsl,
            "cloid": self.cloid,
            "tif": self.tif,
            "children": list(self.children),
        }

    @property
    def is_buy(self) -> bool:
        return self.side == "B"

    def to_common(self, status: str = "open", dex: str = "") -> _acct.Order:
        """`status` is "open" for a resting order from `frontendOpenOrders`, or the
        lifecycle string `orderStatus` reported alongside the order. `dex` tags the
        ledger the order was read from (`orderStatus` does not say, so it stays "")."""
        return _acct.Order(
            order_id=_decimal_str(self.oid),
            client_order_id=self.cloid,
            name=self.coin,
            is_buy=self.is_buy,
            price=self.limit_px,
            original_size=self.orig_sz,
            remaining_size=self.sz,
            order_type=self.order_type,
            status=status,
            reduce_only=self.reduce_only,
            timestamp_ms=self.timestamp,
            dex=dex,
            venue=self,
        )


@dataclass
class OrderStatus:
    """Result of an `orderStatus` lookup by oid/cloid.

    `found` is False when Hyperliquid answers `{"status": "unknownOid"}`; then
    `order`, `status` and `status_timestamp` are all None. Otherwise `status` is
    the order's lifecycle state (e.g. "open", "filled", "canceled", "rejected",
    "triggered", "marginCanceled", ...)."""

    found: bool
    order: Optional[OpenOrder] = None
    status: Optional[str] = None
    status_timestamp: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OrderStatus":
        if data.get("status") != "order":
            return cls(found=False)
        wrapper = data["order"]
        return cls(
            found=True,
            order=OpenOrder.from_dict(wrapper["order"]),
            status=wrapper["status"],
            status_timestamp=int(wrapper["statusTimestamp"]),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "found": self.found,
            "order": self.order.to_dict() if self.order is not None else None,
            "status": self.status,
            "statusTimestamp": self.status_timestamp,
        }

    def to_common(self) -> Optional[_acct.Order]:
        """None for an `unknownOid` lookup. Keyed off `order` rather than `found` so the
        "found implies an order" invariant is enforced here instead of assumed."""
        if self.order is None:
            return None
        return self.order.to_common(status=self.status or "open")


# --- trading results (`exchange` endpoint) -----------------------------------

@dataclass
class OrderPlacementResult:
    """The per-order outcome of a `place_order` submission.

    The exchange answers an ok envelope even when the order itself is rejected at the
    venue (e.g. insufficient margin): such rejections arrive as ``{"error": "..."}``
    entries in ``data.statuses`` rather than as an err envelope. Exactly one of `oid`
    (with `status` "resting" or "filled") and `error` is set; `avg_px` is present only
    for an immediately-filled order.
    """

    coin: str
    oid: Optional[int] = None
    status: Optional[str] = None  # "resting" | "filled" | None (when errored)
    avg_px: Optional[Decimal] = None
    error: Optional[str] = None

    @classmethod
    def from_response(cls, coin: str, data: Dict[str, Any]) -> "OrderPlacementResult":
        statuses = (data.get("data") or {}).get("statuses") or []
        if not statuses:
            raise _ers.HyperLiquidError(f"Unexpected order response shape (no statuses): {data!r}")
        entry = statuses[0]  # this client submits one order per action
        if "resting" in entry:
            return cls(coin=coin, oid=int(entry["resting"]["oid"]), status="resting")
        if "filled" in entry:
            filled = entry["filled"]
            return cls(
                coin=coin,
                oid=int(filled["oid"]),
                status="filled",
                avg_px=Decimal(filled["avgPx"]) if filled.get("avgPx") is not None else None,
            )
        if "error" in entry:
            return cls(coin=coin, error=entry["error"])
        raise _ers.HyperLiquidError(f"Unrecognized order status entry: {entry!r}")

    @property
    def ok(self) -> bool:
        """True when the venue accepted the order (resting or filled)."""
        return self.error is None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "coin": self.coin,
            "oid": self.oid,
            "status": self.status,
            "avgPx": _acct.str_or_none(self.avg_px),
            "error": self.error,
        }
        return out


@dataclass
class CancelResult:
    """Outcome of a cancel action.

    The venue answers a cancel with per-id statuses (``data.statuses``), each either the
    bare string ``"success"`` (confirmed live) or a rejection
    (``{"error": "Order was never placed, already canceled, or filled."}``). Ids it could
    not cancel (already filled/canceled, or unknown) therefore come back as errors, so
    callers must check `ok` rather than assume a submitted cancel worked.
    """

    coin: str
    canceled_oids: List[int] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @classmethod
    def from_response(cls, coin: str, data: Dict[str, Any], requested_oids: Optional[List[int]] = None) -> "CancelResult":
        """`requested_oids` are the oids asked for, in order: a ``"success"`` status carries no
        id of its own, so it is matched to the request at the same position."""
        statuses = (data.get("data") or {}).get("statuses") or []
        canceled_oids: List[int] = []
        errors: List[str] = []
        for i, entry in enumerate(statuses):
            if isinstance(entry, dict) and "error" in entry:
                errors.append(entry["error"])
                continue
            oid = None
            if isinstance(entry, dict):
                for key in ("resting", "filled"):
                    if isinstance(entry.get(key), dict) and entry[key].get("oid") is not None:
                        oid = int(entry[key]["oid"])
                        break
            elif requested_oids is not None and i < len(requested_oids):
                oid = int(requested_oids[i])
            # An acknowledgement whose oid we cannot recover (e.g. a cancel by cloid) is still an
            # acknowledgement; record -1 so `ok` reflects it without inventing an id.
            canceled_oids.append(oid if oid is not None else -1)
        return cls(coin=coin, canceled_oids=canceled_oids, errors=errors)

    @property
    def ok(self) -> bool:
        """True only when the venue acknowledged canceling every requested id."""
        return not self.errors and bool(self.canceled_oids)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "coin": self.coin,
            "canceledOids": list(self.canceled_oids),
            "errors": list(self.errors),
        }


@dataclass
class UserFill:
    """One of the account's fills, from `userFills` / `userFillsByTime`.

    `dir` is Hyperliquid's human-readable direction ("Open Long", "Close Short",
    "Buy", ...). `crossed` is True for taker fills. `fee` is in `fee_token`
    (USDC on perps). `builder_fee` is only present when a builder fee applied."""

    coin: str
    px: Decimal
    sz: Decimal
    side: Literal["A", "B"]
    time: int
    start_position: Decimal
    dir: str
    closed_pnl: Decimal
    hash: str
    oid: int
    crossed: bool
    fee: Decimal
    tid: int
    fee_token: str = "USDC"
    builder_fee: Optional[Decimal] = None
    cloid: Optional[str] = None
    twap_id: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserFill":
        return cls(
            coin=data["coin"],
            px=Decimal(data["px"]),
            sz=Decimal(data["sz"]),
            side=data["side"],
            time=int(data["time"]),
            start_position=Decimal(data["startPosition"]),
            dir=data["dir"],
            closed_pnl=Decimal(data["closedPnl"]),
            hash=data["hash"],
            oid=int(data["oid"]),
            crossed=bool(data["crossed"]),
            fee=Decimal(data["fee"]),
            tid=int(data["tid"]),
            fee_token=data.get("feeToken", "USDC"),
            builder_fee=_dec_or_none(data.get("builderFee")),
            cloid=data.get("cloid"),
            twap_id=data.get("twapId"),
        )

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "coin": self.coin,
            "px": _decimal_str(self.px),
            "sz": _decimal_str(self.sz),
            "side": self.side,
            "time": self.time,
            "startPosition": _decimal_str(self.start_position),
            "dir": self.dir,
            "closedPnl": _decimal_str(self.closed_pnl),
            "hash": self.hash,
            "oid": self.oid,
            "crossed": self.crossed,
            "fee": _decimal_str(self.fee),
            "feeToken": self.fee_token,
            "tid": self.tid,
            "cloid": self.cloid,
            "twapId": self.twap_id,
        }
        if self.builder_fee is not None:
            out["builderFee"] = _decimal_str(self.builder_fee)
        return out

    @property
    def is_buy(self) -> bool:
        return self.side == "B"

    def to_common(self) -> _acct.Trade:
        """`crossed` is Hyperliquid's "this fill crossed the spread", i.e. the account took liquidity."""
        return _acct.Trade(
            trade_id=_decimal_str(self.tid),
            order_id=_decimal_str(self.oid),
            name=self.coin,
            is_buy=self.is_buy,
            price=self.px,
            size=self.sz,
            fee=self.fee,
            is_maker=not self.crossed,
            realized_pnl=self.closed_pnl,
            timestamp_ms=self.time,
            venue=self,
        )


@dataclass
class UserFundingPayment:
    """One funding payment applied to the account, from `userFunding`.

    `usdc` is the signed amount: negative == the account paid funding, positive
    == it received funding. `szi` is the signed position size the payment was
    computed on and `funding_rate` the hourly rate applied."""

    time: int
    hash: str
    coin: str
    funding_rate: Decimal
    szi: Decimal
    usdc: Decimal
    type: str = "funding"
    n_samples: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserFundingPayment":
        delta = data["delta"]
        return cls(
            time=int(data["time"]),
            hash=data["hash"],
            coin=delta["coin"],
            funding_rate=Decimal(delta["fundingRate"]),
            szi=Decimal(delta["szi"]),
            usdc=Decimal(delta["usdc"]),
            type=delta.get("type", "funding"),
            n_samples=delta.get("nSamples"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "time": self.time,
            "hash": self.hash,
            "delta": {
                "type": self.type,
                "coin": self.coin,
                "fundingRate": _decimal_str(self.funding_rate),
                "szi": _decimal_str(self.szi),
                "usdc": _decimal_str(self.usdc),
                "nSamples": self.n_samples,
            },
        }

    def to_common(self) -> _acct.FundingPayment:
        return _acct.FundingPayment(
            name=self.coin,
            timestamp_ms=self.time,
            rate=self.funding_rate,
            position_size=self.szi,
            payment=self.usdc,
            venue=self,
        )


@dataclass
class UserFees:
    """The account's current fee rates and rolling volume, from `userFees`.

    Only the perp-relevant rates are typed; the remainder of the (large, evolving)
    payload -- fee schedule tiers, referral/staking discounts, trial state -- is
    kept verbatim in `raw` so nothing is lost on the wire."""

    user_cross_rate: Decimal
    user_add_rate: Decimal
    user_spot_cross_rate: Decimal
    user_spot_add_rate: Decimal
    active_referral_discount: Decimal
    daily_user_vlm: List[Dict[str, Any]] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserFees":
        return cls(
            user_cross_rate=Decimal(data["userCrossRate"]),
            user_add_rate=Decimal(data["userAddRate"]),
            user_spot_cross_rate=Decimal(data.get("userSpotCrossRate", "0")),
            user_spot_add_rate=Decimal(data.get("userSpotAddRate", "0")),
            active_referral_discount=Decimal(data.get("activeReferralDiscount", "0")),
            daily_user_vlm=list(data.get("dailyUserVlm", [])),
            raw=dict(data),
        )

    def to_dict(self) -> Dict[str, Any]:
        out = dict(self.raw)
        out.update({
            "userCrossRate": _decimal_str(self.user_cross_rate),
            "userAddRate": _decimal_str(self.user_add_rate),
            "userSpotCrossRate": _decimal_str(self.user_spot_cross_rate),
            "userSpotAddRate": _decimal_str(self.user_spot_add_rate),
            "activeReferralDiscount": _decimal_str(self.active_referral_discount),
            "dailyUserVlm": list(self.daily_user_vlm),
        })
        return out

    @property
    def taker_rate(self) -> Decimal:
        return self.user_cross_rate

    @property
    def maker_rate(self) -> Decimal:
        return self.user_add_rate


@dataclass
class UserRateLimit:
    """The account's address-based rate-limit budget, from `userRateLimit`.

    Hyperliquid grants 1 request per 1 USDC of cumulative traded volume (plus an
    initial buffer) for *signed* actions; this is the budget order placement will
    draw down, so it is surfaced now so clients can watch it before trading lands."""

    cum_vlm: Decimal
    n_requests_used: int
    n_requests_cap: int
    n_requests_surplus: int = 0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserRateLimit":
        return cls(
            cum_vlm=Decimal(data["cumVlm"]),
            n_requests_used=int(data["nRequestsUsed"]),
            n_requests_cap=int(data["nRequestsCap"]),
            n_requests_surplus=int(data.get("nRequestsSurplus", 0)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cumVlm": _decimal_str(self.cum_vlm),
            "nRequestsUsed": self.n_requests_used,
            "nRequestsCap": self.n_requests_cap,
            "nRequestsSurplus": self.n_requests_surplus,
        }

    @property
    def n_requests_remaining(self) -> int:
        return max(self.n_requests_cap - self.n_requests_used, 0)


# --- websocket market-data wire encoding -------------------------------------

class HLP2ConvertClass(P2OrderBookConvertClass):
    """
    Duck-typed adapter for `argus.protocol.transmit_mkt_data_with_protocol_2`,
    mirroring `argus.polymarket._classes.P2ConvertClass`'s `.symbol` /
    `.transferable_2()` contract so the P2 wire format -- and any client-side
    parser built against it (e.g. Polymarket's P2 CSV field order) -- is
    identical for Hyperliquid's order book stream. Only `.symbol` construction
    differs: Hyperliquid has no separate ticker/market-slug, so `coin` (e.g.
    "BTC", or "xyz:AAPL" for a HIP-3 dex asset) is used directly as the symbol.

    Thin subclass of `argus.perpetuals.shared._classes.P2OrderBookConvertClass`,
    which enforces the expected market_data shape (see that class's docstring):

    {
        "BTC": {
            "bids": [{"price": "97500", "size": "1.5"}, ...],
            "asks": [{"price": "97501", "size": "2.0"}, ...],
        },
        "timestamp": 1770251679393,
    }
    """

    def __init__(self, coin: str, market_data: Dict[str, Any], order_book_depth: int):
        super().__init__(
            symbol=coin,
            lookup_key=coin,
            market_data=market_data,
            order_book_depth=order_book_depth,
        )


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    with open(path) as f:
        raw = json.load(f)

    cfg = PerpDexConfig.from_dict(raw)

    print(f"{cfg.full_name} ({cfg.name}) — {len(cfg.assets)} assets")
    print("NVDA streaming OI cap:", cfg.streaming_oi_cap_for("xyz:NVDA"))
    print("Addresses that can setOracle:", cfg.addresses_for_action(SubDeployerAction.SET_ORACLE))

    # round-trip check
    assert cfg.to_dict() == raw or json.dumps(cfg.to_dict()) == json.dumps(raw), (
        "to_dict() output differs from source JSON"
    )
    print("Round-trip OK")

    # --- UniverseConfig demo --------------------------------------------------

    universe_sample = {
        "universe": [
            {"name": "BTC", "szDecimals": 5, "maxLeverage": 50},
            {"name": "ETH", "szDecimals": 4, "maxLeverage": 50},
            {"name": "HPOS", "szDecimals": 0, "maxLeverage": 3, "onlyIsolated": True},
            {
                "name": "LOOM",
                "szDecimals": 1,
                "maxLeverage": 3,
                "isDelisted": True,
                "marginMode": "strictIsolated",
                "onlyIsolated": True,
            },
        ],
        "marginTables": [
            [50, {"description": "", "marginTiers": [{"lowerBound": "0.0", "maxLeverage": 50}]}],
            [
                51,
                {
                    "description": "tiered 10x",
                    "marginTiers": [
                        {"lowerBound": "0.0", "maxLeverage": 10},
                        {"lowerBound": "3000000.0", "maxLeverage": 5},
                    ],
                },
            ],
        ],
    }

    universe_cfg = UniverseConfig.from_dict(universe_sample)
    print(universe_cfg.get_asset("LOOM"))
    print(universe_cfg.get_margin_table(51).max_leverage_for_notional(4_000_000))  # -> 5
    assert universe_cfg.to_dict() == universe_sample
    print("UniverseConfig round-trip OK")