"""Offline fixtures that drive the REAL Argus converters / handlers.

Every entry in `build_instances()` is produced by calling real Argus code
(dataclass converters or `PolymarketDispatcher` handler methods on an instance
built with `object.__new__`, so no daemon, network or wallet is involved) on
upstream payloads shaped like the real Polymarket responses.

`build_instances()` returns {golden_name: [json_instance, ...]}.  The FIRST
instance of each list is the primary example exported to golden/examples/;
additional instances exist only to exercise nullability / variants and are
merged into the schema.
"""
from __future__ import annotations

import dataclasses
import json
import socket
import threading
import warnings
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

warnings.filterwarnings("ignore", category=RuntimeWarning)

from argus.polymarket import PolymarketDispatcher  # noqa: E402
from argus.polymarket._classes import ArgsObject, RoutingHelper  # noqa: E402
from argus.polymarket_direct.order_types import (  # noqa: E402
    OrderEvent,
    PolyMarketOrder,
    PolymarketPosition,
    TradeEvent,
)
from argus.protocol import decode_multiple_packets  # noqa: E402

# 77-digit v1 asset id (from P2ConvertClass docstring style) and a real 75-digit v2 id.
V1_ASSET_ID = "10709571168903436267467128546975927414573301583903012586400893265409842166790"
V2_ASSET_ID = "495099235152725588201991226912420825532590817323357329480139627250245959680"
assert len(V1_ASSET_ID) == 77
assert len(V2_ASSET_ID) == 75

WALLET = "0x1111111111111111111111111111111111111111"
OWNER = "6c8d8f2e-0000-4000-8000-000000000001"
ORDER_ID = "0x2a8bc3f1d9a7e0b64f2c1d3e5a7b9c0d1e2f30415263748596a7b8c9d0e1f203"
MARKET = "0x9f1c0b2d3e4f5061728394a5b6c7d8e9f00112233445566778899aabbccddeeff"


class FakeSock:
    """Stands in for a client socket; records everything sent."""

    def __init__(self):
        self.sent = b""

    def sendall(self, data: bytes):
        self.sent += data


def make_dispatcher() -> PolymarketDispatcher:
    d = object.__new__(PolymarketDispatcher)
    RoutingHelper.__init__(d)
    d._configs = {
        "Show P1 Packets": False,
        "show response times": False,
        "Block Order Execution": False,
    }
    d._correlation_id_checker = SimpleNamespace(check_correlation_id=lambda cid: None)
    d._market_cache_lock = threading.Lock()
    d._asset_id_to_ticker = {}
    d._asset_id_to_neg_risk = {}
    d._build_pool = ThreadPoolExecutor(max_workers=2)
    d._orderbook_depth = 10
    d.async_write_log = lambda message: None
    d._apdb = SimpleNamespace(all_tickers=lambda: [])
    d.market_data = SimpleNamespace(get_tick_size=lambda asset_id: 0.01)
    d.rest_api = SimpleNamespace()
    return d


def call(d: PolymarketDispatcher, action: str, args):
    """Route through the real dispatch table (_handle_client_message)."""
    return d._handle_client_message(FakeSock(), ("127.0.0.1", 0), {"action": action, "data": args})


def rt(obj):
    """JSON round-trip: exactly what the client sees on the wire."""
    return json.loads(json.dumps(obj))


# --------------------------------------------------------------------------- #
# Realistic upstream payloads
# --------------------------------------------------------------------------- #
def raw_position(i: int = 0) -> dict:
    # data-api.polymarket.com/positions item
    return {
        "proxyWallet": WALLET,
        "asset": V2_ASSET_ID if i == 0 else V1_ASSET_ID,
        "conditionId": MARKET,
        "size": 150.5,
        "avgPrice": 0.42,
        "initialValue": 63.21,
        "currentValue": 75.25,
        "cashPnl": 12.04,
        "percentPnl": 19.05,
        "totalBought": 150.5,
        "realizedPnl": 0,           # data-api sometimes returns bare ints
        "percentRealizedPnl": 0,
        "curPrice": 0.5,
        "redeemable": False,
        "mergeable": False,
        "title": "Bitcoin Up or Down - 15 minute",
        "slug": "btc-updown-15m-1770413400",
        "icon": "https://polymarket-upload.s3.us-east-2.amazonaws.com/btc.png",
        "eventId": "123456",
        "eventSlug": "btc-updown-15m-1770413400",
        "outcome": "Up",
        "outcomeIndex": 0,
        "oppositeOutcome": "Down",
        "oppositeAsset": V1_ASSET_ID,
        "endDate": "2026-02-06",
        "negativeRisk": False,
    }


def raw_clob_order(i: int = 0) -> dict:
    # py_clob_client get_orders / get_order item
    return {
        "id": ORDER_ID,
        "status": "LIVE",
        "owner": OWNER,
        "maker_address": WALLET,
        "market": MARKET,
        "asset_id": V2_ASSET_ID,
        "side": "BUY",
        "original_size": "100",
        "size_matched": "0",
        "price": "0.45",
        "outcome": "Up",
        "expiration": "0",
        "order_type": "GTC",
        "associate_trades": [],
        "created_at": 1770413401,
    }


def raw_order_event() -> dict:
    return {
        "id": ORDER_ID,
        "owner": OWNER,
        "market": MARKET,
        "asset_id": V2_ASSET_ID,
        "side": "BUY",
        "order_owner": OWNER,
        "original_size": "100",
        "size_matched": "0",
        "price": "0.45",
        "associate_trades": None,
        "outcome": "Up",
        "type": "PLACEMENT",
        "created_at": "1770413401",
        "expiration": "0",
        "order_type": "GTC",
        "status": "LIVE",
        "maker_address": WALLET,
        "timestamp": "1770413401123",
        "event_type": "order",
    }


def raw_trade_event() -> dict:
    return {
        "id": "28c4d2eb-bbea-40e7-a9f0-b2fdb56b2c2e",
        "taker_order_id": ORDER_ID,
        "market": MARKET,
        "asset_id": V2_ASSET_ID,
        "side": "BUY",
        "size": "10",
        "fee_rate_bps": "0",
        "price": "0.57",
        "status": "MATCHED",
        "match_time": "1770413402",
        "last_update": "1770413402",
        "outcome": "Up",
        "owner": OWNER,
        "trade_owner": OWNER,
        "maker_address": WALLET,
        "transaction_hash": "0x" + "ab" * 32,
        "bucket_index": 0,
        "maker_orders": [
            {
                "order_id": "0x" + "cd" * 32,
                "owner": OWNER,
                "maker_address": WALLET,
                "matched_amount": "10",
                "price": "0.57",
                "fee_rate_bps": "0",
                "asset_id": V2_ASSET_ID,
                "outcome": "Up",
                "outcome_index": 0,
                "side": "BUY",
            }
        ],
        "trader_side": "TAKER",
        "timestamp": "1770413402456",
        "event_type": "trade",
    }


PLACE_OK = {
    "errorMsg": "",
    "orderID": ORDER_ID,
    "takingAmount": "",
    "makingAmount": "",
    "status": "live",
    "success": True,
}
PLACE_FILLED = {
    "errorMsg": "",
    "orderID": "0x" + "ef" * 32,
    "takingAmount": "10",
    "makingAmount": "5.7",
    "status": "matched",
    "success": True,
}


def _account_update(d: PolymarketDispatcher, update) -> dict:
    s = FakeSock()
    d.add_socket(s)
    d._account_update_callback(update)
    d.remove_socket(s)
    (pkt,) = decode_multiple_packets(s.sent)
    return json.loads(pkt)


def _envelope(d: PolymarketDispatcher, content: dict) -> dict:
    from argus.protocol import encode_packet

    s = FakeSock()
    d._handle_incoming_packets(s, ("127.0.0.1", 0), encode_packet(json.dumps(content).encode()))
    (pkt,) = decode_multiple_packets(s.sent)
    return json.loads(pkt)


def _clob_event(outcomes):
    market = SimpleNamespace(
        clobTokenIds=[V2_ASSET_ID, V1_ASSET_ID],
        outcomes=outcomes,
        slug="btc-updown-15m-1770413400",
        question="Bitcoin Up or Down - February 6, 4:30AM-4:45AM ET?",
    )
    return SimpleNamespace(
        title="Bitcoin Up or Down - February 6, 4:30AM-4:45AM ET",
        ticker="btc-updown-15m-1770413400",
        markets=[market],
    )


def build_instances() -> dict[str, list]:
    out: dict[str, list] = {}
    d = make_dispatcher()

    # ---- positions: PolymarketPosition.from_dict -> asdict, via real handler
    positions = [PolymarketPosition.from_dict(raw_position(i)) for i in range(2)]
    d.rest_api.get_positions = lambda: SimpleNamespace(positions=positions)
    out["positions"] = [rt(call(d, "get_positions", []))]

    # ---- orders: PolyMarketOrder(**x) -> asdict
    orders = [PolyMarketOrder(**raw_clob_order(0)), PolyMarketOrder(**raw_clob_order(1))]
    d.rest_api.get_orders = lambda: orders
    out["orders"] = [rt(call(d, "get_orders", []))]
    d.rest_api.get_order_status = lambda order_id: orders[0]
    out["order_status"] = [rt(call(d, "get_order_status", {"order_id": ORDER_ID}))]

    # ---- account_update (P1 push) for both event kinds
    out["account_update_order"] = [
        _account_update(d, OrderEvent.from_dict(raw_order_event()))
    ]
    out["account_update_trade"] = [
        _account_update(d, TradeEvent.from_dict(raw_trade_event()))
    ]

    # ---- place_order passthrough
    d.rest_api.place_order = lambda **kw: dict(PLACE_OK)
    d._asset_id_to_neg_risk[V2_ASSET_ID] = False
    out["place_order"] = [
        rt(call(d, "place_order", {"token_id": V2_ASSET_ID, "price": 0.45, "size": 100,
                                    "side": "buy", "order_type": "GTC"})),
        rt(PLACE_FILLED),
    ]

    # ---- place_multiple_orders {success, failed}
    d.rest_api.build_order = lambda *a, **kw: object()
    d.rest_api.place_built_orders = lambda built: {
        "success": [dict(PLACE_OK), dict(PLACE_FILLED)],
        "failed": [{**PLACE_OK, "errorMsg": "invalid amount for a marketable BUY order "
                    "($0.01), min size: $1", "orderID": "", "status": ""}],
    }
    out["place_multiple_orders"] = [
        rt(call(d, "place_multiple_orders", {"orders": [
            {"token_id": V2_ASSET_ID, "price": 0.45, "size": 100, "side": "buy"},
            {"token_id": V2_ASSET_ID, "price": 0.46, "size": 100, "side": "buy"},
        ]}))
    ]

    # ---- cancels
    d.rest_api.cancel_order = lambda order_id: {"not_canceled": {}, "canceled": [ORDER_ID]}
    out["cancel_order"] = [
        rt(call(d, "cancel_order", {"order_id": ORDER_ID})),
        {"canceled": [], "not_canceled": {
            ORDER_ID: "order can't be found - already canceled or matched"}},
    ]
    d.rest_api.cancel_multiple = lambda order_ids: {
        "not_canceled": {"0x" + "11" * 32: "order can't be found - already canceled or matched"},
        "canceled": [ORDER_ID],
    }
    out["cancel_multiple_orders"] = [
        rt(call(d, "cancel_multiple_orders", {"order_ids": [ORDER_ID, "0x" + "11" * 32]}))
    ]

    # ---- subscribe {subscribed, failed}
    d.market_data = SimpleNamespace(
        get_tick_size=lambda asset_id: 0.01,
        subscribe_to_asset_id=lambda cid: (_ for _ in ()).throw(RuntimeError("boom"))
        if cid == "bad" else None,
    )
    d._warm_clob_caches_for_subscribed_asset = lambda cid: None
    out["subscribe"] = [rt(call(d, "subscribe", [V2_ASSET_ID, V1_ASSET_ID, "bad"]))]

    # ---- unsubscribe {unsubscribed, failed} (the SDK reads it via a serde alias)
    d.remove_socket_from_subscription = lambda sock, cid: (_ for _ in ()).throw(KeyError(cid)) if cid == "bad" else None
    out["unsubscribe"] = [rt(call(d, "unsubscribe", [V2_ASSET_ID, V1_ASSET_ID, "bad"]))]

    # ---- fetch_clob_id_information
    clob_variants = []
    for outcomes in (["Up", "Down"], None):
        d._resolve_market_from_token_id = lambda token_id, o=outcomes: _clob_event(o)
        clob_variants.append(rt(call(d, "fetch_clob_id_information", [V2_ASSET_ID])))
    out["clob_info"] = clob_variants

    # ---- balances / price_to_beat (floats), search_markets (list[str])
    d.rest_api.get_balance = lambda: 1234.567891
    out["balance"] = [rt(call(d, "get_balance", []))]
    d.rest_api.get_token_balance = lambda token_id: 149.25
    out["token_balance"] = [rt(call(d, "get_token_balance", {"token_id": V2_ASSET_ID}))]

    import time as _time
    real_sleep = _time.sleep
    _time.sleep = lambda s: None
    try:
        d._handle_get_price_to_beat_inner = lambda args_obj: 97234.51
        out["price_to_beat"] = [rt(call(d, "get_price_to_beat", ["btc-updown-15m-1770413400"]))]
    finally:
        _time.sleep = real_sleep

    d._apdb = SimpleNamespace(all_tickers=lambda: [
        "btc-updown-15m-1770413400", "eth-updown-15m-1770413400", "xrp-updown-15m-1770413400"])
    out["search_markets"] = [rt(call(d, "search_markets", ["btc", "2"]))]

    # ---- P1 response envelopes (what the SDK's InBoundMessage parses)
    out["envelope_ok"] = [_envelope(d, {"action": "get_balance", "data": [],
                                         "correlation_id": "c0ffee00-0000-4000-8000-000000000001"})]
    out["envelope_error"] = [_envelope(d, {"action": "nope", "data": [],
                                            "correlation_id": "c0ffee00-0000-4000-8000-000000000002"})]
    big = [f"btc-updown-15m-{1770413400 + i * 900}-{'x' * 40}" for i in range(400)]
    d._apdb = SimpleNamespace(all_tickers=lambda: big)
    out["envelope_compressed"] = [_envelope(d, {"action": "search_markets", "data": ["btc", "400"],
                                                  "correlation_id": "c0ffee00-0000-4000-8000-000000000003"})]
    return out
