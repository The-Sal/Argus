from dataclasses import dataclass
from typing import Dict, Optional, Any

@dataclass
class SearchResult:
    """Represents a search result for a contract."""
    conid: str
    companyHeader: str
    companyName: str
    symbol: str
    description: str
    restricted: Optional[str]
    sections: list[dict[str, Any]]

    def __str__(self) -> str:
        """Return a string representation of the search result."""
        return f"{self.companyHeader} ({self.symbol}) - {self.description}"

    def __init__(self, conid: str, companyHeader: str, companyName: str,
                 symbol: str, description: str, restricted: Optional[str],
                 sections: list[dict[str, Any]], **kwargs: Optional[dict[str, Any]]):

        """Initialize the SearchResult object."""
        self.conid = conid
        self.companyHeader = companyHeader
        self.companyName = companyName
        self.symbol = symbol
        self.description = description
        self.restricted = restricted
        self.sections = sections
        for key, value in kwargs.items():
            setattr(self, key, value)



class IBKRFields:
    """
    A class that maps IBKR field names to their respective field codes.

    This implementation explicitly defines all fields as class attributes
    to enable IDE autocompletion and type checking support.

    Usage:
    fields = IBKRFields
    print(fields.LAST_PRICE)  # Returns 31
    print(fields.get_description(31))  # Returns description for field 31
    """

    # Field Codes as Class Constants
    LAST_PRICE = 31
    SYMBOL = 55
    TEXT = 58
    HIGH = 70
    LOW = 71
    MARKET_VALUE = 73
    AVG_PRICE = 74
    UNREALIZED_PNL = 75
    FORMATTED_POSITION = 76
    FORMATTED_UNREALIZED_PNL = 77
    DAILY_PNL = 78
    REALIZED_PNL = 79
    UNREALIZED_PNL_PERCENT = 80
    CHANGE = 82
    CHANGE_PERCENT = 83
    BID_PRICE = 84
    ASK_SIZE = 85
    ASK_PRICE = 86
    VOLUME = 87
    BID_SIZE = 88
    EXCHANGE = 6004
    CONID = 6008
    SEC_TYPE = 6070
    MONTHS = 6072
    REGULAR_EXPIRY = 6073
    MARKET_DATA_MARKER = 6119
    UNDERLYING_CONID = 6457
    SERVICE_PARAMS = 6508
    MARKET_DATA_AVAILABILITY = 6509
    COMPANY_NAME = 7051
    ASK_EXCHANGE = 7057
    LAST_EXCHANGE = 7058
    LAST_SIZE = 7059
    BID_EXCHANGE = 7068
    IMPLIED_VOL_HIST_VOL_PERCENT = 7084
    PUT_CALL_INTEREST = 7085
    PUT_CALL_VOLUME = 7086
    HIST_VOL_PERCENT = 7087
    HIST_VOL_CLOSE_PERCENT = 7088
    OPT_VOLUME = 7089
    CONID_EXCHANGE = 7094
    CAN_BE_TRADED = 7184
    CONTRACT_DESCRIPTION1 = 7219
    CONTRACT_DESCRIPTION2 = 7220
    LISTING_EXCHANGE = 7221
    INDUSTRY = 7280
    CATEGORY = 7281
    AVERAGE_VOLUME = 7282
    OPTION_IMPLIED_VOL_PERCENT = 7283
    HISTORICAL_VOLATILITY_PERCENT = 7284
    PUT_CALL_RATIO = 7285
    DIVIDEND_AMOUNT = 7286
    DIVIDEND_YIELD_PERCENT = 7287
    DIVIDEND_EX_DATE = 7288
    MARKET_CAP = 7289
    PE_RATIO = 7290
    EPS = 7291
    COST_BASIS = 7292
    WEEK_52_HIGH = 7293
    WEEK_52_LOW = 7294
    OPEN = 7295
    CLOSE = 7296
    DELTA = 7308
    GAMMA = 7309
    THETA = 7310
    VEGA = 7311
    OPT_VOLUME_CHANGE_PERCENT = 7607
    IMPLIED_VOL_PERCENT = 7633
    MARK = 7635
    SHORTABLE_SHARES = 7636
    FEE_RATE = 7637
    OPTION_OPEN_INTEREST = 7638
    PERCENT_OF_MARK_VALUE = 7639
    SHORTABLE = 7644
    MORNINGSTAR_RATING = 7655
    DIVIDENDS = 7671
    DIVIDENDS_TTM = 7672
    EMA_200 = 7674
    EMA_100 = 7675
    EMA_50 = 7676
    EMA_20 = 7677
    PRICE_EMA_200 = 7678
    PRICE_EMA_100 = 7679
    PRICE_EMA_50 = 7724
    PRICE_EMA_20 = 7681
    CHANGE_SINCE_OPEN = 7682
    UPCOMING_EVENT = 7683
    UPCOMING_EVENT_DATE = 7684
    UPCOMING_ANALYST_MEETING = 7685
    UPCOMING_EARNINGS = 7686
    UPCOMING_MISC_EVENT = 7687
    RECENT_ANALYST_MEETING = 7688
    RECENT_EARNINGS = 7689
    RECENT_MISC_EVENT = 7690
    PROBABILITY_OF_MAX_RETURN1 = 7694
    BREAK_EVEN = 7695
    SPX_DELTA = 7696
    FUTURES_OPEN_INTEREST = 7697
    LAST_YIELD = 7698
    BID_YIELD = 7699
    PROBABILITY_OF_MAX_RETURN2 = 7700
    PROBABILITY_OF_MAX_LOSS = 7702
    PROFIT_PROBABILITY = 7703
    ORGANIZATION_TYPE = 7704
    DEBT_CLASS = 7705
    RATINGS = 7706
    BOND_STATE_CODE = 7707
    BOND_TYPE = 7708
    LAST_TRADING_DATE = 7714
    ISSUE_DATE = 7715
    BETA = 7718
    ASK_YIELD = 7720
    PRIOR_CLOSE = 7741
    VOLUME_LONG = 7762
    HAS_TRADING_PERMISSIONS = 7768
    DAILY_PNL_RAW = 7920
    COST_BASIS_RAW = 7921

    # Static description dictionary
    _DESCRIPTIONS: Dict[int, str] = {
        31: "Last Price. The last price at which the contract traded. May contain one of the following prefixes: C - Previous day's closing price. H - Trading has halted.",
        55: "Symbol.",
        58: "Text.",
        70: "High. Current day high price",
        71: "Low. Current day low price",
        73: "Market Value. The current market value of your position in the security. Market Value is calculated with real time market data (even when not subscribed to market data).",
        74: "Avg Price. The average price of the position.",
        75: "Unrealized PnL. Unrealized profit or loss. Unrealized PnL is calculated with real time market data (even when not subscribed to market data).",
        76: "Formatted position.",
        77: "Formatted Unrealized PnL.",
        78: "Daily PnL. Your profit or loss of the day since prior close. Daily PnL is calculated with real time market data (even when not subscribed to market data).",
        79: "Realized PnL. Realized profit or loss. Realized PnL is calculated with real time market data (even when not subscribed to market data).",
        80: "Unrealized PnL %. Unrealized profit or loss expressed in percentage.",
        82: "Change. The difference between the last price and the close on the previous trading day",
        83: "Change %. The difference between the last price and the close on the previous trading day in percentage.",
        84: "Bid Price. The highest-priced bid on the contract.",
        85: "Ask Size. The number of contracts or shares offered at the ask price. For US stocks",
        86: "Ask Price. The lowest-priced offer on the contract.",
        87: "Volume. Volume for the day",
        88: "Bid Size. The number of contracts or shares bid for at the bid price. For US stocks",
        6004: "Exchange.",
        6008: "Conid. Contract identifier from IBKR's database.",
        6070: "SecType. The asset class of the instrument.",
        6072: "Months.",
        6073: "Regular Expiry.",
        6119: "Marker for market data delivery method (similar to request id).",
        6457: "Underlying Conid. Use /trsrv/secdef to get more information about the security.",
        6508: "Service Params..",
        6509: "Market Data Availability.",
        7051: "Company name.",
        7057: "Ask Exch.",
        7058: "Last Exch.",
        7059: "Last Size. The number of unites traded at the last price",
        7068: "Bid Exch.",
        7084: "Implied Vol./Hist. Vol %. The ratio of the implied volatility over the historical volatility, expressed as a percentage.",
        7085: "Put/Call Interest. Put option open interest/call option open interest for the trading day.",
        7086: "Put/Call Volume. Put option volume/call option volume for the trading day.",
        7087: "Hist. Vol. %. 30-day real-time historical volatility.",
        7088: "Hist. Vol. Close %. Shows the historical volatility based on previous close price.",
        7089: "Opt. Volume. Option Volume",
        7094: "Conid + Exchange.",
        7184: "canBeTraded. If contract is a trade-able instrument. Returns 1(true) or 0(false).",
        7219: "Contract Description.",
        7220: "Contract Description.",
        7221: "Listing Exchange.",
        7280: "Industry.",
        7281: "Category.",
        7282: "Average Volume. The average daily trading volume over 90 days.",
        7283: "Option Implied Vol. %.",
        7284: "Historical volatility %. Deprecated",
        7285: "Put/Call Ratio.",
        7286: "Dividend Amount. Displays the amount of the next dividend.",
        7287: "Dividend Yield %.",
        7288: "Ex-date of the dividend.",
        7289: "Market Cap.",
        7290: "P/E.",
        7291: "EPS.",
        7292: "Cost Basis.",
        7293: "52 Week High. The highest price for the past 52 weeks.",
        7294: "52 Week Low. The lowest price for the past 52 weeks.",
        7295: "Open. Today's opening price.",
        7296: "Close. Today's closing price.",
        7308: "Delta.",
        7309: "Gamma.",
        7310: "Theta.",
        7311: "Vega.",
        7607: "Opt. Volume Change %.",
        7633: "Implied Vol. %.",
        7635: "Mark.",
        7636: "Shortable Shares.",
        7637: "Fee Rate.",
        7638: "Option Open Interest.",
        7639: "% of Mark Value.",
        7644: "Shortable.",
        7655: "Morningstar Rating.",
        7671: "Dividends.",
        7672: "Dividends TTM.",
        7674: "EMA(200).",
        7675: "EMA(100).",
        7676: "EMA(50).",
        7677: "EMA(20).",
        7678: "Price/EMA(200).",
        7679: "Price/EMA(100).",
        7724: "Price/EMA(50).",
        7681: "Price/EMA(20).",
        7682: "Change Since Open.",
        7683: "Upcoming Event.",
        7684: "Upcoming Event Date.",
        7685: "Upcoming Analyst Meeting.",
        7686: "Upcoming Earnings.",
        7687: "Upcoming Misc Event.",
        7688: "Recent Analyst Meeting.",
        7689: "Recent Earnings.",
        7690: "Recent Misc Event.",
        7694: "Probability of Max Return.",
        7695: "Break Even.",
        7696: "SPX Delta.",
        7697: "Futures Open Interest.",
        7698: "Last Yield.",
        7699: "Bid Yield.",
        7700: "Probability of Max Return.",
        7702: "Probability of Max Loss.",
        7703: "Profit Probability.",
        7704: "Organization Type.",
        7705: "Debt Class.",
        7706: "Ratings.",
        7707: "Bond State Code.",
        7708: "Bond Type.",
        7714: "Last Trading Date.",
        7715: "Issue Date.",
        7718: "Beta.",
        7720: "Ask Yield.",
        7741: "Prior Close.",
        7762: "Volume Long.",
        7768: "hasTradingPermissions.",
        7920: "Daily PnL Raw.",
        7921: "Cost Basis Raw."
    }

    @classmethod
    def get_description(cls, field_code: int) -> str:
        """Get the description for a field code"""
        return cls._DESCRIPTIONS.get(field_code, "Unknown field code")

    @classmethod
    def code_to_name(cls) -> Dict[int, str]:
        """Get mapping of field codes to their attribute names"""
        result = {}
        for attr_name in dir(cls):
            # Skip private attributes and methods
            if attr_name.startswith('_') or callable(getattr(cls, attr_name)):
                continue
            value = getattr(cls, attr_name)
            if isinstance(value, int):
                result[value] = attr_name
        return result

    @classmethod
    def name_to_code(cls) -> Dict[str, int]:
        """Get mapping of attribute names to field codes"""
        result = {}
        for attr_name in dir(cls):
            # Skip private attributes and methods
            if attr_name.startswith('_') or callable(getattr(cls, attr_name)):
                continue
            value = getattr(cls, attr_name)
            if isinstance(value, int):
                result[attr_name] = value
        return result


# Example usage
if __name__ == "__main__":
    # Access field codes as class constants
    print(f"Last Price field code: {IBKRFields.LAST_PRICE}")  # Returns: 31
    print(f"Volume field code: {IBKRFields.VOLUME}")  # Returns: 87

    # Get description for a field code
    print(f"Description for field 31: {IBKRFields.get_description(31)}")

    # Demonstrate autocompletion support (this is what an IDE would show)
    # IBKRFields.LA[TAB] would show all attributes starting with LA:
    # LAST_PRICE, LAST_EXCHANGE, LAST_SIZE, LAST_TRADING_DATE, LAST_YIELD

    # Test with a few examples
    test_fields = [IBKRFields.LAST_PRICE, IBKRFields.BID_PRICE, IBKRFields.ASK_PRICE,
                   IBKRFields.VOLUME, IBKRFields.MARKET_CAP]
    for code in test_fields:
        name = IBKRFields.code_to_name().get(code)
        print(f"{name}: {code} - {IBKRFields.get_description(code)}")