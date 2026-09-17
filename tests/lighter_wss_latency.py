#!/usr/bin/env python3
"""
Live latency test for the Lighter market-data websocket path.

White-box test: imports LighterMarketDataWss from argus.perpetuals.lighter.wss
(private internals included on purpose), subscribes to a market directly against
wss://mainnet.zklighter.elliot.ai/stream -- bypassing the dispatcher entirely --
then measures per-channel receive latency as:

    latency_ms = local_wall_clock_ms - exchange_event_time_ms

for both the `order_book` channel (snapshot + ~50ms-batched deltas) and the
`ticker` channel (BBO fast path, cadence undocumented -- this test is what
actually establishes it, per docs/perf/lighter-market-data-parity-plan.md
Section 7/8). Also validates the merged book (sorted both sides, no cross),
exercises the nonce-chain gap-detection/resubscribe path by forcing a
synthetic desync, and forces a mid-run socket close to verify the reconnect
path fires and data resumes (subscriptions replayed) afterward.

Usage:
    uv run tests/lighter_wss_latency.py            # market_id 0, 15s
    uv run tests/lighter_wss_latency.py 2 30       # market_id 2, 30s

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

    os.environ.pop('WIREPROXY_MAPPING_LIGHTER', None)
    os.environ.pop('WIREPROXY_BLIND_BIND', None)
    os.chdir(tempfile.gettempdir())

    from argus.perpetuals.lighter.wss import LighterMarketDataWss
    return LighterMarketDataWss


class LatencyCollector:
    def __init__(self):
        self._lock = threading.Lock()
        self._source = 'unknown'
        self._records = []

    def set_source(self, source):
        self._source = source

    def on_update(self, update):
        market_id = next((k for k in update.keys() if k != 'timestamp'), None)
        if market_id is None:
            return

        exchange_ts = update.get('timestamp')
        if not exchange_ts:
            return

        book = update[market_id]
        bids = book.get('bids') or []
        asks = book.get('asks') or []
        now_ms = time.time() * 1000.0

        record = {
            'source': self._source,
            'market_id': market_id,
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


def run_probe(LighterMarketDataWss, market_id, duration_s):
    collector = LatencyCollector()
    wss = LighterMarketDataWss(order_book_update_callback=collector.on_update)

    store = wss._store
    original_order_book_update = store._handle_order_book_update
    original_ticker = store._handle_ticker

    def order_book_spy(content, is_snapshot):
        collector.set_source('order_book')
        return original_order_book_update(content, is_snapshot)

    def ticker_spy(content):
        collector.set_source('ticker')
        return original_ticker(content)

    store._handle_order_book_update = order_book_spy
    store._handle_ticker = ticker_spy

    print("connecting to wss://mainnet.zklighter.elliot.ai/stream ...")
    wss.run(main_thread=False)
    if not wss.wait_till_socket_open.wait(timeout=15):
        raise RuntimeError('websocket did not open within 15s')

    print(f"subscribing to market_id={market_id} (order_book + ticker) ...")
    started_ms = time.time() * 1000.0
    wss.subscribe_to_market(market_id)

    # Give the snapshot+a few deltas time to land, then force a synthetic nonce
    # gap to exercise the resubscribe path (design doc Section 7, item 1).
    time.sleep(min(3.0, duration_s / 3))
    resync_triggered = threading.Event()
    original_resync = wss._resubscribe_order_book

    def resync_spy(mid):
        resync_triggered.set()
        return original_resync(mid)

    wss._resubscribe_order_book = resync_spy
    store._resync_callback = resync_spy

    book_before = store.order_book_for_market(market_id)
    if book_before is not None:
        with store._dict_lock:
            nonce_state = store._market_id_to_nonce.get(market_id)
        if nonce_state is not None:
            # Feed a delta whose begin_nonce can't possibly chain from the real one.
            fake_nonce = (nonce_state.get('nonce') or 0) + 10_000_000
            store._handle_order_book_delta(market_id, {
                'begin_nonce': fake_nonce,
                'bids': [], 'asks': [],
            }, int(time.time() * 1000))

    remaining = max(0.0, duration_s - (time.time() * 1000.0 - started_ms) / 1000.0)

    # Also force a reconnect and verify the reconnect path fires and data resumes
    # (order_book + ticker subscriptions replayed) on the new socket.
    reconnect_triggered = threading.Event()
    original_reconnect_start = wss._on_reconnect_start

    def reconnect_spy():
        reconnect_triggered.set()
        return original_reconnect_start()

    wss._on_reconnect_start = reconnect_spy

    reconnect_at = max(2.0, duration_s * 2 / 3)
    remaining_to_reconnect = max(0.0, reconnect_at - (time.time() * 1000.0 - started_ms) / 1000.0)
    time.sleep(min(remaining, remaining_to_reconnect))
    forced_close_ms = time.time() * 1000.0
    print("forcing a reconnect (closing the socket) ...")
    try:
        wss._ws.close()
    except Exception:
        pass

    remaining = max(0.0, duration_s - (time.time() * 1000.0 - started_ms) / 1000.0)
    time.sleep(remaining)
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
        resync_triggered.is_set(),
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


def report(market_id, records, duration_s, resync_triggered, reconnect_triggered, resumed_after_close):
    by_source = defaultdict(list)
    for record in records:
        by_source[record['source']].append(record)

    print()
    print(f"Lighter raw feed market_id={market_id} -- {duration_s:.1f}s")
    header = f"{'CHANNEL':<10} {'PUSHES':<8} {'RATE/s':<8} {'MIN':<10} {'P50':<10} {'MEAN':<10} {'P95':<10} {'MAX':<10}"
    print(header)
    print("-" * len(header))

    failures = []
    for source in ('order_book', 'ticker'):
        channel_records = by_source.get(source, [])
        if not channel_records:
            failures.append(f"no '{source}' pushes received")
            print(f"{source:<10} {'0':<8} {'-':<8} {'-':<10} {'-':<10} {'-':<10} {'-':<10} {'-':<10}")
            continue

        latencies = sorted(r['latency_ms'] for r in channel_records)
        rate = len(latencies) / duration_s
        mean = sum(latencies) / len(latencies)
        print(
            f"{source:<10} {len(latencies):<8} {rate:<8.2f} "
            f"{latencies[0]:<10.1f} {_percentile(latencies, 50):<10.1f} {mean:<10.1f} "
            f"{_percentile(latencies, 95):<10.1f} {latencies[-1]:<10.1f}"
        )

        if mean > 5000:
            failures.append(f"{source} mean latency {mean:.0f}ms looks wrong (clock skew or unit mismatch?)")

    for record in records:
        try:
            _validate_book(record)
        except AssertionError as exc:
            failures.append(f"{record['source']} book invariant violated: {exc}")
            break

    if not resync_triggered:
        failures.append("synthetic nonce-gap did not trigger the resubscribe path")
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
    print(f"resubscribe-on-desync path triggered: {resync_triggered}")
    print(f"reconnect path triggered: {reconnect_triggered}")
    print(f"data resumed after forced reconnect: {resumed_after_close}")
    print()
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return failures

    print("PASS: order_book + ticker both streaming, book merged cleanly, latencies sane, resync + reconnect verified")
    return failures


def main():
    parser = argparse.ArgumentParser(description='Live latency test for argus Lighter wss')
    parser.add_argument('market_id', nargs='?', type=int, default=0, help='market_id to subscribe to (default: 0)')
    parser.add_argument('duration', nargs='?', type=float, default=15.0, help='sample duration in seconds (default: 15)')
    args = parser.parse_args()

    LighterMarketDataWss = _load_wss_class()
    records, duration_s, resync_triggered, reconnect_triggered, resumed_after_close = run_probe(
        LighterMarketDataWss, args.market_id, args.duration
    )
    failures = report(
        args.market_id, records, duration_s, resync_triggered, reconnect_triggered, resumed_after_close
    )
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
