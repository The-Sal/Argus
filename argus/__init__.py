"""
Argus: A Python Library for Financial Real-Time & Historical Data

The following sources are available through Argus:
- Interactive Brokers (IB): Real-time and historical data for stocks, options, futures, and forex.
- TradingView (TV): Real-time and historical data for stocks, forex, and cryptocurrencies.
- Nasdaq: Historical data for stocks and ETFs.
- Capital.com (Capital): Real-Time data
- Polymarket-Direct: Real-time market data from Polymarket prediction markets.
- Polymarket (polymarket): Real-time market data and order ability from Polymarket prediction markets.
"""

__version__ = '0.3.0'

# Warning: Kept for compatibility with a cache mechanism; do not remove
from argus._argus_utils import throw_fuss
