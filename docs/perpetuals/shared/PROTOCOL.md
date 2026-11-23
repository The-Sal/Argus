# Perpetual Dispatchers: Shared TCP Protocol

Everything that is identical across the Argus v2 perpetual dispatchers
(`HyperLiquidDispatcher` on `9972`, `LighterDispatcher` on `9974`). It is implemented once in
`argus/perpetuals/shared/` (`BaseDispatcher`, `OutboundMessage`, `P2OrderBookConvertClass`,
`NewFundingRate`) and inherits `PolymarketDispatcher`'s wire format (`docs/POLYMARKET.md`), with
the differences listed under [Differences from Polymarket](#differences-from-polymarketdispatcher).

Per-exchange specs: [`../hyperliquid/DISPATCHER.md`](../hyperliquid/DISPATCHER.md).
Shared account actions: [`ACCOUNT.md`](ACCOUNT.md). Running both side by side: [`CONCURRENCY.md`](CONCURRENCY.md).

---

## Protocols

- **P1**: JSON control protocol. Requests, responses, errors and server pushes.
- **P2**: binary market-data protocol. Order book snapshots pushed to subscribed sockets.

Both travel over the same TCP stream; a client reads packets off one socket and tells them apart by the header.

### P1 packet

```
~<length:04d>|<json-data>
```

`<length>` is the byte length of the UTF-8 JSON (max **9999**). Several packets may arrive in one
`recv`; split them with `argus.protocol.decode_multiple_packets`.

### Request

```json
{ "action": "<name>", "data": { }, "correlation_id": "<uuid>" }
```

- `data` is the action's arguments: a JSON object for most actions, a **list** for `subscribe` /
  `unsubscribe`. `{}` or `null` where an action takes no arguments.
- **`correlation_id` is mandatory** (unlike Polymarket, where it is optional). It must be a string of at most
  **40** characters (`MAX_CORRELATION_ID_LENGTH`) and **unique** per dispatcher: the last
  100 000 ids (`MAX_SEEN_CORRELATION_IDS`) are remembered and a repeat is rejected. Use a uuid4.
  Each dispatcher keeps its own seen-set, so the same id may be used against both.

### Response

```json
{
  "action": "response",
  "data": { },
  "error": null,
  "compressed": false,
  "correlation_id": "<uuid>"
}
```

- `action` is the literal **`"response"`** on success and **`"error"`** on failure. It is *not* the
  request's action name; match on `correlation_id`.
- On failure `data` is `null` and `error` is the exception message (`str(e)`). There is no machine-readable
  error code on the wire; the table under [Errors](#errors) lists the class behind each message.
- `correlation_id` is `null` only when the request could not be attributed to one (undecodable packet,
  missing id).
- A packet that cannot be decoded as P1 gets `{"action": "error", "error": "Unable to decode message. Ensure payload was encoded with Protocol 1", ...}`.
- Each packet in a batch is handled independently: one failing request never swallows the others' responses.

### Auto-compression and the packet cap

| Serialized `data` size | Behaviour |
|---|---|
| < 9500 bytes | sent as-is, `compressed: false` |
| ≥ 9500 bytes | `zlib` level 9, base64; `data` becomes a **string**, `compressed: true` |
| compressed > 9990 bytes | `PacketTooLargeError` (an `error` response; nothing is truncated) |

Decode with `json.loads(zlib.decompress(base64.b64decode(data)))`. Every large collection is therefore
served in pages; see below.

### Pagination

Every list action takes `offset` (default `0`) and `limit` and returns an empty list once `offset` is
past the end. Negative values are rejected. The defaults differ per action and are given in each
action's spec (`25` for the account lists, `20`/`100` for the market-data lists). A short page
(`len(page) < limit`) means the end:

```python
offset, limit, rows = 0, 25, []
while True:
    page = request('get_trades', {'offset': offset, 'limit': limit})['trades']
    rows.extend(page)
    if len(page) < limit:
        break
    offset += limit
```

### Unknown arguments

Account and trading actions **reject unknown keys** with `Unknown argument(s) [...]; accepted: [...]`
rather than ignoring them: on a trading API a mistyped key silently answering for the wrong ledger is worse
than a refusal. The market-data actions read only the keys they know and ignore the rest.

---

## Push messages (server-initiated)

Pushes use the P1 envelope with `correlation_id: null` and are delivered only to sockets that have
**subscribed** to the coin/market concerned (the socket set is populated by `subscribe`).

### `funding_rate_update`

```json
{ "action": "funding_rate_update", "data": { "coin": "BTC", "funding_rate": "0.0000125" }, "error": null, "compressed": false, "correlation_id": null }
```

`funding_rate` is a decimal string (`null` if unknown). Sent:

1. once per coin shortly after `subscribe` (random 0.1–1.0 s jitter, so it does not land before the
   `subscribe` response), and
2. to every subscriber of a coin each time the dispatcher refreshes its perpetual list, at the top of
   every UTC hour.

**Hyperliquid** pushes `account_update` (events `order`, `fill`, `gap`; one record per message) to every
connected client with no `subscribe` required; see `docs/perpetuals/hyperliquid/DISPATCHER.md#account_update`.
**Lighter** pushes the same events, also to every connected client; see
`docs/perpetuals/lighter/DISPATCHER.md#account_update`. Both venues' payloads reuse the `get_orders` /
`get_trades` record shapes.

### `fatal_error`

```json
{ "action": "fatal_error", "data": { "function": "place_order", "exception": "<message>", "traceback": "<last 1500 chars>", "order_execution_blocked": false }, "error": "<message>", "compressed": false, "correlation_id": null }
```

Broadcast to **every connected client** (no `subscribe` needed) when a trading handler wrapped in `fatal_decorator`
(`argus/perpetuals/shared/__init__.py`) raises an error it was not ready for: anything other than the dispatcher's
expected errors (bad arguments, unknown coin, the venue rejecting an action, the kill switch). The state of any
in-flight order or leverage change is then uncertain, so reconcile with `get_orders` / `get_positions`. The
request that triggered it still gets its normal error response. The dispatcher also alerts on its console. It
never engages the order-execution kill switch (`order_execution_blocked` just reports its current state).
Hyperliquid wraps its trading handlers today.

---

## P2: market data

Same framing and CSV layout as Polymarket's P2.

```
~<packet-length:04d><symbol-length:04d>|<symbol><csv-data>L
```

The symbol is the venue's coin identifier (see each exchange's spec). The CSV is:

```
<bid1_px>,<bid1_sz>,...,<bidN_px>,<bidN_sz>,<ask1_px>,<ask1_sz>,...,<askN_px>,<askN_sz>,<exchange_ts_ms>,<server_ts_s>
```

- `N` is the venue's `*_ORDERBOOK_DEPTH` (default **10**, max 20 on Hyperliquid). Missing levels are `0,0`.
- Bids are best-first (highest price first), asks best-first (lowest first).
- `exchange_ts_ms` is the venue's timestamp (unix ms, empty if the venue omitted it); `server_ts_s`
  is the dispatcher's `time.time()` (unix **seconds**, float) at encode time.
- A packet is sent on every book change for the coin; there is no snapshot-on-subscribe request as in
  Polymarket's `orderbook_snapshot`. The first update arrives with the next book change.

---

## Errors

All are `DispatcherError` subclasses from `argus/perpetuals/shared/errors.py` unless noted; the wire
carries only the message.

| Class | Raised when |
|---|---|
| `CorrelationIDError` | request has no `correlation_id` |
| `CorrelationIDLengthTooLongError` / `CorrelationIDAlreadySeenError` | id longer than 40 chars / id already used |
| `InvalidFunctionError` | unknown `action` (`Function <x> is not valid`) |
| `MissingArgumentError` | required argument absent, unknown argument supplied, or a value out of range (also used for a bad `order_type`) |
| `InvalidCoinError` | `subscribe` / `get_funding_rate` on a symbol the venue does not list |
| `OrderExecutionDisabledError` | `place_order` / `place_multiple_orders` / `set_leverage` while the operator's "Block Order Execution" kill switch is on (`Order execution is currently blocked by server configuration.`). Cancels and reads are never blocked; no client action toggles it |
| `RoutingDisabledError` | the hourly perpetual refresh failed repeatedly; routing is off until it recovers (`Routing is currently disabled`) |
| `PacketTooLargeError` | response still > 9990 bytes after compression |
| `AccountNotConfiguredError` | account action on a dispatcher with no (or insufficient) account credentials; market data unaffected |
| `FatalDispatcherError` | code/config bug; not retried, surfaced loudly in the dispatcher's logs |

Venue-specific errors (e.g. Hyperliquid's `ExchangeActionError`) are described in the exchange's spec and
also arrive as plain `error` messages.

---

## Common utilities

Every perpetual dispatcher exposes `products_version` (below) and the account actions in
[`ACCOUNT.md`](ACCOUNT.md). The market-data surface is per venue but follows one vocabulary:
`subscribe` / `unsubscribe`, `search_perpetuals`, `get_funding_rate`, the funding-rate listings and
`perpetual_info`-style metadata.

### `products_version`

```json
{ "action": "products_version", "data": {}, "correlation_id": "<uuid>" }
```

Output:

```json
{ "argus": "<argus version>", "<venue>_dispatcher": [1, 0, 0, 0], "sidecars": {} }
```

The dispatcher version is four integers `[api-breaking, new-functionality, behavioural, bug-fix]`; pin
clients to it rather than to the Argus version.

---

## Differences from PolymarketDispatcher

| | Polymarket | Perpetual dispatchers |
|---|---|---|
| `correlation_id` | optional | **required**, unique |
| Response `action` | echoes the request action | `"response"` / `"error"` |
| Tradable only if subscribed | yes | **no**; trading and account reads need no subscription |
| `account_update` push | yes, only to clients that subscribed | yes (Hyperliquid and Lighter), to every connected client, differently shaped (`data.event` = `order` / `fill` / `gap`) |
| `ping`, `rtt_to_exchange`, `version` | yes | none (`products_version` instead) |
| `orderbook_snapshot` | yes | none |
| Large collections | some un-paginated | every list paginated |
| Size / compression limits | 9500 / 9990 | same |
| Symbol | `<ticker>-<slug>-<asset id>` | the venue's coin string |
