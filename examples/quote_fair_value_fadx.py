import os
import time
import datetime
import subprocess
from tool import plotter
from utils3 import runAsThread
from dotenv import load_dotenv
from argus.tv.multisymbol import Ticker


load_dotenv()
plt = plotter.PlotWriter("/Users/Salman/Library/Containers/SVO-Productions.plotview/Data/tmp/plot.plt")

chadx15_weights = {
    "IHC": 33.72,
    "FAB": 14.88,
    "EAND": 12.89,
    "ADIB": 7.57,
    "ADCB": 6.41,
    "ALDAR": 6.20,
    "ADNOCGAS": 4.56,
    "ALPHADHABI": 3.19,
    "ADNOCDRILL": 2.51,
    "ADNOCDIST": 1.95,
    "PUREHEALTH": 1.39,
    "ADNOCLS": 1.28,
    "MULTIPLY": 1.24,
    "NMDC": 1.01,
}

def notify(message):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_message = f"[{timestamp}] {message}"
    subprocess.check_call([
        'imessage-cli',
        '-m',
        full_message,
        '+971506940015'
    ])

class FADX15FairValue:
    def __init__(self):
        self.tickers = {}
        self.index_etf = "ADX:CHADX15"
        self.tracker_etf = "ADX:FADX15"
        request_ids = [self.index_etf, self.tracker_etf]
        for key in chadx15_weights.keys():
            request_ids.append(f"ADX:{key}")

        try:
            token = os.environ['TOKEN']
        except KeyError:
            raise ValueError("Please set the TOKEN environment variable.")

        self.tick = Ticker(self.callback, request_ids, verbose=False, auth_token=token, save=True, database_name="ADX_data")
        self.tick.start()
        self.history = []
        self.logFeed = True
        self.spread_alert_sent = False

        print('Total Weights: {}%'.format(sum(chadx15_weights.values())))
        self.calculate_fair_value()

    def callback(self, symbol, data):
        if self.logFeed:
            print(datetime.datetime.now(), f'Received data for {symbol}, value: {data}')
        self.tickers[symbol] = data
        self.history.append(data)

    @runAsThread
    def calculate_fair_value(self):
        while True:
            time.sleep(1)
            contribution = {}

            if len(self.tickers.items()) < len(chadx15_weights)+1:
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

            keys = list(contribution.keys())
            values = list(contribution.values())
            fair_value = sum(values)

            try:
                etf_value = float(self.tickers[self.index_etf]['changePercentage']) / 100
            except KeyError:
                print("ETF value not found")
                return

            spread = abs(fair_value - etf_value)

            # Send notification logic
            if not self.spread_alert_sent and spread > 0.003:
                notify(f"Spread Alert: Spread is high at {spread * 100:.2f}%")
                self.spread_alert_sent = True
            elif self.spread_alert_sent and spread <= 0.003:
                notify(f"Spread Closed: Spread has come down to {spread * 100:.2f}%")
                self.spread_alert_sent = False


            for key in keys:
                contribution[key] = contribution[key] / fair_value

            keys = sorted(keys, key=lambda x: contribution[x], reverse=True)
            nice_pretty = ""
            for key in keys:
                nice_pretty += "{}: {:.2f}% | ".format(key, contribution[key] * 100)
            nice_pretty = nice_pretty[:-3]

            plt.write(spread * 100)

            print("FADX15 Fair Value: {:.2f}% | CHADX15 Value: {} | Spread: {:.2f}% | Spread Distribution: {}".format(
                fair_value * 100,
                self.tickers[self.index_etf]['changePercentage'],
                spread * 100,
                nice_pretty
            ))


if __name__ == '__main__':
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
            break
