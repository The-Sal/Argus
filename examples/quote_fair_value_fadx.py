import enum
import time
import datetime
import subprocess
from utils3 import runAsThread
from dotenv import load_dotenv
from utils3.plot import SimplePlotWriter
from argus.tv.multisymbol import Ticker
from argus import QuoteSession, MarketData

load_dotenv()
plt = SimplePlotWriter("/Users/Salman/Library/Containers/SVO-Productions.plotview/Data/tmp/plot.plt")

chadx15_weights = {
    "IHC": 30.14,
    "FAB": 15.78,
    "EAND": 11.72,
    "ADIB": 8.22,
    "ADCB": 8.40,
    "ALDAR": 6.89,
    "ADNOCGAS": 4.31,
    "ALPHADHABI": 3.43,
    "ADNOCDRILL": 2.76,
    "ADNOCDIST": 2.03,
    "PUREHEALTH": 1.21,
    "ADNOCLS": 1.41,
    "MULTIPLY": 1.84,
    "NMDC": 1.03,
}
NOTIFY_NUM = '+971506940015'
def notify(message):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    full_message = f"[{timestamp}] {message}"
    subprocess.check_call([
        'imessage-cli',
        '-m',
        full_message,
        NOTIFY_NUM
    ])
    an.notify(
        event_type=ProjectEvents.UNCLASSIFIED_EVENT,
        event_description=full_message,
    )


# noinspection PyUnresolvedReferences
from ic_audit import AuditNotifier, ProjectPrivileges, ProjectEvents
an = AuditNotifier(
    project_name="ADX Arbitrage Fair Value Monitor",
    project_market="ADX",
    project_description="Monitor the fair value of the CHADX15 index and notify on actionable opportunities.",
    project_privileges=[ProjectPrivileges.LIVE_MONITORING],
    notifying_events_to=NOTIFY_NUM
)


class NotificationSentType(enum.Enum):
    ACTIONABLE_LONG = "actionable_long"
    ACTIONABLE_SHORT = "actionable_short"
    NOT_ACTIONABLE = "not_actionable"
    CLOSED_SPREAD = "closed_spread"
    NO_MSG = "no_msg"


class FADX15FairValue:
    def __init__(self):
        self.tickers = {}
        self.index_etf = "ADX:CHADX15"
        self.tracker_etf = "ADX:FADX15"
        request_ids = [self.index_etf, self.tracker_etf]
        for key in chadx15_weights.keys():
            request_ids.append(f"ADX:{key}")

        try:
            import os
            token = os.environ['TOKEN']
        except KeyError:
            raise ValueError("Please set the TOKEN environment variable.")

        # auth_token=token,
        self.tick = Ticker(self.callback, request_ids, verbose=False,
                           save=True, database_name="ADX_data", auth_token=token)

        self.chadx15Session = QuoteSession("ADX:CHADX15", self._chadx15_callback)
        self.chadx15LatestBidAsk = None

        runAsThread(self.chadx15Session.ws.run_forever)()
        self.tick.start()
        self.history = []
        self.logFeed = True
        self.spread_alert_sent = False
        self._last_notification = NotificationSentType.NO_MSG

        print('Total Weights: {}%'.format(sum(chadx15_weights.values())))
        self.calculate_fair_value()

    def _chadx15_callback(self, data: MarketData):
        if self.logFeed:
            print(datetime.datetime.now(), f'CHADX15 data received: {data} for bid/ask')

        # merge the last bid/ask and the new one so since the updates are only delta updates
        if self.chadx15LatestBidAsk is None:
            self.chadx15LatestBidAsk = data
        else:
            for key, value in data.__dict__.items():
                if value is not None:
                    print('Updating CHADX15 Latest Bid/Ask:', key, value)
                    setattr(self.chadx15LatestBidAsk, key, value)

            print('Current object:', self.chadx15LatestBidAsk)

    def callback(self, symbol, data):
        if self.logFeed:
            print(datetime.datetime.now(), f'Received data for {symbol}, value: {data}')
        self.tickers[symbol] = data
        self.history.append(data)

    @runAsThread
    def calculate_fair_value(self):
        while True:
            time.sleep(0.5)
            contribution = {}

            if len(self.tickers.items()) < len(chadx15_weights) + 1:
                print('Missing: {}'.format(
                    [key for key in chadx15_weights.keys() if f"ADX:{key}" not in self.tickers.keys()]
                ))
                continue

            for key, value in self.tickers.items():
                if key == self.index_etf:
                    continue

                if key == self.tracker_etf:
                    continue

                name = key.split(":")[1]
                weight = chadx15_weights[name] / 100
                changePct = float(value['changePercentage']) / 100
                contribution[name] = weight * changePct

            # keys = list(contribution.keys())
            values = list(contribution.values())
            fair_value = sum(values)

            etf_bid_change_pct = None
            etf_ask_change_pct = None
            etf_bid_spread = None
            etf_ask_spread = None

            try:
                etf_value = float(self.tickers[self.index_etf]['changePercentage']) / 100
                # calculate the new changePercentage had we executed the bid or the ask
                etf_price = float(self.tickers[self.index_etf]['price'])
                # based on the current price and % return calculate the original opening price
                etf_open_price = etf_price / (1 + etf_value)
                # calculate the % change it would be from the opening price to the bid/ask price
                if self.chadx15LatestBidAsk is not None:
                    try:
                        etf_bid_price = float(self.chadx15LatestBidAsk.bid_price)
                        etf_ask_price = float(self.chadx15LatestBidAsk.ask_price)
                        etf_bid_change_pct = (etf_bid_price / etf_open_price) - 1
                        etf_ask_change_pct = (etf_ask_price / etf_open_price) - 1

                        # Calculate the % difference between the bid/ask
                        # (if executed as a %change from the opening price) and the current ETF return %
                        etf_bid_spread = etf_bid_change_pct - etf_value
                        etf_ask_spread = etf_ask_change_pct - etf_value
                    except TypeError:
                        print("CHADX15 Latest Bid/Ask data is incomplete or not available.")
                        continue

            except KeyError:
                print("ETF value not found")
                continue

            spread = abs(fair_value - etf_value)
            # Send notification logic
            if not self.spread_alert_sent and spread > 0.003:
                print('Sending spread alert...')
                self.spread_alert_sent = True
                msg = f"Spread Alert: Spread is high at {spread * 100:.2f}%. Fair Value: {fair_value * 100:.2f}%, ETF Value: {etf_value * 100:.2f}%"
                if etf_value > fair_value:
                    msg += " (ETF is overpriced)"
                else:
                    msg += " (ETF is underpriced)"

                if etf_ask_change_pct is None or etf_bid_change_pct is None:
                    msg += "\nNo ETF Bid/Ask Change Percentage available."
                    self.spread_alert_sent = False
                else:
                    if fair_value > etf_ask_change_pct:
                        msg += ("\n✅Actionable Long Opportunity: Fair Value is higher than ETF Ask Change Percentage. "
                                f"({fair_value * 100:.2f}% > {etf_ask_change_pct * 100:.2f}%)")
                        msg += "\nExpected Profit: {:.2f}%".format(
                            (fair_value - etf_ask_change_pct) * 100)
                    elif fair_value < etf_bid_change_pct:
                        msg += ("\n✅Actionable Short Opportunity: Fair Value is lower than ETF Bid Change Percentage. "
                                f"({fair_value * 100:.2f}% < {etf_bid_change_pct * 100:.2f}%)")
                        msg += "\nExpected Profit: {:.2f}%".format(
                            (etf_bid_change_pct - fair_value) * 100)

                notify(msg)


            elif self.spread_alert_sent and spread <= 0.003:
                notify(f"Spread Closed: Spread has come down to {spread * 100:.2f}%")
                self.spread_alert_sent = False

            plt.write(spread * 100)

            try:
                subprocess.check_call(['clear'])
            except subprocess.CalledProcessError:
                pass

            # noinspection all
            print("[{}] FADX15 Fair Value: {:.2f}% | CHADX15 Value: {}% | Spread: {:.2f}% | Bid Spread: {}% "
                  "| Ask Spread: {}% | ETF Bid Change: {}% | ETF Ask Change: {}% | Bid/Ask Prices: {}/{}".format(
                datetime.datetime.now().strftime("%H:%M:%S"),
                fair_value * 100,
                self.tickers[self.index_etf]['changePercentage'],
                spread * 100,
                f"{etf_bid_spread * 100:.2f}" if etf_bid_spread is not None else "N/A",
                f"{etf_ask_spread * 100:.2f}" if etf_ask_spread is not None else "N/A",
                f"{etf_bid_change_pct * 100:.2f}" if etf_bid_change_pct is not None else "N/A",
                f"{etf_ask_change_pct * 100:.2f}" if etf_ask_change_pct is not None else "N/A",
                etf_bid_price, etf_ask_price
            ))


if __name__ == '__main__':
    try:
        plt.reset()
        time.sleep(2)
        xx = FADX15FairValue()
        while True:
            i = input("")
            if i == "-":
                xx.logFeed = False
            elif i == "+":
                xx.logFeed = True
            elif i == "q":
                an.notify(ProjectEvents.UNCLASSIFIED_EVENT,
                          "Fair Value Monitor was stopped by user. Exiting...")
                break
    except Exception as e:
        an.notify(event_type=ProjectEvents.ERROR, event_description=f"Error in Fair Value Monitor: {e}")
