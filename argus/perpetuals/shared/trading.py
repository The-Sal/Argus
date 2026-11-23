"""
Venue-agnostic trading control flow for Argus v2's perpetuals dispatchers.

This module is to *trading* what `argus.perpetuals.shared.account` is to account
data: the one place that defines the read-current -> apply -> place -> roll-back
machinery, so the dispatcher-facing handlers (`get_leverage`, `set_leverage`,
`place_order`, `place_multiple_orders`, `cancel_order`, `cancel_multiple_orders`,
`cancel_all_orders`) are written once and shared by `HyperLiquidDispatcher` and
`LighterDispatcher`.

`get_leverage`'s *shape* is venue-specific (each venue reports different sizing/margin
fields), so the handler here is only the argument shell: it calls the venue's
`_trading_leverage_info(coin)` for the payload.

Why this is shareable
---------------------
Every venue we support persists leverage per market, and that persistence is the
only reason `place_order` takes a leverage at all: the order has to be opened under
a known margin mode, and a failed order must not leave the market on a leverage the
caller did not ask for (which silently moves the liquidation price of any position
on it). The algorithm is identical everywhere:

  1. read the market's CURRENT leverage (also the roll-back target);
  2. apply the caller's leverage only if it differs from the current one;
  3. submit the order;
  4. on a DEFINITIVE failure (a venue rejection, i.e. the order was not accepted),
     roll every changed market back to its previous leverage;
     "Definitive" means rejected SYNCHRONOUSLY. A venue that accepts an order asynchronously (Lighter's
     `sendTx` answers 200 and the sequencer may reject later) cannot trigger this roll-back for a late
     rejection; its docs say so and the account stream is how a client learns of it.
  5. on an AMBIGUOUS failure (a timeout mid-POST), leave leverage alone -- the order
     may be live and rolling leverage back under it would be wrong.
  6. if the roll-back itself fails, raise `LeverageRevertError` (a
     `FatalDispatcherError`) so the dispatcher's contingency runs.

What a venue must provide
-------------------------
`TradingHandlersMixin` talks to its host through a small set of `_trading_*` methods
(the `TradingVenue` protocol below). Everything venue-specific -- how a market is
named, how its current leverage is read, how an order is signed and submitted, how
an order id is parsed, how a cancel-all is performed -- lives in those methods. The
mixin owns only the cross-venue control flow and the wire-facing handler shapes.

The mixin also expects the host to be a `BaseDispatcher` (for `self._read_args`,
`self._require_order_execution_enabled`, `self._on_fatal_error`, `self.pi` and
`self.account_rest`). The host's `_expected_errors` must include the venue's own
trading error type so ordinary rejections do not trip the contingency.
"""
from argus._argus_utils import ArgsObject
from argus.perpetuals.shared import errors as ers
from argus.perpetuals.shared import fatal_decorator
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable

from argus.perpetuals.shared._classes import (
    BatchCancelResult,
    CancelOutcome,
    LeverageChange,
    LeverageSetting,
    OrderLeverage,
    OrderRequest,
    PositionLeverage,
)


@runtime_checkable
class TradingVenue(Protocol):
    """
    The venue-specific surface `TradingHandlersMixin` drives. A dispatcher implements these as thin
    `_trading_*` methods that delegate to its exchange client and REST client; the mixin never imports a
    venue module.

    Every method raises only the venue's own error types (or shared `DispatcherError`s); the host's
    `_expected_errors` must therefore include the venue's trading error base.
    """

    def _trading_leverage_info(self, coin: str) -> dict:
        """The venue-specific `get_leverage` payload for `coin` (at least a 'leverage' entry)."""
        ...

    def _trading_read_leverage(self, coin: str) -> PositionLeverage:
        """The leverage currently in force for the account on `coin` (the roll-back target)."""
        ...

    def _trading_set_leverage(self, coin: str, applied: PositionLeverage) -> None:
        """Sign and submit the leverage update putting `coin` on `applied`."""
        ...

    def _trading_place_order(self, coin: str, side: str, price: Any, size: Any, tif: str,
                             reduce_only: bool, cloid: Optional[Any]):
        """Sign and submit one limit order; returns the venue's `OrderPlacementResult`."""
        ...

    def _trading_place_orders(self, requests: List[OrderRequest]) -> list:
        """Sign and submit several limit orders in one action; results are positional."""
        ...

    def _trading_cancel(self, coin: str, kind: str, identifier: Any):
        """Cancel one order; `kind` is whatever `_parse_order_id` classified it as."""
        ...

    def _trading_cancel_many(self, items: List[tuple]) -> BatchCancelResult:
        """Cancel several orders; `items` are `(coin, kind, identifier)` triples."""
        ...

    def _trading_cancel_all(self, coin: Optional[str], dex: Optional[str]) -> dict:
        """Cancel every resting order (optionally one coin's / one ledger's); returns a summary dict."""
        ...

    def _trading_resolve_order_coin(self, kind: str, identifier: Any) -> Optional[str]:
        """Resolve the market of an order from the venue, or None if the venue does not know it."""
        ...

    def _trading_open_orders_index(self) -> Dict[str, str]:
        """A `{str(cancellable_id): coin}` map over the open orders, used to resolve coins for a batch
        cancel without a per-order lookup. Must include every id form the venue's `_parse_order_id` can
        return (a venue order id and, where cancellable, a client id)."""
        ...

    def _trading_index_key(self, kind: str, identifier: Any) -> str:
        """The key under which `_trading_open_orders_index` stores an order for this `(kind, identifier)`.
        Optional: the default is `str(identifier)`; override when different kinds can collide."""
        ...

    def _trading_is_definitive_failure(self, error: Exception) -> bool:
        """True when `error` proves the order was NOT accepted, so rolling leverage back is safe."""
        ...

    @staticmethod
    def _parse_order_id(value: Any) -> Tuple[str, Any]:
        """Classify a caller-supplied order id into `(kind, identifier)`. Venue-specific: a client id is a
        0x-hex string on Hyperliquid but an integer on Lighter, so each venue defines its own grammar. The
        `kind` is passed back to `_trading_cancel` / `_trading_cancel_many`."""
        ...

    @property
    def _trading_max_batch_size(self) -> int:
        """The venue's cap on orders per batch action."""
        ...


class TradingHandlersMixin:
    """
    Dispatcher-side trading actions, written once for every venue (see module docstring).

    Subclasses merge the seven handlers into their routing table and implement the `_trading_*` hooks of
    `TradingVenue`. A venue's order-placement result objects must expose:
      - `.ok` (bool: the venue accepted the order),
      - `.coin` (the market the result belongs to),
      - `.price` (the submitted limit price, or None),
      - `.to_dict()`, and
      - a writable `.leverage_report` (the mixin attaches an `OrderLeverage` to each result).
    A venue's cancel result must expose `.to_dict()`; a batch cancel result `.outcomes` and `.to_dict()`.
    """

    if TYPE_CHECKING:
        # Provided by the host `BaseDispatcher`; declared here for type checkers only (a real stub would
        # shadow the host's implementation in the MRO).
        def _read_args(self, args: ArgsObject, *accepted: str) -> Dict[str, Any]: ...

        def _require_order_execution_enabled(self) -> None: ...

    ########################################
    # Venue hooks (must be overridden; see TradingVenue)
    ########################################

    def _trading_leverage_info(self, coin: str) -> dict:
        raise NotImplementedError("_trading_leverage_info is not implemented by this venue.")

    def _trading_read_leverage(self, coin: str) -> PositionLeverage:
        raise NotImplementedError("_trading_read_leverage is not implemented by this venue.")

    def _trading_set_leverage(self, coin: str, applied: PositionLeverage) -> None:
        raise NotImplementedError("_trading_set_leverage is not implemented by this venue.")

    def _trading_place_order(self, coin, side, price, size, tif, reduce_only, cloid) -> Any:
        raise NotImplementedError("_trading_place_order is not implemented by this venue.")

    def _trading_place_orders(self, requests: List[OrderRequest]) -> List[Any]:
        raise NotImplementedError("_trading_place_orders is not implemented by this venue.")

    def _trading_cancel(self, coin: str, kind: str, identifier) -> Any:
        raise NotImplementedError("_trading_cancel is not implemented by this venue.")

    def _trading_cancel_many(self, items: List[tuple]) -> BatchCancelResult:
        raise NotImplementedError("_trading_cancel_many is not implemented by this venue.")

    def _trading_cancel_all(self, coin: Optional[str], dex: Optional[str]) -> dict:
        raise NotImplementedError("_trading_cancel_all is not implemented by this venue.")

    def _trading_resolve_order_coin(self, kind: str, identifier) -> Optional[str]:
        raise NotImplementedError("_trading_resolve_order_coin is not implemented by this venue.")

    def _trading_open_orders_index(self) -> Dict[str, str]:
        raise NotImplementedError("_trading_open_orders_index is not implemented by this venue.")

    @staticmethod
    def _trading_index_key(kind: str, identifier: Any) -> str:
        return str(identifier)

    @staticmethod
    def _trading_is_definitive_failure(error: Exception) -> bool:
        raise NotImplementedError("_trading_is_definitive_failure is not implemented by this venue.")

    @staticmethod
    def _parse_order_id(value: Any) -> Tuple[str, Any]:
        raise NotImplementedError("_parse_order_id is not implemented by this venue.")

    @property
    def _trading_max_batch_size(self) -> int:
        raise NotImplementedError("_trading_max_batch_size is not implemented by this venue.")

    #: Populated by BaseDispatcher; used only for logging. The fallback keeps dispatchable hosts that do not
    #: set it (e.g. a lightweight test double) working.
    pi: Any = None

    def _trading_log(self):
        from argus.perpetuals.shared import PrintInterface
        return getattr(self, "pi", None) or PrintInterface("Trading")

    ########################################
    # Leverage read / apply / roll-back
    ########################################

    def _apply_leverage(self, wanted: Dict[str, LeverageSetting]) -> Dict[str, LeverageChange]:
        """
        Bring each coin in `wanted` to the requested leverage and return what that did. For every coin the
        CURRENT leverage is read first (that is the roll-back target; if the read fails nothing has changed and
        the call fails); the signed update is only sent when the coin is not already there. If any coin fails
        part-way, the coins already changed are rolled back before the error propagates.
        """
        changes: Dict[str, LeverageChange] = {}
        try:
            for coin, setting in wanted.items():
                previous = self._trading_read_leverage(coin)
                applied = setting.resolve(previous)
                change = LeverageChange(coin=coin, previous=previous, applied=applied)
                if change.changed:
                    self._trading_set_leverage(coin, applied)
                changes[coin] = change
        except Exception as e:
            # Roll the coins already changed back before propagating. If the roll-back itself fails,
            # `_revert_or_raise` escalates to `LeverageRevertError` (so the contingency runs) instead of
            # silently leaving a market on a leverage the caller did not ask for.
            self._revert_or_raise(changes.values(), e)
            raise
        return changes

    def _revert_leverage(self, changes) -> Dict[str, str]:
        """Best-effort roll back of every changed coin to its previous leverage. Returns {coin: error} for the
        coins that could NOT be rolled back (empty when everything is restored)."""
        failed: Dict[str, str] = {}
        for change in changes:
            if not change.changed:
                continue
            try:
                self._trading_set_leverage(change.coin, change.previous)
            except Exception as e:
                self._trading_log().prt(
                    f"Could not roll back leverage on {change.coin} to {change.previous.to_dict()}: {e}"
                )
                failed[change.coin] = str(e)
        return failed

    def _revert_or_raise(self, changes, cause: Exception) -> None:
        """Roll back after a DEFINITIVE order failure; if the roll-back itself fails raise
        `LeverageRevertError` (which trips the contingency) naming the coins left on the wrong leverage."""
        failed = self._revert_leverage(changes)
        if failed:
            raise ers.LeverageRevertError(
                f"Order failed ({cause}) and leverage could not be restored for {failed}; "
                f"check get_leverage and repair with set_leverage"
            ) from cause

    def _definitive_failure(self, error: Exception) -> bool:
        """True when `error` proves the order was NOT accepted (rejected before sending, or an err envelope).
        Anything else (a timeout mid-POST, an unparsable reply) leaves the order's fate unknown: rolling the
        leverage back under a possibly-live order would be wrong, so we leave it and let the contingency run.

        A `NotImplementedError` (a venue hook that was never wired up) always counts as definitive: nothing
        was submitted, so rolling leverage back is safe and desirable."""
        if isinstance(error, NotImplementedError):
            return True
        return self._trading_is_definitive_failure(error)

    @staticmethod
    def _leverage_report(change: LeverageChange, result, size: Any, reduce_only: bool,
                         reverted: bool) -> OrderLeverage:
        """The `OrderLeverage` for one finished order (see its docstring for the margin estimate)."""
        from decimal import Decimal
        margin = None
        if getattr(result, "ok", False) and not reduce_only and getattr(result, "price", None) is not None:
            margin = result.price * Decimal(str(size)) / change.applied.value
        return OrderLeverage(
            leverage=change.previous if reverted else change.applied,
            previous=change.previous,
            changed=change.changed,
            reverted=reverted,
            estimated_initial_margin=None if reverted else margin,
        )

    ########################################
    # Handlers
    ########################################

    @fatal_decorator('get_leverage')
    def _get_leverage(self, args: ArgsObject) -> dict:
        """
        The leverage in force for the account on one market, and what it may be set to. Read-only, so the kill
        switch does not apply.

        :param args: Accepts 'coin' (required, e.g. "BTC").
        :return: The venue's payload -- see the venue's `_trading_leverage_info` for the exact fields
            (typically 'coin', 'leverage': {type, value}, 'max_leverage', 'allowed_margin_modes', ...).
        """
        data = self._read_args(args, 'coin')
        if data.get('coin') is None:
            raise ers.MissingArgumentError("Missing required argument 'coin'")
        return self._trading_leverage_info(data['coin'])

    @fatal_decorator('set_leverage')
    def _set_leverage(self, args: ArgsObject) -> dict:
        """
        Set the leverage (and margin mode) used for NEW positions on one coin. Blocked by the kill switch.

        :param args: Accepts 'coin' (required), 'leverage' (required integer, 1..the coin's max) and
            'margin_mode' (optional "cross" | "isolated"; omitted keeps the coin's current mode).
        :return: {'coin', 'leverage', 'margin_mode', 'previous': {type, value}}. Lowering leverage or
            switching mode with an open position may be rejected by the venue (the error says so).
        """
        self._require_order_execution_enabled()
        data = self._read_args(args, 'coin', 'leverage', 'margin_mode')
        for required in ('coin', 'leverage'):
            if data.get(required) is None:
                raise ers.MissingArgumentError(f"Missing required argument {required!r}")
        setting = LeverageSetting.from_args(data['leverage'], data.get('margin_mode'))
        change = self._apply_leverage({data['coin']: setting})[data['coin']]
        return {
            'coin': change.coin,
            'leverage': change.applied.value,
            'margin_mode': change.applied.type,
            'previous': {'type': change.previous.type, 'value': change.previous.value},
        }

    @fatal_decorator('place_order')
    def _place_order(self, args: ArgsObject) -> dict:
        """
        Place a single limit order on the account at an explicit leverage. Blocked by the kill switch
        (checked before anything else).

        Leverage is per market, so the order names it: the market's current leverage is read, the requested one
        applied (one signed action, skipped when already in force), then the order placed. If the venue rejects
        the order, or the submission definitively fails, a changed leverage is ROLLED BACK so a failed order
        never leaves a position's liquidation price moved. If the submission fails ambiguously (e.g. a timeout)
        the order may be live, so leverage is left alone and the fatal contingency runs.

        :param args: Accepts 'coin' (required, e.g. "BTC"), 'side' (required, "buy" or "sell"), 'price'
            (required, limit price), 'size' (required, size in coins), 'leverage' (required integer),
            'margin_mode' (optional "cross" | "isolated", default: the coin's current mode), 'order_type'
            (optional, "GTC" | "IOC" | "ALO", default "GTC"), 'reduce_only' (optional bool, default False),
            'cloid' (optional client order id, in the venue's own format).
        :return: {'coin', <venue result fields>, 'leverage', 'margin_mode', 'previous_leverage',
            'leverage_changed', 'leverage_reverted', 'estimated_initial_margin', ['leverage_revert_error']}.
            A venue-level rejection comes back with the result's 'error' set instead of a packet error.
        """
        self._require_order_execution_enabled()
        data = self._read_args(
            args, 'coin', 'side', 'price', 'size', 'leverage', 'margin_mode', 'order_type', 'reduce_only', 'cloid'
        )
        for required in ('coin', 'side', 'price', 'size', 'leverage'):
            if data.get(required) is None:
                raise ers.MissingArgumentError(f"Missing required argument {required!r}")
        coin = data['coin']
        order_type = str(data.get('order_type') or 'GTC').upper()
        tif = {'GTC': 'Gtc', 'IOC': 'Ioc', 'ALO': 'Alo'}.get(order_type)
        if tif is None:
            raise ers.MissingArgumentError(f"Invalid order_type {order_type!r}: expected GTC, IOC or ALO")
        setting = LeverageSetting.from_args(data['leverage'], data.get('margin_mode'))
        reduce_only = bool(data.get('reduce_only') or False)

        changes = self._apply_leverage({coin: setting})
        change = changes[coin]
        try:
            result = self._trading_place_order(
                coin, str(data['side']), data['price'], data['size'], tif, reduce_only, data.get('cloid'),
            )
        except Exception as e:
            if self._definitive_failure(e):
                self._revert_or_raise(changes.values(), e)
            raise
        if not result.ok and change.changed:
            failed = self._revert_leverage([change])
            reverted = not failed
            report = self._leverage_report(change, result, data['size'], reduce_only, reverted)
            if failed:
                report.revert_error = failed[coin]
                self._trading_log().throw_fuss(
                    f"Order rejected and leverage on {coin} could not be restored: {failed}",
                    title="Leverage Revert Failed", notify=True,
                )
        else:
            report = self._leverage_report(change, result, data['size'], reduce_only, False)
        result.leverage_report = report
        return result.to_dict()

    @fatal_decorator('place_multiple_orders')
    def _place_multiple_orders(self, args: ArgsObject) -> dict:
        """
        Place several limit orders in ONE signed action. Blocked by the kill switch.

        Every order carries its own 'leverage' (required) and optional 'margin_mode'; leverage is per market, so
        two orders on the same market must agree. Everything is validated before any leverage is changed or
        anything is signed (size cap, fields, duplicate client ids, per-market consistency). Leverage is then
        applied per distinct market, the batch submitted, and any market on which NO order was accepted is
        rolled back to its previous leverage (a market with at least one accepted order keeps the new one, since
        the resting order was opened under it). Same ambiguity rule as `place_order` for non-definitive errors.

        :param args: Accepts 'orders': a non-empty list (at most the venue's batch cap) of
            {coin, side, price, size, leverage, margin_mode?, order_type?, reduce_only?, cloid?}.
        :return: {'results': [<place_order result>, ...] (index i answers orders[i]), 'ok_count',
            'error_count'}. A venue rejection of one order is that result's 'error', not a packet error.
        """
        self._require_order_execution_enabled()
        data = self._read_args(args, 'orders')
        raw_orders = data.get('orders')
        max_batch = self._trading_max_batch_size
        if not isinstance(raw_orders, list) or not raw_orders:
            raise ers.MissingArgumentError("'orders' must be a non-empty list")
        if len(raw_orders) > max_batch:
            raise ers.DispatcherError(f"Too many orders: {len(raw_orders)} > {max_batch}")

        requests: List[OrderRequest] = []
        wanted: Dict[str, LeverageSetting] = {}
        for raw in raw_orders:
            if not isinstance(raw, dict):
                raise ers.MissingArgumentError("Each order must be an object")
            if raw.get('leverage') is None:
                raise ers.MissingArgumentError("Missing required order field 'leverage'")
            order_fields = {k: v for k, v in raw.items() if k not in ('leverage', 'margin_mode')}
            request = OrderRequest.from_dict(order_fields)
            setting = LeverageSetting.from_args(raw['leverage'], raw.get('margin_mode'))
            if wanted.setdefault(request.coin, setting) != setting:
                raise ers.DispatcherError(
                    f"Orders on {request.coin} disagree on leverage/margin_mode; leverage is per market"
                )
            requests.append(request)
        cloids = [r.cloid for r in requests if r.cloid is not None]
        if len(set(cloids)) != len(cloids):
            raise ers.DispatcherError("Duplicate cloid within the batch")

        changes = self._apply_leverage(wanted)
        try:
            results = self._trading_place_orders(requests)
        except Exception as e:
            if self._definitive_failure(e):
                self._revert_or_raise(changes.values(), e)
            raise
        # A batch response must answer every request positionally; a venue that returns a different count
        # has left some orders' fates unknown, so refuse to report a half-attributed result. Nothing is
        # rolled back here: an order may be live, so this is an ambiguous failure (contingency, no revert).
        if len(results) != len(requests):
            raise ers.DispatcherError(
                f"Batch returned {len(results)} results for {len(requests)} orders; cannot attribute results"
            )

        accepted = {r.coin for r in results if r.ok}
        to_revert = [c for coin, c in changes.items() if coin not in accepted]
        failed = self._revert_leverage(to_revert)
        reverted_coins = {c.coin for c in to_revert if c.changed and c.coin not in failed}
        if failed:
            self._trading_log().throw_fuss(
                f"Orders rejected and leverage could not be restored: {failed}",
                title="Leverage Revert Failed", notify=True,
            )
        for request, result in zip(requests, results):
            change = changes[request.coin]
            report = self._leverage_report(change, result, request.size, request.reduce_only,
                                           change.coin in reverted_coins)
            if request.coin in failed:
                report.revert_error = failed[request.coin]
            result.leverage_report = report
        ok_count = sum(1 for r in results if r.ok)
        return {
            'results': [r.to_dict() for r in results],
            'ok_count': ok_count,
            'error_count': len(results) - ok_count,
        }

    @fatal_decorator('cancel_order')
    def _cancel_order(self, args: ArgsObject) -> dict:
        """
        Cancel one order by a venue id or a client id (the accepted forms are the venue's; see
        `_parse_order_id`).

        :param args: Accepts 'order_id' (required) and an optional 'coin'. When 'coin' is omitted it is
            resolved from the order itself via the venue, which also fails cleanly if the order no longer
            exists. Note the resolution costs one extra read per cancel.
        :return: The venue result's `to_dict()` -- the venue reports per-id statuses, so an id it could not
            cancel (already filled/canceled, or unknown) appears as an error; callers must check for that
            rather than assume a submitted cancel worked.
        """
        data = self._read_args(args, 'order_id', 'coin')
        if data.get('order_id') is None:
            raise ers.MissingArgumentError("Missing required argument 'order_id'")
        kind, identifier = self._parse_order_id(data['order_id'])
        coin = data.get('coin')
        if coin is None:
            coin = self._trading_resolve_order_coin(kind, identifier)
            if coin is None:
                raise ers.DispatcherError(
                    f"Order {identifier} not found (and no 'coin' given to skip the lookup)"
                )
        result = self._trading_cancel(coin, kind, identifier)
        return result.to_dict()

    @fatal_decorator('cancel_multiple_orders')
    def _cancel_multiple_orders(self, args: ArgsObject) -> dict:
        """
        Cancel several orders by venue id or client id. NOT blocked by the kill switch (cancels only reduce
        risk).

        :param args: Accepts 'orders': a non-empty list of {order_id, coin?}. When every item has a 'coin' no
            lookup is needed; when any lacks one, ONE read of the open orders resolves them (much cheaper than
            a per-order lookup), and an id not among the open orders is reported as an error outcome without
            calling the venue.
        :return: {'outcomes': [{order_id, coin, ok, error}, ...] in request order, 'ok_count', 'error_count'}.
            Partial success is normal; an order that filled in the meantime is an item-level error.
        """
        data = self._read_args(args, 'orders')
        raw_orders = data.get('orders')
        if not isinstance(raw_orders, list) or not raw_orders:
            raise ers.MissingArgumentError("'orders' must be a non-empty list")
        parsed = []  # (order_id as given, kind, identifier, coin or None)
        for raw in raw_orders:
            if not isinstance(raw, dict):
                raise ers.MissingArgumentError("Each order must be an object with an 'order_id'")
            unknown = sorted(set(raw) - {'order_id', 'coin'})
            if unknown:
                raise ers.MissingArgumentError(
                    f"Unknown order field(s) {unknown}; accepted: ['coin', 'order_id']")
            if raw.get('order_id') is None:
                raise ers.MissingArgumentError("Missing required order field 'order_id'")
            kind, identifier = self._parse_order_id(raw['order_id'])
            parsed.append((raw['order_id'], kind, identifier, raw.get('coin')))

        coin_by_id: Dict[str, str] = {}
        if any(p[3] is None for p in parsed):
            coin_by_id = self._trading_open_orders_index()

        outcomes: List[Optional[CancelOutcome]] = [None] * len(parsed)
        sendable = []  # (index, (coin, kind, identifier))
        for i, (original, kind, identifier, coin) in enumerate(parsed):
            coin = coin or coin_by_id.get(self._trading_index_key(kind, identifier))
            if coin is None:
                outcomes[i] = CancelOutcome(str(original), None, False, "not found among open orders")
            else:
                sendable.append((i, (coin, kind, identifier)))
        if sendable:
            sent = self._trading_cancel_many([item for _, item in sendable])
            for (i, _), outcome in zip(sendable, sent.outcomes):
                outcomes[i] = outcome
        return BatchCancelResult(outcomes=outcomes).to_dict()

    @fatal_decorator('cancel_all_orders')
    def _cancel_all_orders(self, args: ArgsObject) -> dict:
        """
        Cancel every resting order (optionally only one market's / one ledger's). NOT blocked by the kill
        switch.

        :param args: Accepts 'coin' (optional, only that market's orders) and 'dex' (optional: "" = primary
            ledger, else a sub-ledger name; omitted = every ledger). Venues without sub-ledgers reject a
            non-empty 'dex' as usual.
        :return: {'requested', 'canceled', 'failed', 'failures': [... up to 50], 'failures_truncated'} --
            compact on purpose (up to ~1000 orders must fit Protocol 1's byte cap). 'failed' > 0 means some
            orders may still be resting: check get_orders.
        """
        data = self._read_args(args, 'coin', 'dex')
        return self._trading_cancel_all(data.get('coin'), data.get('dex'))
