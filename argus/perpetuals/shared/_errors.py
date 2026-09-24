
class DispatcherError(Exception):
    pass

class InvalidFunctionError(DispatcherError):
    pass


class CorrelationIDError(DispatcherError):
    pass


class MissingArgumentError(DispatcherError):
    pass

class PacketTooLargeError(DispatcherError):
    pass

class RoutingDisabledError(DispatcherError):
    pass


class InvalidCoinError(DispatcherError):
    pass


class FatalDispatcherError(DispatcherError):
    """
    Base class for errors that mean the running code itself is misconfigured or
    incomplete -- e.g. an abstract method a subclass was required to override was
    invoked anyway. This is NOT a transient/retryable runtime failure, so call sites
    must let it propagate (logging via pi.throw_fuss first is fine) rather than
    catching it in a broad `except Exception` retry/recovery block -- doing so would
    silently mask a real bug as a flaky failure and retry/disable-routing around it.
    Future "this should be impossible" errors should subclass this directly.
    """
    pass


class AbstractMethodNotImplementedError(FatalDispatcherError, NotImplementedError):
    """
    Raised instead of a bare NotImplementedError when an abstract method that a
    subclass MUST override is invoked without one (e.g. a feature flag was enabled
    that depends on the override). Still an instance of NotImplementedError for
    idiomatic isinstance checks, but also a FatalDispatcherError so retry/recovery
    logic can specifically exempt it instead of swallowing it.
    """
    pass

class AccountNotConfiguredError(DispatcherError):
    """
    Raised by an account-data action when the dispatcher has no account to query
    (e.g. LIGHTER_ACCOUNT_INDEX unset) or the venue needs an auth token for that
    particular read and none was configured (e.g. LIGHTER_AUTH_TOKEN). This is a
    per-request client-facing error, not a FatalDispatcherError: market-data actions
    keep working on a dispatcher whose account side is unconfigured.
    """
    pass
