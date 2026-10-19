"""
Polymarket Protocol V2 position-id helpers.

V2 markets trade through ExchangeV3 and their ``clobTokenIds`` are structured
``positionIds`` rather than keccak-derived CTF token ids. The layout (uint256):

    bits 248..255  moduleId       (1 = Binary, 2 = NegRisk, 3 = Combinatorial)
    bits 104..119  arity
    bits  40..103  reserved, MUST be zero for a v2 position id
    bits   8..23   conditionIndex
    bits   0..7    outcomeIndex

A v1 / CTF token id is a keccak hash, so the chance of its reserved bits being
all zero is 2**-64; this mirrors ``is_v2_position_id`` in the official
polymarket-client SDK.
"""
from dataclasses import dataclass

_V2_RESERVED_BITS_MASK = ((1 << 64) - 1) << 40
_UINT256_MAX = (1 << 256) - 1

MODULE_BINARY = 1
MODULE_NEG_RISK = 2
MODULE_COMBINATORIAL = 3


@dataclass(frozen=True)
class V2PositionId:
    position_id: int
    module_id: int
    outcome_index: int
    condition_index: int
    arity: int
    condition_id: bytes  # bytes31 (id with bits 0..7 cleared, top 31 bytes)
    event_id: int  # id with bits 0..23 cleared


def _to_int(value: "str | int") -> "int | None":
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        n = value
    elif isinstance(value, str):
        s = value.strip()
        if not s.isascii() or not s.isdigit():
            return None
        n = int(s)
    else:
        return None
    if n <= 0 or n > _UINT256_MAX:
        return None
    return n


def is_v2_position_id(value: "str | int") -> bool:
    """True if ``value`` (decimal string or int) is a Protocol V2 position id."""
    n = _to_int(value)
    if n is None:
        return False
    return (n & _V2_RESERVED_BITS_MASK) == 0


def decode_v2_position_id(value: "str | int") -> V2PositionId:
    """Decode a v2 position id. Raises ValueError if it is not one."""
    n = _to_int(value)
    if n is None or (n & _V2_RESERVED_BITS_MASK) != 0:
        raise ValueError("not a Polymarket v2 position id: {!r}".format(value))
    condition_int = n & ~0xFF
    # bytes31: the 32-byte big-endian word with the (zeroed) low byte dropped
    condition_id = (condition_int >> 8).to_bytes(31, "big")
    return V2PositionId(
        position_id=n,
        module_id=n >> 248,
        outcome_index=n & 0xFF,
        condition_index=(n >> 8) & 0xFFFF,
        arity=(n >> 104) & 0xFFFF,
        condition_id=condition_id,
        event_id=n & ~0xFFFFFF,
    )
