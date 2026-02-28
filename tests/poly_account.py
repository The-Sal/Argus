#!/usr/bin/env python3
"""
Poly Account - Real-time Account Update Monitor for Polymarket

A dedicated TUI for monitoring Polymarket account lifecycle events (PLACEMENT, 
CANCELLATION, MATCH, TRADE, etc.) in real-time.

IMPORTANT: You must subscribe to at least one asset to receive account updates.
The dispatcher only sends account_update pushes to clients with active subscriptions.

Usage:
    python poly_account.py                    # Subscribe to default asset
    python poly_account.py <clob_id>          # Subscribe to specific CLOB ID
    python poly_account.py --host localhost --port 9972 <clob_id>

Press Ctrl+C to exit gracefully.
"""

import sys
import json
import time
import socket
import signal
import argparse
from datetime import datetime
from typing import List, Tuple, Optional

# =============================================================================
# Protocol Implementation
# =============================================================================

def encode_packet(data: bytes) -> bytes:
    """Encode a packet with length prefix. Format: ~NNNN|<data>"""
    data_length = len(data)
    return f"~{data_length:04d}|".encode('ascii') + data


def decode_multiple_packets(data: bytes) -> List[bytes]:
    """Decode multiple P1 packets from a byte stream."""
    packets = []
    position = 0
    
    while position < len(data):
        remaining_data = data[position:]
        
        if not remaining_data.startswith(b"~"):
            raise ValueError(f"Invalid packet format at position {position}")
        
        if len(remaining_data) < 6:
            raise ValueError(f"Invalid packet format at position {position}: packet too short")
        
        try:
            length_str = int(remaining_data[1:5].decode('ascii'))
        except (ValueError, UnicodeDecodeError):
            raise ValueError(f"Invalid data length format at position {position}")
        
        if remaining_data[5:6] != b"|":
            raise ValueError(f"Invalid packet format at position {position}: missing pipe separator")
        
        packet_end = 6 + length_str
        if packet_end > len(remaining_data):
            raise ValueError(f"Data length does not match specified length at position {position}")
        
        packet_data = remaining_data[6:packet_end]
        packets.append(packet_data)
        position += packet_end
    
    return packets


# =============================================================================
# Argus Client
# =============================================================================

class ArgusClient:
    """Simple client for connecting to Argus dispatcher."""
    
    def __init__(self, host: str = 'localhost', port: int = 9972):
        self.host = host
        self.port = port
        self.socket: Optional[socket.socket] = None
        self._subscribed_assets: List[str] = []
    
    def connect(self) -> None:
        """Connect to the Argus dispatcher."""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(30)
            self.socket.connect((self.host, self.port))
        except (ConnectionRefusedError, OSError) as e:
            raise ConnectionError(f"Could not connect to dispatcher at {self.host}:{self.port} - {e}")
    
    def disconnect(self) -> None:
        """Close the connection."""
        if self.socket:
            self.socket.close()
            self.socket = None
    
    def send_request(self, action: str, data=None, timeout: int = 30) -> Tuple[dict, float]:
        """Send a request and return the parsed JSON response + round-trip time."""
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
        """Ping the server."""
        resp, dt = self.send_request('ping')
        if resp.get('error'):
            raise Exception(f"Ping failed: {resp['error']}")
        return str(resp.get('data', '')), dt
    
    def subscribe(self, clob_ids: List[str]) -> Tuple[dict, float]:
        """Subscribe to one or more CLOB IDs."""
        resp, dt = self.send_request('subscribe', clob_ids)
        if resp.get('error'):
            raise Exception(f"Subscribe failed: {resp['error']}")
        self._subscribed_assets.extend(clob_ids)
        return resp.get('data', {}), dt
    
    def unsubscribe(self, clob_ids: List[str]) -> Tuple[dict, float]:
        """Unsubscribe from one or more CLOB IDs."""
        resp, dt = self.send_request('unsubscribe', clob_ids)
        if resp.get('error'):
            raise Exception(f"Unsubscribe failed: {resp['error']}")
        for clob_id in clob_ids:
            if clob_id in self._subscribed_assets:
                self._subscribed_assets.remove(clob_id)
        return resp.get('data', {}), dt
    
    def listen_for_pushes(self):
        """Listen indefinitely for P1 push messages and yield each one."""
        if not self.socket:
            raise ConnectionError("Not connected to server")
        
        raw = b''
        while True:
            try:
                chunk = self.socket.recv(65536)
                if not chunk:
                    break
                raw += chunk
                
                # Try to extract complete packets
                while raw.startswith(b'~'):
                    pipe_idx = raw.find(b'|')
                    if pipe_idx == -1:
                        break
                    
                    # Check if this is P2 (pipe at position 9 after packet_len and sym_len)
                    if pipe_idx == 9 and len(raw) >= 9:
                        try:
                            pkt_len = int(raw[1:5].decode('ascii'))
                            total_len = 5 + pkt_len
                            if len(raw) >= total_len and raw[total_len - 1:total_len] == b'L':
                                # It's P2, skip it
                                raw = raw[total_len:]
                                continue
                        except (ValueError, UnicodeDecodeError):
                            pass
                    
                    # P1 packet
                    try:
                        payload_len = int(raw[1:pipe_idx].decode('ascii'))
                        header_len = pipe_idx + 1
                        needed = header_len + payload_len
                        
                        if len(raw) < needed:
                            break
                        
                        payload = raw[header_len:needed]
                        raw = raw[needed:]
                        
                        try:
                            msg = json.loads(payload.decode('utf-8'))
                            yield msg
                        except json.JSONDecodeError:
                            pass
                    except (ValueError, UnicodeDecodeError):
                        break
                        
            except socket.timeout:
                continue
            except OSError:
                break


# =============================================================================
# Order Event Display
# =============================================================================

# ANSI color codes
COLORS = {
    'PLACEMENT': '\033[92m',      # Green
    'CANCELLATION': '\033[93m',  # Yellow
    'MATCH': '\033[94m',         # Blue
    'TRADE': '\033[95m',         # Magenta
    'LIVE': '\033[92m',          # Green
    'CANCELED': '\033[91m',      # Red
    'FILLED': '\033[96m',        # Cyan
    'BUY': '\033[92m',           # Green
    'SELL': '\033[91m',          # Red
    'reset': '\033[0m',
    'bold': '\033[1m',
    'dim': '\033[2m',
    'header': '\033[1;36m',      # Bold cyan
    'border': '\033[36m',        # Cyan
}


def color(text: str, color_name: str) -> str:
    """Apply color to text."""
    if not sys.stdout.isatty():
        return text
    return f"{COLORS.get(color_name, '')}{text}{COLORS['reset']}"


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text for width calculation."""
    import re
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)


def visible_len(text: str) -> int:
    """Calculate visible width of text (excluding ANSI codes)."""
    return len(strip_ansi(text))


def pad_to_width(text: str, width: int) -> str:
    """Pad text to exact width, accounting for ANSI codes."""
    visible = visible_len(text)
    if visible < width:
        return text + ' ' * (width - visible)
    elif visible > width:
        # Truncate visible portion
        stripped = strip_ansi(text)
        truncated = stripped[:width-3] + '...'
        return truncated
    return text


def format_timestamp(ts_str: str) -> str:
    """Format timestamp string to human readable."""
    try:
        # Handle both seconds and milliseconds
        ts = float(ts_str)
        if ts > 1e12:  # Likely milliseconds
            ts = ts / 1000
        dt = datetime.fromtimestamp(ts)
        return dt.strftime('%H:%M:%S.%f')[:-3]
    except (ValueError, TypeError):
        return ts_str


def format_order_event(event_data: dict, event_number: int) -> str:
    """Format an OrderEvent into a compact hybrid TUI display."""
    lines = []
    
    # Event data
    event_type = event_data.get('type', 'UNKNOWN')
    status = event_data.get('status', 'UNKNOWN')
    side = event_data.get('side', 'UNKNOWN')
    
    # Colors
    status_color = status if status in COLORS else 'reset'
    side_color = side if side in COLORS else 'reset'
    
    # Header line with event info
    time_str = format_timestamp(event_data.get('timestamp', 'N/A'))
    price = event_data.get('price', 'N/A')
    size = event_data.get('original_size', 'N/A')
    
    header_text = f" {event_type} #{event_number} "
    header_line = color(f"┌─{header_text}─", 'border')
    
    # First content line: time | side | price | size @ status
    side_str = color(f" {side} ", side_color)
    status_str = color(status, status_color)
    line1 = f" {time_str} │{side_str}│ {price} │ {size} @ {status_str}"
    
    # Abbreviate IDs for compact display
    market = event_data.get('market', 'N/A')
    if len(market) > 15:
        market = market[:6] + '...' + market[-4:]
    
    order_id = event_data.get('id', 'N/A')
    if len(order_id) > 20:
        order_id = order_id[:8] + '...'
    
    asset_id = event_data.get('asset_id', 'N/A')
    if len(asset_id) > 20:
        asset_id = asset_id[:8] + '...'
    
    outcome = event_data.get('outcome', 'N/A')
    size_filled = event_data.get('size_matched', '0')
    created = format_timestamp(event_data.get('created_at', 'N/A'))
    
    # Build lines with proper padding
    width = 78
    line1_padded = pad_to_width(line1, width - 2)
    line2_padded = pad_to_width(f" Market: {market} │ Outcome: {outcome}", width - 2)
    line3_padded = pad_to_width(f" ID: {order_id} │ Asset: {asset_id}", width - 2)
    line4_padded = pad_to_width(f" Filled: {size_filled}/{size} │ Created: {created}", width - 2)
    
    lines.append(header_line + color('─' * (width - visible_len(header_line) - 1) + '┐', 'border'))
    lines.append(color('│', 'border') + line1_padded + color('│', 'border'))
    lines.append(color('│', 'border') + line2_padded + color('│', 'border'))
    lines.append(color('│', 'border') + line3_padded + color('│', 'border'))
    lines.append(color('│', 'border') + line4_padded + color('│', 'border'))
    lines.append(color('└' + '─' * (width - 2) + '┘', 'border'))
    
    return '\n'.join(lines)


def format_trade_event(event_data: dict, event_number: int) -> str:
    """Format a TradeEvent into a compact hybrid TUI display."""
    lines = []
    
    # Event data
    side = event_data.get('side', 'UNKNOWN')
    side_color = side if side in COLORS else 'reset'
    
    # Header
    header_text = f" TRADE #{event_number} "
    header_line = color(f"┌─{header_text}─", 'border')
    
    # First content line
    time_str = format_timestamp(event_data.get('timestamp', 'N/A'))
    price = event_data.get('price', 'N/A')
    size = event_data.get('size', 'N/A')
    fee_rate = event_data.get('fee_rate_bps', '0')
    
    side_str = color(f" {side} ", side_color)
    line1 = f" {time_str} │{side_str}│ {price} │ {size} (fee: {fee_rate}bps)"
    
    # Abbreviate IDs
    market = event_data.get('market', 'N/A')
    if len(market) > 15:
        market = market[:6] + '...' + market[-4:]
    
    trade_id = event_data.get('id', 'N/A')
    if len(trade_id) > 20:
        trade_id = trade_id[:8] + '...'
    
    outcome = event_data.get('outcome', 'N/A')
    match_time = format_timestamp(event_data.get('match_time', 'N/A'))
    
    # Build lines with proper padding
    width = 78
    line1_padded = pad_to_width(line1, width - 2)
    line2_padded = pad_to_width(f" Market: {market} │ Outcome: {outcome}", width - 2)
    line3_padded = pad_to_width(f" Trade ID: {trade_id} │ Match Time: {match_time}", width - 2)
    
    lines.append(header_line + color('─' * (width - visible_len(header_line) - 1) + '┐', 'border'))
    lines.append(color('│', 'border') + line1_padded + color('│', 'border'))
    lines.append(color('│', 'border') + line2_padded + color('│', 'border'))
    lines.append(color('│', 'border') + line3_padded + color('│', 'border'))
    lines.append(color('└' + '─' * (width - 2) + '┘', 'border'))
    
    return '\n'.join(lines)


def print_summary_stats(stats: dict):
    """Print summary statistics."""
    print('\n' + color('═' * 80, 'border'))
    print(color(' SESSION SUMMARY ', 'header').center(80))
    print(color('═' * 80, 'border'))
    print(f"  Total Events:     {stats['total_events']}")
    print(f"  Duration:         {stats['duration']:.1f} seconds")
    if stats['duration'] > 0:
        print(f"  Events/sec:       {stats['total_events']/stats['duration']:.2f}")
    
    print('\n  Event Type Breakdown:')
    for event_type, count in sorted(stats['by_type'].items()):
        pct = (count / stats['total_events']) * 100 if stats['total_events'] > 0 else 0
        bar = '█' * int(count / max(stats['by_type'].values()) * 30) if stats['by_type'] else ''
        print(f"    {event_type:<15} {count:>5} ({pct:>5.1f}%) {bar}")
    
    print('\n  Status Breakdown:')
    for status, count in sorted(stats['by_status'].items()):
        pct = (count / stats['total_events']) * 100 if stats['total_events'] > 0 else 0
        bar = '█' * int(count / max(stats['by_status'].values()) * 30) if stats['by_status'] else ''
        print(f"    {status:<15} {count:>5} ({pct:>5.1f}%) {bar}")
    
    print(color('═' * 80, 'border') + '\n')


# =============================================================================
# Main Account Monitor
# =============================================================================

def print_banner():
    """Print welcome banner."""
    print(color('╔' + '═' * 78 + '╗', 'border'))
    print(color('║', 'border') + color(' POLY ACCOUNT - Real-time Account Update Monitor '.center(78), 'header') + color('║', 'border'))
    print(color('║', 'border') + ' ' * 78 + color('║', 'border'))
    print(color('║', 'border') + '  Monitor Polymarket account lifecycle events in real-time'.ljust(78) + color('║', 'border'))
    print(color('║', 'border') + '  Press Ctrl+C to exit'.ljust(78) + color('║', 'border'))
    print(color('╚' + '═' * 78 + '╝', 'border'))
    print()


def account_monitor_mode(client: ArgusClient, assets: List[str]):
    """
    Monitor account updates in real-time.
    
    Args:
        client: Connected ArgusClient instance
        assets: List of CLOB IDs to subscribe to (required for receiving pushes)
    """
    # Subscribe to assets
    print(color('📡 Subscribing to assets...', 'dim'))
    try:
        result, rtt = client.subscribe(assets)
        print(f"✓ Subscribed successfully (RTT: {rtt*1000:.1f}ms)")
        if result.get('subscribed'):
            print(f"  Subscribed: {', '.join(result['subscribed'][:5])}")
            if len(result['subscribed']) > 5:
                print(f"  ... and {len(result['subscribed']) - 5} more")
        if result.get('failed'):
            print(f"  ⚠ Failed: {result['failed']}")
    except Exception as e:
        print(f"✗ Subscription failed: {e}", file=sys.stderr)
        sys.exit(1)
    
    print()
    print(color('╔' + '═' * 78 + '╗', 'border'))
    print(color('║', 'border') + ' Listening for account updates... '.center(78) + color('║', 'border'))
    print(color('╚' + '═' * 78 + '╝', 'border'))
    print()
    
    # Statistics tracking
    stats = {
        'total_events': 0,
        'by_type': {},
        'by_status': {},
        'start_time': time.time()
    }
    
    event_number = 0
    
    try:
        for msg in client.listen_for_pushes():
            action = msg.get('action', '')
            
            # Filter for account_update events
            if action != 'account_update':
                continue
            
            event_number += 1
            event_data = msg.get('data', {})
            event_type = event_data.get('type', 'UNKNOWN')
            status = event_data.get('status', 'UNKNOWN')
            
            # Update stats
            stats['total_events'] += 1
            stats['by_type'][event_type] = stats['by_type'].get(event_type, 0) + 1
            stats['by_status'][status] = stats['by_status'].get(status, 0) + 1
            
            # Format and display
            if event_data.get('event_type') == 'trade':
                formatted = format_trade_event(event_data, event_number)
            else:
                formatted = format_order_event(event_data, event_number)
            
            print(formatted)
            print()  # Empty line between events
            
    except KeyboardInterrupt:
        print('\n\n' + color('🛑 Stopped by user.', 'dim'))
    except Exception as e:
        print(f"\n\nError: {e}", file=sys.stderr)
    finally:
        # Calculate stats
        stats['duration'] = time.time() - stats['start_time']
        
        # Unsubscribe
        if assets:
            try:
                print(color('📡 Unsubscribing...', 'dim'))
                client.unsubscribe(assets)
                print('✓ Unsubscribed successfully')
            except Exception as e:
                print(f'⚠ Unsubscribe warning: {e}')
        
        # Print summary
        if stats['total_events'] > 0:
            print_summary_stats(stats)
        else:
            print(f"\nNo account events received in {stats['duration']:.1f} seconds.")
            print("Note: You must have active orders or trades to see events.")


def generate_mock_events():
    """Generate mock account events for testing the UI."""

    mock_events = [
        {
            'type': 'PLACEMENT',
            'status': 'LIVE',
            'side': 'BUY',
            'id': '0xb3910713d0d75f6a7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1',
            'price': '0.84',
            'original_size': '8',
            'size_matched': '0',
            'market': '0xeda225fe8a06a10d7fd646e4b8ded67af783c03bbb4c6abe5dbb164dc2859950',
            'outcome': 'Up',
            'asset_id': '6519393243364562127553246667723212123456789012345678901234567890',
            'created_at': '1740612571',
            'timestamp': '1740612572.164',
            'event_type': 'order'
        },
        {
            'type': 'TRADE',
            'side': 'BUY',
            'id': '67396891-5b18-420a-b123-4567890abcdef',
            'price': '0.15',
            'size': '8',
            'fee_rate_bps': '0',
            'market': '0xf22704964fbe69d0a4faf656beb067da03e0398cac14c8aaee84bc9b58b40c9a',
            'outcome': 'Down',
            'match_time': '1740612571',
            'timestamp': '1740612572.198',
            'event_type': 'trade'
        },
        {
            'type': 'TRADE',
            'side': 'BUY',
            'id': '896d004e-67c1-463a-9def-fedcba0987654321',
            'price': '0.84',
            'size': '1.96',
            'fee_rate_bps': '1000',
            'market': '0xeda225fe8a06a10d7fd646e4b8ded67af783c03bbb4c6abe5dbb164dc2859950',
            'outcome': 'Up',
            'match_time': '1740612571',
            'timestamp': '1740612572.199',
            'event_type': 'trade'
        },
        {
            'type': 'UPDATE',
            'status': 'MATCHED',
            'side': 'BUY',
            'id': '0xb3910713d0d75f6a7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1',
            'price': '0.84',
            'original_size': '8',
            'size_matched': '8.00',
            'market': '0xeda225fe8a06a10d7fd646e4b8ded67af783c03bbb4c6abe5dbb164dc2859950',
            'outcome': 'Up',
            'asset_id': '6519393243364562127553246667723212123456789012345678901234567890',
            'created_at': '1740612572',
            'timestamp': '1740612609.487',
            'event_type': 'order'
        },
        {
            'type': 'PLACEMENT',
            'status': 'LIVE',
            'side': 'SELL',
            'id': '0xcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890ab',
            'price': '0.25',
            'original_size': '12',
            'size_matched': '0',
            'market': '0xf22704964fbe69d0a4faf656beb067da03e0398cac14c8aaee84bc9b58b40c9a',
            'outcome': 'Down',
            'asset_id': '1234567890123456789012345678901234567890123456789012345678901234',
            'created_at': '1740612600',
            'timestamp': '1740612600.500',
            'event_type': 'order'
        },
        {
            'type': 'CANCELLATION',
            'status': 'CANCELED',
            'side': 'SELL',
            'id': '0xdeadbeef1234567890abcdef1234567890abcdef1234567890abcdef12345678',
            'price': '0.50',
            'original_size': '5',
            'size_matched': '0',
            'market': '0xeda225fe8a06a10d7fd646e4b8ded67af783c03bbb4c6abe5dbb164dc2859950',
            'outcome': 'Up',
            'asset_id': '9876543210987654321098765432109876543210987654321098765432109876',
            'created_at': '1740612550',
            'timestamp': '1740612620.123',
            'event_type': 'order'
        },
        {
            'type': 'TRADE',
            'side': 'SELL',
            'id': 'cb5ff47b-63fa-40c1-a2b3-c4d5e6f7a8b9',
            'price': '0.16',
            'size': '6.04',
            'fee_rate_bps': '1000',
            'market': '0xeda225fe8a06a10d7fd646e4b8ded67af783c03bbb4c6abe5dbb164dc2859950',
            'outcome': 'Down',
            'match_time': '1740612609',
            'timestamp': '1740612636.463',
            'event_type': 'trade'
        }
    ]
    
    return mock_events


def test_ui_mode():
    """Display mock events to test the UI formatting."""
    print_banner()
    print(color('🧪 TEST MODE - Displaying mock events with new UI\n', 'dim'))
    
    mock_events = generate_mock_events()
    stats = {
        'total_events': 0,
        'by_type': {},
        'by_status': {},
        'start_time': time.time()
    }
    
    event_number = 0
    
    try:
        for event_data in mock_events:
            event_number += 1
            event_type = event_data.get('type', 'UNKNOWN')
            status = event_data.get('status', 'UNKNOWN')
            
            # Update stats
            stats['total_events'] += 1
            stats['by_type'][event_type] = stats['by_type'].get(event_type, 0) + 1
            stats['by_status'][status] = stats['by_status'].get(status, 0) + 1
            
            # Format and display
            if event_data.get('event_type') == 'trade':
                formatted = format_trade_event(event_data, event_number)
            else:
                formatted = format_order_event(event_data, event_number)
            
            print(formatted)
            print()  # Empty line between events
            
            # Small delay to simulate real-time feel
            time.sleep(0.1)
            
    except KeyboardInterrupt:
        print('\n\n' + color('🛑 Test interrupted.', 'dim'))
    
    # Calculate stats
    stats['duration'] = time.time() - stats['start_time']
    
    # Print summary
    if stats['total_events'] > 0:
        print_summary_stats(stats)
    
    print(color('\n✓ Test UI mode complete. Run without --test-ui for real-time monitoring.\n', 'dim'))


def main():
    parser = argparse.ArgumentParser(
        description='Real-time Account Update Monitor for Polymarket',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python poly_account.py                           # Use default subscription
  python poly_account.py <clob_id>                 # Subscribe to specific asset
  python poly_account.py --host 192.168.1.100 --port 9972 <clob_id>
  python poly_account.py --test-ui                 # Test UI with mock data

Note: You must subscribe to at least one asset to receive account updates.
        """
    )
    
    parser.add_argument(
        'clob_ids',
        nargs='*',
        help='CLOB IDs to subscribe to (optional, uses default if not provided)'
    )
    parser.add_argument(
        '--host',
        default='localhost',
        help='Dispatcher host (default: localhost)'
    )
    parser.add_argument(
        '--port',
        type=int,
        default=9972,
        help='Dispatcher port (default: 9972)'
    )
    parser.add_argument(
        '--default-clob',
        default='661095475084821930790589425827399710453605787397495798070750303202782280580',
        help='Default CLOB ID to subscribe if none provided'
    )
    parser.add_argument(
        '--test-ui',
        action='store_true',
        help='Test UI mode: display mock events without connecting to server'
    )
    
    args = parser.parse_args()
    
    # Handle test UI mode
    if args.test_ui:
        test_ui_mode()
        return
    
    # Determine which assets to subscribe to
    if args.clob_ids:
        assets = args.clob_ids
    else:
        assets = [args.default_clob]
        print(f"Using default CLOB ID: {args.default_clob}")
        print("(Provide CLOB IDs as arguments to subscribe to specific assets)\n")
    
    # Setup signal handler for graceful exit
    def signal_handler(sig, frame):
        print('\n\nReceived interrupt signal...')
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    
    # Connect and run
    client = ArgusClient(host=args.host, port=args.port)
    
    try:
        print_banner()
        print(f"Connecting to {args.host}:{args.port}...")
        client.connect()
        
        # Test connection
        pong, rtt = client.ping()
        print(f"✓ Connected (ping: {rtt*1000:.1f}ms)\n")
        
        # Start monitoring
        account_monitor_mode(client, assets)
        
    except ConnectionError as e:
        print(f"✗ Connection failed: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.disconnect()


if __name__ == '__main__':
    main()
