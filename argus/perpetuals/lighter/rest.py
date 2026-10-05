import re
import time
from utils3.networking import Session
from argus.perpetuals.shared import ers as _ers
from argus.perpetuals.shared import account as _acct
from argus.perpetuals.lighter import _classes as _cls
from typing import Any, Callable, Dict, List, Optional
from argus.perpetuals.shared import BaseDispatcherCompatibleRest, BaseDispatcherCompatibleAccountRest

_ep = {
    'base': 'https://mainnet.zklighter.elliot.ai',
}

ALL_MARKETS = 255
"""Lighter's sentinel `market_id` meaning "every market" on account order/trade queries."""

MAX_PAGE = 100
"""Lighter's hard cap on `limit` for every cursor-paged account endpoint."""

_RO_TOKEN_RE = re.compile(r'^ro:(\d+):(single|all):(\d+):[0-9a-fA-F]+$')


class LighterRest(BaseDispatcherCompatibleRest, BaseDispatcherCompatibleAccountRest):
    """Client for the Lighter exchange REST API.

    Market data (metadata, prices, funding) is public. Account data is keyed by
    integer `account_index` and splits two ways:
      - `GET /api/v1/account` (balance + positions) is public: only `account_index` is needed.
      - orders, trades and funding payments are auth-gated: Lighter wants a token in
        the `authorization` header. This client takes a *read-only API token*
        (`ro:<account_index>:<single|all>:<expiry>:<hex>`, minted in the Lighter web
        UI or via `POST /api/v1/tokens_create`, valid up to 10 years) so no native
        signer is needed for reads. The short-lived signed tokens the Lighter SDK
        mints from an API-key private key are for order execution and are out of
        scope here.

    Both `account_index` and `auth_token` are optional so a market-data-only
    dispatcher keeps working; account methods raise `AccountNotConfiguredError`
    naming the missing piece instead of returning an empty account.
    """

    #: Upper bound on settlements walked by `get_funding_payments`, so an open-ended
    #: window cannot turn into an unbounded number of upstream requests. At hourly
    #: funding this is about three weeks of history.
    MAX_FUNDING_PAYMENTS = 500

    def __init__(self, base_url: str = _ep['base'], account_index: Optional[int] = None,
                 auth_token: Optional[str] = None):
        super().__init__()
        self.base_url = base_url
        self.session = Session()
        self.session.headers = {
            'Content-Type': 'application/json',
        }
        self.account_index = int(account_index) if account_index is not None else None
        self.auth_token = auth_token or None
        if self.auth_token is not None:
            match = _RO_TOKEN_RE.match(self.auth_token)
            if match is None:
                raise ValueError(
                    "LIGHTER_AUTH_TOKEN does not look like a Lighter read-only API token "
                    "('ro:<account_index>:<single|all>:<expiry_unix>:<hex>')"
                )
            token_account = int(match.group(1))
            if self.account_index is None:
                self.account_index = token_account
            elif match.group(2) == 'single' and token_account != self.account_index:
                raise ValueError(
                    f"LIGHTER_AUTH_TOKEN is scoped to account {token_account} but "
                    f"LIGHTER_ACCOUNT_INDEX is {self.account_index}"
                )
            if int(match.group(3)) <= int(time.time()):
                raise ValueError("LIGHTER_AUTH_TOKEN has expired; mint a new read-only token")
        # utils3.networking.Session always sends its own `headers`, so auth-gated
        # calls go through a second session carrying the token.
        self._auth_session = Session()
        self._auth_session.headers = {**self.session.headers, 'authorization': self.auth_token or ''}
        self._symbol_by_market_id: Dict[int, str] = {}

    def _get(self, path: str, params: Optional[dict] = None, auth: bool = False) -> dict:
        session = self._auth_session if auth else self.session
        response = session.get(url=f'{self.base_url}{path}', params=params)
        payload = response.json()
        # Lighter wraps every payload in {code, message, ...}; non-200 codes carry the
        # reason in `message` (e.g. auth failures) rather than an HTTP error.
        if isinstance(payload, dict) and payload.get('code') not in (None, 200):
            raise RuntimeError(f"Lighter API error {payload.get('code')} on {path}: {payload.get('message')}")
        return payload

    # --- markets -------------------------------------------------------------

    def get_markets(self) -> List[_cls.Perpetual]:
        """All perpetual markets' metadata + live data (mark price, index price, 24h stats, ...)."""
        response = self._get('/api/v1/orderBookDetails', params={'filter': 'perp'})
        markets = [_cls.Perpetual.from_dict(m) for m in response['order_book_details']]
        assert all(market.market.is_perp for market in markets), 'orderBookDetails?filter=perp returned a non-perp market'
        return markets

    # --- funding rates ---------------------------------------------------------

    def get_funding_rates(self) -> List[_cls.FundingRateEntry]:
        """Current funding rate for every market, on Lighter and the external CEXs
        (binance, bybit, hyperliquid) it benchmarks against."""
        response = self._get('/api/v1/funding-rates')
        return [_cls.FundingRateEntry.from_dict(e) for e in response['funding_rates']]

    def get_cross_exchange_fundings(self) -> List[_cls.CrossExchangeFunding]:
        """`get_funding_rates()`, grouped by market so each market's rates across
        exchanges can be compared side by side."""
        by_market: Dict[int, _cls.CrossExchangeFunding] = {}
        for entry in self.get_funding_rates():
            group = by_market.setdefault(
                entry.market_id, _cls.CrossExchangeFunding(market_id=entry.market_id, symbol=entry.symbol)
            )
            group.rates.append(entry)
        return list(by_market.values())

    def get_funding_history(
        self,
        market_id: int,
        start_timestamp: int,
        end_timestamp: Optional[int] = None,
        resolution: str = '1h',
        count_back: int = 0,
    ) -> List[_cls.FundingHistoryEntry]:
        """Historical funding for one market, in [start_timestamp, end_timestamp] (unix
        seconds). `end_timestamp` defaults to now. `resolution` is "1h" or "1d"; at most
        750 entries are returned per call. `count_back=0` returns everything in range."""
        params = {
            'market_id': market_id,
            'resolution': resolution,
            'start_timestamp': start_timestamp,
            'end_timestamp': end_timestamp if end_timestamp is not None else int(time.time()),
            'count_back': count_back,
        }
        response = self._get('/api/v1/fundings', params=params)
        return [_cls.FundingHistoryEntry.from_dict(market_id, e) for e in response['fundings']]

    # --- combined convenience ---------------------------------------------------

    def get_all_perpetuals(self) -> _cls.PerpetualsIndex:
        """All perpetual markets with Lighter's current funding rate attached,
        as one sortable/filterable index."""
        perpetuals = self.get_markets()
        lighter_rates = {e.market_id: e.rate for e in self.get_funding_rates() if e.exchange == 'lighter'}
        for perpetual in perpetuals:
            perpetual.funding_rate = lighter_rates.get(perpetual.market_id)
        return _cls.PerpetualsIndex(perpetuals)

    # --- account (venue-typed) ---------------------------------------------------

    def _require_account_index(self) -> int:
        if self.account_index is None:
            raise _ers.AccountNotConfiguredError(
                "No Lighter account configured: set LIGHTER_ACCOUNT_INDEX (or LIGHTER_AUTH_TOKEN, "
                "which embeds the account index)."
            )
        return self.account_index

    def _require_auth(self) -> None:
        if self.auth_token is None:
            raise _ers.AccountNotConfiguredError(
                "This Lighter account read needs a read-only API token: set LIGHTER_AUTH_TOKEN "
                "(mint one in the Lighter web UI or via POST /api/v1/tokens_create)."
            )

    def get_account(self, account_index: Optional[int] = None) -> _cls.AccountSummary:
        """Balance + positions for one account (public; no token needed). Positions
        for markets the account is flat in are excluded server-side (`active_only`)."""
        wanted = self._require_account_index() if account_index is None else int(account_index)
        response = self._get('/api/v1/account', params={'by': 'index', 'value': str(wanted), 'active_only': 'true'})
        accounts = response.get('accounts') or []
        if not accounts:
            raise RuntimeError(f"Lighter returned no account for index {wanted}")
        return _cls.AccountSummary.from_dict(accounts[0])

    def get_accounts_by_l1_address(self, l1_address: str) -> List[Dict[str, Any]]:
        """Master + sub-account rows for an L1 address (public). Handy for finding
        the `account_index` to configure; returned raw since it is a one-off lookup."""
        response = self._get('/api/v1/accountsByL1Address', params={'l1_address': l1_address})
        return list(response.get('sub_accounts') or [])

    def get_account_active_orders(self, market_id: int = ALL_MARKETS) -> List[_cls.LighterOrder]:
        """Resting orders (auth-gated). `market_id` defaults to every market."""
        self._require_auth()
        params = {'account_index': self._require_account_index(), 'market_id': market_id, 'market_type': 'perp'}
        response = self._get('/api/v1/accountActiveOrders', params=params, auth=True)
        return [_cls.LighterOrder.from_dict(o) for o in response.get('orders') or []]

    def get_account_inactive_orders(self, limit: int = 100, cursor: Optional[str] = None,
                                    market_id: int = ALL_MARKETS) -> tuple[List[_cls.LighterOrder], Optional[str]]:
        """One page (<= 100) of filled/cancelled orders, newest first (auth-gated).
        Returns (orders, next_cursor); Lighter keeps the last 1000 inactive orders."""
        self._require_auth()
        params = {'account_index': self._require_account_index(), 'limit': max(1, min(int(limit), MAX_PAGE)),
                  'market_id': market_id, 'market_type': 'perp'}
        if cursor:
            params['cursor'] = cursor
        response = self._get('/api/v1/accountInactiveOrders', params=params, auth=True)
        orders = [_cls.LighterOrder.from_dict(o) for o in response.get('orders') or []]
        return orders, response.get('next_cursor') or None

    def get_account_trades(self, limit: int = 100, cursor: Optional[str] = None,
                           market_id: int = ALL_MARKETS) -> tuple[List[_cls.LighterTrade], Optional[str]]:
        """One page (<= 100) of the account's trades, newest first (auth-gated).
        Returns (trades, next_cursor)."""
        self._require_auth()
        params = {'account_index': self._require_account_index(), 'sort_by': 'timestamp', 'sort_dir': 'desc',
                  'limit': max(1, min(int(limit), MAX_PAGE)), 'market_id': market_id, 'market_type': 'perp'}
        if cursor:
            params['cursor'] = cursor
        response = self._get('/api/v1/trades', params=params, auth=True)
        trades = [_cls.LighterTrade.from_dict(t) for t in response.get('trades') or []]
        return trades, response.get('next_cursor') or None

    def get_position_fundings(self, start_timestamp: Optional[int] = None, end_timestamp: Optional[int] = None,
                              limit: int = 100, cursor: Optional[str] = None) -> tuple[List[_cls.PositionFunding], Optional[str]]:
        """One page (<= 100) of funding settlements in [start, end] (unix **seconds**,
        like `/api/v1/fundings`), newest first (auth-gated). Returns (payments, next_cursor)."""
        self._require_auth()
        params: Dict[str, Any] = {'account_index': self._require_account_index(), 'limit': max(1, min(int(limit), MAX_PAGE))}
        if start_timestamp is not None:
            params['start_timestamp'] = int(start_timestamp)
        if end_timestamp is not None:
            params['end_timestamp'] = int(end_timestamp)
        if cursor:
            params['cursor'] = cursor
        response = self._get('/api/v1/positionFunding', params=params, auth=True)
        payments = [_cls.PositionFunding.from_dict(entry) for entry in response.get('position_fundings') or []]
        return payments, response.get('next_cursor') or None

    # --- account (homogenous; BaseDispatcherCompatibleAccountRest) ----------------

    def _symbol_for(self, market_id: int) -> str:
        """market_id -> symbol, from the market list (fetched once, refreshed on a miss
        so a newly listed market resolves without a restart)."""
        if market_id not in self._symbol_by_market_id:
            self._symbol_by_market_id = {m.market_id: m.name for m in self.get_markets()}
        return self._symbol_by_market_id.get(market_id, f"market:{market_id}")

    @staticmethod
    def _walk_cursor(fetch_page: Callable[[int, Optional[str]], tuple], max_count: int) -> list:
        """Follow `next_cursor` through a paged endpoint until `max_count` records
        are collected or the venue runs out. `fetch_page(limit, cursor) -> (items, next_cursor)`."""
        collected: list = []
        cursor = None
        while len(collected) < max_count:
            items, cursor = fetch_page(min(MAX_PAGE, max_count - len(collected)), cursor)
            collected.extend(items)
            if not items or not cursor:
                break
        return collected[:max_count]

    def account_identity(self) -> str:
        return str(self._require_account_index())

    @staticmethod
    def _reject_dex(dex: Optional[str]) -> None:
        """Lighter has a single ledger per `account_index` -- no HIP-3-style sub-venues --
        so any non-empty `dex` is rejected rather than silently answered from the only
        ledger there is. See BaseDispatcherCompatibleAccountRest for the parameter.

        Deliberately reuses InvalidCoinError ("you named something this venue does not
        have") because the shared error taxonomy has no scope-specific type yet. SDK
        clients matching on InvalidCoinError therefore see this alongside unknown
        symbols; add a dedicated error before that distinction starts to matter."""
        if dex:
            raise _ers.InvalidCoinError(
                f"Lighter has no sub-ledgers; 'dex' must be empty, got {dex!r}. "
                f"Use LIGHTER_ACCOUNT_INDEX to choose the account."
            )

    def get_account_balance(self, dex: str = "") -> _acct.AccountBalance:
        self._reject_dex(dex)
        return self.get_account().to_balance()

    def get_positions(self, dex: Optional[str] = None) -> List[_acct.Position]:
        self._reject_dex(dex)
        return [position.to_common() for position in self.get_account().open_positions]

    def get_open_orders(self, dex: Optional[str] = None) -> List[_acct.Order]:
        self._reject_dex(dex)
        orders = self.get_account_active_orders()
        orders.sort(key=lambda o: o.timestamp, reverse=True)
        return [o.to_common(self._symbol_for(o.market_index)) for o in orders]

    def get_order_status(self, order_id: str) -> Optional[_acct.Order]:
        """Lighter has no by-venue-id lookup, so this scans the resting orders and then
        the most recent page of inactive orders (100). Older orders are not found."""
        for order in self.get_account_active_orders():
            if order.order_id == order_id or str(order.order_index) == order_id:
                return order.to_common(self._symbol_for(order.market_index))
        inactive, _ = self.get_account_inactive_orders(limit=100)
        for order in inactive:
            if order.order_id == order_id or str(order.order_index) == order_id:
                return order.to_common(self._symbol_for(order.market_index))
        return None

    def get_recent_trades(self, max_count: int) -> List[_acct.Trade]:
        account_index = self._require_account_index()
        trades = self._walk_cursor(lambda limit, cursor: self.get_account_trades(limit, cursor), max_count)
        return [t.to_common(account_index, self._symbol_for(t.market_id)) for t in trades]

    def get_funding_payments(self, start_time_ms: int, end_time_ms: Optional[int] = None) -> List[_acct.FundingPayment]:
        """Walks the cursor to at most MAX_FUNDING_PAYMENTS settlements; see that constant."""
        end_s = int((end_time_ms if end_time_ms is not None else time.time() * 1000) // 1000)
        payments = self._walk_cursor(
            lambda limit, cursor: self.get_position_fundings(start_time_ms // 1000, end_s, limit, cursor),
            self.MAX_FUNDING_PAYMENTS,
        )
        payments.sort(key=lambda payment: payment.timestamp, reverse=True)
        return [payment.to_common(self._symbol_for(payment.market_id)) for payment in payments]


def _demo() -> None:
    """Manual smoke check: python -m argus.perpetuals.lighter.rest"""
    rest = LighterRest()

    index = rest.get_all_perpetuals()
    print(f'There are {len(index)} perpetual markets on Lighter ({len(index.excluding_inactive())} active).')

    for label, ranked in (('Top', index.highest_funding(5)), ('Bottom', index.lowest_funding(5))):
        print(f'\n{label} 5 by funding rate:')
        for perpetual in ranked:
            rate = _acct.str_or_none(perpetual.funding_rate)
            print(f'  {perpetual.name:<10} funding={rate!s:<14} mark={_acct.decimal_str(perpetual.mark_price)}')


if __name__ == '__main__':
    _demo()
