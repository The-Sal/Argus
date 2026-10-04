from argus.perpetuals.shared.errors import FatalDispatcherError

class HyperLiquidError(Exception):
    pass

class HyperLiquidDispatcherError(HyperLiquidError):
    pass

class InvalidFunctionError(HyperLiquidDispatcherError):
    pass


class CorrelationIDError(HyperLiquidDispatcherError):
    pass


class ExchangeActionError(HyperLiquidDispatcherError):
    """
    Raised when the `exchange` endpoint rejects a signed action at the envelope level
    (``{"status": "err", ...}``) -- e.g. an invalid signature, a stale nonce, or a
    venue-level rejection of the whole submission. This is distinct from a per-order
    outcome reported *inside* an otherwise-ok order response (insufficient margin for
    one order), which `OrderPlacementResult.error` carries instead.
    """
    pass


class UnsupportedAccountModeError(HyperLiquidError):
    """
    Raised when `userAbstraction` reports an account mode this client does not know
    how to read a balance for. Failing loudly beats guessing: a wrong guess reports a
    funded account as empty (or the reverse), which is worse than an error on a trading API.
    """
    pass


class LeverageRevertError(FatalDispatcherError):
    """
    An order failed AND rolling the coin's leverage back to its previous value failed too, so the coin may be
    left on a leverage the caller did not ask for (which silently moves the liquidation price of any position
    on it). A `FatalDispatcherError` so the dispatcher's contingency action runs; the caller must inspect
    `get_leverage` and repair with `set_leverage`.
    """
    pass
