"""
Satellite Systems – This module is responsible for installing and managing all the satellite systems that are part of the Argus project.
As of August 2026 there are the following subsystems:
– WpDaemon: C++ re-write of WireProxyServer to cut RAM by ~96%
– APDB: Rust write of a custom database for the Polymarket Dispatcher to cut RAM in the dispatcher ~80%
"""
import os
import subprocess
from utils3 import Container, networking


class GenericError(Exception):
    pass

class UnableToInstall(GenericError):
    pass

class UnableToUpdate(GenericError):
    pass

class UnableToStartSidecar(GenericError):
    pass

class GenericSatelliteSys:
    def check_installed(self) -> bool:
        """
        Check if the satellite system is installed.
        Returns:
            bool: True if installed, False otherwise.
        """
        raise NotImplementedError("This method should be implemented by subclasses.")

    def install(self):
        """
        Install the satellite system. Raise an exception if installation fails.
        :raises UnableToInstall: If installation fails.
        :return: None
        """

    def update(self):
        """
        Update the satellite system. Raise an exception if update fails.
        :raises UnableToUpdate: If update fails.
        :return: None
        """

    def start_sidecar(self):
        """
        Start the satellite system. Raise an exception if start fails.
        :return: None
        """

    def stop_sidecar(self):
        """
        Stop the satellite system. Raise an exception if stop fails.
        :return: None
        """

def get_name() -> str:
    return subprocess.check_output(['uname', '-sm']).decode().strip().replace(" ", "-").lower()

def get_sidecar_path() -> str:
    pth = os.path.expanduser("~/.argus/sidecars")
    os.makedirs(pth, exist_ok=True)
    return pth

class WpDaemon(GenericSatelliteSys):
    def __init__(self):
        self.addr = "https://github.com/The-Sal/WpDaemon/releases/latest/download/builds.zip"
        self.install_path = os.path.join(get_sidecar_path(), "WpDaemon")  # binary location

    def install(self):
        with Container():
            session = networking.Session()
            session.downloadFile(
                self.addr,
                "builds.zip",
                lambda x: print("\rDownloading WpDaemon: {:.2f}%".format(x*100), end="\r")
            )
            subprocess.check_call(['unzip', 'builds.zip'])
            daemon = "builds/{}/WpDaemon".format(get_name())
            print("Expecting Daemon:", daemon)
            if os.path.exists(daemon):
                print("Found Daemon:", daemon)
                subprocess.check_call(['cp', daemon, self.install_path])
            else:
                raise UnableToInstall("WpDaemon binary not found in the downloaded archive. Available daemons: {}".format(os.listdir("builds/")))

    def check_installed(self) -> bool:
        return os.path.exists(self.install_path)

    def update(self):
        self.install()

    def start_sidecar(self):
        if not self.check_installed():
            raise UnableToStartSidecar("WpDaemon is not installed.")
        subprocess.Popen([
            self.install_path,
            "--daemon",
        ], start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True
        )

    def stop_sidecar(self):
        subprocess.check_call(['killall', 'WpDaemon'])




if __name__ == "__main__":
    daemon = WpDaemon()
    print("Checking if WpDaemon is installed...")
    os.remove(daemon.install_path)
    if daemon.check_installed():
        print("WpDaemon is installed.")
    daemon.install()
    daemon.start_sidecar()
    daemon.stop_sidecar()
