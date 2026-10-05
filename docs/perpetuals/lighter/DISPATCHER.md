# LighterDispatcher TCP Protocol Specification

## Overview

`LighterDispatcher` (`argus/perpetuals/lighter/__init__.py`) is a TCP server exposing Lighter (zkLighter)
perpetuals through the same two protocols as `PolymarketDispatcher` and `HyperLiquidDispatcher`:

- **P1**: JSON request/response and server pushes
- **P2**: binary order-book stream

**Default connection:** `localhost:9974` (`python runtime.py lighter --port 9974`)

Framing, the envelope, `correlation_id` rules, auto-compression, pagination, error classes, the P2 CSV layout and
the `funding_rate_update` push are **shared by all perpetual dispatchers** and specified once in
[`../shared/PROTOCOL.md`](../shared/PROTOCOL.md). Read that first; it is not repeated here. The six account
actions (`get_balance` ... `get_funding_payments`) have their record shapes in
[`../shared/ACCOUNT.md`](../shared/ACCOUNT.md); this document lists them with only what is Lighter-specific. The
venue-generic trading control flow (read current leverage -> apply -> place -> roll back) is specified in
[`argus/perpetuals/shared/trading.py`](../../../argus/perpetuals/shared/trading.py), whose module docstring is the
authoritative description of that algorithm; this document lists only what is Lighter-specific.

Quick reminders from the shared spec: `correlation_id` is **required** and unique; success responses carry
`"action": "response"` (failures `"error"`); responses ≥ 9500 bytes are zlib+base64 compressed
(`compressed: true`); nothing here needs a prior `subscribe`.

## Endpoint index

| Action | Group | Paginated | Signed |
|---|---|---|---|
| [`products_version`](#products_version) | meta | no | no |
| [`get_markets`](#get_markets) | market info | yes | no |
| [`get_funding_rates_for_all_perpetuals`](#get_funding_rates_for_all_perpetuals) | market info | yes | no |
| [`get_funding_rate`](#get_funding_rate) | market info | no | no |
| [`market_info`](#market_info) | market info | no | no |
| [`search_perpetuals`](#search_perpetuals) | market info | no | no |
| [`get_funding_history`](#get_funding_history) | market info | yes | no |
| [`subscribe`](#subscribe) / [`unsubscribe`](#unsubscribe) | market data | no | no |
| [`funding_rate_update`](#funding_rate_update) | push (server-initiated) | n/a | no |
| [`account_update`](#account_update) | push (server-initiated) | n/a | no |
| [`get_balance`](#account-actions) | account | no | no |
| [`get_positions`](#account-actions) | account | yes | no |
| [`get_orders`](#account-actions) | account | yes | no |
| [`get_order_status`](#account-actions) | account | no | no |
| [`get_trades`](#account-actions) | account | yes | no |
| [`get_funding_payments`](#account-actions) | account | yes | no |
| [`get_leverage`](#get_leverage) | trading | no | no |
| [`set_leverage`](#set_leverage) | trading | no | **yes** |
| [`place_order`](#place_order) | trading | no | **yes** |
| [`place_multiple_orders`](#place_multiple_orders) | trading | no | **yes** |
| [`cancel_order`](#cancel_order) | trading | no | **yes** |
| [`cancel_multiple_orders`](#cancel_multiple_orders) | trading | no | **yes** |
| [`cancel_all_orders`](#cancel_all_orders) | trading | no | **yes** |

Deliberately absent (no Lighter analog): `get_dexs` / `get_perpetuals_for_dex` / `perpetual_info` (Lighter has a
single unified market list, not per-dex universes), `get_account_fees` / `get_rate_limit_usage`, and order
modification.

---

## Symbols and markets

- Lighter is a **single unified exchange** -- one account/margin system with every market listed flat under it.
  There are no HIP-3-style builder-deployed dexes, so there is no `dex` and no `"<dex>:"` prefix anywhere.
- A **symbol** (e.g. `"BTC"`, `"TENCENT"`) is the client-facing identity everywhere: `subscribe`,
  `get_funding_rate`, `market_info`, `place_order`, the P2 packet symbol, and the `name` field of account records.
- Internally the wire keys quotes and account updates by integer `market_id`; the dispatcher translates once at
  its boundary (`subscribe` / `unsubscribe` / P2 fan-out). `market_info` accepts either `symbol` or `market_id`.

## Configuration

| Env | Needed for |
|---|---|
| `LIGHTER_ACC_INDEX` | the account actions and trading. Legacy alias `LIGHTER_ACCOUNT_INDEX` is accepted. Without it (and `LIGHTER_AUTH_TOKEN`) account actions answer `AccountNotConfiguredError`, and the `account_update` stream is not started |
| `LIGHTER_API_INDEX` | the trading actions and native auth-token minting. API-key slot bound to `LIGHTER_PRIVATE_KEY` |
| `LIGHTER_PRIVATE_KEY` | the trading actions and native auth-token minting. The **API key** private key, not the L1 wallet key |
| `LIGHTER_AUTH_TOKEN` | optional; a long-lived read-only `ro:...` token that unlocks the auth-gated REST reads without the trading credentials |
| `LIGHTER_BLOCK_ORDER_EXECUTION` | optional; `1`/`true`/`yes` starts the dispatcher with the **order execution kill switch** on (see below). Default off |
| `LIGHTER_ORDERBOOK_DEPTH` | P2 levels per side, default 10 |
| `LIGHTER_MAX_SOCKET_RETRIES`, `LIGHTER_MAX_PING_PONG_FAILURES`, `LIGHTER_PING_INTERVAL_S`, `LIGHTER_DISABLE_PING_PONG_LOGS`, `LIGHTER_WS_RESTORE_TIMEOUT` | upstream websocket tuning (apply to both Lighter sockets; `LIGHTER_WS_RESTORE_TIMEOUT` only to the order-book socket) |

Market data (`subscribe`, `get_markets`, ...) and `get_balance` / `get_positions` need no credentials. Trading
needs all three of `LIGHTER_ACC_INDEX` + `LIGHTER_API_INDEX` + `LIGHTER_PRIVATE_KEY`; without them trading actions
answer `AccountNotConfiguredError` and everything else keeps running. The auth-gated REST reads (`get_orders`,
`get_order_status`, `get_trades`, `get_funding_payments`) need `LIGHTER_AUTH_TOKEN` **or** the trading
credentials: when the token is unset but the API key is present, the dispatcher mints a native signed token and
refreshes it before expiry. A native REST token's deadline is short -- verified live, 6h is accepted and 12h is
rejected with `invalid auth: invalid deadline` -- so it cannot be minted once for a long-running process;
`LighterRest.set_auth_token_provider` re-mints it lazily (`_NATIVE_AUTH_TOKEN_DEADLINE_S` is 6h, refreshed within
30 minutes of expiry). The **account stream** (`account_update`) additionally needs a signed token for its
`account_all_orders` channel: it reuses the same API key to mint one, and without `LIGHTER_API_INDEX` /
`LIGHTER_PRIVATE_KEY` it still starts for `account_all_trades` and pushes fills but not order updates.

Full descriptions: `docs/system/ENVIRONMENT_VARIABLES.md`.

**Block Order Execution (kill switch).** While on, `place_order`, `place_multiple_orders` and `set_leverage`
fail with `OrderExecutionDisabledError` as their very first step, before any argument parsing, venue read or
signing. **Cancels and every read are never blocked.** The switch is set by the env var at boot or by the
dispatcher's interactive menu; no wire action toggles it, and nothing engages it automatically (an unexpected
trading error alerts and broadcasts a `fatal_error`, but never blocks). Clients read its state from
`products_version.order_execution_blocked`.

---

# Meta

### `products_version`

```json
{ "action": "products_version", "data": {}, "correlation_id": "<uuid>" }
```

**Output:**
```json
{ "argus": "<argus version>", "lighter_dispatcher": [1, 0, 0, 0], "sidecars": {}, "order_execution_blocked": false }
```

# Market information

The perpetual universe (static metadata + live mark price, funding and open interest) is loaded at startup and
refreshed from Lighter at the top of **every UTC hour**; these actions read that in-memory snapshot, so live
fields are at most an hour old. Use the P2 stream for live prices. (`get_funding_history` hits the venue on each
call.)

### `get_markets`

All perpetual markets, in the venue's order.

```json
{ "action": "get_markets", "data": { "offset": 0, "limit": 10 }, "correlation_id": "<uuid>" }
```

`offset` default `0`; `limit` default `min(10, count)`. **Output:** `{ "perpetuals": [Perpetual, ...] }`. Past the
end: `[]`.

`Perpetual` = static `market` + live `context` + the current `funding_rate` (a decimal string, `null` if unknown):

```json
{
  "market": {
    "symbol": "BTC", "market_id": 1, "market_type": "perp", "status": "active",
    "taker_fee": "0.0000", "maker_fee": "0.0002", "liquidation_fee": "0.0",
    "min_base_amount": "0.00020", "min_quote_amount": "10", "order_quote_limit": "1000000",
    "size_decimals": 5, "price_decimals": 1, "supported_size_decimals": 5, "supported_price_decimals": 1,
    "default_initial_margin_fraction": 500, "min_initial_margin_fraction": 200,
    "maintenance_margin_fraction": 250, "closeout_margin_fraction": 125,
    "base_asset_id": 0, "quote_asset_id": 0, "created_at": "...", "multiplier": "1",
    "market_config": { "market_margin_mode": 0, "rfq_enabled": false, "...": "..." },
    "funding_clamp_small": "0.0001", "funding_clamp_big": "0.004", "base_interest_rate": "0.0",
    "...": "..."
  },
  "context": {
    "mark_price": "34397.8", "index_price": "34380.1", "last_trade_price": "34399.0",
    "daily_trades_count": 12345, "daily_base_token_volume": "...", "daily_quote_token_volume": "...",
    "daily_price_low": "...", "daily_price_high": "...", "daily_price_change": "...",
    "open_interest": "...", "daily_chart": {}
  },
  "funding_rate": "0.0000125"
}
```

Every number is a decimal **string** except the integer fractions (basis points), decimals and counts.
`default_initial_margin_fraction` / `min_initial_margin_fraction` are **basis points** (`10000 / imf` =
leverage; live BTC: 500 -> 20x, 200 -> 50x max); see [`get_leverage`](#get_leverage) for the account-readback
unit caveat.

### `get_funding_rates_for_all_perpetuals`

Every perpetual, sorted by funding rate **descending** (highest first). Same `Perpetual` shape as above.

```json
{ "action": "get_funding_rates_for_all_perpetuals", "data": { "offset": 0, "limit": 20 }, "correlation_id": "<uuid>" }
```

`offset` default `0`; `limit` default `min(20, count)` (a larger value is honoured, and logged). **Output:**
`{ "funding_rates": [Perpetual, ...] }`. Funding is the hourly rate; annualize by `× 24 × 365`.

### `get_funding_rate`

```json
{ "action": "get_funding_rate", "data": { "symbol": "BTC" }, "correlation_id": "<uuid>" }
```

**Output:**
```json
{ "symbol": "BTC", "funding_rate": "0.0000125", "funding_rate_apr": "0.1095" }
```

`funding_rate_apr` is naive (`funding_rate × 8760`). Unknown symbol → `InvalidCoinError`. `symbol` required.

### `market_info`

Metadata + live data for a single market, a direct lookup into the in-memory index (Lighter has no
annotation/category system). Supply exactly one of `symbol` or `market_id`.

```json
{ "action": "market_info", "data": { "symbol": "BTC" }, "correlation_id": "<uuid>" }
```

**Output:** `{ "perpetual": Perpetual | null }` (the same `Perpetual` shape as `get_markets`).

### `get_funding_history`

Historical funding for one market, in `[start_timestamp, end_timestamp]` (unix **seconds**).

```json
{ "action": "get_funding_history", "data": { "market_id": 1, "start_timestamp": 1791100000, "resolution": "1h" }, "correlation_id": "<uuid>" }
```

| Field | Required | Notes |
|---|---|---|
| `market_id` | yes | integer market id |
| `start_timestamp` | yes | unix seconds |
| `end_timestamp` | no | default now |
| `resolution` | no | `"1h"` (default) or `"1d"`; at most 750 entries |

**Output:** `{ "funding_history": [{ "timestamp": <s>, "value": "...", "rate": "...", "direction": "long"|"short" }, ...] }`.
`rate` is Lighter's realized/settled rate for that period; it was **not** observed to equal the live
`FundingRateEntry.rate` for the same market at the same time, so treat the two as separate quantities.

### `search_perpetuals`

Fuzzy ticker search over the in-memory universe (case-insensitive `difflib` ratio, best match first; no network
call). Mirrors Polymarket's `search_markets`.

```json
{ "action": "search_perpetuals", "data": { "keyword": "btc", "limit": 10 }, "correlation_id": "<uuid>" }
```

`keyword` required, `limit` default `10`. **Output:** `{ "perpetuals": ["BTC", ...] }`, names usable as symbols.
It always returns up to `limit` names (the closest ones), not only good matches.

# Market data streaming

### `subscribe`

Subscribe the calling socket to the live order book of one or more symbols. `data` is a **list**.

```json
{ "action": "subscribe", "data": ["BTC", "ETH"], "correlation_id": "<uuid>" }
```

**Output:** `{ "subscribed": ["BTC", "ETH"], "failed": [] }`

- A symbol that does not exist raises `InvalidCoinError` for the **whole** request (nothing after it is
  processed). `failed` lists symbols whose upstream subscription errored.
- The first subscriber to a symbol opens the upstream subscription; the last unsubscribe/disconnect closes it.
- Afterwards the socket receives [P2 packets](#p2-order-book-packets) for the symbol and, ~0.1–1 s after the
  response, one `funding_rate_update` push (then one hourly). See the shared spec.

### `unsubscribe`

```json
{ "action": "unsubscribe", "data": ["BTC"], "correlation_id": "<uuid>" }
```

**Output:** `{ "unsubscribed": ["BTC"], "failed": [] }`. A disconnect unsubscribes everything automatically.

### P2 order book packets

Shared layout (`../shared/PROTOCOL.md#p2-market-data`) with the Lighter specifics:

- **Symbol:** the symbol string (e.g. `BTC`), matching the client-facing identity, not the internal `market_id`.
- **Depth:** `LIGHTER_ORDERBOOK_DEPTH` levels each side (default 10). Lighter allows 255 connections and 500
  subscriptions per connection, so one order-book connection comfortably holds every market.
- **Source:** Lighter's `order_book/{market_id}` channel, which is **snapshot + incremental delta** (the first
  frame is a full snapshot; later frames are price-keyed upserts/removals). The `ticker/{market_id}` BBO fast
  path is overlaid onto the top level (with a dedup short-circuit). Nonce-chain gaps (`begin_nonce` not chaining
  from the last `nonce`) drop the local book and force an unsubscribe+resubscribe of that channel.
- `exchange_ts_ms` is the frame's top-level `timestamp` (unix **ms**); the payload's own `last_updated_at` is in
  **microseconds** and is deliberately not used.

Example (depth 2; whitespace added):

```
~0123 0003|BTC  97500,1.5, 97499,3.2,  97501,2.0, 97502,0.5,  1770251679393,1770251679.412  L
```

# Push system (server-initiated messages)

Pushes use the P1 envelope with `correlation_id: null`. Market pushes go only to sockets subscribed to the symbol
concerned (via [`subscribe`](#subscribe)); `account_update` goes to **every** connected client. Lighter has three
push streams:

| Stream | Protocol | Trigger |
|---|---|---|
| Order book | P2 | every book change for a subscribed market (see [P2 packets](#p2-order-book-packets)) |
| `funding_rate_update` | P1 | after `subscribe`, then hourly |
| `account_update` | P1 | any change to the configured account's orders or fills; no `subscribe` needed |

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

- `coin` is the same symbol string used everywhere else; `funding_rate` is the hourly rate as a decimal string.
- Sent once per symbol ~0.1–1 s after the `subscribe` response (random jitter), then to every subscriber of a
  symbol at the top of each UTC hour, when the perpetual list refreshes.

### `account_update`

Order lifecycle and fills for the configured `LIGHTER_ACC_INDEX`, across every market. Unlike Polymarket, a
client does **not** need to `subscribe` to anything: every socket that has sent at least one request receives
them (a socket that connects and stays silent is not yet registered, so send any cheap request such as
`products_version` first). The stream runs on its own websocket (`LighterAccountWss`) fed by
`account_all_orders` (auth) and `account_all_trades` (no auth), so a failure there never affects market data (if
it cannot start, the dispatcher logs loudly and keeps serving everything else). **One record per message**, never
a batch. `data.event` discriminates, and the payloads reuse the shared `OrderUpdate` / `Trade` records (named
like the `get_orders` / `get_trades` fields):

```json
{"action":"account_update","data":{"event":"order","order":{
  "order_id":"844421364625660","client_order_id":"691684318611","name":"BTC","side":"buy","price":"34437.6",
  "original_size":"0.00030","remaining_size":"0.00030","status":"open","status_timestamp_ms":1791195947000,
  "timestamp_ms":1791195947000,"dex":"","venue":{ "order_index":844421364625660,"status":"open","...":"..." }}},
 "error":null,"compressed":false,"correlation_id":null}

{"action":"account_update","data":{"event":"fill","trade":{ "...same record as get_trades..." }},"error":null,...}

{"action":"account_update","data":{"event":"gap","reason":"reconnected","since_ms":1791195948210},"error":null,...}
```

- `order`: a status transition. `status` is Lighter's lifecycle string passed through untranslated (`open`,
  `filled`, `canceled`, `canceled-post-only`, `canceled-margin-not-allowed`, ...). The `venue` object is the full
  `account_all_orders` record (the same shape as the REST `get_orders` record), so a client that has already
  parsed that has everything; `dex` is always `""` (Lighter has one unified ledger). This is the shared
  `OrderUpdate`, not the `get_orders` record -- the stream is not guaranteed to carry every field.
- `fill`: one fill; `trade` is exactly a `get_trades` record. Treat `fill` as the source of truth for a size
  change.
- `gap`: sent once after the account websocket **reconnects** (not on the first connect) and both required
  channels re-ack. `since_ms` is when the old connection was lost, unix ms; events in that window may have been
  missed (neither channel replays), so reconcile with `get_orders` / `get_trades`.
- **No snapshot flood and no duplicate fills:** the first `subscribed/...` frame of each channel is a silent
  baseline; order events are deduplicated on `(order_index, status, remaining_size, updated_at)` and fills on
  `trade_id_str`. `account_all_trades` is a bounded recent-trades snapshot on subscribe and deltas afterwards
  (verified live); `account_all_orders` carries the changed order per frame (verified live), and the signature
  dedup suppresses unchanged re-sends either way.
- **Ordering is not guaranteed.** Treat `fill` as the source of truth and do not assume the `order` events arrive
  in lifecycle order or before/after the `place_order` response.
- The `account_all_orders` token is short-lived (Lighter rejects a deadline much past ~6h); the stream mints a
  fresh one from the API key and re-sends the subscription before it expires. If the channel is rejected, the
  stream keeps pushes trades-only and the reconnect `gap` still fires.
- If `account_all_positions` / `account_all_assets` are never subscribed, **position and asset changes are not
  pushed** -- use `get_positions` / `get_balance`.

### Not pushed

- No `funding`, `liquidation` or `nonUserCancel` events (Phase 2 of the Hyperliquid push work); Lighter pushes
  only `order`, `fill` and `gap`.
- No position or asset push (`account_all_positions` / `account_all_assets` are out of scope).
- No order-modification action and no `fatal_error` broadcast as a *push stream of its own*: a `fatal_error` is
  sent only by the trading contingency (see [Trading](#trading)), not by the account stream.

Clients must handle pushes interleaved with responses on the same socket: match responses by `correlation_id`, and
treat any packet whose `action` is a known push (`account_update`, `fatal_error`, `funding_rate_update`) as a
push, never as a reply (`tests/lighter_cli.py` does exactly this).

# Account actions

Request/response shapes, the `venue` object, pagination defaults (25) and unknown-key rejection are in
[`../shared/ACCOUNT.md`](../shared/ACCOUNT.md). Lighter specifics:

| Action | `data` accepted | Lighter behaviour |
|---|---|---|
| `get_balance` | `dex` (must be empty) | Public `GET /api/v1/account`. `account_value` = `total_asset_value`, `available_balance`, margin/notional computed over open positions; `account_mode` is `null`; `venue` nests the summary **without** positions |
| `get_positions` | `offset`, `limit`, `dex` (must be empty) | Non-zero positions only. `leverage` is `null` (Lighter reports a margin fraction per position, kept under `venue.initial_margin_fraction`); `margin_used` is `allocated_margin` |
| `get_orders` | `offset`, `limit`, `dex` (must be empty) | Resting orders from `accountActiveOrders`, newest first |
| `get_order_status` | `order_id` | Numeric `order_index` or the venue `order_id` string; scans the resting orders then the most recent 100 inactive ones. Older orders → `found: false` |
| `get_trades` | `offset`, `limit` | Auth-gated and cursor-paged; the `venue` record is symmetric (both sides), and the client's side is derived from `account_index` |
| `get_funding_payments` | `start_time`, `end_time`, `offset`, `limit` | Unix ms; `end_time` defaults to now, `start_time` to 7 days before it. Walks at most 500 settlements (about three weeks of hourly funding) |

Lighter has a single ledger per `account_index`, so any non-empty `dex` is rejected with `InvalidCoinError`
rather than silently answered from the only ledger there is. `get_account_fees` and `get_rate_limit_usage` have
no Lighter analog and are absent.

Lighter has no missing-account mode where a credential is optional at the venue: `get_orders` /
`get_order_status` / `get_trades` / `get_funding_payments` answer `AccountNotConfiguredError` when neither
`LIGHTER_AUTH_TOKEN` nor the trading credentials are present, and `get_balance` / `get_positions` do the same
when `LIGHTER_ACC_INDEX` is unset.

---

# Trading

Signed order execution against Lighter's `/api/v1/sendTx` (and `/api/v1/sendTxBatch`) endpoint
(`argus/perpetuals/lighter/exchange.py`, `LighterExchange`). Names match `PolymarketDispatcher`'s
`place_order` / `place_multiple_orders` / `cancel_order` / `cancel_multiple_orders`.

**Unexpected errors (`fatal_error`).** Every trading handler is wrapped in `fatal_decorator`. Errors the handler
is *ready for* (bad arguments, unknown symbol, the venue rejecting an action, the kill switch) are ordinary
request errors. Anything else (a timeout mid-POST, an unparsable reply, a bug) may leave an order or a leverage
change in an unknown state, so the dispatcher raises a console alert and pushes a **`fatal_error`** message to
every client (`data: {function, exception, traceback, order_execution_blocked}`, `error` set), then still answers
the request with the normal error. Reconcile with `get_orders` / `get_positions`. It deliberately does **not**
block order execution.

### Leverage is per market, and every order names it

Lighter stores leverage (initial margin fraction) per **market and margin mode** (`cross` / `isolated`), and it
applies to *new* positions. Unlike Hyperliquid, a leverage update is signed with the **same API key and nonce
stream** as orders (no L1 wallet key). So `place_order` / `place_multiple_orders` take a **required `leverage`**
(and optional `margin_mode`, default: the market's current mode). For each market the dispatcher:

1. reads the market's current leverage with `GET /api/v1/account?active_only=false` (so a market the account has
   traded but is flat in still reports its persisted setting; a never-touched market falls back to
   `10000 / default_initial_margin_fraction`);
2. sends one signed `SignUpdateLeverage` only if it differs (nothing is signed when it already matches);
3. places the order(s);
4. **rolls back** to the previous leverage if the order was rejected or definitively refused. A timeout mid-POST
   is ambiguous (the order may be live), so leverage is **left as is** and the `fatal_error` contingency runs. If
   a roll-back itself fails, the call fails with a `fatal_error` naming the market to repair with `set_leverage`.

Whether *lowering* leverage or *switching mode* is allowed while a position is open is enforced by the venue; the
dispatcher does not pre-validate it, and the rejection surfaces as an `ExchangeActionError` before any order is
sent.

### Signing (handled by the dispatcher)

Nothing is EIP-712-signed in Python. The action is signed by a **vendored native signer** (Apache-2.0 shared
library loaded via `ctypes`, `argus/perpetuals/lighter/_signer.py`), which returns the transaction type plus a
JSON `tx_info` body. That body is POSTed as a form field:

```
POST <base>/api/v1/sendTx       form: tx_type=<int>, tx_info=<json string>
POST <base>/api/v1/sendTxBatch  form: tx_types=<json int array>, tx_infos=<json str array>
```

A `code: 200` means the sequencer **accepted** the transaction, not that it executed. Nonces are keyed on
`(account_index, api_key_index)`, strictly sequential, and fetched lazily from `GET /api/v1/nextNonce`; they are
handed out under a process-wide lock, and a batch uses one key with **consecutive** nonces. Nonces are not
persisted -- the first use after a restart re-reads the server, which self-heals. Do not sign for the same API key
from a second process.

**Scaling and enums.** `BaseAmount = size × 10**size_decimals`, `Price = price × 10**price_decimals` per market
(from `get_markets`). Sizes round **down** and prices round half-even to the market's decimals, and
`min_base_amount` / `min_quote_amount` are enforced before signing. Order types are `LIMIT` only (stop/TWAP are
out of parity scope); TIF maps `GTC -> GOOD_TILL_TIME`, `IOC -> IOC`, `ALO -> POST_ONLY`. `cloid` is Lighter's
`client_order_index` (a uint48, unique across all markets); one is allocated from the clock when omitted.

### `get_leverage`

Read-only, so the kill switch does not apply.

```json
{ "action": "get_leverage", "data": { "coin": "BTC" }, "correlation_id": "<uuid>" }
```

**Output:**
```json
{ "coin": "BTC", "leverage": { "type": "cross", "value": 20 }, "max_leverage": 50,
  "allowed_margin_modes": ["cross", "isolated"] }
```

- Reads the account with `active_only=false`, so a market the account has traded but is flat in still reports
  its persisted margin settings; a never-touched market falls back to the market default.
- `max_leverage` derives from `min_initial_margin_fraction`; `allowed_margin_modes` is `["isolated"]` for an
  isolated-only market, else both.
- **Unit caveat:** the market's `default` / `min_initial_margin_fraction` are **basis points** (live BTC: 500 ->
  20x, 200 -> 50x), but `AccountPosition.initial_margin_fraction` is a **percentage** string (fixtures use
  `"10.00"` -> 10x). `read_leverage` disambiguates by taking the basis-point reading when it lands in the market's
  valid leverage range and the percentage reading otherwise; for Lighter's leverage range the two never overlap.

### `set_leverage`

Blocked by the kill switch.

```json
{ "action": "set_leverage", "data": { "coin": "BTC", "leverage": 10, "margin_mode": "cross" }, "correlation_id": "<uuid>" }
```

`coin` and `leverage` (integer, `1..max_leverage`; bool/float/string rejected) are required; `margin_mode` is
optional (default: keep the current mode). Isolated-only markets cannot use `cross`. **Output:**
`{ "coin", "leverage", "margin_mode", "previous": { "type", "value" } }`. On the wire this signs transaction type
20 with `initial_margin_fraction = 10000 // leverage`.

### `place_order`

Place one limit order. Blocked by the kill switch.

```json
{
  "action": "place_order",
  "data": {
    "coin": "BTC",
    "side": "buy",
    "price": "30000",
    "size": "0.001",
    "leverage": 10,
    "margin_mode": "cross",
    "order_type": "GTC",
    "reduce_only": false,
    "cloid": 691684318611
  },
  "correlation_id": "<uuid>"
}
```

| Field | Required | Notes |
|---|---|---|
| `coin` | yes | the symbol, e.g. `"BTC"` |
| `side` | yes | `"buy"` or `"sell"` |
| `price` | yes | limit price, rounded half-even to the market's `price_decimals` before signing |
| `size` | yes | size in base units, rounded **down** to the market's `size_decimals` |
| `leverage` | **yes** | integer `1..max_leverage`; applied to the market before the order and rolled back if the order fails (see [above](#leverage-is-per-market-and-every-order-names-it)) |
| `margin_mode` | no | `"cross"` or `"isolated"`; default: the market's current mode |
| `order_type` | no | `"GTC"` (default), `"IOC"`, `"ALO"` (post-only); anything else rejected |
| `reduce_only` | no | default `false` |
| `cloid` | no | Lighter `client_order_index`, a uint48 (not a `0x` hex cloid); allocated when omitted |

Unknown keys are rejected.

**Output:**
```json
{
  "coin": "BTC",
  "clientOrderIndex": 691684318611,
  "txHash": "f641...81d1",
  "status": "submitted",
  "price": "34437.6",
  "requestedPrice": "34437.6",
  "priceAdjusted": false,
  "error": null,
  "leverage": { "type": "cross", "value": 20 },
  "margin_mode": "cross",
  "previous_leverage": { "type": "cross", "value": 20 },
  "leverage_changed": false,
  "leverage_reverted": false,
  "estimated_initial_margin": "0.516564"
}
```

- **⚠ Lighter divergence:** `sendTx` does **not** return a per-order status (`code: 200` means "accepted by the
  sequencer", not "resting"/"filled"), so `status` is always `"submitted"` and there is no `oid`/`avgPx`. Confirm
  via `get_orders` / `get_order_status` by `clientOrderIndex` or `order_index`, or wait for the `account_update`
  push. Hyperliquid returns `oid` + `resting`/`filled` synchronously; Lighter cannot.
- A **venue-level rejection** (insufficient margin, below the minimum notional, a price too far from the mark,
  ...) raises `ExchangeActionError` as a packet-level `error` (there is no per-order envelope `error` field).
- `price` is the price actually submitted; `requestedPrice` is what you sent; `priceAdjusted` is `true` when they
  differ. `estimated_initial_margin` is `size × price / leverage`, an **estimate**; `null` for `reduce_only` and
  failed orders.

### `place_multiple_orders`

Place up to **50** orders (the venue's documented batch cap) in **one** `sendTxBatch`. Each item is a
`place_order` body; `leverage` is required per item:

```json
{ "action": "place_multiple_orders", "data": { "orders": [
  { "coin": "BTC", "side": "buy", "price": "30000", "size": "0.001", "leverage": 10, "order_type": "ALO" },
  { "coin": "ETH", "side": "sell", "price": "4000", "size": "0.01", "leverage": 5,
    "cloid": 691684318612 }
] }, "correlation_id": "<uuid>" }
```

Leverage is per market, so two items on the same market must agree. **Everything is validated before anything is
signed**; one bad item rejects the whole request. All transactions share one API key and **consecutive** nonces.
**Output:** `{ "results": [<place_order output>, ...], "ok_count", "error_count" }`, positional (`results[i]`
answers `orders[i]`). Blocked by the kill switch.

### `cancel_order`

Cancel one order by venue id or client id. Never blocked by the kill switch.

```json
{ "action": "cancel_order", "data": { "order_id": "c:691684318611", "coin": "BTC" }, "correlation_id": "<uuid>" }
```

`order_id` is a numeric venue `order_index`, a venue `order_id` string, or a client id written
`"c:<client_order_index>"`; the latter two are resolved through the open orders first (one REST read), each
against its own id space, so a client index can never match a different order's `order_id`/`order_index`. The
`c:` prefix exists because a client id and an order index are both integers on the wire. `coin` is optional (Lighter
can resolve it, but passing it avoids a lookup).

**Output:** `{ "coin", "canceledOrderIndexes": [...], "txHashes": [...], "errors": [...] }`. As with
`place_order`, only **acceptance** is synchronous: a submitted cancel is not proof the order was canceled --
reconcile with `get_orders`.

### `cancel_multiple_orders`

```json
{ "action": "cancel_multiple_orders", "data": { "orders": [
  { "order_id": "c:691684318611", "coin": "BTC" },
  { "order_id": 844421364625661 }
] }, "correlation_id": "<uuid>" }
```

Items are `{order_id, coin?}`. When any item lacks `coin`, ONE read of the open orders resolves every missing
coin, and an id that is not among the open orders is reported as an error outcome **without** calling the venue
(this also holds when `coin` is given: an unresolvable id or unknown coin is that item's error, never the whole
call's). Resolved cancels are submitted in chunks of 15 per REST request (Lighter documents ~15 as safe; the
batch hard cap is 50), so there is no per-call size limit; a rejected chunk marks only its own items as errors,
and a rate limit (HTTP 429) marks the unsent remainder as errors instead of retrying.
**Output:** `{ "outcomes": [{ "order_id", "coin", "ok", "error" }], "ok_count", "error_count" }` in request
order. Partial success is normal. Never blocked by the kill switch.

### `cancel_all_orders`

Uses Lighter's **native immediate cancel-all** (transaction type 16, `time_in_force 0`, timestamp `0`,
`cancel_all_market_index 255` = every market), unlike Hyperliquid's client-side sweep. `coin` scopes it to one
market; a non-empty `dex` is rejected (Lighter has no sub-ledgers). Never blocked by the kill switch.

```json
{ "action": "cancel_all_orders", "data": { "coin": "BTC" }, "correlation_id": "<uuid>" }
```

**Output:** `{ "status": "submitted", "txHash", "marketIndex" }`. There is no per-order outcome, and only
acceptance is synchronous; reconcile with `get_orders`. Note this is **not** the `requested/canceled/failed`
summary Hyperliquid's client-side sweep returns -- check `status` and reconcile rather than reading counts.

---

## Known quirks (trading)

- **Leverage is shared per market, and the dispatcher does not serialize it.** Two concurrent `place_order` calls
  on the **same market with different leverage** race: each reads the previous value, applies its own, and a
  roll-back after a rejection can restore a value the other call has since replaced. Send same-market orders with
  one leverage (a batch enforces this), or serialize them client-side.
- **A rolled-back leverage is written explicitly.** "Previous" is whatever the account read reported, including
  the market default for a market you never touched, so a roll-back leaves that market with an explicit setting
  equal to the default.
- **Ambiguous failures never roll back.** A timeout mid-POST may mean the order is live, so leverage stays as
  requested and `fatal_error` is pushed; reconcile with `get_orders` / `get_leverage`.
- **Every order costs an extra read.** One `GET /api/v1/account?active_only=false` per distinct market per call
  (the roll-back target, so it is fresh by default), plus one signed leverage update only when leverage changes.
  On rate-limited accounts set `LIGHTER_LEVERAGE_CACHE_TTL_S` (see ENVIRONMENT_VARIABLES.md) to reuse a read.
- **Rate limits.** Standard accounts get 60 `sendTx`/`sendTxBatch` requests per minute; an order that changes
  leverage is up to three signed requests (leverage, order, roll-back). HTTP 429 surfaces as `RateLimitError`
  (an `ExchangeActionError`: definitive, nothing was accepted, so a leverage roll-back is attempted).
- **Batches are chunked.** `place_multiple_orders` sends at most 15 orders per REST request (consecutive nonces
  across chunks). If a LATER chunk is rejected, earlier chunks are already live: the unsent orders come back
  with `status: "rejected"` and an `error`, and leverage is kept on every market that got an order accepted.
  A rejected FIRST chunk is a normal error and rolls leverage back.
- **`cloid` 0 is rejected.** Lighter reports 0 for orders with no client index, so an order sent with 0 could
  never be referenced again; omit `cloid` to have one allocated (1..2^48-1 accepted).
- **Acceptance is not execution.** Lighter answers `code: 200` for anything syntactically valid and may still
  reject the order afterwards; that is invisible to the synchronous reply, so the leverage roll-back (which only
  fires on a synchronous rejection) does NOT run for such an order. Watch the `account_update` push and use
  `get_leverage` / `set_leverage` to repair.
- **Market metadata is refreshed** every 5 minutes (decimals, minimums, leverage caps, new listings); an unknown
  coin does not trigger a re-fetch.
- **Pushes interleave with replies.** Placing or cancelling an order triggers `account_update` pushes on the same
  socket, often before the next reply. Match replies by `correlation_id` and skip pushes by `action`
  (`tests/lighter_cli.py` does).
- **No synchronous order status.** `place_order` always answers `status: "submitted"`; there is no `oid`/`avgPx`.
  Confirm with `get_orders` / `get_order_status` or the `account_update` push.
- **`cancel_all_orders` is actual and indiscriminate.** It cancels every resting order matching the filter,
  including ones other clients placed, and takes effect immediately on the venue.
- **A price far from the mark is rejected** (live: ~0.1% of mark → code `21734`, `limit order price is too far
  from the mark price`), surfaced as `ExchangeActionError`.
- **A native REST/WS token's deadline is short** (~6h max); the dispatcher mints and refreshes it, so do not
  cache one.

---

## Concurrency and ordering notes

- One TCP connection may pipeline requests; responses are matched by `correlation_id`, not order.
- Concurrent trading calls are safe: nonces are allocated per `(account_index, api_key_index)` under a lock, and
  a write holds the submit lock from nonce allocation through submission. Only one *process* may sign for an API
  key.
- The `account_update` stream runs on its own websocket, so its failure/reconnect cannot affect market data; a
  reconnect emits one `gap`. See [`../shared/CONCURRENCY.md`](../shared/CONCURRENCY.md) for the websocket budget
  and the encrypted-`.env` launch caveat.

## Example client workflow

```python
import json, socket, uuid

s = socket.create_connection(("localhost", 9974))

def call(action, data=None):
    body = json.dumps({"action": action, "data": data or {}, "correlation_id": str(uuid.uuid4())}).encode()
    s.sendall(f"~{len(body):04d}|".encode() + body)
    # read '~LLLL|' then LLLL bytes; skip P2 / push packets; match on correlation_id
    ...

call("subscribe", ["BTC"])                       # optional: only for the order book stream
call("get_balance")
r = call("place_order", {"coin": "BTC", "side": "buy", "price": "30000", "size": "0.001", "leverage": 10})
if r["error"] is None:                            # confirm asynchronously, then cancel
    ...
    call("cancel_order", {"order_id": f"c:{r['data']['clientOrderIndex']}", "coin": "BTC"})
```

## Verification

- **Offline:** `env PYTHONPATH=. uv run pytest tests/test_lighter_exchange.py tests/test_lighter_signer.py tests/test_perpetuals_account.py tests/test_lighter_account_stream.py`
  (native-signer scaling/rounding, nonce allocation, batch submission, cancel-all, leverage read-back, dispatcher
  handlers, wire byte budget, `account_update` parsing / dedup / gap / fan-out, and the CLI push-skipping logic).
- **Live order smoke:** `tests/lighter_account_stream_smoke.py --go` starts a real dispatcher, places a
  far-below-market BTC GTC buy at the leverage already in force, asserts the `order` `open` and `canceled` pushes,
  forces a reconnect and asserts one `gap`, then sweeps and proves 0 resting orders (real orders, pennies of
  risk).
- **Live, through the dispatcher:** `tests/lighter_cli.py --test` always runs the read-only checks and a
  side-effect-free validation check; with `--enable-execution` it also re-applies the leverage already in force
  (a no-op, verified unchanged) and runs a place / batch place / cancel / `cancel_all_orders` lifecycle on
  far-below-market BTC buys swept in a `finally`. **`cancel_all_orders` in that lifecycle sweeps every resting
  BTC order on the account, not just the test's.**
- **REPL:** `env PYTHONPATH=. uv run python tests/lighter_cli.py` (`version`, `markets`, `leverage`, `place`,
  `placemany`, `orders`, `cancel`, `cancelmany`, `cancelall`, `balance`, `positions`, `trades`, ...).

## Still open

- `place_order` confirmation is still via `get_orders` / the account stream, not a synchronous status: Lighter's
  `sendTx` returns only a tx hash.
- The account used for the live probe holds no positions, so the roll-back target for a live position has only
  been exercised through the never-traded default path. Position-level `initial_margin_fraction` unit handling is
  defensive (see the unit caveat above) pending a live position to confirm which reading the venue returns.
- Whether the websocket `auth` field accepts the read-only `ro:` token is unverified (no `ro:` token was
  available to probe); the stream mints a short-lived native token from the API key instead.
- Partial-fill order pushes were not directly observed (the probe account had no partial fills); the parser
  handles either order shape (signature dedup) and treats `fill` as the source of truth for a size change.
