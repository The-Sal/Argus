import os
import time
import socket
import logging
import threading
import traceback
from utils3.networking import Session as _RAW_SESSION
from argus.capital import DomainCache, CapitalComMKTDataLive
from argus._argus_utils import Notification

logger = logging.getLogger(__name__)


def expand_exception_decorator(func_uuid, propagate=True):
    """A decorator to expand exceptions and print them in a more readable format."""

    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                print("" + "=" * 80)
                print("An exception occurred in function:", func.__name__)
                print("Function UUID:", func_uuid)
                print("Arguments:", args, kwargs)
                print("PRINTING TRACEBACK:")
                traceback.print_exc()
                print("=" * 80 + "")
                time.sleep(1)
                if propagate:
                    raise e
        return wrapper
    return decorator


@expand_exception_decorator(func_uuid="enforce_currency_v1", propagate=True)
def enforce_currency(value, raise_on_fail=True, fallback=0.0) -> float:
    """Ensure the currency is converted into a float. Removing any codes or symbols."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        # only remove when starts with 'C'
        if value.startswith('C'):
            value = value[1:]
        # remove any leading or trailing non-numeric characters
        value = value.strip().lstrip('$').rstrip('USD').strip()
        try:
            return float(value)
        except ValueError:
            if raise_on_fail:
                raise ValueError(f"Cannot convert value to float: {value}")
            else:
                logger.warning(f"Cannot convert value to float: {value}, returning fallback: {fallback}")
                return fallback

    if raise_on_fail:
        raise TypeError(f"Unsupported type for currency conversion: {type(value)}")

    logger.warning(f"Unsupported type for currency conversion: {type(value)}, returning fallback: {fallback}")
    return fallback

# monkey patch for some sketchy stuff
setattr(socket.socket, 'idx', 'real')


class FakeSocket:
    # The problem is quite annoying, MKT-Dispatcher is designed around the idea
    # where it's internal ledger is socket-connections to clients and it automatically
    # manages sub/unsubs based on client connections we want a callback direcrly
    # from the Dispatcher without having to open a socket because it's internal-use
    # i.e. within the `AcounterLedger` class and we don't want to add
    # P2 serialization for an in memory passing of data but the entire design of
    # MKT-Dispatcher is based around sockets so we have to fake it. Now we could
    # contiously call isinstance on the socket object and see if it's a real
    # socket or fake but that would cause so much overhead compared to checking
    # one static property `idx` so we just add a property to the "real" for real sockets
    # and fake sockets and check that instead when data is being sent.
    # This allows the subscription model not to change, avoid massive refactoring
    # to create a channel just for AccountLedger without distributing other clients
    # who may also need the exact contract data. also idx is incredibly begin and does not
    # change anything fundamentally about the socket object whatsoever just adds a property.
    # Also since MKT-Dispatcher is the 'final' endpoint for this program [if you are using MKTDispatcher]
    # there is nothing else 'downstream' that needs to be changed from breaking.

    # The largest issue with modifying MKTDispatcher is that it's wrapped in thread-locks, concurrency,
    # multiplexing logic, load-balancing and various other logic that would be a nightmare to refactor
    # without breaking at least something since they all need to play nice together and to-date it's all been
    # stable for a while even under immense load so let's not play with it too much.

    # Also wondering this solves two problems:
    # 1. We can pass a callback function to MKTDispatcher that gets called when
    #    market data is received instead of having to open a socket connection.
    # 2. MKTDispatcher automatically mamanges connection by sending `pings` to clients
    #    and removing them if they are not responsive, because this will be a in-memory
    #    callback we don't have to worry about that since it will always be responsive
    #    meaning it will never be removed from the subscription list.
    #    In addition to this we have a backstop in IBWss another critical component that we did not want to modify
    #    we added the `protected_assets` set which is a list of contract IDs that cannot be unsubscribed from
    #    no matter what, this is to prevent accidental unsubscriptions from critical assets that must
    #    always be streamed which are also these assets this class is made to handle for AccountLedger.
    #    So 1) No Unsub by the automatic connection management in MKTDispatcher, 2) under the case it does get
    #    unsubscribed we have a backstop in IBWss to just throw an exception and make a big loud bang if that attempts to
    #    happen. Given IBWss is the socket-layer for MKTDispatcher this is the last line of defense.
    #    This is about the cleanest possible way to add AccountLedger functionality to MKTDispatcher
    #    without refactoring the entire codebase and risking breaking something.
    #    This way we can preserve the prior automatic contract sub/un-sub, load balancing, concurrency magic with very little mods
    def __init__(self, callback):
        self.callback = callback
        self.idx = 'fake'

    def sendall(self, data):
        self.callback(data)

class AbstractSocketMessage:
    def __init__(self, content, origin, timestamp=time.time()):
        self.content = content
        self.origin = origin
        self.timestamp = timestamp

    @property
    def as_dict(self):
        return {
            'content': self.content,
            'origin': self.origin,
            'timestamp': self.timestamp
        }

    def time(self):
        return self.timestamp

    def timestamp_str(self):
        return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.timestamp))


IB_Cache = DomainCache('IBKR')
NOTIFICATION = Notification(
    number=os.getenv("NOTIFICATION_NUMBER", None), active=True if os.getenv("NOTIFICATION_NUMBER", None) else False,
)


class LockedSession(_RAW_SESSION):
    """A session that is locked to prevent concurrent access."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lock = threading.Lock()

    def get(self, url, params=None, **kwargs):
        with self.lock:
            return super().get(url, params=params, **kwargs)

    # noinspection all
    def post(self, url, data=None, json=None, **kwargs):
        with self.lock:
            return super().post(url, data=data, json=json, **kwargs)


class IBKRModes:
    ASK = 'ASK'
    ASK_BID_LAST = 'ASK+BID+LAST'
    FULL_PKL = 'FULL_PKL'
    FULL_JSON = 'FULL_JSON'
    PROTOCOL_2 = 'PROTOCOL_2'


class IBKR_CapitalComMKTDataLive(CapitalComMKTDataLive):
    """This class is an extension of the CapitalComMKTDataLive class to support IBKR fields. Its only
    purpose is to conform with the 'transmit_mkt_data_with_protocol_2' function.
    NOTE: Given that this is an extended version of the CapitalComMKTDataLive class with additional
    attributes the DECODER should be updated to handle the additional fields and orders from protocol 2."""

    def __init__(self, shortable_shares, unrealized_pnl=0.0, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.shortable_shares = shortable_shares
        self.unrealized_pnl = unrealized_pnl

    @classmethod
    def from_capital_com(cls, shortable_shares, capital_com_data: CapitalComMKTDataLive):
        """Create an instance from a CapitalComMKTDataLive object."""
        return cls(
            shortable_shares=shortable_shares,
            symbol=capital_com_data.symbol,
            bid=capital_com_data.bid,
            bid_size=capital_com_data.bid_size,
            ask=capital_com_data.ask,
            ask_size=capital_com_data.ask_size,
            last=capital_com_data.last,
            last_size=capital_com_data.last_size
        )

    def transferable_2(self, **kwargs) -> bytes:
        """This function is used to convert the object to a dictionary that can be used with the protocol 2."""
        data: list[str] = super().transferable_2(encode=False)
        # print('Prior to inserting shortable_shares, data is:', data, 'length:', len(data))

        # Insert shortable_shares before the last two elements, that is before both timestamps (old capital.com and Python)

        # example ['bid', 'bid_size', 'ask', 'ask_size', 'last', 'last_size', 'timestamp1', 'timestamp2']
        # size=8
        # insert at index -3 to place before last two elements
        data.insert(len(data) - 2, str(self.shortable_shares))
        bytes_packet = ",".join(data).encode('ascii')
        # print('After inserting shortable_shares, data is:', data, 'length:', len(data))
        return bytes_packet


class IBError(Exception):
    pass


class AuthenticationTimeout(IBError):
    pass


class MarketDataRefused(IBError):
    pass


class MarketData:
    """User IBKRFields to query for market data"""

    def __init__(self, contract_id, server_id, contract_exchange, topic, data):
        self.contract_id = contract_id
        self.server_id = server_id
        self.contract_exchange = contract_exchange
        self.topic = topic
        self.data = data

    def get(self, field: int, default=None, strip_commas=True, string_values=True):
        a1 = self.data.get(str(field), None)
        a2 = self.data.get(int(field), None)

        if a1 == '':
            a1 = None
        if a2 == '':
            a2 = None

        final_value = a1 if a1 is not None else a2
        if final_value is None:
            final_value = default
            return final_value

        if strip_commas:
            final_value = str(final_value).replace(',', '')
        if string_values:
            final_value = str(final_value)

        return final_value


class Account:
    """A class representing an IBKR Account with it's own account id and all other data."""

    def __init__(self, account_id: str, **kwargs):
        """
        :param account_id: The account id of the IBKR account.
        :param kwargs: Additional attributes for the account.
        """
        self.account_id = account_id
        for key, value in kwargs.items():
            setattr(self, key, value)

    @classmethod
    def from_dict(cls, data: dict):
        """Create an Account instance from a dictionary."""
        account_id = data.get('accountId')
        if account_id is None:
            raise ValueError("Account ID is required to create an Account instance.")
        return cls(account_id=account_id, **data)

from typing import Dict, Optional


class AccountBalances:
    """
    Parses Interactive Brokers WebSocket 'spl' (Profit and Loss) topic data.

    Example data structure:
    {'topic': 'spl', 'args': {'U9126451.': {'rowType': 1, 'dpl': 15.72, 'nl': 624.7,
     'upl': 270.7, 'el': 0.92, 'uel': 0.92, 'mv': 623.2}}}
    """

    def __init__(
            self,
            daily_pnl: float,
            pnl: float,
            market_value: float,
            account_id: Optional[str] = None,
            net_liquidation: Optional[float] = None,
            excess_liquidity: Optional[float] = None,
            unrealized_excess_liquidity: Optional[float] = None,
            row_type: Optional[int] = None
    ):
        self.account_id = account_id
        self.daily_pnl = daily_pnl  # dpl - Daily Profit/Loss
        self.pnl = pnl  # upl - Unrealized Profit/Loss
        self.market_value = market_value  # mv - Margin Value
        self.net_liquidation = net_liquidation  # nl - Net Liquidation Value
        self.excess_liquidity = excess_liquidity  # el - Excess Liquidity
        self.unrealized_excess_liquidity = unrealized_excess_liquidity  # uel
        self.row_type = row_type

    @classmethod
    def from_dict(cls, data: Dict) -> 'AccountBalances':
        """
        Create AccountBalances instance from WebSocket dictionary response.

        Args:
            data: Dictionary with structure {'topic': 'spl', 'args': {...}}

        Returns:
            AccountBalances instance

        Raises:
            ValueError: If data structure is invalid
        """
        if data.get('topic') != 'spl':
            raise ValueError(f"Expected topic 'spl', got '{data.get('topic')}'")

        args = data.get('args', {})
        if not args:
            raise ValueError("No 'args' found in data")

        # Extract account ID and account data
        # The account key may have a trailing dot (e.g., 'U9126451.')
        account_id = next(iter(args.keys()))
        account_data = args[account_id]

        # Clean up account ID (remove trailing dot if present)
        clean_account_id = account_id.rstrip('.')

        return cls(
            account_id=clean_account_id,
            daily_pnl=account_data.get('dpl', 0.0),
            pnl=account_data.get('upl', 0.0),
            market_value=account_data.get('mv', 0.0),
            net_liquidation=account_data.get('nl'),
            excess_liquidity=account_data.get('el'),
            unrealized_excess_liquidity=account_data.get('uel'),
            row_type=account_data.get('rowType')
        )

    def __repr__(self) -> str:
        return (
            f"AccountBalances(account={self.account_id}, "
            f"daily_pnl={self.daily_pnl:.2f}, "
            f"pnl={self.pnl:.2f}, "
            f"net_liq={self.net_liquidation:.2f}, "
            f"mv={self.market_value:.2f}, "
            f"el={self.excess_liquidity:.2f})"
        )

    def __str__(self) -> str:
        return (
            f"Account: {self.account_id}\n"
            f"  Daily P&L: ${self.daily_pnl:.2f}\n"
            f"  Unrealized P&L: ${self.pnl:.2f}\n"
            f"  Net Liquidation: ${self.net_liquidation:.2f}\n"
            f"  Market Value: ${self.market_value:.2f}\n"
            f"  Excess Liquidity: ${self.excess_liquidity:.2f}"
        )

    def to_dict(self):
        ec = enforce_currency
        return {
            'account_id': self.account_id,
            'daily_pnl': ec(self.daily_pnl),
            'pnl': ec(self.pnl),
            'market_value': ec(self.market_value),
            'net_liquidation': ec(self.net_liquidation, raise_on_fail=False),
            'excess_liquidity': ec(self.excess_liquidity, raise_on_fail=False),
            'unrealized_excess_liquidity': ec(self.unrealized_excess_liquidity, raise_on_fail=False),
            'row_type': self.row_type
        }

class STK_Position:
    """A class representing an IBKR Stock Position with it's own account id and all other data.
    Based on the following type of asset:
       {
          "acctId":"0",
          "conid":0,
          "contractDesc":"TQQQ",
          "position":0.0,
          "mktPrice":0.0,
          "mktValue":0.0,
          "currency":"USD",
          "avgCost":0.0,
          "avgPrice":0.0,
          "realizedPnl":0.0,
          "unrealizedPnl":0.0,
          "exchs":"None",
          "expiry":"None",
          "putOrCall":"None",
          "multiplier":"None",
          "strike":0.0,
          "exerciseStyle":"None",
          "conExchMap":[

          ],
          "assetClass":"STK",
          "undConid":0
       }
    """

    def __init__(self, account_id: str, conid: int, contract_desc: str, position: float,
                 mkt_price: float, mkt_value: float, currency: str, avg_cost: float,
                 avg_price: float, realized_pnl: float, unrealized_pnl: float,
                 exchs: str, expiry: str, put_or_call: str, multiplier: str,
                 strike: float, exercise_style: str, con_exch_map: list,
                 asset_class: str, und_conid: int):
        self.account_id = account_id
        self.conid = conid
        self.contract_desc = contract_desc
        self.position = position
        self.mkt_price = mkt_price
        self.mkt_value = mkt_value
        self.currency = currency
        self.avg_cost = avg_cost
        self.avg_price = avg_price
        self.realized_pnl = realized_pnl
        self.unrealized_pnl = unrealized_pnl
        self.exchs = exchs
        self.expiry = expiry
        self.put_or_call = put_or_call
        self.multiplier = multiplier
        self.strike = strike
        self.exercise_style = exercise_style
        self.con_exch_map = con_exch_map
        self.asset_class = asset_class
        self.und_conid = und_conid

    @classmethod
    def from_dict(cls, data: dict):
        """Create an STK_Position instance from a dictionary."""
        account_id = data.get('acctId')
        conid = data.get('conid')
        contract_desc = data.get('contractDesc')
        position = data.get('position')
        mkt_price = data.get('mktPrice')
        mkt_value = data.get('mktValue')
        currency = data.get('currency')
        avg_cost = data.get('avgCost')
        avg_price = data.get('avgPrice')
        realized_pnl = data.get('realizedPnl')
        unrealized_pnl = data.get('unrealizedPnl')
        exchs = data.get('exchs')
        expiry = data.get('expiry')
        put_or_call = data.get('putOrCall')
        multiplier = data.get('multiplier')
        strike = data.get('strike')
        exercise_style = data.get('exerciseStyle')
        con_exch_map = data.get('conExchMap', [])
        asset_class = data.get('assetClass')
        und_conid = data.get('undConid')

        if account_id is None or conid is None:
            raise ValueError("Account ID and ConID are required to create an STK_Position instance.")

        return cls(
            account_id=account_id,
            conid=conid,
            contract_desc=contract_desc,
            position=position,
            mkt_price=mkt_price,
            mkt_value=mkt_value,
            currency=currency,
            avg_cost=avg_cost,
            avg_price=avg_price,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            exchs=exchs,
            expiry=expiry,
            put_or_call=put_or_call,
            multiplier=multiplier,
            strike=strike,
            exercise_style=exercise_style,
            con_exch_map=con_exch_map,
            asset_class=asset_class,
            und_conid=und_conid
        )

    def __repr__(self):
        return f"STK_Position(account_id={self.account_id}, conid={self.conid}, contract_desc={self.contract_desc}, position={self.position}, mkt_price={self.mkt_price}, mkt_value={self.mkt_value}, currency={self.currency}, avg_cost={self.avg_cost}, avg_price={self.avg_price}, realized_pnl={self.realized_pnl}, unrealized_pnl={self.unrealized_pnl}, exchs={self.exchs}, expiry={self.expiry}, put_or_call={self.put_or_call}, multiplier={self.multiplier}, strike={self.strike}, exercise_style={self.exercise_style}, con_exch_map={self.con_exch_map}, asset_class={self.asset_class}, und_conid={self.und_conid})"

    def to_dict(self):
        ec = enforce_currency
        return {
            'account_id': self.account_id,
            'conid': self.conid,
            'contract_desc': self.contract_desc,
            'position': ec(self.position),
            'mkt_price': ec(self.mkt_price),
            'mkt_value': ec(self.mkt_value),
            'currency': self.currency,
            'avg_cost': ec(self.avg_cost),
            'avg_price': ec(self.avg_price),
            'realized_pnl': ec(self.realized_pnl),
            'unrealized_pnl': ec(self.unrealized_pnl),
            'exchs': self.exchs,
            'expiry': self.expiry,
            'put_or_call': self.put_or_call,
            'multiplier': self.multiplier,
            'strike': self.strike,
            'exercise_style': self.exercise_style,
            'con_exch_map': self.con_exch_map,
            'asset_class': self.asset_class,
            'und_conid': self.und_conid
        }



# if __name__ == '__main__':
#     throw_fuss("Hello World!\nThis is a test of the emergency broadcast system.\nHave a nice day!")
#     throw_fuss("This is still a test of the emergency broadcast system.\nHave a nice day!", boarder="#")
#     # try using emojis
#     throw_fuss("This is an emergency broadcast system test.\nHave a nice day! 😊", boarder="🚨")
