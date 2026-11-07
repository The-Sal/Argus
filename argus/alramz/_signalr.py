"""
Al Ramz market-data transport: a minimal SignalR 2.1 WebSocket client.
=======================================================================

``feed.alramz.ae`` speaks ASP.NET SignalR 2.1 under the ``integrationhub`` hub. This module owns
everything between "I have a `SocketSession`" and "I hand decoded hub messages to a callback":

1. **Negotiate** (``GET .../hubs/negotiate?...``) with a ``requests.Session``.
   ``SocketSession`` is a long opaque token minted by the logged-in ``webtrade.alramz.ae`` app (see
   `argus.alramz.set_auth`); it is the *only* credential and it must be sent on every request.
2. **Connect** the upgrade at the ``Url`` negotiate returns, carrying the negotiate's
   ``ConnectionToken``. Two non-obvious requirements, both found the hard way:
   - the Oracle load balancer's ``X-Oracle-BMC-LBS-Route`` cookie set by negotiate **must** be
     echoed on the WebSocket handshake, or the upgrade 401s on a different node that does not know
     the connection token;
   - the ``Origin`` must be ``https://webtrade.alramz.ae`` and a browser ``User-Agent`` helps.
3. **Handshake / framing** (SignalR 2.x JSON):
   - server-initiated : ``{"C": <cursor>, "M": [{"H": "IntegrationHub", "M": <method>, "A": [...]}]}``
     (``M`` may batch several invocations), plus ``{"C": ..., "S": 1, "M": []}`` on connect and a
     ``ConnectionStatus`` push;
   - client-initiated : ``{"H": "integrationhub", "M": <method>, "A": [...], "I": <int>}``, acked
     with ``{"I": "<int>"}``;
   - keep-alive       : ``{}`` both ways (server every ~10 s; we send one on the same cadence).

The upstream **roster** (symbols, indices, depth) is owned here and replayed on every (re)connect,
so a reconnect never requires clients to re-subscribe -- the same contract as the TradingView /
Hyperliquid dispatchers. Subscribing while disconnected just updates the roster; it is sent when the
handshake completes.
"""

from __future__ import annotations

import json
import os
import ssl
import time
import random
import logging
import threading
import traceback
from urllib.parse import quote

import requests
import websocket
from utils3 import runAsThread

log = logging.getLogger("argus.alramz")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)

#: Server pushes `{}` (and expects the same) on this cadence; KeepAliveTimeout is 20 s.
KEEPALIVE_INTERVAL_SECONDS = 10.0


class AlRamzAuthError(Exception):
    """Raised when negotiate/connect is rejected (almost always a stale/absent `SocketSession`)."""


class AlRamzSignalR:
    """
    One shared SignalR connection for all symbols/clients. Thread-safe: `subscribe_*` /
    `unsubscribe_*` may be called from P1 handler threads while the receive loop and the keepalive
    thread run.
    """

    def __init__(
        self,
        socket_session: str,
        on_push=None,
        *,
        feed_host: str = "feed.alramz.ae",
        hub: str = "integrationhub",
        url_path: str = "/IntegrationAPI/sr/signalr/hubs",
        origin: str = "https://webtrade.alramz.ae",
        user_agent: str = DEFAULT_USER_AGENT,
        reconnect_delay: float = 1.0,
    ):
        if not socket_session:
            raise AlRamzAuthError(
                "No SocketSession supplied. Set ALRAMZ_SOCKET_SESSION or run "
                "`python -m argus.alramz.set_auth` to mint one from a Chromium login."
            )
        self._socket_session = socket_session
        self._on_push = on_push
        self._feed_host = feed_host
        self._hub = hub
        self._hub_server = hub[:1].upper() + hub[1:]  # server pushes as "IntegrationHub"
        self._url_path = url_path.rstrip("/")
        self._origin = origin
        self._user_agent = user_agent
        self._reconnect_delay = reconnect_delay

        self._http = requests.Session()
        self._http.headers.update({"User-Agent": user_agent, "Origin": origin})

        self._ws: websocket.WebSocketApp | None = None
        self._send_lock = threading.Lock()
        self._roster_lock = threading.Lock()
        self._invoke_id = 0
        self._handshake = threading.Event()
        # SignalR 2.x needs an explicit HTTP `/start` after the transport connects before the hub
        # reaches Connected (and starts pushing). The HAR hid it because it was WS-filtered.
        self._started = threading.Event()
        self._negotiate_result: dict | None = None
        self._running = False
        self._keepalive_stop = threading.Event()
        self._last_connected = 0.0

        # Upstream roster (owned here, replayed on reconnect).
        self._symbols: list[str] = []
        self._indices: list[str] = []
        self._depth: dict[str, int] = {}

    # ------------------------------------------------------------------ state
    @property
    def socket_session(self) -> str:
        return self._socket_session

    @property
    def symbols(self) -> list[str]:
        with self._roster_lock:
            return list(self._symbols)

    @property
    def indices(self) -> list[str]:
        with self._roster_lock:
            return list(self._indices)

    @property
    def depth_symbols(self) -> dict[str, int]:
        with self._roster_lock:
            return dict(self._depth)

    @property
    def connected(self) -> bool:
        return self._handshake.is_set()

    def update_socket_session(self, socket_session: str) -> None:
        """Swap in a refreshed token; takes effect on the next (re)connect."""
        self._socket_session = socket_session

    # ------------------------------------------------------------- negotiate
    def _negotiate(self) -> dict:
        connection_data = quote(json.dumps([{"name": self._hub}]), safe="")
        url = (
            f"https://{self._feed_host}{self._url_path}/negotiate"
            f"?clientProtocol=2.1&connectionData={connection_data}"
            f"&SocketSession={quote(self._socket_session, safe='')}"
        )
        try:
            resp = self._http.get(url, timeout=30)
        except requests.RequestException as e:
            raise AlRamzAuthError(f"Negotiate request failed: {e}") from e
        if resp.status_code == 401:
            raise AlRamzAuthError(
                "Negotiate returned 401 Unauthorized -- the SocketSession is missing or expired. "
                "Re-run `python -m argus.alramz.set_auth`."
            )
        resp.raise_for_status()
        try:
            payload = resp.json()
        except ValueError as e:
            raise AlRamzAuthError(f"Negotiate returned non-JSON: {resp.text[:200]!r}") from e
        if not payload.get("ConnectionToken"):
            raise AlRamzAuthError(f"Negotiate response missing ConnectionToken: {payload!r}")
        return payload

    def _connection_query(self, negotiate: dict, extra: str) -> str:
        connection_data = quote(json.dumps([{"name": self._hub}]), safe="")
        return (
            f"{negotiate['Url']}/{extra}"
            f"?transport=webSockets&clientProtocol=2.1"
            f"&SocketSession={quote(self._socket_session, safe='')}"
            f"&connectionToken={quote(negotiate['ConnectionToken'], safe='')}"
            f"&connectionData={connection_data}"
        )

    def _connect_url(self, negotiate: dict) -> str:
        return f"wss://{self._feed_host}{self._connection_query(negotiate, 'connect')}&tid={random.randint(1, 2_000_000_000)}"

    def _start_connection(self) -> None:
        """
        Complete the SignalR handshake with an HTTP ``/start`` (the app's `globalSocketManager.js`
        does it via `$.connection.hub.start()`, but the WS-filtered HAR never captured it). Without
        it the server accepts the socket and ACKs invocations yet never pushes market data.
        """
        negotiate = self._negotiate_result
        if not negotiate:
            return
        url = f"https://{self._feed_host}{self._connection_query(negotiate, 'start')}&_={int(time.time() * 1000)}"
        try:
            resp = self._http.get(url, timeout=30)
            if resp.status_code >= 400:
                log.warning("Al Ramz /start returned %s", resp.status_code)
            else:
                log.info("Al Ramz SignalR /start -> %s", resp.status_code)
        except requests.RequestException as e:
            log.warning("Al Ramz /start request failed: %s", e)

    def _cookie_header(self) -> str:
        return "; ".join(f"{c.name}={c.value}" for c in self._http.cookies)

    # --------------------------------------------------------------- sending
    def _invoke(self, method: str, args: list) -> int | None:
        """Send one hub invocation. Returns the invocation id, or None if not connected."""
        ws = self._ws
        if ws is None or not self._handshake.is_set():
            return None
        with self._send_lock:
            self._invoke_id += 1
            invocation_id = self._invoke_id
            try:
                ws.send(json.dumps({"H": self._hub, "M": method, "A": args, "I": invocation_id}))
            except Exception as e:
                log.warning("Failed to send %s: %s", method, e)
                return None
            return invocation_id

    def _ready(self, timeout: float = 10.0) -> bool:
        # Both the `/start` (Connected) and the server's S=1 frame must have happened before any
        # subscription will be acted on.
        return self._started.wait(timeout=timeout) and self._handshake.wait(timeout=timeout)

    # --------------------------------------------------- subscription roster
    def subscribe_symbol(self, symbol: str) -> None:
        with self._roster_lock:
            if symbol not in self._symbols:
                self._symbols.append(symbol)
        if self._ready():
            self._sync_symbols()

    def unsubscribe_symbol(self, symbol: str) -> None:
        with self._roster_lock:
            if symbol in self._symbols:
                self._symbols.remove(symbol)
        if self._handshake.is_set():
            self._invoke("UnSubscribeSymbol", [symbol])

    def subscribe_index(self, index: str) -> None:
        with self._roster_lock:
            if index not in self._indices:
                self._indices.append(index)
        if self._ready():
            self._sync_indices()

    def unsubscribe_index(self, index: str) -> None:
        with self._roster_lock:
            if index in self._indices:
                self._indices.remove(index)
        if self._handshake.is_set():
            self._invoke("UnSubscribeIndex", [index])

    def subscribe_depth(self, symbol: str, depth: int) -> None:
        with self._roster_lock:
            self._depth[symbol] = depth
        if self._ready():
            self._invoke("SubscribeMarketDepthByPrice", [f"{symbol},{depth}"])

    def unsubscribe_depth(self, symbol: str) -> None:
        with self._roster_lock:
            self._depth.pop(symbol, None)
        if self._handshake.is_set():
            self._invoke("UnSubscribeMarketDepthByPrice", [f"|{symbol}"])

    # Full-replace syncs (safe/idempotent; the server replaces the client's symbol set).
    def _sync_symbols(self) -> None:
        with self._roster_lock:
            symbols = list(self._symbols)
        if symbols:
            self._invoke("SubscribeSymbol", ["|".join(symbols)])

    def _sync_indices(self) -> None:
        with self._roster_lock:
            indices = list(self._indices)
        if indices:
            self._invoke("SubscribeIndex", ["|".join(indices)])

    def _replay_roster(self) -> None:
        """Re-register everything after a (re)connect. Best-effort per stream."""
        self._sync_symbols()
        self._sync_indices()
        for symbol, depth in self.depth_symbols.items():
            self._invoke("SubscribeMarketDepthByPrice", [f"{symbol},{depth}"])

    # ------------------------------------------------------------- receiving
    def _on_message(self, ws, raw):
        if isinstance(raw, (bytes, bytearray)):
            raw = bytes(raw).decode("utf-8", errors="replace")
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            return  # SignalR/keepalive noise we do not care about

        if not isinstance(obj, dict):
            return

        if obj.get("S") == 1:
            if not self._handshake.is_set():
                self._handshake.set()
                self._last_connected = time.time()
                log.info("Al Ramz SignalR handshake complete; replaying roster")
                try:
                    self._replay_roster()
                except Exception:
                    traceback.print_exc()

        for message in obj.get("M") or []:
            if not isinstance(message, dict):
                continue
            method = message.get("M")
            args = message.get("A") or []
            if not method:
                continue
            if method == "ConnectionStatus":
                log.info("Al Ramz feed ConnectionStatus: %s", args)
                continue
            if self._on_push is not None:
                try:
                    self._on_push(method, args)
                except Exception:
                    # A malformed/unexpected frame must never kill the receive loop.
                    log.warning("Error handling upstream %s frame", method)
                    traceback.print_exc()

    def _on_open(self, ws):
        self._handshake.clear()
        self._started.clear()
        # Complete the SignalR connection before the receive loop starts handling messages. This
        # blocks ~100 ms; anything the server sends meanwhile is buffered by the socket.
        self._start_connection()
        self._started.set()

    def _on_close(self, ws, code, msg):
        self._handshake.clear()
        self._started.clear()
        log.warning("Al Ramz SignalR connection closed (%s: %s)", code, msg)

    def _on_error(self, ws, error):
        log.warning("Al Ramz SignalR error: %s", error)

    # ------------------------------------------------------------- lifecycle
    @runAsThread
    def _keepalive_loop(self):
        while not self._keepalive_stop.wait(KEEPALIVE_INTERVAL_SECONDS):
            if self._handshake.is_set() and self._ws is not None:
                with self._send_lock:
                    try:
                        self._ws.send("{}")
                    except Exception:
                        pass

    def _run_once(self):
        negotiate = self._negotiate()
        self._negotiate_result = negotiate
        header = [f"User-Agent: {self._user_agent}", f"Cookie: {self._cookie_header()}"]
        self._ws = websocket.WebSocketApp(
            self._connect_url(negotiate),
            header=header,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._ws.run_forever(
            origin=self._origin,
            sslopt={"cert_reqs": ssl.CERT_REQUIRED},
            skip_utf8_validation=True,
        )

    @runAsThread
    def run(self, main_thread: bool = False):
        """
        Connect-and-reconnect forever. `main_thread` is accepted for API parity with the other
        Argus market-data classes and ignored (this always runs on a background thread).
        """
        _ = main_thread
        self._running = True
        self._keepalive_stop.clear()
        threading.Thread(target=self._keepalive_loop, name="alramz-keepalive", daemon=True).start()
        while self._running:
            try:
                self._run_once()
            except AlRamzAuthError as e:
                # Retrying an auth failure forever just spams the server; surface it and back off
                # longer. The dispatcher's interactive "Refresh Authentication" recovers.
                log.error("Al Ramz auth error: %s", e)
                self._keepalive_stop.wait(30)
            except Exception:
                traceback.print_exc()
            if self._running:
                self._keepalive_stop.wait(self._reconnect_delay)

    def close(self):
        self._running = False
        self._keepalive_stop.set()
        self._handshake.clear()
        if self._ws is not None:
            self._ws.keep_running = False
            try:
                self._ws.close()
            except Exception:
                pass
