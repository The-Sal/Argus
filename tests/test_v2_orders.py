"""
Offline tests: build (never post) signed orders through PolyRestAPI.build_order for a
v1/CTF token id and a Protocol V2 position id. Uses a throwaway random key and no network;
tick size / neg-risk are stubbed through warm_clob_caches_for_token.
"""
import pytest
from eth_account import Account
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.order_utils.exchange_order_builder_v2 import ExchangeOrderBuilderV2

from argus.polymarket_direct import PolymarketEvent
from argus.polymarket_direct.rest import PolyRestAPI

V2_YES = "495099235152725588201991226912420825532590817323357329480139627250245959680"
V1_ID = "54533043819946592547517511176940999955633860128497669742211153063842200957669"

EXCHANGE_V2 = "0xE111180000d2663C0091e4f400237545B87B996B"
NEG_RISK_V2 = "0xe2222d279d744050d28e00520010520000310F59"
EXCHANGE_V3 = "0xe3333700cA9d93003F00f0F71f8515005F6c00Aa"


@pytest.fixture()
def api(monkeypatch):
    key = Account.create().key.hex()  # throwaway, never a real key
    clob = ClobClient("https://clob.invalid", chain_id=137, key=key)
    clob._ClobClient__cached_version = 2  # no GET /version
    rest = object.__new__(PolyRestAPI)  # skip __init__ (network / wireproxy)
    rest.clob = clob

    seen = []
    orig = ExchangeOrderBuilderV2.build_signed_order

    def spy(self, order_data):
        seen.append((self.contract_address, self.domain_version))
        return orig(self, order_data)

    monkeypatch.setattr(ExchangeOrderBuilderV2, "build_signed_order", spy)
    rest.seen = seen
    return rest


def _build(api, token_id, tick="0.01", neg_risk=False, tick_arg=None, price=0.5):
    api.warm_clob_caches_for_token(token_id, tick, neg_risk)
    return api.build_order(
        token_id, PolymarketEvent(negRisk=neg_risk), price, 10.0, "buy", tick_size=tick_arg
    )


def test_v1_id_uses_exchange_v2_domain_2(api):
    order = _build(api, V1_ID)
    assert api.seen == [(EXCHANGE_V2, "2")]
    assert order.tokenId == V1_ID


def test_v1_neg_risk_uses_neg_risk_exchange(api):
    order = _build(api, V1_ID, neg_risk=True)
    assert api.seen == [(NEG_RISK_V2, "2")]
    assert order.tokenId == V1_ID


def test_v2_id_uses_exchange_v3_domain_3(api):
    order = _build(api, V2_YES)
    assert api.seen == [(EXCHANGE_V3, "3")]
    assert order.tokenId == V2_YES


def test_v2_ignores_neg_risk_flag(api):
    order = _build(api, V2_YES, neg_risk=True)
    assert api.seen == [(EXCHANGE_V3, "3")]
    assert order.tokenId == V2_YES
    # neg-risk cache must not be populated for v2 ids
    assert V2_YES not in api.clob._ClobClient__neg_risk


@pytest.mark.parametrize("tick", ["0.005", "0.0025"])
def test_new_tick_sizes(api, tick):
    order = _build(api, V2_YES, tick=tick, price=0.5)
    assert order.tokenId == V2_YES
    assert api.seen[-1] == (EXCHANGE_V3, "3")


@pytest.mark.parametrize("given,expected", [
    (0.005, "0.005"), (0.0025, "0.0025"), (0.01, "0.01"), ("0.001", "0.001"), (0.0001, "0.0001"),
])
def test_tick_normalization(given, expected):
    assert PolyRestAPI._normalize_tick_size(given) == expected


def test_tick_normalization_rejects_unknown():
    with pytest.raises(ValueError):
        PolyRestAPI._normalize_tick_size("0.02")


def test_float_tick_arg_accepted_by_build(api):
    order = _build(api, V2_YES, tick="0.005", tick_arg=0.005)
    assert order.tokenId == V2_YES


class _FakeClob:
    def __init__(self):
        self.params = []

    def update_balance_allowance(self, params):
        self.params.append(params)

    def get_balance_allowance(self, params):
        return {"balance": "2500000"}


@pytest.mark.parametrize("token,asset_type", [(V2_YES, "CONDITIONAL-V2"), (V1_ID, "CONDITIONAL")])
def test_balance_asset_type(token, asset_type):
    rest = object.__new__(PolyRestAPI)
    rest.clob = _FakeClob()
    rest._div = 10 ** 6
    bal = rest.get_token_balance(token)
    assert bal == 2.5 and isinstance(bal, float)
    assert str(rest.clob.params[0].asset_type) == asset_type
    assert rest.clob.params[0].token_id == token
