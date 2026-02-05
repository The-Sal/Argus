# PolyMarketOrderBookWss Performance Documentation

## Overview

The `PolyMarketOrderBookWss` class provides real-time Level 2 order book updates from Polymarket via WebSocket. This document outlines the message processing capabilities based on comprehensive performance testing with real production message samples.

## Processing Performance

Under benchmark conditions with 2.29 million real Polymarket messages, the handler demonstrates consistent sub-millisecond processing with an average overhead of **~19.7 microseconds per message**. The median processing time sits at 28.5 microseconds, indicating that the handler spends the majority of its time in a tight, predictable performance envelope.

The handler achieves sustained throughput of **47,000–48,000 messages per second** on a single thread. This translates to processing capacity of approximately 4.1 billion messages per day, which provides substantial headroom even under high-frequency market conditions.

### Message Processing Overhead

Each message incurs the following computational costs:
- JSON deserialization (parsing the WebSocket message)
- Event type discrimination and routing
- Order book dictionary lookups and updates
- Price level sorting for bids/asks
- Callback invocation (if configured)

The overhead remains largely constant regardless of message size, with message length showing only a weak correlation (r=0.047-0.063) to processing time. This indicates that fixed costs—primarily function call overhead and dictionary operations—dominate the processing budget rather than the data payload itself.

### Real-World Context

In typical production scenarios:
- A single asset generates approximately **200 messages/second** during active trading
- A standard two-outcome market (YES/NO) produces roughly **400 messages/second** combined

At the measured throughput of 48K msg/s, the handler can theoretically track **120 simultaneous single-asset streams** or **240 concurrent assets across multiple markets** before approaching saturation. In practice, market activity is bursty rather than sustained, providing additional safety margin.

### Tail Latency Characteristics

While average performance is excellent, occasional outliers exist:
- **99th percentile**: Processing completes within ~50 microseconds
- **Maximum observed**: 9.3ms (standard JSON) / 25.4ms (orjson variant)

These maximum latencies—representing 471x to 1,293x the average case—likely stem from garbage collection pauses or system-level interference rather than inherent handler limitations. For applications requiring strict latency guarantees, these tail events should be considered when designing backpressure and buffering strategies.

## Performance Testing Details

Comprehensive benchmark data and testing utilities are maintained in the `perf/rest-wss-orderbook-tuning` branch. This branch contains:

- **Test Dataset**: 2,229 production messages captured from live Polymarket WebSocket streams, multiplied 1000x and randomized for statistical validity
- **Profiling Scripts**: Message replay harness with microsecond timing instrumentation
- **Dependencies**: Testing requires `orjson`,`seaborn` and `matplotlib`—these are *not* included in the main project dependencies

The testing infrastructure is intentionally isolated from the main codebase to avoid dependency bloat. Engineers interested in performance validation or optimization work should checkout the tuning branch and install the additional test dependencies separately.

**Note**: The test dataset and profiling code will not be present in feature branches or main. Reference the `perf/rest-wss-orderbook-tuning` branch for reproducible benchmarks.
