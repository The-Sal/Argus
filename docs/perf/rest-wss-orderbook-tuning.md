# PolyMarketOrderBookWss Performance Profile

## Overview

The `PolyMarketOrderBookWss` class provides a Level 2 order book WebSocket handler for Polymarket market data. This document outlines the measured performance characteristics based on production-scale load testing.

## Performance Metrics

**Tested Configuration:**
- Dataset: 2,229,000 real Polymarket WebSocket messages (replayed)
- Test Branch: `perf/rest-wss-orderbook-tuning`
- Baseline: Standard JSON library vs orjson comparison

### Processing Throughput

The handler demonstrates consistent performance across varying message structures:

- **Standard JSON**: ~47K messages/second (46,958 msg/s measured)
- **orjson optimized**: ~48K messages/second (48,140 msg/s measured)

The minimal improvement with orjson (2.5% gain) indicates that JSON parsing is not the primary bottleneck. Most overhead comes from Python function calls, dictionary operations, and order book manipulation logic.

### Latency Characteristics

**Per-Message Processing Time:**
- Average: 19.7 microseconds
- Median: 28.5 microseconds  
- Minimum: 2.3 microseconds
- Maximum: 9.3-25.4 milliseconds (outliers, likely GC pauses)

The weak correlation between message size and processing time (r=0.047-0.063) suggests that the handler's performance is dominated by fixed overhead rather than payload complexity. This is expected given that order book updates follow predictable structures.

### Real-World Capacity

At the measured throughput of **~48,000 messages/second**, this handler can sustain:

- 172 million messages per hour
- 4.1 billion messages per day
- Multiple simultaneous market subscriptions with headroom

For typical Polymarket usage patterns (hundreds to low thousands of markets), this provides substantial capacity even during high-volatility periods when update frequencies spike.

## Architectural Notes

The handler maintains in-memory order books keyed by asset ID, with sorted bid/ask arrays that are rebuilt on each price update. While this approach trades computational overhead for simplicity, the measured performance indicates it's sufficient for the current scale of operations.

**Key bottlenecks identified:**
- Dictionary lookups and nested access patterns
- Order book sorting on every update
- Python interpreter overhead (function calls, object creation)

Future optimization efforts should focus on these areas rather than JSON parsing, which is already near-optimal.

## Testing & Benchmarking

**Important:** The performance testing harness and dataset used for this analysis exist **only** in the `perf/rest-wss-orderbook-tuning` branch. This branch contains:

- Testing script: `argus/__build_tools/perf/__polymarket_wss_orderbook.py`
- Real message dataset: `_polymarket_socket_debug.log` (2,229 unique messages × 1000 replications)
- Additional testing dependencies are not included in the main project:
  - `orjson` (for JSON parsing comparison)
  - `seaborn`, `matplotlib` (for visualization)

These dependencies are intentionally excluded from the main branches as they are strictly for performance analysis. If you need to reproduce these benchmarks, checkout the performance tuning branch and install the additional requirements manually.

**Branch Context:**  
The `perf/rest-wss-orderbook-tuning` branch is a dedicated performance analysis branch that will not be merged directly. Optimizations discovered here may be selectively backported to feature branches (such as `feature/polymarket-dispatcher`) as appropriate.


