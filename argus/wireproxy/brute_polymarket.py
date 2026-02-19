"""
Brute force test all VPN configurations against Polymarket geoblock endpoint.

Tests each available WireGuard configuration by:
1. Starting WireProxy server with the config
2. Checking geoblock status at https://polymarket.com/api/geoblock
3. Running ping and curl to measure latency
4. Comparing results and displaying best performer
"""

import sys
import json
import time
import socket
import subprocess
import statistics
from termcolor import colored
from typing import Dict, List, Optional, Tuple
from argus.wireproxy import WireProxy, WireProxyServer
from argus.wireproxy.__main__ import send_server_command, ensure_daemon_running, check_daemon_running


class PolymarketBruteTester:
    """Test all VPN configs against Polymarket endpoints."""

    GEOBLOCK_ENDPOINT = "https://polymarket.com/api/geoblock"
    POLYMARKET_ENDPOINTS = [
        "https://polymarket.com",
        "https://clob.polymarket.com",
        "https://gamma-api.polymarket.com"
    ]

    def __init__(self):
        self.wp = WireProxy()
        self.results: List[Dict] = []
        self.configs = self.wp.list_confs()

    def test_geoblock_status(self, bind_address: str) -> Tuple[Optional[bool], Optional[str]]:
        """
        Test if the endpoint is geoblocked.

        Returns:
            Tuple of (is_blocked, error_type)
            - is_blocked: True/False if JSON received, None if error
            - error_type: None if success, error description otherwise
        """
        try:
            args = [
                'curl',
                '-s',
                '-X', 'GET',
                '--socks5-hostname', bind_address,
                '-k',
                '--max-time', '15',
                '--connect-timeout', '10',
                self.GEOBLOCK_ENDPOINT
            ]

            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=20
            )

            if result.returncode != 0:
                # Map curl exit codes to descriptive errors
                curl_errors = {
                    1: "unsupported_protocol",
                    6: "couldnt_resolve_host",
                    7: "failed_to_connect",
                    28: "operation_timeout",
                    35: "ssl_connect_error",
                    51: "peer_cert_error",
                    52: "empty_response",
                    60: "ssl_cert_problem",
                    97: "socks5_connect_failed"
                }
                error_desc = curl_errors.get(result.returncode, f"exit_{result.returncode}")
                return None, error_desc

            try:
                data = json.loads(result.stdout)
                is_blocked = data.get('blocked', False)
                return is_blocked, None
            except json.JSONDecodeError as e:
                # If we got non-JSON response, it might be HTML error page
                if result.stdout.strip().startswith('<'):
                    return None, "html_response"
                return None, "json_decode_error"

        except subprocess.TimeoutExpired:
            return None, "timeout"
        except FileNotFoundError:
            return None, "curl_not_found"
        except Exception as e:
            return None, f"error({type(e).__name__})"

    def measure_latency(self, bind_address: str, endpoint: str) -> Optional[float]:
        """
        Measure latency to endpoint using curl.

        Returns:
            Average latency in milliseconds or None if failed
        """
        times = []
        for _ in range(3):
            try:
                args = [
                    'curl',
                    '-s',
                    '-o', '/dev/null',
                    '-w', '%{time_total}',
                    '--socks5-hostname', bind_address,
                    '-k',
                    '--max-time', '15',
                    '--connect-timeout', '10',
                    endpoint
                ]

                result = subprocess.run(
                    args,
                    capture_output=True,
                    text=True,
                    timeout=20
                )

                if result.returncode == 0:
                    t = float(result.stdout.strip()) * 1000  # Convert to ms
                    times.append(t)
            except (subprocess.TimeoutExpired, ValueError):
                pass

        if times:
            return statistics.mean(times)
        return None

    def measure_ping(self, bind_address: str) -> Optional[float]:
        """
        Measure ICMP latency to proxy host.

        Returns:
            Average latency in milliseconds or None if failed
        """
        try:
            host = bind_address.split(':')[0]

            if host in ['127.0.0.1', 'localhost', '::1']:
                return None

            result = subprocess.run(
                ['ping', '-c', '3', host],
                capture_output=True,
                text=True,
                timeout=15
            )

            times = []
            for line in result.stdout.split('\n'):
                if 'time=' in line:
                    try:
                        time_str = line.split('time=')[1].split(' ')[0]
                        times.append(float(time_str))
                    except (ValueError, IndexError):
                        pass

            if times:
                return statistics.mean(times)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        return None

    def test_config(self, conf_name: str) -> Dict:
        """
        Test a single configuration.

        Returns:
            Dict with test results
        """
        result = {
            'config': conf_name,
            'status': 'unknown',
            'blocked': None,
            'ping_ms': None,
            'latency_ms': None,
            'error': None
        }

        try:
            # Ensure daemon is running
            if not ensure_daemon_running():
                result['error'] = 'Failed to start daemon'
                return result

            # First, stop any running instance
            try:
                send_server_command('spin_down')
                time.sleep(1)
            except:
                pass

            # Spin up the config
            response = send_server_command('spin_up', conf_name)
            if response.get('error'):
                result['error'] = f"Failed to spin up: {response['error']}"
                return result

            # Wait for server to stabilize and SOCKS5 to be ready
            time.sleep(4)

            # Get bind address from available_confs or use default
            bind_address = '127.0.0.1:25344'

            # Verify SOCKS5 port is accessible by trying to connect
            sock_ready = False
            for attempt in range(5):
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(2)
                    sock.connect(('127.0.0.1', 25344))
                    sock.close()
                    sock_ready = True
                    break
                except (socket.timeout, ConnectionRefusedError):
                    time.sleep(0.5)

            if not sock_ready:
                result['error'] = 'socks5_port_not_responsive'
                return result

            # Test geoblock status
            is_blocked, error_type = self.test_geoblock_status(bind_address)
            result['blocked'] = is_blocked

            if error_type:
                result['status'] = 'geoblock_test_failed'
                result['error'] = f"geoblock_{error_type}"
            elif is_blocked:
                result['status'] = 'blocked'
            else:
                result['status'] = 'accessible'

                # Measure ping to proxy
                ping_ms = self.measure_ping(bind_address)
                result['ping_ms'] = ping_ms

                # Measure latency to primary endpoint
                latency_ms = self.measure_latency(bind_address, self.POLYMARKET_ENDPOINTS[0])
                result['latency_ms'] = latency_ms

            # Spin down the config
            send_server_command('spin_down')
            time.sleep(0.5)

        except Exception as e:
            result['error'] = str(e)
            try:
                send_server_command('spin_down')
            except:
                pass

        return result

    def print_results_table(self):
        """Print results in a formatted table."""
        if not self.results:
            print("No results to display")
            return

        # Filter accessible configs
        accessible = [r for r in self.results if r['status'] == 'accessible']

        print("\n" + "=" * 120)
        print("POLYMARKET VPN CONFIGURATION TEST RESULTS")
        print("=" * 120)
        print()

        # Header
        header = f"{'Config':<32} {'Status':<22} {'Blocked':<10} {'Latency (ms)':<15} {'Notes':<25}"
        print(header)
        print("-" * 120)

        # Results
        for result in self.results:
            config = result['config'][:30]
            status = colored(result['status'].upper(), 'green' if result['status'] == 'accessible' else ('red' if result['status'] == 'blocked' else 'yellow'))
            blocked = 'Yes' if result['blocked'] else ('No' if result['blocked'] is False else 'N/A')
            latency_str = f"{result['latency_ms']:.2f}" if result['latency_ms'] is not None else "N/A"
            
            # Notes for different statuses
            if result['error']:
                notes = result['error'][:23]
            elif result['ping_ms'] is not None:
                notes = f"ping:{result['ping_ms']:.1f}ms"
            else:
                notes = ""

            print(f"{config:<32} {status:<22} {blocked:<10} {latency_str:<15} {notes:<25}")

        print("-" * 120)

        # Summary
        blocked_count = len([r for r in self.results if r['status'] == 'blocked'])
        geoblock_failed = len([r for r in self.results if r['status'] == 'geoblock_test_failed'])
        spinup_failed = len([r for r in self.results if r['status'] == 'unknown' and r['error']])
        
        print(f"\nTotal configs tested: {len(self.results)}")
        print(f"  ✓ Accessible: {len(accessible)}")
        print(f"  ✗ Blocked: {blocked_count}")
        print(f"  ! Geoblock check failed: {geoblock_failed}")
        print(f"  ! Spin-up/other errors: {spinup_failed}")

        # Best performer
        if accessible:
            best = min(accessible, key=lambda x: x['latency_ms'] if x['latency_ms'] is not None else float('inf'))
            if best['latency_ms'] is not None:
                print()
                print("=" * 120)
                print("BEST CONFIGURATION")
                print("=" * 120)
                print(f"Config:    {best['config']}")
                print(f"Status:    {best['status']}")
                print(f"Latency:   {best['latency_ms']:.2f} ms")
                if best['ping_ms'] is not None:
                    print(f"Ping:      {best['ping_ms']:.2f} ms")
                print("=" * 120)

    def run(self) -> int:
        """Run the brute force test."""
        if not self.configs:
            print("Error: No VPN configurations found")
            print("Add configurations using: python3 -m argus.wireproxy --add-conf <path>")
            return 1

        print(f"Testing {len(self.configs)} VPN configuration(s) against Polymarket...")
        print()

        # Start daemon
        if not check_daemon_running():
            print("Starting WireProxyServer daemon...")
            if not ensure_daemon_running():
                print("Error: Failed to start daemon")
                return 1

        # Test each config
        for i, conf in enumerate(self.configs, 1):
            print(f"[{i}/{len(self.configs)}] Testing {conf}...", end=' ', flush=True)
            result = self.test_config(conf)
            self.results.append(result)

            if result['error']:
                print(f"✗ {result['error']}")
            elif result['status'] == 'blocked':
                print(f"✗ BLOCKED")
            elif result['status'] == 'geoblock_test_failed':
                print(f"! GEOBLOCK_TEST_FAILED")
            else:
                latency_str = f"{result['latency_ms']:.1f}ms" if result['latency_ms'] else "N/A"
                print(f"✓ ACCESSIBLE ({latency_str})")

        # Print summary table
        self.print_results_table()

        return 0


def brute_polymarket_configs() -> int:
    """Main entry point for brute polymarket command."""
    try:
        tester = PolymarketBruteTester()
        return tester.run()
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        return 1
    except Exception as e:
        print(f"Error: {e}")
        return 1
