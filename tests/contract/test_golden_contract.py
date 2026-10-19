"""Current handler/converter output vs the committed goldens."""
import base64
import json
import zlib
from pathlib import Path

import pytest

from contract_fixtures import build_instances
from contract_schema import check, infer_many

GOLDEN = Path(__file__).parent / "golden"
EXAMPLES = GOLDEN / "examples"

# Every response type the Rust SDK parses (socket.rs / data_and_encoders.rs).
EXPECTED = {
    "positions", "orders", "order_status", "account_update_order", "account_update_trade",
    "place_order", "place_multiple_orders", "cancel_order", "cancel_multiple_orders",
    "subscribe", "clob_info", "balance", "token_balance", "price_to_beat", "search_markets",
    "envelope_ok", "envelope_error", "envelope_compressed",
}


@pytest.fixture(scope="module")
def instances():
    return build_instances()


def load(name):
    return json.loads((GOLDEN / f"{name}.json").read_text())


def test_golden_set_is_complete(instances):
    assert set(instances) == EXPECTED
    assert {p.stem for p in GOLDEN.glob("*.json")} == EXPECTED


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_schema_equals_golden(name, instances):
    """Key set, JSON types and nullability all identical (exact schema equality)."""
    assert infer_many(instances[name]) == load(name)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_instance_conforms(name, instances):
    schema = load(name)
    for inst in instances[name]:
        assert check(inst, schema) == []


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_examples_match_current_output(name, instances):
    for i, inst in enumerate(instances[name]):
        f = f"{name}.json" if i == 0 else f"{name}__variant{i}.json"
        assert json.loads((EXAMPLES / f).read_text()) == inst
        assert check(json.loads((EXAMPLES / f).read_text()), load(name)) == []


def _top_keys(name):
    return set(load(name)["keys"])


def test_explicit_sdk_key_sets():
    """Hand-written mirror of the Rust structs, independent of the generator."""
    assert _top_keys("place_order") == {"errorMsg", "orderID", "takingAmount",
                                        "makingAmount", "status", "success"}
    assert _top_keys("place_multiple_orders") == {"success", "failed"}
    assert _top_keys("cancel_order") == {"canceled", "not_canceled"}
    assert _top_keys("cancel_multiple_orders") == {"canceled", "not_canceled"}
    assert _top_keys("subscribe") == {"subscribed", "failed"}
    assert _top_keys("clob_info") == {"event_name", "market_name", "outcome", "ticker",
                                      "market_slug", "aot_p2_symbol"}
    assert set(load("order_status")["keys"]) == set(load("orders")["items"]["keys"]) == {
        "id", "status", "owner", "maker_address", "market", "asset_id", "side",
        "original_size", "size_matched", "price", "outcome", "expiration", "order_type",
        "associate_trades", "created_at"}
    assert set(load("positions")["items"]["keys"]) == {
        "proxy_wallet", "asset", "condition_id", "size", "avg_price", "initial_value",
        "current_value", "cash_pnl", "percent_pnl", "total_bought", "realized_pnl",
        "percent_realized_pnl", "cur_price", "redeemable", "mergeable", "title", "slug",
        "icon", "event_id", "event_slug", "outcome", "outcome_index", "opposite_outcome",
        "opposite_asset", "end_date", "negative_risk"}
    for n in ("envelope_ok", "envelope_error", "envelope_compressed"):
        assert {"action", "data", "error", "compressed", "correlation_id"} == _top_keys(n)
    for n in ("account_update_order", "account_update_trade"):
        assert {"action", "data", "error", "compressed"} == _top_keys(n)


def test_scalar_types():
    assert load("balance")["type"] == "float"
    assert load("token_balance")["type"] == "float"
    assert load("price_to_beat")["type"] == "float"
    s = load("search_markets")
    assert s["type"] == "array" and s["items"]["type"] == "string"
    pos = load("positions")["items"]["keys"]
    for k in ("size", "avg_price", "realized_pnl", "cur_price"):  # ints in upstream -> float
        assert pos[k]["type"] == "float"
    assert pos["outcome_index"]["type"] == "int"
    assert pos["redeemable"]["type"] == "bool"
    assert load("place_order")["keys"]["success"]["type"] == "bool"
    assert load("orders")["items"]["keys"]["created_at"]["type"] == "int"


def test_nullability_locked():
    assert load("clob_info")["keys"]["outcome"]["nullable"] is True
    for n in ("envelope_ok", "envelope_compressed"):
        assert load(n)["keys"]["error"]["type"] == "null"   # success: error is null
    assert load("envelope_error")["keys"]["data"]["nullable"] is True
    assert load("envelope_error")["keys"]["error"]["type"] == "string"
    assert load("envelope_ok")["keys"]["data"]["type"] == "float"


def test_compressed_envelope_roundtrip(instances):
    """Mirror of Rust maybe_decompress_p1: base64 -> zlib -> JSON."""
    env = instances["envelope_compressed"][0]
    assert env["compressed"] is True and isinstance(env["data"], str)
    data = json.loads(zlib.decompress(base64.b64decode(env["data"])))
    assert isinstance(data, list) and data and all(isinstance(x, str) for x in data)
    assert check(data, load("search_markets")) == []
