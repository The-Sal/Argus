"""
TradingView Dispatcher
======================

Streams TradingView quote data to multiple clients over TCP, in the same style as the
Polymarket / Perpetuals dispatchers:

- Protocol 1 (P1): request/response for subscribe / unsubscribe / get_subscriptions / ping / version
- Protocol 2 (P2): unsolicited push of live quote updates to subscribed clients

The heavy lifting is reused from the rest of the codebase rather than re-implemented:

- argus.tv.QuoteSession            -- the upstream TradingView WebSocket protocol
                                      (auth, session creation, field setup, framing, heartbeat echo)
- argus.perpetuals.shared.BaseDispatcher -- the P1 TCP server, correlation-ID enforcement,
                                      routing table, per-socket send routine and interactive mode
- argus.protocol                   -- P1 framing + P2 packet encoding

The only genuinely new pieces are:

- DispatcherQuoteSession : a thin QuoteSession subclass that can hold MANY symbols on ONE
  connection (the dispatcher runs a single shared upstream socket for all clients and all
  symbols -- there is deliberately no per-asset connection), reports the symbol with each
  quote, and re-runs its handshake (replaying the full symbol roster) after every reconnect.
- TradingViewQuoteWss    : lifecycle wrapper (run forever with built-in auto-reconnect).
- TVP2ConvertClass       : shapes a TradingView quote into P2 CSV.
- TradingViewDispatcher  : the routing table + P1 handlers + P2 fan-out.

See docs/TV_DISPATCHER.md for the full API spec (envelopes, field order, lifecycle).
"""

import os
import time
import threading
import traceback

from utils3 import runAsThread
from argus import __version__ as argus_version
from argus.protocol import transmit_mkt_data_with_protocol_2
from argus._argus_utils import ArgsObject
from argus.tv import QuoteSession, QUOTE_FIELDS
from argus.perpetuals.shared import BaseDispatcher, PrintInterface
from argus.perpetuals.shared import _errors as ers

__version__ = [1, 0, 0, 0]
pi = PrintInterface('TradingView')


class TVP2ConvertClass:
    """
    Duck-typed adapter for argus.protocol.transmit_mkt_data_with_protocol_2 -- exposes
    .symbol and .transferable_2() just like the other venues' P2 convert classes.

    P2 field order (clients must decode with this exact order):
        bid, bid_size, ask, ask_size, last, change, change_pct, volume, timestamp, transmission_time
    """

    FIELD_ORDER = ['bid', 'bid_size', 'ask', 'ask_size', 'last', 'change',
                   'change_pct', 'volume', 'timestamp', 'transmission_time']

    def __init__(self, symbol: str, quote: dict):
        self._symbol = symbol
        self._quote = quote or {}

    @property
    def symbol(self) -> str:
        return self._symbol

    @staticmethod
    def _num(value) -> float:
        if value is None:
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def transferable_2(self) -> bytes:
        q = self._quote
        values = [
            self._num(q.get('bid')),
            self._num(q.get('bid_size')),
            self._num(q.get('ask')),
            self._num(q.get('ask_size')),
            self._num(q.get('lp')),
            self._num(q.get('ch')),
            self._num(q.get('chp')),
            self._num(q.get('volume')),
            self._num(q.get('lp_time')),
            time.time(),
        ]
        return ','.join(str(v) for v in values).encode('ascii')


class DispatcherQuoteSession(QuoteSession):
    """
    A QuoteSession that can hold many symbols on one connection. Everything else -- auth,
    session creation, field setup, framing, heartbeat echo, qsd decoding -- is inherited
    from QuoteSession unchanged.

    Differences from QuoteSession:
    - maintains a symbol roster (self._symbols); add_symbols/remove_symbols register it on
      both quote sessions (main + snapshotter) of this single connection.
    - callback contract: callback(symbol, values_dict) instead of callback(MarketData), so
      the dispatcher can route each quote to the clients subscribed to that symbol.
    - re-runs its first-message handshake after every (re)connect (on_open fires on both
      initial connect and websocket-client's built-in reconnect), which replays the full
      roster automatically.
    """

    def __init__(self, initial_symbols=(), callback=None, sendAuth=False):
        symbols = [s for s in (initial_symbols or []) if s]
        # The parent treats self.symbol as its "anchor" symbol, added by its setup_qs.
        super().__init__(symbol=symbols[0] if symbols else "", callback=callback, sendAuth=sendAuth)
        self._symbols = set(symbols)
        self._handshaked = threading.Event()
        self._send_lock = threading.Lock()

    # -- lifecycle -----------------------------------------------------------

    def on_open(self, ws):
        super().on_open(ws)
        # Force the first-message handshake (setup + setup_qs) to run again so the full
        # symbol roster is re-registered after every (re)connection.
        self.msgs_read = 0
        self._handshaked.clear()

    def setup_qs(self):
        if self.symbol:
            super().setup_qs()  # parent logic: create sessions, set fields, add anchor symbol
            extras = [s for s in self._symbols if s != self.symbol]
        else:
            # No anchor symbol yet: bootstrap both sessions without adding any symbols.
            self.send_msg(self.craft_message("quote_create_session", [self.quote_session_id]))
            self.send_msg(self.craft_message("quote_set_fields", [self.quote_session_id, *QUOTE_FIELDS]))
            self.send_msg(self.craft_message("quote_create_session", [self.quote_snapshotter]))
            extras = list(self._symbols)
        # Mark the handshake complete BEFORE replaying the roster: add_symbols waits on
        # this event, and concurrent subscribers must be able to send immediately after.
        self._handshaked.set()
        if extras:
            self.add_symbols(extras)

    # -- roster ---------------------------------------------------------------

    @property
    def symbols(self) -> list:
        return sorted(self._symbols)

    def _is_connected(self) -> bool:
        sock = getattr(self.ws, "sock", None)  # websocket-client: core socket is ws.sock (None pre-connect)
        return sock is not None and bool(getattr(sock, "connected", False))

    def add_symbols(self, symbols):
        """Register symbols on this connection's quote sessions (idempotent)."""
        symbols = [s for s in (symbols or []) if s]
        if not symbols:
            return
        self._symbols.update(symbols)
        if not self._is_connected():
            return  # will be replayed by the (re)handshake
        if not self._handshaked.wait(timeout=10):
            pi.prt(f"TradingView handshake incomplete; {symbols} will be registered on next reconnect")
            return
        try:
            self.send_msg(self.craft_message("quote_add_symbols", [self.quote_session_id] + symbols))
            self.send_msg(self.craft_message("quote_add_symbols", [self.quote_snapshotter] + symbols))
        except Exception as e:
            pi.prt(f"Failed to send quote_add_symbols for {symbols}: {e}")

    def remove_symbols(self, symbols):
        """Unregister symbols from this connection's quote sessions (idempotent)."""
        symbols = [s for s in (symbols or []) if s]
        if not symbols:
            return
        for s in symbols:
            self._symbols.discard(s)
        if not (self._is_connected() and self._handshaked.is_set()):
            return  # was never registered upstream
        try:
            self.send_msg(self.craft_message("quote_remove_symbols", [self.quote_session_id] + symbols))
            self.send_msg(self.craft_message("quote_remove_symbols", [self.quote_snapshotter] + symbols))
        except Exception as e:
            pi.prt(f"Failed to send quote_remove_symbols for {symbols}: {e}")

    # -- data -----------------------------------------------------------------

    def decode_message(self, raw_msg, multiple=False):
        """TradingView delivers every frame as a BINARY websocket frame containing the usual
        ~m~-framed text payload. Normalize to str so the inherited QuoteSession logic (which
        assumes str) works unchanged."""
        if isinstance(raw_msg, (bytes, bytearray)):
            raw_msg = bytes(raw_msg).decode("utf-8", errors="replace")
        return super().decode_message(raw_msg, multiple)

    def handle_quote_data(self, params):
        """Like QuoteSession.handle_quote_data, but reports WHICH symbol the quote is for."""
        if len(params) < 2:
            return
        data = params[1] or {}
        symbol = data.get('n')
        values = data.get('v') or {}
        if not symbol or self.callback is None:
            return
        self.callback(symbol, values)

    # -- thread-safe send -------------------------------------------------------
    # add/remove_symbols are called from P1 handler threads while the WS thread sends
    # heartbeats/handshake messages, so serialize ws.send.

    def send_msg(self, msg: dict):
        with self._send_lock:
            super().send_msg(msg)


# TradingView silently stops delivering `qsd` quote updates on a long-lived quote session
# (the socket stays open -- ~h~ heartbeats keep flowing -- but the server-side symbol
# subscription goes stale) after roughly a minute of no session activity. Replaying the
# roster (quote_remove_symbols + quote_add_symbols) counts as session activity and
# restarts the quote stream, so the dispatcher nudges the roster on this cadence.
KEEPALIVE_INTERVAL_SECONDS = 25


class TradingViewQuoteWss:
    """
    Lifecycle wrapper around DispatcherQuoteSession: runs the single shared upstream
    TradingView WebSocket forever and exposes a per-symbol subscribe/unsubscribe API for
    the dispatcher. Reconnection is websocket-client's built-in `reconnect=1` (1s delay,
    forever); DispatcherQuoteSession re-runs its handshake and replays the full roster on
    every (re)connect, so clients never need to re-subscribe.

    A background keepalive thread also replays the roster periodically -- without it the
    upstream stops pushing quotes after ~a minute even though the socket stays open (see
    KEEPALIVE_INTERVAL_SECONDS).
    """

    def __init__(self, on_quote=None, send_auth=False, initial_symbols=()):
        self._on_quote = on_quote
        self.session = DispatcherQuoteSession(initial_symbols=initial_symbols,
                                              callback=self._dispatch, sendAuth=send_auth)
        self._keepalive_stop = threading.Event()

    def _dispatch(self, symbol: str, values: dict):
        if self._on_quote is not None:
            self._on_quote(symbol, values)

    def _keepalive_loop(self):
        """Periodically replay the symbol roster to keep the upstream quote subscription
        fresh; exits once close() is called."""
        while not self._keepalive_stop.wait(KEEPALIVE_INTERVAL_SECONDS):
            symbols = self.session.symbols
            if not symbols:
                continue
            try:
                self.session.remove_symbols(symbols)
                self.session.add_symbols(symbols)
            except Exception as e:
                pi.prt(f"TradingView keepalive failed for {symbols}: {e}")

    @runAsThread
    def run(self, main_thread=False):
        """Run the upstream session forever. `main_thread` is accepted for API parity
        with the other Argus market-data WSS classes (it is always a background thread)."""
        _ = main_thread
        threading.Thread(target=self._keepalive_loop, name='tv-quote-keepalive', daemon=True).start()
        try:
            self.session.ws.run_forever(reconnect=1, skip_utf8_validation=True)
        except KeyboardInterrupt:
            raise
        except Exception:
            traceback.print_exc()

    def close(self):
        self._keepalive_stop.set()
        self.session.ws.keep_running = False
        try:
            self.session.ws.close()
        except Exception:
            pass

    def subscribe_to_symbol(self, symbol: str):
        self.session.add_symbols([symbol])

    def unsubscribe_from_symbol(self, symbol: str):
        self.session.remove_symbols([symbol])

    @property
    def symbols(self) -> list:
        return self.session.symbols


class TradingViewDispatcher(BaseDispatcher):
    """
    A dispatcher for streaming TradingView market data to multiple clients over TCP.

    - Protocol 1 (request/response) for subscribe / unsubscribe / get_subscriptions / ping / version
    - Protocol 2 (unsolicited push) for live quote updates, in TVP2ConvertClass.FIELD_ORDER
    - ONE shared upstream TradingView quote WebSocket for all symbols and all clients
      (see DispatcherQuoteSession / TradingViewQuoteWss).

    See docs/TV_DISPATCHER.md for the full API spec.
    """

    def __init__(self, host="localhost", port=9974, send_auth=False, initial_symbols=()):
        routing_table = {
            'subscribe': self._handle_subscribe,
            'unsubscribe': self._handle_unsubscribe,
            'get_subscriptions': self._handle_get_subscriptions,
            'ping': self._handle_ping,
            'version': self._handle_version,
        }
        super().__init__(
            host=host,
            port=port,
            routing_table=routing_table,
            interactive_functions={
                'Get Upstream Symbols': ('Symbols registered on the shared upstream TradingView session',
                                         lambda: self.market_data.symbols),
                'Clear Correlation IDs': ('Forget all seen correlation IDs (lets clients reuse them)',
                                          lambda: self._corr_id_check.clear_seen_ids()),
            },
        )

        # The single shared upstream connection (NOT one per asset).
        self.market_data = TradingViewQuoteWss(on_quote=self._quote_callback,
                                               send_auth=send_auth,
                                               initial_symbols=initial_symbols)
        self.market_data.run(main_thread=False)

        # Last-known merged quote per symbol (partial upstream updates are merged here;
        # written and read only from the upstream WS thread).
        self._last_quotes: dict = {}

    # -- RoutingHelper ---------------------------------------------------------

    def subscription_expired(self, channel_id):
        """Last client unsubscribed/disconnected from this symbol -> drop it upstream."""
        self.market_data.unsubscribe_from_symbol(channel_id)

    # -- P2 fan-out --------------------------------------------------------------

    def _quote_callback(self, symbol: str, quote: dict):
        clients_to_send = list(self.market_data_routing_table.get(symbol, []))
        if not clients_to_send:
            return
        # TradingView sends PARTIAL per-field qsd updates (main session: lp/ch/volume,
        # snapshotter: bid/ask), so merge each update into the last-known quote for this
        # symbol and push the full merged state -- clients never have to merge themselves.
        # (Only the upstream WS thread calls this, so no locking is needed.)
        merged = self._last_quotes.setdefault(symbol, {})
        for k, v in quote.items():
            if v is not None:
                merged[k] = v
        packet = transmit_mkt_data_with_protocol_2(TVP2ConvertClass(symbol=symbol, quote=merged))
        self._routine_send_packet_to_clients(clients_to_send, packet, f"quote update for {symbol}")

    # -- P1 handlers ---------------------------------------------------------------

    @staticmethod
    def _validate_symbol(symbol):
        if not isinstance(symbol, str) or not symbol or ':' not in symbol:
            raise ers.InvalidCoinError(f"{symbol!r} is not a valid TradingView symbol (expected EXCHANGE:SYMBOL)")

    def _handle_subscribe(self, args: ArgsObject) -> dict:
        sock = args.sock
        self.add_socket(sock)
        symbols = args.args or []
        if not isinstance(symbols, list):
            raise ers.MissingArgumentError('subscribe expects a JSON list of TradingView symbol strings, e.g. ["NASDAQ:AAPL"]')
        subscribed, failed = [], []
        for symbol in symbols:
            try:
                self._validate_symbol(symbol)
                self.add_socket_to_subscription(sock, symbol)
                self.market_data.subscribe_to_symbol(symbol)
                subscribed.append(symbol)
            except Exception as e:
                failed.append(symbol)
                pi.prt(f"Failed to subscribe {sock} to {symbol}: {e}")
        return {"subscribed": subscribed, "failed": failed}

    def _handle_unsubscribe(self, args: ArgsObject) -> dict:
        sock = args.sock
        symbols = args.args or []
        if not isinstance(symbols, list):
            raise ers.MissingArgumentError('unsubscribe expects a JSON list of symbol strings')
        unsubscribed, failed = [], []
        for symbol in symbols:
            try:
                self.remove_socket_from_subscription(sock, symbol)
                unsubscribed.append(symbol)
            except Exception as e:
                failed.append(symbol)
                pi.prt(f"Failed to unsubscribe {sock} from {symbol}: {e}")
        return {"unsubscribed": unsubscribed, "failed": failed}

    def _handle_get_subscriptions(self, args: ArgsObject) -> dict:
        subs = self.order_subscriptions.get(args.sock, [])
        return {"subscriptions": list(subs)}

    def _handle_ping(self, args: ArgsObject) -> str:
        return "pong"

    def _handle_version(self, args: ArgsObject) -> dict:
        return {'argus': argus_version, 'tradingview_dispatcher': __version__}


if __name__ == '__main__':
    dispatcher = TradingViewDispatcher()
    dispatcher.run()
    dispatcher.interactive_mode()
    print("Exiting TradingView dispatcher")
