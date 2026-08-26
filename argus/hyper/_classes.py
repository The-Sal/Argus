from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from decimal import Decimal
from dataclasses import dataclass, field
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


@dataclass
class PerpCategory:
    """One entry of `perpCategories`: a coin and the category it belongs to."""

    coin: str
    category: str

    @classmethod
    def from_pair(cls, pair: Sequence[Any]) -> "PerpCategory":
        coin, category = pair
        return cls(coin=coin, category=category)


@dataclass
class PerpConciseAnnotation:
    coin: str
    category: str
    keywords: List[str] = field(default_factory=list)

    @classmethod
    def from_pair(cls, pair: Sequence[Any]) -> "PerpConciseAnnotation":
        coin, data = pair
        return cls(coin=coin, category=data["category"], keywords=list(data.get("keywords", [])))


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