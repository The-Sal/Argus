"""
Argus: A Python Library for Financial Real-Time & Historical Data

The following sources are available through Argus:
- Interactive Brokers (IB): Real-time and historical data for stocks, options, futures, and forex.
- TradingView (TV): Real-time and historical data for stocks, forex, and cryptocurrencies.
- Nasdaq: Historical data for stocks and ETFs.
- Capital.com (Capital): Real-Time data
- Polymarket-Direct: Real-time market data from Polymarket prediction markets.
"""
from argus.ib import *
from argus.capital import *
from argus import polymarket_direct
from argus.nasdaq import NASDAQDataDownloader
from argus.tv import ChartSession, QuoteSession, multisymbol, MarketData

