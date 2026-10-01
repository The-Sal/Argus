# HyperLiquidDispatcher TCP Protocol Specification

## Overview

`HyperLiquidDispatcher` (`argus/perpetuals/hyper/__init__.py`) is a TCP server exposing Hyperliquid
perpetuals (the default dex and every builder-deployed HIP-3 dex) through the same two protocols as
`PolymarketDispatcher`:

- **P1**: JSON request/response and server pushes
- **P2**: binary order-book stream

**Default connection:** `localhost:9972` (`python runtime.py hyperliquid --port 9972`)

Framing, the envelope, `correlation_id` rules, auto-compression, pagination, error classes, the P2 CSV layout and
the `funding_rate_update` push are **shared by all perpetual dispatchers** and specified once in
[`../shared/PROTOCOL.md`](../shared/PROTOCOL.md). Read that first; it is not repeated here. The six
account actions (`get_balance` ... `get_funding_payments`) have their record shapes in
[`../shared/ACCOUNT.md`](../shared/ACCOUNT.md); this document lists them with only what is Hyperliquid-specific.

Quick reminders from the shared spec: `correlation_id` is **required** and unique; success responses carry
`"action": "response"` (failures `"error"`); responses ≥ 9500 bytes are zlib+base64 compressed
(`compressed: true`); nothing here needs a prior `subscribe`.

## Endpoint index

| Action | Group | Paginated | Signed |
|---|---|---|---|
| [`products_version`](#products_version) | meta | no | no |
| [`get_dexs`](#get_dexs) | market info | no | no |
| [`get_perpetuals_for_dex`](#get_perpetuals_for_dex) | market info | yes | no |
| [`get_funding_rates_for_all_perpetuals`](#get_funding_rates_for_all_perpetuals) | market info | yes | no |
| [`get_funding_rate`](#get_funding_rate) | market info | no | no |
| [`perpetual_info`](#perpetual_info) | market info | no | no |
| [`search_perpetuals`](#search_perpetuals) | market info | no | no |
| [`subscribe`](#subscribe) / [`unsubscribe`](#unsubscribe) | market data | no | no |
| [`funding_rate_update`](#funding_rate_update) | push (server-initiated) | n/a | no |
| [`account_update`](#account_update) | push (server-initiated) | n/a | no |
| [`get_balance`](#account-actions) | account | no | no |
| [`get_positions`](#account-actions) | account | yes | no |
| [`get_orders`](#account-actions) | account | yes | no |
| [`get_order_status`](#account-actions) | account | no | no |
| [`get_trades`](#account-actions) | account | yes | no |
| [`get_funding_payments`](#account-actions) | account | yes | no |
| [`get_account_fees`](#get_account_fees) | account (HL only) | no | no |
| [`get_rate_limit_usage`](#get_rate_limit_usage) | account (HL only) | no | no |
| [`place_order`](#place_order) | trading | no | **yes** |
| [`cancel_order`](#cancel_order) | trading | no | **yes** |

Deliberately absent: order modification, cancel-all (Hyperliquid's only bulk cancel is
`scheduleCancel`, a one-shot trigger ≥ 5 s ahead, max 10/day), `place_multiple_orders`.

---

## Symbols and dexes

- A **coin** is `"BTC"` on the default dex, or `"<dex>:<COIN>"` (e.g. `"xyz:AAPL"`) on a HIP-3 dex. This
  string is the symbol everywhere: `subscribe`, `get_funding_rate`, `perpetual_info`, `place_order`, the P2
  packet symbol, and the `name` field of account records.
- A **dex** is `""` for the default dex, otherwise the HIP-3 dex name (the part before `:`).

## Configuration

| Env | Needed for |
|---|---|
| `HYPERLIQUID_WALLET_ADDRESS` | startup; the `user` on every account read. Must be the **master** address (an API/agent wallet reads as an empty account) |
| `HYPERLIQUID_PRIVATE_KEY` | startup; signing for `place_order` / `cancel_order`. Must belong to the master address |
| `HYPERLIQUID_ORDERBOOK_DEPTH` | P2 levels per side, default 10, max 20 (larger raises `ValueError` at startup) |
| `HYPERLIQUID_MAX_SOCKET_RETRIES`, `HYPERLIQUID_MAX_PING_PONG_FAILURES`, `HYPERLIQUID_PING_INTERVAL_S`, `HYPERLIQUID_DISABLE_PING_PONG_LOGS`, `HYPERLIQUID_WS_RESTORE_TIMEOUT` | upstream websocket tuning |

Both address and key are required at startup (a `KeyError` otherwise) unless passed to the constructor.
Full descriptions: `docs/system/ENVIRONMENT_VARIABLES.md`.

---

# Meta

### `products_version`

```json
{ "action": "products_version", "data": {}, "correlation_id": "<uuid>" }
```

**Output:**
```json
{ "argus": "<argus version>", "hyperliquid_dispatcher": [1, 0, 0, 0], "sidecars": {} }
```

# Market information

The perpetual universe is loaded at startup and refreshed from Hyperliquid at the top of **every UTC hour**;
these actions read that in-memory snapshot, so mark price, funding and open interest are at most an hour
old. Use the P2 stream for live prices. (`get_dexs` and `perpetual_info` hit the venue on each call.)

### `get_dexs`

All builder-deployed (HIP-3) dexes. The default dex has no entry (it is `""`).

```json
{ "action": "get_dexs", "data": {}, "correlation_id": "<uuid>" }
```

**Output:**
```json
{
  "dexes": [
    {
      "name": "xyz",
      "fullName": "XYZ",
      "deployer": "0x...",
      "oracleUpdater": "0x..." ,                 // null if none
      "feeRecipient": "0x...",
      "assetToStreamingOiCap": [["xyz:AAPL", "50000000.0"], ...],
      "subDeployers": [["setOracle", ["0x..."]], ...],
      "assetToFundingMultiplier": [["xyz:AAPL", "1.0"], ...],
      "assetToFundingInterestRate": [["xyz:AAPL", "0.0001"], ...]
    }
  ]
}
```

Keys are exactly Hyperliquid's `perpDexs` response (camelCase); the `[x, y]` entries are `[asset, value]` pairs.
Not paginated; with many dexes this can approach the packet cap.

### `get_perpetuals_for_dex`

The perpetuals of one dex, with static metadata and live context.

```json
{ "action": "get_perpetuals_for_dex", "data": { "dex_name": "", "offset": 0, "limit": 25 }, "correlation_id": "<uuid>" }
```

| Field | Required | Notes |
|---|---|---|
| `dex_name` | yes | `""` for the default dex, else the HIP-3 name |
| `offset` | no | default `0` |
| `limit` | no | default `min(100, count)`. A full 100-record page may exceed the packet cap and come back as `PacketTooLargeError`; pass a smaller `limit` and page |

**Output:** `{ "perpetuals": [Perpetual, ...] }`, in the venue's universe order. Past the end: `[]`.

`Perpetual`:
```json
{
  "dex": "",
  "asset": {
    "name": "BTC",
    "szDecimals": 5,
    "maxLeverage": 40,
    "onlyIsolated": true,            // optional: delisted / isolated-only assets
    "isDelisted": true,              // optional
    "marginMode": "strictIsolated",  // optional: "strictIsolated" | "noCross"
    "marginTableId": 56,             // optional: HIP-3 assets
    "growthMode": "enabled",         // optional: HIP-3 assets
    "lastGrowthModeChangeTime": "..."// optional
  },
  "context": {
    "dayNtlVlm": "1169046.29",
    "funding": "0.0000125",          // current hourly funding rate
    "impactPxs": ["14.3047", "14.3444"],   // null if absent
    "markPx": "14.3161",
    "midPx": "14.314",               // null if absent
    "openInterest": "688.11",        // in coins
    "oraclePx": "14.32",
    "premium": "0.00031774",         // null if absent
    "prevDayPx": "15.322",
    "dayBaseVlm": "..."              // optional: some HIP-3 assets
  }
}
```

Every number is a decimal **string**. Optional `asset` keys are omitted when Hyperliquid does not send them.

### `get_funding_rates_for_all_perpetuals`

Every perpetual on every dex, sorted by funding rate **descending** (highest first). Same `Perpetual`
shape as above.

```json
{ "action": "get_funding_rates_for_all_perpetuals", "data": { "offset": 0, "limit": 20 }, "correlation_id": "<uuid>" }
```

`offset` default `0`; `limit` default `min(20, count)` (a larger value is honoured, and logged). **Output:**
`{ "funding_rates": [Perpetual, ...] }`. Funding is the hourly rate; annualize by `× 24 × 365`.

### `get_funding_rate`

```json
{ "action": "get_funding_rate", "data": { "symbol": "xyz:AAPL" }, "correlation_id": "<uuid>" }
```

**Output:**
```json
{ "symbol": "xyz:AAPL", "funding_rate": "0.0000125", "funding_rate_apr": "0.1095" }
```

`funding_rate_apr` is naive (`funding_rate × 8760`). Unknown symbol → `InvalidCoinError`. `symbol` required.

### `perpetual_info`

Descriptive metadata for one coin, assembled from four separate Hyperliquid info requests
(`perpAnnotation`, `perpCategories`, `perpConciseAnnotations`, `predictedFundings`; four upstream calls per request).
It returns **no** market data; use the listings above.

```json
{ "action": "perpetual_info", "data": { "coin": "xyz:AAPL" }, "correlation_id": "<uuid>" }
```

**Output:**
```json
{
  "coin": "xyz:AAPL",
  "annotation": { "category": "stocks", "description": "..." },      // null if none
  "category": "stocks",                                              // null if none
  "concise_annotation": { "category": "stocks", "keywords": ["apple", "tech"] },  // null if none
  "predicted_funding": [                                             // null if none
    ["HlPerp",  { "fundingRate": "0.0000125", "nextFundingTime": 1733961600000 }],
    ["BinPerp", { "fundingRate": "0.0001",    "nextFundingTime": 1733974400000 }],
    ["BybitPerp", null]
  ]
}
```

`annotation` / `category` / `concise_annotation` are populated for HIP-3 coins only; `predicted_funding`
for default-dex coins only (Hyperliquid's own coverage). Each section is independently `null`. `coin` required.

### `search_perpetuals`

Fuzzy ticker search over the in-memory universe (case-insensitive `difflib` ratio, best match first;
no network call). Mirrors Polymarket's `search_markets`.

```json
{ "action": "search_perpetuals", "data": { "keyword": "btc", "limit": 10 }, "correlation_id": "<uuid>" }
```

`keyword` required, `limit` default `10`. **Output:** `{ "perpetuals": ["BTC", "kBONK", ...] }`, names usable as coins.
It always returns `limit` names (the closest ones), not only good matches.

# Market data streaming

### `subscribe`

Subscribe the calling socket to the live order book of one or more coins. `data` is a **list**.

```json
{ "action": "subscribe", "data": ["BTC", "xyz:AAPL"], "correlation_id": "<uuid>" }
```

**Output:** `{ "subscribed": ["BTC", "xyz:AAPL"], "failed": [] }`

- A coin that does not exist raises `InvalidCoinError` for the **whole** request (nothing after it is processed;
  coins before it stay subscribed). `failed` lists coins whose upstream subscription errored.
- The first subscriber to a coin opens the upstream Hyperliquid book subscription; the last
  unsubscribe/disconnect closes it.
- Afterwards the socket receives [P2 packets](#p2-order-book-packets) for the coin and, ~0.1–1 s after the
  response, one `funding_rate_update` push (then one hourly). See the shared spec.

### `unsubscribe`

```json
{ "action": "unsubscribe", "data": ["BTC"], "correlation_id": "<uuid>" }
```

**Output:** `{ "unsubscribed": ["BTC"], "failed": [] }`. A disconnect unsubscribes everything automatically.

### P2 order book packets

Shared layout (`../shared/PROTOCOL.md#p2-market-data`) with the Hyperliquid specifics:

- **Symbol:** the coin string, e.g. `BTC` or `xyz:AAPL`.
- **Depth:** `HYPERLIQUID_ORDERBOOK_DEPTH` levels each side (default 10).
- **Source:** Hyperliquid's `l2Book` stream with the best bid/offer overlaid from `bbo`, so the top of
  book is current. `exchange_ts_ms` is Hyperliquid's book time.

Example (depth 2; whitespace added):

```
~0123 0003|BTC  97500,1.5, 97499,3.2,  97501,2.0, 97502,0.5,  1770251679393,1770251679.412  L
```

# Push system (server-initiated messages)

Pushes use the P1 envelope with `correlation_id: null`. Market pushes go only to sockets subscribed to the
coin concerned (via [`subscribe`](#subscribe)); `account_update` goes to **every** connected client. Hyperliquid
has three push streams:

| Stream | Protocol | Trigger |
|---|---|---|
| Order book | P2 | every book change for a subscribed coin (see [P2 packets](#p2-order-book-packets)) |
| `funding_rate_update` | P1 | after `subscribe`, then hourly |
| `account_update` | P1 | any change to the master wallet's orders or fills; no `subscribe` needed |

### `funding_rate_update`

```json
{
  "action": "funding_rate_update",
  "data": { "coin": "BTC", "funding_rate": "0.0000125" },
  "error": null,
  "compressed": false,
  "correlation_id": null
}
```

- `coin` is the same coin string used everywhere else (`"BTC"`, `"xyz:AAPL"`); `funding_rate` is the hourly
  rate as a decimal string.
- Sent once per coin ~0.1-1 s after the `subscribe` response (random jitter), then to every subscriber of a coin
  at the top of each UTC hour, when the perpetual list refreshes.

### `account_update`

Order lifecycle and fills for `HYPERLIQUID_WALLET_ADDRESS`, across **all** dexes (HIP-3 coins keep their
`dex:` prefix). Unlike Polymarket, a client does **not** need to `subscribe` to anything: every socket that has
sent at least one request receives them (a socket that connects and stays silent is not yet registered, so send
any cheap request such as `products_version` first). Hyperliquid's own `orderUpdates` and `userEvents` channels
feed it over a dedicated websocket, so a failure there never affects market data (if it cannot start, the
dispatcher logs loudly and keeps serving everything else). **One record per message**, never a batch. `data.event`
discriminates:

```json
{"action":"account_update","data":{"event":"order","order":{
  "order_id":"562041858211","client_order_id":"0xabab...ab","name":"ETH","side":"buy","price":"1086.8",
  "original_size":"0.0102","remaining_size":"0.0102","status":"open","status_timestamp_ms":1790837260523,
  "timestamp_ms":1790837260523,"dex":"","venue":{ "order":{ "...": "..." },"status":"open","statusTimestamp":1790837260523 }}},
 "error":null,"compressed":false,"correlation_id":null}

{"action":"account_update","data":{"event":"fill","trade":{ "...same record as get_trades..." }},"error":null,...}

{"action":"account_update","data":{"event":"gap","reason":"reconnected","since_ms":1790837268373},"error":null,...}
```

- `order`: a status transition. `status` is Hyperliquid's string passed through untranslated (`open`, `filled`,
  `canceled`, `marginCanceled`, any `*Rejected`, ...). A replacement shows as an `open` plus a `canceled`. It is
  not the `get_orders` record: the stream carries no order type / reduce-only / tif, so those fields are absent;
  call `get_order_status` if you need them. `remaining_size` is the unfilled size at that moment.
- `fill`: exactly the `get_trades` record (`trade_id` is the unique fill id). TWAP slice fills are included
  (`venue.twapId` is set). One fill is never delivered twice.
- `gap`: the account websocket dropped and reconnected. The channels do **not** replay (observed live: nothing
  arrives after reconnect), so anything between `since_ms` and now may be missing; reconcile with `get_orders` /
  `get_trades`. Sent once per reconnect, after both subscriptions are re-established, never on first connect.
- **Ordering is not guaranteed.** Observed live for an IOC order: the `fill` arrived *before* the `order`
  `open` and `filled` events. Treat `fill` events as the source of truth for size changes and do not assume the
  `order` events arrive in lifecycle order or before/after the `place_order` response.
- Spot activity on a unified account (coins like `@107`, `PURR/USDC`) is passed through untranslated, not filtered.
- Not pushed yet: funding payments, liquidations and `nonUserCancel` (the matching cancel already arrives as an
  `order` event with a `*Canceled` status).
- Not verified: whether a partial fill (not a full one) also produces an `order` event.

### Not pushed

- **No `fatal_error` broadcast.** Failures surface only as `error` responses to the request that caused them.

Clients must handle pushes interleaved with responses on the same socket: match responses by
`correlation_id`, and treat any packet with `correlation_id: null` as a push.

# Account actions

All reads are unsigned `info` requests against `HYPERLIQUID_WALLET_ADDRESS`. Request/response shapes, the
`venue` object, pagination defaults (25) and unknown-key rejection are in
[`../shared/ACCOUNT.md`](../shared/ACCOUNT.md). Hyperliquid specifics:

| Action | `data` accepted | Hyperliquid behaviour |
|---|---|---|
| `get_balance` | `dex` | Resolves the account mode (`userAbstraction`, cached 5 min): default/disabled/dexAbstraction read the perps clearinghouse for `dex`; unified/portfolio-margin read spot + every dex and **ignore `dex`**. `account` is the wallet address; `account_mode` is set. Unrecognised mode → `UnsupportedAccountModeError` |
| `get_positions` | `offset`, `limit`, `dex` | Every dex by default (one `info` call per dex, 0.2 s apart, so a few seconds); `dex` narrows. `leverage` is populated. Non-zero positions only |
| `get_orders` | `offset`, `limit`, `dex` | Resting orders on every dex by default, newest first |
| `get_order_status` | `order_id` | Numeric → `oid`; anything else → `cloid`. Finds orders in any lifecycle state (`filled`, `canceled`, ...) |
| `get_trades` | `offset`, `limit` | The 2000 most recent fills, newest first |
| `get_funding_payments` | `start_time`, `end_time`, `offset`, `limit` | Unix ms; `end_time` defaults to now, `start_time` to 7 days before it |

### `get_account_fees`

```json
{ "action": "get_account_fees", "data": {}, "correlation_id": "<uuid>" }
```

**Output:** the `userFees` payload, verbatim, with the typed rates normalized to decimal strings:

```json
{
  "userCrossRate": "0.00045",        // taker
  "userAddRate": "0.00015",          // maker
  "userSpotCrossRate": "0.0007",
  "userSpotAddRate": "0.0004",
  "activeReferralDiscount": "0.0",
  "dailyUserVlm": [ { "date": "2025-05-23", "userCross": "...", "userAdd": "...", "exchange": "..." } ],
  "feeSchedule": { },                // tiers, discounts, trial state: untouched
  "...": "any other userFees keys"
}
```

### `get_rate_limit_usage`

```json
{ "action": "get_rate_limit_usage", "data": {}, "correlation_id": "<uuid>" }
```

**Output:** `{ "cumVlm": "2854574.593578", "nRequestsUsed": 2890, "nRequestsCap": 2864574, "nRequestsSurplus": 0 }`.
The address-based budget that signed actions (`place_order`, `cancel_order`) draw down; the cap grows with
cumulative traded volume (≈ 1 request per 1 USDC).

---

# Trading

Signed order execution against Hyperliquid's `/exchange` endpoint
(`argus/perpetuals/hyper/exchange.py`, `HyperLiquidExchange`). Names match `PolymarketDispatcher`'s
`place_order` / `cancel_order`.

### Signing (handled by the dispatcher)

Every action is EIP-712 signed with the wallet key under Hyperliquid's L1 ("phantom agent") scheme:

1. The action dict is `msgpack`-packed in its own key order, followed by an 8-byte big-endian nonce and a vault
   tag (`0x00` when no vault), then keccak-256 hashed.
2. The hash becomes the `connectionId` of a synthetic `Agent` struct signed under the Exchange domain
   (chainId `1337`, zero verifying contract).

Key order is part of the signature. Nonces are unix-millisecond timestamps, handed out from one
process-wide counter per wallet (strictly increasing, bumped by 1 when the clock has not advanced), so
concurrent orders never collide. Do not sign for the same wallet from a second process.

**Asset ids** (the wire's global id, resolved internally by `resolve_asset_id`; per-dex universes cached 60 s):

| Space | Id |
|---|---|
| Default dex perps | universe index (`BTC` = 0, `ETH` = 1, ...) |
| HIP-3 dex perps | `110000 + slot * 10000 + index_in_universe` (`slot` = position in `perpDexs`, whose first entry is `null` for the default dex) |
| Spot | `10000 + ...` (not exposed) |

### `place_order`

Place one limit order.

```json
{
  "action": "place_order",
  "data": {
    "coin": "BTC",
    "side": "buy",
    "price": "30000",
    "size": "0.001",
    "order_type": "GTC",
    "reduce_only": false,
    "cloid": "0x0123456789abcdef0123456789abcdef"
  },
  "correlation_id": "<uuid>"
}
```

| Field | Required | Notes |
|---|---|---|
| `coin` | yes | `"BTC"` or `"xyz:AAPL"` |
| `side` | yes | `"buy"` or `"sell"` |
| `price` | yes | Limit price, rounded to the venue's tick rules before submission: a fractional price keeps 5 significant figures and at most `6 - szDecimals` decimals (nearest integer once the integer part has 5+ digits); a whole-number price is never changed |
| `size` | yes | Size in coins, at most 8 decimals |
| `order_type` | no | `"GTC"` (default), `"IOC"`, `"ALO"` (post-only); anything else is rejected |
| `reduce_only` | no | default `false` |
| `cloid` | no | client order id: `0x` + 32 hex chars (16 bytes) |

Unknown keys are rejected.

**Output:**
```json
{
  "coin": "BTC",
  "oid": 123456789,
  "status": "resting",
  "avgPx": null,
  "price": "30000",
  "requestedPrice": "30000.123456",
  "priceAdjusted": true,
  "error": null
}
```

- `price` is the price **actually submitted** (post tick-rounding); `requestedPrice` is what you sent;
  `priceAdjusted` is `true` when they differ. Reconcile against `price`. All three are also set on rejection.
- `status` is `"resting"` (on the book) or `"filled"` (matched immediately, `avgPx` set).
- A **venue-level rejection of the order** (insufficient margin, below the $10 minimum order value, ...) comes
  back as a normal `response` with `error` set and `oid: null`. Callers must check `error`.
- A rejected *envelope* (invalid signature, stale nonce) is a packet-level `error` (`ExchangeActionError`).

### `cancel_order`

Cancel one order by venue id or client id.

```json
{ "action": "cancel_order", "data": { "order_id": 123456789, "coin": "BTC" }, "correlation_id": "<uuid>" }
```

| Field | Required | Notes |
|---|---|---|
| `order_id` | yes | numeric `oid` (int or digit string) or `0x` + 32 hex cloid; anything else is rejected before any venue call |
| `coin` | no | omitted → resolved via `orderStatus` (one extra info call; errors if the order no longer exists). Pass it when known |

**Output:**
```json
{ "coin": "BTC", "canceledOids": [123456789], "errors": [] }
```

`errors` holds the venue's messages for ids it could not cancel (already filled/canceled, unknown), e.g.
`"Order was never placed, already canceled, or filled."`, with `canceledOids` empty. **A submitted cancel is
not proof the order was canceled; check both.**

---

## Concurrency and ordering notes

- One TCP connection may pipeline requests; responses are matched by `correlation_id`, not order.
- Concurrent trading calls are safe: nonces are allocated per wallet under a lock. Only one *process* may sign for a wallet.
- Run alongside `LighterDispatcher` on a different port; see [`../shared/CONCURRENCY.md`](../shared/CONCURRENCY.md)
  (stagger launches when using the encrypted `.env`).

## Example client workflow

```python
import json, socket, uuid

s = socket.create_connection(("localhost", 9972))

def call(action, data=None):
    body = json.dumps({"action": action, "data": data or {}, "correlation_id": str(uuid.uuid4())}).encode()
    s.sendall(f"~{len(body):04d}|".encode() + body)
    # read '~LLLL|' then LLLL bytes; skip P2 / push packets; match on correlation_id
    ...

call("subscribe", ["BTC"])                      # optional: only for the order book stream
call("get_balance")
r = call("place_order", {"coin": "BTC", "side": "buy", "price": "30000", "size": "0.001"})
if r["error"] is None:                           # order-level outcome lives in data.error
    call("cancel_order", {"order_id": r["oid"], "coin": "BTC"})
```

## Verification

- **Offline:** `env PYTHONPATH=. uv run pytest tests/test_hyper_exchange.py tests/test_perpetuals_account.py tests/test_hyper_account_stream.py`
  (signing round-trip, pinned action-hash vector, wire key order, tick rounding, asset-id offsets, response parsing,
  dispatcher handlers, wire byte budget, `account_update` parsing / gap / fan-out).
- **Live:** `env PYTHONPATH=. uv run python tests/hyper_order_smoke.py` places a far-below-market resting
  order, confirms it via `orderStatus`, cancels it, and repeats with a cloid (needs a funded account).
- **Live `account_update`:** `env PYTHONPATH=. uv run python tests/hyper_account_stream_smoke.py --go [ETH|BTC]` runs a
  real dispatcher, places/cancels a resting order and an ~$11 IOC round trip, kills the account websocket, and asserts
  the `order` / `fill` / `gap` pushes (real orders, pennies of risk; flattens on exit).
- **REPL:** `env PYTHONPATH=. uv run python tests/hyper_cli.py` (`place ...`, `cancel ...`, `balance`, `watch` for live
  `account_update` pushes, ...).
