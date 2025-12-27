"""
Example client for connecting to the PolyMarket Dispatcher and subscribing to assets.

This demonstrates the workflow for clients to:
1. Connect to the dispatcher
2. Fetch available events/markets
3. Subscribe to asset price updates
4. Receive real-time market data via Protocol 2
"""
import socket
import json
import time
import struct


class PolymarketClient:
    """Client for connecting to PolyMarket Dispatcher."""

    def __init__(self, socket_path='/tmp/argus_polymarket.sock'):
        self.socket_path = socket_path
        self.sock = None

    def connect(self):
        """Connect to the dispatcher's Unix Domain Socket."""
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.socket_path)
        print(f"✓ Connected to {self.socket_path}")

    def disconnect(self):
        """Disconnect from the dispatcher."""
        if self.sock:
            self.sock.close()
            print("✓ Disconnected")

    def _encode_packet(self, data: bytes) -> bytes:
        """Encodes data as a packet for transmission."""
        data_length = len(data)
        packet_length_header = data_length.to_bytes(4, byteorder='big')
        return b'~' + packet_length_header + b'|' + data

    def _decode_packet(self, packet: bytes) -> bytes:
        """Decodes a packet into its data component."""
        if not packet.startswith(b'~'):
            raise ValueError("Invalid packet format")
        data_length = int.from_bytes(packet[1:5], byteorder='big')
        data = packet[6:6 + data_length]
        return data

    def _send_command(self, command: dict) -> dict:
        """Send a command to the dispatcher and receive response."""
        # Encode command as JSON packet
        json_data = json.dumps(command).encode('ascii')
        packet = self._encode_packet(json_data)

        # Send to dispatcher
        self.sock.sendall(packet)

        # Receive response (timeout after 5 seconds)
        self.sock.settimeout(5.0)
        response_data = self.sock.recv(4096)

        # Decode response
        response_bytes = self._decode_packet(response_data)
        response = json.loads(response_bytes.decode('ascii'))

        return response

    def fetch_events(self, offset=0, limit=10):
        """Fetch available events from Polymarket.

        Returns:
            dict: Response with status and list of events
        """
        print(f"\n📊 Fetching {limit} events...")
        command = {
            'action': 'fetch_events',
            'offset': offset,
            'limit': limit
        }
        response = self._send_command(command)

        if response['status'] == 'success':
            events = response['data']
            print(f"✓ Received {len(events)} events:")
            for i, event in enumerate(events, 1):
                print(f"  {i}. {event['title']} ({event['markets']} markets)")
            return events
        else:
            print(f"✗ Error: {response.get('message')}")
            return []

    def stream_asset(self, asset_id: str):
        """Subscribe to real-time price updates for an asset.

        Args:
            asset_id: CLOB token ID to subscribe to

        Returns:
            dict: Response with subscription status
        """
        print(f"\n📡 Subscribing to asset: {asset_id}")
        command = {
            'action': 'stream_asset',
            'asset_id': asset_id
        }
        response = self._send_command(command)

        if response['status'] == 'success':
            print(f"✓ {response['message']}")
        else:
            print(f"✗ Error: {response.get('message')}")

        return response

    def stream_market_by_keyword(self, keyword: str):
        """Find and subscribe to a market by keyword search.

        Args:
            keyword: Search term (e.g., "Trump", "Bitcoin", "Election")

        Returns:
            dict: Response with subscription status
        """
        print(f"\n🔍 Searching for markets with keyword: '{keyword}'")
        command = {
            'action': 'stream_market_by_keyword',
            'keyword': keyword
        }
        response = self._send_command(command)

        if response['status'] == 'success':
            print(f"✓ {response['message']}")
        else:
            print(f"✗ {response.get('message')}")

        return response

    def unsubscribe_asset(self, asset_id: str):
        """Unsubscribe from asset price updates.

        Args:
            asset_id: CLOB token ID to unsubscribe from

        Returns:
            dict: Response with unsubscribe status
        """
        print(f"\n🛑 Unsubscribing from asset: {asset_id}")
        command = {
            'action': 'unsubscribe_asset',
            'asset_id': asset_id
        }
        response = self._send_command(command)

        if response['status'] == 'success':
            print(f"✓ {response['message']}")
        else:
            print(f"✗ Error: {response.get('message')}")

        return response

    def receive_market_data(self, timeout=None):
        """Receive real-time market data updates (Protocol 2 format).

        Args:
            timeout: Seconds to wait for data (None = blocking)

        Returns:
            dict: Parsed market data or None if timeout
        """
        self.sock.settimeout(timeout)
        try:
            # Read packet header: ~<data-length><symbol-length>|
            header = self.sock.recv(9)
            if not header or not header.startswith(b'~'):
                return None

            data_length = int.from_bytes(header[1:5], byteorder='big')
            symbol_length = int.from_bytes(header[5:9], byteorder='big')

            # Read remaining data
            remaining = data_length - 4  # Already read 4 bytes (symbol_length header)
            data = self.sock.recv(remaining)

            # Parse Protocol 2 data
            asset_id = data[:symbol_length].decode('ascii')
            values_str = data[symbol_length:-1].decode('ascii')  # Remove 'L' terminator
            values = values_str.split(',')

            if len(values) != 8:
                print(f"⚠️  Invalid data format: expected 8 values, got {len(values)}")
                return None

            market_data = {
                'asset_id': asset_id,
                'best_bid': float(values[0]),
                'liquidity': float(values[1]),
                'best_ask': float(values[2]),
                'volume': float(values[3]),
                'price': float(values[4]),
                'price_change': float(values[5]),
                'timestamp': int(float(values[6])),
                'python_timestamp': float(values[7])
            }

            return market_data

        except socket.timeout:
            return None
        except Exception as e:
            print(f"⚠️  Error receiving data: {e}")
            return None


# ============================================================================
# WORKFLOW EXAMPLES
# ============================================================================

def workflow_1_manual_asset_id():
    """Workflow 1: Subscribe to a specific asset ID (if you already know it)."""
    print("\n" + "="*70)
    print("WORKFLOW 1: Subscribe to Asset by ID")
    print("="*70)

    client = PolymarketClient()
    client.connect()

    try:
        # If you already have the asset ID, subscribe directly
        asset_id = "21742633143463906290569050155826241533067272736897614950488156847949938836455"
        client.stream_asset(asset_id)

        # Listen for market data updates
        print("\n📊 Listening for market data (Ctrl+C to stop)...")
        for i in range(10):  # Receive 10 updates
            data = client.receive_market_data(timeout=30.0)
            if data:
                print(f"\n[Update {i+1}] Asset: {data['asset_id'][:20]}...")
                print(f"  Price: ${data['price']:.4f}")
                print(f"  Best Bid: ${data['best_bid']:.4f} | Best Ask: ${data['best_ask']:.4f}")
                print(f"  Volume: ${data['volume']:,.2f} | Liquidity: ${data['liquidity']:,.2f}")
                print(f"  24h Change: {data['price_change']:+.2%}")

    except KeyboardInterrupt:
        print("\n\n⏹  Stopped by user")
    finally:
        client.disconnect()


def workflow_2_search_by_keyword():
    """Workflow 2: Search for a market by keyword and subscribe."""
    print("\n" + "="*70)
    print("WORKFLOW 2: Search and Subscribe by Keyword")
    print("="*70)

    client = PolymarketClient()
    client.connect()

    try:
        # Search for a market by keyword (e.g., "Bitcoin", "Trump", "Election")
        keyword = "Bitcoin"
        response = client.stream_market_by_keyword(keyword)

        if response['status'] == 'success':
            # Listen for market data updates
            print(f"\n📊 Listening for '{keyword}' market data (Ctrl+C to stop)...")
            for i in range(10):
                data = client.receive_market_data(timeout=30.0)
                if data:
                    print(f"\n[Update {i+1}]")
                    print(f"  Price: ${data['price']:.4f}")
                    print(f"  Probability: {data['price']*100:.2f}%")

    except KeyboardInterrupt:
        print("\n\n⏹  Stopped by user")
    finally:
        client.disconnect()


def workflow_3_browse_and_select():
    """Workflow 3: Browse events, select a market, and subscribe."""
    print("\n" + "="*70)
    print("WORKFLOW 3: Browse Events and Select Market")
    print("="*70)

    client = PolymarketClient()
    client.connect()

    try:
        # Step 1: Fetch available events
        events = client.fetch_events(limit=5)

        if not events:
            print("No events available")
            return

        # Step 2: For this example, we'll search for a specific keyword
        # In a real application, you'd parse event data to get asset IDs
        print("\n💡 Tip: To get asset IDs, you need to:")
        print("   1. Fetch events via dispatcher")
        print("   2. Parse event data to find markets and their clobTokenIds")
        print("   3. Use those asset IDs to subscribe")

        # Example: Search and subscribe
        keyword = input("\nEnter keyword to search and stream (or press Enter for 'Trump'): ").strip()
        if not keyword:
            keyword = "Trump"

        response = client.stream_market_by_keyword(keyword)

        if response['status'] == 'success':
            print(f"\n📊 Streaming market data for '{keyword}'...")
            for i in range(5):
                data = client.receive_market_data(timeout=30.0)
                if data:
                    print(f"\n[{time.strftime('%H:%M:%S')}] Price: ${data['price']:.4f} ({data['price']*100:.2f}%)")

    except KeyboardInterrupt:
        print("\n\n⏹  Stopped by user")
    finally:
        client.disconnect()


def workflow_4_advanced_multi_asset():
    """Workflow 4: Subscribe to multiple assets and monitor them."""
    print("\n" + "="*70)
    print("WORKFLOW 4: Multi-Asset Monitoring")
    print("="*70)

    client = PolymarketClient()
    client.connect()

    try:
        # Subscribe to multiple markets by keyword
        keywords = ["Bitcoin", "Trump", "Ethereum"]

        for keyword in keywords:
            print(f"\n📡 Subscribing to '{keyword}'...")
            client.stream_market_by_keyword(keyword)
            time.sleep(1)  # Small delay between subscriptions

        # Monitor all subscribed assets
        print(f"\n📊 Monitoring {len(keywords)} markets (Ctrl+C to stop)...")
        print("-" * 70)

        while True:
            data = client.receive_market_data(timeout=30.0)
            if data:
                timestamp = time.strftime('%H:%M:%S')
                asset_short = data['asset_id'][:20]
                print(f"[{timestamp}] {asset_short}... | Price: ${data['price']:.4f} | Prob: {data['price']*100:.1f}%")

    except KeyboardInterrupt:
        print("\n\n⏹  Stopped by user")
    finally:
        client.disconnect()


# ============================================================================
# MAIN MENU
# ============================================================================

if __name__ == '__main__':
    print("\n" + "="*70)
    print("PolyMarket Dispatcher - Client Workflow Examples")
    print("="*70)
    print("\nPrerequisite: Make sure the PolyMarket Dispatcher is running!")
    print("Run this in another terminal:")
    print("  python -m argus.polymarket")
    print("\n" + "="*70)

    print("\nSelect a workflow to demonstrate:")
    print("  1. Subscribe to specific asset ID")
    print("  2. Search and subscribe by keyword")
    print("  3. Browse events and select market")
    print("  4. Multi-asset monitoring")
    print("  5. Exit")

    choice = input("\nEnter choice (1-5): ").strip()

    if choice == '1':
        workflow_1_manual_asset_id()
    elif choice == '2':
        workflow_2_search_by_keyword()
    elif choice == '3':
        workflow_3_browse_and_select()
    elif choice == '4':
        workflow_4_advanced_multi_asset()
    elif choice == '5':
        print("Goodbye!")
    else:
        print("Invalid choice")
