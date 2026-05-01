# Polymarket V2 Migration Plan

## Context

Polymarket's CLOB V2 went live **April 28, 2026 ~11:00 UTC** (today). V1 clients no longer work — there is no backward compatibility. The migration requires:
- SDK namespace swap (`py_clob_client` → `py_clob_client_v2`)
- Signed order struct field changes (remove `nonce/feeRateBps/taker`, add `timestamp/metadata/builder`)
- EIP-712 domain version bump ("1" → "2") and new contract addresses
- On-chain pUSD collateral migration (wrap USDC.e before first v2 order)
- Disabling the rapid order builder (it hardcodes v1 `OrderData` fields that no longer exist)

The Rust engine (`bt1560`) communicates with Argus only via the P1/P2 TCP socket protocol — it never touches the SDK. It does not need changes unless Argus changes response shapes (it won't).

---

## Critical Files

| File | Change Type |
|---|---|
| `Argus/argus/polymarket_direct/rest.py` | **Major** — all SDK callsites |
| `Argus/argus/polymarket_direct/wss.py` | **Minor** — one import line |
| `Argus/requirements.txt` | **Dependency** — package name change |
| `Argus/Pipfile` | **Dependency** — package name change |
| `bt1560/` | **None** — P1/P2 protocol unchanged |

SDK reference (v2 source): `migration/py-clob-client-v2/py_clob_client_v2/`

---

## Phase 0 — On-chain Prerequisites (Deploy blocker)

Must be done **before** any v2 order is placed. These are wallet/contract operations, not code changes.

1. **Wrap USDC.e → pUSD** via `Collateral Onramp.wrap()` for the funder address
2. **Approve pUSD spend** on Exchange V2: `0xE111180000d2663C0091e4f400237545B87B996B`
3. **Approve pUSD spend** on Neg Risk Exchange V2: `0xe2222d279d744050d28e00520010520000310F59`

The v2 `ClobClient` constructor accepts `chain_id` and will use `neg_risk_exchange_v2` automatically for neg-risk markets when you pass `neg_risk=True` to `build_order()`.

---

## Phase 1 — Dependency Update

**`Argus/requirements.txt`** — replace:
```
# Remove:
py-clob-client
py-order-utils      # v2 SDK bundles its own order_utils internally

# Add:
py-clob-client-v2
```

**`Argus/Pipfile`** — same swap in `[packages]`.

Note: `py-order-utils` is only needed if you retain direct `OrderData` usage. Since the rapid builder is being disabled, it can be removed. Confirm no other files import from `py_order_utils` outside of `rest.py`.

---

## Phase 2 — `rest.py` Migration

File: `Argus/argus/polymarket_direct/rest.py`

### 2.1 — Replace all imports (top of file)

```python
# REMOVE all of:
from py_clob_client.clob_types import AssetType
from py_clob_client import BalanceAllowanceParams
from py_clob_client.constants import ZERO_ADDRESS
from py_clob_client.clob_types import PostOrdersArgs
from py_clob_client.config import get_contract_config
from py_order_utils.model import OrderData, SignedOrder
from py_order_utils.builders.order_builder import OrderBuilder as UtilsOrderBuilder
from py_clob_client.order_builder.constants import BUY, SELL
from py_clob_client.order_builder.builder import ROUNDING_CONFIG
from py_clob_client.client import OrderArgs, OrderType, ClobClient, PartialCreateOrderOptions

# ADD:
from py_clob_client_v2.clob_types import AssetType
from py_clob_client_v2 import BalanceAllowanceParams
from py_clob_client_v2.clob_types import PostOrdersV2Args
from py_clob_client_v2.config import get_contract_config
from py_clob_client_v2.order_builder.constants import BUY, SELL
from py_clob_client_v2.order_builder.builder import ROUNDING_CONFIG
from py_clob_client_v2.client import OrderArgsV2, OrderType, ClobClient, PartialCreateOrderOptions
```

`ZERO_ADDRESS` is no longer needed (removed from `OrderData`). `OrderData`/`SignedOrder`/`UtilsOrderBuilder` from `py_order_utils` are no longer needed.

### 2.2 — Fix SOCKS5 proxy patch (~line 320, inside `_make_httpx_clob_client`)

**Source-verified architecture (both v1 and v2 are identical):**
- `py_clob_client_v2/http_helpers/helpers.py` line 23: `_http_client = httpx.Client(http2=True)` — module-level singleton
- Every outbound HTTP call in the SDK routes through: `get/post/delete/put` → `request()` → `_http_client.request()`
- The new `RfqClient` (v2-only) also routes through these same module functions via `from ..http_helpers.helpers import get, post, delete` — fully covered
- No escape paths exist: no standalone `httpx.get()`, no `requests`, no `urllib.request` anywhere in the SDK

The monkey-patch is architecturally sound and provides **total coverage**. Replacing `_pm_helpers._http_client` before any request is made guarantees 100% of outbound SDK calls go through the patched client.

Module path update required:
```python
# REMOVE:
import py_clob_client.http_helpers.helpers as _pm_helpers

# ADD:
import py_clob_client_v2.http_helpers.helpers as _pm_helpers
```

**HTTP/2 compatibility concern (new in v2):** The default client in both v1 and v2 is `httpx.Client(http2=True)`. The WireProxy SOCKS5 transport replacement must explicitly declare `http2=True` (or `http2=False`) when constructing the replacement `httpx.Client`. If WireProxy does not support HTTP/2 over SOCKS5, set `http2=False` — httpx will not auto-downgrade gracefully. Mismatching this flag causes connection failures, not silent fallback.

**Startup assertion (required):** After patching, assert the replacement took effect:
```python
import py_clob_client_v2.http_helpers.helpers as _pm_helpers
_pm_helpers._http_client = httpx.Client(transport=socks_transport, http2=False)  # or True
# Assert:
assert hasattr(_pm_helpers._http_client, '_transport'), "proxy patch failed"
logger.info("SOCKS5 patch active: %s", type(_pm_helpers._http_client._transport))
```

### 2.3 — `create_or_derive_api_creds` → `create_or_derive_api_key` (line ~309)

```python
# REMOVE:
creds = self.clob.create_or_derive_api_creds()

# ADD:
creds = self.clob.create_or_derive_api_key()
```

### 2.4 — `post_order` kwarg rename (line ~406)

```python
# REMOVE:
self.clob.post_order(order=order, orderType=order_type)

# ADD:
self.clob.post_order(order=order, order_type=order_type)
```

### 2.5 — `post_orders` (batch) — `PostOrdersArgs` is no longer a constructor (lines ~454-458)

In v2, `PostOrdersArgs = Union[PostOrdersV1Args, PostOrdersV2Args]` — calling it like a class will `TypeError`. Replace the list comprehension:

```python
# REMOVE:
args = [PostOrdersArgs(order=x, orderType=order_type) for x in orders]

# ADD:
args = [PostOrdersV2Args(order=x, orderType=order_type) for x in orders]
```

Import `PostOrdersV2Args` from `py_clob_client_v2.clob_types`.

### 2.6 — `OrderArgs` → `OrderArgsV2` (lines ~571-581)

```python
# REMOVE:
order_args = OrderArgs(
    token_id=token_id,
    price=price,
    size=size,
    side=side,
)

# ADD:
order_args = OrderArgsV2(
    token_id=token_id,
    price=price,
    size=size,
    side=side,
)
```

`OrderArgsV2` is at `py_clob_client_v2.client`.

### 2.7 — Remove rapid order builder (lines ~586-662, `_rapid_order_builder`)

**Source analysis of what the rapid builder actually optimised:**

Reading `_rapid_order_builder` (rest.py:586-662), the path was:
1. `self.get_fee_rate(token_id)` — Argus's own pre-warmed cache (`_fee_rate_cache`), populated by `prefetch_fee_rate()` on market subscribe
2. `tick_size` and `neg_risk` — passed in directly from the `market` object (already from WS data)
3. `builder.get_order_amounts()` → `get_contract_config()` → `UtilsOrderBuilder(...)` → `OrderData(...)` → `build_signed_order()` — all pure CPU

The optimisation was avoiding the SDK's internal `__resolve_fee_rate()` call inside `create_order()`, which on v1 could block on a `GET /fee-rate` request for the first order on a token.

**The rapid builder cannot be ported to v2** — `OrderDataV2` has no `taker`, `feeRateBps`, or `nonce` fields. `UtilsOrderBuilder` is replaced by `ExchangeOrderBuilderV2`. The struct is fundamentally incompatible.

**The optimisation is already redundant in v2 — source proof:**

In v2 `create_order()` (client.py:676-714):
```python
fee_rate_bps = self.__resolve_fee_rate_bps(...) if version == 1 else None
```
For `version == 2` (the normal production case), `fee_rate_bps = None` — the fee rate network call is **completely eliminated by design**. Fees are set at match time; they are not part of the signed order.

The remaining calls inside `create_order()` — `__resolve_tick_size`, `get_neg_risk`, `__resolve_version` — are all either:
- Already bypassed by `PartialCreateOrderOptions`: Argus's normal code path already passes `PartialCreateOrderOptions(tick_size=tick_size, neg_risk=market.negRisk)` (rest.py:578-580), which causes both lookups to skip the network entirely
- Cached globally after the first call: `__cached_version` is set once and never expires

**Steady-state result:** `create_order()` with `PartialCreateOrderOptions(tick_size=..., neg_risk=...)` is already zero-network in v2. There is nothing left to optimise at the rapid builder's level.

**Action:**
1. Delete `_rapid_order_builder` (lines ~586-662), `prefetch_fee_rate`, `get_fee_rate`, `_fee_rate_cache`, `_fee_rate_futures`, `_fee_rate_lock`, `_thread_pool` (if used only for fee prefetching), and `_FakeFuture`
2. Remove `self._rapid_order_build` flag and the `if self._rapid_order_build:` branch in `build_order`
3. Remove the `POLYMARKET_RAPID_ORDER_BUILD` env var check
4. **Add one startup call** to pre-warm the version cache (avoids one HTTP round-trip on the very first order):
   ```python
   self.clob.get_version()  # pre-warms __cached_version on startup
   ```
   Tick size and neg_risk are already provided by Argus's market subscription data so no startup pre-warming needed there.
5. Confirm `build_order` always calls `clob.create_order(..., options=PartialCreateOrderOptions(tick_size=tick_size, neg_risk=market.negRisk))` — this is already the case in the normal path.

### 2.8 — `cancel` → `cancel_order` with `OrderPayload` (line ~721)

```python
# REMOVE:
self.clob.cancel(order_id)

# ADD:
from py_clob_client_v2.clob_types import OrderPayload
self.clob.cancel_order(OrderPayload(orderID=order_id))
```

Move the import to the top of the file.

### 2.9 — `get_orders` → `get_open_orders` (line ~799)

```python
# REMOVE:
self.clob.get_orders()

# ADD:
self.clob.get_open_orders()
```

### 2.10 — `cancel_orders` (batch) — name unchanged, verify call site (~line 755)

The method name is the same. The internal parameter was renamed `order_hashes` but the call site passes a list directly — no change needed. Confirm by reading the v2 SDK at `py-clob-client-v2/py_clob_client_v2/client.py`.

### 2.11 — `ClobClient` constructor — verify `chain_id` is passed

V2 `ClobClient.__init__` requires `chain_id` (137 for Polygon mainnet). Confirm wherever `ClobClient(...)` is constructed that `chain_id=137` (or the appropriate chain) is passed. The v1 constructor had it as optional; v2 treats it as required.

### 2.12 — Response shape verification (P1 protocol continuity for bt1560)

The engine deserializes these JSON fields from Argus responses. Verify each still exists in v2 SDK responses before considering migration complete:

| P1 response field | SDK source |
|---|---|
| `order_id` | `post_order` response |
| `taking_amount` | `post_order` response |
| `making_amount` | `post_order` response |
| `status` | `post_order` response |
| `success` | `post_order` or Argus wrapper |

If any field is absent/renamed in v2 responses, Argus must translate it before sending over P1.

---

## Phase 3 — `wss.py` Migration

File: `Argus/argus/polymarket_direct/wss.py`

### 3.1 — Update single import (line ~12)

```python
# REMOVE:
from py_clob_client.endpoints import GET_TICK_SIZE

# ADD:
from py_clob_client_v2.endpoints import GET_TICK_SIZE
```

### 3.2 — WebSocket URLs (no change needed)

Per the migration doc: "URLs unchanged." These hardcoded values are correct:
- `wss://ws-subscriptions-clob.polymarket.com/ws/user`
- `wss://ws-subscriptions-clob.polymarket.com/ws/market`

### 3.3 — `fee_rate_bps` in WebSocket messages (no change)

The migration doc explicitly states `fee_rate_bps` continues to appear in WS payloads reflecting actual charged fees. `MakerOrder.fee_rate_bps` and `Trade.fee_rate_bps` in `order_types.py` are preserved server-side — no changes needed.

---

## Phase 4 — `order_types.py` and `_types.py` (No Changes)

- `order_types.py`: no SDK imports; populated from REST/WS dicts. `fee_rate_bps` fields remain valid.
- `_types.py`: uses Gamma API only; no CLOB SDK imports.
- `argus/polymarket/__init__.py`: uses `rest.PolyRestAPI` and `wss.*`; no direct SDK imports.

---

## Phase 5 — bt1560 Rust Engine (No Changes)

The engine communicates only via P1/P2 TCP sockets. It has zero Polymarket SDK imports. The only risk is if Phase 2.12 reveals a response shape change — in that case, Argus translates the v2 response into the existing P1 shape before sending it. Do not modify the Rust code.

---

## Verification Steps

1. **Unit smoke test**: Instantiate `ClobClient` with v2 credentials in isolation and call `get_open_orders()` — should return without import errors.
2. **Proxy patch verification**: Add a startup log that prints `_pm_helpers._http_client` after patching — confirm it's an `httpx.Client` with SOCKS proxy transport.
3. **Order round-trip**: Place a small test order via `create_order()` + `post_order()`, then `cancel_order()` — verify P1 response fields match what bt1560 expects.
4. **Batch post**: Test `post_orders()` with a small list — confirm `PostOrdersV2Args` usage does not raise `TypeError`.
5. **On-chain check**: Before any order, confirm pUSD balance > 0 and both V2 exchange allowances are set. Use `get_balance_allowance()` (unchanged) to verify.
6. **WebSocket**: Reconnect `PolyMarketOrderBookPool` and `PolyMarketAccountEventWss` — confirm book snapshots and order lifecycle events arrive normally.
7. **Engine integration**: After Argus is live, place a test order via the P1 socket from bt1560 and verify `OrderPlacedMsg` fields deserialize correctly.

---

## Execution Order

```
Phase 0  (on-chain)  → wrap USDC.e, set allowances on both V2 exchange contracts
Phase 1  (deps)      → update requirements.txt + Pipfile
Phase 2  (rest.py)   → all 12 callsite changes
Phase 3  (wss.py)    → single import change
Phase 4  (verify)    → smoke tests
Deploy               → restart Argus, verify bt1560 integration
```

---

## Risk Notes

- **SOCKS5 proxy HTTP/2 mismatch** (2.2): the default v2 SDK client is `httpx.Client(http2=True)`. If the WireProxy SOCKS5 transport does not support HTTP/2, the replacement client must be constructed with `http2=False`. Getting this wrong causes connection failures on every request, not graceful fallback. Verify before deploy.
- **SOCKS5 proxy module path** (2.2): if `py_clob_client_v2.http_helpers.helpers` is the wrong path at install time, the patch no-ops silently. Add the startup assertion (`logger.info` the transport type) to catch this.
- **PostOrdersArgs TypeError** (2.5): silent at import time, crashes at runtime — easy to miss in testing.
- **Rapid builder auxiliary state** (2.7): `_fee_rate_cache`, `_fee_rate_futures`, `_fee_rate_lock`, `_thread_pool`, `_FakeFuture`, and `prefetch_fee_rate` are all dead code after removal. Any call sites that call `prefetch_fee_rate()` (e.g. on market subscribe) must also be removed.
- **Version pre-warm** (2.7): without a startup `clob.get_version()` call, the very first order will make a serial HTTP call to `/version` before signing. Low-impact but worth adding.
- **On-chain prerequisite** (Phase 0): orders will be rejected if pUSD allowances are not set — V2 exchange does not accept USDC.e.
- **Cutover is today**: all resting V1 orders were wiped at cutover. Re-place any orders after V2 client is live.
