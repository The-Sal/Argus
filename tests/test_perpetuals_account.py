"""
Offline tests for the perpetuals account-data layer (argus/perpetuals/shared/account.py
and the Hyperliquid / Lighter venue records + REST adapters behind it).

Fixtures are the example payloads from the venues' own API docs (Hyperliquid info
endpoint: clearinghouseState / frontendOpenOrders / orderStatus / userFills /
userFunding / userRateLimit; Lighter: /account, /accountActiveOrders, /trades,
/positionFunding). HTTP sessions are stubbed, so everything runs offline.

Run with: pytest tests/test_perpetuals_account.py
"""
import json
import time
import unittest
from decimal import Decimal
from typing import Any, Dict, List, Optional

from argus._argus_utils import ArgsObject
from argus.perpetuals.shared import BaseDispatcher, ers
from argus.perpetuals.shared import account as acct
from argus.perpetuals.shared._classes import OutboundMessage
from argus.perpetuals.hyper import _classes as hyper_cls
from argus.perpetuals.hyper import rest as hyper_rest
from argus.perpetuals.lighter import _classes as lighter_cls
from argus.perpetuals.lighter import rest as lighter_rest


# --- fixtures (from the venues' API docs) ------------------------------------

HL_CLEARINGHOUSE = {
    "assetPositions": [
        {
            "position": {
                "coin": "ETH",
                "cumFunding": {"allTime": "514.085417", "sinceChange": "0.0", "sinceOpen": "0.0"},
                "entryPx": "2986.3",
                "leverage": {"rawUsd": "-95.059824", "type": "isolated", "value": 20},
                "liquidationPx": "2866.26936529",
                "marginUsed": "4.967826",
                "maxLeverage": 50,
                "positionValue": "100.02765",
                "returnOnEquity": "-0.0026789",
                "szi": "0.0335",
                "unrealizedPnl": "-0.0134",
            },
            "type": "oneWay",
        },
        {
            "position": {
                "coin": "BTC",
                "cumFunding": {"allTime": "0.0", "sinceChange": "0.0", "sinceOpen": "0.0"},
                "entryPx": None,
                "leverage": {"type": "cross", "value": 3},
                "liquidationPx": None,
                "marginUsed": "0.0",
                "maxLeverage": 40,
                "positionValue": "0.0",
                "returnOnEquity": "0.0",
                "szi": "0.0",
                "unrealizedPnl": "0.0",
            },
            "type": "oneWay",
        },
    ],
    "crossMaintenanceMarginUsed": "0.0",
    "crossMarginSummary": {"accountValue": "13104.514502", "totalMarginUsed": "0.0", "totalNtlPos": "0.0", "totalRawUsd": "13104.514502"},
    "marginSummary": {"accountValue": "13109.482328", "totalMarginUsed": "4.967826", "totalNtlPos": "100.02765", "totalRawUsd": "13009.454678"},
    "time": 1708622398623,
    "withdrawable": "13104.514502",
}

HL_FRONTEND_ORDER = {
    "coin": "BTC", "isPositionTpsl": False, "isTrigger": False, "limitPx": "29792.0", "oid": 91490942,
    "orderType": "Limit", "origSz": "5.0", "reduceOnly": False, "side": "A", "sz": "5.0",
    "timestamp": 1681247412573, "triggerCondition": "N/A", "triggerPx": "0.0",
}
HL_SLIM_ORDER = {"coin": "BTC", "limitPx": "29792.0", "oid": 91490943, "side": "B", "sz": "1.0", "timestamp": 1681247412574}

HL_ORDER_STATUS = {
    "status": "order",
    "order": {
        "order": {
            "coin": "ETH", "side": "A", "limitPx": "2412.7", "sz": "0.0", "oid": 1, "timestamp": 1724361546645,
            "triggerCondition": "N/A", "isTrigger": False, "triggerPx": "0.0", "children": [], "isPositionTpsl": False,
            "reduceOnly": True, "orderType": "Market", "origSz": "0.0076", "tif": "FrontendMarket", "cloid": None,
        },
        "status": "filled",
        "statusTimestamp": 1724361546645,
    },
}

HL_FILL = {
    "closedPnl": "0.0", "coin": "AVAX", "crossed": False, "dir": "Open Long",
    "hash": "0xa166e3fa63c25663024b03f2e0da011a00307e4017465df020210d3d432e7cb8", "oid": 90542681,
    "px": "18.435", "side": "B", "startPosition": "26.86", "sz": "93.53", "time": 1681222254710,
    "fee": "0.01", "feeToken": "USDC", "builderFee": "0.01", "tid": 118906512037719,
}

HL_FUNDING = {
    "delta": {"coin": "ETH", "fundingRate": "0.0000417", "szi": "49.1477", "type": "funding", "usdc": "-3.625312", "nSamples": None},
    "hash": "0xa166e3fa63c25663024b03f2e0da011a00307e4017465df020210d3d432e7cb8",
    "time": 1681222254710,
}

LIGHTER_ACCOUNT = {
    "code": 200, "message": "", "total": 1,
    "accounts": [{
        "account_type": 1, "account_trading_mode": 1, "index": 6, "l1_address": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        "cancel_all_time": 0, "total_order_count": 100, "total_isolated_order_count": 0, "pending_order_count": 2,
        "available_balance": "19995", "status": 1, "collateral": "46342", "account_index": 6, "name": "main",
        "total_asset_value": "46359.52", "cross_asset_value": "46359.52",
        "cross_initial_margin_requirement": "5998.500000", "cross_maintenance_margin_requirement": "2999.250000",
        "created_at": 1640995200, "transaction_time": 1640995200,
        "positions": [
            {
                "market_id": 1, "symbol": "ETH", "initial_margin_fraction": "20.00", "open_order_count": 3,
                "pending_order_count": 3, "position_tied_order_count": 3, "sign": 1, "position": "3.6956",
                "avg_entry_price": "3024.66", "position_value": "3019.92", "unrealized_pnl": "17.521309",
                "realized_pnl": "2.000000", "liquidation_price": "2424.66", "total_funding_paid_out": "34.2",
                "margin_mode": 1, "allocated_margin": "604.0", "total_discount": "0", "margin_set_flag": 1,
            },
            {
                "market_id": 0, "symbol": "BTC", "initial_margin_fraction": "10.00", "open_order_count": 0,
                "pending_order_count": 0, "position_tied_order_count": 0, "sign": 0, "position": "0",
                "avg_entry_price": "0", "position_value": "0", "unrealized_pnl": "0", "realized_pnl": "0",
                "liquidation_price": "0", "margin_mode": 0, "allocated_margin": "0",
            },
        ],
        "assets": [{"symbol": "USDC", "asset_id": 1, "balance": "1000", "locked_balance": "0"}],
    }],
    "next_cursor": "",
}

LIGHTER_ORDER = {
    "order_index": 1, "client_order_index": 234, "order_id": "1", "client_order_id": "234", "market_index": 1,
    "owner_account_index": 6, "initial_base_amount": "0.1", "price": "3024.66", "nonce": 722,
    "remaining_base_amount": "0.05", "is_ask": True, "base_size": 12354, "base_price": 3024,
    "filled_base_amount": "0.05", "filled_quote_amount": "151.23", "side": "sell", "type": "limit",
    "time_in_force": "good-till-time", "reduce_only": True, "trigger_price": "0", "order_expiry": 1640995200,
    "status": "open", "trigger_status": "na", "block_height": 45434, "timestamp": 1640995200,
    "created_at": 1640995200, "updated_at": 1640995200, "transaction_time": 1640995200000000, "order_version": 1,
}

LIGHTER_TRADE = {
    "trade_id": 145, "trade_id_str": "145", "tx_hash": "0xabc", "type": "trade", "market_id": 1, "size": "0.1",
    "price": "3024.66", "usd_amount": "302.466", "ask_id": 145, "ask_id_str": "145", "bid_id": 245, "bid_id_str": "245",
    "ask_client_id_str": "145", "bid_client_id_str": "245", "ask_account_id": 6, "bid_account_id": 3,
    "is_maker_ask": True, "block_height": 45434, "timestamp": 1640995200123, "taker_fee": 0.3, "maker_fee": 0.1,
    "bid_account_pnl": "-0.022890", "ask_account_pnl": "1.989696", "transaction_time": 1771943742851429,
}

LIGHTER_FUNDING = {
    "timestamp": 1640995200, "market_id": 1, "funding_id": 7, "change": "-1.5", "discount": "0",
    "rate": "0.0001", "position_size": "2", "position_side": "short",
}


# --- stubs -------------------------------------------------------------------

class _StubResponse:
    def __init__(self, payload: Any):
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _StubSession:
    """Records every call; answers from `router(url_or_body) -> payload`."""

    def __init__(self, router):
        self.router = router
        self.headers: Dict[str, str] = {}
        self.calls: List[Dict[str, Any]] = []

    def post(self, url: str, json: dict) -> _StubResponse:
        self.calls.append({"url": url, "json": json, "headers": dict(self.headers)})
        return _StubResponse(self.router(json))

    def get(self, url: str, params: Optional[dict] = None) -> _StubResponse:
        self.calls.append({"url": url, "params": dict(params or {}), "headers": dict(self.headers)})
        return _StubResponse(self.router(url, params or {}))


def _hyper(router) -> hyper_rest.HyperLiquidRest:
    rest = hyper_rest.HyperLiquidRest(wallet_address="0xmaster", private_key="0x0")
    rest.session = _StubSession(router)
    return rest


def _lighter(router, **kwargs) -> lighter_rest.LighterRest:
    rest = lighter_rest.LighterRest(**kwargs)
    rest.session = _StubSession(router)
    rest._auth_session = _StubSession(router)
    rest._auth_session.headers = {"authorization": rest.auth_token or ""}
    return rest


class _StubAccountRest(acct.BaseDispatcherCompatibleAccountRest):
    """Homogenous-record producer with canned data, for exercising the shared handlers."""

    def __init__(self, n: int = 60):
        self.calls: List[tuple] = []
        pos = hyper_cls.Position.from_dict(HL_CLEARINGHOUSE["assetPositions"][0]["position"])
        self._positions = [pos.to_common()] * n
        self._orders = [hyper_cls.OpenOrder.from_dict(HL_FRONTEND_ORDER).to_common()] * n
        self._trades = [hyper_cls.UserFill.from_dict(HL_FILL).to_common()] * n
        self._funding = [hyper_cls.UserFundingPayment.from_dict(HL_FUNDING).to_common()] * n
        self._balance = hyper_cls.ClearinghouseState.from_dict("", HL_CLEARINGHOUSE).to_balance()

    def account_identity(self):
        return "0xmaster"

    def get_account_balance(self, **kw):
        self.calls.append(("balance", kw)); return self._balance

    def get_positions(self, **kw):
        self.calls.append(("positions", kw)); return self._positions

    def get_open_orders(self, **kw):
        self.calls.append(("orders", kw)); return self._orders

    def get_order_status(self, order_id, **kw):
        self.calls.append(("status", order_id, kw))
        return self._orders[0] if order_id == "91490942" else None

    def get_recent_trades(self, max_count, **kw):
        self.calls.append(("trades", max_count, kw)); return self._trades[:max_count]

    def get_funding_payments(self, start_time_ms, end_time_ms=None, **kw):
        self.calls.append(("funding", start_time_ms, end_time_ms, kw)); return self._funding


class _StubDispatcher(acct.AccountHandlersMixin):
    _routine_paginate = staticmethod(BaseDispatcher._routine_paginate)

    def __init__(self, rest):
        self.account_rest = rest


def _args(data=None) -> ArgsObject:
    return ArgsObject(sock=None, args=data)


# --- shared module -----------------------------------------------------------

class NormalizeTimestampTest(unittest.TestCase):
    def test_units_inferred_from_magnitude(self):
        self.assertEqual(acct.normalize_timestamp_ms(1640995200), 1640995200000)          # seconds
        self.assertEqual(acct.normalize_timestamp_ms(1640995200123), 1640995200123)       # ms
        self.assertEqual(acct.normalize_timestamp_ms(1771943742851429), 1771943742851)    # us


class HomogenousRecordsTest(unittest.TestCase):
    def test_position_side_helpers(self):
        pos = hyper_cls.Position.from_dict(HL_CLEARINGHOUSE["assetPositions"][0]["position"]).to_common()
        self.assertTrue(pos.is_long)
        self.assertEqual(pos.size, Decimal("0.0335"))
        d = pos.to_dict()
        self.assertEqual(d["side"], "long")
        self.assertEqual(d["venue"]["coin"], "ETH")
        self.assertEqual(d["leverage"], "20")

    def test_records_are_frozen(self):
        pos = hyper_cls.Position.from_dict(HL_CLEARINGHOUSE["assetPositions"][0]["position"]).to_common()
        with self.assertRaises(Exception):
            pos.name = "X"  # type: ignore[misc]


# --- Hyperliquid venue records ----------------------------------------------

class HyperRecordsTest(unittest.TestCase):
    def test_clearinghouse_state_round_trip_and_balance(self):
        state = hyper_cls.ClearinghouseState.from_dict("", HL_CLEARINGHOUSE)
        self.assertEqual(state.account_value, Decimal("13109.482328"))
        self.assertEqual(len(state.positions), 2)
        self.assertIsNone(state.positions[1].entry_px)
        self.assertIsNone(state.positions[1].liquidation_px)
        rt = state.to_dict()
        for key in ("marginSummary", "crossMarginSummary", "withdrawable", "time", "assetPositions"):
            self.assertIn(key, rt)
        self.assertEqual(rt["assetPositions"][0]["position"]["leverage"], {"type": "isolated", "value": 20, "rawUsd": "-95.059824"})
        self.assertNotIn("rawUsd", rt["assetPositions"][1]["position"]["leverage"])

        bal = state.to_balance()
        self.assertEqual(bal.account_value, Decimal("13109.482328"))
        self.assertEqual(bal.available_balance, Decimal("13104.514502"))
        self.assertEqual(bal.total_margin_used, Decimal("4.967826"))
        self.assertEqual(bal.total_position_notional, Decimal("100.02765"))
        self.assertEqual(bal.to_dict()["venue"]["withdrawable"], "13104.514502")

    def test_open_order_from_both_shapes(self):
        rich = hyper_cls.OpenOrder.from_dict(HL_FRONTEND_ORDER)
        self.assertEqual(rich.orig_sz, Decimal("5.0"))
        self.assertFalse(rich.is_buy)
        slim = hyper_cls.OpenOrder.from_dict(HL_SLIM_ORDER)
        self.assertEqual(slim.orig_sz, slim.sz)          # slim shape has no origSz
        self.assertEqual(slim.order_type, "Limit")
        self.assertTrue(slim.is_buy)
        common = rich.to_common()
        self.assertEqual(common.order_id, "91490942")
        self.assertEqual(common.status, "open")
        self.assertEqual(common.to_dict()["side"], "sell")
        self.assertEqual(common.filled_size, Decimal("0"))

    def test_order_status_found_and_unknown(self):
        found = hyper_cls.OrderStatus.from_dict(HL_ORDER_STATUS)
        self.assertTrue(found.found)
        self.assertEqual(found.status, "filled")
        common = found.to_common()
        self.assertEqual(common.status, "filled")
        self.assertEqual(common.order_type, "Market")
        self.assertTrue(common.reduce_only)
        unknown = hyper_cls.OrderStatus.from_dict({"status": "unknownOid"})
        self.assertFalse(unknown.found)
        self.assertIsNone(unknown.to_common())
        self.assertEqual(unknown.to_dict(), {"found": False, "order": None, "status": None, "statusTimestamp": None})

    def test_fill_to_trade(self):
        fill = hyper_cls.UserFill.from_dict(HL_FILL)
        trade = fill.to_common()
        self.assertEqual(trade.trade_id, "118906512037719")
        self.assertEqual(trade.order_id, "90542681")
        self.assertTrue(trade.is_buy)
        self.assertTrue(trade.is_maker)              # crossed == False -> resting
        self.assertEqual(trade.fee, Decimal("0.01"))
        self.assertEqual(trade.realized_pnl, Decimal("0.0"))
        self.assertEqual(trade.to_dict()["venue"]["builderFee"], "0.01")
        taker = hyper_cls.UserFill.from_dict({**HL_FILL, "crossed": True}).to_common()
        self.assertFalse(taker.is_maker)

    def test_funding_payment(self):
        pay = hyper_cls.UserFundingPayment.from_dict(HL_FUNDING).to_common()
        self.assertEqual(pay.name, "ETH")
        self.assertEqual(pay.payment, Decimal("-3.625312"))
        self.assertEqual(pay.position_size, Decimal("49.1477"))
        self.assertEqual(pay.timestamp_ms, 1681222254710)
        self.assertEqual(pay.to_dict()["venue"]["delta"]["fundingRate"], "0.0000417")

    def test_rate_limit_surplus_optional(self):
        rl = hyper_cls.UserRateLimit.from_dict({"cumVlm": "2854574.593578", "nRequestsUsed": 2890, "nRequestsCap": 2864574, "nRequestsSurplus": 0})
        self.assertEqual(rl.n_requests_remaining, 2864574 - 2890)
        rl2 = hyper_cls.UserRateLimit.from_dict({"cumVlm": "1", "nRequestsUsed": 1, "nRequestsCap": 2})
        self.assertEqual(rl2.to_dict()["nRequestsSurplus"], 0)


class HyperRestAccountTest(unittest.TestCase):
    def _router(self, body):
        t = body["type"]
        self._seen.append(body)
        if t == "clearinghouseState":
            return HL_CLEARINGHOUSE
        if t == "frontendOpenOrders":
            return [HL_FRONTEND_ORDER, {**HL_FRONTEND_ORDER, "oid": 5, "timestamp": HL_FRONTEND_ORDER["timestamp"] + 10}]
        if t == "orderStatus":
            return HL_ORDER_STATUS if body["oid"] in (1, "0xcloid") else {"status": "unknownOid"}
        if t == "userFills":
            return [HL_FILL, {**HL_FILL, "tid": 2, "time": HL_FILL["time"] + 5}, {**HL_FILL, "tid": 3, "time": HL_FILL["time"] - 5}]
        if t == "userFunding":
            return [HL_FUNDING, {**HL_FUNDING, "time": HL_FUNDING["time"] + 1}]
        raise AssertionError(t)

    def setUp(self):
        self._seen = []
        self.rest = _hyper(self._router)

    def test_user_keyed_bodies(self):
        self.rest.get_account_balance()
        self.assertEqual(self._seen[-1], {"type": "clearinghouseState", "user": "0xmaster"})
        self.rest.get_account_balance(dex="xyz")
        self.assertEqual(self._seen[-1], {"type": "clearinghouseState", "user": "0xmaster", "dex": "xyz"})
        self.rest.get_funding_payments(100, 200)
        self.assertEqual(self._seen[-1], {"type": "userFunding", "user": "0xmaster", "startTime": 100, "endTime": 200})

    def test_positions_drop_flat(self):
        positions = self.rest.get_positions()
        self.assertEqual([p.name for p in positions], ["ETH"])

    def test_open_orders_newest_first(self):
        orders = self.rest.get_open_orders()
        self.assertEqual([o.order_id for o in orders], ["5", "91490942"])

    def test_order_status_numeric_vs_cloid(self):
        self.assertEqual(self.rest.get_order_status("1").status, "filled")
        self.assertEqual(self._seen[-1]["oid"], 1)
        self.assertIsNotNone(self.rest.get_order_status("0xcloid"))
        self.assertEqual(self._seen[-1]["oid"], "0xcloid")
        self.assertIsNone(self.rest.get_order_status("999"))

    def test_recent_trades_sorted_and_trimmed(self):
        trades = self.rest.get_recent_trades(2)
        self.assertEqual([t.trade_id for t in trades], ["2", "118906512037719"])

    def test_funding_newest_first(self):
        pays = self.rest.get_funding_payments(0)
        self.assertEqual([p.timestamp_ms for p in pays], [HL_FUNDING["time"] + 1, HL_FUNDING["time"]])


# --- Lighter venue records ---------------------------------------------------

class LighterRecordsTest(unittest.TestCase):
    def test_account_summary_balance_and_open_positions(self):
        summary = lighter_cls.AccountSummary.from_dict(LIGHTER_ACCOUNT["accounts"][0])
        self.assertEqual(summary.index, 6)
        self.assertEqual([p.symbol for p in summary.open_positions], ["ETH"])   # flat BTC row dropped
        self.assertEqual(summary.total_margin_used, Decimal("604.0"))
        bal = summary.to_balance()
        self.assertEqual(bal.account_value, Decimal("46359.52"))
        self.assertEqual(bal.available_balance, Decimal("19995"))
        self.assertEqual(bal.total_position_notional, Decimal("3019.92"))
        venue = bal.to_dict()["venue"]
        self.assertIsNone(venue["positions"])            # positions are served by get_positions, not get_balance
        self.assertEqual(venue["open_position_count"], 1)
        self.assertEqual(venue["collateral"], "46342")

    def test_position_to_common(self):
        pos = lighter_cls.AccountPosition.from_dict(LIGHTER_ACCOUNT["accounts"][0]["positions"][0]).to_common()
        self.assertEqual(pos.signed_size, Decimal("3.6956"))
        self.assertEqual(pos.entry_price, Decimal("3024.66"))
        self.assertEqual(pos.margin_used, Decimal("604.0"))
        self.assertIsNone(pos.leverage)
        short = lighter_cls.AccountPosition.from_dict({**LIGHTER_ACCOUNT["accounts"][0]["positions"][0], "sign": -1, "liquidation_price": "0"}).to_common()
        self.assertTrue(short.is_short)
        self.assertIsNone(short.liquidation_price)       # 0 means "none" on Lighter

    def test_order_to_common(self):
        order = lighter_cls.LighterOrder.from_dict(LIGHTER_ORDER).to_common("ETH")
        self.assertEqual(order.order_id, "1")
        self.assertEqual(order.client_order_id, "234")
        self.assertFalse(order.is_buy)
        self.assertEqual(order.remaining_size, Decimal("0.05"))
        self.assertEqual(order.filled_size, Decimal("0.05"))
        self.assertEqual(order.timestamp_ms, 1640995200000)
        self.assertEqual(order.to_dict()["venue"]["time_in_force"], "good-till-time")

    def test_trade_side_fee_and_pnl_depend_on_account(self):
        trade = lighter_cls.LighterTrade.from_dict(LIGHTER_TRADE)
        as_ask = trade.to_common(account_index=6, symbol="ETH")
        self.assertFalse(as_ask.is_buy)
        self.assertTrue(as_ask.is_maker)                 # ask was the maker
        self.assertEqual(as_ask.fee, Decimal("0.1"))
        self.assertEqual(as_ask.order_id, "145")
        self.assertEqual(as_ask.realized_pnl, Decimal("1.989696"))
        self.assertEqual(as_ask.timestamp_ms, 1640995200123)
        as_bid = trade.to_common(account_index=3, symbol="ETH")
        self.assertTrue(as_bid.is_buy)
        self.assertFalse(as_bid.is_maker)
        self.assertEqual(as_bid.fee, Decimal("0.3"))
        self.assertEqual(as_bid.order_id, "245")
        with self.assertRaises(ValueError):
            trade.to_common(account_index=99, symbol="ETH")

    def test_funding_to_common_signs_short(self):
        pay = lighter_cls.PositionFunding.from_dict(LIGHTER_FUNDING).to_common("ETH")
        self.assertEqual(pay.position_size, Decimal("-2"))
        self.assertEqual(pay.payment, Decimal("-1.5"))
        self.assertEqual(pay.timestamp_ms, 1640995200000)
        self.assertEqual(pay.to_dict()["venue"]["funding_id"], 7)


class LighterRestAccountTest(unittest.TestCase):
    def _router(self, url, params):
        if url.endswith("/api/v1/account"):
            return LIGHTER_ACCOUNT
        if url.endswith("/api/v1/orderBookDetails"):
            return {"code": 200, "order_book_details": []}
        if url.endswith("/api/v1/accountActiveOrders"):
            return {"code": 200, "orders": [LIGHTER_ORDER, {**LIGHTER_ORDER, "order_id": "2", "order_index": 2, "timestamp": 1640995300}]}
        if url.endswith("/api/v1/accountInactiveOrders"):
            return {"code": 200, "orders": [{**LIGHTER_ORDER, "order_id": "9", "order_index": 9, "status": "filled"}], "next_cursor": ""}
        if url.endswith("/api/v1/trades"):
            page = params.get("cursor")
            if page is None:
                return {"code": 200, "trades": [LIGHTER_TRADE] * 2, "next_cursor": "c2"}
            return {"code": 200, "trades": [{**LIGHTER_TRADE, "trade_id": 9, "trade_id_str": "9"}] * 2, "next_cursor": ""}
        if url.endswith("/api/v1/positionFunding"):
            return {"code": 200, "position_fundings": [LIGHTER_FUNDING, {**LIGHTER_FUNDING, "timestamp": 1640995300}], "next_cursor": ""}
        raise AssertionError(url)

    def test_token_parsing(self):
        rest = lighter_rest.LighterRest(auth_token="ro:6:all:9999999999:abcdef")
        self.assertEqual(rest.account_index, 6)
        self.assertEqual(rest._auth_session.headers["authorization"], "ro:6:all:9999999999:abcdef")
        with self.assertRaises(ValueError):
            lighter_rest.LighterRest(account_index=7, auth_token="ro:6:single:9999999999:abcdef")
        lighter_rest.LighterRest(account_index=7, auth_token="ro:6:all:9999999999:abcdef")   # 'all' scope may differ
        with self.assertRaises(ValueError):
            lighter_rest.LighterRest(auth_token="ro:6:all:1000000000:abcdef")               # expired
        with self.assertRaises(ValueError):
            lighter_rest.LighterRest(auth_token="not-a-token")

    def test_unconfigured_errors_name_the_missing_piece(self):
        rest = _lighter(self._router)
        with self.assertRaises(ers.AccountNotConfiguredError) as cm:
            rest.get_account_balance()
        self.assertIn("LIGHTER_ACCOUNT_INDEX", str(cm.exception))
        rest = _lighter(self._router, account_index=6)
        self.assertEqual(rest.get_account_balance().account_value, Decimal("46359.52"))   # public read works
        with self.assertRaises(ers.AccountNotConfiguredError) as cm:
            rest.get_open_orders()
        self.assertIn("LIGHTER_AUTH_TOKEN", str(cm.exception))

    def test_public_vs_authed_sessions(self):
        rest = _lighter(self._router, auth_token="ro:6:all:9999999999:abcdef")
        rest.get_account_balance()
        self.assertEqual(rest.session.calls[-1]["params"], {"by": "index", "value": "6", "active_only": "true"})
        self.assertEqual(rest._auth_session.calls, [])
        orders = rest.get_open_orders()
        call = rest._auth_session.calls[-1]
        self.assertEqual(call["headers"]["authorization"], "ro:6:all:9999999999:abcdef")
        self.assertEqual(call["params"]["account_index"], 6)
        self.assertEqual(call["params"]["market_id"], lighter_rest.ALL_MARKETS)
        self.assertEqual([o.order_id for o in orders], ["2", "1"])                       # newest first
        self.assertEqual(orders[0].name, "market:1")                                     # unknown market -> placeholder symbol

    def test_order_status_scans_active_then_inactive(self):
        rest = _lighter(self._router, auth_token="ro:6:all:9999999999:abcdef")
        self.assertEqual(rest.get_order_status("2").status, "open")
        self.assertEqual(rest.get_order_status("9").status, "filled")
        self.assertIsNone(rest.get_order_status("404"))

    def test_trades_walk_cursor_only_as_far_as_needed(self):
        rest = _lighter(self._router, auth_token="ro:6:all:9999999999:abcdef")
        trades = rest.get_recent_trades(3)
        self.assertEqual(len(trades), 3)
        self.assertEqual([t.trade_id for t in trades], ["145", "145", "9"])
        calls = [c for c in rest._auth_session.calls if c["url"].endswith("/trades")]
        self.assertEqual([c["params"]["limit"] for c in calls], [3, 1])
        self.assertEqual(calls[1]["params"]["cursor"], "c2")
        self.assertEqual(len(rest.get_recent_trades(1)), 1)

    def test_funding_window_in_seconds_and_newest_first(self):
        rest = _lighter(self._router, auth_token="ro:6:all:9999999999:abcdef")
        pays = rest.get_funding_payments(1_640_000_000_000, 1_641_000_000_000)
        call = rest._auth_session.calls[-1]["params"]
        self.assertEqual((call["start_timestamp"], call["end_timestamp"]), (1_640_000_000, 1_641_000_000))
        self.assertEqual([p.timestamp_ms for p in pays], [1640995300000, 1640995200000])

    def test_api_error_code_raises(self):
        rest = _lighter(lambda url, params: {"code": 21100, "message": "invalid auth"}, auth_token="ro:6:all:9999999999:abcdef")
        with self.assertRaises(RuntimeError) as cm:
            rest.get_open_orders()
        self.assertIn("invalid auth", str(cm.exception))


# --- shared handlers ---------------------------------------------------------

class AccountHandlersTest(unittest.TestCase):
    def setUp(self):
        self.rest = _StubAccountRest()
        self.d = _StubDispatcher(self.rest)

    def test_routing_table_matches_polymarket_names(self):
        self.assertEqual(
            set(self.d.account_routing_table()),
            {"get_balance", "get_positions", "get_orders", "get_order_status", "get_trades", "get_funding_payments"},
        )

    def test_unconfigured_dispatcher(self):
        d = _StubDispatcher(None)
        with self.assertRaises(ers.AccountNotConfiguredError):
            d._handle_get_balance(_args({}))

    def test_balance_and_venue_arg_passthrough(self):
        out = self.d._handle_get_balance(_args({"dex": "xyz"}))
        self.assertEqual(out["account"], "0xmaster")
        self.assertEqual(out["account_value"], "13109.482328")
        self.assertEqual(self.rest.calls[-1], ("balance", {"dex": "xyz"}))
        self.d._handle_get_balance(_args(None))        # null data == no args
        self.assertEqual(self.rest.calls[-1], ("balance", {}))

    def test_pagination_defaults_and_offsets(self):
        out = self.d._handle_get_positions(_args({}))
        self.assertEqual(len(out["positions"]), acct.DEFAULT_PAGE_SIZE)
        self.assertEqual(self.rest.calls[-1], ("positions", {}))              # offset/limit are not forwarded
        out = self.d._handle_get_orders(_args({"offset": 50, "limit": 100}))
        self.assertEqual(len(out["orders"]), 10)
        self.assertEqual(self.d._handle_get_orders(_args({"offset": 999}))["orders"], [])
        with self.assertRaises(ers.MissingArgumentError):
            self.d._handle_get_orders(_args({"offset": -1}))

    def test_order_status(self):
        with self.assertRaises(ers.MissingArgumentError):
            self.d._handle_get_order_status(_args({}))
        out = self.d._handle_get_order_status(_args({"order_id": 91490942}))
        self.assertTrue(out["found"])
        self.assertEqual(self.rest.calls[-1], ("status", "91490942", {}))     # ids are always strings
        self.assertEqual(self.d._handle_get_order_status(_args({"order_id": "nope"})), {"found": False, "order": None})

    def test_trades_request_only_the_page(self):
        out = self.d._handle_get_trades(_args({"offset": 10, "limit": 5}))
        self.assertEqual(len(out["trades"]), 5)
        self.assertEqual(self.rest.calls[-1], ("trades", 15, {}))

    def test_funding_window_defaults(self):
        before = int(time.time() * 1000)
        out = self.d._handle_get_funding_payments(_args({}))
        self.assertGreaterEqual(out["end_time"], before)
        self.assertEqual(out["end_time"] - out["start_time"], acct.DEFAULT_FUNDING_LOOKBACK_MS)
        self.assertEqual(len(out["funding_payments"]), acct.DEFAULT_PAGE_SIZE)
        out = self.d._handle_get_funding_payments(_args({"start_time": 5, "end_time": 10, "limit": 2}))
        self.assertEqual(self.rest.calls[-1], ("funding", 5, 10, {}))
        self.assertEqual(len(out["funding_payments"]), 2)
        with self.assertRaises(ers.MissingArgumentError):
            self.d._handle_get_funding_payments(_args({"start_time": 10, "end_time": 5}))


class WireBudgetTest(unittest.TestCase):
    """A default page of the fattest records must fit a single Protocol 1 packet."""

    def _assert_fits(self, payload: dict):
        encoded = OutboundMessage(action="response", data=payload, correlation_id="x").convert_to_protocol_1()
        self.assertLessEqual(len(encoded), 10_000)

    def test_default_pages_fit_protocol_1(self):
        rest = _StubAccountRest(n=acct.DEFAULT_PAGE_SIZE)
        d = _StubDispatcher(rest)
        self._assert_fits(d._handle_get_positions(_args({})))
        self._assert_fits(d._handle_get_orders(_args({})))
        self._assert_fits(d._handle_get_trades(_args({})))
        self._assert_fits(d._handle_get_funding_payments(_args({})))
        # Lighter trades are the largest record on either venue.
        trade = lighter_cls.LighterTrade.from_dict(LIGHTER_TRADE).to_common(6, "ETH")
        self._assert_fits({"trades": [trade.to_dict()] * acct.DEFAULT_PAGE_SIZE})

    def test_oversized_page_is_rejected_not_truncated(self):
        trade = lighter_cls.LighterTrade.from_dict({**LIGHTER_TRADE, "tx_hash": "0x" + "f" * 64}).to_common(6, "ETH")
        page = [trade.to_dict() for _ in range(2000)]
        raw = len(json.dumps(page))
        self.assertGreater(raw, 9500)
        with self.assertRaises(ers.PacketTooLargeError):
            OutboundMessage(action="response", data={"trades": page}, correlation_id="x").convert_to_protocol_1()


if __name__ == "__main__":
    unittest.main()
