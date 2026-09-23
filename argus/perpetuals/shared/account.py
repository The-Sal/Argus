"""
Venue-agnostic account data for Argus v2's perpetual dispatchers.

This module is to *account* data what `argus.perpetuals.shared.wss` is to market
data: the one place that defines the shape every venue must produce, so that the
dispatcher-facing handlers (and therefore the wire format the SDK sees) are
written once and shared by `HyperLiquidDispatcher` and `LighterDispatcher`.

Design
------
Three layers, each typed:

1. **Venue records** live in each venue's `_classes.py` and mirror the upstream
   API one-to-one (Hyperliquid's `ClearinghouseState`, Lighter's `AccountSummary`,
   ...). They know nothing about other venues. Every venue record exposes
   `to_dict()` (the `VenueRecord` protocol below) so nothing upstream sends is
   lost on the wire.

2. **Homogenous records** are the frozen dataclasses in this module
   (`AccountBalance`, `Position`, `Order`, `Trade`, `FundingPayment`). They carry
   the fields any perp venue can supply -- signed size, entry price, unrealized
   PnL, fee, ... -- in one naming scheme, and hold the venue record under
   `.venue`. Each venue's REST client builds these from its own venue records.
   This is the seed of the Phase 1.2 "Homogenous Trading API Specification" and
   what the Phase 2 multi-market dispatcher will aggregate over.

3. **Handlers** (`AccountHandlersMixin`) are the dispatcher actions
   (`get_balance`, `get_positions`, `get_orders`, `get_order_status`,
   `get_trades`, `get_funding_payments`). They only ever see homogenous records
   through the `BaseDispatcherCompatibleAccountRest` interface, so a venue plugs
   in by (a) implementing that interface on its REST client and (b) merging
   `account_routing_table()` into its routing table. Action names follow
   `PolymarketDispatcher` (`get_balance` / `get_positions` / `get_orders` /
   `get_order_status` / `get_trades`) so the SDK's client surface stays uniform
   across dispatchers; `get_funding_payments` is the one perps-only addition.

Wire format
-----------
Every homogenous record's `to_dict()` emits its common fields (Decimals as
strings, like the rest of the perps dispatchers) plus a `"venue"` key holding
the venue record's own `to_dict()`. A client that only needs the common fields
can ignore `venue`; one that needs venue specifics has them without a second
request.

Because `venue` roughly doubles the per-record size (a Hyperliquid fill is ~600
bytes on the wire, a Lighter trade ~900), every list action is paginated with
the dispatcher-wide `offset` / `limit` convention and a small default page
(`DEFAULT_PAGE_SIZE`) so a page stays well under the Protocol 1 ceiling
(`OutboundMessage` auto-compresses at 9500 bytes and hard-fails at 9990 after
compression). Clients wanting everything walk pages exactly as documented for
Polymarket's `get_trades`.

Timestamps
----------
Homogenous records use unix **milliseconds** everywhere (`timestamp_ms`),
matching Hyperliquid and the P2 `book_timestamp`. Venues that report seconds or
microseconds normalise through `normalize_timestamp_ms` and keep the original
value untouched under `venue`.
"""
import time
from decimal import Decimal
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from argus._argus_utils import ArgsObject
from argus.perpetuals.shared import _errors as ers


DEFAULT_PAGE_SIZE = 25
"""Default `limit` for every paginated account action (see module docstring)."""

DEFAULT_FUNDING_LOOKBACK_MS = 7 * 24 * 60 * 60 * 1000
"""How far back `get_funding_payments` looks when the client omits `start_time`."""


def normalize_timestamp_ms(value: int) -> int:
    """
    Coerce a unix timestamp of unknown unit to milliseconds. Venues are not
    consistent (Lighter mixes seconds, milliseconds and microseconds across
    fields; Hyperliquid is ms throughout), so homogenous records route every
    timestamp through here. The unit is inferred from magnitude, which is
    unambiguous for any date between 1973 and 5138:
      < 1e11  -> seconds
      < 1e14  -> milliseconds
      else    -> microseconds
    """
    value = int(value)
    if value < 100_000_000_000:
        return value * 1000
    if value < 100_000_000_000_000:
        return value
    return value // 1000


def dec_or_none(value: Any) -> Optional[Decimal]:
    """Decimal(value), or None for a null upstream field. Shared by every venue's `from_dict`."""
    return Decimal(value) if value is not None else None


def str_or_none(value: Optional[Decimal]) -> Optional[str]:
    """str(value), or None for an absent Decimal. Shared by every venue's `to_dict`."""
    return str(value) if value is not None else None


_s = str_or_none


@runtime_checkable
class VenueRecord(Protocol):
    """What a homogenous record requires of the venue record it wraps."""

    def to_dict(self) -> Dict[str, Any]: ...


# --- homogenous records ------------------------------------------------------

@dataclass(frozen=True)
class AccountBalance:
    """
    Account-level equity and margin for one venue account.

    Attributes:
        account_value: Total equity including unrealized PnL, in the venue's quote/collateral currency (USDC on both venues).
        available_balance: What could be withdrawn or used to open new positions right now.
        total_margin_used: Collateral currently locked as margin across all positions.
        total_position_notional: Sum of |size| * mark price over all open positions.
        venue: The venue's native account record (e.g. Hyperliquid `ClearinghouseState`).
    """

    account_value: Decimal
    available_balance: Decimal
    total_margin_used: Decimal
    total_position_notional: Decimal
    venue: VenueRecord

    def to_dict(self) -> Dict[str, Any]:
        return {
            "account_value": str(self.account_value),
            "available_balance": str(self.available_balance),
            "total_margin_used": str(self.total_margin_used),
            "total_position_notional": str(self.total_position_notional),
            "venue": self.venue.to_dict(),
        }


@dataclass(frozen=True)
class Position:
    """
    One open perpetual position.

    Attributes:
        name: The perpetual's symbol as the dispatcher's market-data actions know it (Hyperliquid coin, Lighter symbol).
        signed_size: Position size in base units; positive is long, negative is short.
        entry_price: Average entry price, or None if the venue does not report one.
        notional: Current |size| * mark price, in quote currency.
        unrealized_pnl: Mark-to-market PnL in quote currency.
        liquidation_price: Estimated liquidation price, or None (e.g. no liquidation risk / not reported).
        leverage: Effective leverage as the venue reports it, or None if not reported as a plain number.
        margin_used: Collateral allocated to this position, or None if not reported.
        venue: The venue's native position record.
    """

    name: str
    signed_size: Decimal
    notional: Decimal
    unrealized_pnl: Decimal
    venue: VenueRecord
    entry_price: Optional[Decimal] = None
    liquidation_price: Optional[Decimal] = None
    leverage: Optional[Decimal] = None
    margin_used: Optional[Decimal] = None

    @property
    def size(self) -> Decimal:
        return abs(self.signed_size)

    @property
    def is_long(self) -> bool:
        return self.signed_size > 0

    @property
    def is_short(self) -> bool:
        return self.signed_size < 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "signed_size": str(self.signed_size),
            "side": "long" if self.is_long else "short",
            "entry_price": _s(self.entry_price),
            "notional": str(self.notional),
            "unrealized_pnl": str(self.unrealized_pnl),
            "liquidation_price": _s(self.liquidation_price),
            "leverage": _s(self.leverage),
            "margin_used": _s(self.margin_used),
            "venue": self.venue.to_dict(),
        }


@dataclass(frozen=True)
class Order:
    """
    One order, resting or historical.

    Attributes:
        order_id: The venue's order id, always as a string (Hyperliquid oids are ints, Lighter's are decimal strings).
        name: The perpetual's symbol.
        is_buy: True for a bid/long-opening order.
        price: Limit price (0 for pure market orders on venues that report it that way).
        original_size: Size at placement, base units.
        remaining_size: Unfilled size, base units.
        order_type: The venue's order-type string, untranslated (e.g. "Limit", "Stop Market" / "limit", "stop-loss").
        status: The venue's lifecycle string, untranslated. Resting orders from `get_orders` are always "open".
        reduce_only: Whether the order can only reduce a position.
        timestamp_ms: Placement time, unix ms.
        client_order_id: Client-assigned id if one was set, else None.
        venue: The venue's native order record.
    """

    order_id: str
    name: str
    is_buy: bool
    price: Decimal
    original_size: Decimal
    remaining_size: Decimal
    order_type: str
    status: str
    reduce_only: bool
    timestamp_ms: int
    venue: VenueRecord
    client_order_id: Optional[str] = None

    @property
    def filled_size(self) -> Decimal:
        return self.original_size - self.remaining_size

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "client_order_id": self.client_order_id,
            "name": self.name,
            "side": "buy" if self.is_buy else "sell",
            "price": str(self.price),
            "original_size": str(self.original_size),
            "remaining_size": str(self.remaining_size),
            "order_type": self.order_type,
            "status": self.status,
            "reduce_only": self.reduce_only,
            "timestamp_ms": self.timestamp_ms,
            "venue": self.venue.to_dict(),
        }


@dataclass(frozen=True)
class Trade:
    """
    One fill of one of the account's orders.

    Attributes:
        trade_id: Venue trade/fill id as a string.
        order_id: Id of the account's order that this fill belongs to, as a string.
        name: The perpetual's symbol.
        is_buy: Direction from the account's perspective.
        price: Fill price.
        size: Fill size, base units.
        fee: Fee charged to the account for this fill, quote currency (negative means a rebate).
        is_maker: True if the account's order was resting (maker) for this fill.
        realized_pnl: PnL realized by this fill if the venue reports it, else None.
        timestamp_ms: Fill time, unix ms.
        venue: The venue's native fill/trade record.
    """

    trade_id: str
    order_id: str
    name: str
    is_buy: bool
    price: Decimal
    size: Decimal
    fee: Decimal
    is_maker: bool
    timestamp_ms: int
    venue: VenueRecord
    realized_pnl: Optional[Decimal] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "order_id": self.order_id,
            "name": self.name,
            "side": "buy" if self.is_buy else "sell",
            "price": str(self.price),
            "size": str(self.size),
            "fee": str(self.fee),
            "is_maker": self.is_maker,
            "realized_pnl": _s(self.realized_pnl),
            "timestamp_ms": self.timestamp_ms,
            "venue": self.venue.to_dict(),
        }


@dataclass(frozen=True)
class FundingPayment:
    """
    One funding settlement applied to one of the account's positions.

    Attributes:
        name: The perpetual's symbol.
        timestamp_ms: Settlement time, unix ms.
        rate: The funding rate applied at that settlement (per-interval, not annualised).
        position_size: Signed position size the payment was computed on.
        payment: Signed cash flow in quote currency: negative means the account paid, positive it received.
        venue: The venue's native funding record.
    """

    name: str
    timestamp_ms: int
    rate: Decimal
    position_size: Decimal
    payment: Decimal
    venue: VenueRecord

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "timestamp_ms": self.timestamp_ms,
            "rate": str(self.rate),
            "position_size": str(self.position_size),
            "payment": str(self.payment),
            "venue": self.venue.to_dict(),
        }


# --- REST interface ----------------------------------------------------------

class BaseDispatcherCompatibleAccountRest:
    """
    The account-data contract a venue REST client fulfils for `AccountHandlersMixin`,
    in the same spirit as `BaseDispatcherCompatibleRest` for market data. A REST
    client may implement both (Hyperliquid's does).

    Every method returns homogenous records built from the venue's own typed
    records. Methods may accept venue-specific keyword arguments (e.g.
    Hyperliquid's `dex`); the handlers forward any request args they do not
    consume themselves as keyword arguments, so a venue that does not support a
    given argument fails loudly with a TypeError that is relayed to the client.

    Implementations raise `ers.AccountNotConfiguredError` when they cannot
    answer because the account (or the credential a specific read needs) was
    never configured, rather than returning empty data that looks like a flat
    account.
    """

    def account_identity(self) -> str:
        """A short, non-secret label for the account (address or index) echoed in responses."""
        raise NotImplementedError("account_identity() not implemented.")

    def get_account_balance(self, **venue_args: Any) -> AccountBalance:
        raise NotImplementedError("get_account_balance() not implemented.")

    def get_positions(self, **venue_args: Any) -> List[Position]:
        """Only positions with non-zero size."""
        raise NotImplementedError("get_positions() not implemented.")

    def get_open_orders(self, **venue_args: Any) -> List[Order]:
        """Currently resting orders, newest first."""
        raise NotImplementedError("get_open_orders() not implemented.")

    def get_order_status(self, order_id: str, **venue_args: Any) -> Optional[Order]:
        """The order with this venue id (any lifecycle state), or None if the venue does not know it."""
        raise NotImplementedError("get_order_status() not implemented.")

    def get_recent_trades(self, max_count: int, **venue_args: Any) -> List[Trade]:
        """Up to `max_count` most recent fills, newest first. Venues may return fewer
        (their own history caps apply) but must not return more."""
        raise NotImplementedError("get_recent_trades() not implemented.")

    def get_funding_payments(self, start_time_ms: int, end_time_ms: Optional[int] = None,
                             **venue_args: Any) -> List[FundingPayment]:
        """Funding settlements in [start_time_ms, end_time_ms] (end defaults to now), newest first."""
        raise NotImplementedError("get_funding_payments() not implemented.")


# --- dispatcher handlers -----------------------------------------------------

def request_args(args: ArgsObject) -> Dict[str, Any]:
    """The request's `data` as a dict; `null` / list payloads count as no arguments."""
    return dict(args.args) if isinstance(args.args, Mapping) else {}


class AccountHandlersMixin:
    """
    The dispatcher-side account actions, written once for every venue.

    Host requirements (satisfied by `BaseDispatcher`):
      - `self.account_rest`: a `BaseDispatcherCompatibleAccountRest`, or None if the
        venue/dispatcher has no account configured. Every handler raises
        `ers.AccountNotConfiguredError` in that case.
      - `self._routine_paginate(items, args, default_limit)`: the dispatcher-wide
        `offset` / `limit` slicer.

    Subclasses merge `self.account_routing_table()` into their routing table.
    Request `data` for every action is an object; unknown keys are forwarded to the
    REST client as venue-specific keyword arguments (see
    `BaseDispatcherCompatibleAccountRest`).
    """

    _PAGING_KEYS = ("offset", "limit")

    account_rest: Optional[BaseDispatcherCompatibleAccountRest]

    def account_routing_table(self) -> Dict[str, Any]:
        """Action name -> handler, for merging into a dispatcher's routing table."""
        return {
            'get_balance': self._handle_get_balance,
            'get_positions': self._handle_get_positions,
            'get_orders': self._handle_get_orders,
            'get_order_status': self._handle_get_order_status,
            'get_trades': self._handle_get_trades,
            'get_funding_payments': self._handle_get_funding_payments,
        }

    def _require_account_rest(self) -> BaseDispatcherCompatibleAccountRest:
        rest = getattr(self, "account_rest", None)
        if rest is None:
            raise ers.AccountNotConfiguredError(
                "This dispatcher has no account configured; account actions are unavailable."
            )
        return rest

    @classmethod
    def _venue_args(cls, args: ArgsObject, *consumed: str) -> Dict[str, Any]:
        """Request args minus the ones the handler itself consumed -- passed through to the venue."""
        skip = set(cls._PAGING_KEYS) | set(consumed)
        return {k: v for k, v in request_args(args).items() if k not in skip}

    def _handle_get_balance(self, args: ArgsObject) -> dict:
        """
        Account equity and margin totals.
        :param args: No required arguments. Venue-specific extras are forwarded (Hyperliquid: 'dex').
        :return: {'account': <identity>, 'account_value', 'available_balance', 'total_margin_used',
                  'total_position_notional', 'venue': {...}}
        """
        rest = self._require_account_rest()
        balance = rest.get_account_balance(**self._venue_args(args))
        return {'account': rest.account_identity(), **balance.to_dict()}

    def _handle_get_positions(self, args: ArgsObject) -> dict:
        """
        Open positions (non-zero size only), paginated.
        :param args: 'offset' (default 0), 'limit' (default DEFAULT_PAGE_SIZE). Venue extras forwarded.
        :return: {'account': <identity>, 'positions': [Position.to_dict(), ...]}
        """
        rest = self._require_account_rest()
        positions = rest.get_positions(**self._venue_args(args))
        page = self._routine_paginate(positions, args, DEFAULT_PAGE_SIZE)
        return {'account': rest.account_identity(), 'positions': [p.to_dict() for p in page]}

    def _handle_get_orders(self, args: ArgsObject) -> dict:
        """
        Resting orders, newest first, paginated.
        :param args: 'offset' (default 0), 'limit' (default DEFAULT_PAGE_SIZE). Venue extras forwarded.
        :return: {'account': <identity>, 'orders': [Order.to_dict(), ...]}
        """
        rest = self._require_account_rest()
        orders = rest.get_open_orders(**self._venue_args(args))
        page = self._routine_paginate(orders, args, DEFAULT_PAGE_SIZE)
        return {'account': rest.account_identity(), 'orders': [o.to_dict() for o in page]}

    def _handle_get_order_status(self, args: ArgsObject) -> dict:
        """
        Look up one order by its venue id, in any lifecycle state.
        :param args: 'order_id' (required; int or string, always matched as a string). Venue extras forwarded.
        :return: {'found': bool, 'order': Order.to_dict() | None}
        """
        rest = self._require_account_rest()
        order_id = request_args(args).get('order_id')
        if order_id is None:
            raise ers.MissingArgumentError("Missing argument: 'order_id'")
        order = rest.get_order_status(str(order_id), **self._venue_args(args, 'order_id'))
        return {'found': order is not None, 'order': order.to_dict() if order is not None else None}

    def _handle_get_trades(self, args: ArgsObject) -> dict:
        """
        The account's most recent fills, newest first, paginated. Only as much history
        as the page needs is requested from the venue (offset + limit).
        :param args: 'offset' (default 0), 'limit' (default DEFAULT_PAGE_SIZE). Venue extras forwarded.
        :return: {'account': <identity>, 'trades': [Trade.to_dict(), ...]}
        """
        rest = self._require_account_rest()
        req = request_args(args)
        max_count = int(req.get('offset', 0)) + int(req.get('limit', DEFAULT_PAGE_SIZE))
        trades = rest.get_recent_trades(max_count, **self._venue_args(args))
        page = self._routine_paginate(trades, args, DEFAULT_PAGE_SIZE)
        return {'account': rest.account_identity(), 'trades': [t.to_dict() for t in page]}

    def _handle_get_funding_payments(self, args: ArgsObject) -> dict:
        """
        Funding settlements applied to the account in a time window, newest first, paginated.
        :param args: 'start_time' (unix ms; default now - 7 days), 'end_time' (unix ms; default now),
                     'offset' (default 0), 'limit' (default DEFAULT_PAGE_SIZE). Venue extras forwarded.
        :return: {'account': <identity>, 'start_time', 'end_time', 'funding_payments': [FundingPayment.to_dict(), ...]}
        """
        rest = self._require_account_rest()
        req = request_args(args)
        now_ms = int(time.time() * 1000)
        end_time = int(req.get('end_time', now_ms))
        start_time = int(req.get('start_time', end_time - DEFAULT_FUNDING_LOOKBACK_MS))
        if start_time > end_time:
            raise ers.MissingArgumentError("'start_time' must not be after 'end_time'")
        payments = rest.get_funding_payments(start_time, end_time, **self._venue_args(args, 'start_time', 'end_time'))
        page = self._routine_paginate(payments, args, DEFAULT_PAGE_SIZE)
        return {
            'account': rest.account_identity(),
            'start_time': start_time,
            'end_time': end_time,
            'funding_payments': [p.to_dict() for p in page],
        }
