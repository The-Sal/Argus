import gc
import os
import time
import random
import orjson
import numpy as np
from tqdm import tqdm
import seaborn as sns
from matplotlib import pyplot as plt


import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))


from argus.polymarket_direct.rest import PolyMarketOrderBookWss


# Wrap key sections in functions so they show up clearly in flame graph
def load_messages(filepath):
    """Load and prepare messages - will show as separate section in flame graph"""
    array_of_msg = []
    with open(filepath, 'r') as f:
        for line in f:
            if line.strip() == 'PONG' or len(line.strip()) <= 5:
                continue
            array_of_msg.append(orjson.loads(line))

    # Multiply and shuffle
    array_of_msg = array_of_msg * 1000
    random.shuffle(array_of_msg)

    # Pre-serialize all messages
    return [orjson.dumps(x) for x in array_of_msg]


def process_messages(wss, dumped_msgs):
    """Main processing loop - will show in flame graph"""
    times = []
    times_and_msg_len = []

    gc.disable()

    for msg in tqdm(dumped_msgs, desc="Processing Polymarket WSS messages"):
        start_time = time.perf_counter()

        # The actual work
        wss._on_message(None, msg)

        elapsed = time.perf_counter() - start_time
        times.append(elapsed)
        times_and_msg_len.append((np.log(len(msg)), elapsed))

    gc.enable()
    gc.collect()

    return times, times_and_msg_len, dumped_msgs


def calculate_statistics(times, times_and_msg_len, dumped_msgs):
    """Calculate and print stats - separate section in flame graph"""
    print(f"\nProcessed {len(dumped_msgs)} messages.")
    print(f"Min time: {np.min(times):.8f} seconds.")
    print(f"Max time: {np.max(times):.8f} seconds.")
    print(f"Average time: {np.mean(times):.8f} seconds.")
    print(f"Median time: {np.median(times):.8f} seconds.")
    print(f"Std time: {np.std(times):.8f} seconds.")
    print(f"Total time: {np.sum(times):.8f} seconds.")

    # Correlation analysis
    msg_lens = [x[0] for x in times_and_msg_len]
    times_taken = [x[1] for x in times_and_msg_len]
    correlation = np.corrcoef(msg_lens, times_taken)[0, 1]
    print(f"Correlation between message length and time taken: {correlation:.4f}")

    return msg_lens, times_taken


def plot_results(msg_lens, times_taken):
    """Plotting - separate section in flame graph"""
    sns.scatterplot(x=msg_lens, y=times_taken)
    plt.xlabel("Message Length (log bytes)")
    plt.ylabel("Time Taken (seconds)")
    plt.title("Polymarket WSS Message Length vs Time Taken")
    plt.show()


def find_slowest_message(times_taken, dumped_msgs):
    """Find and print slowest message"""
    max_time_index = np.argmax(times_taken)
    print("\nMessage that took the longest time:")
    print(orjson.loads(dumped_msgs[max_time_index]))
    print(f"Time taken: {times_taken[max_time_index]:.8f} seconds")


def main():
    """Main entry point"""
    os.environ['POLYMARKET_ORJSON'] = 'true'
    os.chdir(__file__.replace('__polymarket_wss_orderbook.py', ''))
    print("Initializing WebSocket handler...")
    wss = PolyMarketOrderBookWss(lambda x: None)

    print("Loading messages from file...")
    dumped_msgs = load_messages('_polymarket_socket_debug.log')
    print(f"Loaded {len(dumped_msgs)} messages")

    print("\nStarting message processing (this is the section being profiled)...")
    times, times_and_msg_len, dumped_msgs = process_messages(wss, dumped_msgs)

    print("\nCalculating statistics...")
    msg_lens, times_taken = calculate_statistics(times, times_and_msg_len, dumped_msgs)

    # Uncomment if you want to plot (plotting adds overhead to profile)
    # print("\nGenerating plot...")
    # plot_results(msg_lens, times_taken)

    find_slowest_message(times_taken, dumped_msgs)


if __name__ == '__main__':
    main()