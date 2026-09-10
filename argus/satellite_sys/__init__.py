"""
Satellite Systems – This module is responsible for installing and managing all the satellite systems that are part of the Argus project.
As of August 2026 there are the following subsystems:
– WpDaemon: C++ re-write of WireProxyServer to cut RAM by ~96%
– APDB: Rust write of a custom database for the Polymarket Dispatcher to cut RAM in the dispatcher ~80%
"""
import os
import sys
import glob
import json
import time
import socket
import platform
import traceback
import subprocess
from argus.wireproxy import wrapper
from utils3 import Container, networking


_addrs_and_hashes = {
    'WpDaemon': 'https://github.com/The-Sal/WpDaemon/releases/download/v1.0.2/builds.zip', 
    'APDB': 'https://github.com/The-Sal/argus-polymarket-db/releases/download/v2.1.1'
}




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
        raise NotImplementedError("This method should be implemented by subclasses.")

    def update(self):
        """
        Update the satellite system. Raise an exception if update fails.
        :raises UnableToUpdate: If update fails.
        :return: None
        """
        raise NotImplementedError("This method should be implemented by subclasses.")

    def start_sidecar(self):
        """
        Start the satellite system. Raise an exception if start fails.
        :return: None
        """
        raise NotImplementedError("This method should be implemented by subclasses.")

    def stop_sidecar(self):
        """
        Stop the satellite system. Raise an exception if stop fails.
        :return: None
        """
        raise NotImplementedError("This method should be implemented by subclasses.")

    def is_running(self):
        """
        Returns True if the satellite system is running, False otherwise.
        :return:
        """
        raise NotImplementedError("This method should be implemented by subclasses.")

    @staticmethod
    def target_triple() -> str:
        system = platform.system()
        machine = platform.machine().lower()

        arch_map = {
            "amd64": "x86_64", "x86_64": "x86_64", "x64": "x86_64",
            "i386": "i686", "i686": "i686", "x86": "i686",
            "arm64": "aarch64", "aarch64": "aarch64",
            "armv7l": "armv7", "armv6l": "arm",
        }
        arch = arch_map.get(machine, machine)

        if system == "Darwin":
            return f"{arch}-apple-darwin"

        if system == "Linux":
            libc = "gnu"
            # musl loader file is the most reliable signal (e.g. Alpine)
            if glob.glob("/lib/ld-musl-*.so.1") or glob.glob("/lib64/ld-musl-*.so.1"):
                libc = "musl"
            elif not platform.libc_ver()[0]:
                # glibc undetectable -> double check with ldd
                try:
                    out = subprocess.run(["ldd", "--version"],
                                         capture_output=True, text=True)
                    if "musl" in (out.stdout + out.stderr).lower():
                        libc = "musl"
                except FileNotFoundError:
                    pass

            if arch == "armv7":
                return f"armv7-unknown-linux-{libc}eabihf"  # hardfloat is the norm now
            return f"{arch}-unknown-linux-{libc}"

        if system == "Windows":
            abi = "gnu" if "GCC" in platform.python_compiler() else "msvc"
            return f"{arch}-pc-windows-{abi}"

        raise RuntimeError(f"unsupported platform: {system} {machine}")

    @staticmethod
    def get_name() -> str:
        return subprocess.check_output(['uname', '-sm']).decode().strip().replace(" ", "-").lower()

    @staticmethod
    def platform_normal(some_str) -> str:
        return some_str.replace("-", "").replace("_", "").replace(" ", "").lower().strip()

def get_sidecar_path() -> str:
    pth = os.path.expanduser("~/.argus/sidecars")
    os.makedirs(pth, exist_ok=True)
    return pth

class WpDaemon(GenericSatelliteSys):
    def __init__(self):
        super().__init__()
        self.addr = _addrs_and_hashes['WpDaemon']
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
            daemon = "builds/{}/WpDaemon".format(self.get_name())
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

class ArgusPolymarketDB(GenericSatelliteSys):
    def __init__(self):
        self.addr = _addrs_and_hashes["APDB"]
        self.install_path = os.path.join(get_sidecar_path(), "APDB")


    def install_from_source(self):
        with Container():
            print('Installing APDB from source... This may take a while.')
            subprocess.check_call(['git', 'clone', 'https://github.com/The-Sal/argus-polymarket-db'])
            os.chdir('argus-polymarket-db')
            subprocess.check_call(['cargo', 'build', '--release'])

            try:
                subprocess.check_call(['./target/release/argus-polymarket-db', '--version'])
            except subprocess.CalledProcessError as e:
                raise UnableToInstall("APDB binary is not executable. Error: {}".format(e))

            subprocess.check_call(['cp', 'target/release/argus-polymarket-db', self.install_path])


    def install(self):
        self.stop_sidecar()
        try:
            with Container():
                session = networking.Session()
                triple = self.target_triple()
                url = f"{self.addr}/{triple}_argus-polymarket-db"
                print("Downloading APDB from:", url)
                session.downloadFile(
                    url,
                    "apdb",
                    lambda x: print("\rDownloading APDB: {:.2f}%".format(x * 100), end="\r")
                )
                file = self.platform_normal(subprocess.check_output(['file', 'apdb']).decode().strip())
                arch = platform.architecture()
                for arch_str in arch:
                    if self.platform_normal(arch_str) not in file:
                        raise UnableToInstall(f"Downloaded APDB binary architecture mismatch: {file} vs {arch}")

                subprocess.check_call(['chmod', '+x', 'apdb'])
                print('Correct Architecture Found:', ' '.join(arch))
                print('Running final tests...')
                try:
                    subprocess.check_call(['./apdb', '--version'])
                except subprocess.CalledProcessError as e:
                    raise UnableToInstall("APDB binary is not executable. Error: {}".format(e))

                subprocess.check_call(['cp', 'apdb', self.install_path])
        except Exception as e:
            print("The following error occurred while installing APDB:", e)
            traceback.print_exc()
            print("Attempting to install APDB from source...")
            self.install_from_source()

    def check_installed(self) -> bool:
        return os.path.exists(self.install_path)

    def update(self):
        self.install()

    @staticmethod
    def _write_dot_env():
        """
        Replaces whatever is in SOCKS5_ADDRS (if it exists) with the current value from BIND_ADDRESS
        This is to enable a seamless transition from the prior version of Argus where it did not deal
        with these environment variables. This fn automatically translates native Argus env vars into those
        understood and used by APDB. Moreover, this fn keeps the BIND_ADDRESS and SOCKS5_ADDRS in sync with the
        BIND_ADDRESS as the authoritative source of truth. Systems without a BIND_ADDRESS will not have a SOCKS5_ADDRS
        written to the .env file, APDB will attempt a direct connection to Polymarket.
        :return:
        """
        from argus._argus_utils import load_dotenv
        load_dotenv()
        BIND_ADDRESS = wrapper.start_proxy_and_return_bind('POLYMARKET')
        if BIND_ADDRESS is None:
            return
        try:
            read_dot_env = open(".env", "r").read()
        except FileNotFoundError:
            read_dot_env = ""
        found = False
        for line in read_dot_env.split("\n"):
            if line.startswith("SOCKS5_ADDRS"):
                print("Replacing SOCKS5_ADDRS in .env")
                read_dot_env = read_dot_env.replace(line, f"SOCKS5_ADDRS='socks5://{BIND_ADDRESS}'")
                found = True
        if not found:
            read_dot_env += f"\nSOCKS5_ADDRS='socks5://{BIND_ADDRESS}'\n"
        open(".env", "w").write(read_dot_env)

    def start_sidecar(self):
        """
        Attempts to start the APDB server. This function will wait until the server is ready to accept connections, including
        the long startup time (if using a proxy) to build the initial database. This is the same amount of time and UX experience
        as the builtin db which had to do essentially the same thing. There will be automatic logs as the system goes through its
        start-up process, however, once this fn is over, it will stop printing logs (however, users can still access). This fn will
        also invoke WireProxy subsystem as it respects the WIREPROXY_MAPPING_<DISPATCHER> convention. In this instance it will
        work with WIREPROXY_MAPPING_POLYMARKET just like all other Polymarket related subsystems. The DB is fully integrated
        with all conventions within the Argus project incl UNSAFE_RAPID_CONNECTION–esc functionality builtin to the APDB directly.
        :return:
        """
        self._write_dot_env()
        self.stop_sidecar()
        # start the wireproxy server for polymarket
        _ = wrapper.start_proxy_and_return_bind('POLYMARKET')

        fd = open("/tmp/argus_polymarket_db.log", "w")
        read_fd = open("/tmp/argus_polymarket_db.log", "r")
        try:
            print('Logging to /tmp/argus_polymarket_db.log')
            # noinspection all
            self._subproc = subprocess.Popen([
                self.install_path,
            ], start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=fd,
                stderr=fd,
                close_fds=True
            )
            time_waited = 0
            while True:
                try:
                    self.get_version_number()
                    break
                except (OSError, ConnectionRefusedError):
                    time.sleep(0.1)
                    print(f'Waiting for APDB to start... {time_waited:.1f}s', end='\r')
                    print("[APDB LOG]", read_fd.read(), end='')
                    sys.stdout.flush()
                    time_waited += 0.1
                    if self._subproc.poll() is not None:
                        raise UnableToStartSidecar(
                            "APDB failed to start. Please check the log file for more details: /tmp/argus_polymarket_db.log")
        finally:
            fd.close()
            read_fd.close()

    def stop_sidecar(self):
        subprocess.Popen(['killall', 'APDB', 'argus-polymarket-db'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).wait()

    @staticmethod
    def get_version_number():
        """
        Requests the version number from the APDB server
        :return:
        """
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(os.environ.get("APDB_BIND_ADDRESS", "/tmp/argus_polymarket_db.sock"))
        msg = {"op": "db_info"}
        s.sendall(json.dumps(msg).encode("utf-8") + b"\n")
        response_raw = s.recv(1024).decode("utf-8")
        try:
            response = json.loads(response_raw)
            s.close()
            return response['db_version']
        except (json.JSONDecodeError, KeyError) as e:
            raise Exception(f"Failed to parse response from APDB: {response_raw}") from e

    def is_running(self):
        """
        Checks if the unix domain socket is reachable
        :return:
        """
        try:
            _ = self.get_version_number()
            return True
        except (OSError, ConnectionRefusedError):
            return False

    def is_latest(self):
        """
        Ensure that the running version of APDB is the pinned version for this release of Argus.
        :return:
        """
        version = subprocess.check_output([self.install_path, '--version']).decode().strip().split(" ")[-1]
        expected_version = "Argus Polymarket Database {}".format(_addrs_and_hashes['APDB'].split('/')[-1]).split(" ")[-1]
        if version != expected_version:
            print("APDB version mismatch. Expected:", expected_version, "Actual:", version)
            os.remove(self.install_path)
            print("Reinstalling APDB...")
            self.install()


if __name__ == '__main__':
    apdb = ArgusPolymarketDB()
    # apdb.install_from_source()
    # apdb.install()
    # apdb.stop_sidecar()
    # apdb.start_sidecar()
    # print(apdb.is_running())
    # print(apdb.get_version_number())
    apdb.is_latest()