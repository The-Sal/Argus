#!/usr/bin/env python3
"""
Interactive CLI client for querying the Argus TradingView dispatcher.

This mirrors tests/hyper_cli.py / tests/lighter_cli.py but targets
TradingViewDispatcher (argus/tv/dispatcher.py) instead of a perpetuals
dispatcher. Unlike those, the TradingView dispatcher is market-data-only:
there are no trading actions, no REST-backed info lookups and no P1 system
pushes -- just subscribe/unsubscribe and live quote streaming.

Usage:
    python tests/tv_cli.py                 # Start interactive mode
    python tests/tv_cli.py --test          # Run the read-only gauntlet and exit

Protocol:
    - P1 (control): ~NNNN|<json-payload>
      Every TradingView request MUST include a "correlation_id" (enforced by
      BaseDispatcher, same as Hyperliquid/Lighter). Supported actions:
      subscribe / unsubscribe / get_subscriptions / ping / version.
    - P2 (async market-data push): ~<packet-length><symbol-length>|<symbol><csv-data>L
      Pushed unsolicited once a client has `subscribe`d to one or more symbols
      (see TradingViewDispatcher._quote_callback). Encoded with the same
      argus.protocol.transmit_mkt_data_with_protocol_2 wire format the other
      dispatchers use, via TVP2ConvertClass (argus/tv/dispatcher.py).

Symbols use TradingView's EXCHANGE:SYMBOL format, e.g. "NASDAQ:AAPL" or
"BINANCE:BTCUSD". Each P2 packet carries the FULL merged last-known quote
state for its symbol (the dispatcher merges TradingView's partial per-field
updates upstream), so every packet is self-contained.
"""
import os
import sys
import json
import time
import uuid
import socket
from typing import Any, Callable, Dict, List, Optional, Tuple
sys.path.insert(0, __file__.replace('/tests/tv_cli.py', ''))
from argus import protocol

# Must match TVP2ConvertClass.FIELD_ORDER (argus/tv/dispatcher.py), since the
# P2 packet's field count/order depends on it. Unlike the perpetuals CLIs this
# is a fixed 10-field order -- there is no variable order book depth.
P2_DECODING_ORDER: List[str] = [
    'bid', 'bid_size', 'ask', 'ask_size', 'last', 'change',
    'change_pct', 'volume', 'timestamp', 'transmission_time',
]

# Default symbol for the gauntlet's live-stream check. Crypto is used because
# it trades 24/7 (unlike exchange equities, which only tick during their
# session hours); BTCUSD in particular always ticks.
DEFAULT_GAUNTLET_SYMBOL = 'BINANCE:BTCUSD'


# =============================================================================
# P2 Protocol Parser for Quote Data
# =============================================================================
#
# Standalone port of argus.protocol.Protocol2Parser, mirroring the local
# P2PacketParser in tests/hyper_cli.py / tests/lighter_cli.py -- see those
# files for why (keeps the CLI self-contained and explicit about the wire).

class P2PacketParser:
    """
    Parser for Protocol 2 market data packets from the TradingView dispatcher.
    Format: ~<packet-length><symbol-length>|<symbol><market-data>L
    """

    def __init__(self, decoding_order: Optional[List[str]] = None):
        self.decoding_order = decoding_order if decoding_order is not None else P2_DECODING_ORDER

    def parse(self, packet_bytes: bytes) -> Dict[str, Any]:
        if len(packet_bytes) < 11:
            raise ValueError("Packet too short for Protocol 2 format")

        pos = 0
        if packet_bytes[pos] != ord('~'):
            raise ValueError("Invalid header: missing start marker '~'")
        pos += 1

        packet_length = int(packet_bytes[pos:pos + 4].decode('ascii'))
        pos += 4

        expected_total_length = 5 + packet_length
        if len(packet_bytes) != expected_total_length:
            raise ValueError(
                f"Packet length mismatch: expected {expected_total_length}, got {len(packet_bytes)}"
            )

        symbol_length = int(packet_bytes[pos:pos + 4].decode('ascii'))
        pos += 4

        if packet_bytes[pos] != ord('|'):
            raise ValueError("Missing pipe separator after symbol length")
        pos += 1

        symbol = packet_bytes[pos:pos + symbol_length].decode('ascii')
        pos += symbol_length

        if packet_bytes[-1] != ord('L'):
            raise ValueError("Invalid terminator: expected 'L'")

        market_data_str = packet_bytes[pos:-1].decode('ascii')
        values = self._parse_csv_values(market_data_str)

        if len(values) != len(self.decoding_order):
            raise ValueError(
                f"Field count mismatch: expected {len(self.decoding_order)} values, got {len(values)}"
            )

        result: Dict[str, Any] = {'symbol': symbol}
        for i, field_name in enumerate(self.decoding_order):
            result[field_name] = values[i]
        return result

    @staticmethod
    def _parse_csv_values(data_str: str) -> List[float]:
        if not data_str:
            raise ValueError("Empty market data")
        return [float(v) for v in data_str.split(',')]

    def parse_multiple(self, mixed_packets: bytes) -> List[Dict[str, Any]]:
        packets = []
        position = 0
        while position < len(mixed_packets):
            if mixed_packets[position] != ord('~'):
                raise ValueError(f"Invalid packet start at position {position}")
            packet_length = int(mixed_packets[position + 1:position + 5].decode('ascii'))
            total_packet_length = 5 + packet_length
            packet_bytes = mixed_packets[position:position + total_packet_length]
            packets.append(self.parse(packet_bytes))
            position += total_packet_length
        return packets


# =============================================================================
# Mixed P1/P2 Stream Splitting
# =============================================================================
#
# A subscribed socket carries P2 quote frames interleaved with P1 responses.
# The two share a '~NNNN' length header but differ after it, so they are told
# apart by structure: P2's first '|' sits at byte 9 (after a 4-digit symbol
# length) and the frame ends with 'L', while P1 is '~NNNN|' followed directly
# by its JSON payload. Same splitter as tests/hyper_cli.py / tests/lighter_cli.py.

def extract_p1_p2_frames(raw: bytes) -> Tuple[List[Tuple[str, bytes]], bytes]:
    """
    Split a mixed byte stream into complete frames. Returns (frames, leftover),
    where each frame is ('p1', raw_frame_bytes) or ('p2', raw_frame_bytes), and
    `leftover` holds an incomplete trailing frame to prepend on the next call.
    """
    frames: List[Tuple[str, bytes]] = []
    while raw and raw[0:1] == b'~':
        if len(raw) < 5:
            break  # need ~NNNN before any length can be read

        pipe_idx = raw.find(b'|')
        if pipe_idx == -1:
            break  # incomplete frame

        # P2 probe: first pipe at byte 9 (after the 4-digit symbol length) and
        # a trailing 'L'. If the probe fails, fall through to the P1 layout.
        if pipe_idx == 9:
            try:
                p2_packet_len = int(raw[1:5].decode('ascii'))
                p2_total = 5 + p2_packet_len
                if len(raw) >= p2_total and raw[p2_total - 1:p2_total] == b'L':
                    frames.append(('p2', raw[:p2_total]))
                    raw = raw[p2_total:]
                    continue
                if len(raw) < p2_total:
                    break  # incomplete P2 frame
            except (ValueError, UnicodeDecodeError):
                pass

        # P1 frame: ~<length>|<payload>
        try:
            payload_len = int(raw[1:pipe_idx].decode('ascii'))
        except (ValueError, UnicodeDecodeError):
            break
        frame_len = pipe_idx + 1 + payload_len
        if len(raw) < frame_len:
            break  # incomplete P1 frame
        frames.append(('p1', raw[:frame_len]))
        raw = raw[frame_len:]

    return frames, raw


# =============================================================================
# Client
# =============================================================================

class TVArgusClient:
    """Client for the Argus TradingView dispatcher."""

    def __init__(self, host: str = 'localhost', port: int = 9974):
        self.host = host
        self.port = port
        self.socket: Optional[socket.socket] = None
        self.p2_parser = P2PacketParser()
        self._recv_buffer = b''

    def connect(self) -> None:
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(30)
            self.socket.connect((self.host, self.port))
        except (ConnectionRefusedError, OSError) as e:
            raise ConnectionError(f"Could not connect to dispatcher at {self.host}:{self.port} - {e}")

    def disconnect(self) -> None:
        if self.socket:
            self.socket.close()
            self.socket = None

    def _recv_framed_payload(self) -> bytes:
        """
        Read one full P1 packet off the socket and return its payload bytes.
        Interleaved P2 (quote) frames are skipped; an incomplete trailing frame,
        as well as any complete frames after the returned one, are kept in
        self._recv_buffer for the next read.
        """
        if not self.socket:
            raise ConnectionError("Not connected to server")

        while True:
            frames, self._recv_buffer = extract_p1_p2_frames(self._recv_buffer)
            for i, (frame_type, frame_bytes) in enumerate(frames):
                if frame_type == 'p1':
                    later_frames = b''.join(frame for _, frame in frames[i + 1:])
                    self._recv_buffer = later_frames + self._recv_buffer
                    return protocol.decode_packet(frame_bytes)

            chunk = self.socket.recv(131072)
            if not chunk:
                raise ConnectionError("Server closed connection before responding.")
            self._recv_buffer += chunk

    def send_request(self, action: str, data: Any = None, timeout: int = 30) -> Tuple[dict, float]:
        """Send a P1 request (with a fresh correlation_id) and return (response, round-trip time)."""
        if data is None:
            data = {}

        correlation_id = str(uuid.uuid4())
        request = {'action': action, 'data': data, 'correlation_id': correlation_id}
        packet = protocol.encode_packet(json.dumps(request).encode('utf-8'))

        if not self.socket:
            raise ConnectionError("Not connected to server")

        old_timeout = self.socket.gettimeout()
        self.socket.settimeout(timeout)
        try:
            t0 = time.perf_counter()
            self.socket.sendall(packet)
            payload = self._recv_framed_payload()
            elapsed = time.perf_counter() - t0

            response = json.loads(payload.decode('utf-8'))
            response = protocol.decompress_p1_response(response)

            resp_corr_id = response.get('correlation_id')
            if resp_corr_id is not None and resp_corr_id != correlation_id:
                print(f"  ⚠ Warning: response correlation_id {resp_corr_id} does not match request {correlation_id}")

            return response, elapsed
        finally:
            self.socket.settimeout(old_timeout)

    def ping(self, timeout: int = 30) -> Tuple[str, float]:
        resp, dt = self.send_request('ping', timeout=timeout)
        if resp.get('error'):
            raise Exception(f"ping failed: {resp['error']}")
        return str(resp.get('data') or ''), dt

    def version(self, timeout: int = 30) -> Tuple[dict, float]:
        resp, dt = self.send_request('version', timeout=timeout)
        if resp.get('error'):
            raise Exception(f"version failed: {resp['error']}")
        return dict(resp.get('data') or {}), dt

    def subscribe(self, symbols: List[str], timeout: int = 30) -> Tuple[dict, float]:
        """Subscribe to live P2 quote updates for one or more TradingView symbols (e.g. ['NASDAQ:AAPL'])."""
        resp, dt = self.send_request('subscribe', symbols, timeout=timeout)
        if resp.get('error'):
            raise Exception(f"subscribe failed: {resp['error']}")
        return dict(resp.get('data') or {}), dt

    def unsubscribe(self, symbols: List[str], timeout: int = 30) -> Tuple[dict, float]:
        resp, dt = self.send_request('unsubscribe', symbols, timeout=timeout)
        if resp.get('error'):
            raise Exception(f"unsubscribe failed: {resp['error']}")
        return dict(resp.get('data') or {}), dt

    def get_subscriptions(self, timeout: int = 30) -> Tuple[List[str], float]:
        """The symbols THIS client socket is currently subscribed to."""
        resp, dt = self.send_request('get_subscriptions', timeout=timeout)
        if resp.get('error'):
            raise Exception(f"get_subscriptions failed: {resp['error']}")
        return list((resp.get('data') or {}).get('subscriptions') or []), dt

    def receive_packets(self, timeout: float = 0.1) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Drain whatever packets are currently sitting on the socket, without
        blocking, and return them split by type: (P2 quote packets, P1 system
        pushes). Incomplete trailing frames stay buffered for the next call.

        The TradingView dispatcher emits no P1 system pushes today, but the
        split is kept for API parity with the other CLIs (and in case pushes
        are added later). `timeout` is accepted for the same reason: a
        non-blocking recv() either returns available bytes immediately or
        raises BlockingIOError, so the drain is inherently instantaneous.
        """
        if not self.socket:
            raise ConnectionError("Not connected to server")

        _ = timeout
        data = b''
        self.socket.setblocking(False)
        try:
            while True:
                try:
                    chunk = self.socket.recv(131072)
                except BlockingIOError:
                    break
                if not chunk:
                    raise ConnectionError("Server closed connection.")
                data += chunk
        finally:
            self.socket.setblocking(True)

        frames, self._recv_buffer = extract_p1_p2_frames(self._recv_buffer + data)

        p2_packets: List[Dict[str, Any]] = []
        pushes: List[Dict[str, Any]] = []
        for frame_type, frame_bytes in frames:
            if frame_type == 'p2':
                p2_packets.append(self.p2_parser.parse(frame_bytes))
                continue
            try:
                payload = protocol.decode_packet(frame_bytes)
                push = json.loads(payload.decode('utf-8'))
                pushes.append(protocol.decompress_p1_response(push))
            except (ValueError, UnicodeDecodeError):
                continue  # unparseable push -- skip rather than kill the stream

        return p2_packets, pushes

    def receive_p2_packets(self, timeout: float = 0.1) -> List[Dict[str, Any]]:
        """Return only the P2 (quote push) packets from receive_packets(),
        discarding any P1 system pushes. Kept for the gauntlet's stream checks."""
        packets, _ = self.receive_packets(timeout)
        return packets


# =============================================================================
# Gauntlet (live "test mode" that exercises every known action)
# =============================================================================
#
# Reuses TVArgusClient's own methods (no separate request-building logic), so
# this stays honest about what the CLI actually calls. The TradingView
# dispatcher is market-data-only, so there are no trading actions to exclude.

class GauntletFailure(AssertionError):
    """Raised by a gauntlet check to record a readable failure reason."""


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise GauntletFailure(message)


def _check_numeric(value: Any, field: str, context: str) -> None:
    try:
        float(value)
    except (TypeError, ValueError):
        raise GauntletFailure(f"{context}: field '{field}' = {value!r} is not numeric")


def _gauntlet_version(client: 'TVArgusClient', timeout: float) -> Tuple[float, str]:
    data, dt = client.version(timeout=timeout)
    _check(isinstance(data, dict), "response is not an object")
    _check('argus' in data, "missing 'argus' version")
    tv_version = data.get('tradingview_dispatcher')
    _check(isinstance(tv_version, list) and len(tv_version) == 4, f"'tradingview_dispatcher' malformed: {tv_version!r}")
    return dt, f"argus={data.get('argus')} tradingview_dispatcher={tv_version}"


def _gauntlet_ping(client: 'TVArgusClient', timeout: float) -> Tuple[float, str]:
    pong, dt = client.ping(timeout=timeout)
    _check(pong == 'pong', f"expected 'pong', got {pong!r}")
    return dt, "pong"


def _gauntlet_get_subscriptions(client: 'TVArgusClient', timeout: float) -> Tuple[float, str]:
    subs, dt = client.get_subscriptions(timeout=timeout)
    _check(isinstance(subs, list), "'subscriptions' is not a list")
    _check(all(isinstance(s, str) for s in subs), "subscriptions contain non-string symbols")
    return dt, f"{len(subs)} subscription(s): {subs or '(none)'}"


# -----------------------------------------------------------------------------
# Market-data streaming (P2) gauntlet check
# -----------------------------------------------------------------------------
#
# Drives the live quote path: subscribe -> receive + validate the pushed P2
# quotes -> unsubscribe and assert pushes stop -> re-subscribe and assert
# pushes resume. CLI-level counterpart to tests/test_tv_dispatcher_live.py; a
# CLI client cannot force the dispatcher's upstream socket to drop, so this
# verifies the subscription lifecycle end to end instead, including the
# refcounted teardown (`subscription_expired`).

def _drain_p2(client: 'TVArgusClient', seconds: float, stop_on_packet: bool) -> List[Dict[str, Any]]:
    """Drain P2 packets for up to `seconds`. If `stop_on_packet`, return on the
    first non-empty drain rather than waiting out the whole window."""
    deadline = time.time() + seconds
    collected: List[Dict[str, Any]] = []
    while time.time() < deadline:
        packets = client.receive_p2_packets()
        if packets:
            collected.extend(packets)
            if stop_on_packet:
                return collected
        time.sleep(0.02)
    return collected


def _has_price_data(packet: Dict[str, Any]) -> bool:
    """True if the merged quote carries at least one real price. The first push
    after subscribing can be an all-zero placeholder (the dispatcher's merged
    state is empty until the upstream session's fields land), so gauntlet
    checks wait for a meaningful quote rather than validating the placeholder."""
    return any(float(packet.get(f) or 0) > 0 for f in ('last', 'bid', 'ask'))


def _drain_until_meaningful(client: 'TVArgusClient', seconds: float) -> List[Dict[str, Any]]:
    """Drain P2 packets until at least one carries real price data (or `seconds` elapse)."""
    deadline = time.time() + seconds
    collected: List[Dict[str, Any]] = []
    while time.time() < deadline:
        packets = client.receive_p2_packets()
        if packets:
            collected.extend(packets)
            if any(_has_price_data(p) for p in packets):
                return collected
        time.sleep(0.02)
    return collected


def _validate_p2_quote(packet: Dict[str, Any], symbol: str) -> float:
    """Validate one decoded P2 quote packet; return its dispatcher->CLI latency in ms."""
    _check(packet.get('symbol') == symbol, f"P2 packet symbol {packet.get('symbol')!r} != subscribed {symbol!r}")

    for field in ('bid', 'bid_size', 'ask', 'ask_size', 'last', 'change', 'change_pct', 'volume'):
        _check_numeric(packet.get(field), field, f"P2 quote for {symbol!r}")

    last = packet['last']
    bid = packet['bid']
    ask = packet['ask']
    _check(last > 0 or bid > 0 or ask > 0, f"P2 quote for {symbol!r} carried no price data")
    if bid > 0 and ask > 0:
        _check(bid <= ask, f"crossed quote bid={bid} ask={ask}")
    _check(packet['volume'] >= 0, "negative volume")

    transmission_time = packet.get('transmission_time', 0)
    _check(transmission_time > 0, "P2 packet missing/zero transmission_time")
    latency_ms = time.time() * 1000.0 - transmission_time * 1000.0
    _check(
        -2000.0 < latency_ms < 5000.0,
        f"implausible dispatcher->CLI latency {latency_ms:.1f}ms (clock skew or unit mismatch?)"
    )
    return latency_ms


def _gauntlet_market_data_stream(client: 'TVArgusClient', timeout: float) -> Tuple[float, str]:
    symbol = getattr(client, '_gauntlet_symbol', DEFAULT_GAUNTLET_SYMBOL)

    window = max(2.0, min(timeout, 5.0))
    start = time.perf_counter()

    result, _ = client.subscribe([symbol], timeout=int(timeout))
    _check(symbol in (result.get('subscribed') or []), f"subscribe did not confirm {symbol!r}: {result!r}")
    try:
        first = _drain_until_meaningful(client, window)
        meaningful = [p for p in first if _has_price_data(p)]
        _check(len(meaningful) > 0, f"no P2 quote with price data for {symbol!r} within {window:.0f}s of subscribing")
        latencies = [_validate_p2_quote(packet, symbol) for packet in meaningful]

        # Unsubscribe: this socket must stop receiving the symbol's quotes.
        client.unsubscribe([symbol], timeout=int(timeout))
        _drain_p2(client, 0.5, stop_on_packet=False)  # flush in-flight pushes
        quiet = _drain_p2(client, 1.0, stop_on_packet=False)
        _check(not quiet, f"received {len(quiet)} P2 packet(s) after unsubscribing from {symbol!r}")

        # Re-subscribe: pushes must resume.
        client.subscribe([symbol], timeout=int(timeout))
        resumed = _drain_until_meaningful(client, window)
        _check(any(_has_price_data(p) for p in resumed), f"no P2 quote with price data for {symbol!r} after re-subscribing")
    finally:
        try:
            client.unsubscribe([symbol], timeout=int(timeout))
        except Exception:
            pass

    dt = time.perf_counter() - start
    avg = sum(latencies) / len(latencies)
    return dt, f"symbol={symbol} meaningful_quotes={len(meaningful)} avg_latency={avg:.1f}ms (subscribe/unsubscribe/re-subscribe OK)"


# (display name, check function) -- add new actions here as the dispatcher's
# routing table grows.
GAUNTLET_CHECKS: List[Tuple[str, Callable[['TVArgusClient', float], Tuple[float, str]]]] = [
    ("version", _gauntlet_version),
    ("ping", _gauntlet_ping),
    ("get_subscriptions", _gauntlet_get_subscriptions),
    ("market_data (subscribe/P2 lifecycle)", _gauntlet_market_data_stream),
]


def run_gauntlet(client: 'TVArgusClient', timeout: float = 15.0, symbol: str = DEFAULT_GAUNTLET_SYMBOL) -> bool:
    """Calls every known action against a live dispatcher and validates the shape of each response."""
    client._gauntlet_symbol = symbol
    print("\n" + "=" * 72)
    print("TRADINGVIEW DISPATCHER GAUNTLET (live endpoint)")
    print(f"  per-check timeout: {timeout:.0f}s")
    print(f"  stream check symbol: {symbol}")
    print("=" * 72)

    results: List[Tuple[str, str, float, str]] = []
    for name, check in GAUNTLET_CHECKS:
        start = time.perf_counter()
        try:
            dt, detail = check(client, timeout)
            status, message = "PASS", detail
        except GauntletFailure as e:
            status, message, dt = "FAIL", str(e), time.perf_counter() - start
        except socket.timeout:
            status, message, dt = "FAIL", f"timed out after {timeout:.0f}s", time.perf_counter() - start
        except Exception as e:
            status, message, dt = "ERROR", f"{type(e).__name__}: {e}", time.perf_counter() - start

        results.append((name, status, dt, message))
        icon = {"PASS": "✓", "FAIL": "✗", "ERROR": "‼"}[status]
        print(f"  {icon} {name:<40} {status:<6} {dt*1000:>8.1f}ms  {message}")

    passed = sum(1 for _, status, _, _ in results if status == "PASS")
    total = len(results)
    print("=" * 72)
    print(f"  {passed}/{total} checks passed")
    print("=" * 72 + "\n")
    return passed == total


# =============================================================================
# Formatting helpers
# =============================================================================

def format_version(data: dict) -> str:
    output = []
    output.append("\n" + "=" * 60)
    output.append("VERSION")
    output.append("=" * 60)
    output.append(f"  Argus core:                {data.get('argus')}")
    output.append(f"  TradingView dispatcher:    {data.get('tradingview_dispatcher')}")
    output.append("=" * 60)
    return "\n".join(output)


def format_subscriptions(symbols: List[str]) -> str:
    output = ["\n" + "=" * 60, "THIS CLIENT'S SUBSCRIPTIONS", "=" * 60]
    if not symbols:
        output.append("  (none)")
    for i, symbol in enumerate(symbols):
        output.append(f"  {i + 1:>2}. {symbol}")
    output.append("=" * 60)
    return "\n".join(output)


def format_quote(packet: Dict[str, Any], receive_time: Optional[float] = None) -> str:
    """Format one decoded P2 quote packet (the full merged last-known state)."""
    if receive_time is None:
        receive_time = time.time()

    ts = packet.get('timestamp', 0)
    tx = packet.get('transmission_time', 0)
    trade_age_ms = (receive_time - ts) * 1000.0 if ts > 0 else 0.0
    net_latency_ms = (receive_time - tx) * 1000.0 if tx > 0 else 0.0

    output = []
    output.append("\n" + "=" * 60)
    output.append(f"QUOTE: {packet.get('symbol')}")
    output.append("=" * 60)
    output.append(f"  Bid:            {packet.get('bid', 0):,.4f}  (size {packet.get('bid_size', 0):,.2f})")
    output.append(f"  Ask:            {packet.get('ask', 0):,.4f}  (size {packet.get('ask_size', 0):,.2f})")
    output.append(f"  Last:           {packet.get('last', 0):,.4f}")
    output.append(f"  Change:         {packet.get('change', 0):+.4f} ({packet.get('change_pct', 0):+.3f}%)")
    output.append(f"  Volume:         {packet.get('volume', 0):,.0f}")
    if ts > 0:
        trade_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))
        output.append(f"  Last trade:     {trade_time} ({trade_age_ms:,.0f}ms ago)")
    else:
        output.append("  Last trade:     (never observed since dispatcher start)")
    output.append(f"  Net latency:    {net_latency_ms:.1f}ms (dispatcher -> CLI)")
    output.append("=" * 60)
    return "\n".join(output)


# =============================================================================
# Live market-data streaming (P2)
# =============================================================================

def subscribe_quote_mode(client: TVArgusClient, symbols: List[str]):
    """
    Subscribe to one or more symbols' live quotes and display each update with
    latency. Press Ctrl+C to stop, unsubscribe everything, and show aggregate
    statistics. Mirrors tests/hyper_cli.py's subscribe_market_data_mode.
    """
    net_latencies: List[float] = []
    per_symbol_counts: Dict[str, int] = {}
    packet_count = 0
    push_count = 0
    start_time = time.time()

    print(f"\n📡 Subscribing to quotes: {', '.join(symbols)}")
    try:
        result, rtt = client.subscribe(symbols)
        print(f"✓ Subscribed successfully (RTT: {rtt*1000:.1f}ms)")
        print(f"  Subscribed: {result.get('subscribed', [])}")
        if result.get('failed'):
            print(f"  Failed: {result.get('failed', [])}")
    except Exception as e:
        print(f"✗ Subscription failed: {e}")
        return

    print(f"\n📊 Monitoring quotes... Press Ctrl+C to stop and view statistics.")
    print(f"   Each packet is the full merged last-known state for its symbol")
    print(f"\n{'PACKET':<8} {'SYMBOL':<24} {'LAST':<14} {'BID':<14} {'ASK':<14} {'CHG%':<9} {'VOL':<14} {'NET(ms)':<10}")
    print("-" * 115)

    try:
        while True:
            packets, pushes = client.receive_packets(timeout=0.1)

            for push in pushes:
                push_count += 1
                action = push.get('action', 'unknown')
                print(f"[push] {action}: {push.get('data')}")

            for packet_data in packets:
                packet_count += 1
                receive_time = time.time()
                symbol = packet_data.get('symbol', '?')
                per_symbol_counts[symbol] = per_symbol_counts.get(symbol, 0) + 1

                tx = packet_data.get('transmission_time', 0)
                net_latency_ms = (receive_time - tx) * 1000.0 if tx > 0 else 0.0
                if tx > 0:
                    net_latencies.append(net_latency_ms)

                print(
                    f"{packet_count:<8} {symbol:<24} "
                    f"{packet_data.get('last', 0):<14,.4f} "
                    f"{packet_data.get('bid', 0):<14,.4f} "
                    f"{packet_data.get('ask', 0):<14,.4f} "
                    f"{packet_data.get('change_pct', 0):<+9.3f} "
                    f"{packet_data.get('volume', 0):<14,.0f} "
                    f"{net_latency_ms:<10.2f}"
                )

    except KeyboardInterrupt:
        print(f"\n\n🛑 Stopped by user.")

        try:
            print(f"📡 Unsubscribing from: {', '.join(symbols)}")
            client.unsubscribe(symbols)
            print(f"✓ Unsubscribed successfully")
        except Exception as e:
            print(f"⚠ Unsubscribe warning: {e}")

        duration = time.time() - start_time
        print(f"\n📈 Session Summary:")
        print(f"  Duration: {duration:.1f} seconds")
        print(f"  Total packets: {packet_count}")
        print(f"  System pushes: {push_count}")
        if duration > 0:
            print(f"  Packets/sec: {packet_count/duration:.1f}")
        for symbol in sorted(per_symbol_counts):
            print(f"  {symbol}: {per_symbol_counts[symbol]} packet(s)")
        if net_latencies:
            print(f"  Net latency (dispatcher -> CLI):")
            print(f"    Avg: {sum(net_latencies)/len(net_latencies):.2f}ms")
            print(f"    Min/Max: {min(net_latencies):.2f}ms / {max(net_latencies):.2f}ms")


def quote_snapshot_mode(client: TVArgusClient, symbol: str, timeout: float = 30.0):
    """
    One-shot snapshot: subscribe to a symbol, wait for the first merged P2
    quote (TradingView sends the last-known state immediately when a symbol is
    added to the shared upstream session), print it, then unsubscribe again.
    """
    print(f"\n📸 Snapshot of {symbol} (subscribing briefly)...")
    try:
        result, rtt = client.subscribe([symbol])
        if symbol not in (result.get('subscribed') or []):
            print(f"✗ Subscribe did not confirm {symbol!r}: {result}")
            return
        print(f"✓ Subscribed (RTT: {rtt*1000:.1f}ms), waiting for first quote...")

        deadline = time.time() + timeout
        while time.time() < deadline:
            packets = client.receive_p2_packets()
            for packet in packets:
                if packet.get('symbol') == symbol:
                    print(format_quote(packet))
                    return
            time.sleep(0.05)

        print(f"✗ No quote received for {symbol!r} within {timeout:.0f}s")
    except Exception as e:
        print(f"✗ Snapshot failed: {e}")
    finally:
        try:
            client.unsubscribe([symbol])
            print(f"✓ Unsubscribed from {symbol}")
        except Exception as e:
            print(f"⚠ Unsubscribe warning: {e}")


# =============================================================================
# Interactive CLI
# =============================================================================

def _parse_symbols(rest: str) -> List[str]:
    """Split the remainder of a command line into symbol tokens (whitespace or comma separated)."""
    rest = rest.replace(',', ' ')
    return [s for s in rest.split() if s]


def print_banner(host: str, port: int):
    print("\n" + "=" * 50)
    print("  Argus TradingView Interactive CLI")
    print(f"  Connected to {host}:{port}")
    print("  Type 'help' for commands, 'quit' to exit")
    print("=" * 50 + "\n")


def print_help():
    print("\nAvailable commands:")
    print("  ping                       - Ping the dispatcher")
    print("  version                    - Show dispatcher/component version info")
    print("  subs                       - Show this client's current subscriptions")
    print("  sub <symbol> [symbol...]   - Subscribe to live quote streaming (Ctrl+C to stop)")
    print("  unsub <symbol> [symbol...] - Unsubscribe from one or more symbols")
    print("  snap <symbol>              - One-shot: subscribe, print the first merged quote, unsubscribe")
    print("  test | gauntlet            - Call every known action and validate the responses")
    print("  clear                      - Clear screen")
    print("  help                       - Show this help")
    print("  quit                       - Exit the program")
    print("\nSymbols use TradingView's EXCHANGE:SYMBOL format.")
    print("\nExamples:")
    print("  sub NASDAQ:AAPL              # stream Apple quotes")
    print("  sub BINANCE:BTCUSD NASDAQ:AAPL   # stream several symbols at once")
    print("  snap NASDAQ:TSLA             # one-shot quote snapshot of Tesla")
    print("  unsub NASDAQ:AAPL            # stop receiving AAPL quotes")
    print("  test                         # run the read-only gauntlet")
    print()


def interactive_loop(client: TVArgusClient, gauntlet_symbol: str = DEFAULT_GAUNTLET_SYMBOL):
    while True:
        try:
            query = input("tv> ").strip()

            if not query:
                continue

            if query.lower() in ['quit', 'exit', 'q']:
                print("Goodbye!")
                break
            elif query.lower() in ['help', 'h', '?']:
                print_help()
            elif query.lower() == 'clear':
                os.system('clear' if os.name == 'posix' else 'cls')
                print_banner(client.host, client.port)
            elif query.lower() == 'ping':
                try:
                    pong, dt = client.ping()
                    print(f"✓ Ping successful: {pong} ({dt*1000:.1f}ms)")
                except Exception as e:
                    print(f"✗ Ping failed: {e}")
            elif query.lower() == 'version':
                try:
                    data, dt = client.version()
                    print(format_version(data))
                    print(f"  ({dt*1000:.1f}ms)")
                except Exception as e:
                    print(f"✗ Failed to fetch version: {e}")
            elif query.lower() == 'subs':
                try:
                    subs, dt = client.get_subscriptions()
                    print(f"✓ Fetched in {dt*1000:.1f}ms")
                    print(format_subscriptions(subs))
                except Exception as e:
                    print(f"✗ Failed to fetch subscriptions: {e}")
            elif query.lower().startswith('sub '):
                symbols = _parse_symbols(query[4:])
                if not symbols:
                    print("✗ Please provide at least one symbol. Usage: sub <EXCHANGE:SYMBOL> [more...]")
                else:
                    subscribe_quote_mode(client, symbols)
            elif query.lower().startswith('unsub '):
                symbols = _parse_symbols(query[6:])
                if not symbols:
                    print("✗ Please provide at least one symbol. Usage: unsub <EXCHANGE:SYMBOL> [more...]")
                else:
                    try:
                        result, dt = client.unsubscribe(symbols)
                        print(f"✓ Unsubscribed in {dt*1000:.1f}ms")
                        print(f"  Unsubscribed: {result.get('unsubscribed', [])}")
                        if result.get('failed'):
                            print(f"  Failed: {result.get('failed', [])}")
                    except Exception as e:
                        print(f"✗ Unsubscribe failed: {e}")
            elif query.lower().startswith('snap '):
                symbols = _parse_symbols(query[5:])
                if len(symbols) != 1:
                    print("✗ Please provide exactly one symbol. Usage: snap <EXCHANGE:SYMBOL>")
                else:
                    quote_snapshot_mode(client, symbols[0])
            elif query.lower() in ('test', 'gauntlet'):
                run_gauntlet(client, symbol=gauntlet_symbol)
            else:
                print(f"Unknown command: '{query}'. Type 'help' for a list of commands.")

        except KeyboardInterrupt:
            print("\nType 'quit' to exit.")
        except EOFError:
            print("\nGoodbye!")
            break


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Argus TradingView Interactive CLI Client')
    parser.add_argument('--host', default='localhost', help='Argus server host (default: localhost)')
    parser.add_argument('--port', type=int, default=9974, help='Argus server port (default: 9974)')
    parser.add_argument('--symbol', default=DEFAULT_GAUNTLET_SYMBOL,
                        help=f"Symbol used by the gauntlet's live-stream check (default: {DEFAULT_GAUNTLET_SYMBOL})")
    parser.add_argument('--test', action='store_true',
                        help='Run the gauntlet against a live dispatcher and exit (no interactive prompt)')
    parser.add_argument('--test-timeout', type=float, default=15.0,
                        help='Per-check timeout in seconds for --test (default: 15)')

    args = parser.parse_args()

    client = TVArgusClient(args.host, args.port)

    try:
        print(f"Connecting to Argus TradingView dispatcher at {args.host}:{args.port}...")
        client.connect()

        # Test connection (the TV dispatcher has a real ping action).
        pong, rtt = client.ping()
        version, _ = client.version()
        print(f"✓ Connected (ping: {pong}, tradingview_dispatcher: {version.get('tradingview_dispatcher')}, {rtt*1000:.1f}ms)")

        if args.test:
            ok = run_gauntlet(client, timeout=args.test_timeout, symbol=args.symbol)
            sys.exit(0 if ok else 1)

        print_banner(args.host, args.port)
        interactive_loop(client, gauntlet_symbol=args.symbol)

    except ConnectionError as e:
        print(f"Connection error: {e}")
        print("Make sure the Argus TradingView dispatcher is running.")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nOperation cancelled.")
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        client.disconnect()


if __name__ == '__main__':
    main()
