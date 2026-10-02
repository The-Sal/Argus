"""
Unit tests for Hyperliquid order execution (argus/perpetuals/hyper/exchange.py) and the
dispatcher's trading handlers. Everything here is offline and deterministic: signing is
verified by recovering the signer from a known test key, asset-id resolution runs against
a stubbed `info` endpoint, and dispatcher handlers run on a lightweight fake self rather
than a live HyperLiquidDispatcher (which would bind port 9972 and refresh every dex).

Live round-trip coverage (place -> resting -> cancel) lives in tests/hyper_order_smoke.py.
"""
from decimal import Decimal

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data

from argus._argus_utils import ArgsObject
from argus.perpetuals.hyper import _classes as _cls
from argus.perpetuals.hyper import _errors as _ers
from argus.perpetuals.hyper import exchange as _exch
from argus.perpetuals.hyper._classes import CancelResult, OrderPlacementResult
from argus.perpetuals.hyper.exchange import (
    HyperLiquidExchange,
    _to_wire_number,
    validate_cloid,
)
from argus.perpetuals.shared import errors as shared_ers
from argus.perpetuals.shared.errors import InvalidCoinError

# Well-known hardhat/anvil test key: 0xac09...ff80 -> 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266
TEST_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TEST_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"

CLOID = "0x" + "ab" * 16


def _exchange(**kwargs) -> HyperLiquidExchange:
    return HyperLiquidExchange(TEST_ADDRESS, TEST_KEY, **kwargs)


# --- wire number rendering ------------------------------------------------------

class TestWireNumber:
    @pytest.mark.parametrize("value, expected", [
        (50000.5, "50000.5"),
        ("50000.50", "50000.5"),
        (100, "100"),
        ("0.10", "0.1"),
        (0.1, "0.1"),                      # float artifacts must not leak onto the wire
        (Decimal("0.0001"), "0.0001"),
        (123456.78901234, "123456.78901234"),  # exactly 8 decimals is legal
        (-0.0, "0"),
    ])
    def test_render(self, value, expected):
        assert _to_wire_number(value) == expected

    @pytest.mark.parametrize("value", [
        0.123456789,          # 9 decimals
        "1.000000001",
        Decimal("1.123456789"),
    ])
    def test_too_many_decimals(self, value):
        with pytest.raises(_ers.HyperLiquidError):
            _to_wire_number(value)


# --- cloid validation -------------------------------------------------------------

class TestCloid:
    def test_valid_passthrough(self):
        assert validate_cloid(CLOID) == CLOID

    @pytest.mark.parametrize("bad", [
        "ab" * 16,             # missing 0x
        "0x" + "ab" * 15,      # 15 bytes
        "0x" + "ab" * 17,      # 17 bytes
        "0x" + "zz" * 16,      # not hex
        12345,                 # not a string
    ])
    def test_invalid(self, bad):
        with pytest.raises(_ers.HyperLiquidError):
            validate_cloid(bad)


# --- action hash (byte layout is part of the signature) ---------------------------

class TestActionHash:
    ACTION = {"type": "cancel", "cancels": [{"a": 0, "o": 1}]}

    def test_deterministic_and_nonce_sensitive(self):
        h1 = HyperLiquidExchange.action_hash(self.ACTION, nonce=1_700_000_000_000)
        h2 = HyperLiquidExchange.action_hash(self.ACTION, nonce=1_700_000_000_000)
        h3 = HyperLiquidExchange.action_hash(self.ACTION, nonce=1_700_000_000_001)
        assert h1 == h2 and len(h1) == 32
        assert h1 != h3

    def test_vault_and_expires_suffixes(self):
        base = HyperLiquidExchange.action_hash(self.ACTION, nonce=1)
        vaulted = HyperLiquidExchange.action_hash(self.ACTION, nonce=1, vault_address="0x" + "11" * 20)
        expired = HyperLiquidExchange.action_hash(self.ACTION, nonce=1, expires_after=2)
        assert base != vaulted != expired

    def test_pinned_vector(self):
        # Pins the exact byte layout: msgpack(action, key order) + BE64 nonce + 0x00 vault tag.
        # If this changes, every previously-signed action shape is broken -- investigate before updating.
        h = HyperLiquidExchange.action_hash(
            {"type": "cancel", "cancels": [{"a": 7, "o": 123456789}]}, nonce=1_700_000_000_000
        )
        assert h.hex() == "596e6d327491c26b700932f4ee29d1c71dd63ba183ecd2a82cb1f0fdd62d7f77"


class TestMsgpackEncoding:
    def test_installed_msgpack_is_modern(self):
        # Guards against the legacy `msgpack-python` package (0.5.6) shadowing `msgpack`: it ships the
        # same module name with different string encoding and corrupts any signed action with a cloid.
        import msgpack
        assert msgpack.version >= (1, 0, 0)

    def test_pinned_vector_with_cloid(self):
        # A cloid is 34 chars, so it packs as str8 (0xd9) under msgpack 1.x but raw16 (0xda) under
        # 0.5.6 -- different bytes, different hash, venue rejects the signature. Pin the 1.x hash.
        import msgpack
        action = {"type": "cancelByCloid", "cancels": [{"asset": 0, "cloid": CLOID}]}
        modern = msgpack.packb(action, use_bin_type=True)
        assert b"\xd9\x22" + CLOID.encode() in modern
        assert HyperLiquidExchange.action_hash(action, nonce=1_700_000_000_000).hex() == (
            "31a999a50decc69e7a8ddc13d71ce19268945f6c5f84b1a44b6dbf4fa3b683be"
        )


class TestNonce:
    def test_strictly_increasing_even_within_one_millisecond(self, monkeypatch):
        monkeypatch.setattr(_exch.time, "time", lambda: 1_700_000_000.0)
        addr = "0xNonceTestWallet"
        nonces = [_exch._next_nonce(addr) for _ in range(5)]
        assert nonces == sorted(set(nonces)) and len(nonces) == 5

    def test_unique_across_threads(self):
        import threading
        addr = "0xNonceThreads"
        out, lock = [], threading.Lock()

        def work():
            for _ in range(200):
                n = _exch._next_nonce(addr)
                with lock:
                    out.append(n)

        threads = [threading.Thread(target=work) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert len(out) == len(set(out)) == 1600

    def test_wallets_do_not_share_a_counter_and_case_is_ignored(self, monkeypatch):
        monkeypatch.setattr(_exch.time, "time", lambda: 1_800_000_000.0)
        a1 = _exch._next_nonce("0xAbC")
        a2 = _exch._next_nonce("0xabc")
        assert a2 == a1 + 1
        assert _exch._next_nonce("0xOther") == a1


# --- EIP-712 signing round trip -----------------------------------------------------

class TestSignAction:
    def test_recovers_to_signer(self):
        exchange = _exchange()
        action = {
            "type": "order",
            "orders": [{"a": 3, "b": True, "p": "50000.5", "s": "0.001", "r": False,
                        "t": {"limit": {"tif": "Gtc"}}}],
            "grouping": "na",
        }
        nonce = 1_700_000_000_000
        sig = exchange.sign_action(action, nonce)
        assert sig["v"] in (27, 28)
        assert len(sig["r"]) == 66 and len(sig["s"]) == 66 and sig["r"].startswith("0x")

        h = exchange.action_hash(action, nonce)
        recovered = Account.recover_message(
            encode_typed_data(full_message={
                "domain": _exch._L1_DOMAIN,
                "types": _exch._L1_TYPES,
                "primaryType": "Agent",
                "message": {"source": "a", "connectionId": h},
            }),
            vrs=[sig["v"], sig["r"], sig["s"]],
        )
        assert recovered.lower() == TEST_ADDRESS.lower()

    def test_mainnet_and_testnet_differ(self):
        # The EIP-712 "source" tag ("a"/"b") is derived from base_url, so the same action
        # must sign differently per network (no cross-network replay).
        mainnet = _exchange()
        testnet = _exchange(base_url="https://api.hyperliquid-testnet.xyz")
        assert mainnet.is_mainnet and not testnet.is_mainnet
        action = {"type": "cancel", "cancels": [{"a": 0, "o": 1}]}
        assert mainnet.sign_action(action, 1) != testnet.sign_action(action, 1)


class TestRoundPrice:
    def _ex(self):
        return StubInfoExchange()  # default-dex coins have szDecimals=5

    @pytest.mark.parametrize("price, expected", [
        ("50000.50", Decimal("50000.0")),     # 5 sig figs, then 6-5=1 decimal
        ("33761.96", Decimal("33762.0")),
        (100, Decimal("100.0")),
        ("0.123456789", Decimal("0.1")),
    ])
    def test_perp_rules(self, price, expected):
        assert self._ex().round_price("BTC", price) == expected

    @pytest.mark.parametrize("price, expected", [
        ("105432", Decimal("105432")),        # whole numbers are valid at any length: never rounded
        (105432, Decimal("105432")),
        (105432.0, Decimal("105432")),
        ("1234567", Decimal("1234567")),
        ("105432.4", Decimal("105432")),      # fractional with 6 integer digits: nearest integer
        ("99999.5", Decimal("100000")),
    ])
    def test_whole_numbers_pass_through(self, price, expected):
        assert self._ex().round_price("BTC", price) == expected

    def test_more_decimals_for_low_precision_size(self):
        # szDecimals=2 -> up to 4 price decimals; szDecimals=0 -> up to 6.
        ex = StubInfoExchange()
        assert ex.round_price("xyz:AAPL", "1.23456789") == Decimal("1.2346")

    def test_unknown_coin(self):
        with pytest.raises(InvalidCoinError):
            self._ex().round_price("DOGE", 1)


class TestPlaceOrderReportsRoundedPrice:
    def _ex(self, price):
        ex = StubInfoExchange()
        ex.session.post = lambda url, json=None: type("R", (), {"json": lambda s: {
            "status": "ok", "response": {"data": {"statuses": [{"resting": {"oid": 5}}]}}}})()
        return ex.place_order("BTC", "buy", price, 1)

    def test_adjusted_price_is_reported(self):
        r = self._ex("30000.123456")
        d = r.to_dict()
        assert d["price"] == "30000" and d["requestedPrice"] == "30000.123456" and d["priceAdjusted"] is True

    def test_unadjusted_price(self):
        d = self._ex("105432").to_dict()
        assert d["price"] == "105432" and d["requestedPrice"] == "105432" and d["priceAdjusted"] is False

    def test_error_result_still_carries_prices(self):
        ex = StubInfoExchange()
        ex.session.post = lambda url, json=None: type("R", (), {"json": lambda s: {
            "status": "ok", "response": {"data": {"statuses": [{"error": "nope"}]}}}})()
        d = ex.place_order("BTC", "buy", "1.23456789", 1).to_dict()
        assert d["error"] == "nope" and d["price"] is not None and d["priceAdjusted"] is True


# --- asset-id resolution (stubbed info endpoint) -----------------------------------

DEFAULT_UNIVERSE = ["BTC", "ETH", "SOL"]
DEX_RESPONSE = [None, {"name": "xyz"}, {"name": "flx"}]


class StubInfoExchange(HyperLiquidExchange):
    """An exchange whose `info` calls answer from canned data and count themselves."""

    def __init__(self):
        super().__init__(TEST_ADDRESS, TEST_KEY)
        self.info_calls = []

    def _info(self, body):
        self.info_calls.append(body)
        if body["type"] == "meta":
            dex = body.get("dex", "")
            if dex == "xyz":
                return {"universe": [{"name": "AAPL", "szDecimals": 2, "maxLeverage": 10},
                                     {"name": "GOLD", "szDecimals": 2, "maxLeverage": 10, "onlyIsolated": True}]}
            if dex == "flx":
                return {"universe": [{"name": "NVDA", "szDecimals": 2, "maxLeverage": 10}]}
            return {"universe": [{"name": name, "szDecimals": 5, "maxLeverage": 20} for name in DEFAULT_UNIVERSE]}
        if body["type"] == "perpDexs":
            return DEX_RESPONSE
        raise AssertionError(f"unexpected info body: {body!r}")


class TestAssetIdResolution:
    def test_default_dex_is_raw_universe_index(self):
        ex = StubInfoExchange()
        assert ex.resolve_asset_id("BTC") == 0
        assert ex.resolve_asset_id("ETH") == 1
        assert ex.resolve_asset_id("SOL") == 2

    def test_hip3_offsets_follow_perpDexs_order(self):
        # xyz is the first non-null perpDexs entry -> 110000; flx second -> 120000.
        ex = StubInfoExchange()
        assert ex.resolve_asset_id("xyz:AAPL") == 110_000
        assert ex.resolve_asset_id("xyz:GOLD") == 110_001
        assert ex.resolve_asset_id("flx:NVDA") == 120_000

    def test_unknown_coin_and_dex(self):
        ex = StubInfoExchange()
        with pytest.raises(InvalidCoinError):
            ex.resolve_asset_id("DOGE")
        with pytest.raises(InvalidCoinError):
            ex.resolve_asset_id("nope:BTC")

    def test_caches_meta_and_dexs(self):
        ex = StubInfoExchange()
        for _ in range(3):
            ex.resolve_asset_id("BTC")
            ex.resolve_asset_id("xyz:AAPL")
        meta_calls = [c for c in ex.info_calls if c["type"] == "meta"]
        assert len(meta_calls) == 2   # "" and "xyz"
        assert [c["type"] for c in ex.info_calls].count("perpDexs") == 1

    def test_sz_decimals_lookup(self):
        ex = StubInfoExchange()
        assert ex.sz_decimals("BTC") == 5
        assert ex.sz_decimals("xyz:AAPL") == 2
        with pytest.raises(InvalidCoinError):
            ex.sz_decimals("DOGE")


# --- result parsing -----------------------------------------------------------------

class TestResults:
    def test_placement_resting(self):
        r = OrderPlacementResult.from_response("BTC", {"data": {"statuses": [{"resting": {"oid": 42}}]}})
        assert (r.ok, r.oid, r.status, r.error, r.avg_px) == (True, 42, "resting", None, None)
        assert r.to_dict() == {"coin": "BTC", "oid": 42, "status": "resting", "avgPx": None,
                                "price": None, "requestedPrice": None, "priceAdjusted": False, "error": None}

    def test_placement_filled(self):
        r = OrderPlacementResult.from_response(
            "BTC", {"data": {"statuses": [{"filled": {"oid": 43, "avgPx": "50000.5"}}]}}
        )
        assert (r.ok, r.status, r.avg_px) == (True, "filled", Decimal("50000.5"))
        assert r.to_dict()["avgPx"] == "50000.5"

    def test_placement_venue_error(self):
        r = OrderPlacementResult.from_response(
            "BTC", {"data": {"statuses": [{"error": "Insufficient margin"}]}}
        )
        assert (r.ok, r.oid, r.error) == (False, None, "Insufficient margin")

    def test_placement_bad_shapes(self):
        with pytest.raises(_ers.HyperLiquidError):
            OrderPlacementResult.from_response("BTC", {"data": {}})
        with pytest.raises(_ers.HyperLiquidError):
            OrderPlacementResult.from_response("BTC", {"data": {"statuses": [{"weird": 1}]}})

    def test_cancel_ack_and_error(self):
        r = CancelResult.from_response(
            "BTC", {"data": {"statuses": [{"resting": {"oid": 42}}]}}
        )
        assert (r.ok, r.canceled_oids, r.errors) == (True, [42], [])
        assert r.to_dict() == {"coin": "BTC", "canceledOids": [42], "errors": []}

    def test_cancel_success_string_is_the_real_ack(self):
        # Live venue shape: a confirmed cancel is the bare string "success" (no oid of its own).
        r = CancelResult.from_response("ETH", {"data": {"statuses": ["success"]}}, requested_oids=[7])
        assert (r.ok, r.canceled_oids, r.errors) == (True, [7], [])
        r = CancelResult.from_response("ETH", {"data": {"statuses": ["success"]}})  # cancel by cloid
        assert (r.ok, r.canceled_oids) == (True, [-1])
        r = CancelResult.from_response("ETH", {"data": {"statuses": ["success", {"error": "nope"}]}}, requested_oids=[1, 2])
        assert (r.ok, r.canceled_oids, r.errors) == (False, [1], ["nope"])

    def test_cancel_unknown_id_is_a_soft_failure(self):
        # The venue answers a cancel for an id it cannot cancel with a per-id error.
        r = CancelResult.from_response("BTC", {"data": {"statuses": [
            {"error": "Order was never placed, already canceled, or filled."}]}})
        assert r.ok is False and r.canceled_oids == []
        assert "never placed" in r.errors[0]

    def test_cancel_empty(self):
        r = CancelResult.from_response("BTC", {"data": {"statuses": []}})
        assert r.ok is False and r.to_dict()["canceledOids"] == []


# --- exchange submission (stubbed transport) -----------------------------------------

class TestExchangeSubmission:
    def _ex(self, posted=None):
        ex = StubInfoExchange()
        captured = {}

        def fake_post(url, json=None):
            captured["url"] = url
            captured["payload"] = json
            class R:
                def json(self_inner):
                    return posted if posted is not None else {"status": "ok", "response": {}}
            return R()

        ex.session.post = fake_post
        return ex, captured

    def test_place_order_wire_shape_and_key_order(self):
        # Key order on the wire IS part of the signature: pin it.
        ex, captured = self._ex(posted={"status": "ok", "response": {
            "data": {"statuses": [{"resting": {"oid": 7}}]}}})
        result = ex.place_order("BTC", "buy", "50000.50", 0.001, cloid=CLOID)

        payload = captured["payload"]
        assert captured["url"].endswith("/exchange")
        assert set(payload) == {"action", "nonce", "signature", "vaultAddress", "expiresAfter"}
        action = payload["action"]
        assert list(action) == ["type", "orders", "grouping"]
        order = action["orders"][0]
        assert list(order) == ["a", "b", "p", "s", "r", "t", "c"]
        assert order["a"] == 0 and order["b"] is True
        assert order["p"] == "50000" and order["s"] == "0.001"  # price rounded to 5 sig figs, 6-szDecimals places
        assert order["r"] is False and order["t"] == {"limit": {"tif": "Gtc"}}
        assert order["c"] == CLOID
        assert result.ok and result.oid == 7

    def test_place_order_sell_ioc_reduce_only(self):
        ex, captured = self._ex(posted={"status": "ok", "response": {
            "data": {"statuses": [{"filled": {"oid": 8, "avgPx": "100"}}]}}})
        result = ex.place_order("ETH", "sell", 100, 2, tif="Ioc", reduce_only=True)
        order = captured["payload"]["action"]["orders"][0]
        assert order["a"] == 1 and order["b"] is False and order["r"] is True
        assert order["t"] == {"limit": {"tif": "Ioc"}}
        assert "c" not in order
        assert result.status == "filled"

    def test_place_order_rejects_bad_inputs(self):
        ex, _ = self._ex()
        with pytest.raises(_ers.HyperLiquidError):
            ex.place_order("BTC", "hold", 1, 1)
        with pytest.raises(_ers.HyperLiquidError):
            ex.place_order("BTC", "buy", 1, 1, tif="FOK")
        with pytest.raises(InvalidCoinError):
            ex.place_order("DOGE", "buy", 1, 1)

    def test_cancel_wire_shapes(self):
        ex, captured = self._ex(posted={"status": "ok", "response": {
            "data": {"statuses": [{"resting": {"oid": 42}}]}}})
        assert ex.cancel_by_oid("xyz:AAPL", 42).canceled_oids == [42]
        action = captured["payload"]["action"]
        assert action == {"type": "cancel", "cancels": [{"a": 110_000, "o": 42}]}

        ex, captured = self._ex(posted={"status": "ok", "response": {
            "data": {"statuses": [{"resting": {"oid": 7, "cloid": CLOID}}]}}})
        assert ex.cancel_by_cloid("BTC", CLOID).ok
        action = captured["payload"]["action"]
        assert action == {"type": "cancelByCloid", "cancels": [{"asset": 0, "cloid": CLOID}]}

    def test_err_envelope_raises(self):
        ex, _ = self._ex(posted={"status": "err", "response": "invalid signature"})
        with pytest.raises(_ers.ExchangeActionError, match="invalid signature"):
            ex.cancel_by_oid("BTC", 1)


# --- dispatcher trading handlers (fake self, no server) -------------------------------

class _FakeDispatcher:
    """Binds the real handler functions (and the helpers they call) to stubbed collaborators."""

    def __init__(self, exchange=None, account_rest=None, rest=None, blocked=False):
        import inspect
        import types
        from argus.perpetuals.hyper import HyperLiquidDispatcher
        from argus.perpetuals.shared import LockedState
        self._hd = HyperLiquidDispatcher
        self._inspect, self._types = inspect, types
        self.exchange = exchange or StubInfoExchange()
        self.account_rest = account_rest
        self.rest = rest or RecordingRest()
        self._block_order_execution = LockedState(blocked)
        self.fatal = []
        self._on_fatal_error = self.fatal.append  # instance attr beats __getattr__

    def __getattr__(self, name):
        # Delegate everything else (_read_args, _apply_leverage, _require_order_execution_enabled, ...) to the
        # real class so tests exercise the production helpers, not copies.
        try:
            attr = self._inspect.getattr_static(self._hd, name)
        except AttributeError:
            raise AttributeError(name)
        if isinstance(attr, staticmethod):
            return attr.__func__
        if isinstance(attr, property):
            return attr.fget(self)
        if callable(attr):
            return self._types.MethodType(attr, self)
        return attr

    def call(self, handler, data):
        return getattr(self._hd, handler)(self, ArgsObject(sock=None, args=data))

    def place_order(self, data):
        return self.call("_place_order", data)

    def cancel_order(self, data):
        return self.call("_cancel_order", data)


class RecordingRest:
    """Stands in for HyperLiquidRest: serves `activeAssetData` leverage per coin and records reads."""

    def __init__(self, leverage=None, fail=False):
        self.leverage = dict(leverage or {})
        self.fail = fail
        self.reads = []

    def get_active_asset_data(self, coin):
        self.reads.append(coin)
        if self.fail:
            raise RuntimeError("rate limited")
        lev = self.leverage.get(coin, _cls.PositionLeverage(type="cross", value=20))
        return _cls.ActiveAssetData(coin=coin, leverage=lev, mark_px=Decimal("100"),
                                    max_trade_sizes=(Decimal("1"), Decimal("1")),
                                    available_to_trade=(Decimal("1"), Decimal("1")))


class RecordingExchange(StubInfoExchange):
    """A stub-universe exchange whose signed actions are recorded instead of sent."""

    def __init__(self, result=None, results=None, place_error=None):
        super().__init__()
        self.calls = []
        self.result = result or _cls.OrderPlacementResult(coin="BTC", oid=1, status="resting",
                                                          price=Decimal("100"))
        self.results = results
        self.place_error = place_error

    def update_leverage(self, coin, leverage, is_cross):
        self.calls.append(("update_leverage", coin, leverage, is_cross))
        return _cls.LeverageUpdateResult(coin, leverage, "cross" if is_cross else "isolated")

    def place_order(self, **kwargs):
        self.calls.append(("place_order", kwargs))
        if self.place_error:
            raise self.place_error
        return self.result

    def place_orders(self, requests):
        self.calls.append(("place_orders", requests))
        if self.place_error:
            raise self.place_error
        return self.results

    def cancel_by_oid(self, coin, oid):
        self.calls.append(("cancel_by_oid", coin, oid))
        return _cls.CancelResult(coin=coin, canceled_oids=[oid])

    def cancel_by_cloid(self, coin, cloid):
        self.calls.append(("cancel_by_cloid", coin, cloid))
        return _cls.CancelResult(coin=coin, canceled_oids=[7])

    def cancel_many(self, items):
        self.calls.append(("cancel_many", items))
        return _cls.BatchCancelResult([_cls.CancelOutcome(str(i), c, True) for c, i in items])


class TestDispatcherHandlers:
    def test_place_order_maps_args(self):
        fx = _FakeDispatcher(exchange=RecordingExchange())
        out = fx.place_order({
            "coin": "BTC", "side": "buy", "price": "50000.5", "size": 0.001, "leverage": 20,
            "order_type": "ioc", "reduce_only": True, "cloid": CLOID,
        })
        (name, kwargs), = fx.exchange.calls  # leverage already 20x cross: no update action is sent
        assert name == "place_order"
        assert kwargs["tif"] == "Ioc" and kwargs["reduce_only"] is True and kwargs["cloid"] == CLOID
        assert out["coin"] == "BTC" and out["oid"] == 1

    def test_place_order_defaults_and_validation(self):
        fx = _FakeDispatcher(exchange=RecordingExchange())
        fx.place_order({"coin": "BTC", "side": "sell", "price": 1, "size": 1, "leverage": 20})
        assert fx.exchange.calls[0][1]["tif"] == "Gtc"

        with pytest.raises(shared_ers.MissingArgumentError):
            fx.place_order({"coin": "BTC", "side": "buy", "price": 1, "size": 1, "leverage": 20, "order_type": "FOK"})
        with pytest.raises(shared_ers.MissingArgumentError):
            fx.place_order({"coin": "BTC", "side": "buy", "price": 1, "leverage": 20})  # size missing
        with pytest.raises(shared_ers.MissingArgumentError):
            fx.place_order({"coin": "BTC", "side": "buy", "price": 1, "size": 1, "leverage": 20, "typo": True})

    def test_parse_order_id(self):
        parse = _FakeDispatcher()._hd._parse_order_id
        assert parse(42) == ("oid", 42)
        assert parse("42") == ("oid", 42)
        assert parse(CLOID) == ("cloid", CLOID)
        for bad in (True, "0xzz" * 16, "abc", 3.5):
            with pytest.raises(shared_ers.DispatcherError):
                parse(bad)

    def test_cancel_order_with_explicit_coin(self):
        fx = _FakeDispatcher(exchange=RecordingExchange())
        out = fx.cancel_order({"order_id": 42, "coin": "BTC"})
        assert fx.exchange.calls == [("cancel_by_oid", "BTC", 42)]
        assert out == {"coin": "BTC", "canceledOids": [42], "errors": []}

    def test_cancel_order_cloid_with_explicit_coin(self):
        fx = _FakeDispatcher(exchange=RecordingExchange())
        out = fx.cancel_order({"order_id": CLOID, "coin": "BTC"})
        assert fx.exchange.calls == [("cancel_by_cloid", "BTC", CLOID)]
        assert out["canceledOids"] == [7]

    class _StubStatusRest:
        def __init__(self, coin="BTC"):
            self.coin = coin
            self.lookups = []

        def get_order_status_detail(self, identifier):
            self.lookups.append(identifier)
            order = None
            if self.coin is not None:
                class _O:
                    pass
                order = _O()
                order.coin = self.coin
            status = _cls.OrderStatus(found=order is not None)
            status.order = order
            return status

    def test_cancel_order_resolves_coin_via_order_status(self):
        rest = self._StubStatusRest(coin="xyz:AAPL")
        fx = _FakeDispatcher(exchange=RecordingExchange(), account_rest=rest)
        out = fx.cancel_order({"order_id": 42})
        assert rest.lookups == [42]
        assert fx.exchange.calls == [("cancel_by_oid", "xyz:AAPL", 42)]
        assert out["coin"] == "xyz:AAPL"

    def test_cancel_order_unknown_order(self):
        rest = self._StubStatusRest(coin=None)
        fx = _FakeDispatcher(exchange=RecordingExchange(), account_rest=rest)
        with pytest.raises(shared_ers.DispatcherError, match="not found"):
            fx.cancel_order({"order_id": 42})
        assert fx.exchange.calls == []


# --- leverage, batch place / cancel (exchange layer) ----------------------------------------

def _recording_ex(response):
    ex = StubInfoExchange()
    posts = []

    def fake_post(url, json=None):
        posts.append(json)
        class R:
            def json(self_inner):
                return response(json) if callable(response) else response
        return R()

    ex.session.post = fake_post
    return ex, posts


OK_DEFAULT = {"status": "ok", "response": {"type": "default"}}


class TestUpdateLeverage:
    def test_wire_shape_and_key_order(self):
        ex, posts = _recording_ex(OK_DEFAULT)
        result = ex.update_leverage("xyz:AAPL", 5, is_cross=False)
        assert list(posts[0]["action"]) == ["type", "asset", "isCross", "leverage"]
        assert posts[0]["action"] == {"type": "updateLeverage", "asset": 110_000, "isCross": False, "leverage": 5}
        assert result.to_dict() == {"coin": "xyz:AAPL", "leverage": 5, "margin_mode": "isolated"}

    @pytest.mark.parametrize("bad", [True, 1.5, "5", 0, 21, -1])
    def test_rejects_bad_leverage_before_signing(self, bad):
        ex, posts = _recording_ex(OK_DEFAULT)
        with pytest.raises(_ers.HyperLiquidError):
            ex.update_leverage("BTC", bad, is_cross=True)
        assert posts == []

    def test_cross_on_isolated_only_rejected(self):
        ex, posts = _recording_ex(OK_DEFAULT)
        with pytest.raises(_ers.HyperLiquidError, match="isolated-only"):
            ex.update_leverage("xyz:GOLD", 5, is_cross=True)
        assert posts == []
        ex.update_leverage("xyz:GOLD", 5, is_cross=False)

    def test_venue_rejection_raises(self):
        ex, _ = _recording_ex({"status": "err", "response": "nope"})
        with pytest.raises(_ers.ExchangeActionError):
            ex.update_leverage("BTC", 5, True)


def _req(coin="BTC", **kw):
    base = dict(coin=coin, side="buy", price="100", size="1")
    base.update(kw)
    return _cls.OrderRequest.from_dict(base)


class TestPlaceOrders:
    def test_wire_shape_two_coins_and_positional_results(self):
        statuses = [{"resting": {"oid": 1}}, {"error": "Insufficient margin"}, {"filled": {"oid": 3, "avgPx": "9"}}]
        ex, posts = _recording_ex({"status": "ok", "response": {"data": {"statuses": statuses}}})
        results = ex.place_orders([_req("BTC"), _req("xyz:AAPL", cloid=CLOID), _req("ETH", order_type="IOC")])
        action = posts[0]["action"]
        assert list(action) == ["type", "orders", "grouping"] and action["grouping"] == "na"
        assert [o["a"] for o in action["orders"]] == [0, 110_000, 1]
        assert list(action["orders"][1]) == ["a", "b", "p", "s", "r", "t", "c"]
        assert len(posts) == 1
        assert [(r.coin, r.oid, r.error) for r in results] == [("BTC", 1, None), ("xyz:AAPL", None, "Insufficient margin"), ("ETH", 3, None)]
        assert results[2].status == "filled"

    def test_status_count_mismatch_raises(self):
        ex, _ = _recording_ex({"status": "ok", "response": {"data": {"statuses": [{"resting": {"oid": 1}}]}}})
        with pytest.raises(_ers.HyperLiquidError, match="cannot attribute"):
            ex.place_orders([_req(), _req()])

    def test_envelope_error_raises(self):
        ex, _ = _recording_ex({"status": "err", "response": "bad"})
        with pytest.raises(_ers.ExchangeActionError):
            ex.place_orders([_req()])

    def test_validation_sends_nothing(self):
        ex, posts = _recording_ex(OK_DEFAULT)
        for bad in ([], [_req()] * (_exch.MAX_BATCH_SIZE + 1),
                    [_req(cloid=CLOID), _req(cloid=CLOID)],
                    [_req(), _req(cloid="0xnothex")],
                    [_req(), _req("DOGE")]):
            with pytest.raises((_ers.HyperLiquidError, InvalidCoinError)):
                ex.place_orders(bad)
        assert posts == []

    def test_order_request_validation(self):
        for bad in ({"coin": "BTC"}, {"coin": "BTC", "side": "x", "price": 1, "size": 1},
                    {"coin": "BTC", "side": "buy", "price": 1, "size": 1, "order_type": "FOK"},
                    {"coin": "BTC", "side": "buy", "price": 1, "size": 1, "typo": 1}, "nope"):
            with pytest.raises(_ers.HyperLiquidError):
                _cls.OrderRequest.from_dict(bad)


class TestCancelMany:
    def test_mixed_batch_is_two_actions_and_keeps_caller_order(self):
        def respond(payload):
            n = len(payload["action"]["cancels"])
            statuses = ["success"] * n
            if payload["action"]["type"] == "cancelByCloid":
                statuses = [{"error": "Order was never placed, already canceled, or filled."}] * n
            return {"status": "ok", "response": {"data": {"statuses": statuses}}}
        ex, posts = _recording_ex(respond)
        result = ex.cancel_many([("BTC", 5), ("ETH", CLOID), ("xyz:AAPL", 6)])
        assert [p["action"]["type"] for p in posts] == ["cancel", "cancelByCloid"]
        assert [(o.order_id, o.coin, o.ok) for o in result.outcomes] == [("5", "BTC", True), (CLOID, "ETH", False), ("6", "xyz:AAPL", True)]
        assert (result.ok_count, result.error_count) == (2, 1)

    def test_failing_chunk_keeps_earlier_outcomes_and_continues(self):
        calls = {"n": 0}

        def respond(payload):
            calls["n"] += 1
            if calls["n"] == 2:
                return {"status": "err", "response": "boom"}
            return {"status": "ok", "response": {"data": {"statuses": ["success"] * len(payload["action"]["cancels"])}}}
        ex, posts = _recording_ex(respond)
        n = _exch.MAX_BATCH_SIZE * 2 + 1
        result = ex.cancel_many([("BTC", i) for i in range(1, n + 1)])
        assert len(posts) == 3
        flags = [o.ok for o in result.outcomes]
        assert flags[:40] == [True] * 40 and flags[40:80] == [False] * 40 and flags[80:] == [True]
        assert "boom" in result.outcomes[50].error

    def test_validates_before_signing(self):
        ex, posts = _recording_ex(OK_DEFAULT)
        with pytest.raises(_ers.HyperLiquidError):
            ex.cancel_many([])
        with pytest.raises(_ers.HyperLiquidError):
            ex.cancel_many([("BTC", 1), ("BTC", "0xbad")])
        assert posts == []

    def test_summary_truncates_failures_and_fits_p1(self):
        from argus.perpetuals.shared._classes import OutboundMessage
        outcomes = [_cls.CancelOutcome(str(i), "BTC", False, "x" * 100) for i in range(1000)]
        summary = _cls.BatchCancelResult(outcomes).to_summary_dict()
        assert summary["failed"] == 1000 and len(summary["failures"]) == 50 and summary["failures_truncated"]
        OutboundMessage(action="cancel_all_orders", data=summary).convert_to_protocol_1()

    def test_place_multiple_response_fits_p1_worst_case(self):
        from argus.perpetuals.shared._classes import OutboundMessage
        lev = _cls.PositionLeverage("cross", 20)
        results = []
        for i in range(_exch.MAX_BATCH_SIZE):
            r = _cls.OrderPlacementResult(coin="xyz:LONGNAME", error="e" * 200, price=Decimal("123456.78901234"),
                                          requested_price=Decimal("123456.78901234"))
            r.leverage_report = _cls.OrderLeverage(lev, lev, changed=True, reverted=True, revert_error="r" * 100)
            results.append(r.to_dict())
        OutboundMessage(action="place_multiple_orders",
                        data={"results": results, "ok_count": 0, "error_count": 40}).convert_to_protocol_1()


# --- dispatcher: leverage + order handlers ---------------------------------------------------------

class TestLeverageHandlers:
    def test_leverage_is_required(self):
        fx = _FakeDispatcher(exchange=RecordingExchange())
        with pytest.raises(shared_ers.MissingArgumentError, match="leverage"):
            fx.place_order({"coin": "BTC", "side": "buy", "price": 1, "size": 1})
        assert fx.exchange.calls == [] and fx.rest.reads == []

    def test_applies_leverage_and_reports_it(self):
        fx = _FakeDispatcher(exchange=RecordingExchange())  # current: 20x cross
        out = fx.place_order({"coin": "BTC", "side": "buy", "price": 100, "size": 2, "leverage": 10})
        assert [c[0] for c in fx.exchange.calls] == ["update_leverage", "place_order"]
        assert fx.exchange.calls[0] == ("update_leverage", "BTC", 10, True)
        assert out["leverage"] == {"type": "cross", "value": 10} and out["margin_mode"] == "cross"
        assert out["previous_leverage"] == {"type": "cross", "value": 20}
        assert out["leverage_changed"] is True and out["leverage_reverted"] is False
        assert out["estimated_initial_margin"] == "20"  # 100 * 2 / 10

    def test_unchanged_leverage_sends_no_update(self):
        fx = _FakeDispatcher(exchange=RecordingExchange())
        out = fx.place_order({"coin": "BTC", "side": "buy", "price": 100, "size": 1, "leverage": 20})
        assert [c[0] for c in fx.exchange.calls] == ["place_order"] and out["leverage_changed"] is False

    def test_reduce_only_has_no_margin_estimate(self):
        fx = _FakeDispatcher(exchange=RecordingExchange())
        out = fx.place_order({"coin": "BTC", "side": "sell", "price": 100, "size": 1, "leverage": 5, "reduce_only": True})
        assert out["estimated_initial_margin"] is None

    def test_margin_mode_overrides_and_defaults_to_current(self):
        fx = _FakeDispatcher(exchange=RecordingExchange(), rest=RecordingRest({"BTC": _cls.PositionLeverage("isolated", 3)}))
        fx.place_order({"coin": "BTC", "side": "buy", "price": 1, "size": 1, "leverage": 4})
        assert fx.exchange.calls[0] == ("update_leverage", "BTC", 4, False)  # kept isolated
        fx = _FakeDispatcher(exchange=RecordingExchange())
        fx.place_order({"coin": "BTC", "side": "buy", "price": 1, "size": 1, "leverage": 4, "margin_mode": "isolated"})
        assert fx.exchange.calls[0] == ("update_leverage", "BTC", 4, False)

    def test_venue_rejection_reverts_leverage(self):
        rejected = _cls.OrderPlacementResult(coin="BTC", error="Insufficient margin", price=Decimal("1"))
        fx = _FakeDispatcher(exchange=RecordingExchange(result=rejected))
        out = fx.place_order({"coin": "BTC", "side": "buy", "price": 1, "size": 1, "leverage": 10})
        assert [c[:4] for c in fx.exchange.calls if c[0] == "update_leverage"] == [
            ("update_leverage", "BTC", 10, True), ("update_leverage", "BTC", 20, True)]
        assert out["error"] and out["leverage_reverted"] is True
        assert out["leverage"] == {"type": "cross", "value": 20} and out["estimated_initial_margin"] is None

    def test_definitive_exception_reverts_and_reraises(self):
        ex = RecordingExchange(place_error=_ers.ExchangeActionError("rejected"))
        fx = _FakeDispatcher(exchange=ex)
        with pytest.raises(_ers.ExchangeActionError):
            fx.place_order({"coin": "BTC", "side": "buy", "price": 1, "size": 1, "leverage": 10})
        assert ex.calls[-1] == ("update_leverage", "BTC", 20, True)
        assert fx.fatal == []  # an expected error never runs the contingency

    def test_ambiguous_exception_keeps_leverage_and_runs_contingency(self):
        ex = RecordingExchange(place_error=TimeoutError("read timed out"))
        fx = _FakeDispatcher(exchange=ex)
        with pytest.raises(TimeoutError):
            fx.place_order({"coin": "BTC", "side": "buy", "price": 1, "size": 1, "leverage": 10})
        assert [c[0] for c in ex.calls] == ["update_leverage", "place_order"]  # order may be live: no revert
        assert len(fx.fatal) == 1 and fx.fatal[0]["function"] == "place_order"
        assert not fx.order_execution_blocked  # the contingency never trips the kill switch

    def test_revert_failure_is_fatal(self):
        class Flaky(RecordingExchange):
            def update_leverage(self, coin, leverage, is_cross):
                if leverage == 20:
                    raise _ers.ExchangeActionError("cannot revert")
                return super().update_leverage(coin, leverage, is_cross)
        fx = _FakeDispatcher(exchange=Flaky(place_error=_ers.ExchangeActionError("rejected")))
        with pytest.raises(_ers.LeverageRevertError):
            fx.place_order({"coin": "BTC", "side": "buy", "price": 1, "size": 1, "leverage": 10})
        assert len(fx.fatal) == 1

    def test_failed_leverage_read_places_nothing(self):
        fx = _FakeDispatcher(exchange=RecordingExchange(), rest=RecordingRest(fail=True))
        with pytest.raises(RuntimeError):
            fx.place_order({"coin": "BTC", "side": "buy", "price": 1, "size": 1, "leverage": 10})
        assert fx.exchange.calls == []

    def test_set_and_get_leverage(self):
        fx = _FakeDispatcher(exchange=RecordingExchange())
        out = fx.call("_set_leverage", {"coin": "BTC", "leverage": 7, "margin_mode": "isolated"})
        assert out == {"coin": "BTC", "leverage": 7, "margin_mode": "isolated",
                       "previous": {"type": "cross", "value": 20}}
        got = fx.call("_get_leverage", {"coin": "xyz:GOLD"})
        assert got["max_leverage"] == 10 and got["allowed_margin_modes"] == ["isolated"] and got["dex"] == "xyz"
        with pytest.raises(shared_ers.MissingArgumentError):
            fx.call("_set_leverage", {"coin": "BTC"})


def _ok(coin, oid):
    return _cls.OrderPlacementResult(coin=coin, oid=oid, status="resting", price=Decimal("100"))


def _bad(coin):
    return _cls.OrderPlacementResult(coin=coin, error="no", price=Decimal("100"))


class TestPlaceMultipleHandler:
    def _o(self, coin="BTC", leverage=10, **kw):
        return dict(coin=coin, side="buy", price="100", size="1", leverage=leverage, **kw)

    def test_applies_per_coin_and_reports_positionally(self):
        ex = RecordingExchange(results=[_ok("BTC", 1), _ok("ETH", 2)])
        fx = _FakeDispatcher(exchange=ex)
        out = fx.call("_place_multiple_orders", {"orders": [self._o("BTC", 10), self._o("ETH", 5)]})
        assert out["ok_count"] == 2 and out["error_count"] == 0
        assert [r["leverage"]["value"] for r in out["results"]] == [10, 5]
        assert [c[:3] for c in ex.calls if c[0] == "update_leverage"] == [("update_leverage", "BTC", 10), ("update_leverage", "ETH", 5)]

    def test_coin_with_no_accepted_order_is_reverted_others_kept(self):
        ex = RecordingExchange(results=[_ok("BTC", 1), _bad("ETH")])
        fx = _FakeDispatcher(exchange=ex)
        out = fx.call("_place_multiple_orders", {"orders": [self._o("BTC", 10), self._o("ETH", 5)]})
        assert out["results"][0]["leverage_reverted"] is False and out["results"][1]["leverage_reverted"] is True
        assert ex.calls[-1] == ("update_leverage", "ETH", 20, True)

    def test_validation_changes_nothing(self):
        ex = RecordingExchange(results=[])
        fx = _FakeDispatcher(exchange=ex)
        for orders in ([], "x", [self._o("BTC", 10), self._o("BTC", 5)],
                       [self._o(cloid=CLOID), self._o(cloid=CLOID)],
                       [{"coin": "BTC", "side": "buy", "price": 1, "size": 1}],
                       [self._o()] * (_exch.MAX_BATCH_SIZE + 1)):
            with pytest.raises((shared_ers.DispatcherError, _ers.HyperLiquidError)):
                fx.call("_place_multiple_orders", {"orders": orders})
        assert ex.calls == [] and fx.rest.reads == []


class TestKillSwitchAndCancelHandlers:
    BLOCKED = [("_place_order", {"coin": "BTC", "side": "buy", "price": 1, "size": 1, "leverage": 5}),
               ("_place_multiple_orders", {"orders": []}),
               ("_set_leverage", {"coin": "BTC", "leverage": 5})]

    @pytest.mark.parametrize("handler,data", BLOCKED)
    def test_blocked_before_anything_is_touched(self, handler, data):
        ex = RecordingExchange()
        fx = _FakeDispatcher(exchange=ex, blocked=True)
        with pytest.raises(shared_ers.OrderExecutionDisabledError):
            fx.call(handler, data)
        assert ex.calls == [] and fx.rest.reads == [] and fx.fatal == []

    def test_cancels_and_reads_are_never_blocked(self):
        class Rest:
            def get_open_orders(self, dex):
                class O:
                    order_id, name, client_order_id = "9", "BTC", None
                return [O()]
        ex = RecordingExchange()
        fx = _FakeDispatcher(exchange=ex, account_rest=Rest(), blocked=True)
        fx.call("_cancel_order", {"order_id": 1, "coin": "BTC"})
        fx.call("_cancel_multiple_orders", {"orders": [{"order_id": 9}]})
        fx.call("_cancel_all_orders", {})
        fx.call("_get_leverage", {"coin": "BTC"})

    def test_toggle_and_env_flag(self):
        fx = _FakeDispatcher()
        assert fx.order_execution_blocked is False
        assert fx._toggle_block_order_execution() is True and fx.order_execution_blocked
        assert fx._toggle_block_order_execution() is False

    def test_cancel_multiple_resolves_coins_with_one_read_and_flags_unknown(self):
        reads = []

        class Rest:
            def get_open_orders(self, dex):
                reads.append(dex)
                def o(oid, name, cloid=None):
                    class O:
                        order_id, client_order_id = oid, cloid
                    O.name = name
                    return O()
                return [o("9", "BTC"), o("10", "xyz:AAPL", CLOID)]
        ex = RecordingExchange()
        fx = _FakeDispatcher(exchange=ex, account_rest=Rest())
        out = fx.call("_cancel_multiple_orders", {"orders": [{"order_id": 9}, {"order_id": CLOID}, {"order_id": 77}]})
        assert reads == [None]
        assert ex.calls == [("cancel_many", [("BTC", 9), ("xyz:AAPL", CLOID)])]
        assert [o["ok"] for o in out["outcomes"]] == [True, True, False]
        assert out["outcomes"][2]["error"] == "not found among open orders"

    def test_cancel_multiple_with_coins_does_no_lookup(self):
        ex = RecordingExchange()
        fx = _FakeDispatcher(exchange=ex)  # no account_rest: a lookup would blow up
        out = fx.call("_cancel_multiple_orders", {"orders": [{"order_id": 1, "coin": "BTC"}]})
        assert out["ok_count"] == 1

    def test_cancel_all_filters_by_coin(self):
        class Rest:
            def get_open_orders(self, dex):
                def o(oid, name):
                    class O:
                        order_id = oid
                    O.name = name
                    return O()
                return [o("1", "BTC"), o("2", "ETH")]
        ex = RecordingExchange()
        fx = _FakeDispatcher(exchange=ex, account_rest=Rest())
        out = fx.call("_cancel_all_orders", {"coin": "ETH"})
        assert ex.calls == [("cancel_many", [("ETH", 2)])]
        assert (out["requested"], out["canceled"], out["failed"]) == (1, 1, 0)
        assert fx.call("_cancel_all_orders", {"coin": "SOL"})["requested"] == 0


# --- pinned vector maintenance ----------------------------------------------------------
# To re-pin: run the test file's module bottom (or compute manually) and paste the hex.
