import logging
import os
import pickle
import time
import json
import socket
import datetime
import traceback
import websocket
from utils3 import runAsThread
from utils3.networking import Session
from argus.ib.fields import IBKRFields, SearchResult
from argus.capital import DomainCache, transmit_mkt_data_with_protocol_2, CapitalComMKTDataLive

# enable logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class IBKRModes:
    ASK = 'ASK'
    ASK_BID_LAST = 'ASK+BID+LAST'
    FULL_PKL = 'FULL_PKL'
    FULL_JSON = 'FULL_JSON'
    PROTOCOL_2 = 'PROTOCOL_2'


class IBKR_CapitalComMKTDataLive(CapitalComMKTDataLive):
    """This class is a extension of the CapitalComMKTDataLive class to support IBKR fields. Its only
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
        data.insert(len(data) - 1 - 2, str(self.shortable_shares))
        bytes_packet = ",".join(data).encode('ascii')
        # print('After inserting shortable_shares, data is:', data, 'length:', len(data))
        return bytes_packet


_IB_Cache = DomainCache('IBKR')


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
        self.session = Session(headers=self.headers)
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
            'search': 'https://www.interactivebrokers.co.uk/portal.proxy/v1/portal/iserver/secdef/search'
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
            time.sleep(60 * 5)
            response = self.session.post(self.auth_stats['url'])
            data = response.json()
            if data.get('authenticated', False):
                self.authenticated = True
                print("User is authenticated")
            else:
                print("User is not authenticated")
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
            print(f"Error parsing search results: {response}")
            traceback.print_exc()
            raise e

        return results


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

    # noinspection all
    def stream_market_data(self, contract_id, callback,
                           fields=(IBKRFields.LAST_PRICE, IBKRFields.ASK_PRICE, IBKRFields.ASK_SIZE,
                                   IBKRFields.BID_PRICE, IBKRFields.BID_SIZE, IBKRFields.SHORTABLE_SHARES,
                                   IBKRFields.SYMBOL)):
        """
        Stream market data for a given contract ID.
        """
        fields = {"fields": list(fields), "backout": True}
        fields['fields'] = [str(field) for field in fields['fields']]
        msg = f'smd+{contract_id}+{json.dumps(fields)}'
        self.contract_callbacks[contract_id] = callback
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
        try:
            message = json.loads(message)
            topic = message.get('topic')
            if 'smd' in topic:
                self.handle_market_data(message)
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

    @staticmethod
    def on_close(ws, *args):
        """Handle WebSocket connection close event"""
        _ = ws
        print("WebSocket connection closed")

    def handle_market_data(self, message):
        """Handle market data messages"""
        conidEx = message['conidEx']
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
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(('localhost', 9972))
        self.clients = []
        self.con_id_to_client = {}
        if not dryRun:
            self.ws = IBWss()
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
        print('[IMPORTANT] MODE = {}'.format(self.mode))

    def _on_close(self, ws, *args):
        raise Exception("Connection closed")

    @runAsThread
    def _open_ib_wss(self):
        self.ws.ws.run_forever()

    def _quick_add(self, symbol, client):
        hits = self.ws.networker.search_contract(symbol)
        top_hit = None
        for hit in hits:
            if hit.symbol.lower() == symbol.lower():
                top_hit = hit
                break

        if top_hit is None:
            raise ValueError(f"No contract found for symbol: {symbol}")

        conid = int(top_hit.conid)
        print('Top hit for search {} is {}'.format(symbol, top_hit.companyHeader))
        # print('Second Top hit for search {} is {}'.format(search_term, hits[1].companyHeader))
        #
        # print(top_hit)
        # print(hits[1])

        if conid in self.con_id_to_client:
            self.con_id_to_client[conid].append(client)
            return
        self.ws.stream_market_data(conid, self.callback)
        try:
            self.con_id_to_client[conid].append(client)
        except KeyError:
            self.con_id_to_client[conid] = [client]

    @runAsThread
    def _listen_to_client(self, client: socket.socket):
        while True:
            try:
                data = client.recv(9999).decode()
                if not data:
                    break
                if 'add' in data:
                    search_term = data.split('=')[1].strip()
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

        for field in ib_fields:
            current_value = data.get(field, None, strip_commas=False, string_values=False)
            # print("[LOG]", f'Current value for field {field}: {current_value}', type(current_value))
            if current_value is None or current_value == 'None':
                # If the current value is None, use the last cached value
                cached_value = last_cached.get(field, None)
                # print("[LOG]", f'Using cached value for field {field}: {cached_value}')
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

        # print("[LOG]", f'Updating cache for contract ID {data.contract_id} with fields {ib_fields}')

        for field in ib_fields:
            value = data.get(field, None, strip_commas=False, string_values=False)
            # print("[LOG]", f'Field {field} has value {value}')
            if value is not None and value != 'None':
                # print('SETTING CACHE', data.contract_id, field, value, 'INSTANCE:', type(value))
                self.caches[data.contract_id][field] = value
            else:
                # If the value is None, do not update the cache for that field
                continue


    def callback(self, data: MarketData):
        """Callback function to handle market data"""
        clients = self.con_id_to_client.get(data.contract_id, [])
        # Stuff the last cached values into the data object

        # Change as required
        self._update_cache(data)
        data = self._stuff_from_cache(data)

        if not clients:
            print(f"No clients for contract ID {data.contract_id}")
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
                        shortable_shares=data.get(IBKRFields.SHORTABLE_SHARES, 0.0),
                        symbol=data.get(IBKRFields.SYMBOL, default='None'),
                        bid=data.get(IBKRFields.BID_PRICE, default=0.0),
                        bid_size=data.get(IBKRFields.BID_SIZE, default=0),
                        ask=data.get(IBKRFields.ASK_PRICE, default=0.0),
                        ask_size=data.get(IBKRFields.ASK_SIZE, default=0),
                        last=data.get(IBKRFields.LAST_PRICE, default=0.0),
                        last_size=0.0,  # Not available for now
                    )
                    final_packet = transmit_mkt_data_with_protocol_2(ibkr_data)
                    print(f"->{client.getpeername()}: {final_packet}")
                    client.sendall(final_packet)


            except Exception as e:
                print(f"Error sending data to client: {e}")
                traceback.print_exc()
                self.clients.remove(client)
                if data.contract_id in self.con_id_to_client:
                    self.con_id_to_client[data.contract_id].remove(client)
                    if not self.con_id_to_client[data.contract_id]:
                        del self.con_id_to_client[data.contract_id]


def main():
    ib_wss = IBWss()
    # Technically you want to subclass ib_wss to get market data, but this is a 'proof of concept'
    import numpy

    con_id_to_name = {}
    while True:
        contract_name = input('Enter contracts you would like to search for: ')
        if contract_name == 'exit':
            print('Exiting...')
            break
        search_results = ib_wss.networker.search_contract(contract_name)
        for i in range(len(search_results)):
            print(f"{i}: {search_results[i].companyHeader}, {search_results[i].symbol},{search_results[i].conid}")

        # choice
        choice = input('Pick the number of the contract you want to stream: ')
        if choice.isdigit() and int(choice) < len(search_results):
            con_id_to_name[search_results[int(choice)].conid] = search_results[int(choice)].symbol
        else:
            print('Invalid choice. Contract not added.')
            continue

    print('Contracts to stream:', list(con_id_to_name.values()))

    def start_stream_contract_after_delay(delay, contract_id):
        time_takens = []
        last_time = None

        @runAsThread
        def delayed_stream():
            time.sleep(delay)
            input('Hit enter to start streaming for contract_id: ' + str(contract_id))

            def print_mkt_data(data: MarketData):
                nonlocal last_time, time_takens
                current_time = time.time()
                if last_time is not None:
                    time_takens.append(current_time - last_time)
                last_time = current_time
                avg_time = numpy.mean(time_takens) if time_takens else 0
                # x = subprocess.check_output(['clear']).decode()
                # print(x, end='')
                print(
                    f"Contract: {con_id_to_name[str(data.contract_id)]}, Last Price: {data.get(IBKRFields.LAST_PRICE)}, Bid Price: {data.get(IBKRFields.BID_PRICE)}, Bid Size: {data.get(IBKRFields.BID_SIZE)}, "
                    f"Ask Price: {data.get(IBKRFields.ASK_PRICE)}, Ask Size: {data.get(IBKRFields.ASK_SIZE)}, Avg Callback Time: {avg_time:.2f}s"
                )

            print(f'Attempting to stream contract {contract_id}...')
            ib_wss.stream_market_data(contract_id, lambda data: print_mkt_data(data), )

        delayed_stream()

    for key, value in con_id_to_name.items():
        print(f"Starting stream for contract {key} ({value})")
        start_stream_contract_after_delay(0, int(key))

    ib_wss.ws.run_forever()


if __name__ == '__main__':
    def main():
        print('Running IBKR Reversed... Starting MKTDispatcher...')
        try:
            dispatcher = MKTDispatcher(mode=IBKRModes.ASK)
        except AuthenticationTimeout:
            print('Authentication timed out. Attempting to fetch new credentials...')
            from argus.ib.set_auth import update_cookies
            update_cookies(write_env=True)
            main()
            exit(0)

        input('Press enter to exit...\n')


    main()