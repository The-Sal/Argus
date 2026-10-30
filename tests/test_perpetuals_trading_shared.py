"""
Unit tests for the venue-agnostic trading control flow in
`argus.perpetuals.shared.trading` (`TradingHandlersMixin`).

These deliberately do NOT go through `HyperLiquidDispatcher`: the point is that the
read-current -> apply -> place -> roll-back machinery and the handler shells work against
a minimal fake venue that implements only the `_trading_*` hooks. The exhaustive
Hyperliquid-specific coverage lives in `tests/test_hyper_exchange.py`; this file pins the
parts Lighter will inherit for free.
"""
from decimal import Decimal

import pytest

from argus._argus_utils import ArgsObject
from argus.perpetuals.shared import errors as ers
from argus.perpetuals.shared._classes import BatchCancelResult, CancelOutcome, PositionLeverage
from argus.perpetuals.shared.trading import TradingHandlersMixin


class _Result:
    """Minimal stand-in for a venue's `OrderPlacementResult` (see the mixin docstring for the contract)."""

    def __init__(self, coin="BTC", ok=True, price="100", error=None):
        self.coin = coin
        self.ok = ok
        self.price = Decimal(price) if price is not None else None
        self.error = error
        self.leverage_report = None

    def to_dict(self):
        out = {"coin": self.coin, "ok": self.ok,
               "price": str(self.price) if self.price is not None else None, "error": self.error}
        if self.leverage_report is not None:
            out.update(self.leverage_report.to_dict())
        return out


class _CancelResult:
    def __init__(self, coin, ids):
        self.coin = coin
        self.canceled_oids = list(ids)

    def to_dict(self):
        return {"coin": self.coin, "canceledOids": list(self.canceled_oids), "errors": []}


class FakeVenueError(Exception):
    """Stands in for a venue's own expected trading error (e.g. HyperLiquidError / LighterError)."""


class FakeVenue(TradingHandlersMixin):
    """A venue with no exchange at all: it just records the calls the mixin makes."""

    _expected_errors = (ers.DispatcherError, FakeVenueError)
    pi = None

    def __init__(self, leverage=None, blocked=False, place_error=None, results=None, batch_size=5):
        self._lev = dict(leverage or {})
        self._blocked = blocked
        self._place_error = place_error
        self._results = results
        self._batch_size = batch_size
        self.calls = []
        self.fatal = []
        self._on_fatal_error = self.fatal.append

    ########################################
    # BaseDispatcher plumbing the mixin expects
    ########################################
    def _read_args(self, args, *accepted):
        request = dict(args.args) if isinstance(args.args, dict) else {}
        unknown = sorted(set(request) - set(accepted))
        if unknown:
            raise ers.MissingArgumentError(f"Unknown argument(s) {unknown}; accepted: {sorted(accepted)}")
        return request

    def _require_order_execution_enabled(self):
        if self._blocked:
            raise ers.OrderExecutionDisabledError("blocked")

    ########################################
    # TradingVenue hooks
    ########################################
    def _trading_leverage_info(self, coin):
        return {"coin": coin, "leverage": {"type": "cross", "value": 20}, "max_leverage": 20,
                "allowed_margin_modes": ["cross", "isolated"]}

    def _trading_read_leverage(self, coin):
        return self._lev.get(coin, PositionLeverage(type="cross", value=20))

    def _trading_set_leverage(self, coin, applied):
        self.calls.append(("set_leverage", coin, applied.type, applied.value))

    def _trading_place_order(self, coin, side, price, size, tif, reduce_only, cloid):
        self.calls.append(("place_order", coin, side, tif, reduce_only, cloid))
        if self._place_error:
            raise self._place_error
        return _Result(coin=coin)

    def _trading_place_orders(self, requests):
        self.calls.append(("place_orders", list(requests)))
        if self._place_error:
            raise self._place_error
        return self._results

    def _trading_cancel(self, coin, kind, identifier):
        self.calls.append(("cancel", coin, kind, identifier))
        return _CancelResult(coin, [identifier])

    def _trading_cancel_many(self, items):
        self.calls.append(("cancel_many", list(items)))
        return BatchCancelResult(outcomes=[CancelOutcome(str(i), c, True) for c, _k, i in items])

    def _trading_cancel_all(self, coin, dex):
        self.calls.append(("cancel_all", coin, dex))
        return {"requested": 0, "canceled": 0, "failed": 0, "failures": [], "failures_truncated": False}

    def _trading_resolve_order_coin(self, kind, identifier):
        return "BTC"

    def _trading_open_orders_index(self):
        return {}

    @staticmethod
    def _trading_is_definitive_failure(error):
        return isinstance(error, (FakeVenueError, ers.DispatcherError))

    def _parse_order_id(self, value):
        if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
            return "id", int(value)
        raise ers.DispatcherError(f"Invalid order_id {value!r}")

    @property
    def _trading_max_batch_size(self):
        return self._batch_size

    # helpers
    def call(self, handler, data):
        return getattr(self, handler)(ArgsObject(sock=None, args=data))


def _venue(**kw):
    return FakeVenue(**kw)


# --- get_leverage shell -------------------------------------------------------

def test_get_leverage_delegates_to_venue_info():
    out = _venue().call("_get_leverage", {"coin": "BTC"})
    assert out["coin"] == "BTC" and out["max_leverage"] == 20


def test_get_leverage_requires_coin():
    with pytest.raises(ers.MissingArgumentError, match="coin"):
        _venue().call("_get_leverage", {})


# --- leverage apply + roll-back ----------------------------------------------

def test_unchanged_leverage_sends_no_update():
    v = _venue()
    out = v.call("_set_leverage", {"coin": "BTC", "leverage": 20})
    assert v.calls == [] and out["leverage"] == 20 and out["previous"] == {"type": "cross", "value": 20}


def test_set_leverage_applies_and_reports_previous():
    v = _venue()
    out = v.call("_set_leverage", {"coin": "BTC", "leverage": 5, "margin_mode": "isolated"})
    assert v.calls == [("set_leverage", "BTC", "isolated", 5)]
    assert out == {"coin": "BTC", "leverage": 5, "margin_mode": "isolated",
                   "previous": {"type": "cross", "value": 20}}


def test_place_order_attaches_leverage_report():
    v = _venue()
    out = v.call("_place_order", {"coin": "BTC", "side": "buy", "price": 100, "size": 2, "leverage": 10})
    assert [c[0] for c in v.calls] == ["set_leverage", "place_order"]
    assert out["leverage"] == {"type": "cross", "value": 10}
    assert out["leverage_changed"] is True and out["leverage_reverted"] is False
    assert out["estimated_initial_margin"] == "20"  # 100 * 2 / 10


def test_venue_rejection_reverts_leverage():
    v = _venue(results=None)
    v._trading_place_order = lambda *a, **k: _Result(coin="BTC", ok=False, error="Insufficient margin")
    out = v.call("_place_order", {"coin": "BTC", "side": "buy", "price": 1, "size": 1, "leverage": 10})
    assert [c[:4] for c in v.calls if c[0] == "set_leverage"] == [
        ("set_leverage", "BTC", "cross", 10), ("set_leverage", "BTC", "cross", 20)]
    assert out["leverage_reverted"] is True and out["error"] == "Insufficient margin"


def test_definitive_exception_reverts_and_reraises():
    v = _venue(place_error=FakeVenueError("rejected"))
    with pytest.raises(FakeVenueError):
        v.call("_place_order", {"coin": "BTC", "side": "buy", "price": 1, "size": 1, "leverage": 10})
    assert v.calls[-1] == ("set_leverage", "BTC", "cross", 20)
    assert v.fatal == []  # an expected error never runs the contingency


def test_ambiguous_exception_keeps_leverage_and_runs_contingency():
    v = _venue(place_error=TimeoutError("read timed out"))
    with pytest.raises(TimeoutError):
        v.call("_place_order", {"coin": "BTC", "side": "buy", "price": 1, "size": 1, "leverage": 10})
    assert [c[0] for c in v.calls] == ["set_leverage", "place_order"]  # order may be live: no revert
    assert len(v.fatal) == 1 and v.fatal[0]["function"] == "place_order"


def test_revert_failure_is_fatal():
    v = _venue(place_error=FakeVenueError("rejected"))

    def flaky_set_leverage(coin, applied):
        if applied.value == 20:
            raise FakeVenueError("cannot revert")
        v.calls.append(("set_leverage", coin, applied.type, applied.value))

    v._trading_set_leverage = flaky_set_leverage
    with pytest.raises(ers.LeverageRevertError):
        v.call("_place_order", {"coin": "BTC", "side": "buy", "price": 1, "size": 1, "leverage": 10})
    assert len(v.fatal) == 1


# --- batch --------------------------------------------------------------------

def test_batch_mismatch_is_rejected():
    v = _venue(results=[_Result(coin="BTC")])  # one result for two requests
    with pytest.raises(ers.DispatcherError, match="cannot attribute"):
        v.call("_place_multiple_orders", {"orders": [
            {"coin": "BTC", "side": "buy", "price": 1, "size": 1, "leverage": 10},
            {"coin": "ETH", "side": "buy", "price": 1, "size": 1, "leverage": 10},
        ]})


def test_batch_cap_enforced():
    v = _venue(batch_size=1)
    with pytest.raises(ers.DispatcherError, match="Too many orders"):
        v.call("_place_multiple_orders", {"orders": [
            {"coin": "BTC", "side": "buy", "price": 1, "size": 1, "leverage": 10},
            {"coin": "ETH", "side": "buy", "price": 1, "size": 1, "leverage": 10},
        ]})


# --- kill switch --------------------------------------------------------------

def test_kill_switch_blocks_placement_but_not_cancels():
    blocked = _venue(blocked=True)
    with pytest.raises(ers.OrderExecutionDisabledError):
        blocked.call("_place_order", {"coin": "BTC", "side": "buy", "price": 1, "size": 1, "leverage": 5})
    assert blocked.calls == []

    cancels_work_while_blocked = _venue(blocked=True)
    cancels_work_while_blocked.call("_cancel_order", {"order_id": 7, "coin": "BTC"})
    assert cancels_work_while_blocked.calls == [("cancel", "BTC", "id", 7)]


def test_cancel_multiple_resolves_coin_with_the_venues_kind_qualified_index_key():
    """Two orders whose ids are the same integer but different KINDS must resolve to their own markets
    when the venue qualifies its index keys (Lighter: order_index vs client_order_index)."""

    class KindVenue(FakeVenue):
        @staticmethod
        def _trading_index_key(kind, identifier):
            return f"{kind}:{identifier}"

        def _trading_open_orders_index(self):
            return {"order_index:5": "BTC", "client_order_index:5": "ETH"}

        def _parse_order_id(self, value):
            return ("client_order_index", 5) if value == "c:5" else ("order_index", int(value))

    venue = KindVenue()
    venue.call("_cancel_multiple_orders", {"orders": [{"order_id": 5}, {"order_id": "c:5"}]})
    (_name, items), = [c for c in venue.calls if c[0] == "cancel_many"]
    assert [(coin, kind) for coin, kind, _i in items] == [("BTC", "order_index"), ("ETH", "client_order_index")]
