import json
import logging
from datetime import datetime
from typing import List, Optional
from dataclasses import dataclass, fields


@dataclass
class Tag:
    """Represents a tag/label for categorizing markets"""
    id: Optional[str] = None
    label: Optional[str] = None
    slug: Optional[str] = None
    createdAt: Optional[str] = None
    forceShow: Optional[bool] = None
    updatedAt: Optional[str] = None
    isCarousel: Optional[bool] = None
    publishedAt: Optional[str] = None
    createdBy: Optional[int] = None
    updatedBy: Optional[int] = None
    forceHide: Optional[bool] = None

    @classmethod
    def from_dict(cls, data: dict) -> 'Tag':
        """Create a Tag from a dictionary, filtering unknown fields"""
        valid_fields = {f.name for f in fields(cls)}
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered_data)


@dataclass
class Series:
    """Represents a series of recurring markets"""
    # All fields now optional
    id: Optional[str] = None
    ticker: Optional[str] = None
    slug: Optional[str] = None
    title: Optional[str] = None
    active: Optional[bool] = None
    closed: Optional[bool] = None
    archived: Optional[bool] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None
    seriesType: Optional[str] = None
    recurrence: Optional[str] = None
    volume: Optional[float] = None
    liquidity: Optional[float] = None
    commentCount: Optional[int] = None
    image: Optional[str] = None
    icon: Optional[str] = None
    featured: Optional[bool] = None
    restricted: Optional[bool] = None

    @classmethod
    def from_dict(cls, data: dict) -> 'Series':
        """Create a Series from a dictionary, filtering unknown fields"""
        valid_fields = {f.name for f in fields(cls)}
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered_data)


@dataclass
class Market:
    """Represents an individual prediction market within a Polymarket event"""
    # All fields now optional
    id: Optional[str] = None
    question: Optional[str] = None
    slug: Optional[str] = None
    active: Optional[bool] = None
    closed: Optional[bool] = None
    conditionId: Optional[str] = None
    resolutionSource: Optional[str] = None
    endDate: Optional[str] = None
    liquidity: Optional[str] = None
    startDate: Optional[str] = None
    image: Optional[str] = None
    icon: Optional[str] = None
    description: Optional[str] = None
    outcomes: Optional[str] = None
    volume: Optional[str] = None
    marketMakerAddress: Optional[str] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None
    new: Optional[bool] = None
    featured: Optional[bool] = None
    archived: Optional[bool] = None
    restricted: Optional[bool] = None
    groupItemThreshold: Optional[str] = None
    questionID: Optional[str] = None
    enableOrderBook: Optional[bool] = None
    orderPriceMinTickSize: Optional[float] = None
    orderMinSize: Optional[int] = None
    volumeNum: Optional[float] = None
    liquidityNum: Optional[float] = None
    endDateIso: Optional[str] = None
    hasReviewedDates: Optional[bool] = None
    volume24hr: Optional[float] = None
    volume1wk: Optional[float] = None
    volume1mo: Optional[float] = None
    volume1yr: Optional[float] = None
    clobTokenIds: Optional[list] = None  # technically it comes in as a string, but it's a JSON string that needs to be parsed into a list
    volume24hrAmm: Optional[float] = None
    volume1wkAmm: Optional[float] = None
    volume1moAmm: Optional[float] = None
    volume1yrAmm: Optional[float] = None
    volume24hrClob: Optional[float] = None
    volume1wkClob: Optional[float] = None
    volume1moClob: Optional[float] = None
    volume1yrClob: Optional[float] = None
    volumeAmm: Optional[float] = None
    volumeClob: Optional[float] = None
    liquidityAmm: Optional[float] = None
    liquidityClob: Optional[float] = None
    acceptingOrders: Optional[bool] = None
    negRisk: Optional[bool] = None
    ready: Optional[bool] = None
    funded: Optional[bool] = None
    acceptingOrdersTimestamp: Optional[str] = None
    cyom: Optional[bool] = None
    competitive: Optional[int] = None
    pagerDutyNotificationEnabled: Optional[bool] = None
    approved: Optional[bool] = None
    rewardsMinSize: Optional[float] = None
    rewardsMaxSpread: Optional[float] = None
    spread: Optional[float] = None
    oneDayPriceChange: Optional[float] = None
    oneHourPriceChange: Optional[float] = None
    oneWeekPriceChange: Optional[float] = None
    oneMonthPriceChange: Optional[float] = None
    oneYearPriceChange: Optional[float] = None
    lastTradePrice: Optional[float] = None
    bestBid: Optional[float] = None
    bestAsk: Optional[float] = None
    automaticallyActive: Optional[bool] = None
    clearBookOnStart: Optional[bool] = None
    showGmpSeries: Optional[bool] = None
    showGmpOutcome: Optional[bool] = None
    manualActivation: Optional[bool] = None
    negRiskOther: Optional[bool] = None
    umaResolutionStatuses: Optional[str] = None
    pendingDeployment: Optional[bool] = None
    deploying: Optional[bool] = None
    rfqEnabled: Optional[bool] = None
    eventStartTime: Optional[str] = None
    holdingRewardsEnabled: Optional[bool] = None
    feesEnabled: Optional[bool] = None
    outcomePrices: Optional[str] = None
    startDateIso: Optional[str] = None
    # Additional optional fields found in the API
    submitted_by: Optional[str] = None
    resolvedBy: Optional[str] = None
    gameStartTime: Optional[str] = None
    secondsDelay: Optional[int] = None
    umaBond: Optional[str] = None
    umaReward: Optional[str] = None
    negRiskRequestID: Optional[str] = None
    customLiveness: Optional[int] = None
    sportsMarketType: Optional[str] = None
    deployingTimestamp: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> 'Market':
        """Create a Market from a dictionary, filtering unknown fields"""
        valid_fields = {f.name for f in fields(cls)}
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}

        # clobTokenIds is JSON string needs to be converted to list
        if 'clobTokenIds' in filtered_data and isinstance(filtered_data['clobTokenIds'], str):

            try:
                filtered_data['clobTokenIds'] = json.loads(filtered_data['clobTokenIds'])
            except json.JSONDecodeError:
                logging.warning(f"Could not decode clobTokenIds: {filtered_data['clobTokenIds']}")
                pass  # Keep as string if parsing fails

        # same with outcomes
        if 'outcomes' in filtered_data and isinstance(filtered_data['outcomes'], str):
            try:
                filtered_data['outcomes'] = json.loads(filtered_data['outcomes'])
            except json.JSONDecodeError:
                logging.warning(f"Could not decode outcomes: {filtered_data['outcomes']}")
                pass  # Keep as string if parsing fails

        return cls(**filtered_data)

    def convert_to_datetime(self):
        """Converts all non-None date strings (which are ET) into local datetime objects"""
        time_fields = ['endDate', 'startDate', 'createdAt', 'updatedAt',
                       'acceptingOrdersTimestamp', 'gameStartTime', 'deployingTimestamp',
                       'eventStartTime']  # Add this!
        for field in time_fields:
            date_str = getattr(self, field)
            # check if it's already a datetime object
            if isinstance(date_str, datetime):
                continue
            if date_str is not None:
                try:
                    dt_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                    setattr(self, field, dt_obj)
                except ValueError:
                    pass  # Keep original string if parsing fails


@dataclass
class PolymarketEvent:
    """Represents a top-level Polymarket prediction event"""
    # All fields now optional
    id: Optional[str] = None
    ticker: Optional[str] = None
    slug: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    resolutionSource: Optional[str] = None
    endDate: Optional[str] = None
    image: Optional[str] = None
    icon: Optional[str] = None
    active: Optional[bool] = None
    closed: Optional[bool] = None
    archived: Optional[bool] = None
    new: Optional[bool] = None
    featured: Optional[bool] = None
    restricted: Optional[bool] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None
    enableOrderBook: Optional[bool] = None
    negRisk: Optional[bool] = None
    commentCount: Optional[int] = None
    cyom: Optional[bool] = None
    showAllOutcomes: Optional[bool] = None
    showMarketImages: Optional[bool] = None
    enableNegRisk: Optional[bool] = None
    automaticallyActive: Optional[bool] = None
    seriesSlug: Optional[str] = None
    negRiskAugmented: Optional[bool] = None
    pendingDeployment: Optional[bool] = None
    deploying: Optional[bool] = None
    startDate: Optional[str] = None
    creationDate: Optional[str] = None
    # Collections - default to None instead of empty lists
    markets: Optional[List[Market]] = None
    series: Optional[List[Series]] = None
    tags: Optional[List[Tag]] = None
    # Optional fields that may appear in API responses
    openInterest: Optional[float] = None
    startTime: Optional[str] = None
    volume: Optional[float] = None
    volume24hr: Optional[float] = None
    volume1wk: Optional[float] = None
    volume1mo: Optional[float] = None
    volume1yr: Optional[float] = None
    liquidity: Optional[float] = None
    liquidityClob: Optional[float] = None
    liquidityAmm: Optional[float] = None
    competitive: Optional[int] = None
    volumeNum: Optional[float] = None
    liquidityNum: Optional[float] = None
    eventDate: Optional[str] = None
    eventWeek: Optional[int] = None
    gameId: Optional[int] = None
    homeTeamName: Optional[str] = None
    awayTeamName: Optional[str] = None

    def __post_init__(self):
        """Initialize lists if None and set defaults"""
        if self.markets is None:
            self.markets = []
        if self.series is None:
            self.series = []
        if self.tags is None:
            self.tags = []

        # If startDate is missing but startTime exists, use startTime
        if self.startDate is None and self.startTime is not None:
            self.startDate = self.startTime

        # If creationDate is missing but createdAt exists, use createdAt
        if self.creationDate is None and self.createdAt is not None:
            self.creationDate = self.createdAt

    @classmethod
    def from_dict(cls, data: dict) -> 'PolymarketEvent':
        """
        Create a PolymarketEvent from a dictionary, automatically filtering unknown fields

        Args:
            data: Dictionary containing Polymarket event data

        Returns:
            PolymarketEvent instance
        """
        # Parse nested tags using from_dict to filter unknown fields
        tags = [Tag.from_dict(tag) for tag in data.get('tags', [])]

        # Parse nested series using from_dict to filter unknown fields
        series = [Series.from_dict(s) for s in data.get('series', [])]

        # Parse nested markets using from_dict to filter unknown fields
        markets = [Market.from_dict(m) for m in data.get('markets', [])]

        # Get all field names from this dataclass
        valid_fields = {f.name for f in fields(cls)}

        # Create event object, filtering out unknown fields
        event_data = {k: v for k, v in data.items() if k in valid_fields and k not in ['tags', 'series', 'markets']}
        event_data['tags'] = tags
        event_data['series'] = series
        event_data['markets'] = markets

        return cls(**event_data)

    def convert_to_datetime(self):
        """Converts all non-None date strings (which are ET) into local datetime objects"""
        time_fields = ['endDate', 'startDate', 'createdAt', 'updatedAt', 'creationDate']
        for field in time_fields:
            date_str = getattr(self, field)
            if date_str is not None:
                try:
                    dt_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                    setattr(self, field, dt_obj)
                except ValueError:
                    pass  # Keep original string if parsing fails

        for mkt in self.markets:
            mkt.convert_to_datetime()

    def to_dict(self):
        return {
            'id': self.id,
            'ticker': self.ticker,
            'slug': self.slug,
            'title': self.title,
            'description': self.description,
            'resolutionSource': self.resolutionSource,
            'endDate': self.endDate,
            'image': self.image,
            'icon': self.icon,
            'active': self.active,
            'closed': self.closed,
            'archived': self.archived,
            'new': self.new,
            'featured': self.featured,
            'restricted': self.restricted,
            'createdAt': self.createdAt,
            'updatedAt': self.updatedAt,
            'enableOrderBook': self.enableOrderBook,
            'negRisk': self.negRisk,
            'commentCount': self.commentCount,
            'markets': [mkt.__dict__ for mkt in self.markets],
            'series': [s.__dict__ for s in self.series],
            'tags': [tag.__dict__ for tag in self.tags],
            'cyom': self.cyom,
            'showAllOutcomes': self.showAllOutcomes,
            'showMarketImages': self.showMarketImages,
            'enableNegRisk': self.enableNegRisk,
            'automaticallyActive': self.automaticallyActive,
            'seriesSlug': self.seriesSlug,
            'negRiskAugmented': self.negRiskAugmented,
            'pendingDeployment': self.pendingDeployment,
            'deploying': self.deploying,
            'startDate': self.startDate,
            'creationDate': self.creationDate
        }


# Helper function to parse a Polymarket event from dictionary
def parse_polymarket_event(data: dict) -> PolymarketEvent:
    """
    Parse a dictionary into a PolymarketEvent dataclass instance

    Args:
        data: Dictionary containing Polymarket event data

    Returns:
        PolymarketEvent instance
    """
    return PolymarketEvent.from_dict(data)


# Example usage
if __name__ == "__main__":
    # Example: parsing a single market event using from_dict (recommended)
    sample_data = {
        'id': '67413',
        'ticker': 'eth-updown-15m-1761711300',
        'slug': 'eth-updown-15m-1761711300',
        'title': 'Ethereum Up or Down - October 29, 12:15AM-12:30AM ET',
        'description': 'This market will resolve to "Up" if...',
        'resolutionSource': 'https://data.chain.link/streams/eth-usd',
        'creationDate': '2025-10-29T01:17:34.071099Z',
        'endDate': '2025-10-29T04:30:00Z',
        'image': 'https://polymarket-upload.s3.us-east-2.amazonaws.com/ETH+fullsize.jpg',
        'icon': 'https://polymarket-upload.s3.us-east-2.amazonaws.com/ETH+fullsize.jpg',
        'active': True,
        'closed': False,
        'archived': False,
        'new': False,
        'featured': False,
        'restricted': True,
        'openInterest': 0,
        'createdAt': '2025-10-29T01:16:51.713711Z',
        'updatedAt': '2025-10-29T01:17:34.071105Z',
        'enableOrderBook': True,
        'negRisk': False,
        'commentCount': 0,
        'markets': [],
        'series': [],
        'tags': [],
        'cyom': False,
        'showAllOutcomes': True,
        'showMarketImages': True,
        'enableNegRisk': False,
        'automaticallyActive': True,
        'startTime': '2025-10-29T04:15:00Z',
        'seriesSlug': 'eth-up-or-down-15m',
        'negRiskAugmented': False,
        'pendingDeployment': False,
        'deploying': False,
        'unknown_field': 'This will be ignored'  # Unknown fields are automatically filtered
    }

    # Parse using from_dict (automatically handles unknown fields)
    event = PolymarketEvent.from_dict(sample_data)
    print(f"Event: {event.title}")
    print(f"Active: {event.active}")
    print(f"End Date: {event.endDate}")
    print(f"Start Date: {event.startDate}")
    print(f"Creation Date: {event.creationDate}")

    # Test with the error cases from your log
    print("\n" + "=" * 50)
    print("Testing with problematic data from error logs:")
    print("=" * 50 + "\n")

    # Test case 1: Event with startTime but no startDate
    test_data_1 = {
        'id': '67741',
        'ticker': 'sol-updown-15m-1761757200',
        'slug': 'sol-updown-15m-1761757200',
        'title': 'Solana Up or Down - October 29, 1:00PM-1:15PM ET',
        'description': 'This market will resolve to "Up"...',
        'resolutionSource': 'https://data.chain.link/streams/sol-usd',
        'endDate': '2025-10-29T17:15:00Z',
        'image': 'https://polymarket-upload.s3.us-east-2.amazonaws.com/SOL+fullsize.png',
        'icon': 'https://polymarket-upload.s3.us-east-2.amazonaws.com/SOL+fullsize.png',
        'active': True,
        'closed': False,
        'archived': False,
        'new': False,
        'featured': False,
        'restricted': True,
        'createdAt': '2025-10-29T14:01:51.394987Z',
        'updatedAt': '2025-10-29T14:02:16.710887Z',
        'enableOrderBook': True,
        'negRisk': False,
        'commentCount': 0,
        'markets': [],
        'series': [],
        'tags': [],
        'cyom': False,
        'showAllOutcomes': True,
        'showMarketImages': True,
        'enableNegRisk': False,
        'automaticallyActive': True,
        'startTime': '2025-10-29T17:00:00Z',
        'seriesSlug': 'sol-up-or-down-15m',
        'negRiskAugmented': False,
        'pendingDeployment': False,
        'deploying': False
    }

    try:
        event1 = PolymarketEvent.from_dict(test_data_1)
        print(f"✓ Successfully parsed event: {event1.title}")
        print(f"  Start Date: {event1.startDate}")
        print(f"  Creation Date: {event1.creationDate}\n")
    except Exception as e:
        print(f"✗ Failed to parse event: {e}\n")

    # Test case 2: Market with unknown fields
    market_with_unknown_fields = {
        'id': '655151',
        'question': 'Thunder vs. Clippers',
        'conditionId': '0xdaabc6970b0c19844e4babc0e71a3994877fad201eba43847d82a242eaae92c7',
        'slug': 'nba-okc-lac-2025-11-05',
        'resolutionSource': 'https://www.nba.com/',
        'endDate': '2025-11-05T04:00:00Z',
        'liquidity': '0',
        'startDate': '2025-10-29T14:01:25.085662Z',
        'image': 'https://polymarket-upload.s3.us-east-2.amazonaws.com/basketball.png',
        'icon': 'https://polymarket-upload.s3.us-east-2.amazonaws.com/basketball.png',
        'description': 'NBA game market',
        'outcomes': '["Thunder", "Clippers"]',
        'volume': '0',
        'active': True,
        'closed': False,
        'marketMakerAddress': '',
        'createdAt': '2025-10-29T14:00:14.152643Z',
        'updatedAt': '2025-10-29T14:01:25.085662Z',
        'new': False,
        'featured': False,
        'submitted_by': '0x91430CaD2d3975766499717fA0D66A78D814E5c5',  # Unknown field
        'archived': False,
        'resolvedBy': '0x65070BE91477460D8A7AeEb94ef92fe056C2f2A7',  # Unknown field
        'restricted': True,
        'groupItemThreshold': '0',
        'questionID': '0xa3f283507220ac91d1f2b9531905f978563b51606168a58929305229f8207fe2',
        'enableOrderBook': True,
        'orderPriceMinTickSize': 0.01,
        'orderMinSize': 5,
        'volumeNum': 0,
        'liquidityNum': 0,
        'endDateIso': '2025-11-05',
        'hasReviewedDates': True,
        'volume24hr': 0,
        'volume1wk': 0,
        'volume1mo': 0,
        'volume1yr': 0,
        'gameStartTime': '2025-11-05 04:00:00+00',  # Now included
        'secondsDelay': 3,  # Now included
        'clobTokenIds': '["token1", "token2"]',
        'umaBond': '500',  # Now included
        'umaReward': '2',  # Now included
        'volume24hrAmm': 0,
        'volume1wkAmm': 0,
        'volume1moAmm': 0,
        'volume1yrAmm': 0,
        'volume24hrClob': 0,
        'volume1wkClob': 0,
        'volume1moClob': 0,
        'volume1yrClob': 0,
        'volumeAmm': 0,
        'volumeClob': 0,
        'liquidityAmm': 0,
        'liquidityClob': 0,
        'customLiveness': 0,  # Now included
        'acceptingOrders': True,
        'negRisk': False,
        'negRiskRequestID': '',  # Now included
        'ready': False,
        'funded': False,
        'acceptingOrdersTimestamp': '2025-10-29T14:01:03Z',
        'cyom': False,
        'competitive': 0,
        'pagerDutyNotificationEnabled': False,
        'approved': True,
        'rewardsMinSize': 0,
        'rewardsMaxSpread': 0,
        'spread': 1,
        'oneDayPriceChange': 0,
        'oneHourPriceChange': 0,
        'oneWeekPriceChange': 0,
        'oneMonthPriceChange': 0,
        'oneYearPriceChange': 0,
        'lastTradePrice': 0,
        'bestBid': 0,
        'bestAsk': 1,
        'automaticallyActive': True,
        'clearBookOnStart': True,
        'manualActivation': False,
        'negRiskOther': False,
        'sportsMarketType': 'moneyline',  # Now included
        'umaResolutionStatuses': '[]',
        'pendingDeployment': False,
        'deploying': False,
        'deployingTimestamp': '2025-10-29T14:00:34.528007Z',  # Now included
        'rfqEnabled': False,
        'holdingRewardsEnabled': False,
        'feesEnabled': False,
        'showGmpSeries': False,
        'showGmpOutcome': False,
        'eventStartTime': '2025-11-05T04:00:00Z'
    }

    try:
        market = Market.from_dict(market_with_unknown_fields)
        print(f"✓ Successfully parsed market: {market.question}")
        print(f"  Submitted by: {market.submitted_by}")
        print(f"  Sports market type: {market.sportsMarketType}\n")
    except Exception as e:
        print(f"✗ Failed to parse market: {e}\n")
