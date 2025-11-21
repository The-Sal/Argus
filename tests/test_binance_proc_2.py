import sys
import time
import socket
import traceback
from argus.capital._svr_utils import Protocol2Parser


HOST = 'localhost'
if len(sys.argv) > 1:
    print('Using host from command line argument:', sys.argv[1])
    HOST = sys.argv[1]

def main():
    s = socket.socket()
    # Binance MKTDispatcher default port per argus.binance.MKTDispatcher
    try:
        s.connect((HOST, 9982))
    except socket.error:
        print('Trying alternative port 9984...')
        s = socket.socket()
        s.connect((HOST, 9984))
    # Subscribe to BTCUSDT ticker stream
    s.sendall(b'add=BTCUSDT')
    time.sleep(0.1)

    # Protocol 2 decoding order produced by CapitalComMKTDataLive.transferable_2
    parser = Protocol2Parser(['bid', 'bid_size', 'ask', 'ask_size', 'last', 'last_size', 'timestamp', 'transmission_time'])

    print("Waiting for data from Binance dispatcher (BTCUSDT)...")
    try:
        while True:
            data = s.recv(4096)
            if len(data) == 1:
                # Ping byte
                print('Pinged by server, continuing...')
                continue

            # Handle potential ping collision '$' (36)
            if data and data[0] == 36:
                data = data[1:]

            if not data:
                break

            try:
                result = parser.parse(data)
                result_time = result.get('transmission_time', None)
                server_time = result.get('timestamp', None)
                print(result)
                print('Since Timestamp:', time.time() - result_time if result_time else 'N/A')
                print('Since Server Time:', time.time() - server_time if server_time else 'N/A')
            except Exception as e:
                print(f"Error parsing data: {e}")
                traceback.print_exc()
                pass
    except KeyboardInterrupt:
        print("Interrupted by user")
    finally:
        try:
            # Attempt to unsubscribe cleanly
            s.sendall(b'remove=BTCUSDT')
        except Exception:
            pass
        s.close()


if __name__ == '__main__':
    main()

