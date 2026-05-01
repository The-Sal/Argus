"""
Test script for place_multiple_orders endpoint.

Connects to a running dispatcher on localhost:9972 and tests the place_multiple_orders
action which internally uses build_order (with thread pool concurrency) and place_built_orders.

This script will:
1. Connect to the dispatcher
2. Fetch balance and search for markets
3. Find a live BTC hourly market
4. Subscribe to the market
5. Place multiple tiny orders using place_multiple_orders
6. Verify the results

Usage:
    python test_place_multiple_orders.py

The dispatcher must already be running before execution.
"""
import os
import sys
import json
import time
import socket
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from argus.protocol import encode_packet, decompress_p1_response


HOST = 'localhost'
PORT = 9972


def connect() -> socket.socket:
    """Open a TCP connection to the dispatcher."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect((HOST, PORT))
    return sock


def _extract_p1_p2_frames(raw: bytes):
    """Extract all complete P1/P2 frames from raw buffer.
    Returns (list_of_frames, remaining_bytes).
    Each frame is a tuple: ('p1', dict) or ('p2', bytes).
    
    P1 format: ~<variable-length>|<payload>
    P2 format: ~<4-digit-pkt-len><4-digit-sym-len>|<symbol><data>L
    """
    frames = []
    while raw and raw[0:1] == b'~':
        # Need at least ~NNNN (5 bytes) to read a length
        if len(raw) < 5:
            break

        # Find pipe position dynamically
        pipe_idx = raw.find(b'|')
        if pipe_idx == -1:
            break  # incomplete

        # Try P2 first: P2 has the structure ~NNNN where bytes 1-4
        # are the packet length, and the | is at position 9 (after 4-byte sym_len).
        # Also P2 packets end with 'L'.
        is_p2 = False
        if pipe_idx == 9:
            try:
                p2_pkt_len = int(raw[1:5].decode('ascii'))
                p2_total = 5 + p2_pkt_len
                if len(raw) >= p2_total and raw[p2_total - 1:p2_total] == b'L':
                    # This is a P2 frame
                    frame_bytes = raw[:p2_total]
                    raw = raw[p2_total:]
                    frames.append(('p2', frame_bytes))
                    is_p2 = True
                elif len(raw) < p2_total:
                    break  # incomplete P2
            except (ValueError, UnicodeDecodeError):
                pass

        if not is_p2:
            # P1 frame: ~<length>|<payload>
            try:
                payload_len = int(raw[1:pipe_idx].decode('ascii'))
            except (ValueError, UnicodeDecodeError):
                break
            header_len = pipe_idx + 1
            needed = header_len + payload_len
            if len(raw) < needed:
                break  # incomplete

            frame_payload = raw[header_len:needed]
            raw = raw[needed:]

            try:
                msg = json.loads(frame_payload.decode('utf-8'))
                msg = decompress_p1_response(msg)
                frames.append(('p1', msg))
            except json.JSONDecodeError:
                pass  # skip unparseable

    return frames, raw


def send_and_recv(sock: socket.socket, action: str, data=None, timeout=10) -> tuple[dict, float, list]:
    """
    Send a single P1 request and return the parsed JSON response + round-trip time.
    Handles interleaved P2 market data packets and P1 pushes (account_update, etc.).
    """
    if data is None:
        data = {}
    request = {'action': action, 'data': data}
    packet = encode_packet(json.dumps(request).encode('utf-8'))

    old_timeout = sock.gettimeout()
    sock.settimeout(timeout)
    pushed: list[dict] = []
    try:
        t0 = time.perf_counter()
        sock.sendall(packet)

        raw = b''
        while True:
            chunk = sock.recv(131072)
            if not chunk:
                raise ConnectionError("Server closed connection before responding.")
            raw += chunk

            frames, raw = _extract_p1_p2_frames(raw)
            for ftype, fdata in frames:
                if ftype == 'p2':
                    continue  # P2 frames are ignored for this test
                # ftype == 'p1'
                msg = fdata
                if msg.get('action') == action:
                    elapsed = time.perf_counter() - t0
                    return msg, elapsed, pushed
                else:
                    # This is a P1 push message (account_update, fatal_error, etc.)
                    pushed.append(msg)

    finally:
        sock.settimeout(old_timeout)


def fmt_ms(seconds: float) -> str:
    """Format a duration in seconds as milliseconds."""
    return f"{seconds * 1000:>8.1f} ms"


def separator(title: str):
    width = 80
    print()
    print('=' * width)
    print(f"  {title}")
    print('=' * width)


def step_ping(sock: socket.socket):
    """Verify the dispatcher is alive."""
    separator("STEP 1: ping")
    resp, dt, _ = send_and_recv(sock, 'ping')
    assert resp['error'] is None, f"Ping error: {resp['error']}"
    assert resp['data'] == 'pong', f"Expected 'pong', got: {resp['data']}"
    print(f"  OK   data={resp['data']!r}")
    print(f"  RTT: {fmt_ms(dt)}")
    return resp, dt


def step_get_balance(sock: socket.socket):
    """Fetch account USDC balance."""
    separator("STEP 2: get_balance")
    resp, dt, _ = send_and_recv(sock, 'get_balance')
    assert resp['error'] is None, f"get_balance error: {resp['error']}"
    balance = resp['data']
    assert isinstance(balance, (int, float)), f"Expected numeric balance, got: {type(balance)}"
    print(f"  OK   balance={balance} USDC")
    print(f"  RTT: {fmt_ms(dt)}")
    return balance, dt


def step_fetch_all_tickers(sock: socket.socket) -> tuple[list, float]:
    """Fetch every ticker from the dispatcher's market cache using pagination."""
    separator("STEP 3: fetch_all_tickers (paginated)")
    all_tickers = []
    offset = 0
    limit = 200
    total_rtt = 0.0
    page_count = 0

    while True:
        resp, dt, _ = send_and_recv(sock, 'fetch_all_tickers', [limit, offset], timeout=30)
        assert resp['error'] is None, f"fetch_all_tickers error: {resp['error']}"
        page = resp['data']
        assert isinstance(page, list), f"Expected list, got: {type(page)}"

        print(f"    Page {page_count + 1}: received {len(page)} tickers")

        if not page:
            break

        all_tickers.extend(page)
        total_rtt += dt
        page_count += 1
        offset += limit

    assert len(all_tickers) > 0, "Expected non-empty ticker list"
    print(f"  OK   {len(all_tickers)} tickers in cache")
    print(f"  Pages fetched: {page_count}")
    print(f"  Total RTT: {fmt_ms(total_rtt)}")
    return all_tickers, total_rtt


def step_find_live_btc_event(sock: socket.socket, tickers: list) -> tuple[dict, str, float]:
    """
    Find a currently-live Bitcoin hourly market.
    """
    separator("STEP 4: find live BTC hourly event")
    btc_tickers = [t for t in tickers if 'bitcoin-up-or-down' in t and '-et' in t]
    print(f"  Candidate BTC hourly tickers: {len(btc_tickers)}")

    if not btc_tickers:
        raise RuntimeError("No bitcoin-up-or-down hourly tickers found in cache.")

    now = datetime.now(timezone.utc)
    total_rtt = 0.0

    for ticker in btc_tickers:
        resp, dt, _ = send_and_recv(sock, 'fetch_market_by_ticker', [ticker])
        total_rtt += dt
        if resp['error'] is not None:
            continue
        event = resp['data']

        if not event.get('active', False) or event.get('closed', True):
            continue

        markets = event.get('markets', [])
        if not markets:
            continue

        mkt = markets[0]
        clob_ids = mkt.get('clobTokenIds', None)
        if not clob_ids or len(clob_ids) == 0:
            continue

        # Parse start/end times
        start_str = mkt.get('eventStartTime') or mkt.get('startDate')
        end_str = mkt.get('endDate')
        if not start_str or not end_str:
            continue

        try:
            start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
            end_dt = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            continue

        if start_dt <= now <= end_dt:
            token_id = clob_ids[0]
            print(f"  LIVE  ticker={ticker}")
            print(f"        started={start_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            print(f"        ends   ={end_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            print(f"        token_id={token_id}")
            print(f"  Lookup RTT (cumulative): {fmt_ms(total_rtt)}")
            return event, token_id, total_rtt

    raise RuntimeError(
        f"No currently-live BTC hourly market found among {len(btc_tickers)} candidates."
    )


def step_subscribe(sock: socket.socket, token_id: str) -> tuple[dict, float]:
    """Subscribe to the live market's asset_id."""
    separator(f"STEP 5: subscribe ({token_id[:20]}...)")
    resp, dt, _ = send_and_recv(sock, 'subscribe', [token_id], timeout=15)
    assert resp['error'] is None, f"subscribe error: {resp['error']}"
    result = resp['data']
    subscribed = result.get('subscribed', [])
    failed = result.get('failed', [])
    assert token_id in subscribed, f"token_id not in subscribed list: {result}"
    assert len(failed) == 0, f"Subscription failures: {failed}"
    print(f"  OK   subscribed={subscribed}")
    print(f"  RTT: {fmt_ms(dt)}")
    return result, dt


def step_place_multiple_orders(sock: socket.socket, token_id: str) -> tuple[dict, float]:
    """
    Place multiple tiny orders using place_multiple_orders action.
    Tests concurrent build_order execution via thread pool.
    
    Response structure from place_built_orders:
    {
        'success': [
            {
                'errorMsg': '',
                'orderID': '0x...',
                'takingAmount': '',
                'makingAmount': '',
                'status': 'live',
                'success': True
            },
            ...
        ],
        'failed': [
            {
                'errorMsg': 'error description',
                'orderID': '',
                'takingAmount': '',
                'makingAmount': '',
                'status': '',
                'success': True  # Note: CLOB returns success:true even for errors
            },
            ...
        ]
    }
    """
    separator("STEP 6: place_multiple_orders (3 tiny BUY orders @ 0.01)")
    
    # Create 3 tiny orders
    orders = [
        {
            'token_id': token_id,
            'price': 0.01,
            'size': 1,  # Very small size - may fail with "min size: $1" error
            'side': 'buy',
        },
        {
            'token_id': token_id,
            'price': 0.01,
            'size': 1,
            'side': 'buy',
        },
        {
            'token_id': token_id,
            'price': 0.01,
            'size': 1,
            'side': 'buy',
        }
    ]
    
    resp, dt, _ = send_and_recv(sock, 'place_multiple_orders', {'orders': orders}, timeout=30)
    assert resp['error'] is None, f"place_multiple_orders error: {resp['error']}"
    result = resp['data']
    
    print(f"  OK   Response received")
    print(f"  RTT: {fmt_ms(dt)}")
    
    # Validate response structure
    assert isinstance(result, dict), f"Expected dict response, got {type(result)}"
    assert 'success' in result, f"Response missing 'success' key. Got: {result.keys()}"
    assert 'failed' in result, f"Response missing 'failed' key. Got: {result.keys()}"
    
    success_orders = result.get('success', [])
    failed_orders = result.get('failed', [])
    
    print(f"  Successful orders: {len(success_orders)}")
    print(f"  Failed orders: {len(failed_orders)}")
    
    # Print details of each order result
    for i, order_result in enumerate(success_orders):
        order_id = order_result.get('orderID', 'N/A')
        status = order_result.get('status', 'N/A')
        print(f"    Success [{i+1}]: orderID={order_id[:20] if len(order_id) > 20 else order_id}... status={status}")
    
    for i, order_result in enumerate(failed_orders):
        error_msg = order_result.get('errorMsg', 'Unknown error')
        print(f"    Failed [{i+1}]: error={error_msg[:60]}...")
    
    return result, dt


def step_cancel_orders(sock: socket.socket, order_ids: list) -> tuple[list, float]:
    """Cancel multiple orders one by one (since cancel_order handles single order_id)."""
    separator(f"STEP 7: cancel {len(order_ids)} order(s)")
    
    total_dt = 0.0
    all_results = []
    
    for i, order_id in enumerate(order_ids):
        resp, dt, _ = send_and_recv(sock, 'cancel_order', {'order_id': order_id}, timeout=15)
        total_dt += dt
        assert resp['error'] is None, f"cancel_order error for order {i+1}: {resp['error']}"
        result = resp['data']
        all_results.append(result)
        print(f"    Order {i+1} ({order_id[:16]}...): canceled={result.get('canceled', [])}")
    
    print(f"  Total RTT for all cancels: {fmt_ms(total_dt)}")
    return all_results, total_dt


def run_test():
    timings: list[tuple[str, float]] = []

    print(f"Connecting to Polymarket Dispatcher at {HOST}:{PORT} ...")
    try:
        sock = connect()
    except (ConnectionRefusedError, OSError) as e:
        print(f"FATAL: Could not connect to dispatcher — is it running?  ({e})")
        sys.exit(1)
    print("Connected.\n")

    try:
        # 1. Ping
        _, dt = step_ping(sock)
        timings.append(('ping', dt))

        # 2. Get balance
        balance, dt = step_get_balance(sock)
        timings.append(('get_balance', dt))

        # 3. Fetch all tickers
        all_tickers, dt = step_fetch_all_tickers(sock)
        timings.append(('fetch_all_tickers', dt))

        # 4. Find a live BTC hourly event
        event, token_id, dt = step_find_live_btc_event(sock, all_tickers)
        timings.append(('find_live_btc_event', dt))

        # 5. Subscribe to the asset (required for account_update pushes)
        _, dt = step_subscribe(sock, token_id)
        timings.append(('subscribe', dt))

        # 6. Place multiple orders
        result, dt = step_place_multiple_orders(sock, token_id)
        timings.append(('place_multiple_orders', dt))

        # 7. Extract order IDs from successful orders and cancel them
        separator("STEP 7: Analyze results and cancel successful orders")
        
        success_orders = result.get('success', [])
        failed_orders = result.get('failed', [])
        
        # Extract order IDs from successful orders
        order_ids = []
        for order_result in success_orders:
            order_id = order_result.get('orderID', '')
            # Only include orders with valid order IDs
            if order_id and order_id != '':
                order_ids.append(order_id)
        
        print(f"  Extracted {len(order_ids)} order ID(s) from successful orders")
        
        if order_ids:
            cancel_results, dt = step_cancel_orders(sock, order_ids)
            timings.append(('cancel_orders', dt))
        else:
            print(f"  No valid order IDs to cancel.")
            if failed_orders:
                print(f"  All {len(failed_orders)} order(s) failed to place.")
                print("  Common errors:")
                print("    - 'min size: $1' means order size was too small (need at least $1 worth)")
                print("    - Market may be closed or invalid price")
            else:
                print("  Warning: No successful orders but also no failed orders reported.")
                print("  Full result for inspection:")
                print(json.dumps(result, indent=2))

        # ─── Timing Summary ─────────────────────────────────────────────
        separator("TIMING SUMMARY")
        total = 0.0
        for label, dt in timings:
            total += dt
            print(f"  {label:<35s} {fmt_ms(dt)}")
        print(f"  {'─' * 35} {'─' * 10}")
        print(f"  {'TOTAL':<35s} {fmt_ms(total)}")
        print()
        # ─── Final Summary ─────────────────────────────────────────────
        separator("FINAL SUMMARY")
        
        success_count = len(result.get('success', []))
        failed_count = len(result.get('failed', []))
        
        print(f"  Orders placed: {success_count + failed_count}")
        print(f"  Successful: {success_count}")
        print(f"  Failed: {failed_count}")
        print(f"  Orders cancelled: {len(order_ids) if order_ids else 0}")
        print()
        
        if failed_count > 0 and success_count == 0:
            print("  NOTE: All orders failed to place.")
            print("  This may be due to:")
            print("    - Order size too small (minimum is typically $1 worth)")
            print("    - Invalid price for the market")
            print("    - Market not accepting orders")
            print()
            print("  Test completed, but no orders were placed.")
            print("  To fix: Increase order size or adjust price.")
            print("  This can also be because those markets are about to expire.")
        elif success_count > 0:
            print("  Test completed successfully!")
            print("  The place_multiple_orders endpoint is working and uses")
            print("  concurrent build_order calls via thread pool.")
        else:
            print("  Test completed, but no orders were processed.")
            print("  Check dispatcher logs for more details.")

    except AssertionError as e:
        print(f"\nTEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {e}")
        traceback.print_exc()
        sys.exit(1)
    finally:
        sock.close()


if __name__ == '__main__':
    run_test()
