import time
import socket
import traceback
from argus.capital._svr_utils import Protocol2Parser

s = socket.socket()
s.connect(('localhost', 9972))
s.sendall(b'add=QQQ')
time.sleep(0.1)
s.sendall(b'add=SPY')
parser = Protocol2Parser(['bid', 'bid_size', 'ask', 'ask_size', 'last', 'last_size', 'shortable_shares', 'timestamp', 'transmission_time'])
try:
    while True:
        data = s.recv(4096)
        if len(data) == 1:
            print('Pinged by server, continuing...')
            continue

        # '$' is the ping character if there is a collision which would be data[0] == 36
        # then we just need to remove it from the data
        if data[0] == 36:
            data = data[1:]

        if not data:
            break
        try:
            result = parser.parse(data)
            result_time = result.get('transmission_time', None)
            print(result)
            print('Since Timestamp:', time.time() - result_time if result_time else 'N/A')
        except Exception as e:
            print(f"Error parsing data: {e}")
            traceback.print_exc()
            pass
except KeyboardInterrupt:
    print("Interrupted by user")

s.close()

if __name__ == '__main__':
    pass
