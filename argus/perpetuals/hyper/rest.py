import os
import time
from tqdm import tqdm
from utils3.networking import Session
from argus.perpetuals.hyper import _classes as _cls
from typing import Any, List, Optional, Tuple, Union
from argus.perpetuals.shared import account as _acct
from argus.cache_sys import DomainCache as _DomainCache, FastCache
from argus.perpetuals.shared import BaseDispatcherCompatibleRest, BaseDispatcherCompatibleAccountRest


_HL_CACHE = _DomainCache('hyperliquid', FastCache(cache_file="~/.argus/hyperliquid_cache.pkl"))


_ep = {
    'info': 'https://api.hyperliquid.xyz/info'
}

# An account's abstraction mode almost never changes and the dex list changes rarely, so both
# are cached briefly instead of costing an extra `info` call (weight 20) on every balance read.
_ACCOUNT_MODE_TTL_S = 300
_DEX_NAMES_TTL_S = 600
# Spacing between per-dex reads, so reading every dex does not burst the per-IP rate limit.
_DEX_READ_SPACING_S = 0.2


class HyperLiquidRest(BaseDispatcherCompatibleRest, BaseDispatcherCompatibleAccountRest):
    """
    Hyperliquid `info` REST client.

    Market-data methods are keyed by coin/dex and take no account context. The
    account methods are keyed by `wallet_address`, which Hyperliquid expects to be
    the account's *master* address -- querying with an API/agent wallet address
    returns an empty account. These are unsigned public reads, so `private_key` is
    stored for the upcoming order-execution work but never used here.

    Account methods come in two flavours:
      - venue-typed (`get_clearinghouse_state`, `get_frontend_open_orders`,
        `get_order_status_detail`, `get_user_fills*`, `get_user_funding`,
        `get_user_fees`, `get_user_rate_limit`): one-to-one with the info endpoint.
      - homogenous (`get_account_balance`, `get_positions`, `get_open_orders`,
        `get_order_status`, `get_recent_trades`, `get_funding_payments`): the
        BaseDispatcherCompatibleAccountRest contract, built on the venue-typed ones.
        They take an optional `dex` because Hyperliquid keeps a separate clearinghouse
        (balance, positions, orders) per HIP-3 dex; fills and funding are account-wide.
    """

    def __init__(self, wallet_address: str, private_key: str):
        super().__init__()
        self.wallet_address = wallet_address
        self.private_key = private_key
        self.session = Session()
        self.session.headers = {
            'Content-Type': 'application/json',
        }
        self._account_mode_cache: Optional[Tuple[float, _cls.AccountMode]] = None
        self._dex_names_cache: Optional[Tuple[float, List[str]]] = None

    def _post(self, body: dict, allow_null: bool = False):
        response = self.session.post(url=_ep['info'], json=body).json()
        if response is None:
            # A bare `null` body (HTTP 200) usually means the request was rejected for being
            # rate-limited (each `info` call weighs 20 against the 1200/minute per-IP budget),
            # so raise by default rather than letting callers hit an obscure unpack/iteration
            # TypeError. Some endpoints use `null` as a legitimate "no data" response though
            # (e.g. `perpAnnotation` for default-dex coins like BTC); those callers opt in with
            # `allow_null=True` and handle the `None` result.
            if allow_null:
                return None
            raise RuntimeError(
                "HyperLiquid API returned no data for request type='{}' (dex={!r}); "
                "this usually means the request was rate-limited.".format(body.get('type'), body.get('dex'))
            )
        return response

    # --- dexes -----------------------------------------------------------

    def get_dexs(self) -> List[_cls.PerpDexConfig]:
        """All builder-deployed (HIP-3) perp dexes. Does not include the default dex,
        which is always represented by dex="" and has no PerpDexConfig of its own."""
        response: list = self._post({'type': 'perpDexs'})
        return [_cls.PerpDexConfig.from_dict(dex) for dex in response if dex is not None]

    # --- universe / metadata ----------------------------------------------

    def get_meta(self, dex: str = "") -> _cls.UniverseConfig:
        """Perpetuals metadata (universe + margin tables) for a single dex, without market data."""
        body = {'type': 'meta', 'dex': dex}
        return _cls.UniverseConfig.from_dict(self._post(body))

    def get_perpetuals_for_dex(self, dex: str = "") -> _cls.PerpDexSnapshot:
        """Universe + live market data (funding, mark price, open interest, ...) for one dex."""
        body = {'type': 'metaAndAssetCtxs', 'dex': dex}
        response: list = self._post(body)
        return _cls.PerpDexSnapshot.from_response(dex, response)


    def get_all_perpetuals(self) -> _cls.PerpetualsIndex:
        """All perpetuals across the default dex and every HIP-3 dex, as one sortable/filterable index."""
        dex_names = [""] + [dex.name for dex in self.get_dexs()]
        snapshots = []
        for i, dex_name in enumerate(tqdm(dex_names, desc='Fetching perpetuals for each dex')):
            if i > 0:
                # Space out per-dex requests so a full refresh doesn't burst all ~11 `metaAndAssetCtxs`
                # calls (weight 20 each) back-to-back, which risks tripping HyperLiquid's per-IP rate limit.
                time.sleep(0.2)
            snapshots.append(self.get_perpetuals_for_dex(dex_name))
        return _cls.PerpetualsIndex.from_snapshots(snapshots)

    # --- funding rates -----------------------------------------------------

    def get_funding_history(
        self, coin: str, start_time_ms: int, end_time_ms: Optional[int] = None
    ) -> List[_cls.FundingHistoryEntry]:
        body = {'type': 'fundingHistory', 'coin': coin, 'startTime': start_time_ms}
        if end_time_ms is not None:
            body['endTime'] = end_time_ms
        response: list = self._post(body)
        return [_cls.FundingHistoryEntry.from_dict(entry) for entry in response]

    def get_predicted_fundings(self) -> List[_cls.PredictedFunding]:
        """Predicted next funding rates for each coin, across Hyperliquid and external CEXs.
        Only supported for the default (first) perp dex."""
        response: list = self._post({'type': 'predictedFundings'})
        return [_cls.PredictedFunding.from_pair(pair) for pair in response]

    # --- misc dex / coin info ----------------------------------------------

    def get_perps_at_open_interest_cap(self, dex: str = "") -> List[str]:
        return self._post({'type': 'perpsAtOpenInterestCap', 'dex': dex})

    def get_perp_dex_limits(self, dex: str) -> _cls.PerpDexLimits:
        """`dex` must be a non-empty, builder-deployed (HIP-3) dex name."""
        return _cls.PerpDexLimits.from_dict(self._post({'type': 'perpDexLimits', 'dex': dex}))

    def get_perp_dex_status(self, dex: str = "") -> _cls.PerpDexStatus:
        return _cls.PerpDexStatus.from_dict(self._post({'type': 'perpDexStatus', 'dex': dex}))

    def get_perp_deploy_auction_status(self) -> _cls.PerpDeployAuctionStatus:
        return _cls.PerpDeployAuctionStatus.from_dict(self._post({'type': 'perpDeployAuctionStatus'}))

    def get_perp_annotation(self, coin: str) -> Optional[_cls.PerpAnnotation]:
        """Returns None for coins with no annotation (e.g. most default-dex coins)."""
        response = self._post({'type': 'perpAnnotation', 'coin': coin}, allow_null=True)
        return _cls.PerpAnnotation.from_dict(response)

    def get_perp_categories(self) -> List[_cls.PerpCategory]:
        response: list = self._post({'type': 'perpCategories'})
        return [_cls.PerpCategory.from_pair(pair) for pair in response]

    def get_perp_concise_annotations(self) -> List[_cls.PerpConciseAnnotation]:
        response: list = self._post({'type': 'perpConciseAnnotations'})
        return [_cls.PerpConciseAnnotation.from_pair(pair) for pair in response]

    # --- account (read-only, keyed by wallet_address) -----------------------

    def _user_body(self, request_type: str, **extra: Any) -> dict:
        body = {'type': request_type, 'user': self.wallet_address}
        body.update({k: v for k, v in extra.items() if v is not None})
        return body

    def get_clearinghouse_state(self, dex: str = "") -> _cls.ClearinghouseState:
        """Margin summary, withdrawable balance and open positions for one dex.
        Hyperliquid runs a separate clearinghouse per dex, so a HIP-3 dex's account
        state is only visible by passing its name (default dex is `dex=""`)."""
        body = self._user_body('clearinghouseState', dex=dex or None)
        return _cls.ClearinghouseState.from_dict(dex, self._post(body))

    def get_spot_clearinghouse_state(self) -> _cls.SpotClearinghouseState:
        """Spot balances. For unified / portfolio-margin accounts this is the collateral pool
        backing perps too (the perps clearinghouse reads 0 for them)."""
        return _cls.SpotClearinghouseState.from_dict(self._post(self._user_body('spotClearinghouseState')))

    def get_account_mode(self) -> _cls.AccountMode:
        """How the account keeps its books (`userAbstraction`), cached for a few minutes.
        Raises `UnsupportedAccountModeError` for a mode this client does not know."""
        now = time.monotonic()
        if self._account_mode_cache is not None and now - self._account_mode_cache[0] < _ACCOUNT_MODE_TTL_S:
            return self._account_mode_cache[1]
        mode = _cls.AccountMode.from_wire(self._post(self._user_body('userAbstraction')))
        self._account_mode_cache = (now, mode)
        return mode

    def _dex_names(self) -> List[str]:
        """Every dex to read for an account-wide view: the primary ("") then each HIP-3 dex. Cached."""
        now = time.monotonic()
        if self._dex_names_cache is None or now - self._dex_names_cache[0] >= _DEX_NAMES_TTL_S:
            self._dex_names_cache = (now, [""] + [dex.name for dex in self.get_dexs()])
        return self._dex_names_cache[1]

    def _clearinghouse_states(self, dex: Optional[str]) -> List[_cls.ClearinghouseState]:
        """One dex's state, or (`dex=None`) every dex's, spaced out to respect the rate limit."""
        if dex is not None:
            return [self.get_clearinghouse_state(dex)]
        states = []
        for i, name in enumerate(self._dex_names()):
            if i > 0:
                time.sleep(_DEX_READ_SPACING_S)
            states.append(self.get_clearinghouse_state(name))
        return states

    def get_frontend_open_orders(self, dex: str = "") -> List[_cls.OpenOrder]:
        """All resting orders on one dex, in the richer `frontendOpenOrders` shape
        (order type, trigger info, reduce-only, cloid, ...)."""
        body = self._user_body('frontendOpenOrders', dex=dex or None)
        response: list = self._post(body)
        return [_cls.OpenOrder.from_dict(order) for order in response]

    def get_order_status_detail(self, oid: Union[int, str]) -> _cls.OrderStatus:
        """Look up one order by numeric `oid` or by client order id (`cloid`, a
        0x-prefixed 16-byte hex string). Never raises for an unknown id: the returned
        `OrderStatus.found` is False instead."""
        body = self._user_body('orderStatus', oid=oid)
        return _cls.OrderStatus.from_dict(self._post(body))

    def get_user_fills(self, aggregate_by_time: bool = False) -> List[_cls.UserFill]:
        """The account's most recent fills (Hyperliquid caps this at 2000). With
        `aggregate_by_time`, partial fills of one order in the same block are merged."""
        body = self._user_body('userFills', aggregateByTime=aggregate_by_time or None)
        response: list = self._post(body)
        return [_cls.UserFill.from_dict(fill) for fill in response]

    def get_user_fills_by_time(
        self, start_time_ms: int, end_time_ms: Optional[int] = None, aggregate_by_time: bool = False
    ) -> List[_cls.UserFill]:
        """Fills in [start_time_ms, end_time_ms] (unix ms; end defaults to now).
        Hyperliquid returns at most 2000 per call, oldest first."""
        body = self._user_body(
            'userFillsByTime',
            startTime=start_time_ms,
            endTime=end_time_ms,
            aggregateByTime=aggregate_by_time or None,
        )
        response: list = self._post(body)
        return [_cls.UserFill.from_dict(fill) for fill in response]

    def get_user_funding(
        self, start_time_ms: int, end_time_ms: Optional[int] = None
    ) -> List[_cls.UserFundingPayment]:
        """Funding payments applied to the account in [start_time_ms, end_time_ms]
        (unix ms; end defaults to now). At most 500 per call."""
        body = self._user_body('userFunding', startTime=start_time_ms, endTime=end_time_ms)
        response: list = self._post(body)
        return [_cls.UserFundingPayment.from_dict(entry) for entry in response]

    def get_user_fees(self) -> _cls.UserFees:
        """Current maker/taker fee rates and rolling volume for the account."""
        return _cls.UserFees.from_dict(self._post(self._user_body('userFees')))

    def get_user_rate_limit(self) -> _cls.UserRateLimit:
        """Address-based rate-limit budget (used/cap requests, cumulative volume)."""
        return _cls.UserRateLimit.from_dict(self._post(self._user_body('userRateLimit')))

    # --- account (homogenous; BaseDispatcherCompatibleAccountRest) ------------

    def account_identity(self) -> str:
        return self.wallet_address

    def get_account_balance(self, dex: str = "") -> _acct.AccountBalance:
        """One answer whatever the account mode. Unified / portfolio-margin accounts keep one
        collateral pool in the spot ledger (their perps clearinghouse reads 0), so it is derived
        from spot plus every perp dex and `dex` is ignored; other accounts read the `dex` ledger."""
        mode = self.get_account_mode()
        if mode.uses_spot_collateral:
            spot = self.get_spot_clearinghouse_state()
            return _cls.UnifiedAccountState(mode, spot, self._clearinghouse_states(None)).to_balance()
        return self.get_clearinghouse_state(dex).to_balance(account_mode=mode.value)

    def get_positions(self, dex: Optional[str] = None) -> List[_acct.Position]:
        """Non-flat positions on one dex, or (`dex=None`) on every dex, primary first."""
        return [
            p.to_common(dex=state.dex)
            for state in self._clearinghouse_states(dex)
            for p in state.positions
            if p.szi != 0
        ]

    def get_open_orders(self, dex: Optional[str] = None) -> List[_acct.Order]:
        """Resting orders on one dex, or (`dex=None`) on every dex, newest first."""
        names = self._dex_names() if dex is None else [dex]
        orders = []
        for i, name in enumerate(names):
            if i > 0:
                time.sleep(_DEX_READ_SPACING_S)
            orders.extend(o.to_common(status="open", dex=name) for o in self.get_frontend_open_orders(name))
        orders.sort(key=lambda o: o.timestamp_ms, reverse=True)
        return orders

    def get_order_status(self, order_id: str) -> Optional[_acct.Order]:
        """Numeric ids are Hyperliquid oids; anything else is treated as a cloid
        (0x-prefixed 16-byte hex), which `orderStatus` also accepts."""
        oid: Union[int, str] = int(order_id) if order_id.isdigit() else order_id
        return self.get_order_status_detail(oid).to_common()

    def get_recent_trades(self, max_count: int) -> List[_acct.Trade]:
        """`userFills` already caps at the 2000 most recent; sort newest first and trim."""
        fills = self.get_user_fills()
        fills.sort(key=lambda f: f.time, reverse=True)
        return [f.to_common() for f in fills[:max_count]]

    def get_funding_payments(self, start_time_ms: int, end_time_ms: Optional[int] = None) -> List[_acct.FundingPayment]:
        payments = self.get_user_funding(start_time_ms, end_time_ms)
        payments.sort(key=lambda p: p.time, reverse=True)
        return [p.to_common() for p in payments]


def _demo() -> None:
    """Manual smoke check: python -m argus.perpetuals.hyper.rest"""
    from argus._argus_utils import load_dotenv
    load_dotenv()
    rest = HyperLiquidRest(wallet_address=os.environ['HYPERLIQUID_WALLET_ADDRESS'],
                           private_key=os.environ['HYPERLIQUID_PRIVATE_KEY'])

    index = rest.get_all_perpetuals()
    print('There are', len(index), 'perpetuals available across', len({p.dex for p in index}), 'dex(es).')

    for label, ranked in (('Top', index.highest_funding(5)), ('Bottom', index.lowest_funding(5))):
        print(f'\n{label} 5 by funding rate:')
        for perpetual in ranked:
            rate = _acct.decimal_str(perpetual.funding_rate)
            print(f'  {perpetual.dex or "hyperliquid":<12} {perpetual.name:<12} '
                  f'funding={rate:<14} mark={_acct.decimal_str(perpetual.mark_price)}')


if __name__ == '__main__':
    _demo()
