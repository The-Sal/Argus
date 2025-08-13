import os
import json
import tqdm
import time
import pickle
import socket
import logging
import datetime
import traceback
import websocket
import threading
from utils3 import runAsThread
from argus._argus_utils import Notification
from argus.ib.fields import IBKRFields, SearchResult
from utils3.networking import Session as _RAW_SESSION
from argus.ib._shortable_shares_data import ShortableSharesData
from argus.capital import DomainCache, transmit_mkt_data_with_protocol_2, CapitalComMKTDataLive


class LockedSession(_RAW_SESSION):
    """A session that is locked to prevent concurrent access."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lock = threading.Lock()

    def get(self, url, params=None, **kwargs):
        with self.lock:
            return super().get(url, params=params, **kwargs)

    # noinspection all
    def post(self, url, data=None, json=None, **kwargs):
        with self.lock:
            return super().post(url, data=data, json=json, **kwargs)


# enable logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class IBKRModes:
    ASK = 'ASK'
    ASK_BID_LAST = 'ASK+BID+LAST'
    FULL_PKL = 'FULL_PKL'
    FULL_JSON = 'FULL_JSON'
    PROTOCOL_2 = 'PROTOCOL_2'


class IBKR_CapitalComMKTDataLive(CapitalComMKTDataLive):
    """This class is an extension of the CapitalComMKTDataLive class to support IBKR fields. Its only
    purpose is to conform with the 'transmit_mkt_data_with_protocol_2' function.
    NOTE: Given that this is an extended version of the CapitalComMKTDataLive class with additional
    attributes the DECODER should be updated to handle the additional fields and orders from protocol 2."""

    def __init__(self, shortable_shares, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.shortable_shares = shortable_shares

    @classmethod
    def from_capital_com(cls, shortable_shares, capital_com_data: CapitalComMKTDataLive):
        """Create an instance from a CapitalComMKTDataLive object."""
        return cls(
            shortable_shares=shortable_shares,
            symbol=capital_com_data.symbol,
            bid=capital_com_data.bid,
            bid_size=capital_com_data.bid_size,
            ask=capital_com_data.ask,
            ask_size=capital_com_data.ask_size,
            last=capital_com_data.last,
            last_size=capital_com_data.last_size
        )

    def transferable_2(self, **kwargs) -> bytes:
        """This function is used to convert the object to a dictionary that can be used with the protocol 2."""
        data: list[str] = super().transferable_2(encode=False)
        # print('Prior to inserting shortable_shares, data is:', data, 'length:', len(data))

        # Insert shortable_shares before the last two elements, that is before both timestamps (old capital.com and Python)
        data.insert(len(data) - 2, str(self.shortable_shares))
        bytes_packet = ",".join(data).encode('ascii')
        # print('After inserting shortable_shares, data is:', data, 'length:', len(data))
        return bytes_packet


_IB_Cache = DomainCache('IBKR')
_NOTIFICATION = Notification(
    number=os.getenv("NOTIFICATION_NUMBER", None), active=True if os.getenv("NOTIFICATION_NUMBER", None) else False,
)


class IBError(Exception):
    pass


class AuthenticationTimeout(IBError):
    pass


class MarketData:
    """User IBKRFields to query for market data"""

    def __init__(self, contract_id, server_id, contract_exchange, topic, data):
        self.contract_id = contract_id
        self.server_id = server_id
        self.contract_exchange = contract_exchange
        self.topic = topic
        self.data = data

    def get(self, field: int, default=None, strip_commas=True, string_values=True):
        a1 = self.data.get(str(field), default)
        a2 = self.data.get(int(field), default)
        # if a1 is not None:
        #     return str(a1).replace(',', '') if strip_commas else a1
        # else:
        #     return str(a2).replace(',', '') if strip_commas else a2
        final_value = a1 if a1 is not None else a2
        if final_value is None:
            return default

        if strip_commas:
            final_value = str(final_value).replace(',', '')
        if string_values:
            final_value = str(final_value)

        return final_value


class IBNetworker:
    def __init__(self, cookie):
        self.cookie = cookie
        self.headers = {
            'Cookie': self.cookie,
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36'
        }
        self.session = LockedSession(headers=self.headers)
        self.setup_msgs = [
            {
                'url': 'https://www.interactivebrokers.co.uk/portal.proxy/v1/portal/tickle',
                'method': 'POST',
            },
            {
                'url': 'https://www.interactivebrokers.co.uk/portal.proxy/v1/portal/iserver/auth/status',
                'method': 'POST',
            },
            {
                'url': 'https://www.interactivebrokers.co.uk/portal.proxy/v1/portal/iserver/auth/ssodh/init',
                'method': 'POST',
                'data': {"compete": False, "useSecurityContext": True, "locale": "en_US", "tz": "xxx (Europe/London)",
                         "isET": True, "publish": True}
            }
        ]

        self.urls = {
            'search': 'https://www.interactivebrokers.co.uk/portal.proxy/v1/portal/iserver/secdef/search',
            'query_equities_contracts': 'https://www.interactivebrokers.co.uk/portal.proxy/v1/portal/iserver/trsrv/stocks',
        }

        self.tickle = self.setup_msgs[0]
        self.auth_stats = self.setup_msgs[1]
        self.authenticated = False
        self.forbidden_strings = [
            'NYMEX'
        ]

    @runAsThread
    def initialize(self):
        self.run_setup_msgs()
        self._check_authentication()
        self._heartbeat()

    def run_setup_msgs(self):
        """Send setup messages to the server"""
        for msg in self.setup_msgs:
            print(f"Sending setup message to {msg['url']}")
            response = self.session.post(msg['url'], json=msg.get('data'))
            try:
                print(f"Response: {response.json()}")
            except json.JSONDecodeError:
                print(f"Response: {response.text}")
                raise IBError(f"Failed to decode JSON response from {msg['url']}")
            if 'init' in msg['url']:
                self.authenticated = response.json().get('authenticated', False)
                print(f"Authenticated: {self.authenticated}")

            time.sleep(1)

    @runAsThread
    def _check_authentication(self):
        """Check if the user is authenticated"""
        while True:
            time.sleep(60 * 2)  # Check every 4 minutes
            response = self.session.post(self.auth_stats['url'])
            try:
                data = response.json()
            except json.JSONDecodeError:
                _NOTIFICATION.notify(
                    title='IBKR Authentication Check Failed',
                    message=f'Failed to decode JSON response from {self.auth_stats["url"]}. Response: {response.text}'
                )
            if data.get('authenticated', False):
                self.authenticated = True
                print("User is authenticated", 'Response:', data)
            else:
                print("User is not authenticated")
                print('Authentication check failed. Re-running setup messages...')
                self.run_setup_msgs()
            if not self.authenticated:
                raise AuthenticationTimeout("User is not authenticated. Re-authentication required.")

    @runAsThread
    def _heartbeat(self):
        """Send a heartbeat message to keep the connection alive"""
        while True:
            self.session.post(self.tickle['url'])
            time.sleep(2)

    @_IB_Cache.cache_decorator('IBNetworker.search_contract')
    def search_contract(self, contract_name) -> list[SearchResult]:
        """
        Search for a contract by its name. THE CONTRACT MUST BE A STOCK NOTHING ELSE IS SUPPORTED.
        """
        payload = {"symbol": contract_name, "secType": "STK", "referrer": "onebar"}
        response = self.session.post(self.urls['search'], json=payload).json()
        try:
            results = [SearchResult(**result) for result in response]
            for result in results:
                for string in self.forbidden_strings:
                    try:
                        if string in result.description:
                            print(f"Skipping {result.description} because it contains {string}")
                            results.remove(result)
                            break
                    except TypeError:
                        break

        except Exception as e:
            print(f"Error parsing search results: {response}, output request")
            traceback.print_exc()
            raise e

        return results

    # TODO: Finish this function to query multiple equities contracts by their symbols.
    # @_IB_Cache.cache_decorator('IBNetworker.query_equities_contracts')
    # def query_equities_contracts(self, symbols: list[str]) -> list[SearchResult]:
    #     """
    #     Query multiple equities contracts by their symbols.
    #     """
    #     if not isinstance(symbols, list):
    #         raise ValueError("Symbols must be a list of strings.")
    #
    #     results = []
    #     csv = ','.join(symbols)
    #     query = {
    #         'symbols': csv,
    #     }
    #     response = self.session.get(self.urls['query_equities_contracts'], json=query)
    #     return response


class IBWss:
    def __init__(self, cookie=os.getenv('IB_COOKIE')):
        if cookie is None:
            raise ValueError("IB_COOKIE environment variable not set.")
        self.url = 'wss://www.interactivebrokers.co.uk/portal.proxy/v1/portal/ws'
        self.cookie = cookie
        self.user_agent = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36'
        self.headers = {
            'Cookie': self.cookie,
            'User-Agent': self.user_agent,
        }
        self.ws = websocket.WebSocketApp(
            url=self.url,
            header=self.headers,
            on_message=self.on_message,
            on_open=self.on_open,
            on_close=self.on_close,
        )
        self.opened = False
        self.stream_messages = [
            'sor+{}',
            'upl+{}'
        ]
        self.recv = 0
        self.networker = IBNetworker(cookie)
        self.contract_callbacks = {}
        self._heartbeat()
        self.subscribe_tally = 0
        self._subscribe_tally_max = 100  # Max number of subscriptions before we stop streaming
        # make sure the progress bar does not calculate estimated time
        self._load_progress = tqdm.tqdm(
            desc='Live subscriptions to IBKR',
            total=self._subscribe_tally_max,
            unit='contract',
            unit_scale=True,
            unit_divisor=1,
            dynamic_ncols=True,
        )
        self._stream_lock = threading.Lock()
        self._private_contracts = set()  # DO NOT MODIFY

        ######################################################################
        #          This entire section below is only for statistics          #
        ######################################################################

        # Monitor how long it has been since the last market data callback
        self._last_market_data_callback = time.time() - 60 * 60 * 24
        self._sock_msgs = []
        self._contracts_subscribed = set()  # Keep track of subscribed contracts
        self.interactive_functions = {
            'Time since last contract data': lambda: print(
                'Time since last contract data: {:.4f} seconds'.format(time.time() - self._last_market_data_callback)),
            'Total WebSocket messages received': lambda: print(f'Total WebSocket messages received: {self.recv}'),
            'Write all WebSocket messages to a file': lambda: self._write_sock_msgs_to_file(),
            'Unique Contracts subscribed (lifetime)': lambda: print(len(self._contracts_subscribed)),
        }

    def _write_sock_msgs_to_file(self):
        """Write all WebSocket messages to a file for debugging purposes."""
        if not self._sock_msgs:
            print("No WebSocket messages to write.")
            return
        with open('ibkr_websocket_messages.txt', 'w', encoding='utf-8') as f:
            for msg in self._sock_msgs:
                f.write(f"{msg}\n")
        print(f"Wrote {len(self._sock_msgs)} WebSocket messages to ibkr_websocket_messages.txt")

    def interactive_mode(self):
        while True:
            print("\nInteractive Functions:")
            for i, func in enumerate(self.interactive_functions.keys(), start=1):
                print(f"{i}. {func}")
            print("0. Exit")
            choice = input("Select an option: ")
            if choice == '0':
                break
            try:
                choice = int(choice)
                if 1 <= choice <= len(self.interactive_functions):
                    func_name = list(self.interactive_functions.keys())[choice - 1]
                    self.interactive_functions[func_name]()
                else:
                    print("Invalid choice. Please try again.")
            except ValueError:
                print("Invalid input. Please enter a number.")

    # noinspection all
    def stream_market_data(self, contract_id, callback,
                           fields=(IBKRFields.LAST_PRICE, IBKRFields.ASK_PRICE, IBKRFields.ASK_SIZE,
                                   IBKRFields.BID_PRICE, IBKRFields.BID_SIZE, IBKRFields.SHORTABLE_SHARES,
                                   IBKRFields.SYMBOL)):
        """
        Stream market data for a given contract ID.
        
        Args:
            contract_id: The contract ID to stream market data for.
            callback: The callback function to call when market data is received.
            fields: The fields to stream.
        """
        with self._stream_lock:
            if self.subscribe_tally >= self._subscribe_tally_max:
                raise ValueError(
                    f"Maximum number of subscriptions reached: {self._subscribe_tally_max}. "
                    "Please unsubscribe some contracts before subscribing to new ones."
                )
            self._contracts_subscribed.add(contract_id)
            if not contract_id in self._private_contracts:
                self._private_contracts.add(contract_id)
                self.subscribe_tally += 1

            fields = {"fields": list(fields), "backout": True}
            fields['fields'] = [str(field) for field in fields['fields']]
            msg = f'smd+{contract_id}+{json.dumps(fields)}'
            self.contract_callbacks[contract_id] = callback
            self.ws.send(msg)
            self._load_progress.update(1)

    def unsubscribe_market_data(self, contract_id):
        """
        Unsubscribe from market data for a given contract ID.
        
        Args:
            contract_id: The contract ID to unsubscribe from.
        """
        with self._stream_lock:
            if contract_id in self.contract_callbacks:
                del self.contract_callbacks[contract_id]
            if contract_id in self._private_contracts:
                self._private_contracts.remove(contract_id)
                self.subscribe_tally -= 1
                self._load_progress.update(-1)
                self._load_progress.refresh()
            msg = f'umd+{contract_id}' + '{}'
            self.ws.send(msg)

    @runAsThread
    def _heartbeat(self):
        """Send a heartbeat message to keep the connection alive"""
        while self.opened:
            self.ws.send("ech+hb")
            time.sleep(10)

    def on_message(self, ws, message):
        """Handle incoming messages from the WebSocket"""
        _ = ws, message
        self._sock_msgs.append(message)
        try:
            message = json.loads(message)
            topic = message.get('topic')
            if 'smd' in topic:
                self.handle_market_data(message)
            else:
                if topic == 'system' and message.get('hb', False):
                    return  # Heartbeat message, do nothing
                elif topic == 'system' and message.get('success', False):
                    self.recv = 1
                    print('[IMPORTANT] Successfully connected to IBKR WebSocket as {}'.format(message.get('success')))

                _NOTIFICATION.notify(
                    title='IBKR WebSocket Message',
                    message=f'Received message on topic {topic}: {message}'
                )
        except json.JSONDecodeError:
            print("Message:", message, datetime.datetime.now())

        if not self.opened:
            self.opened = True
        self.recv += 1
        if self.recv == 2:
            self.networker.initialize()
            for msg in self.stream_messages:
                self.ws.send(msg)

    @staticmethod
    def on_open(ws):
        """Handle WebSocket connection open event"""
        _ = ws
        print("WebSocket connection opened")
        _NOTIFICATION.notify(
            title='IBKR WebSocket Connected',
            message='The IBKR WebSocket connection has been established successfully.'
        )

    @staticmethod
    def on_close(ws, *args):
        """Handle WebSocket connection close event"""
        _ = ws
        _ = args
        print("WebSocket connection closed")
        _NOTIFICATION.notify(
            title='IBKR WebSocket Disconnected',
            message='The IBKR WebSocket connection has been closed.'
        )

    def handle_market_data(self, message):
        """Handle market data messages"""
        self._last_market_data_callback = time.time()
        conidEx = message.get('conidEx', None)
        conid = message['conid']
        topic = message['topic']
        server_id = message['server_id']

        obj = MarketData(
            contract_id=conid,
            server_id=server_id,
            contract_exchange=conidEx,
            topic=topic,
            data=message
        )

        if conid in self.contract_callbacks:
            callback = self.contract_callbacks[conid]
            if callable(callback):
                callback(obj)
            else:
                print(f"Callback for contract ID {conid} is not callable.")


class MKTDispatcher:
    def __init__(self, timeout=60, mode="ASK", dryRun=False):
        """
        Initialize the MKTDispatcher.

        Args:
            timeout (int): Maximum time in seconds to wait for IBKR authentication.
            mode (str): Data transmission mode. Options include "ASK", "ASK+BID+LAST", "FULL_PKL", "FULL_JSON", "PROTOCOL_2".
            dryRun (bool): If True, do not start the WebSocket or client listener.

        Sets up a TCP server socket for client connections, initializes the IBKR WebSocket connection,
        manages client subscriptions, and starts background threads for client and connection management.
        """
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('localhost', 9972))
        self.clients = []
        self.con_id_to_client = {}
        if not dryRun:
            self.ws = IBWss()
            self.ws.interactive_functions[
                'Modify dispatcher configurations interactively'] = self._modify_configs_interactive
            self.ws.on_close = self._on_close
            self._open_ib_wss()
            x = 0
            while not self.ws.networker.authenticated:
                print(f'Waiting for authentication... {x}/{timeout}s')
                time.sleep(1)
                x += 1
                if x == timeout:
                    raise AuthenticationTimeout('Timeout waiting for authentication')

            print('Authenticated')
            # self.conid = int(self.ws.networker.search_contract(self.stock_name)[0].conid)
            # self.ws.stream_market_data(self.conid, self.callback)
            self._add_clients()
        self.mode = mode
        self.caches = {}
        self.cache_values = [IBKRFields.SYMBOL, IBKRFields.LAST_PRICE, IBKRFields.SHORTABLE_SHARES]
        self.shortable_shares_data = ShortableSharesData()
        self._check_clients_live()
        self._thread_lock = threading.Lock()
        self._configs = {
            'Print data packets': False,
            'Use TQDM Progress bar for subscription checking': False,
            'Use TQDM Progress bar for subscription current load': True,
            'Show search results from quick_add': False,
        }

        print('[IMPORTANT] MODE = {}'.format(self.mode))

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
                    self._configs[choice] = new_value
                print(f"Updated {choice} to {self._configs[choice]}")
            else:
                print(f"Invalid configuration: {choice}")

    @staticmethod
    def _on_close(ws, *args):
        _NOTIFICATION.notify(
            title='IBKR WebSocket Disconnected',
            message='The IBKR WebSocket connection has been closed.'
        )
        _ = ws, args
        raise Exception("Connection closed")

    @runAsThread
    def _open_ib_wss(self):
        self.ws.ws.run_forever()

    def _quick_add(self, symbol, client, _retry=True):
        hits = self.ws.networker.search_contract(symbol)
        top_hit = None
        for hit in hits:
            if hit.symbol.lower() == symbol.lower():
                top_hit = hit
                break

        if top_hit is None:
            raise ValueError(f"No contract found for symbol: {symbol}")

        conid = int(top_hit.conid)
        if self._configs['Show search results from quick_add']:
            print('Top hit for search {} is {}'.format(symbol, top_hit.companyHeader))
        if conid in self.con_id_to_client:
            # print(f"Already streaming market data for contract ID {conid}. Adding client to existing stream.")
            self.con_id_to_client[conid].append(client)
            return
        try:
            self.ws.stream_market_data(conid, self.callback)
        except ValueError:
            self._force_check_clients_live(one_alloc=True)
            # self.ws.stream_market_data(conid, self.callback)
            # self._quick_add(symbol, client, _retry=False)
            raise

        shortable_shares_num = self.shortable_shares_data.get_shortable_shares(top_hit.symbol)

        try:
            self.con_id_to_client[conid].append(client)
        except KeyError:
            self.con_id_to_client[conid] = [client]

        # Force update the cache with the shortable shares by sending MarketData with the shortable shares
        # IBKR will never send the shortable shares in the market data stream, so we need to do it manually.
        self._update_cache(
            MarketData(
                contract_id=top_hit.conid,
                server_id=None,
                contract_exchange=None,
                topic='smd',
                data={
                    IBKRFields.SHORTABLE_SHARES: shortable_shares_num,
                    str(IBKRFields.SHORTABLE_SHARES): shortable_shares_num
                },
            ), ib_fields=[IBKRFields.SHORTABLE_SHARES]
        )

    @runAsThread
    def _listen_to_client(self, client: socket.socket):
        while True:
            try:
                data = client.recv(9999).decode()
                if not data:
                    break
                if 'add' in data:
                    search_term = data.split('=')[1].strip()
                    if self._configs['Show search results from quick_add']:
                        print(f"Adding contract {search_term} to stream")
                    self._quick_add(search_term, client)


            except Exception as e:
                print(f"Error receiving data from client: {e}")
                break
        client.close()

    @runAsThread
    def _add_clients(self):
        while True:
            self.sock.listen()
            client, addr = self.sock.accept()
            self.clients.append(client)
            self._listen_to_client(client)

    def _stuff_from_cache(self, data: MarketData, ib_fields: list[IBKRFields] = None) -> MarketData:
        """Given market data is received with only the latest fields not all fields this will stuff the last cached values
        requested fields into the data object."""
        if ib_fields is None:
            ib_fields = self.cache_values

        # print("[LOG]", f'Stuffing from cache for contract ID {data.contract_id} with fields {ib_fields}')

        last_cached = self.caches.get(data.contract_id, {})
        if not last_cached:
            # print("[LOG]", f'No cached values for contract ID {data.contract_id}')
            return data

        # noinspection all
        for field in ib_fields:
            current_value = data.get(field, None, strip_commas=False, string_values=False)
            # print("[LOG]", f'Current value for field {field}: {current_value}', type(current_value))
            if current_value is None or current_value == 'None':
                # If the current value is None, use the last cached value
                cached_value = last_cached.get(field, None)
                # print("[LOG]", f'Using cached value for field {field}: {cached_value}')
                # noinspection all
                if cached_value is not None and cached_value != 'None':
                    data.data[str(field)] = cached_value
                    # print("[LOG]", f'SETTING cached value for field {field}: {cached_value}')

        return data

    def _update_cache(self, data: MarketData, ib_fields: list[IBKRFields] = None):
        """Only updates the cache with the fields that are not None."""
        if ib_fields is None:
            ib_fields = self.cache_values
        if data.contract_id not in self.caches:
            self.caches[data.contract_id] = {}

        for field in ib_fields:
            value = data.get(field, None, strip_commas=False, string_values=False)
            if value is not None and value != 'None' and value != 0.0 and value != '0.0':
                # print(f'Updating cache for field {data.contract_id}:', field, 'with value:', value, 'old value:', self.caches[data.contract_id])
                self.caches[data.contract_id][field] = value
            else:
                continue

    @runAsThread
    def _check_clients_live(self):
        """Send the following character to all clients to check if they are still connected, otherwise
        remove them from the con_id_to_client mapping and unsubscribe from the contract ID if no clients are left."""
        while True:
            time.sleep(5)
            # logging.info("Checking for stale subscriptions...")
            try:
                self._force_check_clients_live()
            except Exception as e:
                print('Unable to check clients live')
                traceback.print_exc()
                _ = e

    def _force_check_clients_live(self, one_alloc=False):
        """
        Force check if clients are still connected by sending a simple character to each client to ping them.
        Contracts with no clients will be unsubscribed from the market data stream.
        :param one_alloc: If true, return when the first stream is closed.
        :return:
        """
        with self._thread_lock:
            x = self.con_id_to_client
            remove_keys = []

            if x.items() == 0:
                return

            if not one_alloc:
                if self._configs['Use TQDM Progress bar for subscription checking']:
                    iterator = tqdm.tqdm(x.items(), desc="Checking subscriptions",
                                         unit="contract")
                else:
                    iterator = x.items()
            else:
                iterator = x.items()

            exit_loop = False
            for contract_id, clients in iterator:
                if exit_loop:
                    break
                for client in clients:
                    try:
                        client.sendall(b'$')  # Send a simple character to check if the client is still connected
                    except (OSError, ConnectionResetError):
                        # logging.warning(f"Client for {contract_id} disconnected. Removing from subscription.")
                        try:
                            self.con_id_to_client[contract_id].remove(client)
                        except ValueError:
                            pass
                        if not self.con_id_to_client[contract_id]:
                            # logging.info(f"No clients left for contract ID {contract_id}. Unsubscribing.")
                            self.ws.unsubscribe_market_data(contract_id)
                            remove_keys.append(contract_id)
                            if one_alloc:
                                # Found the first contract with no clients, exit the loop
                                exit_loop = True
                                break

                    except Exception as e:
                        traceback.print_exc()
                        print(f"Error with _check_clients_live: {e}")

            try:
                for key in remove_keys:
                    del self.con_id_to_client[key]
            except KeyError:
                pass

    def callback(self, data: MarketData):
        """Callback function to handle market data"""
        clients = self.con_id_to_client.get(data.contract_id, [])
        # Stuff the last cached values into the data object

        # Change as required
        self._update_cache(data)
        data = self._stuff_from_cache(data)

        if not clients:
            # print(f"No clients for contract ID {data.contract_id}")
            # stop streaming if no clients are connected
            self.ws.unsubscribe_market_data(data.contract_id)
            # print(f"Unsubscribed from contract ID {data.contract_id} as no clients are connected.")
            return

        for client in clients:
            try:
                # Send the data to the client
                if self.mode == "ASK":
                    client.sendall(str(data.get(IBKRFields.ASK_PRICE)).encode())
                elif self.mode == "ASK+BID+LAST":
                    client.sendall(
                        f"{data.get(IBKRFields.ASK_PRICE)}|{data.get(IBKRFields.BID_PRICE)}|{data.get(IBKRFields.LAST_PRICE)}".encode()
                    )
                elif self.mode == "FULL_PKL":
                    client.sendall(pickle.dumps(data))
                elif self.mode == "FULL_JSON":
                    client.sendall(json.dumps(data.data).encode())
                elif self.mode == "PROTOCOL_2":
                    # Convert to IBKR_CapitalComMKTDataLive and send with protocol 2
                    ibkr_data = IBKR_CapitalComMKTDataLive(
                        shortable_shares=data.get(IBKRFields.SHORTABLE_SHARES, default=0.0),
                        symbol=data.get(IBKRFields.SYMBOL, default='None'),
                        bid=data.get(IBKRFields.BID_PRICE, default=0.0),
                        bid_size=data.get(IBKRFields.BID_SIZE, default=0),
                        ask=data.get(IBKRFields.ASK_PRICE, default=0.0),
                        ask_size=data.get(IBKRFields.ASK_SIZE, default=0),
                        last=data.get(IBKRFields.LAST_PRICE, default=0.0),
                        last_size=0.0,  # Not available for now
                    )
                    final_packet = transmit_mkt_data_with_protocol_2(ibkr_data)
                    if self._configs['Print data packets']:
                        print(f"{client.getpeername()}: {final_packet}")
                    client.sendall(final_packet)


            except (Exception, OSError) as e:
                # print(f"Error sending data to client: {e}")
                if not isinstance(e, OSError):
                    traceback.print_exc()

                try:
                    self.clients.remove(client)
                except ValueError:
                    # client was not in the list, so we can ignore this
                    pass

                if data.contract_id in self.con_id_to_client:
                    try:
                        self.con_id_to_client[data.contract_id].remove(client)
                    except ValueError:
                        pass
                    if not self.con_id_to_client[data.contract_id]:
                        # print(f"Removing contract ID {data.contract_id} from con_id_to_client as no clients are left.")
                        del self.con_id_to_client[data.contract_id]
                        # print(f"Unsubscribing from contract ID {data.contract_id} as no clients are left.")
                        self.ws.unsubscribe_market_data(data.contract_id)


if __name__ == '__main__':
    def main():
        print('Running IBKR Reversed... Starting MKTDispatcher...')
        try:
            dispatcher = MKTDispatcher(mode=IBKRModes.PROTOCOL_2)
            dispatcher.ws.interactive_mode()
        except AuthenticationTimeout:
            print('Authentication timed out. Attempting to fetch new credentials...')
            from argus.ib.set_auth import update_cookies
            update_cookies(write_env=True)
            main()
            exit(0)

        input('Press enter to exit...\n')


    main()
