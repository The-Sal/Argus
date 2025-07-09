import time
import socket
from argus.capital._svr_utils import Protocol2Parser

s = socket.socket()
s.connect(('localhost', 9972))
s.sendall(b'add=QQQ')
parser = Protocol2Parser(['bid', 'bid_size', 'ask', 'ask_size', 'last', 'last_size', 'shortable_shares', 'timestamp', 'transmission_time'])
try:
    while True:
        data = s.recv(4096)
        if not data:
            break
        try:
            result = parser.parse(data)
            result_time = result.get('transmission_time', None)
            print(result)
            print('Since Timestamp:', time.time() - result_time if result_time else 'N/A')
        except Exception as e:
            print(f"Error parsing data: {e}")
            pass
except KeyboardInterrupt:
    print("Interrupted by user")

s.close()


