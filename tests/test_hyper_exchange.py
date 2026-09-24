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
from argus.perpetuals.shared import _errors as shared_ers
from argus.perpetuals.shared._errors import InvalidCoinError

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

    def test_more_decimals_for_low_precision_size(self):
        # szDecimals=2 -> up to 4 price decimals; szDecimals=0 -> up to 6.
        ex = StubInfoExchange()
        assert ex.round_price("xyz:AAPL", "1.23456789") == Decimal("1.2346")

    def test_unknown_coin(self):
        with pytest.raises(InvalidCoinError):
            self._ex().round_price("DOGE", 1)


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
                return {"universe": [{"name": "AAPL", "szDecimals": 2},
                                     {"name": "GOLD", "szDecimals": 2}]}
            if dex == "flx":
                return {"universe": [{"name": "NVDA", "szDecimals": 2}]}
            return {"universe": [{"name": name, "szDecimals": 5} for name in DEFAULT_UNIVERSE]}
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
        assert r.to_dict() == {"coin": "BTC", "oid": 42, "status": "resting", "avgPx": None, "error": None}

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
    """Binds the real handler functions to a stubbed exchange/account_rest."""

    def __init__(self, exchange=None, account_rest=None):
        from argus.perpetuals.hyper import HyperLiquidDispatcher
        self._hd = HyperLiquidDispatcher
        self.exchange = exchange or StubInfoExchange()
        self.account_rest = account_rest

    def _read_args(self, args, *accepted):
        return self._hd._read_args(args, *accepted)  # staticmethod from the shared mixin

    def _parse_order_id(self, value):
        return self._hd._parse_order_id(value)

    def place_order(self, data):
        return self._hd._place_order(self, ArgsObject(sock=None, args=data))

    def cancel_order(self, data):
        return self._hd._cancel_order(self, ArgsObject(sock=None, args=data))


class RecordingExchange:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or _cls.OrderPlacementResult(coin="BTC", oid=1, status="resting")

    def place_order(self, **kwargs):
        self.calls.append(("place_order", kwargs))
        return self.result

    def cancel_by_oid(self, coin, oid):
        self.calls.append(("cancel_by_oid", coin, oid))
        return _cls.CancelResult(coin=coin, canceled_oids=[oid])

    def cancel_by_cloid(self, coin, cloid):
        self.calls.append(("cancel_by_cloid", coin, cloid))
        return _cls.CancelResult(coin=coin, canceled_oids=[7])


class TestDispatcherHandlers:
    def test_place_order_maps_args(self):
        fx = _FakeDispatcher(exchange=RecordingExchange())
        out = fx.place_order({
            "coin": "BTC", "side": "buy", "price": "50000.5", "size": 0.001,
            "order_type": "ioc", "reduce_only": True, "cloid": CLOID,
        })
        (name, kwargs), = fx.exchange.calls
        assert name == "place_order"
        assert kwargs["tif"] == "Ioc" and kwargs["reduce_only"] is True and kwargs["cloid"] == CLOID
        assert out["coin"] == "BTC" and out["oid"] == 1

    def test_place_order_defaults_and_validation(self):
        fx = _FakeDispatcher(exchange=RecordingExchange())
        fx.place_order({"coin": "BTC", "side": "sell", "price": 1, "size": 1})
        assert fx.exchange.calls[0][1]["tif"] == "Gtc"

        with pytest.raises(shared_ers.MissingArgumentError):
            fx.place_order({"coin": "BTC", "side": "buy", "price": 1, "size": 1, "order_type": "FOK"})
        with pytest.raises(shared_ers.MissingArgumentError):
            fx.place_order({"coin": "BTC", "side": "buy", "price": 1})  # size missing
        with pytest.raises(shared_ers.MissingArgumentError):
            fx.place_order({"coin": "BTC", "side": "buy", "price": 1, "size": 1, "typo": True})

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


# --- pinned vector maintenance ----------------------------------------------------------
# To re-pin: run the test file's module bottom (or compute manually) and paste the hex.
