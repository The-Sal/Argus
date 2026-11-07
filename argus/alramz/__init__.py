"""
Al Ramz market-data integration (``feed.alramz.ae``).

Public entry points:

- :class:`argus.alramz.dispatcher.AlRamzDispatcher` -- the TCP P1/P2 dispatcher (BaseDispatcher).
- :class:`argus.alramz._signalr.AlRamzSignalR` -- the shared upstream SignalR 2.1 client.
- :class:`argus.alramz._classes.AlRamzP2ConvertClass` -- order book -> Protocol-2 CSV.
- :mod:`argus.alramz.set_auth` -- IB-style Chromium login that mints ``ALRAMZ_SOCKET_SESSION``.

See ``docs/alramz/DISPATCHER.md`` for the wire spec.
"""

from argus.alramz._classes import (
    SymbolQuote,
    IndexQuote,
    DepthBook,
    AlRamzP2ConvertClass,
    parse_group,
    parse_groups,
)
from argus.alramz._signalr import AlRamzSignalR, AlRamzAuthError
from argus.alramz.dispatcher import AlRamzDispatcher

__all__ = [
    "AlRamzDispatcher",
    "AlRamzSignalR",
    "AlRamzAuthError",
    "AlRamzP2ConvertClass",
    "SymbolQuote",
    "IndexQuote",
    "DepthBook",
    "parse_group",
    "parse_groups",
]
