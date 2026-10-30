import pytest

from argus.polymarket_direct.position_ids import (
    decode_v2_position_id,
    is_v2_position_id,
)

V2_YES = 495099235152725588201991226912420825532590817323357329480139627250245959680
V2_NO = V2_YES + 1
V1 = 54533043819946592547517511176940999955633860128497669742211153063842200957669


def test_v2_detected_str_and_int():
    for v in (V2_YES, V2_NO, str(V2_YES), str(V2_NO)):
        assert is_v2_position_id(v)


def test_v1_not_detected():
    assert not is_v2_position_id(V1)
    assert not is_v2_position_id(str(V1))


@pytest.mark.parametrize("bad", ["", "abc", "0x12", "-5", "0", 0, -1, None, 1.5, 2**256])
def test_garbage_is_not_v2(bad):
    assert not is_v2_position_id(bad)


def test_decode_yes_no():
    yes = decode_v2_position_id(str(V2_YES))
    no = decode_v2_position_id(V2_NO)
    assert yes.module_id == 1  # Binary
    assert yes.outcome_index == 0 and no.outcome_index == 1
    assert yes.condition_id == no.condition_id
    assert len(yes.condition_id) == 31
    assert int.from_bytes(yes.condition_id, "big") << 8 == V2_YES & ~0xFF
    assert yes.event_id == V2_YES & ~0xFFFFFF
    assert yes.condition_index == (V2_YES >> 8) & 0xFFFF
    assert yes.arity == (V2_YES >> 104) & 0xFFFF
    assert yes.position_id == V2_YES


def test_decode_v1_raises():
    with pytest.raises(ValueError):
        decode_v2_position_id(V1)
