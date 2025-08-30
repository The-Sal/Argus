import os
import threading
from argus._argus_utils import Notification
from utils3.networking import Session as _RAW_SESSION
from argus.capital import DomainCache, CapitalComMKTDataLive

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

    def __init__(self, shortable_shares, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.shortable_shares = shortable_shares

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