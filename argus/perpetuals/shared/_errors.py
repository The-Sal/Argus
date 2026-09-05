
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