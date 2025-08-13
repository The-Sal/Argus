import os
import time

from utils3 import runAsThread
from datetime import datetime
from dotenv import load_dotenv
from argus.tv.multisymbol import Ticker
from argus.tv import QuoteSession, MarketData


load_dotenv()
class FADX15FairValue:
    def __init__(self):
        self.fadx15_cache = None
        self.chadx15_cache = None

        self.fadxDataProvider = Ticker(self.fadx_15_callback, "ADX:FADX15")
        self.chadxDataProvider = QuoteSession("ADX:CHADX15", self.chadx_15_callback)
        self.fadxDataProvider.start()


    @runAsThread
    def run(self):
        self.chadxDataProvider.ws.run_forever()

    def fadx_15_callback(self, symbol, data):
        _ = symbol
        print(datetime.now(), f"FADX15:", data)

    def chadx_15_callback(self, data: MarketData):
        print(datetime.now(), f"CHADX15:", data)
        self.chadx15_cache = data


    def calc_fv(self):
        if self.fadx15_cache is None or self.chadx15_cache is None:
            return None

        ret_spread = abs(self.fadx15_cache.change_percentage - self.chadx15_cache.change_percentage)
        print(datetime.now(), f"FADX15: {self.fadx15_cache.last_price}, CHADX15: {self.chadx15_cache.last_price}, Spread: {ret_spread}")


        return None


if __name__ == '__main__':
    fair_value = FADX15FairValue()
    fair_value.run()

    while True:
        fair_value.calc_fv()
        time.sleep(0.1)