"""
Binance Market Data Dispatcher

This module provides a market data dispatcher for Binance, similar to the IB MKTDispatcher.
It uses python-binance for WebSocket connections and follows Protocol 2 for data transmission.
"""
import os
import json
import time
import socket
import logging
import threading
import traceback
from typing import Dict, List, Callable, Optional
from binance import ThreadedWebsocketManager
from binance.client import Client
from utils3 import runAsThread
from argus.capital import transmit_mkt_data_with_protocol_2, CapitalComMKTDataLive

# Enable logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class BinanceError(Exception):
    """Base exception for Binance-related errors."""
    pass


class BinanceMarketData:
    """
    Market data object for Binance.
    Normalizes Binance ticker data into a standard format.
    """
    def __init__(self, symbol: str, bid: float, bid_qty: float, ask: float,
                 ask_qty: float, last: float, last_qty: float, timestamp: int = None):
        self.symbol = symbol
        self.bid = bid
        self.bid_qty = bid_qty
        self.ask = ask
        self.ask_qty = ask_qty
        self.last = last
        self.last_qty = last_qty
        self.timestamp = timestamp or int(time.time() * 1000)

    def to_capital_com_format(self) -> CapitalComMKTDataLive:
        """Convert to CapitalComMKTDataLive for Protocol 2 transmission."""
        return CapitalComMKTDataLive(
            symbol=self.symbol,
            bid=self.bid,
            bid_size=self.bid_qty,
            ask=self.ask,
            ask_size=self.ask_qty,
            last=self.last,
            last_size=self.last_qty,
            timestamp=self.timestamp
        )

    def __repr__(self):
        return (f"BinanceMarketData(symbol={self.symbol}, bid={self.bid}, "
                f"ask={self.ask}, last={self.last})")


class BinanceWss:
    """
    WebSocket manager for Binance using python-binance ThreadedWebsocketManager.
    Handles subscription management and data streaming.
    """
    def __init__(self, api_key: str = None, api_secret: str = None, testnet: bool = False):
        """
        Initialize Binance WebSocket manager.

        Args:
            api_key: Binance API key (optional for public data)
            api_secret: Binance API secret (optional for public data)
            testnet: Use testnet instead of production
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet

        # Check connectivity to Binance before initializing
        if not testnet:
            self._check_binance_connectivity()

        # Initialize client (for symbol validation if needed)
        if api_key and api_secret:
            self.client = Client(api_key, api_secret, testnet=testnet)
        else:
            self.client = None

        # ThreadedWebsocketManager for handling streams
        # For public streams (market data), no API credentials are needed
        # Only pass credentials if they're actually provided
        if api_key and api_secret:
            self.twm = ThreadedWebsocketManager(api_key=api_key, api_secret=api_secret, testnet=testnet)
        else:
            # Public streams only - no authentication needed
            if testnet:
                self.twm = ThreadedWebsocketManager(testnet=testnet)
            else:
                self.twm = ThreadedWebsocketManager()

        # Track subscriptions: symbol -> (stream_name, callback)
        self.subscriptions: Dict[str, tuple] = {}

        # Track active streams
        self.active_streams: Dict[str, str] = {}  # stream_name -> connection_key

        # Lock for thread-safe operations
        self.lock = threading.Lock()

        # Running flag
        self.running = False

        logger.info(f"Initialized BinanceWss (testnet={testnet})")

    def _check_binance_connectivity(self):
        """
        Check if we can reach Binance production endpoints.
        Raises BinanceError if connectivity fails.
        """
        import socket as sock

        host = 'stream.binance.com'
        port = 9443
        timeout = 5

        logger.info(f"Checking connectivity to {host}:{port}...")

        try:
            # Try to establish a TCP connection
            test_socket = sock.socket(sock.AF_INET, sock.SOCK_STREAM)
            test_socket.settimeout(timeout)
            test_socket.connect((host, port))
            test_socket.close()
            logger.info(f"Successfully connected to {host}:{port}")
        except sock.timeout:
            raise BinanceError(
                f"Connection to {host}:{port} timed out after {timeout}s.\n"
                f"Binance production endpoint is unreachable from your network.\n\n"
                f"Possible causes:\n"
                f"  - Firewall blocking cryptocurrency exchanges\n"
                f"  - ISP blocking Binance\n"
                f"  - Regional restrictions\n"
                f"  - Network connectivity issues\n\n"
                f"Solutions:\n"
                f"  1. Use testnet: python runtime.py binance --testnet\n"
                f"  2. Try a different network (mobile hotspot, VPN)\n"
                f"  3. Check firewall settings\n"
                f"  4. Contact your network administrator"
            )
        except sock.gaierror as e:
            raise BinanceError(
                f"Cannot resolve hostname {host}: {e}\n"
                f"DNS resolution failed. Check your internet connection."
            )
        except ConnectionRefusedError:
            raise BinanceError(
                f"Connection to {host}:{port} was refused.\n"
                f"Binance endpoint is actively blocking your connection."
            )
        except Exception as e:
            raise BinanceError(
                f"Failed to connect to {host}:{port}: {e}\n\n"
                f"Cannot reach Binance production endpoint.\n"
                f"Try using testnet: python runtime.py binance --testnet"
            )

    def start(self):
        """Start the WebSocket manager."""
        if not self.running:
            self.twm.start()
            self.running = True
            logger.info("BinanceWss started")

    def stop(self):
        """Stop the WebSocket manager and close all streams."""
        if self.running:
            self.twm.stop()
            self.running = False
            logger.info("BinanceWss stopped")

    def subscribe_ticker(self, symbol: str, callback: Callable):
        """
        Subscribe to real-time ticker data for a symbol.

        Args:
            symbol: Trading pair symbol (e.g., 'BTCUSDT')
            callback: Function to call when data is received
        """
        with self.lock:
            symbol = symbol.upper()

            if symbol in self.subscriptions:
                logger.warning(f"Already subscribed to {symbol}")
                return

            # Create a wrapper callback to normalize the data
            def ticker_callback(msg):
                if msg['e'] == 'error':
                    logger.error(f"WebSocket error for {symbol}: {msg}")
                    return

                try:
                    # Parse Binance ticker data
                    # Binance ticker format: https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams#individual-symbol-ticker-streams
                    market_data = BinanceMarketData(
                        symbol=msg['s'],
                        bid=float(msg['b']),
                        bid_qty=float(msg['B']),
                        ask=float(msg['a']),
                        ask_qty=float(msg['A']),
                        last=float(msg['c']),
                        last_qty=float(msg['Q']),
                        timestamp=msg['E']  # Event time
                    )
                    callback(market_data)
                except Exception as e:
                    logger.error(f"Error parsing ticker data for {symbol}: {e}")
                    traceback.print_exc()

            # Start the ticker stream
            stream_name = f"{symbol.lower()}@ticker"
            conn_key = self.twm.start_symbol_ticker_socket(
                callback=ticker_callback,
                symbol=symbol
            )

            self.subscriptions[symbol] = (stream_name, callback)
            self.active_streams[stream_name] = conn_key

            logger.info(f"Subscribed to ticker for {symbol}")

    def unsubscribe_ticker(self, symbol: str):
        """
        Unsubscribe from ticker data for a symbol.

        Args:
            symbol: Trading pair symbol
        """
        with self.lock:
            symbol = symbol.upper()

            if symbol not in self.subscriptions:
                logger.warning(f"Not subscribed to {symbol}")
                return

            stream_name, _ = self.subscriptions[symbol]

            if stream_name in self.active_streams:
                conn_key = self.active_streams[stream_name]
                self.twm.stop_socket(conn_key)
                del self.active_streams[stream_name]

            del self.subscriptions[symbol]
            logger.info(f"Unsubscribed from {symbol}")

    def get_subscribed_symbols(self) -> List[str]:
        """Get list of currently subscribed symbols."""
        with self.lock:
            return list(self.subscriptions.keys())


class MKTDispatcher:
    """
    Market Data Dispatcher for Binance.

    Manages client connections via TCP and streams Binance market data
    using Protocol 2 format (compatible with existing clients).
    """
    def __init__(self, host: str = 'localhost', port: int = 9974,
                 api_key: str = None, api_secret: str = None, testnet: bool = False,
                 checkpoint_url: str = None):
        """
        Initialize the Binance MKTDispatcher.

        Args:
            host: Host to bind TCP server
            port: Port to bind TCP server
            api_key: Binance API key (optional for public data)
            api_secret: Binance API secret (optional for public data)
            testnet: Use Binance testnet
            checkpoint_url: Optional URL for progress checkpoints
        """
        self.host = host
        self.port = port
        self.checkpoint_url = checkpoint_url

        # Initialize WebSocket manager
        self.ws = BinanceWss(api_key=api_key, api_secret=api_secret, testnet=testnet)

        # TCP server socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))

        # Client management
        self.clients: List[socket.socket] = []
        self.symbol_to_clients: Dict[str, List[socket.socket]] = {}

        # Thread lock for client operations
        self.client_lock = threading.Lock()

        # Configuration
        self.configs = {
            'Print data packets': False,
            'Show client messages': True,
            'Show live stream': False,
        }

        # Live stream monitoring
        self.live_stream_symbol = None

        logger.info(f"MKTDispatcher initialized on {host}:{port}")
        self._checkpoint("MKTDispatcher.__init__", "complete")

    def _checkpoint(self, task_name: str, status: str):
        """Send checkpoint notification."""
        if not self.checkpoint_url:
            return
        try:
            import requests
            requests.post(
                self.checkpoint_url,
                json={"task_name": task_name, "status": status},
                timeout=5
            )
        except Exception as e:
            logger.debug(f"Checkpoint notification failed: {e}")

    def start(self):
        """Start the dispatcher: WebSocket manager and client listener."""
        self._checkpoint("MKTDispatcher.start", "start")

        # Start WebSocket manager
        self.ws.start()

        # Start client listener
        self._add_clients()

        # Start client health checker
        self._check_clients_live()

        logger.info(f"MKTDispatcher running on {self.host}:{self.port}")
        self._checkpoint("MKTDispatcher.start", "complete")

    @runAsThread
    def _add_clients(self):
        """Listen for incoming client connections."""
        while True:
            self.sock.listen()
            client, addr = self.sock.accept()
            logger.info(f"Client connected from {addr}")

            with self.client_lock:
                self.clients.append(client)

            self._listen_to_client(client)

    @runAsThread
    def _listen_to_client(self, client: socket.socket):
        """
        Listen to client requests and handle subscriptions.

        Protocol:
            - "add=BTCUSDT" - Subscribe to BTCUSDT ticker
            - "remove=BTCUSDT" - Unsubscribe from BTCUSDT ticker
        """
        while True:
            try:
                data = client.recv(4096).decode('utf-8').strip()
                if not data:
                    break

                if self.configs['Show client messages']:
                    logger.info(f"Client request: {data}")

                if data.startswith('add='):
                    symbol = data.split('=', 1)[1].strip().upper()
                    self._add_symbol(symbol, client)

                elif data.startswith('remove='):
                    symbol = data.split('=', 1)[1].strip().upper()
                    self._remove_symbol(symbol, client)

            except Exception as e:
                logger.error(f"Error handling client request: {e}")
                break

        # Client disconnected
        self._cleanup_client(client)

    def _add_symbol(self, symbol: str, client: socket.socket):
        """Add a symbol subscription for a client."""
        self._checkpoint(f"MKTDispatcher.add_symbol({symbol})", "start")

        with self.client_lock:
            if symbol not in self.symbol_to_clients:
                # First client for this symbol - subscribe to WebSocket
                def callback(market_data: BinanceMarketData):
                    self._broadcast_market_data(symbol, market_data)

                try:
                    self.ws.subscribe_ticker(symbol, callback)
                    self.symbol_to_clients[symbol] = [client]
                    logger.info(f"Subscribed to {symbol} for client")
                except Exception as e:
                    logger.error(f"Failed to subscribe to {symbol}: {e}")
                    client.sendall(f"ERROR: Failed to subscribe to {symbol}".encode())
                    return
            else:
                # Already subscribed, just add client to list
                if client not in self.symbol_to_clients[symbol]:
                    self.symbol_to_clients[symbol].append(client)
                    logger.info(f"Added client to existing {symbol} subscription")

        self._checkpoint(f"MKTDispatcher.add_symbol({symbol})", "complete")

    def _remove_symbol(self, symbol: str, client: socket.socket):
        """Remove a symbol subscription for a client."""
        with self.client_lock:
            if symbol in self.symbol_to_clients:
                if client in self.symbol_to_clients[symbol]:
                    self.symbol_to_clients[symbol].remove(client)

                    # If no more clients, unsubscribe from WebSocket
                    if not self.symbol_to_clients[symbol]:
                        self.ws.unsubscribe_ticker(symbol)
                        del self.symbol_to_clients[symbol]
                        logger.info(f"Unsubscribed from {symbol} (no more clients)")

    def _broadcast_market_data(self, symbol: str, market_data: BinanceMarketData):
        """Broadcast market data to all subscribed clients using Protocol 2."""
        with self.client_lock:
            clients = self.symbol_to_clients.get(symbol, [])

            if not clients:
                return

            # Convert to Protocol 2 format
            capital_data = market_data.to_capital_com_format()
            packet = transmit_mkt_data_with_protocol_2(capital_data)

            if self.configs['Print data packets']:
                logger.info(f"Broadcasting {symbol}: {market_data}")

            # Live stream display
            if self.configs['Show live stream'] and symbol == self.live_stream_symbol:
                print(f"\r[LIVE] {symbol}: Bid={market_data.bid:.8f} Ask={market_data.ask:.8f} Last={market_data.last:.8f}", end='', flush=True)

            # Send to all clients
            for client in clients[:]:  # Copy list to avoid modification during iteration
                try:
                    client.sendall(packet)
                except (BrokenPipeError, ConnectionResetError, OSError) as e:
                    logger.warning(f"Client disconnected during broadcast: {e}")
                    self._cleanup_client(client)

    def _cleanup_client(self, client: socket.socket):
        """Remove a client and clean up its subscriptions."""
        with self.client_lock:
            if client in self.clients:
                self.clients.remove(client)

            # Remove from all symbol subscriptions
            for symbol in list(self.symbol_to_clients.keys()):
                if client in self.symbol_to_clients[symbol]:
                    self.symbol_to_clients[symbol].remove(client)

                    # Unsubscribe if no more clients
                    if not self.symbol_to_clients[symbol]:
                        self.ws.unsubscribe_ticker(symbol)
                        del self.symbol_to_clients[symbol]

        try:
            client.close()
        except:
            pass

    @runAsThread
    def _check_clients_live(self):
        """Periodically check if clients are still connected."""
        while True:
            time.sleep(5)

            with self.client_lock:
                for client in self.clients[:]:
                    try:
                        client.sendall(b'$')  # Ping
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        logger.info("Detected dead client, cleaning up")
                        self._cleanup_client(client)

    def interactive_mode(self):
        """Interactive mode for debugging and monitoring."""
        print("\nBinance MKTDispatcher Interactive Mode")
        print("=" * 50)

        # Create a fake socket for manual subscriptions
        # The FakeSocket receives Protocol 2 bytes from _broadcast_market_data
        # We don't need to do anything with it - just keep the subscription alive
        from argus.ib._ib_utils import FakeSocket

        def manual_callback(data):
            # FakeSocket receives Protocol 2 bytes - just ignore it
            # The data has already been broadcast by _broadcast_market_data
            pass

        manual_socket = FakeSocket(callback=manual_callback)
        manual_socket.idx = 'manual'

        while True:
            if self.configs['Show live stream']:
                print()  # New line after live stream display

            print("\nOptions:")
            print("1. Show subscribed symbols")
            print("2. Show connected clients")
            print("3. Toggle packet printing")
            print("4. Add symbol manually")
            print("5. Remove symbol manually")
            print("6. Toggle live stream display")
            print("0. Exit")

            choice = input("\nSelect option: ").strip()

            if choice == '1':
                symbols = self.ws.get_subscribed_symbols()
                print(f"\nSubscribed symbols ({len(symbols)}):")
                for symbol in symbols:
                    num_clients = len(self.symbol_to_clients.get(symbol, []))
                    print(f"  - {symbol} ({num_clients} clients)")

            elif choice == '2':
                print(f"\nConnected clients: {len(self.clients)}")

            elif choice == '3':
                self.configs['Print data packets'] = not self.configs['Print data packets']
                print(f"Packet printing: {self.configs['Print data packets']}")

            elif choice == '4':
                symbol = input("Enter symbol to add (e.g., BTCUSDT): ").strip().upper()
                if symbol:
                    try:
                        self._add_symbol(symbol, manual_socket)
                        print(f"Successfully subscribed to {symbol}")
                    except Exception as e:
                        print(f"Error subscribing to {symbol}: {e}")

            elif choice == '5':
                symbol = input("Enter symbol to remove (e.g., BTCUSDT): ").strip().upper()
                if symbol:
                    try:
                        self._remove_symbol(symbol, manual_socket)
                        print(f"Successfully unsubscribed from {symbol}")
                    except Exception as e:
                        print(f"Error unsubscribing from {symbol}: {e}")

            elif choice == '6':
                if self.configs['Show live stream']:
                    # Turn off
                    self.configs['Show live stream'] = False
                    self.live_stream_symbol = None
                    print("Live stream display: OFF")
                else:
                    # Turn on - ask for symbol
                    symbol = input("Enter symbol to display (e.g., BTCUSDT): ").strip().upper()
                    if symbol:
                        # Subscribe if not already subscribed
                        if symbol not in self.symbol_to_clients:
                            try:
                                self._add_symbol(symbol, manual_socket)
                            except Exception as e:
                                print(f"Error subscribing to {symbol}: {e}")
                                continue

                        self.live_stream_symbol = symbol
                        self.configs['Show live stream'] = True
                        print(f"Live stream display: ON for {symbol}")
                        print("(Press Enter to stop live stream)")

            elif choice == '0':
                break


if __name__ == '__main__':
    # Example usage
    api_key = os.getenv('BINANCE_API_KEY')
    api_secret = os.getenv('BINANCE_API_SECRET')

    dispatcher = MKTDispatcher(
        host='localhost',
        port=9974,
        api_key=api_key,
        api_secret=api_secret,
        testnet=False
    )

    dispatcher.start()
    dispatcher.interactive_mode()
