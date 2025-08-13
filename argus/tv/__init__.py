import os
import json
import time
import string
import random
import datetime
import websocket
import traceback
import pandas as pd


from dotenv import load_dotenv
load_dotenv()


class MarketData:
    def __init__(self, last_price, bid_price, bid_size, ask_price, ask_size, change_percentage, change_value):
        self.last_price = last_price
        self.change_percentage = change_percentage
        self.change_value = change_value

        self.bid_price = bid_price
        self.bid_size = bid_size
        self.ask_price = ask_price
        self.ask_size = ask_size

    def __repr__(self):
        return (f"MarketData(last_price={self.last_price}, "
                f"bid_price={self.bid_price}, bid_size={self.bid_size}, "
                f"ask_price={self.ask_price}, ask_size={self.ask_size}, "
                f"change_percentage={self.change_percentage}, change_value={self.change_value})")

    def __getitem__(self, item):
        """Allow attribute access via dictionary-like syntax"""
        return getattr(self, item, None)

def force_print_traceback(func):
    """force a traceback to be printed"""

    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            str(e)
            traceback.print_exc()
            raise

    return wrapper

class TradingViewConnection:
    def __init__(self, send_auth=True):
        self.ws = websocket.WebSocketApp(
            url='wss://data.tradingview.com/socket.io/websocket',
            on_message=self.on_message,
            on_open=self.on_open
        )
        self.send_auth = send_auth

    def on_message(self, ws, message):
        """Handle incoming messages from the WebSocket"""
        raise NotImplementedError("on_message method not implemented")

    def heartbeat_reply(self, heartbeat_msg):
        """Handle heartbeat messages"""
        # Heartbeat messages are usually just echoed back
        self.ws.send(heartbeat_msg)

    def on_open(self, ws):
        """Handle WebSocket connection open event"""
        print("WebSocket connection opened")

    @force_print_traceback
    def setup(self):
        required = [
            self.craft_message("set_locale", ["en", "US"]),
        ]
        if self.send_auth:
            required.insert(0, self.craft_message("set_auth_token", [os.getenv("TOKEN")]))
        else:
            required.insert(0, self.craft_message("set_auth_token", ["unauthorized_user_token"]))

        # Send the required messages to initialize the connection
        for msg in required:
            self.send_msg(msg)

    @staticmethod
    def craft_message(method, params):
        """Craft a message to send to the WebSocket"""
        return {
            "m": method,
            "p": params
        }

    def send_msg(self, msg: dict):
        """Send a message to the WebSocket"""
        wrapped = self.wrap_message(msg)
        # print('Sending message:', wrapped)
        self.ws.send(wrapped)

    @staticmethod
    def wrap_message(msg: dict) -> str:
        """Wrap a message in the required format"""
        raw_msg = json.dumps(msg)
        size = len(raw_msg)
        encoding_data = f"~m~{size}~m~"
        return f"{encoding_data}{raw_msg}"

    @staticmethod
    def decode_message(raw_msg: str, multiple=False) -> dict:
        """Decode a message from the WebSocket"""
        delim = "~m~"
        parts = raw_msg.split(delim)
        size = int(parts[1])
        message = parts[2]

        # check if there is extra messages
        next_message = None
        count_of_delim = raw_msg.count(delim)
        if count_of_delim > 2:
            next_message = TradingViewConnection.decode_message(raw_msg.split(message)[1], multiple=True)




        if not multiple:
            try:
                return json.loads(message)
            except json.JSONDecodeError:
                if message.__contains__('~h~'):
                    return {"m": "heartbeat"}
                else:
                    print("Error decoding message:", message)
            return None
        else:
            try:
                if next_message:
                    return [json.loads(message)] + next_message
                else:
                    return [json.loads(message)]
            except json.JSONDecodeError:
                if message.__contains__('~h~'):
                    return [{"m": "heartbeat"}]
                else:
                    print("Error decoding message:", message)
                return None


    def post_setup(self):
        """Post setup actions after the WebSocket is opened and initialized"""
        pass

class QuoteSession(TradingViewConnection):
    def __init__(self, symbol="ADX:FADX15", callback=None, sendAuth=True):
        q = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
        self.quote_session_id = "qs_{}".format(q)
        self.quote_snapshotter = "qs_snapshoter_basic-symbol-quotes_" + q
        self.symbol = symbol
        self.msgs_read = 0
        self.callback = callback
        super().__init__(send_auth=sendAuth)
        self.msg_history = []

    def setup_qs(self):
        """
        Set up the quote session by sending the necessary WebSocket messages.

        This method prepares the quote session by:
        - Creating a new quote session using the `quote_create_session` method.
        - Setting the fields for the quote session using the `quote_set_fields` method.

        The fields include various attributes such as currency details, exchange information,
        and other metadata required for the session.
        """
        msgs = [

            self.craft_message("quote_create_session", [self.quote_session_id]),
            self.craft_message("quote_set_fields", [
                self.quote_session_id,
                "base-currency-logoid",
                "ch",
                "chp",
                "currency-logoid",
                "currency_code",
                "currency_id",
                "base_currency_id",
                "current_session",
                "description",
                "exchange",
                "format",
                "fractional",
                "is_tradable",
                "language",
                "local_description",
                "listed_exchange",
                "logoid",
                "lp",
                "lp_time",
                "minmov",
                "minmove2",
                "original_name",
                "pricescale",
                "pro_name",
                "short_name",
                "type",
                "typespecs",
                "update_mode",
                "volume",
                "variable_tick_size",
                "value_unit_id",
                "unit_id",
                "measure"
            ]),
            self.craft_message("quote_add_symbols", [
                self.quote_session_id, self.symbol
            ]),
            self.craft_message("quote_fast_symbols", [
                self.quote_session_id, self.symbol
            ]),
            self.craft_message("quote_fast_symbols", [
                self.quote_session_id,
                '={"adjustment":"splits", "session":"regular","symbol":"&&"}'.replace("&&", self.symbol),
                self.symbol
            ]),
            self.craft_message("quote_create_session", [self.quote_snapshotter]),
            self.craft_message("quote_set_fields", [
                self.quote_session_id,
                "base-currency-logoid",
                "ch",
                "chp",
                "currency-logoid",
                "currency_code",
                "currency_id",
                "base_currency_id",
                "current_session",
                "description",
                "exchange",
                "format",
                "fractional",
                "is_tradable",
                "language",
                "local_description",
                "listed_exchange",
                "logoid",
                "lp",
                "lp_time",
                "minmov",
                "minmove2",
                "original_name",
                "pricescale",
                "pro_name",
                "short_name",
                "type",
                "typespecs",
                "update_mode",
                "volume",
                "variable_tick_size",
                "value_unit_id",
                "unit_id",
                "measure"
            ]),
            self.craft_message("quote_add_symbols", [
                self.quote_snapshotter, self.symbol
            ]),
            self.craft_message("quote_fast_symbols", [
                self.quote_snapshotter,
                '={"adjustment":"splits", "session":"regular","symbol":"&&"}'.replace("&&", self.symbol),
                self.symbol
            ]),
        ]

        for msg in msgs:
            self.send_msg(msg)

    @force_print_traceback
    def on_message(self, ws, message):
        """Handle incoming messages from the WebSocket"""
        self.msgs_read += 1
        if self.msgs_read == 1:
            self.setup()
            self.setup_qs()

        decoded_message = self.decode_message(message)
        method = decoded_message.get("m")
        params = decoded_message.get("p", [])
        if method == "heartbeat":
            self.heartbeat_reply(message)
        else:
            # print(f"Received message: {decoded_message}")
            if method == "qsd":
                # Handle the quote data message
                self.handle_quote_data(params)

    def handle_quote_data(self, params):
        """Handle the quote data message"""
        if len(params) < 2:
            return
        src = params[0]
        data = params[1]
        # if its snapshotter we will have bid-ask otherwise we will have last price
        # not all data sets come with bid-ask or together so we dispatch on-demand rather than waiting for all aggregation
        mkt_data = data['v']
        obj = MarketData(
            last_price=mkt_data.get('lp'),
            bid_price=mkt_data.get('bid'),
            bid_size=mkt_data.get('bid_size'),
            ask_price=mkt_data.get('ask'),
            ask_size=mkt_data.get('ask_size'),
            change_percentage=mkt_data.get('chp'),
            change_value=mkt_data.get('ch')
        )

        self.callback(obj) if self.callback else None

class ChartSession(TradingViewConnection):
    def __init__(self, callback=None):
        self.callback = callback
        super().__init__(send_auth=False)
        self.messages = 0
        self.chart_session_id = self._gen_chart_session()
        self.message_history = []
        self.start_capture = False
        self._capture_callback = None

    @staticmethod
    def _gen_chart_session():
        return  "cs_{}".format(''.join(random.choices(string.ascii_letters + string.digits, k=12)))

    # @assertTypes((str, str, int, bool), auto_convert=True, class_method=True)
    def get_symbol_data(self, symbol, interval="D", total_ticks=500):
        idx = self._gen_chart_session()
        self.chart_session_id = idx
        rq = [
            self.craft_message("chart_create_session", [
                idx, ""
            ]),

            self.craft_message("resolve_symbol", [self.chart_session_id, "sds_sym_1", "=" + json.dumps({
                "adjustment": "splits", "symbol": symbol}, indent=0)]),
        ]

        for msg in rq:
            self.send_msg(msg)

        time.sleep(1)
        self.send_msg(
            self.craft_message("create_series", [self.chart_session_id, "sds_1",
                                                 "s1", "sds_sym_1", interval, int(total_ticks), ""])
        )

        result = self.wait_for_timescale_change()
        data = result['p'][1]['sds_1']['s']
        print("Total ticks received:", len(data))
        return self.convert_to_df(data)






    @staticmethod
    def convert_to_df(data):
        """
        Converts the data into a pandas dataframe with the following columns:
        Date: The date of the data point which will be a native datetime object
        Open: The open price of the data point
        High: The high price of the data point
        Low: The low price of the data point
        Close: The close price of the data point
        Volume: The volume of the data point
        """
        points = []
        for point in data:
            # dates need to be converted from epoch to datetime
            point = point['v']
            points.append({
                "Date": datetime.datetime.fromtimestamp(point[0]),
                "Open": point[1],
                "High": point[2],
                "Low": point[3],
                "Close": point[4],
                "Volume": point[5]
            })

        df = pd.DataFrame(points)
        return df


    def wait_for_timescale_change(self, timeout=10):
        """Wait to receive a timescale message"""
        timescales = sum(map(lambda x: int( x.get("m") == "timescale_update"), self.message_history))
        print(f"Waiting for timescale change, received {timescales} messages")
        now = time.time()
        while True:
            current_timescales = sum(map(lambda x: int( x.get("m") == "timescale_update"), self.message_history))
            if current_timescales > timescales:
                print(f"Received {current_timescales} timescale messages")
                break
            time.sleep(0.1)
            if (time.time() - now) > timeout:
                raise TimeoutError("Timeout waiting for timescale change")

        reversed_msgs = self.message_history[::-1]
        for msg in reversed_msgs:
            if msg.get("m") == "timescale_update":
                return msg

        raise ValueError("No timescale update message found")

    @force_print_traceback
    def on_message(self, ws, message):
        """Handle incoming messages from the WebSocket"""
        self.messages += 1
        if self.messages == 1:
            self.setup()
            return


        decoded_messages = self.decode_message(message, multiple=True)

        for decoded_message in decoded_messages:
            method = decoded_message.get("m")
            params = decoded_message.get("p", [])
            if method == "timescale_update":
                print("Timescale update received")
                self.message_history.append(decoded_message)
            elif method == "symbol_resolved":
                print("Resolved Symbol:", params[2]['short_description'])

            elif method == "heartbeat":
                self.heartbeat_reply(message)
            elif method == "du":
                # market-data not going to be handled in ChartSession
                # use QuoteSession for that
                pass
            else:
                # Handle other messages
                print(f"Received message: {decoded_message}")
                # self.message_history.append(decoded_message)
                # with open("chart_data.json", "w+") as f:
                #     f.write(json.dumps(decoded_message, indent=4))


# TODO: Implement NewsSession
class NewsSession(TradingViewConnection):
    def __init__(self, callback=None):
        self.callback = callback
        super().__init__(send_auth=False)
        self.messages = 0