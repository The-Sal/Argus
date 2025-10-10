import pandas as pd
from utils3 import assertTypes

class PMarketToken:
    """
    tokens:
    {
      "token_id": "2006684570364241896555682377056547936147873475006821796350135481625081540323",
       "outcome": "Yes",
       "price": 0,
        "winner": false
    },
    """

    _types = {
        'token_id': str,
        'outcome': str,
        'price': float,
        'winner': bool,
    }

    def __init__(self, d: dict):
        self.token_id: str = d['token_id']
        self.outcome: str = d['outcome']
        self.price: float = d['price']
        self.winner: bool = d['winner']


    @staticmethod
    def make_list_init(array: list[dict]) -> list['PMarketToken']:
        return list(map(lambda x: PMarketToken(x), array))



class PMarket:
    """
    Based on:
    {
            "enable_order_book": false,
            "active": true,
            "closed": true,
            "archived": false,
            "accepting_orders": false,
            "accepting_order_timestamp": null,
            "minimum_order_size": 15,
            "minimum_tick_size": 0.01,
            "condition_id": "0x0de4ed9c811667ff9485e0f7aa3788a8db7c2147050f33defef2e2a302665433",
            "question_id": "0x9f9aa97d3b818387161a93d2fbe59f1642c87529839f393ca503e368800e665f",
            "question": "2022 NBA Finals: Who will win Celtics vs. Warriors Game 4?",
            "description": "The 2022 NBA Finals is the championship series of the National Basketball Association (NBA)'s 2021\u201322 season and conclusion of the season's playoffs. The finals follow a tournament format in a best-of-seven series. This season the finals are to be played between the Eastern Conference champions, the Boston Celtics, and the Western Conference champions, the Golden State Warriors.\n\nThis is a market on who will win Game 4 of the 2022 NBA Finals, a matchup scheduled for June 10, 2022 (9 PM ET).\n\nThis market will resolve to \u201cCeltics\u201d if the Boston Celtics win Game 4, and \u201cWarriors\u201d if the Golden State Warriors win. \n\nIf for any reason the winner of this game is not decided by June 30, 2022 (ET), this market will resolve 50-50.",
            "market_slug": "2022-nba-finals-who-will-win-celtics-vs-warriors-game-4",
            "end_date_iso": "2022-06-10T00:00:00Z",
            "game_start_time": null,
            "seconds_delay": 0,
            "fpmm": "0x44140477Eebf99286cAC5968B4c3E2Bdb5d4CC34",
            "maker_base_fee": 0,
            "taker_base_fee": 0,
            "notifications_enabled": true,
            "neg_risk": false,
            "neg_risk_market_id": "",
            "neg_risk_request_id": "",
            "icon": "https://polymarket-upload.s3.us-east-2.amazonaws.com/Repetitive-markets/Logo+NBA.png",
            "image": "https://polymarket-upload.s3.us-east-2.amazonaws.com/Repetitive-markets/Logo+NBA.png",
            "rewards": {
                "rates": null,
                "min_size": 0,
                "max_spread": 0
            },
            "is_50_50_outcome": false,
            "tokens": [
                {
                    "token_id": "45119618568427259556353873688243668201774394578145125280438548676993229690946",
                    "outcome": "Celtics",
                    "price": 0,
                    "winner": false
                },
                {
                    "token_id": "93862367603666595364600979608925062192225587043933771308112737299403278286433",
                    "outcome": "Warriors",
                    "price": 1,
                    "winner": false
                }
            ],
            "tags": [
                "All"
            ]
        },
    """

    _types = {
        'tokens': PMarketToken.make_list_init
    }


    def __init__(self, d: dict):
        self.enable_order_book: bool = d['enable_order_book']
        self.active: bool = d['active']
        self.closed: bool = d['closed']
        self.archived: bool = d['archived']
        self.accepting_orders: bool = d['accepting_orders']
        self.accepting_order_timestamp = d['accepting_order_timestamp']  # null
        self.minimum_order_size: int = d['minimum_order_size']
        self.minimum_tick_size: float = d['minimum_tick_size']
        self.condition_id: str = d['condition_id']
        self.question_id: str = d['question_id']
        self.question: str = d['question']
        self.description: str = d['description']
        self.market_slug: str = d['market_slug']
        self.end_date_iso: str = d['end_date_iso']
        self.game_start_time = d['game_start_time']  # null
        self.seconds_delay: int = d['seconds_delay']
        self.fpmm: str = d['fpmm']
        self.maker_base_fee: int = d['maker_base_fee']
        self.taker_base_fee: int = d['taker_base_fee']
        self.notifications_enabled: bool = d['notifications_enabled']
        self.neg_risk: bool = d['neg_risk']
        self.neg_risk_market_id: str = d['neg_risk_market_id']
        self.neg_risk_request_id: str = d['neg_risk_request_id']
        self.icon: str = d['icon']
        self.image: str = d['image']
        self.rewards: dict = d['rewards']
        self.is_50_50_outcome: bool = d['is_50_50_outcome']
        self.tokens: list[PMarketToken] = PMarketToken.make_list_init(d['tokens'])
        self.tags: list = d['tags']

        self._df: pd.DataFrame = None

    def to_dict(self):
        """Returns a dictionary representation of the market, including nested tokens as dictionaries."""
        copy_self = self.__dict__.copy()
        copy_self['tokens'] = list(map(lambda x: x.__dict__, copy_self['tokens']))
        return copy_self

    @property
    def df(self) -> pd.DataFrame:
        """Returns the market state as a pandas DataFrame, Returns none if not initialized yet."""
        return self._df

    @assertTypes([pd.DataFrame], auto_convert=False, class_method=True)
    def set_df(self, df: pd.DataFrame):
        """Sets the market state as a pandas DataFrame."""
        self._df = df

