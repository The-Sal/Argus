#!/usr/bin/env python3
"""
Interactive CLI client for querying Argus Polymarket servers.

This script connects to an Argus dispatcher and allows you to type queries
interactively to search markets and view their details.

Usage:
    python argus_cli.py    # Start interactive mode

Protocol:
    - P1 (control): ~NNNN|<json-payload>
    - P2 (market data): ~NNNN<sym-len>|<symbol><csv>L
"""

import os
import sys
import json
import time
import socket
import statistics
from typing import List, Tuple, Optional, Dict
from datetime import datetime


# =============================================================================
# Protocol Implementation (standalone)
# =============================================================================

def encode_packet(data: bytes) -> bytes:
    """Encode a packet with length prefix."""
    data_length = len(data)
    return f"~{data_length:04d}|".encode('ascii') + data


def decode_packet(packet: bytes) -> bytes:
    """Decode a packet and return the data."""
    if not packet.startswith(b"~"):
        raise ValueError("Invalid packet format: missing start marker '~'")
    
    if len(packet) < 6:
        raise ValueError("Invalid packet format: packet too short")
    
    try:
        length_str = int(packet[1:5].decode('ascii'))
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Invalid data length format in packet")
    
    if packet[5:6] != b"|":
        raise ValueError("Invalid packet format: missing pipe separator")
    
    data = packet[6:6 + length_str]
    if len(data) != length_str:
        raise ValueError("Data length does not match the specified length")
    
    return data


# =============================================================================
# P2 Protocol Parser for Orderbook Data (standalone)
# =============================================================================

# Get orderbook depth from environment or use default
ORDERBOOK_DEPTH = int(os.environ.get('POLYMARKET_ORDERBOOK_DEPTH', 10))


def build_p2_decoding_order(depth: int = ORDERBOOK_DEPTH) -> List[str]:
    """Build the field decoding order for P2 protocol based on orderbook depth."""
    fields = []
    
    # Bid levels: bid_0_price, bid_0_size, bid_1_price, bid_1_size, etc.
    for i in range(depth):
        fields.append(f'bid_{i}_price')
        fields.append(f'bid_{i}_size')
    
    # Ask levels: ask_0_price, ask_0_size, ask_1_price, ask_1_size, etc.
    for i in range(depth):
        fields.append(f'ask_{i}_price')
        fields.append(f'ask_{i}_size')
    
    # Timestamps at the end
    fields.append('clob_timestamp')
    fields.append('server_timestamp')
    
    return fields


P2_DECODING_ORDER = build_p2_decoding_order()


class P2PacketParser:
    """
    Parser for Protocol 2 market data packets from Polymarket.
    Format: ~<packet-length><symbol-length>|<symbol><market-data>L
    """
    
    def __init__(self, decoding_order=None):
        if decoding_order is None:
            decoding_order = P2_DECODING_ORDER
        self.decoding_order = decoding_order
    
    def parse(self, packet_bytes: bytes) -> Dict:
        """Parse a single P2 packet and return dict with symbol and all fields."""
        if len(packet_bytes) < 11:  # Minimum: ~0000|0000|L
            raise ValueError("Packet too short for Protocol 2 format")
        
        pos = 0
        
        # Parse header ~NNNN (5 bytes)
        if packet_bytes[pos] != ord('~'):
            raise ValueError("Invalid header: missing start marker '~'")
        pos += 1
        
        # Extract packet length
        try:
            packet_length = int(packet_bytes[pos:pos + 4].decode('ascii'))
        except (ValueError, UnicodeDecodeError):
            raise ValueError("Invalid packet length format in header")
        pos += 4
        
        # Validate total packet size
        expected_total_length = 5 + packet_length
        if len(packet_bytes) != expected_total_length:
            raise ValueError(f"Packet length mismatch: expected {expected_total_length}, got {len(packet_bytes)}")
        
        # Parse symbol length NNNN| (5 bytes)
        try:
            symbol_length = int(packet_bytes[pos:pos + 4].decode('ascii'))
        except (ValueError, UnicodeDecodeError):
            raise ValueError("Invalid symbol length format")
        pos += 4
        
        if packet_bytes[pos] != ord('|'):
            raise ValueError("Missing pipe separator after symbol length")
        pos += 1
        
        # Parse symbol
        if pos + symbol_length > len(packet_bytes):
            raise ValueError("Symbol length exceeds available packet data")
        
        try:
            symbol = packet_bytes[pos:pos + symbol_length].decode('ascii')
        except UnicodeDecodeError:
            raise ValueError("Invalid ASCII encoding in symbol")
        pos += symbol_length
        
        # Validate terminator
        if packet_bytes[-1] != ord('L'):
            raise ValueError("Invalid terminator: expected 'L'")
        
        # Parse market data (everything except last byte 'L')
        market_data_bytes = packet_bytes[pos:-1]
        try:
            market_data_str = market_data_bytes.decode('ascii')
        except UnicodeDecodeError:
            raise ValueError("Invalid ASCII encoding in market data")
        
        # Parse CSV values
        values = self._parse_csv_values(market_data_str)
        
        # Validate value count matches expected fields
        if len(values) != len(self.decoding_order):
            raise ValueError(f"Field count mismatch: expected {len(self.decoding_order)} values, got {len(values)}")
        
        # Build result dictionary
        result: Dict[str, any] = {'symbol': symbol}
        for i, field_name in enumerate(self.decoding_order):
            result[field_name] = values[i]
        
        return result
    
    def _parse_csv_values(self, data_str: str) -> List[float]:
        """Parse comma-separated values from string."""
        if not data_str:
            raise ValueError("Empty market data")
        
        values = []
        current_value = ""
        
        for char in data_str:
            if char == ',':
                if not current_value:
                    raise ValueError("Empty value found in market data")
                try:
                    values.append(float(current_value))
                except ValueError:
                    raise ValueError(f"Invalid numeric value: '{current_value}'")
                current_value = ""
            else:
                current_value += char
        
        # Handle last value (no trailing comma)
        if current_value:
            try:
                values.append(float(current_value))
            except ValueError:
                raise ValueError(f"Invalid numeric value: '{current_value}'")
        elif data_str.endswith(','):
            raise ValueError("Trailing comma in market data")
        
        return values
    
    def parse_multiple(self, data: bytes) -> List[Dict]:
        """Parse multiple P2 packets from a byte stream."""
        packets = []
        position = 0
        
        while position < len(data):
            if data[position] != ord('~'):
                raise ValueError(f"Invalid packet start at position {position}")
            
            # Extract packet length
            try:
                packet_length = int(data[position + 1:position + 5].decode('ascii'))
            except (ValueError, UnicodeDecodeError):
                raise ValueError(f"Invalid packet length format at position {position}")
            
            total_packet_length = 5 + packet_length  # 5 bytes for header
            packet_bytes = data[position:position + total_packet_length]
            packets.append(self.parse(packet_bytes))
            position += total_packet_length
        
        return packets


class ArgusClient:
    """Simple client for connecting to Argus dispatcher."""
    
    def __init__(self, host: str = 'localhost', port: int = 9972):
        self.host = host
        self.port = port
        self.socket: Optional[socket.socket] = None
        self.p2_parser = P2PacketParser()
    
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
        """
        Send a request and return the parsed JSON response + round-trip time.
        """
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
    
    def get_balance(self) -> Tuple[float, float]:
        """Get account balance."""
        resp, dt = self.send_request('get_balance')
        if resp.get('error'):
            raise Exception(f"Get balance failed: {resp['error']}")
        return float(resp.get('data') or 0), dt
    
    def search_markets(self, keyword: str, limit: int = 100) -> Tuple[List[str], float]:
        """Search for markets by keyword."""
        resp, dt = self.send_request('search_markets', [keyword, limit])
        if resp.get('error'):
            raise Exception(f"Search failed: {resp['error']}")
        return list(resp.get('data') or []), dt
    
    def fetch_all_tickers(self) -> Tuple[List[str], float]:
        """Fetch all tickers from cache using pagination."""
        all_tickers = []
        offset = 0
        limit = 200  # Reduced from 1000 to avoid payload size limits
        total_dt = 0.0

        while True:
            resp, dt = self.send_request('fetch_all_tickers', [limit, offset], timeout=60)
            if resp.get('error'):
                raise Exception(f"Fetch tickers failed: {resp['error']}")
            page = list(resp.get('data') or [])
            if not page:
                break
            all_tickers.extend(page)
            total_dt += dt
            offset += limit

        return all_tickers, total_dt
    
    def fetch_market_by_ticker(self, ticker: str) -> Tuple[dict, float]:
        """Fetch market details by ticker."""
        resp, dt = self.send_request('fetch_market_by_ticker', [ticker])
        if resp.get('error'):
            raise Exception(f"Fetch market failed: {resp['error']}")
        return dict(resp.get('data') or {}), dt
    
    def fetch_clob_id_info(self, clob_id: str) -> Tuple[dict, float]:
        """Fetch information about a clob_id from the market cache."""
        resp, dt = self.send_request('fetch_clob_id_information', [clob_id])
        if resp.get('error'):
            raise Exception(f"Fetch clob_id info failed: {resp['error']}")
        return dict(resp.get('data') or {}), dt
    
    def get_price_to_beat(self, ticker: str) -> Tuple[float, float]:
        """Get the price to beat for an Up/Down market."""
        resp, dt = self.send_request('get_price_to_beat', [ticker])
        if resp.get('error'):
            raise Exception(f"Get price to beat failed: {resp['error']}")
        return float(resp.get('data') or 0), dt
    
    def subscribe(self, clob_ids: List[str]) -> Tuple[dict, float]:
        """Subscribe to one or more CLOB IDs."""
        resp, dt = self.send_request('subscribe', clob_ids)
        if resp.get('error'):
            raise Exception(f"Subscribe failed: {resp['error']}")
        return resp.get('data', {}), dt
    
    def unsubscribe(self, clob_ids: List[str]) -> Tuple[dict, float]:
        """Unsubscribe from one or more CLOB IDs."""
        resp, dt = self.send_request('unsubscribe', clob_ids)
        if resp.get('error'):
            raise Exception(f"Unsubscribe failed: {resp['error']}")
        return resp.get('data', {}), dt
    
    def receive_p2_packets(self, timeout: float = 0.1) -> List[Dict]:
        """Receive and parse P2 packets from the socket."""
        if not self.socket:
            raise ConnectionError("Not connected to server")
        
        # Set non-blocking mode temporarily
        self.socket.setblocking(False)
        try:
            data = b''
            while True:
                try:
                    chunk = self.socket.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                except BlockingIOError:
                    break
            
            if data:
                return self.p2_parser.parse_multiple(data)
            return []
        finally:
            self.socket.setblocking(True)


# =============================================================================
# Data Processing and Display
# =============================================================================

def extract_market_info(event_data: dict) -> dict:
    """Extract important information from market event data."""
    info = {
        'ticker': str(event_data.get('question', 'Unknown')),
        'description': str(event_data.get('description', '')),
        'active': bool(event_data.get('active', False)),
        'closed': bool(event_data.get('closed', True)),
        'markets': []
    }
    
    for market in event_data.get('markets', []):
        # Handle numeric values that might be strings or None
        liquidity = market.get('liquidity') or 0
        if isinstance(liquidity, str):
            try:
                liquidity = float(liquidity)
            except (ValueError, TypeError):
                liquidity = 0
        
        volume = market.get('volume24hr') or 0
        if isinstance(volume, str):
            try:
                volume = float(volume)
            except (ValueError, TypeError):
                volume = 0
        
        # Handle outcomes as a list
        outcomes = market.get('outcomes', [])
        if isinstance(outcomes, list) and outcomes:
            outcome_str = ', '.join(str(o) for o in outcomes)
        else:
            outcome_str = str(outcomes) if outcomes else ''
        
        market_info = {
            'slug': str(market.get('slug', '')),
            'outcomes': outcomes,
            'outcome': outcome_str,  # For backward compatibility
            'clob_token_ids': list(market.get('clobTokenIds', [])),
            'start_time': str(market.get('eventStartTime', market.get('startDate', ''))),
            'end_time': str(market.get('endDate', '')),
            'liquidity': float(liquidity),
            'volume': float(volume)
        }
        info['markets'].append(market_info)
    
    return info


def format_market_table(markets: List[dict], limit: int = 100) -> str:
    """Format market data into a readable table."""
    output = []
    output.append(f"\n{'TICKER':<40} {'OUTCOME':<20} {'ACTIVE':<8} {'LIQUIDITY':<12} {'VOLUME 24H':<12}")
    output.append("=" * 100)
    
    count = 0
    for market in markets:
        if count >= limit:
            break
        
        ticker = market.get('ticker', 'Unknown')
        if len(ticker) > 37:
            ticker = ticker[:37] + '...'
        
        # Get the first market's outcomes as representative
        outcome = 'N/A'
        if market.get('markets'):
            outcomes = market['markets'][0].get('outcomes', [])
            if isinstance(outcomes, list) and outcomes:
                outcome = ', '.join(str(o) for o in outcomes)
            else:
                outcome = str(outcomes) if outcomes else 'N/A'
            if len(outcome) > 17:
                outcome = outcome[:17] + '...'
        
        active = 'Yes' if market.get('active', False) and not market.get('closed', True) else 'No'
        
        liquidity = 0
        volume = 0
        if market.get('markets'):
            for mkt in market['markets']:
                liquidity += mkt.get('liquidity', 0)
                volume += mkt.get('volume', 0)
        
        output.append(f"{ticker:<40} {outcome:<20} {active:<8} {liquidity:>12.2f} {volume:>12.2f}")
        count += 1
    
    output.append(f"\nShowing {count} markets")
    return "\n".join(output)


def format_detailed_market(market_data: dict, ticker: str) -> str:
    """Format detailed information about a specific market."""
    output = []
    output.append("=" * 80)
    output.append(f"DETAILED MARKET INFORMATION: {ticker}")
    output.append("=" * 80)
    
    # Basic event information
    output.append(f"\n📊 EVENT INFORMATION:")
    output.append(f"  Question: {market_data.get('question', 'N/A')}")
    output.append(f"  Description: {market_data.get('description', 'N/A')}")
    output.append(f"  Active: {market_data.get('active', False)}")
    output.append(f"  Closed: {market_data.get('closed', True)}")
    output.append(f"  End Date: {market_data.get('endDate', 'N/A')}")
    
    # Markets within this event
    markets = market_data.get('markets', [])
    output.append(f"\n📈 MARKETS ({len(markets)} market(s) in this event):")
    
    for i, market in enumerate(markets, 1):
        output.append(f"\n  Market {i}:")
        output.append(f"    Slug: {market.get('slug', 'N/A')}")
        outcomes = market.get('outcomes', [])
        if isinstance(outcomes, list) and outcomes:
            outcome_str = ', '.join(str(o) for o in outcomes)
        else:
            outcome_str = str(outcomes) if outcomes else 'N/A'
        output.append(f"    Outcomes: {outcome_str}")
        
        # Time information
        start_time = market.get('eventStartTime') or market.get('startDate')
        end_time = market.get('endDate')
        output.append(f"    Start Time: {start_time or 'N/A'}")
        output.append(f"    End Time: {end_time or 'N/A'}")

        # Financial metrics - handle None values
        liquidity = market.get('liquidity') or 0
        volume_24h = market.get('volume24hr') or 0
        output.append(f"    Liquidity: ${float(liquidity):,.2f}")
        output.append(f"    24h Volume: ${float(volume_24h):,.2f}")

        # CLOB Token IDs (important for trading)
        clob_tokens = market.get('clobTokenIds', [])
        output.append(f"    CLOB Token IDs: {len(clob_tokens)} token(s)")
        for j, token_id in enumerate(clob_tokens[:3], 1):
            output.append(f"      [{j}] {token_id}")
        if len(clob_tokens) > 3:
            output.append(f"      ... and {len(clob_tokens) - 3} more")
        
        # Prices and odds (if available)
        prices = market.get('prices', {})
        if prices:
            output.append(f"    Prices:")
            for outcome, price in prices.items():
                output.append(f"      {outcome}: {price}")
        
        # Additional metadata
        output.append(f"    Active: {market.get('active', False)}")
        output.append(f"    Closed: {market.get('closed', True)}")
    
    # Explanation section
    output.append(f"\n📖 KEY TERMS EXPLAINED:")
    output.append(f"  • Slug: URL-friendly identifier for the market")
    output.append(f"  • CLOB Token ID: Unique identifier for trading on the order book")
    output.append(f"  • Liquidity: Amount of money available for immediate trading")
    output.append(f"  • 24h Volume: Total trading volume in the last 24 hours")
    output.append(f"  • Outcome: The possible result you can bet on")
    output.append(f"  • Active/End Time: When the market is open for trading")
    
    output.append("=" * 80)
    return "\n".join(output)


def format_clob_info(clob_data: dict, clob_id: str) -> str:
    """Format information about a specific clob_id."""
    output = []
    output.append("=" * 80)
    output.append(f"CLOB ID INFORMATION: {clob_id}")
    output.append("=" * 80)
    
    if not clob_data:
        output.append("\n⚠ No information found for this CLOB ID")
        output.append("The CLOB ID may not exist in the market cache.")
        output.append("=" * 80)
        return "\n".join(output)
    
    output.append(f"\n📋 MARKET DETAILS:")
    output.append(f"  Event Name: {clob_data.get('event_name', 'N/A')}")
    output.append(f"  Market Name: {clob_data.get('market_name', 'N/A')}")
    output.append(f"  Outcome: {clob_data.get('outcome', 'N/A')}")
    output.append(f"  Ticker: {clob_data.get('ticker', 'N/A')}")
    output.append(f"  Market Slug: {clob_data.get('market_slug', 'N/A')}")
    
    output.append(f"\n📖 KEY TERMS EXPLAINED:")
    output.append(f"  • Event Name: The title/description of the prediction event")
    output.append(f"  • Market Name: Specific market within the event")
    output.append(f"  • Outcome: The specific result this CLOB ID represents")
    output.append(f"  • Ticker: Unique identifier for the event")
    output.append(f"  • Market Slug: URL-friendly identifier for the market")
    
    output.append("=" * 80)
    return "\n".join(output)


def format_orderbook(parsed_data: Dict, depth: int = 5) -> str:
    """Format orderbook data from parsed P2 packet."""
    output = []
    output.append(f"\n{'SIDE':<6} {'LEVEL':<6} {'PRICE':<12} {'SIZE':<15}")
    output.append("-" * 45)
    
    # Show top N bids (descending order - best bid first)
    bids = []
    for i in range(depth):
        price = parsed_data.get(f'bid_{i}_price', 0)
        size = parsed_data.get(f'bid_{i}_size', 0)
        if price > 0 and size > 0:
            bids.append((price, size))
    
    # Show top N asks (ascending order - best ask first)
    asks = []
    for i in range(depth):
        price = parsed_data.get(f'ask_{i}_price', 0)
        size = parsed_data.get(f'ask_{i}_size', 0)
        if price > 0 and size > 0:
            asks.append((price, size))
    
    # Display bids (best first)
    for i, (price, size) in enumerate(bids[:depth]):
        output.append(f"{'BID':<6} {i:<6} {price:<12.4f} {size:<15.2f}")
    
    output.append("-" * 45)
    
    # Display asks (best first)
    for i, (price, size) in enumerate(asks[:depth]):
        output.append(f"{'ASK':<6} {i:<6} {price:<12.4f} {size:<15.2f}")
    
    return "\n".join(output)


def show_aggregate_stats(clob_to_argus_latencies: List[float], 
                         argus_to_cli_latencies: List[float],
                         total_latencies: List[float]):
    """Display aggregate latency statistics with seaborn visualizations for all three hops."""
    import numpy as np
    
    def calc_stats(latencies_ms: List[float], name: str):
        """Calculate statistics for a single latency dataset."""
        if not latencies_ms:
            return None
        
        n = len(latencies_ms)
        min_lat = min(latencies_ms)
        max_lat = max(latencies_ms)
        avg_lat = statistics.mean(latencies_ms)
        median_lat = statistics.median(latencies_ms)
        
        sorted_lats = sorted(latencies_ms)
        p95_idx = int(n * 0.95)
        p99_idx = int(n * 0.99)
        p95_lat = sorted_lats[min(p95_idx, n-1)]
        p99_lat = sorted_lats[min(p99_idx, n-1)]
        
        if n > 1:
            std_lat = statistics.stdev(latencies_ms)
        else:
            std_lat = 0.0
        
        return {
            'name': name,
            'n': n,
            'min': min_lat,
            'max': max_lat,
            'avg': avg_lat,
            'median': median_lat,
            'std': std_lat,
            'p95': p95_lat,
            'p99': p99_lat,
            'data': latencies_ms,
            'sorted': sorted_lats
        }
    
    # Calculate stats for all three hops
    exchange_stats = calc_stats(clob_to_argus_latencies, "CLOB → Argus (Exchange)")
    argus_stats = calc_stats(argus_to_cli_latencies, "Argus → CLI (Network)")
    total_stats = calc_stats(total_latencies, "Total (End-to-End)")
    
    if not total_stats:
        print("\n⚠ No latency data collected.")
        return
    
    all_stats = [s for s in [exchange_stats, argus_stats, total_stats] if s]
    
    print("\n" + "=" * 100)
    print("📊 LATENCY STATISTICS BY HOP")
    print("=" * 100)
    
    for stats in all_stats:
        print(f"\n🏷️  {stats['name']}")
        print("-" * 100)
        print(f"  Total packets:     {stats['n']:,}")
        print(f"  Min latency:       {stats['min']:.2f} ms")
        print(f"  Max latency:       {stats['max']:.2f} ms")
        print(f"  Average:           {stats['avg']:.2f} ms")
        print(f"  Median:            {stats['median']:.2f} ms")
        print(f"  Std Dev:           {stats['std']:.2f} ms")
        print(f"  P95:               {stats['p95']:.2f} ms")
        print(f"  P99:               {stats['p99']:.2f} ms")
        
        # Distribution histogram
        print(f"\n  📈 Distribution:")
        bucket_size = max(1, int((stats['max'] - stats['min']) / 10)) if stats['max'] > stats['min'] else 1
        buckets = {}
        for lat in stats['data']:
            bucket = int(lat / bucket_size) * bucket_size
            buckets[bucket] = buckets.get(bucket, 0) + 1
        
        sorted_buckets = sorted(buckets.items())
        max_count = max(buckets.values()) if buckets else 1
        
        for bucket, count in sorted_buckets:
            bar_len = int(30 * count / max_count)
            bar = "█" * bar_len
            pct = 100 * count / stats['n']
            print(f"    {bucket:>6.0f}-{bucket+bucket_size:<6.0f} ms: {bar:<30} {count:>6,} ({pct:>5.1f}%)")
    
    print("=" * 100)
    
    # Comparison table - only show if all three have data
    if exchange_stats and argus_stats and total_stats:
        print("\n📊 SIDE-BY-SIDE COMPARISON")
        print("-" * 100)
        print(f"{'Metric':<20} {'Exchange→Argus':<20} {'Argus→CLI':<20} {'Total E2E':<20}")
        print("-" * 100)
        print(f"{'Min (ms)':<20} {exchange_stats['min']:<20.2f} {argus_stats['min']:<20.2f} {total_stats['min']:<20.2f}")
        print(f"{'Max (ms)':<20} {exchange_stats['max']:<20.2f} {argus_stats['max']:<20.2f} {total_stats['max']:<20.2f}")
        print(f"{'Avg (ms)':<20} {exchange_stats['avg']:<20.2f} {argus_stats['avg']:<20.2f} {total_stats['avg']:<20.2f}")
        print(f"{'Median (ms)':<20} {exchange_stats['median']:<20.2f} {argus_stats['median']:<20.2f} {total_stats['median']:<20.2f}")
        print(f"{'Std Dev (ms)':<20} {exchange_stats['std']:<20.2f} {argus_stats['std']:<20.2f} {total_stats['std']:<20.2f}")
        print(f"{'P95 (ms)':<20} {exchange_stats['p95']:<20.2f} {argus_stats['p95']:<20.2f} {total_stats['p95']:<20.2f}")
        print(f"{'P99 (ms)':<20} {exchange_stats['p99']:<20.2f} {argus_stats['p99']:<20.2f} {total_stats['p99']:<20.2f}")
        print("=" * 100)
    
    # Try to create seaborn visualization with all three metrics
    try:
        import matplotlib.pyplot as plt
        import seaborn as sns
        
        sns.set_style("whitegrid")
        
        fig, axes = plt.subplots(3, 2, figsize=(16, 14))
        fig.suptitle(f'CLOB Latency Analysis by Hop\n({total_stats["n"]:,} packets, {ORDERBOOK_DEPTH} orderbook levels)', 
                     fontsize=16, fontweight='bold')
        
        colors = ['#1f77b4', '#ff7f0e', '#2ca02c']  # Blue, Orange, Green
        labels = ['Exchange→Argus', 'Argus→CLI', 'Total E2E']
        datasets = [clob_to_argus_latencies, argus_to_cli_latencies, total_latencies]
        
        # Row 0: Exchange→Argus
        if exchange_stats:
            ax = axes[0, 0]
            sns.histplot(clob_to_argus_latencies, bins=50, kde=True, ax=ax, color=colors[0], alpha=0.7)
            ax.axvline(exchange_stats['avg'], color='red', linestyle='--', linewidth=2, label=f'Mean: {exchange_stats["avg"]:.2f} ms')
            ax.axvline(exchange_stats['median'], color='green', linestyle='--', linewidth=2, label=f'Median: {exchange_stats["median"]:.2f} ms')
            ax.set_xlabel('Latency (ms)')
            ax.set_ylabel('Count')
            ax.set_title('Exchange → Argus Latency Distribution')
            ax.legend()
            
            ax = axes[0, 1]
            sns.boxplot(y=clob_to_argus_latencies, ax=ax, color=colors[0])
            ax.set_ylabel('Latency (ms)')
            ax.set_title('Exchange → Argus (Box Plot)')
        
        # Row 1: Argus→CLI
        if argus_stats:
            ax = axes[1, 0]
            sns.histplot(argus_to_cli_latencies, bins=50, kde=True, ax=ax, color=colors[1], alpha=0.7)
            ax.axvline(argus_stats['avg'], color='red', linestyle='--', linewidth=2, label=f'Mean: {argus_stats["avg"]:.2f} ms')
            ax.axvline(argus_stats['median'], color='green', linestyle='--', linewidth=2, label=f'Median: {argus_stats["median"]:.2f} ms')
            ax.set_xlabel('Latency (ms)')
            ax.set_ylabel('Count')
            ax.set_title('Argus → CLI Latency Distribution')
            ax.legend()
            
            ax = axes[1, 1]
            sns.boxplot(y=argus_to_cli_latencies, ax=ax, color=colors[1])
            ax.set_ylabel('Latency (ms)')
            ax.set_title('Argus → CLI (Box Plot)')
        
        # Row 2: Comparison and ECDF
        if all([exchange_stats, argus_stats, total_stats]):
            # Combined histogram
            ax = axes[2, 0]
            for i, (data, label, color) in enumerate(zip(datasets, labels, colors)):
                sns.histplot(data, bins=50, kde=True, ax=ax, label=label, color=color, alpha=0.5)
            ax.set_xlabel('Latency (ms)')
            ax.set_ylabel('Count')
            ax.set_title('All Hops Comparison')
            ax.legend()
            
            # ECDF comparison
            ax = axes[2, 1]
            for i, (data, label, color) in enumerate(zip(datasets, labels, colors)):
                sorted_data = np.array(sorted(data))
                yvals = np.arange(1, len(sorted_data) + 1) / len(sorted_data) * 100
                ax.plot(sorted_data, yvals, linewidth=2, label=label, color=color)
            ax.axhline(95, color='red', linestyle='--', alpha=0.5, label='P95')
            ax.axhline(99, color='darkred', linestyle='--', alpha=0.5, label='P99')
            ax.set_xlabel('Latency (ms)')
            ax.set_ylabel('Cumulative Percentage (%)')
            ax.set_title('Cumulative Distribution Comparison (ECDF)')
            ax.legend()
            ax.grid(True, alpha=0.3)
        
        plt.tight_layout()
        
        # Save and show
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"clob_latency_{timestamp}.png"
        plt.savefig(filename, dpi=150, bbox_inches='tight')
        print(f"\n  📁 Saved visualization to: {filename}")
        plt.show()
        
    except ImportError as e:
        print(f"\n  ⚠ Seaborn/Matplotlib not available ({e}). Skipping visualization.")
        print("    To enable: pip install seaborn matplotlib")


# =============================================================================
# CLOB Subscription with Latency Monitoring
# =============================================================================

def subscribe_clob_latency_mode(client: ArgusClient, clob_id: str):
    """
    Subscribe to a CLOB and display real-time latency measurements.
    Press Ctrl+C to stop and show aggregate statistics.
    """
    # Track separate latencies for each hop
    clob_to_argus_latencies = []  # Exchange -> Argus dispatcher
    argus_to_cli_latencies = []   # Argus dispatcher -> CLI client
    total_latencies = []          # End-to-end
    packet_count = 0
    start_time = time.time()
    
    # Subscribe to the CLOB
    print(f"\n📡 Subscribing to CLOB: {clob_id}")
    try:
        result, rtt = client.subscribe([clob_id])
        print(f"✓ Subscribed successfully (RTT: {rtt*1000:.1f}ms)")
        print(f"  Subscribed: {result.get('subscribed', [])}")
        print(f"  Failed: {result.get('failed', [])}")
    except Exception as e:
        print(f"✗ Subscription failed: {e}")
        return
    
    print(f"\n📊 Monitoring latency... Press Ctrl+C to stop and view statistics.")
    print(f"   Orderbook depth: {ORDERBOOK_DEPTH} levels")
    print(f"\n{'PACKET':<8} {'CLOB->ARGUS':<15} {'ARGUS->CLI':<15} {'TOTAL':<15} {'BEST BID':<12} {'BEST ASK':<12}")
    print("-" * 85)
    
    try:
        while True:
            # Receive P2 packets
            packets = client.receive_p2_packets(timeout=0.1)
            
            for packet_data in packets:
                packet_count += 1
                receive_time = time.time()
                
                # Extract timestamps
                clob_ts_ms = packet_data.get('clob_timestamp', 0)
                server_ts = packet_data.get('server_timestamp', 0)
                
                # Calculate latencies
                if clob_ts_ms > 0 and server_ts > 0:
                    # CLOB timestamp is in milliseconds from Polymarket
                    clob_to_argus_ms = (server_ts * 1000) - clob_ts_ms
                    argus_to_cli_ms = (receive_time - server_ts) * 1000
                    total_latency_ms = (receive_time * 1000) - clob_ts_ms
                    
                    # Store all three metrics
                    clob_to_argus_latencies.append(clob_to_argus_ms)
                    argus_to_cli_latencies.append(argus_to_cli_ms)
                    total_latencies.append(total_latency_ms)
                    
                    # Get best bid/ask for display
                    best_bid = packet_data.get('bid_0_price', 0)
                    best_ask = packet_data.get('ask_0_price', 0)
                    
                    # Print packet info
                    print(f"{packet_count:<8} {clob_to_argus_ms:<15.2f} {argus_to_cli_ms:<15.2f} "
                          f"{total_latency_ms:<15.2f} {best_bid:<12.4f} {best_ask:<12.4f}")
                    
                    # Show full orderbook every 10 packets
                    if packet_count % 10 == 0:
                        print(format_orderbook(packet_data, depth=3))
                        print(f"\n{'PACKET':<8} {'CLOB->ARGUS':<15} {'ARGUS->CLI':<15} {'TOTAL':<15} {'BEST BID':<12} {'BEST ASK':<12}")
                        print("-" * 85)
            
            # Small sleep to prevent CPU spinning
            # time.sleep(0.01)
            
    except KeyboardInterrupt:
        print(f"\n\n🛑 Stopped by user.")
        
        # Unsubscribe
        try:
            print(f"📡 Unsubscribing from CLOB: {clob_id}")
            result, _ = client.unsubscribe([clob_id])
            print(f"✓ Unsubscribed successfully")
        except Exception as e:
            print(f"⚠ Unsubscribe warning: {e}")
        
        # Show aggregate statistics
        duration = time.time() - start_time
        print(f"\n📈 Session Summary:")
        print(f"  Duration: {duration:.1f} seconds")
        print(f"  Total packets: {packet_count}")
        print(f"  Packets/sec: {packet_count/duration:.1f}" if duration > 0 else "  N/A")
        
        # Show separate statistics for each hop
        show_aggregate_stats(clob_to_argus_latencies, argus_to_cli_latencies, total_latencies)


# =============================================================================
# Interactive CLI Interface
# =============================================================================

def print_banner():
    """Print welcome banner."""
    print("\n" + "="*50)
    print("  Argus Polymarket Interactive CLI")
    print("  Type 'help' for commands, 'quit' to exit")
    print("="*50 + "\n")


def print_help():
    """Print help information."""
    print("\nAvailable commands:")
    print("  <query>                    - Search for markets (e.g., 'bitcoin', 'trump')")
    print("  top [N]                   - Show top N markets (default: 100)")
    print("  info <ticker>              - Show detailed info about specific market")
    print("  clob <clob_id>            - Show info about a CLOB token ID")
    print("  sub <clob_id>             - Subscribe to CLOB and monitor latency (Ctrl+C to stop)")
    print("  price <ticker>            - Get price to beat for Up/Down markets")
    print("  balance                    - Show account balance")
    print("  ping                       - Test connection")
    print("  stats                      - Show market statistics")
    print("  clear                      - Clear screen")
    print("  help                       - Show this help")
    print("  quit                       - Exit the program")
    print("\nExamples:")
    print("  info bitcoin-up-or-down-february-10-11-am-et")
    print("  clob 661095475084821930790589425827399710453605787397495798070750303202782280580")
    print("  sub 661095475084821930790589425827399710453605787397495798070750303202782280580")
    print("  price bitcoin-up-or-down-february-10-4pm-et")
    print("  trump")
    print("  top 50")
    print()


def interactive_loop(client: ArgusClient):
    """Main interactive loop."""
    print_banner()
    
    while True:
        try:
            query = input("argus> ").strip()
            
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
                print_banner()
            elif query.lower() == 'ping':
                try:
                    pong, rtt = client.ping()
                    print(f"✓ Ping successful: {pong} ({rtt*1000:.1f}ms)")
                except Exception as e:
                    print(f"✗ Ping failed: {e}")
            elif query.lower() == 'balance':
                try:
                    balance, rtt = client.get_balance()
                    print(f"✓ Balance: {balance} USDC ({rtt*1000:.1f}ms)")
                except Exception as e:
                    print(f"✗ Failed to get balance: {e}")
            elif query.lower() == 'stats':
                try:
                    tickers, fetch_time = client.fetch_all_tickers()
                    print(f"✓ Total tickers in cache: {len(tickers)} ({fetch_time*1000:.1f}ms)")
                except Exception as e:
                    print(f"✗ Failed to get stats: {e}")
            elif query.lower().startswith('info '):
                ticker = query[5:].strip()
                if not ticker:
                    print("✗ Please provide a ticker name. Usage: info <ticker>")
                    continue
                try:
                    print(f"Fetching detailed information for '{ticker}'...")
                    event_data, fetch_time = client.fetch_market_by_ticker(ticker)
                    print(f"✓ Fetched in {fetch_time*1000:.1f}ms")
                    print(format_detailed_market(event_data, ticker))
                except Exception as e:
                    print(f"✗ Failed to fetch market info: {e}")
                    print(f"  Tip: Use the search command first to find the exact ticker name")
            elif query.lower().startswith('clob '):
                clob_id = query[5:].strip()
                if not clob_id:
                    print("✗ Please provide a CLOB ID. Usage: clob <clob_id>")
                    continue
                try:
                    print(f"Fetching information for CLOB ID '{clob_id}'...")
                    clob_data, fetch_time = client.fetch_clob_id_info(clob_id)
                    print(f"✓ Fetched in {fetch_time*1000:.1f}ms")
                    print(format_clob_info(clob_data, clob_id))
                except Exception as e:
                    print(f"✗ Failed to fetch CLOB info: {e}")
                    print(f"  Tip: Use the 'info' command to see available CLOB Token IDs for a market")
            elif query.lower().startswith('sub '):
                clob_id = query[4:].strip()
                if not clob_id:
                    print("✗ Please provide a CLOB ID. Usage: sub <clob_id>")
                    print(f"  Orderbook depth: {ORDERBOOK_DEPTH} levels")
                    continue
                subscribe_clob_latency_mode(client, clob_id)
            elif query.lower().startswith('price '):
                ticker = query[6:].strip()
                if not ticker:
                    print("✗ Please provide a ticker. Usage: price <ticker>")
                    continue
                try:
                    print(f"Fetching price to beat for '{ticker}'...")
                    price, fetch_time = client.get_price_to_beat(ticker)
                    print(f"✓ Price to beat: {price:,.2f} ({fetch_time*1000:.1f}ms)")
                    print(f"\n  This is the reference price for Up/Down markets.")
                    print(f"  If the final price is ABOVE this, 'Up' wins.")
                    print(f"  If the final price is BELOW this, 'Down' wins.")
                except Exception as e:
                    print(f"✗ Failed to fetch price to beat: {e}")
                    print(f"  Tip: This command only works for Up/Down markets (e.g., btc-updown, bitcoin-up-or-down)")
            elif query.lower().startswith('top'):
                parts = query.split()
                limit = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 100
                try:
                    print(f"Fetching top {limit} markets...")
                    tickers, fetch_time = client.fetch_all_tickers()
                    tickers = tickers[:limit]
                    
                    markets = []
                    print(f"Fetching details for {len(tickers)} markets...")
                    for i, ticker in enumerate(tickers):
                        try:
                            event_data, _ = client.fetch_market_by_ticker(ticker)
                            market_info = extract_market_info(event_data)
                            market_info['ticker'] = ticker
                            markets.append(market_info)
                            
                            if (i + 1) % 20 == 0:
                                print(f"  Progress: {i + 1}/{len(tickers)}")
                        except Exception as e:
                            continue
                    
                    # Sort by active status and liquidity
                    markets.sort(key=lambda m: (
                        not m.get('active', False) or m.get('closed', True),
                        -sum(mkt.get('liquidity', 0) for mkt in m.get('markets', []))
                    ))
                    
                    print(format_market_table(markets, limit))
                    
                    # Show statistics
                    active_markets = sum(1 for m in markets if m.get('active', False) and not m.get('closed', True))
                    total_liquidity = sum(sum(mkt.get('liquidity', 0) for mkt in m.get('markets', [])) for m in markets)
                    total_volume = sum(sum(mkt.get('volume', 0) for mkt in m.get('markets', [])) for m in markets)
                    
                    print(f"\nStatistics:")
                    print(f"  Total markets: {len(markets)}")
                    print(f"  Active markets: {active_markets}")
                    print(f"  Total liquidity: ${total_liquidity:,.2f}")
                    print(f"  Total 24h volume: ${total_volume:,.2f}")
                    
                except Exception as e:
                    print(f"✗ Failed to fetch top markets: {e}")
            else:
                # Treat as search query
                try:
                    print(f"Searching for markets matching '{query}'...")
                    tickers, search_time = client.search_markets(query, 50)
                    print(f"Found {len(tickers)} matching tickers ({search_time*1000:.1f}ms)")
                    
                    if tickers:
                        markets = []
                        print(f"Fetching details for {len(tickers)} markets...")
                        for i, ticker in enumerate(tickers):
                            try:
                                event_data, _ = client.fetch_market_by_ticker(ticker)
                                market_info = extract_market_info(event_data)
                                market_info['ticker'] = ticker
                                markets.append(market_info)
                            except Exception:
                                continue
                        
                        # Sort by active status and liquidity
                        markets.sort(key=lambda m: (
                            not m.get('active', False) or m.get('closed', True),
                            -sum(mkt.get('liquidity', 0) for mkt in m.get('markets', []))
                        ))
                        
                        print(format_market_table(markets, 50))
                    else:
                        print("No markets found matching your query.")
                        
                except Exception as e:
                    print(f"✗ Search failed: {e}")
                    
        except KeyboardInterrupt:
            print("\nType 'quit' to exit.")
        except EOFError:
            print("\nGoodbye!")
            break


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Argus Polymarket Interactive CLI Client')
    parser.add_argument('--host', default='localhost', help='Argus server host (default: localhost)')
    parser.add_argument('--port', type=int, default=9972, help='Argus server port (default: 9972)')
    parser.add_argument('--no-interactive', action='store_true', help='Run in non-interactive mode')
    
    args = parser.parse_args()
    
    client = ArgusClient(args.host, args.port)
    
    try:
        print(f"Connecting to Argus server at {args.host}:{args.port}...")
        client.connect()
        
        # Test connection
        pong, rtt = client.ping()
        print(f"✓ Connected (ping: {rtt*1000:.1f}ms)")
        
        if args.no_interactive:
            # Non-interactive mode - just show top markets
            interactive_loop(client)
        else:
            # Interactive mode
            interactive_loop(client)
        
    except ConnectionError as e:
        print(f"Connection error: {e}")
        print("Make sure the Argus dispatcher is running.")
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
