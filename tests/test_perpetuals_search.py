"""
Offline tests for the perpetuals search + single-funding-rate endpoints.

Covers the `search()` helper added to both venues' PerpetualsIndex, and the
`search_perpetuals` / `get_funding_rate` dispatcher handlers. The handlers are
called unbound against a minimal stub dispatcher (only `_all_perps` is used),
so no REST/websocket/env is required and these run offline.

Run with: pytest tests/test_perpetuals_search.py
"""
import unittest
from decimal import Decimal
from typing import Any

from argus._argus_utils import ArgsObject
from argus.perpetuals.shared import ers as shared_ers
from argus.perpetuals.hyper import HyperLiquidDispatcher
from argus.perpetuals.hyper import _classes as hyper_cls
from argus.perpetuals.lighter import LighterDispatcher
from argus.perpetuals.lighter import _classes as lighter_cls


# --- builders ----------------------------------------------------------------

def _hyper_perp(name: str, funding: str = "0.0000125", dex: str = "") -> hyper_cls.Perpetual:
    asset = hyper_cls.Asset.from_dict({"name": name, "szDecimals": 2, "maxLeverage": 10})
    context = hyper_cls.AssetContext.from_dict({
        "dayNtlVlm": "1000",
        "funding": funding,
        "markPx": "100",
        "openInterest": "10",
        "oraclePx": "100",
        "prevDayPx": "99",
    })
    return hyper_cls.Perpetual(dex=dex, asset=asset, context=context)


def _lighter_perp(symbol: str, market_id: int, funding: str | None = "0.0001") -> lighter_cls.Perpetual:
    market = lighter_cls.Market.from_dict({
        "symbol": symbol,
        "market_id": market_id,
        "market_type": "perp",
        "base_asset_id": 1,
        "quote_asset_id": 0,
        "status": "active",
        "taker_fee": "0",
        "is_taker_fee_enabled": False,
        "maker_fee": "0",
        "is_maker_fee_enabled": False,
        "liquidation_fee": "0",
        "min_base_amount": "0.001",
        "min_quote_amount": "1",
        "order_quote_limit": "1",
        "supported_size_decimals": 3,
        "supported_price_decimals": 2,
        "supported_quote_decimals": 2,
        "created_at": 0,
        "multiplier": "1",
        "size_decimals": 3,
        "price_decimals": 2,
        "quote_multiplier": 1,
        "default_initial_margin_fraction": 100,
        "min_initial_margin_fraction": 50,
        "maintenance_margin_fraction": 25,
        "closeout_margin_fraction": 10,
        "market_config": {
            "market_margin_mode": 0,
            "insurance_fund_account_index": 0,
            "liquidation_mode": 0,
            "force_reduce_only": False,
            "trading_hours": "24/7",
            "funding_fee_discounts_enabled": False,
            "hidden": False,
            "rfq_enabled": False,
        },
        "strategy_index": 0,
        "market_flags": 0,
        "funding_premium_multiplier": 1,
        "funding_clamp_small": "0",
        "funding_clamp_big": "0",
        "base_interest_rate": "0",
    })
    context = lighter_cls.MarketContext.from_dict({
        "mark_price": "100",
        "index_price": "100",
        "last_trade_price": "100",
        "daily_trades_count": 0,
        "daily_base_token_volume": "0",
        "daily_quote_token_volume": "0",
        "daily_price_low": "100",
        "daily_price_high": "100",
        "daily_price_change": "0",
        "open_interest": "0",
    })
    rate = Decimal(funding) if funding is not None else None
    return lighter_cls.Perpetual(market=market, context=context, funding_rate=rate)


class _StubPerps:
    def __init__(self, index):
        self.value = index


def _stub(index) -> Any:
    return type("_StubDispatcher", (), {"_all_perps": _StubPerps(index)})()


def _args(data: dict) -> ArgsObject:
    return ArgsObject(None, data)  # type: ignore[arg-type]


# --- search ranking ----------------------------------------------------------

class HyperSearchTest(unittest.TestCase):
    def setUp(self):
        self.index = hyper_cls.PerpetualsIndex([
            _hyper_perp("BTC"),
            _hyper_perp("ETH"),
            _hyper_perp("SOL"),
            _hyper_perp("xyz:AAPL", dex="xyz"),
            _hyper_perp("xyz:GOLD", dex="xyz"),
        ])

    def test_exact_match_ranks_first(self):
        self.assertEqual(self.index.search("BTC")[0], "BTC")

    def test_case_insensitive(self):
        self.assertEqual(self.index.search("btc")[0], "BTC")
        self.assertEqual(self.index.search("btc"), self.index.search("BTC"))

    def test_fuzzy_match_prefers_closest(self):
        self.assertEqual(self.index.search("BTO")[0], "BTC")

    def test_limit_is_respected(self):
        self.assertEqual(len(self.index.search("B", limit=2)), 2)

    def test_nonpositive_limit_returns_empty(self):
        self.assertEqual(self.index.search("BTC", limit=0), [])
        self.assertEqual(self.index.search("BTC", limit=-5), [])

    def test_empty_index_returns_empty(self):
        self.assertEqual(hyper_cls.PerpetualsIndex([]).search("BTC"), [])

    def test_hip3_names_are_searchable(self):
        self.assertEqual(self.index.search("xyz:AAPL")[0], "xyz:AAPL")


class LighterSearchTest(unittest.TestCase):
    def setUp(self):
        self.index = lighter_cls.PerpetualsIndex([
            _lighter_perp("BTC", 0),
            _lighter_perp("ETH", 1),
            _lighter_perp("SOL", 2),
            _lighter_perp("DOGE", 3),
        ])

    def test_exact_match_ranks_first(self):
        self.assertEqual(self.index.search("ETH")[0], "ETH")

    def test_case_insensitive(self):
        self.assertEqual(self.index.search("eth")[0], "ETH")

    def test_limit_is_respected(self):
        self.assertEqual(len(self.index.search("O", limit=2)), 2)

    def test_nonpositive_limit_returns_empty(self):
        self.assertEqual(self.index.search("BTC", limit=0), [])

    def test_empty_index_returns_empty(self):
        self.assertEqual(lighter_cls.PerpetualsIndex([]).search("BTC"), [])


# --- search_perpetuals handler ----------------------------------------------

class SearchHandlerTest(unittest.TestCase):
    def setUp(self):
        self.hyper_stub = _stub(hyper_cls.PerpetualsIndex([_hyper_perp("BTC"), _hyper_perp("ETH")]))
        self.lighter_stub = _stub(lighter_cls.PerpetualsIndex([_lighter_perp("BTC", 0), _lighter_perp("ETH", 1)]))

    def test_missing_keyword_raises(self):
        for handler, stub in (
            (HyperLiquidDispatcher._search_perpetuals, self.hyper_stub),
            (LighterDispatcher._search_perpetuals, self.lighter_stub),
        ):
            with self.assertRaises(shared_ers.MissingArgumentError):
                handler(stub, _args({}))

    def test_response_is_wrapped_under_perpetuals_key(self):
        for handler, stub in (
            (HyperLiquidDispatcher._search_perpetuals, self.hyper_stub),
            (LighterDispatcher._search_perpetuals, self.lighter_stub),
        ):
            result = handler(stub, _args({"keyword": "BTC"}))
            self.assertEqual(list(result.keys()), ["perpetuals"])
            self.assertEqual(result["perpetuals"][0], "BTC")

    def test_default_limit_is_ten(self):
        index = hyper_cls.PerpetualsIndex([_hyper_perp(f"COIN{i}") for i in range(15)])
        result = HyperLiquidDispatcher._search_perpetuals(_stub(index), _args({"keyword": "COIN"}))
        self.assertEqual(len(result["perpetuals"]), 10)


# --- get_funding_rate handler -----------------------------------------------

class HyperFundingRateHandlerTest(unittest.TestCase):
    def setUp(self):
        self.stub = _stub(hyper_cls.PerpetualsIndex([
            _hyper_perp("BTC", funding="0.0000125"),
            _hyper_perp("xyz:AAPL", funding="-0.00002", dex="xyz"),
        ]))

    def test_missing_symbol_raises(self):
        with self.assertRaises(shared_ers.MissingArgumentError):
            HyperLiquidDispatcher._get_funding_rate(self.stub, _args({}))

    def test_unknown_symbol_raises(self):
        with self.assertRaises(shared_ers.InvalidCoinError):
            HyperLiquidDispatcher._get_funding_rate(self.stub, _args({"symbol": "NOPE"}))

    def test_response_shape(self):
        result = HyperLiquidDispatcher._get_funding_rate(self.stub, _args({"symbol": "BTC"}))
        self.assertEqual(result["symbol"], "BTC")
        self.assertEqual(Decimal(result["funding_rate"]), Decimal("0.0000125"))
        self.assertEqual(Decimal(result["funding_rate_apr"]), Decimal("0.0000125") * 8760)

    def test_hip3_symbol_resolves_across_dexes(self):
        result = HyperLiquidDispatcher._get_funding_rate(self.stub, _args({"symbol": "xyz:AAPL"}))
        self.assertEqual(result["symbol"], "xyz:AAPL")
        self.assertEqual(Decimal(result["funding_rate"]), Decimal("-0.00002"))


class LighterFundingRateHandlerTest(unittest.TestCase):
    def setUp(self):
        self.stub = _stub(lighter_cls.PerpetualsIndex([
            _lighter_perp("BTC", 0, funding="0.0001"),
            _lighter_perp("ETH", 1, funding=None),
        ]))

    def test_missing_symbol_raises(self):
        with self.assertRaises(shared_ers.MissingArgumentError):
            LighterDispatcher._get_funding_rate(self.stub, _args({}))

    def test_unknown_symbol_raises(self):
        with self.assertRaises(shared_ers.InvalidCoinError):
            LighterDispatcher._get_funding_rate(self.stub, _args({"symbol": "NOPE"}))

    def test_response_shape(self):
        result = LighterDispatcher._get_funding_rate(self.stub, _args({"symbol": "BTC"}))
        self.assertEqual(result["symbol"], "BTC")
        self.assertEqual(Decimal(result["funding_rate"]), Decimal("0.0001"))
        self.assertEqual(Decimal(result["funding_rate_apr"]), Decimal("0.0001") * 8760)

    def test_missing_rate_is_null_not_stringified(self):
        result = LighterDispatcher._get_funding_rate(self.stub, _args({"symbol": "ETH"}))
        self.assertIsNone(result["funding_rate"])
        self.assertIsNone(result["funding_rate_apr"])


if __name__ == "__main__":
    unittest.main()
