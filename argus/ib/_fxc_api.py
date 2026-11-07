"""
REST client for Interactive Brokers' ForecastEx / Event Contract "EventTrader" API.

IBKR decommissioned the old ``api.ibkr.com/v1/api/trsrv/event/*`` endpoints
(they now return HTTP 404).  The EventTrader web app instead talks to the
``tws.proxy/forecasttrader/*`` service, which is what this module wraps:

    GET /forecasttrader/category/tree               -> full category/market tree
    GET /forecasttrader/contract/market?underlyingConid=<conid>&exchange=FORECASTX
                                                    -> all micro contracts for one market
    GET /forecasttrader/contract/rules?conid=<outcome>
                                                    -> market-level rules/metadata
                                                       (one call restores the legacy
                                                       descriptive/resolution fields)

Design goals:
    * **Do not hammer IBKR.**  Every response is cached in the shared Argus
      ``DomainCache`` and a minimum interval is enforced between live requests.
    * **Offline testability.**  The cache can be disabled/replaced by tests so
      fixture data is used verbatim.
"""
import time
import threading
from argus.cache_sys import NotKey
from argus.ib._ib_utils import IBError, IB_Cache
from argus.ib._forcast_utils import AbstractMarket, FxContractBig


# Live EventTrader REST gateway.  The websocket uses the .ie host but the REST
# service is reachable (and stable) on the .com host.
FXC_REST_BASE = "https://www.interactivebrokers.com/tws.proxy/forecasttrader"
# NOTE: the ForecastEx exchange routing code is FORECASTX (without the final E).
FXC_EXCHANGE = "FORECASTX"


class FxCRestError(IBError):
    """Raised when an EventTrader REST call fails."""


class FxCRestClient:
    """Thin, cached, rate-limited wrapper around the EventTrader REST API."""

    def __init__(
        self,
        session,
        base_url: str = FXC_REST_BASE,
        min_interval: float = 0.25,
        cache=IB_Cache,
        category_ttl: int = 15 * 60,
        market_ttl: int = 5 * 60,
        rules_ttl: int = 30 * 60,
    ):
        """
        Args:
            session: an authenticated ``requests``-like session (the IBKR
                networker session).  Only ``session.get`` is used.
            base_url: EventTrader REST base URL.
            min_interval: minimum seconds between two *live* HTTP requests.
                Cached responses do not count.  Set to 0 to disable throttling.
            cache: an object exposing ``get(key)`` / ``set(key, value, expiration=...)``
                and raising ``argus.cache_sys.NotKey`` on a miss.  Pass ``None`` to
                disable caching entirely (used by tests).
            category_ttl: cache lifetime in seconds for the category tree.
            market_ttl: cache lifetime in seconds for per-market contract payloads.
            rules_ttl: cache lifetime in seconds for per-market rules metadata.
        """
        self.session = session
        self.base_url = base_url.rstrip('/')
        self.min_interval = min_interval
        self.cache = cache
        self.category_ttl = category_ttl
        self.market_ttl = market_ttl
        self.rules_ttl = rules_ttl

        self._last_request_ts = 0.0
        self._throttle_lock = threading.Lock()

    ############################
    # Low-level plumbing
    ############################
    def _throttle(self):
        if self.min_interval <= 0:
            return
        with self._throttle_lock:
            now = time.monotonic()
            wait = self.min_interval - (now - self._last_request_ts)
            if wait > 0:
                time.sleep(wait)
            self._last_request_ts = time.monotonic()

    def _cached(self, key: str, ttl: int, producer):
        if self.cache is not None:
            try:
                return self.cache.get(key)
            except NotKey:
                pass
            except Exception:
                # A broken cache must never break a live call; treat as a miss.
                pass
        value = producer()
        if self.cache is not None:
            try:
                self.cache.set(key, value, expiration=ttl)
            except Exception:
                # A cache failure must never break a live call.
                pass
        return value

    def _get_json(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        self._throttle()
        response = self.session.get(url, params=params)
        try:
            response.raise_for_status()
        except Exception as exc:
            raise FxCRestError(f"EventTrader request failed for {url}: {exc}") from exc
        try:
            return response.json()
        except ValueError as exc:
            raise FxCRestError(
                f"EventTrader returned non-JSON for {url}: {response.text[:200]!r}"
            ) from exc

    ############################
    # Public API
    ############################
    def category_tree(self) -> dict:
        """Return the raw category tree payload (cached)."""
        return self._cached(
            'fxc.category_tree',
            self.category_ttl,
            lambda: self._get_json('/category/tree'),
        )

    def markets(self) -> list[AbstractMarket]:
        """Return every tradable market in the category tree as ``AbstractMarket``.

        Results are deduplicated by conid.  The wire shape of each market is kept
        identical to the legacy implementation: ``name``, ``symbol``, ``exchange``
        and ``conid`` only.
        """
        payload = self.category_tree()
        categories = payload.get('categories') or {}
        markets: list[AbstractMarket] = []
        seen: set[int] = set()
        for category in categories.values():
            for raw in category.get('markets') or []:
                try:
                    market = AbstractMarket.from_dict(raw)
                except (ValueError, KeyError, TypeError):
                    continue
                if market.conid in seen:
                    continue
                seen.add(market.conid)
                markets.append(market)
        return markets

    def market_payload(self, underlying_conid: int, exchange: str = FXC_EXCHANGE) -> dict:
        """Return the raw ``contract/market`` payload for a market (cached)."""
        key = f"fxc.market.{underlying_conid}.{exchange}"
        params = {'underlyingConid': underlying_conid, 'exchange': exchange}
        return self._cached(
            key,
            self.market_ttl,
            lambda: self._get_json('/contract/market', params=params),
        )

    def contract_rules(self, conid: int, cache_key=None) -> dict:
        """Return market-level rules metadata for a contract (cached).

        ``cache_key`` lets callers key the cache by the *market* conid so rules
        are shared across every expiration of the same market (rules are
        market-level, not per-outcome).
        """
        key = f"fxc.rules.{cache_key if cache_key is not None else conid}"
        return self._cached(
            key,
            self.rules_ttl,
            lambda: self._get_json('/contract/rules', params={'conid': conid}),
        )

    def expirations(self, underlying_conid: int, exchange: str = FXC_EXCHANGE) -> list[str]:
        """Return the sorted expirations (``YYYYMMDD``) available for a market."""
        payload = self.market_payload(underlying_conid, exchange=exchange)
        return sorted({
            str(contract['expiration'])
            for contract in payload.get('contracts') or []
            if contract.get('expiration') is not None
        })

    def big_contract(
        self,
        underlying_conid: int,
        expiration: str | None = None,
        exchange: str = FXC_EXCHANGE,
        enrich: bool = True,
    ) -> FxContractBig:
        """Build a single-expiration :class:`FxContractBig` for a market.

        ``expiration`` defaults to the nearest not-yet-expired expiration.
        When ``enrich`` is true (default) a single cached ``contract/rules``
        request per market restores the legacy descriptive/resolution fields.
        Enrichment is best-effort and never blocks activation.
        """
        payload = self.market_payload(underlying_conid, exchange=exchange)
        big = FxContractBig.from_ibkr_market(payload, expiration=expiration)

        if enrich and big.all_conids:
            try:
                rules = self.contract_rules(big.all_conids[0], cache_key=underlying_conid)
                big.apply_contract_rules(rules)
            except Exception:
                pass

        return big
