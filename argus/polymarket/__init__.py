"""
Refreshed Polymarket Dispatcher based on the polymarket_direct module. For the old version
see https://github.com/The-Sal/Argus/tree/legacy/polymarket-dispatcher

The below code removes the entire old stub with a new implementation based on polymarket_direct.
In future version this documentation referencing the old dispatcher will be removed.
"""
import logging
from argus import polymarket_direct

class PolymarketDispatcher:
    """
    A Polymarket Dispatcher with the following features on the API:
    – No Ping Required
    – Multiplexing supported
    – No Protocol 2 Support* (see note below)
    – 20 concurrent symbol streaming (configurable)
    – Automatic reconnection on disconnection (handled by polymarket_direct)

    The following features on the Introspective Terminal:
    – Real-time symbol quote display
    – Real-time connection status display
    – RTT/Latency Statistics for polymarket
    – Socket Statistics and Status Monitoring


    * Note: P2 Support is complicated with the Polymarket Dispatcher because there are two levels of data to consider
    tick-by-tick and market state. Looking at FxC which uses a dataframe structure representing entire markets
    on every socket update this allows for a full market snapshot rather than sending deltas–something the argus project
    does NOT like doing. FxC uses incremental dataframes that get filled/sent as deltas arrive on the FxCDispatcher.
    These datastructures do not work with P2. Hence, the Polymarket Dispatcher does not support P2. But the issue
    is that unlike FxC which is a relatively 'slow' market and we can aford to send full market snapshots every delta,
    polymarket is extremely high-frequency and sending full market snapshots on every delta would be inefficient
    and lead to performance issues [not really but would be suboptimal depending on requirements]. For this reasoning
    PolymarketDispatcher opens a second port that is dedicated to tick-by-tick data only,
    while the main port handles market state updates as well as control messages. These tick-by-tick updates will use
    P2 protocol for efficiency. However, they will be much longer than traditional P2 messages because the 'symbol' field
    used to identify this packet will be created for anti-collision. A dedicated P2 Parser will be available with
    version 0.0.9 of argus. This dispatcher is still undergoing heavy R&D and testing these are NOT final features
    or how the dispatcher will operate. We HIGHLY recommend using this dispatcher with Python 3.14t (free-threading)
    for best performance as well be using that version to tune the dispatcher.

    """