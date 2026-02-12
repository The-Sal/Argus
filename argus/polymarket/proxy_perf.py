"""
Proxy Performance Profiler for Polymarket

Measures and displays network latency through SOCKS5 proxies with
unified table output showing hop breakdowns and proxy overhead.
"""

import subprocess
from typing import Optional


class ProxyPerformanceProfiler:
    """Profiles network performance to Polymarket endpoints via SOCKS5 proxy."""

    # Polymarket endpoints to measure
    TARGET_DOMAINS = [
        'https://clob.polymarket.com',
        'https://gamma-api.polymarket.com',
        'https://polymarket.com/api/geoblock'
    ]

    def __init__(self, print_callback=print):
        """
        Initialize the profiler.

        :param print_callback: Function to use for output (defaults to print)
        """
        self._print = print_callback
        self._results = {
            'proxy': {},
            'direct': {},
            'bind_addr_hop': None
        }

    def measure_bind_addr_hop(self, bind_address: str) -> Optional[dict]:
        """
        Measures ICMP latency to the bind address host.

        :param bind_address: SOCKS5 proxy address (e.g., '100.81.248.63:25344')
        :return: Dict with {'min': float, 'avg': float, 'max': float} or None
        """
        if bind_address is None or bind_address == 'localhost':
            return None

        try:
            host = bind_address.split(':')[0]
        except (IndexError, ValueError):
            return None

        if host in ['127.0.0.1', 'localhost', '::1']:
            return None

        self._print(f"Measuring latency to bind address at {host}...")

        times = []
        try:
            result = subprocess.run(
                ['ping', '-c', '5', host],
                capture_output=True,
                text=True,
                timeout=15
            )

            # Parse individual response times
            for line in result.stdout.split('\n'):
                if 'time=' in line and 'bytes from' in line:
                    try:
                        time_str = line.split('time=')[1].split(' ')[0]
                        time_ms = float(time_str)
                        times.append(time_ms / 1000)
                    except (ValueError, IndexError):
                        pass

            # Parse summary line as fallback
            if not times:
                for line in result.stdout.split('\n'):
                    if 'round-trip' in line and 'min/avg/max' in line:
                        try:
                            stats_str = line.split('=')[1].strip()
                            values = stats_str.split('/')[0:3]
                            min_ms = float(values[0])
                            avg_ms = float(values[1])
                            max_ms = float(values[2])
                            times = [min_ms / 1000, avg_ms / 1000, max_ms / 1000]
                        except (ValueError, IndexError):
                            pass
                        break

        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        if times:
            result = {
                'min': min(times),
                'avg': sum(times) / len(times),
                'max': max(times),
                'times': times
            }
            self._print(
                f"  Bind address hop: min={result['min'] * 1000:.2f}ms, "
                f"avg={result['avg'] * 1000:.2f}ms, max={result['max'] * 1000:.2f}ms"
            )
            return result
        else:
            self._print(f"  Failed to measure bind address hop to {host}")
            return None

    def measure_rtt(self, bind_address: Optional[str] = None) -> dict:
        """
        Measures HTTP RTT to Polymarket endpoints.

        :param bind_address: SOCKS5 proxy address. If None, measures direct.
        :return: Dict mapping domain -> {'min': float, 'avg': float, 'max': float}
        """
        bind_args = ['--socks5-hostname', bind_address] if bind_address else []
        use_proxy = bind_address is not None

        # Verify curl is available
        try:
            subprocess.check_call(
                ['curl', '--version'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            self._print("Warning: 'curl' not found. Proxy profiling disabled.")
            return {}

        results = {}
        for addr in self.TARGET_DOMAINS:
            if use_proxy:
                self._print(f"Checking RTT to {addr} via proxy at {bind_address}...")
            else:
                self._print(f"Checking RTT to {addr} without proxy...")

            times = []
            for _ in range(5):
                try:
                    args = [
                               'curl', '-s', '-o', '/dev/null',
                               '-w', '%{time_total}',
                               '-k',
                               addr
                           ] + bind_args

                    result = subprocess.run(
                        args,
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    if result.returncode == 0:
                        t = float(result.stdout.strip())
                        times.append(t)
                except (subprocess.TimeoutExpired, ValueError):
                    pass

            if times:
                results[addr] = {
                    'min': min(times),
                    'avg': sum(times) / len(times),
                    'max': max(times),
                    'times': times
                }
                self._print(
                    f"  RTT: min={results[addr]['min']:.3f}s, "
                    f"avg={results[addr]['avg']:.3f}s, max={results[addr]['max']:.3f}s"
                )
            else:
                self._print(f"  No successful requests to {addr}")
                results[addr] = {'min': None, 'avg': None, 'max': None, 'times': []}

        return results

    def run_profiling(
            self,
            bind_address: Optional[str],
            profile_mode: int
    ) -> dict:
        """
        Run the full profiling suite.

        :param bind_address: SOCKS5 proxy address
        :param profile_mode: 0 = proxy only, 1 = proxy + direct comparison
        :return: All measurement results
        """
        # Measure proxy path
        if profile_mode >= 0:
            self._results['proxy'] = self.measure_rtt(bind_address)

            # Measure bind address hop if non-local
            if bind_address and bind_address != 'localhost':
                try:
                    host = bind_address.split(':')[0]
                    if host not in ['127.0.0.1', 'localhost', '::1']:
                        self._results['bind_addr_hop'] = self.measure_bind_addr_hop(bind_address)
                except (IndexError, ValueError):
                    pass

        # Measure direct path for comparison
        if profile_mode == 1:
            self._results['direct'] = self.measure_rtt(None)

        return self._results

    def display_table(self, bind_address: Optional[str], profile_mode: int):
        """
        Display unified performance table with dynamic column widths.

        :param bind_address: SOCKS5 proxy address used
        :param profile_mode: 0 = proxy only, 1 = with direct comparison
        """
        proxy_results = self._results.get('proxy', {})
        direct_results = self._results.get('direct', {})
        bind_hop = self._results.get('bind_addr_hop')

        if not proxy_results:
            self._print("No proxy results to display")
            return

        # Determine host from bind address
        host = "localhost"
        if bind_address and bind_address != 'localhost':
            try:
                host = bind_address.split(':')[0]
            except (IndexError, ValueError):
                host = "unknown"

        is_local = host in ['127.0.0.1', 'localhost', '::1']
        all_domains = sorted(set(proxy_results.keys()) | set(direct_results.keys() if direct_results else []))

        # First pass: calculate column widths based on actual content
        if profile_mode == 1:
            # Columns: Domain, Hop1, Hop2, Proxy, Direct, Overhead
            headers = ['Domain', 'Hop 1 (ms)', 'Hop 2 (ms)', 'Proxy (ms)', 'Direct (ms)', 'Overhead (ms)']
            col_widths = [len(h) for h in headers]

            for domain in all_domains:
                proxy = proxy_results.get(domain, {})
                direct = direct_results.get(domain, {}) if direct_results else {}
                proxy_avg = proxy.get('avg')

                if proxy_avg is None:
                    continue

                proxy_ms = proxy_avg * 1000
                hop1_ms = 0
                hop2_ms = proxy_ms

                if bind_hop and bind_hop.get('avg'):
                    hop1_ms = bind_hop['avg'] * 1000
                    hop2_ms = proxy_ms - hop1_ms

                direct_avg = direct.get('avg')
                direct_ms = direct_avg * 1000 if direct_avg else None
                overhead_ms = (proxy_ms - direct_ms) if direct_ms else None

                values = [
                    domain,
                    f"{hop1_ms:.2f}" if hop1_ms > 0 else "N/A",
                    f"{hop2_ms:.2f}" if hop2_ms > 0 else "N/A",
                    f"{proxy_ms:.2f}",
                    f"{direct_ms:.2f}" if direct_ms else "N/A",
                    f"{overhead_ms:.2f}" if overhead_ms else "N/A"
                ]
                col_widths = [max(col_widths[i], len(values[i])) for i in range(len(values))]
        else:
            if is_local or not bind_hop:
                # Columns: Domain, Latency, Path
                headers = ['Domain', 'Latency (ms)', 'Path']
                col_widths = [len(h) for h in headers]
                path_str = "localhost → SOCKS5 → Target"

                for domain in all_domains:
                    proxy = proxy_results.get(domain, {})
                    proxy_avg = proxy.get('avg')

                    if proxy_avg is None:
                        continue

                    proxy_ms = proxy_avg * 1000
                    values = [domain, f"{proxy_ms:.2f}", path_str]
                    col_widths = [max(col_widths[i], len(values[i])) for i in range(len(values))]
            else:
                # Columns: Domain, Hop1, Hop2, Total, Hop1%
                headers = ['Domain', 'Hop 1 (ms)', 'Hop 2 (ms)', 'Total (ms)', 'Hop 1 %']
                col_widths = [len(h) for h in headers]

                for domain in all_domains:
                    proxy = proxy_results.get(domain, {})
                    proxy_avg = proxy.get('avg')

                    if proxy_avg is None:
                        continue

                    proxy_ms = proxy_avg * 1000
                    hop1_ms = bind_hop['avg'] * 1000
                    hop2_ms = proxy_ms - hop1_ms
                    hop1_pct = (hop1_ms / proxy_ms * 100) if proxy_ms > 0 else 0

                    values = [
                        domain,
                        f"{hop1_ms:.2f}",
                        f"{hop2_ms:.2f}",
                        f"{proxy_ms:.2f}",
                        f"{hop1_pct:.1f}%"
                    ]
                    col_widths = [max(col_widths[i], len(values[i])) for i in range(len(values))]

        # Add padding between columns
        padding = 3
        col_widths = [w + padding for w in col_widths]
        col_widths[0] = max(col_widths[0], 30)  # Minimum domain width

        # Calculate total table width
        total_width = sum(col_widths) + len(col_widths) - 1  # -1 because last col doesn't need trailing space

        # Build format strings dynamically
        if profile_mode == 1:
            # Order: Domain, Hop1, Hop2, Proxy, Direct, Overhead
            header_fmt = "  ".join([f"{{:<{col_widths[0]}}}", f"{{:>{col_widths[1] - padding}}}",
                                    f"{{:>{col_widths[2] - padding}}}", f"{{:>{col_widths[3] - padding}}}",
                                    f"{{:>{col_widths[4] - padding}}}", f"{{:>{col_widths[5] - padding}}}"])
            row_fmt = "  ".join([f"{{:<{col_widths[0]}}}", f"{{:>{col_widths[1] - padding}}}",
                                 f"{{:>{col_widths[2] - padding}}}", f"{{:>{col_widths[3] - padding}.2f}}",
                                 f"{{:>{col_widths[4] - padding}}}", f"{{:>{col_widths[5] - padding}}}"])
        elif is_local or not bind_hop:
            header_fmt = "  ".join([f"{{:<{col_widths[0]}}}", f"{{:>{col_widths[1] - padding}}}",
                                    f"{{:<{col_widths[2] - padding}}}"])
            row_fmt = "  ".join([f"{{:<{col_widths[0]}}}", f"{{:>{col_widths[1] - padding}.2f}}",
                                 f"{{:<{col_widths[2] - padding}}}"])
        else:
            # Order: Domain, Hop1, Hop2, Total, Hop1%
            header_fmt = "  ".join([f"{{:<{col_widths[0]}}}", f"{{:>{col_widths[1] - padding}}}",
                                    f"{{:>{col_widths[2] - padding}}}", f"{{:>{col_widths[3] - padding}}}",
                                    f"{{:>{col_widths[4] - padding}}}"])
            row_fmt = "  ".join([f"{{:<{col_widths[0]}}}", f"{{:>{col_widths[1] - padding}.2f}}",
                                 f"{{:>{col_widths[2] - padding}.2f}}", f"{{:>{col_widths[3] - padding}.2f}}",
                                 f"{{:>{col_widths[4] - padding}.1f}}%"])

        # Print table
        self._print("=" * total_width)
        self._print("PROXY PERFORMANCE ANALYSIS")
        self._print("=" * total_width)
        self._print()

        if profile_mode == 1:
            self._print(header_fmt.format(*headers))
        elif is_local or not bind_hop:
            self._print(header_fmt.format(*headers))
        else:
            self._print(header_fmt.format(*headers))

        self._print("-" * total_width)

        totals = {'proxy': [], 'hop1': [], 'hop2': [], 'direct': [], 'overhead': []}

        for domain in all_domains:
            proxy = proxy_results.get(domain, {})
            direct = direct_results.get(domain, {}) if direct_results else {}
            proxy_avg = proxy.get('avg')

            if proxy_avg is None:
                self._print(f"{domain:<{col_widths[0]}}  {'N/A':>{col_widths[1] - padding}}")
                continue

            proxy_ms = proxy_avg * 1000

            if profile_mode == 1:
                hop1_ms = 0
                hop2_ms = proxy_ms

                if bind_hop and bind_hop.get('avg'):
                    hop1_ms = bind_hop['avg'] * 1000
                    hop2_ms = proxy_ms - hop1_ms

                direct_avg = direct.get('avg')
                direct_ms = direct_avg * 1000 if direct_avg else None
                overhead_ms = (proxy_ms - direct_ms) if direct_ms else None

                hop1_str = f"{hop1_ms:.2f}" if hop1_ms > 0 else "N/A"
                hop2_str = f"{hop2_ms:.2f}" if hop2_ms > 0 else "N/A"
                direct_str = f"{direct_ms:.2f}" if direct_ms else "N/A"
                overhead_str = f"{overhead_ms:.2f}" if overhead_ms else "N/A"

                self._print(row_fmt.format(domain, hop1_str, hop2_str, proxy_ms, direct_str, overhead_str))

                totals['proxy'].append(proxy_ms)
                if hop1_ms > 0:
                    totals['hop1'].append(hop1_ms)
                    totals['hop2'].append(hop2_ms)
                if direct_ms:
                    totals['direct'].append(direct_ms)
                if overhead_ms:
                    totals['overhead'].append(overhead_ms)
            else:
                if is_local or not bind_hop:
                    path_str = "localhost → SOCKS5 → Target"
                    self._print(row_fmt.format(domain, proxy_ms, path_str))
                    totals['proxy'].append(proxy_ms)
                else:
                    hop1_ms = bind_hop['avg'] * 1000
                    hop2_ms = proxy_ms - hop1_ms
                    hop1_pct = (hop1_ms / proxy_ms * 100) if proxy_ms > 0 else 0

                    self._print(row_fmt.format(domain, hop1_ms, hop2_ms, proxy_ms, hop1_pct))

                    totals['proxy'].append(proxy_ms)
                    totals['hop1'].append(hop1_ms)
                    totals['hop2'].append(hop2_ms)

        # Summary
        self._print("-" * total_width)
        self._print("SUMMARY")
        self._print("-" * total_width)

        if totals['proxy']:
            avg_proxy = sum(totals['proxy']) / len(totals['proxy'])
            label_width = 30
            self._print(f"{'Average proxy latency:':<{label_width}} {avg_proxy:>10.2f} ms")

            if profile_mode == 1:
                if totals['direct']:
                    avg_direct = sum(totals['direct']) / len(totals['direct'])
                    avg_overhead = avg_proxy - avg_direct
                    impact = (avg_overhead / avg_direct * 100) if avg_direct > 0 else 0
                    self._print(f"{'Average direct latency:':<{label_width}} {avg_direct:>10.2f} ms")
                    self._print(
                        f"{'Average proxy overhead:':<{label_width}} {avg_overhead:>10.2f} ms ({impact:>6.1f}%)")

                if totals['hop1']:
                    avg_hop1 = sum(totals['hop1']) / len(totals['hop1'])
                    avg_hop2 = sum(totals['hop2']) / len(totals['hop2'])
                    hop1_pct = (avg_hop1 / avg_proxy * 100) if avg_proxy > 0 else 0
                    hop2_pct = (avg_hop2 / avg_proxy * 100) if avg_proxy > 0 else 0
                    self._print(f"Hop breakdown:")
                    hop_label = f"  Hop 1 (to {host}):"
                    self._print(f"{hop_label:<{label_width}} {avg_hop1:>10.2f} ms ({hop1_pct:>5.1f}%)")
                    self._print(
                        f"{'  Hop 2 (SOCKS5 + network):':<{label_width}} {avg_hop2:>10.2f} ms ({hop2_pct:>5.1f}%)")
            else:
                if totals['hop1'] and not is_local:
                    avg_hop1 = sum(totals['hop1']) / len(totals['hop1'])
                    avg_hop2 = sum(totals['hop2']) / len(totals['hop2'])
                    hop1_pct = (avg_hop1 / avg_proxy * 100) if avg_proxy > 0 else 0
                    self._print(f"Hop breakdown (to {host}):")
                    self._print(f"{'  Hop 1 (bind address):':<{label_width}} {avg_hop1:>10.2f} ms ({hop1_pct:>5.1f}%)")
                    self._print(f"{'  Hop 2 (SOCKS5 + target):':<{label_width}} {avg_hop2:>10.2f} ms")

        self._print("=" * total_width + "\n")
