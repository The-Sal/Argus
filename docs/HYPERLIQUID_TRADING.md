# Hyperliquid Trading (Order Execution)

Signed order execution for `HyperLiquidDispatcher` (port `9972`): placing limit orders and
canceling them. This surface is **Hyperliquid-only** -- `LighterDispatcher` remains
read-only until its trading layer is implemented.

It complements `docs/PERPETUALS_ACCOUNT.md` (read-only account data) and `docs/POLYMARKET.md`
(the naming source for the actions): the two trading actions are named `place_order` /
`cancel_order`, matching `PolymarketDispatcher`, so a client that already trades against
Polymarket needs no new vocabulary.

Implementation: `argus/perpetuals/hyper/exchange.py` (`HyperLiquidExchange`). Credentials
come from `HYPERLIQUID_WALLET_ADDRESS` + `HYPERLIQUID_PRIVATE_KEY` -- the private key must
belong to the **master** account address, not an API wallet.

> **Scope:** order *modification* is deliberately not implemented, and neither is a
> cancel-all. Hyperliquid has no immediate cancel-all action -- its only bulk cancel is
> `scheduleCancel`, a one-shot trigger at least 5 seconds in the future (max 10/day).

---

## Signing (what the dispatcher does for you)

Every action is EIP-712 signed with the wallet key under Hyperliquid's L1 ("phantom agent")
scheme, then POSTed to `/exchange`:

1. The action dict is `msgpack`-packed in its own key order, followed by an 8-byte
   big-endian nonce and a vault tag (`0x00` when no vault), and keccak-256 hashed.
2. The hash becomes the `connectionId` of a synthetic `Agent` struct signed under the
   Exchange domain (chainId `1337`, zero verifying contract).

Because the server recomputes step 1 from the received JSON, **key order is part of the
signature** -- the client packs and sends the same dict. Nonces are unix-millisecond
timestamps and must be unique; two orders submitted in the same millisecond would collide,
so serialize submissions per wallet.

### Asset ids

The wire's asset field is a **global** id:

| Space | Id range |
|---|---|
| Default dex perps | raw universe index (`BTC` = 0, `ETH` = 1, ...) |
| Builder-deployed (HIP-3) dexes | `110000 + slot * 10000 + index_in_universe` |
| Spot | `10000 + ...` (not exposed here) |

`slot` is the dex's position in the `perpDexs` info response (its first entry is `null` for
the default dex). `HyperLiquidExchange.resolve_asset_id` does this mapping and caches the
per-dex universes for 60s.

---

## `place_order`

Place a single limit order.

### Request

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
| `coin` | yes | `"BTC"` (default dex) or `"xyz:AAPL"` (HIP-3 dex) |
| `side` | yes | `"buy"` or `"sell"` |
| `price` | yes | Limit price. Rounded to the venue's tick rules before submission: a fractional price keeps 5 significant figures and at most `6 - szDecimals` decimals (nearest integer once the integer part already has 5+ digits); a whole-number price is never changed. The response reports the price actually used |
| `size` | yes | Size in coins (at most 8 decimals) |
| `order_type` | no | `"GTC"` (default), `"IOC"`, `"ALO"` (post-only) |
| `reduce_only` | no | Default `false`. `true` only reduces an existing position |
| `cloid` | no | Client order id: `0x` + 32 hex chars (16 bytes) |

### Response

```json
{
  "action": "place_order",
  "data": {
    "coin": "BTC",
    "oid": 123456789,
    "status": "resting",
    "avgPx": null,
    "price": "30000",
    "requestedPrice": "30000.123456",
    "priceAdjusted": true,
    "error": null
  }
}
```

- `price` is the limit price **actually submitted** (after tick rounding); `requestedPrice` is what you
  sent; `priceAdjusted` is `true` when they differ. Always compare against `price`, not your own
  input, when reconciling the order. They are also set when the order is rejected (`error`).
- `status` is `"resting"` (on the book) or `"filled"` (immediately matched, with `avgPx` set).
- A **venue-level rejection of the order** (e.g. insufficient margin, below the $10 minimum
  value) is reported in `error` with `oid: null` -- it is **not** a packet error. This is the
  venue's normal per-order outcome channel; callers must check `error` rather than assume
  the order was accepted.
- A rejected *envelope* (invalid signature, stale nonce) raises a dispatcher error instead.

---

## `cancel_order`

Cancel one order by venue id (`oid`) or client id (`cloid`).

### Request

```json
{ "action": "cancel_order", "data": { "order_id": 123456789, "coin": "BTC" }, "correlation_id": "<uuid>" }
```

| Field | Required | Notes |
|---|---|---|
| `order_id` | yes | Numeric oid, or a `0x`-prefixed 16-byte cloid |
| `coin` | no | When omitted, the dispatcher resolves it from the order via `orderStatus` -- which also fails cleanly if the order no longer exists. Resolution costs one extra info call, so pass `coin` when you already know it |

### Response

```json
{
  "action": "cancel_order",
  "data": { "coin": "BTC", "canceledOids": [123456789], "errors": [] }
}
```

The venue answers with per-id statuses: `canceledOids` holds the ids it acknowledged and
`errors` holds the messages for ids it could not cancel (already filled/canceled, or
unknown). **Check `errors`/`canceledOids`** -- a submitted cancel is not proof the order
was canceled. The typical error for a dead id is:

```
Order was never placed, already canceled, or filled.
```

---

## Verification

- **Offline:** `env PYTHONPATH=. uv run pytest tests/test_hyper_exchange.py` -- signing
  round-trip (recovers to the signer), pinned action-hash vector, wire key order, tick
  rounding, asset-id offsets, response parsing, and the dispatcher handlers.
- **Live:** `env PYTHONPATH=. uv run python tests/hyper_order_smoke.py` -- places a
  far-below-market resting order, confirms it via `orderStatus`, cancels it, and repeats
  with a cloid. Requires a funded account to meet the venue's minimum order value; without
  funds it reports the funding shortfall and exits.
- **REPL:** `env PYTHONPATH=. uv run python tests/hyper_cli.py` then `place ...` / `cancel ...`.
