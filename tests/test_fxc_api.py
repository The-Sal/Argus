"""
Offline tests for the ForecastEx / EventTrader REST migration.

All HTTP is mocked with the JSON fixtures captured by
``tests/fixtures/fxc/capture_fixtures.py`` so these tests never touch IBKR.
"""
import json
import os
import time

import pytest

from argus.cache_sys import NotKey
from argus.ib._fxc_api import FXC_EXCHANGE, FxCRestClient, FxCRestError
from argus.ib._forcast_utils import (
    AbstractMarket,
    AbstractionError,
    FxContractBig,
    FxContractMini,
    FxContractMicro,
    normalize_ibkr_market_contract,
    select_default_expiration,
)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'fxc')


def load(name: str):
    with open(os.path.join(FIXTURES, name)) as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, payload, status=200, text=None):
        self._payload = payload
        self.status_code = status
        self.text = text if text is not None else json.dumps(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    """Routes ``GET`` by substring match and records every call."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        for fragment, response in self.routes.items():
            if fragment in url:
                return FakeResponse(response)
        raise AssertionError(f"Unexpected request: {url} params={params}")


class MemoryCache:
    def __init__(self):
        self.store = {}

    def get(self, key):
        if key not in self.store:
            raise NotKey(key)
        return self.store[key]

    def set(self, key, value, expiration=None):
        self.store[key] = value


def make_client(routes, cache=None, min_interval=0.0):
    return FxCRestClient(FakeSession(routes), min_interval=min_interval, cache=cache)


# --------------------------------------------------------------------------- #
# Category tree -> markets
# --------------------------------------------------------------------------- #
def test_markets_parsed_and_deduplicated():
    tree = load('category_tree.json')
    client = make_client({'/category/tree': tree})
    markets = client.markets()

    assert len(markets) == 895
    assert all(isinstance(m, AbstractMarket) for m in markets)
    assert len({m.conid for m in markets}) == len(markets)

    by_symbol = {m.symbol: m for m in markets}
    assert by_symbol['FF'].conid == 658663572
    assert by_symbol['FF'].name == 'US Fed Funds Target Rate'
    assert by_symbol['FF'].exchange == 'FORECASTX'


def test_market_wire_shape_is_unchanged():
    """Downstream clients parse ``get_all_markets`` output; keep the 4 legacy keys."""
    tree = load('category_tree.json')
    client = make_client({'/category/tree': tree})
    market = next(m for m in client.markets() if m.symbol == 'FF')
    assert set(market.__dict__) == {'name', 'symbol', 'exchange', 'conid'}


# --------------------------------------------------------------------------- #
# Caching + throttling (do not hammer IBKR)
# --------------------------------------------------------------------------- #
def test_cache_prevents_repeat_requests():
    session = FakeSession({'/category/tree': load('category_tree.json')})
    client = FxCRestClient(session, min_interval=0.0, cache=MemoryCache())

    first = client.markets()
    second = client.markets()

    assert [m.conid for m in first] == [m.conid for m in second]
    assert len(session.calls) == 1


def test_market_payload_is_cached_per_conid():
    session = FakeSession({
        '/contract/market': load('market_FF.json'),
    })
    client = FxCRestClient(session, min_interval=0.0, cache=MemoryCache())

    a = client.market_payload(658663572)
    b = client.market_payload(658663572)
    c = client.market_payload(751047561)

    assert a == b == c  # same fake payload regardless
    assert len(session.calls) == 2  # once per distinct conid


def test_throttle_enforced_between_live_requests():
    session = FakeSession({
        '/category/tree': load('category_tree.json'),
        '/contract/market': load('market_FF.json'),
    })
    client = FxCRestClient(session, min_interval=0.2, cache=None)

    start = time.monotonic()
    client.category_tree()
    client.market_payload(658663572)
    elapsed = time.monotonic() - start

    assert elapsed >= 0.2
    assert len(session.calls) == 2


def test_no_cache_object_is_supported():
    client = make_client({'/category/tree': load('category_tree.json')}, cache=None)
    assert len(client.markets()) == 895


# --------------------------------------------------------------------------- #
# Expirations
# --------------------------------------------------------------------------- #
def test_expirations_sorted():
    client = make_client({'/contract/market': load('market_FF.json')})
    assert client.expirations(658663572) == ['20261209', '20270127']


def test_select_default_expiration_prefers_earliest_future():
    from datetime import date

    contracts = [{'expiration': '20260101'}, {'expiration': '20270101'}]
    assert select_default_expiration(contracts, today=date(2026, 6, 1)) == '20270101'
    # all in the past -> fall back to the earliest
    assert select_default_expiration(contracts, today=date(2099, 1, 1)) == '20260101'


def test_select_default_expiration_requires_expirations():
    with pytest.raises(AbstractionError):
        select_default_expiration([{'conid': 1}])


# --------------------------------------------------------------------------- #
# Big contract construction
# --------------------------------------------------------------------------- #
def test_big_contract_single_expiration():
    payload = load('market_FF.json')
    big = FxContractBig.from_ibkr_market(payload, expiration='20270127')

    assert big.conid == 658663572
    assert big.expiration == 20270127
    assert big.underlyingName == 'US Fed Funds Target Rate'
    assert big.total_outcomes == 16
    assert len(big.all_conids) == 32

    # labels are unique inside one expiration and every mini is a proper YES/NO pair
    assert len(big.strikes_labels) == len(set(big.strikes_labels))
    for mini in big.mini_contracts:
        assert isinstance(mini, FxContractMini)
        assert mini.micro_yes.putOrCall == 'C'
        assert mini.micro_no.putOrCall == 'P'
        assert mini.micro_yes.strikeLabel == mini.micro_no.strikeLabel


def test_big_contract_default_picks_nearest_expiration():
    payload = load('market_FF.json')
    big = FxContractBig.from_ibkr_market(payload)
    assert big.expiration == 20261209


def test_legacy_from_json_still_works():
    """The old camelCase micro-contract schema must keep parsing."""
    legacy = [
        {'conid': 1, 'putOrCall': 'C', 'strikeLabel': 'Alice', 'underlyingConid': 99,
         'underlyingName': 'Election', 'strike': 1.0, 'expiration': '20251129'},
        {'conid': 2, 'putOrCall': 'P', 'strikeLabel': 'Alice', 'underlyingConid': 99,
         'underlyingName': 'Election', 'strike': 1.0, 'expiration': '20251129'},
    ]
    big = FxContractBig.from_json(legacy)
    assert big.total_outcomes == 1
    assert big.conids_by_outcome() == {'Alice': (1, 2)}
    assert big.expiration == 20251129
    assert big.conid == 99


def test_apply_contract_rules_restores_legacy_fields():
    from datetime import datetime

    big = FxContractBig.from_ibkr_market(load('market_FF.json'), expiration='20270127')
    big.apply_contract_rules(load('rules_FF.json'))
    micro = big.mini_contracts[0].micro_yes

    assert micro.longDescription.startswith('The federal funds rate')
    assert micro.sourceAgency == 'U.S. Federal Reserve'
    assert micro.eventAuthorityURL == 'https://www.federalreserve.gov/monetarypolicy/openmarket.htm'
    assert micro.marketRulesLink.endswith('FFTermsandConditions.pdf')
    assert micro.priceIncrement == pytest.approx(0.01)
    assert micro.eventFixedPayout == pytest.approx(1.0)
    assert micro.timezone == 'US/Central'
    # 1801076400 UTC -> 2027-01-27 12:00 US/Central
    assert isinstance(micro.expectedResolutionTime, datetime)
    assert micro.expectedResolutionTime.strftime('%Y%m%d%H%M%S') == '20270127130000'
    assert micro.expectedPayoutTime.strftime('%Y%m%d%H%M%S') == '20270128130000'
    assert micro.lastTradeMillis == 1801076400000
    assert micro.lastTradeDate == '20270127'
    assert micro.lastTradeTime == '1300'

    # rules are market-level so they are applied to every micro, not just the first
    for mini in big.mini_contracts:
        assert mini.micro_no.longDescription == micro.longDescription


def test_contract_rules_are_cached_per_market():
    session = FakeSession({'/contract/rules': load('rules_FF.json')})
    client = FxCRestClient(session, min_interval=0.0, cache=MemoryCache())

    a = client.contract_rules(722489372, cache_key=658663572)
    b = client.contract_rules(722489376, cache_key=658663572)  # different outcome, same market
    assert a == b
    assert len(session.calls) == 1


def test_client_big_contract_enriches_by_default():
    routes = {
        '/contract/market': load('market_FF.json'),
        '/contract/rules': load('rules_FF.json'),
    }
    client = make_client(routes)
    big = client.big_contract(658663572, expiration='20270127')
    assert big.mini_contracts[0].micro_yes.sourceAgency == 'U.S. Federal Reserve'

    # enrichment can be disabled explicitly
    plain = client.big_contract(658663572, expiration='20270127', enrich=False)
    assert plain.mini_contracts[0].micro_yes.sourceAgency is None


def test_big_contract_recovers_free_fields_from_market_payload():
    """symbol / payout from the parent payload fill legacy fields at no extra cost."""
    big = FxContractBig.from_ibkr_market(load('market_FF.json'), expiration='20270127')
    micro = big.mini_contracts[0].micro_yes
    assert micro.underlyingSymbol == 'FF'
    assert micro.commodityCode == 'FF'
    assert micro.eventFixedPayout == 1.0


def test_big_contract_unknown_expiration_raises():
    payload = load('market_FF.json')
    with pytest.raises(AbstractionError) as exc:
        FxContractBig.from_ibkr_market(payload, expiration='19000101')
    assert 'Available expirations' in str(exc.value)


def test_big_contract_usip_market():
    payload = load('market_USIP.json')
    big = FxContractBig.from_ibkr_market(payload)
    assert big.conid == 751047561
    assert big.underlyingName == 'US Industrial Production'
    assert big.total_outcomes > 0


def test_client_big_contract_uses_market_payload():
    client = make_client({'/contract/market': load('market_FF.json')})
    big = client.big_contract(658663572, expiration='20261209')
    assert big.expiration == 20261209


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #
def test_normalize_market_contract_maps_side_and_fields():
    raw = load('market_FF.json')['contracts'][0]
    normalized = normalize_ibkr_market_contract(raw, 'US Fed Funds Target Rate', FXC_EXCHANGE)

    assert normalized['putOrCall'] == 'C'
    assert normalized['strikeLabel'] == raw['strike_label']
    assert normalized['conid'] == raw['conid']
    assert normalized['underlyingConid'] == raw['underlying_conid']
    assert normalized['underlyingName'] == 'US Fed Funds Target Rate'

    micro = FxContractMicro(normalized)
    assert micro.conid == raw['conid']
    assert micro.putOrCall == 'C'
    assert micro.underlyingName == 'US Fed Funds Target Rate'
    assert micro.strike == pytest.approx(raw['strike'])
    assert micro.expiration == int(raw['expiration'])


def test_market_memory_key_uses_string_expiration():
    """The resolved-market memory key must match the string expiration the client sends."""
    from argus.ib.forecast import FXCDispatcher

    big = FxContractBig.from_ibkr_market(load('market_FF.json'), expiration='20270127')
    key = FXCDispatcher._market_memory_key(big)
    assert key == (658663572, '20270127')
    # this is the key shape activate_market builds from a client-provided expiration
    assert key == (int(658663572), '20270127')


def test_get_big_contract_is_rest_only_and_returns_metadata():
    """Subscription-free command: full Big tree + rules metadata, no market_data."""
    import types

    from argus.ib.forecast import FXCDispatcher

    rest = make_client({
        '/contract/market': load('market_FF.json'),
        '/contract/rules': load('rules_FF.json'),
    })
    dispatcher = types.SimpleNamespace(_fxc_rest=rest)
    dispatcher.get_big_contract = lambda cid, expiration=None: FXCDispatcher.get_big_contract(
        dispatcher, cid, expiration
    )

    payload = json.loads(
        FXCDispatcher._client_get_big_contract(dispatcher, '658663572', '20270127')
    )

    assert payload['conid'] == 658663572
    assert payload['expiration'] == 20270127
    micro = payload['mini_contracts'][0]['micro_yes']
    assert micro['longDescription']  # rules enrichment applied
    assert micro['sourceAgency'] == 'U.S. Federal Reserve'
    assert micro['market_data'] is None  # no streaming / no subscription


def test_get_big_contract_rejects_non_integer_conid():
    import types

    from argus.ib.forecast import FXCDispatcher

    dispatcher = types.SimpleNamespace(_fxc_rest=None)
    with pytest.raises(ValueError):
        FXCDispatcher._client_get_big_contract(dispatcher, 'not-an-int')


def test_get_big_contract_requires_expiration():
    """A market conid spans several expirations, so omitting it is not idempotent."""
    import types

    from argus.ib.forecast import FXCDispatcher

    dispatcher = types.SimpleNamespace(_fxc_rest=None)
    with pytest.raises(ValueError, match='expiration is required'):
        FXCDispatcher.get_big_contract(dispatcher, 658663572, expiration=None)
    with pytest.raises(ValueError, match='expiration is required'):
        FXCDispatcher.get_big_contract(dispatcher, 658663572, expiration='')


def test_normalize_rejects_unknown_side():
    with pytest.raises(AbstractionError):
        normalize_ibkr_market_contract(
            {'conid': 1, 'strike_label': 'X', 'side': 'Q'}, 'm'
        )


# --------------------------------------------------------------------------- #
# Serialization regression (to_json used to reference an unimported `json`)
# --------------------------------------------------------------------------- #
def test_big_contract_to_json_serializes():
    big = FxContractBig.from_ibkr_market(load('market_FF.json'), expiration='20270127')
    parsed = json.loads(big.to_json())

    assert parsed['conid'] == 658663572
    assert parsed['expiration'] == 20270127
    assert len(parsed['mini_contracts']) == 16
    micro = parsed['mini_contracts'][0]['micro_yes']
    # legacy schema keys remain present even when the new endpoint has no value
    for key in ('putOrCall', 'strikeLabel', 'underlyingConid', 'underlyingName',
                'expectedResolutionTime', 'expectedPayoutTime', 'conid'):
        assert key in micro


# --------------------------------------------------------------------------- #
# Error handling
# --------------------------------------------------------------------------- #
def test_http_error_wrapped():
    class HttpErrorSession:
        def get(self, url, params=None):
            return FakeResponse({'error': 'boom'}, status=500)

    client = FxCRestClient(HttpErrorSession(), min_interval=0.0, cache=None)
    with pytest.raises(FxCRestError):
        client.category_tree()
