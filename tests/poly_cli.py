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

import sys
import json
import time
import socket
from typing import List, Tuple, Optional


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


class ArgusClient:
    """Simple client for connecting to Argus dispatcher."""
    
    def __init__(self, host: str = 'localhost', port: int = 9972):
        self.host = host
        self.port = port
        self.socket: Optional[socket.socket] = None
    
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
        """Fetch all tickers from cache."""
        resp, dt = self.send_request('fetch_all_tickers', timeout=60)
        if resp.get('error'):
            raise Exception(f"Fetch tickers failed: {resp['error']}")
        return list(resp.get('data') or []), dt
    
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