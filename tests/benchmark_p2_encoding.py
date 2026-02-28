#!/usr/bin/env python3
"""
Benchmark script for P2ConvertClass encoding performance.

Measures the latency of encoding market data into P2 protocol format
across 10,000 iterations with randomized junk data.

Usage:
    python benchmark_p2_encoding.py
"""

import os
import sys
import time
import random
import statistics

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tqdm import tqdm
from argus.polymarket._classes import P2ConvertClass


def generate_junk_market_data(order_book_depth: int = 10) -> dict:
    """Generate randomized junk market data for benchmarking."""
    asset_id = f"asset_{random.randint(10000000000000000000000000000000000000000000000000000000000000000000000000000, 99999999999999999999999999999999999999999999999999999999999999999999999999999)}"
    
    # Generate random bids (descending order)
    bids = []
    base_price = random.uniform(0.01, 0.99)
    for i in range(order_book_depth):
        price = base_price - (i * random.uniform(0.001, 0.01))
        size = random.uniform(1, 1000)
        bids.append({'price': f"{price:.4f}", 'size': str(size)})
    
    # Generate random asks (ascending order)
    asks = []
    for i in range(order_book_depth):
        price = base_price + random.uniform(0.01, 0.1) + (i * random.uniform(0.001, 0.01))
        size = random.uniform(1, 1000)
        asks.append({'price': f"{price:.4f}", 'size': str(size)})
    
    return {
        asset_id: {
            'bids': bids,
            'asks': asks
        },
        'timestamp': str(int(time.time() * 1000))
    }


def benchmark_p2_encoding(iterations: int = 10000, order_book_depth: int = 10):
    """Run P2 encoding benchmark and display statistics."""
    
    print("=" * 80)
    print("P2ConvertClass Encoding Benchmark")
    print("=" * 80)
    print(f"Iterations: {iterations:,}")
    print(f"Order book depth: {order_book_depth}")
    print()
    
    # Storage for latencies
    latencies_ms = []
    
    # Progress bar with histogram
    pbar = tqdm(total=iterations, desc="Encoding", unit="iter")
    
    for i in range(iterations):
        # Generate junk data
        market_data = generate_junk_market_data(order_book_depth)
        asset_id = [k for k in market_data.keys() if k != 'timestamp'][0]
        
        # Create P2 converter
        p2_converter = P2ConvertClass(
            ticker=f"test-ticker-{i % 100}",
            market_slug=f"test-market-{i % 100}",
            asset_id=asset_id,
            market_data=market_data,
            order_book_depth=order_book_depth
        )
        
        # Time the encoding
        t0 = time.perf_counter()
        encoded = p2_converter.transferable_2()
        t1 = time.perf_counter()
        
        latency_ms = (t1 - t0) * 1000
        latencies_ms.append(latency_ms)
        
        # Update progress bar with current latency
        pbar.set_postfix({
            'last_ms': f'{latency_ms:.3f}',
            'avg_ms': f'{statistics.mean(latencies_ms):.3f}'
        })
        pbar.update(1)
    
    pbar.close()
    
    # Calculate statistics
    n = len(latencies_ms)
    min_lat = min(latencies_ms)
    max_lat = max(latencies_ms)
    avg_lat = statistics.mean(latencies_ms)
    median_lat = statistics.median(latencies_ms)
    
    if n > 1:
        std_lat = statistics.stdev(latencies_ms)
    else:
        std_lat = 0.0
    
    sorted_lats = sorted(latencies_ms)
    p95_idx = int(n * 0.95)
    p99_idx = int(n * 0.99)
    p95_lat = sorted_lats[min(p95_idx, n-1)]
    p99_lat = sorted_lats[min(p99_idx, n-1)]
    
    # Print statistics
    print()
    print("=" * 80)
    print("📊 LATENCY STATISTICS")
    print("=" * 80)
    print(f"  Total iterations:  {n:,}")
    print(f"  Min latency:       {min_lat:.3f} ms")
    print(f"  Max latency:       {max_lat:.3f} ms")
    print(f"  Average:           {avg_lat:.3f} ms")
    print(f"  Median:            {median_lat:.3f} ms")
    print(f"  Std Dev:           {std_lat:.3f} ms")
    print(f"  P95:               {p95_idx:.3f} ms")
    print(f"  P99:               {p99_idx:.3f} ms")
    
    # Distribution histogram
    print()
    print("  📈 Distribution:")
    bucket_size = max(0.001, (max_lat - min_lat) / 15) if max_lat > min_lat else 0.001
    buckets = {}
    for lat in latencies_ms:
        bucket = int(lat / bucket_size) * bucket_size
        buckets[bucket] = buckets.get(bucket, 0) + 1
    
    sorted_buckets = sorted(buckets.items())
    max_count = max(buckets.values()) if buckets else 1
    
    for bucket, count in sorted_buckets:
        bar_len = int(40 * count / max_count)
        bar = "█" * bar_len
        pct = 100 * count / n
        print(f"    {bucket:>7.3f}-{bucket+bucket_size:<7.3f} ms: {bar:<40} {count:>6,} ({pct:>5.1f}%)")
    
    print("=" * 80)
    
    # Summary
    print()
    print("💡 SUMMARY:")
    print(f"   P2 encoding takes ~{avg_lat:.2f}ms on average")
    print(f"   With {order_book_depth} orderbook levels")
    print(f"   This is {'FAST' if avg_lat < 0.1 else 'MODERATE' if avg_lat < 1.0 else 'SLOW'} for Python")
    print()
    
    return latencies_ms


if __name__ == '__main__':
    # Allow environment variable to control orderbook depth
    depth = int(os.environ.get('POLYMARKET_ORDERBOOK_DEPTH', 10))
    iterations = int(os.environ.get('BENCHMARK_ITERATIONS', 10000))
    
    benchmark_p2_encoding(iterations=iterations, order_book_depth=depth)
