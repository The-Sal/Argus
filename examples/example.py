import numpy as np
from tool import plotter
from building.ui import callbk
from utils3 import runAsThread
from building.quote_session import *
from matplotlib import pyplot as plt


def chart_examples():
    chs = ChartSession()

    @runAsThread
    def fuckurmother():
        time.sleep(1)
        t = time.time()
        dat = chs.get_symbol_data(input("Enter Symbol: "), "1", "500")
        t2 = time.time()
        plt.plot(dat['Date'], dat['Close'])
        plt.show()
        print("Total Time taken:", t2 - t)
        fuckurmother()

    symbols = ["BINANCE:BTCUSD", "NASDAQ:SQQQ", "AMEX:YMAX", "ADX:CHADX15", "NASDAQ:TQQQ", "CBOE:VIXY", "CBOE:SVXY"]

    @runAsThread
    def multi_shot():
        """
        Fetch data for multiple symbols, calculate log-returns since inception,
        and plot them on a single chart.
        """
        from tqdm import tqdm
        time.sleep(5)
        # chs = ChartSession()
        data_frames = {}

        for symbol in tqdm(symbols, desc="Fetching data", unit="symbol"):
            print(f"Fetching data for {symbol}...")
            data = chs.get_symbol_data(symbol, "D", 500)  # Fetch data for the symbol
            data['LogReturn'] = np.log(data['Close'] / data['Close'].shift(1))  # Calculate log-returns
            data_frames[symbol] = data

        # Plot log-returns for all symbols
        plt.figure(figsize=(12, 8))
        for symbol, df in data_frames.items():
            # make sure the date is today only
            plt.plot(df['Date'], df['LogReturn'].cumsum(), label=symbol)  # Cumulative log-returns

        plt.title("Cumulative Log-Returns Since Inception")
        plt.xlabel("Date")
        plt.ylabel("Cumulative Log-Return")
        plt.legend()
        plt.grid()
        plt.show()
        ask = input("Do you want to add more symbols? (y/n): ")
        if ask == "y":
            symbol = input("Enter Symbol: ")
            symbols.append(symbol)
            multi_shot()
        else:
            print("Exiting...")

    def plot_today_log_returns(symbol1, symbol2, chs):
        today = datetime.datetime.now().date()

        # Fetch minute data
        data1 = chs.get_symbol_data(symbol1, "1", "400")
        data2 = chs.get_symbol_data(symbol2, "1", "400")

        # Convert to pandas
        data1['DateTime'] = pd.to_datetime(data1['Date'])
        data2['DateTime'] = pd.to_datetime(data2['Date'])

        # Split into today's data and previous data
        data1_today = data1[data1['DateTime'].dt.date == today]
        data2_today = data2[data2['DateTime'].dt.date == today]

        data1_previous = data1[data1['DateTime'].dt.date < today]
        data2_previous = data2[data2['DateTime'].dt.date < today]

        # Get last close before today
        last_close1 = data1_previous['Close'].iloc[-1] if not data1_previous.empty else data1_today['Close'].iloc[0]
        last_close2 = data2_previous['Close'].iloc[-1] if not data2_previous.empty else data2_today['Close'].iloc[0]

        # Calculate log-returns using yesterday's close as base
        data1_today['LogReturn'] = np.log(data1_today['Close'] / last_close1)
        data2_today['LogReturn'] = np.log(data2_today['Close'] / last_close2)

        # Simple plot
        plt.figure(figsize=(12, 6))
        plt.plot(data1_today['DateTime'], data1_today['LogReturn'], label=symbol1, linewidth=2)
        plt.plot(data2_today['DateTime'], data2_today['LogReturn'], label=symbol2, linewidth=2)

        plt.title(f"Today's Log-Returns vs Yesterday's Close: {symbol1} vs {symbol2}")
        plt.xlabel("Time")
        plt.ylabel("Log-Return")
        plt.legend()

        # Add text showing each one's return
        plt.text(data1_today['DateTime'].iloc[-1], data1_today['LogReturn'].iloc[-1],
                 f"{symbol1}: {data1_today['LogReturn'].iloc[-1]:.2%}", fontsize=10, color='blue')
        plt.text(data2_today['DateTime'].iloc[-1], data2_today['LogReturn'].iloc[-1],
                 f"{symbol2}: {data2_today['LogReturn'].iloc[-1]:.2%}", fontsize=10, color='orange')

        # plt.grid(True, alpha=0.3)
        # plt.axhline(y=0, color='black', linestyle='-', alpha=0.3)
        #
        # plt.tight_layout()
        plt.show()

    # Optional: Decorator version for threading
    @runAsThread
    def today_log_returns_thread(chs):
        """Thread version of the function that prompts for input"""
        time.sleep(1)
        # symbol1 = input("Enter first symbol: ")
        # symbol2 = input("Enter second symbol: ")
        plot_today_log_returns("ADX:FADX15", "ADX:CHADX15", chs)

        # Ask if user wants to plot again
        again = input("Plot another pair? (y/n): ")
        if again.lower() == 'y':
            today_log_returns_thread(chs)

    chs.ws.run_forever()



def capture_CHADX15_FADX15():
    """Capture and Store both the CHADX15 & FADX15 data tick-by-tick"""
    qs = QuoteSession()


