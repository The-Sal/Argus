"""
This module provides classes and methods to interact with Interactive Brokers' Forecasting Contracts (FXC)
via WebSocket connections. It includes the FXCWss class for managing WebSocket connections and
the FXCDispatcher class for dispatching market data and handling client interactions.
"""
import os
import copy
import json
import time
import base64
import socket
import inspect
import threading
import traceback
from utils3 import runAsThread
from tempfile import gettempdir
from argus.ib.fields import IBKRFields
from argus.ib import IBWss, MKTDispatcher
from argus.ib._forcast_utils import AbstractMarket, FxContractBig, FxCMarketNotFinishedResolution, NoValueMarketData, \
    AbstractionError
from argus.ib._ib_utils import (
    throw_fuss, NOTIFICATION as _NOTIFICATION, expand_exception_decorator, AbstractSocketMessage, MarketData,
    IBKRModes
)


def apply_some_lock(lock_name):
    def decorator(func):
        def wrapper(self, *args, **kwargs):
            lock = getattr(self, lock_name)
            with lock:
                return func(self, *args, **kwargs)

        return wrapper

    return decorator


class FXCWss(IBWss):
    """
    This is the same as IBWss but for Forecasting Contracts within IBKR.

    Notes:
        YOU CANNOT USE it with MKTDispatcher.
        YOU CANNOT USE IBWss AND FXCWss TOGETHER.
        There can only be one WebSocket connection to IBKR at a time.
    """

    def __init__(self, cookie=os.getenv('IB_COOKIE')):
        super().__init__(cookie=cookie)
        self.url = "wss://forecasttrader.interactivebrokers.ie/portal.proxy/v1/etp/ws"
        self._topic_routing_table = {
            'act': self._act_handler,
            'smd': self.handle_market_data,  # Underlying IBWss handler
            'system': self._system_handler,
            'sts': self._status_handler,
            'spl': self.handle_account_pnl  # Underlying IBWss handler
        }
        self._conn_statistics = {
            'topics_received': [],
            'messages_received': 0,
        }

        self._socket_pipline: list[AbstractSocketMessage] = []
        self._private_send_msg = copy.copy(self.ws.send)
        self.ws.send = self._send_with_monitoring
        self.authenticated_semaphore = threading.Semaphore(0)
        self._configs = {
            'Translate Socket Messages': True,
            'Realtime Logging': False,
            'Realtime Logging Interval': 1,
            'Pause realtime logging': False,
        }

        self.interactive_functions.update({
            'Modify FxcWss Configs': self.modify_configs_interactive,
        })

    ############################
    # Utility Methods
    ############################

    def _send_with_monitoring(self, msg, *args, **kwargs):
        """Send a message and monitor the sending process"""
        try:
            self._private_send_msg(msg, *args, **kwargs)
            self._socket_pipline.append(
                AbstractSocketMessage(content=msg, origin='sent')
            )
        except Exception as e:
            print(f"Error sending message: {e}")
            raise

    def _write_sock_msgs_to_file(self, verbose=True,
                                 filename=f"fxc_socket_messages_{int(time.time())}.log"):
        """
        Write received socket messages to a file for debugging purposes
        Args:
            verbose: Whether to print a message when done
            filename: The filename to write to. If None, returns the content as a string
        Returns:
            None if filename is provided, otherwise the content as a string
        """
        if not self._sock_msgs:
            return None

        consolidated_stats = self._conn_statistics.copy()
        consolidated_stats['total_messages'] = len(self._sock_msgs)
        consolidated_stats['topics_received'] = list(set(consolidated_stats['topics_received']))
        translation = IBKRFields.code_to_name()

        segment_sep = "\n" + ("-" * 40) + "\n"
        file_content = "Connection Statistics:\n"
        for key, value in consolidated_stats.items():
            file_content += f"{key}: {value}\n"
        file_content += segment_sep
        file_content += "\nReceived Messages:\n"
        for msg in self._socket_pipline:
            timestamp_str = msg.timestamp_str()
            origin = msg.origin
            content = msg.content

            if self._configs['Translate Socket Messages']:
                # Convert all fieldsIDs into their string 'real' names if possible
                # this requires converting the content to JSON
                try:
                    original_content = json.loads(content)
                    content_json = original_content.copy()
                    if isinstance(content_json, dict):
                        for k, v in original_content.items():
                            # check if k is in IBKRFields, first convert to int, then check in the translation dict
                            try:
                                k_int = int(k)
                                if k_int in translation:
                                    content_json[translation[k_int]] = v
                                    continue
                            except (ValueError, KeyError):
                                pass
                            # if not, keep the original key
                            content_json[k] = v

                    content = json.dumps(content_json, indent=2)
                except json.JSONDecodeError:
                    pass  # If it's not JSON, we can't translate it

            if isinstance(content, bytes):
                try:
                    content = content.decode('utf-8')
                except UnicodeDecodeError:
                    content = str(content)
            file_content += f"[{timestamp_str}] ({origin}): {content}\n"
        file_content += segment_sep

        if filename is None:
            return file_content
        else:
            with open(filename, 'w') as f:
                f.write(file_content)
            if verbose:
                print(f"Socket messages written to {filename}")
            return None

    def _countdown_to_exit(self, seconds=10):
        """Countdown to exit the application"""
        self.opened = False
        self._ready = False
        for i in range(seconds, 0, -1):
            print(f"Exiting in {i} seconds...", end='\r')
            time.sleep(1)
        print("Exiting now.                     ")
        os.kill(os.getpid(), 9)

    def modify_configs_interactive(self):
        """
        Interactive method to modify configurations at runtime. You can
        index into the configs by an integer generated from the list of keys.
        """
        config_keys = list(self._configs.keys())
        print("Current Configurations:")
        for idx, key in enumerate(config_keys):
            print(f"{idx + 1}. {key}: {self._configs[key]}")
        choice = input("Select a configuration to modify (or 'q' to quit): ")
        if choice.lower() == 'q':
            return
        try:
            choice = int(choice)
            if 1 <= choice <= len(config_keys):
                selected_key = config_keys[choice - 1]
                current_value = self._configs[selected_key]
                new_value = input(f"Enter new value for '{selected_key}' (current: {current_value}): ")
                # Attempt to cast to the type of the current value
                if isinstance(current_value, bool):
                    new_value = new_value.lower() in ['true', '1', 'yes']
                elif isinstance(current_value, int):
                    new_value = int(new_value)
                elif isinstance(current_value, float):
                    new_value = float(new_value)
                self._configs[selected_key] = new_value
                print(f"Configuration '{selected_key}' updated to: {new_value}")
            else:
                print("Invalid choice. Please try again.")
        except ValueError:
            print("Invalid input. Please enter a number.")
        except Exception as e:
            print(f"Error updating configuration: {e}")
            traceback.print_exc()

    @runAsThread
    def real_time_logging(self, interval=1, filename=None):
        """
        Real-time logging of socket messages to a file at regular intervals.
        Args:
            interval: Time in seconds between log writes
            filename: The filename to write to. If None, uses a default naming scheme.
        """
        if filename is None:
            # use .log for Monitor app to be able to perpetually read it
            filename = os.path.join(
                gettempdir(), f"fxc_socket_messages_realtime_{int(time.time())}.log"
            )

        print(f"Starting real-time logging to {filename} every {interval} seconds.")
        while self.opened:
            if not self._configs['Pause realtime logging']:
                self._write_sock_msgs_to_file(verbose=False, filename=filename)
            time.sleep(interval)

    ############################
    # Market Data Methods
    ############################

    def stream_market_data(self, contract_id, callback, fields=None):
        """
        Stream real-time market data for a given contract.

        Args:
            contract_id: The contract identifier to stream data for
            callback: Function to call when new data is received
            fields: DOES NOT MATTER FOR FXC, only for continuity with IBWss
        """
        rfields = [
            IBKRFields.CHANGE_PERCENT, IBKRFields.CHANGE, IBKRFields.LAST_PRICE,
            IBKRFields.HIGH, IBKRFields.LOW, IBKRFields.OPEN, IBKRFields.CLOSE,
            IBKRFields.PRIOR_CLOSE, IBKRFields.WEEK_52_HIGH, IBKRFields.WEEK_52_LOW,
            IBKRFields.VOLUME, IBKRFields.VOLUME_LONG, IBKRFields.AVERAGE_VOLUME,
            IBKRFields.HISTORICAL_VOLATILITY_PERCENT, IBKRFields.OPTION_IMPLIED_VOL_PERCENT,
            IBKRFields.ASK_PRICE, IBKRFields.ASK_SIZE, IBKRFields.BID_PRICE,
            IBKRFields.BID_SIZE, IBKRFields.OPTION_OPEN_INTEREST, IBKRFields.SYMBOL
        ]

        super().stream_market_data(
            contract_id=contract_id,
            callback=callback,
            fields=rfields
        )

    ############################
    # Socket Events
    ############################

    @expand_exception_decorator('fxc.on_open')
    def on_open(self, ws):
        """Handle WebSocket connection open event"""
        # TLDR; the old version could have the luxury of recv == 2, this socket doesn't give that luxury
        self.opened = True
        self._ready = True
        self.recv = 50  # Again for continuity with IBWss
        self._boot()
        print("FXC WebSocket connection opened")
        if self._configs['Realtime Logging']:
            self.real_time_logging(interval=self._configs['Realtime Logging Interval'])

        self.authenticated_semaphore.release()

    @expand_exception_decorator('fxc.on_close')
    def on_close(self, ws, *args):
        """Handle WebSocket connection close event"""
        _ = ws
        _ = args
        print("FXC WebSocket connection closed")
        throw_fuss(
            msg="IBKR FXC WebSocket connection closed unexpectedly. Please restart the application.",
            boarder="="
        )
        _NOTIFICATION.notify(
            title='IBKR FXC WebSocket Disconnected',
            message='The IBKR FXC WebSocket connection has been closed.'
        )
        self.opened = False
        self._countdown_to_exit(5)

    ############################
    # Socket Message Handlers
    ############################

    @expand_exception_decorator('fxc.on_message', propagate=False)
    def on_message(self, ws, msg: bytes):
        """Handle incoming WebSocket messages"""
        self._sock_msgs.append(msg)
        self._socket_pipline.append(
            AbstractSocketMessage(content=msg, origin='received')
        )
        self.recv += 1
        _ = ws
        ignored_list = [b'ech+hb']
        if msg in ignored_list:
            return
        try:
            content = json.loads(msg)
            topic = content.get('topic')
            topic_keys = list(self._topic_routing_table.keys())
            for key in topic_keys:
                if key in topic:
                    self._conn_statistics['topics_received'].append(topic)
                    self._conn_statistics['messages_received'] += 1
                    handler = self._topic_routing_table[key]
                    handler(content)
                    return
            print("Received message with unknown topic:", msg)

        except json.JSONDecodeError:
            print("Received non-JSON message:", msg)
            return

        # print(msg[:100])  # Print first 100 bytes for debugging

    @expand_exception_decorator('fxc._act_handler', propagate=False)
    def _act_handler(self, message: dict):
        pass

    @expand_exception_decorator('fxc._system_handler', propagate=False)
    def _system_handler(self, message: dict):
        pass

    @expand_exception_decorator('fxc._status_handler', propagate=False)
    def _status_handler(self, message: dict):
        pass

    ############################
    # Properties
    ############################
    @property
    def interactive_funcs(self):
        return self.interactive_functions.copy()


class FxCTimeout:
    def __init__(self, timeout_cb, timeout=10, warning_interval=60):
        self.timeout = timeout
        self.timeout_cb = timeout_cb
        self._signaled = False
        self._last_warning = 0
        self.warning_interval = warning_interval  # seconds

    def signal(self):
        self._signaled = True

    @runAsThread
    def start(self):
        self._signaled = False
        start_time = time.time()
        while not self._signaled:
            if time.time() - start_time > self.timeout:
                self.timeout_cb()
                break
            if time.time() - self._last_warning > self.warning_interval:
                print("Warning: {}s left until timeout...".format(
                    self.timeout - (time.time() - start_time)
                ))
                self._last_warning = time.time()
            time.sleep(0.1)


class FXCDispatcher(MKTDispatcher):
    """
    Dispatcher for FXC WebSocket connections.
    Notes:
        YOU CANNOT USE it with MKTDispatcher.
        Does NOT have the same interface as MKTDispatcher (for clients)
        Only supports Protocol JSON and uses the Big/Mini/Micro contracts abstractions
        from _forcast_utils.py
    """

    # Note: host and port are identical to MKTDispatcher defaults, DO NOT CHANGE
    def __init__(self, cookie=os.getenv('IB_COOKIE'), host='localhost', port=9972):
        self.ws = FXCWss(cookie=cookie)
        self.ws.run()
        self.ws.authenticated_semaphore.acquire()

        self.interactive_menu = {
            'dynamic_func_calls': self._dynamic_func_calls_interactive,
        }
        # dryRun is always True for FXCDispatcher
        super().__init__(dryRun=True, mode=IBKRModes.PROTOCOL_2, host=host, port=port)
        # within MKTDispatcher this is usually called after IBWss is initialized
        # but because we dry-run it's not going to be called
        # so we need to call it manually here
        self._add_clients()
        self._urls = {
            'tree': 'https://api.ibkr.com/v1/api/trsrv/event/category-tree',
            # requires params i.e. market=733131966&exchange=FORECASTX
            'contract': 'https://api.ibkr.com/v1/api/trsrv/event/contracts?market={}&exchange=FORECASTX'
            # ^ returns the 'big' contract, from which you can derive the mini and micro
        }
        self._all_markets: list[AbstractMarket] | None = None
        self._active_market: FxContractBig | None = None

        self._active_market_memory: dict[int, FxContractBig] = {}

        self._configs.update({
            'Auto-Print Pandas DataFrame on Update': True,
        })
        self.ws.interactive_functions[
            'Modify dispatcher configurations interactively'
        ] = self._modify_configs_interactive

        self._failed_big_markets: list[dict] = []

        self._resolution_timeout = FxCTimeout(
            timeout_cb=self._resolution_timeout_callback,
            timeout=20  # seconds
        )

        # Market setting, switching, etc. lock
        self.fxc_market_lock = threading.Lock()
        self.fxc_market_data_lock = threading.Lock()

    ############################
    # Callbacks
    ############################
    @apply_some_lock('fxc_market_data_lock')
    @expand_exception_decorator('fxc._internal_callback', propagate=False)
    def _internal_callback(self, data: MarketData):
        """
        The internal callback class handles incoming market data
        :param data: An instance of MarketData containing market data.
        :return: None
        """
        if not isinstance(data, MarketData):
            raise TypeError("Callback data must be an instance of MarketData")
        if self._active_market is not None:
            if data.contract_id in self._active_market.all_conids:
                try:
                    self._active_market.apply_mkt_data_update(conid=data.contract_id, mkt_data=data)
                    self._propagate_market_update()
                except NoValueMarketData:
                    pass  # Ignore no-value updates

                if self._configs['Auto-Print Pandas DataFrame on Update']:
                    self.show_market_state()

        if self.market_fully_resolved:
            self._resolution_timeout.signal()
            throw_fuss(
                msg=("Market fully resolved! All available data has been received. Unsubscribing from further updates."
                     "Values are saved, you can use .show_market_state() to view the current state."),
                notify=False
            )
            for conid in self._active_market.all_conids:
                self.ws.unsubscribe_market_data(conid)

            self._active_market_memory[self._active_market.conid] = self._active_market

    ############################
    # API Methods
    ############################

    ############################
    # API Methods
    ############################
    @expand_exception_decorator('fxc.generate_all_markets', propagate=True)
    def generate_all_markets(self) -> list[AbstractMarket]:
        """
            Generate a dictionary of all markets available in FXC.
            The dictionary keys are market names, and the values are their corresponding IDs.
        """
        if self._all_markets:
            return self._all_markets  # Return cached version if already generated

        print("Fetching all markets from IBKR...")
        response = self.ws.networker.session.get(self._urls['tree'])
        try:
            response.raise_for_status()
        except Exception as e:
            print('Response content:', response.content)
            raise e
        data: dict[str, dict[str, str | list]] = response.json()
        all_categories = data.keys()
        print("Found {} categories.".format(len(all_categories)))
        all_markets = []
        for category in all_categories:
            contents: dict = data[category]
            markets = contents.get('markets', [])
            if not markets:
                continue
            for market in markets:
                all_markets.append(AbstractMarket.from_dict(market))

        print("Total unique markets found:", len(all_markets))
        self._all_markets = all_markets
        return self._all_markets

    @apply_some_lock('fxc_market_lock')
    def activate_market(self, market_contract_id: int) -> FxContractBig:
        """
        Activate a market by its contract ID. This fetches the contract details
        and starts to resolve all market data for it.

        Args:
            market_contract_id: The contract ID of the market to activate.

        Returns:
            An instance of FxContractBig representing the activated market.
        """
        if self._active_market is not None and not self.market_fully_resolved:
            raise FxCMarketNotFinishedResolution(
                "Current market is not fully resolved. Please wait until it is fully resolved before activating a new market."
            )

        if market_contract_id in self._active_market_memory:
            self._active_market = self._active_market_memory[market_contract_id]
            print(
                f"Market {self._active_market.underlyingName} with contract ID {market_contract_id} re-activated from memory."
            )
            return self._active_market

        response = self.ws.networker.session.get(
            self._urls['contract'].format(market_contract_id)
        )
        response.raise_for_status()
        data = response.json()
        try:
            big = FxContractBig.from_json(data['contracts'])
        except AbstractionError as e:
            self._failed_big_markets.append(data)
            raise e
        self._active_market = big
        return big

    @apply_some_lock('fxc_market_lock')
    def start_market_resolution(self, force: bool = False):
        """
        Start the market resolution process for the currently active market.
        This will stream market data for all associated contracts (big, mini, micro).
        """
        if self._active_market is None:
            raise ValueError("No active market. Please activate a market first.")

        if not force and self.market_fully_resolved:
            print("Market is already fully resolved. No action taken.")
            return

        contracts = self._active_market.all_conids
        for conid in contracts:
            self.ws.stream_market_data(
                contract_id=conid,
                callback=self._internal_callback
            )
        self._resolution_timeout.start()

    # DOES NOT REQUIRE LOCK, as it calls other methods that have the lock
    def activate_and_resolve_market(self, market_contract_id: int) -> FxContractBig:
        """
        Activate a market by its contract ID and start the resolution process.

        Args:
            market_contract_id: The contract ID of the market to activate.
        Returns:
            An instance of FxContractBig representing the activated market.
        """
        market = self.activate_market(market_contract_id)
        self.start_market_resolution()
        return market

    def available_pre_resolved_markets(self) -> list[FxContractBig]:
        """Return a list of all markets that have been fully resolved and are stored in memory"""
        return list(self._active_market_memory.values())

    def show_market_state(self):
        df = self._active_market.table_dataframe().to_string()
        throw_fuss(df, boarder='=', notify=False)

    @apply_some_lock('fxc_market_lock')
    def _resolution_timeout_callback(self):
        if self._active_market is None:
            return
        throw_fuss(
            msg=f"Market resolution for {self._active_market.underlyingName} timed out after "
                f"{self._resolution_timeout.timeout} seconds. Market will be deleted from memory "
                f"and subscriptions will be cancelled.",
            boarder="="
        )
        for conid in self._active_market.all_conids:
            self.ws.unsubscribe_market_data(conid)

        self.delete_active_market_from_memory()

    ############################
    # CLI-Dev Methods
    ############################

    def delete_active_market_from_memory(self):
        """Delete the currently active market from memory, if it exists"""
        if self._active_market is None:
            print("No active market to delete.")
            return
        conid = self._active_market.conid
        if conid in self._active_market_memory:
            del self._active_market_memory[conid]
            print(f"Deleted market with contract ID {conid} from memory.")
        else:
            print("Active market not found in memory.")

        self._active_market = None

    def interactive_mode(self):
        interactive_builtin = list(self.interactive_menu.keys())
        external_funcs = self.ws.interactive_funcs
        external_funcs_keys = list(external_funcs.keys())
        while True:
            print("\nInteractive Menu:")
            for idx, func_name in enumerate(interactive_builtin + external_funcs_keys):
                print(f"{idx + 1}. {func_name}, external={func_name in external_funcs_keys}")
            print(f"{len(interactive_builtin) + len(external_funcs_keys) + 1}. Exit")

            choice = input("Select an option: ")
            try:
                choice = int(choice)
                if choice == len(interactive_builtin) + len(external_funcs_keys) + 1:
                    print("Exiting interactive mode.")
                    break
                elif 1 <= choice <= len(interactive_builtin):
                    func_name = interactive_builtin[choice - 1]
                    func = self.interactive_menu[func_name]
                    func()
                elif len(interactive_builtin) < choice <= len(interactive_builtin) + len(external_funcs_keys):
                    func_name = external_funcs_keys[choice - len(interactive_builtin) - 1]
                    func = external_funcs[func_name]
                    func()
                else:
                    print("Invalid choice. Please try again.")
            except ValueError as e:
                print("Invalid input. Please enter a number.")
            except Exception as e:
                print(f"Error executing function: {e}")
                traceback.print_exc()

    def _dynamic_func_calls_interactive(self):
        available_methods = dir(self)
        callable_methods = [m for m in available_methods if callable(getattr(self, m)) and not m.startswith("_")]
        # also get properties
        properties = [p for p in available_methods if isinstance(getattr(type(self), p, None), property)]
        callable_methods.extend(properties)

        supported_types = {
            'int': int,
            'float': float,
            'str': str,
            'bool': bool
        }

        print("\nAvailable Methods:")
        for idx, method_name in enumerate(callable_methods):
            print(f"{idx + 1}. {method_name}")
        choice = input("Select a method to call (or 'q' to quit): ")
        if choice.lower() == 'q':
            return
        try:
            choice = int(choice)
            if 1 <= choice <= len(callable_methods):
                selected_method_name = callable_methods[choice - 1]
                # if property, just get the value
                if selected_method_name in properties:
                    prop_value = getattr(self, selected_method_name)
                    print(f"Property '{selected_method_name}' value: {prop_value}")
                    return

                selected_method = getattr(self, selected_method_name)
                # check if args are needed using inspect
                sig = inspect.signature(selected_method)
                params = sig.parameters
                args = []
                if params:
                    print(f"Method '{selected_method_name}' requires {len(params)} argument(s).")
                    # ask the user if they want to skip adding args and just call with no args
                    skip_args = input("Do you want to skip adding arguments and call with no args? (y/n): ")
                    if skip_args.lower() == 'y':
                        args = []
                    else:
                        for param_name, param in params.items():
                            if param.default is param.empty:
                                user_input = input(f"Enter value for required parameter '{param_name}': ")
                            else:
                                user_input = input(
                                    f"Enter value for optional parameter '{param_name}' (default={param.default}): ")
                                if user_input == '':
                                    user_input = param.default

                            # ask for type
                            type_input = input(
                                f"Enter type for parameter '{param_name}' (int, float, str, bool) or press Enter to keep as str: ")
                            if type_input in supported_types:
                                cast_type = supported_types[type_input]
                                try:
                                    user_input = cast_type(user_input)
                                except ValueError:
                                    print(f"Failed to cast input to {type_input}, keeping as str.")
                            else:
                                print("Keeping input as str.")
                            args.append(user_input)

                result = selected_method(*args)

                print(f"Method '{selected_method_name}' executed successfully. Result: {result}")
            else:
                print("Invalid choice. Please try again.")
        except ValueError:
            traceback.print_exc()
        except Exception as e:
            print(f"Error calling method: {e}")
            traceback.print_exc()

    def test_func(self):
        self.activate_and_resolve_market(796056051)

    def save_failed_big_markets(self, filename=None):
        """Save the failed big markets to a JSON file for later inspection"""
        if not self._failed_big_markets:
            print("No failed big markets to save.")
            return
        if filename is None:
            filename = os.path.join(gettempdir(), f"failed_big_markets_{int(time.time())}.json")
        with open(filename, 'w') as f:
            json.dump(self._failed_big_markets, f, indent=2)
        print(f"Saved {len(self._failed_big_markets)} failed big markets to {filename}")

    ############################
    # Properties
    ############################
    @property
    def market_fully_resolved(self) -> bool:
        """Check if the current active market has all available data, returns True if current market is fully resolved
        or is None (no active market)"""

        if self._active_market is None:
            return True

        conid_missing = self._active_market.all_conid_states()
        for conid, missing in conid_missing.items():
            if missing:
                return False
        return True

    ############################
    # Client-Side API
    ############################

    @runAsThread
    def _listen_to_client(self, client: socket.socket):
        """
        This is the following client-API interface for FXCDispatcher.
        Output Protocol: ~{JSON}L
        Input Protocol: cmd:arg1,arg2,arg3...

        Available Commands:
            - get_active_market: Returns the current active market as JSON (FxContractBig)
            - activate_market: Activates a market by its contract ID. Args: contract_id
            - start_market_resolution: Starts the market resolution process for the active market. No args.
            - force_start_market_resolution: Force starts the market resolution process even if already fully resolved. No args.
            - market_fully_resolved: Returns True/False if the market is fully resolved. No args.
            - show_market_state: Returns the current state of the market as a base64 encoded CSV string. No args.
            - verify_socket_sanity: In the case for whatever reason MKTDispatcher could not ping you, but you are still
                connected, this will re-add you to the internal list of clients within MKTDispatcher.
                (this should be impossible to need) alas. No args.
            - get_all_markets: Returns a list of all available markets as JSON. No args.

        All responses are in JSON format with the following guaranteed structure:
        {
            "command": "whatever_command_was_sent_by_client",
            "value": SOME_VALUE,  # The value returned by the command, can be of any type
            "error": null  # If an error occurred, this will be a string describing the error, otherwise null
        }

        Updates to the state will use the 'command' field as 'market_update' with the field as the base64 encoded CSV string.

        Other guarantees:
        - If an error occurs within a COMMAND <NOT INTERNAL ERROR> the server will still maintain
          the above guarantee and populate the "error" field in the response.

        NOT GUARANTEED:
        - Calling get_active_market returns FxContractBig however internally during serialization
          fields are converted by traversing the 'raw' underlying structure the guarantees that
          methods like .to_dict() promise such as enforced_currency, etc. may not be enforced.
          The above does not apply for show_market_state because it uses the methods defined in FxContractBig
          to generate the DataFrame and then convert it to CSV. Not traversing the raw structure.
          By extension, it's highly recommended to use show_market_state() for any client-side processing
          of the market state.

        Notes:
        - All market-based functions are locked, if the server is busy processing a market function
          it will not accept other market function commands until the current one is finished.
        - The underlying MKTDispatcher is still managing the client list, you must be able
          to handle pings from MKTDispatcher to keep the connection alive.

        When you call 'start_market_resolution' you will get full snapshots of the market state as
        they are updated, until the market is fully resolved. This is the identical output as
        show_market_state() but sent automatically when updates occur.

        You can re-call activate_market to switch markets, but only when the current market
        is fully resolved. Old markets are cached in memory and can be re-activated instantly.


        :param client: A socket object representing the connected client.
        :return:
        """
        while True:
            data = client.recv(1024).decode('ascii').strip()
            if not data:
                print("Client disconnected: {}".format(client.getpeername()))
                self.clients.remove(client)
                break

            cmd_router = {
                'get_active_market': self.client_get_active_market,
                'activate_market': self._client_activate_market,
                'start_market_resolution': self._client_start_market_resolution,
                'force_start_market_resolution': self._client_force_start_market_resolution,
                'market_fully_resolved': self._client_market_fully_resolved,
                'show_market_state': self._client_show_market_state,
                'verify_socket_sanity': self._client_verify_socket_sanity,
                'get_all_markets': self._client_get_all_markets,
            }

            if ':' not in data:
                self._craft_send_response(
                    client=client,
                    command="unknown",
                    value=None,
                    error="Invalid command format. Expected 'command:arg1,arg2,...'"
                )
                continue

            command, *args = data.split(':')
            args = args[0].split(',') if args else []
            for arg in args:
                if arg.strip() == '':
                    args.remove(arg)

            command = command.strip()
            possible_cmds = list(cmd_router.keys())
            if command not in possible_cmds:
                self._craft_send_response(
                    client=client,
                    command=command,
                    value=None,
                    error=f"Unknown command '{command}'. Available commands: {possible_cmds}"
                )
                continue
            try:
                func = cmd_router[command]
                if len(args) > 0:
                    # noinspection all
                    result = func(*args)
                else:
                    result = func()
                self._craft_send_response(
                    client=client,
                    command=command,
                    value=result,
                    error=None
                )
            except Exception as e:
                tb_str = traceback.format_exc()
                self._craft_send_response(
                    client=client,
                    command=command,
                    value=None,
                    error=f"Error executing command '{command}': {e}"
                )
                print("ERROR OCCURRED FOR CLIENT {} COMMAND {}:\n {}".format(
                    client.getpeername(), command, tb_str
                ))

    def client_get_active_market(self):
        """Gets the current active market as a JSON string, or None if no active market"""
        if self._active_market is None:
            return None
        active_market: FxContractBig = self._active_market
        return active_market.to_json()

    def _client_activate_market(self, contract_id: str):
        try:
            contract_id = int(contract_id)
        except ValueError:
            raise ValueError("contract_id must be an integer")

        market = self.activate_market(contract_id)
        return "Activated market: {}".format(market.underlyingName)

    def _client_start_market_resolution(self):
        self.start_market_resolution()
        return "Started market resolution for active market."

    def _client_force_start_market_resolution(self):
        self.start_market_resolution(force=True)
        return "Force started market resolution for active market."

    def _client_market_fully_resolved(self) -> bool:
        return self.market_fully_resolved

    def _client_show_market_state(self) -> str:
        if self._active_market is None:
            return "No active market."
        df = self._active_market.table_dataframe()
        csv_str = df.to_csv(index=False)
        # encode to base64 to ensure safe transmission
        b64_str = base64.b64encode(csv_str.encode('utf-8')).decode('utf-8')
        return b64_str

    def _client_verify_socket_sanity(self):
        if self not in self.clients:
            self.clients.append(self)
            return "Socket sanity verified and re-added to client list."
        return "Socket already in client list."

    def _propagate_market_update(self):
        """Propagate the current market state to all connected clients as a base64 encoded CSV string."""
        if self._active_market is None:
            return
        df = self._active_market.table_dataframe()
        csv_str = df.to_csv(index=False)
        b64_str = base64.b64encode(csv_str.encode('utf-8')).decode('utf-8')
        for client in self.clients:
            try:
                self._craft_send_response(
                    client=client,
                    command='market_update',
                    value=b64_str,
                    error=None
                )
            except Exception as e:
                try:
                    print(f"Error sending market update to client {client.getpeername()}: {e}")
                except OSError:
                    print(f"Error sending market update to a disconnected client, removing from list. {client}, {e}")
                self.clients.remove(client)

    def _client_get_all_markets(self):
        markets = self.generate_all_markets()
        markets_dicts = [market.__dict__ for market in markets]
        return json.dumps(markets_dicts)

    @staticmethod
    def _craft_send_response(client, command: str, value, error: str | None = None) -> str:
        response = {
            'command': command,
            'value': value,
            'error': error
        }
        msg = "~" + json.dumps(response) + "L"
        client.sendall(msg.encode('ascii'))


if __name__ == '__main__':
    dispatcher = FXCDispatcher()
    dispatcher.select_account_interactive()
    dispatcher.interactive_mode()
    input("Press Enter to exit...")
