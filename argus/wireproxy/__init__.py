"""
WireProxy – This module allows certain Argus Dispatchers (if they support it) to run via WireProxy.
WireProxy is a full userspace wireguard implementation from https://github.com/whyvl/wireproxy

Argus & WireProxy:
Argus will manage all WireProxy binaries and configurations within this module. All the extra assets
will be stored within the argus cache directory ~/.argus, sample place as the cache. At the time of
writing this WireProxy compiles for all our target platforms. Argus will also manage all Wireguard certificates.
It will work with 'classic' WG certificates that don't have the [Socks5] segment

[Interface]\n
PrivateKey =\n
Address = XXXX\n
DNS = XXXX\n

[Peer]\n
PublicKey =\n
AllowedIPs = 0.0.0.0/0, ::/0\n
Endpoint = XXXX\n


Argus will automatically add:\n
[Socks5]\n
BindAddress = 127.0.0.1:25344\n


Argus will automatically manage:
– Spinning WireProxy
– Terminating WireProxy
– Selecting Conf (adjust via .env)
– Notifying Dispatchers to work with WireProxy Routing


In addition, the following utilities will also be available via py -m argus.wireproxy through a CLI:\n
– Adding Confs
– Removing Confs
– Benchmarking all Confs against dispatcher expected URLs

"""

import os
import time
import json
import socket
import platform
import subprocess
from datetime import datetime
from utils3 import Container, runAsThread
from argus.wireproxy._utils import download
from utils3.networking.sockets import Server
from argus.cache_utils import ARGUS_CACHE_DIR

# fix for bug 53
def get_wireproxy_filename():
    """
    Returns the appropriate wireproxy filename based on the current platform.

    Returns:
        str: The wireproxy filename for the current platform

    Raises:
        RuntimeError: If the current platform is not supported
    """

    current_platform = platform.system()
    arch = platform.machine()


    # Map platform.machine() to wireproxy architecture naming
    # Note: Linux uses 'aarch64' but wireproxy uses 'arm'
    # macOS uses 'arm64' and wireproxy also uses 'arm64'
    arch_map = {
        'x86_64': 'amd64',
        'AMD64': 'amd64',
        'arm64': 'arm64',
        'aarch64': 'arm'
    }

    # Get mapped values
    os_name = current_platform.lower()
    arch_name = arch_map.get(arch)

    # Check if a platform is supported
    if os_name is None or arch_name is None:
        raise RuntimeError(
            f"Unsupported platform: {current_platform} {arch}. "
            f"Supported platforms are: Linux (amd64, arm64), macOS (amd64, arm64)"
        )

    # Construct filename
    filename = f"wireproxy_{os_name}_{arch_name}.tar.gz"

    # Verify it's one of the valid filenames
    valid_filenames = {
        'wireproxy_darwin_amd64.tar.gz',
        'wireproxy_darwin_arm64.tar.gz',
        'wireproxy_linux_amd64.tar.gz',
        'wireproxy_linux_arm.tar.gz'
    }

    if filename not in valid_filenames:
        raise RuntimeError(
            f"Unsupported platform: {current_platform} {arch}. "
            f"Supported platforms are: Linux (amd64, arm64), macOS (amd64, arm64)"
        )

    return filename


class WireProxyManagement:
    """
    This class manages instances WireProxy used by the entire Argus Project.
    """

    def __init__(self):
        # OS, Architecture
        self._repo_url = "https://github.com/whyvl/wireproxy/releases/latest/download/{}"
        self._wp_instance_dir = os.path.join(ARGUS_CACHE_DIR, "wireproxy")
        self.wg_confs_dir = os.path.join(ARGUS_CACHE_DIR, "wireproxy_confs")
        self.logs_dir = os.path.join(ARGUS_CACHE_DIR, "wp-server-logs")

        if not os.path.exists(self._wp_instance_dir):
            os.mkdir(self._wp_instance_dir)

        if not os.path.exists(self.wg_confs_dir):
            os.mkdir(self.wg_confs_dir)

        if not os.path.exists(self.logs_dir):
            os.mkdir(self.logs_dir)

        if not self.wp_exists:
            self.update_wireproxy()

    def find_correct_wp_version(self):
        filename = get_wireproxy_filename()
        url = self._repo_url.format(filename)
        return url

    def update_wireproxy(self):
        print('Checking OS information...')
        url = self.find_correct_wp_version()
        print('Downloading WireProxy from {}'.format(url))
        try:
            with Container() as c:
                filename, response = download(url)
                response.raise_for_status()
                subprocess.check_call(['tar', '-xzf', filename])
                if not c.join('wireproxy', modify=False).exists():
                    raise FileNotFoundError('Unable to find wireproxy version')

                print('Moving wireproxy...')
                subprocess.check_call(['cp', c.join('wireproxy').path, self._wp_instance_dir])
                print('*' * 40)
                subprocess.check_call([
                    self.wp_fp, '-v'
                ])
                print('*' * 40)
        except subprocess.CalledProcessError as e:
            print('Something went wrong while downloading wireproxy...')
            raise e

    @property
    def wp_exists(self):
        fp = self.wp_fp
        return os.path.exists(fp) and os.path.isfile(fp)

    @property
    def wp_fp(self):
        return os.path.join(self._wp_instance_dir, 'wireproxy')

    @property
    def confs(self):
        return os.listdir(self.wg_confs_dir)

    def get_wireproxy_version(self):
        """Get the WireProxy version"""
        try:
            result = subprocess.check_output([self.wp_fp, '-v'], stderr=subprocess.STDOUT)
            return result.decode().strip()
        except Exception as e:
            return f"Unknown (Error: {e})"


class WireProxy:
    def __init__(self):
        self.asset_management = WireProxyManagement()

    def _add_single_conf(self, conf_path, conf_name=None):
        """
        Helper method to add a single WireGuard configuration file.
        Returns True if successful, False otherwise.
        """
        if not os.path.exists(conf_path):
            print(f"Error: File not found: {conf_path}")
            return False

        # Read the config file
        try:
            with open(conf_path, 'r') as f:
                conf_content = f.read()
        except Exception as e:
            print(f"Error reading file {conf_path}: {e}")
            return False

        # Check if [Socks5] section exists
        if '[Socks5]' not in conf_content and '[socks5]' not in conf_content.lower():
            print(f"  Adding [Socks5] section to configuration...")
            # Add the Socks5 section at the end
            if not conf_content.endswith('\n'):
                conf_content += '\n'
            conf_content += '\n[Socks5]\nBindAddress = 127.0.0.1:25344\n'
        else:
            print(f"  [Socks5] section already exists")

        # Generate conf_name if not provided
        if conf_name is None:
            conf_name = os.path.basename(conf_path)

        if not conf_name.endswith('.conf'):
            conf_name += '.conf'

        # Save to wireproxy_confs directory
        dest_path = os.path.join(self.asset_management.wg_confs_dir, conf_name)

        # Check if file already exists
        if os.path.exists(dest_path):
            print(f"  Warning: {conf_name} already exists, skipping...")
            return False

        try:
            with open(dest_path, 'w') as f:
                f.write(conf_content)
            print(f"  Saved as: {conf_name}")
            return True
        except Exception as e:
            print(f"  Error saving file: {e}")
            return False

    def _add_confs(self):
        """Add a WireGuard configuration file"""
        print("\n--- Add Configuration ---")
        conf_path = input("Enter path to WireGuard config file: ").strip()

        if self._add_single_conf(conf_path):
            dest_path = os.path.join(self.asset_management.wg_confs_dir, os.path.basename(conf_path))
            if not os.path.basename(conf_path).endswith('.conf'):
                dest_path += '.conf'
            print(f"Location: {dest_path}")

    def _bulk_import(self):
        """Bulk import WireGuard configurations from a directory"""
        print("\n--- Bulk Import Configurations ---")
        dir_path = input("Enter path to directory containing WireGuard configs: ").strip()

        if not os.path.exists(dir_path):
            print(f"Error: Directory not found: {dir_path}")
            return

        if not os.path.isdir(dir_path):
            print(f"Error: {dir_path} is not a directory")
            return

        # Get all files in the directory
        files = [f for f in os.listdir(dir_path) if os.path.isfile(os.path.join(dir_path, f))]

        if not files:
            print("No files found in directory")
            return

        print(f"\nFound {len(files)} file(s) in directory")
        print("Processing...\n")

        success_count = 0
        failed_count = 0

        for filename in files:
            file_path = os.path.join(dir_path, filename)
            print(f"Processing: {filename}")

            if self._add_single_conf(file_path, filename):
                success_count += 1
            else:
                failed_count += 1
            print()

        print("=" * 50)
        print(f"Bulk import complete!")
        print(f"  Successfully imported: {success_count}")
        print(f"  Failed/Skipped: {failed_count}")
        print("=" * 50)

    def _remove_confs(self):
        """Remove a WireGuard configuration"""
        print("\n--- Remove Configuration ---")
        confs = self.asset_management.confs

        if not confs:
            print("No configurations found")
            return

        print("Available configurations:")
        for idx, conf in enumerate(confs):
            print(f"  [{idx}] {conf}")

        try:
            choice = input("\nEnter number to remove (or 'q' to cancel): ").strip()
            if choice.lower() == 'q':
                return

            idx = int(choice)
            if 0 <= idx < len(confs):
                conf_to_remove = confs[idx]
                conf_path = os.path.join(self.asset_management.wg_confs_dir, conf_to_remove)

                confirm = input(f"Are you sure you want to remove '{conf_to_remove}'? (y/n): ").strip().lower()
                if confirm == 'y':
                    os.remove(conf_path)
                    print(f"Removed: {conf_to_remove}")
                else:
                    print("Cancelled")
            else:
                print("Invalid selection")
        except ValueError:
            print("Invalid input")

    def _view_confs(self):
        """View all WireGuard configurations"""
        print("\n--- View Configurations ---")
        confs = self.asset_management.confs

        if not confs:
            print("No configurations found")
            return

        print(f"Configurations directory: {self.asset_management.wg_confs_dir}")
        print(f"\nFound {len(confs)} configuration(s):")
        for idx, conf in enumerate(confs):
            print(f"  [{idx}] {conf}")

    # Public API methods for programmatic access
    def add_conf(self, conf_path):
        """
        Programmatically add a WireGuard configuration file.

        Args:
            conf_path: Path to the WireGuard configuration file

        Returns:
            bool: True if successful, False otherwise
        """
        return self._add_single_conf(conf_path)

    def bulk_import(self, dir_path):
        """
        Programmatically bulk import WireGuard configurations from a directory.

        Args:
            dir_path: Path to directory containing WireGuard configs

        Returns:
            tuple: (success_count, failed_count)
        """
        if not os.path.exists(dir_path):
            print(f"Error: Directory not found: {dir_path}")
            return (0, 0)

        if not os.path.isdir(dir_path):
            print(f"Error: {dir_path} is not a directory")
            return (0, 0)

        files = [f for f in os.listdir(dir_path) if os.path.isfile(os.path.join(dir_path, f))]

        if not files:
            print("No files found in directory")
            return (0, 0)

        success_count = 0
        failed_count = 0

        for filename in files:
            file_path = os.path.join(dir_path, filename)
            if self._add_single_conf(file_path, filename):
                success_count += 1
            else:
                failed_count += 1

        return (success_count, failed_count)

    def remove_conf(self, conf_name):
        """
        Programmatically remove a WireGuard configuration.

        Args:
            conf_name: Name of the configuration file to remove

        Returns:
            bool: True if successful, False otherwise
        """
        # Add .conf extension if not present
        if not conf_name.endswith('.conf'):
            conf_name += '.conf'

        conf_path = os.path.join(self.asset_management.wg_confs_dir, conf_name)

        if not os.path.exists(conf_path):
            print(f"Error: Configuration '{conf_name}' not found")
            return False

        try:
            os.remove(conf_path)
            print(f"Removed: {conf_name}")
            return True
        except Exception as e:
            print(f"Error removing {conf_name}: {e}")
            return False

    def list_confs(self):
        """
        Programmatically list all WireGuard configurations.

        Returns:
            list: List of configuration filenames
        """
        return self.asset_management.confs

    def _start_server_interactive(self):
        """Interactive server start"""
        print("\n--- Start WireProxy Server ---")
        confs = self.asset_management.confs

        if not confs:
            print("No configurations found. Please add a configuration first.")
            return

        print("Available configurations:")
        for idx, conf in enumerate(confs):
            print(f"  [{idx}] {conf}")

        try:
            choice = input("\nEnter number to start (or 'q' to cancel): ").strip()
            if choice.lower() == 'q':
                return

            idx = int(choice)
            if 0 <= idx < len(confs):
                conf_name = confs[idx]
                print(f"\nStarting server with config: {conf_name}")

                # Import here to avoid circular dependency
                from argus.wireproxy.__main__ import send_server_command, ensure_daemon_running

                # Ensure daemon is running
                if not ensure_daemon_running():
                    print("Failed to start daemon. Cannot proceed.")
                    return

                response = send_server_command('spin_up', conf_name)

                if response.get('error'):
                    print(f"Error: {response['error']}")
                else:
                    result = response.get('result', {})
                    print(f"\n✓ Server started successfully!")
                    print(f"  Status: {result.get('status')}")
                    print(f"  Config: {result.get('config')}")
                    print(f"  PID: {result.get('pid')}")
                    if result.get('log_file'):
                        print(f"  Log File: {result.get('log_file')}")
            else:
                print("Invalid selection")
        except ValueError:
            print("Invalid input")
        except Exception as e:
            print(f"Error: {e}")

    def _stop_server_interactive(self):
        """Interactive server stop"""
        print("\n--- Stop WireProxy Server ---")

        from argus.wireproxy.__main__ import send_server_command, ensure_daemon_running

        # Ensure daemon is running
        if not ensure_daemon_running():
            print("Daemon is not running. Nothing to stop.")
            return

        response = send_server_command('spin_down')

        if response.get('error'):
            print(f"Error: {response['error']}")
        else:
            result = response.get('result', {})
            print(f"\n✓ Server stopped successfully!")
            print(f"  Status: {result.get('status')}")
            print(f"  Previous Config: {result.get('previous_config')}")
            if result.get('log_file'):
                print(f"  Log File: {result.get('log_file')}")

    def _server_status_interactive(self):
        """Interactive server status check"""
        print("\n--- WireProxy Server Status ---")

        from argus.wireproxy.__main__ import send_server_command, check_daemon_running, ensure_daemon_running

        # Check if daemon is running
        if not check_daemon_running():
            print("\n✗ WireProxyServer daemon is NOT running")
            print("\nWould you like to start the daemon? (y/n): ", end='')
            choice = input().strip().lower()
            if choice == 'y':
                if ensure_daemon_running():
                    print("Daemon started successfully!")
                else:
                    print("Failed to start daemon.")
                    return
            else:
                return

        response = send_server_command('state')

        if response.get('error'):
            print(f"Error: {response['error']}")
        else:
            result = response.get('result', {})
            print("\n✓ WireProxyServer daemon is running")
            if result.get('running'):
                print(f"\n✓ WireProxy server is RUNNING")
                print(f"  Config: {result.get('config')}")
                print(f"  PID: {result.get('pid')}")
                if result.get('log_file'):
                    print(f"  Log File: {result.get('log_file')}")
            else:
                print("\n✗ WireProxy server is NOT running")
                print("  (Daemon is running but no WireProxy instance is active)")

    def command_line(self):
        commands = [
            ('Add Config', 'Add a WireGuard configuration to Argus', self._add_confs),
            ('Bulk Import', 'Import all configs from a directory', self._bulk_import),
            ('Remove Config', 'Remove a WireGuard configuration', self._remove_confs),
            ('View Configs', 'View all WireGuard configurations', self._view_confs),
            ('Start Server', 'Start the WireProxy server', self._start_server_interactive),
            ('Stop Server', 'Stop the WireProxy server', self._stop_server_interactive),
            ('Server Status', 'Check WireProxy server status', self._server_status_interactive),
        ]

        while True:
            print("\n" + "=" * 50)
            print("WireProxy Configuration & Server Manager")
            print("=" * 50)

            for idx in range(len(commands)):
                print(f'[{idx}] {commands[idx][0]} – {commands[idx][1]}')
            print(f'[q] Quit')

            choice = input("\nSelect an option: ").strip().lower()

            if choice == 'q':
                print("Exiting...")
                break

            try:
                choice_idx = int(choice)
                if 0 <= choice_idx < len(commands):
                    commands[choice_idx][2]()  # Call the function
                else:
                    print("Invalid selection")
            except ValueError:
                print("Invalid input. Please enter a number or 'q'")


class WireProxyServer:
    def __init__(self):
        self.svr = Server(
            host="127.0.0.1",
            port=23888,
            on_recv=self._recv,
            on_disconnect=lambda client, addr: print("{} Disconnected".format(addr))
        )
        self.proc: subprocess.Popen = None
        self.management = WireProxyManagement()
        self.current_conf = None
        self.log_file = None
        self.log_file_handle = None

    @runAsThread
    def run_server(self):
        self.svr.start()


    def _recv(self, client: socket.socket, addr: tuple, data: bytes):
        """
        API:
        IN==> [CMD]:[ARG1,ARG2]\n
        OUT==>{
            'CMD': String (echos CMD),
            'result': Any,
            'error': None | String
        }

        Common Errors:
        – Newline Not found = If the input bytes do not end with a new line, it will not be processed
        – Not enough args = If there are not enough args to fulfil the function
        – Parsing error = If unable to parse, note in the event of no args the command should be CMD:\n you
        cannot commit ':'.

        """
        commands = {
            'spin_up': self.spin_up,
            'spin_down': self.spin_down,
            'state': self._state,
            'available_confs': self._available_confs
        }

        # Helper function to send response
        def send_response(cmd, result=None, error=None):
            response = {
                'CMD': cmd,
                'result': result,
                'error': error
            }
            client.send((json.dumps(response) + '\n').encode())

        try:
            # Check if data ends with newline
            if not data.endswith(b'\n'):
                send_response('unknown', error='Newline not found')
                return

            # Decode and strip newline
            message = data.decode('utf-8').strip()

            # Parse command and arguments
            if ':' not in message:
                send_response('unknown', error='Parsing error: colon not found')
                return

            parts = message.split(':', 1)
            cmd = parts[0].strip()
            args_str = parts[1].strip() if len(parts) > 1 else ''

            # Parse arguments (comma-separated)
            args = [arg.strip() for arg in args_str.split(',') if arg.strip()] if args_str else []

            # Check if the command exists
            if cmd not in commands:
                send_response(cmd, error=f'Unknown command: {cmd}')
                return

            # Execute command
            try:
                if len(args) == 0:
                    result = commands[cmd]()
                else:
                    # noinspection all
                    result = commands[cmd](*args)
                send_response(cmd, result=result)
            except TypeError as e:
                send_response(cmd, error=f'Not enough args: {str(e)}')
            except Exception as e:
                send_response(cmd, error=str(e))

        except Exception as e:
            send_response('unknown', error=f'Parsing error: {str(e)}')

    def spin_up(self, conf_name):
        """
        Start WireProxy with the specified configuration.

        Args:
            conf_name: Name of the configuration file (with or without .conf extension)

        Returns:
            dict: Status information
        """
        # Check if WireProxy is already running
        if self.proc is not None and self.proc.poll() is None:
            raise Exception(f'WireProxy is already running with config: {self.current_conf}')

        # Add .conf extension if not present
        if not conf_name.endswith('.conf'):
            conf_name += '.conf'

        # Check if config exists
        conf_path = os.path.join(self.management.wg_confs_dir, conf_name)
        if not os.path.exists(conf_path):
            raise Exception(f'Configuration not found: {conf_name}')

        # Create log file
        timestamp = int(time.time())
        conf_name_clean = conf_name.replace('.conf', '')
        log_filename = f"{timestamp}_{conf_name_clean}.log"
        log_file_path = os.path.join(self.management.logs_dir, log_filename)

        # Start the WireProxy process
        try:
            # Open log file with header
            log_handle = open(log_file_path, 'w', buffering=1)  # Line buffered
            log_handle.write("=" * 80 + "\n")
            log_handle.write("WireProxy Server Log\n")
            log_handle.write("=" * 80 + "\n")
            log_handle.write(f"Start Time: {datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')}\n")
            log_handle.write(f"Unix Timestamp: {timestamp}\n")
            log_handle.write(f"Configuration: {conf_name}\n")

            # Get WireProxy version
            wp_version = self.management.get_wireproxy_version()
            log_handle.write(f"WireProxy Version: {wp_version}\n")
            log_handle.write(f"Configuration File: {conf_path}\n")
            log_handle.write("\nProcess Output:\n")
            log_handle.write("=" * 80 + "\n")
            log_handle.flush()

            # Start subprocess with log file as stdout/stderr
            self.proc = subprocess.Popen(
                [self.management.wp_fp, '-c', conf_path],
                stdout=log_handle,
                stderr=log_handle,
                bufsize=1,
                text=True
            )
            self.current_conf = conf_name
            self.log_file = log_file_path
            self.log_file_handle = log_handle

            # Give it a moment to start
            time.sleep(0.5)

            # Check if it's still running
            if self.proc.poll() is not None:
                # Close the log file
                log_handle.close()

                # Read any error from the log file
                with open(log_file_path, 'r') as log:
                    log_content = log.read()

                self.proc = None
                self.current_conf = None
                self.log_file = None
                self.log_file_handle = None
                raise Exception(f'WireProxy failed to start. Check log: {log_file_path}')

            return {
                'status': 'running',
                'config': conf_name,
                'pid': self.proc.pid,
                'log_file': log_file_path
            }
        except Exception as e:
            # Clean up if we opened the log file
            if hasattr(self, 'log_file_handle') and self.log_file_handle:
                self.log_file_handle.close()
            self.proc = None
            self.current_conf = None
            self.log_file = None
            self.log_file_handle = None
            raise e

    def spin_down(self):
        """
        Terminate the running WireProxy process.

        Returns:
            dict: Status information
        """
        if self.proc is None or self.proc.poll() is not None:
            raise Exception('WireProxy is not running')

        try:
            # Write teardown header to log
            if self.log_file_handle:
                self.log_file_handle.write("\n" + "=" * 80 + "\n")
                self.log_file_handle.write("WireProxy Server Teardown\n")
                self.log_file_handle.write("=" * 80 + "\n")
                self.log_file_handle.write(f"Stop Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                self.log_file_handle.write(f"Unix Timestamp: {int(time.time())}\n")
                self.log_file_handle.write("Status: Initiating shutdown\n")
                self.log_file_handle.flush()

            self.proc.terminate()

            # Wait for process to terminate (with timeout)
            try:
                self.proc.wait(timeout=5)
                shutdown_method = "Graceful termination"
            except subprocess.TimeoutExpired:
                # Force kill if it doesn't terminate gracefully
                self.proc.kill()
                self.proc.wait()
                shutdown_method = "Force killed"

            # Write final status to log and close
            if self.log_file_handle:
                self.log_file_handle.write(f"Shutdown Method: {shutdown_method}\n")
                self.log_file_handle.write(f"Final Status: Process terminated\n")
                self.log_file_handle.write("=" * 80 + "\n")
                self.log_file_handle.write("End of log\n")
                self.log_file_handle.write("=" * 80 + "\n")
                self.log_file_handle.close()

            old_conf = self.current_conf
            old_log_file = self.log_file

            self.proc = None
            self.current_conf = None
            self.log_file = None
            self.log_file_handle = None

            return {
                'status': 'stopped',
                'previous_config': old_conf,
                'log_file': old_log_file
            }
        except Exception as e:
            raise Exception(f'Failed to stop WireProxy: {str(e)}')

    def _state(self):
        """
        Get the current state of WireProxy.

        Returns:
            dict: Current state information
        """
        if self.proc is None:
            return {
                'running': False,
                'config': None,
                'pid': None,
                'log_file': None
            }

        # Check if process is still alive
        if self.proc.poll() is not None:
            # Process has terminated
            old_log_file = self.log_file

            # Close the log file handle if still open
            if self.log_file_handle:
                try:
                    self.log_file_handle.close()
                except:
                    pass

            self.proc = None
            self.current_conf = None
            self.log_file = None
            self.log_file_handle = None

            return {
                'running': False,
                'config': None,
                'pid': None,
                'log_file': old_log_file
            }

        return {
            'running': True,
            'config': self.current_conf,
            'pid': self.proc.pid,
            'log_file': self.log_file
        }

    def _available_confs(self):
        """
        Get list of available WireGuard configurations.

        Returns:
            dict: List of available configurations
        """
        confs = self.management.confs
        return {
            'count': len(confs),
            'configs': confs
        }


