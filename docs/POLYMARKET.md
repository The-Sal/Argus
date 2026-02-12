# Polymarket Module

> **Status Update (2025+):** This documentation has been rewritten to reflect the current architecture. The `polymarket/` directory now contains a fully functional `PolymarketDispatcher` that follows the standard Argus dispatcher pattern, consistent with IB, Capital.com, and Binance modules.

The Polymarket module provides access to prediction market data from Polymarket via a dispatcher-based TCP server architecture.

## Quick Start

**Recommended:** Use `runtime.py` to start the dispatcher:

```bash
# Start with defaults (localhost:9972)
python runtime.py polymarket

# Or specify custom host/port
python runtime.py polymarket --host 0.0.0.0 --port 9972
```

Then connect to it via TCP using Protocol 1 (JSON) for control and Protocol 2 for market data. See **[POLYMARKET_DISPATCHER.md](./POLYMARKET_DISPATCHER.md)** for the complete API specification.

---

## Architecture Overview

```
[Client Code] → [TCP:9972] → [PolymarketDispatcher] → [Polymarket REST API / WebSocket]
```

The dispatcher follows the standard Argus pattern:
- **P1 Protocol**: JSON-encoded control messages (subscribe, place_order, etc.)
- **P2 Protocol**: Binary market data streaming (order book updates)
- **Single socket**: All communication through one TCP connection on port 9972
- **Full market cache**: ~6000 markets refreshed automatically every 5 minutes

---

## Deprecation Notice: EnhancedPM

⚠️ **`argus.polymarket_direct.EnhancedPM` is DEPRECATED and will be removed in a future version.**

The `EnhancedPM` class in `polymarket_direct` was an interim solution built while the proper dispatcher was being developed. It is now considered legacy code kept only for backward compatibility.

**Do not use `EnhancedPM` for new code.** It will be dropped eventually without further notice.

### Migration Guide: EnhancedPM → Dispatcher

| EnhancedPM (Old) | Dispatcher (New) |
|-----------------|------------------|
| Direct Python API calls | `python runtime.py polymarket` |
| `client.fetch_events()` | Send via TCP: `{"action": "fetch_all_markets"}` |
| `client.subscribe_to_market_data(ids, callback)` | Send via TCP: `{"action": "subscribe", "data": ["id1", "id2"]}` |
| Callback-based market data | P2 binary stream on same socket |

See **[POLYMARKET_DISPATCHER.md](./POLYMARKET_DISPATCHER.md)** for complete protocol documentation.

---

## Internal Module: polymarket_direct

**⚠️ WARNING:** The `polymarket_direct` module is **INTERNAL** and **UNSTABLE**. 

- It was created specifically to support the `PolymarketDispatcher` implementation
- It is subject to breaking changes without notice
- **Do not import or use directly in your code**

All public functionality is exposed through the `PolymarketDispatcher` class in `argus.polymarket`.

---

## Dispatcher Features

- **Market Data Subscriptions**: Subscribe to order book updates via asset IDs (CLOB token IDs)
- **Order Management**: Place, cancel, and query orders
- **Account Updates**: Real-time order status notifications (requires active market data subscription)
- **Market Discovery**: Search and fetch market metadata for ~6000 active markets
- **Protocol 2 Streaming**: Binary-encoded market data for efficient transmission
- **Auto-reconnect**: WebSocket connections automatically reestablish on disconnection

---

## Configuration

### Environment Variables

```bash
# Required for trading (order placement)
POLYMARKET_PRIVATE_KEY=your_private_key
POLYMARKET_PROXY_FUNDER=your_proxy_address

# Optional tuning
POLYMARKET_ORDERBOOK_DEPTH=10          # Default order book depth for P2 packets
POLYMARKET_MAX_SOCKET_RETRIES=100      # WebSocket reconnection attempts
POLYMARKET_FULL_MARKET_CACHE_REFRESH_INTERVAL=300  # Market cache refresh (seconds)
```

### Cache

**Location:** `~/.argus/polymarket_cache.pkl`

**Cached Data:**
- All ~6000 market metadata entries
- Automatically refreshed every 5 minutes
- Persisted to disk for fast startup

---

## Important Notes

### Account Update Delivery

Real-time account events (order placement, cancellation, fills) are **only** broadcast to client sockets that have an active market data subscription. You **must** subscribe to at least one asset before placing orders if your workflow depends on receiving `account_update` pushes.

### Historical Legacy

The original `polymarket` module (pre-2025) was a stub pointing to a legacy branch. This has been completely replaced with the current dispatcher implementation. If you encounter old references to:
- "No dispatcher for Polymarket"
- "Use polymarket_direct instead"
- "Stub implementation"

These are **outdated**. The dispatcher is now fully implemented and production-ready.

---

## File Reference

```
argus/polymarket/
├── __init__.py           # PolymarketDispatcher class (USE THIS)
└── _classes.py           # Supporting classes

argus/polymarket_direct/  # INTERNAL - DO NOT USE DIRECTLY
├── __init__.py           # EnhancedPM (DEPRECATED)
├── rest.py               # REST API client (internal)
├── wss.py                # WebSocket clients (internal)
├── _types.py             # Data models
└── ...                   # Other internal modules

docs/
├── POLYMARKET.md         # This file (overview + deprecation notice)
└── POLYMARKET_DISPATCHER.md  # Complete dispatcher API specification
```

---

## Summary

- ✅ **Use:** `python runtime.py polymarket` (proper dispatcher architecture)
- ⚠️ **Avoid:** `argus.polymarket_direct.EnhancedPM` (deprecated, will be removed)
- ❌ **Do not use:** `polymarket_direct` module directly (internal/unstable)
- 📖 **Read:** [POLYMARKET_DISPATCHER.md](./POLYMARKET_DISPATCHER.md) for complete API documentation

The Polymarket module is now consistent with other Argus dispatchers (IB, Capital, Binance) and provides a unified, stable interface for prediction market data and order execution.
