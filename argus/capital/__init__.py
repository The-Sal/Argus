import os
import json
import time
import socket
import logging

import tqdm
from dotenv import load_dotenv
from utils3 import runAsThread, assertTypes
from utils3.networking.sockets import Server
from argus.capital._caches import DomainCache, NotKey
from argus.capital._svr_utils import encode_packet, decode_packet
from argus.capital._lib import (CapitalComAPI, Environment, TradeDirection, HistoricalPriceResolution,
                                WebsocketDataType, CapitalComAPIError, WebSocketStatus)


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
            'timestamp': self.timestamp
        }

class CapitalComOHLCData:
    """OHLC Data Object for Capital.com API."""
    # TODO: Implement OHLC Data Object
    pass

load_dotenv()

class SvrExport:
    def __init__(self, host: str = 'localhost', port: int = 9964):
        self.host = host
        self.port = port
        self.server = Server(
            on_disconnect=lambda *args: None,
            host=host,
            port=port,
            on_recv=self._on_recv,
        )
        self.packets_read = 0
        self.client_list = []

    def _on_recv(self, client: socket.socket, address: tuple, data: bytes):
        """Handle Incoming data from a client. This method should be OVERRIDDEN by subclasses. and called as super() in the subclass."""
        self.packets_read += 1
        self.client_list.append((client, address))
        return

    def transmit(self, json_data: dict):
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

    @runAsThread
    def start_server(self):
        """Starts the server in a separate thread."""
        print(f"Starting server on {self.host}:{self.port}...")
        self.server.start()  # this is blocking call, so it will run in a separate thread

    def stop_server(self):
        """Stops the server."""
        print("Stopping server...")
        self.server.stop()
        print("Server stopped.")




class MKTDispatcher(SvrExport):
    """Market Data Dispatcher for Capital.com API."""
    def __init__(self, host: str = 'localhost', port: int = 9964,
                 api_key=os.environ['CAPITAL_DOTCOM_API_KEY'],
                 api_password=os.environ['CAPITAL_DOT_CUSTOM_PW'],
                 identifier=os.environ['CAPITAL_DOTCOM_IDENTIFIER'], environment=Environment.DEMO):
        """Initializes the Market Data Dispatcher with API credentials and environment."""
        super().__init__(host, port)
        self.api = CapitalComAPI(
            api_key=api_key,
            identifier=identifier,
            password=api_password,
            environment=environment  # Change to Environment.LIVE for live trading
        )
        if not self.api.login():
            raise CapitalComAPIError("Failed to login to Capital.com API. Check your credentials and environment.")
    
        print(f"Logged in to Capital.com API in {environment.name} environment.")

    @CACHE.cache_decorator('resolve_symbol')
    def resolve_symbol(self, symbol: str, market: str = None):
        """Resolves a symbol into a Capital.com-compatible 'EPIC' format. It's assumed that the symbol provided is
        the real valid symbol found on the exchange it's listed on."""
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
            resolved_symbol = self.resolve_symbol(symbol)
            if resolved_symbol:
                resolved_symbols.append(resolved_symbol)
            else:
                logger.error(f"Symbol '{symbol}' could not be resolved.")
        return resolved_symbols


    def stream_epic(self, epic: str):
        """Streams market data for a specific epic."""
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
        self.transmit(mkt_data.transferable())

    def _on_recv(self, client: socket.socket, address: tuple, data: bytes):
        """Handles incoming data from a client. This method is overridden to handle client requests."""
        super()._on_recv(client, address, data)
        decoded_data = decode_packet(data)
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
                response = {
                    'status': 'success',
                    'message': f"Unsubscribed from epic '{epic}'."
                }
            else:
                response = {
                    'status': 'error',
                    'message': "No epic provided for unsubscription."
                }

        else:
            response = {
                'status': 'error',
                'message': f"Unknown action '{action}'."
            }

        # Send the response back to the client
        response['object'] = 'Response'
        client.sendall(encode_packet(json.dumps(response).encode('ascii')))






    # def __del__(self):
    #     """Ensures the API is logged out when the dispatcher is deleted."""
    #     self.api.logout()



if __name__ == '__main__':
    dispatcher = MKTDispatcher(environment=Environment.LIVE)
    dispatcher.start_server()
    input('Press enter to exit.')
    # sym = dispatcher.resolve_symbol('BTCUSD', None)
    # dispatcher.stream_epic(sym['instrument']['epic'])
    # time.sleep(10)  # Allow some time for data to be streamed
    dispatcher.api.logout()
    os.kill(os.getpid(), 9)  # Force exit to ensure cleanup



