# ForecastEx API Migration: `trsrv/event/*` → EventTrader `tws.proxy`

**Status:** implemented
**Scope:** `argus/ib/forecast.py`, `argus/ib/_forcast_utils.py`, new `argus/ib/_fxc_api.py`
**Tests:** `tests/test_fxc_api.py` (offline fixtures in `tests/fixtures/fxc/`)

## Symptom

The `ib.forecast` dispatcher failed on `get_all_markets` with:

```
requests.exceptions.HTTPError: 404 Client Error: Not Found for url:
  https://api.ibkr.com/v1/api/trsrv/event/category-tree
```

The auth session was healthy (`iserver/auth/status` returned
`"authenticated": true`), so this was **not** a login/cookie problem.

## Root cause

IBKR decommissioned the entire `api.ibkr.com/v1/api/trsrv/event/*` service. Both
endpoints the dispatcher depended on now return `HTTP 404`:

| Old endpoint | Result |
| --- | --- |
| `GET api.ibkr.com/v1/api/trsrv/event/category-tree` | `404` |
| `GET api.ibkr.com/v1/api/trsrv/event/contracts?market=…` | `404` |

Meanwhile the live **EventTrader** web app (the ForecastEx front-end) uses a
different service, `tws.proxy/forecasttrader/*`. That is now the only source of
ForecastEx market/contract metadata.

> The EventTrader app fetches this service relative to its own origin
> (`https://www.interactivebrokers.com/tws.proxy/…`). The REST calls work with
> the same IBKR cookie the dispatcher already holds; they do **not** need the
> `iserver` brokerage bridge that the `api.ibkr.com` host requires.

## New endpoints

Base: `https://www.interactivebrokers.com/tws.proxy/forecasttrader`

| Purpose | Endpoint | When it is hit |
| --- | --- | --- |
| Category / market tree | `GET /category/tree` | first `get_all_markets` (cached) |
| All contracts for a market | `GET /contract/market?underlyingConid=<conid>&exchange=FORECASTX` | first `activate_market` for a market (cached) |
| Market rules / metadata | `GET /contract/rules?conid=<outcome>` | first activation of a market (cached) |

Key structural facts discovered while mapping the responses:

* `category/tree` returns `{"categories": {<id>: {"name", "parent_id", "markets":[…]}}}` —
  note the top-level `categories` wrapper and `name`/`parent_id` (the old tree
  used `label`/`parentId` at the top level).
* A category-tree market entry is `{name, symbol, exchange, conid, product_conid}`.
  Its `conid` is the **underlying** conid used by `/contract/market`.
* `/contract/market` can span **several expirations** (e.g. successive FOMC
  meetings), and the same `strike_label` is reused across them. A "big contract"
  is therefore exactly one expiration.
* `/contract/rules` is **market-level** metadata (description, source agency,
  links, payout, price increment, resolution/payout times). One call for any
  outcome covers the whole big contract.
* The `/contract/market` contract payload uses `snake_case` and `side` (`Y`/`N`),
  whereas the legacy internal schema is `camelCase` with `putOrCall` (`C`/`P`).

## Architecture

```
FXCDispatcher
  └── FxCRestClient            argus/ib/_fxc_api.py   (HTTP, cache, throttle, rules enrichment)
        ├── category_tree()                          -> raw tree
        ├── markets()                                -> list[AbstractMarket]
        ├── market_payload(conid)                    -> raw /contract/market
        ├── contract_rules(conid, cache_key=market)  -> raw /contract/rules
        ├── expirations(conid)                       -> [YYYYMMDD, …]
        └── big_contract(conid, expiration, enrich)  -> FxContractBig

Contract layer              argus/ib/_forcast_utils.py
  ├── normalize_ibkr_market_contract()   snake_case -> legacy camelCase micro
  ├── select_default_expiration()        nearest future expiration
  ├── FxContractBig.from_ibkr_market()   group one expiration into mini/micro
  └── FxContractBig.apply_contract_rules()  restore legacy rules-derived fields
```

`FXCDispatcher.__init__` builds `self._fxc_rest = FxCRestClient(self.ws.networker.session)`.
`_urls` was removed.

## Data mapping

### `/contract/market` → legacy micro schema

| Legacy field | Source |
| --- | --- |
| `conid` | `conid` |
| `putOrCall` | `side`: `Y`→`C`, `N`→`P` |
| `strikeLabel` | `strike_label` |
| `strike` | `strike` |
| `expiration` | `expiration` |
| `underlyingConid` | `underlying_conid` |
| `underlyingName`, `market`, `name` | parent `market_name` (+ synthesized `name`) |
| `exchange` | `exchange` |
| `timespecifierParam` | `time_specifier` |
| `underlyingSymbol`, `commodityCode` | parent `symbol` |
| `eventFixedPayout` | parent `payout` |

### `/contract/rules` → legacy micro schema (enrichment)

| Legacy field | Rules field |
| --- | --- |
| `longDescription` | `description` |
| `sourceAgency` | `source_agency` |
| `eventAuthorityURL` | `data_and_resolution_link` |
| `marketRulesLink` | `market_rules_link` |
| `priceIncrement` | `price_increment` (`"$0.01"` → `0.01`) |
| `eventFixedPayout` | `payout` (`"$1.00"` → `1.0`) |
| `timezone` | `exchange_timezone` (`US/Central`) |
| `lastTradeMillis` | `last_trade_time` (epoch s → ms) |
| `lastTradeDate`, `lastTradeTime` | derived from `last_trade_time` in the exchange timezone |
| `expectedResolutionTime` | `release_time` (epoch, exchange-local naive `datetime`) |
| `expectedPayoutTime` | `payout_time` (epoch, exchange-local naive `datetime`) |

The epoch→legacy-time mappings were validated against the archived legacy NYC
response (e.g. `payout_time 1764529200` == old `20251130130000` US/Central).

## Caching & rate limits (do not hammer IBKR)

`FxCRestClient` is deliberately conservative:

* Every response is stored in the shared Argus `DomainCache` (domain `IBKR`):
  * category tree — **15 min**
  * `/contract/market` — **5 min**
  * `/contract/rules` — **30 min**
* A minimum interval (**default 250 ms**) is enforced between *live* requests.
  Cache hits never touch the network.
* `get_all_markets` also memoizes in-process (`self._all_markets`).
* `contract/rules` is cached under the **market** conid (`cache_key`), so all
  expirations share one call.
* Net per market activation: **2 requests** (contracts + rules), then cached.

## Multi-expiration handling

A market such as `FF` returns several expirations with reused strike labels, so
one `FxContractBig` cannot hold them all. Consequences:

* `FxContractBig` gained an `expiration` attribute (int `YYYYMMDD`).
* `activate_market(conid, expiration=None)` selects one expiration; without an
  explicit value it picks the **nearest not-yet-expired** one.
* Resolved-market memory is keyed by `(conid, expiration)` instead of `conid`.
* New client command `get_market_expirations:<conid>` lists available expirations.

## Downstream compatibility

No in-repo consumers exist beyond `runtime.py` and the docs, so the contract is
the TCP command protocol.

**Unchanged**
* `get_all_markets` output: `{name, symbol, exchange, conid}` (4 keys).
* Market conids remain in the same `underlyingConid` space (FF = `658663572`).
* `activate_market` response string.
* All legacy `FxContractMicro` keys are always present in `get_active_market`
  JSON (unavailable values are `null`, never missing keys).

**Extended (additive)**
* `activate_market:<conid>[,<YYYYMMDD>]`.
* `get_market_expirations:<conid>`.
* `get_big_contract:<conid>,<YYYYMMDD>` — subscription-free, stateless REST fetch
  of a single Big contract (metadata only; `market_data` is `null`). The
  expiration is **required** so the request is idempotent across time.
* `FxContractBig` JSON gained `expiration`.

**Semantically changed**
* `market` / `name` values differ (now derived from `market_name`).

**Permanently unavailable** (no live EventTrader source)
* `popularityRank`
* `tradingHours` holidays (only `timezone` is recoverable)
* full ancestor `categories` list
* `currency`
* exact legacy `shortDescription` string

## Testing

All tests are offline against captured fixtures — running the suite issues
**zero** IBKR requests:

```bash
.venv/bin/python -m pytest tests/test_fxc_api.py -q
```

Fixtures live in `tests/fixtures/fxc/` and were captured once by
`tests/fixtures/fxc/capture_fixtures.py` (throttled; run only when the upstream
shape changes):

```bash
python tests/fixtures/fxc/capture_fixtures.py
```

Coverage includes: category-tree parsing/dedup, wire-shape stability, cache and
throttle behaviour, multi-expiration selection, YES/NO pairing, normalization,
rules enrichment (including timezone-correct timestamps), legacy `from_json`
regression, and `to_json` serialization.

A final live smoke test was run once and exercised only 2 requests:
`/contract/market` + `/contract/rules`.

## Files

| File | Change |
| --- | --- |
| `argus/ib/_fxc_api.py` | **new** — EventTrader REST client (cache + throttle + enrichment) |
| `argus/ib/_forcast_utils.py` | normalization, `from_ibkr_market`, `apply_contract_rules`, `to_json` import fix |
| `argus/ib/forecast.py` | use `FxCRestClient`; expiration-aware activation/memory; new command |
| `tests/test_fxc_api.py` | **new** — offline tests |
| `tests/fixtures/fxc/` | **new** — captured API fixtures + capture script |
| `docs/IB.md` | endpoint/command/compatibility section |

## References

* `docs/IB.md` → “FXCWss and FXCDispatcher”
* IBKR Campus — *Event Contracts in the Web API* / *CPAPI Endpoints – Event Contracts*
