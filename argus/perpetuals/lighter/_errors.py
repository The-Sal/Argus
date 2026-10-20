"""
Lighter dispatcher/venue error taxonomy.

`LighterError` is a `DispatcherError` (unlike Hyperliquid's standalone `HyperLiquidError`)
so that every Lighter failure is an "expected" trading error by default and does not trip
`fatal_decorator`'s contingency. The dispatcher still lists it explicitly in
`_expected_errors` for clarity.

`SignerError` and `ExchangeActionError` are the two failure modes of a signed write:
  - `SignerError` -- the native signer rejected the action locally (bad parameters, a
    malformed key, a nonce/expiry problem). Nothing was submitted, so rolling leverage back
    is always safe.
  - `ExchangeActionError` -- `sendTx`/`sendTxBatch` answered with a non-OK code. Per
    Lighter's contract a `code: 200` means "accepted, not necessarily executed"; anything
    else is a definitive venue-side rejection.
Neither is a `FatalDispatcherError`: both are ordinary per-request failures.
"""
from argus.perpetuals.shared.errors import DispatcherError


class LighterError(DispatcherError):
    """Base class for every Lighter dispatcher/venue error."""
    pass


class LighterDispatcherError(LighterError):
    """Base for Lighter-dispatcher-local errors (mirrors HyperLiquidDispatcherError)."""
    pass


class InvalidFunctionError(LighterDispatcherError):
    pass


class CorrelationIDError(LighterDispatcherError):
    pass


class SignerError(LighterError):
    """
    Raised when the native signer rejects an action locally (before any network call). A
    definitive failure: the action was not submitted, so leverage roll-back is safe.
    """
    pass


class ExchangeActionError(LighterError):
    """
    Raised when `sendTx`/`sendTxBatch` answers with a non-OK code. Distinct from a
    per-order outcome: Lighter has no synchronous per-order status (see the exchange
    module docstring), so this only covers envelope-level rejections.
    """
    pass


class RateLimitError(ExchangeActionError):
    """
    Lighter answered HTTP 429. The request was refused before processing, so (like any other
    envelope rejection) it is a definitive failure: nothing was submitted and leverage roll-back
    is safe. Standard accounts get only 60 `sendTx`/`sendTxBatch` requests per minute.
    """
    pass
