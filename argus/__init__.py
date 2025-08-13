"""
Argus: A Python Library for Financial Real-Time & Historical Data

The following sources are available through Argus:
- Interactive Brokers (IB): Real-time and historical data for stocks, options, futures, and forex.
- TradingView (TV): Real-time and historical data for stocks, forex, and cryptocurrencies.
- Nasdaq: Historical data for stocks and ETFs.
"""
from argus.ib import *
from argus.capital import *
from argus.tv import ChartSession, QuoteSession, multisymbol, MarketData
