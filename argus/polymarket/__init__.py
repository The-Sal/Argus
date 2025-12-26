"""
Refreshed Polymarket Dispatcher based on the polymarket_direct module. For the old version
see https://github.com/The-Sal/Argus/tree/legacy/polymarket-dispatcher

This dispatcher provides Protocol 2 streaming support for Polymarket prediction markets,
following the standard Argus dispatcher architecture similar to Capital.com and Binance modules.
"""
import os
import json
import time
import socket
import logging
from typing import Dict, List, Optional, Callable
from utils3 import runAsThread, assertTypes
from utils3.networking.sockets import UDSServer
from argus.polymarket_direct import EnhancedPM
from argus.polymarket_direct._types import PolymarketEvent, Market
from argus.capital import DomainCache

logger = logging.getLogger(__name__)


class TransferPROTOCOL:
    """Protocol for transferring data between the server and clients."""
    # Packet Encoding Rules for version 1
    # Start packet
    # ~<data-length>|{data}
    # Where:
    #   data-length is the length of the data in bytes and is a 4-byte integer
    #   data is the actual data being sent, encoded as ascii bytes
    VERSION_1 = 1

    # Packet Encoding Rules for version 2
    # Start packet
    # ~<data-length><symbol-length>|{symbol}{data}L
    # Where:
    #   data-length is the length of the data in bytes and is a 4-byte integer
    #   symbol-length is the length of the symbol in bytes and is a 4-byte integer
    #   symbol is the actual symbol being sent, encoded as ascii bytes
    #   data is the actual data being sent, encoded as ascii bytes and ORDERED as:
    #   bid, bid_size, ask, ask_size, last, last_size, timestamp, python_timestamp
    #   L is a literal character to indicate this is the end of a packet and more importantly this is
    #   a version 2 packet.
    VERSION_2 = 2


CACHE = DomainCache('polymarket')


def encode_packet(data: bytes) -> bytes:
    """Encodes data as a packet for transmission."""
    data_length = len(data)
    packet_length_header = data_length.to_bytes(4, byteorder='big')
    return b'~' + packet_length_header + b'|' + data


def decode_packet(packet: bytes) -> bytes:
    """Decodes a packet into its data component."""
    if not packet.startswith(b'~'):
        raise ValueError("Invalid packet format")
    data_length = int.from_bytes(packet[1:5], byteorder='big')
    data = packet[6:6 + data_length]
    return data


def decode_multiple_packets(data: bytes) -> List[bytes]:
    """Decodes multiple packets from a byte stream."""
    packets = []
    offset = 0
    while offset < len(data):
        if data[offset:offset + 1] != b'~':
            break
        data_length = int.from_bytes(data[offset + 1:offset + 5], byteorder='big')
        packet = data[offset:offset + 6 + data_length]
        packets.append(decode_packet(packet))
        offset += 6 + data_length
    return packets


class PolymarketMKTDataLive:
    """Market Data Object for Polymarket prediction markets."""

    @assertTypes(types=[str, float, float, float, float, float, float, int],
                 auto_convert=True, class_method=True)
    def __init__(self, asset_id: str, price: float, price_change: float,
                 volume: float, liquidity: float, best_bid: float, best_ask: float,
                 timestamp: int = None):
        """Initializes the Market Data Object with asset_id and market data.

        Args:
            asset_id: The CLOB token ID (asset identifier)
            price: Current price (last trade price or mid price)
            price_change: Price change (24hr, 1hr, etc.)
            volume: Total volume
            liquidity: Market liquidity
            best_bid: Best bid price
            best_ask: Best ask price
            timestamp: Unix timestamp (milliseconds)
        """
        self.asset_id = asset_id
        self.price = price
        self.price_change = price_change
        self.volume = volume
        self.liquidity = liquidity
        self.best_bid = best_bid
        self.best_ask = best_ask
        self.timestamp = timestamp if timestamp is not None else int(time.time() * 1000)

    def transferable(self):
        """Returns a dictionary representation of the market data for transfer."""
        return {
            'object': 'MKTDataLive',
            'asset_id': self.asset_id,
            'price': self.price,
            'price_change': self.price_change,
            'volume': self.volume,
            'liquidity': self.liquidity,
            'best_bid': self.best_bid,
            'best_ask': self.best_ask,
            'timestamp': self.timestamp,
            'python_timestamp': time.time(),
        }

    def transferable_2(self, encode: bool = True) -> bytes | list[str]:
        """Returns Protocol 2 encoded data for transfer.

        Protocol 2 format: bid, bid_size, ask, ask_size, last, last_size, timestamp, python_timestamp
        For Polymarket we adapt this to: best_bid, liquidity, best_ask, volume, price, price_change, timestamp, python_timestamp
        """
        data = [
            str(self.best_bid),
            str(self.liquidity),
            str(self.best_ask),
            str(self.volume),
            str(self.price),
            str(self.price_change),
            str(self.timestamp),
            str(time.time())
        ]
        if encode:
            return ",".join(data).encode('ascii')
        else:
            return data

    @classmethod
    def from_protocol_2(cls, data: bytes):
        """Creates an instance of PolymarketMKTDataLive from protocol 2 encoded data."""
        _ = data[:4]  # packet_length_header

        packet_symbol_length_header = data[4:8]
        real_data = data[8:]
        symbol_length = int.from_bytes(packet_symbol_length_header, byteorder='big')
        asset_id = real_data[:symbol_length].decode('ascii')
        values = real_data[symbol_length:-1].decode('ascii').split(',')
        if len(values) != 8:
            raise ValueError("Invalid data length for protocol 2. Values: " + str(values))
        return cls(
            asset_id=asset_id,
            best_bid=float(values[0]),
            liquidity=float(values[1]),
            best_ask=float(values[2]),
            volume=float(values[3]),
            price=float(values[4]),
            price_change=float(values[5]),
            timestamp=int(float(values[6]))
        )


def transmit_mkt_data_with_protocol_2(mkt_data: PolymarketMKTDataLive) -> bytes:
    """Encodes market data as a Protocol 2 packet."""
    if not isinstance(mkt_data, PolymarketMKTDataLive):
        raise TypeError("mkt_data must be an instance of PolymarketMKTDataLive")

    asset_id_bytes = mkt_data.asset_id.encode('ascii')
    asset_id_length = len(asset_id_bytes)
    data = mkt_data.transferable_2(encode=True)

    total_length = asset_id_length + len(data) + 1  # +1 for 'L' terminator
    packet_length_header = total_length.to_bytes(4, byteorder='big')
    symbol_length_header = asset_id_length.to_bytes(4, byteorder='big')

    return b'~' + packet_length_header + symbol_length_header + b'|' + asset_id_bytes + data + b'L'


class SvrExport:
    """Base class for server export functionality."""

    def __init__(self, path='/tmp/argus_polymarket.sock'):
        self.path = path
        self.server = UDSServer(
            on_disconnect=lambda *args: None,
            path=path,
            on_recv=self._on_recv,
        )
        self.packets_read = 0
        self.client_list = []

    def _on_recv(self, client: socket.socket, address: tuple, data: bytes):
        """Handle Incoming data from a client. This method should be OVERRIDDEN by subclasses."""
        self.packets_read += 1
        self.client_list.append((client, address))
        return

    def transmit(self, some_data, protocol: int = TransferPROTOCOL.VERSION_1):
        """Transmits data to all connected clients using the specified protocol."""
        if protocol == TransferPROTOCOL.VERSION_1:
            self.transmit_mkt_data_with_protocol_1(some_data)
        elif protocol == TransferPROTOCOL.VERSION_2:
            if isinstance(some_data, PolymarketMKTDataLive):
                self.transmit_mkt_data_with_protocol_2(some_data)
            else:
                raise TypeError("some_data must be an instance of PolymarketMKTDataLive for protocol 2")
        else:
            raise ValueError(f"Unsupported protocol version: {protocol}")

    def transmit_mkt_data_with_protocol_1(self, json_data: dict):
        """Transmits data to all connected clients, encoded as a packet."""
        packet = encode_packet(json.dumps(json_data).encode('ascii'))
        for client, address in self.client_list:
            try:
                client.sendall(packet)
            except socket.error:
                logger.warning(f"Client {address} disconnected or error occurred. Removing from client list.")
                self.client_list.remove((client, address))
            except Exception as e:
                logger.error(f"Error sending data to client {client}: {e}")

    def transmit_mkt_data_with_protocol_2(self, mkt_data: PolymarketMKTDataLive):
        """Transmits market data to all connected clients using Protocol 2."""
        if not isinstance(mkt_data, PolymarketMKTDataLive):
            raise TypeError("mkt_data must be an instance of PolymarketMKTDataLive")
        packet = transmit_mkt_data_with_protocol_2(mkt_data)
        for client, address in self.client_list:
            try:
                client.sendall(packet)
            except socket.error:
                logger.warning(f"Client {address} disconnected or error occurred. Removing from client list.")
                self.client_list.remove((client, address))
            except Exception as e:
                logger.error(f"Error sending data to client {client}: {e}")

    @runAsThread
    def start_server(self):
        """Starts the server in a separate thread."""
        logger.info(f"Starting server on {self.path}...")
        self.server.start()  # this is blocking call, so it will run in a separate thread

    def stop_server(self):
        """Stops the server."""
        logger.info("Stopping server...")
        self.server.stop()
        logger.info("Server stopped.")


class PolyDispatcher(SvrExport):
    """Market Data Dispatcher for Polymarket prediction markets.

    This dispatcher provides Protocol 2 streaming support for Polymarket,
    backed by the EnhancedPM client from polymarket_direct module.
    """

    def __init__(self, path='/tmp/argus_polymarket.sock',
                 private_key: Optional[str] = None,
                 proxy_funder: Optional[str] = None,
                 dry_mode: bool = True,
                 max_socket_retries: int = 100):
        """Initializes the Polymarket Dispatcher.

        Args:
            path: Unix domain socket path for the server
            private_key: Polymarket private key (optional, only for trading)
            proxy_funder: Polymarket proxy funder address (optional, only for trading)
            dry_mode: If True, runs in read-only mode without credentials
            max_socket_retries: Maximum WebSocket reconnection attempts
        """
        super().__init__(path=path)

        # Initialize EnhancedPM client
        self.pm_client = EnhancedPM(
            private_key=private_key,
            proxy_funder=proxy_funder,
            dry_mode=dry_mode,
            max_socket_retries=max_socket_retries
        )

        # Start WebSocket connection
        self.pm_client.start_market_ws()
        logger.info("Waiting for Polymarket WebSocket connection...")
        self.pm_client.market_open_semaphore.acquire()
        logger.info("Polymarket WebSocket connected!")

        # Tracking
        self.asset_subscriptions: Dict[str, bool] = {}  # asset_id -> subscribed status
        self.market_cache: Dict[str, Market] = {}  # market_id -> Market object
        self.asset_to_market: Dict[str, str] = {}  # asset_id -> market_id mapping

    @CACHE.cache_decorator('fetch_events')
    def fetch_events(self, offset: int = 0, limit: int = 20) -> List[PolymarketEvent]:
        """Fetches active Polymarket events.

        Args:
            offset: Offset for pagination
            limit: Number of events to fetch

        Returns:
            List of PolymarketEvent objects
        """
        return self.pm_client.fetch_events(offset=offset, limit=limit)

    def find_market_by_keyword(self, keyword: str, limit: int = 100) -> Optional[Market]:
        """Finds a market by keyword search in question/title.

        Args:
            keyword: Search keyword
            limit: Maximum events to search

        Returns:
            First matching Market object or None
        """
        events = self.fetch_events(limit=limit)
        for event in events:
            for market in event.markets:
                if keyword.lower() in market.question.lower() if market.question else False:
                    return market
        return None

    def stream_asset(self, asset_id: str, market_metadata: Optional[Market] = None):
        """Streams market data for a specific asset ID.

        Args:
            asset_id: CLOB token ID to subscribe to
            market_metadata: Optional Market object for caching
        """
        if asset_id in self.asset_subscriptions:
            if self.asset_subscriptions[asset_id]:
                logger.warning(f"Already streaming data for asset '{asset_id}'.")
                return

        self.asset_subscriptions[asset_id] = True

        # Cache market metadata if provided
        if market_metadata:
            self.market_cache[market_metadata.id] = market_metadata
            self.asset_to_market[asset_id] = market_metadata.id

        # Subscribe with callback
        self.pm_client.subscribe_to_market_data(
            asset_ids=[asset_id],
            callback=self._on_market_data_received
        )
        logger.info(f"Subscribed to asset: {asset_id}")

    def stream_market(self, market: Market):
        """Streams all asset IDs associated with a market.

        Args:
            market: Market object to stream
        """
        if not market.clobTokenIds:
            logger.error(f"Market {market.id} has no CLOB token IDs")
            return

        # Parse CLOB token IDs
        if isinstance(market.clobTokenIds, str):
            asset_ids = market.clobTokenIds.split(',')
        elif isinstance(market.clobTokenIds, list):
            asset_ids = market.clobTokenIds
        else:
            logger.error(f"Invalid clobTokenIds type: {type(market.clobTokenIds)}")
            return

        # Subscribe to each asset
        for asset_id in asset_ids:
            self.stream_asset(asset_id.strip(), market_metadata=market)

    def _on_market_data_received(self, data: dict):
        """Handles incoming market data from Polymarket WebSocket.

        Args:
            data: Price change data from WebSocket
                  Format: {'asset_id': str, 'price': str, 'timestamp': int}
        """
        asset_id = data.get('asset_id')
        price = float(data.get('price', 0.0))
        timestamp = data.get('timestamp', int(time.time() * 1000))

        # Try to get market metadata from cache
        market_id = self.asset_to_market.get(asset_id)
        market = self.market_cache.get(market_id) if market_id else None

        # Extract additional data from cached market if available
        volume = float(market.volume) if market and market.volume else 0.0
        liquidity = float(market.liquidity) if market and market.liquidity else 0.0
        best_bid = float(market.bestBid) if market and market.bestBid else 0.0
        best_ask = float(market.bestAsk) if market and market.bestAsk else 0.0
        price_change = float(market.oneDayPriceChange) if market and market.oneDayPriceChange else 0.0

        # Create market data object
        mkt_data = PolymarketMKTDataLive(
            asset_id=asset_id,
            price=price,
            price_change=price_change,
            volume=volume,
            liquidity=liquidity,
            best_bid=best_bid,
            best_ask=best_ask,
            timestamp=timestamp
        )

        # Transmit using Protocol 2
        self.transmit(mkt_data, protocol=TransferPROTOCOL.VERSION_2)

    def _on_recv(self, client: socket.socket, address: tuple, data: bytes):
        """Handles incoming data from a client."""
        logger.info(f"Received data from {address}: {data}")
        super()._on_recv(client, address, data)
        decoded_datas = decode_multiple_packets(data)
        logger.info(f"Decoded {len(decoded_datas)} packets from {address}.")
        for decoded_data in decoded_datas:
            if not decoded_data:
                logger.warning(f"Received empty or invalid packet from {address}.")
                return

            # Sample decoded_data structure:
            # {'action': 'fetch_events', 'offset': 0, 'limit': 20}
            # {'action': 'stream_asset', 'asset_id': '123456'}
            # {'action': 'stream_market_by_keyword', 'keyword': 'Trump'}
            try:
                data_dict = json.loads(decoded_data.decode('ascii'))
                self.handle_client_request(data_dict, client)
            except json.JSONDecodeError as e:
                logger.error(f"Error decoding JSON from {address}: {e}")

    def handle_client_request(self, data: dict, client: socket.socket):
        """Handles client requests based on the action specified in the data.

        Supported actions:
            - fetch_events: Fetch active events
            - stream_asset: Subscribe to asset updates
            - stream_market_by_keyword: Find and subscribe to market by keyword
            - unsubscribe_asset: Unsubscribe from asset updates
        """
        action = data.get('action')

        if action == 'fetch_events':
            offset = data.get('offset', 0)
            limit = data.get('limit', 20)
            try:
                events = self.fetch_events(offset=offset, limit=limit)
                response = {
                    'status': 'success',
                    'data': [
                        {
                            'id': e.id,
                            'title': e.title,
                            'markets': len(e.markets) if e.markets else 0
                        }
                        for e in events
                    ]
                }
            except Exception as e:
                logger.error(f"Error fetching events: {e}")
                response = {
                    'status': 'error',
                    'message': str(e)
                }

        elif action == 'stream_asset':
            asset_id = data.get('asset_id')
            if asset_id:
                try:
                    self.stream_asset(asset_id)
                    response = {
                        'status': 'success',
                        'message': f"Started streaming asset '{asset_id}'"
                    }
                except Exception as e:
                    logger.error(f"Error streaming asset: {e}")
                    response = {
                        'status': 'error',
                        'message': str(e)
                    }
            else:
                response = {
                    'status': 'error',
                    'message': 'No asset_id provided'
                }

        elif action == 'stream_market_by_keyword':
            keyword = data.get('keyword')
            if keyword:
                try:
                    market = self.find_market_by_keyword(keyword)
                    if market:
                        self.stream_market(market)
                        response = {
                            'status': 'success',
                            'message': f"Started streaming market: {market.question}"
                        }
                    else:
                        response = {
                            'status': 'error',
                            'message': f"No market found for keyword '{keyword}'"
                        }
                except Exception as e:
                    logger.error(f"Error streaming market: {e}")
                    response = {
                        'status': 'error',
                        'message': str(e)
                    }
            else:
                response = {
                    'status': 'error',
                    'message': 'No keyword provided'
                }

        elif action == 'unsubscribe_asset':
            asset_id = data.get('asset_id')
            if asset_id:
                try:
                    self.pm_client.unsubscribe_from_market_data([asset_id])
                    self.asset_subscriptions[asset_id] = False
                    response = {
                        'status': 'success',
                        'message': f"Unsubscribed from asset '{asset_id}'"
                    }
                except Exception as e:
                    logger.error(f"Error unsubscribing: {e}")
                    response = {
                        'status': 'error',
                        'message': str(e)
                    }
            else:
                response = {
                    'status': 'error',
                    'message': 'No asset_id provided'
                }

        else:
            response = {
                'status': 'error',
                'message': f"Unknown action: {action}"
            }

        # Send response back to client
        try:
            packet = encode_packet(json.dumps(response).encode('ascii'))
            client.sendall(packet)
        except Exception as e:
            logger.error(f"Error sending response to client: {e}")

    def interactive_mode(self):
        """Start interactive mode for managing the dispatcher."""
        print("\n" + "=" * 60)
        print("Polymarket Dispatcher - Interactive Mode")
        print("=" * 60 + "\n")

        while True:
            print("\nAvailable commands:")
            print("  1. Show active subscriptions")
            print("  2. Show connected clients")
            print("  3. Fetch events")
            print("  4. Stream asset by ID")
            print("  5. Stream market by keyword")
            print("  6. Unsubscribe from asset")
            print("  7. Exit")

            try:
                choice = input("\nEnter command number: ").strip()

                if choice == '1':
                    self.show_subscriptions()
                elif choice == '2':
                    self.show_clients()
                elif choice == '3':
                    self.interactive_fetch_events()
                elif choice == '4':
                    self.interactive_stream_asset()
                elif choice == '5':
                    self.interactive_stream_market()
                elif choice == '6':
                    self.interactive_unsubscribe()
                elif choice == '7':
                    print("\nExiting interactive mode...")
                    break
                else:
                    print("Invalid choice. Please try again.")

            except KeyboardInterrupt:
                print("\n\nExiting interactive mode...")
                break
            except Exception as e:
                logger.error(f"Error in interactive mode: {e}")
                print(f"Error: {e}")

    def show_subscriptions(self):
        """Display all active asset subscriptions."""
        print("\n" + "=" * 60)
        print("Active Subscriptions")
        print("=" * 60)
        if not self.asset_subscriptions:
            print("No active subscriptions")
        else:
            active = {k: v for k, v in self.asset_subscriptions.items() if v}
            if not active:
                print("No active subscriptions")
            else:
                for asset_id, _ in active.items():
                    market_id = self.asset_to_market.get(asset_id)
                    market = self.market_cache.get(market_id) if market_id else None
                    if market:
                        print(f"  Asset: {asset_id} | Market: {market.question[:50]}...")
                    else:
                        print(f"  Asset: {asset_id}")
        print()

    def show_clients(self):
        """Display all connected clients."""
        print("\n" + "=" * 60)
        print(f"Connected Clients ({len(self.client_list)})")
        print("=" * 60)
        for i, (client, addr) in enumerate(self.client_list, 1):
            try:
                print(f"  {i}. {addr}")
            except Exception:
                print(f"  {i}. <error getting address>")
        print()

    def interactive_fetch_events(self):
        """Interactive event fetching."""
        try:
            limit = input("Number of events to fetch (default: 10): ").strip()
            limit = int(limit) if limit else 10

            print(f"\nFetching {limit} events...")
            events = self.fetch_events(limit=limit)

            print("\n" + "=" * 60)
            print(f"Fetched {len(events)} events")
            print("=" * 60)
            for i, event in enumerate(events, 1):
                print(f"\n{i}. {event.title}")
                if event.markets:
                    print(f"   Markets: {len(event.markets)}")
                    for j, market in enumerate(event.markets[:3], 1):  # Show first 3 markets
                        print(f"     {j}. {market.question[:60]}...")
                if event.volume:
                    print(f"   Volume: ${event.volume:,.2f}")
        except Exception as e:
            print(f"Error fetching events: {e}")

    def interactive_stream_asset(self):
        """Interactive asset streaming."""
        asset_id = input("Enter asset ID to stream: ").strip()
        if asset_id:
            try:
                self.stream_asset(asset_id)
                print(f"Started streaming asset: {asset_id}")
            except Exception as e:
                print(f"Error: {e}")
        else:
            print("No asset ID provided")

    def interactive_stream_market(self):
        """Interactive market streaming by keyword."""
        keyword = input("Enter keyword to search: ").strip()
        if keyword:
            try:
                print(f"Searching for markets with keyword '{keyword}'...")
                market = self.find_market_by_keyword(keyword)
                if market:
                    print(f"\nFound: {market.question}")
                    confirm = input("Stream this market? (y/n): ").strip().lower()
                    if confirm == 'y':
                        self.stream_market(market)
                        print(f"Started streaming market")
                else:
                    print(f"No market found for keyword '{keyword}'")
            except Exception as e:
                print(f"Error: {e}")
        else:
            print("No keyword provided")

    def interactive_unsubscribe(self):
        """Interactive unsubscribe from asset."""
        asset_id = input("Enter asset ID to unsubscribe: ").strip()
        if asset_id:
            try:
                self.pm_client.unsubscribe_from_market_data([asset_id])
                self.asset_subscriptions[asset_id] = False
                print(f"Unsubscribed from asset: {asset_id}")
            except Exception as e:
                print(f"Error: {e}")
        else:
            print("No asset ID provided")


if __name__ == '__main__':
    # Example usage
    logging.basicConfig(level=logging.INFO)

    dispatcher = PolyDispatcher(
        path='/tmp/argus_polymarket.sock',
        dry_mode=True
    )

    # Start server
    dispatcher.start_server()

    # Start interactive mode
    try:
        dispatcher.interactive_mode()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        dispatcher.stop_server()
