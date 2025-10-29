import os
import time
import json
import pickle
import datetime
import threading
from termcolor import colored
from dotenv import load_dotenv
from argus.polymarket_direct import EnhancedPM, PolymarketEvent


class StreamRecorder:
    """
    Thread-safe recorder that accumulates market updates in-memory and persists them to disk via pickle
    at a fixed interval. It can also load an existing pickle to continue appending where it left off.
    """
    def __init__(self, pickle_path: str = 'polymarket_stream.pkl', save_interval_sec: int = 60):
        self.pickle_path = pickle_path
        self.save_interval_sec = int(save_interval_sec)
        self.records: list[dict] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def load_if_exists(self):
        try:
            if os.path.exists(self.pickle_path):
                with open(self.pickle_path, 'rb') as f:
                    data = pickle.load(f)
                    if isinstance(data, list):
                        with self._lock:
                            self.records = data
                        print(f"Loaded {len(self.records)} prior records from {self.pickle_path}")
                    else:
                        print(f"Existing pickle at {self.pickle_path} was not a list; starting fresh")
        except Exception as e:
            print(f"Failed to load existing pickle {self.pickle_path}: {e}")

    def append(self, rec: dict):
        with self._lock:
            self.records.append(rec)

    def _atomic_save(self):
        tmp_path = self.pickle_path + '.tmp'
        try:
            with self._lock:
                snapshot = list(self.records)
            with open(tmp_path, 'wb') as f:
                pickle.dump(snapshot, f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp_path, self.pickle_path)
            return True, len(snapshot)
        except Exception as e:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            print(f"Failed to save pickle {self.pickle_path}: {e}")
            return False, 0

    def _loop(self):
        next_time = time.time() + self.save_interval_sec
        while not self._stop.is_set():
            now = time.time()
            if now >= next_time:
                ok, n = self._atomic_save()
                if ok:
                    print(f"[Recorder] Saved {n} records to {self.pickle_path}")
                next_time = now + self.save_interval_sec
            # sleep a bit but wake quickly on stop
            self._stop.wait(0.5)
        # final flush
        ok, n = self._atomic_save()
        if ok:
            print(f"[Recorder] Final save of {n} records to {self.pickle_path}")

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name='PM-StreamRecorder', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        t = self._thread
        if t:
            t.join(timeout=5)
            self._thread = None


def example_usage():
    assert load_dotenv()
    enhanced_pm = EnhancedPM(
        private_key=os.environ['POLYMARKET_PRIVATE_KEY'],
        proxy_funder=os.environ['POLYMARKET_PROXY_FUNDER']
    )
    enhanced_pm.start_market_ws()

    # Recorder setup: load previous pickle (if any) and start background saver
    pickle_path = os.environ.get('POLYMARKET_STREAM_PICKLE', 'polymarket_stream.pkl')
    save_interval = int(os.environ.get('POLYMARKET_STREAM_SAVE_INTERVAL', '60'))
    recorder = StreamRecorder(pickle_path=pickle_path, save_interval_sec=save_interval)
    recorder.load_if_exists()
    recorder.start()

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

    def _release_at(dt_utc: datetime.datetime, sem: threading.Semaphore):
        now = datetime.datetime.now(datetime.UTC)
        delay = (dt_utc - now).total_seconds()
        if delay <= 0:
            sem.release()
            return
        timer = threading.Timer(delay, sem.release)
        timer.daemon = True
        timer.start()

    # main loop
    sem = threading.Semaphore(0)
    idx = _find_start_index(datetime.datetime.now(datetime.UTC))
    if idx == -1:
        print("All events are in the past. Nothing to subscribe to.")
        recorder.stop()
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
            if now < st:
                print(f"Waiting until {datetime.datetime.strftime(st, '%Y-%m-%d %H:%M:%S UTC')} to start {ev.ticker}...")
                _release_at(st, sem)
                sem.acquire()

            # Subscribe for this event and set a timer to release at end
            current_tokens = _subscribe_for_event(ev)
            print(f"Subscribed. Will switch at end time {datetime.datetime.strftime(et, '%Y-%m-%d %H:%M:%S UTC')}\n")
            _release_at(et, sem)
            sem.acquire()

            # Time to switch: unsubscribe and continue to next
            print(f"Event ended: {ev.ticker}. Switching...")
            _unsubscribe_tokens(current_tokens)

        print("Reached the end of scheduled events. Exiting continuous stream loop.")
    finally:
        # Ensure we stop recorder and perform final save
        try:
            recorder.stop()
        except Exception as e:
            print("Error stopping recorder:", e)

if __name__ == '__main__':
    example_usage()
