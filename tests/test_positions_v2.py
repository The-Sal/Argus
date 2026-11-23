"""Offline tests for the Data API v2 positions mapping (PolymarketPosition.from_v2_dict)
and PolyRestAPI.get_positions (cursor pagination, v1 param translation).

The Rust SDK deserializes dataclasses.asdict(PolymarketPosition): 26 required, never-None fields.
"""
import copy
import dataclasses
import json
import pathlib
import types

import pytest

from argus.polymarket_direct import rest
from argus.polymarket_direct.order_types import PolymarketPosition, PositionData

FIX = pathlib.Path(__file__).parent / "fixtures" / "data_api"

STR_FIELDS = ["proxy_wallet", "asset", "condition_id", "title", "slug", "icon", "event_id", "event_slug",
              "outcome", "opposite_outcome", "opposite_asset", "end_date"]
FLOAT_FIELDS = ["size", "avg_price", "initial_value", "current_value", "cash_pnl", "percent_pnl", "total_bought",
                "realized_pnl", "percent_realized_pnl", "cur_price"]
BOOL_FIELDS = ["redeemable", "mergeable", "negative_risk"]
INT_FIELDS = ["outcome_index"]


def assert_contract(pos: PolymarketPosition):
    d = dataclasses.asdict(pos)
    assert len(d) == 26
    for k in STR_FIELDS:
        assert type(d[k]) is str, k
    for k in FLOAT_FIELDS:
        assert type(d[k]) is float, k
    for k in BOOL_FIELDS:
        assert type(d[k]) is bool, k
    for k in INT_FIELDS:
        assert type(d[k]) is int, k


def load(name):
    return json.loads((FIX / name).read_text())


V2_ROW = {
    "proxy_wallet": "0xabc", "token_id": "1" * 75, "condition_id": "0x" + "a" * 64,
    "current_size": 10, "avg_price": 0.5, "entry_cost_usdc": 5, "entry_fees_usdc": 0.1, "total_cost_usdc": 5.1,
    "current_price": 0.6, "current_value": 6, "total_size": 12, "realized_pnl": 1, "unrealized_pnl": 1,
    "total_pnl": 2, "percent_pnl": 20, "percent_realized_pnl": 15, "status": "OPEN", "redeemable": False,
    "mergeable": True, "negative_risk": False, "archived": False, "title": "T", "slug": "s", "icon": "i",
    "event_id": "42", "event_slug": "es", "outcome": "Yes", "outcome_index": 0, "opposite_outcome": "No",
    "opposite_token_id": "2" * 75, "end_date": "2026-10-10", "last_event_at": 1, "first_entry_at": 1,
    "name": "n", "profile_image": "", "verified": False,
}


def test_fixture_v2_maps_to_v1_values():
    v1 = load("positions_v1_sample.json")
    v2 = load("positions_v2_sample.json")["data"]
    assert len(v1) == len(v2) and v1
    m1 = {r["asset"]: PolymarketPosition.from_dict(r) for r in v1}
    for row in v2:
        p = PolymarketPosition.from_v2_dict(row)
        assert_contract(p)
        a, b = dataclasses.asdict(m1[p.asset]), dataclasses.asdict(p)
        for k in a:
            if isinstance(a[k], float):
                assert b[k] == pytest.approx(a[k], rel=1e-3, abs=1e-3), k
            else:
                assert a[k] == b[k], k


def test_explicit_mapping():
    p = PolymarketPosition.from_v2_dict(V2_ROW)
    assert_contract(p)
    assert (p.asset, p.opposite_asset) == ("1" * 75, "2" * 75)
    assert (p.size, p.initial_value, p.cash_pnl, p.total_bought, p.cur_price) == (10.0, 5.0, 1.0, 12.0, 0.6)
    assert p.mergeable is True and p.redeemable is False


def test_missing_and_none_fields_fall_back_to_contract_types():
    p = PolymarketPosition.from_v2_dict({})
    assert_contract(p)
    d = dataclasses.asdict(p)
    assert all(v is not None for v in d.values())
    assert p.outcome_index == 999 and p.title == "" and p.size == 0.0 and p.redeemable is False

    row = {k: None for k in V2_ROW}
    p = PolymarketPosition.from_v2_dict(row)
    assert_contract(p)


def test_numeric_event_id_and_numeric_ids_become_str():
    row = dict(V2_ROW, event_id=1090172, event_slug=None, outcome_index="1", current_size="3.5")
    p = PolymarketPosition.from_v2_dict(row)
    assert p.event_id == "1090172" and type(p.event_id) is str
    assert p.outcome_index == 1 and p.size == 3.5
    assert PolymarketPosition.from_v2_dict(dict(V2_ROW, event_id=12.0)).event_id == "12"
    assert_contract(p)


def test_75_digit_asset_ids_are_not_mangled():
    big = "9" * 75
    p = PolymarketPosition.from_v2_dict(dict(V2_ROW, token_id=big, opposite_token_id=big[:-1] + "8"))
    assert p.asset == big and len(p.asset) == 75
    assert p.opposite_asset.endswith("8")
    # even if a numeric id slipped through as an int, it stays exact
    p = PolymarketPosition.from_v2_dict(dict(V2_ROW, token_id=int(big)))
    assert p.asset == big
    assert json.loads(json.dumps(dataclasses.asdict(p)))["asset"] == big


def test_mix_of_v1_and_v2_positions():
    v1 = load("positions_v1_sample.json")[0]
    v2 = load("positions_v2_sample.json")["data"][0]
    data = PositionData(positions=[PolymarketPosition.from_dict(v1), PolymarketPosition.from_v2_dict(v2),
                                   PolymarketPosition.from_v2_dict(V2_ROW)])
    assert len(data) == 3
    for pos in data.positions:
        assert_contract(pos)
    assert list(dataclasses.asdict(data.positions[0])) == list(dataclasses.asdict(data.positions[1]))
    assert data.to_dict()[0]["proxyWallet"]
    json.loads(data.to_json())


# ---------------- get_positions ----------------

class FakeResp:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


class FakeSession:
    def __init__(self, pages):
        self.pages = pages  # cursor(None for first) -> body
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, dict(params)))
        return FakeResp(copy.deepcopy(self.pages[params.get("cursor")]))


def make_stub(pages):
    return types.SimpleNamespace(raw_session=FakeSession(pages), proxy_funder="0xfunder", fatal_callback=None)


def row(i, **kw):
    return dict(V2_ROW, token_id=str(i) * 75, **kw)


def page(rows, nxt):
    return {"data": rows, "pagination": {"limit": 2, "offset": 0, "has_more": bool(nxt), "next_cursor": nxt}}


def test_get_positions_follows_cursor_and_resends_filters():
    stub = make_stub({
        None: page([row(1), row(2)], "c1"),
        "c1": page([row(3), row(4)], "c2"),
        "c2": page([row(5)], None),
    })
    res = rest.PolyRestAPI.get_positions(stub, title="foo")
    assert [p.asset[0] for p in res.positions] == list("12345")
    calls = stub.raw_session.calls
    assert len(calls) == 3
    assert all(c[0].endswith("/v2/positions") for c in calls)
    assert all(c[1]["user"] == "0xfunder" and c[1]["title"] == "foo" for c in calls)
    assert [c[1].get("cursor") for c in calls] == [None, "c1", "c2"]
    for p in res.positions:
        assert_contract(p)


def test_get_positions_empty_and_null_data():
    stub = make_stub({None: {"data": None, "pagination": {"has_more": False, "next_cursor": None}}})
    assert len(rest.PolyRestAPI.get_positions(stub)) == 0


def test_get_positions_translates_v1_params():
    stub = make_stub({None: page([row(1)], None)})
    rest.PolyRestAPI.get_positions(stub, user="0xu", market="0xm", eventId="7", sizeThreshold=1,
                                   sortBy="CURRENT", sortDirection="DESC", redeemable=True)
    q = stub.raw_session.calls[0][1]
    assert q["user"] == "0xu" and q["condition"] == "0xm" and q["event_id"] == "7"
    assert q["filter_type"] == "TOKENS" and q["filter_amount"] == 1
    assert q["sort_by"] == "CURRENT_VALUE" and q["sort_direction"] == "DESC" and q["status"] == "REDEEMABLE"
    assert not {"market", "eventId", "sizeThreshold", "sortBy", "redeemable"} & q.keys()


def test_get_positions_local_filters_and_limit():
    rows = [row(1, redeemable=True), row(2, redeemable=False), row(3, redeemable=False), row(4, redeemable=False)]
    stub = make_stub({None: page(rows, None)})
    res = rest.PolyRestAPI.get_positions(stub, redeemable=False, limit=2, offset=1)
    assert [p.asset[0] for p in res.positions] == ["3", "4"]
    assert "status" not in stub.raw_session.calls[0][1]
    assert stub.raw_session.calls[0][1]["limit"] == 2


def test_get_positions_v2_params_pass_through():
    stub = make_stub({None: page([], None)})
    rest.PolyRestAPI.get_positions(stub, status="CLOSED", filter_amount=5)
    q = stub.raw_session.calls[0][1]
    assert q["status"] == "CLOSED" and q["filter_amount"] == 5 and q["limit"] == 1000


def test_get_positions_default_size_floor_matches_v1():
    stub = make_stub({None: page([], None)})
    rest.PolyRestAPI.get_positions(stub)
    assert stub.raw_session.calls[0][1]["filter_amount"] == 1
    stub = make_stub({None: page([], None)})
    rest.PolyRestAPI.get_positions(stub, sizeThreshold=0)
    assert stub.raw_session.calls[0][1]["filter_amount"] == 0
