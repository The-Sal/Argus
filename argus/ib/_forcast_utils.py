"""
Forcasting Contracts internally are represented like so:
The smallest unit:
    {"market":null,"popularityRank":null,"name":null,"longDescription":null,
    "putOrCall":null,"expiration":null,"currency":null,"lastTradeMillis":null,
    "lastTradeDate":null,"lastTradeTime":null,"timezone":null,"commodityCode":null,
    "eventAuthorityURL":null,"eventFixedPayout":null,"sourceAgency":null,
    "marketRulesLink":null,"underlyingName":null,"categories":[],
    "expectedResolutionTime":null,"expectedPayoutTime":null,"timespecifierParam":null,
    "exchange":null,"priceIncrement":null,"tradingHours":{},"conid":null,
    "underlyingConid":null,"underlyingSymbol":null,"shortDescription":null,
    "strike":null,"strikeLabel":null}
    Call these 'mico' contracts.

But these are usually in an array for the 'Big' contract:
so if the 'big' contract is NYC Mayor then it will have the above
as an array of these micro-contracts with TWO per candidate. The reason why
is that for each person there is a YES and NO contract. This is represented
by the PutOrCall field. Where Put=No and Call=Yes.

Given that each outcome has two micro-contracts by extension the 'mini' contract
will have two micro-contracts per outcome.

The order will be FxContractBig [mini, mini, mini, ...]
where each mini is [micro, micro] for each outcome.

This allows for comparison at the mini level which is more useful.
For example if there are three candidates A, B, C then the big contract will have
six micro-contracts:
    A-Yes, A-No, B-Yes, B-No, C-Yes, C-No
but the mini contracts will be:
    [A-Yes, A-No], [B-Yes, B-No], [C-Yes, C-No]

So you can compare mini contracts against each other or other platforms.
All micro-contracts will have bid/ask/last price/volume data.
This also represents a problem for P2 (Protocol 2) because it doesn't allow us
to send complex data structures easily. So we will have to flatten the data
in transport and re-construct after P2 deserialization. So 'double' serialization
one from P2, then from P2 -> FxContractBig/Mini/Micro. Moreover, each contract
requires its own subscription for data. So you can imagine if there are 10 outcomes
then there are 20 micro-contracts and 10 mini-contracts. 1 micro = 1 subscription.
Consider this when requesting for contracts.

Notes:
    These are NOT IBKR-defined ways of representing these contracts Big/Mini/Micro.
    Within IBKR it's just an array of the smallest contracts (what we call micro).
    We created these classes because it's more intuitive and useful for clients
    to think of them this way and to build against it. Especially 'mini' contracts.
    IBKR also on each level of contract has a conid. So each micro has a conid which
    makes it super confusing what to request which is why here it will attempt
    to automatically enumerate the conids from the big -> micro contracts (if requested).


"""
from datetime import datetime
from utils3 import assertTypes
from argus.ib.fields import IBKRFields
from argus.ib._ib_utils import MarketData


def enforce_type(value, expected_type):
    """Enforce that a value is of the expected type, raising a TypeError if not."""
    if not isinstance(value, expected_type):
        return expected_type(value)
    return value


def et_decor(etype):
    """Decorator to enforce type on function return value."""

    def decorator(func):
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            if isinstance(result, tuple) or isinstance(result, list):
                new_tuple = tuple(enforce_type(item, etype) for item in result)
                return new_tuple

            return enforce_type(result, etype)

        return wrapper

    return decorator


def parse_timestamp(timestamp_str):
    """
    Parse a timestamp string in YYYYMMDDHHMMSS format to a datetime object.

    Args:
        timestamp_str (str): Timestamp in format YYYYMMDDHHMMSS

    Returns:
        datetime: Parsed datetime object

    Raises:
        ValueError: If the timestamp string is invalid or incorrectly formatted
    """
    try:
        # Parse using strptime with the specific format
        dt = datetime.strptime(timestamp_str, "%Y%m%d%H%M%S")
        return dt
    except ValueError as e:
        raise ValueError(f"Invalid timestamp format '{timestamp_str}'. Expected YYYYMMDDHHMMSS format.") from e


@assertTypes((dict, dict), auto_convert=False)
def _dict_convertor(obj, mapper):
    """Covert values of a dict based on a mapper dict
    Args:
        obj (dict): The original dict to convert
        mapper (dict): A dict mapping of old_key -> type
    """

    new_dict = {}
    for key, value in obj.items():
        if key in mapper and value is not None:
            try:
                new_dict[key] = mapper[key](value)
            except Exception as e:
                raise ValueError(f"Error converting key '{key}' with value '{value}': {e}") from e
        else:
            new_dict[key] = value
    return new_dict


class FxContractMicro:
    """This is the smallest unit of a Forcasting contract."""
    # {"market": null, "popularityRank": null, "name": null, "longDescription": null,
    #  "putOrCall": null, "expiration": null, "currency": null, "lastTradeMillis": null,
    #  "lastTradeDate": null, "lastTradeTime": null, "timezone": null, "commodityCode": null,
    #  "eventAuthorityURL": null, "eventFixedPayout": null, "sourceAgency": null,
    #  "marketRulesLink": null, "underlyingName": null, "categories": [],
    #  "expectedResolutionTime": null, "expectedPayoutTime": null, "timespecifierParam": null,
    #  "exchange": null, "priceIncrement": null, "tradingHours": {}, "conid": null,
    #  "underlyingConid": null, "underlyingSymbol": null, "shortDescription": null,
    #  "strike": null, "strikeLabel": null}

    # BID = Available Orders
    # ASK = Buy NOW Orders (instant fill)

    TYPE_CONVERSIONS = {
        "popularityRank": int,
        "expiration": int,
        "lastTradeMillis": int,
        "eventFixedPayout": float,
        "priceIncrement": float,
        "conid": int,
        "underlyingConid": int,
        "strike": float,
        "expectedResolutionTime": parse_timestamp,
        "expectedPayoutTime": parse_timestamp,
    }

    def __init__(self, data: dict):
        data = _dict_convertor(data, self.TYPE_CONVERSIONS)
        self.market = data.get("market")
        self.popularityRank = data.get("popularityRank")
        self.name = data.get("name")
        self.longDescription = data.get("longDescription")
        self.putOrCall = data.get("putOrCall")  # Put=No, Call=Yes
        self.expiration = data.get("expiration")
        self.currency = data.get("currency")
        self.lastTradeMillis = data.get("lastTradeMillis")
        self.lastTradeDate = data.get("lastTradeDate")
        self.lastTradeTime = data.get("lastTradeTime")
        self.timezone = data.get("timezone")
        self.commodityCode = data.get("commodityCode")
        self.eventAuthorityURL = data.get("eventAuthorityURL")
        self.eventFixedPayout = data.get("eventFixedPayout")
        self.sourceAgency = data.get("sourceAgency")
        self.marketRulesLink = data.get("marketRulesLink")
        self.underlyingName = data.get("underlyingName")
        self.categories = data.get("categories", [])
        self.expectedResolutionTime = data.get("expectedResolutionTime")
        self.expectedPayoutTime = data.get("expectedPayoutTime")
        self.timespecifierParam = data.get("timespecifierParam")
        self.exchange = data.get("exchange")
        self.priceIncrement = data.get("priceIncrement")
        self.tradingHours = data.get("tradingHours", {})
        self.conid = data.get("conid")  # Each micro has its own conid
        self.underlyingConid = data.get("underlyingConid")
        self.underlyingSymbol = data.get("underlyingSymbol")
        self.shortDescription = data.get("shortDescription")
        self.strike = data.get("strike")
        self.strikeLabel = data.get("strikeLabel")
        self._required_fields = [IBKRFields.BID_PRICE, IBKRFields.BID_SIZE,
                          IBKRFields.ASK_PRICE, IBKRFields.ASK_SIZE]

        self.market_data: MarketData = None  # To be populated by Market Data subscription

    def __repr__(self):
        return (
            f"FxContractMicro(conid={self.conid}, name={self.name}, putOrCall={self.putOrCall}, strikeLabel={self.strikeLabel}, "
            f"expectedResolutionTime={self.expectedResolutionTime}, expectedPayoutTime={self.expectedPayoutTime}")

    def update_market_data(self, mkt_data: MarketData):
        """Update the market data for this micro contract."""
        self.market_data = mkt_data

    def delta_update_market_data(self, mkt_data: MarketData):
        """Delta update the market data for this micro contract."""
        if self.market_data is None:
            self.market_data = mkt_data
        else:
            for field in self._required_fields:
                new_value = mkt_data.get(field, default=None)
                if new_value is not None:
                    self.market_data.data[field] = new_value

    def data_available(self):
        """Check if market data is available."""
        return self.market_data is not None

    @et_decor(float)
    def buy_data(self):
        """Get the current price, size for buying the contract at the latest price"""
        data = (
            self.market_data.get(IBKRFields.BID_PRICE, default=0.0),
            self.market_data.get(IBKRFields.BID_SIZE, default=0.0)
        )
        return data

    @et_decor(float)
    def buy_now_data(self):
        """Get the current price, size for buying the contract at the instant fill price"""
        data = (
            self.market_data.get(IBKRFields.ASK_PRICE, default=0.0),
            self.market_data.get(IBKRFields.ASK_SIZE, default=0.0)
        )
        return data

    def market_data_state(self) -> list[int]:
        """
        Checks if the market data is available and what's missing if anything.
        Returns:
            Array of missing fields or empty array if all data is available. Uses
            IBKRFields
        """

        missing = []
        if self.data_available():
            for field in self._required_fields:
                get = self.market_data.get(field, default=None)
                if get is None:
                    missing.append(field)
        else:
            missing = self._required_fields.copy()
        return missing




class FxContractMini:
    """A mini contract is a pair of micro contracts for a single outcome.
    For example if the outcome is "Curtis Sliwa wins" then there will be
    two micro contracts:
        - Curtis Sliwa wins - YES (putOrCall='C')
        - Curtis Sliwa wins - NO  (putOrCall='P')

    Assumes micro contracts have populated market data.
    """

    def __init__(self, micro_yes: FxContractMicro, micro_no: FxContractMicro):
        if micro_yes.putOrCall != 'C':
            raise ValueError("micro_yes must have putOrCall='C'")
        if micro_no.putOrCall != 'P':
            raise ValueError("micro_no must have putOrCall='P'")
        if micro_yes.strikeLabel != micro_no.strikeLabel:
            raise ValueError("Both micro contracts must have the same strikeLabel")

        self.micro_yes = micro_yes
        self.micro_no = micro_no
        self.strikeLabel = micro_yes.strikeLabel  # The outcome label

    def __repr__(self):
        return f"FxContractMini(strikeLabel={self.strikeLabel}, yes_conid={self.micro_yes.conid}, no_conid={self.micro_no.conid})"

    def data_available(self):
        """Check if market data is available for both micro contracts."""
        return self.micro_yes.data_available() and self.micro_no.data_available()

    @et_decor(float)
    def yes_data(self):
        """Get the current price, size for buying the YES contract at the latest price"""
        return self.micro_yes.buy_data()

    @et_decor(float)
    def yes_now_data(self):
        """Get the current price, size for buying the YES contract at the instant fill price"""
        return self.micro_yes.buy_now_data()

    @et_decor(float)
    def no_data(self):
        """Get the current price, size for buying the NO contract at the latest price"""
        return self.micro_no.buy_data()

    @et_decor(float)
    def no_now_data(self):
        """Get the current price, size for buying the NO contract at the instant fill price"""
        return self.micro_no.buy_now_data()

    @et_decor(int)
    def conids(self):
        """Get all conids for this mini contract. In the form (yes_conid, no_conid)"""
        return self.micro_yes.conid, self.micro_no.conid

    def market_data_state(self) -> dict[str, list[int]]:
        """
        Checks if the market data is available for both micro contracts and what's missing if anything.
        Returns:
            Dict with keys 'yes' and 'no' mapping to arrays of missing fields or empty array if all data is available. Uses
            IBKRFields
        """
        state = {
            'yes': self.micro_yes.market_data_state(),
            'no': self.micro_no.market_data_state()
        }
        return state


class FxContractBig:
    """A big contract is a collection of mini contracts for all outcomes of an event.
    For example if the event is "New York City Mayor Election" and there are three
    candidates A, B, C then there will be three mini contracts:
        - [A-YES, A-NO]
        - [B-YES, B-NO]
        - [C-YES, C-NO]
    Each mini contract contains two micro contracts.


    This is often the entrypoint from the API, use .market_data_state() which gives the exact missing

    """

    def __init__(self, mini_contracts: list[FxContractMini], conid_mapping: dict[int, FxContractMicro] = None):
        if not all(isinstance(mc, FxContractMini) for mc in mini_contracts):
            raise ValueError("All items in mini_contracts must be instances of FxContractMini")
        self.mini_contracts = mini_contracts
        self.conid = self.mini_contracts[0].micro_yes.underlyingConid
        self.underlyingName = self.mini_contracts[0].micro_yes.underlyingName

        # A mapping of conid to micro contract for quick lookup
        if conid_mapping is None:
            self.mapping = {}
            for mc in mini_contracts:
                yes_conid, no_conid = mc.conids()
                self.mapping[yes_conid] = mc.micro_yes
                self.mapping[no_conid] = mc.micro_no
        else:
            self.mapping = conid_mapping

        self._strike_mapping = {mc.strikeLabel: mc for mc in mini_contracts}


    @classmethod
    def from_json(cls, data: list[dict]):
        """Create a FxContractBig from a list of micro contract dicts."""

        micro_contracts = [FxContractMicro(micro) for micro in data]
        mapping = {}
        # Group micro contracts into mini contracts
        mini_contracts = []
        used_indices = set()
        for i, micro in enumerate(micro_contracts):

            # Note this works because FxContractMicro is a OBJECT that's pointed to by reference
            # So when we update mapping[micro.conid] = micro it's the same object in
            # mini_contracts
            mapping[micro.conid] = micro
            if i in used_indices:
                continue
            if micro.putOrCall == 'C':
                # Find the corresponding NO contract
                for j in range(i + 1, len(micro_contracts)):
                    if (j not in used_indices and
                            micro_contracts[j].putOrCall == 'P' and
                            micro_contracts[j].strikeLabel == micro.strikeLabel):
                        mini_contracts.append(FxContractMini(micro, micro_contracts[j]))
                        used_indices.update({i, j})
                        break
            elif micro.putOrCall == 'P':
                # Find the corresponding YES contract
                for j in range(i + 1, len(micro_contracts)):
                    if (j not in used_indices and
                            micro_contracts[j].putOrCall == 'C' and
                            micro_contracts[j].strikeLabel == micro.strikeLabel):
                        mini_contracts.append(FxContractMini(micro_contracts[j], micro))
                        used_indices.update({i, j})
                        break
        return cls(mini_contracts, mapping)

    def conids_by_outcome(self):
        """Get a mapping of outcome strikeLabel to its (yes_conid, no_conid)"""
        return {mc.strikeLabel: mc.conids() for mc in self.mini_contracts}

    def market_data_state(self) -> dict[str, dict[str, dict]]:
        """
        Gets the market data state for all mini contracts in a structured way.
        The structure is a dict mapping strikeLabel to another dict with keys 'yes' and 'no'.
        Each of these maps to a dict with keys 'conid' and 'missing_fields'.
        Missing fields is an array of IBKRFields that are missing for that contract.

        Example:
        {
            "Candidate A": {
                "yes": {
                    conid: 12345,
                    missing_fields: [1, 2]  # IBKRFields
                }
                "no": {
                    conid: 12346,
                    missing_fields: []  # All data available
                }
            },
        }

        :return:
        """
        structure = {}
        for mc in self.mini_contracts:
            yes_conid, no_conid = mc.conids()
            yes_no_data = mc.market_data_state()
            structure[mc.strikeLabel] = {
                'yes': {
                    'conid': yes_conid,
                    'missing_fields': yes_no_data['yes']
                },
                'no': {
                    'conid': no_conid,
                    'missing_fields': yes_no_data['no']
                }
            }
        return structure

    def lookup_mini_by_strike_label(self, strike_label: str) -> FxContractMini:
        """Get the mini contract for a specific outcome by its strike label."""
        if strike_label in self._strike_mapping:
            return self._strike_mapping[strike_label]
        raise ValueError(f"No mini contract found for strike label '{strike_label}'")

    def lookup_micro_by_conid(self, conid: int) -> FxContractMicro:
        """Lookup a micro contract by its conid."""
        if conid in self.mapping:
            return self.mapping[conid]
        raise ValueError(f"No micro contract found for conid '{conid}'")

    @property
    def strikes_labels(self) -> list[str]:
        """Get a list of all strike labels (outcomes) in this big contract."""
        return [mc.strikeLabel for mc in self.mini_contracts]

    @property
    def all_conids(self) -> list[int]:
        """Get a flat list of all conids in this big contract."""
        conids = []
        for mc in self.mini_contracts:
            yes_conid, no_conid = mc.conids()
            conids.extend([yes_conid, no_conid])
        return conids

    @property
    def total_outcomes(self) -> int:
        """Get the total number of outcomes (mini contracts) in this big contract."""
        return len(self.mini_contracts)

    def apply_mkt_data_update(self, conid: int, mkt_data: MarketData):
        """Apply a market data update to the appropriate micro contract by conid."""
        if conid in self.mapping:
            self.mapping[conid].delta_update_market_data(mkt_data)
        else:
            raise ValueError(f"No micro contract found for conid '{conid}'")



if __name__ == '__main__':
    import json
    with open('/Users/Salman/Projects/Imperium/Argus/nyc_mayor_data.json') as f:
        data = json.load(f)['contracts']
    big = FxContractBig.from_json(data)
    print(big.conids_by_outcome())
    print(json.dumps(big.market_data_state()))
    print(big.lookup_micro_by_conid(796056496))

    mock_data = MarketData(
        contract_id=796056496,
        server_id=None,
        contract_exchange=None,
        topic=None,
        data={
            IBKRFields.BID_PRICE: 0.25,
            IBKRFields.BID_SIZE: 10,
            IBKRFields.ASK_PRICE: 0.3,
            IBKRFields.ASK_SIZE: 15
        }
    )

    print("Applying market data update...")
    big.apply_mkt_data_update(796056496, mock_data)
    strike_name = big.lookup_micro_by_conid(796056496).strikeLabel
    mini = big.lookup_mini_by_strike_label(strike_name)
    print(mini)
    print(mini.market_data_state())

    print(big.market_data_state())

