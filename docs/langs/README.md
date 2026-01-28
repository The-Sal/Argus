# Language-Specific Documentation

This directory contains documentation for language-specific implementations and optimization techniques beyond Python.

---

## 📁 Structure

### `/cpp/` - C++ Conversion Documentation

Comprehensive guides for converting Argus (or specific modules) from Python to C++.

**Quick Links:**
- **[EXECUTIVE_SUMMARY.md](cpp/EXECUTIVE_SUMMARY.md)** - 10-minute decision guide
- **[CONVERSION_INDEX.md](cpp/CONVERSION_INDEX.md)** - Navigation hub for all C++ docs
- **[CONVERSION_ANALYSIS.md](cpp/CONVERSION_ANALYSIS.md)** - Full 60+ page analysis
- **[POLYMARKET_QUICKSTART.md](cpp/POLYMARKET_QUICKSTART.md)** - Week-by-week implementation guide

**Key Findings:**
- Polymarket dispatcher: 2-4 weeks
- Full Argus-CPP: 6-12 person-months
- Expected performance: 2-5x faster WebSocket latency

---

### `ASSEMBLY_OPTIMIZATION.md` - Hand-Tuned Assembly Analysis

Deep dive into using hand-tuned assembly for performance-critical hot paths.

**Topics Covered:**
- Protocol 2 encoder/decoder optimization (15-25% improvement)
- JSON parsing with known schemas (20-30% improvement)
- SIMD intrinsics vs hand-tuned assembly trade-offs
- Maintenance considerations and when to use assembly

**Key Recommendations:**
- ✅ Use Ryu algorithm for P2 encoder (3-4x speedup, pure C++)
- ✅ Use intrinsics for P2 decoder (2x speedup, maintainable)
- ✅ Use simdjson for JSON (5x speedup, battle-tested)
- ❌ Skip hand-tuned assembly (marginal benefit, high maintenance)

---

## 🎯 Quick Navigation

**Want to convert to C++?**
1. Start with [cpp/EXECUTIVE_SUMMARY.md](cpp/EXECUTIVE_SUMMARY.md)
2. Check decision framework (10 minutes)
3. Follow recommended path

**Want maximum performance?**
1. Read [ASSEMBLY_OPTIMIZATION.md](ASSEMBLY_OPTIMIZATION.md)
2. Review Protocol 2 optimization recommendations
3. Consider intrinsics before assembly

**Just browsing?**
- [cpp/CONVERSION_INDEX.md](cpp/CONVERSION_INDEX.md) - Overview of C++ conversion
- [ASSEMBLY_OPTIMIZATION.md](ASSEMBLY_OPTIMIZATION.md) - Low-level optimization techniques

---

## 🔮 Future Language Additions

This structure allows for additional language-specific documentation:

```
docs/langs/
├── cpp/              ✅ Complete
├── swift/            🔮 Future (argus-swift branch in progress)
├── rust/             🔮 Future consideration
├── go/               🔮 Future consideration
└── ASSEMBLY_OPTIMIZATION.md  ✅ Complete
```

---

## 📚 Related Documentation

**Module-specific docs (in `docs/` root):**
- [BINANCE.md](../BINANCE.md) - Binance dispatcher
- [CAPITAL.md](../CAPITAL.md) - Capital.com dispatcher
- [IB.md](../IB.md) - Interactive Brokers
- [POLYMARKET.md](../POLYMARKET.md) - Polymarket integration
- [TV.md](../TV.md) - TradingView
- [NASDAQ.md](../NASDAQ.md) - NASDAQ data downloader
- [WIREPROXY.md](../WIREPROXY.md) - Wireguard proxy
- [CACHE.md](../CACHE.md) - Caching system

---

**Last Updated:** January 28, 2026
