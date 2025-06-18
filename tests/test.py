import json
import socket
from argus.capital import encode_packet, decode_packet

def send_request(client: socket.socket, request: dict):
    """Sends a request to the server."""
    packet = encode_packet(json.dumps(request).encode('ascii'))
    client.sendall(packet)

def receive_response(client: socket.socket):
    """Receives a response from the server."""
    data = client.recv(4096)
    decoded_data = decode_packet(data)
    return json.loads(decoded_data.decode('ascii'))

def test_mkt_dispatcher(host: str, port: int, symbols: list):
    """Tests the MKTDispatcher by resolving and streaming symbols."""
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect((host, port))

    for symbol in symbols:
        # Resolve symbol
        resolve_request = {'action': 'resolve_symbol', 'symbol': symbol}
        send_request(client, resolve_request)
        response = receive_response(client)
        print(f"Resolve response for {symbol}: {response}")

        if response.get('status') == 'success':
            epic = response['data']['instrument']['epic']

            # Stream epic
            stream_request = {'action': 'stream_epic', 'epic': epic}
            send_request(client, stream_request)
            print(f"Started streaming for epic: {epic}")

            # Print a few entries
            for _ in range(5):
                data = receive_response(client)
                print(f"Market data: {data}")

            # Unsubscribe
            unsubscribe_request = {'action': 'unsubscribe', 'epic': epic}
            send_request(client, unsubscribe_request)
            response = receive_response(client)
            print(f"Unsubscribe response for {epic}: {response}")

    client.close()

if __name__ == '__main__':
    test_mkt_dispatcher(host='localhost', port=9964, symbols=['BTCUSD', 'ETHUSD'])