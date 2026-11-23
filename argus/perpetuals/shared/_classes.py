import json
import time
import zlib
import base64
from argus import protocol
from decimal import Decimal
from collections.abc import Mapping
from dataclasses import dataclass, field
from argus.perpetuals.shared import errors as ers
from typing import Any, Dict, List, Literal, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # account.py imports this module, so these are annotation-only
    from argus.perpetuals.shared.account import OrderUpdate, Trade


def compress(data: dict) -> str:
    minified = json.dumps(data, separators=(',', ':')).encode()
    return base64.b64encode(zlib.compress(minified, level=9)).decode()



def paginate(items: list, offset: int, limit: int) -> list:
    """
    The dispatcher-wide pagination primitive behind every `offset` / `limit` action.
    Exists because Protocol 1 caps a single response at 9990 bytes after compression
    (see OutboundMessage), so any collection that can grow past a few dozen records
    must be served in pages. An offset past the end yields [] rather than raising;
    negative values are rejected.
    """
    if offset < 0 or limit < 0:
        raise ers.MissingArgumentError("'offset' and 'limit' must be non-negative")
    if offset >= len(items):
        return []
    return items[offset: offset + limit]


class OutboundMessage:
    """
    This class enforces the following structure for outbound messages:
    {
      "action": "<command_name>",
      "data": { /* response data or null */ },
      "error": "<error message or null>",
      "compressed": <bool>, // true when data is auto-compressed (see polymarket docs for details)
      "correlation_id": "<uuid>" // None if the request errors before the packet was processed, or a pushed response
    }
    """
    def __init__(self, action: str, data: Optional[Dict[str, Any]] = None, error: Optional[str] = None, compressed: bool = False, correlation_id: Optional[str] = None):
        self.action = action
        self.data = data
        self.error = error
        self.compressed = compressed
        self.correlation_id = correlation_id

    def convert_to_protocol_1(self) -> bytes:
        """
        Converts the outbound message into P1 bytes
        :return:
        """
        return protocol.encode_packet(json.dumps(self._compress_and_validate()).encode('utf-8'))

    def _compress_and_validate(self) -> dict:
        """
        Checks if the data requires compression, then compresses it.
        Checks max len of the data <= 9990
        :return:
        """
        size_of_payload = len(json.dumps(self.data))
        if size_of_payload >= 9500:
            print("[auto-compress] Data is being auto-compressed original size: " + str(size_of_payload))
            compressed_data = compress(self.data)
            print("[auto-compress] Compressed size: " + str(len(compressed_data)))
            if len(compressed_data) > 9990:
                raise ers.PacketTooLargeError("Data exceeds max size of 9990 bytes, size of compressed data: " + str(len(compressed_data)) + "")
            return {
                "action": self.action,
                "data": compressed_data,
                "error": self.error,
                "compressed": True,
                "correlation_id": self.correlation_id
            }
        else:
            return {
                "action": self.action,
                "data": self.data,
                "error": self.error,
                "compressed": False,
                "correlation_id": self.correlation_id
            }


class P2OrderBookConvertClass:
    """
    Shared base for the duck-typed adapter consumed by
    `argus.protocol.transmit_mkt_data_with_protocol_2` (`.symbol` /
    `.transferable_2()`). Hyperliquid's `HLP2ConvertClass` and Lighter's
    `LighterP2ConvertClass` both subclass this, differing only in the key under
    which the order book is stored in `market_data`.

    Enforces the exact market-data shape both venues produce (the same shape
    `argus.polymarket._classes.P2ConvertClass` uses):

        {
            <lookup_key>: {
                "bids": [{"price": "97500", "size": "1.5"}, ...],
                "asks": [{"price": "97501", "size": "2.0"}, ...],
            },
            "timestamp": 1770251679393,
        }

    `symbol` is the P2 wire identity; `lookup_key` is the key the book is stored
    under in `market_data` (the coin string for Hyperliquid, the integer
    `market_id` for Lighter). `timestamp` is optional and rendered as an empty
    field when absent, matching the previous behavior.

    A malformed book raises from the constructor (TypeError/ValueError) rather
    than silently emitting a packet full of zeros.
    """

    def __init__(self, symbol: str, lookup_key, market_data: Mapping, order_book_depth: int):
        if not isinstance(order_book_depth, int) or isinstance(order_book_depth, bool) or order_book_depth < 0:
            raise ValueError(
                f"{type(self).__name__}: order_book_depth must be a non-negative int, got {order_book_depth!r}"
            )
        self._symbol = symbol
        self._lookup_key = lookup_key
        self._order_book_depth = order_book_depth
        self._validate_market_data(market_data)
        self.market_data = market_data

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def order_book_depth(self) -> int:
        return self._order_book_depth

    def _validate_market_data(self, market_data: Mapping) -> None:
        if not isinstance(market_data, Mapping):
            raise TypeError(
                f"{type(self).__name__}: market_data must be a mapping, got {type(market_data).__name__}"
            )

        book = market_data.get(self._lookup_key)
        if not isinstance(book, Mapping):
            present = [key for key in market_data if key != 'timestamp']
            raise ValueError(
                f"{type(self).__name__}: market_data has no order book for key {self._lookup_key!r} "
                f"(present keys: {present!r})"
            )

        for side in ('bids', 'asks'):
            levels = book.get(side)
            if not isinstance(levels, list):
                raise ValueError(
                    f"{type(self).__name__}: {side!r} for key {self._lookup_key!r} must be a list, "
                    f"got {type(levels).__name__}"
                )
            for i, level in enumerate(levels):
                if not isinstance(level, Mapping) or 'price' not in level or 'size' not in level:
                    raise ValueError(
                        f"{type(self).__name__}: {side}[{i}] must be a mapping with 'price' and 'size', "
                        f"got {level!r}"
                    )

    def transferable_2(self) -> bytes:
        data_obj = self.market_data.get(self._lookup_key, {})
        bids = data_obj.get('bids', [])[:self._order_book_depth]
        asks = data_obj.get('asks', [])[:self._order_book_depth]

        market_packet = ""
        for i in range(self._order_book_depth):
            if i < len(bids):
                market_packet += f"{bids[i]['price']},{bids[i]['size']},"
            else:
                market_packet += "0,0,"

        for i in range(self._order_book_depth):
            if i < len(asks):
                market_packet += f"{asks[i]['price']},{asks[i]['size']},"
            else:
                market_packet += "0,0,"

        market_packet += f"{self.market_data.get('timestamp', '')},{time.time()}"
        return market_packet.encode('ascii')

class NewFundingRate:
    """
    A funding rate for a perpetual that will be sent unsolicited to the client under
    the action "funding_rate_update". Can directly be converted to bytes through the
    OutboundMessage.convert_to_protocol_1() method. Everyone using _distribute_refreshed_perpetuals
    should use this class. Downstream SDK depends on this structure.
    """
    def __init__(self, perp_name: str, funding_rate: Decimal | None):
        self.perp_name = perp_name
        self.funding_rate = str(funding_rate) if funding_rate is not None else None

    def convert_to_protocol_1(self):
        payload = {
            "coin": self.perp_name,
            "funding_rate": self.funding_rate
        }
        return OutboundMessage(action="funding_rate_update", data=payload).convert_to_protocol_1()


class AccountUpdate:
    """
    An unsolicited account event pushed to every connected client under the action "account_update".
    Can directly be converted to bytes through `convert_to_protocol_1()`, exactly like `NewFundingRate`,
    so the envelope and auto-compression stay identical to every other push. Every dispatcher that
    pushes account events must build them with this class; the downstream SDK depends on the shape:

        {"event": "order", "order": <OrderUpdate.to_dict()>}
        {"event": "fill",  "trade": <Trade.to_dict()>}
        {"event": "gap",   "reason": <str>, "since_ms": <int>}

    One record per message, never a batch: a venue frame can hold many records and a batch could exceed the
    Protocol 1 byte cap (OutboundMessage raises PacketTooLargeError). Build instances through the
    `order` / `fill` / `gap` constructors rather than `__init__`, which only stores the finished payload.
    """

    def __init__(self, event: str, payload: dict):
        self.event = event
        self.payload = payload

    @classmethod
    def order(cls, update: "OrderUpdate") -> "AccountUpdate":
        """An order lifecycle transition (open, canceled, filled, rejected, ...)."""
        return cls("order", {"order": update.to_dict()})

    @classmethod
    def fill(cls, trade: "Trade") -> "AccountUpdate":
        """One fill of one of the account's orders."""
        return cls("fill", {"trade": trade.to_dict()})

    @classmethod
    def gap(cls, reason: str, since_ms: int) -> "AccountUpdate":
        """
        The account stream was interrupted and events since `since_ms` (unix ms) may have been missed;
        clients should reconcile with `get_orders` / `get_trades`.
        """
        return cls("gap", {"reason": reason, "since_ms": since_ms})

    def to_dict(self) -> dict:
        return {"event": self.event, **self.payload}

    def convert_to_protocol_1(self):
        return OutboundMessage(action="account_update", data=self.to_dict()).convert_to_protocol_1()


# --- shared trading shapes ---------------------------------------------------
#
# These are the venue-agnostic value objects the trading handlers in
# `argus.perpetuals.shared.trading` (and every venue's exchange client) pass around. They
# used to live in `argus/perpetuals/hyper/_classes.py`; they are here because the trading
# control flow that consumes them (read current leverage -> apply -> place -> roll back) is
# identical on every venue. Venue-specific result records (`OrderPlacementResult`,
# `CancelResult`, `BatchCancelResult`) deliberately stay in each venue's `_classes.py` -- the
# mixin only relies on the small shape they expose (`.ok`, `.price`, `.to_dict()`, ...).
#
# The only venue semantics baked in is the *margin-mode vocabulary* ("cross" / "isolated")
# and the TIF vocabulary ("Gtc" / "Ioc" / "Alo"); a venue whose wire spelling differs
# translates at its exchange boundary, not here.


def _dec_or_none(value: Any) -> Optional[Decimal]:
    """Decimal(value), or None for a null upstream field. Local copy so this module stays free of
    a module-level import of `account.py` (which imports `paginate` from here)."""
    return Decimal(value) if value is not None else None


@dataclass
class PositionLeverage:
    """Leverage applied to one position. `raw_usd` is only present for isolated margin on Hyperliquid."""

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
            out["rawUsd"] = format(self.raw_usd)
        return out


@dataclass
class OrderLeverage:
    """
    Leverage bookkeeping for one order, attached to its `OrderPlacementResult` by the dispatcher.

    `leverage` is what is in force on the coin AFTER the call. The dispatcher applies the caller's leverage
    before submitting; if the order then fails (venue rejection or exception) a changed leverage is rolled
    back to `previous`, flagged by `reverted` (and `revert_error` if the rollback itself failed, in which case
    the caller must check/repair leverage with `get_leverage` / `set_leverage`).
    `estimated_initial_margin` is ``size * price / leverage`` -- an ESTIMATE (a marketable IOC fills at the book
    price and cross margin also nets unrealized PnL); None for reduce-only orders and failed orders.
    """

    leverage: PositionLeverage
    previous: PositionLeverage
    changed: bool = False
    reverted: bool = False
    revert_error: Optional[str] = None
    estimated_initial_margin: Optional[Decimal] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "leverage": {"type": self.leverage.type, "value": self.leverage.value},
            "margin_mode": self.leverage.type,
            "previous_leverage": {"type": self.previous.type, "value": self.previous.value},
            "leverage_changed": self.changed,
            "leverage_reverted": self.reverted,
            "estimated_initial_margin": format(self.estimated_initial_margin) if self.estimated_initial_margin is not None else None,
        }
        if self.revert_error is not None:
            out["leverage_revert_error"] = self.revert_error
        return out


@dataclass
class OrderRequest:
    """
    One limit order to place, as the dispatcher and exchange layers pass it around (no raw dicts).

    `from_dict` takes the wire-facing dispatcher shape (``coin, side, price, size, order_type?, reduce_only?,
    cloid?``) and does ALL the validation `place_order` does, so a batch fails client-side, before anything is
    signed, on its first bad item. `tif` is the venue-agnostic spelling ("Gtc" | "Ioc" | "Alo"); a venue
    maps it to its own wire enum at the exchange boundary. `cloid` is the caller-supplied client order id in
    the venue's own format (a Hyperliquid 0x-prefixed 16-byte hex string, a Lighter uint48 for a venue whose
    client id is an integer); the venue validates/converts it.
    """

    coin: str
    side: str  # "buy" | "sell"
    price: Any
    size: Any
    tif: str = "Gtc"  # "Gtc" | "Ioc" | "Alo"
    reduce_only: bool = False
    cloid: Optional[str] = None

    _TIFS = {"GTC": "Gtc", "IOC": "Ioc", "ALO": "Alo"}
    FIELDS = ("coin", "side", "price", "size", "order_type", "reduce_only", "cloid")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OrderRequest":
        if not isinstance(data, dict):
            raise ers.DispatcherError(f"Order must be an object, got {type(data).__name__}")
        unknown = sorted(set(data) - set(cls.FIELDS))
        if unknown:
            raise ers.DispatcherError(f"Unknown order field(s) {unknown}; accepted: {sorted(cls.FIELDS)}")
        for required in ("coin", "side", "price", "size"):
            if data.get(required) is None:
                raise ers.DispatcherError(f"Missing required order field {required!r}")
        order_type = str(data.get("order_type") or "GTC").upper()
        tif = cls._TIFS.get(order_type)
        if tif is None:
            raise ers.DispatcherError(f"Invalid order_type {order_type!r}: expected GTC, IOC or ALO")
        side = str(data["side"]).lower()
        if side not in ("buy", "sell"):
            raise ers.DispatcherError(f"Invalid side {data['side']!r}: expected 'buy' or 'sell'")
        return cls(
            coin=data["coin"], side=side, price=data["price"], size=data["size"], tif=tif,
            reduce_only=bool(data.get("reduce_only") or False), cloid=data.get("cloid"),
        )

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "coin": self.coin, "side": self.side, "price": str(self.price), "size": str(self.size),
            "order_type": self.tif.upper(), "reduce_only": self.reduce_only,
        }
        if self.cloid is not None:
            out["cloid"] = self.cloid
        return out


@dataclass(frozen=True)
class LeverageSetting:
    """
    The leverage a caller wants for one coin, as passed to `place_order` / `place_multiple_orders` /
    `set_leverage`. Leverage is stored per coin and margin mode, so every order names its own.
    `margin_mode` None means "keep the coin's current mode".
    """

    leverage: int
    margin_mode: Optional[Literal["cross", "isolated"]] = None

    @classmethod
    def from_args(cls, leverage: Any, margin_mode: Any = None) -> "LeverageSetting":
        """Validate wire-facing values: an integer (not bool/float/str) and a known mode."""
        if isinstance(leverage, bool) or not isinstance(leverage, int):
            raise ers.DispatcherError(f"Invalid leverage {leverage!r}: expected an integer")
        if margin_mode is not None and margin_mode not in ("cross", "isolated"):
            raise ers.DispatcherError(f"Invalid margin_mode {margin_mode!r}: expected 'cross' or 'isolated'")
        return cls(leverage=leverage, margin_mode=margin_mode)

    def resolve(self, current: "PositionLeverage") -> "PositionLeverage":
        """The leverage this setting yields given the coin's `current` one (mode defaults to current)."""
        return PositionLeverage(type=self.margin_mode or current.type, value=self.leverage)


@dataclass(frozen=True)
class LeverageChange:
    """What applying a `LeverageSetting` to one coin did: the leverage it had (`previous`, the roll-back
    target) and the one now in force (`applied`). `changed` is False when the coin already had it, in which
    case nothing was signed and there is nothing to roll back."""

    coin: str
    previous: PositionLeverage
    applied: PositionLeverage

    @property
    def changed(self) -> bool:
        return (self.previous.type, self.previous.value) != (self.applied.type, self.applied.value)


@dataclass
class CancelOutcome:
    """One item of a batch cancel: the id the caller gave (a venue id as str, or a client id), its coin, and
    whether the venue acknowledged it. A cancel for an order that filled/cancelled in the meantime is an
    item-level error."""

    order_id: str
    coin: Optional[str]
    ok: bool
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"order_id": self.order_id, "coin": self.coin, "ok": self.ok, "error": self.error}


@dataclass
class BatchCancelResult:
    """Per-item outcomes of a batch cancel, in the caller's original order. Partial success is normal."""

    outcomes: List[CancelOutcome] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for o in self.outcomes if o.ok)

    @property
    def error_count(self) -> int:
        return len(self.outcomes) - self.ok_count

    def to_dict(self) -> Dict[str, Any]:
        return {
            "outcomes": [o.to_dict() for o in self.outcomes],
            "ok_count": self.ok_count,
            "error_count": self.error_count,
        }

    def to_summary_dict(self, max_failures: int = 50) -> Dict[str, Any]:
        """Compact form for sweeps of up to ~1000 orders (one record per order would blow Protocol 1's
        byte cap): counts plus at most `max_failures` failure records."""
        failures = [o.to_dict() for o in self.outcomes if not o.ok]
        return {
            "requested": len(self.outcomes),
            "canceled": self.ok_count,
            "failed": self.error_count,
            "failures": failures[:max_failures],
            "failures_truncated": len(failures) > max_failures,
        }
