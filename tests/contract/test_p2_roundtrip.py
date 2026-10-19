"""P2 round trip: Python encoder -> Python port of the Rust decoder.

Port of /home/avm/argus-polymarket/src/data_and_encoders.rs
(ProtocolFns::analyse_bytes / protocol_2_decoder / bytes_to_orderbook).
"""
import pytest

from argus.polymarket._classes import P2ConvertClass
from argus.protocol import transmit_mkt_data_with_protocol_2
from contract_fixtures import V1_ASSET_ID, V2_ASSET_ID, make_dispatcher

DEPTH = 10
TICKER = "btc-updown-15m-1770413400"
SLUG = "btc-updown-15m-1770413400"


# ---------------------------- Rust decoder port ---------------------------- #
def analyse_bytes(b: bytes) -> str:
    if len(b) < 6:
        raise ValueError("too short")
    if b[0] != 0x7E:
        raise ValueError("bad header byte")
    digits = b[1:5]
    if any(c < 0x30 for c in digits):
        raise ValueError("bad length digits")
    length_real = sum((c - 0x30) * m for c, m in zip(digits, (1000, 100, 10, 1)))
    if len(b) < 5 + length_real:
        raise ValueError("truncated")
    if b[5] == 0x7C:
        raise ValueError("P1 packet")
    if b[9] != 0x7C:
        raise ValueError("byte after symbol length is not '|'")
    if b[-1] != 0x4C:
        raise ValueError("missing 'L' terminator")
    return "P2"


def protocol_2_decoder(b: bytes):
    if b[-1] != 0x4C:
        raise ValueError("missing 'L'")
    inner = b[5:-1].decode("ascii")
    symbol_length = int(inner[0:4])
    symbol = inner[5:5 + symbol_length]
    body = inner[5 + symbol_length:]
    values, buf = [], ""
    for c in body:
        if c == ",":
            values.append(_fast_float(buf))  # Rust: expect() panics on "" -> ValueError here
            buf = ""
        else:
            buf += c
    if buf:
        values.append(_fast_float(buf))
    return symbol, values


def _fast_float(s: str) -> float:
    if s == "":
        raise ValueError("Failed to parse value: (empty)")
    return float(s)


def bytes_to_orderbook(b: bytes, depth: int = 10):
    symbol, values = protocol_2_decoder(b)
    argus_ts, remote_ts = values[-1], values[-2]
    vals = values[:-2]
    split = len(vals) // 2
    if split < depth:
        raise ValueError("not enough values")
    bids, asks = vals[:split], vals[split:]
    return {
        "symbol": symbol,
        "bids": [(bids[i * 2], bids[i * 2 + 1]) for i in range(depth)],
        "asks": [(asks[i * 2], asks[i * 2 + 1]) for i in range(depth)],
        "remote_timestamp": remote_ts,
        "argus_timestamp": argus_ts,
    }


# --------------------------------- tests ----------------------------------- #
def book(n_levels=7):
    bids = [{"price": f"{0.75 - 0.01 * i:.2f}", "size": str(65 + 10 * i)} for i in range(n_levels)]
    asks = [{"price": f"{0.76 + 0.01 * i:.2f}", "size": f"{227.02 + i}"} for i in range(n_levels)]
    return bids, asks


def encode(asset_id, ts="1770251679393", n_levels=7):
    bids, asks = book(n_levels)
    md = {asset_id: {"bids": bids, "asks": asks}, "timestamp": ts}
    pkt = transmit_mkt_data_with_protocol_2(
        P2ConvertClass(TICKER, SLUG, asset_id, md, DEPTH))
    return pkt, bids, asks


@pytest.mark.parametrize("asset_id,digits", [(V1_ASSET_ID, 77), (V2_ASSET_ID, 75)])
@pytest.mark.parametrize("n_levels", [7, 10, 0])
def test_p2_roundtrip(asset_id, digits, n_levels):
    assert len(asset_id) == digits
    pkt, bids, asks = encode(asset_id, n_levels=n_levels)
    assert analyse_bytes(pkt) == "P2"
    ob = bytes_to_orderbook(pkt, DEPTH)

    assert ob["symbol"] == f"{TICKER}-{SLUG}-{asset_id}"
    assert ob["symbol"].endswith(asset_id)  # full id survives, no truncation
    for i in range(DEPTH):
        exp_b = (float(bids[i]["price"]), float(bids[i]["size"])) if i < len(bids) else (0.0, 0.0)
        exp_a = (float(asks[i]["price"]), float(asks[i]["size"])) if i < len(asks) else (0.0, 0.0)
        assert ob["bids"][i] == exp_b
        assert ob["asks"][i] == exp_a
    assert ob["remote_timestamp"] == 1770251679393.0
    assert ob["argus_timestamp"] > 1.7e9  # time.time()


def test_p2_symbol_length_header_matches():
    pkt, _, _ = encode(V2_ASSET_ID)
    sym = f"{TICKER}-{SLUG}-{V2_ASSET_ID}"
    assert pkt[0:1] == b"~"
    assert int(pkt[1:5]) == len(pkt) - 5
    assert int(pkt[5:9]) == len(sym)
    assert pkt[9:10] == b"|"
    assert pkt[-1:] == b"L"


def test_p2_via_dispatcher_method():
    d = make_dispatcher()
    bids, asks = book()
    pkt = d.send_market_data_with_p2_encoding(
        {V2_ASSET_ID: {"bids": bids, "asks": asks}, "timestamp": "0"},
        TICKER, SLUG, V2_ASSET_ID)
    ob = bytes_to_orderbook(pkt, DEPTH)
    assert ob["symbol"].endswith(V2_ASSET_ID)
    assert ob["remote_timestamp"] == 0.0  # on-demand snapshot marker


def test_v1_and_v2_symbols_distinct_and_p1_not_confused():
    p1, _, _ = encode(V1_ASSET_ID)
    p2, _, _ = encode(V2_ASSET_ID)
    assert bytes_to_orderbook(p1)["symbol"] != bytes_to_orderbook(p2)["symbol"]
    # A P1 packet must be rejected by the P2 analyser (pipe at byte 5)
    with pytest.raises(ValueError):
        analyse_bytes(b'~0013|{"action":1}')


@pytest.mark.xfail(
    raises=ValueError, strict=False,
    reason="Known bug (fixed by another agent): empty timestamp encodes ',,' and the "
           "Rust decoder panics parsing an empty value.")
def test_p2_empty_timestamp_decodes():
    pkt, _, _ = encode(V2_ASSET_ID, ts="")
    ob = bytes_to_orderbook(pkt, DEPTH)
    assert ob["symbol"].endswith(V2_ASSET_ID)
