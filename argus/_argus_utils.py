"""Utilities for the Argus package."""
import os
import inspect
import platform
import traceback
import subprocess
from utils3 import assertTypes

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
                    type_hint = input(f"No type hint found. What type should I cast '{name}' to? (int, float, str, bool): ").strip()

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
