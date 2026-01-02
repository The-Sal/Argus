"""
Refreshed Polymarket Dispatcher based on the polymarket_direct module. For the old version
see https://github.com/The-Sal/Argus/tree/legacy/polymarket-dispatcher

The below code removes the entire old stub with a new implementation based on polymarket_direct.
In future version this documentation referencing the old dispatcher will be removed.
"""
import json
import time
import socket
import threading
import traceback
from typing import Dict, List, Optional
from utils3 import runAsThread
from argus._argus_utils import throw_fuss, Introspective
from argus import polymarket_direct


class PolymarketDispatcher(Introspective):
    """
    A Polymarket Dispatcher with the following features on the API:
    – No Ping Required
    – Multiplexing supported
    – No Protocol 2 Support* (see note below)
    – 20 concurrent symbol streaming (configurable)
    – Automatic reconnection on disconnection (handled by polymarket_direct)

    The following features on the Introspective Terminal:
    – Real-time symbol quote display
    – Real-time connection status display
    – RTT/Latency Statistics for polymarket
    – Socket Statistics and Status Monitoring


    * Note: P2 Support is complicated with the Polymarket Dispatcher because there are two levels of data to consider
    tick-by-tick and market state. Looking at FxC which uses a dataframe structure representing entire markets
    on every socket update this allows for a full market snapshot rather than sending deltas–something the argus project
    does NOT like doing. FxC uses incremental dataframes that get filled/sent as deltas arrive on the FxCDispatcher.
    These datastructures do not work with P2. Hence, the Polymarket Dispatcher does not support P2. But the issue
    is that unlike FxC which is a relatively 'slow' market and we can aford to send full market snapshots every delta,
    polymarket is extremely high-frequency and sending full market snapshots on every delta would be inefficient
    and lead to performance issues [not really but would be suboptimal depending on requirements]. For this reasoning
    PolymarketDispatcher opens a second port that is dedicated to tick-by-tick data only,
    while the main port handles market state updates as well as control messages. These tick-by-tick updates will use
    P2 protocol for efficiency. However, they will be much longer than traditional P2 messages because the 'symbol' field
    used to identify this packet will be created for anti-collision. A dedicated P2 Parser will be available with
    version 0.0.9 of argus. This dispatcher is still undergoing heavy R&D and testing these are NOT final features
    or how the dispatcher will operate. We HIGHLY recommend using this dispatcher with Python 3.14t (free-threading)
    for best performance as well be using that version to tune the dispatcher.

    """

    def __init__(self, host='localhost', port=9983, max_concurrent_streams=20, 
                 private_key=None, proxy_funder=None, dry_mode=True):
        """
        Initialize the PolymarketDispatcher.

        Args:
            host (str): Host to bind TCP server to
            port (int): Port to bind TCP server to (default 9983 to avoid conflicts)
            max_concurrent_streams (int): Maximum concurrent symbol streams
            private_key (str): Polymarket private key (optional, defaults to None for dry mode)
            proxy_funder (str): Polymarket proxy funder (optional, defaults to None for dry mode)
            dry_mode (bool): Whether to run in dry mode (no credentials required)
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
        self.token_to_clients = {}  # Maps token_id -> list of clients
        self.token_data_cache = {}  # Maps token_id -> latest market data
        self.max_concurrent_streams = max_concurrent_streams
        self.current_streams = 0

        # Initialize Polymarket client
        self.pm_client = polymarket_direct.EnhancedPM(
            private_key=private_key or "",
            proxy_funder=proxy_funder or "",
            dry_mode=dry_mode
        )

        # Threading lock for client management
        self._thread_lock = threading.Lock()

        # Configuration options
        self._configs: Dict[str, bool | str] = {
            'Print data packets': False,
            'Show subscription changes': True,
            'Auto-unsubscribe disconnected clients': True,
            'Show connection statistics': True,
        }

        self.host = host
        self.port = port

        # Statistics tracking
        self.connection_stats = {
            'total_messages': 0,
            'connection_start_time': time.time(),
            'last_message_time': None,
            'reconnections': 0
        }

        # Start client listener and health check
        self._add_clients()
        self._check_clients_live()
        self._display_connection_stats()

        # Start Polymarket WebSocket
        self.pm_client.start_market_ws()
        self.pm_client.market_open_semaphore.acquire()

        print(f'[PolymarketDispatcher] Initialized on {host}:{port}')
        print('[IMPORTANT] MODE = JSON PROTO (No P2 Support)')
        print(f'[CONFIG] Max concurrent streams: {max_concurrent_streams}')
        print(f'[CONFIG] Dry mode: {dry_mode}')

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
                current_value = self._configs[choice]
                if isinstance(current_value, bool):
                    if new_value.lower() in ['true', 't', '1', 'yes', 'y']:
                        self._configs[choice] = True
                    elif new_value.lower() in ['false', 'f', '0', 'no', 'n']:
                        self._configs[choice] = False
                    else:
                        print(f"Invalid boolean value: {new_value}. Please use true/false")
                        continue
                else:
                    self._configs[choice] = new_value
                print(f"Updated {choice} to {self._configs[choice]}")
            else:
                print(f"Invalid configuration: {choice}")

    def _subscribe_to_token(self, token_id: str, client: socket.socket):
        """Subscribe to a token and add client to the subscription list."""
        with self._thread_lock:
            if token_id not in self.token_to_clients:
                # Check if we can add more streams
                if self.current_streams >= self.max_concurrent_streams:
                    if self._configs['Show subscription changes']:
                        print(f"[SUBSCRIBE] Rejected subscription to {token_id}: max concurrent streams reached")
                    return False

                # First client for this token, subscribe to Polymarket WebSocket
                if self._configs['Show subscription changes']:
                    print(f"[SUBSCRIBE] New subscription to token {token_id}")
                
                callback = lambda msg: self._polymarket_callback(token_id, msg)
                self.pm_client.subscribe_to_market_data([token_id], callback)
                self.token_to_clients[token_id] = []
                self.current_streams += 1

            if client not in self.token_to_clients[token_id]:
                self.token_to_clients[token_id].append(client)
                if self._configs['Show subscription changes']:
                    print(f"[CLIENT] Added client to token {token_id} subscription (total: {len(self.token_to_clients[token_id])})")
            
            return True

    def _polymarket_callback(self, token_id: str, msg):
        """Callback function to handle Polymarket market data."""
        try:
            # Update statistics
            self.connection_stats['total_messages'] += 1
            self.connection_stats['last_message_time'] = time.time()

            # Cache the latest data
            self.token_data_cache[token_id] = msg

            # Get clients subscribed to this token
            clients = self.token_to_clients.get(token_id, [])
            if not clients:
                return

            # Create JSON packet for transmission
            packet = json.dumps({
                'type': 'market_data',
                'token_id': token_id,
                'timestamp': time.time(),
                'data': msg
            }).encode() + b'\n'

            if self._configs['Print data packets']:
                print(f"[TX {token_id}] {packet.decode().strip()}")

            # Send to all subscribed clients
            disconnected_clients = []
            for client in clients:
                try:
                    client.sendall(packet)
                except (OSError, ConnectionResetError) as e:
                    # Client disconnected, will be cleaned up by _check_clients_live
                    if self._configs['Show subscription changes']:
                        print(f"[CLIENT] Error sending to client: {e}")
                    disconnected_clients.append(client)

        except Exception as e:
            print(f"[ERROR] Error in Polymarket callback for token {token_id}: {e}")
            traceback.print_exc()

    @runAsThread
    def _listen_to_client(self, client: socket.socket):
        """Listen to client commands for subscribing to tokens."""
        while True:
            try:
                data = client.recv(9999).decode()
                if not data:
                    break

                # Parse command: "add=TOKEN_ID" or "subscribe=TOKEN_ID"
                if 'add' in data or 'subscribe' in data:
                    parts = data.split('=')
                    if len(parts) == 2:
                        token_id = parts[1].strip()
                        if self._configs['Show subscription changes']:
                            print(f"[CLIENT] Subscribing to token {token_id}")
                        self._subscribe_to_token(token_id, client)
                    else:
                        print(f"[CLIENT] Invalid command format: {data}")

                # Handle unsubscribe command
                elif 'unsubscribe' in data or 'remove' in data:
                    parts = data.split('=')
                    if len(parts) == 2:
                        token_id = parts[1].strip()
                        self._unsubscribe_from_token(token_id, client)

            except Exception as e:
                print(f"[CLIENT] Error receiving data from client: {e}")
                break

        # Client disconnected
        client.close()

    def _unsubscribe_from_token(self, token_id: str, client: socket.socket):
        """Unsubscribe a client from a token."""
        with self._thread_lock:
            if token_id in self.token_to_clients:
                if client in self.token_to_clients[token_id]:
                    self.token_to_clients[token_id].remove(client)
                    if self._configs['Show subscription changes']:
                        print(f"[CLIENT] Removed client from token {token_id} subscription")

                # If no clients left, unsubscribe from Polymarket
                if not self.token_to_clients[token_id]:
                    del self.token_to_clients[token_id]
                    self.pm_client.unsubscribe_from_market_data([token_id])
                    self.current_streams -= 1
                    if self._configs['Show subscription changes']:
                        print(f"[UNSUBSCRIBE] No clients for token {token_id}, cleaned up")

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
                    # Check each token's client list
                    tokens_to_remove = []
                    disconnected_clients = []

                    for token_id, clients in self.token_to_clients.items():
                        clients_to_remove = []

                        for client in clients:
                            try:
                                # Send ping to check if client is alive
                                client.sendall(b'$')
                            except (OSError, ConnectionResetError):
                                # Client disconnected
                                clients_to_remove.append(client)
                                disconnected_clients.append(client)
                                if self._configs['Show subscription changes']:
                                    print(f"[CLIENT] Disconnected client removed from token {token_id}")

                        # Remove disconnected clients
                        for client in clients_to_remove:
                            try:
                                clients.remove(client)
                            except ValueError:
                                pass

                        # If no clients left, mark token for unsubscription
                        if not clients:
                            tokens_to_remove.append(token_id)

                    # Clean up tokens with no clients
                    for token_id in tokens_to_remove:
                        del self.token_to_clients[token_id]
                        self.pm_client.unsubscribe_from_market_data([token_id])
                        self.current_streams -= 1
                        if self._configs['Show subscription changes']:
                            print(f"[UNSUBSCRIBE] No clients for token {token_id}, cleaned up")

                    # Remove disconnected clients from main client list
                    for client in disconnected_clients:
                        if client in self.clients:
                            self.clients.remove(client)

            except Exception as e:
                print(f"[ERROR] Error in _check_clients_live: {e}")
                traceback.print_exc()

    @runAsThread
    def _display_connection_stats(self):
        """Display connection statistics periodically."""
        while True:
            time.sleep(30)  # Update every 30 seconds
            if self._configs['Show connection statistics']:
                now = time.time()
                uptime = now - self.connection_stats['connection_start_time']
                avg_msg_rate = self.connection_stats['total_messages'] / uptime if uptime > 0 else 0
                
                print(f"\n[STATS] Uptime: {uptime:.1f}s")
                print(f"[STATS] Total messages: {self.connection_stats['total_messages']}")
                print(f"[STATS] Average rate: {avg_msg_rate:.2f} msgs/sec")
                print(f"[STATS] Current streams: {self.current_streams}/{self.max_concurrent_streams}")
                print(f"[STATS] Connected clients: {len(self.clients)}")
                
                last_msg = self.connection_stats['last_message_time']
                if last_msg:
                    time_since_last = now - last_msg
                    print(f"[STATS] Last message: {time_since_last:.1f}s ago")
                print()

    def interactive_mode(self):
        """Start interactive mode for managing the dispatcher."""
        functions = {
            'show_subscriptions': ('Show all active token subscriptions', self.show_subscriptions),
            'show_clients': ('Show all connected clients', self.show_clients),
            'show_stats': ('Show connection statistics', self.show_stats),
            'modify_configs': ('Modify dispatcher configurations', self._modify_configs_interactive),
            'restart_websocket': ('Restart Polymarket WebSocket connection', self.restart_websocket),
        }
        self._interactive_ui(functions)

    def show_subscriptions(self):
        """Display all active token subscriptions."""
        print("\n=== Active Token Subscriptions ===")
        if not self.token_to_clients:
            print("No active subscriptions")
        else:
            for token_id, clients in self.token_to_clients.items():
                print(f"{token_id}: {len(clients)} client(s)")
        print(f"Total streams: {self.current_streams}/{self.max_concurrent_streams}")
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

    def show_stats(self):
        """Display detailed connection statistics."""
        print("\n=== Connection Statistics ===")
        now = time.time()
        uptime = now - self.connection_stats['connection_start_time']
        avg_msg_rate = self.connection_stats['total_messages'] / uptime if uptime > 0 else 0
        
        print(f"Uptime: {uptime:.1f}s")
        print(f"Total messages: {self.connection_stats['total_messages']}")
        print(f"Average rate: {avg_msg_rate:.2f} msgs/sec")
        print(f"Current streams: {self.current_streams}/{self.max_concurrent_streams}")
        print(f"Connected clients: {len(self.clients)}")
        print(f"Reconnections: {self.connection_stats['reconnections']}")
        
        last_msg = self.connection_stats['last_message_time']
        if last_msg:
            time_since_last = now - last_msg
            print(f"Last message: {time_since_last:.1f}s ago")
        print()

    def restart_websocket(self):
        """Restart the Polymarket WebSocket connection."""
        print("[RESTART] Restarting Polymarket WebSocket connection...")
        try:
            self.pm_client.restart_ws_connections()
            self.connection_stats['reconnections'] += 1
            print("[RESTART] WebSocket connection restarted successfully")
        except Exception as e:
            print(f"[RESTART] Error restarting WebSocket: {e}")


if __name__ == '__main__':
    # Example usage
    dispatcher = PolymarketDispatcher(
        host='localhost',
        port=9983,
        dry_mode=True  # Run in dry mode by default
    )
    
    print("Polymarket Dispatcher is running. Use interactive_mode() for management.")
    dispatcher.interactive_mode()