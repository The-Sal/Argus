"""
Hyperliquid websocket streaming: market data (order books) and the account's order/fill events.

The account stream (`HyperLiquidAccountWss`, bottom of this module) is a separate connection from the
order-book one; see its docstring. The rest of this docstring describes the market-data side.

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
from typing import Callable, Optional, Union
from argus.perpetuals.hyper import _classes as _cls
from argus.perpetuals.shared import account as _acct
from argus.perpetuals.shared.wss import MarketDataWssBase, VenueWSSBase

WS_URL = 'wss://api.hyperliquid.xyz/ws'


class HyperLiquidFramingMixin:
    """
    Hyperliquid's keepalive framing, shared by every Hyperliquid websocket class (market data and account)
    so it is defined once. List it *before* the `VenueWSSBase` subclass in the bases so these override the
    base's abstract hooks.
    """

    def _ping_frame(self) -> str:
        return json.dumps({"method": "ping"})

    def _is_pong_frame(self, parsed) -> bool:
        return parsed.get('channel') == 'pong'


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


class HyperLiquidMarketDataWss(HyperLiquidFramingMixin, MarketDataWssBase):
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
            url=WS_URL,
            env_prefix='HYPERLIQUID',
            proxy_idx='HYPERLIQUID',
            default_ping_interval_s=20,
        )
        self._store = HyperLiquidOrderBookStore(order_book_update_callback=order_book_update_callback)

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


AccountUpdateCallback = Callable[[Union[_acct.OrderUpdate, _acct.Trade]], None]
"""Receives one `_acct.OrderUpdate` or one `_acct.Trade` per call."""

GapCallback = Callable[[int], None]
"""Receives `since_ms`: when the previous connection was lost (unix ms)."""


class HyperLiquidAccountStream:
    """
    Pure parser/state for the account websocket (no socket, so it unit-tests offline). Mirrors the role
    `HyperLiquidOrderBookStore` plays for market data.

    Two subscriptions feed it (both keyed by the wallet address, no signature needed):
      - `orderUpdates` (channel "orderUpdates"): a list of `{order, status, statusTimestamp}`, one frame
        possibly mixing statuses. Becomes one `_acct.OrderUpdate` per element.
      - `userEvents` (channel "user", *not* "userEvents"): `{"fills": [...]}` and `{"twapSliceFills": [...]}`.
        Becomes one `_acct.Trade` per fill. Other keys (funding, liquidation, nonUserCancel) are logged and
        skipped until a later phase.
    `userFills` is deliberately not used: it would deliver every fill a second time and opens with a
    ~2000-fill snapshot.

    Neither channel replays history on (re)subscribe, so after a reconnect anything that happened while
    down is lost. `note_disconnect()` / `begin_connection()` track that: once both subscription acks of a
    *re*connection are in, `gap_callback(since_ms)` fires exactly once. The first connection never gaps.

    Every record is delivered through its own callback call (never batched; see `AccountUpdate`). A
    malformed frame or a raising callback is logged and skipped: this runs on the websocket thread and
    must never kill it.
    """

    SUBSCRIPTION_TYPES = ("orderUpdates", "userEvents")

    def __init__(self, update_callback: AccountUpdateCallback, gap_callback: Optional[GapCallback] = None):
        self._update_callback = update_callback
        self._gap_callback = gap_callback
        self._lock = threading.Lock()
        self._acked: set = set()
        self._gap_since_ms: Optional[int] = None

    def note_disconnect(self) -> None:
        """The socket dropped. Remember the *earliest* drop time of this outage (a failed reconnect
        attempt closes again before any ack, and must not move the gap's start forward)."""
        with self._lock:
            if self._gap_since_ms is None:
                self._gap_since_ms = int(time.time() * 1000)

    def begin_connection(self) -> None:
        """A socket just opened; the new connection has no acks yet."""
        with self._lock:
            self._acked = set()

    def apply_message(self, message: str) -> None:
        try:
            content = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            logging.warning('Non-JSON message from Hyperliquid Account WebSocket (ignored): "%s"', message)
            return
        if not isinstance(content, dict):
            logging.warning('Unexpected Hyperliquid Account WebSocket frame (ignored): %s', content)
            return

        channel = content.get('channel')
        data = content.get('data')
        try:
            if channel == 'orderUpdates':
                self._handle_order_updates(data)
            elif channel == 'user':
                self._handle_user_event(data)
            elif channel == 'subscriptionResponse':
                self._handle_subscription_response(data)
            elif channel == 'error':
                logging.warning('Hyperliquid Account WebSocket error frame: %s', content)
        except Exception:
            logging.exception('Failed to process Hyperliquid Account WebSocket frame: %s', message)

    def _deliver(self, record) -> None:
        try:
            self._update_callback(record)
        except Exception:
            logging.exception('Account update callback raised for %s', record)

    def _handle_order_updates(self, data) -> None:
        for raw in data or []:
            # Per element so one malformed order does not swallow the rest of the frame.
            try:
                update = _cls.WsOrderUpdate.from_dict(raw)
            except Exception:
                logging.exception('Malformed orderUpdates element (skipped): %s', raw)
                continue
            self._deliver(update.to_common())

    def _handle_user_event(self, data) -> None:
        event = _cls.WsUserEvent.from_dict(data or {})
        for key in event.ignored:
            logging.info('Hyperliquid userEvents key not supported yet (ignored): %s', key)
        for fill in event.fills:
            self._deliver(fill.to_common())

    def _handle_subscription_response(self, data) -> None:
        logging.info('Hyperliquid Account WebSocket subscription ack: %s', data)
        sub_type = ((data or {}).get('subscription') or {}).get('type')
        if sub_type not in self.SUBSCRIPTION_TYPES:
            return
        since_ms = None
        with self._lock:
            self._acked.add(sub_type)
            if self._gap_since_ms is not None and self._acked.issuperset(self.SUBSCRIPTION_TYPES):
                since_ms, self._gap_since_ms = self._gap_since_ms, None
        if since_ms is not None and self._gap_callback:
            try:
                self._gap_callback(since_ms)
            except Exception:
                logging.exception('Account gap callback raised')


class HyperLiquidAccountWss(HyperLiquidFramingMixin, VenueWSSBase):
    """
    Authenticated-by-address websocket for the account's order and fill events. A separate connection from
    `HyperLiquidMarketDataWss` (that class is built around a per-coin roster and an order-book store, and the
    account stream must not be able to take market data down). Costs 1 of the 10 per-IP connections and 1 of
    the 10 per-IP unique users. The subscription set is fixed (two), so it is simply re-sent on every open,
    including reconnects; none of the market-data roster/restore machinery is needed.

    `update_callback` gets one `_acct.OrderUpdate` or `_acct.Trade` per call; `gap_callback(since_ms)` fires
    once after a reconnect re-establishes both subscriptions (see `HyperLiquidAccountStream`). Reconnect,
    ping/pong and proxy handling, and the `HYPERLIQUID_*` env knobs, come from `VenueWSSBase`.
    """

    def __init__(self, wallet_address: str, update_callback: AccountUpdateCallback,
                 gap_callback: Optional[GapCallback] = None):
        super().__init__(
            name="Hyperliquid Account",
            url=WS_URL,
            env_prefix='HYPERLIQUID',
            proxy_idx='HYPERLIQUID',
            default_ping_interval_s=20,
        )
        self._wallet_address = wallet_address
        self._stream = HyperLiquidAccountStream(update_callback, gap_callback)

    def _on_open_impl(self):
        self._stream.begin_connection()
        for sub_type in HyperLiquidAccountStream.SUBSCRIPTION_TYPES:
            self._ws.send(json.dumps({
                "method": "subscribe",
                "subscription": {"type": sub_type, "user": self._wallet_address},
            }))

    def _on_message_impl(self, message: str):
        self._stream.apply_message(message)

    def _on_reconnect_start(self):
        self._stream.note_disconnect()


if __name__ == '__main__':
    hl = HyperLiquidMarketDataWss(order_book_update_callback=lambda update: None)
    hl.run(main_thread=False)
    hl.subscribe_to_coin("SOL")
    input("Press Enter to exit...\n")