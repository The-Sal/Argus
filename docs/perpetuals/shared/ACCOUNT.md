# Perpetuals Account Data (Hyperliquid + Lighter)

Read-only account data for the Argus v2 perpetual dispatchers: balance, positions,
orders, fills and funding payments. Both `HyperLiquidDispatcher` (port `9972`) and
`LighterDispatcher` (port `9974`) expose the **same six actions with the same request
and response shapes**, served by one shared implementation
(`argus/perpetuals/shared/account.py`). Order execution is a separate surface with the same
action names on both venues, driven by the shared trading control flow in
`argus/perpetuals/shared/trading.py`; the venue-specific halves are described in
`docs/perpetuals/hyperliquid/DISPATCHER.md` and `docs/perpetuals/lighter/DISPATCHER.md`.

The actions are named after `PolymarketDispatcher`'s (`docs/POLYMARKET.md`) so a client
that already speaks to Polymarket needs no new vocabulary:

| Action | Purpose | Paginated |
|---|---|---|
| `get_balance` | Equity, available balance, margin in use | no |
| `get_positions` | Open positions (non-zero size only) | yes |
| `get_orders` | Resting orders, newest first | yes |
| `get_order_status` | One order by venue id, in any lifecycle state | no |
| `get_trades` | The account's fills, newest first | yes |
| `get_funding_payments` | Funding settlements in a time window, newest first | yes |

Hyperliquid additionally exposes `get_account_fees` and `get_rate_limit_usage`
(no Lighter analog; specified in `docs/perpetuals/hyperliquid/DISPATCHER.md`). Transport, framing,
correlation IDs and error envelopes are exactly as for every other perps action
(`docs/perpetuals/shared/PROTOCOL.md`): Protocol 1 request/response over the dispatcher's TCP socket.

## Design

Three typed layers, so nothing is "dict-ed through":

1. **Venue records** (`argus/perpetuals/hyper/_classes.py`, `argus/perpetuals/lighter/_classes.py`)
   mirror each venue's API one-to-one with `Decimal` fields and `from_dict` / `to_dict`
   (Hyperliquid keeps camelCase, Lighter snake_case, exactly as upstream).
2. **Homogenous records** (`argus/perpetuals/shared/account.py`: `AccountBalance`,
   `Position`, `Order`, `Trade`, `FundingPayment`) carry the fields any perp venue can
   supply under one naming scheme, and hold the venue record under `.venue`. Each venue
   REST client implements `BaseDispatcherCompatibleAccountRest`, returning these. That
   interface has no `**kwargs` escape hatch: every argument a client can send is a
   declared, typed parameter on both the base and each implementation, so a venue cannot
   quietly diverge from the contract.
3. **Handlers** (`AccountHandlersMixin`, inherited by `BaseDispatcher`) implement the six
   actions once against that interface: argument parsing, pagination, error mapping.

On the wire every record is its homogenous `to_dict()` **plus a `venue` object** with the
venue record's full `to_dict()`. Clients that only need the common fields ignore `venue`;
clients that need venue specifics (Hyperliquid's `cumFunding`, Lighter's `allocated_margin`,
...) have them without a second request. Decimals are strings; timestamps are unix
**milliseconds** in the common fields (`timestamp_ms`); the venue's own timestamp fields are
left in whatever unit the venue uses.

## Pagination and the packet cap

A Protocol 1 response auto-compresses at **9500 bytes** and fails with
`PacketTooLargeError` if still over **9990 bytes** compressed. Because `venue` roughly
doubles each record (a Hyperliquid fill is ~600 bytes, a Lighter trade ~900), every list
action takes `offset` (default `0`) and `limit` (default **25**) and clients walk pages the
same way `docs/POLYMARKET.md` shows for `get_trades`:

```python
offset, limit, everything = 0, 25, []
while True:
    page = send_request('get_trades', {'offset': offset, 'limit': limit})['trades']
    everything.extend(page)
    if len(page) < limit:
        break
    offset += limit
```

A page of 25 of the largest record on either venue fits comfortably (verified in
`tests/test_perpetuals_account.py::WireBudgetTest`). Asking for hundreds in one page can
exceed the cap and is reported as an error rather than silently truncated.

`get_trades` only asks the venue for `offset + limit` records, so paging deeper costs more
upstream calls on Lighter (100 per request) but never more than the page needs.

## Configuration

| Venue | Env | Needed for |
|---|---|---|
| Hyperliquid | `HYPERLIQUID_WALLET_ADDRESS` (must be the **master** address, not an API wallet) | everything; all reads are unsigned `info` requests |
| Lighter | `LIGHTER_ACCOUNT_INDEX` | `get_balance`, `get_positions` (public reads) |
| Lighter | `LIGHTER_AUTH_TOKEN` (read-only token, `ro:...`) | `get_orders`, `get_order_status`, `get_trades`, `get_funding_payments` |

Trading credentials are separate from the account-read credentials above: see
`docs/perpetuals/lighter/DISPATCHER.md` (Lighter) and `docs/perpetuals/hyperliquid/DISPATCHER.md`
(Hyperliquid). Lighter trading uses `LIGHTER_ACC_INDEX` + `LIGHTER_API_INDEX` +
`LIGHTER_PRIVATE_KEY`; the read-only token only unlocks the REST reads.

Details, including how to mint a Lighter read-only token, are in
`docs/system/ENVIRONMENT_VARIABLES.md`. An action whose configuration is **missing** answers
with an error whose message names the missing variable (`AccountNotConfiguredError`);
market-data actions are never affected.

A value that is **present but invalid** is different: a `LIGHTER_AUTH_TOKEN` that is malformed,
expired, or scoped (`single`) to another account than `LIGHTER_ACCOUNT_INDEX` makes the Lighter
dispatcher **refuse to start** (`ValueError` from `LighterRest.__init__`), market data
included. Unset it to run market-data-only, or mint a fresh token. Read-only tokens are valid for
at most 10 years, so put the expiry date in your calendar.

## Actions

Every request is `{"action": ..., "data": {...}, "correlation_id": "<uuid>"}`. `data` may be
`{}` or `null` where no arguments are needed.

**Unknown keys are rejected**, they are not ignored. Each action below lists exactly the keys
it accepts and anything else comes back as an error naming the accepted set. On a trading API
a typo such as `dxe` quietly answering from the primary ledger instead of the one the caller
asked for is worse than a refused request.

`dex` is the one venue-scoping argument, accepted by `get_balance`, `get_positions` and
`get_orders`. It names one of a venue's sub-ledgers and `""` always means the primary one.
Hyperliquid maps it to the HIP-3 dex whose clearinghouse holds the positions and orders.
Lighter has a single ledger per account today, so it accepts only `""` and rejects anything
else rather than silently reporting the wrong account. Fills and funding are account-wide on
both venues, so the other three actions take no scope at all.

**Clients do not need to know which ledgers exist.** `get_positions` and `get_orders`
default to **every** ledger (omit `dex`), and each record carries a `dex` field saying where
it lives; pass `dex` (`""` or a HIP-3 name) only to narrow. `get_balance` answers for the
account as a whole whenever the venue keeps one pool of collateral (see below).

## Account modes (Hyperliquid)

Hyperliquid accounts can keep their books in different ways (the `userAbstraction` info
request). The dispatcher reads the mode (cached for 5 minutes) and derives the same
`get_balance` shape either way, so a client never branches on it:

| Mode | Where collateral lives | `get_balance` reads |
|---|---|---|
| `default`, `disabled`, `dexAbstraction` | separate perp / spot balances | the perps clearinghouse for `dex` |
| `unifiedAccount`, `portfolioMargin` | one pool in the **spot** ledger backing spot and every perp dex | spot balances + every perp dex (`dex` is ignored) |

For unified / portfolio-margin accounts Hyperliquid's own docs say the perps
`accountValue` / `withdrawable` are "not meaningful" (they read `0`), which is why an
account funded in the UI used to look empty here. There, `account_value` is spot USDC plus
unrealized PnL across all perp dexes, `available_balance` is spot's
`tokenToAvailableAfterMaintenance` for USDC, and margin / notional are summed over the dexes.

Caveats: the docs give no equity formula, so "spot USDC + unrealized PnL" was inferred, then
checked live with a ~$11 ETH position open (`tests/hyper_order_lifecycle.py`: balance 14.994 =
15.0 USDC minus 0.006 unrealized PnL, margin used 0.556, notional 11.12); other collateral tokens (USDT0, ...) are listed in
`assets` but not priced into `account_value`; portfolio-margin borrowing is not modelled. A
mode this client does not recognise raises `UnsupportedAccountModeError` instead of
guessing. Reading every dex costs one `info` call per dex (spaced 0.2 s apart), so an
account-wide read takes a couple of seconds.

### `get_balance`

```json
{"action": "get_balance", "data": {}}
```
Accepts `dex` (default `""`; Hyperliquid only, see above; ignored for unified accounts).

**Output:**
```json
{
  "account": "0x...",                      // wallet address (Hyperliquid) or account index (Lighter)
  "account_value": "13109.482328",         // equity incl. unrealized PnL
  "available_balance": "13104.514502",     // withdrawable / free
  "total_margin_used": "4.967826",
  "total_position_notional": "100.02765",
  "account_mode": "default",               // informational (Hyperliquid: default | unifiedAccount | ...); null on Lighter
  "assets": [                              // per-asset holdings where the venue reports them (Hyperliquid unified: spot balances)
    {"asset": "USDC", "total": "15.0", "available": "15.0", "usd_value": "15.0"}   // usd_value null when not priced (e.g. HYPE)
  ],
  "venue": { ... }                         // Hyperliquid clearinghouseState (or, for unified accounts, {mode, spot, perps[]}) / Lighter account
}
```

### `get_positions`

```json
{"action": "get_positions", "data": {"offset": 0, "limit": 25}}
```
Accepts `offset`, `limit`, `dex` (default: every ledger).

**Output:** `{"account": ..., "positions": [Position, ...]}` where

```json
{
  "name": "ETH",                 // symbol as used by subscribe / get_funding_rate
  "signed_size": "0.0335",       // + long, - short
  "side": "long",
  "entry_price": "2986.3",       // null if not reported
  "notional": "100.02765",
  "unrealized_pnl": "-0.0134",
  "liquidation_price": "2866.26936529",   // null if none
  "leverage": "20",              // null on Lighter (it reports a margin fraction; see venue.initial_margin_fraction)
  "margin_used": "4.967826",
  "dex": "",                     // ledger the position lives on ("" = primary, else the HIP-3 dex name)
  "venue": { ... }
}
```

### `get_orders`

```json
{"action": "get_orders", "data": {"offset": 0, "limit": 25}}
```
Accepts `offset`, `limit`, `dex` (default: every ledger).

**Output:** `{"account": ..., "orders": [Order, ...]}` where

```json
{
  "order_id": "91490942",        // always a string (Lighter ids exceed 2^53)
  "client_order_id": null,
  "name": "BTC",
  "side": "sell",
  "price": "29792.0",
  "original_size": "5.0",
  "remaining_size": "5.0",
  "order_type": "Limit",         // venue string, untranslated
  "status": "open",              // venue lifecycle string, untranslated; always "open" here
  "reduce_only": false,
  "timestamp_ms": 1681247412573,
  "dex": "",                     // ledger the order rests on ("" = primary, else the HIP-3 dex name)
  "venue": { ... }
}
```

### `get_order_status`

```json
{"action": "get_order_status", "data": {"order_id": "91490942"}}
```
Hyperliquid: a numeric id is an `oid`; anything else is treated as a `cloid`.
Lighter: has no by-id lookup, so resting orders are scanned first, then the most recent
100 inactive orders. Older orders report `found: false`.

**Output:** `{"found": true, "order": Order}` or `{"found": false, "order": null}`. The
order's `status` carries the lifecycle state (`filled`, `canceled`, ...).

### `get_trades`

```json
{"action": "get_trades", "data": {"offset": 0, "limit": 25}}
```

**Output:** `{"account": ..., "trades": [Trade, ...]}` where

```json
{
  "trade_id": "118906512037719",
  "order_id": "90542681",        // the account's order this fill belongs to
  "name": "AVAX",
  "side": "buy",
  "price": "18.435",
  "size": "93.53",
  "fee": "0.01",                 // negative = rebate
  "is_maker": true,
  "realized_pnl": "0.0",         // null if the venue does not report it
  "timestamp_ms": 1681222254710,
  "venue": { ... }
}
```
History depth is the venue's: Hyperliquid serves its 2000 most recent fills; Lighter is
walked by cursor as deep as the page requires.

### `get_funding_payments`

```json
{"action": "get_funding_payments", "data": {"start_time": 1681000000000, "end_time": 1681222254710, "offset": 0, "limit": 25}}
```
`start_time` / `end_time` are unix ms; `end_time` defaults to now and `start_time` to seven
days before `end_time`.

**Output:**
```json
{
  "account": "...",
  "start_time": 1681000000000,
  "end_time": 1681222254710,
  "funding_payments": [
    {
      "name": "ETH",
      "timestamp_ms": 1681222254710,
      "rate": "0.0000417",          // per settlement interval
      "position_size": "49.1477",   // signed
      "payment": "-3.625312",       // signed: negative = paid, positive = received
      "venue": { ... }
    }
  ]
}
```
Lighter is capped at 500 settlements per request (about three weeks of hourly funding) so a
wide window cannot turn into an unbounded number of upstream calls.

### Push (Hyperliquid and Lighter)

Order lifecycle changes and fills are also pushed as `account_update` (no polling needed to learn about a
fill or cancel); see `docs/perpetuals/hyperliquid/DISPATCHER.md#account_update` and
`docs/perpetuals/lighter/DISPATCHER.md#account_update`. The records reuse the shapes above (`fill` is a
`get_trades` record), and on both venues no `subscribe` is required to receive the push.

### Hyperliquid-only

`get_account_fees` and `get_rate_limit_usage` have no Lighter analog and are specified in
`docs/perpetuals/hyperliquid/DISPATCHER.md`.

## CLI

`tests/hyper_cli.py` and `tests/lighter_cli.py` have matching commands: `balance`,
`positions`, `orders`, `order <id>`, `trades`, `fundingpay [days]` (plus `fees` and
`ratelimit` on Hyperliquid, and `watch` for live `account_update` pushes), and their `test` / `--test` gauntlet validates every account
action's shape live. Account checks report `SKIP` rather than `FAIL` on a dispatcher whose
account side is unconfigured.

## Adding a venue

1. Type the venue's account payloads in its `_classes.py` with `from_dict` / `to_dict`, and
   give each a `to_common()` returning the matching homogenous record.
2. Implement `BaseDispatcherCompatibleAccountRest` on the venue REST client, matching its
   signatures exactly (`get_positions` / `get_open_orders` take `dex: Optional[str] = None`,
   meaning every ledger). If the venue has no sub-ledgers, reject a non-empty `dex` rather
   than ignoring it. If the venue has account modes that move collateral around, resolve
   them inside `get_account_balance` so clients keep one shape.
3. Pass it as `account_rest=` to `BaseDispatcher.__init__` and merge
   `self.account_routing_table()` into the routing table.

Nothing in the handlers or the wire format changes.
