class HyperLiquidError(Exception):
    pass

class HyperLiquidDispatcherError(HyperLiquidError):
    pass

class InvalidFunctionError(HyperLiquidDispatcherError):
    pass


class CorrelationIDError(HyperLiquidDispatcherError):
    pass