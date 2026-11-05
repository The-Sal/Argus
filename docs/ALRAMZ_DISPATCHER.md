# Al Ramz Dispatcher — API Spec

A TCP dispatcher that streams **Al Ramz** (`feed.alramz.ae`) market data to multiple clients, in the
same style as the TradingView / Hyperliquid / Lighter dispatchers.

- **Protocol 1 (P1)** — request/response over JSON, for `subscribe` / `unsubscribe` /
  `subscribe_depth` / `subscribe_index` / `get_subscriptions` / `ping` / `version`.
- **Protocol 2 (P2)** — unsolicited binary order-book pushes to subscribed clients.

`AlRamzDispatcher` (`argus/alramz/dispatcher.py`) subclasses
`argus.perpetuals.shared.BaseDispatcher`, so the P1 server, mandatory unique `correlation_id`,
routing table, per-socket send locks and interactive mode are shared with the rest of Argus.

**One upstream connection is shared by ALL clients and ALL symbols.** The first client to ask for a
stream registers it upstream, the last to leave tears it down (`subscription_expired`); a reconnect
replays the full roster automatically.

```
client A ─┐                                        ┌─ SubscribeSymbol        ─► P2 (top-of-book)
          │  TCP :9975                              │
client B ─┴──► AlRamzDispatcher ◄───────────────────┤─ SubscribeMarketDepth…  ─► P2 (full depth)
             (P1 in/out, P2 out)   ONE shared       └─ SubscribeIndex         ─► P1 index_update
                     SignalR 2.1 WebSocket
          wss://feed.alramz.ae/IntegrationAPI/sr/signalr/hubs/connect
```

---

## Running it

```bash
# via runtime.py (port 9975 by default)
python3 runtime.py alramz --host localhost --port 9975

# or directly
from argus.alramz import AlRamzDispatcher
d = AlRamzDispatcher(host="localhost", port=9975)
d.run()                 # starts the P1 server (threaded)
d.interactive_mode()    # REPL: refresh auth, list the upstream roster, clear correlation IDs
```

### Authentication (`ALRAMZ_SOCKET_SESSION`)

`feed.alramz.ae` is gated by an opaque **`SocketSession`** token that only the logged-in
`webtrade.alramz.ae` app ever sees. The login cannot be replayed over plain HTTP: the app AES-encrypts
the credentials client-side, runs an invisible reCAPTCHA v3 and often requires an OTP step. So, like
`argus.ib.set_auth`, we drive a real (not headless) Chromium, let it do all of that, and read the token
back out of the network logs.

```bash
# Reads ALRAMZ_USERNAME / ALRAMZ_PASSWORD through the shared secure env loader
# (plaintext .env, or .env.enc.se decrypted just-in-time by SDist), opens Chromium,
# you complete the OTP, and ALRAMZ_SOCKET_SESSION is minted + persisted.
python3 -m argus.alramz.set_auth
```

The dispatcher's interactive menu ("Refresh Authentication (Chromium)") runs the same flow. If the
token is missing/expired the dispatcher still serves P1 (answering `products_version` with
`authenticated: false`) but pushes no market data until it is refreshed.

---

## Protocol 1 — request/response

### Wire framing

`~<4-digit length>|<payload>` — the standard Argus P1 frame. Payload is UTF-8 JSON. Responses whose
`data` serializes to ≥ 9500 bytes are auto-compressed (zlib → base64, `"compressed": true`).

### Request envelope

```json
{ "action": "subscribe", "data": ["ADSM,AEI000201014"], "correlation_id": "<uuid>" }
```

`correlation_id` is **required** and must be unique per dispatcher (max 40 chars, last 100 000 ids
remembered; a repeat is rejected). `data` is a **list** for the subscribe/unsubscribe actions and
`{}`/`null` otherwise. Instruments are always `"EXCHANGE,CODE"` (e.g. `"ADSM,AEI000201014"`,
`"NSDQ,US46090E1038"`, index `"DFM,DFMGI"`).

### Response envelope

```json
{ "action": "response", "data": { "subscribed": ["ADSM,AEI000201014"], "failed": [] },
  "error": null, "compressed": false, "correlation_id": "<uuid>" }
```

`action` is `"response"` on success and `"error"` on failure (`data: null`, `error: "<message>"`).
Match replies by `correlation_id`.

### Actions

#### `subscribe`

Subscribe this client to the **top-of-book** quote stream (`SubscribeSymbol` upstream) for one or more
symbols. This carries best bid/ask + sizes plus session stats (last, change, %, volume) and is pushed
as **partial deltas** upstream; the dispatcher merges them into the full last-known quote.

- request `data`: list of `"EXCHANGE,CODE"`.
- response `data`: `{ "subscribed": [...], "failed": [...] }`.

```json
{ "action": "subscribe", "data": ["ADSM,AEI000201014", "NSDQ,US46090E1038"], "correlation_id": "<uuid>" }
```

#### `subscribe_depth`

Subscribe to the **full aggregated book** (`SubscribeMarketDepthByPrice` upstream). Items may be a
plain string or `{"symbol": "EXCHANGE,CODE"}`; the depth is the dispatcher-wide
`ALRAMZ_ORDERBOOK_DEPTH` (a per-item `depth` key is accepted but ignored).

- response `data`: `{ "subscribed": [...], "failed": [...] }`.

```json
{ "action": "subscribe_depth", "data": ["ADSM,AEI000201014"], "correlation_id": "<uuid>" }
```

#### `subscribe_index`

Subscribe to an index quote (`SubscribeIndex` upstream). Indices are pushed as a P1 `index_update`
message (they have no bid/ask, so they are **not** P2).

```json
{ "action": "subscribe_index", "data": ["DFM,DFMGI", "ADSM,ADI"], "correlation_id": "<uuid>" }
```

#### `unsubscribe` / `unsubscribe_depth` / `unsubscribe_index`

Mirror the three subscribe actions and return `{ "unsubscribed": [...], "failed": [...] }`.
`unsubscribe` drops **every** mode the client holds for the symbol (top-of-book, depth and index); the
`*_depth` / `*_index` variants drop only that mode. A client disconnect unsubscribes everything.

#### `get_subscriptions`

```json
{ "action": "get_subscriptions", "data": null, "correlation_id": "<uuid>" }
```

```json
{ "action": "response", "data": { "symbols": ["ADSM,AEI000201014"], "depth": [], "indices": ["DFM,DFMGI"] },
  "error": null, "compressed": false, "correlation_id": "<uuid>" }
```

#### `ping` / `version` / `products_version`

- `ping` → `"pong"`.
- `version` → `{ "argus": "<version>", "alramz_dispatcher": [1, 0, 0, 0] }`.
- `products_version` → `{ "argus": ..., "alramz_dispatcher": ..., "order_book_depth": 10,
  "authenticated": true, "connected": true }`.

### Errors

Unknown action, missing/duplicate `correlation_id`, bad argument type, unknown mode, or an instrument
that is not `"EXCHANGE,CODE"` returns an `action: "error"` response. When the upstream is not
authenticated, subscribe calls still succeed (they are recorded and replayed on reconnect) but no data
is pushed until `ALRAMZ_SOCKET_SESSION` is valid.

---

## Protocol 2 — market-data push

Unsolicited. Sent to every client subscribed (via `subscribe` **or** `subscribe_depth`) to that symbol,
on every upstream update.

### Frame

`~<packet-length><symbol-length>|<symbol><csv>L` — standard Argus P2 framing
(`argus.protocol.transmit_mkt_data_with_protocol_2`). The `<symbol>` is the `"EXCHANGE,CODE"` string
exactly as subscribed.

### CSV layout

```
bid1_px,bid1_sz, bid2_px,bid2_sz, … bidN_px,bidN_sz,
ask1_px,ask1_sz, ask2_px,ask2_sz, … askN_px,askN_sz,
<exchange_ts_ms>,<server_ts_s>
```

- `N` = `ALRAMZ_ORDERBOOK_DEPTH` (default **10**, max **20**). Missing levels render as `0,0`.
- Bids are best-first (highest first), asks best-first (lowest first).
- `exchange_ts_ms` is the venue timestamp (from field `24` when available, else encode time);
  `server_ts_s` is the dispatcher's `time.time()` at encode time.

**One shape for both streams.** `subscribe` (top-of-book) fills level 1 and zero-pads the rest;
`subscribe_depth` fills up to `N`. When *both* are active for a symbol, the freshest quote's best
bid/ask is overlaid on the depth snapshot's level 1 (the same `l2Book`+`bbo` overlay Hyperliquid uses),
so the top of book stays current between depth snapshots. Clients therefore decode every symbol
identically with `Protocol2Parser(decoding_order=AlRamzP2ConvertClass.field_order(N))`.

Example (symbol `ADSM,AEI000201014`, depth 3; the final field is the dispatcher's encode time):

```
~01010017|ADSM,AEI000201014333,1000,331.9,200,0,0,333.9,1594,334,1500,0,0,1793788045848,1793788349.618698L
```

---

## Push messages (server-initiated)

Pushes use the P1 envelope with `correlation_id: null`.

### `index_update`

```json
{ "action": "index_update",
  "data": { "index": "DFM,DFMGI", "exchange": "DFM", "code": "DFMGI", "last": "5988.67",
            "change": "177.81", "change_pct": "3.06", "volume": "295524517",
            "advancing": "31", "declining": "13", "unchanged": "1100",
            "timestamp": "2026-11-04T14:27:39.6110017+04:00" },
  "error": null, "compressed": false, "correlation_id": null }
```

Sent to every subscriber of the index (`subscribe_index`).

---

## Lifecycle semantics

- **Client disconnect** ⇒ that client's subscriptions are dropped; any symbol/stream with no remaining
  subscribers is unregistered upstream.
- **Upstream drop** ⇒ `AlRamzSignalR` re-negotiates, re-opens the WebSocket, re-runs the `/start`
  handshake and replays the full roster. Client-side P1 subscriptions survive; clients do **not**
  re-subscribe.
- **Dispatcher restart** ⇒ everything is lost; clients must re-subscribe.

---

## Upstream protocol (maintainers)

`feed.alramz.ae` speaks **ASP.NET SignalR 2.1**, hub `integrationhub` (server pushes as
`IntegrationHub`). `argus/alramz/_signalr.py` implements it; the app's own client is
`webtrade.alramz.ae/js/Common/globalSocketManager.js`.

### Handshake

```
GET  https://feed.alramz.ae/IntegrationAPI/sr/signalr/hubs/negotiate
       ?clientProtocol=2.1&SocketSession=<token>&connectionData=[{"name":"integrationhub"}]
WS   wss://feed.alramz.ae/IntegrationAPI/sr/signalr/hubs/connect
       ?transport=webSockets&clientProtocol=2.1&SocketSession=<token>
       &connectionToken=<from negotiate>&connectionData=…&tid=<n>
GET  https://feed.alramz.ae/IntegrationAPI/sr/signalr/hubs/start
       ?transport=webSockets&clientProtocol=2.1&SocketSession=<token>
       &connectionToken=<from negotiate>&connectionData=…&_=<ts>
```

Two non-obvious requirements, both found the hard way:

1. **Echo the negotiate cookie.** The Oracle load balancer sets `X-Oracle-BMC-LBS-Route` on negotiate;
   it must be sent on the WebSocket handshake or the upgrade 401s on a different node. `Origin` must
   be `https://webtrade.alramz.ae` and a browser `User-Agent` is required.
2. **Call `/start`.** Without this HTTP call the socket opens, invocations are ACKed, but the hub never
   reaches `Connected` and pushes **no data**. (The captured HAR was WS-filtered and hid both the
   negotiate and start calls; they were recovered from the app's JS and a full-network capture.)

### Framing

- server-initiated: `{"C": <cursor>, "M": [{"H":"IntegrationHub","M":<method>,"A":[...]}]}` (may batch),
  `{"C":…,"S":1,"M":[]}` on connect, and a `ConnectionStatus` push;
- client-initiated: `{"H":"integrationhub","M":<method>,"A":[...],"I":<n>}`, acked with `{"I":"<n>"}`;
- keep-alive: `{}` both ways on the `KeepAliveTimeout` cadence (observed ~10 s).

### Hub methods

| Method | Argument | Stream |
|---|---|---|
| `SubscribeSymbol` / `UnSubscribeSymbol` | `"EXCH,ISIN\|EXCH,ISIN"` | top-of-book quote + stats (partial deltas) |
| `SubscribeIndex` / `UnSubscribeIndex` | `"EXCH,CODE\|EXCH,CODE"` | index quote |
| `SubscribeMarketDepthByPrice` / `UnSubscribeMarketDepthByPrice` | `"EXCH,ISIN,Depth\|…"` (`Depth` 5, 20 or UT) | full aggregated book (snapshot) |
| `SubscribeMarketDepthByOrder` / `UnSubscribeMarketDepthByOrder` | same shape | order-level book (not exposed by the dispatcher) |
| `SubscribeMarketStatus`, `SubscribeOrderStatus`, `SubscribeGainerLoserMostActive`, `SubscribeOptions` | — | not exposed by the dispatcher |

Payloads are pipe-separated groups; each group is `key=value` comma-pairs. `SubscribeSymbol` is a
shared channel: the server sends a symbol's initial snapshot only in response to a subscribe that
**names** it (idempotent), which is why the dispatcher re-sends the full roster on changes.

### Field maps

**Symbol** (`1`=exchange, `2`=ISIN): `3` open, `4` high, `5` low, `6` prev close, `7` volume, `8` last,
`9` change, `10` change %, **`11` bid, `12` bid size, `13` ask, `14` ask size**, `15` trades,
`16` turnover, `17`/`18`/`19`/`20`/`21`/`22` ranges/avg, `23` flag, `24` ISO timestamp. Deltas carry
only changed keys → merged by key.

**Index** (`1`=exchange, `2`=code): `3`/`4`/`5`/`6` OHLC-ish, `7` volume, `8` last, `9` change,
`10` change %, `15`, `16` turnover, `24` timestamp, `25`/`26`/`27` advancing/declining/unchanged.

**Depth**: `500`=depth N; **bid price `501+4k`, bid size `502+4k`; ask price `503+4k`, ask size
`504+4k`** for `k=0..N-1`, zero-padded, bids descending / asks ascending.

---

## Environment variables

| Env | Used by | Meaning |
|---|---|---|
| `ALRAMZ_SOCKET_SESSION` | dispatcher + `set_auth` | The feed credential. Minted by `python -m argus.alramz.set_auth`; required for market data. |
| `ALRAMZ_ORDERBOOK_DEPTH` | dispatcher | P2 levels per side, default **10**, max **20**. |
| `ALRAMZ_USERNAME` / `ALRAMZ_PASSWORD` | `set_auth` | Login credentials (aliases: `ALRAMZ_USER`/`ALRAMZ_PASS`, `USERNAME`/`PASSWORD`). Loaded via the secure env loader. |
| `ALRAMZ_CHROME_BINARY` | `set_auth` | Optional Chromium/Chrome binary path. |
| `ALRAMZ_CHROME_PROFILE` | `set_auth` | Optional persistent `--user-data-dir` (reuse the login across runs). |
| `ALRAMZ_AUTH_DEBUG` | `set_auth` | When set, dumps cookies + the full network log to `alramz_auth_debug.json`. |

---

## Module layout

| file | what it is |
|---|---|
| `argus/alramz/dispatcher.py` | `AlRamzDispatcher` — P1 surface, per-mode refcounting, P2 fan-out |
| `argus/alramz/_signalr.py` | `AlRamzSignalR` — SignalR 2.1 client (negotiate → connect → `/start` → replay) |
| `argus/alramz/_classes.py` | wire decoders (`SymbolQuote`, `IndexQuote`, `DepthBook`) + `AlRamzP2ConvertClass` |
| `argus/alramz/set_auth.py` | IB-style Chromium login → `ALRAMZ_SOCKET_SESSION` (secure-env aware) |
| `argus/alramz/__init__.py` | exports |
| `runtime.py` | `alramz` target |
