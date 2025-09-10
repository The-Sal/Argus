import time
import tempfile
from pathlib import Path
from tqdm import tqdm
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException


class NASDAQDataDownloader:
    """
    A reusable class for downloading historical stock data from NASDAQ.
    """

    def __init__(self, headless=True):
        """
        Initialize the NASDAQ data downloader.

        Args:
            headless (bool): Whether to run browser in headless mode
        """
        self.headless = headless
        self.driver = None
        self.temp_dir = None
        self.cookie_accepted = False
        self.base_url = 'https://www.nasdaq.com/market-activity/stocks/{}/historical?page=1&rows_per_page=10&timeline=y10'
        self._setup_environment()
        self._init_driver()

    def _setup_environment(self):
        """Setup temporary directory for downloads."""
        # Create temporary directory with argus.nasdaq prefix
        self.temp_dir = Path(tempfile.mkdtemp(prefix='argus.nasdaq.'))
        print(f"Using temporary directory: {self.temp_dir}")

    def _init_driver(self):
        """Initialize the Firefox webdriver with appropriate options."""
        firefox_options = Options()

        if self.headless:
            firefox_options.add_argument('--headless')

        # Set download preferences to use our temp directory
        firefox_options.set_preference('browser.download.folderList', 2)
        firefox_options.set_preference('browser.download.dir', str(self.temp_dir))
        firefox_options.set_preference('browser.download.useDownloadDir', True)
        firefox_options.set_preference('browser.helperApps.neverAsk.saveToDisk', 'text/csv,application/csv')

        self.driver = webdriver.Firefox(options=firefox_options)
        print("Firefox driver initialized" + (" (headless)" if self.headless else ""))

    def _accept_cookies(self):
        """Accept cookies if not already done."""
        if not self.cookie_accepted:
            try:
                cookie_button = WebDriverWait(self.driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, '//*[@id="onetrust-accept-btn-handler"]'))
                )
                cookie_button.click()
                self.cookie_accepted = True
                print("Cookies accepted")
            except TimeoutException:
                print("No cookie banner found or already accepted")

    def _wait_for_page_ready(self):
        """Wait for page to be fully loaded."""
        WebDriverWait(self.driver, 10).until(
            lambda driver: driver.execute_script("return document.readyState") == "complete"
        )

    def _wait_with_progress(self, seconds, tracker, ticker):
        """Wait while updating progress tracker."""
        for _ in range(int(seconds * 10)):
            time.sleep(0.1)
            tracker.set_postfix({"current": ticker}, refresh=True)

    def _download_single_ticker(self, ticker, tracker):
        """
        Download data for a single ticker.

        Args:
            ticker (str): Stock ticker symbol
            tracker: Progress tracker

        Returns:
            Path or None: Path to downloaded file or None if failed
        """
        try:
            url = self.base_url.format(ticker)
            self.driver.get(url)
            tracker.set_postfix({"current": ticker}, refresh=True)

            # Wait for page to be ready
            self._wait_for_page_ready()

            self._accept_cookies()

            # Scroll down to access buttons (page is dynamic)
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight/12);")

            # Click on Historical Data tab (retry for intercepted clicks)
            for attempt in range(10):
                try:
                    historical_tab = WebDriverWait(self.driver, 10).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, 'button.jupiter22-tab:nth-child(6)'))
                    )
                    historical_tab.click()
                    break
                except ElementClickInterceptedException:
                    time.sleep(1)

            # Get existing files before download
            existing_files = set(self.temp_dir.glob('*'))

            # Click download button when ready
            download_button = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, '.historical-download'))
            )
            download_button.click()

            # Give download time to complete
            time.sleep(2)

            # Find new downloaded file
            current_files = set(self.temp_dir.glob('*'))
            new_files = current_files - existing_files

            if new_files:
                downloaded_file = list(new_files)[0]
                # Rename file to ticker name
                new_path = self.temp_dir / f"{ticker}.csv"
                downloaded_file.rename(new_path)
                return new_path
            else:
                print(f"No new file found for {ticker}")
                return None

        except Exception as e:
            print(f'Failed to download {ticker} data: {e}')
            return None

    def download_tickers(self, tickers):
        """
        Download historical data for multiple tickers.

        Args:
            tickers (list): List of ticker symbols

        Returns:
            dict: Dictionary with 'succeeded' and 'failed' lists, and 'files' paths
        """
        if not isinstance(tickers, list):
            tickers = [tickers]

        failed = []
        succeeded = []
        downloaded_files = []

        print(f'{len(tickers)} ticker data to download')

        with tqdm(total=len(tickers), desc="Downloading") as tracker:
            for ticker in tickers:
                file_path = self._download_single_ticker(ticker, tracker)

                if file_path:
                    succeeded.append(ticker)
                    downloaded_files.append(file_path)
                else:
                    failed.append(ticker)

                tracker.update(1)

        print(f'Successfully downloaded {len(succeeded)} tickers')
        print(f'Failed to download {len(failed)} tickers')

        return {
            'succeeded': succeeded,
            'failed': failed,
            'files': downloaded_files,
            'temp_dir': self.temp_dir
        }

    def destroy(self):
        """Clean up resources and close the browser."""
        if self.driver:
            self.driver.quit()
            self.driver = None
            print("Browser driver closed")

        # Optional: Clean up temp directory
        # if self.temp_dir and self.temp_dir.exists():
        #     import shutil
        #     shutil.rmtree(self.temp_dir)
        #     print(f"Temporary directory {self.temp_dir} cleaned up")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with automatic cleanup."""
        self.destroy()


# Example usage
if __name__ == '__main__':
    # Example 1: Using context manager (recommended)
    with NASDAQDataDownloader(headless=False) as downloader:
        result = downloader.download_tickers([
                                                 # Technology
                                                 "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "NVDA",
                                                 "NFLX", "ADBE",
                                                 "CRM", "ORCL", "IBM", "INTC", "AMD", "QCOM", "AVGO", "TXN", "AMAT",
                                                 "LRCX",
                                                 "KLAC", "MRVL", "MU", "WDC", "STX", "NTAP", "HPQ", "DELL", "VMW",
                                                 "SNOW",
                                                 "PLTR", "U", "DDOG", "CRWD", "ZS", "OKTA", "SPLK", "NOW", "TEAM",
                                                 "ATLASSIAN",

                                                 # Financial Services
                                                 "JPM", "BAC", "WFC", "C", "GS", "MS", "BRK-A", "BRK-B", "V", "MA",
                                                 "AXP", "COF", "USB", "PNC", "TFC", "BK", "STT", "SCHW", "CME", "ICE",
                                                 "SPGI", "MCO", "BLK", "TROW", "IVZ", "AMG", "NTRS", "RF", "ZION",
                                                 "HBAN",

                                                 # Cryptocurrency/Blockchain
                                                 "COIN", "MSTR", "SQ", "PYPL", "RIOT", "MARA", "HUT", "BITF", "CAN",
                                                 "CONY",

                                                 # Healthcare & Pharmaceuticals
                                                 "JNJ", "PFE", "ABBV", "MRK", "LLY", "TMO", "ABT", "DHR", "BMY", "AMGN",
                                                 "GILD", "VRTX", "REGN", "BIIB"])

        print("\nDownload Results:")
        print(f"Succeeded: {result['succeeded']}")
        print(f"Failed: {result['failed']}")
        print(f"Files location: {result['temp_dir']}")

        for file_path in result['files']:
            print(f"Downloaded: {file_path}")

    # Example 2: Manual management
    # downloader = NASDAQDataDownloader(headless=False)
    # try:
    #     result = downloader.download_tickers(["TSLA", "MSFT"])
    # finally:
    #     downloader.destroy()