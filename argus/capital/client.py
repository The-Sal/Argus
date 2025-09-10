"""A client for the MKTDispatcher class in the Argus/Capital module. This module only supports protocol version 2."""
import json
import socket
import logging
from utils3 import runAsThread
from typing import Any, Dict, List, Optional

from argus.capital import CapitalComMKTDataLive
from argus.capital import encode_packet


class CapitalComClient:
    """A UDS client for the MKTDispatcher (server) in the Argus/Capital module.
    - Only supports Protocol 2 market data stream (for ticks).
    - Control requests (resolve/stream/unsubscribe) are sent using Protocol 1 packets.

    Usage:
      - Either subclass and override symbol_callback(), or pass a callback to stream_symbols().
    """

    def __init__(self, socket_addr: str = '/tmp/argus_capital.sock'):
        self.socket_addr = socket_addr
        self.sock: Optional[socket.socket] = None
        self.logger = logging.getLogger(__name__)
        self._listener_thread = None
        self._running = False

        # For every symbol we are streaming, we will store the latest state here.
        # Limit to only 30 symbols concurrently to not get rate limited by Capital.com
        # if you stream over 30 an exception will be raised. You must unsubscribe from some symbols first.
        self.states: Dict[str, CapitalComMKTDataLive] = {}

    def connect(self):
        """Connect to the UDS socket and start listener."""
        if self.sock is not None:
            return
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.sock.connect(self.socket_addr)
            self.sock.setblocking(True)
            self._running = True
            self._listener_thread = self._listen_to_socket()
            self.logger.info(f"Connected to socket at {self.socket_addr}")
        except socket.error:
            # Re-raise so caller can handle
            self.sock = None
            self._running = False
            raise

    def close(self):
        """Close the client connection and stop the listener."""
        self._running = False
        try:
            if self.sock is not None:
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                self.sock.close()
        finally:
            self.sock = None

    def send_dict(self, data: Dict[str, Any]):
        """Send a dictionary to the server using Protocol 1 framing."""
        if self.sock is None:
            raise RuntimeError("Socket is not connected. Call connect() first.")
        payload = json.dumps(data).encode('ascii')
        packet = encode_packet(payload)
        self.sock.sendall(packet)

    def stream_symbols(self, symbols: List[str], callback: Optional[callable] = None):
        """Request streaming for the given list of symbols.

        - Uses server-side action 'resolve/stream' per symbol.
        - Stores last tick in self.states and calls callback(symbol, CapitalComMKTDataLive).
        """
        if self.sock is None:
            self.connect()
        for sym in symbols:
            try:
                self.send_dict({'action': 'resolve/stream', 'symbol': sym})
            except Exception as e:
                self.logger.error(f"Failed to request stream for {sym}: {e}")
        if callback is not None:
            # Set an instance callback wrapper
            self.symbol_callback = callback  # type: ignore

    def unsubscribe(self, epic: str):
        if self.sock is None:
            raise RuntimeError("Socket is not connected. Call connect() first.")
        self.send_dict({'action': 'unsubscribe', 'epic': epic})

    def symbol_callback(self, symbol: str, data: CapitalComMKTDataLive):
        """Override in subclasses or pass a callback to stream_symbols()."""
        pass

    @runAsThread
    def _listen_to_socket(self):
        """Listen to the socket for incoming data and dispatch packets.

        The stream coming from server may contain a mix of:
          - Protocol 1 packets: ~LLLL|{json}
          - Protocol 2 packets: ~LLLL<NNNN|><symbol><csv>L
        We differentiate by inspecting the byte at position 5 (0-based): '|' means protocol 1.
        """
        if self.sock is None:
            raise RuntimeError("Socket is not connected. Call connect() first.")

        buf = bytearray()
        while self._running:
            try:
                chunk = self.sock.recv(8192)
            except (socket.error, OSError):
                break
            if not chunk:
                break
            buf.extend(chunk)

            while True:
                # Need at least 5 bytes to read header
                if len(buf) < 5:
                    break
                if buf[0] != ord('~'):
                    # Desync: drop until next '~'
                    del buf[0]
                    continue
                # Read 4 ascii digits for length
                try:
                    length_val = int(bytes(buf[1:5]).decode('ascii'))
                except Exception:
                    # malformed; drop leading byte
                    del buf[0]
                    continue
                total_needed = 5 + length_val + 1
                if len(buf) < total_needed:
                    break  # wait for more data

                packet = bytes(buf[:total_needed])
                del buf[:total_needed]

                # Distinguish protocol 1 vs 2
                if packet[5:6] == b'|':
                    # Protocol 1 JSON control/response
                    payload = packet[6:]
                    self._handle_protocol1(payload)
                else:
                    # Protocol 2 market data
                    self._handle_protocol2(packet)

        self._running = False

    def _handle_protocol1(self, payload: bytes):
        try:
            data = json.loads(payload.decode('ascii'))
        except Exception as e:
            self.logger.error(f"Failed to decode protocol1 JSON: {e}")
            return
        # For now we just log responses; user focuses on market data via protocol 2
        obj = data.get('object')
        if obj == 'Response':
            self.logger.debug(f"Server response: {data}")
        else:
            self.logger.debug(f"Protocol1 data: {data}")

    def _handle_protocol2(self, packet: bytes):
        # Convert server packet to the format expected by CapitalComMKTDataLive.from_protocol_2
        # Server packet layout: '~' + 4 ascii length + 4 ascii symbol_len + '|' + symbol + csv + 'L'
        # We will build: 4 dummy bytes + 4-byte big-endian symbol_len + symbol + csv + 'L'
        try:
            no_tilde = packet[1:]
            if len(no_tilde) < 9:
                return
            # Extract symbol length (ascii) and ensure there is a pipe
            sym_len_ascii = no_tilde[4:8]
            pipe = no_tilde[8:9]
            if pipe != b'|':
                return
            try:
                sym_len = int(sym_len_ascii.decode('ascii'))
            except Exception:
                return
            symbol_and_rest = no_tilde[9:]
            if len(symbol_and_rest) < sym_len + 1:  # needs at least symbol plus 'L'
                return
            symbol_bytes = symbol_and_rest[:sym_len]
            rest = symbol_and_rest[sym_len:]
            # Recompose expected bytes
            rebuilt = b"\x00\x00\x00\x00" + sym_len.to_bytes(4, byteorder='big') + symbol_bytes + rest
            tick = CapitalComMKTDataLive.from_protocol_2(rebuilt)
        except Exception as e:
            self.logger.error(f"Failed to parse protocol2 packet: {e}")
            return

        # Update state and invoke callback
        self.states[tick.symbol] = tick
        try:
            self.symbol_callback(tick.symbol, tick)
        except Exception as e:
            self.logger.error(f"symbol_callback error for {tick.symbol}: {e}")


import argparse
import threading
import time
import os


def _format_tick(t: CapitalComMKTDataLive) -> str:
    return f"{t.symbol} bid={t.bid} ({t.bid_size}) ask={t.ask} ({t.ask_size}) last={t.last} ({t.last_size}) ts={t.timestamp}"


def run_edit_mode(client: CapitalComClient):
    """Interactive edit mode for managing symbols."""
    print("\n=== EDIT MODE ===")
    print("Commands: add <symbol>, remove <epic|symbol>, list, help, back")
    print("Note: Market data updates are suppressed in edit mode")

    # Store original callback to restore later
    original_callback = getattr(client, 'symbol_callback', None)
    
    # Disable market data printing in edit mode
    def silent_callback(symbol: str, data: CapitalComMKTDataLive):
        pass
    
    client.symbol_callback = silent_callback

    # helper to map possible symbol->epic using latest states
    def resolve_epic(name: str) -> str:
        if name in client.states:
            return client.states[name].symbol
        # If an epic is passed directly, just return it
        return name

    try:
        while True:
            try:
                cmd = input('edit> ').strip()
            except EOFError:
                break
            if not cmd:
                continue
            parts = cmd.split()
            op = parts[0].lower()
            if op in ('back', 'exit'):
                break
            elif op == 'help':
                print("Commands:\n  add <symbol>\n  remove <epic|symbol>\n  list\n  help\n  back")
            elif op == 'add' and len(parts) >= 2:
                symbol = parts[1]
                client.stream_symbols([symbol])
                print(f"Requested streaming for {symbol}")
            elif op == 'remove' and len(parts) >= 2:
                name = parts[1]
                epic = resolve_epic(name)
                try:
                    client.unsubscribe(epic)
                    print(f"Unsubscribed {epic}")
                    # also remove from local state if present
                    client.states.pop(name, None)
                    client.states.pop(epic, None)
                except Exception as e:
                    print(f"Failed to unsubscribe {epic}: {e}")
            elif op == 'list':
                if not client.states:
                    print("No active symbols.")
                else:
                    print("Active symbols:")
                    for sym, tick in client.states.items():
                        print(f"  {sym}")
            else:
                print("Unknown command. Type 'help' for available commands.")
    except KeyboardInterrupt:
        pass
    finally:
        # Restore original callback
        if original_callback:
            client.symbol_callback = original_callback


def _clear_screen():
    """Clear the terminal screen."""
    os.system('cls' if os.name == 'nt' else 'clear')


def _format_table_row(symbol: str, tick: CapitalComMKTDataLive) -> str:
    """Format a single row for the market data table."""
    return f"{symbol:<12} {tick.bid:<10.5f} {tick.bid_size:<8} {tick.ask:<10.5f} {tick.ask_size:<8} {tick.last:<10.5f} {tick.last_size:<8}"


def _display_market_data_table(client: CapitalComClient):
    """Display a formatted table of all market data."""
    _clear_screen()
    print("="*80)
    print("                        LIVE MARKET DATA")
    print("="*80)
    
    if not client.states:
        print("\nNo active symbols subscribed.")
        print("Use Edit Mode to add symbols first.")
        return
    
    # Table header
    print(f"{'Symbol':<12} {'Bid':<10} {'Bid Size':<8} {'Ask':<10} {'Ask Size':<8} {'Last':<10} {'Last Size':<8}")
    print("-" * 80)
    
    # Table rows sorted by symbol name
    for symbol in sorted(client.states.keys()):
        tick = client.states[symbol]
        print(_format_table_row(symbol, tick))
    
    print("-" * 80)
    print(f"Active Symbols: {len(client.states)} | Press Ctrl+C to return to main menu")


def run_view_mode(client: CapitalComClient, symbols: Optional[List[str]] = None):
    """View mode for watching market data updates in a table format."""
    print("\n=== VIEW MODE ===")
    if symbols:
        print(f"Subscribing to symbols: {', '.join(symbols)}")
        client.stream_symbols(symbols)
        time.sleep(1)  # Give a moment for initial data to arrive
    else:
        print("Viewing all currently subscribed symbols")
    print("Starting live table view...")
    
    # Silent callback - we just want data stored in states
    def silent_callback(symbol: str, data: CapitalComMKTDataLive):
        pass
    
    # Store original callback
    original_callback = getattr(client, 'symbol_callback', None)
    client.symbol_callback = silent_callback

    try:
        while True:
            _display_market_data_table(client)
            time.sleep(1)  # Refresh every second
    except KeyboardInterrupt:
        print("\nReturning to main menu...")
    finally:
        # Restore original callback
        if original_callback:
            client.symbol_callback = original_callback


def show_main_menu():
    """Display the main menu options."""
    print("\n" + "="*50)
    print("   Capital.com Argus Client - Interactive Mode")
    print("="*50)
    print("1. Edit Mode    - Add/remove/list symbols")
    print("2. View Mode    - Watch live market data")  
    print("3. Quick View   - Enter symbols to watch")
    print("4. Status       - Show connection and symbol status")
    print("5. Help         - Show this menu")
    print("6. Quit         - Exit the application")
    print("="*50)


def run_cli():
    parser = argparse.ArgumentParser(description='Capital.com Argus client CLI')
    parser.add_argument('--socket', default='/tmp/argus_capital.sock', help='UDS socket path')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s %(name)s: %(message)s')

    client = CapitalComClient(socket_addr=args.socket)
    
    try:
        client.connect()
        print(f"Connected to socket at {args.socket}")
    except Exception as e:
        print(f"Failed to connect to socket {args.socket}: {e}")
        print("Make sure the Argus Capital server is running.")
        return

    try:
        while True:
            show_main_menu()
            try:
                choice = input("\nSelect option (1-6): ").strip()
            except EOFError:
                break
            
            if choice == '1':
                run_edit_mode(client)
            elif choice == '2':
                run_view_mode(client)
            elif choice == '3':
                symbols_input = input("Enter symbols (space-separated): ").strip()
                if symbols_input:
                    symbols = symbols_input.split()
                    run_view_mode(client, symbols)
                else:
                    print("No symbols entered.")
            elif choice == '4':
                print(f"\nConnection: {'Connected' if client.sock else 'Disconnected'}")
                print(f"Socket: {client.socket_addr}")
                if client.states:
                    print(f"Active symbols: {len(client.states)}")
                    for sym in client.states.keys():
                        print(f"  - {sym}")
                else:
                    print("No active symbol subscriptions")
            elif choice == '5':
                continue  # Show menu again
            elif choice == '6':
                print("Goodbye!")
                break
            else:
                print("Invalid choice. Please select 1-6.")
    except KeyboardInterrupt:
        print("\nGoodbye!")
    finally:
        client.close()


if __name__ == '__main__':
    run_cli()
