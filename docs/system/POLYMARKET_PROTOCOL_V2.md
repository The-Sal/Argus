# Polymarket Protocol V2 — Master Plan

> **Status:** in progress (branch `feat/protocol-v2`), started 2026-10-19
> **Repos involved:** `Argus` (this repo), `argus-polymarket-db` (APDB sidecar), `argus-polymarket` (downstream Rust SDK)
> **Audience:** anyone who needs to understand why this change happened, what Polymarket changed, and how Argus was adapted without breaking downstream clients.

---

## 0. TL;DR

* Polymarket launched **Protocol V2**, a new on-chain *position system* that replaces the Gnosis
  Conditional Token Framework (CTF) for **new** markets. Old (v1/CTF) markets keep running unchanged.
  Both systems coexist.
* Argus already worked with v1 markets. It could **not** see or trade v2 markets because:
  1. APDB never crawled them (Gamma only returns them with `version=v2`).
  2. Orders for v2 markets must be signed against a different exchange contract (ExchangeV3, EIP-712 domain `"3"`).
  3. Token balances of v2 positions use a different asset type (`CONDITIONAL-V2`).
* Separately, and **urgently**: **Polymarket Data API v1 is retired on 2026-10-24.** Argus's
  `get_positions` uses it and must move to Data API v2 regardless of Protocol V2.
* **Hard rule of this project: the Argus client API (P1/P2) does not change.** Clients keep sending the same
  `token_id`s and keep receiving the same keys, types and nullability. Everything is internal.

---

## 1. Background: the three things called "V2"

Polymarket has used the label "V2" for three unrelated things. Mixing them up causes confusion,
so they are kept apart here:

| Name | What it is | When | Argus status |
|---|---|---|---|
| **CLOB V2** | New exchange contracts and order format (`SignedOrderV2`, domain `"2"`), collateral moved USDC.e → **pUSD** | Live 2026-04-28 | ✅ Already done (`py-clob-client-v2`, commits `49e5aef`, `138456a`) |
| **Protocol V2** | New *position system* (PositionManager + modules + Router + ExchangeV3) | Rolling out 2026 | 🚧 **This document** |
| **Data API v2** | New REST routes under `data-api.polymarket.com/v2/...` | v1 retired **2026-10-24** | 🚧 **This document** (urgent) |

(`py-clob-client-v2` is just the name of the Python SDK. It supports CLOB V2 and, from 1.2.0 on, Protocol V2 orders.)

---

## 2. What Protocol V2 actually is

### 2.1 Before: v1 / CTF markets

```
                 pUSD
                  │ split
                  ▼
   ┌───────────────────────────────┐
   │  Gnosis Conditional Tokens    │   generic 3rd-party ERC-1155
   │  (CTF)                        │   token id = hash(conditionId,
   └───────────────────────────────┘             collectionId, collateral)
          │ YES token   │ NO token               → opaque, tells you nothing
          ▼             ▼
   ┌──────────────┐  ┌──────────────────────┐
   │ CTF Exchange │  │ NegRisk CTF Exchange │   neg-risk (multi-outcome) needed
   │ 0xE111…996B  │  │ 0xe222…0F59          │   a separate adapter + exchange
   └──────────────┘  └──────────────────────┘
        EIP-712 domain "Polymarket CTF Exchange", version "2"
```

### 2.2 After: Protocol V2 markets

```
                 pUSD
                  │ split / merge / redeem
                  ▼
          ┌──────────────┐
          │    Router    │  0x1212…2600   (single entry point)
          └──────┬───────┘
                 ▼
   ┌─────────────────────────────────────────────┐
   │              PositionManager                │  0x006F…9fEF
   │   (Polymarket's own position token store)   │
   ├───────────────┬───────────────┬─────────────┤
   │ BinaryModule  │ NegRiskModule │ Combinatorial│  modules define market types
   │   id = 1      │    id = 2     │ Module id = 3│  (3 = combos / parlays, RFQ)
   └───────────────┴───────────────┴─────────────┘
                 ▲
                 │ settles trades
          ┌──────┴───────┐
          │  ExchangeV3  │  0xe333…00Aa  — ONE exchange for all v2 markets
          └──────────────┘    (standard AND neg-risk)
        EIP-712 domain "Polymarket CTF Exchange", version "3"
```

Main differences:

* **Polymarket's own system, not a third-party one.** Neg-risk is built into the core design
  (NegRiskModule) instead of being bolted on.
* **One exchange (ExchangeV3)** for every v2 market. On v2, `neg_risk` no longer selects an exchange.
* **Combinatorial markets** (combos and parlays traded through RFQ) become possible. CTF could not express these cleanly.
* **No forced migration.** Existing CTF markets and holdings stay on CTF. Polymarket can register
  individual migrations, but nothing is converted automatically. The docs give no sunset date for v1.

### 2.3 Position IDs are structured, not hashed

A v2 position ID is a 256-bit integer, transmitted as a decimal string of about 75 digits (v1 IDs are about 77).
Unlike v1 IDs it can be decoded:

```
 bits: 255..248   247..120     119..104    103..40     39..24      23..8       7..0
      ┌────────┬────────────┬──────────┬───────────┬──────────┬───────────┬─────────┐
      │moduleId│ event hash │  arity   │ reserved  │event hash│ condIndex │ outcome │
      │ (8b)   │  (128b)    │  (16b)   │ = 0 (64b) │  (16b)   │  (16b)    │  (8b)   │
      └────────┴────────────┴──────────┴───────────┴──────────┴───────────┴─────────┘
      └──────────────────── eventId (bits 24..255) ─────────────┘
      └────────────────────────── conditionId (bits 8..255) ────────────────┘
   (The masks below are from the docs. The "event hash" labels are inferred from them;
    treat everything except the masks as opaque.)

 moduleId      = id >> 248                    1=Binary 2=NegRisk 3=Combinatorial
 outcomeIndex  = id & 0xff                    0=YES 1=NO
 conditionIdx  = (id >> 8)  & 0xffff
 arity         = (id >> 104) & 0xffff
 conditionId   = id with bits 0..7  cleared   (bytes31)
 eventId       = id with bits 0..23 cleared   (bytes29)

 v2 detection  = bits 40..103 are all zero    (same rule as Polymarket's official
                                               polymarket-client SDK: is_v2_position_id)
```

Verified against live IDs (2026-10-19):

| ID | reserved bits zero | module | outcome |
|---|---|---|---|
| `4950992351…959680` (v2 YES, *polyv2-central-park-rain-2026-10-16*) | ✅ | 1 | 0 |
| `4950992351…959681` (v2 NO) | ✅ | 1 | 1 |
| `5453304381…957669` (v1) | ❌ | 120 (meaningless) | 229 |

Argus uses this rule to decide **per order** whether a token is v2. Clients never have to say which it is.

### 2.4 What a trader has to do differently

| Area | v1 / CTF market | v2 market |
|---|---|---|
| Market ID list (Gamma) | `clobTokenIds` (JSON string) | `positionIds` (array) **and** `clobTokenIds` (equal values, verified on 101/101 live markets) |
| Gamma `version` | `"v1"` | `"v2"` |
| Order signing domain | `"2"` | `"3"` |
| Exchange (`verifyingContract`) | `0xE111…996B` or neg-risk `0xe222…0F59` | `0xe333…00Aa` (ExchangeV3) |
| Order struct | salt, maker, signer, tokenId, makerAmount, takerAmount, side, signatureType, timestamp, metadata, builder | **same**; `tokenId` carries the position ID |
| BUY approval | pUSD → CTF exchange | pUSD `approve(ExchangeV3)` |
| SELL approval | CTF `setApprovalForAll(exchange)` | PositionManager `setApprovalForAll(ExchangeV3)` |
| Position balance | `asset_type=CONDITIONAL` | `asset_type=CONDITIONAL-V2` |
| Split / merge / redeem | CTF + adapters | Router; redeem takes `outcomeIndex` 0/1, not index sets |
| Resolution field | `umaResolutionStatus` | `resolutionStatus` |
| Order book / WebSocket | unchanged | unchanged, plus a `version: "v2"` key on `book` |
| Fees | taker-only, `C × rate × p × (1−p)` | same |

> Old CTF approvals **do not** carry over. Each wallet must make the two v2 approvals once before
> trading v2 markets. This is an on-chain action and is **not** automated by this project.

### 2.5 Contract addresses (Polygon, chainId 137)

| Contract | Address |
|---|---|
| ExchangeV3 | `0xe3333700cA9d93003F00f0F71f8515005F6c00Aa` |
| PositionManager | `0x006F54F7f9A22e0000CC2AB60031000000ae9fEF` |
| Router | `0x12121212006e4CD160D18e3f00711DA5c3372600` |
| BinaryModule | `0x1000008dD9001B968442c1000017eaE6E0dA00Ba` |
| NegRiskModule | `0x200000900045e3B6259600682756002200028933` |
| CombinatorialModule | `0x30000034706C7d8e12009DAB006Be20000c031A8` |
| pUSD (collateral, 6 dp) | `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` |
| CTF Exchange (v1, domain "2") | `0xE111180000d2663C0091e4f400237545B87B996B` |
| NegRisk CTF Exchange (v1) | `0xe2222d279d744050d28e00520010520000310F59` |

Source: `docs.polymarket.com/resources/contracts.md`, cross-checked against `py-clob-client-v2` 1.2.x `config.py`.

---

## 3. System architecture and where v2 touches it

```
   Downstream clients (Rust SDK: The-Sal/argus-polymarket, others)
          │  P1 JSON  (~NNNN|{action,data,correlation_id})
          │  P2 binary market data
          ▼  TCP :9972
 ┌───────────────────────────────────────────────────────────────┐
 │                 Argus  PolymarketDispatcher                   │
 │  argus/polymarket/__init__.py                                 │
 │                                                               │
 │  ┌───────────────┐  ┌──────────────────┐  ┌────────────────┐  │
 │  │ asset→ticker  │  │ order path       │  │ account / pos. │  │
 │  │ maps, subs    │  │ build_order  [B] │  │ get_positions  │  │
 │  │ (clobTokenIds)│  │ sign + post      │  │   [C] DataAPIv2│  │
 │  └──────┬────────┘  └────────┬─────────┘  │ token balance  │  │
 │         │                    │            │   [B] COND-V2  │  │
 │  ┌──────┴────────────────────┴─────────┐  └────────────────┘  │
 │  │ polymarket_direct: rest.py, wss.py  │  WS parsing      [D] │
 │  │ py-clob-client-v2 (pinned 1.2.0)[B] │                      │
 │  └──────────────────┬──────────────────┘                      │
 └─────────┬───────────┼─────────────────────────────────────────┘
           │ unix sock │ SOCKS5 via wireproxy (127.0.0.1:25344)
           ▼           ▼
 ┌──────────────────┐  ┌──────────────────────────────────────────┐
 │ APDB sidecar [A] │  │ Polymarket: clob / ws-subscriptions /    │
 │ argus-polymarket │──│ gamma-api / data-api (/v2)               │
 │ -db (Rust)       │  └──────────────────────────────────────────┘
 │ crawls Gamma     │
 │ + mesh sync      │
 └──────────────────┘
```

`[A]–[D]` are the work packages in §5.

### 3.1 Why APDB is pivotal

Argus never calls Gamma itself; every market it knows comes from APDB. APDB crawls

```
GET gamma-api/events?order=endDate&ascending=true&closed=false&limit=100&offset=N
```

with **no `version` param**. Live check, 2026-10-19: that query returned 100 events, **0** of them v2.
The same query with `&version=v2` returned **80** events, all v2. Without an APDB change, Argus never
sees a v2 market, however correct the rest of the code is.

APDB stores events as raw pass-through JSON (`#[serde(flatten)]`). Once crawled, `version`,
`positionIds` and `resolutionStatus` reach Argus with no schema change. Mesh sync records are opaque
NDJSON, so nodes on mixed versions stay compatible. **`DB_FORMAT_VERSION` must NOT be bumped**:
peers require an exact match, so a bump would split the mesh.

---

## 4. The downstream contract (must not change)

The Rust SDK (`argus-polymarket`) was audited field by field. Facts:

* **No `deny_unknown_fields`** anywhere, so *adding* keys is safe.
* **All IDs are `String`**, never parsed as integers, so 75- and 77-digit IDs are safe.
* **P2 has no fixed-width fields.** The decoder matches the Python encoder byte for byte. Depth is hard-coded to 10 in the SDK.
* The SDK does **not** call `fetch_market_by_ticker` / `fetch_all_markets`, so market metadata
  changes cannot reach it.

What **would** break it: a required field going **missing, null, or changing JSON type**.

| Response | Strict requirements | v2 risk | Mitigation |
|---|---|---|---|
| `get_positions` | all 26 fields required and non-null; `outcome_index` int; `event_id` str | Data API v2 renames fields (snake_case, `market`→`condition`), may send null/numeric values | map into the *same* dataclass with type coercion; documented fallbacks |
| `account_update` TradeEvent | `fee_rate_bps` (str), `bucket_index` (int), `maker_orders[].outcome_index` | fee fields may disappear (fees are no longer signed) | tolerant `from_dict` with type-preserving defaults |
| `place_order` | exact keys `errorMsg, orderID, takingAmount, makingAmount, status, success` | SDK 1.2.1 adds tx-hash polling and fields | pin `py-clob-client-v2==1.2.0` |
| `get_orders` | sizes and prices are **strings**, `created_at` int | — | passthrough unchanged |
| `fetch_clob_id_information` | 6 non-null strings | pre-existing bug: `outcome` may be `None` | return `''` |
| P2 | numeric CSV | pre-existing bug: empty timestamp panics the decoder | fall back to receive time |

**Enforcement:** golden key-and-type snapshots of every client-visible response, taken from the
**unmodified** code (`tests/contract/`), plus the same examples deserialised by the Rust SDK's own test suite.
Any shape change fails the tests.

---

## 5. Work packages

```
            ┌─────────────────────────── parallel ───────────────────────────┐
 Argus      │ [0] contract goldens (baseline)                                 │
 (worktrees)│ [B] position-ID utils + v2 orders + SDK pin 1.2.0 + COND-V2 bal │
            │ [C] Data API v2 positions          ← deadline 2026-10-24        │
            │ [D] WS/event robustness + pre-existing bug fixes                │
 APDB       │ [A] version=v2 crawl pass, proxy hardening, toolchain           │
 SDK        │ [E] offline contract tests (no code changes)                    │
            └────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
            merge B, C, D onto feat/protocol-v2 → run goldens [0] + SDK tests [E]
                                  │
                                  ▼
            live read-only verification through the proxy (APDB → Argus → SDK)
                                  │
                                  ▼
            (owner approval only) on-chain approvals + one tiny v2 test order
```

| # | Repo | Scope | Key files |
|---|---|---|---|
| 0 | Argus | Golden contract snapshots and P2 round-trip, taken from baseline code | `tests/contract/` |
| A | APDB | Optional `version` param plus a second `version=v2` crawl pass (toggle `APDB_CRAWL_V2`, default on); `positionIds` fallback in `extract_asset_ids`; `NULL_DISABLED=true` means never direct; rustc ≥ 1.95 | `src/poly_api.rs`, `src/refresh.rs`, `src/api.rs`, `src/proxy.rs` |
| B | Argus | `position_ids.py` (detect/decode); `build_order` uses `OrderArgsV2(position_id=…)` for v2; skip neg-risk for v2; string tick sizes incl. `0.005`/`0.0025`; `CONDITIONAL-V2` token balance; pin SDK `==1.2.0` | `argus/polymarket_direct/rest.py`, `position_ids.py`, `pyproject.toml` |
| C | Argus | `/v2/positions` with cursor pagination, mapped to the unchanged `PolymarketPosition` (`from_v2_dict`; v1 `from_dict` kept for rollback) | `rest.py:get_positions`, `order_types.py` |
| D | Argus | Tolerant `OrderEvent`/`TradeEvent`/`Trade`/`MakerOrder`; route user WS on `event_type`; handle `last_trade_price`; numeric zero-size check; `asset.id` bug (`wss.py:~1169`); P2 empty timestamp; CLOBInfo `outcome` never None; `Market.version/positionIds/resolutionStatus` (internal only) | `wss.py`, `order_types.py`, `_types.py`, `_classes.py` |
| E | SDK | serde fixtures (v1, v2, negative cases), P2 decoder tests, golden-dir test (`ARGUS_GOLDEN_DIR`), doctest fix | `tests/` |

### 5.1 Why the SDK is pinned to 1.2.0, not 1.2.1

* 1.2.0 adds everything Protocol V2 needs: `position_id`, ExchangeV3 routing and the new tick sizes.
* 1.2.1 adds synchronous polling for transaction hashes inside `post_order`/`post_orders`
  (every 0.25 s, up to 30 s, using `time.sleep`). On Argus's hot order path that is unacceptable latency.
* `py-clob-client-v2` is itself deprecated in favour of the async `polymarket-client` (`Polymarket/py-sdk`).
  Moving to it would be a rewrite and is **out of scope**.

### 5.2 Not in scope / deferred

* Automated on-chain approvals (pUSD → ExchangeV3, PositionManager → ExchangeV3).
* Split / merge / redeem through the Router (no such code exists in Argus today).
* Honouring the `order_type` argument on `place_order`. It is documented, but every order is sent as GTC today
  (`__init__.py:~1711`). This is unchanged, to keep behaviour stable; it is tracked separately.
* Migrating to `polymarket-client`.

---

## 6. Test plan

| Layer | What | Network |
|---|---|---|
| Unit | position-ID detection and decoding on real v1/v2 IDs | none |
| Unit | signed-order build for v1, v1 neg-risk and v2 IDs with a throwaway key: domain version, `verifyingContract`, `tokenId` | none |
| Unit | tick-size rounding incl. `0.005`, `0.0025` | none |
| Unit | Data API v2 → `PolymarketPosition` mapping, incl. null/numeric/missing fields | none (recorded fixtures) |
| Unit | WS frames: v1/v2 `book`, `price_change`, `last_trade_price`, unknown events, missing fields | none (recorded frames) |
| Contract | golden key/type snapshots for every client-visible response | none |
| Contract | Rust SDK deserialises the golden examples and P2 packets with v1/v2 IDs | none |
| APDB | `extract_asset_ids` on v2 markets; crawl with `version=v2` past page 0 | live, via proxy |
| Live read-only | APDB → Argus → SDK: v2 market visible, subscribable, book streams, positions match v1↔v2 | live, via proxy |
| Proxy leak | assert no socket reaches `*.polymarket.com` except through the SOCKS5 proxy | live |
| Lifecycle | one tiny far-from-market GTC on a v2 market, then cancel | **only with owner approval** |

**Proxy rule:** every request to a `*.polymarket.com` domain during development and testing goes through
the SOCKS5 proxy (`curl --socks5-hostname <host>:<port>`, or wireproxy `127.0.0.1:25344` in production).

---

## 7. Rollout order

1. **APDB** first. Deploying it only adds events, which is harmless for current Argus: unknown markets are just
   more entries, and v2 IDs look like any other token ID until someone trades them.
2. **Argus** after that. Data API v2 must be live **before 2026-10-24**, whether or not Protocol V2 is.
3. Downstream SDK: **no release required.** The new tests prove compatibility.
4. Owner performs the two on-chain approvals before trading any v2 market.

Rollback:
* APDB: set `APDB_CRAWL_V2=false`.
* Argus positions: `PolymarketPosition.from_dict` (v1) is kept, but the v1 route stops working on 2026-10-24.
* Argus orders: v1 IDs never touch the v2 path.

---

## 8. Open questions

* Whether Polymarket still creates new v1 markets, and when (if ever) v1 sunsets. The docs say nothing.
* The exact `/balance-allowance` response shape for `CONDITIONAL-V2`.
* Whether v2 user-channel WebSocket events add or drop fields. No live v2 account events have been captured yet.
* v2 markets with real liquidity: the sampled v2 book was empty, so live `price_change` on v2 is untested.
* Event-level vs market-level `version` can differ (mixed events are possible).
* Two markets labelled `v1` (559651, 560317) carry `positionIds` that **differ** from their `clobTokenIds`, possibly
  registered migrations. For trading, Argus keys off the ID bits, not the presence of `positionIds`.

---

## 9. Sources

* docs.polymarket.com: `/migrate/polymarket-v2/overview.md`, `/migrate/polymarket-v2/api-integrations.md`,
  `/resources/contracts.md`, `/resources/onchain-position-data.md`, `/trading/place-orders.md`,
  `/trading/positions/how-positions-work.md`, `/trading/positions/manage.md`, `/trading/fees.md`,
  `/migrate/data-api-v1-to-v2.md`, `/v2-migration.md` (CLOB V2), `/changelog/predictions.md`
* `py-clob-client-v2` 1.0.1 / 1.2.0 / 1.2.1 source diff (PyPI sdists)
* `polymarket-client` 0.13.0 (`_internal/protocol.py`, `actions/orders/*`)
* Live probes through the SOCKS5 proxy, 2026-10-19 (Gamma, CLOB REST, market WebSocket, Data API)
