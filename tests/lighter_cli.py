#!/usr/bin/env python3
"""
Interactive CLI client for querying the Argus Lighter perpetuals dispatcher.

This mirrors tests/hyper_cli.py but targets LighterDispatcher
(argus/perpetuals/lighter/__init__.py) instead of the HyperLiquid dispatcher.

Usage:
    python tests/lighter_cli.py    # Start interactive mode

Protocol:
    - P1 (control): ~NNNN|<json-payload>
    Every Lighter request MUST include a "correlation_id".

Unlike Hyperliquid, Lighter has no HIP-3-style builder-deployed dexes -- there is a
single unified market list (no dex_name param), and market_info is a single flat
lookup rather than a multi-call annotation/category assembly. See
docs/Hyperliquid_and_Lighter_HYPE_Trading_API_Report.md section 7.
"""
import sys
import time
import uuid
import socket
from typing import Any, Callable, Dict, List, Optional, Tuple
sys.path.insert(0, __file__.replace('/tests/lighter_cli.py', ''))
from argus import protocol


# =============================================================================
# Client
# =============================================================================

class LighterArgusClient:
    """Client for the Argus Lighter perpetuals dispatcher."""

    def __init__(self, host: str = 'localhost', port: int = 9974):
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

    def get_markets(self, offset: int = 0, limit: Optional[int] = None, timeout: int = 30) -> Tuple[List[dict], float]:
        data = {'offset': offset}
        if limit is not None:
            data['limit'] = limit
        resp, dt = self.send_request('get_markets', data, timeout=timeout)
        if resp.get('error'):
            raise Exception(f"get_markets failed: {resp['error']}")
        return list((resp.get('data') or {}).get('perpetuals') or []), dt

    def get_funding_rates_for_all_perpetuals(self, offset: int = 0, limit: Optional[int] = None, timeout: int = 30) -> Tuple[List[dict], float]:
        data = {'offset': offset}
        if limit is not None:
            data['limit'] = limit
        resp, dt = self.send_request('get_funding_rates_for_all_perpetuals', data, timeout=timeout)
        if resp.get('error'):
            raise Exception(f"get_funding_rates_for_all_perpetuals failed: {resp['error']}")
        return list((resp.get('data') or {}).get('funding_rates') or []), dt

    def market_info(self, symbol: Optional[str] = None, market_id: Optional[int] = None, timeout: int = 30) -> Tuple[Optional[dict], float]:
        if symbol is None and market_id is None:
            raise ValueError("market_info requires 'symbol' or 'market_id'")
        data = {'symbol': symbol} if symbol is not None else {'market_id': market_id}
        resp, dt = self.send_request('market_info', data, timeout=timeout)
        if resp.get('error'):
            raise Exception(f"market_info failed: {resp['error']}")
        return (resp.get('data') or {}).get('perpetual'), dt

    def get_funding_history(
        self, market_id: int, start_timestamp: int, end_timestamp: Optional[int] = None,
        resolution: str = '1h', timeout: int = 30
    ) -> Tuple[List[dict], float]:
        data = {'market_id': market_id, 'start_timestamp': start_timestamp, 'resolution': resolution}
        if end_timestamp is not None:
            data['end_timestamp'] = end_timestamp
        resp, dt = self.send_request('get_funding_history', data, timeout=timeout)
        if resp.get('error'):
            raise Exception(f"get_funding_history failed: {resp['error']}")
        return list((resp.get('data') or {}).get('funding_history') or []), dt


# =============================================================================
# Gauntlet (live "test mode" that exercises every known read-only action)
# =============================================================================
#
# Reuses LighterArgusClient's own methods (no separate request-building logic),
# so this stays honest about what the CLI actually calls. Trading actions are
# intentionally excluded -- as of this writing none are wired into the
# dispatcher's routing table yet (see argus/perpetuals/lighter/__init__.py).

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


def _validate_market(market: dict, context: str) -> None:
    _check(isinstance(market, dict), f"{context}: 'market' is not an object")
    for field in ('symbol', 'market_id', 'market_type', 'status'):
        _check(field in market, f"{context}: market missing '{field}'")
    _check(isinstance(market.get('symbol'), str) and market['symbol'], f"{context}: market 'symbol' is empty")


def _validate_context(ctx: dict, context: str) -> None:
    _check(isinstance(ctx, dict), f"{context}: 'context' is not an object")
    for field in ('mark_price', 'index_price', 'open_interest'):
        _check(field in ctx, f"{context}: context missing '{field}'")
        _check_numeric(ctx.get(field), field, context)


def _validate_perp(perp: dict, context: str) -> None:
    _check(isinstance(perp, dict), f"{context}: perpetual is not an object")
    _validate_market(perp.get('market') or {}, context)
    _validate_context(perp.get('context') or {}, context)


def _gauntlet_products_version(client: 'LighterArgusClient', timeout: float) -> Tuple[float, str]:
    data, dt = client.products_version(timeout=timeout)
    _check(isinstance(data, dict), "response is not an object")
    _check('argus' in data, "missing 'argus' version")
    lighter_version = data.get('lighter_dispatcher')
    _check(isinstance(lighter_version, list) and len(lighter_version) == 4, f"'lighter_dispatcher' malformed: {lighter_version!r}")
    return dt, f"argus={data.get('argus')} lighter_dispatcher={lighter_version}"


def _gauntlet_get_markets(client: 'LighterArgusClient', timeout: float) -> Tuple[float, str]:
    perps, dt = client.get_markets(offset=0, limit=10, timeout=timeout)
    _check(isinstance(perps, list), "'perpetuals' is not a list")
    _check(len(perps) > 0, "expected at least one perpetual market")
    for perp in perps:
        _validate_perp(perp, "get_markets")
    return dt, f"{len(perps)} market(s)"


def _gauntlet_get_funding_rates(client: 'LighterArgusClient', timeout: float) -> Tuple[float, str]:
    perps, dt = client.get_funding_rates_for_all_perpetuals(offset=0, timeout=timeout)
    _check(isinstance(perps, list), "'funding_rates' is not a list")
    _check(len(perps) > 0, "expected at least one funding rate entry")
    for perp in perps:
        _validate_perp(perp, "funding rates")
    rates = [float(p['funding_rate']) for p in perps if p.get('funding_rate') is not None]
    _check(rates == sorted(rates, reverse=True), "funding rates are not sorted descending")
    if rates:
        return dt, f"{len(perps)} market(s), top funding={rates[0]:.6f}"
    return dt, f"{len(perps)} market(s), no rates present"


def _gauntlet_market_info(client: 'LighterArgusClient', timeout: float) -> Tuple[float, str]:
    perps, _ = client.get_markets(offset=0, limit=10, timeout=timeout)
    _check(len(perps) > 0, "expected at least one market to test market_info with")
    symbol = perps[0]['market']['symbol']
    info, dt = client.market_info(symbol=symbol, timeout=timeout)
    _check(info is not None, f"market_info('{symbol}') returned null")
    _validate_perp(info, f"market_info('{symbol}')")
    _check(info['market']['symbol'] == symbol, f"market_info returned symbol {info['market']['symbol']!r}, expected {symbol!r}")
    return dt, f"symbol='{symbol}' mark_price={info['context']['mark_price']}"


def _gauntlet_get_funding_history(client: 'LighterArgusClient', timeout: float) -> Tuple[float, str]:
    perps, _ = client.get_markets(offset=0, limit=10, timeout=timeout)
    _check(len(perps) > 0, "expected at least one market to test get_funding_history with")
    market_id = perps[0]['market']['market_id']
    start_ts = int(time.time()) - 24 * 60 * 60
    history, dt = client.get_funding_history(market_id=market_id, start_timestamp=start_ts, timeout=timeout)
    _check(isinstance(history, list), "'funding_history' is not a list")
    for entry in history:
        _check(isinstance(entry, dict), "funding_history entry is not an object")
        for field in ('timestamp', 'value', 'rate', 'direction'):
            _check(field in entry, f"funding_history entry missing '{field}'")
    return dt, f"market_id={market_id} -> {len(history)} entry(ies) in last 24h"


# (display name, check function) -- add new read-only actions here as the
# dispatcher's routing table grows. Trading actions should never be added.
GAUNTLET_CHECKS: List[Tuple[str, Callable[['LighterArgusClient', float], Tuple[float, str]]]] = [
    ("products_version", _gauntlet_products_version),
    ("get_markets", _gauntlet_get_markets),
    ("get_funding_rates_for_all_perpetuals", _gauntlet_get_funding_rates),
    ("market_info", _gauntlet_market_info),
    ("get_funding_history", _gauntlet_get_funding_history),
]


def run_gauntlet(client: 'LighterArgusClient', timeout: float = 15.0) -> bool:
    """Calls every known read-only action against a live dispatcher and validates the shape of each response."""
    print("\n" + "=" * 72)
    print("LIGHTER DISPATCHER GAUNTLET (read-only actions, live endpoint)")
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
    output.append(f"  Argus core:         {data.get('argus')}")
    output.append(f"  Lighter dispatcher: {data.get('lighter_dispatcher')}")
    sidecars = data.get('sidecars') or {}
    if sidecars:
        output.append("  Sidecars:")
        for name, version in sidecars.items():
            output.append(f"    {name}: {version}")
    else:
        output.append("  Sidecars: (none)")
    output.append("=" * 60)
    return "\n".join(output)


def _perp_fields(perp: dict) -> Dict[str, Any]:
    market = perp.get('market') or {}
    ctx = perp.get('context') or {}

    def _f(v, default=0.0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    mark_price = _f(ctx.get('mark_price'))
    open_interest = _f(ctx.get('open_interest'))
    funding_rate = _f(perp.get('funding_rate')) if perp.get('funding_rate') is not None else None
    return {
        'symbol': market.get('symbol', 'Unknown'),
        'market_id': market.get('market_id'),
        'mark_price': mark_price,
        'funding_hourly': funding_rate,
        'funding_apr_pct': (funding_rate * 24 * 365 * 100) if funding_rate is not None else None,
        'open_interest': open_interest,
        'open_interest_usd': open_interest * mark_price,
        'day_volume': _f(ctx.get('daily_quote_token_volume')),
    }


def format_market_info(info: dict) -> str:
    output = []
    f = _perp_fields(info)
    output.append("\n" + "=" * 60)
    output.append(f"MARKET INFO: {f['symbol']}")
    output.append("=" * 60)
    output.append(f"  Market ID:      {f['market_id']}")
    output.append(f"  Mark price:     {f['mark_price']}")
    output.append(f"  Open interest:  {f['open_interest']} ({f['open_interest_usd']:,.0f} USD)")
    if f['funding_hourly'] is not None:
        output.append(f"  Funding/hr:     {f['funding_hourly']*100:.4f}%  (APR {f['funding_apr_pct']:.2f}%)")
    else:
        output.append("  Funding:        (none)")
    output.append("=" * 60)
    return "\n".join(output)


def format_perpetuals(perps: List[dict], limit: Optional[int] = None) -> str:
    output = []
    output.append(f"\n{'SYMBOL':<12} {'MARK PX':<14} {'FUNDING/HR':<12} {'FUNDING APR':<14} {'OI (USD)':<16} {'24H VOL':<16}")
    output.append("=" * 100)
    for perp in (perps[:limit] if limit is not None else perps):
        f = _perp_fields(perp)
        funding_hourly = f"{f['funding_hourly']*100:.4f}%" if f['funding_hourly'] is not None else "n/a"
        funding_apr = f"{f['funding_apr_pct']:.2f}%" if f['funding_apr_pct'] is not None else "n/a"
        output.append(
            f"{f['symbol']:<12} {f['mark_price']:<14.4f} {funding_hourly:<12} "
            f"{funding_apr:<14} {f['open_interest_usd']:<16,.0f} {f['day_volume']:<16,.0f}"
        )
    shown = len(perps) if limit is None else min(limit, len(perps))
    output.append(f"\nShowing {shown} of {len(perps)} market(s)")
    return "\n".join(output)


# =============================================================================
# Interactive CLI
# =============================================================================

def print_banner(host: str, port: int):
    print("\n" + "=" * 50)
    print("  Argus Lighter Interactive CLI")
    print(f"  Connected to {host}:{port}")
    print("  Type 'help' for commands, 'quit' to exit")
    print("=" * 50 + "\n")


def print_help():
    print("\nAvailable commands:")
    print("  version                    - Show dispatcher/component version info")
    print("  markets [offset] [limit]   - List all perpetual markets (single unified venue, no dex param)")
    print("  funding [offset] [limit]   - Show markets by funding rate, highest first (default limit: 20)")
    print("  info <symbol>              - Show market metadata + live data for one symbol")
    print("  history <market_id> [days] - Show funding history for a market over the last N days (default: 1)")
    print("  test | gauntlet            - Call every known read-only action and validate the responses")
    print("  clear                      - Clear screen")
    print("  help                       - Show this help")
    print("  quit                       - Exit the program")
    print("\nExamples:")
    print("  markets                    # all markets")
    print("  markets 0 20               # first 20 markets")
    print("  funding 0 10               # top 10 markets by hourly funding rate")
    print("  funding 10 10              # next 10 markets by hourly funding rate")
    print("  info BTC                   # info for the BTC market")
    print("  history 0 7                # last 7 days of funding history for market_id 0")
    print()


def interactive_loop(client: LighterArgusClient):
    while True:
        try:
            query = input("lighter> ").strip()

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
            elif query.lower().startswith('markets'):
                parts = query.split()[1:]
                offset = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 0
                limit = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
                try:
                    print(f"Fetching markets (offset={offset}, limit={'server default' if limit is None else limit})...")
                    perps, dt = client.get_markets(offset=offset, limit=limit)
                    print(f"✓ Fetched in {dt*1000:.1f}ms")
                    print(format_perpetuals(perps, limit=limit))
                except Exception as e:
                    print(f"✗ Failed to fetch markets: {e}")
            elif query.lower().startswith('funding'):
                parts = query.split()[1:]
                offset = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 0
                limit = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 20
                try:
                    print(f"Fetching markets by funding rate (offset={offset}, limit={limit})...")
                    perps, dt = client.get_funding_rates_for_all_perpetuals(offset=offset, limit=limit)
                    print(f"✓ Fetched in {dt*1000:.1f}ms")
                    print(format_perpetuals(perps, limit=limit))
                except Exception as e:
                    print(f"✗ Failed to fetch funding rates: {e}")
            elif query.lower().startswith('info'):
                parts = query.split()[1:]
                if not parts:
                    print("Usage: info <symbol>")
                else:
                    symbol = parts[0].strip()
                    try:
                        print(f"Fetching info for '{symbol}'...")
                        info, dt = client.market_info(symbol=symbol)
                        print(f"✓ Fetched in {dt*1000:.1f}ms")
                        if info is None:
                            print(f"No market found for symbol '{symbol}'")
                        else:
                            print(format_market_info(info))
                    except Exception as e:
                        print(f"✗ Failed to fetch market info: {e}")
            elif query.lower().startswith('history'):
                parts = query.split()[1:]
                if not parts:
                    print("Usage: history <market_id> [days]")
                else:
                    try:
                        market_id = int(parts[0])
                        days = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
                        start_ts = int(time.time()) - days * 24 * 60 * 60
                        print(f"Fetching funding history for market_id={market_id} (last {days} day(s))...")
                        history, dt = client.get_funding_history(market_id=market_id, start_timestamp=start_ts)
                        print(f"✓ Fetched in {dt*1000:.1f}ms -- {len(history)} entry(ies)")
                        for entry in history[-20:]:
                            print(f"  {entry.get('timestamp')}  rate={entry.get('rate')}  direction={entry.get('direction')}")
                    except Exception as e:
                        print(f"✗ Failed to fetch funding history: {e}")
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

    parser = argparse.ArgumentParser(description='Argus Lighter Interactive CLI Client')
    parser.add_argument('--host', default='localhost', help='Argus server host (default: localhost)')
    parser.add_argument('--port', type=int, default=9974, help='Argus server port (default: 9974)')
    parser.add_argument('--test', action='store_true', help='Run the read-only gauntlet against a live dispatcher and exit (no interactive prompt)')
    parser.add_argument('--test-timeout', type=float, default=15.0, help='Per-check timeout in seconds for --test (default: 15)')

    args = parser.parse_args()

    client = LighterArgusClient(args.host, args.port)

    try:
        print(f"Connecting to Argus Lighter dispatcher at {args.host}:{args.port}...")
        client.connect()

        # Test connection (no ping action on this dispatcher; use products_version instead).
        version, rtt = client.products_version()
        print(f"✓ Connected (lighter_dispatcher: {version.get('lighter_dispatcher')}, {rtt*1000:.1f}ms)")

        if args.test:
            ok = run_gauntlet(client, timeout=args.test_timeout)
            sys.exit(0 if ok else 1)

        print_banner(args.host, args.port)
        interactive_loop(client)

    except ConnectionError as e:
        print(f"Connection error: {e}")
        print("Make sure the Argus Lighter dispatcher is running.")
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
