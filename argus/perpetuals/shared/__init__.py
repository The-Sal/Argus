import os
import re
import sys
import copy
import json
import time
import atexit
import random
import socket
import functools
import threading
import traceback
from argus import protocol
from utils3 import runAsThread
from collections.abc import Mapping
from argus.perpetuals.shared import account
from utils3.networking.sockets import Server
from datetime import datetime, timedelta, UTC
from typing import Callable, Any, Generic, TypeVar
from argus.perpetuals.shared import _classes as cls, errors as ers
from argus.perpetuals.shared.account import AccountHandlersMixin, BaseDispatcherCompatibleAccountRest
from argus.perpetuals.shared._classes import P2OrderBookConvertClass, OutboundMessage, NewFundingRate, AccountUpdate
from argus._argus_utils import Introspective, CorrelationIDChecker, RoutingHelper, ArgsObject, Notification, throw_fuss


class PrintInterface:
    """
    A class that wraps logging + printing + other forms
    of communication into one class
    """

    def __init__(self, name):
        self.name = name
        self.nf = Notification()

    def prt(self, *args, **kwargs):
        print(f"[{self.name}]", *args, **kwargs)

    def notify(self, *args):
        msg = " ".join(args)
        self.nf.notify(title=self.name, message=msg)

    def throw_fuss(self, msg, title=None, boarder="=", notify=True):
        if title is None:
            title = self.name
        throw_fuss(
            msg=msg,
            title=title,
            notify=notify,
            boarder=boarder
        )


_p = PrintInterface("BaseDispatcher")

T = TypeVar("T")


class LockedState(Generic[T]):
    """
    A lock-guarded, atomically-swappable reference. The lock only protects
    reassigning/reading `value` itself -- it does not make `value` immutable.

    For a truly immutable `value` (bool, int, float, str, None, or a tuple of
    such -- nothing mutable through a reference like a list, dict, or set),
    this is deep-copy-safe: __copy__ / __deepcopy__ only shallow-copy `value`,
    so callers of BaseDispatcher.state can't mutate this object's live
    internal state through the "copy" they were handed.

    For a referential `value` (e.g. PerpetualsIndex), that guarantee does not
    hold: `.value` returns the live object on every access (not a copy), so
    callers must not mutate it in place -- treat it as read-only after
    construction. Replace it wholesale via the `value` setter instead.
    """

    def __init__(self, value: T):
        self._value = value
        self.lock = threading.Lock()

    @property
    def value(self) -> T:
        """
        Do not use inside context managers.
        :return:
        """
        with self.lock:
            return self._value

    @value.setter
    def value(self, value: T):
        with self.lock:
            self._value = value

    def __bool__(self):
        return bool(self.value)

    def __copy__(self):
        """
        Remove reference to this object.
        :return:
        """
        return copy.copy(self.value)

    def __deepcopy__(self, memo):
        """
        Remove reference to this object.

        This is only a shallow copy of `value`, not a true deep copy -- see
        the class docstring for why `value` must be immutable/non-referential
        so that this is safe.
        :return:
        """
        _ = memo
        return copy.copy(self.value)


def fatal_decorator(function_name: str):
    """
    Wrap a dispatcher HANDLER (a method taking `self`) like Polymarket's `fatal_decorator`: if anything
    in the stack raises an error the handler was not ready for, run the dispatcher's contingency action
    (`self._on_fatal_error`) and then re-raise so the client still gets its normal error response.

    "Ready for" means the dispatcher's expected errors (`self._expected_errors`, by default every
    `DispatcherError` except `FatalDispatcherError`): bad arguments, unknown coins, a venue rejecting an
    action, the kill switch, ... Those are ordinary request failures and must not trip the contingency.
    Anything else -- a timeout mid-order, a parse failure, a bug -- may have left the account in a state
    we cannot reason about (an order that may or may not exist, leverage half-changed), which is exactly
    when the contingency has to run. The contingency itself never raises.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            # Audit trail for everything that can move money or risk (reads are not audited).
            audit = not function_name.startswith("get_")
            logs = getattr(self, "logs", None) if audit else None
            request = args[0] if args else None
            corr_id = getattr(request, "correlation_id", None)
            if logs is not None:
                try:
                    logs.info(f"REQUEST {function_name} corr_id={corr_id} "
                              f"args={json.dumps(getattr(request, 'args', None), default=str)}")
                except Exception:
                    pass
            try:
                result = func(self, *args, **kwargs)
                if logs is not None:
                    try:
                        logs.info(f"RESULT {function_name} corr_id={corr_id} ok "
                                  f"{json.dumps(result, default=str)[:1500]}")
                    except Exception:
                        pass
                return result
            except Exception as e:
                expected = getattr(self, "_expected_errors", (ers.DispatcherError,))
                is_fatal = not isinstance(e, expected) or isinstance(e, ers.FatalDispatcherError)
                if logs is not None:
                    try:
                        if is_fatal:
                            logs.critical(f"RESULT {function_name} corr_id={corr_id} UNEXPECTED {e!r}: the order/"
                                          f"leverage state may be uncertain; reconcile with get_orders/get_positions")
                        else:
                            logs.warning(f"RESULT {function_name} corr_id={corr_id} rejected "
                                         f"{type(e).__name__}: {e}")
                    except Exception:
                        pass
                if is_fatal:
                    try:
                        self._on_fatal_error({
                            "function": function_name,
                            "exception": e,
                            "traceback": traceback.format_exc(),
                        })
                    except Exception:
                        traceback.print_exc()
                raise

        return wrapper

    return decorator


_SECRET_NAME_RE = re.compile(r"(private|secret|token|pass|cookie|auth|signature|seed|mnemonic|api_?key|^key$|_key$)", re.I)


def scrub(value: Any, _depth: int = 0) -> Any:
    """
    A copy of `value` that is safe to write to a log: any dict entry whose key looks like a credential
    (private key, token, password, cookie, signature, ...) has its value replaced by "<redacted>", recursively.
    Long strings are truncated, so a pasted blob cannot flood the log.
    """
    if _depth > 8:
        return "<too deep>"
    if isinstance(value, Mapping):
        return {k: ("<redacted>" if isinstance(k, str) and _SECRET_NAME_RE.search(k) else scrub(v, _depth + 1))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v, _depth + 1) for v in value]
    if isinstance(value, str) and len(value) > 300:
        return value[:300] + f"...<{len(value) - 300} more chars>"
    return value


def _env_number(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


class DispatcherLogs:
    """
    Persistent on-disk log of the important errors/events that impact a dispatcher. It should not be disabled
    even in production as it is how issues are traced after the fact: dispatchers typically run behind a
    screen/tmux/whatever, so the printed output scrolls away. Thread-safe; usable from any thread.

    Layout and rolling. Each dispatcher logs into its own folder, ``<logs_dir>/<identity>/<identity>.log``
    (``logs_dir`` is ``ARGUS_LOGS_DIR`` or ``~/.argus/dispatcher_logs/<identity>``; see __init__). The log
    rolls, i.e. the current file is renamed to ``<identity>.<UTC timestamp>.log`` and a fresh
    ``<identity>.log`` is started, (1) every time the dispatcher starts (a reboot, a restart or a crash
    recovery always begins on a new file), and (2) whenever the file reaches ``ARGUS_LOGS_MAX_MB``
    megabytes (default 100). Old files are never deleted.

    Line format: ``<UTC ISO timestamp> [LEVEL] message``. `info` / `warning` are buffered and flushed by a
    background thread about once a second; `error` / `critical` (and `flush()`) are written synchronously so the
    lines that matter most survive a crash that follows them.
    """

    DEFAULT_MAX_MB = 100.0

    def __init__(self, identity: str):
        self.identity = identity
        logs_dir = os.environ.get("ARGUS_LOGS_DIR", os.path.expanduser(f"~/.argus/dispatcher_logs/{identity}"))
        os.makedirs(logs_dir, exist_ok=True)
        self.log_file = os.path.join(logs_dir, f"{identity}.log")
        self.max_bytes = int(_env_number("ARGUS_LOGS_MAX_MB", self.DEFAULT_MAX_MB) * 1024 * 1024)
        self.lock = threading.Lock()
        self.event = threading.Event()
        self._log_buffer = ""
        with self.lock:
            self._roll_locked(reason="startup")
        self._write_on_event()
        atexit.register(self.flush)

    ########################################
    # Rolling
    ########################################
    def _roll_locked(self, reason: str) -> None:
        """Archive the current file (if it has content) and stamp a fresh one. Caller holds `self.lock`."""
        from argus import __version__
        try:
            if os.path.exists(self.log_file) and os.path.getsize(self.log_file) > 0:
                mtime = datetime.fromtimestamp(os.path.getmtime(self.log_file), UTC).strftime("%Y%m%dT%H%M%S")
                stem = self.log_file[:-len(".log")]
                archive, n = f"{stem}.{mtime}.log", 1
                while os.path.exists(archive):
                    archive, n = f"{stem}.{mtime}.{n}.log", n + 1
                os.rename(self.log_file, archive)
        except OSError:
            traceback.print_exc()  # never let a rename problem take the dispatcher down; keep appending
        with open(self.log_file, "a") as f:
            f.write(f"Argus version: {__version__}\n")
            f.write(f"Log started at: {datetime.now(UTC).isoformat()} ({reason}), pid={os.getpid()}\n")
            f.write("=" * 80 + "\n")

    def _write_buffer_locked(self) -> None:
        """Write the buffer to disk and roll if the file grew past the cap. Caller holds `self.lock`."""
        if self._log_buffer:
            with open(self.log_file, "a") as f:
                f.write(self._log_buffer)
            self._log_buffer = ""
        try:
            if os.path.getsize(self.log_file) >= self.max_bytes:
                self._roll_locked(reason=f"size >= {self.max_bytes / (1024 * 1024):g}MB")
        except OSError:
            pass

    ########################################
    # Writing
    ########################################
    @runAsThread
    def _write_on_event(self):
        """
        This thread watches for the event to be set and writes the buffer to disk when it is set.
        It then clears the event and waits for the next event. It forms the backbone of the async logging system.
        """
        while True:
            self.event.wait()
            try:
                with self.lock:
                    self._write_buffer_locked()
                    self.event.clear()
            except Exception:
                traceback.print_exc()
                time.sleep(5)

            time.sleep(1)  # rational being, if there are many loggers right now
            # let them all write to buffer cheaply then we can write it all into disk
            # at once.

    def _append(self, level: str, text: str, sync: bool) -> None:
        line = f"{datetime.now(UTC).isoformat(timespec='milliseconds')} [{level}] {text}\n"
        with self.lock:
            self._log_buffer += line
            if sync:
                try:
                    self._write_buffer_locked()
                except Exception:
                    traceback.print_exc()
            else:
                self.event.set()

    @staticmethod
    def _join(args) -> str:
        return " ".join(str(arg) for arg in args)

    def log(self, *args):
        """Concatenate the args into a single INFO line (kept for callers that predate levels)."""
        self._append("INFO", self._join(args), sync=False)

    def info(self, *args):
        self._append("INFO", self._join(args), sync=False)

    def warning(self, *args):
        self._append("WARNING", self._join(args), sync=False)

    def error(self, *args):
        """An ERROR line, written to disk before this returns."""
        self._append("ERROR", self._join(args), sync=True)

    def critical(self, *args):
        """A CRITICAL line (money/state at risk), written to disk before this returns."""
        self._append("CRITICAL", self._join(args), sync=True)

    def exception(self, *args):
        """An ERROR line followed by the traceback of the exception being handled, written synchronously."""
        self._append("ERROR", self._join(args) + "\n" + traceback.format_exc().rstrip(), sync=True)

    def flush(self):
        """Write anything buffered to disk now."""
        try:
            with self.lock:
                self._write_buffer_locked()
        except Exception:
            pass


_LOGS_REGISTRY: dict[str, DispatcherLogs] = {}
_LOGS_REGISTRY_LOCK = threading.Lock()


def get_dispatcher_logs(identity: str) -> DispatcherLogs:
    """
    The one `DispatcherLogs` for `identity` in this process, created on first use. The dispatcher and its
    websocket clients both call this (the latter before the dispatcher's own __init__ has finished), so they
    share one file and one rolling policy.
    """
    with _LOGS_REGISTRY_LOCK:
        logs = _LOGS_REGISTRY.get(identity)
        if logs is None:
            logs = _LOGS_REGISTRY[identity] = DispatcherLogs(identity)
        return logs


class BaseDispatcherCompatibleRest:
    """
    A class REST clients should inherit from and subclass it's methods.
    These are the methods that are going to be used by the BaseDispatcher for
    some of its builtin functionality.
    """

    def __init__(self):
        pass

    def get_all_perpetuals(self):
        """
        This function should return a list of all perpetuals
        :return:
        """
        raise NotImplementedError("get_all_perpetuals() not implemented.")


class BaseDispatcher(AccountHandlersMixin, Introspective, RoutingHelper):
    """
    A base class designed for Argus v2's Perpetual Dispatchers. This dispatcher inherits almost all of PolymarketDispatcher's inbound
    and outbound message shapes. It uses Introspective, RoutingHelper, CorrelationIDChecker, Server (utils3.networking.sockets.Server),
    etc... to provide the foundation for a trading-enabled dispatcher. The common data shapes for this dispatcher
    can be found in shared/_classes.py & shared/errors.py

    This base class supports runtime.py's .interactive_mode() [to Introspective._interactive_ui].
    Subclasses should NOT override this function to provide custom functionality. Rather provide custom
    fn's through `interactive_functions` param. This base class change adds its own functionality
    into the interactive method depending on what configs are enabled. (e.g., if you use _distribute_refreshed_perps,
    then certain interactive functions are enabled)

    The server enforces correlation IDs for all requests. A request without a correlation ID will be rejected;
    The server uses P1 protocol to encode the messages. It uses the same shape as Polymarket's P1 messages with
    the same fields for in-out.

    This base class also provides common utilities for perpetuals such as refreshing perpetual lists on the subclass's
    behalf. To enable this feature (and other to come), subclasses should pass in a BaseDispatcherCompatibleRest instance
    to the constructor. None of these utilities are enabled by default and must be enabled by the subclasses by calling the
    respective utility functions. (e.g. _refresh_perpetuals() to refresh the perpetual list every UTC hour). Ensure
    the REST client passed in conforms semantically with the BaseDispatcherCompatibleRest interface. Everything is
    already pre-threaded with @runAsThread. If you call a utility function without passing in a BaseDispatcherCompatibleRest
    instance, a RuntimeError will be raised. A NotImplementedError will be raised if the REST client does not implement
    the functions required by the BaseDispatcherCompatibleRest interface and used by the utility function.

    Additionally, utilities can impact routing the behavior can be controlled by the configurations dict passed
    into the constructor. E.g., disable_routing_on_prep_cache_failure=True to disable routing temporarily if the
    refresh_perpetuals() utility fails to refresh the perpetual list. See the source code for more details.
    Utilities can also set the system into a terminal state (e.g., disable routing) if they fail to complete after a
    certain number of retries. This is to ensure the dispatcher is not left providing bad data to clients.

    Additionally, there are routine_* functions; these are common subroutines that are used by dispatchers. They
    interact with clients for you. You must call them when required; they have no impact and cannot be opt-into
    like some of the utility classes (e.g., _distribute_refreshed_perpetuals gates on config). It's very important
    to read the doc string of these functions as they may require objects that follow a certain shape. e.g., the
    _routine_push_funding_rates_for_client requires some object that has a .name and .funding_rate attribute. Its
    highly recommended to use these subroutines as they homogenise the API response between dispatchers making the
    SDK cleaner.

    That retry/terminal-state handling is only for transient runtime failures (e.g. a REST call failing). It is
    deliberately NOT applied to argus.perpetuals.shared.errors.FatalDispatcherError (and its subclasses, e.g.
    AbstractMethodNotImplementedError) -- those mean the running code itself is misconfigured or incomplete, such
    as a subclass enabling a utility (e.g. distribute_refreshed_perps) without overriding the abstract method it
    depends on (_distribute_refreshed_perpetuals). Utilities specifically exempt FatalDispatcherError from their
    retry loops: they log it loudly via pi.throw_fuss and then let it propagate uncaught, rather than retrying or
    silently disabling routing around it, because retrying would only mask the bug as a flaky failure. Any future
    "this should be impossible, the code is wrong if we get here" error added to a utility should subclass
    FatalDispatcherError so it gets the same treatment.

    The state of the base class is managed by self._state with each value being of type LockedState. Subclasses
    should not modify self._state directly. Internally, they are all thread-safe. You can access the state of the
    base dispatcher, however, through the self.state property. This property returns a copy of the state. Note
    that the returned copy is run through deepcopy where __deepcopy__ on LockedState strips itself and returns
    only the value without a reference to the LockedState object.

    To keep type inference while still passing the rest instance, the BaseDispatcher deliberately does not use
    self.rest rather uses self.common_rest. This allows the subclass to bind self.rest to the concrete type of the
    rest client and pass it to the superclass constructor. For guidance on this pattern, see:
    argus/perpetuals/hyper/__init__.py: HyperLiquidDispatcher

    Account data (balance, positions, orders, trades, funding payments) is likewise shared: the handlers come from
    argus.perpetuals.shared.account.AccountHandlersMixin and only ever talk to `self.account_rest`, a
    BaseDispatcherCompatibleAccountRest. Pass the venue's account-capable REST client as `account_rest` (it may be
    the same object as `common_rest`) and merge `self.account_routing_table()` into the routing table to expose the
    actions. Leave `account_rest` as None for a dispatcher with no account configured; the actions then answer
    with AccountNotConfiguredError instead of silently reporting an empty account. See shared/account.py for the
    design and the wire format.



    """

    def __init__(self, host: str, port: int,
                 routing_table: Mapping[str, Callable[[ArgsObject], Any]],
                 pi: "PrintInterface" = _p,
                 common_rest: BaseDispatcherCompatibleRest = None,
                 configurations: dict = None,
                 interactive_functions: dict = None,
                 account_rest: BaseDispatcherCompatibleAccountRest = None):
        super().__init__()
        RoutingHelper.__init__(self)
        self._dispatcher_server = Server(
            host=host,
            port=port,
            on_recv=self._on_recv,
            on_disconnect=self._on_disconnect
        )

        self._corr_id_check = CorrelationIDChecker()
        self.routing_table = routing_table
        self.pi = pi
        self.common_rest: BaseDispatcherCompatibleRest = common_rest
        self.account_rest: BaseDispatcherCompatibleAccountRest | None = account_rest
        self._state = {
            "enable_routing": LockedState(True)
        }
        if configurations is None:
            configurations = {}

        self._max_retry_range_rest = configurations.get("max_retry_range_rest", 10)
        self._disable_routing_on_prep_cache_failure = configurations.get("disable_routing_on_prep_cache_failure", True)
        self._retry_backoff_base_rest = configurations.get("retry_backoff_base_rest", 3.0)
        self._retry_backoff_max_rest = configurations.get("retry_backoff_max_rest", 30.0)
        self._distribute_refreshed_perps = configurations.get("distribute_refreshed_perps", False)
        self._interactive_fns = interactive_functions if interactive_functions is not None else {}
        # Server-side kill switch for order placement, like Polymarket's "Block Order Execution". Seeded from
        # configurations["block_order_execution"]; a LockedState because the dispatcher runs one thread per
        # client socket. Interactive-only: no wire action toggles it, so a client cannot unblock itself.
        self._block_order_execution = LockedState(bool(configurations.get("block_order_execution", False)))

        self.logs = get_dispatcher_logs(self.__class__.__name__)
        self._log_startup_snapshot(host, port, configurations)

    #: Environment variables whose names start with one of these are written to the startup snapshot.
    _SNAPSHOT_ENV_PREFIXES = ("ARGUS_", "APDB_", "WIREPROXY_", "HYPERLIQUID_", "LIGHTER_", "POLYMARKET_")

    def _log_startup_snapshot(self, host: str, port: int, configurations: dict) -> None:
        """
        Write what this process is running with: version/python/pid, bind address, kill-switch state, retry and
        routing settings and the relevant environment. A value is shown as "<set>" when its name looks like a
        credential (keys, tokens, passwords...), never verbatim. Never raises.
        """
        try:
            env = {}
            for name in sorted(os.environ):
                if name.startswith(self._SNAPSHOT_ENV_PREFIXES):
                    env[name] = "<set>" if _SECRET_NAME_RE.search(name) else os.environ[name]
            self.logs.info(
                f"STARTUP dispatcher={self.__class__.__name__} host={host} port={port} pid={os.getpid()} "
                f"python={sys.version.split()[0]} account_rest={'configured' if self.account_rest is not None else 'none'}"
            )
            self.logs.info(
                f"STARTUP block_order_execution={self._block_order_execution.value} "
                f"max_retry_range_rest={self._max_retry_range_rest} "
                f"disable_routing_on_prep_cache_failure={self._disable_routing_on_prep_cache_failure} "
                f"retry_backoff_base_rest={self._retry_backoff_base_rest} "
                f"retry_backoff_max_rest={self._retry_backoff_max_rest} "
                f"distribute_refreshed_perps={self._distribute_refreshed_perps} "
                f"configurations={json.dumps(scrub(configurations), default=str)}"
            )
            self.logs.info(f"STARTUP env={json.dumps(env)}")
        except Exception:
            traceback.print_exc()

    ########################################
    # Threads and utilities
    ########################################
    @runAsThread
    def _refresh_perpetuals(self):
        """
        Every UTC hour, refresh the perpetual list. This function set's the value of
        self._all_perps to LockedState(self.common_rest.get_all_perpetuals()) you must write users of all_perps
        to use LockedState.value to access the underlying value.
        :return:
        """

        try:
            # noinspection PyUnresolvedReferences
            if self._all_perps is not None and not isinstance(self._all_perps, LockedState):
                raise TypeError("self._all_perps must be a LockedState. Update source code to use LockedState.")
        except AttributeError:  # self._all_perps is not set
            pass

        if self.common_rest is None:
            raise RuntimeError("Cannot refresh perpetuals: self.common_rest is None. "
                               "Subclasses must set self.common_rest to a BaseDispatcherCompatibleRest "
                               "instance before calling _refresh_perpetuals().")
        while True:
            next_utc_hour_in = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            time_to_wait = (next_utc_hour_in - datetime.now(UTC)).total_seconds()
            self.pi.prt(f"Next perpetual refresh in {time_to_wait} seconds (at {next_utc_hour_in})")
            time.sleep(max(time_to_wait - 0.5, 0))
            self.pi.prt("Polling till time boundary is reached for perpetual refresh.")
            while True:
                if datetime.now(UTC) >= next_utc_hour_in:
                    self.pi.prt("Refreshing perpetual list.")
                    refreshed = False
                    for i in range(self._max_retry_range_rest):
                        try:
                            self._all_perps = LockedState(self.common_rest.get_all_perpetuals())
                            self._state["enable_routing"].value = True
                            refreshed = True
                            if self._distribute_refreshed_perps:
                                self._distribute_refreshed_perpetuals()
                            break
                        except ers.FatalDispatcherError as e:
                            # Not a transient refresh failure -- the perpetual list above
                            # already refreshed fine. This means the running code is
                            # misconfigured (e.g. distribute_refreshed_perps enabled on a
                            # subclass that never overrode the distribute method), so retrying
                            # or disabling routing around it would only hide the real bug.
                            # Surface it loudly and let it propagate instead of swallowing it.
                            self.logs.exception(f"Fatal dispatcher error during perpetual refresh: {e}")
                            self.pi.throw_fuss(
                                f"Fatal dispatcher error during perpetual refresh: {e}. "
                                f"This is a code/configuration bug, not a transient failure -- not retrying.",
                                title="Fatal Dispatcher Error",
                                notify=True,
                            )
                            raise
                        except Exception as e:
                            msg = f"Failed to refresh perpetual list. Err={e}. Retrying ({i + 1}/{self._max_retry_range_rest})..."
                            if self._disable_routing_on_prep_cache_failure:
                                msg += " Disabling routing temporarily."
                                self._state["enable_routing"].value = False

                            if self._state["enable_routing"]:
                                title = "Perpetual Refresh Error (Routing Enabled)"
                            else:
                                title = "Perpetual Refresh Error (Routing Disabled)"

                            self.pi.throw_fuss(msg, title=title, notify=True)
                            traceback.print_exc()
                            self.logs.exception(
                                f"Failed to refresh perpetual list. Err={e}. Retrying ({i + 1}/{self._max_retry_range_rest})... "
                                f"routing_enabled={bool(self._state['enable_routing'])}")

                            if i < self._max_retry_range_rest - 1:
                                # Back off before retrying. Retrying instantly turns one transient
                                # failure (e.g. a rate limit) into a burst of full re-fetches that
                                # keeps tripping the same limit, which is why routing could stay
                                # disabled far longer than the underlying hiccup warranted.
                                backoff = min(self._retry_backoff_base_rest * (2 ** i), self._retry_backoff_max_rest)
                                time.sleep(backoff)

                    if not refreshed:
                        self._state["enable_routing"].value = False
                        self.logs.critical(
                            f"Perpetual list refresh failed after {self._max_retry_range_rest} retries. Routing disabled.")
                        raise RuntimeError(
                            "Failed to refresh perpetual list after {} retries. Disabling routing.".format(
                                self._max_retry_range_rest))

                    # Without this, `datetime.now(UTC) >= next_utc_hour_in` stays true for the
                    # rest of the hour, so the inner loop would immediately refresh again (and
                    # again) instead of returning to the outer loop to wait for the *next* UTC
                    # hour -- hammering the REST API in a tight back-to-back loop.
                    break

                time.sleep(0.01)

    def _distribute_refreshed_perpetuals(self):
        """
        After `_refresh_perpetuals` has refreshed the perpetual list (and thereby the funding rates),
        send the new funding rates to all clients who've subscribed to that perpetual. This fn is opt-in
        just like `_refresh_perpetuals`. Because each dispatcher's perpetual has a different shape, this function
        needs to be overridden by the subclass.

        Raises ers.AbstractMethodNotImplementedError if not overridden. See the BaseDispatcher class docstring
        ("terminal state" section) for why this is a FatalDispatcherError rather than a bare NotImplementedError:
        _refresh_perpetuals deliberately does not retry or disable routing around it, it logs via pi.throw_fuss
        and re-raises, since a missing override is a code bug, not a transient failure.
        :return:
        """
        raise ers.AbstractMethodNotImplementedError("Subclasses must implement _distribute_refreshed_perpetuals()")

    ########################################
    # INTERNAL SERVER FUNCTIONS & Callbacks
    ########################################

    def _on_recv(self, client: socket.socket, address: tuple[str, int], data: bytes):
        """
        This function is called when new data (bytes) is received from a client, the function
        then passes it into the decode_multiple_packets function so multipart messages can be
        deconstructed. It then serialises the data into JSON -> ArgsObject -> route_request.
        :param client: The client's socket
        :param address: The client's address
        :param data: The data received from the client
        :return:
        """

        # Any client that talks to us receives unsolicited account_update pushes. This is done here rather
        # than via Server(on_connect=...) because utils3's Server treats on_connect as a *replacement* for
        # its receive loop, which would stop on_recv from ever being called. add_socket is idempotent.
        self.add_socket(client)

        try:
            packets = protocol.decode_multiple_packets(data)
        except ValueError:
            client.sendall(cls.OutboundMessage(
                action="error",
                data=None,
                correlation_id=None,
                error="Unable to decode message. Ensure payload was encoded with Protocol 1"
            ).convert_to_protocol_1())
            traceback.print_exc()
            self.logs.warning(f"P1 DECODE FAILURE from {address}: {len(data)} bytes, starts with {data[:80]!r}")
            return

        # Each packet gets its own try/except so that (a) one bad/erroring packet in a batch doesn't
        # prevent the rest of the batch from being processed, and (b) every packet we can attribute a
        # correlation_id to always gets a response -- including packets whose handler raised (e.g.
        # ers.MissingArgumentError, ers.InvalidFunctionError) -- rather than the caller hanging until
        # its socket read times out.
        for packet in packets:
            corr_id = None
            function_name = None
            try:
                js_load = json.loads(packet)
                function_name = js_load.get("action")
                args = ArgsObject(
                    sock=client,
                    args=js_load.get("data"),
                )
                corr_id = js_load.get("correlation_id", None)
                args.correlation_id = corr_id  # for the audit trail (fatal_decorator)
                if corr_id is None:
                    raise ers.CorrelationIDError("Correlation ID is required for all requests")

                self._corr_id_check.check_correlation_id(corr_id)
                response = self.route_request(function_name, args)
                client.sendall(cls.OutboundMessage(
                    action="response",
                    data=response,
                    correlation_id=corr_id
                ).convert_to_protocol_1())
            except Exception as e:
                traceback.print_exc()
                if isinstance(e, self._expected_errors):
                    self.logs.warning(f"P1 request failed from {address} action={function_name} "
                                      f"corr_id={corr_id} {type(e).__name__}: {e}")
                else:
                    self.logs.exception(f"P1 request raised an unexpected error from {address} "
                                        f"action={function_name} corr_id={corr_id}: {e!r}")
                client.sendall(cls.OutboundMessage(
                    action="error",
                    data=None,
                    error=str(e),
                    correlation_id=corr_id
                ).convert_to_protocol_1())

    def _on_disconnect(self, client, address):
        self.remove_socket(client)
        _p.prt(f"Client {address} disconnected")

    def route_request(self, function: str, args: ArgsObject):
        """
        This function routes a single request encapsulated by ArgsObject. It will route to the appropriate
        function and return transparently to the caller whatever the function returns.
        If the function is not valid, it will raise an InvalidFunctionError.

        :arg function: str = function name
        :arg args: ArgsObject = argument object and the socket
        :return:
        """
        if not self._state["enable_routing"]:
            raise ers.RoutingDisabledError(
                "Routing is currently disabled. Function '{}' was not routed.".format(function)
            )

        func = self.routing_table.get(function)
        if func is None:
            raise ers.InvalidFunctionError(f"Function {function} is not valid")

        _p.prt("[{}] Routing: {} with args: {}".format(datetime.now().strftime("%H:%M:%S:%f %d-%m-%Y"), function,
                                                       args.args))

        # noinspection all
        response = func(args)
        return response

    ########################################
    # Common subroutines for dispatchers
    ########################################

    def _routine_send_packet_to_clients(self, clients: list[socket.socket], packet: bytes, context: str):
        """
        Send an already-encoded packet (P1 or P2 -- this doesn't care which) to a list of client
        sockets, one at a time, cleaning up any socket that turns out to be dead. Shared by every
        "broadcast this packet to subscribed clients" callback (order book updates, refreshed-perpetual
        pushes, ...) so the send/error handling isn't duplicated per callback per venue.
        :param clients: Sockets to send `packet` to.
        :param packet: The already protocol-encoded bytes to send.
        :param context: Human-readable description of what's being sent, used only for logging,
        e.g. "perpetual info for coin BTC" or "order book update for coin BTC".
        :return:
        """
        for sock in clients:
            try:
                with self.send_lock_for(sock):
                    sock.sendall(packet)
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                self.remove_socket(sock)
                self.pi.prt(f"Removed dead socket while sending {context}: {e}")
            except Exception as e:
                self.pi.prt(f"Unexpected error sending {context} to socket: {e}")
                self.remove_socket(sock)
                traceback.print_exc()

    def _routine_push_account_update(self, update: AccountUpdate):
        """
        Push one account event (order transition, fill, gap) to every connected client, whether or not
        it has subscribed to any market. Unlike Polymarket, where `self.sockets` only holds clients that
        subscribed, account events are not tied to a coin, so recipients are every socket that has made
        a request (`_on_recv` registers it). Callers run on a venue websocket thread: this encodes once,
        sends per socket under its send lock and prunes dead sockets, and never raises on a bad client.
        :param update: The AccountUpdate to broadcast; one record per call (see AccountUpdate).
        :return:
        """
        packet = update.convert_to_protocol_1()
        self._routine_send_packet_to_clients(self.sockets, packet, context=f"account update ({update.event})")

    @runAsThread
    def _routine_push_funding_rates_for_client(self, client: socket.socket, PerpObject):
        """
        :param client: A socket object to send the funding rates to.
        :param PerpObject: Any object with a .name and .funding_rate attribute.
        :return:
        """
        time.sleep(random.randint(1, 10) / 10)  # random jitter important for SDK
        new_rate = NewFundingRate(
            perp_name=PerpObject.name,
            funding_rate=PerpObject.funding_rate
        )
        self._routine_send_packet_to_clients([client], new_rate.convert_to_protocol_1(), context="Funding Rate Update")

    #: Errors a `fatal_decorator`-wrapped handler is "ready for"; subclasses extend it with their venue's
    #: expected errors (e.g. an exchange rejecting an action). `FatalDispatcherError` is always excluded.
    _expected_errors: tuple = (ers.DispatcherError,)

    def _on_fatal_error(self, info: dict) -> None:
        """
        The contingency action for an unexpected error in a trading handler (see `fatal_decorator`):
        1. shout on the console, 2. broadcast a `fatal_error` message to every connected client (so SDKs know
        the state of any in-flight order is uncertain and must be reconciled with `get_orders` /
        `get_positions`). It deliberately does NOT touch the order-execution kill switch: that is only ever
        engaged by the operator (interactive menu or environment variable). Never raises.
        """
        try:
            function, exception, tb = info.get("function"), info.get("exception"), info.get("traceback", "")
            self.logs.critical(
                f"FATAL ERROR in '{function}': {exception!r} order_execution_blocked={self.order_execution_blocked} "
                f"clients_notified={len(self.sockets)}\n{tb}")
            self.pi.throw_fuss(
                f"UNEXPECTED ERROR in '{function}': {exception!r}\n{tb}\n"
                f"Reconcile with get_orders / get_positions.",
                title="Fatal Dispatcher Error", notify=True,
            )
            packet = cls.OutboundMessage(
                action="fatal_error",
                data={"function": function, "exception": str(exception), "traceback": tb[-1500:],
                      "order_execution_blocked": self.order_execution_blocked},
                error=str(exception),
            ).convert_to_protocol_1()
            self._routine_send_packet_to_clients(list(self.sockets), packet, context="fatal error")
        except Exception:
            traceback.print_exc()

    ########################################
    # Order execution kill switch
    ########################################
    @property
    def order_execution_blocked(self) -> bool:
        """True while placing orders (and changing leverage) is refused. Cancels are never blocked."""
        return self._block_order_execution.value

    def _require_order_execution_enabled(self) -> None:
        """
        Call as the FIRST statement of every handler that can open or increase risk (before argument parsing,
        any info call, or any signing). Raises `ers.OrderExecutionDisabledError` while the switch is on.
        """
        if self._block_order_execution.value:
            raise ers.OrderExecutionDisabledError("Order execution is currently blocked by server configuration.")

    def _toggle_block_order_execution(self) -> bool:
        """Interactive toggle for the kill switch; returns (and prints) the new state."""
        with self._block_order_execution.lock:
            self._block_order_execution._value = not self._block_order_execution._value
            blocked = self._block_order_execution._value
        logs = getattr(self, "logs", None)
        if logs is not None:
            logs.warning(f"KILL SWITCH toggled by operator: block_order_execution={blocked}")
            logs.flush()
        print(
            f"[CONFIG] Block Order Execution: {'ENABLED (orders refused)' if blocked else 'DISABLED (orders allowed)'}")
        return blocked

    ########################################
    # PUBLIC FUNCTIONS
    ########################################
    def interactive_mode(self):
        fns = {
            "Toggle Block Order Execution": (
                "Refuse (or allow again) order placement and leverage changes; cancels always work",
                self._toggle_block_order_execution,
            ),
        }
        if self._distribute_refreshed_perps:
            fns["Distribute Refreshed Perpetuals"] = (
                "Distributes the refreshed perpetuals currently subscribed to their respective clients as a P1 message",
                self._distribute_refreshed_perpetuals,
            )
        fns.update(self._interactive_fns)
        self._interactive_ui(fns)

    def run_server(self):
        _p.prt("Starting dispatcher server on host: {}, port: {}".format(self._dispatcher_server.host,
                                                                         self._dispatcher_server.port))
        self._dispatcher_server.start()

    @runAsThread
    def run(self):
        """Starts the dispatcher server on a background thread. Use this (rather than
        run_server directly) when the caller also wants to run interactive_mode(), since
        run_server() blocks forever accepting connections."""
        self.run_server()

    @property
    def state(self):
        return copy.deepcopy(self._state)
