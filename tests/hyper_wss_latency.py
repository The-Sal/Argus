#!/usr/bin/env python3
"""
Live latency test for the Hyperliquid market-data websocket path.

White-box test: imports HyperLiquidMarketDataWss from
argus.perpetuals.hyper.wss (private internals included on purpose),
subscribes to a coin directly against wss://api.hyperliquid.xyz/ws -- bypassing
the dispatcher and the SecureEnv loader -- then measures per-channel receive
latency as:

    latency_ms = local_wall_clock_ms - exchange_event_time_ms

for both the fast l2Book snapshots (5 levels, ~0.5s cadence) and the bbo
stream (top of book, pushed per block when it changes). Also validates the
merged book (same-side ordering, no cross), that the l2Book push rate
actually reflects the `fast: true` subscription, and the reconnect path: the
socket is forcibly closed mid-run and the test asserts the reconnect fires and
data resumes (subscription roster replayed) afterward.

Usage:
    uv run tests/hyper_wss_latency.py            # BTC, 15s
    uv run tests/hyper_wss_latency.py ETH 30     # ETH, 30s

Exit code 0 = every channel arrived and every check passed, 1 otherwise.
"""
import os
import sys
import time
import argparse
import tempfile
import threading
from collections import defaultdict


def _load_wss_class():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    os.environ.pop('WIREPROXY_MAPPING_HYPERLIQUID', None)
    os.environ.pop('WIREPROXY_BLIND_BIND', None)
    os.chdir(tempfile.gettempdir())

    from argus.perpetuals.hyper.wss import HyperLiquidMarketDataWss
    return HyperLiquidMarketDataWss


class LatencyCollector:
    def __init__(self):
        self._lock = threading.Lock()
        self._source = 'unknown'
        self._records = []

    def set_source(self, source):
        self._source = source

    def on_update(self, update):
        coin = next((k for k in update.keys() if k != 'timestamp'), None)
        if coin is None:
            return

        exchange_ts = update.get('timestamp')
        if not exchange_ts:
            return

        book = update[coin]
        bids = book.get('bids') or []
        asks = book.get('asks') or []
        now_ms = time.time() * 1000.0

        record = {
            'source': self._source,
            'coin': coin,
            'latency_ms': now_ms - exchange_ts,
            'recv_ms': now_ms,
            'best_bid': bids[0]['price'] if bids else None,
            'best_ask': asks[0]['price'] if asks else None,
            'book': book,
        }
        with self._lock:
            self._records.append(record)

    def snapshot_records(self):
        with self._lock:
            return list(self._records)


def run_probe(HyperLiquidMarketDataWss, coin, duration_s):
    collector = LatencyCollector()
    wss = HyperLiquidMarketDataWss(order_book_update_callback=collector.on_update)

    store = wss._store
    original_l2_book = store._handle_l2_book
    original_bbo = store._handle_bbo

    def l2_book_spy(data):
        collector.set_source('l2Book')
        return original_l2_book(data)

    def bbo_spy(data):
        collector.set_source('bbo')
        return original_bbo(data)

    store._handle_l2_book = l2_book_spy
    store._handle_bbo = bbo_spy

    print(f"connecting to wss://api.hyperliquid.xyz/ws ...")
    wss.run(main_thread=False)
    if not wss.wait_till_socket_open.wait(timeout=15):
        raise RuntimeError('websocket did not open within 15s')

    print(f"subscribing to {coin} (l2Book fast + bbo) ...")
    started_ms = time.time() * 1000.0
    wss.subscribe_to_coin(coin)

    # Force a reconnect mid-run and verify (a) the reconnect path runs at all and
    # (b) the subscription roster is replayed so data resumes on the new socket.
    reconnect_triggered = threading.Event()
    original_reconnect_start = wss._on_reconnect_start

    def reconnect_spy():
        reconnect_triggered.set()
        return original_reconnect_start()

    wss._on_reconnect_start = reconnect_spy

    reconnect_at = max(2.0, duration_s / 2)
    time.sleep(reconnect_at)
    forced_close_ms = time.time() * 1000.0
    print("forcing a reconnect (closing the socket) ...")
    try:
        wss._ws.close()
    except Exception:
        pass

    time.sleep(max(0.0, duration_s - reconnect_at))
    ended_ms = time.time() * 1000.0

    records = collector.snapshot_records()
    wss._internally_closed = True
    try:
        wss._ws.close()
    except Exception:
        pass

    resumed_after_close = any(r['recv_ms'] > forced_close_ms for r in records)
    return (
        records,
        (ended_ms - started_ms) / 1000.0,
        reconnect_triggered.is_set(),
        resumed_after_close,
    )


def _percentile(sorted_values, percentile):
    index = min(len(sorted_values) - 1, int(round(percentile / 100.0 * (len(sorted_values) - 1))))
    return sorted_values[index]


def _validate_book(record):
    book = record['book']
    bids = [float(level['price']) for level in book.get('bids') or []]
    asks = [float(level['price']) for level in book.get('asks') or []]
    assert bids == sorted(bids, reverse=True), f"bids not descending: {bids}"
    assert asks == sorted(asks), f"asks not ascending: {asks}"
    if bids and asks:
        assert bids[0] < asks[0], f"crossed book bid={bids[0]} ask={asks[0]}"


def report(coin, records, duration_s, reconnect_triggered, resumed_after_close):
    by_source = defaultdict(list)
    for record in records:
        by_source[record['source']].append(record)

    print()
    print(f"Hyperliquid raw feed '{coin}' -- {duration_s:.1f}s")
    header = f"{'CHANNEL':<8} {'PUSHES':<8} {'RATE/s':<8} {'MIN':<10} {'P50':<10} {'MEAN':<10} {'P95':<10} {'MAX':<10}"
    print(header)
    print("-" * len(header))

    failures = []
    for source in ('l2Book', 'bbo'):
        channel_records = by_source.get(source, [])
        if not channel_records:
            failures.append(f"no '{source}' pushes received")
            print(f"{source:<8} {'0':<8} {'-':<8} {'-':<10} {'-':<10} {'-':<10} {'-':<10} {'-':<10}")
            continue

        latencies = sorted(r['latency_ms'] for r in channel_records)
        rate = len(latencies) / duration_s
        mean = sum(latencies) / len(latencies)
        print(
            f"{source:<8} {len(latencies):<8} {rate:<8.2f} "
            f"{latencies[0]:<10.1f} {_percentile(latencies, 50):<10.1f} {mean:<10.1f} "
            f"{_percentile(latencies, 95):<10.1f} {latencies[-1]:<10.1f}"
        )

        if source == 'l2Book' and rate < 1.0:
            failures.append(
                f"l2Book rate {rate:.2f}/s is below 1/s -- `fast: true` does not appear to be applied"
            )
        if mean > 5000:
            failures.append(f"{source} mean latency {mean:.0f}ms looks wrong (clock skew or unit mismatch?)")

    for record in records:
        try:
            _validate_book(record)
        except AssertionError as exc:
            failures.append(f"{record['source']} book invariant violated: {exc}")
            break

    if not reconnect_triggered:
        failures.append("forced socket close did not trigger the reconnect path")
    if not resumed_after_close:
        failures.append("no data arrived after the forced reconnect -- subscription restore failed")

    latest = records[-1] if records else None
    if latest is not None:
        print()
        print(
            f"latest: channel={latest['source']} bid={latest['best_bid']} ask={latest['best_ask']} "
            f"latency={latest['latency_ms']:.1f}ms"
        )

    print()
    print(f"reconnect path triggered: {reconnect_triggered}")
    print(f"data resumed after forced reconnect: {resumed_after_close}")
    print()
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return failures

    print("PASS: l2Book fast + bbo both streaming, book merged cleanly, latencies sane, reconnect restored")
    return failures


def main():
    parser = argparse.ArgumentParser(description='Live latency test for argus Hyperliquid wss')
    parser.add_argument('coin', nargs='?', default='BTC', help='coin to subscribe to (default: BTC)')
    parser.add_argument('duration', nargs='?', type=float, default=15.0, help='sample duration in seconds (default: 15)')
    args = parser.parse_args()

    HyperLiquidMarketDataWss = _load_wss_class()
    records, duration_s, reconnect_triggered, resumed_after_close = run_probe(
        HyperLiquidMarketDataWss, args.coin, args.duration
    )
    failures = report(args.coin, records, duration_s, reconnect_triggered, resumed_after_close)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
