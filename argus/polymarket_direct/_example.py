import os
import sys
import time
import json
import pickle
import signal
import datetime
import threading
import websocket
from termcolor import colored
from dotenv import load_dotenv
from argus.polymarket_direct import EnhancedPM, PolymarketEvent


class StreamRecorder:
    """
    Thread-safe recorder that accumulates market updates in-memory and persists them to disk via pickle
    at a fixed interval using hourly rotation. On exit, merges all hourly files into one mega file.
    """
    def __init__(self, pickle_dir: str = '.', file_prefix: str = 'polymarket_stream', save_interval_sec: int = 60):
        self.pickle_dir = pickle_dir
        self.file_prefix = file_prefix
        self.save_interval_sec = int(save_interval_sec)
        self.records: list[dict] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.current_hour_key: str | None = None  # Format: YYYY-MM-DD_HH

        # Ensure directory exists
        os.makedirs(self.pickle_dir, exist_ok=True)

    def _get_hour_key(self, dt: datetime.datetime) -> str:
        """Get hour key for a datetime (YYYY-MM-DD_HH format)"""
        return dt.strftime('%Y-%m-%d_%H')

    def _get_hourly_filename(self, hour_key: str) -> str:
        """Get filename for a given hour key"""
        return os.path.join(self.pickle_dir, f"{self.file_prefix}_{hour_key}.pkl")

    def _get_current_hour_key(self) -> str:
        """Get the current hour key based on UTC time"""
        return self._get_hour_key(datetime.datetime.now(datetime.UTC))

    def load_if_exists(self):
        """Load existing hourly pickle files if any exist (for continuity after restart)"""
        try:
            # Find all existing hourly files for this prefix
            pattern = f"{self.file_prefix}_*.pkl"
            import glob
            matching_files = glob.glob(os.path.join(self.pickle_dir, pattern))

            if matching_files:
                print(f"Found {len(matching_files)} existing hourly pickle files")
                # Note: We don't load them into memory as that would defeat the purpose
                # They'll be merged on exit
            else:
                print("No existing hourly pickle files found; starting fresh")
        except Exception as e:
            print(f"Failed to check for existing pickles: {e}")

    def append(self, rec: dict):
        """Append a record to the in-memory buffer"""
        with self._lock:
            self.records.append(rec)

    def _atomic_save(self):
        """Save current records to hourly file and clear buffer. Thread-safe."""
        try:
            with self._lock:
                if not self.records:
                    return True, 0

                # Determine current hour
                current_hour = self._get_current_hour_key()

                # Check if we've rotated to a new hour
                if self.current_hour_key is None:
                    self.current_hour_key = current_hour
                elif current_hour != self.current_hour_key:
                    # Hour changed - finalize the old file first
                    print(f"[Recorder] Hour rotation: {self.current_hour_key} -> {current_hour}")
                    self.current_hour_key = current_hour

                # Get filename for current hour
                hour_file = self._get_hourly_filename(self.current_hour_key)
                tmp_path = hour_file + '.tmp'

                # Load existing records from this hour file if it exists
                existing_records = []
                if os.path.exists(hour_file):
                    try:
                        with open(hour_file, 'rb') as f:
                            data = pickle.load(f)
                            if isinstance(data, list):
                                existing_records = data
                    except Exception as e:
                        print(f"Warning: Could not load existing {hour_file}: {e}")

                # Combine existing with new records
                snapshot = list(self.records)
                combined_records = existing_records + snapshot
                record_count = len(snapshot)

                # Clear the buffer NOW (before writing to avoid holding lock during I/O)
                self.records.clear()

            # Write atomically (outside lock to avoid holding during I/O)
            with open(tmp_path, 'wb') as f:
                pickle.dump(combined_records, f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp_path, hour_file)

            return True, record_count

        except Exception as e:
            try:
                if 'tmp_path' in locals() and os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            print(f"Failed to save hourly pickle: {e}")
            return False, 0

    def _loop(self):
        """Background thread that periodically saves records"""
        next_time = time.time() + self.save_interval_sec
        while not self._stop.is_set():
            now = time.time()
            if now >= next_time:
                ok, n = self._atomic_save()
                if ok and n > 0:
                    print(f"[Recorder] Saved {n} new records to {self.current_hour_key}.pkl")
                next_time = now + self.save_interval_sec
            # Sleep but wake quickly on stop
            self._stop.wait(0.5)

        # Final flush on stop
        ok, n = self._atomic_save()
        if ok and n > 0:
            print(f"[Recorder] Final save of {n} records")

    def start(self):
        """Start the background saving thread"""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name='PM-StreamRecorder', daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the background saving thread"""
        self._stop.set()
        t = self._thread
        if t:
            t.join(timeout=5)
            self._thread = None

    def merge_hourly_files(self) -> str | None:
        """
        Merge all hourly pickle files into one mega file with start-end time in filename.
        Returns the path to the merged file, or None if no files to merge.
        """
        print("[Recorder] Merging hourly files no longer supported in this environment.")
        sys.exit(0)

        # try:
        #     import glob
        #
        #     # Find all hourly files for this prefix (excluding already-merged files)
        #     pattern = f"{self.file_prefix}_????-??-??_??.pkl"
        #     hourly_files = sorted(glob.glob(os.path.join(self.pickle_dir, pattern)))
        #
        #     if not hourly_files:
        #         print("[Recorder] No hourly files to merge")
        #         return None
        #
        #     print(f"[Recorder] Merging {len(hourly_files)} hourly files...")
        #
        #     # Load all records from all hourly files
        #     all_records = []
        #     for hourly_file in hourly_files:
        #         try:
        #             with open(hourly_file, 'rb') as f:
        #                 data = pickle.load(f)
        #                 if isinstance(data, list):
        #                     all_records.extend(data)
        #                     print(f"  Loaded {len(data)} records from {os.path.basename(hourly_file)}")
        #         except Exception as e:
        #             print(f"  Warning: Could not load {hourly_file}: {e}")
        #
        #     if not all_records:
        #         print("[Recorder] No records found in hourly files")
        #         return None
        #
        #     print(f"[Recorder] Total records loaded: {len(all_records)}")
        #
        #     # Sort by timestamp for consistency
        #     try:
        #         all_records.sort(key=lambda r: r.get('timestamp', 0))
        #     except Exception as e:
        #         print(f"Warning: Could not sort records: {e}")
        #
        #     # Determine start and end times from the data
        #     start_time = None
        #     end_time = None
        #
        #     for rec in all_records:
        #         ts_utc = rec.get('ts_utc')
        #         if ts_utc:
        #             try:
        #                 if isinstance(ts_utc, str):
        #                     dt = datetime.datetime.fromisoformat(ts_utc.replace('Z', '+00:00'))
        #                 else:
        #                     dt = ts_utc
        #
        #                 if start_time is None or dt < start_time:
        #                     start_time = dt
        #                 if end_time is None or dt > end_time:
        #                     end_time = dt
        #             except Exception:
        #                 pass
        #
        #     # Format timestamps for filename
        #     if start_time and end_time:
        #         start_str = start_time.strftime('%Y%m%d_%H%M%S')
        #         end_str = end_time.strftime('%Y%m%d_%H%M%S')
        #         merged_filename = f"{self.file_prefix}_MERGED_{start_str}_{end_str}.pkl"
        #     else:
        #         # Fallback if we couldn't parse timestamps
        #         timestamp_str = datetime.datetime.now(datetime.UTC).strftime('%Y%m%d_%H%M%S')
        #         merged_filename = f"{self.file_prefix}_MERGED_{timestamp_str}.pkl"
        #
        #     merged_path = os.path.join(self.pickle_dir, merged_filename)
        #
        #     # Save merged file
        #     with open(merged_path, 'wb') as f:
        #         pickle.dump(all_records, f, protocol=pickle.HIGHEST_PROTOCOL)
        #
        #     print(f"[Recorder] Merged file created: {merged_filename}")
        #     print(f"[Recorder] Total records in merged file: {len(all_records)}")
        #
        #     if start_time and end_time:
        #         duration = (end_time - start_time).total_seconds()
        #         print(f"[Recorder] Time range: {start_time.isoformat()} to {end_time.isoformat()} ({duration/3600:.2f} hours)")
        #
        #     return merged_path
        #
        # except Exception as e:
        #     print(f"[Recorder] Error during merge: {e}")
        #     import traceback
        #     traceback.print_exc()
        #     return None


def example_usage():
    assert load_dotenv()
    enhanced_pm = EnhancedPM(
        private_key=os.environ['POLYMARKET_PRIVATE_KEY'],
        proxy_funder=os.environ['POLYMARKET_PROXY_FUNDER']
    )
    enhanced_pm.start_market_ws()

    # Recorder setup with hourly rotation
    pickle_dir = os.environ.get('POLYMARKET_STREAM_DIR', './polymarket_data')
    file_prefix = os.environ.get('POLYMARKET_STREAM_PREFIX', 'polymarket_stream')
    save_interval = int(os.environ.get('POLYMARKET_STREAM_SAVE_INTERVAL', '60'))

    recorder = StreamRecorder(pickle_dir=pickle_dir, file_prefix=file_prefix, save_interval_sec=save_interval)
    recorder.load_if_exists()
    recorder.start()

    # Signal handler for graceful shutdown on Ctrl+C
    def signal_handler(sig, frame):

        print("\n[SIGNAL] Ctrl+C detected, shutting down gracefully...")
        try:
            recorder.stop()
            print("[SIGNAL] Merging hourly files...")

            merged_path = recorder.merge_hourly_files()
            if merged_path:
                print(f"[SIGNAL] Successfully created merged file: {merged_path}")
            else:
                print("[SIGNAL] No merge performed (no hourly files found)")
        except Exception as e:
            print(f"[SIGNAL] Error during shutdown: {e}")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    current_time = datetime.datetime.now(datetime.UTC)

    all_bitcoin_hourly = []
    raw_data_dump = []
    offset_val = 0
    offset_step = 150
    total_fetched = 0
    while True:
        rq = enhanced_pm.fetch_events(limit=offset_step, offset=offset_val,
                                      debug_raw_callback=lambda x: raw_data_dump.append(x))
        total_fetched += len(rq)
        if len(rq) == 0:
            break
        for market in rq:
            try:
                if 'bitcoin-up-or-down' in market.ticker and '-et' in market.ticker:
                    all_bitcoin_hourly.append(market)
            except TypeError:
                pass

        print(f"\rFetched {total_fetched} markets, total bitcoin hourly markets: {len(all_bitcoin_hourly)}", end='')
        offset_val += offset_step
    print('' * 100)

    cleaned_raw_dump = []
    for raw in raw_data_dump:
        if 'bitcoin-up-or-down' in raw['ticker']:
            cleaned_raw_dump.append(raw)

    with open('all_bitcoin_hourly.json', 'w') as f:
        f.write(json.dumps(cleaned_raw_dump, indent=4))
    
    # Free memory: clear temporary lists that are no longer needed
    # These lists can grow large during initialization and should be released
    # to prevent memory growth over 24+ hour runtime (Issue: memory growth)
    del raw_data_dump
    del cleaned_raw_dump


    def _sorted_by_time_key(sorting_event: PolymarketEvent):
        [x.convert_to_datetime() for x in sorting_event.markets]
        return sorting_event.markets[0].eventStartTime


    all_bitcoin_hourly.sort(key=_sorted_by_time_key)
    print('*' * 100)
    for market in all_bitcoin_hourly:
        startTime = market.markets[0].eventStartTime
        endTime = market.markets[0].endDate
        diff = startTime - current_time
        seconds_till_start = diff.total_seconds()
        print(
            f"Market: {market.ticker}, Starts in: {seconds_till_start / 3600:.2f} hours at "
            f"{datetime.datetime.strftime(startTime, '%Y-%m-%d %H:%M:%S UTC')}",
            f"Ends in: {(endTime - current_time).total_seconds() / 3600:.2f} hours at {datetime.datetime.strftime(endTime, '%Y-%m-%d %H:%M:%S UTC')} "
        )

    # find everything that is currently live
    print('\n' + '=' * 100 + '\n')
    print("Currently Live Events:")
    live_markets = []
    for market in all_bitcoin_hourly:
        startTime = market.markets[0].eventStartTime
        endTime = market.markets[0].endDate
        if startTime < current_time < endTime:
            print(
                f"Market: {market.ticker}, Started at "
                f"{datetime.datetime.strftime(startTime, '%Y-%m-%d %H:%M:%S UTC')}, "
                f"Ends at {datetime.datetime.strftime(endTime, '%Y-%m-%d %H:%M:%S UTC')} "
            )
            live_markets.append(market)
    print('\n' + '=' * 100 + '\n')
    
    # Free memory: live_markets list is only used for display
    del live_markets

    # here's the idea because markets in bitcoin hourly start one after the other, we want a continuous stream of data
    # that follows the current live market and when it ends, switches to the next one. We do that
    # by ordering them all in an array (already done above), and the index is the currently live one
    # everytime a market ends we switch to the next one in the list by index++

    # the main loop should be blocked by a threading.Semaphore only released when a market ends
    # a thread should be made within the loop that waits until the market end time, then releases the semaphore
    # this should make the main loop continue at which point it will unsubscribe from the old market
    # then go to the next iteration which will subscribe to the new market

    # ===== Continuous stream implementation =====
    def _subscribe_for_event(event: PolymarketEvent):
        assert len(event.markets) == 1, "Expected exactly one market per event, do not support multi-market events yet."
        mkt = event.markets[0]
        outcomes = mkt.outcomes or []
        tokens = mkt.clobTokenIds or []
        # Ensure lists
        if not isinstance(outcomes, list) or not isinstance(tokens, list):
            raise AssertionError("Expected 'outcomes' and 'clobTokenIds' to be lists after parsing.")
        assert len(outcomes) == len(tokens) and len(tokens) > 0, "Expected equal non-empty number of outcomes and tokens."

        print(f"Subscribing to {event.ticker} | {mkt.question}")
        print('Outcome:Token ID')
        for outcome, token_id in zip(outcomes, tokens):
            print(f"  {outcome}: {token_id}")

        def callback_wrapper(outcome_name, token_id):
            def inner_callback(orderbook_data):
                try:
                    best_ask = orderbook_data.get('best_ask')
                    best_bid = orderbook_data.get('best_bid')
                    size = orderbook_data.get('size')
                    px = orderbook_data.get('price')
                    ts = orderbook_data.get('timestamp') or orderbook_data.get('ts')
                    # compute time till close (seconds) based on market end time
                    now = datetime.datetime.now(datetime.UTC)
                    et = mkt.endDate
                    if isinstance(et, str):
                        try:
                            et = datetime.datetime.fromisoformat(et.replace('Z', '+00:00'))
                        except Exception:
                            et = None
                    secs_till_close = None
                    if isinstance(et, datetime.datetime):
                        try:
                            secs_till_close = max(0.0, (et - now).total_seconds())
                        except Exception:
                            secs_till_close = None
                    display_ttc = f"{secs_till_close:.1f}s" if secs_till_close is not None else "n/a"
                    if outcome_name.lower() == 'up':
                        color = 'green'
                    elif outcome_name.lower() == 'down':
                        color = 'red'
                    print(f"[{event.ticker} | ttc={display_ttc}] {colored(outcome_name, color=color)} ask={best_ask} size={size} price={px}")
                    # Enriched record for persistent storage
                    rec = {
                        'ts_utc': now.isoformat(),
                        'ws_ts': ts,
                        'event_id': event.id,
                        'event_ticker': event.ticker,
                        'market_id': mkt.id,
                        'market_question': mkt.question,
                        'outcome': outcome_name,
                        'token_id': token_id,
                        'secs_till_close': secs_till_close,
                        'payload': dict(orderbook_data),
                        'timestamp': time.time()
                    }
                    recorder.append(rec)
                except Exception as e:
                    print("Callback error:", e)
            return inner_callback

        callbacks = [callback_wrapper(outcome, token_id) for outcome, token_id in zip(outcomes, tokens)]
        # subscribe each token with its own callback
        for cb, token_id in zip(callbacks, tokens):
            enhanced_pm.subscribe_to_market_data([token_id], cb)
        return tokens  # so we can later unsubscribe

    def _unsubscribe_tokens(tokens: list[str]):
        if not tokens:
            return
        try:
            enhanced_pm.unsubscribe_from_market_data(tokens)
        except Exception as e:
            print("Unsubscribe error:", e)

    # compute start index: the first market whose window contains 'now', else upcoming
    def _find_start_index(now_utc: datetime.datetime) -> int:
        for i, ev in enumerate(all_bitcoin_hourly):
            st = ev.markets[0].eventStartTime
            et = ev.markets[0].endDate
            if st <= now_utc <= et:
                return i
            if now_utc < st:
                return i  # upcoming: start from this
        return -1  # past all

    def _wait_until(dt_utc: datetime.datetime, label: str = "") -> bool:
        """Block until the target UTC time using a local semaphore and a timer.
        Returns True when the target time has passed (either via timer firing or timeout safeguard).
        """
        now = datetime.datetime.now(datetime.UTC)
        delay = (dt_utc - now).total_seconds()
        # If already past target, return immediately
        if delay <= 0:
            return True
        # Use a local semaphore to avoid cross-release between phases
        sem_local = threading.Semaphore(0)

        def _release_and_log():
            try:
                print(f"[Timer] Reached {datetime.datetime.strftime(dt_utc, '%Y-%m-%d %H:%M:%S UTC')} {f'({label})' if label else ''}; releasing.")
            except Exception:
                pass
            sem_local.release()

        timer = threading.Timer(delay, _release_and_log)
        timer.daemon = True
        timer.start()
        try:
            print(f"[Timer] Waiting {delay:.2f}s until {datetime.datetime.strftime(dt_utc, '%Y-%m-%d %H:%M:%S UTC')} {f'({label})' if label else ''}")
        except Exception:
            pass
        # Add a small cushion to ensure we don't miss due to scheduling jitter
        acquired = sem_local.acquire(timeout=delay + 5.0)
        if not acquired:
            print(f"[Timer] Timeout after {delay + 5.0:.2f}s waiting for {label or 'target'}; proceeding.")
        return True

    # main loop
    idx = _find_start_index(datetime.datetime.now(datetime.UTC))
    if idx == -1:
        print("All events are in the past. Nothing to subscribe to.")
        recorder.stop()
        recorder.merge_hourly_files()
        return

    current_tokens = []
    try:
        # Iterate from idx to the end, switching at each end time
        for i in range(idx, len(all_bitcoin_hourly)):
            ev = all_bitcoin_hourly[i]
            st = ev.markets[0].eventStartTime
            et = ev.markets[0].endDate

            # If we're before start, wait until start (no subscription yet)
            now = datetime.datetime.now(datetime.UTC)
            # if now < st:
            #     print(f"Waiting until {datetime.datetime.strftime(st, '%Y-%m-%d %H:%M:%S UTC')} to start {ev.ticker}...")
            #     _wait_until(st, f"start {ev.ticker}")

            # Subscribe for this event and wait until end time
            enhanced_pm.restart_ws_connections()
            time.sleep(5.0)  # brief pause to ensure ws is ready
            current_tokens = _subscribe_for_event(ev)
            print(f"Subscribed. Will switch at end time {datetime.datetime.strftime(et, '%Y-%m-%d %H:%M:%S UTC')}\n")
            now = datetime.datetime.now(datetime.UTC)
            if now >= et:
                print(f"End time {datetime.datetime.strftime(et, '%Y-%m-%d %H:%M:%S UTC')} already passed; switching immediately.")
            else:
                _wait_until(et, f"end {ev.ticker}")

            # Time to switch: unsubscribe and continue to next
            print(f"Event ended: {ev.ticker}. Switching...")
            _unsubscribe_tokens(current_tokens)

        print("Reached the end of scheduled events. Exiting continuous stream loop.")
    finally:
        # Ensure we stop recorder and perform final merge
        try:
            try:
                _unsubscribe_tokens(current_tokens)
            except Exception as e:
                print(f"[CLEANUP] Error during cleanup: {e}, continuing...")

            print("[CLEANUP] Stopping recorder...")
            recorder.stop()
            print("[CLEANUP] Merging hourly files...")
            merged_path = recorder.merge_hourly_files()
            if merged_path:
                print(f"[CLEANUP] Successfully created merged file: {merged_path}")
        except Exception as e:
            print(f"[CLEANUP] Error during cleanup: {e}")

if __name__ == '__main__':
    websocket.enableTrace(True)
    example_usage()