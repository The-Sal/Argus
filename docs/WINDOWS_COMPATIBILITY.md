# Windows Cross-Platform Compatibility Analysis

## Executive Summary

This document provides an architectural analysis of Argus modules and their Windows compatibility. The analysis evaluates each module for platform-specific dependencies and provides recommendations for cross-platform support.

**Overall Assessment:** Partial Windows compatibility is achievable with moderate effort. Several modules can work on Windows with no changes, while others have fundamental architectural barriers.

---

## Module-by-Module Analysis

### 1. Interactive Brokers (IB) Module ❌ NOT COMPATIBLE

**Location:** `argus/ib/`

**Blocking Issues:**

1. **ShortableSharesData Class** (`_shortable_shares_data.py`)
   - Uses macOS Finder to mount FTP server: `open -a Finder ftp://...`
   - Mounts to `/Volumes/` which is macOS-specific
   - Uses `grep` command-line tool (not available on Windows by default)
   - Path: `/Volumes/ftp2.interactivebrokers.com/usa.txt`
   
   ```python
   # Lines 14-21: macOS-specific FTP mounting
   subprocess.check_call(['open', '-a', 'Finder', self._server_addr])
   ```

2. **Desktop Notifications** (via `_argus_utils.py`)
   - Uses `osascript` for macOS notifications
   - Uses `imessage-cli` for iMessage notifications
   - These fail gracefully on non-macOS (print to console instead)

**Verdict:** Cannot work on Windows without significant refactoring. The ShortableSharesData FTP mounting and grep usage are fundamental blockers. Would need to reimplement using Python's `ftplib` for FTP access and Python string searching instead of `grep`.

---

### 2. Capital.com Module ✅ MOSTLY COMPATIBLE

**Location:** `argus/capital/`

**Compatibility Analysis:**

1. **MKTDispatcher** uses Unix Domain Sockets (UDS) via `UDSServer`
   - Default path: `/tmp/argus_capital.sock`
   - **Issue:** UDS has limited Windows support (Windows 10 1803+ only, with WSL)
   - **Workaround:** Could offer TCP socket fallback

2. **CapitalComAPI** (`_lib.py`)
   - Uses standard `requests` library
   - Uses standard `websocket` library
   - No platform-specific code
   - ✅ Fully cross-platform

3. **Protocol 2 Parser** (`_svr_utils.py`)
   - Pure Python implementation
   - ✅ Fully cross-platform

**Verdict:** The core API client is cross-platform. The dispatcher uses UDS which is problematic on Windows but could be refactored to use TCP sockets (similar to other dispatchers).

**Recommendation:** Add a TCP socket option to `MKTDispatcher` similar to `BinanceMKTDispatcher`.

---

### 3. Binance Module ✅ COMPATIBLE

**Location:** `argus/binance/`

**Compatibility Analysis:**

1. **BinanceWss** and **BinanceMKTDispatcher**
   - Uses standard TCP sockets: `socket.socket(socket.AF_INET, socket.SOCK_STREAM)`
   - Uses `websocket-client` library (cross-platform)
   - Only platform check is for chart display:
   
   ```python
   # Line 70-72: Optional macOS feature
   if platform.platform() != 'Darwin':
       print("Show me charts disabled: not running on macOS")
       self.configs[BinanceWssConfig.SHOW_ME_CHARTS] = False
   ```
   
2. **File I/O**
   - Uses standard Python file operations
   - Auto-dump uses relative paths (cross-platform)

**Verdict:** Fully Windows compatible. The only macOS-specific feature (charts) is already handled gracefully with a fallback.

---

### 4. TradingView Module ✅ COMPATIBLE

**Location:** `argus/tv/`

**Compatibility Analysis:**

1. **QuoteSession** and **ChartSession**
   - Uses `websocket-client` library
   - Pure Python WebSocket protocol handling
   - Uses standard `pandas` for data frames
   - No file system dependencies
   - No platform-specific code

**Verdict:** Fully Windows compatible with no changes required.

---

### 5. NASDAQ Module ✅ COMPATIBLE (with dependency requirements)

**Location:** `argus/nasdaq/`

**Compatibility Analysis:**

1. **NASDAQDataDownloader**
   - Uses Selenium with Firefox WebDriver
   - Uses Python's `tempfile.mkdtemp()` (cross-platform)
   - Uses `pathlib.Path` (cross-platform)
   - No hardcoded paths or platform-specific code

**Dependency Requirements:**
   - Firefox browser must be installed
   - geckodriver must be in PATH

**Verdict:** Windows compatible. Requires Firefox and geckodriver installation on Windows.

---

### 6. Polymarket Module ✅ COMPATIBLE

**Location:** `argus/polymarket/` and `argus/polymarket_direct/`

**Compatibility Analysis:**

1. **PolyDispatcher** (`argus/polymarket/`)
   - Currently a stub implementation
   - No platform-specific code

2. **EnhancedPM** (`argus/polymarket_direct/`)
   - Uses `websocket-client` library
   - Uses `requests` library
   - Uses standard file I/O for message logging
   - No platform-specific code

**Verdict:** Fully Windows compatible.

---

### 7. Cache System ✅ COMPATIBLE

**Location:** `argus/cache_utils/`

**Compatibility Analysis:**

1. **DomainCache** and **CacheInspector**
   - Uses `os.path.expanduser("~")` for home directory (cross-platform)
   - Uses `pickle` for serialization (cross-platform)
   - Cache path: `~/.argus/capital_cache.pkl`
   - Uses standard Python file operations

**Verdict:** Fully Windows compatible. The `~/.argus` directory will be created in the user's home folder on Windows.

---

### 8. Core Utilities ⚠️ PARTIALLY COMPATIBLE

**Location:** `argus/_argus_utils.py`

**Compatibility Analysis:**

1. **Notifications**
   - macOS: Uses `osascript`, `imessage-cli`
   - Other platforms: Falls back to console printing
   - Already handles non-macOS gracefully:
   
   ```python
   # Lines 40-54: Graceful fallback for non-macOS
   else:
       def system_notification(title: str, message: str) -> None:
           print('WARNING: SYSTEM NOTIFICATIONS ARE ONLY SUPPORTED ON macOS SYSTEMS.')
           print('Notification:', title, '-', message)
   ```

2. **throw_fuss function**
   - Uses `os.get_terminal_size()` (cross-platform)
   - Falls back to 80 columns if terminal size unavailable

**Verdict:** Windows compatible with reduced functionality (no desktop notifications).

---

## Compatibility Summary Table

| Module | Windows Compatible | Notes |
|--------|-------------------|-------|
| **IB (ib.core, ib.forecast)** | ❌ No | ShortableSharesData requires macOS Finder/FTP mount |
| **Capital.com** | ⚠️ Partial | UDS not fully supported; TCP refactor needed |
| **Binance** | ✅ Yes | Fully compatible |
| **TradingView** | ✅ Yes | Fully compatible |
| **NASDAQ** | ✅ Yes | Requires Firefox + geckodriver |
| **Polymarket** | ✅ Yes | Fully compatible |
| **Polymarket Direct** | ✅ Yes | Fully compatible |
| **Cache System** | ✅ Yes | Fully compatible |
| **Core Utilities** | ⚠️ Partial | No desktop notifications |

---

## Architectural Recommendations

### For Windows Support

1. **Binance, TradingView, NASDAQ, Polymarket**
   - No changes required
   - These modules are production-ready for Windows

2. **Capital.com**
   - Add TCP socket option alongside UDS
   - Make socket type configurable via parameter/environment variable
   - Example: `MKTDispatcher(transport='tcp', port=9970)`

3. **IB Module** (Major effort)
   - Replace `ShortableSharesData` FTP mounting with `ftplib`
   - Replace `grep` with Python string searching
   - Implement cross-platform FTP data fetching
   - Example refactor:
   ```python
   import ftplib
   
   def _fetch_shortable_shares_data(self):
       ftp = ftplib.FTP('ftp2.interactivebrokers.com')
       ftp.login('shortstock', '')
       data = []
       ftp.retrbinary('RETR usa.txt', data.append)
       return b''.join(data).decode('utf-8')
   ```

4. **Notifications**
   - Consider adding Windows toast notifications via `win10toast` or `plyer`
   - Keep current fallback behavior as default

### Environment Variables for Windows

The following environment variables work on all platforms:
- `ARGUS_CACHES_DISABLED` - Disables caching
- `ARGUS_DISABLE_NOTIFICATIONS` - Disables notifications
- All API key environment variables

---

## Runtime.py Compatibility

The runtime entrypoint (`runtime.py`) already acknowledges Windows limitations in its docstring (quoted verbatim):

```
- Supports: macOS, Linux, (almost anything UNIX-based or UNIX-like) does NOT support Windows.
- IB Dispatchers requires macOS due to ShortableShares() class implementation requires Finder
- Push Notifications requires macOS due to the use of osascript to notify on machine-local notifications
- Capital.com, Polymarket, Binance, TradingView (Chart+Quote), etc... work on all platforms.
```

This existing documentation could be updated to reflect:
1. Capital.com requires UDS (Unix) unless refactored
2. NASDAQ works on Windows with proper dependencies

---

## Conclusion

**Windows Users Can Use:**
- Binance module (full functionality)
- TradingView module (full functionality)  
- NASDAQ module (requires Firefox/geckodriver)
- Polymarket modules (full functionality)
- Cache system (full functionality)

**Windows Users Cannot Use:**
- IB modules (requires macOS for ShortableSharesData)
- Capital.com dispatcher (requires Unix Domain Sockets or refactor)

**Reduced Functionality on Windows:**
- No desktop notifications (falls back to console)
- No iMessage alerts

The modular architecture of Argus means Windows users can selectively use compatible modules without affecting the codebase. The existing platform detection in `_argus_utils.py` provides a good pattern for graceful degradation that should be extended to other modules if Windows support becomes a priority.
