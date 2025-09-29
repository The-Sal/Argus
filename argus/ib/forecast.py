import os
import sys
import json
import copy
import time
import traceback
import threading
from utils3 import runAsThread
from tempfile import gettempdir
from argus.ib.fields import IBKRFields
from argus.ib import IBWss, AccountProvider, MKTDispatcher
from argus.ib._ib_utils import (
    throw_fuss, NOTIFICATION as _NOTIFICATION, expand_exception_decorator,
    Account, FakeSocket, IBKR_CapitalComMKTDataLive, AbstractSocketMessage
)





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
            'smd': self._handle_market_data,
            'system': self._system_handler,
            'sts': self._status_handler,
            'spl': self._portfolio_handler,
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
            IBKRFields.BID_SIZE, IBKRFields.OPTION_OPEN_INTEREST
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

    @expand_exception_decorator('fxc._handle_market_data', propagate=False)
    def _handle_market_data(self, message: dict):
        print("Market Data Message:", message)
        # send to client callbacks
        contract_id = message.get('conid')
        callbacks = self.contract_callbacks.get(contract_id, [])
        if not callbacks:
            print(f"No callbacks registered for contract ID {contract_id}")
            return

        data = IBKR_CapitalComMKTDataLive

    @expand_exception_decorator('fxc._act_handler', propagate=False)
    def _act_handler(self, message: dict):
        pass

    @expand_exception_decorator('fxc._system_handler', propagate=False)
    def _system_handler(self, message: dict):
        pass

    @expand_exception_decorator('fxc._status_handler', propagate=False)
    def _status_handler(self, message: dict):
        pass

    @expand_exception_decorator('fxc._portfolio_handler', propagate=False)
    def _portfolio_handler(self, message: dict):
        pass

    ############################
    # Properties
    ############################
    @property
    def interactive_funcs(self):
        return self.interactive_functions.copy()


class FXCDispatcher(MKTDispatcher):
    """
    Dispatcher for FXC WebSocket connections.
    Notes:
        YOU CANNOT USE it with MKTDispatcher.
        Does NOT have the same interface as MKTDispatcher (for clients)
        Only supports Protocol 2. No support for pickle, JSON, etc.
    """

    def __init__(self, cookie=os.getenv('IB_COOKIE')):
        self.ws = FXCWss(cookie=cookie)
        self.ws.run()
        self.ws.authenticated_semaphore.acquire()

        self.interactive_menu = {
            'add_contract': self.add_contract_interactive,
            # 'remove_contract': self.remove_contract_interactive,
            # 'list_contracts': self.list_contracts,
        }
        # dryRun is always True for FXCDispatcher
        super().__init__(dryRun=True)
        # within MKTDispatcher this is usually called after IBWss is initialized
        # but because we dry-run it's not going to be called
        # so we need to call it manually here
        self._add_clients()
        self._private_socket = FakeSocket(callback=self._internal_callback)

    @staticmethod
    def _internal_callback(data: IBKR_CapitalComMKTDataLive):
        """
        The internal callback class handles incoming market data that
        was request by the user via the interactive menu. It is NOT streamed
        to any clients. It simply prints the data to the console.
        :param data: An instance of IBKR_CapitalComMKTDataLive containing market data.
        :return: None
        """

        if isinstance(data, str):
            # It's a ping
            return

        msg = "Symbol: {}".format(data.symbol)
        msg += json.dumps(data.__dict__, indent=2)
        throw_fuss(msg, boarder='-', notify=False)

    def add_contract_interactive(self):
        contract_id = input("Enter the contract ID to add: ")
        try:
            contract_id = int(contract_id)
            self.ws.stream_market_data(
                contract_id=contract_id,
                callback=self._internal_callback
            )
            print(f"Contract {contract_id} added successfully.")
        except ValueError:
            print("Invalid contract ID. Please enter a numeric value.")
        except Exception as e:
            print(f"Error adding contract: {e}")
            traceback.print_exc()

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
            except ValueError:
                print("Invalid input. Please enter a number.")
            except Exception as e:
                print(f"Error executing function: {e}")
                traceback.print_exc()


if __name__ == '__main__':
    dispatcher = FXCDispatcher()
    dispatcher.select_account_interactive()
    dispatcher.interactive_mode()
    input("Press Enter to exit...")
