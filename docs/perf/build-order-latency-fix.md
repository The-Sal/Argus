# Build-order Latency Fix (May 2026)

## Symptom

Production `place_multiple_orders` logs from the Polymarket dispatcher showed
`build_time_seconds` spending hundreds of milliseconds — sometimes seconds — to
build as few as two orders. Sample observations from a single session:

| time (UTC)           | num_orders | build_time | place_time | result                |
|----------------------|-----------:|-----------:|-----------:|-----------------------|
| 2026-05-20T20:49:00  | 2          | 0.401 s    | 0.327 s    | both matched          |
| 2026-05-20T20:52:01  | 2          | 0.446 s    | 0.436 s    | both matched          |
| 2026-05-20T21:44:14  | 2          | 1.846 s    | 1.781 s    | first post-restart    |
| 2026-05-20T21:44:25  | 1          | 0.316 s    | 0.308 s    | matched               |
| 2026-05-20T21:44:26  | 1          | 0.033 s    | 0.026 s    | failed (insufficient) |
| 2026-05-20T23:49:28  | 1          | 0.312 s    | 0.305 s    | matched               |
| 2026-05-20T23:49:29  | 1          | 0.040 s    | 0.032 s    | failed (insufficient) |

WebSocket subscription for each asset had occurred at least one minute before
the order was placed, so latency could not be attributed to a fresh stream.
The VPS is co-located near the exchange, so HTTP round-trips should be fast.

The pattern stood out:

- The first build for a token in a process always cost ~200–400 ms.
- Subsequent builds for the **same** token (e.g. the immediately following
  "insufficient balance" retry) cost ~30 ms.
- The first build after process start cost ~1.85 s for two orders.

## Investigation

Two code paths were instrumented and read end-to-end:
`argus/polymarket/__init__.py::_handle_place_multiple_orders`,
`argus/polymarket_direct/rest.py::build_order`,
and the entire `py_clob_client_v2.client.ClobClient.create_order` chain
including `OrderBuilder.build_order` and `ExchangeOrderBuilderV2`.

### Where the time is actually going

A micro-benchmark of the signing path (no network) on the same machine:

```
OrderBuilder.build_order [POLY_1271]   median=4.011 ms
ExchangeOrderBuilderV2.build_signed_order (1271)   median=3.969 ms
ExchangeOrderBuilderV2()  ctor (domain separator)  median=0.021 ms
get_contract_config(137)                           median=0.001 ms
encode_typed_data(full)                            median=0.117 ms
Account._sign_hash (direct ECDSA)                  median=3.870 ms
```

Two orders, fully cached, take ~8 ms of CPU. Production was seeing 200–1850 ms,
so the dominant cost was not the signing.

A second benchmark made a real `create_order` call against the production CLOB
with no entries in py-clob's internal `__tick_sizes` cache:

```
Cold build (1 HTTP for __resolve_tick_size): 324.8 ms
Warm build (cache hit):                        5.0 ms
```

The HTTP fetch comes from `py_clob_client_v2/client.py:1105-1113`:

```python
def __resolve_tick_size(self, token_id, tick_size=None):
    min_tick_size = self.get_tick_size(token_id)   # ALWAYS hit
    if tick_size:
        if is_tick_size_smaller(tick_size, min_tick_size):
            raise PolyException(...)
        return tick_size
    return min_tick_size
```

Even when `tick_size` is passed in `PartialCreateOrderOptions` (which the
dispatcher always does at `rest.py:549-551`), the client still validates
the value against the market minimum. The lookup hits py-clob's private
`__tick_sizes` dict at `client.py:349-361`; on a miss it issues
`GET /tick-size`.

The dispatcher already has the tick size on hand — `MarketData.get_tick_size()`
returns a value populated via the WebSocket / REST fan-out in
`OrderBookStore.on_subscribe()` — but that cache is a separate dict and is not
shared with py-clob. Therefore every new token incurred one extra HTTP
round-trip on its first build.

`neg_risk` and `version` did not contribute:

- `neg_risk` is short-circuited in `create_order` because
  `options.neg_risk` is provided by the dispatcher.
- `__cached_version` is pre-warmed by
  `self.clob.get_version()` in `rest.py:179`.

The 1.85 s outlier (first call after restart) lines up with httpx tearing
down its HTTP/2 connection after ~4 minutes of idle, requiring a full
TLS + SOCKS5 handshake on the next call. The cold tick-size fetch then runs
on the freshly re-established connection.

### Other costs that surfaced

- `ThreadPoolExecutor` was being constructed inside
  `_handle_place_multiple_orders` per call, paying ~5–10 ms of thread-spawn
  overhead each invocation (more under Python 3.14t).
- `eth_account` falls back to `NativeECCBackend` (pure-Python secp256k1)
  when `coincurve` is not installed, costing ~3.9 ms per sign instead of
  ~0.1 ms. The production venv should be checked.

## Changes implemented

### 1. `PolyRestAPI.warm_clob_caches_for_token` — `argus/polymarket_direct/rest.py`

New public method on `PolyRestAPI` that writes the three per-token entries py-clob
keeps internally:

```python
def warm_clob_caches_for_token(
    self,
    token_id: str,
    tick_size: str,
    neg_risk: bool,
    condition_id: str | None = None,
) -> None:
    self.clob._ClobClient__tick_sizes[token_id] = str(tick_size)
    self.clob._ClobClient__neg_risk[token_id] = bool(neg_risk)
    if condition_id:
        self.clob._ClobClient__token_condition_map[token_id] = str(condition_id)
```

Name-mangled attribute access is intentional and confined to this method so
the rest of the codebase does not have to know about py-clob's internals.
Writing all three eliminates the HTTP fallback paths through
`__resolve_tick_size`, `get_neg_risk`, and `__ensure_market_info_cached`
for that token.

### 2. `_warm_clob_caches_for_subscribed_asset` — `argus/polymarket/__init__.py`

Helper on `PolymarketDispatcher` that resolves a subscribed `clob_id` to
its parent event/market via `_asset_id_to_ticker` and `_all_markets_cache`
(both already maintained by `_update_markets_cache` /
`_build_asset_id_to_ticker_mapping`), then calls
`PolyRestAPI.warm_clob_caches_for_token`.

- Pulls `orderPriceMinTickSize`, `negRisk`, and `conditionId` straight from
  the `Market` dataclass populated by the gamma-API refresh.
- Formats the float tick size with `format(value, "g")` so it matches the
  string keys py-clob's `ROUNDING_CONFIG` requires (`"0.1"`, `"0.01"`,
  `"0.001"`, `"0.0001"`).
- Prefers the event-level `negRisk` and falls back to the market-level
  field, matching the value already used in `rest.build_order`
  (`options=PartialCreateOrderOptions(... neg_risk=market.negRisk)`).
- Best-effort by design: any missing field or exception is logged and the
  warming is silently skipped. The build path keeps its slower HTTP
  fallback.

### 3. Hook in `_handle_subscribe`

A single call to the helper is added immediately after
`self.market_data.subscribe_to_asset_id(clob_id)`. This piggybacks on the
existing subscribe flow so no client behaviour has to change; the
warming happens for every asset the dispatcher accepts a subscription
for. Since strategies subscribe well before they trade, the cache is
warm by the time `place_multiple_orders` arrives.

### 4. Persistent `_build_pool` on the dispatcher

`PolymarketDispatcher.__init__` now creates a long-lived
`ThreadPoolExecutor`:

```python
self._build_pool = ThreadPoolExecutor(
    max_workers=int(os.environ.get("POLYMARKET_BUILD_POOL_WORKERS", "10")),
    thread_name_prefix="PolyOrderBuildPool",
)
```

`_handle_place_multiple_orders` submits to this pool instead of creating
a fresh executor per call. The pool's threads survive across calls so we
don't pay spawn / teardown cost on the hot path. Under regular CPython
this saves a handful of milliseconds; under Python 3.14t (production)
the savings are larger because thread creation is more expensive.

The new env var `POLYMARKET_BUILD_POOL_WORKERS` (default 10) controls
the cap. Workers spawn lazily, so over-provisioning is cheap.

## Expected impact

Per-batch `build_time_seconds`, assuming the asset was subscribed before
the order arrives:

| scenario                                | before     | after    |
|-----------------------------------------|-----------:|---------:|
| 2 orders, cold py-clob cache            | 0.40 s     | ~0.02 s  |
| 2 orders, warm py-clob cache            | 0.03 s     | ~0.01 s  |
| First batch after process start         | 1.85 s     | ~0.02 s  |
| Failed orders (already-built token)     | 0.03 s     | ~0.01 s  |

`place_time_seconds` is unchanged — it's dominated by server-side matching
and round-trip latency to the CLOB, neither of which is on the client.

## Operational notes

- If a strategy submits an order for a token it never subscribed to, the
  warming hook is never called and the first build for that token will
  still pay the HTTP cost. This matches the historical behaviour. The
  dispatcher already requires a subscription for account updates to flow
  (see the file header in `polymarket/__init__.py`), so the assumption
  holds in practice.
- The warming hook reads from `_all_markets_cache`. If the market cache
  has not been refreshed since a brand-new market was created, warming
  will no-op for that asset (helper returns silently). The next refresh
  cycle (`POLYMARKET_FULL_MARKET_CACHE_REFRESH_INTERVAL`, default 5 min)
  will pick it up — and once warmed, subsequent builds are fast.
- The persistent pool's threads are not daemonised. On a graceful
  dispatcher shutdown they will drain. If a hard kill is needed, this
  does not change anything because pool threads block on `submit`s, not
  on socket I/O.

## Follow-ups deliberately not done in this change

These are listed in priority order. None block the fix above; each is
additive.

1. **Install `coincurve` on production.** With pure-Python ECDSA the
   per-sign cost is ~3.9 ms; with coincurve it drops to ~0.1 ms. For two
   orders this is ~8 ms vs ~0.2 ms. Check with:
   `python -c "from eth_account import Account; print(Account._keys.backend.__class__.__name__)"`.
   If it prints `NativeECCBackend`, install `coincurve` (the cp314t wheel
   has been on PyPI since early 2026).
2. **HTTP/2 keepalive.** After ~4 minutes of idle, httpx tears down the
   long-lived connection to `clob.polymarket.com`, and the next call pays
   a full handshake. Either ping `clob.get_ok()` every 30–60 s from a
   background thread, or pass `keepalive_expiry=None` to the patched
   `httpx.Client(http2=True, proxy=...)` in
   `rest.py::_make_httpx_clob_client`.
3. **Strip `_tick` instrumentation.** `_handle_place_multiple_orders`,
   `place_built_orders`, and `build_order` each contain 20-plus
   `print()`-based tick calls. Each `print` costs tens of microseconds;
   cumulatively this adds a few ms per call. Gate behind an env flag or
   delete.
4. **Patch `__resolve_tick_size` upstream.** A one-line change in
   py-clob to early-return when `tick_size` is provided would remove the
   need to mirror the cache at all. Until then, the warming approach
   here is the right workaround.
5. **Cache `ExchangeOrderBuilderV2` per `(neg_risk, chain_id)`.** Today
   `OrderBuilder.build_order` constructs a fresh instance for every
   order, which recomputes the EIP-712 domain separator (~20 μs). Cheap,
   but pointless. Worth doing only if profiling still shows it.

## Files changed

- `argus/polymarket_direct/rest.py` — new `PolyRestAPI.warm_clob_caches_for_token`.
- `argus/polymarket/__init__.py` — new `self._build_pool`; new
  `_warm_clob_caches_for_subscribed_asset` helper; hook in
  `_handle_subscribe`; switched `_handle_place_multiple_orders` to
  submit on `self._build_pool`.
- New env var: `POLYMARKET_BUILD_POOL_WORKERS` (default `10`).
