import copy
import json
import time
import socket
import threading
import traceback
from argus import protocol
from utils3 import runAsThread
from collections.abc import Mapping
from utils3.networking.sockets import Server
from datetime import datetime, timedelta, UTC
from typing import Callable, Any, Generic, TypeVar
from argus.perpetuals.shared import _classes as cls, _errors as ers
from argus.perpetuals.shared._classes import P2OrderBookConvertClass, OutboundMessage
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


class BaseDispatcher(Introspective, RoutingHelper):
    """
    A base class designed for Argus v2's Perpetual Dispatchers. This dispatcher inherits almost all of PolymarketDispatcher's inbound
    and outbound message shapes. It uses Introspective, RoutingHelper, CorrelationIDChecker, Server (utils3.networking.sockets.Server),
    etc... to provide the foundation for a trading-enabled dispatcher. The common data shapes for this dispatcher
    can be found in shared/_classes.py & shared/_errors.py

    This base class supports runtime.py's .interactive_mode() [to Introspective._interactive_ui]
    that defaults to no custom functions. Subclasses should override this function to provide custom functionality.
    See PolymarketDispatcher for an example of how to do this.

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

    That retry/terminal-state handling is only for transient runtime failures (e.g. a REST call failing). It is
    deliberately NOT applied to argus.perpetuals.shared._errors.FatalDispatcherError (and its subclasses, e.g.
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



    """

    def __init__(self, host: str, port: int,
                 routing_table: Mapping[str, Callable[[ArgsObject], Any]],
                 pi: "PrintInterface" = _p,
                 common_rest: BaseDispatcherCompatibleRest = None,
                 configurations: dict = None,
                 interactive_functions: dict = None):
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

                            if i < self._max_retry_range_rest - 1:
                                # Back off before retrying. Retrying instantly turns one transient
                                # failure (e.g. a rate limit) into a burst of full re-fetches that
                                # keeps tripping the same limit, which is why routing could stay
                                # disabled far longer than the underlying hiccup warranted.
                                backoff = min(self._retry_backoff_base_rest * (2 ** i), self._retry_backoff_max_rest)
                                time.sleep(backoff)

                    if not refreshed:
                        self._state["enable_routing"].value = False
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

    def _send_packet_to_clients(self, clients: list[socket.socket], packet: bytes, context: str):
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
        _ = address  # this will be used later for logging. However, for now the logging functionality
        # is not implemented.

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
            return

        # Each packet gets its own try/except so that (a) one bad/erroring packet in a batch doesn't
        # prevent the rest of the batch from being processed, and (b) every packet we can attribute a
        # correlation_id to always gets a response -- including packets whose handler raised (e.g.
        # ers.MissingArgumentError, ers.InvalidFunctionError) -- rather than the caller hanging until
        # its socket read times out.
        for packet in packets:
            corr_id = None
            try:
                js_load = json.loads(packet)
                function_name = js_load.get("action")
                args = ArgsObject(
                    sock=client,
                    args=js_load.get("data"),
                )
                corr_id = js_load.get("correlation_id", None)
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
    # PUBLIC FUNCTIONS
    ########################################
    def interactive_mode(self):
        fns = {}
        if self._distribute_refreshed_perps:
            fns["Distribute Refreshed Perpetuals"] = (
                "Distributes the refreshed perpetuals currently subscribed to their respective clients as a P1 message",
                self._distribute_refreshed_perpetuals,
            )
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
