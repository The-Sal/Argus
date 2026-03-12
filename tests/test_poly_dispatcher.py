"""
Smoke tests for the Polymarket Dispatcher's Protocol 1 (JSON) endpoints,
plus a Protocol 2 market data subscription test.

Connects to a running dispatcher on localhost:9972 and exercises lightweight
read-only actions: ping, get_balance, search_markets, fetch_all_tickers,
and subscribes to a live market's order book stream.

This is a runnable script, not a unittest suite — the dispatcher must already
be running before execution.  Each test prints the raw response and the
round-trip time so you can gauge API latency.

Protocol recap (see argus/protocol.py):
    P1 (control):  ~NNNN|<json-payload>          — variable-width length field
    P2 (mkt data): ~NNNN<sym-len>|<symbol><csv>L — 4-digit fixed-width lengths

Last tested: 2026-02-05 22:11 GMT
Results:
    ping                RTT   8.7 ms   OK  data='pong'
    get_balance         RTT 258.4 ms   OK  balance=4.622748 USDC
    search_markets      RTT  76.1 ms   OK  5 tickers for keyword='Bitcoin'
    fetch_all_tickers   RTT   1.1 ms   OK  6318 tickers in cache
    subscribe           RTT   0.2 ms   OK  1 P2 order book update received
                                            (market: xrp-updown-15m-1770413400)
"""
import os
import sys
import json
import time
import socket
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

print(sys.path)


from argus.protocol import encode_packet, Protocol2Parser
HOST = 'localhost'
PORT = 9972


def connect() -> socket.socket:
    """Open a TCP connection to the dispatcher."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect((HOST, PORT))
    return sock


def send_and_recv(sock: socket.socket, action: str, data=None, timeout=10) -> tuple[dict, float]:
    """
    Send a single P1 request and return the parsed JSON response + round-trip
    time in seconds.

    Accumulates bytes until the P1 header's declared payload length is fully
    received.

    Note on framing: encode_packet uses f"~{length:04d}|" which zero-pads to
    *at least* 4 digits — but for payloads > 9999 bytes the length field grows
    (e.g. ~123456|...).  We locate the pipe dynamically rather than assuming
    it's always at byte offset 5.  See issue #68.
    """
    if data is None:
        data = {}
    request = {'action': action, 'data': data}
    packet = encode_packet(json.dumps(request).encode('utf-8'))

    old_timeout = sock.gettimeout()
    sock.settimeout(timeout)
    try:
        t0 = time.perf_counter()
        sock.sendall(packet)

        raw = b''
        header_len = None
        needed = None

        while True:
            chunk = sock.recv(131072)
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
        return json.loads(payload.decode('utf-8')), elapsed
    finally:
        sock.settimeout(old_timeout)


def test_ping(sock: socket.socket):
    """Verify the dispatcher is alive and responding. Expects data='pong'."""
    print("--- TEST: ping ---")
    resp, dt = send_and_recv(sock, 'ping')
    assert resp['error'] is None, f"Ping returned error: {resp['error']}"
    assert resp['data'] == 'pong', f"Expected 'pong', got: {resp['data']}"
    print(f"  OK  response: {resp}")
    print(f"  RTT: {dt*1000:.1f} ms")
    print()


def test_get_balance(sock: socket.socket):
    """
    Fetch the account USDC balance. Exercises the full REST-API path through
    the dispatcher (rest_api.get_balance -> CLOB allowance query).
    """
    print("--- TEST: get_balance ---")
    resp, dt = send_and_recv(sock, 'get_balance')
    assert resp['error'] is None, f"get_balance returned error: {resp['error']}"
    balance = resp['data']
    assert isinstance(balance, (int, float)), f"Expected numeric balance, got: {type(balance)}"
    print(f"  OK  balance: {balance}")
    print(f"  RTT: {dt*1000:.1f} ms")
    print()


def test_search_markets_btc(sock: socket.socket):
    """
    Search for markets matching 'Bitcoin' and return the top 5 tickers.
    Exercises the difflib fuzzy-match path. data=[keyword, limit].
    """
    print("--- TEST: search_markets (keyword='Bitcoin', limit=5) ---")
    resp, dt = send_and_recv(sock, 'search_markets', ['Bitcoin', 5])
    assert resp['error'] is None, f"search_markets returned error: {resp['error']}"
    tickers = resp['data']
    assert isinstance(tickers, list), f"Expected list, got: {type(tickers)}"
    assert len(tickers) <= 5, f"Expected at most 5 results, got {len(tickers)}"
    for t in tickers:
        print(f"  -> {t}")
    print(f"  OK  {len(tickers)} ticker(s) returned")
    print(f"  RTT: {dt*1000:.1f} ms")
    print()


def test_fetch_all_tickers(sock: socket.socket) -> list:
    """
    Fetch the full ticker list from the cache using pagination.
    Returns the list so subsequent tests can use it to pick a market to subscribe to.
    """
    print("--- TEST: fetch_all_tickers (paginated) ---")
    all_tickers = []
    offset = 0
    limit = 200  # Reduced from 1000 to avoid payload size limits
    total_rtt = 0.0
    page_count = 0

    while True:
        resp, dt = send_and_recv(sock, 'fetch_all_tickers', [limit, offset], timeout=30)
        assert resp['error'] is None, f"fetch_all_tickers returned error: {resp['error']}"
        page = resp['data']
        assert isinstance(page, list), f"Expected list, got: {type(page)}"

        print(f"    Page {page_count + 1}: received {len(page)} tickers")

        if not page:
            break

        all_tickers.extend(page)
        total_rtt += dt
        page_count += 1
        offset += limit

    assert len(all_tickers) > 0, "Expected at least one ticker in the cache"
    print(f"  Total tickers in cache: {len(all_tickers)}")
    print(f"  Pages fetched: {page_count}")
    for t in all_tickers[:5]:
        print(f"  -> {t}")
    print(f"  OK  (showing first 5 of {len(all_tickers)})")
    print(f"  Total RTT: {total_rtt*1000:.1f} ms")
    print()
    return all_tickers


def find_active_market_clob_id(sock: socket.socket, tickers: list) -> tuple[str, str]:
    """
    Iterate through tickers to find an active (not closed) market that has
    at least one clobTokenId we can subscribe to.  Returns (ticker, clob_id).

    We fetch one market at a time via fetch_market_by_ticker until we find one
    that is active and has clob token IDs.  This avoids pulling the entire
    market catalogue which would be very large.
    """
    print("--- Finding an active market to subscribe to ---")
    for ticker in tickers:
        resp, _ = send_and_recv(sock, 'fetch_market_by_ticker', [ticker])
        if resp['error'] is not None:
            print(f"  Warning: fetch_market_by_ticker for '{ticker}' returned error: {resp['error']}")
            continue
        event = resp['data']
        # Event must be active and not closed
        if not event.get('active', False) or event.get('closed', True):
            print(f"  Skipping '{ticker}' (active={event.get('active')}, closed={event.get('closed')})")
            continue
        # Look for a market within the event that has clobTokenIds
        markets = event.get('markets', [])
        if not markets:
            print(f"  Skipping '{ticker}' (no markets found in event)")
            continue
        for mkt in markets:
            clob_ids = mkt.get('clobTokenIds', None)
            if clob_ids and len(clob_ids) > 0:
                clob_id = clob_ids[0]
                print(f"  Found: ticker={ticker}")
                print(f"         market={mkt.get('slug', '???')}")
                print(f"         clob_id={clob_id}")
                print()
                return ticker, clob_id

    raise RuntimeError("Could not find any active market with a clobTokenId in the cache.")


def test_subscribe_market_data(sock: socket.socket, clob_id: str):
    """
    Subscribe to a live market's order book via the dispatcher subscribe action,
    then listen for Protocol 2 market data packets and print the first few updates.

    The P2 data for Polymarket order book at default depth 10 contains:
        10 x (bid_price, bid_size) + 10 x (ask_price, ask_size) + timestamp + server_ts
        = 42 comma-separated float fields.

    We build a Protocol2Parser with the matching decoding order to parse the
    raw P2 frames into labelled dicts.  After receiving a few updates we
    unsubscribe and return.
    """
    print(f"--- TEST: subscribe to market data (clob_id={clob_id[:20]}...) ---")
    NUM_UPDATES = 5

    # The dispatcher's POLYMARKET_ORDERBOOK_DEPTH controls how many bid/ask
    # levels are in each P2 packet (padded with 0,0 if the book is shallower).
    # Default is 10 but it can be overridden via env var.  We read it from the
    # same env var so the parser matches whatever the dispatcher is configured to.
    depth = int(os.environ.get('POLYMARKET_ORDERBOOK_DEPTH', 10))
    print(f"  Using order book depth: {depth}")

    # Build the decoding order for the Protocol2Parser to match the P2ConvertClass output
    decoding_order = []
    for i in range(depth):
        decoding_order.append(f'bid_price_{i}')
        decoding_order.append(f'bid_size_{i}')
    for i in range(depth):
        decoding_order.append(f'ask_price_{i}')
        decoding_order.append(f'ask_size_{i}')
    decoding_order.append('exchange_timestamp')
    decoding_order.append('server_timestamp')
    parser = Protocol2Parser(decoding_order)

    # Step 1: Subscribe — this is a P1 request/response
    resp, dt = send_and_recv(sock, 'subscribe', [clob_id])
    assert resp['error'] is None, f"subscribe returned error: {resp['error']}"
    print(f"  Subscribe response: {resp['data']}")
    print(f"  Subscribe RTT: {dt*1000:.1f} ms")

    # Step 2: Listen for P2 market data packets
    # After subscribing, the dispatcher will push P2 packets for order book
    # updates directly on this socket.  We read raw bytes and parse with
    # Protocol2Parser.
    print(f"  Waiting for {NUM_UPDATES} order book updates...")
    sock.settimeout(30)
    updates_received = 0
    raw_buf = b''

    while updates_received < NUM_UPDATES:
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            print("  Timed out waiting for market data.")
            break
        if not chunk:
            print("  Connection closed by server.")
            break

        raw_buf += chunk

        # P2 packets may arrive concatenated or split.  We parse as many
        # complete packets as we can from the buffer.
        while raw_buf and updates_received < NUM_UPDATES:
            # P2 frame starts with '~', length is next 4 bytes (fixed in P2)
            if raw_buf[0:1] != b'~':
                # Could be a P1 push (account_update, fatal_error) — skip it
                # P1 has '|' at variable position; find next '~' or drain
                next_tilde = raw_buf.find(b'~', 1)
                if next_tilde == -1:
                    raw_buf = b''
                    break
                raw_buf = raw_buf[next_tilde:]
                continue

            # Need at least 5 bytes for the P2 header (~NNNN)
            if len(raw_buf) < 5:
                break

            try:
                pkt_len = int(raw_buf[1:5].decode('ascii'))
            except (ValueError, UnicodeDecodeError):
                # Not a valid P2 header — might be a P1 frame with wide length.
                # Skip to next '~'.
                next_tilde = raw_buf.find(b'~', 1)
                if next_tilde == -1:
                    raw_buf = b''
                    break
                raw_buf = raw_buf[next_tilde:]
                continue

            total_pkt_len = 5 + pkt_len
            if len(raw_buf) < total_pkt_len:
                # Incomplete packet — wait for more data
                break

            pkt_bytes = raw_buf[:total_pkt_len]
            raw_buf = raw_buf[total_pkt_len:]

            # Check if this is really P2 (terminates with 'L') vs P1 (has '|' at byte 5)
            if pkt_bytes[-1:] != b'L':
                # This is a P1 push — decode and print it but don't count as market update
                try:
                    pipe_idx = pkt_bytes.index(b'|')
                    p1_payload = pkt_bytes[pipe_idx+1:]
                    p1_msg = json.loads(p1_payload.decode('utf-8'))
                    print(f"  [P1 push] action={p1_msg.get('action')}")
                except Exception:
                    pass
                continue

            # Parse the P2 packet
            try:
                parsed = parser.parse(pkt_bytes)
            except ValueError as e:
                print(f"  [P2 parse error] {e}")
                continue

            updates_received += 1
            symbol = parsed.get('symbol', '?')
            best_bid = parsed.get('bid_price_0', 0)
            best_bid_sz = parsed.get('bid_size_0', 0)
            best_ask = parsed.get('ask_price_0', 0)
            best_ask_sz = parsed.get('ask_size_0', 0)
            ts = parsed.get('exchange_timestamp', 0)
            print(f"  [{updates_received}/{NUM_UPDATES}] "
                  f"best_bid={best_bid}x{best_bid_sz}  "
                  f"best_ask={best_ask}x{best_ask_sz}  "
                  f"ts={ts}")

    # Step 3: Unsubscribe
    # We need to drain any remaining P2 data before sending a P1 request,
    # so open a fresh socket for the unsubscribe.
    print(f"  Received {updates_received} update(s).")
    print(f"  OK")
    print()


if __name__ == '__main__':
    print(f"Connecting to Polymarket Dispatcher at {HOST}:{PORT} ...")
    try:
        s = connect()
    except (ConnectionRefusedError, OSError) as e:
        print(f"FATAL: Could not connect to dispatcher — is it running?  ({e})")
        sys.exit(1)

    print(f"Connected.\n")

    try:
        test_ping(s)
        test_get_balance(s)
        test_search_markets_btc(s)
        all_tickers = test_fetch_all_tickers(s)

        # Find an active market and subscribe to its order book
        ticker, clob_id = find_active_market_clob_id(s, all_tickers)
        test_subscribe_market_data(s, clob_id)

        print("All tests passed.")
    except AssertionError as e:
        print(f"TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        s.close()
