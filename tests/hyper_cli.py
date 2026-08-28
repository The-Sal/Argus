#!/usr/bin/env python3
"""
Interactive CLI client for querying the Argus Hyperliquid perpetuals dispatcher.

This mirrors tests/poly_cli.py but targets HyperLiquidDispatcher
(argus/perpetuals/hyper/__init__.py) instead of the Polymarket dispatcher.

Usage:
    python tests/hyper_cli.py    # Start interactive mode

Protocol:
    - P1 (control): ~NNNN|<json-payload>
    Unlike Polymarket, every Hyperliquid request MUST include a "correlation_id".
"""
import os
import sys
import time
import uuid
import socket
from typing import Any, Callable, Dict, List, Optional, Tuple
sys.path.insert(0, __file__.replace('/tests/hyper_cli.py', ''))
from argus import protocol


# =============================================================================
# Client
# =============================================================================

class HyperArgusClient:
    """Client for the Argus Hyperliquid perpetuals dispatcher."""

    def __init__(self, host: str = 'localhost', port: int = 9972):
        self.host = host
        self.port = port
        self.socket: Optional[socket.socket] = None

    def connect(self) -> None:
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(30)
            self.socket.connect((self.host, self.port))
        except (ConnectionRefusedError, OSError) as e:
            raise ConnectionError(f"Could not connect to dispatcher at {self.host}:{self.port} - {e}")

    def disconnect(self) -> None:
        if self.socket:
            self.socket.close()
            self.socket = None

    def _recv_framed_payload(self) -> bytes:
        """Read one full P1 packet off the socket and return its payload bytes."""
        if not self.socket:
            raise ConnectionError("Not connected to server")

        buf = b''
        # Header is fixed-width: '~' + 4-digit length + '|' = 6 bytes.
        while len(buf) < 6:
            chunk = self.socket.recv(4096)
            if not chunk:
                raise ConnectionError("Server closed connection before responding.")
            buf += chunk

        declared_len = int(buf[1:5].decode('ascii'))
        total_needed = 6 + declared_len
        while len(buf) < total_needed:
            chunk = self.socket.recv(131072)
            if not chunk:
                raise ConnectionError("Server closed connection before responding.")
            buf += chunk

        return protocol.decode_packet(buf[:total_needed])

    def send_request(self, action: str, data: Any = None, timeout: int = 30) -> Tuple[dict, float]:
        """Send a P1 request (with a fresh correlation_id) and return (response, round-trip time)."""
        import json

        if data is None:
            data = {}

        correlation_id = str(uuid.uuid4())
        request = {'action': action, 'data': data, 'correlation_id': correlation_id}
        packet = protocol.encode_packet(json.dumps(request).encode('utf-8'))

        if not self.socket:
            raise ConnectionError("Not connected to server")

        old_timeout = self.socket.gettimeout()
        self.socket.settimeout(timeout)
        try:
            t0 = time.perf_counter()
            self.socket.sendall(packet)
            payload = self._recv_framed_payload()
            elapsed = time.perf_counter() - t0

            response = json.loads(payload.decode('utf-8'))
            response = protocol.decompress_p1_response(response)

            resp_corr_id = response.get('correlation_id')
            if resp_corr_id is not None and resp_corr_id != correlation_id:
                print(f"  ⚠ Warning: response correlation_id {resp_corr_id} does not match request {correlation_id}")

            return response, elapsed
        finally:
            self.socket.settimeout(old_timeout)

    def products_version(self, timeout: int = 30) -> Tuple[dict, float]:
        resp, dt = self.send_request('products_version', timeout=timeout)
        if resp.get('error'):
            raise Exception(f"products_version failed: {resp['error']}")
        return dict(resp.get('data') or {}), dt

    def get_dexs(self, timeout: int = 30) -> Tuple[List[dict], float]:
        resp, dt = self.send_request('get_dexs', timeout=timeout)
        if resp.get('error'):
            raise Exception(f"get_dexs failed: {resp['error']}")
        return list((resp.get('data') or {}).get('dexes') or []), dt

    def get_perpetuals_for_dex(self, dex_name: str = "", offset: int = 0, limit: Optional[int] = None, timeout: int = 30) -> Tuple[List[dict], float]:
        data = {'dex_name': dex_name, 'offset': offset}
        if limit is not None:
            data['limit'] = limit
        resp, dt = self.send_request('get_perpetuals_for_dex', data, timeout=timeout)
        if resp.get('error'):
            raise Exception(f"get_perpetuals_for_dex failed: {resp['error']}")
        return list((resp.get('data') or {}).get('perpetuals') or []), dt

    def get_funding_rates_for_all_perpetuals(self, offset: int = 0, limit: Optional[int] = None, timeout: int = 30) -> Tuple[List[dict], float]:
        data = {'offset': offset}
        if limit is not None:
            data['limit'] = limit
        resp, dt = self.send_request('get_funding_rates_for_all_perpetuals', data, timeout=timeout)
        if resp.get('error'):
            raise Exception(f"get_funding_rates_for_all_perpetuals failed: {resp['error']}")
        return list((resp.get('data') or {}).get('funding_rates') or []), dt


# =============================================================================
# Gauntlet (live "test mode" that exercises every known read-only action)
# =============================================================================
#
# Reuses HyperArgusClient's own methods (no separate request-building logic),
# so this stays honest about what the CLI actually calls. Trading actions are
# intentionally excluded -- as of this writing none are wired into the
# dispatcher's routing table yet (see argus/perpetuals/hyper/__init__.py).

class GauntletFailure(AssertionError):
    """Raised by a gauntlet check to record a readable failure reason."""


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise GauntletFailure(message)


def _check_numeric(value: Any, field: str, context: str) -> None:
    try:
        float(value)
    except (TypeError, ValueError):
        raise GauntletFailure(f"{context}: field '{field}' = {value!r} is not numeric")


def _validate_asset(asset: dict, context: str) -> None:
    _check(isinstance(asset, dict), f"{context}: 'asset' is not an object")
    for field in ('name', 'szDecimals', 'maxLeverage'):
        _check(field in asset, f"{context}: asset missing '{field}'")
    _check(isinstance(asset.get('name'), str) and asset['name'], f"{context}: asset 'name' is empty")


def _validate_context(ctx: dict, context: str) -> None:
    _check(isinstance(ctx, dict), f"{context}: 'context' is not an object")
    for field in ('markPx', 'funding', 'openInterest', 'oraclePx', 'prevDayPx', 'dayNtlVlm'):
        _check(field in ctx, f"{context}: context missing '{field}'")
        _check_numeric(ctx.get(field), field, context)


def _validate_perp(perp: dict, context: str) -> None:
    _check(isinstance(perp, dict), f"{context}: perpetual is not an object")
    _check('dex' in perp, f"{context}: perpetual missing 'dex'")
    _validate_asset(perp.get('asset') or {}, context)
    _validate_context(perp.get('context') or {}, context)


def _gauntlet_products_version(client: 'HyperArgusClient', timeout: float) -> Tuple[float, str]:
    data, dt = client.products_version(timeout=timeout)
    _check(isinstance(data, dict), "response is not an object")
    _check('argus' in data, "missing 'argus' version")
    hl_version = data.get('hyperliquid_dispatcher')
    _check(isinstance(hl_version, list) and len(hl_version) == 4, f"'hyperliquid_dispatcher' malformed: {hl_version!r}")
    return dt, f"argus={data.get('argus')} hyperliquid_dispatcher={hl_version}"


def _gauntlet_get_dexs(client: 'HyperArgusClient', timeout: float) -> Tuple[float, str]:
    dexes, dt = client.get_dexs(timeout=timeout)
    _check(isinstance(dexes, list), "'dexes' is not a list")
    for dex in dexes:
        _check(isinstance(dex, dict), "dex entry is not an object")
        for field in ('name', 'fullName', 'deployer', 'feeRecipient'):
            _check(field in dex, f"dex entry missing '{field}'")
    return dt, f"{len(dexes)} dex(es)"


def _gauntlet_get_perpetuals_default_dex(client: 'HyperArgusClient', timeout: float) -> Tuple[float, str]:
    perps, dt = client.get_perpetuals_for_dex("", offset=0, timeout=timeout)
    _check(isinstance(perps, list), "'perpetuals' is not a list")
    _check(len(perps) > 0, "expected at least one perpetual on the default dex")
    for perp in perps:
        _validate_perp(perp, "default dex")
    return dt, f"{len(perps)} perp(s)"


def _gauntlet_get_perpetuals_hip3_dex(client: 'HyperArgusClient', timeout: float) -> Tuple[float, str]:
    dexes, _ = client.get_dexs(timeout=timeout)
    if not dexes:
        return 0.0, "skipped (no HIP-3 dexes registered)"
    dex_name = dexes[0].get('name', '')
    perps, dt = client.get_perpetuals_for_dex(dex_name, offset=0, timeout=timeout)
    _check(isinstance(perps, list), "'perpetuals' is not a list")
    for perp in perps:
        _validate_perp(perp, f"dex '{dex_name}'")
    return dt, f"dex='{dex_name}' -> {len(perps)} perp(s)"


def _gauntlet_get_funding_rates(client: 'HyperArgusClient', timeout: float) -> Tuple[float, str]:
    perps, dt = client.get_funding_rates_for_all_perpetuals(offset=0, timeout=timeout)
    _check(isinstance(perps, list), "'funding_rates' is not a list")
    _check(len(perps) > 0, "expected at least one funding rate entry")
    for perp in perps:
        _validate_perp(perp, "funding rates")
    fundings = [float(p['context']['funding']) for p in perps]
    _check(fundings == sorted(fundings, reverse=True), "funding rates are not sorted descending")
    return dt, f"{len(perps)} perp(s), top funding={fundings[0]:.6f}"


# (display name, check function) -- add new read-only actions here as the
# dispatcher's routing table grows. Trading actions should never be added.
GAUNTLET_CHECKS: List[Tuple[str, Callable[['HyperArgusClient', float], Tuple[float, str]]]] = [
    ("products_version", _gauntlet_products_version),
    ("get_dexs", _gauntlet_get_dexs),
    ("get_perpetuals_for_dex (default dex)", _gauntlet_get_perpetuals_default_dex),
    ("get_perpetuals_for_dex (HIP-3 dex)", _gauntlet_get_perpetuals_hip3_dex),
    ("get_funding_rates_for_all_perpetuals", _gauntlet_get_funding_rates),
]


def run_gauntlet(client: 'HyperArgusClient', timeout: float = 15.0) -> bool:
    """Calls every known read-only action against a live dispatcher and validates the shape of each response."""
    print("\n" + "=" * 72)
    print("HYPERLIQUID DISPATCHER GAUNTLET (read-only actions, live endpoint)")
    print(f"  per-check timeout: {timeout:.0f}s")
    print("=" * 72)

    results: List[Tuple[str, str, float, str]] = []
    for name, check in GAUNTLET_CHECKS:
        start = time.perf_counter()
        try:
            dt, detail = check(client, timeout)
            status, message = "PASS", detail
        except GauntletFailure as e:
            status, message, dt = "FAIL", str(e), time.perf_counter() - start
        except socket.timeout:
            status, message, dt = "FAIL", f"timed out after {timeout:.0f}s", time.perf_counter() - start
        except Exception as e:
            status, message, dt = "ERROR", f"{type(e).__name__}: {e}", time.perf_counter() - start

        results.append((name, status, dt, message))
        icon = {"PASS": "✓", "FAIL": "✗", "ERROR": "‼"}[status]
        print(f"  {icon} {name:<40} {status:<6} {dt*1000:>8.1f}ms  {message}")

    passed = sum(1 for _, status, _, _ in results if status == "PASS")
    total = len(results)
    print("=" * 72)
    print(f"  {passed}/{total} checks passed")
    print("=" * 72 + "\n")
    return passed == total


# =============================================================================
# Formatting helpers
# =============================================================================

def format_version(data: dict) -> str:
    output = []
    output.append("\n" + "=" * 60)
    output.append("PRODUCTS VERSION")
    output.append("=" * 60)
    output.append(f"  Argus core:              {data.get('argus')}")
    output.append(f"  Hyperliquid dispatcher:  {data.get('hyperliquid_dispatcher')}")
    sidecars = data.get('sidecars') or {}
    if sidecars:
        output.append("  Sidecars:")
        for name, version in sidecars.items():
            output.append(f"    {name}: {version}")
    else:
        output.append("  Sidecars: (none)")
    output.append("=" * 60)
    return "\n".join(output)


def format_dexs(dexes: List[dict]) -> str:
    output = []
    output.append(f"\n{'NAME':<12} {'FULL NAME':<30} {'DEPLOYER':<44} {'ASSETS':<8}")
    output.append("=" * 100)
    for dex in dexes:
        name = str(dex.get('name', ''))
        full_name = str(dex.get('fullName', ''))[:28]
        deployer = str(dex.get('deployer', ''))
        assets = len(dex.get('assetToStreamingOiCap') or [])
        output.append(f"{name:<12} {full_name:<30} {deployer:<44} {assets:<8}")
    output.append(f"\nShowing {len(dexes)} dex(es)")
    output.append("Tip: dex_name \"\" is the default dex and is not listed here.")
    return "\n".join(output)


def _perp_fields(perp: dict) -> Dict[str, Any]:
    asset = perp.get('asset') or {}
    ctx = perp.get('context') or {}

    def _f(v, default=0.0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    mark_px = _f(ctx.get('markPx'))
    funding = _f(ctx.get('funding'))
    open_interest = _f(ctx.get('openInterest'))
    return {
        'dex': perp.get('dex', ''),
        'name': asset.get('name', 'Unknown'),
        'max_leverage': asset.get('maxLeverage', 0),
        'mark_px': mark_px,
        'funding_hourly': funding,
        'funding_apr_pct': funding * 24 * 365 * 100,
        'open_interest': open_interest,
        'open_interest_usd': open_interest * mark_px,
        'day_volume': _f(ctx.get('dayNtlVlm')),
    }


def format_perpetuals(perps: List[dict], limit: Optional[int] = None) -> str:
    output = []
    output.append(f"\n{'DEX':<10} {'NAME':<12} {'MARK PX':<14} {'FUNDING/HR':<12} {'FUNDING APR':<14} {'OI (USD)':<16} {'24H VOL':<16}")
    output.append("=" * 100)
    for perp in (perps[:limit] if limit is not None else perps):
        f = _perp_fields(perp)
        dex = f['dex'] or '(default)'
        output.append(
            f"{dex:<10} {f['name']:<12} {f['mark_px']:<14.4f} {f['funding_hourly']*100:<11.4f}% "
            f"{f['funding_apr_pct']:<13.2f}% {f['open_interest_usd']:<16,.0f} {f['day_volume']:<16,.0f}"
        )
    shown = len(perps) if limit is None else min(limit, len(perps))
    output.append(f"\nShowing {shown} of {len(perps)} perpetual(s)")
    return "\n".join(output)


# =============================================================================
# Interactive CLI
# =============================================================================

def print_banner(host: str, port: int):
    print("\n" + "=" * 50)
    print("  Argus Hyperliquid Interactive CLI")
    print(f"  Connected to {host}:{port}")
    print("  Type 'help' for commands, 'quit' to exit")
    print("=" * 50 + "\n")


def print_help():
    print("\nAvailable commands:")
    print("  version                    - Show dispatcher/component version info")
    print("  dexs                       - List HIP-3 (builder-deployed) perp dexes")
    print("  perps [dex_name] [offset] [limit] - List perpetuals for a dex (default: \"\" main dex, offset 0, limit set by server)")
    print("  funding [N]                - Show top N perpetuals by funding rate (default: 20)")
    print("  test | gauntlet            - Call every known read-only action and validate the responses")
    print("  clear                      - Clear screen")
    print("  help                       - Show this help")
    print("  quit                       - Exit the program")
    print("\nExamples:")
    print("  perps                      # main dex perpetuals")
    print("  perps xyz                  # perpetuals for the 'xyz' HIP-3 dex")
    print("  perps xyz 0 20             # first 20 perpetuals for the 'xyz' dex")
    print("  funding 10                 # top 10 perpetuals by hourly funding rate")
    print()


def interactive_loop(client: HyperArgusClient):
    while True:
        try:
            query = input("hyper> ").strip()

            if not query:
                continue

            if query.lower() in ['quit', 'exit', 'q']:
                print("Goodbye!")
                break
            elif query.lower() in ['help', 'h', '?']:
                print_help()
            elif query.lower() == 'clear':
                import os
                os.system('clear' if os.name == 'posix' else 'cls')
                print_banner(client.host, client.port)
            elif query.lower() == 'version':
                try:
                    data, dt = client.products_version()
                    print(format_version(data))
                    print(f"  ({dt*1000:.1f}ms)")
                except Exception as e:
                    print(f"✗ Failed to fetch version: {e}")
            elif query.lower() == 'dexs':
                try:
                    print("Fetching dexes...")
                    dexes, dt = client.get_dexs()
                    print(f"✓ Fetched in {dt*1000:.1f}ms")
                    print(format_dexs(dexes))
                except Exception as e:
                    print(f"✗ Failed to fetch dexes: {e}")
            elif query.lower().startswith('perps'):
                parts = query.split()[1:]
                dex_name = parts[0].strip() if len(parts) > 0 else ""
                offset = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
                limit = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
                try:
                    print(f"Fetching perpetuals for dex '{dex_name or '(default)'}' (offset={offset}, limit={'server default' if limit is None else limit})...")
                    perps, dt = client.get_perpetuals_for_dex(dex_name, offset=offset, limit=limit)
                    print(f"✓ Fetched in {dt*1000:.1f}ms")
                    print(format_perpetuals(perps, limit=limit))
                except Exception as e:
                    print(f"✗ Failed to fetch perpetuals: {e}")
            elif query.lower().startswith('funding'):
                parts = query.split()
                limit = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 20
                try:
                    print(f"Fetching top {limit} perpetuals by funding rate...")
                    perps, dt = client.get_funding_rates_for_all_perpetuals(offset=0, limit=limit)
                    print(f"✓ Fetched in {dt*1000:.1f}ms")
                    print(format_perpetuals(perps, limit=limit))
                except Exception as e:
                    print(f"✗ Failed to fetch funding rates: {e}")
            elif query.lower() in ('test', 'gauntlet'):
                run_gauntlet(client)
            else:
                print(f"Unknown command: '{query}'. Type 'help' for a list of commands.")

        except KeyboardInterrupt:
            print("\nType 'quit' to exit.")
        except EOFError:
            print("\nGoodbye!")
            break


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Argus Hyperliquid Interactive CLI Client')
    parser.add_argument('--host', default='localhost', help='Argus server host (default: localhost)')
    parser.add_argument('--port', type=int, default=9972, help='Argus server port (default: 9972)')
    parser.add_argument('--test', action='store_true', help='Run the read-only gauntlet against a live dispatcher and exit (no interactive prompt)')
    parser.add_argument('--test-timeout', type=float, default=15.0, help='Per-check timeout in seconds for --test (default: 15)')

    args = parser.parse_args()

    client = HyperArgusClient(args.host, args.port)

    try:
        print(f"Connecting to Argus Hyperliquid dispatcher at {args.host}:{args.port}...")
        client.connect()

        # Test connection (no ping action on this dispatcher; use products_version instead).
        version, rtt = client.products_version()
        print(f"✓ Connected (hyperliquid_dispatcher: {version.get('hyperliquid_dispatcher')}, {rtt*1000:.1f}ms)")

        if args.test:
            ok = run_gauntlet(client, timeout=args.test_timeout)
            sys.exit(0 if ok else 1)

        print_banner(args.host, args.port)
        interactive_loop(client)

    except ConnectionError as e:
        print(f"Connection error: {e}")
        print("Make sure the Argus Hyperliquid dispatcher is running.")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nOperation cancelled.")
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        client.disconnect()


if __name__ == '__main__':
    main()
