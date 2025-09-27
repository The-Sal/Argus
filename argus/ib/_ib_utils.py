import os
import threading
from utils3.networking import Session as _RAW_SESSION
from argus.capital import DomainCache, CapitalComMKTDataLive
from argus._argus_utils import Notification, macos_notification_with_custom_sound

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
        a1 = self.data.get(str(field), default)
        a2 = self.data.get(int(field), default)
        # if a1 is not None:
        #     return str(a1).replace(',', '') if strip_commas else a1
        # else:
        #     return str(a2).replace(',', '') if strip_commas else a2
        final_value = a1 if a1 is not None else a2
        if final_value is None:
            return default

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


def throw_fuss(msg: str, boarder="*", notify=True):
    """A helper function to make a large-print fuss to the user good for critical errors. This function FORCES notifications."""
    environment_size = os.get_terminal_size().columns
    if environment_size < 80:
        environment_size = 80
    opening_line = boarder * environment_size
    closing_line = boarder * environment_size
    print(opening_line)
    # message should be centered and maybe multiple lines
    for line in msg.split('\n'):
        centered_line = line.center(environment_size)
        print(centered_line)
    print(closing_line)

    if notify:
        macos_notification_with_custom_sound(
            title="Argus IBKR Alert",
            message=msg,
        )


if __name__ == '__main__':
    throw_fuss("Hello World!\nThis is a test of the emergency broadcast system.\nHave a nice day!")
    throw_fuss("This is still a test of the emergency broadcast system.\nHave a nice day!", boarder="#")
    # try using emojis
    throw_fuss("This is an emergency broadcast system test.\nHave a nice day! 😊", boarder="🚨")
