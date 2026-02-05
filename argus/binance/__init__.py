import json
import uuid
import time
import socket
import platform
import threading
import traceback
from utils3 import runAsThread
from websocket import WebSocketApp
from argus.wireproxy.wrapper import start_proxy_aware_ws
from argus._argus_utils import throw_fuss, Introspective
from argus.protocol import transmit_mkt_data_with_protocol_2
from argus.binance._classes import (DepthUpdate, DepthStreamMessage, AggTradeMessage,
                                    AggTradeData, KlineEventData, KlineData, KlineMessage,
                                    Binance_CapitalComMKTDataLive, BookTicker)


class BinanceTypes:
    DEPTH_STREAM = 'depth_stream'  # THIS IS DIFFERENT FROM THE FULL DEPTH STREAM
    AGG_TRADE = 'agg_trade'
    KLINE = 'kline'
    BOOK_TICKER = 'book_ticker'


class AbstractBinanceType:
    """
    Abstract wrapper for Binance WebSocket message types.
    The only attribute directly accessible is 'idx' to identify the type.
    Everything else is taken from the 'obj' attribute.
    """

    def __init__(self, idx: str, obj):
        self.idx = idx
        self.obj = obj


class BinanceWssConfig:
    AUTO_DUMP = 'auto_dump'
    TOTAL_MESSAGE_STATISTICS = 'total_message_statistics'
    SHOW_ME_CHARTS = 'show_me_charts'


class BinanceWss:
    def __init__(self, configs=None):

        if configs is None:
            configs = {
                BinanceWssConfig.AUTO_DUMP: True,
                BinanceWssConfig.TOTAL_MESSAGE_STATISTICS: True,
                BinanceWssConfig.SHOW_ME_CHARTS: True,
            }

        self.endpoint = 'wss://stream.binance.com/stream'
        self.ws = WebSocketApp(
            url=self.endpoint,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

        self.semaphore = threading.Semaphore(0)
        self.init_websocket()

        self.callbacks = {}
        self.msgs = []
        self.stats_stamps = []
        self.configs = configs

        if platform.system() != 'Darwin':
            print("Show me charts disabled: not running on macOS")
            self.configs[BinanceWssConfig.SHOW_ME_CHARTS] = False

        self._dump_interval = 30
        self._statistics_interval = 10
        self._max_message_count = 5000

        self.auto_message_dumper()
        self.statistic_showcase()

        self.uuid = str(uuid.uuid4())
        self.message_seg_id = 0

        print('[BinanceWss] Initialized with UUID:', self.uuid)

    def unique_file_name(self, file_name, file_type):
        return '{}_{}-{}.{}'.format(file_name, self.uuid, self.message_seg_id, file_type)

    def rollover_message_segment(self):
        self.message_seg_id += 1
        self.msgs = []

    def init_websocket(self):
        self.semaphore = threading.Semaphore(0)
        self.run_ws_forever()
        self.semaphore.acquire()

    def _config_active(self, config_name: str) -> bool:
        return self.configs[config_name]

    @staticmethod
    def _craft_msg(symbol: str, auto_dump=True, method="SUBSCRIBE", idx=1) -> dict | str:
        _ = idx  # idx is unused but kept for compatibility
        symbol = symbol.lower()
        msg = {
            "method": method,
            "params": [
                symbol + "@aggTrade",
                symbol + "@depth@100ms",
                symbol + "@kline_1s",
                symbol + "@bookTicker"
            ],
            "id": method
        }
        if auto_dump:
            return json.dumps(msg)
        return msg

    # noinspection PyUnusedLocal
    def _on_open(self, ws):
        print("WebSocket connection opened.")
        self.semaphore.release()

    # noinspection PyUnusedLocal
    def _on_message(self, ws, message):
        try:
            self.stats_stamps.append(time.time())
            msg = json.loads(message)
            msg['received_at'] = time.time()
            self.msgs.append(msg)

            if len(self.msgs) > self._max_message_count:
                self.rollover_message_segment()

            message_type = msg.get('stream', None)
            if message_type is None:
                print('Malformed message received:', msg)
                return

            try:
                symbol, stream_type = message_type.split('@', 1)
            except ValueError:
                print('Malformed message received:', msg)
                return

            if stream_type == 'depth@100ms':
                msg = AbstractBinanceType(
                    idx=BinanceTypes.DEPTH_STREAM,
                    obj=DepthStreamMessage.from_dict(msg)
                )
            elif stream_type == 'aggTrade':
                msg = AbstractBinanceType(
                    idx=BinanceTypes.AGG_TRADE,
                    obj=AggTradeMessage.from_dict(msg)
                )
            elif stream_type == 'kline_1s':
                msg = AbstractBinanceType(
                    idx=BinanceTypes.KLINE,
                    obj=KlineMessage.from_dict(msg)
                )
            elif stream_type == 'bookTicker':
                msg = AbstractBinanceType(
                    idx=BinanceTypes.BOOK_TICKER,
                    obj=BookTicker.from_dict(msg)
                )
            elif '!miniTicker' in stream_type:
                # Currently ignoring miniTicker messages
                return
            elif 'arr@1000ms' in stream_type:
                # Currently ignoring arr@1000ms messages
                return
            else:
                print('Unknown message {} received: {}'.format(stream_type, str(msg)[:100] + '...'))
                return

            if symbol in self.callbacks:
                callback = self.callbacks[symbol]
                callback(msg)
            else:
                throw_fuss(
                    msg="No callback registered for symbol: {}".format(symbol),
                    notify=False,
                    boarder="<>"
                )
        except Exception as e:
            print("Error processing WebSocket message:", e)
            traceback.print_exc()

    # noinspection PyUnusedLocal
    def _on_error(self, ws, error):
        print("WebSocket error:", error)
        throw_fuss(
            msg=traceback.format_exc(),
            title="Binance WebSocket Error",
        )
        _ = self

    # noinspection PyUnusedLocal
    def _on_close(self, ws, close_status_code, close_msg):
        print("WebSocket connection closed:", close_status_code, close_msg)
        throw_fuss(
            msg="Binance WebSocket connection closed:\nCode: {}\nMessage: {}".format(close_status_code, close_msg),
            title="Binance WebSocket Closed",
        )

        print('Reinitializing WebSocket connection...')
        self.init_websocket()
        cb_copy = self.callbacks.copy()
        print('Current subscriptions to re-establish:')
        print(cb_copy)
        for symbol, callback in cb_copy.items():
            print('Re-establishing', symbol)
            self.subscribe(symbol, callback)

    def subscribe(self, symbol: str, callback):
        self.ws.send(self._craft_msg(symbol))
        self.callbacks[symbol.lower()] = callback

    def unsubscribe(self, symbol):
        msg = self._craft_msg(symbol, method="UNSUBSCRIBE", idx=312)

        if symbol.lower() in self.callbacks:
            del self.callbacks[symbol.lower()]
        self.ws.send(msg)

    @runAsThread
    def run_ws_forever(self):
        start_proxy_aware_ws(
            idx='BINANCE',
            websocket=self.ws
        )

    @runAsThread
    def auto_message_dumper(self):
        while True:
            time.sleep(self._dump_interval)
            if self._config_active(BinanceWssConfig.AUTO_DUMP):
                fname = self.unique_file_name('binance_wss_dump', 'json')
                try:
                    with open(fname, 'w') as f:
                        json.dump(self.msgs, f)
                    print('[AUTO-DUMP] Dumped {} messages to {}'.format(len(self.msgs), fname))
                except KeyboardInterrupt:
                    throw_fuss('WAIT A SECOND ATTEMPTING TO WRITE DUMP, AUTO-DUMP WILL STOP WHEN THIS IS COMPLETE',
                               notify=False)
                    with open(fname, 'w') as f:
                        json.dump(self.msgs, f)
                    throw_fuss('DUMP COMPLETE, AUTO-DUMP STOPPED', notify=False)
                    break

    @runAsThread
    def statistic_showcase(self):
        while True:
            time.sleep(self._statistics_interval)
            if self._config_active(BinanceWssConfig.TOTAL_MESSAGE_STATISTICS):
                now = time.time()
                cutoff = now - self._statistics_interval
                count = len([stamp for stamp in self.stats_stamps if stamp >= cutoff])
                print('[STATISTICS] Received {} messages in the last {} seconds (avg: {:.2f} msgs/sec)'.format(
                    count,
                    self._statistics_interval,
                    count / self._statistics_interval
                ))
                # Clean up old stamps
                self.stats_stamps = [stamp for stamp in self.stats_stamps if stamp >= cutoff]


class BinanceMKTDispatcher(Introspective):
    """
    Market data dispatcher for Binance, following the same pattern as IBKR's MKTDispatcher.

    This dispatcher:
    - Uses BinanceWss for WebSocket connections to Binance
    - Manages TCP client connections
    - Converts Binance market data to Protocol 2 format
    - Supports interactive mode via Introspective base class
    - Handles client subscriptions and automatically unsubscribes when clients disconnect
    """

    def __init__(self, host='localhost', port=9982):
        """
        Initialize the BinanceMKTDispatcher.

        Args:
            host (str): Host to bind TCP server to
            port (int): Port to bind TCP server to (default 9982 to avoid conflicts)
        """
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.bind((host, port))
        except OSError as e:
            print(f"[ERROR] Failed to bind socket to {host}:{port}: {e}")
            print("Please check if the port is already in use or if you have the necessary permissions.")
            raise
        self.clients = []
        self.symbol_to_clients = {}  # Maps symbol -> list of clients
        self.symbol_data_cache = {}  # Maps symbol -> Binance_CapitalComMKTDataLive

        # Initialize Binance WebSocket
        self.ws = BinanceWss()

        # Threading lock for client management
        self._thread_lock = threading.Lock()

        # Configuration options
        self._configs = {
            'Print data packets': False,
            'Show subscription changes': True,
            'Auto-unsubscribe disconnected clients': True,
        }

        self.host = host
        self.port = port

        # Start client listener and health check
        self._add_clients()
        self._check_clients_live()

        print(f'[BinanceMKTDispatcher] Initialized on {host}:{port}')
        print('[IMPORTANT] MODE = PROTOCOL_2')

    # noinspection DuplicatedCode
    def _modify_configs_interactive(self):
        """Modify the dispatcher configurations interactively."""
        print("Current configurations:")
        for key, value in self._configs.items():
            print(f"{key}: {value}")
        print("Enter the configuration you want to modify (or 'exit' to quit):")
        while True:
            choice = input("Configuration: ").strip()
            if choice.lower() == 'exit':
                break

            if choice in self._configs:
                new_value = input(f"Enter new value for {choice} (current: {self._configs[choice]}): ")
                if new_value.lower() == 'true':
                    self._configs[choice] = True
                elif new_value.lower() == 'false':
                    self._configs[choice] = False
                else:
                    # noinspection all
                    self._configs[choice] = new_value
                print(f"Updated {choice} to {self._configs[choice]}")
            else:
                print(f"Invalid configuration: {choice}")

    def _subscribe_to_symbol(self, symbol: str, client: socket.socket):
        """Subscribe to a symbol and add client to the subscription list."""
        symbol = symbol.upper()

        with self._thread_lock:
            if symbol not in self.symbol_to_clients:
                # First client for this symbol, subscribe to Binance WebSocket
                if self._configs['Show subscription changes']:
                    print(f"[SUBSCRIBE] New subscription to {symbol}")
                self.ws.subscribe(symbol, lambda msg: self._binance_callback(symbol, msg))
                self.symbol_to_clients[symbol] = []

            if client not in self.symbol_to_clients[symbol]:
                self.symbol_to_clients[symbol].append(client)
                if self._configs['Show subscription changes']:
                    print(
                        f"[CLIENT] Added client to {symbol} subscription (total: {len(self.symbol_to_clients[symbol])})")

    # noinspection all
    def _binance_callback(self, symbol: str, msg: AbstractBinanceType):
        """Callback function to handle Binance market data and convert to Protocol 2."""
        try:
            # Get or create market data cache for this symbol
            existing_data = self.symbol_data_cache.get(symbol, None)
            if msg.idx == BinanceTypes.BOOK_TICKER:
                # Book ticker update (best bid/ask)
                book_ticker: BookTicker = msg.obj
                market_data = Binance_CapitalComMKTDataLive.from_binance_book_ticker(
                    symbol, book_ticker, existing_data
                )
            else:
                # Other message types (kline, etc.) - skip for now
                return

            # Update cache
            self.symbol_data_cache[symbol] = market_data

            # Get clients subscribed to this symbol
            clients = self.symbol_to_clients.get(symbol, [])
            if not clients:
                return

            # Transmit to all clients using Protocol 2
            packet = transmit_mkt_data_with_protocol_2(market_data)

            if self._configs['Print data packets']:
                print(f"[TX {symbol}] {packet}")

            for client in clients:
                try:
                    client.sendall(packet)
                except (OSError, ConnectionResetError) as e:
                    # Client disconnected, will be cleaned up by _check_clients_live
                    if self._configs['Show subscription changes']:
                        print(f"[CLIENT] Error sending to client: {e}")

        except Exception as e:
            print(f"[ERROR] Error in Binance callback for {symbol}: {e}")
            import traceback
            traceback.print_exc()

    @runAsThread
    def _listen_to_client(self, client: socket.socket):
        """Listen to client commands for subscribing to symbols."""
        while True:
            try:
                data = client.recv(9999).decode()
                if not data:
                    break

                # Parse command: "add=BTCUSDT" or "subscribe=ETHUSDT"
                if 'add' in data or 'subscribe' in data:
                    parts = data.split('=')
                    if len(parts) == 2:
                        symbol = parts[1].strip().upper()
                        if self._configs['Show subscription changes']:
                            print(f"[CLIENT] Subscribing to {symbol}")
                        self._subscribe_to_symbol(symbol, client)
                    else:
                        print(f"[CLIENT] Invalid command format: {data}")

            except Exception as e:
                print(f"[CLIENT] Error receiving data from client: {e}")
                break

        # Client disconnected
        client.close()

    @runAsThread
    def _add_clients(self):
        """Accept new client connections."""
        while True:
            self.sock.listen()
            client, addr = self.sock.accept()
            print(f"[CLIENT] New connection from {addr}")
            self.clients.append(client)
            self._listen_to_client(client)

    @runAsThread
    def _check_clients_live(self):
        """Periodically check if clients are still connected and clean up disconnected ones."""
        while True:
            time.sleep(5)
            if not self._configs['Auto-unsubscribe disconnected clients']:
                continue

            try:
                with self._thread_lock:
                    # Check each symbol's client list
                    symbols_to_remove = []

                    for symbol, clients in self.symbol_to_clients.items():
                        clients_to_remove = []

                        for client in clients:
                            try:
                                # Send ping to check if client is alive
                                client.sendall(b'$')
                            except (OSError, ConnectionResetError):
                                # Client disconnected
                                clients_to_remove.append(client)
                                if self._configs['Show subscription changes']:
                                    print(f"[CLIENT] Disconnected client removed from {symbol}")

                        # Remove disconnected clients
                        for client in clients_to_remove:
                            try:
                                clients.remove(client)
                            except ValueError:
                                pass

                        # If no clients left, mark symbol for unsubscription
                        if not clients:
                            symbols_to_remove.append(symbol)

                    # Clean up symbols with no clients
                    for symbol in symbols_to_remove:
                        del self.symbol_to_clients[symbol]
                        self.ws.unsubscribe(symbol)
                        if self._configs['Show subscription changes']:
                            print(f"[UNSUBSCRIBE] No clients for {symbol}, cleaned up")

            except Exception as e:
                print(f"[ERROR] Error in _check_clients_live: {e}")
                import traceback
                traceback.print_exc()

    def interactive_mode(self):
        """Start interactive mode for managing the dispatcher."""
        functions = {
            'show_subscriptions': ('Show all active symbol subscriptions', self.show_subscriptions),
            'show_clients': ('Show all connected clients', self.show_clients),
            'modify_configs': ('Modify dispatcher configurations', self._modify_configs_interactive),
        }
        self._interactive_ui(functions)

    def show_subscriptions(self):
        """Display all active symbol subscriptions."""
        print("\n=== Active Subscriptions ===")
        if not self.symbol_to_clients:
            print("No active subscriptions")
        else:
            for symbol, clients in self.symbol_to_clients.items():
                print(f"{symbol}: {len(clients)} client(s)")
        print()

    def show_clients(self):
        """Display all connected clients."""
        print(f"\n=== Connected Clients ({len(self.clients)}) ===")
        for i, client in enumerate(self.clients, 1):
            try:
                addr = client.getpeername()
                print(f"{i}. {addr}")
            except (OSError, socket.error):
                print(f"{i}. <disconnected>")
        print()


if __name__ == '__main__':
    # noinspection all
    def highest_bid_ask_price_callback(msg: AbstractBinanceType):
        if msg.idx == BinanceTypes.BOOK_TICKER:
            ticker: BookTicker = msg.obj
            top_ask = ticker.a
            top_bid = ticker.b
            print('[{}] Top Bid: {:.2f}, Top Ask: {:.2f}'.format(ticker.s, top_bid, top_ask))


    binance_wss = BinanceWss()
    binance_wss.subscribe('BTCUSDT', lambda msg: highest_bid_ask_price_callback(msg))
    binance_wss.subscribe('ETHUSDT', lambda msg: highest_bid_ask_price_callback(msg))
    input('Press Enter to exit...\n')
