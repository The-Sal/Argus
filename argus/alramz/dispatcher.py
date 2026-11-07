"""
Al Ramz Dispatcher
==================

Streams Al Ramz (``feed.alramz.ae``) market data to multiple TCP clients, in the same style as the
TradingView / Hyperliquid / Lighter dispatchers:

- Protocol 1 (P1): request/response for ``subscribe`` / ``unsubscribe`` / ``subscribe_depth`` /
  ``unsubscribe_depth`` / ``subscribe_index`` / ``unsubscribe_index`` / ``get_subscriptions`` /
  ``ping`` / ``version`` / ``products_version``.
- Protocol 2 (P2): unsolicited order-book pushes to subscribed clients.

The heavy lifting is shared rather than re-implemented:

- :class:`argus.alramz._signalr.AlRamzSignalR` -- the upstream SignalR 2.1 connection.
- :class:`argus.perpetuals.shared.BaseDispatcher` -- the P1 TCP server, correlation-ID enforcement,
  routing table, per-socket send routine and interactive mode.
- :mod:`argus.protocol` -- P1 framing + P2 packet encoding.
- :class:`argus.alramz._classes.AlRamzP2ConvertClass` -- order book -> P2 CSV.

Two upstream streams feed one P2 shape. ``SubscribeSymbol`` is a top-of-book quote (level 1, partial
updates), ``SubscribeMarketDepthByPrice`` is a full-depth snapshot. Both are normalized to the shared
order-book CSV with ``order_book_depth`` levels (missing levels ``0,0``), so a client decodes every
symbol identically whether it asked for full depth or top of book. When *both* are active for a
symbol, the freshest quote's best bid/ask is overlaid on the depth snapshot's level 1 (the same
``l2Book`` + ``bbo`` overlay Hyperliquid uses).

All upstream subscriptions are owned collectively: the first client to ask for a symbol stream
registers it upstream, the last to leave tears it down (``subscription_expired``). A single shared
upstream connection serves every client and every symbol.
"""

from __future__ import annotations

import os
import time
import threading
import traceback

from argus import __version__ as argus_version
from argus._argus_utils import ArgsObject
from argus.protocol import transmit_mkt_data_with_protocol_2
from argus.perpetuals.shared import BaseDispatcher, PrintInterface, errors as ers
from argus.perpetuals.shared._classes import OutboundMessage
from argus.alramz._classes import (
    SymbolQuote,
    IndexQuote,
    DepthBook,
    AlRamzP2ConvertClass,
    parse_groups,
    merge_order_books,
)
from argus.alramz._signalr import AlRamzSignalR, AlRamzAuthError

__version__ = [1, 0, 0, 0]
pi = PrintInterface("AlRamz")

MODE_TOB = "tob"        # SubscribeSymbol  -- top-of-book quote
MODE_DEPTH = "depth"    # SubscribeMarketDepthByPrice -- full book
MODE_INDEX = "index"    # SubscribeIndex -- index quote

UPSTREAM_SYMBOL = "SubscribeSymbol"
UPSTREAM_INDEX = "SubscribeIndex"
UPSTREAM_DEPTH = "SubscribeMarketDepthByPrice"

DEFAULT_PORT = 9975
MAX_ORDER_BOOK_DEPTH = 20


class AlRamzDispatcher(BaseDispatcher):
    """TCP dispatcher fanning out the Al Ramz feed. See the module docstring and docs/alramz/DISPATCHER.md."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = DEFAULT_PORT,
        socket_session: str | None = None,
        order_book_depth: int | None = None,
        feed_host: str = "feed.alramz.ae",
    ):
        routing_table = {
            "products_version": self._handle_products_version,
            "subscribe": self._handle_subscribe,
            "unsubscribe": self._handle_unsubscribe,
            "subscribe_depth": self._handle_subscribe_depth,
            "unsubscribe_depth": self._handle_unsubscribe_depth,
            "subscribe_index": self._handle_subscribe_index,
            "unsubscribe_index": self._handle_unsubscribe_index,
            "get_subscriptions": self._handle_get_subscriptions,
            "ping": self._handle_ping,
            "version": self._handle_version,
        }
        super().__init__(
            host=host,
            port=port,
            routing_table=routing_table,
            interactive_functions={
                "Refresh Authentication (Chromium)": (
                    "Log in through Chromium, mint a fresh SocketSession and reconnect the upstream feed",
                    self.refresh_auth,
                ),
                "Get Upstream Symbols": (
                    "Symbols / indices / depth currently registered on the shared upstream feed",
                    self._upstream_summary,
                ),
                "Clear Correlation IDs": (
                    "Forget all seen correlation IDs (lets clients reuse them)",
                    lambda: self._corr_id_check.clear_seen_ids(),
                ),
            },
        )

        depth = order_book_depth if order_book_depth is not None else int(os.environ.get("ALRAMZ_ORDERBOOK_DEPTH", 10))
        if depth < 1 or depth > MAX_ORDER_BOOK_DEPTH:
            raise ValueError(f"ALRAMZ_ORDERBOOK_DEPTH must be between 1 and {MAX_ORDER_BOOK_DEPTH}, got {depth}")
        self._orderbook_depth = depth
        self._feed_host = feed_host

        # Last-known merged state, mutated only by the upstream receive thread.
        self._quotes: dict[str, SymbolQuote] = {}
        self._books: dict[str, DepthBook] = {}
        self._indices: dict[str, IndexQuote] = {}

        # Per-(symbol, mode) subscriber sets + the upstream subscription refcount.
        self._mode_lock = threading.Lock()
        self._mode_clients: dict[tuple[str, str], set] = {}

        self.market_data: AlRamzSignalR | None = None
        session = socket_session or os.environ.get("ALRAMZ_SOCKET_SESSION")
        if session:
            self._start_market_data(session)
        else:
            pi.throw_fuss(
                "No ALRAMZ_SOCKET_SESSION set. The feed is offline until you run "
                "`python -m argus.alramz.set_auth` or pick 'Refresh Authentication (Chromium)' "
                "from the interactive menu.",
                title="Al Ramz Not Authenticated",
                notify=True,
            )

    # ------------------------------------------------------------------ upstream
    def _start_market_data(self, socket_session: str) -> None:
        """(Re)create and start the shared upstream connection, replaying the current roster."""
        if self.market_data is not None:
            self.market_data.close()
        self.market_data = AlRamzSignalR(
            socket_session,
            on_push=self._on_upstream_push,
            feed_host=self._feed_host,
        )
        self.market_data.run(main_thread=False)
        # If clients subscribed while we were offline, register them now.
        for (symbol, mode) in list(self._mode_clients):
            self._upstream_subscribe(symbol, mode)

    def refresh_auth(self, write_env: bool = True) -> str | None:
        """Interactive/automation hook: mint a SocketSession via Chromium and reconnect."""
        from argus.alramz.set_auth import update_socket_session

        try:
            session = update_socket_session(write_env=write_env)
        except Exception as e:
            pi.throw_fuss(f"Chromium authentication failed: {e}", title="Al Ramz Auth Failed", notify=True)
            traceback.print_exc()
            return None
        if not session:
            pi.throw_fuss("Chromium login finished but no SocketSession was found.", title="Al Ramz Auth Failed", notify=True)
            return None
        os.environ["ALRAMZ_SOCKET_SESSION"] = session
        self._start_market_data(session)
        return session

    def _upstream_subscribe(self, symbol: str, mode: str) -> None:
        if self.market_data is None:
            return
        if mode == MODE_TOB:
            self.market_data.subscribe_symbol(symbol)
        elif mode == MODE_DEPTH:
            self.market_data.subscribe_depth(symbol, self._orderbook_depth)
        elif mode == MODE_INDEX:
            self.market_data.subscribe_index(symbol)

    def _upstream_unsubscribe(self, symbol: str, mode: str) -> None:
        if self.market_data is None:
            return
        if mode == MODE_TOB:
            self.market_data.unsubscribe_symbol(symbol)
        elif mode == MODE_DEPTH:
            self.market_data.unsubscribe_depth(symbol)
        elif mode == MODE_INDEX:
            self.market_data.unsubscribe_index(symbol)

    def _upstream_summary(self) -> dict:
        if self.market_data is None:
            return {"authenticated": False}
        return {
            "authenticated": True,
            "connected": self.market_data.connected,
            "symbols": self.market_data.symbols,
            "indices": self.market_data.indices,
            "depth": self.market_data.depth_symbols,
        }

    # --------------------------------------------------------- upstream -> P2
    def _on_upstream_push(self, method: str, args: list) -> None:
        """Called on the upstream receive thread for every hub push."""
        if not args:
            return
        if method == UPSTREAM_SYMBOL:
            for group in parse_groups(args[0]):
                quote = SymbolQuote.from_group(group)
                if not quote.exchange:
                    continue
                self._quotes[quote.symbol] = quote
                self._emit_symbol(quote.symbol)
        elif method == UPSTREAM_INDEX:
            for group in parse_groups(args[0]):
                quote = IndexQuote.from_group(group)
                if not quote.exchange:
                    continue
                self._indices[quote.symbol] = quote
                self._emit_index(quote.symbol)
        elif method == UPSTREAM_DEPTH:
            for group in parse_groups(args[0]):
                book = DepthBook.from_group(group)
                if book is None:
                    continue
                self._books[book.symbol] = book
                self._emit_symbol(book.symbol)
        else:
            pi.prt(f"Ignoring unsupported upstream stream {method!r}")

    def _emit_symbol(self, symbol: str) -> None:
        clients = list(self.market_data_routing_table.get(symbol, []))
        if not clients:
            return
        quote = self._quotes.get(symbol)
        depth = self._books.get(symbol)
        depth_book = depth.order_book() if depth else None
        quote_book = quote.order_book() if quote else None
        # Full depth as the baseline, freshest quote's best bid/ask overlaid on level 1.
        book = merge_order_books(depth_book, quote_book, quote_book if depth_book else None)
        timestamp_ms = (quote.timestamp_ms() if quote else None) or int(time.time() * 1000)
        try:
            packet = transmit_mkt_data_with_protocol_2(
                AlRamzP2ConvertClass(
                    symbol=symbol,
                    book=book,
                    order_book_depth=self._orderbook_depth,
                    timestamp_ms=timestamp_ms,
                )
            )
        except (TypeError, ValueError) as e:
            pi.prt(f"Skipping malformed book for {symbol}: {e}")
            return
        self._routine_send_packet_to_clients(clients, packet, f"book update for {symbol}")

    def _emit_index(self, symbol: str) -> None:
        clients = list(self.market_data_routing_table.get(symbol, []))
        if not clients:
            return
        quote = self._indices.get(symbol)
        if quote is None:
            return
        packet = OutboundMessage(action="index_update", data=quote.to_dict()).convert_to_protocol_1()
        self._routine_send_packet_to_clients(clients, packet, f"index update for {symbol}")

    # --------------------------------------------------------- RoutingHelper
    def subscription_expired(self, channel_id: str) -> None:
        """
        Last client left this symbol (any mode) -> drop every remaining upstream subscription for it.
        Normally the per-mode teardown in the handlers already ran; this is the defensive backstop
        for disconnect-driven expiry.
        """
        with self._mode_lock:
            modes = [m for (s, m) in list(self._mode_clients) if s == channel_id]
            for mode in modes:
                self._mode_clients.pop((channel_id, mode), None)
        for mode in modes:
            self._upstream_unsubscribe(channel_id, mode)

    def remove_socket(self, sock) -> None:
        """Override so a dead socket's per-mode refcounts (and upstream subs) are cleaned up too."""
        with self._mode_lock:
            pairs = [(s, m) for (s, m), clients in self._mode_clients.items() if sock in clients]
        for symbol, mode in pairs:
            self._teardown_mode(sock, symbol, mode)
        super().remove_socket(sock)

    # ------------------------------------------------------------ mode table
    def _register_mode(self, sock, symbol: str, mode: str) -> bool:
        """Register `sock` for `(symbol, mode)`; returns True if upstream must be (re)subscribed."""
        with self._mode_lock:
            clients = self._mode_clients.setdefault((symbol, mode), set())
            created = not clients
            clients.add(sock)
        return created

    def _teardown_mode(self, sock, symbol: str, mode: str) -> bool:
        """Remove `sock` from `(symbol, mode)`; returns True if upstream must be unsubscribed."""
        with self._mode_lock:
            clients = self._mode_clients.get((symbol, mode))
            if not clients or sock not in clients:
                return False
            clients.discard(sock)
            expired = not clients
            if expired:
                self._mode_clients.pop((symbol, mode), None)
        if expired:
            self._upstream_unsubscribe(symbol, mode)
        return expired

    def _socket_modes(self, sock, symbol: str) -> set:
        with self._mode_lock:
            return {m for (s, m), clients in self._mode_clients.items() if s == symbol and sock in clients}

    # ------------------------------------------------------------- validation
    @staticmethod
    def _validate_instrument(value, kind: str) -> str:
        if not isinstance(value, str) or "," not in value:
            raise ers.MissingArgumentError(
                f"{value!r} is not a valid Al Ramz {kind} (expected 'EXCHANGE,CODE', e.g. 'ADSM,AEI000201014')"
            )
        exchange, _, code = value.partition(",")
        if not exchange.strip() or not code.strip():
            raise ers.MissingArgumentError(f"{value!r} is not a valid Al Ramz {kind}")
        return value

    @staticmethod
    def _as_list(args, action: str) -> list:
        if not isinstance(args, list):
            raise ers.MissingArgumentError(f"{action} expects a JSON list of instruments, got {type(args).__name__}")
        return args

    # ------------------------------------------------------------- P1 handlers
    def _handle_subscribe(self, args: ArgsObject) -> dict:
        """Subscribe a client to the top-of-book quote stream of one or more symbols."""
        sock = args.sock
        self.add_socket(sock)
        subscribed, failed = [], []
        for raw in self._as_list(args.args, "subscribe"):
            try:
                symbol = self._validate_instrument(raw, "symbol")
                created = self._register_mode(sock, symbol, MODE_TOB)
                self.add_socket_to_subscription(sock, symbol)
                if created:
                    self._upstream_subscribe(symbol, MODE_TOB)
                subscribed.append(symbol)
            except Exception as e:
                failed.append(raw)
                pi.prt(f"Failed to subscribe {sock} to {raw!r}: {e}")
        return {"subscribed": subscribed, "failed": failed}

    def _handle_subscribe_depth(self, args: ArgsObject) -> dict:
        """
        Subscribe a client to the full-depth stream of one or more symbols. Items may be a plain
        ``"EXCHANGE,CODE"`` string or ``{"symbol": "EXCHANGE,CODE"}``; depth is the dispatcher-wide
        ``order_book_depth`` (the venue caps it at 20).
        """
        sock = args.sock
        self.add_socket(sock)
        subscribed, failed = [], []
        for raw in self._as_list(args.args, "subscribe_depth"):
            symbol = raw.get("symbol") if isinstance(raw, dict) else raw
            try:
                symbol = self._validate_instrument(symbol, "symbol")
                created = self._register_mode(sock, symbol, MODE_DEPTH)
                self.add_socket_to_subscription(sock, symbol)
                if created:
                    self._upstream_subscribe(symbol, MODE_DEPTH)
                subscribed.append(symbol)
            except Exception as e:
                failed.append(symbol)
                pi.prt(f"Failed to subscribe {sock} to depth {symbol!r}: {e}")
        return {"subscribed": subscribed, "failed": failed}

    def _handle_subscribe_index(self, args: ArgsObject) -> dict:
        sock = args.sock
        self.add_socket(sock)
        subscribed, failed = [], []
        for raw in self._as_list(args.args, "subscribe_index"):
            try:
                index = self._validate_instrument(raw, "index")
                created = self._register_mode(sock, index, MODE_INDEX)
                self.add_socket_to_subscription(sock, index)
                if created:
                    self._upstream_subscribe(index, MODE_INDEX)
                subscribed.append(index)
            except Exception as e:
                failed.append(raw)
                pi.prt(f"Failed to subscribe {sock} to index {raw!r}: {e}")
        return {"subscribed": subscribed, "failed": failed}

    def _handle_unsubscribe(self, args: ArgsObject) -> dict:
        """Drop every mode this client holds for the symbols (top-of-book, depth and index)."""
        sock = args.sock
        unsubscribed, failed = [], []
        for raw in self._as_list(args.args, "unsubscribe"):
            try:
                symbol = self._validate_instrument(raw, "symbol")
                for mode in list(self._socket_modes(sock, symbol)):
                    self._teardown_mode(sock, symbol, mode)
                if not self._socket_modes(sock, symbol):
                    self.remove_socket_from_subscription(sock, symbol)
                unsubscribed.append(symbol)
            except Exception as e:
                failed.append(raw)
                pi.prt(f"Failed to unsubscribe {sock} from {raw!r}: {e}")
        return {"unsubscribed": unsubscribed, "failed": failed}

    def _handle_unsubscribe_depth(self, args: ArgsObject) -> dict:
        sock = args.sock
        unsubscribed, failed = [], []
        for raw in self._as_list(args.args, "unsubscribe_depth"):
            symbol = raw.get("symbol") if isinstance(raw, dict) else raw
            try:
                symbol = self._validate_instrument(symbol, "symbol")
                self._teardown_mode(sock, symbol, MODE_DEPTH)
                if not self._socket_modes(sock, symbol):
                    self.remove_socket_from_subscription(sock, symbol)
                unsubscribed.append(symbol)
            except Exception as e:
                failed.append(symbol)
                pi.prt(f"Failed to unsubscribe {sock} from depth {symbol!r}: {e}")
        return {"unsubscribed": unsubscribed, "failed": failed}

    def _handle_unsubscribe_index(self, args: ArgsObject) -> dict:
        sock = args.sock
        unsubscribed, failed = [], []
        for raw in self._as_list(args.args, "unsubscribe_index"):
            try:
                index = self._validate_instrument(raw, "index")
                self._teardown_mode(sock, index, MODE_INDEX)
                if not self._socket_modes(sock, index):
                    self.remove_socket_from_subscription(sock, index)
                unsubscribed.append(index)
            except Exception as e:
                failed.append(raw)
                pi.prt(f"Failed to unsubscribe {sock} from index {raw!r}: {e}")
        return {"unsubscribed": unsubscribed, "failed": failed}

    def _handle_get_subscriptions(self, args: ArgsObject) -> dict:
        sock = args.sock
        symbols, depth, indices = [], [], []
        with self._mode_lock:
            for (symbol, mode), clients in self._mode_clients.items():
                if sock not in clients:
                    continue
                if mode == MODE_TOB:
                    symbols.append(symbol)
                elif mode == MODE_DEPTH:
                    depth.append(symbol)
                elif mode == MODE_INDEX:
                    indices.append(symbol)
        return {"symbols": sorted(symbols), "depth": sorted(depth), "indices": sorted(indices)}

    def _handle_ping(self, args: ArgsObject) -> str:
        return "pong"

    def _handle_version(self, args: ArgsObject) -> dict:
        return {"argus": argus_version, "alramz_dispatcher": __version__}

    def _handle_products_version(self, args: ArgsObject) -> dict:
        return {
            "argus": argus_version,
            "alramz_dispatcher": __version__,
            "order_book_depth": self._orderbook_depth,
            "authenticated": self.market_data is not None,
            "connected": bool(self.market_data and self.market_data.connected),
        }


if __name__ == "__main__":
    dispatcher = AlRamzDispatcher()
    dispatcher.run()
    dispatcher.interactive_mode()
    print("Exiting Al Ramz dispatcher")
