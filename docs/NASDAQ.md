# NASDAQ Module

The NASDAQ module provides automated historical stock data downloads from NASDAQ.com using Selenium web automation.

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Usage](#usage)
- [Configuration](#configuration)
- [Examples](#examples)
- [Limitations](#limitations)

## Overview

The NASDAQ module is **not a dispatcher or real-time data source**. It's a **utility for downloading historical CSV files**.

**Location:** `/argus/nasdaq/__init__.py`

**Primary Component:**
- `NASDAQDataDownloader` - Selenium-based web scraper

**Key Features:**
- 10-year historical data downloads
- Batch downloading with progress tracking
- Headless browser support
- Automatic cookie handling
- Context manager for cleanup
- CSV file output

**Use Cases:**
- Backtesting historical strategies
- Data collection for research
- Building training datasets
- Archiving historical prices

## Architecture

### Why Selenium?

NASDAQ.com uses JavaScript to dynamically load historical data. Traditional HTTP scraping (requests/BeautifulSoup) doesn't work because:
1. Page requires JavaScript execution
2. Data loads via AJAX
3. Download button triggers client-side logic

**Solution:** Selenium automates a real browser (Firefox) to:
- Load JavaScript-heavy pages
- Click UI elements (Historical Data tab, Download button)
- Handle cookies and dynamic content
- Download CSV files

### Data Source

**URL Pattern:**
```
https://www.nasdaq.com/market-activity/stocks/{ticker}/historical?page=1&rows_per_page=10&timeline=y10
```

**Timeline:** `y10` = 10 years of historical data

**Data Format:** CSV file with columns:
- Date
- Close/Last
- Volume
- Open
- High
- Low

### Workflow

```
[Initialize NASDAQDataDownloader]
    ↓
[Setup Firefox WebDriver with download preferences]
    ↓
[Create temporary directory for CSVs]
    ↓
For each ticker:
    ↓
[Navigate to NASDAQ.com historical page]
    ↓
[Accept cookies (first time only)]
    ↓
[Click "Historical Data" tab]
    ↓
[Click "Download" button]
    ↓
[Wait for CSV download]
    ↓
[Rename file to {ticker}.csv]
    ↓
[Move to next ticker]
    ↓
[Return results summary]
    ↓
[Cleanup: Close browser, keep temp files]
```

## Usage

### Basic Usage (Context Manager)

**Recommended** - Automatic cleanup:

```python
from argus.nasdaq import NASDAQDataDownloader

with NASDAQDataDownloader(headless=True) as downloader:
    result = downloader.download_tickers(["AAPL", "MSFT", "GOOGL"])

    print(f"Succeeded: {result['succeeded']}")
    print(f"Failed: {result['failed']}")
    print(f"Files: {result['temp_dir']}")
```

### Manual Management

Explicit cleanup control:

```python
from argus.nasdaq import NASDAQDataDownloader

downloader = NASDAQDataDownloader(headless=True)
try:
    result = downloader.download_tickers(["TSLA"])
finally:
    downloader.destroy()  # Close browser
```

### Class Initialization

```python
class NASDAQDataDownloader:
    def __init__(self, headless=True):
        """
        Args:
            headless (bool): Run browser without GUI (default: True)
        """
```

**Headless Mode:**
- `headless=True` (default) - Browser runs in background (faster)
- `headless=False` - Browser window visible (useful for debugging)

### Download Methods

#### download_tickers(tickers)

Download historical data for one or more tickers.

**Parameters:**
- `tickers` (str or list): Single ticker or list of tickers

**Returns:**
```python
{
    'succeeded': ['AAPL', 'MSFT'],  # Successfully downloaded
    'failed': ['INVALID'],           # Failed downloads
    'files': [Path(...), Path(...)], # Paths to CSV files
    'temp_dir': Path(...)            # Temporary directory
}
```

**Example:**
```python
result = downloader.download_tickers(["AAPL", "TSLA", "NVDA"])

for file_path in result['files']:
    print(f"Downloaded: {file_path}")
    # Process CSV file
```

### Cleanup

#### destroy()

Close browser and cleanup resources.

```python
downloader.destroy()
```

**What it does:**
- Quits Firefox WebDriver
- Does **NOT** delete temporary directory (files preserved)

**Manual cleanup** (optional):
```python
import shutil
shutil.rmtree(result['temp_dir'])
```

## Configuration

### Firefox Preferences

The downloader configures Firefox to:
- Save files to temporary directory without prompting
- Auto-save CSV files
- Download to `tempfile.mkdtemp(prefix='argus.nasdaq.')`

```python
firefox_options.set_preference('browser.download.folderList', 2)
firefox_options.set_preference('browser.download.dir', str(temp_dir))
firefox_options.set_preference('browser.download.useDownloadDir', True)
firefox_options.set_preference('browser.helperApps.neverAsk.saveToDisk', 'text/csv,application/csv')
```

### Temporary Directory

**Location:** System temp directory (e.g., `/tmp/argus.nasdaq.abc123/`)

**Naming:** `argus.nasdaq.<random>`

**Persistence:** Files are **kept** after `destroy()` for manual processing.

### Timing and Retries

**Page Load Wait:** 10 seconds (WebDriverWait)

**Download Wait:** 2 seconds (hardcoded sleep)

**Click Retries:** Up to 10 attempts for intercepted clicks

```python
for attempt in range(10):
    try:
        historical_tab.click()
        break
    except ElementClickInterceptedException:
        time.sleep(1)
```

## Examples

### Example 1: Single Ticker

```python
from argus.nasdaq import NASDAQDataDownloader

with NASDAQDataDownloader() as downloader:
    result = downloader.download_tickers("AAPL")

    if result['succeeded']:
        csv_file = result['files'][0]
        print(f"Apple data saved to: {csv_file}")

        # Read CSV
        import pandas as pd
        df = pd.read_csv(csv_file)
        print(df.head())
```

### Example 2: Batch Download with Progress

```python
from argus.nasdaq import NASDAQDataDownloader

tickers = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA",
    "NVDA", "META", "NFLX", "AMD", "INTC"
]

with NASDAQDataDownloader(headless=True) as downloader:
    result = downloader.download_tickers(tickers)

    print(f"\nDownload Summary:")
    print(f"  Success: {len(result['succeeded'])} tickers")
    print(f"  Failed: {len(result['failed'])} tickers")

    if result['failed']:
        print(f"  Failed tickers: {result['failed']}")

    print(f"\nFiles saved to: {result['temp_dir']}")
```

**Output:**
```
10 ticker data to download
Downloading: 100%|██████████| 10/10 [01:23<00:00,  8.35s/it]
Successfully downloaded 10 tickers
Failed to download 0 tickers

Download Summary:
  Success: 10 tickers
  Failed: 0 tickers

Files saved to: /tmp/argus.nasdaq.abc123/
```

### Example 3: Process Downloaded Files

```python
from argus.nasdaq import NASDAQDataDownloader
import pandas as pd

with NASDAQDataDownloader() as downloader:
    result = downloader.download_tickers(["AAPL", "MSFT", "GOOGL"])

    # Combine all CSVs into one DataFrame
    all_data = []
    for file_path in result['files']:
        df = pd.read_csv(file_path)
        ticker = file_path.stem  # Filename without extension
        df['ticker'] = ticker
        all_data.append(df)

    combined = pd.concat(all_data, ignore_index=True)
    combined.to_csv('tech_stocks_10year.csv', index=False)
    print(f"Combined {len(combined)} rows into tech_stocks_10year.csv")
```

### Example 4: Retry Failed Downloads

```python
from argus.nasdaq import NASDAQDataDownloader

all_tickers = ["AAPL", "INVALID_TICKER", "TSLA", "FAKE"]

with NASDAQDataDownloader(headless=True) as downloader:
    result = downloader.download_tickers(all_tickers)

    # Retry failed ones
    if result['failed']:
        print(f"\nRetrying {len(result['failed'])} failed tickers...")
        retry_result = downloader.download_tickers(result['failed'])

        # Update results
        result['succeeded'].extend(retry_result['succeeded'])
        result['failed'] = retry_result['failed']
        result['files'].extend(retry_result['files'])

    print(f"\nFinal Results:")
    print(f"  Succeeded: {result['succeeded']}")
    print(f"  Failed: {result['failed']}")
```

### Example 5: Visible Browser (Debugging)

```python
from argus.nasdaq import NASDAQDataDownloader

# See browser in action
with NASDAQDataDownloader(headless=False) as downloader:
    result = downloader.download_tickers(["AAPL"])
    input("Press Enter to close browser...")
```

### Example 6: Large Batch Download

```python
from argus.nasdaq import NASDAQDataDownloader

# Download all S&P 100 tickers (example subset)
sp100 = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META", "JPM",
    "JNJ", "V", "PG", "XOM", "UNH", "HD", "CVX", "MA", "BAC", "ABBV",
    # ... add more tickers ...
]

with NASDAQDataDownloader(headless=True) as downloader:
    result = downloader.download_tickers(sp100)

    print(f"\nS&P 100 Download Complete:")
    print(f"  {len(result['succeeded'])}/{len(sp100)} successful")

    # Copy to permanent location
    import shutil
    dest = Path.home() / 'nasdaq_data' / 'sp100'
    dest.mkdir(parents=True, exist_ok=True)

    for file_path in result['files']:
        shutil.copy(file_path, dest / file_path.name)

    print(f"  Copied to: {dest}")
```

## Error Handling

### Common Failures

1. **Invalid Ticker:**
   - Ticker doesn't exist on NASDAQ
   - Typo in ticker symbol
   - **Result:** Added to `failed` list

2. **Network Issues:**
   - Slow connection / timeout
   - NASDAQ.com down
   - **Result:** TimeoutException, added to `failed`

3. **Element Not Found:**
   - NASDAQ.com changed UI
   - Adblocker interfering
   - **Result:** Exception, download fails

4. **Download Not Completed:**
   - Browser closed too early
   - Filesystem permissions
   - **Result:** File not found, added to `failed`

### Debugging

**Enable visible browser:**
```python
downloader = NASDAQDataDownloader(headless=False)
```

**Check temporary directory:**
```python
result = downloader.download_tickers(["AAPL"])
print(f"Check temp dir: {result['temp_dir']}")
# Inspect files manually
```

**Verbose logging:**
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## Limitations

### 1. Requires Firefox

**Constraint:** Only supports Firefox WebDriver.

**Impact:**
- Must have Firefox installed
- Must have geckodriver in PATH

**Installation:**
```bash
# macOS
brew install firefox geckodriver

# Ubuntu
sudo apt-get install firefox geckodriver

# Windows
# Download geckodriver.exe and add to PATH
```

### 2. Slow Performance

**Constraint:** Browser automation is slower than API calls.

**Typical Speed:**
- ~8-10 seconds per ticker
- 100 tickers ≈ 15 minutes

**Mitigation:**
- Use `headless=True` (default)
- Run during off-hours
- Batch downloads overnight

### 3. Web Scraping Fragility

**Constraint:** Breaks if NASDAQ.com changes UI.

**Selectors that may break:**
```python
# CSS selectors hardcoded
'button.jupiter22-tab:nth-child(6)'  # Historical Data tab
'.historical-download'               # Download button
'//*[@id="onetrust-accept-btn-handler"]'  # Cookie button
```

**Mitigation:**
- Check for errors regularly
- Update selectors if NASDAQ redesigns
- Consider NASDAQ Data Link API (paid)

### 4. No Real-Time Data

**Constraint:** Historical data only, delayed by 1 day.

**Impact:**
- Cannot get today's prices
- Not suitable for live trading

**Workaround:**
- Use IB/Capital/Binance modules for real-time
- Use NASDAQ module for historical backtesting

### 5. 10-Year Limit

**Constraint:** Maximum 10 years of data (`timeline=y10`).

**Impact:**
- Cannot get data older than 10 years
- Fixed by NASDAQ.com API

**Workaround:**
- Use alternative data sources for older data
- Yahoo Finance, Alpha Vantage, Quandl, etc.

### 6. Rate Limiting Risk

**Constraint:** Excessive requests may trigger NASDAQ anti-bot.

**Impact:**
- IP block or CAPTCHA
- Failed downloads

**Mitigation:**
- Add delays between requests
- Limit batch size (e.g., 50 tickers at a time)
- Use residential proxy if needed

### 7. Headless Mode Detection

**Constraint:** Some sites detect headless browsers.

**Impact:**
- NASDAQ.com generally allows headless
- May change in future

**Workaround:**
- Use `headless=False` if detected
- Spoof user agent (add to Firefox options)

## Dependencies

```
selenium
tqdm
```

**System Requirements:**
- Firefox browser
- geckodriver (Firefox WebDriver)

## File Reference

```
argus/nasdaq/
└── __init__.py  # NASDAQDataDownloader
```

## Summary

The NASDAQ module provides automated historical stock data downloads from NASDAQ.com:

**Key Characteristics:**
- ✅ 10-year historical data
- ✅ Batch downloading
- ✅ Progress tracking
- ✅ Headless mode
- ❌ No real-time data
- ❌ Slow (browser automation)
- ❌ Fragile (web scraping)

**Best For:**
- Historical data collection
- Backtesting research
- Building training datasets
- One-time bulk downloads

**Not Suitable For:**
- Real-time trading
- High-frequency updates
- Production systems (use APIs)
- Long-term maintenance (UI changes)

**Complementary Usage:**

Combine NASDAQ module with other Argus modules:
- **NASDAQ** → Historical data for backtesting
- **IB/Capital/Binance** → Real-time data for live trading
- **TradingView** → Charts and technical analysis

Together, they provide comprehensive historical and real-time market data infrastructure.
