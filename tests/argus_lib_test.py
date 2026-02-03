"""General Tests for the Argus package."""
import unittest

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
                PolymarketDispatcher,
                PolymarketEvent,
            )

            del MarketData, PolymarketDispatcher, PolymarketEvent
            del ib, capital, NASDAQDataDownloader, ChartSession, QuoteSession, multisymbol
        except ImportError as e:
            self.fail(f"Import failed: {e}")

    def test_imports(self):
        """Run the import tests."""
        self.imports()


if __name__ == '__main__':
    unittest.main()