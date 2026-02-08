"""
End-to-end order lifecycle test for the Polymarket Dispatcher.

Connects to a running dispatcher on localhost:9972 and exercises the full
subscribe -> place-order -> get-order-status -> cancel-order -> verify flow
against a live Bitcoin hourly market, mirroring the behaviour of
polymarket_direct/_examples/send_order_cancel_order_with_wss.py but
driven entirely through the dispatcher's P1/P2 protocol.

Every request is wrapped with a perf_counter timer so round-trip latency
is visible at a glance.

NOTE: The client MUST subscribe to at least one asset_id before placing
orders if it wants to receive real-time account_update pushes (PLACEMENT,
CANCELLATION, etc.).  See the module docstring in argus/polymarket/__init__.py
for details on this requirement.

This is a runnable script, not a unittest suite — the dispatcher must
already be running before execution.

Protocol recap (see argus/protocol.py):
    P1 (control):  ~NNNN|<json-payload>          — variable-width length field
    P2 (mkt data): ~NNNN<sym-len>|<symbol><csv>L — 4-digit fixed-width lengths

Flow:
     1. ping                 — verify dispatcher is alive
     2. get_balance          — fetch USDC balance
     3. search_markets       — fuzzy-search for 'Bitcoin' tickers
     4. fetch_all_tickers    — pull the full ticker list from cache
     5. find live BTC event  — filter for bitcoin-up-or-down hourly markets,
                               parse start/end times, pick one that is
                               currently live
     6. subscribe            — subscribe to the live market's asset_id so
                               that we receive account_update pushes AND
                               P2 market data
     7. place_order          — buy 5 contracts at minimum tick price
     8. get_order_status     — confirm the order is live on the CLOB
     9. cancel_order         — cancel the order
    10. get_order_status     — confirm the order is now cancelled
    11. drain & summarise    — drain remaining P1 pushes and P2 packets,
                               report counts and parseability
"""
import os
import sys
import json
import time
import socket
import traceback
from datetime import datetime, timezone
from argus.protocol import encode_packet
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


HOST = 'localhost'
PORT = 9972

# ─── Packet Counters ────────────────────────────────────────────────────────

class PacketCounters:
    """Accumulates P2 market data packets and P1 account_update pushes seen
    throughout the test.  Every call to send_and_recv feeds into this."""

    def __init__(self):
        self.p2_total = 0
        self.p2_parseable = 0
        self.p2_errors = 0
        self.p1_pushes: list[dict] = []  # account_update / fatal_error etc.

    def record_p2(self, parseable: bool):
        self.p2_total += 1
        if parseable:
            self.p2_parseable += 1
        else:
            self.p2_errors += 1

    def record_p1_push(self, msg: dict):
        self.p1_pushes.append(msg)


counters = PacketCounters()

# ─── Helpers ────────────────────────────────────────────────────────────────

def connect() -> socket.socket:
    """Open a TCP connection to the dispatcher."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect((HOST, PORT))
    return sock


def _try_parse_p2_frame(frame_bytes: bytes) -> bool:
    """Attempt to validate a P2 frame.  Returns True if parseable."""
    try:
        if len(frame_bytes) < 11:
            return False
        # ~NNNN<sym-len>|<symbol><csv>L
        pkt_len = int(frame_bytes[1:5].decode('ascii'))
        if len(frame_bytes) != 5 + pkt_len:
            return False
        sym_len = int(frame_bytes[5:9].decode('ascii'))
        if frame_bytes[9:10] != b'|':
            return False
        # symbol is readable ascii
        frame_bytes[10:10 + sym_len].decode('ascii')
        # ends with L
        if frame_bytes[-1:] != b'L':
            return False
        return True
    except Exception:
        return False


def _extract_frames(raw: bytes) -> tuple[list, bytes]:
    """Extract as many complete P1/P2 frames as possible from raw buffer.
    Returns (list_of_frames, remaining_bytes).
    Each frame is a tuple: ('p1', dict) or ('p2', bytes)."""
    frames = []
    while raw and raw[0:1] == b'~':
        # Need at least ~NNNN (5 bytes) to read a length
        if len(raw) < 5:
            break

        # Peek at bytes 5+ to determine P1 vs P2:
        #   P1: ~<len>|  where | is immediately after the variable-width length
        #   P2: ~NNNN<sym-len>|  where byte 5 is a digit (part of sym_len)
        # Simplest heuristic: find the | position.  For P1, it's at the end of
        # the length field.  For P2, | is at byte 9.
        # But P1 length can be >4 digits for large payloads, so we find | dynamically.

        pipe_idx = raw.find(b'|')
        if pipe_idx == -1:
            break  # incomplete

        # Check if this looks like P2: P2 has the structure ~NNNN where bytes 1-4
        # are the packet length, and the | is at position 9 (after 4-byte sym_len).
        # Also P2 packets end with 'L'.
        # For P1, the | comes right after the length digits.

        # Try P2 first: packet_len at [1:5], sym_len at [5:9], pipe at 9
        is_p2 = False
        if pipe_idx == 9:
            try:
                p2_pkt_len = int(raw[1:5].decode('ascii'))
                p2_total = 5 + p2_pkt_len
                if len(raw) >= p2_total and raw[p2_total - 1:p2_total] == b'L':
                    # This is a P2 frame
                    frame_bytes = raw[:p2_total]
                    raw = raw[p2_total:]
                    parseable = _try_parse_p2_frame(frame_bytes)
                    counters.record_p2(parseable)
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
                frames.append(('p1', msg))
            except json.JSONDecodeError:
                pass  # skip unparseable

    return frames, raw


def send_and_recv(sock: socket.socket, action: str, data=None, timeout=10) -> tuple[dict, float, list]:
    """
    Send a single P1 request and return the parsed JSON response + round-trip
    time in seconds.

    Handles interleaved P1 pushes (account_update, fatal_error) and P2
    market data packets that arrive on the same socket after subscribing.
    P2 packets are counted in the global PacketCounters; P1 pushes are
    collected and returned alongside the response.
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

            frames, raw = _extract_frames(raw)
            for ftype, fdata in frames:
                if ftype == 'p2':
                    continue  # already counted in _extract_frames
                # ftype == 'p1'
                msg = fdata
                if msg.get('action') == action:
                    elapsed = time.perf_counter() - t0
                    return msg, elapsed, pushed
                else:
                    counters.record_p1_push(msg)
                    pushed.append(msg)

    finally:
        sock.settimeout(old_timeout)


def fmt_ms(seconds: float) -> str:
    """Format a duration in seconds as a right-aligned millisecond string."""
    return f"{seconds * 1000:>8.1f} ms"


def separator(title: str):
    width = 80
    print()
    print('=' * width)
    print(f"  {title}")
    print('=' * width)


# ─── Test Steps ──────────────────────────────────────────────────────────────

def step_ping(sock: socket.socket):
    """1. Verify the dispatcher is alive."""
    separator("STEP 1: ping")
    resp, dt, pushes = send_and_recv(sock, 'ping')
    assert resp['error'] is None, f"Ping error: {resp['error']}"
    assert resp['data'] == 'pong', f"Expected 'pong', got: {resp['data']}"
    print(f"  OK   data={resp['data']!r}")
    print(f"  RTT: {fmt_ms(dt)}")
    return resp, dt


def step_get_balance(sock: socket.socket):
    """2. Fetch account USDC balance."""
    separator("STEP 2: get_balance")
    resp, dt, pushes = send_and_recv(sock, 'get_balance')
    assert resp['error'] is None, f"get_balance error: {resp['error']}"
    balance = resp['data']
    assert isinstance(balance, (int, float)), f"Expected numeric balance, got: {type(balance)}"
    print(f"  OK   balance={balance} USDC")
    print(f"  RTT: {fmt_ms(dt)}")
    return balance, dt


def step_search_markets(sock: socket.socket):
    """3. Search for Bitcoin markets via fuzzy match."""
    separator("STEP 3: search_markets (keyword='Bitcoin', limit=5)")
    resp, dt, pushes = send_and_recv(sock, 'search_markets', ['Bitcoin', 5])
    assert resp['error'] is None, f"search_markets error: {resp['error']}"
    tickers = resp['data']
    assert isinstance(tickers, list), f"Expected list, got: {type(tickers)}"
    for t in tickers:
        print(f"    -> {t}")
    print(f"  OK   {len(tickers)} ticker(s)")
    print(f"  RTT: {fmt_ms(dt)}")
    return tickers, dt


def step_fetch_all_tickers(sock: socket.socket) -> tuple[list, float]:
    """4. Fetch every ticker from the dispatcher's market cache."""
    separator("STEP 4: fetch_all_tickers")
    resp, dt, pushes = send_and_recv(sock, 'fetch_all_tickers', timeout=30)
    assert resp['error'] is None, f"fetch_all_tickers error: {resp['error']}"
    tickers = resp['data']
    assert isinstance(tickers, list) and len(tickers) > 0, "Expected non-empty ticker list"
    print(f"  OK   {len(tickers)} tickers in cache")
    print(f"  RTT: {fmt_ms(dt)}")
    return tickers, dt


def step_find_live_btc_event(sock: socket.socket, tickers: list) -> tuple[dict, str, float]:
    """
    5. Find a currently-live Bitcoin hourly market.

    Filters tickers for `bitcoin-up-or-down` + `-et`, fetches each via
    fetch_market_by_ticker, parses start/end times, and picks the first
    event whose window contains the current UTC time.

    Returns (event_dict, token_id, cumulative_rtt).
    """
    separator("STEP 5: find live BTC hourly event")
    btc_tickers = [t for t in tickers if 'bitcoin-up-or-down' in t and '-et' in t]
    print(f"  Candidate BTC hourly tickers: {len(btc_tickers)}")

    if not btc_tickers:
        raise RuntimeError("No bitcoin-up-or-down hourly tickers found in cache.")

    now = datetime.now(timezone.utc)
    total_rtt = 0.0

    for ticker in btc_tickers:
        resp, dt, pushes = send_and_recv(sock, 'fetch_market_by_ticker', [ticker])
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
        f"No currently-live BTC hourly market found among {len(btc_tickers)} candidates. "
        f"Ensure the dispatcher is running during BTC hourly market hours."
    )


def step_subscribe(sock: socket.socket, token_id: str) -> tuple[dict, float]:
    """
    6. Subscribe to the live market's asset_id.

    This is REQUIRED before placing orders if you want to receive
    account_update pushes (PLACEMENT, CANCELLATION, etc.) from the
    dispatcher.  It also starts P2 market data delivery on this socket.
    """
    separator(f"STEP 6: subscribe ({token_id[:20]}...)")
    resp, dt, pushes = send_and_recv(sock, 'subscribe', [token_id], timeout=15)
    assert resp['error'] is None, f"subscribe error: {resp['error']}"
    result = resp['data']
    subscribed = result.get('subscribed', [])
    failed = result.get('failed', [])
    assert token_id in subscribed, f"token_id not in subscribed list: {result}"
    assert len(failed) == 0, f"Subscription failures: {failed}"
    print(f"  OK   subscribed={subscribed}")
    print(f"  RTT: {fmt_ms(dt)}")
    return result, dt


def step_place_order(sock: socket.socket, token_id: str) -> tuple[dict, str, float]:
    """
    7. Place a BUY order for 5 contracts at the minimum tick price (0.01).

    This mirrors the example script which uses:
        price=float(rest.get_tick_size(tkn_id))  -> typically 0.01
        size=5
        side='buy'

    The tick size is not exposed through the dispatcher, but the minimum
    tick for BTC hourly markets on Polymarket is 0.01.  We hardcode this
    to keep the test self-contained (the order will sit on the book and
    never fill at this price).
    """
    separator("STEP 7: place_order (BUY 5 @ 0.01)")
    order_data = {
        'token_id': token_id,
        'price': 0.01,
        'size': 5,
        'side': 'buy',
    }
    resp, dt, pushes = send_and_recv(sock, 'place_order', order_data, timeout=15)
    assert resp['error'] is None, f"place_order error: {resp['error']}"
    result = resp['data']
    assert result.get('success') is True, f"Order not successful: {result}"
    order_id = result['orderID']
    print(f"  OK   order_id={order_id}")
    print(f"       status={result.get('status')}")
    print(f"  RTT: {fmt_ms(dt)}")

    for p in pushes:
        if p.get('action') == 'account_update':
            evt = p['data']
            print(f"  [WSS push] type={evt.get('type')}  status={evt.get('status')}")

    return result, order_id, dt


def step_get_order_status(sock: socket.socket, order_id: str, label: str) -> tuple[dict, float]:
    """
    8 / 10. Get the status of an order.  Used after placement and after cancellation.
    """
    separator(f"STEP {label}: get_order_status ({order_id[:16]}...)")
    resp, dt, pushes = send_and_recv(sock, 'get_order_status', {'order_id': order_id}, timeout=15)
    assert resp['error'] is None, f"get_order_status error: {resp['error']}"
    order = resp['data']
    print(f"  OK   id={order.get('id', '?')[:16]}...")
    print(f"       status={order.get('status')}")
    print(f"       side={order.get('side')}  price={order.get('price')}  size={order.get('original_size')}")
    print(f"       outcome={order.get('outcome')}")
    print(f"  RTT: {fmt_ms(dt)}")

    for p in pushes:
        if p.get('action') == 'account_update':
            evt = p['data']
            print(f"  [WSS push] type={evt.get('type')}  status={evt.get('status')}")

    return order, dt


def step_cancel_order(sock: socket.socket, order_id: str) -> tuple[dict, float]:
    """9. Cancel the order."""
    separator(f"STEP 9: cancel_order ({order_id[:16]}...)")
    resp, dt, pushes = send_and_recv(sock, 'cancel_order', {'order_id': order_id}, timeout=15)
    assert resp['error'] is None, f"cancel_order error: {resp['error']}"
    result = resp['data']
    canceled = result.get('canceled', [])
    not_canceled = result.get('not_canceled', {})
    assert order_id in canceled, (
        f"Order {order_id} was not in 'canceled' list. "
        f"canceled={canceled}, not_canceled={not_canceled}"
    )
    print(f"  OK   canceled={canceled}")
    if not_canceled:
        print(f"       not_canceled={not_canceled}")
    print(f"  RTT: {fmt_ms(dt)}")

    for p in pushes:
        if p.get('action') == 'account_update':
            evt = p['data']
            print(f"  [WSS push] type={evt.get('type')}  status={evt.get('status')}")

    return result, dt


def step_drain_and_summarise(sock: socket.socket):
    """
    11. Drain any remaining P1 pushes and P2 packets, then print a
    summary of everything we saw during the whole test.
    """
    separator("STEP 11: drain remaining packets & summarise")
    sock.settimeout(2.0)
    raw = b''
    try:
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            raw += chunk
    except socket.timeout:
        pass

    # Parse anything left in the buffer
    frames, _ = _extract_frames(raw)
    drain_p1 = 0
    drain_p2 = 0
    for ftype, fdata in frames:
        if ftype == 'p1':
            counters.record_p1_push(fdata)
            drain_p1 += 1
        else:
            drain_p2 += 1

    print(f"  Drained: {drain_p1} P1 push(es), {drain_p2} P2 packet(s)")
    print()

    # ─── P2 Market Data Summary ──────────────────────────────────────
    print(f"  P2 Market Data Packets (total across entire test):")
    print(f"    Total received:  {counters.p2_total}")
    print(f"    Parseable:       {counters.p2_parseable}")
    print(f"    Parse errors:    {counters.p2_errors}")
    print()

    # ─── P1 Account Update Summary ───────────────────────────────────
    acct_updates = [p for p in counters.p1_pushes if p.get('action') == 'account_update']
    other_pushes = [p for p in counters.p1_pushes if p.get('action') != 'account_update']
    print(f"  P1 Account Update Pushes (total across entire test):")
    print(f"    Total:  {len(acct_updates)}")
    # Print first N
    for i, p in enumerate(acct_updates):
        evt = p.get('data', {})
        print(f"    [{i + 1}] type={evt.get('type'):<14s} status={evt.get('status'):<10s} "
              f"side={evt.get('side'):<5s} price={evt.get('price'):<6s} "
              f"outcome={evt.get('outcome')}")
    if other_pushes:
        print(f"  Other P1 pushes: {len(other_pushes)}")
        for p in other_pushes:
            print(f"    action={p.get('action')}")


# ─── Main ────────────────────────────────────────────────────────────────────

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

        # 3. Search markets
        _, dt = step_search_markets(sock)
        timings.append(('search_markets', dt))

        # 4. Fetch all tickers
        all_tickers, dt = step_fetch_all_tickers(sock)
        timings.append(('fetch_all_tickers', dt))

        # 5. Find a live BTC hourly event
        event, token_id, dt = step_find_live_btc_event(sock, all_tickers)
        timings.append(('find_live_btc_event', dt))

        # 6. Subscribe to the asset (required for account_update pushes)
        _, dt = step_subscribe(sock, token_id)
        timings.append(('subscribe', dt))

        # 7. Place order
        order_result, order_id, dt = step_place_order(sock, token_id)
        timings.append(('place_order', dt))

        # 8. Get order status (should be LIVE)
        order_status, dt = step_get_order_status(sock, order_id, label='8')
        timings.append(('get_order_status (live)', dt))

        # 9. Cancel the order
        cancel_result, dt = step_cancel_order(sock, order_id)
        timings.append(('cancel_order', dt))

        # 10. Get order status (should be CANCELED)
        order_status_after, dt = step_get_order_status(sock, order_id, label='10')
        timings.append(('get_order_status (cancelled)', dt))

        # 11. Drain and summarise
        step_drain_and_summarise(sock)

        # ─── Timing Summary ─────────────────────────────────────────────
        separator("TIMING SUMMARY")
        total = 0.0
        for label, dt in timings:
            total += dt
            print(f"  {label:<35s} {fmt_ms(dt)}")
        print(f"  {'─' * 35} {'─' * 10}")
        print(f"  {'TOTAL':<35s} {fmt_ms(total)}")
        print()
        print("All steps passed.")

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
