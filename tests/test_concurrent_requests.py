"""
Test to verify that multiple requests can be in flight from the same client.

This test connects to a running dispatcher on localhost:9972 and sends multiple
concurrent requests to verify they can be processed simultaneously.

The dispatcher must already be running before execution.
"""
import os
import sys
import json
import time
import socket
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from argus.protocol import encode_packet

# Configuration constants
HOST = 'localhost'
PORT = 9972
CONNECTION_TIMEOUT = 30  # seconds
RECV_BUFFER_SIZE = 131072  # 128KB
NUM_CONCURRENT_PINGS = 10
NUM_MIXED_REQUESTS_PER_TYPE = 3


def connect() -> socket.socket:
    """Open a TCP connection to the dispatcher."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(CONNECTION_TIMEOUT)
    sock.connect((HOST, PORT))
    return sock


def send_request(sock: socket.socket, action: str, data=None) -> float:
    """
    Send a single request and measure the time it takes.
    Returns the time taken in seconds.
    """
    if data is None:
        data = {}
    request = {'action': action, 'data': data}
    packet = encode_packet(json.dumps(request).encode('utf-8'))
    
    t0 = time.perf_counter()
    sock.sendall(packet)
    
    # Read the response
    raw = b''
    header_len = None
    needed = None
    
    while True:
        chunk = sock.recv(RECV_BUFFER_SIZE)
        if not chunk:
            raise ConnectionError("Server closed connection before responding.")
        raw += chunk
        
        if needed is None and b'|' in raw:
            pipe_idx = raw.index(b'|')
            payload_len = int(raw[1:pipe_idx].decode('ascii'))
            header_len = pipe_idx + 1
            needed = header_len + payload_len
        
        if needed is not None and len(raw) >= needed:
            break
    
    elapsed = time.perf_counter() - t0
    payload = raw[header_len:needed]
    response = json.loads(payload.decode('utf-8'))
    
    return elapsed, response


def test_concurrent_pings():
    """
    Test that multiple ping requests can be sent concurrently from the same socket
    and all get responses.
    """
    print("--- TEST: Concurrent Pings from Single Socket ---")
    sock = connect()
    
    results = []
    lock = threading.Lock()
    
    def send_ping(idx):
        elapsed, response = send_request(sock, 'ping')
        with lock:
            results.append((idx, elapsed, response))
        print(f"  Request {idx}: RTT {elapsed*1000:.1f} ms, response: {response.get('data')}")
        return idx, elapsed, response
    
    # Send all requests concurrently
    start_time = time.perf_counter()
    with ThreadPoolExecutor(max_workers=NUM_CONCURRENT_PINGS) as executor:
        futures = [executor.submit(send_ping, i) for i in range(NUM_CONCURRENT_PINGS)]
        
        # Wait for all to complete
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"  ERROR: {e}")
    
    total_time = time.perf_counter() - start_time
    
    sock.close()
    
    # Verify results
    assert len(results) == NUM_CONCURRENT_PINGS, f"Expected {NUM_CONCURRENT_PINGS} responses, got {len(results)}"
    
    for idx, elapsed, response in results:
        assert response['error'] is None, f"Request {idx} returned error: {response['error']}"
        assert response['data'] == 'pong', f"Request {idx} got unexpected response: {response['data']}"
    
    avg_time = sum(r[1] for r in results) / len(results)
    print(f"\n  OK: All {NUM_CONCURRENT_PINGS} concurrent requests completed")
    print(f"  Total time: {total_time*1000:.1f} ms")
    print(f"  Average RTT: {avg_time*1000:.1f} ms")
    print(f"  Min RTT: {min(r[1] for r in results)*1000:.1f} ms")
    print(f"  Max RTT: {max(r[1] for r in results)*1000:.1f} ms")
    
    # If requests were truly concurrent, total time should be closer to max RTT
    # than to sum of all RTTs (which would be the case for sequential processing)
    sequential_time = sum(r[1] for r in results)
    print(f"  Sequential time would be: {sequential_time*1000:.1f} ms")
    print(f"  Speedup: {sequential_time/total_time:.2f}x")
    print()


def test_mixed_concurrent_requests():
    """
    Test that different types of requests can be processed concurrently.
    """
    print("--- TEST: Mixed Concurrent Requests ---")
    sock = connect()
    
    results = []
    lock = threading.Lock()
    
    def send_ping():
        elapsed, response = send_request(sock, 'ping')
        with lock:
            results.append(('ping', elapsed, response))
        print(f"  Ping: RTT {elapsed*1000:.1f} ms")
        return elapsed, response
    
    def send_balance():
        elapsed, response = send_request(sock, 'get_balance')
        with lock:
            results.append(('get_balance', elapsed, response))
        print(f"  Balance: RTT {elapsed*1000:.1f} ms")
        return elapsed, response
    
    def send_search():
        elapsed, response = send_request(sock, 'search_markets', ['Bitcoin', 3])
        with lock:
            results.append(('search_markets', elapsed, response))
        print(f"  Search: RTT {elapsed*1000:.1f} ms")
        return elapsed, response
    
    # Send all requests concurrently
    start_time = time.perf_counter()
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = []
        # Send multiple of each type
        for _ in range(NUM_MIXED_REQUESTS_PER_TYPE):
            futures.append(executor.submit(send_ping))
            futures.append(executor.submit(send_balance))
            futures.append(executor.submit(send_search))
        
        # Wait for all to complete
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"  ERROR: {e}")
    
    total_time = time.perf_counter() - start_time
    
    sock.close()
    
    # Verify results
    assert len(results) == 9, f"Expected 9 responses, got {len(results)}"
    
    for action, elapsed, response in results:
        assert response['error'] is None, f"{action} returned error: {response['error']}"
    
    print(f"\n  OK: All 9 mixed concurrent requests completed")
    print(f"  Total time: {total_time*1000:.1f} ms")
    
    # Break down by action type
    ping_times = [r[1] for r in results if r[0] == 'ping']
    balance_times = [r[1] for r in results if r[0] == 'get_balance']
    search_times = [r[1] for r in results if r[0] == 'search_markets']
    
    print(f"  Ping avg: {sum(ping_times)/len(ping_times)*1000:.1f} ms")
    print(f"  Balance avg: {sum(balance_times)/len(balance_times)*1000:.1f} ms")
    print(f"  Search avg: {sum(search_times)/len(search_times)*1000:.1f} ms")
    print()


if __name__ == '__main__':
    print(f"Connecting to Polymarket Dispatcher at {HOST}:{PORT} ...")
    try:
        s = connect()
        s.close()
    except (ConnectionRefusedError, OSError) as e:
        print(f"FATAL: Could not connect to dispatcher — is it running?  ({e})")
        sys.exit(1)
    
    print(f"Connected.\n")
    
    try:
        test_concurrent_pings()
        test_mixed_concurrent_requests()
        
        print("All concurrent request tests passed!")
    except AssertionError as e:
        print(f"TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
