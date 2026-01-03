#!/usr/bin/env python3
"""
WireProxy CLI - Manage WireGuard configurations and server for Argus

Usage:
    python3 -m argus.wireproxy # Interactive mode
    python3 -m argus.wireproxy --add-conf <path>  # Add a config
    python3 -m argus.wireproxy --bulk-import <dir> # Bulk import from directory
    python3 -m argus.wireproxy --remove-conf <name> # Remove a config
    python3 -m argus.wireproxy --list-confs # List all configs
    python3 -m argus.wireproxy --start-server <conf> # Start WireProxy server
    python3 -m argus.wireproxy --stop-server # Stop WireProxy server
    python3 -m argus.wireproxy --server-status # Check server status
"""

import argparse
import sys
import json
import socket
import subprocess
import time
from argus.wireproxy import WireProxy, WireProxyServer


def check_daemon_running():
    """Check if the WireProxyServer daemon is running"""
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(1)
        result = client.connect_ex(('127.0.0.1', 23888))
        client.close()
        return result == 0
    except Exception as e:
        print('WARNING: Could not check daemon status:', str(e))
        return False


def start_daemon():
    """Start the WireProxyServer daemon in the background"""
    # Start the daemon process by calling ourselves with --run-server-daemon
    subprocess.Popen(
        [sys.executable, '-m', 'argus.wireproxy', '--run-server-daemon'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True  # Detach from parent process
    )

    # Wait a moment for the daemon to start
    time.sleep(1)

    # Check if it started successfully
    if check_daemon_running():
        print("✓ WireProxyServer daemon started successfully")
        return True
    else:
        print("✗ Failed to start WireProxyServer daemon")
        return False


def ensure_daemon_running():
    """Ensure the daemon is running, start it if not"""
    if not check_daemon_running():
        print("WireProxyServer daemon is not running, starting it...")
        return start_daemon()
    return True


def send_server_command(cmd, *args):
    """Send a command to the WireProxyServer via socket"""
    try:
        # Connect to the server
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(5)
        client.connect(('127.0.0.1', 23888))

        # Format command
        if args:
            message = f"{cmd}:{','.join(args)}\n"
        else:
            message = f"{cmd}:\n"

        # Send command
        client.send(message.encode())

        # Receive response
        response = b''
        while True:
            chunk = client.recv(1024)
            if not chunk:
                break
            response += chunk
            if b'\n' in chunk:
                break

        client.close()

        # Parse response
        if response:
            return json.loads(response.decode().strip())
        else:
            return {'error': 'No response from server'}

    except ConnectionRefusedError:
        return {'error': 'Could not connect to WireProxyServer daemon'}
    except socket.timeout:
        return {'error': 'Connection to WireProxyServer timed out'}
    except Exception as e:
        return {'error': f'Failed to communicate with server: {str(e)}'}


def main():
    parser = argparse.ArgumentParser(
        description='WireProxy Configuration and Server Manager for Argus',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive mode
  python3 -m argus.wireproxy

  # Configuration Management
  python3 -m argus.wireproxy --add-conf /path/to/config.conf
  python3 -m argus.wireproxy --bulk-import /path/to/configs/
  python3 -m argus.wireproxy --remove-conf myconfig
  python3 -m argus.wireproxy --list-confs

  # Server Management
  python3 -m argus.wireproxy --start-server myconfig
  python3 -m argus.wireproxy --stop-server
  python3 -m argus.wireproxy --server-status
        """
    )

    parser.add_argument(
        '--add-conf',
        metavar='PATH',
        help='Add a WireGuard configuration file'
    )

    parser.add_argument(
        '--bulk-import',
        metavar='DIR',
        help='Bulk import WireGuard configurations from a directory'
    )

    parser.add_argument(
        '--remove-conf',
        metavar='NAME',
        help='Remove a WireGuard configuration by name (e.g., "myconfig" or "myconfig.conf")'
    )

    parser.add_argument(
        '--list-confs',
        action='store_true',
        help='List all WireGuard configurations'
    )

    parser.add_argument(
        '--start-server',
        metavar='CONF',
        help='Start WireProxy server with specified configuration'
    )

    parser.add_argument(
        '--stop-server',
        action='store_true',
        help='Stop the running WireProxy server'
    )

    parser.add_argument(
        '--server-status',
        action='store_true',
        help='Check the status of the WireProxy server'
    )

    parser.add_argument(
        '--run-server-daemon',
        action='store_true',
        help='Run the WireProxyServer daemon (keeps running until stopped)'
    )

    args = parser.parse_args()

    # Initialize WireProxy
    wp = WireProxy()

    # Check if any flags were provided
    if args.add_conf:
        # Add a single configuration
        print(f"Adding configuration: {args.add_conf}")
        if wp.add_conf(args.add_conf):
            print("Successfully added configuration")
            sys.exit(0)
        else:
            print("Failed to add configuration")
            sys.exit(1)

    elif args.bulk_import:
        # Bulk import configurations
        print(f"Bulk importing from: {args.bulk_import}")
        success, failed = wp.bulk_import(args.bulk_import)
        print(f"\nImport complete: {success} successful, {failed} failed/skipped")
        sys.exit(0 if success > 0 else 1)

    elif args.remove_conf:
        # Remove a configuration
        print(f"Removing configuration: {args.remove_conf}")
        if wp.remove_conf(args.remove_conf):
            print("Successfully removed configuration")
            sys.exit(0)
        else:
            print("Failed to remove configuration")
            sys.exit(1)

    elif args.list_confs:
        # List all configurations
        confs = wp.list_confs()
        if confs:
            print(f"Found {len(confs)} configuration(s):")
            for idx, conf in enumerate(confs, 1):
                print(f"  {idx}. {conf}")
            sys.exit(0)
        else:
            print("No configurations found")
            sys.exit(0)

    elif args.run_server_daemon:
        # Run the server daemon (foreground mode)
        print("Starting WireProxyServer daemon...")
        print("Press Ctrl+C to stop the daemon")
        print("-" * 50)

        server = WireProxyServer()
        server.run_server()

        try:
            # Keep the process alive
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n" + "-" * 50)
            print("Stopping WireProxyServer daemon...")
            sys.exit(0)

    elif args.start_server:
        # Ensure daemon is running
        if not ensure_daemon_running():
            print("Failed to start daemon. Cannot proceed.")
            sys.exit(1)

        # Start the WireProxy server
        print(f"Starting WireProxy server with config: {args.start_server}")
        response = send_server_command('spin_up', args.start_server)

        if response.get('error'):
            print(f"Error: {response['error']}")
            sys.exit(1)
        else:
            result = response.get('result', {})
            print(f"✓ Server started successfully!")
            print(f"  Status: {result.get('status')}")
            print(f"  Config: {result.get('config')}")
            print(f"  PID: {result.get('pid')}")
            if result.get('log_file'):
                print(f"  Log File: {result.get('log_file')}")
            sys.exit(0)

    elif args.stop_server:
        # Ensure daemon is running
        if not ensure_daemon_running():
            print("Daemon is not running. Nothing to stop.")
            sys.exit(1)

        # Stop the WireProxy server
        print("Stopping WireProxy server...")
        response = send_server_command('spin_down')

        if response.get('error'):
            print(f"Error: {response['error']}")
            sys.exit(1)
        else:
            result = response.get('result', {})
            print(f"✓ Server stopped successfully!")
            print(f"  Status: {result.get('status')}")
            print(f"  Previous Config: {result.get('previous_config')}")
            if result.get('log_file'):
                print(f"  Log File: {result.get('log_file')}")
            sys.exit(0)

    elif args.server_status:
        # Check if daemon is running first
        if not check_daemon_running():
            print("✗ WireProxyServer daemon is NOT running")
            print("\nTo start the daemon, run:")
            print("  python3 -m argus.wireproxy --run-server-daemon")
            sys.exit(1)

        # Check server status
        response = send_server_command('state')

        if response.get('error'):
            print(f"Error: {response['error']}")
            sys.exit(1)
        else:
            result = response.get('result', {})
            print("✓ WireProxyServer daemon is running")
            if result.get('running'):
                print(f"\n✓ WireProxy server is RUNNING")
                print(f"  Config: {result.get('config')}")
                print(f"  PID: {result.get('pid')}")
                if result.get('log_file'):
                    print(f"  Log File: {result.get('log_file')}")
            else:
                print("\n✗ WireProxy server is NOT running")
                print("  (Daemon is running but no WireProxy instance is active)")
            sys.exit(0)

    else:
        # No flags provided - run interactive mode
        print("No flags provided, starting interactive mode...")
        print("Use -h or --help to see available options\n")
        wp.command_line()


if __name__ == '__main__':
    main()
