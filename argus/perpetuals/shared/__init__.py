import json
import socket
import traceback
from argus import protocol
from typing import Callable, Any
from utils3.networking.sockets import Server
from argus.perpetuals.shared import _classes as cls, _errors as ers
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

class BaseDispatcher(Introspective, RoutingHelper):
    """
    A base class designed for Argus v2's Perpetual Dispatchers.This dispatcher inherits almost all of Polymarket's inbound
    and outbound message shapes. It uses Introspective, RoutingHelper, CorrelationIDChecker, Server (utils3.networking.sockets.Server),
    etc... to provide the foundation for a trading-enabled dispatcher. The common data shapes for this dispatcher
    can be found in shared/_classes.py & shared/_errors.py

    The server enforces correlation IDs for all requests. A request without a correlation ID will be rejected;
    The server uses P1 protocol to encode the messages. It uses the same shape as Polymarket's P1 messages with
    the same fields for in-out.

    """
    def __init__(self, host: str, port: int,
                 routing_table: dict[str, Callable[[str, ArgsObject], Any]]):
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
            for packet in protocol.decode_multiple_packets(data):
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
        except ValueError:
            client.sendall(cls.OutboundMessage(
                action="error",
                data=None,
                correlation_id=None,
                error="Unable to decode message. Ensure payload was encoded with Protocol 1"
            ).convert_to_protocol_1())
            traceback.print_exc()

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
        func = self.routing_table.get(function)
        if func is None:
            raise ers.InvalidFunctionError(f"Function {function} is not valid")

        _p.prt("Routing: {} with args: {}".format(function, args.args))

        # noinspection all
        response = func(args)
        return response

    ########################################
    # PUBLIC FUNCTIONS
    ########################################
    def interactive_ui(self):
        self._interactive_ui({})

    def run_server(self):
        _p.prt("Starting dispatcher server on host: {}, port: {}".format(self._dispatcher_server.host, self._dispatcher_server.port))
        self._dispatcher_server.start()
