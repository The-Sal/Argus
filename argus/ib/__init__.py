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
from argus.ib.fields import IBKRFields, SearchResult
from argus.capital import transmit_mkt_data_with_protocol_2
from argus.ib._shortable_shares_data import ShortableSharesData
from argus.ib._ib_utils import (LockedSession, IBKRModes, IBKR_CapitalComMKTDataLive,
                                AuthenticationTimeout, MarketData, IBError, NOTIFICATION as _NOTIFICATION,
                                IB_Cache as _IB_Cache, Account, MarketDataRefused, STK_Position, FakeSocket, enforce_currency, expand_exception_decorator, AccountBalances)
from argus._argus_utils import throw_fuss

# noinspection PyUnresolvedReferences
from argus.capital import Protocol2Parser

# enable logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class ProtectedAssetViolation(IBError):
    """Raised when attempting to unsubscribe from a protected asset."""
    pass


# NOTE: DO NOT ADD THREAD-LOCKS TO IBNetworker, The LockedSession is already thread-safe
# the thread-locks for LockedSession are available for the .get and .post methods which covers
# 99.99% of all the calls within IBNetworker and derived classes.
# Adding additional thread-locks will only serve to create deadlocks and other issues.
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
            'portfolio_accounts': 'https://api.ibkr.com/v1/api/portfolio/accounts',
            'account_ledger': 'https://api.ibkr.com/v1/api/portfolio/{}/ledger',
            'account_summary': 'https://api.ibkr.com/v1/api/portfolio/{}/summary',
            'account_positions': 'https://api.ibkr.com/v1/api/portfolio/{}/positions'
        }
        self.tickle = self.setup_msgs[0]
        self.auth_stats = self.setup_msgs[1]
        self.authenticated = False
        self.forbidden_strings = [
            'NYMEX'
        ]
        self._order_data = {}
        self._trading_account_id = None
        self._ledger_data = None
        self._account_summary = None

    def set_trading_account_id(self, account_id):
        if self._trading_account_id is not None:
            raise RuntimeError('Changing trading account ID is not allowed. ')
        self._trading_account_id = account_id
        self.setup_trading_account_data()

    def get_account_ledger(self):
        account_ledger = self.session.get(self.urls['account_ledger'].format(self._trading_account_id)).json()
        return account_ledger

    def setup_trading_account_data(self):
        logging.info(f"Setting up trading account data for account ID: {self._trading_account_id}")
        # ADD THIS CRITICAL CALL:
        positions_response = self.session.get(self.urls['account_positions'].format(self._trading_account_id))
        positions = positions_response.json()
        logging.info(f"Portfolio Positions: {positions}")

        account_summary = self.session.get(self.urls['account_summary'].format(self._trading_account_id)).json()
        self._ledger_data = self.get_account_ledger()
        self._account_summary = account_summary
        logging.info(f"Account Ledger:\n{'*' * 50}\n{self._ledger_data}\n{'*' * 50}")
        logging.info(f"Account Summary:\n{'*' * 50}\n{account_summary}\n{'*' * 50}")

        # set account

        # You're maybe wondering why this is not in the dict well the reason
        # is that for some reason when it's inside the dictionary the
        #  request fails. Don't ask me why it's just one of those things
        _url = 'https://www.interactivebrokers.co.uk/portal.proxy/v1/portal/iserver/account'
        print(f"Setting trading account to {self._trading_account_id}")
        response = self.session.post(
            url=_url,
            json={"acctId": self._trading_account_id}
        )

        throw_fuss(response.text, notify=False)

    def get_all_trading_accounts_ids(self):
        response = self.session.get(self.urls['portfolio_accounts'])
        try:
            data = response.json()
            return list(map(Account.from_dict, data))
        except json.JSONDecodeError:
            raise IBError(f"Failed to decode JSON response from {self.urls['portfolio_accounts']}. "
                          f"Response: {response.text}")

    # Note: run setup messages is blocking after which the rest is all on threads
    # and will return the function immediately
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
                # print("User is authenticated", 'Response:', data)
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
        try:
            response = self.session.post(self.urls['search'], json=payload).json()
        except json.JSONDecodeError:
            raise IBError(f"Failed to decode JSON response from {self.urls['search']}. "
                          f"Response: {response.text}")
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

    def fetch_account_positions(self) -> list[STK_Position]:
        """Fetch account positions for the given IBKR Account id. Supports STK only!"""
        response = self.session.get(
            self.urls['account_positions'].format(self.trading_account_id)
        )
        try:
            data = response.json()
            portfolio = []
            for asset in data:
                if not isinstance(asset, dict):
                    print(f"Skipping asset because it's not a dict: {asset}")
                    continue
                if asset["assetClass"] != "STK":
                    continue

                portfolio.append(STK_Position.from_dict(asset))
            return portfolio
        except json.JSONDecodeError:
            raise IBError(f"Failed to decode JSON response from {self.urls['account_positions']}. "
                          f"Response: {response.text}")

    @property
    def trading_account_id(self):
        return self._trading_account_id

    @property
    def ledger_data(self):
        return self._ledger_data

    @property
    def account_summary(self):
        return self._account_summary


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
        self._ready = False
        self.stream_messages = [
            'sor+{}',
            'upl+{}',
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
        self._max_contract_buffer = 5

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
            'Socket Still Open': lambda: print(f'Socket still open: {self.test_conn()}'),
        }

        self._protected_assets = set()
        self._pnl_subscriptions = []

    def write_protected_assets(self, assets: list[str]):
        """Write a list of protected assets that should not be unsubscribed no matter what."""
        # yes we need to lock-this, because we are adding to a set that
        # will be read by `unsubscribe_market_data` which is also called on a different threads
        with self._stream_lock:
            self._protected_assets = set(assets)

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
                                   IBKRFields.SYMBOL, IBKRFields.FORMATTED_UNREALIZED_PNL)):
        """
        Stream market data for a given contract ID.

        Args:
            contract_id: The contract ID to stream market data for.
            callback: The callback function to call when market data is received.
            fields: The fields to stream.
        """
        with self._stream_lock:
            if (self.subscribe_tally + self._max_contract_buffer) >= self._subscribe_tally_max:
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
            if contract_id in self._protected_assets:
                raise ProtectedAssetViolation(
                    f"Cannot unsubscribe from protected asset: {contract_id}"
                )
            if contract_id in self.contract_callbacks:
                del self.contract_callbacks[contract_id]
            if contract_id in self._private_contracts:
                self._private_contracts.remove(contract_id)
                self.subscribe_tally -= 1
                self._load_progress.update(-1)
                self._load_progress.refresh()
            msg = f'umd+{contract_id}' + '{}'
            self.ws.send(msg)


    def on_message(self, ws, message):
        try:
            """Handle incoming messages from the WebSocket"""
            _ = ws, message
            self._sock_msgs.append(message)
            try:
                message = json.loads(message)
                topic = message.get('topic')
                if 'smd' in topic:
                    self.handle_market_data(message)
                elif 'spl' in topic:
                    self.handle_account_pnl(message)

                else:
                    if topic == 'system' and message.get('hb', False):
                        return  # Heartbeat message, do nothing
                    elif topic == 'system' and message.get('success', False):
                        self.recv = 1
                        print(
                            '[IMPORTANT] Successfully connected to IBKR WebSocket as {}'.format(message.get('success')))

                    _NOTIFICATION.notify(
                        title='IBKR WebSocket Message',
                        message=f'Received message on topic {topic}: {message}'
                    )
            except json.JSONDecodeError:
                if not message.decode() == 'ech+hb':
                    print("Message:", message, datetime.datetime.now())

            if not self.opened:
                self.opened = True
            self.recv += 1
            if self.recv == 2:
                self._boot()
        except Exception as e:
            traceback.print_exc()
            raise e

    def _boot(self):
        self.networker.initialize()
        for msg in self.stream_messages:
            self.ws.send(msg)

        self._ready = True

    def wait_till_read(self):
        """Wait until the WebSocket is ready to receive messages"""
        start_time = time.time()
        while not self._ready:
            time.sleep(1)
            logging.info(
                'Waiting for WebSocket to be ready.. Time elapsed: {:.2f} seconds'.format(time.time() - start_time))
        return True

    @staticmethod
    def on_open(ws):
        """Handle WebSocket connection open event"""
        _ = ws
        print("WebSocket connection opened")
        _NOTIFICATION.notify(
            title='IBKR WebSocket Connected',
            message='The IBKR WebSocket connection has been established successfully.'
        )

    def on_close(self, ws, *args):
        """Handle WebSocket connection close event"""
        _ = ws
        _ = args
        print("WebSocket connection closed")
        throw_fuss(
            msg="IBKR WebSocket connection closed unexpectedly. Please restart the application.",
            boarder="="
        )
        _NOTIFICATION.notify(
            title='IBKR WebSocket Disconnected',
            message='The IBKR WebSocket connection has been closed.'
        )
        self.opened = False
        raise RuntimeError("WebSocket connection closed")

    def handle_market_data(self, message):
        """Handle market data messages"""
        try:
            self._last_market_data_callback = time.time()
            conidEx = message.get('conidEx', None)
            conid = message.get('conid', None)

            if conid is None:
                print('Market data message missing conid:', message)
                return

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
                    print(f"Callback for contract ID {conid} is not callable. Data:")
                    print(obj.__dict__)
        except Exception as e:
            print("FAILED TO HANDLE MARKET DATA MESSAGE")
            print(message)
            traceback.print_exc()
            raise e

    def test_conn(self):
        # try to send a heartbeat if the connection is lost it will raise an exception
        try:
            self.ws.send_text("ech+hb")
            return True
        except websocket.WebSocketConnectionClosedException:
            return False

    @property
    def private_contracts(self):
        return list(self._private_contracts)


    @runAsThread
    def _heartbeat(self):
        """Send a heartbeat message to keep the connection alive"""
        while True:
            if self.opened:
                self.ws.send("ech+hb")
                time.sleep(10)
            else:
                logging.warning(
                    "Attempted to send heartbeat before WebSocket was opened. Make websocket is opened first."
                )
                time.sleep(5)

    @runAsThread
    def run(self):
        try:
            self.ws.run_forever()
        except Exception as e:
            print("WEBSOCKET FAILED TO RUN")
            traceback.print_exc()
            raise e

    def subscribe_to_portfolio(self, callback):
        """Subscribe to portfolio updates. """
        self._pnl_subscriptions.append(callback)


    def handle_account_pnl(self, message):
        """Stub for now. Expect to send down the market-data rails"""
        for callback in self._pnl_subscriptions:
            callback(AccountBalances.from_dict(message))


class AccountProvider:
    """
    Provides live-streaming support for account positions and PnL. This class unfortunately
    requires pulling some interesting tricks to get working the major issue is we do not
    have a public API to get live-streaming of account positions and PnL so we have to
    leverage the GET requests, live market data and some internal bookkeeping to get it working. And
    also re-synchronize the positions between our internal bookkeeping and the real account to make sure
    we haven't left reality. We have finally figured out how to implement this and it's as follows:

    [IBWss]  <---- [IBNetworker]
       |______________|_____________|
       |              |             |
       V              V             V
      [AccountProvider] <-----> [MKTDispatcher] <-----> [Clients]


    Essentially we have IBWss and IBNetworker as the two main components that interact with IBKR,
    MKTDispatcher initializes these two components and manages client connections and subscriptions.
    It then configures them per the users wishes, then MKTDispatcher
    passes it's configured IBWsss and IBNetworker to AccountProvider which then uses them to provide
    live-streaming support for account positions and PnL. This data then gets forwarded to MKTDispatcher
    and then further out to clients. Also as to how does AccountProvider get live-streaming of account positions and PnL?
    Thats were the magic happens, MKTDispatcher adds AccountProvider as a "client" to itself using FakeSocket
    for the assets required to be streamed for the account positions and PnL. This way AccountProvider
    gets all the market data for the assets it needs to track the account positions and PnL. And within AccountProvider
    it has direct access to IBWss and it adds these assests to the protect assets so they never get unsubscribed from.

    Long/Short
    ===============================
    MKTDispatcher (init):
    1) [IBNetworker] (init) --> [IBWss] (init)
    2) [AccountProvider] (init with IBWss and IBNetworker)

    AccountProvider (init) calls:
    1) [IBNetworker] (fetch_account_positions) --> Sets up initial portfolio
    2) [IBWss] (write_protected_assets) --> Protect assets from unsubscription

    MKTDispatcher (post-AccountProvider init):
    1) [MKTDispatcher] (_add_clients) --> [AccountProvider] (as FakeSocket client)

    AccountProvider (as FakeSocket client):
    1) Market data comes in --> Updates internal positions and PnL
    2) Updates stats and calls MKTDispatcher via callback to forward data to real clients

    MKTDispatcher (on_portfolio_change) [callback]:
    1) Forwards data to real clients
    """

    def __init__(self, init_ib_wss: IBWss, init_ib_networker: IBNetworker):
        if init_ib_networker.trading_account_id is None:
            raise ValueError("Trading account ID is not set in IBNetworker.")

        self._ib_wss = init_ib_wss
        self._ib_networker = init_ib_networker
        self._account_positions = self._ib_networker.fetch_account_positions()
        self._account_ledger = self._ib_networker.get_account_ledger()

        # Represents the current portfolio as a dictionary of conid to STK_Position
        # STK_Positions are lively updated via market data callbacks
        self._portfolio = {}
        self._account_balances: AccountBalances = None

        self._symbols_to_conids = {}
        self.ss = ShortableSharesData()

        print("*" * 50)
        print("ACCOUNT POSITIONS:")
        print(self._account_positions)
        print("*" * 50)

        self._fake_socket = FakeSocket(callback=self._on_market_data)
        self._populate_conids()
        self._ib_wss.subscribe_to_portfolio(self._on_account_balances)

        ##################################################
        # DEBUG SOCKETS AND CODE
        ###################################################

        # While clients may use this for the time being
        # it's primarily for debugging and development purposes
        # This data is sent in the format of ~{JSON}L and transmits
        # both account balances and positions updates
        # Note: For internal testing the sum(PnL) per position from the
        # stock-based PnL we send are MORE accurate than the
        # account-based PnL we get from IBKR directly.
        # Usually the difference is ~0.01 presumably due to rounding inside IBKR

        self._debug_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._debug_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._debug_clients = set()
        self._debug_listener()
        self._last_send = time.time()
        self._debug_lock = threading.Lock()
        self.propagate_at_freq(interval=10)

    @runAsThread
    def propagate_at_freq(self, interval=10):
        """Every interval seconds propagate the entire portfolio and account balances to the debug socket."""
        while True:
            time.sleep(interval)
            for position in self._portfolio.values():
                self._transmit(position)
            self._transmit(None)

    @runAsThread
    def _debug_listener(self):
        self._debug_socket.bind(('localhost', 9973))
        while True:
            self._debug_socket.listen()
            accepted, addr = self._debug_socket.accept()
            del addr
            self._debug_clients.add(accepted)

    def _debug_propagate(self, message: str):
        for client in list(self._debug_clients):
            try:
                client.sendall(message.encode())
            except (ConnectionResetError, BrokenPipeError):
                self._debug_clients.remove(client)

    def _transmit(self, asset: STK_Position | None):
        """
        Convert the data into JSON and then frames it with ~{JSON}L and sends it to the debug socket.
        Args:
            asset (STK_Position | None): The asset to transmit. If None, transmit account balances.
        """
        with self._debug_lock:
            self._last_send = time.time()
            if asset is None:
                if self._account_balances is None:
                    return None
                data = {
                    'type': 'account_balances',
                    'data': self._account_balances.to_dict()
                }

            else:
                data = {
                    'type': 'position',
                    'data': asset.to_dict()
                }

            self._debug_propagate(f"~{json.dumps(data)}L")
            return None

    def _on_account_balances(self, data: AccountBalances):
        if data.net_liquidation is None:
            return
        self._account_balances = data
        self._transmit(None)

    @expand_exception_decorator('AccountProvider._on_market_data', propagate=False)
    def _on_market_data(self, data: IBKR_CapitalComMKTDataLive):
        """Handle market data received via FakeSocket"""
        if not isinstance(data, IBKR_CapitalComMKTDataLive):
            # it could be a ping
            if isinstance(data, str) or data == b'$' or data == '$':
                return

            print(f"Received unexpected data type: {type(data)}")
            print("Value:", data)
        try:
            contract_id = int(self.ss.translate_symbol_to_conid(data.symbol))
        except TypeError:
            print(f"Could not translate symbol {data.symbol} to contract ID. Obj={data.__dict__}")
            return
        position: STK_Position = self._portfolio.get(contract_id)
        cost = enforce_currency(position.avg_cost)
        if data.last != 0:
            pnl = (enforce_currency(data.last) - cost) * float(position.position)
            position.formatted_unrealized_pnl = f"{pnl:.2f}"
            position.unrealized_pnl = pnl
            self._transmit(position)

    def required_assets(self) -> list[int]:
        """Return a list of contract IDs required to be streamed for account positions and PnL."""
        return list(self._portfolio.keys())

    def _populate_conids(self):
        """We should maintain a dictionary of coinds to positions for quick lookup to update the system"""
        for position in self._account_positions:
            self._portfolio[position.conid] = position

        self._ib_wss.write_protected_assets(list(self._portfolio.keys()))

    @property
    def socket(self):
        return self._fake_socket

    @property
    def account_positions(self) -> list[STK_Position]:
        return list(self._portfolio.values())

    @property
    def account_balances(self) -> AccountBalances:
        return self._account_balances


class MKTDispatcher:
    def __init__(self, timeout=60, mode="ASK", dryRun=False, host='localhost', port=9972):
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
        self.sock.bind((host, port))
        self.clients = []
        self.con_id_to_client = {}
        self.account_provider: AccountProvider = None
        if not dryRun:
            self.ws = IBWss()
            self.ws.interactive_functions[
                'Modify dispatcher configurations interactively'
            ] = self._modify_configs_interactive
            self.ws.on_close = self._on_close
            self._open_ib_wss()
            self.ws.wait_till_read()
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
            'Block New MKT Data': True,  # this is to make sure on first-pass we wait till account id is set,
            # from that point it will be False and can be updated interactively.
            'Show blocked MKT Data Warning': False
        }
        self.host = host
        self.port = port
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
        throw_fuss(
            msg="IBKR WebSocket connection closed unexpectedly. Please restart the application.",
            boarder="="
        )
        _NOTIFICATION.notify(
            title='IBKR WebSocket Disconnected',
            message='The IBKR WebSocket connection has been closed.'
        )
        _ = ws, args

        raise Exception("Connection closed")

    def _open_ib_wss(self):
        self.ws.run()

    def _quick_add(self, symbol, client, _retry=True, conid=None):
        if self._configs['Block New MKT Data']:
            raise MarketDataRefused(
                "New market data subscriptions are blocked. "
                "Please enable 'Block New MKT Data' in the dispatcher configurations.")

        if symbol is None and conid is None:
            raise RuntimeError("Either symbol or conid must be provided to quick_add.")

        if conid is None:
            hits = self.ws.networker.search_contract(symbol)
            top_hit = None
            for hit in hits:
                if hit.symbol.lower() == symbol.lower():
                    top_hit = hit
                    break

            if top_hit is None:
                raise ValueError(f"No contract found for symbol: {symbol}")

            conid = int(top_hit.conid)
        else:
            class DummyTopHit:
                pass

            top_hit = DummyTopHit()
            setattr(top_hit, 'conid', conid)
            setattr(top_hit, 'symbol', 'Unknown')
            setattr(top_hit, 'companyHeader', 'Unknown')

        if self._configs['Show search results from quick_add']:
            print('Top hit for search {} is {}'.format(symbol, top_hit.companyHeader))
        if conid in self.con_id_to_client:
            # print(f"Already streaming market data for contract ID {conid}. Adding client to existing stream.")
            self.con_id_to_client[conid].append(client)
            return
        try:
            self.ws.stream_market_data(conid, self.callback)
        except ValueError:
            # self._force_check_clients_live(one_alloc=True)
            # self.ws.stream_market_data(conid, self.callback)
            # self._quick_add(symbol, client, _retry=False)
            raise

        if top_hit is None:
            top_hit = "Unknown"
            shortable_shares_num = self.shortable_shares_data.get_shortable_shares_by_conid(conid)
        else:
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
            ), ib_fields=[IBKRFields.SHORTABLE_SHARES, IBKRFields.FORMATTED_UNREALIZED_PNL]
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

                elif 'conid' in data:
                    conid_str = data.split('=')[1].strip()
                    try:
                        conid = int(conid_str)
                    except ValueError:
                        client.sendall(b'Invalid conid format. Must be an integer. ')
                        continue
                    if self._configs['Show search results from quick_add']:
                        print(f"Adding contract ID {conid} to stream")
                    self._quick_add(symbol=None, conid=conid, client=client)


            except MarketDataRefused:
                if self._configs['Show blocked MKT Data Warning']:
                    client.sendall(b'New market data subscriptions are blocked. ')
                    raise

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
            x = self.con_id_to_client.copy()
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
        if not isinstance(data, MarketData):
            raise RuntimeError("Callback received non-MarketData object")
        clients = self.con_id_to_client.get(data.contract_id, [])
        # Stuff the last cached values into the data object

        # Change as required

        # print(data.data)  # Note: This is for debugging to see the 'raw' market data received from IBKR
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
                        bid=enforce_currency(data.get(IBKRFields.BID_PRICE, default=0.0)),
                        bid_size=enforce_currency(data.get(IBKRFields.BID_SIZE, default=0)),
                        ask=enforce_currency(data.get(IBKRFields.ASK_PRICE, default=0.0)),
                        ask_size=enforce_currency(data.get(IBKRFields.ASK_SIZE, default=0)),
                        last=enforce_currency(data.get(IBKRFields.LAST_PRICE, default=0.0)),
                        last_size=0.0,  # Not available for now,
                        # PER VERSION ARGUS 0.0.4 THIS IS NOT IMPLEMENTED IN PROTOCOL 2 YET
                        unrealized_pnl=enforce_currency(data.get(IBKRFields.FORMATTED_UNREALIZED_PNL, default=0.0)),
                    )
                    if client.idx != 'real':
                        client.sendall(ibkr_data)
                        continue

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

    def select_account_interactive(self):
        accounts_available = self.ws.networker.get_all_trading_accounts_ids()
        for index, account in enumerate(accounts_available):
            print(f"{index + 1}. {account.accountId}")

        account_choice = input("Select an account by number (default is 1): ")
        if account_choice.strip() == '':
            account_choice = 0
        else:
            account_choice = int(account_choice) - 1
        selected_account = accounts_available[account_choice]
        print(f"Selected account: {selected_account.accountId}")
        self.ws.networker.set_trading_account_id(selected_account.accountId)
        if self.mode == IBKRModes.PROTOCOL_2:
            self.account_provider = AccountProvider(self.ws, self.ws.networker)
            self._configs['Block New MKT Data'] = False
            print("New market data subscriptions are now unblocked.")
            # If this fails the program should crash
            for conid in self.account_provider.required_assets():
                # hence why this is main-threaded
                self._quick_add(symbol=None, client=self.account_provider.socket, _retry=False, conid=conid)
            print('{} protected assets added from AccountProvider'.format(len(self.account_provider.required_assets())))

            self.ws.ws.send('upl+{}')
            time.sleep(1)
            self.ws.ws.send('spl+{}')
        else:
            self._configs['Block New MKT Data'] = False
            print("New market data subscriptions are now unblocked.")
            print("Warning: AccountProvider is not initialized in this mode, so account positions and PnL will not be available.")
            print("Please switch to PROTOCOL_2 mode to enable account positions and PnL streaming.")


if __name__ == '__main__':
    def main():
        print('Running IBKR Reversed... Starting MKTDispatcher...')
        try:
            dispatcher = MKTDispatcher(mode=IBKRModes.PROTOCOL_2)
            dispatcher.select_account_interactive()
            dispatcher.ws.interactive_mode()
        except AuthenticationTimeout:
            print('Authentication timed out. Attempting to fetch new credentials...')
            from argus.ib.set_auth import update_cookies
            update_cookies(write_env=True)
            main()
            exit(0)

        input('Press enter to exit...\n')


    main()
