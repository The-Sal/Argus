import os
import time
import pandas as pd
from tqdm import tqdm
from utils3 import runAsThread
from dotenv import load_dotenv
from argus.tv.multisymbol import Ticker


class NoClue:
    def __init__(self):
        self.ticker = Ticker(self.callback, ["ADX:FADX15", "ADX:CHADX15"], verbose=False, auth_token=os.environ['TOKEN'])
        self.ticker.start()

        self.last_chadx15 = None
        self.last_fadx15 = None
        self.history = []
        # seconds till 3pm
        time_to_3pm = (15 * 60 * 60) - (time.localtime().tm_hour * 60 * 60 + time.localtime().tm_min * 60 + time.localtime().tm_sec)
        self.tqdm = tqdm(total=time_to_3pm, desc="Running")
        self.update_tqdm()

    @runAsThread
    def update_tqdm(self):
        while True:
            time.sleep(1)
            self.tqdm.update(1)
            self.tqdm.set_postfix({
                "chadx15": str(self.last_chadx15['changePercentage']) + "%",
                "fadx15": str(self.last_fadx15['changePercentage']) + "%",
                "callbacks": len(self.history),
            })


    def callback(self, symbol, data):
        if symbol == "ADX:CHADX15":
            self.last_chadx15 = data
        elif symbol == "ADX:FADX15":
            self.last_fadx15 = data

        history = {
            "timestamp": time.time(),
            "chadx15": self.last_chadx15,
            "fadx15": self.last_fadx15
        }
        self.history.append(history)


    def save(self):
        df = pd.DataFrame(self.history)
        df.to_csv("history.csv", index=False)


if __name__ == '__main__':
    no_clue = NoClue()

    def interactive():
        while True:
            i = input("Press Enter to save history or 'q' to quit: ")
            if i == 'q':
                break
            elif i == '':
                no_clue.save()
                print("History saved.")

    interactive()