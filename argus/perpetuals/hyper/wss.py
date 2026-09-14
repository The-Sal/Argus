"""
Hyperliquid market-data (order book) websocket streaming.

The reconnect/ping-pong/threading-event skeleton and the roster/restore-on-
reconnect machinery live in `argus.perpetuals.shared.wss` (`VenueWSSBase` /
`MarketDataWssBase`); this module supplies only what is Hyperliquid-specific:
the JSON ping/pong framing, the l2Book+bbo subscription payloads, and the
snapshot-only book store. See that shared module's docstring for the pattern
this was extracted from (originally modeled on `argus/polymarket_direct/wss.py`).

Key protocol notes, confirmed against Hyperliquid's docs
(hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket):
  - Ping/pong is JSON, not a literal "PING"/"PONG" string: client sends
    {"method": "ping"} and the server replies {"channel": "pong"}. The server
    closes any connection that hasn't seen a message in 60s, so we ping well
    under that (default 20s).
  - Subscribe/unsubscribe: {"method": "subscribe"|"unsubscribe",
    "subscription": {"type": "l2Book", "coin": "<coin>", "fast": true}} plus a
    companion {"type": "bbo", "coin": "<coin>"} subscription per coin. The
    unsubscribe object must match the subscribe object, so `fast` is replayed
    there too.
  - l2Book push: {"channel": "l2Book", "data": {"coin": "BTC",
    "levels": [[{"px","sz","n"}, ...bids], [{"px","sz","n"}, ...asks]], "time": ms}}.
    Bids/asks arrive pre-sorted (best first) directly from Hyperliquid -- there is
    no incremental price_change/best_bid_ask delta stream like Polymarket's; every
    l2Book push is a full snapshot for that coin, which makes the store far
    simpler than Polymarket's OrderBookStore (no bisect-insert delta application,
    no tick-size fetching, no best-bid-ask dedup path).
  - bbo push: {"channel": "bbo", "data": {"coin": "BTC", "time": ms,
    "bbo": [bid_level | null, ask_level | null]}}, each level {"px","sz","n"}.
    Since Hyperliquid's June 2026 public-WebSocket throttling, a plain l2Book
    subscription is only pushed every 2s (20 levels); `fast: true` gives 5 levels
    every 0.5s, and `bbo` fires per block (~70ms) when the top of book changes.
    The store therefore keeps the fast l2Book snapshot as its depth baseline and
    overlays each bbo update onto the best level so top-of-book is real-time
    between snapshots.
  - No sharding: Hyperliquid's documented per-IP limits are 10 connections but up
    to 1000 subscriptions and 2000 inbound msgs/minute -- the opposite shape of
    Polymarket (which caps out around 4 assets/connection in practice). Since
    Hyperliquid has on the order of a few hundred perpetuals total, one connection
    comfortably holds every coin, so this file does not implement Polymarket's
    shard-pool (PolyMarketOrderBookPool); `HyperLiquidMarketDataWss` below *is*
    the whole thing. If Hyperliquid's limits change, or if a future dispatcher
    wants to subscribe user-specific channels per end-client (which does count
    against the 10-unique-user cap), revisit this.
"""
import json
import time
import logging
import threading
from argus.perpetuals.shared.wss import MarketDataWssBase


class HyperLiquidOrderBookStore:
    """
    Pure book-state container, keyed by coin symbol (e.g. "BTC", "xyz:AAPL").

    Unlike Polymarket's OrderBookStore, Hyperliquid's l2Book push is always a full
    snapshot (not a price_change delta stream), so there is no delta application,
    no bisect-insert, no tick-size REST fetch, and no best-bid-ask dedup path to
    port -- this store replaces the book wholesale on every l2Book message. The
    companion `bbo` channel is then overlaid onto the snapshot's best level (with
    stale crossed levels dropped) so top-of-book stays fresh between snapshots.
    """

    def __init__(self, order_book_update_callback=None):
        self._coin_to_order_book: dict = {}
        self._order_book_update_callback = order_book_update_callback
        self._dict_lock = threading.Lock()
        self._last_msg_recv_ts: float = 0.0

    def forget(self, coin: str) -> None:
        with self._dict_lock:
            self._coin_to_order_book.pop(coin, None)

    def apply_message(self, message: str) -> None:
        self._last_msg_recv_ts = time.perf_counter()

        try:
            content = json.loads(message)
        except json.JSONDecodeError:
            logging.debug('Non-JSON message from Hyperliquid Order Book WebSocket (ignored): "%s"', message)
            return

        channel = content.get('channel')
        if channel == 'l2Book':
            self._handle_l2_book(content.get('data') or {})
        elif channel == 'bbo':
            self._handle_bbo(content.get('data') or {})
        elif channel == 'subscriptionResponse':
            logging.info('Hyperliquid WebSocket subscription ack: %s', content.get('data'))
        elif channel == 'error':
            logging.warning('Hyperliquid WebSocket error frame: %s', content)
        # Other channels (allMids, trades, ...) aren't subscribed by this store and are ignored.

    def _handle_l2_book(self, data: dict) -> None:
        coin = data.get('coin')
        levels = data.get('levels')
        if not coin or not levels or len(levels) != 2:
            logging.warning('Malformed l2Book payload (missing coin/levels): %s', data)
            return

        raw_bids, raw_asks = levels
        book = {
            'bids': [{'price': lvl['px'], 'size': lvl['sz']} for lvl in raw_bids],
            'asks': [{'price': lvl['px'], 'size': lvl['sz']} for lvl in raw_asks],
        }

        with self._dict_lock:
            self._coin_to_order_book[coin] = book

        if self._order_book_update_callback:
            self._order_book_update_callback({
                coin: book,
                'timestamp': data.get('time', int(time.time() * 1000)),
            })

    def _handle_bbo(self, data: dict) -> None:
        coin = data.get('coin')
        bbo = data.get('bbo')
        if not coin or not isinstance(bbo, list) or len(bbo) != 2:
            logging.warning('Malformed bbo payload (missing coin/bbo): %s', data)
            return

        best_bid, best_ask = bbo
        with self._dict_lock:
            book = self._coin_to_order_book.get(coin)
            if book is None:
                book = {'bids': [], 'asks': []}
                self._coin_to_order_book[coin] = book

            book['bids'] = self._overlay_bbo_side(book.get('bids', []), best_bid, is_bid=True)
            book['asks'] = self._overlay_bbo_side(book.get('asks', []), best_ask, is_bid=False)

        if self._order_book_update_callback:
            self._order_book_update_callback({
                coin: book,
                'timestamp': data.get('time', int(time.time() * 1000)),
            })

    @staticmethod
    def _overlay_bbo_side(levels: list, best, is_bid: bool) -> list:
        if best is None:
            return []

        best_px = best.get('px')
        best_sz = best.get('sz')
        if best_px is None or best_sz is None:
            logging.warning('Malformed bbo level (missing px/sz): %s', best)
            return levels

        best_px_float = float(best_px)
        if is_bid:
            kept = [lvl for lvl in levels if float(lvl['price']) < best_px_float]
        else:
            kept = [lvl for lvl in levels if float(lvl['price']) > best_px_float]
        return [{'price': best_px, 'size': best_sz}] + kept

    def order_book_for_coin(self, coin: str):
        return self._coin_to_order_book.get(coin, None)

    @property
    def order_books(self):
        return self._coin_to_order_book

    @property
    def coins(self):
        return list(self._coin_to_order_book.keys())


class HyperLiquidMarketDataWss(MarketDataWssBase):
    """
    Single-connection order book streamer for Hyperliquid.

    Exposes the same dispatcher-facing surface Polymarket's PolyMarketOrderBookPool
    does (`run`, `subscribe_to_coin`/`unsubscribe_from_coin` in place of
    `subscribe_to_asset_id`/`unsubscribe_from_asset_id`, `order_book_for_coin`,
    `order_books`) so wiring into HyperLiquidDispatcher mirrors PolymarketDispatcher's
    wiring of `self.market_data`, minus the shard/pool indirection -- see this
    module's docstring for why sharding isn't needed here. The reconnect/ping-pong
    skeleton and subscription restore come from `MarketDataWssBase`; only the
    Hyperliquid JSON framing and l2Book+bbo wire ops are defined below.
    """

    def __init__(self, order_book_update_callback=None):
        super().__init__(
            name="Hyperliquid Order Book",
            url='wss://api.hyperliquid.xyz/ws',
            env_prefix='HYPERLIQUID',
            proxy_idx='HYPERLIQUID',
            default_ping_interval_s=20,
        )
        self._store = HyperLiquidOrderBookStore(order_book_update_callback=order_book_update_callback)

    ########################################
    # Venue framing (shared base hooks)
    ########################################

    def _ping_frame(self) -> str:
        return json.dumps({"method": "ping"})

    def _is_pong_frame(self, parsed) -> bool:
        return parsed.get('channel') == 'pong'

    ########################################
    # Subscription ops
    ########################################

    def _send_subscribe_op(self, key: str) -> None:
        self._ws.send(json.dumps({
            "method": "subscribe",
            "subscription": {"type": "l2Book", "coin": key, "fast": True},
        }))
        self._ws.send(json.dumps({
            "method": "subscribe",
            "subscription": {"type": "bbo", "coin": key},
        }))

    def _send_unsubscribe_op(self, key: str) -> None:
        self._ws.send(json.dumps({
            "method": "unsubscribe",
            "subscription": {"type": "l2Book", "coin": key, "fast": True},
        }))
        self._ws.send(json.dumps({
            "method": "unsubscribe",
            "subscription": {"type": "bbo", "coin": key},
        }))

    ########################################
    # Dispatcher-facing surface
    ########################################

    def subscribe_to_coin(self, coin: str) -> None:
        self._subscribe_to_key(coin)

    def unsubscribe_from_coin(self, coin: str) -> None:
        self._unsubscribe_from_key(coin)

    def order_book_for_coin(self, coin: str):
        return self._store.order_book_for_coin(coin)

    @property
    def coins(self):
        return self._store.coins

    # `_last_msg_recv_ts` (read by the dispatcher for WS-arrival -> sendall latency,
    # the same way it reads `PolyMarketOrderBookPool._last_msg_recv_ts`) needs no
    # forwarding property here the way Polymarket's pool needs one: Polymarket's
    # pool isn't itself a WSSBase and has to reach into its shards' store, whereas
    # this class's `MarketDataWssBase._on_message_base` already sets the plain
    # attribute on `self` and it is accurate as-is.
