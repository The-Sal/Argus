"""Client for argus-polymarket-db (APDB): a Unix domain socket JSON server
that crawls and serves Polymarket Gamma events, replacing the in-process
`_all_events_cache` dict PolymarketDispatcher used to hold every tracked
market in memory (several GB under load at ~16k+ events). APDB owns the
Gamma crawl and its own refresh interval; this dispatcher now only queries
it, never holds the event set itself.

Wire protocol: one JSON object per line in, one JSON object per line out,
over a persistent Unix domain socket connection. See the server's
`src/api.rs` for the authoritative request/response shapes; summarized here:

    request:  {"op": "<name>", ...op-specific fields}
    success:  {"ok": true,  "db_version": "...", "result": {...}}
    error:    {"ok": false, "db_version": "...", "error": {"code": ..., "message": ...}}
"""
import os
import json
import socket
import threading
from typing import Iterator, List, Optional

from argus.polymarket_direct import PolymarketEvent

DEFAULT_SOCKET_PATH = "/tmp/argus_polymarket_db.sock"
# Same env var name the Rust server itself reads for its own bind address —
# a single source of truth for both processes.
SOCKET_PATH_ENV_VAR = "APDB_BIND_ADDRESS"

# Server-side caps (see argus-polymarket-db/src/api.rs) — requesting above
# these gets silently clamped server-side, so we page at the max to
# minimize round trips.
_LIST_EVENTS_MAX_LIMIT = 500
_LIST_TICKERS_MAX_LIMIT = 20_000

_SOCKET_TIMEOUT_SECS = 30.0


class APDBError(Exception):
    """Raised on any APDB transport failure or `ok:false` response."""


class APDBClient:
    """Thread-safe client for the APDB UDS server.

    Each thread gets its own persistent socket connection (via
    threading.local), connected lazily on first use and transparently
    reconnected on failure — including the common case where the server's
    own idle-connection read timeout (60s, see src/server.rs) closes a
    thread's connection between infrequent calls. This avoids needing a
    lock around request/response round trips (the NDJSON protocol is
    strictly one-request-one-response per connection; interleaving two
    threads' requests on a shared socket would corrupt the stream) while
    still giving real concurrency across the dispatcher's many threads (WS
    callbacks, background refresh, per-client request handlers).
    """

    def __init__(self, socket_path: Optional[str] = None, timeout: float = _SOCKET_TIMEOUT_SECS):
        self._socket_path = socket_path or os.environ.get(SOCKET_PATH_ENV_VAR, DEFAULT_SOCKET_PATH)
        self._timeout = timeout
        self._local = threading.local()

    def _get_connection(self):
        sock = getattr(self._local, "sock", None)
        rfile = getattr(self._local, "rfile", None)
        if sock is not None and rfile is not None:
            return sock, rfile
        return self._connect()

    def _connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self._timeout)
        try:
            sock.connect(self._socket_path)
        except OSError as e:
            sock.close()
            raise APDBError(
                f"Could not connect to APDB at {self._socket_path}: {e}. "
                f"Is the argus-polymarket-db server running?"
            ) from e
        rfile = sock.makefile("rb")
        self._local.sock = sock
        self._local.rfile = rfile
        return sock, rfile

    def _drop_connection(self):
        sock = getattr(self._local, "sock", None)
        rfile = getattr(self._local, "rfile", None)
        if rfile is not None:
            try:
                rfile.close()
            except Exception:
                pass
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        self._local.sock = None
        self._local.rfile = None

    def _call(self, op: str, **fields) -> dict:
        """Sends one NDJSON request and returns the parsed `result` dict.

        Raises APDBError on any transport failure or `ok:false` response.
        Retries the round trip once (reconnecting first) if the connection
        turns out to be dead — covers a server-side idle timeout or restart
        without masking a genuinely unreachable server, which will still
        fail on the second attempt.
        """
        request = {"op": op}
        request.update(fields)
        line = (json.dumps(request) + "\n").encode("utf-8")

        last_error: Optional[Exception] = None
        response = None
        for _attempt in range(2):
            try:
                sock, rfile = self._get_connection()
                sock.sendall(line)
                response_line = rfile.readline()
                if not response_line:
                    raise APDBError("APDB closed the connection (idle timeout or restart).")
                response = json.loads(response_line)
            except (OSError, APDBError, json.JSONDecodeError) as e:
                self._drop_connection()
                last_error = e
                continue
            else:
                break
        else:
            raise APDBError(f"APDB request '{op}' failed after retry: {last_error}") from last_error

        if not response.get("ok", False):
            error = response.get("error") or {}
            raise APDBError(
                f"APDB request '{op}' failed [{error.get('code', 'unknown')}]: "
                f"{error.get('message', response)}"
            )
        return response.get("result") or {}

    def db_info(self) -> dict:
        return self._call("db_info")

    def get_event(self, ticker: str) -> Optional[PolymarketEvent]:
        """Mirrors dict.get(ticker, None) semantics: returns None on a
        miss, never raises for "not found" — only for a genuine APDB
        transport/protocol failure."""
        result = self._call("get_event", ticker=ticker)
        event_json = result.get("event")
        if event_json is None:
            return None
        return PolymarketEvent.from_dict(event_json)

    def iter_all_events(self) -> Iterator[PolymarketEvent]:
        """Pages through every event APDB knows about, ticker-sorted. Each
        page is one APDB round trip; only one page of events is held in
        memory at a time — use this instead of materializing a list when
        walking the full set."""
        after = None
        while True:
            result = self._call("list_events", after=after, limit=_LIST_EVENTS_MAX_LIMIT)
            for event_json in result.get("events", []):
                yield PolymarketEvent.from_dict(event_json)
            after = result.get("next_after")
            if after is None:
                return

    def all_tickers(self) -> List[str]:
        """Materializes every ticker APDB knows about, sorted. Cheap
        compared to iter_all_events: no event bodies are transferred, just
        ticker strings."""
        tickers: List[str] = []
        after = None
        while True:
            result = self._call("list_tickers", after=after, limit=_LIST_TICKERS_MAX_LIMIT)
            tickers.extend(result.get("tickers", []))
            after = result.get("next_after")
            if after is None:
                return tickers
