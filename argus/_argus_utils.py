"""Utilities for the Argus package."""
import platform
import subprocess
from utils3 import assertTypes

if platform.system() == "Darwin":
    # macOS specific Function
    def system_notification(title: str, message: str) -> None:
        """Send a system notification on macOS."""
        subprocess.run(['osascript', '-e', f'display notification "{message}" with title "{title}"'])


    @assertTypes((str, str, str), auto_convert=True)
    def iMessage_notification(title: str, message: str, number: str) -> None:
        """Send an iMessage notification on macOS."""
        # Usage: imessage-cli --message "Your message" recipient1 [recipient2 ...]
        # Note: Ensure imessage-cli is installed and configured
        subprocess.check_call([
            'imessage-cli', '--message', "{}\n{}".format(title, message), number
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Alternative implementation with more sound options:
    @assertTypes((str, str, str), auto_convert=True)
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
