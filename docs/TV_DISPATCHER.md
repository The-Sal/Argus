# TradingView Dispatcher — API Spec

A TCP dispatcher that streams **TradingView quote data** to multiple clients, in the same
style as the Polymarket / Perpetuals dispatchers.

- **Protocol 1 (P1)** — request/response over JSON, for `subscribe` / `unsubscribe` /
  `get_subscriptions` / `ping` / `version`.
- **Protocol 2 (P2)** — unsolicited binary-ish push of live quote updates to subscribed clients.

**One upstream TradingView WebSocket is shared by ALL clients and ALL symbols** (multi-symbol
quote sessions). There is deliberately no per-asset connection: the first client to subscribe a
symbol registers it on the shared upstream session, and the last client to unsubscribe (or
disconnect) removes it.

```
client A ─┐                                        ┌─ qsd NASDAQ:AAPL ──► P2 push to A, B
          │  TCP :9974                              │
client B ─┴──► TradingViewDispatcher ◄──────────────┤
             (P1 in/out, P2 out)      ONE shared    └─ qsd BINANCE:BTCUSDT ─► P2 push to C
                          wss (multi-symbol quote sessions)
                     wss://data.tradingview.com/socket.io/websocket
```

## Running it

```bash
# via runtime.py (port 9974 by default; 9972 is taken by the other dispatchers, 9973 by IB debug)
python runtime.py tradingview --host localhost --port 9974

# or directly
from argus.tv import TradingViewDispatcher
d = TradingViewDispatcher(host="localhost", port=9974)
d.run()                 # starts the P1 server (threaded)
d.interactive_mode()    # REPL: toggle P2 printing, list upstream symbols, clear correlation IDs
```

Optional auth: set the `TOKEN` env var to a TradingView auth token (same convention as the rest
of `argus.tv`). Without it the dispatcher connects anonymously.

---

## Protocol 1 — request/response

### Wire framing (both directions)

`~<4-digit length>|<payload>` — the standard Argus P1 frame. Payload is UTF-8 JSON.
Max payload 9999 bytes (hard limit). If a response `data` serializes to ≥ 9500 bytes it is
auto-compressed (zlib → base64) and `"compressed": true` is set.

### Request envelope (client → dispatcher)

```json
{
  "action": "subscribe",
  "data": ["NASDAQ:AAPL", "BINANCE:BTCUSDT"],
  "correlation_id": "5f2c1a3e-8b4d-4c6a-9e0f-1d2e3f4a5b6c"
}
```

| field            | type   | notes                                                                 |
|------------------|--------|-----------------------------------------------------------------------|
| `action`         | string | one of `subscribe`, `unsubscribe`, `get_subscriptions`, `ping`, `version` |
| `data`           | any    | action-specific (see below); may be `null`                            |
| `correlation_id` | string | **required**. UUID-ish, unique per client session, ≤ 40 chars. Reusing a seen ID is rejected. |

### Response envelope (dispatcher → client)

```json
{
  "action": "response",
  "data": { "subscribed": ["NASDAQ:AAPL"], "failed": [] },
  "error": null,
  "compressed": false,
  "correlation_id": "5f2c1a3e-8b4d-4c6a-9e0f-1d2e3f4a5b6c"
}
```

| field            | type                  | notes                                                    |
|------------------|-----------------------|----------------------------------------------------------|
| `action`         | string                | `"response"` on success, `"error"` on failure            |
| `data`           | any                   | action-specific result; `null` on error                  |
| `error`          | string \| null        | human-readable error message when `action == "error"`    |
| `compressed`     | bool                  | `true` ⇒ `data` is a base64(zlib(json)) string            |
| `correlation_id` | string                | echoed back from the request                             |

### Actions

#### `subscribe`

Subscribe this client to live P2 updates for one or more TradingView symbols.

- request `data`: list of symbol strings, e.g. `["NASDAQ:AAPL", "BINANCE:BTCUSDT"]`.
  Symbols must use TradingView's `EXCHANGE:SYMBOL` format (must contain `:`).
- response `data`:

```json
{ "subscribed": ["NASDAQ:AAPL"], "failed": [] }
```

Subscribing is idempotent per (client, symbol). The **first** subscriber for a symbol causes the
symbol to be registered on the shared upstream TradingView session; subsequent subscribers just
join the fan-out.

#### `unsubscribe`

- request `data`: list of symbol strings.
- response `data`:

```json
{ "unsubscribed": ["NASDAQ:AAPL"], "failed": [] }
```

When the **last** client unsubscribes from a symbol, it is removed from the upstream session.
Unsubscribing a symbol you never subscribed to is a no-op (reported under `unsubscribed`).

#### `get_subscriptions`

- request `data`: `null`.
- response `data`:

```json
{ "subscriptions": ["NASDAQ:AAPL", "BINANCE:BTCUSDT"] }
```

The symbols **this client** is subscribed to.

#### `ping`

- request `data`: `null`.
- response `data`: `"pong"` (a plain JSON string).

#### `version`

- request `data`: `null`.
- response `data`:

```json
{ "argus": "<argus package version>", "tradingview_dispatcher": [1, 0, 0, 0] }
```

### Error responses

Any of: unknown `action`, missing/invalid `correlation_id`, duplicate correlation ID, malformed
JSON/frame, bad argument types →

```json
{ "action": "error", "data": null, "error": "<message>", "compressed": false, "correlation_id": "<echoed or null>" }
```

---

## Protocol 2 — market data push (dispatcher → subscribed clients)

Unsolicited. Sent to every client subscribed to the symbol, as soon as an update arrives from the
upstream TradingView session (last-price ticks, bid/ask changes, volume, ...). Clients do not
acknowledge these.

**Merged state:** TradingView itself sends *partial* per-field updates (its main quote session
updates `lp`/`ch`/`chp`/`volume`, the snapshotter session updates `bid`/`ask`). The dispatcher
merges each update into the last-known quote for that symbol, so **every P2 packet is the full
last-known state** of the quote — clients never have to merge partials. A field renders as `0`
only if it has never been observed since the dispatcher started (e.g. `bid`/`ask` on exchanges
that don't report them).

### Frame

`~<packet-length><symbol-length>|<symbol><csv>L` — standard Argus P2 framing
(`argus.protocol.transmit_mkt_data_with_protocol_2`).

- `<packet-length>` (4 digits): byte count of everything AFTER this 4-digit field, i.e.
  the symbol-length field + `|` + symbol + csv + the trailing `L`. Total frame = `5 + packet-length` bytes.
- `<symbol>`: the TradingView symbol string exactly as subscribed, e.g. `NASDAQ:AAPL`.
- `L`: literal terminator byte (the parser expects it).
- `<csv>`: comma-separated decimal strings, **in this exact order**:

| # | field             | source (TradingView field)      | meaning                                   |
|---|-------------------|---------------------------------|-------------------------------------------|
| 1 | `bid`             | `bid`                           | best bid                                  |
| 2 | `bid_size`        | `bid_size`                      | size at best bid                          |
| 3 | `ask`             | `ask`                           | best ask                                  |
| 4 | `ask_size`        | `ask_size`                      | size at best ask                          |
| 5 | `last`            | `lp`                            | last traded price                         |
| 6 | `change`          | `ch`                            | absolute change vs previous close         |
| 7 | `change_pct`      | `chp`                           | percent change vs previous close          |
| 8 | `volume`          | `volume`                        | session volume                            |
| 9 | `timestamp`       | `lp_time`                       | epoch seconds of the last trade (from TV) |
| 10| `transmission_time` | dispatcher's `time.time()`   | when the dispatcher emitted the packet    |

Missing/unknown values are rendered as `0`. Decode with
`argus.protocol.Protocol2Parser(decoding_order=TVP2ConvertClass.FIELD_ORDER)`.

Example (symbol `NASDAQ:AAPL`, 11 chars; csv is 75 bytes ⇒ packet-length = 5 + 11 + 75 + 1 = 92):

```
~00920011|NASDAQ:AAPL232.45,40,232.47,30,232.46,1.23,0.53,51234567,1758000000.123,1758000000.456L
```

---

## Lifecycle semantics

- **Client disconnect** ⇒ all of that client's subscriptions are dropped (and upstream symbols
  with no remaining subscribers are unregistered).
- **Upstream TradingView drop** ⇒ the dispatcher auto-reconnects (1 s backoff, forever) and
  re-registers the full symbol roster automatically. Client-side P1 subscriptions survive this —
  clients do NOT need to re-subscribe.
- **Dispatcher restart** ⇒ everything is lost; clients must re-subscribe.

## Minimal client example

```python
import socket, json, uuid
from argus.protocol import Protocol2Parser
from argus.tv import TVP2ConvertClass

s = socket.create_connection(("localhost", 9974))

def p1(action, data=None):
    req = {"action": action, "data": data, "correlation_id": str(uuid.uuid4())}
    payload = json.dumps(req).encode()
    s.sendall(f"~{len(payload):04d}|".encode() + payload)
    # read one P1 frame (length-prefixed)
    header = b""
    while not header.endswith(b"|"):
        header += s.recv(1)
    n = int(header[1:-1])
    body = b""
    while len(body) < n:
        body += s.recv(n - len(body))
    return json.loads(body)

print(p1("subscribe", ["NASDAQ:AAPL"]))
print(p1("get_subscriptions"))

parser = Protocol2Parser(decoding_order=TVP2ConvertClass.FIELD_ORDER)
buf = b""
while True:
    buf += s.recv(4096)
    while (start := buf.find(b"~")) != -1:
        n = int(buf[start+1:start+5])      # packet-length: bytes after the 4-digit field
        end = start + 5 + n                # includes the trailing L terminator
        if len(buf) < end: break
        sym, values = parser.parse(buf[start:end])
        print(sym, dict(zip(TVP2ConvertClass.FIELD_ORDER, values)))
        buf = buf[end:]
```

## Module layout

| file                        | what it is                                                                    |
|-----------------------------|-------------------------------------------------------------------------------|
| `argus/tv/dispatcher.py`    | `TradingViewDispatcher`, `TVP2ConvertClass`, `TradingViewQuoteWss`, `DispatcherQuoteSession` |
| `argus/tv/__init__.py`      | pre-existing TV protocol classes (`QuoteSession`, ...); now also exports the dispatcher |
| `tests/test_tv_dispatcher.py` | offline unit tests (P2 round-trip, session roster logic, P1 routing)        |
