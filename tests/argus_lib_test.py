"""General Tests for the Argus package."""
import sys
import unittest
import importlib

class TestArgusLib(unittest.TestCase):
    """Test the Argus library imports."""

    def imports(self):
        """Test if the Argus library imports correctly."""
        try:
            import argus.ib as ib
            import argus.capital as capital
            from argus.nasdaq import NASDAQDataDownloader
            from argus.tv import ChartSession, QuoteSession, multisymbol, MarketData
            from argus.polymarket import (
                PolymarketAPI,
                PolyDispatcher,
                PolyApiException,
                PMarket,
                PMarketToken,
            )

            del ib, capital, NASDAQDataDownloader, ChartSession, QuoteSession, multisymbol
            del MarketData, PolymarketAPI, PolyDispatcher, PolyApiException, PMarket, PMarketToken
        except ImportError as e:
            self.fail(f"Import failed: {e}")

    def test_import_with_cached_values(self):
        """Known issue with polymarket cached values, this test ensures it doesn't break."""
        try:
            import argus.polymarket
            # set some cached values into PolyCache
            argus.polymarket._POLYCACHE.set(
                key='test_key',
                value=[argus.polymarket.PMarket.dummy_init() for _ in range(10)]
            )
            importlib.reload(argus.polymarket)
            self.imports()
            from argus.polymarket import _POLYCACHE
            val = _POLYCACHE.get('test_key')
            self.assertEqual(len(val), 10)
            _POLYCACHE.delete('test_key')

        except Exception as e:
            self.fail(f"Import with cached values failed: {e}")


if __name__ == '__main__':
    unittest.main()