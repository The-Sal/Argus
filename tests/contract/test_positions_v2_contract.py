"""Data API v2 positions must map onto exactly the same client-visible shape as v1 (positions golden)."""
import dataclasses
import json
from pathlib import Path

from contract_schema import check

from argus.polymarket_direct.order_types import PositionData

GOLDEN = Path(__file__).parent / "golden"
FIXTURE = Path(__file__).parent.parent / "fixtures" / "data_api" / "positions_v2_sample.json"


def _v2_rows():
    raw = json.loads(FIXTURE.read_text())
    return raw["data"] if isinstance(raw, dict) else raw


def test_v2_positions_conform_to_positions_golden():
    schema = json.loads((GOLDEN / "positions.json").read_text())
    out = [dataclasses.asdict(p) for p in PositionData.from_v2_list(_v2_rows()).positions]
    assert out
    assert check(out, schema) == []


def test_v2_positions_with_nulls_and_numeric_ids_conform():
    schema = json.loads((GOLDEN / "positions.json").read_text())
    row = dict(_v2_rows()[0])
    row.update(event_id=12345, icon=None, end_date=None, opposite_token_id=None, current_price=None)
    out = [dataclasses.asdict(p) for p in PositionData.from_v2_list([row]).positions]
    assert check(out, schema) == []
