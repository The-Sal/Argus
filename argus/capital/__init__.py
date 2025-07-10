import os
import json
import time
import tqdm
import socket
import logging
from dotenv import load_dotenv
from utils3 import runAsThread, assertTypes
from utils3.networking.sockets import UDSServer
from argus.capital._caches import DomainCache, NotKey
from argus.capital._svr_utils import encode_packet, decode_packet, decode_multiple_packets, \
    transmit_mkt_data_with_protocol_2
from argus.capital._lib import (CapitalComAPI, Environment, TradeDirection, HistoricalPriceResolution,
                                WebsocketDataType, CapitalComAPIError, WebSocketStatus)


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


CACHE = DomainCache('capital_com.api')
logger = logging.getLogger(__name__)


class CapitalComMKTDataLive:
    """Market Data Object for Capital.com API."""

    @assertTypes(types=[str, float, float, float, float, float, float, int],
                 auto_convert=True, class_method=True)
    def __init__(self, symbol: str, bid: float, bid_size: float,
                 ask: float, ask_size: float, last: float, last_size: float,
                 timestamp: int = time.time()):
        """Initializes the Market Data Object with symbol and market data."""
        self.symbol = symbol
        self.bid = bid
        self.bid_size = bid_size
        self.ask = ask
        self.ask_size = ask_size
        self.last = last
        self.last_size = last_size
        self.timestamp = timestamp

    def transferable(self):
        """Returns a dictionary representation of the market data for transfer."""
        return {
            'object': 'MKTDataLive',
            'symbol': self.symbol,
            'bid': self.bid,
            'bid_size': self.bid_size,
            'ask': self.ask,
            'ask_size': self.ask_size,
            'last': self.last,
            'last_size': self.last_size,
            'timestamp': self.timestamp,
            'python_timestamp': time.time(),
        }

    def transferable_2(self, encode: bool = True) -> bytes or list[str]:
        """Returns a dictionary representation of the market data for transfer. When encode is FALSE
        it returns a list of strings instead of a bytes."""
        data = [
            str(self.bid),
            str(self.bid_size),
            str(self.ask),
            str(self.ask_size),
            str(self.last),
            str(self.last_size),
            str(self.timestamp),
            str(time.time())
        ]
        if encode:
            return ",".join(data).encode('ascii')
        else:
            return data


class CapitalComOHLCData:
    """OHLC Data Object for Capital.com API."""
    # TODO: Implement OHLC Data Object
    pass


load_dotenv()


class SvrExport:
    def __init__(self, path='/tmp/argus_capital.sock'):
        self.path = path
        self.server = UDSServer(
            on_disconnect=lambda *args: None,
            path=path,
            on_recv=self._on_recv,
        )
        self.packets_read = 0
        self.client_list = []

    def _on_recv(self, client: socket.socket, address: tuple, data: bytes):
        """Handle Incoming data from a client. This method should be OVERRIDDEN by subclasses. and called as super() in the subclass."""
        self.packets_read += 1
        self.client_list.append((client, address))
        return

    def transmit(self, some_data, protocol: int = TransferPROTOCOL.VERSION_1):
        """Transmits data to all connected clients using the specified protocol."""
        if protocol == TransferPROTOCOL.VERSION_1:
            self.transmit_mkt_data_with_protocol_1(some_data)
        elif protocol == TransferPROTOCOL.VERSION_2:
            if isinstance(some_data, CapitalComMKTDataLive):
                self.transmit_mkt_data_with_protocol_2(some_data)
            else:
                raise TypeError("some_data must be an instance of CapitalComMKTDataLive for protocol 2")
        else:
            raise ValueError(f"Unsupported protocol version: {protocol}")

    def transmit_mkt_data_with_protocol_1(self, json_data: dict):
        """Transmits data to all connected clients, encoded as a packet. Note: Only clients who've sent data to the server will receive this."""
        packet = encode_packet(json.dumps(json_data).encode('ascii'))
        for client, address in self.client_list:
            try:
                client.sendall(packet)
            except socket.error:
                print(f"Client {address} disconnected or error occurred. Removing from client list.")
                self.client_list.remove((client, address))
            except Exception as e:
                print(f"Error sending data to client {client}: {e}")

    def transmit_mkt_data_with_protocol_2(self, mkt_data: CapitalComMKTDataLive):
        """Transmits market data to all connected clients."""
        if not isinstance(mkt_data, CapitalComMKTDataLive):
            raise TypeError("mkt_data must be an instance of CapitalComMKTDataLive")
        packet = transmit_mkt_data_with_protocol_2(mkt_data)
        for client, address in self.client_list:
            try:
                client.sendall(packet)
            except socket.error:
                print(f"Client {address} disconnected or error occurred. Removing from client list.")
                self.client_list.remove((client, address))
            except Exception as e:
                print(f"Error sending data to client {client}: {e}")

    @runAsThread
    def start_server(self):
        """Starts the server in a separate thread."""
        print(f"Starting server on {self.path}...")
        self.server.start()  # this is blocking call, so it will run in a separate thread

    def stop_server(self):
        """Stops the server."""
        print("Stopping server...")
        self.server.stop()
        print("Server stopped.")


class MKTDispatcher(SvrExport):
    """Market Data Dispatcher for Capital.com API."""

    def __init__(self, path='/tmp/argus_capital.sock',
                 api_key=os.environ['CAPITAL_DOTCOM_API_KEY'],
                 api_password=os.environ['CAPITAL_DOT_CUSTOM_PW'],
                 identifier=os.environ['CAPITAL_DOTCOM_IDENTIFIER'], environment=Environment.DEMO):
        """Initializes the Market Data Dispatcher with API credentials and environment."""
        super().__init__(path=path)
        self.api = CapitalComAPI(
            api_key=api_key,
            identifier=identifier,
            password=api_password,
            environment=environment  # Change to Environment.LIVE for live trading
        )
        if not self.api.login():
            raise CapitalComAPIError("Failed to login to Capital.com API. Check your credentials and environment.")

        print(f"Logged in to Capital.com API in {environment.name} environment.")
        self.epic_streams = {}
        self.resolutions = {}

    @CACHE.cache_decorator('resolve_symbol')
    def resolve_symbol(self, symbol: str, market: str = None):
        """Resolves a symbol into a Capital.com-compatible 'EPIC' format. It's assumed that the symbol provided is
        the real valid symbol found on the exchange it's listed on."""
        _ = market
        try:
            resolved_symbol = self.api.get_market_details(epic=symbol)
        except CapitalComAPIError as e:
            resolved_symbol = None
            # logger.log('No direct mapping found for symbol:', symbol)
            # logger.log('Attempting to resolve via search...')
            search_result = self.api.search_market_for_security(search_term=symbol)
            for result in search_result:
                try:
                    if result['symbol'].lower() == symbol.lower():
                        resolved_symbol = self.api.get_market_details(epic=result['epic'])
                        break
                except (AttributeError, Exception):
                    logger.error(f"Error resolving symbol '{symbol}': {e}")
                    continue

        return resolved_symbol

    def resolve_symbols_from_list(self, symbols: list, progress=True):
        """Resolves a list of symbols into Capital.com-compatible 'EPIC' format."""
        resolved_symbols = []
        if progress:
            itera = tqdm.tqdm(symbols)
        else:
            itera = iter(symbols)
        for symbol in itera:
            if progress:
                itera.set_description(f"Resolving {symbol}")
            attempt_resolve = self.resolutions.get(symbol, True)
            if not attempt_resolve:
                logger.info(f"NOT resolving symbol '{symbol}' again, already resolved.")
                continue

            resolved_symbol = self.resolve_symbol(symbol)
            if resolved_symbol:
                resolved_symbols.append(resolved_symbol)
            else:
                logger.error(f"Symbol '{symbol}' could not be resolved.")

            self.resolutions[symbol] = False  # Mark as resolved to avoid re-resolving

        return resolved_symbols

    def stream_epic(self, epic: str):
        """Streams market data for a specific epic."""
        if epic in self.epic_streams:
            if self.epic_streams[epic]:
                logger.warning(f"Already streaming data for epic '{epic}'.")
                return
        self.epic_streams[epic] = True
        self.api.subscribe_to_epic_data(
            epic=epic,
            data_type=WebsocketDataType.MARKET,
            callback=self._on_market_data_received

        )

    def _on_market_data_received(self, data: dict):
        """Handles incoming market data from the API."""
        # Sample data structure:
        # {'epic': 'BTCUSD', 'product': 'CFD', 'bid': 105099.85, '
        # bidQty': 0.2, 'ofr': 105149.85, 'ofrQty': 0.2, 'timestamp': 1750217540462}
        mkt_data = CapitalComMKTDataLive(
            symbol=data['epic'],
            bid=data['bid'],
            bid_size=data['bidQty'],
            ask=data['ofr'],
            ask_size=data['ofrQty'],
            last=data.get('last', 0.0),
            last_size=data.get('lastQty', 0.0),
            timestamp=data['timestamp']
        )

        # self.transmit(mkt_data.transferable())
        self.transmit(mkt_data, protocol=TransferPROTOCOL.VERSION_2)

    def _on_recv(self, client: socket.socket, address: tuple, data: bytes):
        """Handles incoming data from a client. This method is overridden to handle client requests."""
        logger.info(f"Received data from {address}: {data}")
        super()._on_recv(client, address, data)
        decoded_datas = decode_multiple_packets(data)
        logger.info(f"Decoded {len(decoded_datas)} packets from {address}.")
        for decoded_data in decoded_datas:
            if not decoded_data:
                print(f"Received empty or invalid packet from {address}.")
                return

            # Sample decoded_data structure:
            # {'action': 'resolve_symbol', 'symbol': 'BTCUSD'}
            # {'action': 'stream_epic', 'epic': 'BTCUSD'}
            # {'action': 'resolve/stream', 'symbol': 'BTCUSD' }
            data = json.loads(decoded_data.decode('ascii'))
            self.handle_client_request(data, client)

    def handle_client_request(self, data: dict, client: socket.socket):
        """Handles client requests based on the action specified in the data."""
        action = data.get('action')
        if action == 'resolve_symbol':
            symbol = data.get('symbol')
            if symbol:
                resolved_symbol = self.resolve_symbol(symbol, None)
                if resolved_symbol:
                    response = {
                        'status': 'success',
                        'data': resolved_symbol
                    }
                else:
                    response = {
                        'status': 'error',
                        'message': f"Symbol '{symbol}' could not be resolved."
                    }
            else:
                response = {
                    'status': 'error',
                    'message': "No symbol provided for resolution."
                }

        elif action == 'stream_epic':
            epic = data.get('epic')
            if epic:
                self.stream_epic(epic)
                response = {
                    'status': 'success',
                    'message': f"Started streaming data for epic '{epic}'."
                }
            else:
                response = {
                    'status': 'error',
                    'message': "No epic provided for streaming."
                }

        elif action == 'resolve/stream':
            symbol = data.get('symbol')
            if symbol:
                resolved_symbol = self.resolve_symbol(symbol, None)
                if resolved_symbol:
                    self.stream_epic(resolved_symbol['instrument']['epic'])
                    response = {
                        'status': 'success',
                        'data': resolved_symbol
                    }
                else:
                    response = {
                        'status': 'error',
                        'message': f"Symbol '{symbol}' could not be resolved."
                    }
            else:
                response = {
                    'status': 'error',
                    'message': "No symbol provided for resolution and streaming."
                }

        elif action == 'unsubscribe':
            epic = data.get('epic')
            if epic:
                self.api.unsubscribe_from_epic_data(epic=epic, data_type=WebsocketDataType.MARKET)
                self.epic_streams[epic] = False
                response = {
                    'status': 'success',
                    'message': f"Unsubscribed from epic '{epic}'."
                }
            else:
                response = {
                    'status': 'error',
                    'message': "No epic provided for unsubscription."
                }
        elif action == 'resolve/stream/batch/file':
            try:
                file = data.get('file')
                with open(file, 'r') as f:
                    symbols = [line.strip() for line in f if line.strip()]
                    resolved_symbols = self.resolve_symbols_from_list(symbols, progress=False)
                    worked = 0
                    # disable logging for '_lib' module to avoid cluttering the output
                    logging.getLogger('argus.capital._lib').setLevel(logging.ERROR)

                    for resolved_symbol in tqdm.tqdm(resolved_symbols, desc="Streaming resolved symbols"):
                        try:
                            self.stream_epic(resolved_symbol['instrument']['epic'])
                            worked += 1
                        except Exception as e:
                            logger.error(
                                f"Error streaming epic for symbol '{resolved_symbol['instrument']['epic']}': {e}")
                            continue

                        time.sleep(0.1)  # Sleep to avoid overwhelming the server with requests

                    logging.getLogger('argus.capital._lib').setLevel(logging.INFO)
                response = {
                    'status': 'success',
                    'message': f"Started streaming data for {worked} symbols."
                }
            except FileNotFoundError:
                response = {
                    'status': 'error',
                    'message': f"File '{file}' not found."
                }


        else:
            response = {
                'status': 'error',
                'message': f"Unknown action '{action}'."
            }

        # Send the response back to the client
        response['object'] = 'Response'
        client.sendall(encode_packet(json.dumps(response).encode('ascii')))


if __name__ == '__main__':
    dispatcher = MKTDispatcher(environment=Environment.LIVE)
    dispatcher.start_server()
    input('Press enter to exit.')
    # sym = dispatcher.resolve_symbol('BTCUSD', None)
    # dispatcher.stream_epic(sym['instrument']['epic'])
    # time.sleep(10)  # Allow some time for data to be streamed
    dispatcher.api.logout()
    os.kill(os.getpid(), 9)  # Force exit to ensure cleanup
