#!/usr/bin/env python3
"""
Interactive CLI client for querying the Argus Lighter perpetuals dispatcher.

This mirrors tests/hyper_cli.py but targets LighterDispatcher
(argus/perpetuals/lighter/__init__.py) instead of the HyperLiquid dispatcher.

Usage:
    python tests/lighter_cli.py    # Start interactive mode

Protocol:
    - P1 (control): ~NNNN|<json-payload>
    Every Lighter request MUST include a "correlation_id".
    Once a client has subscribed, the same socket also carries unsolicited P1
    system pushes (e.g. the hourly refreshed funding rate, action "market_info";
    see LighterDispatcher._distribute_refreshed_perpetuals).
    - P2 (async market-data push): ~<packet-length><symbol-length>|<symbol><csv-data>L
    Pushed unsolicited once a client has `subscribe`d to one or more symbols (see
    LighterDispatcher._order_book_update_callback / argus/perpetuals/lighter/wss.py).
    Encoded with the same argus.protocol.transmit_mkt_data_with_protocol_2 wire
    format Hyperliquid/Polymarket use, via LighterP2ConvertClass
    (argus/perpetuals/lighter/_classes.py).

Unlike Hyperliquid, Lighter has no HIP-3-style builder-deployed dexes -- there is a
single unified market list (no dex_name param), and market_info is a single flat
lookup rather than a multi-call annotation/category assembly.
"""
import os
import sys
import json
import time
from datetime import datetime
import uuid
import socket
from typing import Any, Callable, Dict, List, Optional, Tuple
sys.path.insert(0, __file__.replace('/tests/lighter_cli.py', ''))
from argus import protocol

# Must match LighterDispatcher's default (argus/perpetuals/lighter/__init__.py),
# since the P2 packet's field count/order depends on it.
ORDERBOOK_DEPTH = int(os.environ.get('LIGHTER_ORDERBOOK_DEPTH', 10))


# =============================================================================
# P2 Protocol Parser for Order Book Data
# =============================================================================
#
# Standalone port of argus.protocol.Protocol2Parser, mirroring tests/hyper_cli.py's
# own local P2PacketParser -- see that file for why (a fixed decoding_order doesn't
# fit a variable order book depth).

def build_p2_decoding_order(depth: int = ORDERBOOK_DEPTH) -> List[str]:
    """Build the field decoding order for a P2 packet at a given order book depth."""
    fields = []
    for i in range(depth):
        fields.append(f'bid_{i}_price')
        fields.append(f'bid_{i}_size')
    for i in range(depth):
        fields.append(f'ask_{i}_price')
        fields.append(f'ask_{i}_size')
    fields.append('book_timestamp')
    fields.append('server_timestamp')
    return fields


P2_DECODING_ORDER = build_p2_decoding_order()


class P2PacketParser:
    """
    Parser for Protocol 2 market data packets from the Lighter dispatcher.
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


def format_orderbook(parsed_data: Dict[str, Any], depth: int = 5) -> str:
    """Format order book data from a parsed P2 packet."""
    output = [f"\n{'SIDE':<6} {'LEVEL':<6} {'PRICE':<14} {'SIZE':<15}", "-" * 45]

    bids, asks = [], []
    for i in range(depth):
        price, size = parsed_data.get(f'bid_{i}_price', 0), parsed_data.get(f'bid_{i}_size', 0)
        if price > 0 and size > 0:
            bids.append((price, size))
    for i in range(depth):
        price, size = parsed_data.get(f'ask_{i}_price', 0), parsed_data.get(f'ask_{i}_size', 0)
        if price > 0 and size > 0:
            asks.append((price, size))

    for i, (price, size) in enumerate(bids[:depth]):
        output.append(f"{'BID':<6} {i:<6} {price:<14.4f} {size:<15.4f}")
    output.append("-" * 45)
    for i, (price, size) in enumerate(asks[:depth]):
        output.append(f"{'ASK':<6} {i:<6} {price:<14.4f} {size:<15.4f}")

    return "\n".join(output)


# =============================================================================
# Mixed P1/P2 Stream Splitting
# =============================================================================
#
# A subscribed socket carries both P2 order book frames and P1 system pushes
# (e.g. funding-rate updates) interleaved. The two share a '~NNNN' length header
# but differ after it, so they are told apart by structure: P2's first '|' sits
# at byte 9 (after a 4-digit symbol length) and the frame ends with 'L', while
# P1 is '~NNNN|' followed directly by its JSON payload. Mirrors the extraction
# helpers in tests/test_poly_dispatcher_order_lifecycle.py.

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

class LighterArgusClient:
    """Client for the Argus Lighter perpetuals dispatcher."""

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
        Interleaved P2 (order book) frames are skipped; an incomplete trailing
        frame, as well as any complete frames after the returned one, are kept
        in self._recv_buffer for the next read.
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

    def products_version(self, timeout: int = 30) -> Tuple[dict, float]:
        resp, dt = self.send_request('products_version', timeout=timeout)
        if resp.get('error'):
            raise Exception(f"products_version failed: {resp['error']}")
        return dict(resp.get('data') or {}), dt

    def get_markets(self, offset: int = 0, limit: Optional[int] = None, timeout: int = 30) -> Tuple[List[dict], float]:
        data = {'offset': offset}
        if limit is not None:
            data['limit'] = limit
        resp, dt = self.send_request('get_markets', data, timeout=timeout)
        if resp.get('error'):
            raise Exception(f"get_markets failed: {resp['error']}")
        return list((resp.get('data') or {}).get('perpetuals') or []), dt

    def get_funding_rates_for_all_perpetuals(self, offset: int = 0, limit: Optional[int] = None, timeout: int = 30) -> Tuple[List[dict], float]:
        data = {'offset': offset}
        if limit is not None:
            data['limit'] = limit
        resp, dt = self.send_request('get_funding_rates_for_all_perpetuals', data, timeout=timeout)
        if resp.get('error'):
            raise Exception(f"get_funding_rates_for_all_perpetuals failed: {resp['error']}")
        return list((resp.get('data') or {}).get('funding_rates') or []), dt

    def market_info(self, symbol: Optional[str] = None, market_id: Optional[int] = None, timeout: int = 30) -> Tuple[Optional[dict], float]:
        if symbol is None and market_id is None:
            raise ValueError("market_info requires 'symbol' or 'market_id'")
        data = {'symbol': symbol} if symbol is not None else {'market_id': market_id}
        resp, dt = self.send_request('market_info', data, timeout=timeout)
        if resp.get('error'):
            raise Exception(f"market_info failed: {resp['error']}")
        return (resp.get('data') or {}).get('perpetual'), dt

    def get_funding_history(
        self, market_id: int, start_timestamp: int, end_timestamp: Optional[int] = None,
        resolution: str = '1h', timeout: int = 30
    ) -> Tuple[List[dict], float]:
        data = {'market_id': market_id, 'start_timestamp': start_timestamp, 'resolution': resolution}
        if end_timestamp is not None:
            data['end_timestamp'] = end_timestamp
        resp, dt = self.send_request('get_funding_history', data, timeout=timeout)
        if resp.get('error'):
            raise Exception(f"get_funding_history failed: {resp['error']}")
        return list((resp.get('data') or {}).get('funding_history') or []), dt

    def search_perpetuals(self, keyword: str, limit: int = 10, timeout: int = 30) -> Tuple[List[str], float]:
        resp, dt = self.send_request('search_perpetuals', {'keyword': keyword, 'limit': limit}, timeout=timeout)
        if resp.get('error'):
            raise Exception(f"search_perpetuals failed: {resp['error']}")
        return list((resp.get('data') or {}).get('perpetuals') or []), dt

    def get_funding_rate(self, symbol: str, timeout: int = 30) -> Tuple[dict, float]:
        resp, dt = self.send_request('get_funding_rate', {'symbol': symbol}, timeout=timeout)
        if resp.get('error'):
            raise Exception(f"get_funding_rate failed: {resp['error']}")
        return dict(resp.get('data') or {}), dt

    # --- account (shared actions; see argus/perpetuals/shared/account.py) ---
    #
    # Same action names as poly_cli / PolymarketDispatcher (get_balance, get_positions,
    # get_orders, get_order_status, get_trades) plus get_funding_payments. Every list
    # action is paginated with offset/limit because each record carries the venue's
    # full payload under "venue" and a P1 packet caps at ~10KB.

    def _account_request(self, action: str, data: dict, key: Optional[str], timeout: int) -> Tuple[Any, float]:
        resp, dt = self.send_request(action, data, timeout=timeout)
        if resp.get('error'):
            raise Exception(f"{action} failed: {resp['error']}")
        payload = resp.get('data') or {}
        return (payload if key is None else payload.get(key)), dt

    def get_balance(self, timeout: int = 30) -> Tuple[dict, float]:
        data: Dict[str, Any] = {}
        return self._account_request('get_balance', data, None, timeout)

    def get_positions(self, offset: int = 0, limit: Optional[int] = None, timeout: int = 30) -> Tuple[List[dict], float]:
        data: Dict[str, Any] = {'offset': offset}
        if limit is not None:
            data['limit'] = limit
        positions, dt = self._account_request('get_positions', data, 'positions', timeout)
        return list(positions or []), dt

    def get_orders(self, offset: int = 0, limit: Optional[int] = None, timeout: int = 30) -> Tuple[List[dict], float]:
        data: Dict[str, Any] = {'offset': offset}
        if limit is not None:
            data['limit'] = limit
        orders, dt = self._account_request('get_orders', data, 'orders', timeout)
        return list(orders or []), dt

    def get_order_status(self, order_id: str, timeout: int = 30) -> Tuple[dict, float]:
        return self._account_request('get_order_status', {'order_id': order_id}, None, timeout)

    def get_trades(self, offset: int = 0, limit: Optional[int] = None, timeout: int = 30) -> Tuple[List[dict], float]:
        data: Dict[str, Any] = {'offset': offset}
        if limit is not None:
            data['limit'] = limit
        trades, dt = self._account_request('get_trades', data, 'trades', timeout)
        return list(trades or []), dt

    def get_funding_payments(self, start_time: Optional[int] = None, end_time: Optional[int] = None,
                             offset: int = 0, limit: Optional[int] = None, timeout: int = 30) -> Tuple[dict, float]:
        """Returns the whole payload ({'account', 'start_time', 'end_time', 'funding_payments'}); times are unix ms."""
        data: Dict[str, Any] = {'offset': offset}
        if limit is not None:
            data['limit'] = limit
        if start_time is not None:
            data['start_time'] = start_time
        if end_time is not None:
            data['end_time'] = end_time
        return self._account_request('get_funding_payments', data, None, timeout)

    def subscribe(self, symbols: List[str], timeout: int = 30) -> Tuple[dict, float]:
        """Subscribe to live order book updates for one or more symbols (e.g. ['BTC'])."""
        resp, dt = self.send_request('subscribe', symbols, timeout=timeout)
        if resp.get('error'):
            raise Exception(f"subscribe failed: {resp['error']}")
        return dict(resp.get('data') or {}), dt

    def unsubscribe(self, symbols: List[str], timeout: int = 30) -> Tuple[dict, float]:
        resp, dt = self.send_request('unsubscribe', symbols, timeout=timeout)
        if resp.get('error'):
            raise Exception(f"unsubscribe failed: {resp['error']}")
        return dict(resp.get('data') or {}), dt

    def receive_packets(self, timeout: float = 0.1) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Drain whatever packets are currently sitting on the socket, without
        blocking, and return them split by type: (P2 order book packets,
        P1 system pushes -- e.g. updated funding rates). Incomplete trailing
        frames stay buffered for the next call.

        `timeout` is accepted for API parity with hyper_cli.py's client but unused
        here for the same reason: a non-blocking recv() either returns available
        bytes immediately or raises BlockingIOError, so the drain is inherently
        instantaneous.
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
        """Return only the P2 (order book push) packets from receive_packets(),
        discarding any P1 system pushes. Kept for the gauntlet's stream checks."""
        packets, _ = self.receive_packets(timeout)
        return packets


# =============================================================================
# Gauntlet (live "test mode" that exercises every known read-only action)
# =============================================================================
#
# Reuses LighterArgusClient's own methods (no separate request-building logic),
# so this stays honest about what the CLI actually calls. Trading actions are
# intentionally excluded -- as of this writing none are wired into the
# dispatcher's routing table yet (see argus/perpetuals/lighter/__init__.py).

VENUE_NAME = "Lighter"


class GauntletSkip(Exception):
    """A check that cannot run against this dispatcher as configured (e.g. no account
    credentials). Reported as SKIP and not counted as a failure."""


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


def _validate_market(market: dict, context: str) -> None:
    _check(isinstance(market, dict), f"{context}: 'market' is not an object")
    for field in ('symbol', 'market_id', 'market_type', 'status'):
        _check(field in market, f"{context}: market missing '{field}'")
    _check(isinstance(market.get('symbol'), str) and market['symbol'], f"{context}: market 'symbol' is empty")


def _validate_context(ctx: dict, context: str) -> None:
    _check(isinstance(ctx, dict), f"{context}: 'context' is not an object")
    for field in ('mark_price', 'index_price', 'open_interest'):
        _check(field in ctx, f"{context}: context missing '{field}'")
        _check_numeric(ctx.get(field), field, context)


def _validate_perp(perp: dict, context: str) -> None:
    _check(isinstance(perp, dict), f"{context}: perpetual is not an object")
    _validate_market(perp.get('market') or {}, context)
    _validate_context(perp.get('context') or {}, context)


def _gauntlet_products_version(client: 'LighterArgusClient', timeout: float) -> Tuple[float, str]:
    data, dt = client.products_version(timeout=timeout)
    _check(isinstance(data, dict), "response is not an object")
    _check('argus' in data, "missing 'argus' version")
    lighter_version = data.get('lighter_dispatcher')
    _check(isinstance(lighter_version, list) and len(lighter_version) == 4, f"'lighter_dispatcher' malformed: {lighter_version!r}")
    return dt, f"argus={data.get('argus')} lighter_dispatcher={lighter_version}"


def _gauntlet_get_markets(client: 'LighterArgusClient', timeout: float) -> Tuple[float, str]:
    perps, dt = client.get_markets(offset=0, limit=10, timeout=timeout)
    _check(isinstance(perps, list), "'perpetuals' is not a list")
    _check(len(perps) > 0, "expected at least one perpetual market")
    for perp in perps:
        _validate_perp(perp, "get_markets")
    return dt, f"{len(perps)} market(s)"


def _gauntlet_get_funding_rates(client: 'LighterArgusClient', timeout: float) -> Tuple[float, str]:
    perps, dt = client.get_funding_rates_for_all_perpetuals(offset=0, timeout=timeout)
    _check(isinstance(perps, list), "'funding_rates' is not a list")
    _check(len(perps) > 0, "expected at least one funding rate entry")
    for perp in perps:
        _validate_perp(perp, "funding rates")
    rates = [float(p['funding_rate']) for p in perps if p.get('funding_rate') is not None]
    _check(rates == sorted(rates, reverse=True), "funding rates are not sorted descending")
    if rates:
        return dt, f"{len(perps)} market(s), top funding={rates[0]:.6f}"
    return dt, f"{len(perps)} market(s), no rates present"


def _gauntlet_market_info(client: 'LighterArgusClient', timeout: float) -> Tuple[float, str]:
    perps, _ = client.get_markets(offset=0, limit=10, timeout=timeout)
    _check(len(perps) > 0, "expected at least one market to test market_info with")
    symbol = perps[0]['market']['symbol']
    info, dt = client.market_info(symbol=symbol, timeout=timeout)
    _check(info is not None, f"market_info('{symbol}') returned null")
    _validate_perp(info, f"market_info('{symbol}')")
    _check(info['market']['symbol'] == symbol, f"market_info returned symbol {info['market']['symbol']!r}, expected {symbol!r}")
    return dt, f"symbol='{symbol}' mark_price={info['context']['mark_price']}"


def _gauntlet_get_funding_history(client: 'LighterArgusClient', timeout: float) -> Tuple[float, str]:
    perps, _ = client.get_markets(offset=0, limit=10, timeout=timeout)
    _check(len(perps) > 0, "expected at least one market to test get_funding_history with")
    market_id = perps[0]['market']['market_id']
    start_ts = int(time.time()) - 24 * 60 * 60
    history, dt = client.get_funding_history(market_id=market_id, start_timestamp=start_ts, timeout=timeout)
    _check(isinstance(history, list), "'funding_history' is not a list")
    for entry in history:
        _check(isinstance(entry, dict), "funding_history entry is not an object")
        for field in ('timestamp', 'value', 'rate', 'direction'):
            _check(field in entry, f"funding_history entry missing '{field}'")
    return dt, f"market_id={market_id} -> {len(history)} entry(ies) in last 24h"


def _gauntlet_search_perpetuals(client: 'LighterArgusClient', timeout: float) -> Tuple[float, str]:
    perps, _ = client.get_markets(offset=0, limit=10, timeout=int(timeout))
    _check(len(perps) > 0, "expected at least one market to search over")
    symbol = perps[0]['market']['symbol']
    matches, dt = client.search_perpetuals(symbol, limit=10, timeout=int(timeout))
    _check(isinstance(matches, list), "'perpetuals' search result is not a list")
    _check(len(matches) > 0, f"search for {symbol!r} returned no matches")
    _check(all(isinstance(m, str) for m in matches), "search results contain non-string symbols")
    _check(symbol in matches, f"exact symbol {symbol!r} missing from its own search results: {matches!r}")
    return dt, f"keyword={symbol!r} -> {len(matches)} match(es), best={matches[0]!r}"


def _gauntlet_get_funding_rate(client: 'LighterArgusClient', timeout: float) -> Tuple[float, str]:
    perps, _ = client.get_markets(offset=0, limit=10, timeout=int(timeout))
    _check(len(perps) > 0, "expected at least one market to fetch a funding rate for")
    symbol = perps[0]['market']['symbol']
    data, dt = client.get_funding_rate(symbol, timeout=int(timeout))
    _check(isinstance(data, dict), "response is not an object")
    _check(data.get('symbol') == symbol, f"'symbol' = {data.get('symbol')!r} does not match requested {symbol!r}")
    if data.get('funding_rate') is not None:
        float(data['funding_rate'])
    return dt, f"symbol={symbol} funding_rate={data.get('funding_rate')} apr={data.get('funding_rate_apr')}"


# -----------------------------------------------------------------------------
# Market-data streaming (P2) gauntlet check
# -----------------------------------------------------------------------------
#
# Unlike the REST-backed checks above, this drives the live order book path:
# subscribe -> receive + validate the pushed P2 book -> unsubscribe and assert
# pushes stop -> re-subscribe and assert pushes resume. It is the CLI-level
# counterpart to tests/lighter_wss_latency.py (which white-boxes the wss
# itself, including the upstream reconnect and nonce-gap resync); a CLI client
# cannot force the dispatcher's upstream socket to drop, so this verifies the
# subscription lifecycle end to end instead, including the symbol<->market_id
# translation and the refcounted teardown (`subscription_expired`).

def _drain_p2(client: 'LighterArgusClient', seconds: float, stop_on_packet: bool) -> List[Dict[str, Any]]:
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


def _validate_p2_book(packet: Dict[str, Any], symbol: str) -> float:
    """Validate one decoded P2 order-book packet; return its latency in ms."""
    _check(packet.get('symbol') == symbol, f"P2 packet symbol {packet.get('symbol')!r} != subscribed {symbol!r}")

    bids, asks = [], []
    for i in range(ORDERBOOK_DEPTH):
        price, size = packet.get(f'bid_{i}_price', 0.0), packet.get(f'bid_{i}_size', 0.0)
        if price > 0 and size > 0:
            bids.append(price)
    for i in range(ORDERBOOK_DEPTH):
        price, size = packet.get(f'ask_{i}_price', 0.0), packet.get(f'ask_{i}_size', 0.0)
        if price > 0 and size > 0:
            asks.append(price)

    _check(len(bids) > 0 or len(asks) > 0, f"P2 packet for {symbol!r} carried no levels")
    _check(bids == sorted(bids, reverse=True), f"bids not descending: {bids}")
    _check(asks == sorted(asks), f"asks not ascending: {asks}")
    if bids and asks:
        _check(bids[0] < asks[0], f"crossed book bid={bids[0]} ask={asks[0]}")

    book_ts = packet.get('book_timestamp', 0)
    _check(book_ts > 0, "P2 packet missing/zero book_timestamp")
    latency_ms = time.time() * 1000.0 - book_ts
    _check(
        -2000.0 < latency_ms < 5000.0,
        f"implausible book latency {latency_ms:.1f}ms (clock skew or unit mismatch?)"
    )
    return latency_ms


def _gauntlet_market_data_stream(client: 'LighterArgusClient', timeout: float) -> Tuple[float, str]:
    perps, _ = client.get_markets(offset=0, limit=10, timeout=int(timeout))
    _check(len(perps) > 0, "need at least one market to stream")
    symbol = perps[0]['market']['symbol']

    window = max(2.0, min(timeout, 5.0))
    start = time.perf_counter()

    result, _ = client.subscribe([symbol], timeout=int(timeout))
    _check(symbol in (result.get('subscribed') or []), f"subscribe did not confirm {symbol!r}: {result!r}")
    try:
        first = _drain_p2(client, window, stop_on_packet=True)
        _check(len(first) > 0, f"no P2 packets for {symbol!r} within {window:.0f}s of subscribing")
        latencies = [_validate_p2_book(packet, symbol) for packet in first]

        # Unsubscribe: this socket must stop receiving the symbol's book.
        client.unsubscribe([symbol], timeout=int(timeout))
        _drain_p2(client, 0.5, stop_on_packet=False)  # flush in-flight pushes
        quiet = _drain_p2(client, 1.0, stop_on_packet=False)
        _check(not quiet, f"received {len(quiet)} P2 packet(s) after unsubscribing from {symbol!r}")

        # Re-subscribe: pushes must resume.
        client.subscribe([symbol], timeout=int(timeout))
        resumed = _drain_p2(client, window, stop_on_packet=True)
        _check(len(resumed) > 0, f"no P2 packets for {symbol!r} after re-subscribing")
    finally:
        try:
            client.unsubscribe([symbol], timeout=int(timeout))
        except Exception:
            pass

    dt = time.perf_counter() - start
    avg = sum(latencies) / len(latencies)
    return dt, f"symbol={symbol} first_batch={len(first)} avg_latency={avg:.1f}ms (subscribe/unsubscribe/re-subscribe OK)"


# (display name, check function) -- add new read-only actions here as the
# dispatcher's routing table grows. Trading actions should never be added.
# -----------------------------------------------------------------------------
# Account gauntlet checks
# -----------------------------------------------------------------------------
#
# These validate the homogenous account records (argus/perpetuals/shared/account.py):
# common fields present + numeric, and the venue payload nested under "venue". They
# SKIP (rather than fail) when the dispatcher reports AccountNotConfiguredError, so
# the gauntlet stays useful on a market-data-only deployment.

_ACCOUNT_UNCONFIGURED_MARKERS = ("no account configured", "LIGHTER_ACCOUNT_INDEX", "LIGHTER_AUTH_TOKEN")


def _account_call(fn):
    try:
        return fn()
    except Exception as e:
        if any(marker in str(e) for marker in _ACCOUNT_UNCONFIGURED_MARKERS):
            raise GauntletSkip(str(e).split(': ', 1)[-1]) from e
        raise


def _validate_common(record: dict, fields: Tuple[str, ...], numeric: Tuple[str, ...], context: str) -> None:
    _check(isinstance(record, dict), f"{context}: record is not an object")
    for field in fields:
        _check(field in record, f"{context}: missing '{field}'")
    for field in numeric:
        if record.get(field) is not None:
            _check_numeric(record[field], field, context)
    _check(isinstance(record.get('venue'), dict), f"{context}: 'venue' payload missing")


def _gauntlet_get_balance(client: 'LighterArgusClient', timeout: float) -> Tuple[float, str]:
    data, dt = _account_call(lambda: client.get_balance(timeout=int(timeout)))
    _validate_common(data, ('account', 'account_value', 'available_balance', 'total_margin_used', 'total_position_notional'),
                     ('account_value', 'available_balance', 'total_margin_used', 'total_position_notional'), 'balance')
    return dt, f"account={data['account']} value={data['account_value']} available={data['available_balance']}"


def _gauntlet_get_positions(client: 'LighterArgusClient', timeout: float) -> Tuple[float, str]:
    positions, dt = _account_call(lambda: client.get_positions(timeout=int(timeout)))
    _check(isinstance(positions, list), "'positions' is not a list")
    for i, pos in enumerate(positions):
        _validate_common(pos, ('name', 'signed_size', 'side', 'notional', 'unrealized_pnl'),
                         ('signed_size', 'notional', 'unrealized_pnl', 'entry_price', 'liquidation_price'), f"positions[{i}]")
        _check(pos['side'] in ('long', 'short'), f"positions[{i}]: bad side {pos['side']!r}")
    return dt, f"{len(positions)} open position(s)" + (f", first={positions[0]['name']} {positions[0]['signed_size']}" if positions else "")


def _gauntlet_get_orders(client: 'LighterArgusClient', timeout: float) -> Tuple[float, str]:
    orders, dt = _account_call(lambda: client.get_orders(timeout=int(timeout)))
    _check(isinstance(orders, list), "'orders' is not a list")
    for i, order in enumerate(orders):
        _validate_common(order, ('order_id', 'name', 'side', 'price', 'original_size', 'remaining_size', 'order_type', 'status', 'timestamp_ms'),
                         ('price', 'original_size', 'remaining_size'), f"orders[{i}]")
        _check(isinstance(order['order_id'], str), f"orders[{i}]: order_id must be a string")
    return dt, f"{len(orders)} resting order(s)"


def _gauntlet_get_order_status(client: 'LighterArgusClient', timeout: float) -> Tuple[float, str]:
    orders, _ = _account_call(lambda: client.get_orders(limit=1, timeout=int(timeout)))
    if orders:
        order_id = orders[0]['order_id']
        data, dt = client.get_order_status(order_id, timeout=int(timeout))
        _check(data.get('found') is True, f"resting order {order_id} not found via get_order_status")
        _check(data['order']['order_id'] == order_id, "returned order_id does not match")
        return dt, f"order_id={order_id} status={data['order']['status']}"
    data, dt = client.get_order_status("0", timeout=int(timeout))
    _check(data.get('found') is False and data.get('order') is None, f"unknown order should be found=False, got {data!r}")
    return dt, "no resting orders; unknown id correctly reports found=False"


def _gauntlet_get_trades(client: 'LighterArgusClient', timeout: float) -> Tuple[float, str]:
    trades, dt = _account_call(lambda: client.get_trades(limit=5, timeout=int(timeout)))
    _check(isinstance(trades, list), "'trades' is not a list")
    _check(len(trades) <= 5, "limit=5 not honoured")
    for i, trade in enumerate(trades):
        _validate_common(trade, ('trade_id', 'order_id', 'name', 'side', 'price', 'size', 'fee', 'is_maker', 'timestamp_ms'),
                         ('price', 'size', 'fee', 'realized_pnl'), f"trades[{i}]")
    return dt, f"{len(trades)} recent fill(s)" + (f", latest={trades[0]['name']} {trades[0]['side']} {trades[0]['size']}@{trades[0]['price']}" if trades else "")


def _gauntlet_get_funding_payments(client: 'LighterArgusClient', timeout: float) -> Tuple[float, str]:
    data, dt = _account_call(lambda: client.get_funding_payments(limit=5, timeout=int(timeout)))
    _check(isinstance(data, dict), "response is not an object")
    for field in ('account', 'start_time', 'end_time', 'funding_payments'):
        _check(field in data, f"missing '{field}'")
    payments = data['funding_payments']
    _check(isinstance(payments, list) and len(payments) <= 5, "'funding_payments' malformed or limit not honoured")
    for i, pay in enumerate(payments):
        _validate_common(pay, ('name', 'timestamp_ms', 'rate', 'position_size', 'payment'),
                         ('rate', 'position_size', 'payment'), f"funding_payments[{i}]")
    return dt, f"{len(payments)} settlement(s) in the default 7-day window"


GAUNTLET_CHECKS: List[Tuple[str, Callable[['LighterArgusClient', float], Tuple[float, str]]]] = [
    ("products_version", _gauntlet_products_version),
    ("get_markets", _gauntlet_get_markets),
    ("get_funding_rates_for_all_perpetuals", _gauntlet_get_funding_rates),
    ("market_info", _gauntlet_market_info),
    ("get_funding_history", _gauntlet_get_funding_history),
    ("search_perpetuals", _gauntlet_search_perpetuals),
    ("get_funding_rate", _gauntlet_get_funding_rate),
    ("get_balance", _gauntlet_get_balance),
    ("get_positions", _gauntlet_get_positions),
    ("get_orders", _gauntlet_get_orders),
    ("get_order_status", _gauntlet_get_order_status),
    ("get_trades", _gauntlet_get_trades),
    ("get_funding_payments", _gauntlet_get_funding_payments),
    ("market_data (subscribe/P2 lifecycle)", _gauntlet_market_data_stream),
]


def run_gauntlet(client: 'LighterArgusClient', timeout: float = 15.0) -> bool:
    """Calls every known read-only action against a live dispatcher and validates the shape of each response."""
    print("\n" + "=" * 72)
    print("LIGHTER DISPATCHER GAUNTLET (read-only actions, live endpoint)")
    print(f"  per-check timeout: {timeout:.0f}s")
    print("=" * 72)

    results: List[Tuple[str, str, float, str]] = []
    for name, check in GAUNTLET_CHECKS:
        start = time.perf_counter()
        try:
            dt, detail = check(client, timeout)
            status, message = "PASS", detail
        except GauntletFailure as e:
            status, message, dt = "FAIL", str(e), time.perf_counter() - start
        except GauntletSkip as e:
            status, message, dt = "SKIP", str(e), time.perf_counter() - start
        except socket.timeout:
            status, message, dt = "FAIL", f"timed out after {timeout:.0f}s", time.perf_counter() - start
        except Exception as e:
            status, message, dt = "ERROR", f"{type(e).__name__}: {e}", time.perf_counter() - start

        results.append((name, status, dt, message))
        icon = {"PASS": "✓", "FAIL": "✗", "ERROR": "‼", "SKIP": "-"}[status]
        print(f"  {icon} {name:<40} {status:<6} {dt*1000:>8.1f}ms  {message}")

    passed = sum(1 for _, status, _, _ in results if status == "PASS")
    skipped = sum(1 for _, status, _, _ in results if status == "SKIP")
    total = len(results)
    print("=" * 72)
    print(f"  {passed}/{total} checks passed" + (f" ({skipped} skipped)" if skipped else ""))
    print("=" * 72 + "\n")
    return passed + skipped == total


# =============================================================================
# Formatting helpers
# =============================================================================

def format_version(data: dict) -> str:
    output = []
    output.append("\n" + "=" * 60)
    output.append("PRODUCTS VERSION")
    output.append("=" * 60)
    output.append(f"  Argus core:         {data.get('argus')}")
    output.append(f"  Lighter dispatcher: {data.get('lighter_dispatcher')}")
    sidecars = data.get('sidecars') or {}
    if sidecars:
        output.append("  Sidecars:")
        for name, version in sidecars.items():
            output.append(f"    {name}: {version}")
    else:
        output.append("  Sidecars: (none)")
    output.append("=" * 60)
    return "\n".join(output)


def _perp_fields(perp: dict) -> Dict[str, Any]:
    market = perp.get('market') or {}
    ctx = perp.get('context') or {}

    def _f(v, default=0.0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    mark_price = _f(ctx.get('mark_price'))
    open_interest = _f(ctx.get('open_interest'))
    funding_rate = _f(perp.get('funding_rate')) if perp.get('funding_rate') is not None else None
    return {
        'symbol': market.get('symbol', 'Unknown'),
        'market_id': market.get('market_id'),
        'mark_price': mark_price,
        'funding_hourly': funding_rate,
        'funding_apr_pct': (funding_rate * 24 * 365 * 100) if funding_rate is not None else None,
        'open_interest': open_interest,
        'open_interest_usd': open_interest * mark_price,
        'day_volume': _f(ctx.get('daily_quote_token_volume')),
    }


def format_market_info(info: dict) -> str:
    output = []
    f = _perp_fields(info)
    output.append("\n" + "=" * 60)
    output.append(f"MARKET INFO: {f['symbol']}")
    output.append("=" * 60)
    output.append(f"  Market ID:      {f['market_id']}")
    output.append(f"  Mark price:     {f['mark_price']}")
    output.append(f"  Open interest:  {f['open_interest']} ({f['open_interest_usd']:,.0f} USD)")
    if f['funding_hourly'] is not None:
        output.append(f"  Funding/hr:     {f['funding_hourly']*100:.4f}%  (APR {f['funding_apr_pct']:.2f}%)")
    else:
        output.append("  Funding:        (none)")
    output.append("=" * 60)
    return "\n".join(output)


def format_search_results(symbols: List[str]) -> str:
    output = ["\n" + "=" * 60, "SEARCH RESULTS", "=" * 60]
    if not symbols:
        output.append("  (no matches)")
    for i, symbol in enumerate(symbols):
        output.append(f"  {i + 1:>2}. {symbol}")
    output.append("=" * 60)
    return "\n".join(output)


def format_funding_rate(data: dict) -> str:
    output = ["\n" + "=" * 60, f"FUNDING RATE: {data.get('symbol')}", "=" * 60]
    output.append(f"  Hourly:       {data.get('funding_rate')}")
    output.append(f"  Annualized:   {data.get('funding_rate_apr')}")
    output.append("=" * 60)
    return "\n".join(output)


def format_perpetuals(perps: List[dict], limit: Optional[int] = None) -> str:
    output = []
    output.append(f"\n{'SYMBOL':<12} {'MARK PX':<14} {'FUNDING/HR':<12} {'FUNDING APR':<14} {'OI (USD)':<16} {'24H VOL':<16}")
    output.append("=" * 100)
    for perp in (perps[:limit] if limit is not None else perps):
        f = _perp_fields(perp)
        funding_hourly = f"{f['funding_hourly']*100:.4f}%" if f['funding_hourly'] is not None else "n/a"
        funding_apr = f"{f['funding_apr_pct']:.2f}%" if f['funding_apr_pct'] is not None else "n/a"
        output.append(
            f"{f['symbol']:<12} {f['mark_price']:<14.4f} {funding_hourly:<12} "
            f"{funding_apr:<14} {f['open_interest_usd']:<16,.0f} {f['day_volume']:<16,.0f}"
        )
    shown = len(perps) if limit is None else min(limit, len(perps))
    output.append(f"\nShowing {shown} of {len(perps)} market(s)")
    return "\n".join(output)


def _ts(ms: Any) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000).strftime('%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError, OSError):
        return str(ms)


def format_balance(data: dict) -> str:
    return "\n".join([
        "\n" + "=" * 60,
        f"BALANCE ({VENUE_NAME} account {data.get('account')})",
        "=" * 60,
        f"  Account value:      {data.get('account_value')}",
        f"  Available:          {data.get('available_balance')}",
        f"  Margin used:        {data.get('total_margin_used')}",
        f"  Position notional:  {data.get('total_position_notional')}",
        "=" * 60,
    ])


def format_positions(positions: List[dict]) -> str:
    output = [f"\n{'NAME':<12} {'SIDE':<6} {'SIZE':<16} {'ENTRY':<14} {'NOTIONAL':<14} {'UPNL':<14} {'LIQ PX':<14} {'LEV':<6}", "=" * 100]
    if not positions:
        output.append("  (no open positions)")
    for p in positions:
        output.append(f"{p.get('name', ''):<12} {p.get('side', ''):<6} {p.get('signed_size', ''):<16} {str(p.get('entry_price')):<14} "
                      f"{p.get('notional', ''):<14} {p.get('unrealized_pnl', ''):<14} {str(p.get('liquidation_price')):<14} {str(p.get('leverage')):<6}")
    output.append("=" * 100)
    return "\n".join(output)


def format_orders(orders: List[dict]) -> str:
    output = [f"\n{'ORDER ID':<20} {'NAME':<12} {'SIDE':<5} {'PRICE':<14} {'REMAINING/ORIG':<22} {'TYPE':<12} {'STATUS':<10} {'PLACED':<20}", "=" * 120]
    if not orders:
        output.append("  (no orders)")
    for o in orders:
        output.append(f"{o.get('order_id', ''):<20} {o.get('name', ''):<12} {o.get('side', ''):<5} {o.get('price', ''):<14} "
                      f"{o.get('remaining_size', '')}/{o.get('original_size', ''):<12} {o.get('order_type', ''):<12} {o.get('status', ''):<10} {_ts(o.get('timestamp_ms')):<20}")
    output.append("=" * 120)
    return "\n".join(output)


def format_order_status(data: dict) -> str:
    if not data.get('found'):
        return "\n  Order not found."
    return format_orders([data['order']])


def format_trades(trades: List[dict]) -> str:
    output = [f"\n{'TIME':<20} {'NAME':<12} {'SIDE':<5} {'SIZE':<14} {'PRICE':<14} {'FEE':<12} {'MAKER':<6} {'PNL':<12} {'ORDER ID':<20}", "=" * 120]
    if not trades:
        output.append("  (no fills)")
    for t in trades:
        output.append(f"{_ts(t.get('timestamp_ms')):<20} {t.get('name', ''):<12} {t.get('side', ''):<5} {t.get('size', ''):<14} {t.get('price', ''):<14} "
                      f"{t.get('fee', ''):<12} {str(t.get('is_maker')):<6} {str(t.get('realized_pnl')):<12} {t.get('order_id', ''):<20}")
    output.append("=" * 120)
    return "\n".join(output)


def format_funding_payments(data: dict) -> str:
    payments = data.get('funding_payments') or []
    output = [
        f"\nFunding payments {_ts(data.get('start_time'))} -> {_ts(data.get('end_time'))} (account {data.get('account')})",
        f"{'TIME':<20} {'NAME':<12} {'RATE':<14} {'POSITION':<16} {'PAYMENT':<14}",
        "=" * 80,
    ]
    if not payments:
        output.append("  (no settlements in window)")
    total = 0.0
    for p in payments:
        output.append(f"{_ts(p.get('timestamp_ms')):<20} {p.get('name', ''):<12} {p.get('rate', ''):<14} {p.get('position_size', ''):<16} {p.get('payment', ''):<14}")
        try:
            total += float(p.get('payment') or 0)
        except (TypeError, ValueError):
            pass
    output.append("=" * 80)
    output.append(f"  Net over shown page: {total:+.6f}")
    return "\n".join(output)


def format_system_push(push: Dict[str, Any]) -> str:
    """
    Format one P1 system push (e.g. a funding-rate update sent by
    _distribute_refreshed_perpetuals) for display in sub mode.
    """
    action = push.get('action', 'unknown')
    data = push.get('data')
    if isinstance(data, dict) and 'funding_rate' in data:
        subject = data.get('coin') or data.get('symbol') or '?'
        return f"[push] {action}: {subject} funding_rate={data.get('funding_rate')}"
    return f"[push] {action}: {data}"


# =============================================================================
# Live market-data streaming (P2)
# =============================================================================

def subscribe_market_data_mode(client: LighterArgusClient, symbol: str):
    """
    Subscribe to a symbol's order book and display live updates + latency, along
    with any P1 system pushes the dispatcher emits (e.g. updated funding rates).
    Press Ctrl+C to stop, unsubscribe, and show aggregate statistics.
    Mirrors tests/hyper_cli.py's subscribe_market_data_mode.
    """
    latencies: List[float] = []
    packet_count = 0
    push_count = 0
    start_time = time.time()

    print(f"\n📡 Subscribing to order book: {symbol}")
    try:
        result, rtt = client.subscribe([symbol])
        print(f"✓ Subscribed successfully (RTT: {rtt*1000:.1f}ms)")
        print(f"  Subscribed: {result.get('subscribed', [])}")
        print(f"  Failed: {result.get('failed', [])}")
    except Exception as e:
        print(f"✗ Subscription failed: {e}")
        return

    print(f"\n📊 Monitoring order book... Press Ctrl+C to stop and view statistics.")
    print(f"   Orderbook depth: {ORDERBOOK_DEPTH} levels")
    print(f"   System pushes (e.g. funding rate updates) print as [push] lines")
    print(f"\n{'PACKET':<8} {'LATENCY(ms)':<14} {'BEST BID':<14} {'BEST ASK':<14}")
    print("-" * 60)

    try:
        while True:
            packets, pushes = client.receive_packets(timeout=0.1)

            for push in pushes:
                push_count += 1
                print(format_system_push(push))

            for packet_data in packets:
                packet_count += 1
                receive_time = time.time()

                book_ts = packet_data.get('book_timestamp', 0)
                if book_ts > 0:
                    latency_ms = (receive_time * 1000) - book_ts
                    latencies.append(latency_ms)
                else:
                    latency_ms = 0.0

                best_bid = packet_data.get('bid_0_price', 0)
                best_ask = packet_data.get('ask_0_price', 0)

                print(f"{packet_count:<8} {latency_ms:<14.2f} {best_bid:<14.4f} {best_ask:<14.4f}")

                if packet_count % 10 == 0:
                    print(format_orderbook(packet_data, depth=3))
                    print(f"\n{'PACKET':<8} {'LATENCY(ms)':<14} {'BEST BID':<14} {'BEST ASK':<14}")
                    print("-" * 60)

    except KeyboardInterrupt:
        print(f"\n\n🛑 Stopped by user.")

        try:
            print(f"📡 Unsubscribing from: {symbol}")
            client.unsubscribe([symbol])
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
        if latencies:
            print(f"  Avg latency: {sum(latencies)/len(latencies):.2f}ms")
            print(f"  Min/Max latency: {min(latencies):.2f}ms / {max(latencies):.2f}ms")


# =============================================================================
# Interactive CLI
# =============================================================================

def print_banner(host: str, port: int):
    print("\n" + "=" * 50)
    print("  Argus Lighter Interactive CLI")
    print(f"  Connected to {host}:{port}")
    print("  Type 'help' for commands, 'quit' to exit")
    print("=" * 50 + "\n")


def print_help():
    print("\nAvailable commands:")
    print("  version                    - Show dispatcher/component version info")
    print("  markets [offset] [limit]   - List all perpetual markets (single unified venue, no dex param)")
    print("  funding [offset] [limit]   - Show markets by funding rate, highest first (default limit: 20)")
    print("  info <symbol>              - Show market metadata + live data for one symbol")
    print("  history <market_id> [days] - Show funding history for a market over the last N days (default: 1)")
    print("  search <keyword>           - Fuzzy-search market symbols (e.g. search BTC)")
    print("  rate <symbol>              - Show the live hourly + annualized funding rate for one symbol")
    print("  sub <symbol>               - Subscribe to live order book + system pushes (e.g. funding rate updates), Ctrl+C to stop")
    print("  balance                    - Account equity/margin")
    print("  positions [offset] [limit] - Open positions")
    print("  orders [offset] [limit]    - Resting orders, newest first")
    print("  order <order_id>           - Look up one order by venue id (any state)")
    print("  trades [offset] [limit]    - Recent fills, newest first")
    print("  fundingpay [days] [limit]  - Funding settlements over the last N days (default: 7)")
    print("  test | gauntlet            - Call every known read-only action and validate the responses")
    print("  clear                      - Clear screen")
    print("  help                       - Show this help")
    print("  quit                       - Exit the program")
    print("\nExamples:")
    print("  markets                    # all markets")
    print("  markets 0 20               # first 20 markets")
    print("  funding 0 10               # top 10 markets by hourly funding rate")
    print("  funding 10 10              # next 10 markets by hourly funding rate")
    print("  info BTC                   # info for the BTC market")
    print("  history 0 7                # last 7 days of funding history for market_id 0")
    print("  search BTC                 # fuzzy-search market symbols for 'BTC'")
    print("  rate BTC                   # live funding rate for BTC")
    print("  sub BTC                    # stream BTC's live order book")
    print()


def interactive_loop(client: LighterArgusClient):
    while True:
        try:
            query = input("lighter> ").strip()

            if not query:
                continue

            if query.lower() in ['quit', 'exit', 'q']:
                print("Goodbye!")
                break
            elif query.lower() in ['help', 'h', '?']:
                print_help()
            elif query.lower() == 'clear':
                import os
                os.system('clear' if os.name == 'posix' else 'cls')
                print_banner(client.host, client.port)
            elif query.lower().split()[0] in ('balance', 'positions', 'orders', 'order', 'trades', 'fundingpay'):
                cmd, *parts = query.split()
                cmd = cmd.lower()
                nums = [int(p) for p in parts if p.isdigit()]
                offset = nums[0] if len(nums) > 0 else 0
                limit = nums[1] if len(nums) > 1 else None
                try:
                    if cmd == 'balance':
                        data, dt = client.get_balance()
                        print(f"✓ Fetched in {dt*1000:.1f}ms")
                        print(format_balance(data))
                    elif cmd == 'positions':
                        positions, dt = client.get_positions(offset=offset, limit=limit)
                        print(f"✓ Fetched in {dt*1000:.1f}ms")
                        print(format_positions(positions))
                    elif cmd == 'orders':
                        orders, dt = client.get_orders(offset=offset, limit=limit)
                        print(f"✓ Fetched in {dt*1000:.1f}ms")
                        print(format_orders(orders))
                    elif cmd == 'order':
                        if not parts:
                            print("Usage: order <order_id>")
                        else:
                            data, dt = client.get_order_status(parts[0])
                            print(f"✓ Fetched in {dt*1000:.1f}ms")
                            print(format_order_status(data))
                    elif cmd == 'trades':
                        trades, dt = client.get_trades(offset=offset, limit=limit)
                        print(f"✓ Fetched in {dt*1000:.1f}ms")
                        print(format_trades(trades))
                    elif cmd == 'fundingpay':
                        days = nums[0] if len(nums) > 0 else 7
                        page_limit = nums[1] if len(nums) > 1 else None
                        now_ms = int(time.time() * 1000)
                        data, dt = client.get_funding_payments(start_time=now_ms - days * 86_400_000, end_time=now_ms, limit=page_limit)
                        print(f"✓ Fetched in {dt*1000:.1f}ms")
                        print(format_funding_payments(data))
                except Exception as e:
                    print(f"✗ {cmd} failed: {e}")
            elif query.lower() == 'version':
                try:
                    data, dt = client.products_version()
                    print(format_version(data))
                    print(f"  ({dt*1000:.1f}ms)")
                except Exception as e:
                    print(f"✗ Failed to fetch version: {e}")
            elif query.lower().startswith('markets'):
                parts = query.split()[1:]
                offset = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 0
                limit = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
                try:
                    print(f"Fetching markets (offset={offset}, limit={'server default' if limit is None else limit})...")
                    perps, dt = client.get_markets(offset=offset, limit=limit)
                    print(f"✓ Fetched in {dt*1000:.1f}ms")
                    print(format_perpetuals(perps, limit=limit))
                except Exception as e:
                    print(f"✗ Failed to fetch markets: {e}")
            elif query.lower().startswith('funding'):
                parts = query.split()[1:]
                offset = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 0
                limit = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 20
                try:
                    print(f"Fetching markets by funding rate (offset={offset}, limit={limit})...")
                    perps, dt = client.get_funding_rates_for_all_perpetuals(offset=offset, limit=limit)
                    print(f"✓ Fetched in {dt*1000:.1f}ms")
                    print(format_perpetuals(perps, limit=limit))
                except Exception as e:
                    print(f"✗ Failed to fetch funding rates: {e}")
            elif query.lower().startswith('info'):
                parts = query.split()[1:]
                if not parts:
                    print("Usage: info <symbol>")
                else:
                    symbol = parts[0].strip()
                    try:
                        print(f"Fetching info for '{symbol}'...")
                        info, dt = client.market_info(symbol=symbol)
                        print(f"✓ Fetched in {dt*1000:.1f}ms")
                        if info is None:
                            print(f"No market found for symbol '{symbol}'")
                        else:
                            print(format_market_info(info))
                    except Exception as e:
                        print(f"✗ Failed to fetch market info: {e}")
            elif query.lower().startswith('history'):
                parts = query.split()[1:]
                if not parts:
                    print("Usage: history <market_id> [days]")
                else:
                    try:
                        market_id = int(parts[0])
                        days = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
                        start_ts = int(time.time()) - days * 24 * 60 * 60
                        print(f"Fetching funding history for market_id={market_id} (last {days} day(s))...")
                        history, dt = client.get_funding_history(market_id=market_id, start_timestamp=start_ts)
                        print(f"✓ Fetched in {dt*1000:.1f}ms -- {len(history)} entry(ies)")
                        for entry in history[-20:]:
                            print(f"  {entry.get('timestamp')}  rate={entry.get('rate')}  direction={entry.get('direction')}")
                    except Exception as e:
                        print(f"✗ Failed to fetch funding history: {e}")
            elif query.lower().startswith('search'):
                parts = query.split()[1:]
                if not parts:
                    print("Usage: search <keyword>")
                else:
                    keyword = " ".join(parts)
                    try:
                        print(f"Searching markets for '{keyword}'...")
                        symbols, dt = client.search_perpetuals(keyword)
                        print(f"✓ Searched in {dt*1000:.1f}ms")
                        print(format_search_results(symbols))
                    except Exception as e:
                        print(f"✗ Search failed: {e}")
            elif query.lower().startswith('rate'):
                parts = query.split()[1:]
                if not parts:
                    print("Usage: rate <symbol>")
                else:
                    symbol = parts[0].strip()
                    try:
                        print(f"Fetching funding rate for '{symbol}'...")
                        data, dt = client.get_funding_rate(symbol)
                        print(f"✓ Fetched in {dt*1000:.1f}ms")
                        print(format_funding_rate(data))
                    except Exception as e:
                        print(f"✗ Failed to fetch funding rate: {e}")
            elif query.lower().startswith('sub '):
                symbol = query[4:].strip()
                if not symbol:
                    print("✗ Please provide a symbol. Usage: sub <symbol>")
                else:
                    subscribe_market_data_mode(client, symbol)
            elif query.lower() in ('test', 'gauntlet'):
                run_gauntlet(client)
            else:
                print(f"Unknown command: '{query}'. Type 'help' for a list of commands.")

        except KeyboardInterrupt:
            print("\nType 'quit' to exit.")
        except EOFError:
            print("\nGoodbye!")
            break


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Argus Lighter Interactive CLI Client')
    parser.add_argument('--host', default='localhost', help='Argus server host (default: localhost)')
    parser.add_argument('--port', type=int, default=9974, help='Argus server port (default: 9974)')
    parser.add_argument('--test', action='store_true', help='Run the read-only gauntlet against a live dispatcher and exit (no interactive prompt)')
    parser.add_argument('--test-timeout', type=float, default=15.0, help='Per-check timeout in seconds for --test (default: 15)')

    args = parser.parse_args()

    client = LighterArgusClient(args.host, args.port)

    try:
        print(f"Connecting to Argus Lighter dispatcher at {args.host}:{args.port}...")
        client.connect()

        # Test connection (no ping action on this dispatcher; use products_version instead).
        version, rtt = client.products_version()
        print(f"✓ Connected (lighter_dispatcher: {version.get('lighter_dispatcher')}, {rtt*1000:.1f}ms)")

        if args.test:
            ok = run_gauntlet(client, timeout=args.test_timeout)
            sys.exit(0 if ok else 1)

        print_banner(args.host, args.port)
        interactive_loop(client)

    except ConnectionError as e:
        print(f"Connection error: {e}")
        print("Make sure the Argus Lighter dispatcher is running.")
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
