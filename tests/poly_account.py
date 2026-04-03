#!/usr/bin/env python3
"""
Poly Account - Position Monitor with Live Prices and PnL for Polymarket

Connects to the Argus dispatcher, fetches all open positions (derived from trades),
subscribes to the relevant asset IDs, then displays a live table showing:
  - Asset ID, Outcome, Side, Position Size
  - Best Bid / Best Ask / Mid Price
  - Average Entry Price
  - Unrealized PnL (in USDC)

Every 30 seconds it re-polls for positions to detect new opens or closed positions,
updating the subscription list and the table accordingly.

Usage:
    python poly_account.py                    # Connect to default localhost:9972
    python poly_account.py --host <host> --port <port>

Press Ctrl+C to exit gracefully.
"""

import sys
import json
import time
import socket
import signal
import argparse
import os
from datetime import datetime
from typing import List, Tuple, Optional, Dict, Any
from collections import defaultdict


COLORS = {
    'green': '\033[92m',
    'red': '\033[91m',
    'yellow': '\033[93m',
    'cyan': '\033[96m',
    'magenta': '\033[95m',
    'dim': '\033[2m',
    'bold': '\033[1m',
    'reset': '\033[0m',
}


def color(text: str, color_name: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"{COLORS.get(color_name, '')}{text}{COLORS['reset']}"


def strip_ansi(text: str) -> str:
    import re
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)


def visible_len(text: str) -> int:
    return len(strip_ansi(text))


def pad_to_width(text: str, width: int) -> str:
    visible = visible_len(text)
    if visible < width:
        return text + ' ' * (width - visible)
    elif visible > width:
        stripped = strip_ansi(text)
        return stripped[:width-3] + '...'
    return text


def format_timestamp(ts) -> str:
    try:
        ts_float = float(ts)
        if ts_float > 1e12:
            ts_float /= 1000
        dt = datetime.fromtimestamp(ts_float)
        return dt.strftime('%H:%M:%S')
    except (ValueError, TypeError):
        return str(ts)


def abbreviate(s: str, max_len: int = 16) -> str:
    if len(s) <= max_len:
        return s
    return s[:max_len//2 - 2] + '...' + s[-max_len//2 + 2:]


def encode_packet(data: bytes) -> bytes:
    data_length = len(data)
    return f"~{data_length:04d}|".encode('ascii') + data


class ArgusClient:
    def __init__(self, host: str = 'localhost', port: int = 9972):
        self.host = host
        self.port = port
        self.socket: Optional[socket.socket] = None
        self._subscribed_assets: List[str] = []

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

    def send_request(self, action: str, data=None, timeout: int = 30) -> Tuple[dict, float]:
        if data is None:
            data = {}
        request = {'action': action, 'data': data}
        packet = encode_packet(json.dumps(request).encode('utf-8'))

        if not self.socket:
            raise ConnectionError("Not connected to server")

        old_timeout = self.socket.gettimeout()
        self.socket.settimeout(timeout)
        try:
            t0 = time.perf_counter()
            self.socket.sendall(packet)

            raw = b''
            header_len = None
            needed = None

            while True:
                chunk = self.socket.recv(131072)
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
            return response, elapsed
        finally:
            self.socket.settimeout(old_timeout)

    def ping(self) -> Tuple[str, float]:
        resp, dt = self.send_request('ping')
        if resp.get('error'):
            raise Exception(f"Ping failed: {resp['error']}")
        return str(resp.get('data', '')), dt

    def subscribe(self, clob_ids: List[str]) -> Tuple[dict, float]:
        resp, dt = self.send_request('subscribe', clob_ids)
        if resp.get('error'):
            raise Exception(f"Subscribe failed: {resp['error']}")
        self._subscribed_assets.extend(clob_ids)
        return resp.get('data', {}), dt

    def unsubscribe(self, clob_ids: List[str]) -> Tuple[dict, float]:
        resp, dt = self.send_request('unsubscribe', clob_ids)
        if resp.get('error'):
            raise Exception(f"Unsubscribe failed: {resp['error']}")
        for clob_id in clob_ids:
            if clob_id in self._subscribed_assets:
                self._subscribed_assets.remove(clob_id)
        return resp.get('data', {}), dt

    def get_orders(self) -> Tuple[List[dict], float]:
        resp, dt = self.send_request('get_orders')
        if resp.get('error'):
            raise Exception(f"get_orders failed: {resp['error']}")
        return resp.get('data', []), dt

    def get_trades(self, limit: int = None, offset: int = None) -> Tuple[List[dict], float]:
        data = []
        if limit is not None and offset is not None:
            data = [limit, offset]
        elif limit is not None:
            data = [limit]
        resp, dt = self.send_request('get_trades', data)
        if resp.get('error'):
            raise Exception(f"get_trades failed: {resp['error']}")
        return resp.get('data', []), dt

    def get_all_trades(self) -> Tuple[List[dict], float]:
        """
        Fetch all trades using pagination to avoid packet size errors.
        Returns all trades and the total time taken.
        """
        all_trades = []
        offset = 0
        limit = 5  # Must match the default limit in _handle_get_trades
        total_dt = 0.0

        while True:
            trades, dt = self.get_trades(limit=limit, offset=offset)
            total_dt += dt
            if not trades:
                break
            all_trades.extend(trades)
            if len(trades) < limit:
                break  # Last page
            offset += limit

        return all_trades, total_dt

    def recv_raw(self, timeout: float = 1.0) -> bytes:
        if not self.socket:
            raise ConnectionError("Not connected to server")
        old = self.socket.gettimeout()
        self.socket.settimeout(timeout)
        try:
            return self.socket.recv(131072)
        except socket.timeout:
            return b''
        finally:
            self.socket.settimeout(old)


class Position:
    def __init__(self, asset_id: str, outcome: str, market: str):
        self.asset_id = asset_id
        self.outcome = outcome
        self.market = market
        self.buy_size: float = 0.0
        self.sell_size: float = 0.0
        self.buy_cost: float = 0.0
        self.sell_revenue: float = 0.0
        self.fees: float = 0.0
        self.best_bid: float = 0.0
        self.best_ask: float = 0.0
        self.last_update: float = 0.0

    @property
    def net_size(self) -> float:
        return self.buy_size - self.sell_size

    @property
    def side(self) -> str:
        if self.net_size > 0:
            return 'BUY'
        elif self.net_size < 0:
            return 'SELL'
        return 'FLAT'

    @property
    def avg_entry_price(self) -> float:
        if self.side == 'BUY' and self.buy_size > 0:
            return self.buy_cost / self.buy_size
        elif self.side == 'SELL' and self.sell_size > 0:
            return self.sell_revenue / self.sell_size
        return 0.0

    @property
    def mid_price(self) -> float:
        if self.best_bid > 0 and self.best_ask > 0:
            return (self.best_bid + self.best_ask) / 2.0
        return 0.0

    @property
    def unrealized_pnl(self) -> float:
        if self.net_size == 0 or self.mid_price == 0:
            return 0.0
        if self.side == 'BUY':
            return (self.mid_price - self.avg_entry_price) * self.net_size - self.fees
        else:
            return (self.avg_entry_price - self.mid_price) * abs(self.net_size) - self.fees


def compute_positions(trades: List[dict]) -> Dict[str, Position]:
    positions: Dict[str, Position] = {}
    for t in trades:
        asset_id = t.get('asset_id', '')
        if not asset_id:
            continue
        if asset_id not in positions:
            positions[asset_id] = Position(
                asset_id=asset_id,
                outcome=t.get('outcome', 'N/A'),
                market=t.get('market', 'N/A'),
            )
        pos = positions[asset_id]
        size = float(t.get('size', 0))
        price = float(t.get('price', 0))
        fee_bps = int(t.get('fee_rate_bps', 0))
        fee = size * price * (fee_bps / 10000.0)
        side = t.get('side', '')

        if side.upper() == 'BUY':
            pos.buy_size += size
            pos.buy_cost += size * price
        else:
            pos.sell_size += size
            pos.sell_revenue += size * price
        pos.fees += fee

    return {k: v for k, v in positions.items() if v.net_size != 0}


def parse_p2_price(packet_bytes: bytes) -> Optional[Tuple[str, float, float]]:
    try:
        if len(packet_bytes) < 11:
            return None
        pos = 0
        if packet_bytes[pos] != ord('~'):
            return None
        pos += 1
        try:
            packet_length = int(packet_bytes[pos:pos+4].decode('ascii'))
        except (ValueError, UnicodeDecodeError):
            return None
        pos += 4
        total_len = 5 + packet_length
        if len(packet_bytes) < total_len:
            return None

        try:
            sym_len = int(packet_bytes[pos:pos+4].decode('ascii'))
        except (ValueError, UnicodeDecodeError):
            return None
        pos += 4
        pipe_pos = pos + sym_len
        if pipe_pos >= len(packet_bytes) or packet_bytes[pipe_pos] != ord('|'):
            return None
        symbol = packet_bytes[pos:pos+sym_len].decode('ascii', errors='replace')
        pos = pipe_pos + 1

        end = total_len - 1
        if packet_bytes[end - 1:end] != b'L':
            return None
        csv_data = packet_bytes[pos:end-1].decode('ascii', errors='replace')

        fields = csv_data.split(',')
        if len(fields) < 2:
            return None

        best_bid = float(fields[0]) if fields[0] and fields[0] != '0' else 0.0
        best_ask = float(fields[1]) if fields[1] and fields[1] != '0' else 0.0

        parts = symbol.split('-')
        if len(parts) >= 3:
            asset_id = parts[-1]
        else:
            asset_id = symbol

        return (asset_id, best_bid, best_ask)
    except Exception:
        return None


def recv_and_parse(client: ArgusClient, positions: Dict[str, Position], timeout: float = 1.0) -> Dict[str, Tuple[float, float]]:
    raw = client.recv_raw(timeout)
    if not raw:
        return {}
    updates = {}
    position = 0
    while position < len(raw):
        if raw[position] != ord('~'):
            position += 1
            continue
        if position + 5 > len(raw):
            break
        try:
            pkt_len = int(raw[position+1:position+5].decode('ascii'))
        except (ValueError, UnicodeDecodeError):
            position += 1
            continue
        total = 5 + pkt_len
        if position + total > len(raw):
            break
        pkt = raw[position:position+total]

        result = parse_p2_price(pkt)
        if result:
            asset_id, bid, ask = result
            if asset_id in positions:
                updates[asset_id] = (bid, ask)

        position += total
    return updates


def render_table(positions: Dict[str, Position], term_width: int = 120) -> str:
    if not positions:
        return color('  No open positions.', 'dim')

    col_asset = 18
    col_outcome = 14
    col_side = 6
    col_size = 12
    col_bid = 10
    col_ask = 10
    col_mid = 10
    col_avg = 10
    col_pnl = 14

    lines = []
    header = ' ASSET'.ljust(col_asset)
    header += 'OUTCOME'.ljust(col_outcome)
    header += 'SIDE'.ljust(col_side)
    header += 'SIZE'.rjust(col_size)
    header += 'BID'.rjust(col_bid)
    header += 'ASK'.rjust(col_ask)
    header += 'MID'.rjust(col_mid)
    header += 'AVG ENTRY'.rjust(col_avg)
    header += 'UNREALIZED PNL'.rjust(col_pnl)
    lines.append(color('─' * min(len(header), term_width), 'border'))
    lines.append(header)
    lines.append(color('─' * min(len(header), term_width), 'border'))

    total_pnl = 0.0
    for asset_id, pos in sorted(positions.items(), key=lambda x: -abs(x[1].unrealized_pnl)):
        asset_str = abbreviate(asset_id, col_asset - 1)
        outcome_str = abbreviate(pos.outcome, col_outcome - 1)

        side_str = color(f"{'BUY':>4}", 'green') if pos.side == 'BUY' else color(f"{'SELL':>4}", 'red')
        size_str = f"{abs(pos.net_size):>10.2f}"
        bid_str = f"{pos.best_bid:>9.2f}" if pos.best_bid > 0 else f"{'N/A':>9}"
        ask_str = f"{pos.best_ask:>9.2f}" if pos.best_ask > 0 else f"{'N/A':>9}"
        mid_str = f"{pos.mid_price:>9.4f}" if pos.mid_price > 0 else f"{'N/A':>9}"
        avg_str = f"{pos.avg_entry_price:>9.4f}" if pos.avg_entry_price > 0 else f"{'N/A':>9}"

        pnl = pos.unrealized_pnl
        total_pnl += pnl
        if pnl > 0:
            pnl_str = color(f"+{pnl:>10.2f}", 'green')
        elif pnl < 0:
            pnl_str = color(f"{pnl:>11.2f}", 'red')
        else:
            pnl_str = f"{'0.00':>11}"

        line = asset_str.ljust(col_asset)
        line += outcome_str.ljust(col_outcome)
        line += side_str.ljust(col_side)
        line += size_str
        line += bid_str
        line += ask_str
        line += mid_str
        line += avg_str
        line += pnl_str
        lines.append(line)

    lines.append(color('─' * min(len(header), term_width), 'border'))
    if total_pnl > 0:
        total_str = color(f"  TOTAL UNREALIZED PNL: +{total_pnl:.2f} USDC", 'green')
    elif total_pnl < 0:
        total_str = color(f"  TOTAL UNREALIZED PNL: {total_pnl:.2f} USDC", 'red')
    else:
        total_str = "  TOTAL UNREALIZED PNL: 0.00 USDC"
    lines.append(total_str)
    return '\n'.join(lines)


def clear_screen():
    os.system('clear' if os.name != 'nt' else 'cls')


def main():
    parser = argparse.ArgumentParser(description='Position Monitor with Live Prices and PnL for Polymarket')
    parser.add_argument('--host', default='localhost', help='Dispatcher host (default: localhost)')
    parser.add_argument('--port', type=int, default=9972, help='Dispatcher port (default: 9972)')
    args = parser.parse_args()

    def signal_handler(sig, frame):
        print('\n')
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    client = ArgusClient(host=args.host, port=args.port)

    try:
        print(f"Connecting to {args.host}:{args.port}...")
        client.connect()
        pong, rtt = client.ping()
        print(f"Connected (ping: {rtt*1000:.1f}ms)\n")
    except ConnectionError as e:
        print(f"Connection failed: {e}", file=sys.stderr)
        sys.exit(1)

    positions: Dict[str, Position] = {}
    last_poll = 0.0
    poll_interval = 30.0
    iteration = 0

    try:
        while True:
            now = time.time()

            if now - last_poll >= poll_interval or not positions:
                try:
                    trades, _ = client.get_all_trades()

                    new_positions = compute_positions(trades)
                    new_asset_ids = set(new_positions.keys())
                    old_asset_ids = set(positions.keys())

                    to_unsub = old_asset_ids - new_asset_ids
                    to_sub = new_asset_ids - old_asset_ids

                    if to_unsub:
                        try:
                            client.unsubscribe(list(to_unsub))
                        except Exception:
                            pass

                    if to_sub:
                        try:
                            client.subscribe(list(to_sub))
                        except Exception as e:
                            print(f"Warning: subscribe failed for some assets: {e}")

                    positions = new_positions
                    last_poll = now

                    if to_unsub or to_sub:
                        print(f"[{format_timestamp(now)}] Positions updated: "
                              f"+{len(to_sub)} subscribed, -{len(to_unsub)} unsubscribed. "
                              f"Total: {len(positions)} open positions")

                except Exception as e:
                    print(f"[{format_timestamp(now)}] Error fetching trades: {e}")

            raw = client.recv_raw(timeout=0.5)
            if raw:
                updates = {}
                position = 0
                while position < len(raw):
                    if raw[position] != ord('~'):
                        position += 1
                        continue
                    if position + 5 > len(raw):
                        break
                    try:
                        pkt_len = int(raw[position+1:position+5].decode('ascii'))
                    except (ValueError, UnicodeDecodeError):
                        position += 1
                        continue
                    total = 5 + pkt_len
                    if position + total > len(raw):
                        break
                    pkt = raw[position:position+total]
                    result = parse_p2_price(pkt)
                    if result:
                        asset_id, bid, ask = result
                        if asset_id in positions:
                            updates[asset_id] = (bid, ask)
                    position += total

                for asset_id, (bid, ask) in updates.items():
                    if asset_id in positions:
                        if bid > 0:
                            positions[asset_id].best_bid = bid
                        if ask > 0:
                            positions[asset_id].best_ask = ask
                        positions[asset_id].last_update = now

            if iteration % 5 == 0:
                term_width = os.get_terminal_size().columns if sys.stdout.isatty() else 120
                clear_screen()
                print(color(f" POLY ACCOUNT - Position Monitor ({args.host}:{args.port}) ", 'header').center(term_width))
                print(f"  {len(positions)} open positions  |  Last poll: {format_timestamp(last_poll)}  |  "
                      f"Next poll in: {max(0, int(poll_interval - (now - last_poll)))}s")
                print()
                print(render_table(positions, term_width))

            iteration += 1

    except KeyboardInterrupt:
        print('\n')
    finally:
        if positions:
            try:
                client.unsubscribe(list(positions.keys()))
            except Exception:
                pass
        client.disconnect()


if __name__ == '__main__':
    main()
