"""
Refreshed Polymarket Dispatcher based on the polymarket_direct module. For the old version
see https://github.com/The-Sal/Argus/tree/legacy/polymarket-dispatcher

The below code removes the entire old stub with a new implementation based on polymarket_direct.
In a future version this documentation referencing the old dispatcher will be removed.

IMPORTANT — Account Update Delivery Requirement:
    Real-time account events (order PLACEMENT, CANCELLATION, MATCH, etc.) are
    only broadcast to client sockets that have an active market data subscription.
    A client that connects and issues order management commands (place_order,
    cancel_order, get_order_status, etc.) WITHOUT first subscribing to at least
    one asset via the 'subscribe' action will NEVER receive account_update pushes,
    even though the dispatcher's internal WebSocket is receiving them from the CLOB.

    This is because the dispatcher tracks connected clients through the
    RoutingHelper's socket set, which is only populated when a client subscribes
    to market data.  If your workflow depends on receiving account lifecycle
    events, you MUST subscribe to at least one asset_id before placing orders.
"""
import gc
import os
import json
import time
import tqdm
import zlib
import atexit
import base64
import socket
import difflib
import logging
import threading
import traceback
import subprocess
import dataclasses
from typing import List
from collections import deque
from datetime import datetime
from termcolor import colored
from utils3 import runAsThread, Timer
from argus.polymarket_direct import wss
from utils3.networking.sockets import Server
from argus import __version__ as ARGUS_VERSION
from argus.wireproxy.wrapper import BIND_ADDRESS
from argus.cache_sys import DomainCache, FastCache
from argus._argus_utils import Introspective, throw_fuss
from argus.polymarket._mem_slim import traverse_and_slim
from argus.polymarket_direct import rest, PolymarketEvent
from argus.polymarket_direct.order_types import OrderEvent
from concurrent.futures import ThreadPoolExecutor, as_completed
from argus.polymarket.proxy_perf import ProxyPerformanceProfiler
from argus.polymarket_direct.unsafe_api import UnsafePolyMarket, UnableToReachPolymarket
from argus.protocol import decode_multiple_packets, encode_packet, transmit_mkt_data_with_protocol_2
from argus.polymarket._classes import (
    PolyMarketDispatcherError,
    InvalidArgumentError,
    RoutingHelper,
    ArgsObject,
    P2ConvertClass,
    print_with_name,
    CorrelationIDChecker,
    OrderExecutionDisabledError,
)

# Much like it's predecessor on legacy/ this dispatcher is contained to its own cache file due to bloat.
_poly_cache = FastCache(cache_file="~/.argus/polymarket_cache.pkl")
_CACHE = DomainCache("polymarket_dispatcher_v2", cache=_poly_cache)



def compress(data: dict) -> str:
    minified = json.dumps(data, separators=(',', ':')).encode()
    return base64.b64encode(zlib.compress(minified, level=9)).decode()


class PolymarketDispatcher(Introspective, RoutingHelper):
    """
    TCP server that exposes Polymarket's CLOB via a P1 (JSON) control protocol
    and a P2 (binary) market data protocol.

    WARNING: Account lifecycle events (PLACEMENT, CANCELLATION, MATCH, etc.)
    are only forwarded to client sockets that have an active market data
    subscription via the 'subscribe' action.  Clients that only use order
    management actions (place_order, cancel_order, get_order_status, ...) will
    NOT receive real-time account_update pushes unless they first subscribe to
    at least one asset_id.  This is a consequence of the RoutingHelper's socket
    tracking — sockets are registered only on subscription.
    """

    def __init__(
            self,
            private_key: str = None,
            proxy_funder: str = None,
            host="localhost",
            port=9972,
            profile_proxy=-1,
    ):
        """
        Initializes the PolymarketDispatcher instance to handle incoming market data, account events,
        and routing tasks across relevant components. Configures the REST API and WebSocket
        connections for managing and processing Polymarket events effectively. Ensures proper
        initialization of market caches and spawns background threads for continuous data updates.

        :param private_key: The private key used for authentication with the PolyRestAPI. Defaults
            to the value of the 'POLYMARKET_PRIVATE_KEY' environment variable if not explicitly provided.
        :type private_key: Str, optional

        :param proxy_funder: The address or identifier of the proxy funder for routing transactions
            within the Polymarket system. Defaults to the value of the 'POLYMARKET_PROXY_FUNDER'
            environment variable if not explicitly provided.
        :type proxy_funder: Str, optional

        :param host: The hostname or IP address on which the dispatcher server listens for incoming
            connections. Defaults to 'localhost'.
        :type host: Str, optional

        :param port: The port number on which the dispatcher server listens for incoming connections.
            Defaults to 9972.
        :type port: Int, optional

        :param profile_proxy: -1 (no profile), 0 (profile via proxy only), 1 (profile with proxy and local)
        :type profile_proxy: Int, optional

        """

        super().__init__()
        RoutingHelper.__init__(self)

        # Configs dictionary for dispatcher settings
        self._configs = {
            "Show P1 Packets": False,
            "Print P2 packets": False,
            "Show packet timestamps": True,
            "Block Order Execution": False, # if this is true, when an order execution endpoint is called, an exception will be raised.
            "show response times": False,
            "P2 Packets for RTDS": False,
        }
        if private_key is None:
            private_key = os.environ["POLYMARKET_PRIVATE_KEY"]

        if proxy_funder is None:
            proxy_funder = os.environ["POLYMARKET_PROXY_FUNDER"]

        self.dispatcher_svr = Server(
            on_recv=self._handle_incoming_packets,
            on_disconnect=lambda *args: print(
                "PolymarketDispatcher: Disconnected from client.", args
            ),
            host=host,
            port=port,
        )

        # All the below are already registered with WireProxy system
        self.rest_api = rest.PolyRestAPI(
            private_key=private_key,
            proxy_funder=proxy_funder,
            fatal_callback=self._on_fatal_error,
        )
        self.market_data = wss.PolyMarketOrderBookPool(
            order_book_update_callback=self._order_book_update_callback
        )
        self.account_updates = wss.PolyMarketAccountEventWss(
            auth=self.rest_api.credentials,
            update_callback=self._account_update_callback,
        )

        self.unsafe_api = UnsafePolyMarket()

        # Proxy Profiling if enabled
        if profile_proxy in [0, 1]:
            profiler = ProxyPerformanceProfiler(print_callback=print_with_name)
            profiler.run_profiling(BIND_ADDRESS, profile_proxy)
            profiler.display_table(BIND_ADDRESS, profile_proxy)

        self._market_cache_lock = threading.Lock()

        # Persistent thread pool reused by _handle_place_multiple_orders.
        # The previous implementation built a fresh ThreadPoolExecutor per call,
        # paying ~5-10 ms of thread-spawn overhead each invocation (and more under
        # python 3.14t). With this pool, build_order calls execute on long-lived
        # workers — under 3.14t the work also runs truly in parallel since the
        # sign path is CPU-bound.
        self._build_pool = ThreadPoolExecutor(
            max_workers=int(os.environ.get("POLYMARKET_BUILD_POOL_WORKERS", "10")),
            thread_name_prefix="PolyOrderBuildPool",
        )
        atexit.register(self._build_pool.shutdown, wait=False)

        self._routing_helper = RoutingHelper()
        # str is 'ticker' for Polymarket
        self._all_markets_cache: dict[str, PolymarketEvent] = {}

        # TL;DR the P2 encoding format's ticker field
        # is formatted like <Event-Ticker><Market-Slug><Asset_id>
        # now we will get asset_id from the market data wss, but we need
        # to match the asset_id to the ticker and market index so we can route the data and also decode the market data correctly.
        self._asset_id_to_ticker = {}
        # ^^^ is locked with '_market_cache_lock' since it is only updated in the market cache refresh
        # function and read in the market data update callback, which are both protected by the same lock.

        self._orderbook_depth = int(os.environ.get("POLYMARKET_ORDERBOOK_DEPTH", 10))

        # Configs
        self._market_cache_refresh_interval = int(
            os.environ.get("POLYMARKET_FULL_MARKET_CACHE_REFRESH_INTERVAL", 300)
        )
        self._market_api_limit = 150  # Max markets per API call
        self._max_seen_markets = 10100  # Typical polymarket size
        self._fetch_on_cache_miss = os.environ.get("POLYMARKET_FETCH_ON_MISS", "false") == "true"
        self._fetched_objects: List[PolymarketEvent] = []

        # Make sure we have markets ready to serve
        self._update_markets_cache(
            invalidate_cache=False
        )  # load from cache or fetch fresh

        # Start background tasks
        self.market_data.run(main_thread=False)
        self.start_update_markets_cache_thread()

        self._correlation_id_checker = CorrelationIDChecker()
        self._log_file = os.environ.get(
            "POLYMARKET_DISPATCHER_LOG_FILE",
            os.path.expanduser("~/.argus/polymarket_dispatcher.log"),
        )

        self.rtds_magic_asset_id = "RTDS_MAGIC_ID"
        self.real_time_data = wss.PolymarketRTDSWss(
            on_msg_callback=self._rtd_callback,
            book_depth=self._orderbook_depth
        )

        self.real_time_data.start()

        with open(self._log_file, "a") as f:
            f.write(
                f"\n\n--- PolymarketDispatcher(argus=v{ARGUS_VERSION}) started at {datetime.now().isoformat()} ---\n"
            )

        self._log_file_lock = threading.Lock()

        # Latency samples: time (ms) from WS packet arrival to sock.sendall() completion (market data only)
        self._latency_samples: deque[float] = deque(maxlen=10_000)

        logging.info("PolymarketDispatcher initialized on %s:%d", host, port)
        logging.info(
            "Market cache refresh interval set to %d seconds",
            self._market_cache_refresh_interval,
        )
        logging.info("Market API limit set to %d", self._market_api_limit)
        logging.info("Max seen markets initialized to %d", self._max_seen_markets)

    @runAsThread
    def async_write_log(self, message: str):
        with self._log_file_lock:
            with open(self._log_file, "a") as f:
                f.write(f"{datetime.now().isoformat()} ==> {message}\n")

    #######################################
    # Worker Threads & Functions
    #######################################

    @runAsThread
    def start_update_markets_cache_thread(self):
        """
        Periodically refresh the cache of all markets.
        :return:
        """
        while True:
            # Sleep first, to defer the first refresh and allow the initial load to complete
            time.sleep(self._market_cache_refresh_interval)
            self._update_markets_cache(invalidate_cache=True)


    def _fetch_event_on_cache_miss(self, slug: str) -> PolymarketEvent:
        """
        if a cache miss is triggered, fetch that slug.
        Note: on the next refresh this slug will be dropped, triggering this
        process again. This is intentional since this object is excluded from
        the _mem slim process. To avoid bloating memory they get dropped each
        refresh. Additonally the only reason this is not in cache bc it's an expired
        market so....
        """

        obj = self.rest_api.fetch_event_by_slug(slug=slug)
        with self._market_cache_lock:
            self._all_markets_cache.update({slug: obj})
        self._build_asset_id_to_ticker_mapping()
        return obj
        
    
    # Note: The intended logic is that when the program boots, we already have a cache of markets
    # loaded from disk (if available) or freshly fetched from the API. Subsequent calls to this function with invalidate_cache=True
    # will force a refresh from the API. The invalidation call would be coming from the background thread.
    def _update_markets_cache(self, invalidate_cache: bool = False):
        """Updates markets cache; logs errors"""
        uuid_of_func = "_update_markets_cache.internal"

        if invalidate_cache:
            print_with_name("Invalidating Polymarket markets cache.")
            _CACHE.invalidate_key(
                _CACHE.generate_key(
                    func_uuid=uuid_of_func,
                )
            )
        else:
            print_with_name(
                "Loading Polymarket markets cache from disk or fetching fresh if not available."
            )

        @_CACHE.cache_decorator(
            func_uuid=uuid_of_func,
            expiration=60 * 60 * 3,  # 3 hours
            should_cache_function=lambda x: len(x.keys()) > 0,
        )
        def fetch_all_markets_cached():
            scoped_all_markets_cache = {}
            progress = tqdm.tqdm(
                total=self._max_seen_markets,
                desc="Refreshing Polymarket markets cache",
                unit="markets",
                dynamic_ncols=True,
            )
            try:
                offset = 0
                while True:
                    markets = self.rest_api.fetch_events(
                        offset=offset, limit=self._market_api_limit
                    )
                    scoped_all_markets_cache.update(
                        {market.ticker: market for market in markets}
                    )
                    offset += len(markets)
                    progress.update(len(markets))
                    progress.set_postfix(
                        {
                            "Total Markets": len(scoped_all_markets_cache),
                            "Offset": offset,
                        }
                    )
                    progress.refresh()
                    if len(markets) == 0:
                        break

                progress.close()
                progress.refresh()
                time.sleep(1)  # tqdm refresh
                logging.info(
                    "Refreshed all markets cache with %d markets.",
                    len(scoped_all_markets_cache),
                )
                self._max_seen_markets = max(self._max_seen_markets, len(markets))
            except Exception as e:
                logging.error("Error refreshing all markets cache: %s", e)

            return scoped_all_markets_cache

        markets_cached = fetch_all_markets_cached()

        if os.environ.get("POLYMARKET_MEMORY_PRUNING", "false").lower() == "true":
            for key, value in tqdm.tqdm(
                    markets_cached.items(),
                    desc="Pruning events data in cache",
                    unit="events",
                    dynamic_ncols=True,
            ):
                markets_cached[key] = traverse_and_slim(value)

        _poly_cache.unload_cache()
        gc.collect()

        with self._market_cache_lock:
            self._all_markets_cache.update(markets_cached)

        self._build_asset_id_to_ticker_mapping()

    # ALREADY LOCKED WITH `_market_cache_lock` DO NOT use it inside with `_market_cache_lock` block
    def _build_asset_id_to_ticker_mapping(self):
        """
        Build a mapping of asset_id to ticker for a quick lookup when receiving market data updates.
        This should be called after the markets cache is updated.
        :return:
        """
        dict_asset_id_to_ticker = {}
        with self._market_cache_lock:
            for ticker, event in tqdm.tqdm(
                    self._all_markets_cache.items(),
                    desc="Building asset_id to ticker mapping",
                    unit="markets",
                    dynamic_ncols=True,
            ):
                markets = event.markets
                for index in range(len(markets)):
                    clobs: list[str] = markets[index].clobTokenIds
                    if clobs is None:
                        # logging.warning("Market %s has no clobTokenIds, skipping.", markets[index].slug)
                        continue
                    for clob_id in clobs:
                        # Store ticker and market index for later use in market data updates
                        dict_asset_id_to_ticker[clob_id] = (ticker, index)

        with self._market_cache_lock:
            self._asset_id_to_ticker.update(dict_asset_id_to_ticker)

    #######################################
    # Callbacks
    #######################################
    def _handle_incoming_packets(
            self, client_socket: socket.socket, address, data: bytes
    ):
        try:
            packets = decode_multiple_packets(data)
        except Exception as e:
            logging.error(
                "Failed to decode incoming data from client %s: %s. Data: %s",
                address,
                e,
                data,
            )
            response = {
                "action": None,
                "data": None,
                "error": f"Failed to decode incoming data: {str(e)}. YOU ARE NOT ENCODING PROPERLY OR YOU SENT MALFORMED DATA. Data must be encoded with the P1 protocol (JSON) and then P1 packet encoded. Original error: {str(e)}",
                "compressed": False,
            }
            response_bytes = encode_packet(json.dumps(response).encode("utf-8"))
            try:
                client_socket.sendall(response_bytes)
            except Exception as e:
                print_with_name(
                    "ERROR: Unable to send message {} to {} error={}",
                    response_bytes,
                    address,
                    e,
                )
            return

        for packet in packets:
            compressed = False
            content = json.loads(packet.decode("utf-8"))
            logging.debug("Received data from Polymarket client: %s", content)
            correlation_id = content.get(
                "correlation_id", None
            )  # Optional field for client-side tracking of requests/responses
            try:
                if correlation_id is not None:
                    self._correlation_id_checker.check_correlation_id(
                        correlation_id
                    )  # throws if invalid, caught by the exception below

                response = self._handle_client_message(client_socket, address, content)

                if isinstance(response, (dict, list)):
                    response_size = len(json.dumps(response).encode("utf-8"))
                    if response_size >= 9500:
                        response = compress(response)
                        compressed = True

                    if len(response) >= 9500:
                        raise Exception("Response size exceeds maximum allowed size even after compression.")


                msg = {
                    "action": content.get("action", None),
                    "data": response,
                    "error": None,
                    "compressed": compressed,
                }
                # because encoding can fail!
                if correlation_id is not None:
                    msg["correlation_id"] = correlation_id
                response_bytes = encode_packet(json.dumps(msg).encode("utf-8"))
            except Exception as e:
                throw_fuss(msg=traceback.format_exc(), notify=False)
                msg = {
                    "action": content.get("action", None),
                    "data": None,
                    "error": str(e),
                    "compressed": False,
                }
                if correlation_id is not None:
                    msg["correlation_id"] = correlation_id
                response_bytes = encode_packet(json.dumps(msg).encode("utf-8"))

            if self._configs["Show P1 Packets"]:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                if self._configs.get("Show packet timestamps", True):
                    print(
                        f"[{timestamp}] → ({len(response_bytes)} bytes): {response_bytes!r}"
                    )
                else:
                    print(
                        f"P1 Packet ({len(response_bytes)} bytes): {response_bytes!r}"
                    )

            try:
                client_socket.sendall(response_bytes)
            except Exception as e:
                print_with_name(
                    "ERROR: Unable to send message {} to {} error={}",
                    response_bytes,
                    address,
                    e,
                )

    def _on_fatal_error(self, error: dict):
        """
        Callback invoked by PolyRestAPI's fatal_decorator when a critical REST operation fails.
        Performs two actions:
            1. Logs the error to stdout via throw_fuss with notifications disabled (notify=False),
               since throw_fuss expects a str, not a dict — we format the error dict into a readable
               multi-line string containing the function name, exception, and traceback.
            2. Broadcasts a P1-encoded fatal_error message to all connected clients following the
               dispatcher's standard response format: {'action': 'fatal_error', 'data': ..., 'error': ...}.
               Dead sockets are cleaned up on send failure.

        :param error: Dict from fatal_decorator with keys:
            'function' (str), 'exception' (Exception), 'traceback' (str), 'args', 'kwargs', 'self'.
        :return:
        """
        func_name = error.get("function", "unknown")
        exception = error.get("exception", "unknown")
        tb = error.get("traceback", "")

        fuss_msg = (
            f"POLYMARKET DISPATCHER FATAL ERROR\n"
            f"Function: {func_name}\n"
            f"Exception: {exception}\n"
            f"Traceback:\n{tb}"
        )
        throw_fuss(fuss_msg, notify=False, title="Argus Polymarket Fatal Error")

        # Build a serializable error payload for clients (Exception objects are not JSON serializable)
        client_error_payload = {
            "function": func_name,
            "exception": str(exception),
            "traceback": tb,
        }

        error_packet = self.send_with_p1_encoding(
            {
                "action": "fatal_error",
                "data": client_error_payload,
                "error": str(exception),
                "compressed": False,
            }
        )

        for sock in self.sockets:
            try:
                with self.send_lock_for(sock):
                    sock.sendall(error_packet)
            except (ConnectionResetError, BrokenPipeError) as e:
                self.remove_socket(sock)
                print_with_name(
                    "Removed socket due to error while broadcasting fatal error:", e
                )
            except Exception as e:
                print_with_name(
                    "Unexpected error broadcasting fatal error to socket:", e
                )
                traceback.print_exc()

    def _order_book_update_callback(self, update: dict):
        """
        Callback for market data updates from Polymarket.
        :param update: The market data update. Contains asset_id as the key for the updated market.
        :return:
        """

        # The update dict from the order book WebSocket contains the asset_id as a key
        # alongside a 'timestamp' metadata key, e.g.:
        #   {'<asset_id>': {'bids': [...], 'asks': [...]}, 'timestamp': '177...'}
        # We filter out 'timestamp' to reliably extract the actual asset_id regardless
        # of dict key ordering.
        asset_keys = [k for k in update.keys() if k != "timestamp"]
        if len(asset_keys) != 1:
            logging.warning(
                "Unexpected keys in market data update (expected 1 asset_id + timestamp): %s",
                list(update.keys()),
            )
            return

        asset_id = asset_keys[0]
        with self._market_cache_lock:
            ticker_market_index = self._asset_id_to_ticker.get(asset_id, None)
            if ticker_market_index is None:
                logging.warning(
                    "Received market data update for unknown asset_id: %s, dict: %s",
                    asset_id,
                    update,
                )
                return
        ticker, market_index = ticker_market_index

        clients_to_send = []
        with self._lock:
            if asset_id in self._market_data_routing_table:
                clients_to_send = list(self._market_data_routing_table[asset_id])

        # If no clients are subscribed (e.g. last client disconnected between the
        # routing table read and this point), bail out early.  Continuing would
        # attempt to build a P2 packet from the update which can crash if the
        # message type (e.g., last_trade_price) doesn't carry full order book data.
        if not clients_to_send:
            # the rational for commeting this out is that the price_changes message
            # contains for clob's we did not sub for, so we will get a LOT of these warnings.
            # logging.warning("No clients subscribed to market data for asset_id: %s, this should not be possible.",
            #                 asset_id)
            return

        p2_obj = self.send_market_data_with_p2_encoding(
            market_data=update,
            ticker=ticker,
            market_slug=self._all_markets_cache[ticker].markets[market_index].slug,
            asset_id=asset_id,
        )

        # Print P2 packets if config is enabled
        if self._configs.get("Print P2 packets", False):
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            if self._configs.get("Show packet timestamps", True):
                print(f"[{timestamp}] → ({len(p2_obj)} bytes): {p2_obj!r}")
            else:
                print(f"P2 Packet ({len(p2_obj)} bytes): {p2_obj!r}")

        # Broadcast P2-encoded market data to all clients subscribed to this asset_id.
        # On sent failure (dead/disconnected client), remove the socket via remove_socket()
        # which cascades cleanup through the routing table and triggers subscription_expired
        # if no clients remain for a given clob_id.
        # NOTE: remove_socket() and friends are thread-safe (all guarded by self._lock),
        # so it is safe to call from this WSS callback thread.
        for sock in clients_to_send:
            try:
                # Per-client sendall lock prevents byte interleaving when multiple
                # WS shard threads broadcast to the same client concurrently.
                with self.send_lock_for(sock):
                    sock.sendall(p2_obj)
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                # OSError [Errno 9] Bad file descriptor occurs when the client
                # has already closed the socket but the routing table still holds
                # a reference to it (race between disconnect and this callback).
                self.remove_socket(sock)
                print_with_name(
                    "Removed dead socket while sending market data for asset_id %s: %s",
                    asset_id,
                    e,
                )
            except Exception as e:
                print_with_name(
                    "Unexpected error sending market data for asset_id %s to socket: %s",
                    asset_id,
                    e,
                )
                self.remove_socket(sock)
                traceback.print_exc()

        # Record WS-arrival → sendall latency (ms)
        recv_ts = getattr(self.market_data, "_last_msg_recv_ts", 0.0)
        if recv_ts:
            self._latency_samples.append((time.perf_counter() - recv_ts) * 1000)

    def _rtd_callback(self, object: P2ConvertClass):
        """
        The callback should get P2 classes from the RTDS already populated now its just about
        finding out who to cast to and blast
        """
        clients_to_send = []
        with self._lock:
            if self.rtds_magic_asset_id in self._market_data_routing_table:
                clients_to_send = list(self._market_data_routing_table[self.rtds_magic_asset_id])

        p2_packet = transmit_mkt_data_with_protocol_2(object)

        if self._configs.get("P2 Packets for RTDS", False):
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            if self._configs.get("Show packet timestamps", True):
                print(f"[{timestamp}] → ({len(p2_packet)} bytes): {p2_packet!r}")
            else:
                print(f"P2 Packet ({len(p2_packet)} bytes): {p2_packet!r}")

        for sock in clients_to_send:
            try:
                with self.send_lock_for(sock):
                    sock.sendall(p2_packet)
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                self.remove_socket(sock)
                print_with_name("Removed dead socket while sending market data for: ",
                                 object, "socket:", sock)
            except Exception as e:
                self.remove_socket(sock)
                print_with_name("Unexpected error sending market data for: ",
                                 object, "socket:", sock, "error:", e)
                traceback.print_exc()
        # this is not considered a WS-arrival so it will not count towards latency samples        
    
    def print_latency_stats(self):
        """Print WS-arrival → sock.sendall propagation latency percentiles (market data only)."""
        samples = sorted(self._latency_samples)
        if not samples:
            print_with_name("No latency samples yet.")
            return
        n = len(samples)
        p = lambda pct: samples[min(int(pct / 100 * n), n - 1)]
        print_with_name(
            f"Propagation latency ({n} samples) — "
            f"min={samples[0]:.3f}ms  p50={p(50):.3f}ms  "
            f"p95={p(95):.3f}ms  p99={p(99):.3f}ms  max={samples[-1]:.3f}ms"
        )

    @runAsThread
    def _latency_stats_loop(self, interval: int = 10):
        """Repeatedly print latency stats every `interval` seconds (runs in background thread)."""
        while True:
            self.print_latency_stats()
            time.sleep(interval)

    # noinspection PyProtectedMember
    def visualise_shards(self):
        """
        Display a visualization of all WebSocket shards, their states, load, and assets.
        """
        pool = self.market_data

        # Check if market_data is actually a pool (has sharding internals)
        if not hasattr(pool, "_shards"):
            print(
                "Shard visualization not available - market_data is not a PolyMarketOrderBookPool"
            )
            return

        # Header
        print("\n" + "=" * 65)
        print("                    SHARD VISUALIZATION")
        print("=" * 65)

        # Configuration
        print("\nPOOL CONFIGURATION")
        print(f"  Min Shards: {pool._min_shards}")
        print(f"  Max Shards: {pool._max_shards}")
        print(f"  Max Assets Per Shard: {pool._max_assets_per_shard}")

        # Gather shard info under lock for thread safety
        with pool._lock:
            shards = list(pool._shards)
            draining = dict(pool._draining)
            asset_to_shard = dict(pool._asset_to_shard)

        now = time.monotonic()

        # Per-shard details
        print("\n" + "-" * 65)
        for shard in shards:
            shard_idx = shard._shard_index
            roster_size = shard.roster_size
            roster = shard.roster_snapshot
            is_draining = shard in draining
            drain_remaining = None

            if is_draining:
                drain_elapsed = now - draining[shard]
                drain_remaining = max(0, pool._scale_down_idle_seconds - drain_elapsed)

            # Determine state
            if shard._internally_closed:
                state = "CLOSED"
            elif is_draining:
                state = f"DRAINING ({int(drain_remaining)}s)"
            else:
                state = "ACTIVE"

            # Full indicator
            full_indicator = (
                " [FULL]" if roster_size >= pool._max_assets_per_shard else ""
            )

            print(f"\nSHARD #{shard_idx} [{state}]{full_indicator}")
            print(f"  Load: {roster_size}/{pool._max_assets_per_shard} assets")

            # Assets
            if roster:
                print("  Assets:")
                for asset_id in roster:
                    # Truncate long asset IDs for readability
                    display_id = (
                        asset_id[:35] + "..." if len(asset_id) > 35 else asset_id
                    )
                    print(f"    • {display_id}")
            else:
                print("  Assets: (none)")

            # Health metrics
            ping_sent, ping_recv = shard._ping_pongs
            ping_delta = ping_sent - ping_recv
            last_msg_age = (
                time.perf_counter() - shard._last_msg_recv_ts
                if shard._last_msg_recv_ts > 0
                else None
            )

            print(f"  Health:")
            print(
                f"    Ping/Pong delta: {ping_delta} (sent: {ping_sent}, recv: {ping_recv})"
            )
            if last_msg_age is not None:
                print(f"    Last message: {last_msg_age:.2f}s ago")
            else:
                print(f"    Last message: (no messages yet)")

            if is_draining:
                print(f"  Note: Shard will close when drain timeout expires")

        # Summary
        print("\n" + "=" * 65)
        print("POOL SUMMARY")

        active_shards = [
            s for s in shards if not s._internally_closed and s not in draining
        ]
        draining_shards = list(draining.keys())
        total_assets = len(asset_to_shard)
        total_capacity = len(shards) * pool._max_assets_per_shard
        avg_load = total_assets / len(shards) if shards else 0
        utilization = (total_assets / total_capacity * 100) if total_capacity > 0 else 0

        print(f"  Active Shards: {len(active_shards)}")
        print(f"  Draining Shards: {len(draining_shards)}")
        print(f"  Total Assets: {total_assets}")
        print(f"  Average Load: {avg_load:.1f} assets per shard")
        print(f"  Capacity Utilization: {utilization:.1f}%")
        print("=" * 65 + "\n")

    #######################################
    # MAIN CLIENT MESSAGE HANDLER
    #######################################
    def _handle_client_message(self, sock: socket.socket, address: tuple[str, int], content: dict):

        def _inline_timer(result):
            if self._configs["show response times"]:
                print_with_name(
                    f"Handled client message in {result:.4f} seconds: {content}"
                )

        with Timer(_inline_timer):
            _ = address
            action = content.get("action", None)
            data = content.get("data", None)
            if action is None:
                raise InvalidArgumentError("Received message without action field.")

            functions_available = {
                # Market Data Subscriptions
                "subscribe": self._handle_subscribe,
                "subscribe_to_market_by_ticker": self._handle_subscribe_to_market_by_ticker,
                "unsubscribe": self._handle_unsubscribe,
                "unsubscribe_from_market_by_ticker": self._handle_unsubscribe_from_market_by_ticker,
                "orderbook_snapshot": self._handle_orderbook_snapshot,
                "rtds_subscribe": self._handle_rtds_subscribe,
                "rtds_unsubscribe": self._handle_rtds_unsubscribe,
                # Market Data Requests
                "fetch_all_markets": self._handle_fetch_all_markets,
                "fetch_all_tickers": self._handle_fetch_all_markets_ticker,
                "fetch_market_by_ticker": self._handle_fetch_market_by_ticker,
                "search_markets": self._handle_search_markets,
                "fetch_clob_id_information": self._fetch_clob_id_information,
                # Order Management
                "place_order": self._handle_place_order,
                "place_multiple_orders": self._handle_place_multiple_orders,
                "cancel_order": self._handle_cancel_order,
                "cancel_multiple_orders": self._handle_cancel_multiple_orders,
                "get_order_status": self._handle_get_order_status,
                "get_orders": self._handle_get_orders,
                "get_balance": self._handle_get_balance,
                "get_token_balance": self._handle_get_token_balance,
                "get_trades": self._handle_get_trades,
                # Crypto Utilities
                "get_price_to_beat": self._handle_get_price_to_beat,
                # Utilities
                "ping": self._handle_ping,
                "rtt_to_exchange": self._handle_rtt_to_exchange,
                'version': lambda *arg, **kwargs: ARGUS_VERSION,
            }

            func = functions_available.get(action, None)
            if func is None:
                raise InvalidArgumentError(
                    f"Unknown action '{action}' received from client."
                )

            args = data if data is not None else {}
            if func is not None:
                # noinspection all
                response = func(args_obj=ArgsObject(sock, args))

            return response

    def _account_update_callback(self, update: OrderEvent):
        """
        Callback for account updates from Polymarket.
        :param update: The order event update.
        :return:
        """
        obj = self.send_with_p1_encoding(
            {"action": "account_update", "data": update.to_dict(), "error": None, "compressed": False}
        )

        for sock in self.sockets:
            try:
                with self.send_lock_for(sock):
                    sock.sendall(obj)
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                self.remove_socket(sock)
                print_with_name(
                    "Removed socket due to error while sending account update:", e
                )
            except Exception as e:
                print_with_name("Error while sending account update to socket:", e)
                print_with_name("THIS SHOULD NOT HAPPEN, INVESTIGATE!")
                traceback.print_exc()

    ########################################
    # Subscription
    ########################################
    def subscription_expired(self, clob_id):
        """
        Handle subscription expiration logic.
        :param clob_id:
        :return:
        """
        if clob_id == self.rtds_magic_asset_id:
            return
        self.market_data.unsubscribe_from_asset_id(clob_id)

    def _warm_clob_caches_for_subscribed_asset(self, clob_id: str) -> None:
        """
        Pre-populate py-clob-client-v2's per-token caches (tick_size, neg_risk,
        condition_id) from data already present in _all_markets_cache. This avoids
        the GET /tick-size HTTP round-trip that would otherwise fire on the first
        build_order for this token (see PolyRestAPI.warm_clob_caches_for_token).

        Best-effort: if the market is not yet in cache, or the tick_size field is
        missing, we silently skip — the order build path will fall back to its
        normal (slower) HTTP fetch.
        """
        with self._market_cache_lock:
            ticker_market_index = self._asset_id_to_ticker.get(clob_id)
            if ticker_market_index is None:
                return
            ticker, market_index = ticker_market_index
            event = self._all_markets_cache.get(ticker)

        if event is None or not event.markets or market_index >= len(event.markets):
            return

        market = event.markets[market_index]
        tick_size = market.orderPriceMinTickSize
        if tick_size is None:
            return

        # py-clob's ROUNDING_CONFIG is keyed on the strings "0.1" / "0.01" / "0.001" /
        # "0.0001"; format with :g to drop trailing zeros and avoid scientific notation
        # for the supported range.
        tick_size_str = format(float(tick_size), "g")

        neg_risk = event.negRisk if event.negRisk is not None else market.negRisk
        condition_id = market.conditionId

        try:
            self.rest_api.warm_clob_caches_for_token(
                token_id=clob_id,
                tick_size=tick_size_str,
                neg_risk=bool(neg_risk),
                condition_id=condition_id,
            )
        except Exception as e:
            # Warming is purely an optimization — never let it fail the subscribe.
            print_with_name(
                "warm_clob_caches_for_token failed for {}: {}".format(clob_id, e)
            )

    def _handle_subscribe(self, args_obj: ArgsObject):
        """
        Handle subscription request from a client.
        :param args_obj: ArgsObject containing the socket and arguments.
            Args is expected to be a list of clob_ids to subscribe to.
        :return:
        """
        sock = args_obj.sock
        self.add_socket(sock)
        subscribed = []
        failed = []
        for clob_id in args_obj.args:
            try:
                self.add_socket_to_subscription(sock, clob_id)
                self.market_data.subscribe_to_asset_id(clob_id)
                # Pre-warm py-clob's internal caches so the next build_order for this
                # token skips its tick_size HTTP fetch.
                self._warm_clob_caches_for_subscribed_asset(clob_id)
                subscribed.append(clob_id)
            except Exception as e:
                failed.append(clob_id)
                print_with_name(
                    "Error subscribing to clob_id {}: {}".format(clob_id, e)
                )
                traceback.print_exc()

        return {"subscribed": subscribed, "failed": failed}

    def _handle_unsubscribe(self, args_obj: ArgsObject):
        """
        Handle unsubscription request from a client.
        :param args_obj: ArgsObject containing the socket and arguments.
            Args is expected to be a list of clob_ids to unsubscribe from.
        :return:
        """
        sock = args_obj.sock
        unsubscribed = []
        failed = []
        for clob_id in args_obj.args:
            try:
                self.remove_socket_from_subscription(sock, clob_id)
                unsubscribed.append(clob_id)
            except Exception as e:
                failed.append(clob_id)
                print_with_name(
                    "Error unsubscribing from clob_id {}: {}".format(clob_id, e)
                )
                traceback.print_exc()

        return {"unsubscribed": unsubscribed, "failed": failed}

    def _handle_subscribe_to_market_by_ticker(self, args_obj: ArgsObject):
        """
        Subscribe to all clob_ids for a market identified by its ticker.
        Note: This subscribes to all submarkets of the event that may not be desired
        use `_handle_subscribe` with specific clob_ids for more granular control.
        :param args_obj: Where args_obj.args[0] is expected to be the ticker string of the market to subscribe to.
        :return:
        """

        sock = args_obj.sock
        self.add_socket(sock)
        ticker = args_obj.args[0]
        market = self._all_markets_cache.get(ticker, None)
        if market is None:
            if self._fetch_on_cache_miss:
                market = self._fetch_event_on_cache_miss(ticker)
            else:
                raise PolyMarketDispatcherError(
                    f"Market with ticker '{ticker}' not found for subscription."
                )

        subscribed = []
        failed = []
        for market_index in range(len(market.markets)):
            clobs: list[str] = market.markets[market_index].clobTokenIds
            if clobs is None:
                logging.warning(
                    "Market %s has no clobTokenIds, skipping subscription for this submarket.",
                    market.markets[market_index].slug,
                )
                continue
            for clob_id in clobs:
                try:
                    self.add_socket_to_subscription(sock, clob_id)
                    self.market_data.subscribe_to_asset_id(clob_id)
                    # Pre-warm py-clob's internal caches so the next build_order for this
                    # token skips its tick_size HTTP fetch.
                    self._warm_clob_caches_for_subscribed_asset(clob_id)
                    subscribed.append(clob_id)
                except Exception as e:
                    failed.append(clob_id)
                    print_with_name(
                        "Error subscribing to clob_id {}: {}".format(clob_id, e)
                    )
                    traceback.print_exc()

        return {"subscribed": subscribed, "failed": failed}

    def _handle_unsubscribe_from_market_by_ticker(self, args_obj: ArgsObject):
        """
        Unsubscribe from all clob_ids for a market identified by its ticker.
         Note: This unsubscribes from all submarkets of the event that may not be desired
            use `_handle_unsubscribe` with specific clob_ids for more granular control.
        :param args_obj:
        :return:
        """

        sock = args_obj.sock
        ticker = args_obj.args[0]
        market = self._all_markets_cache.get(ticker, None)
        if market is None:
            raise PolyMarketDispatcherError(
                f"Market with ticker '{ticker}' not found for unsubscription."
            )

        unsubscribed = []
        failed = []
        for market_index in range(len(market.markets)):
            clobs: list[str] = market.markets[market_index].clobTokenIds
            if clobs is None:
                logging.warning(
                    "Market %s has no clobTokenIds, skipping unsubscription for this submarket.",
                    market.markets[market_index].slug,
                )
                continue
            for clob_id in clobs:
                try:
                    self.remove_socket_from_subscription(sock, clob_id)
                    unsubscribed.append(clob_id)
                except Exception as e:
                    failed.append(clob_id)
                    print_with_name(
                        "Error unsubscribing from clob_id {}: {}".format(clob_id, e)
                    )
                    traceback.print_exc()

        return {"unsubscribed": unsubscribed, "failed": failed}

    def _handle_rtds_subscribe(self, args_obj: ArgsObject):
        """
        Subscribe a client socket to RTDS (Real-Time Data Streams).
        :param args_obj: ArgsObject containing the socket.
        :return:
        """
        sock = args_obj.sock
        self.add_socket(sock)
        try:
            self.add_socket_to_subscription(sock, self.rtds_magic_asset_id)
            return {"subscribed": [self.rtds_magic_asset_id], "failed": []}
        except Exception as e:
            print_with_name("Error in RTDS subscribe: {}".format(e))
            traceback.print_exc()
            return {"subscribed": [], "failed": [str(e)]}

    def _handle_rtds_unsubscribe(self, args_obj: ArgsObject):
        """
        Unsubscribe a client socket from RTDS.
        :param args_obj: ArgsObject containing the socket.
        :return:
        """
        sock = args_obj.sock
        try:
            self.remove_socket_from_subscription(sock, self.rtds_magic_asset_id)
            return {"unsubscribed": [self.rtds_magic_asset_id], "failed": []}
        except Exception as e:
            print_with_name("Error in RTDS unsubscribe: {}".format(e))
            traceback.print_exc()
            return {"unsubscribed": [], "failed": [str(e)]}

    ########################################
    # Market Data Requests
    #########################################

    # Warning: This is a chunky method it sends a LOT of data,
    # use `_handle_fetch_all_markets_ticker` instead if possible
    def _handle_fetch_all_markets(self, args_obj: ArgsObject):
        """
        Handle request to fetch all markets.
        :param args_obj: ArgsObject containing the socket and arguments.
            Args is expected to be empty.
        :return:
        """
        _ = args_obj
        markets = self._all_markets_cache
        return [market.to_dict() for market in markets.values()]

    def _handle_fetch_all_markets_ticker(self, args_obj: ArgsObject):
        """
        Handle request to fetch all market tickers.
        :param args_obj: ArgsObject containing the socket and arguments.
            Args[0] is expected to be the limit of tickers to return (optional, default 100).
            Args[1] is expected to be the offset for pagination (optional, default 0).
        :return:
        """
        _ = args_obj
        markets = self._all_markets_cache

        offset = 0
        limit = 100
        if len(args_obj.args) > 1:
            limit = int(args_obj.args[0])
            offset = int(args_obj.args[1])
        elif len(args_obj.args) > 0:
            limit = int(args_obj.args[0])

        items = list(markets.keys())
        max_items = len(items)

        max_limit = min(limit, max_items)
        if offset >= max_items:
            return []

        return items[offset: offset + max_limit]

    def _handle_fetch_market_by_ticker(self, args_obj: ArgsObject):
        """
        Handle a request to fetch a market by ticker.
        :param args_obj: ArgsObject containing the socket and arguments.
            [0] it is expected to be the ticker string.
        :return:
        """
        try:
            ticker = args_obj.args[0]
        except IndexError:
            raise InvalidArgumentError(
                "Ticker argument is required for fetch_market_by_ticker."
            )
        market = self._all_markets_cache.get(ticker, None)
        if market is None:
            raise PolyMarketDispatcherError(f"Market with ticker '{ticker}' not found.")
        return market.to_dict()

    def _handle_search_markets(self, args_obj: ArgsObject):
        """
        Handle a request to search markets by keyword.
        :param args_obj: ArgsObject containing the socket and arguments.
            [0] is expected to be the search keyword string.
            [1] optional is the limit of results to return (default 10).

        Returns only the tickers of matching markets.
        :return:
        """
        sorted_markets = sorted(
            self._all_markets_cache.keys(),
            key=lambda x: difflib.SequenceMatcher(None, args_obj.args[0], x).ratio(),
            reverse=True,
        )
        limit = 10
        if len(args_obj.args) > 1:
            try:
                limit = int(args_obj.args[1])
            except ValueError:
                pass
        return sorted_markets[:limit]

    def _handle_orderbook_snapshot(self, args_obj: ArgsObject):
        """
        Trigger an orderbook snapshot for a given clob_id(s). The data will come over the normal
        P2 channels. This is from a cache, NOT a live request to the CLOB. The endpoint
        is designed for stale markets that are already SUBSCRIBED to get a snapshot on demand.
        The endpoint will trigger a push of the latest order book with timestamp 0, which clients
        can identify as an on-demand snapshot.

        :param args_obj: Arg[0...n] of args_obj is expected to be the clob_id to fetch the snapshot for.
        """

        clobs = args_obj.args
        successful = []
        failed = []
        for clob_id in clobs:
            try:
                self._order_book_update_callback(
                    {
                        clob_id: self.market_data.order_book_for_asset_id(
                            asset_id=clob_id
                        ),
                        "timestamp": 0,  # Clients can identify this as a snapshot by the timestamp of 0
                    }
                )
                successful.append(clob_id)
            except Exception as e:
                failed.append(clob_id)
                print_with_name(
                    "Error triggering orderbook snapshot for clob_id {}: {}".format(
                        clob_id, e
                    )
                )
                traceback.print_exc()

        return {"successful": successful, "failed": failed}


    def _fetch_clob_id_information(self, args_obj: ArgsObject):
        """
        Gets information about a clob_id by querying the internal market cache.
        :param args_obj: [0] of args_obj is expected to be the clob_id to fetch information for.
        :return:
        """

        clob_id = args_obj.args[0]
        event = self._resolve_market_from_token_id(clob_id)
        # Find the market and outcome associated with this clob_id
        for market in event.markets:
            if market.clobTokenIds and clob_id in market.clobTokenIds:
                outcome_index = market.clobTokenIds.index(clob_id)
                if market.outcomes and isinstance(market.outcomes, list):
                    outcome = market.outcomes[outcome_index]
                else:
                    outcome = None

                aot_symbol_from_p2 = P2ConvertClass(
                    ticker=event.ticker,
                    market_slug=market.slug,
                    asset_id=clob_id,
                    market_data={},
                    order_book_depth=0,
                )

                return {
                    "event_name": event.title,
                    "market_name": market.question,
                    "outcome": outcome,
                    "ticker": event.ticker,
                    "market_slug": market.slug,
                    "aot_p2_symbol": aot_symbol_from_p2.symbol,
                }

        raise PolyMarketDispatcherError(
            f"clob_id '{clob_id}' not found in any market outcomes."
        )

    def _handle_get_price_to_beat_inner(self, args_obj: ArgsObject):
        """
        Handle request to get the price to beat for an Up/Down market.

        This method implements a dual-strategy approach to fetch the price to beat, ensuring maximum
        reliability by attempting multiple methods in sequence. Both methods MUST be tried before
        returning an error to the user.

        DUAL METHOD STRATEGY:
        --------------------

        METHOD 1 - Frontend HTML Scraper (Primary):
            Uses UnsafePolyMarket.get_price_to_beat(slug) to scrape the price directly from
            Polymarket's frontend HTML. This method:
            - Makes an HTTP GET request to https://polymarket.com/event/{market_slug}
            - Parses the embedded JSON in the HTML page props to extract 'openPrice'
            - Is more stable as it doesn't require forging API tokens or special headers
            - Benefits from frontend caching via @_unsafe_api_cache.cache_decorator
            - May fail if Polymarket changes their HTML structure or if the market slug is invalid

        METHOD 2 - Crypto Price API (Fallback):
            Uses UnsafePolyMarket.build_crypto_price_url_and_get_price() as a fallback when
            the scraper fails. This method:
            - Extracts metadata from the market (crypto symbol, variant, start/end dates)
            - Builds a direct API URL to Polymarket's crypto price endpoint
            - Returns the 'priceToBeat' field from the JSON response
            - Requires proper parsing of market metadata from the ticker and resolution source
            - Validates all required parameters before making the API call

        METADATA EXTRACTION:
        -------------------
        The fallback method requires extracting the following from market metadata:
        - Symbol: BTC, ETH, SOL (extracted from ticker or resolutionSource URL)
        - Variant: 'fifteen', 'hourly', or 'daily' (parsed from ticker pattern like '15m', 'hour', etc.)
        - Start Date: Event start time from market.eventStartTime or market.startDate
        - End Date: Market end time from market.endDate

        EXECUTION FLOW:
        --------------
        1. Validate ticker argument
        2. Look up market in cache to get metadata
        3. Attempt METHOD 1 (scraper)
        4. If METHOD 1 fails, capture error and proceed to METHOD 2
        5. If METHOD 2 succeeds, return price; otherwise return combined error

        The input expected from the user is the ticker of the market, for example:
        "bitcoin-up-or-down-february-10-4pm-et" or "btc-updown-15m-1769111100"

        :param args_obj: ArgsObject containing the socket and arguments.
            args_obj.args[0] is expected to be the market ticker string.
        :return: float representing the price to beat
        :raises InvalidArgumentError: If ticker argument is missing
        :raises PolyMarketDispatcherError: If market not found or both methods fail
        """
        # Extract the ticker from the arguments
        try:
            ticker = args_obj.args[0]
        except IndexError:
            raise InvalidArgumentError(
                "Ticker argument is required for get_price_to_beat."
            )

        # Look up the market in the cache to get metadata
        with self._market_cache_lock:
            market_event = self._all_markets_cache.get(ticker, None)

        if market_event is None:
            raise PolyMarketDispatcherError(f"Market with ticker '{ticker}' not found.")

        # Get the market slug from the first market in the event
        # Most Up/Down events have a single market, so we use index 0
        if not market_event.markets or len(market_event.markets) == 0:
            raise PolyMarketDispatcherError(f"Market '{ticker}' has no submarkets.")

        market = market_event.markets[0]
        market_slug = market.slug

        # Check if market_slug is available
        if market_slug is None:
            raise PolyMarketDispatcherError(f"Market '{ticker}' has no slug defined.")

        # METHOD 1: Try the scraper first (get_price_to_beat using market slug)
        scraper_error = None
        try:
            price = self.unsafe_api.get_price_to_beat(market_slug)
            if price is not None:
                return price
        except UnableToReachPolymarket as e:
            scraper_error = str(e)
            logging.warning(
                f"Scraper method failed for ticker '{ticker}': {scraper_error}"
            )
        except Exception as e:
            scraper_error = str(e)
            logging.warning(
                f"Unexpected error in scraper method for ticker '{ticker}': {scraper_error}"
            )

        # METHOD 2: Fall back to crypto price API if scraper failed
        # We need to extract metadata from the market to build the API call
        try:
            # Extract symbol from ticker or resolution source
            symbol = self._extract_crypto_symbol(ticker, market_event)

            # Get start and end dates from market metadata FIRST
            # (needed for variant calculation)
            start_date = self._extract_start_date(market)
            end_date = self._extract_end_date(market)

            # Extract variant (fifteen, hourly, daily) from market duration
            variant = self._extract_variant(start_date, end_date)

            if symbol and variant and start_date and end_date:
                price = self.unsafe_api.build_crypto_price_url_and_get_price(
                    symbol=symbol,
                    variant=variant,
                    start_date=start_date,
                    end_date=end_date,
                )
                if price is not None:
                    return price
            else:
                missing = []
                if not symbol:
                    missing.append("symbol")
                if not variant:
                    missing.append("variant")
                if not start_date:
                    missing.append("start_date")
                if not end_date:
                    missing.append("end_date")
                raise PolyMarketDispatcherError(
                    f"Cannot use crypto price API for ticker '{ticker}': missing {', '.join(missing)}"
                )

        except UnableToReachPolymarket as e:
            # Both methods failed - return comprehensive error
            raise PolyMarketDispatcherError(
                f"Failed to get price to beat for ticker '{ticker}'. "
                f"Scraper error: {scraper_error}. "
                f"Crypto API error: {str(e)}"
            )
        except Exception as e:
            # Unexpected error in fallback method
            raise PolyMarketDispatcherError(
                f"Failed to get price to beat for ticker '{ticker}'. "
                f"Scraper error: {scraper_error}. "
                f"Crypto API error: {str(e)}"
            )

    def _handle_get_price_to_beat(self, args_obj: ArgsObject):
        """
        Wrapper for _handle_get_price_to_beat_inner to add retry logic
        """
        max_tries = 10
        for attempt in range(1, max_tries + 1):
            time.sleep(attempt * 0.5)
            logging.info(
                f"Attempt {attempt} to get price to beat for ticker '{args_obj.args[0]}'"
            )
            try:
                return self._handle_get_price_to_beat_inner(args_obj)
            except PolyMarketDispatcherError as e:
                logging.warning(f"Attempt {attempt} to get price to beat failed: {e}")
                if attempt >= max_tries:
                    raise e

        logging.warning(
            "This code block should never be reached due to the retry logic, investigate if it is. pos=_handle_get_price_to_beat"
        )
        return None

    @staticmethod
    def _extract_crypto_symbol(ticker: str, market_event) -> str:
        """
        Extract the crypto symbol (e.g., 'BTC', 'ETH') from the ticker or resolution source.

        :param ticker: The market ticker string
        :param market_event: The PolymarketEvent object
        :return: Uppercase crypto symbol or None if cannot extract
        """
        # Map of common crypto abbreviations in tickers to symbols
        crypto_map = {
            "btc": "BTC",
            "bitcoin": "BTC",
            "eth": "ETH",
            "ethereum": "ETH",
            "sol": "SOL",
            "solana": "SOL",
            "xrp": "XRP",
            "ripple": "XRP",
        }

        # Try to extract from ticker first (e.g., "btc-updown-15m-1769111100")
        ticker_lower = ticker.lower()
        for key, symbol in crypto_map.items():
            if key in ticker_lower:
                return symbol

        # Try to extract from resolution source (e.g., "https://data.chain.link/streams/btc-usd")
        resolution_source = market_event.resolutionSource or ""
        if "btc" in resolution_source.lower():
            return "BTC"
        elif "eth" in resolution_source.lower():
            return "ETH"
        elif "sol" in resolution_source.lower():
            return "SOL"

        return None

    @staticmethod
    def _extract_variant(start_date, end_date) -> str:
        """
        Calculate the variant type (fifteen, hourly, daily) from the market duration.

        Instead of parsing human-readable slugs like "bitcoin-up-or-down-february-10-5pm-et",
        we calculate the duration between start and end dates to determine the market type.

        :param start_date: The market start datetime
        :param end_date: The market end datetime
        :return: Variant string ('fifteen', 'hourly', 'daily') or None if cannot determine
        """
        if start_date is None or end_date is None:
            return None

        try:
            # Calculate duration
            duration = end_date - start_date
            duration_minutes = duration.total_seconds() / 60

            # Determine variant based on duration

            # 5-minute markets: ~5 minutes
            if 3 <= duration_minutes <= 7:
                return "fiveminute"

            # 15-minute markets: ~15 minutes
            if 10 <= duration_minutes <= 20:
                return "fifteen"

            # Hourly markets: ~60 minutes (with some tolerance)
            if 50 <= duration_minutes <= 70:
                return "hourly"

            # Daily markets: ~24 hours (1440 minutes)
            if 1380 <= duration_minutes <= 1500:
                return "daily"

            # Log warning for unclassified durations
            logging.warning(
                f"Could not determine variant for duration of {duration_minutes:.1f} minutes"
            )
            return None

        except (TypeError, AttributeError) as e:
            logging.warning(f"Error calculating variant from dates: {e}")
            return None

    @staticmethod
    def _extract_start_date(market):
        """
        Extract the start datetime from the market metadata.

        :param market: The Market object
        :return: datetime object or None
        """
        # Try eventStartTime first, then startDate, then startDateIso
        date_str = market.eventStartTime or market.startDate or market.startDateIso

        if date_str:
            try:
                # Parse ISO format datetime string
                return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        return None

    @staticmethod
    def _extract_end_date(market):
        """
        Extract the end datetime from the market metadata.

        :param market: The Market object
        :return: datetime object or None
        """
        # Try endDate first, then endDateIso
        date_str = market.endDate or market.endDateIso

        if date_str:
            try:
                # Parse ISO format datetime string
                return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        return None

    ########################################
    # Order Management
    ########################################

    def _resolve_market_from_token_id(self, token_id: str) -> PolymarketEvent:
        """
        Resolves a token_id (asset_id / clob_id) to its parent PolymarketEvent using the
        dispatcher's internal caches. The lookup path is:
            token_id -> _asset_id_to_ticker[token_id] -> (ticker, market_index)
                     -> _all_markets_cache[ticker] -> PolymarketEvent

        This is required because the REST API's place_order method needs a full PolymarketEvent
        object (for negRisk and other market metadata), but clients only send a token_id.
        Both caches are protected by _market_cache_lock.

        :param token_id: The asset_id / clob_id identifying a specific market outcome.
        :return: The PolymarketEvent object associated with this token_id.
        :raises InvalidArgumentError: If the token_id is not found in the asset-to-ticker mapping.
        :raises PolyMarketDispatcherError: If the resolved ticker is not found in the markets cache.
        """
        with self._market_cache_lock:
            ticker_market_index = self._asset_id_to_ticker.get(token_id, None)
        if ticker_market_index is None:
            raise InvalidArgumentError(
                f"token_id '{token_id}' not found in asset-to-ticker mapping. "
                f"The market may not exist or the cache may not have refreshed yet."
            )
        ticker, _ = ticker_market_index

        with self._market_cache_lock:
            market = self._all_markets_cache.get(ticker, None)
        if market is None:
            raise PolyMarketDispatcherError(
                f"Market with ticker '{ticker}' resolved from token_id '{token_id}' "
                f"was not found in markets cache."
            )
        return market

    def _handle_place_order(self, args_obj: ArgsObject):
        """
        Handle an order placement request from a client. Resolves the token_id to its parent
        PolymarketEvent from the internal cache, then delegates to the REST API's place_order.

        :param args_obj: ArgsObject containing the socket and arguments.
            Args is expected to be a dict with:
                'token_id' (str): The asset_id / clob_id to trade.
                'price' (float): The price at which to place the order.
                'size' (float): The size (number of contracts) of the order.
                'side' (str): The side of the order ('buy' or 'sell').
                'order_type' (str, optional): The order type, defaults to 'GTC'.
                    Accepted values match py_clob_client.OrderType enum names.
        :return: Dict from the CLOB API, e.g.:
            {'errorMsg': '', 'orderID': '0x...', 'takingAmount': '', 'makingAmount': '',
             'status': 'live', 'success': True}
        """

        args = args_obj.args
        token_id = args.get("token_id", None)
        if token_id is None:
            raise InvalidArgumentError("'token_id' is required for place_order.")

        price = args.get("price", None)
        if price is None:
            raise InvalidArgumentError("'price' is required for place_order.")

        size = args.get("size", None)
        if size is None:
            raise InvalidArgumentError("'size' is required for place_order.")

        side = args.get("side", None)
        if side is None:
            raise InvalidArgumentError("'side' is required for place_order.")

        market = self._resolve_market_from_token_id(token_id)
        tick_size = self.market_data.get_tick_size(asset_id=token_id)

        if self._configs["Block Order Execution"]:
            raise OrderExecutionDisabledError(
                "Order execution is currently blocked by server configuration."
            )

        result = self.rest_api.place_order(
            token_id=token_id,
            market=market,
            price=float(price),
            size=float(size),
            side=str(side),
            tick_size=tick_size,
        )
        return result

    def _handle_place_multiple_orders(self, args_obj: ArgsObject):
        """
        Handle multiple order placements in a single request. Expects a list of orders in the arguments,
        where each order contains the same fields as required by _handle_place_order. This method builds
        all orders concurrently using a thread pool, then places them as a batch via the REST API.

        :param args_obj: ArgsObject containing the socket and arguments.
            Args is expected to be a dict with:
                'orders' (list): A list of order dicts, each containing:
                    'token_id' (str): The asset_id / clob_id to trade.
                    'price' (float): The price at which to place the order.
                    'size' (float): The size (number of contracts) of the order.
                    'side' (str): The side of the order ('buy' or 'sell').
        :return: Dict from the CLOB API containing the batch order placement results.
        """
        from argus.polymarket_direct.rest import _tick

        _tick("_handle_place_multiple_orders", "start")

        _tick("_handle_place_multiple_orders", "after_block_check")

        args = args_obj.args
        _tick("_handle_place_multiple_orders", "after_get_args")
        orders_list = args.get("orders", [])
        _tick("_handle_place_multiple_orders", "after_get_orders_list")

        if not orders_list:
            raise InvalidArgumentError(
                "'orders' list is required and cannot be empty for place_multiple_orders."
            )
        _tick("_handle_place_multiple_orders", "after_check_orders_list_empty")

        if not isinstance(orders_list, list):
            raise InvalidArgumentError("'orders' must be a list of order dictionaries.")
        _tick("_handle_place_multiple_orders", "after_check_orders_list_type")

        # Validate each order and resolve markets first (sequential since it uses cache)
        order_specs = []
        _tick("_handle_place_multiple_orders", "before_order_specs_loop")
        for order in orders_list:
            _tick("_handle_place_multiple_orders", "loop_start")
            token_id = order.get("token_id", None)
            _tick("_handle_place_multiple_orders", "after_get_token_id")
            if token_id is None:
                raise InvalidArgumentError("Each order must have a 'token_id' field.")
            _tick("_handle_place_multiple_orders", "after_check_token_id")

            price = order.get("price", None)
            _tick("_handle_place_multiple_orders", "after_get_price")
            if price is None:
                raise InvalidArgumentError("Each order must have a 'price' field.")
            _tick("_handle_place_multiple_orders", "after_check_price")

            size = order.get("size", None)
            _tick("_handle_place_multiple_orders", "after_get_size")
            if size is None:
                raise InvalidArgumentError("Each order must have a 'size' field.")
            _tick("_handle_place_multiple_orders", "after_check_size")

            side = order.get("side", None)
            _tick("_handle_place_multiple_orders", "after_get_side")
            if side is None:
                raise InvalidArgumentError("Each order must have a 'side' field.")
            _tick("_handle_place_multiple_orders", "after_check_side")

            market = self._resolve_market_from_token_id(token_id)
            _tick("_handle_place_multiple_orders", "after_resolve_market")
            tick_size = self.market_data.get_tick_size(asset_id=token_id)
            _tick("_handle_place_multiple_orders", "after_get_tick_size")
            order_specs.append(
                {
                    "token_id": token_id,
                    "market": market,
                    "price": float(price),
                    "size": float(size),
                    "side": str(side),
                    "tick_size": tick_size,
                }
            )
            _tick("_handle_place_multiple_orders", "after_append_order_spec")
        _tick("_handle_place_multiple_orders", "after_order_specs_loop")

        # Build orders concurrently using thread pool since build_order involves HTTP requests
        built_orders = []
        _tick("_handle_place_multiple_orders", "after_built_orders_init")
        build_errors = []
        _tick("_handle_place_multiple_orders", "after_build_errors_init")

        start_time = time.time()
        _tick("_handle_place_multiple_orders", "after_start_time")
        # Reuse the dispatcher's persistent build pool — see __init__ for the
        # rationale. We deliberately do NOT wrap this in a `with` block, since the
        # pool is shared across calls and owned by the dispatcher.
        future_to_order = {
            self._build_pool.submit(
                self.rest_api.build_order,
                spec["token_id"],
                spec["market"],
                spec["price"],
                spec["size"],
                spec["side"],
                tick_size=spec["tick_size"],
            ): spec
            for spec in order_specs
        }
        _tick("_handle_place_multiple_orders", "after_submit_futures")

        for future in as_completed(future_to_order):
            _tick("_handle_place_multiple_orders", "future_loop_start")
            spec = future_to_order[future]
            _tick("_handle_place_multiple_orders", "after_get_spec")
            try:
                _tick("_handle_place_multiple_orders", "before_future_result")
                signed_order = future.result()
                _tick("_handle_place_multiple_orders", "after_future_result")
                built_orders.append(signed_order)
                _tick("_handle_place_multiple_orders", "after_append_built_order")
            except Exception as e:
                _tick("_handle_place_multiple_orders", "exception_caught")
                build_errors.append({"token_id": spec["token_id"], "error": str(e)})
                _tick("_handle_place_multiple_orders", "after_append_build_error")
        _tick("_handle_place_multiple_orders", "after_threadpool_done")

        if build_errors:
            raise PolyMarketDispatcherError(
                f"Failed to build {len(build_errors)} order(s): {build_errors}"
            )
        _tick("_handle_place_multiple_orders", "after_build_errors_check")

        if not built_orders:
            raise PolyMarketDispatcherError("No orders were built successfully.")
        _tick("_handle_place_multiple_orders", "after_built_orders_check")

        logging.info(
            colored(
                f"Successfully Built {len(built_orders)} orders in {time.time() - start_time:.4f} seconds. Placing batch order now.",
                "yellow",
            )
        )
        _tick("_handle_place_multiple_orders", "after_build_log")
        time_two = time.time()
        _tick("_handle_place_multiple_orders", "before_place_built_orders")
        print_with_name("Submitting orders at:", time.time_ns())
        if self._configs["Block Order Execution"]:
            raise OrderExecutionDisabledError(
                "Order execution is currently blocked by server configuration."
            )
        build_end_time = time.time()
        result = self.rest_api.place_built_orders(built_orders)
        _tick("_handle_place_multiple_orders", "after_place_built_orders")
        logging.info(
            colored(
                f"Batch order placement completed in {time.time() - time_two:.4f} seconds.",
                "yellow",
            )
        )
        _tick("_handle_place_multiple_orders", "after_place_log")
        self.async_write_log(
            json.dumps(
                {
                    "event": "place_multiple_orders",
                    "num_orders": len(built_orders),
                    "build_time_seconds": build_end_time - start_time,
                    "place_time_seconds": time.time() - time_two,
                    "result": result,
                }
            )
        )
        _tick("_handle_place_multiple_orders", "after_async_write_log")
        return result

    def _handle_cancel_order(self, args_obj: ArgsObject):
        """
        Handle an order cancellation request from a client. Delegates directly to the
        REST API's cancel_order with the provided order_id.

        :param args_obj: ArgsObject containing the socket and arguments.
            Args is expected to be a dict with:
                'order_id' (str): The ID of the order to cancel.
        :return: Dict from the CLOB API, e.g.:
            {'not_canceled': {}, 'canceled': ['0x...']}
            or failure example:
            {"canceled": Array [], "not_canceled": Object {"0x..": String("order can't be found - already canceled or matched")}}

        """
        args = args_obj.args
        order_id = args.get("order_id", None)
        if order_id is None:
            raise InvalidArgumentError("'order_id' is required for cancel_order.")

        result = self.rest_api.cancel_order(order_id=str(order_id))
        return result

    def _handle_cancel_multiple_orders(self, args_obj: ArgsObject):
        """
        Handle a batch order cancellation request from a client. Delegates to the
        REST API's cancel_multiple to cancel multiple orders in a single HTTP POST request.

        :param args_obj: ArgsObject containing the socket and arguments.
            Args is expected to be a dict with:
                'order_ids' (list[str]): List of order IDs to cancel.
        :return: Dict from the CLOB API, e.g.:
            {
                'not_canceled': {'0x...': 'order can\'t be found - already canceled or matched'},
                'canceled': ['0x...', '0x...']
            }

        """
        args = args_obj.args
        order_ids = args.get("order_ids", None)
        if order_ids is None:
            raise InvalidArgumentError(
                "'order_ids' is required for cancel_multiple_orders."
            )
        if not isinstance(order_ids, list):
            raise InvalidArgumentError("'order_ids' must be a list of order IDs.")
        if len(order_ids) == 0:
            raise InvalidArgumentError("'order_ids' cannot be empty.")

        result = self.rest_api.cancel_multiple(order_ids=order_ids)
        return result

    def _handle_get_order_status(self, args_obj: ArgsObject):
        """
        Handle a request to get the status of a specific order. Delegates to the REST API's
        get_order_status and serializes the resulting PolyMarketOrder dataclass to a dict.

        :param args_obj: ArgsObject containing the socket and arguments.
            Args is expected to be a dict with:
                'order_id' (str): The ID of the order to query.
        :return: Dict representation of the PolyMarketOrder, containing fields such as:
            id, status, owner, maker_address, market, asset_id, side, original_size,
            size_matched, price, outcome, expiration, order_type, associate_trades, created_at.
        """
        args = args_obj.args
        order_id = args.get("order_id", None)
        if order_id is None:
            raise InvalidArgumentError("'order_id' is required for get_order_status.")

        order = self.rest_api.get_order_status(order_id=str(order_id))
        return dataclasses.asdict(order)

    def _handle_get_orders(self, args_obj: ArgsObject):
        """
        Handle a request to fetch all open orders for the account. Delegates to the REST API's
        get_orders and serializes each PolyMarketOrder dataclass to a dict.

        :param args_obj: ArgsObject containing the socket and arguments.
            Args is expected to be empty (no arguments required).
        :return: List of dicts, each representing a PolyMarketOrder.
        """
        _ = args_obj
        orders = self.rest_api.get_orders()
        return [dataclasses.asdict(order) for order in orders]

    def _handle_get_trades(self, args_obj: ArgsObject):
        """
        Handle a request to fetch all trades for the account. Delegates to the REST API's
        get_trades and serializes each Trade dataclass to a dict.

        :param args_obj: ArgsObject containing the socket and arguments.
            Args[0] can optionally be the limit of how many trades to return (default is 50).
            Args[1] can optionally be the offset for pagination (default is 0).
            To avoid packet size errors, use pagination with limit/offset.
        :return: List of dicts, each representing a Trade.
        """
        limit = 50  # Default smaller limit to avoid packet size errors
        offset = 0
        if len(args_obj.args) > 1:
            limit = int(args_obj.args[0])
            offset = int(args_obj.args[1])
        elif len(args_obj.args) > 0:
            limit = int(args_obj.args[0])

        trades = self.rest_api.get_trades()
        raw = [dataclasses.asdict(trade) for trade in trades.trades]
        return raw[offset: offset + limit]

    def _handle_get_balance(self, args_obj: ArgsObject):
        """
        Handle a request to get the account's USDC balance. Delegates to the REST API's
        get_balance which returns the collateral balance divided by the chain divisor.

        :param args_obj: ArgsObject containing the socket and arguments.
            Args is expected to be empty (no arguments required).
        :return: Float representing the account balance in USDC.
        """
        _ = args_obj
        balance = self.rest_api.get_balance()
        return balance

    def _handle_get_token_balance(self, args_obj: ArgsObject):
        """
        Handle a request to get the balance of a specific conditional outcome token (YES or NO token).

        Args:
            args_obj: ArgsObject containing the socket and arguments.
                Args is expected to be a dict with:
                    'token_id' (str): The outcome token asset ID (YES or NO token).
                    It can also be a 'symbol' like the one generated by the `P2ConvertClass`

        Returns:
            float: Number of shares (e.g. 150.0).
        """
        args = args_obj.args
        token_id = args.get("token_id", None)
        if token_id is None:
            raise InvalidArgumentError("'token_id' is required for get_token_balance.")

        token_id = str(token_id)
        if token_id.count("-") == 3:
            old_symbol = token_id
            token_id = token_id.split("-")[-1]
            print_with_name('Found {} converting to token_id={}'.format(old_symbol, token_id))

        balance = self.rest_api.get_token_balance(token_id=token_id)
        return balance

    def _handle_cancel_all_open_orders(self, args_obj: ArgsObject):
        """
        Handle a request to cancel all open orders for the account. Delegates to the REST API's
        cancel_all_open_orders which returns a dict containing lists of canceled and not canceled order IDs.

        :param args_obj: ArgsObject containing the socket and arguments.
            Args is expected to be empty (no arguments required).
        :return: Dict from the CLOB API, e.g.:
            {'not_canceled': {'0x...': 'reason'}, 'canceled': ['0x...', '0x...']}
        """
        _ = args_obj
        result = self.rest_api.cancel_all()
        return result

    ########################################
    # Utilities
    ########################################
    @staticmethod
    def _handle_ping(args_obj: ArgsObject):
        _ = args_obj
        response = "pong"
        return response

    def _handle_rtt_to_exchange(self, args_obj: ArgsObject) -> float:
        """
        Calculates the RTT to the exchange by measuring the time taken to
        check the account balance via the rest api.
        :param args_obj: Expects no arguments, just a trigger to perform the RTT check.
        :return: A float representing the round-trip time in seconds from the dispatcher to the exchange and back.
        """
        time_now = time.time()
        _ = args_obj
        self.rest_api.get_balance()
        time_after = time.time()
        rtt = time_after - time_now
        return rtt

    @staticmethod
    def send_with_p1_encoding(dict_data: dict) -> bytes:
        """
        Encodes a dictionary into bytes using JSON and P1 packet encoding.
        :param dict_data: The dictionary to encode.
        :return:
        """
        json_data = json.dumps(dict_data).encode("utf-8")
        packet = encode_packet(json_data)
        return packet

    def send_market_data_with_p2_encoding(
            self, market_data: dict, ticker: str, market_slug: str, asset_id: str
    ) -> bytes:
        """
        Encodes market data into bytes using a custom P2 encoding format.
        The P2 encoding format's ticker field is formatted like <Event-Ticker><Market-Slug><Asset_id>.

        P2 Layout:
        ~<packet-len><ticker-len><[event-ticker][market-slug][asset_id]><[Nx (price, size) for bid]><[Nx (price, size) for bid]>L

        Control N with `POLYMARKET_ORDERBOOK_DEPTH` environment variable (default 10)

        :param market_data: The market data to encode.
        :param ticker: The event ticker for the market data.
        :param market_slug: The market slug for the market data.
        :param asset_id: The asset ID for the market data.
        :return:
        """

        return transmit_mkt_data_with_protocol_2(
            P2ConvertClass(
                ticker=ticker,
                market_slug=market_slug,
                asset_id=asset_id,
                market_data=market_data,
                order_book_depth=self._orderbook_depth,
            )
        )

    @runAsThread
    def run(self):
        self.dispatcher_svr.start()

    def _eject_all_subscriptions(self):
        """Evicts all the items from all shards"""
        clobs = list(self._routing_helper.market_data_routing_table.keys())
        for clob in tqdm.tqdm(clobs, desc="Removing CLOB"):
            sockets = self._routing_helper.market_data_routing_table[clob]
            for sock in sockets:
                self._routing_helper.remove_socket_from_subscription(sock, clob)


    def _toggle_print_p2_packets(self):
        """Toggle the printing of raw P2 packets with timestamps."""
        current = self._configs["Print P2 packets"]
        self._configs["Print P2 packets"] = not current
        status = "ENABLED" if self._configs["Print P2 packets"] else "DISABLED"
        print(f"[CONFIG] Print P2 packets: {status}")
        return self._configs["Print P2 packets"]

    def _modify_configs_interactive(self):
        """Modify the dispatcher configurations interactively."""
        while True:
            print("\nCurrent configurations:")
            config_keys = list(self._configs.keys())
            for i, key in enumerate(config_keys, start=1):
                print(f"  {i}. {key}: {self._configs[key]}")
            print("  0. Exit")

            choice = input("\nSelect configuration number to modify: ").strip()

            if choice == "0":
                break

            try:
                choice_idx = int(choice) - 1
                if choice_idx < 0 or choice_idx >= len(config_keys):
                    print(
                        f"Invalid choice. Please select a number between 0 and {len(config_keys)}"
                    )
                    continue

                key = config_keys[choice_idx]
                current_value = self._configs[key]

                if isinstance(current_value, bool):
                    self._configs[key] = not current_value
                    print(f"Updated {key} to {self._configs[key]}")
                else:
                    new_value = input(
                        f"Enter new value for {key} (current: {current_value}): "
                    )
                    if new_value.lower() == "true":
                        self._configs[key] = True
                    elif new_value.lower() == "false":
                        self._configs[key] = False
                    else:
                        self._configs[key] = new_value
                    print(f"Updated {key} to {self._configs[key]}")

            except ValueError:
                print("Invalid input. Please enter a number.")

    def interactive_mode(self):
        self._interactive_ui(
            {
                "Toggle print P2 packets": (
                    "Toggle printing of raw P2 packets with timestamps",
                    self._toggle_print_p2_packets,
                ),
                "Modify dispatcher configurations": (
                    "Modify dispatcher configurations interactively",
                    self._modify_configs_interactive,
                ),
                "Clear console": (
                    "Clear the console output",
                    lambda: subprocess.check_call(["clear"]),
                ),
                "Clear correlation ids": (
                    "Clear all correlation IDs from the dispatcher cache",
                    self._correlation_id_checker.clear_seen_ids,
                ),
                "Get all open orders": (
                    "Fetch and display all open orders for the account",
                    lambda: print(
                        json.dumps(
                            self._handle_get_orders(ArgsObject(args=[], sock=None)),
                            indent=4,
                        )
                    ),
                ),
                "Get all orders": (
                    "Fetch and display all orders for the account",
                    lambda: print(json.dumps(self.rest_api.get_orders(), indent=4)),
                ),
                "Cancel all open orders": (
                    "Cancel all open orders for the account",
                    lambda: print(
                        colored(
                            json.dumps(
                                self._handle_cancel_all_open_orders(
                                    ArgsObject(args=[], sock=None)
                                ),
                                indent=4,
                            ),
                            color="yellow",
                        )
                    ),
                ),
                "Print latency stats": (
                    "Print WS→client propagation latency percentiles (one-shot)",
                    self.print_latency_stats,
                ),
                "Start latency stats loop": (
                    "Print latency stats every 10s in background",
                    lambda: self._latency_stats_loop(10),
                ),
                "Visualise Shards": (
                    "Display all shards and their states",
                    self.visualise_shards,
                ),
            }
        )


if __name__ == "__main__":
    dispatcher = PolymarketDispatcher()
    dispatcher.run()
    dispatcher.interactive_mode()
    input("Polymarket Dispatcher running. Press Enter to exit...\n")
