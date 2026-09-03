"""Utilities for the Argus package."""
import os
import socket
import inspect
import logging
import platform
import threading
import traceback
import subprocess
from utils3 import assertTypes
from collections import OrderedDict
from dotenv import load_dotenv as _dotenv_load_dotenv

if platform.system() == "Darwin":
    # macOS specific Function
    def system_notification(title: str, message: str) -> None:
        """Send a system notification on macOS."""
        subprocess.run(['osascript', '-e', f'display notification "{message}" with title "{title}"'])


    @assertTypes([str, str, str], auto_convert=True)
    def iMessage_notification(title: str, message: str, number: str) -> None:
        """Send an iMessage notification on macOS."""
        # Usage: imessage-cli --message "Your message" recipient1 [recipient2 ...]
        # Note: Ensure imessage-cli is installed and configured
        subprocess.check_call([
            'imessage-cli', '--message', "{}\n{}".format(title, message), number
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Alternative implementation with more sound options:
    @assertTypes([str, str, str], auto_convert=True)
    def macos_notification_with_custom_sound(title: str, message: str, sound_name: str = "default") -> None:
        """Send a macOS notification with custom sound.

        Args:
            title: Notification title
            message: Notification message
            sound_name: Sound to play (default, glass, hero, funk, etc.)
        """
        subprocess.check_call([
            'osascript',
            '-e',
            f'display notification "{message}" with title "{title}" sound name "{sound_name}"'
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
else:
    print('[_argus_utils] Note: You are currently on {}, '
          'this platform is probably supported but system notifications will not work.'.format(platform.system()))


    def system_notification(title: str, message: str) -> None:
        print('WARNING: SYSTEM NOTIFICATIONS ARE ONLY SUPPORTED ON macOS SYSTEMS.')
        print('Notification:', title, '-', message)


    @assertTypes([str, str, str], auto_convert=True)
    def iMessage_notification(title: str, message: str, number: str) -> None:
        print('WARNING: IMESSAGE NOTIFICATIONS ARE ONLY SUPPORTED ON macOS SYSTEMS.')
        print('iMessage Notification to', number, ':', title, '-', message)


    def macos_notification_with_custom_sound(title: str, message: str, sound_name: str = "default") -> None:
        """Placeholder for non-macOS systems."""
        print(f"macOS notification with sound not supported on this platform: {title} - {message}")


class Notification:
    """Dispatcher for notifications."""

    def __init__(self, number: str = None, active=True):
        """
        :param number: The phone number to send notifications to.
        :param active: If True, notifications are sent; otherwise, they are not.
        """
        self.number = number
        self.active = active

    def notify(self, title: str, message: str) -> None:
        """
        Send a notification.
        :param title: The title of the notification.
        :param message: The message of the notification.
        """
        if not self.active:
            print(f"Notification suppressed: {title} - {message}")
            return

        if platform.system() == "Darwin":
            system_notification(title, message)
            if self.number:
                iMessage_notification(title, message, self.number)
        else:
            print(f"Notification: {title} - {message}")


class Introspective:
    """Class with method to call its own methods interactively."""

    def _interactive_ui(self, functions: dict):
        """
        An interactive UI to call methods of the class and other functions. Automatically
        includes .call_methods as an option.

        :param functions: A dictionary of function names to functions.
            Example { 'func_name': ('this is func docstring', func_reference) }
        :return:
        """
        functions['call_method'] = ('Interactively call a method of this class', self.call_method)
        functions['exit'] = ('Exit the interactive UI', lambda: None)
        while True:
            print("\nAvailable functions/methods:")
            for i, (name, (doc, _)) in enumerate(functions.items(), 1):
                print(f"{i}. {name} - {doc}")

            try:
                choice = int(input("Choose a function/method number to call (or 0 to exit): "))
                if choice == 0:
                    print("Exiting interactive UI.")
                    break
                func_name = list(functions.keys())[choice - 1]
            except (ValueError, IndexError):
                print("Invalid choice.")
                continue

            func = functions[func_name][1]
            print(f"Selected function/method: {func_name}")

            if func_name == 'exit':
                print("Exiting interactive UI.")
                break

            try:
                value = func()
                print("Result:", value)
            except Exception as e:
                traceback.print_exc()
                print(f"Error calling function/method: {e}")

    def call_method(self):
        # List all public methods of the current instance
        methods = {name: func for name, func in inspect.getmembers(self, predicate=inspect.ismethod)
                   if not name.startswith('_')}

        # remove this method from the list
        methods.pop('call_method', None)

        if not methods:
            print("No callable public methods found.")
            return

        # Show available methods
        print("Available methods:")
        for i, name in enumerate(methods.keys(), 1):
            print(f"{i}. {name}")

        try:
            choice = int(input("Choose a method number to call: "))
            method_name = list(methods.keys())[choice - 1]
        except (ValueError, IndexError):
            print("Invalid choice.")
            return

        method = methods[method_name]
        sig = inspect.signature(method)
        params = sig.parameters

        print(f"Selected method: {method_name}")
        print(f"Signature: {sig}")

        use_args = input("Do you want to provide arguments? (y/n): ").strip().lower()

        args, kwargs = [], {}

        if use_args == 'y':
            for name, param in params.items():
                if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                    print(f"Parameter '{name}' allows variable arguments, skipping prompt.")
                    continue

                val = input(f"Enter a value for '{name}' (leave empty to skip): ").strip()
                if not val:
                    continue

                type_hint = param.annotation if param.annotation != param.empty else None
                if type_hint:
                    print(f"Type hint detected for '{name}': {type_hint}")
                else:
                    type_hint = input(
                        f"No type hint found. What type should I cast '{name}' to? (int, float, str, bool): ").strip()

                # Type casting
                try:
                    if type_hint in [int, 'int']:
                        val = int(val)
                    elif type_hint in [float, 'float']:
                        val = float(val)
                    elif type_hint in [bool, 'bool']:
                        val = val.lower() in ['true', '1', 'yes']
                    else:
                        val = str(val)
                except ValueError:
                    print(f"Failed to cast '{name}', keeping as string.")

                kwargs[name] = val

        # Attempt to call the method
        try:
            result = method(*args, **kwargs)
            print("Result:", result)
        except TypeError as e:
            print(f"TypeError: {e}")
            print("Trying to call method without arguments...")
            try:
                result = method()
                print("Result:", result)
            except Exception as e2:
                print(f"Failed to call method: {e2}")
        except Exception as e:
            print(f"Error calling method: {e}")


def throw_fuss(msg: str, boarder="=", notify=True, title="Argus IBKR Alert") -> None:
    """A helper function to make a large-print fuss to the user good for critical errors. This function FORCES notifications."""
    try:
        environment_size = os.get_terminal_size().columns
    except OSError:
        environment_size = 80

    if environment_size < 80:
        environment_size = 80
    opening_line = boarder * environment_size
    closing_line = boarder * environment_size
    print(opening_line)
    # message should be centered and maybe multiple lines
    for line in msg.split('\n'):
        centered_line = line.center(environment_size)
        print(centered_line)
    print(closing_line)

    if notify and os.getenv('ARGUS_DISABLE_NOTIFICATIONS', '0') != '1':
        macos_notification_with_custom_sound(
            title=title,
            message=msg,
        )


########################################
# Trading Dispatcher Plumbing
#
# Shared by every Argus trading dispatcher (Polymarket, HyperLiquid, Lighter,
# and future exchanges). None of the classes below know anything about a
# specific exchange's wire format — they only deal in generic sockets,
# channel ids, and correlation ids.
########################################

class CorrelationIDError(Exception):
    """Base class for correlation-id validation errors raised by CorrelationIDChecker."""
    pass


class CorrelationIDLengthTooLongError(CorrelationIDError):
    pass


class CorrelationIDAlreadySeenError(CorrelationIDError):
    pass


class RoutingHelper:
    """
    Helper class to manage routing of market data and order subscriptions for a
    trading dispatcher. Exchange-agnostic: works purely in terms of sockets and
    string channel ids (e.g. a CLOB token id, a perpetual's market id, etc).
    You must override the subscription_expired method to handle subscription expiration logic.
    Features:
        1. Market Data Routing Table: channel_id -> list of sockets subscribed to that channel
        2. Order Subscriptions: socket -> list of channel_ids the socket is subscribed to
        3. Thread-safe operations using a lock
        4. Methods to add/remove sockets and manage subscriptions
        5. Properties to access the current state of sockets and subscriptions
        6. Logging for subscription management actions
    """

    def __init__(self):
        self._sockets: set[socket.socket] = set()
        self._market_data_routing_table: dict[str, list[socket.socket]] = {}  # channel_id -> list[socket.socket]
        self._order_subscriptions: dict[socket.socket, list[str]] = {}  # socket.socket -> list[channel_id]
        self._lock = threading.Lock()
        # Per-client sendall lock — prevents byte interleaving when multiple
        # WS shard threads broadcast to the same client socket.  Per-socket
        # granularity means a slow client A never blocks sends to client B.
        # Lazily populated by add_socket and `send_lock_for`; cleaned up by remove_socket.
        self._sendall_locks: dict[socket.socket, threading.Lock] = {}

    def send_lock_for(self, sock: socket.socket) -> threading.Lock:
        """
        Return the per-socket sendall lock, creating it on first access.
        Callers MUST hold this lock around every sock.sendall() into `sock`
        from any thread that may run concurrently with another sender.
        """
        with self._lock:
            lock = self._sendall_locks.get(sock)
            if lock is None:
                lock = threading.Lock()
                self._sendall_locks[sock] = lock
            return lock

    def add_socket(self, sock: socket.socket):
        with self._lock:
            self._sockets.add(sock)
            if sock not in self._sendall_locks:
                self._sendall_locks[sock] = threading.Lock()

    def remove_socket(self, sock: socket.socket):
        """
        Remove a socket and clean up its subscriptions.
        :param sock: The socket to remove.
        :return:
        """
        with self._lock:
            self._sockets.discard(sock)
            self._sendall_locks.pop(sock, None)
            subscribed_channel_ids = self._order_subscriptions.pop(sock, [])
            for channel_id in subscribed_channel_ids:
                if channel_id in self._market_data_routing_table:
                    # Remove the socket from the routing table
                    self._market_data_routing_table[channel_id].remove(sock)
                    # If no more sockets are subscribed to this channel_id, remove the entry
                    if not self._market_data_routing_table[channel_id]:
                        del self._market_data_routing_table[channel_id]
                        self.subscription_expired(channel_id)

    # THIS METHOD TO BE OVERRIDDEN
    def subscription_expired(self, channel_id):
        """
        This method should be implemented to handle subscription expiration logic.
        What happens when a subscription expires? – Probably tell Ws to stop sending updates.
        :param channel_id:
        :return:
        """
        raise NotImplementedError("Subscription expiration handling not implemented.")

    def add_socket_to_subscription(self, sock: socket.socket, channel_id: str):
        """Adds socket to market data and order subscriptions"""
        with self._lock:
            if channel_id not in self._market_data_routing_table:
                self._market_data_routing_table[channel_id] = []
            if sock not in self._market_data_routing_table[channel_id]:
                self._market_data_routing_table[channel_id].append(sock)

            if sock not in self._order_subscriptions:
                self._order_subscriptions[sock] = []
            if channel_id not in self._order_subscriptions[sock]:
                self._order_subscriptions[sock].append(channel_id)

    def remove_socket_from_subscription(self, sock: socket.socket, channel_id: str):
        """Removes socket from market data and order subscriptions"""
        with self._lock:
            if channel_id in self._market_data_routing_table:
                if sock in self._market_data_routing_table[channel_id]:
                    self._market_data_routing_table[channel_id].remove(sock)
                    if not self._market_data_routing_table[channel_id]:
                        del self._market_data_routing_table[channel_id]
                        self.subscription_expired(channel_id)
                        logging.info('Market data subscription for channel_id %s has expired', channel_id)
                    else:
                        logging.info('Removed socket from market data subscription for channel_id %s', channel_id)
                else:
                    logging.warning('Tried to remove socket not subscribed to market data for channel_id %s',
                                    channel_id)
            else:
                logging.warning('Tried to remove socket from non-existent market data subscription for channel_id %s',
                                channel_id)

            if sock in self._order_subscriptions:
                if channel_id in self._order_subscriptions[sock]:
                    self._order_subscriptions[sock].remove(channel_id)
                    if not self._order_subscriptions[sock]:
                        del self._order_subscriptions[sock]
                        logging.info('Order subscriptions for socket has expired after removing channel_id %s',
                                     channel_id)
                else:
                    logging.warning(
                        'Tried to remove channel_id %s from `order_subscriptions` but not found for socket.',
                        channel_id)
            else:
                logging.warning('Tried to remove socket from `order_subscriptions` but socket not found.')

    @property
    def sockets(self):
        with self._lock:
            return list(self._sockets)

    @property
    def market_data_routing_table(self):
        with self._lock:
            return dict(self._market_data_routing_table)

    @property
    def order_subscriptions(self):
        with self._lock:
            return dict(self._order_subscriptions)


class ArgsObject:
    """
    A simple class to hold arguments for handler functions.
    The order of 'args' is important as handler functions expect
    specific args in a certain order.
    """

    def __init__(self, sock: socket.socket, args):
        """
        The first argument is always the socket.
        The order of 'args' is important as handler functions expect specific args in a certain order.
        :param sock:
        :param args:
        """
        self.sock = sock
        self.args = args


class CorrelationIDChecker:
    """
    A simple class to check if we've already seen this correlation ID before and raise an error if we have.
    Correlation IDs must be unique for each request and should not be reused. This is to prevent client-side
    matching engines from getting confused. This class is thread-safe and uses a lock to ensure that multiple threads
    can check correlation IDs without running into race conditions. Automatically trims the dict of seen correlation
    IDs if it exceeds a certain size to prevent memory issues.

    Shared by every Argus trading dispatcher (Polymarket, HyperLiquid, Lighter, ...) that needs
    request/response correlation-id de-duplication.
    """

    def __init__(self):
        self.seen_correlation_ids: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()

        self._max_seen_ids = int(os.environ.get('MAX_SEEN_CORRELATION_IDS', 100_000))
        # ^^^^^^^^ ~roughly 7MB if each ID is uuid4

        self._max_id_length = int(os.environ.get('MAX_CORRELATION_ID_LENGTH', 40))
        # ^^^^^^ slightly above uuid4 length, ideally leave alone unless you have a reason to change it
        # uuid4's has 2^122 possible values, so the chance of a collision is astronomically low. Leaving
        # arbitrarily high limits will bloat the memory usage of this class.

    @assertTypes([str], auto_convert=True, class_method=True)
    def check_correlation_id(self, correlation_id: str):
        if len(correlation_id) > self._max_id_length:
            raise CorrelationIDLengthTooLongError(
                f"Correlation ID {correlation_id} is too long. Maximum length is {self._max_id_length} characters."
            )
        with self._lock:
            if correlation_id in self.seen_correlation_ids:  # O(1)
                raise CorrelationIDAlreadySeenError(
                    f"Correlation ID {correlation_id} has already been seen. Correlation IDs must be unique."
                )
            self.seen_correlation_ids[correlation_id] = None

            if len(self.seen_correlation_ids) > self._max_seen_ids:
                self._trim_seen_ids_locked()

    def _trim_seen_ids_locked(self):
        """
        Trims the oldest 50% of seen correlation IDs to free up memory.
        Must be called while self._lock is already held.
        """
        num_to_remove = len(self.seen_correlation_ids) // 2
        for _ in range(num_to_remove):
            self.seen_correlation_ids.popitem(last=False)  # O(1) — pops from front (oldest)

    def clear_seen_ids(self):
        """Clears all seen correlation IDs. Use with caution as this can lead to accepting duplicate IDs."""
        with self._lock:
            self.seen_correlation_ids.clear()


_LOADED_ALREADY = False
_LOAD_RESULT = None


class EnvLoader:
    """
    Module to integrate SDist from https://github.com/the-sal/SDist to securely load .env files by decrypting them
    just-in-time to load then deleting the encrypted file.
    """

    def __init__(self):
        try:
            self.sdist_path = self.load_sdist_path()
        except FileNotFoundError:
            self.sdist_path = None
        self._active = False

        if os.path.exists(".env.enc.se") and self.sdist_path is not None:
            print('[SecureEnvLoader] Found .env.enc.se, will decrypt when loading env')
            self._active = True
        elif os.path.exists(".env.enc.se") and self.sdist_path is None:
            print('[SecureEnvLoader] Found .env.enc.se, but SDist not found, will not decrypt')
            self._active = False

        if platform.system() != "Darwin" and self._active:
            print(
                "[SecureEnvLoader] Warning: An .env.enc.se file was found, but SDist's decrypt-se requires macOS secure enclave. This functionality will"
                "not work on Linux. Defaulting to loading .env, ensure .env is present. Delete the .env.enc.se file to supress this warning.")
            self._active = False

    @staticmethod
    def load_sdist_path():
        response = subprocess.check_output(["which", "sdist"]).decode("utf-8").strip()
        if not os.path.exists(response):
            raise FileNotFoundError("SDist not found, response:", response)
        return response

    def decrypt_env(self):
        """
        Decrypt the .env file using SDist.
        :return:
        """
        subprocess.check_call([
            self.sdist_path,
            "-c",
            "-p",
            "NONE",
            "--args-only",
            "-f",
            "decrypt-se",
            "-a",
            ".env.enc.se",
            ".env"
        ])

    # NOTE: There is no need for this function to be called
    # because decryption is not a destructive action,
    # so after loading once decrypted, you can just delete the .env file
    def encrypt_env(self):
        """
        Encrypt the .env file using SDist.
        raises an exception if there is an error
        :return:
        """
        subprocess.check_call([
            self.sdist_path,
            "-c",
            "-p",
            "NONE",
            "--args-only",
            "-f",
            "encrypt-se",
            "-a",
            ".env",
            ".env.enc.se",
            "?"
        ])
        try:
            os.remove(".env")
        except FileNotFoundError:
            raise FileNotFoundError("State was corrupted, after encryption failed to remove .env")

    def load_env(self) -> bool:
        """
        Load the .env file using SDist if encryped, otherwise load from the normal dotenv method (.env)
        Many places within the codebase load the .env file at their own pace,
        this method ensures the .env is only decrypted and loaded once to avoid
        repeatedly decrypting and loading the file.
        :return: True if a .env file was found and loaded, False otherwise. Cached across calls since
                 the actual load only ever happens once per process.
        """
        global _LOADED_ALREADY, _LOAD_RESULT
        if not _LOADED_ALREADY:
            if self._active:
                self.decrypt_env()
                # decrypt_env() always writes to CWD, so load from there explicitly rather than
                # letting find_dotenv() search upward from this module's own directory.
                _LOAD_RESULT = _dotenv_load_dotenv(dotenv_path=".env")
            else:
                _LOAD_RESULT = _dotenv_load_dotenv()
            if self._active:
                try:
                    os.remove(".env")
                except FileNotFoundError:
                    raise FileNotFoundError(
                        "After decryption failed to remove .env, the state of the system was corrupted")
            _LOADED_ALREADY = True
        return _LOAD_RESULT


_ENV_VAR_LOADER = EnvLoader()


def load_dotenv() -> bool:
    """
    Load the .env file using SDist if encryped, otherwise load from the normal dotenv method (.env)
    :return: True if a .env file was found and loaded, False otherwise.
    """
    return _ENV_VAR_LOADER.load_env()


def check_env_compatibility():
    """
    This fn is mainly designed for Linux (it works on macOS too) to check if the shell
    environment is compatible with Argus. It checks for all the POSIX-Y subprocesses Argus uses.
    The list below _should_ be extensive.

    Checks happen in two tiers:
    - hard_procs: used on Argus's happy path with no fallback. Missing one raises.
    - soft_procs: only used on non-happy paths (e.g. install-from-source fallbacks,
      platform-specific extras). Missing one just prints a warning.

    :return: Dict mapping each resolved subprocess name to its absolute path. Soft procs
        that could not be resolved are omitted (a warning is printed for each of those).
    """
    hard_procs = [
        "mv",
        "cp",
        "tar",
        "unzip",
        "killall",
        "clear",
        "uname",
        "file",
        "chmod",
    ]

    soft_procs = [
        "tree",
        "cargo",
        "curl",
        "git",
        "ping"
    ]

    if platform.system() == "Darwin":
        hard_procs += ["osascript"]
        soft_procs += ["sdist", "imessage-cli"]
        # imessage-cli is only used for sending iMessage notifications. This system will be depreciated
        # in a future version of Argus. and imessage-cli will be removed from here. imessage-cli
        # is a very old dep from ib-era. Before v3 this will be dropped.

    try:
        subprocess.check_output(["which", "which"], stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise FileNotFoundError(
            "'which' command not found on this system. Argus requires POSIX 'which' "
            "to resolve the subprocesses it depends on.")

    def resolve(_proc):
        try:
            path_ = subprocess.check_output(["which", _proc], stderr=subprocess.DEVNULL).decode("utf-8").strip()
        except subprocess.CalledProcessError:
            path_ = None
        return path_ if path_ and os.path.exists(path_) else None

    resolved_paths = {}
    missing_hard = []
    for proc in hard_procs:
        path = resolve(proc)
        if path is None:
            missing_hard.append(proc)
        else:
            resolved_paths[proc] = path
            print('[env-compatibility] OK Found:', proc, 'at', path)

    if missing_hard:
        raise FileNotFoundError(
            "The following required subprocess(es) could not be resolved via 'which': {}. "
            "These are used on Argus's happy path and have no fallback.".format(", ".join(missing_hard)))

    for proc in soft_procs:
        path = resolve(proc)
        if path is None:
            print("[check_env_compatibility] Warning: '{}' could not be resolved, "
                  "features depending on it may not work.".format(proc))
        else:
            print('[env-compatibility] OK Found:', proc, 'at', path)
            resolved_paths[proc] = path

    return resolved_paths
