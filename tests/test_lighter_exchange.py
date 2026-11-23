"""
Offline unit tests for Lighter signed order execution
(argus/perpetuals/lighter/exchange.py).

No network and no native library: the signer (`_signer`) and the HTTP `Session` are
monkeypatched, so this asserts scaling, order/TIF mapping, nonce allocation, batch
submission, cancel-all and leverage read-back deterministically. Live round-trip coverage
lives in `tests/lighter_order_smoke.py`.
"""
import json
import time
import types
from decimal import Decimal

import pytest

from argus.perpetuals.lighter import _errors as _ers
from argus.perpetuals.lighter import _signer
from argus.perpetuals.lighter import exchange as _exch
from argus.perpetuals.shared import errors as _shared_ers


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _market(market_id=0, size_decimals=4, price_decimals=1, min_base="0.001", min_quote="1",
            default_imf=1000, min_imf=1000, flags=0):
    return types.SimpleNamespace(
        market_id=market_id, size_decimals=size_decimals, price_decimals=price_decimals,
        min_base_amount=Decimal(min_base), min_quote_amount=Decimal(min_quote),
        default_initial_margin_fraction=default_imf, min_initial_margin_fraction=min_imf,
        market_flags=flags,
    )


def _perp(name="BTC", market_id=0, **kw):
    return types.SimpleNamespace(name=name, market=_market(market_id=market_id, **kw))


class FakeRest:
    def __init__(self, perps=None, positions=None, active_orders=None):
        self.perps = perps if perps is not None else {"BTC": _perp("BTC"), "ETH": _perp("ETH", market_id=1)}
        self._positions = positions if positions is not None else []
        self._active_orders = active_orders or []
        self.calls = []

    def get_markets(self):
        return list(self.perps.values())

    def get_account(self, account_index=None, active_only=True):
        self.calls.append(("get_account", active_only))
        return types.SimpleNamespace(positions=self._positions)

    def get_account_active_orders(self):
        return list(self._active_orders)


class FakeSession:
    def __init__(self, next_nonce=100):
        self.gets = []
        self.posts = []
        self._next_nonce = next_nonce

    def get(self, url=None, params=None):
        self.gets.append((url, params))
        return _Resp({"code": 200, "nonce": self._next_nonce})

    def post(self, url=None, data=None):
        self.posts.append((url, data))
        if "sendTxBatch" in url:
            n = len(json.loads(data["tx_types"]))
            return _Resp({"code": 200, "tx_hash": [f"0xh{i}" for i in range(n)]})
        return _Resp({"code": 200, "tx_hash": "0xhash"})


@pytest.fixture
def fx(monkeypatch):
    """Build a LighterExchange whose signer and session are fakes. Returns (exchange, rest, session, signer)."""
    monkeypatch.setattr(_signer, "create_client", lambda *a, **k: None)
    _exch._last_nonce.clear()
    signer = {"create_order": [], "cancel_order": [], "cancel_all": [], "update_leverage": [], "auth": []}

    def fake_create_order(**kw):
        signer["create_order"].append(kw)
        return 14, json.dumps({"nonce": kw["nonce"]}), "0xcreate"

    def fake_cancel_order(**kw):
        signer["cancel_order"].append(kw)
        return 15, json.dumps(kw), "0xcancel"

    def fake_cancel_all(**kw):
        signer["cancel_all"].append(kw)
        return 16, json.dumps(kw), "0xcancelall"

    def fake_update_leverage(**kw):
        signer["update_leverage"].append(kw)
        return 20, json.dumps(kw), "0xlev"

    monkeypatch.setattr(_signer, "sign_create_order", fake_create_order)
    monkeypatch.setattr(_signer, "sign_cancel_order", fake_cancel_order)
    monkeypatch.setattr(_signer, "sign_cancel_all_orders", fake_cancel_all)
    monkeypatch.setattr(_signer, "sign_update_leverage", fake_update_leverage)
    monkeypatch.setattr(_signer, "create_auth_token",
                        lambda deadline_s, api, acc, **k: signer["auth"].append((deadline_s, api, acc)) or "tok")

    rest = FakeRest()
    session = FakeSession()
    monkeypatch.setattr(_exch, "Session", lambda *a, **k: session)
    ex = _exch.LighterExchange(account_index=1, api_key_index=0, api_key_private_key="deadbeef", rest=rest)
    return ex, rest, session, signer


# --- scaling and TIF ----------------------------------------------------------

def test_place_order_scales_and_submits(fx):
    ex, rest, session, signer = fx
    result = ex.place_order("BTC", "buy", "50000.5", "0.01")
    call = signer["create_order"][0]
    assert call["market_index"] == 0
    assert call["base_amount"] == 100          # 0.01 * 10^4
    assert call["price"] == 500005             # 50000.5 * 10^1
    assert call["is_ask"] is False
    assert call["time_in_force"] == 1 and call["order_expiry"] == -1   # GTC
    assert call["nonce"] == 100
    assert result.ok and result.status == "submitted" and result.tx_hash == "0xcreate"
    assert result.price == Decimal("50000.5") and result.requested_price == Decimal("50000.5")
    assert session.posts[0][0].endswith("/api/v1/sendTx")


@pytest.mark.parametrize("tif, expected_tf, expected_expiry", [
    ("Gtc", 1, -1), ("Ioc", 0, 0), ("Alo", 2, -1),
])
def test_tif_mapping(fx, tif, expected_tf, expected_expiry):
    ex, _rest, _session, signer = fx
    ex.place_order("BTC", "sell", "100", "1", tif=tif)
    call = signer["create_order"][0]
    assert call["is_ask"] is True
    assert call["time_in_force"] == expected_tf and call["order_expiry"] == expected_expiry


def test_price_rounds_half_even_and_size_rounds_down(fx):
    ex, _rest, _session, signer = fx
    # price_decimals=1 -> 50000.55 rounds to 50000.6; size_decimals=4 -> 0.01999 rounds down to 0.0199
    ex.place_order("BTC", "buy", "50000.55", "0.01999")
    call = signer["create_order"][0]
    assert call["price"] == 500006
    assert call["base_amount"] == 199


def test_below_minimum_is_rejected_before_signing(fx):
    ex, _rest, _session, signer = fx
    with pytest.raises(_ers.SignerError, match="minimum"):
        ex.place_order("BTC", "buy", "100", "0.0001")  # below min_base_amount 0.001
    assert signer["create_order"] == []


def test_unknown_coin_rejected(fx):
    ex, _rest, _session, _signer_calls = fx
    with pytest.raises(_shared_ers.InvalidCoinError):
        ex.place_order("DOGE", "buy", "1", "1")


# --- nonces -------------------------------------------------------------------

def test_nonces_fetched_once_then_incremented(fx):
    ex, _rest, session, signer = fx
    ex.place_order("BTC", "buy", "100", "1")
    ex.place_order("BTC", "buy", "100", "1")
    assert [c["nonce"] for c in signer["create_order"]] == [100, 101]
    assert len(session.gets) == 1  # nextNonce fetched lazily, once


def test_batch_uses_consecutive_nonces_and_sendtxbatch(fx):
    ex, _rest, session, signer = fx
    requests = [
        _exch.OrderRequest(coin="BTC", side="buy", price="100", size="1"),
        _exch.OrderRequest(coin="ETH", side="sell", price="100", size="1"),
    ]
    results = ex.place_orders(requests)
    assert [c["nonce"] for c in signer["create_order"]] == [100, 101]
    assert [c["market_index"] for c in signer["create_order"]] == [0, 1]
    assert session.posts[0][0].endswith("/api/v1/sendTxBatch")
    assert [r.status for r in results] == ["submitted", "submitted"]
    assert [r.tx_hash for r in results] == ["0xh0", "0xh1"]   # sendTxBatch's `tx_hash` list


def test_send_failure_resets_nonce(fx):
    ex, _rest, session, signer = fx
    responses = [{"code": 40001, "message": "invalid nonce"}, {"code": 200, "tx_hash": "0xok"}]
    session.post = lambda url=None, data=None: _Resp(responses.pop(0))
    with pytest.raises(_ers.ExchangeActionError, match="invalid nonce"):
        ex.place_order("BTC", "buy", "100", "1")
    ex.place_order("BTC", "buy", "100", "1")
    # The failed submission dropped the cached counter, so the retry re-read nextNonce and reused 100.
    assert [c["nonce"] for c in signer["create_order"]] == [100, 100]
    assert len(session.gets) == 2


def test_batch_cap_enforced(fx):
    ex, _rest, _session, _signer_calls = fx
    requests = [_exch.OrderRequest(coin="BTC", side="buy", price="100", size="1")] * (_exch.MAX_BATCH_SIZE + 1)
    with pytest.raises(_ers.LighterError, match="Too many orders"):
        ex.place_orders(requests)


# --- leverage -----------------------------------------------------------------

def test_update_leverage_uses_imf_and_mode(fx):
    ex, _rest, _session, signer = fx
    ex.update_leverage("BTC", 10, "isolated")
    call = signer["update_leverage"][0]
    assert call["market_index"] == 0
    assert call["initial_margin_fraction"] == 1000   # 10000 // 10
    assert call["margin_mode"] == 1


def test_update_leverage_rejects_out_of_range(fx):
    ex, _rest, _session, signer = fx
    with pytest.raises(_ers.SignerError):
        ex.update_leverage("BTC", 0, "cross")
    assert signer["update_leverage"] == []


def test_read_leverage_from_position_then_default(fx):
    from decimal import Decimal as D
    ex, _rest, _session, _signer_calls = fx  # market min_imf=1000 -> max 10x
    # The account endpoint reports this field as a PERCENTAGE (fixtures use "10.00"/"20.00"):
    # 20.00 == 20% == 5x. The basis-point reading (500x) is out of range, so percent wins.
    ex.rest._positions = [types.SimpleNamespace(market_id=0, initial_margin_fraction=D("20.00"), margin_mode=0)]
    lev = ex.read_leverage("BTC", fresh=True)
    assert lev.type == "cross" and lev.value == 5
    # A basis-point-shaped value in range is honoured too (2000bp == 20% == 5x).
    ex.rest._positions = [types.SimpleNamespace(market_id=0, initial_margin_fraction=D("2000"), margin_mode=1)]
    lev = ex.read_leverage("BTC", fresh=True)
    assert lev.type == "isolated" and lev.value == 5
    # Never traded -> market default_initial_margin_fraction 1000 (basis points) -> 10x.
    ex.rest._positions = []
    lev = ex.read_leverage("BTC", fresh=True)
    assert lev.type == "cross" and lev.value == 10
    assert ("get_account", False) in ex.rest.calls       # active_only=False so flat rows are visible


# --- cancels ------------------------------------------------------------------

def test_cancel_order_index_submits_cancel(fx):
    ex, _rest, session, signer = fx
    result = ex.cancel_order_index("BTC", 42)
    assert signer["cancel_order"][0]["market_index"] == 0
    assert signer["cancel_order"][0]["order_index"] == 42
    assert result.ok and result.canceled_order_indexes == [42]
    assert session.posts[0][0].endswith("/api/v1/sendTx")


def test_cancel_all_uses_native_immediate_path(fx):
    ex, _rest, session, signer = fx
    out = ex.cancel_all()
    assert signer["cancel_all"][0]["cancel_all_market_index"] == _signer.NIL_MARKET_INDEX
    assert signer["cancel_all"][0]["time_in_force"] == 0
    assert out["status"] == "submitted" and out["marketIndex"] == _signer.NIL_MARKET_INDEX


def test_cancel_many_resolves_client_index(fx):
    ex, _rest, session, signer = fx
    ex.rest._active_orders = [types.SimpleNamespace(client_order_index=7, order_index=99)]
    result = ex.cancel_many([("BTC", "client_order_index", 7)])
    assert signer["cancel_order"][0]["order_index"] == 99
    assert result.ok_count == 1 and result.outcomes[0].coin == "BTC"


# --- error envelope -----------------------------------------------------------

def test_auth_token_provider_refreshes_near_expiry():
    from argus.perpetuals.lighter.rest import LighterRest
    rest = LighterRest(account_index=1)   # construction is offline (no network)
    calls = {"n": 0}

    def provider():
        calls["n"] += 1
        return f"{int(time.time()) + 6 * 3600}:1:0:deadbeef"

    rest.set_auth_token_provider(provider)
    assert calls["n"] == 1                       # minted eagerly on install
    rest._ensure_auth_token()
    assert calls["n"] == 1                       # still valid: no re-mint
    rest._auth_token_expiry = int(time.time()) + 60   # inside the 30-min refresh margin
    rest._ensure_auth_token()
    assert calls["n"] == 2                       # refreshed


def test_non_200_code_raises_exchange_action_error(fx):
    ex, _rest, session, _signer_calls = fx
    session.post = lambda url=None, data=None: _Resp({"code": 40001, "message": "invalid nonce"})
    with pytest.raises(_ers.ExchangeActionError, match="invalid nonce"):
        ex.place_order("BTC", "buy", "100", "1")


# --- rate limit ---------------------------------------------------------------

class _Resp429(_Resp):
    status_code = 429
    headers = {"Retry-After": "7"}


def test_http_429_is_a_definitive_rate_limit_error_and_resets_nonce(fx):
    ex, _rest, session, _signer_calls = fx
    session.post = lambda url=None, data=None: _Resp429({})
    with pytest.raises(_ers.RateLimitError, match="retry after 7s"):
        ex.place_order("BTC", "buy", "100", "1")
    assert isinstance(_ers.RateLimitError("x"), _ers.ExchangeActionError)   # definitive via LighterError
    assert not _exch._last_nonce                                            # counter dropped -> re-read


# --- client order index -------------------------------------------------------

def test_cloid_zero_is_rejected_and_autoallocation_never_returns_zero(fx, monkeypatch):
    ex, _rest, _session, _signer_calls = fx
    with pytest.raises(_ers.SignerError, match="no client order index"):
        ex.place_order("BTC", "buy", "100", "1", cloid=0)
    monkeypatch.setattr(_exch, "_client_order_index", iter([_exch._MAX_CLIENT_ORDER_INDEX + 1, 5]))
    assert _exch._next_client_order_index() == 5      # wrapped value 0 is skipped


# --- leverage cache -----------------------------------------------------------

def _cached_exchange(rest, monkeypatch, ttl=10.0):
    monkeypatch.setattr(_signer, "create_client", lambda *a, **k: None)
    return _exch.LighterExchange(1, 0, "deadbeef", rest=rest, leverage_cache_ttl_s=ttl)


def test_leverage_is_read_fresh_by_default(fx):
    ex, rest, _session, _signer_calls = fx
    ex.read_leverage("BTC")
    ex.read_leverage("BTC")
    assert rest.calls.count(("get_account", False)) == 2     # the roll-back target is never stale by default


def test_opt_in_leverage_cache_serves_then_fresh_bypasses(fx, monkeypatch):
    _ex, rest, _session, _signer_calls = fx
    ex = _cached_exchange(rest, monkeypatch)
    ex.read_leverage("BTC")
    ex.read_leverage("BTC")
    assert rest.calls.count(("get_account", False)) == 1
    ex.read_leverage("BTC", fresh=True)
    assert rest.calls.count(("get_account", False)) == 2


def test_update_leverage_refreshes_cache_and_failure_evicts_it(fx, monkeypatch):
    _ex, rest, session, _signer_calls = fx
    ex = _cached_exchange(rest, monkeypatch)
    ex.update_leverage("BTC", 4, "isolated")
    lev = ex.read_leverage("BTC")                          # served from the cache our own update wrote
    assert (lev.type, lev.value) == ("isolated", 4) and rest.calls == []
    ex.session = session
    session.post = lambda url=None, data=None: _Resp({"code": 40001, "message": "no"})
    with pytest.raises(_ers.ExchangeActionError):
        ex.update_leverage("BTC", 5, "isolated")
    ex.read_leverage("BTC")
    assert rest.calls == [("get_account", False)]          # evicted -> real read


# --- market cache -------------------------------------------------------------

def test_market_cache_refreshes_after_ttl_and_survives_a_failed_refresh(fx):
    ex, rest, _session, _signer_calls = fx
    fetches = []
    real = rest.get_markets
    rest.get_markets = lambda: fetches.append(1) or real()
    ex._perp_by_symbol, ex._markets_loaded_at = {}, 0.0
    ex.max_leverage("BTC"); ex.max_leverage("BTC")
    assert len(fetches) == 1                                # fresh: no re-fetch
    ex._markets_loaded_at = time.time() - ex._market_cache_ttl_s - 1
    ex.max_leverage("BTC")
    assert len(fetches) == 2                                # TTL expired -> refreshed
    ex._markets_loaded_at = time.time() - ex._market_cache_ttl_s - 1
    rest.get_markets = lambda: (_ for _ in ()).throw(RuntimeError("down"))
    assert ex.max_leverage("BTC") == 10                     # stale list still serves
    assert ex._markets_loaded_at > time.time() - ex._market_cache_ttl_s   # and the retry is backed off


# --- chunked batches ----------------------------------------------------------

def _orders(n):
    return [_exch.OrderRequest(coin="BTC", side="buy", price="100", size="1") for _ in range(n)]


def test_place_orders_is_chunked_with_consecutive_nonces(fx):
    ex, _rest, session, signer = fx
    results = ex.place_orders(_orders(20))
    sizes = [len(json.loads(d["tx_types"])) for _u, d in session.posts]
    assert sizes == [_exch.REST_BATCH_CHUNK, 20 - _exch.REST_BATCH_CHUNK]
    assert [c["nonce"] for c in signer["create_order"]] == list(range(100, 120))
    assert len(results) == 20 and all(r.ok for r in results)


def test_place_orders_later_chunk_rejection_returns_per_order_errors(fx):
    ex, _rest, session, _signer_calls = fx
    real_post = session.post
    session.post = lambda url=None, data=None: (
        real_post(url, data) if not session.posts else (session.posts.append((url, data)) or
                                                       _Resp({"code": 40001, "message": "bad"})))
    results = ex.place_orders(_orders(20))
    first, rest_ = results[:_exch.REST_BATCH_CHUNK], results[_exch.REST_BATCH_CHUNK:]
    assert all(r.ok and r.status == "submitted" for r in first)
    assert all((not r.ok) and r.status == "rejected" and "bad" in r.error and r.tx_hash is None for r in rest_)


def test_place_orders_first_chunk_rejection_raises(fx):
    ex, _rest, session, _signer_calls = fx
    session.post = lambda url=None, data=None: _Resp({"code": 40001, "message": "bad"})
    with pytest.raises(_ers.ExchangeActionError, match="bad"):
        ex.place_orders(_orders(20))


def _open_orders(n):
    return [types.SimpleNamespace(order_id=str(1000 + i), order_index=1000 + i, client_order_index=i + 1)
            for i in range(n)]


def test_cancel_many_chunks_and_reports_partial_failure(fx):
    ex, rest, session, signer = fx
    items = [("BTC", "order_index", 1000 + i) for i in range(20)]
    result = ex.cancel_many(items)
    assert [len(json.loads(d["tx_types"])) for _u, d in session.posts] == [_exch.REST_BATCH_CHUNK, 5]
    assert result.ok_count == 20
    # Second chunk rejected: only its items fail, the first chunk's stay ok.
    session.posts.clear()
    real = session.post
    session.post = lambda url=None, data=None: (
        real(url, data) if not session.posts else (session.posts.append((url, data)) or
                                                  _Resp({"code": 40001, "message": "nope"})))
    result = ex.cancel_many(items)
    assert [o.ok for o in result.outcomes] == [True] * 15 + [False] * 5
    assert all("nope" in o.error for o in result.outcomes[15:])


def test_cancel_many_stops_after_a_rate_limit(fx):
    ex, _rest, session, _signer_calls = fx
    session.post = lambda url=None, data=None: _Resp429({})
    result = ex.cancel_many([("BTC", "order_index", 1 + i) for i in range(20)])
    assert result.ok_count == 0
    assert all("rate limit" in o.error for o in result.outcomes)


def test_cancel_many_kinds_do_not_collide_and_misses_are_item_errors(fx):
    ex, rest, _session, signer = fx
    # Order A: client index 5, order_index 900. Order B: order_id "5", order_index 901 -- the same
    # integer 5 names a DIFFERENT order depending on the kind.
    rest._active_orders = [
        types.SimpleNamespace(order_id="900", order_index=900, client_order_index=5),
        types.SimpleNamespace(order_id="5", order_index=901, client_order_index=77),
    ]
    result = ex.cancel_many([
        ("BTC", "client_order_index", 5),     # -> A (900)
        ("BTC", "order_id", 5),               # -> B (901)
        ("BTC", "client_order_index", 999),   # unknown -> item error, batch still goes
        ("NOPE", "order_index", 1),           # unknown coin -> item error
    ])
    assert [c["order_index"] for c in signer["cancel_order"]] == [900, 901]
    assert [o.ok for o in result.outcomes] == [True, True, False, False]
    assert "no open order" in result.outcomes[2].error


def test_cancel_order_resolves_by_kind_and_raises_when_missing(fx):
    ex, rest, _session, signer = fx
    rest._active_orders = _open_orders(3)
    ex.cancel_order("BTC", "client_order_index", 2)
    assert signer["cancel_order"][0]["order_index"] == 1001
    with pytest.raises(_shared_ers.InvalidCoinError):
        ex.cancel_order("BTC", "client_order_index", 424242)
