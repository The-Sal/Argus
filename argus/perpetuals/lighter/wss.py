"""
Lighter market-data (order book) websocket streaming.

The reconnect/ping-pong/threading-event skeleton and the roster/restore-on-
reconnect machinery live in `argus.perpetuals.shared.wss` (`VenueWSSBase` /
`MarketDataWssBase`); this module supplies only what is Lighter-specific: the
bidirectional JSON ping/pong framing, the order_book+ticker subscription
payloads, and the snapshot+delta book store. See that shared module's docstring
for the pattern this was extracted from. The store logic is NOT ported from
Hyperliquid's -- see `docs/perf/lighter-market-data-parity-plan.md` (the design
doc this module implements) for the full reasoning. Short version:

  - Hyperliquid's `l2Book` push is always a full snapshot -> its store just
    replaces the book wholesale on every message.
  - Lighter's `order_book/{market_id}` channel is snapshot+incremental-delta
    (first push is a full snapshot, every push after that is a delta: price-
    keyed upsert, or removal when size=="0"). This is structurally the same
    shape as Polymarket's `price_change` delta stream, so
    `LighterOrderBookStore._handle_order_book_delta` ports
    `argus/polymarket_direct/wss.py`'s `OrderBookStore._update_order_book`
    (bisect-insert to keep levels sorted), not Hyperliquid's snapshot-replace.

Two channels matter for order-book parity, keyed by integer `market_id` (NOT
the symbol string -- see LighterDispatcher for the symbol<->market_id
translation, done once at the dispatcher boundary):

  - `order_book/{market_id}` -- depth baseline, delta-maintained as above.
    Server-side batched at ~50ms (per docs) -> ~20 updates/s out of the box.
  - `ticker/{market_id}` -- BBO fast path (best ask `a` / best bid `b`, each
    `{price, size}`). This is Lighter's analog of Hyperliquid's `bbo` and
    Polymarket's `best_bid_ask`: it is overlaid onto the book's top level
    (Hyperliquid-shaped overlay) AND fired immediately with a dedup check
    against the order_book delta path (Polymarket-shaped short-circuit) --
    see `LighterOrderBookStore._handle_ticker` / `_matches_ticker_cache`.

Exact wire shape was VERIFIED LIVE against `wss://mainnet.zklighter.elliot.ai/stream`
while building this module (see `tests/lighter_wss_latency.py`, which is the repeatable
version of that check) -- and differs from the design doc's Section 3 assumptions (based
on docs.lighter.xyz / the reference lighter-python SDK) in three ways this module
codes around explicitly:
  - The channel field on inbound frames uses a COLON, e.g. `"order_book:0"`, not the
    slash used when sending the subscribe/unsubscribe request itself (both are accepted
    on the request side; only the colon form has been observed on responses) --
    `_market_id_from_channel` accepts either.
  - The first order_book/ticker frame after subscribing arrives with type
    `"subscribed/order_book"` / `"subscribed/ticker"` (not `"update/order_book"`) and
    IS the full payload (snapshot for order_book), not a bare ack -- dispatch is by
    exact `type`, not by "do we already have local state" guessing.
  - The payload's own `last_updated_at` field is in MICROSECONDS; the message's
    top-level `timestamp` field is in milliseconds (matching Hyperliquid's/Polymarket's
    convention) -- callbacks use the top-level field. Using the nested one silently
    produced 1000x-inflated "latency" numbers during testing.

Reconnect-gap handling is new machinery neither Hyperliquid's nor Polymarket's
store needs: because `order_book` is snapshot+delta (not snapshot-only), a
dropped/out-of-order delta would silently desync the local book forever with
no self-correction. Lighter's payload carries `offset`/`nonce`/`begin_nonce`
specifically so a consumer can detect this -- `_handle_order_book_delta`
verifies each delta's `begin_nonce` chains from the last recorded `nonce`, and
on mismatch drops local state and forces an unsubscribe+resubscribe of that
market's `order_book` channel (via the `resync_callback` wired in by
`LighterMarketDataWss`) to force a fresh snapshot, rather than applying an
out-of-order delta.

Ping/pong direction is reversed from Hyperliquid (and from Polymarket), and is
handled in BOTH directions here per the design doc:
  - Lighter's docs say clients must send a frame at least every 2 minutes; we
    send our own `{"type": "ping"}` on a timer (default well under that -- see
    the `LIGHTER_PING_INTERVAL_S` env var, honored by the shared base) and track
    `{"type": "pong"}` replies exactly like `VenueWSSBase.ping()` does.
  - The reference SDK's actual behavior is that the SERVER also sends
    `{"type": "ping"}`, and the client must reply `{"type": "pong"}`
    immediately -- this is what actually keeps the server-side connection
    alive. `_handle_server_ping` answers this unconditionally, independent of
    our own ping timer/lock state, since it is a protocol requirement rather
    than a liveness probe we control the cadence of.
"""
import json
import time
import bisect
import logging
import threading
from collections import OrderedDict
from typing import Callable, Optional, Union
from argus.perpetuals.lighter import _classes as _cls
from argus.perpetuals.shared import account as _acct
from argus.perpetuals.shared.wss import MarketDataWssBase, VenueWSSBase

#: Lighter's public websocket endpoint. Both the market-data and account streams use it; they are kept
#: on separate connections purely for fault isolation (the account stream is not a different server).
WS_URL = 'wss://mainnet.zklighter.elliot.ai/stream'


class LighterFramingMixin:
    """
    Lighter's keepalive framing, shared by the market-data and account websockets so it is defined once.

    Lighter's ping/pong is bidirectional: we send `{"type":"ping"}` on our own timer and track
    `{"type":"pong"}` replies exactly like the shared base does, while the SERVER also sends
    `{"type":"ping"}` that the client must answer immediately with `{"type":"pong"}` regardless of
    our timer/lock state (that reply is what actually keeps the server-side connection alive). List
    this *before* the `WSSBase` subclass in the bases so these hooks override the base's abstract ones.
    """

    def _ping_frame(self) -> str:
        return json.dumps({"type": "ping"})

    def _is_pong_frame(self, parsed) -> bool:
        return parsed.get('type') == 'pong'

    def _handle_server_ping(self, parsed) -> bool:
        # Server-initiated keepalive -- required response, independent of our own ping
        # timer/lock. See module docstring: this is the direction that actually keeps
        # the connection alive per the reference SDK.
        if parsed.get('type') != 'ping':
            return False
        logging.debug('%s WebSocket received server ping; replying pong.', self._name)
        try:
            self._ws.send(json.dumps({"type": "pong"}))
        except Exception as e:
            logging.warning('%s: failed to reply to server ping: %s', self._name, e)
        return True


class LighterOrderBookStore:
    """
    Pure book-state container, keyed by integer `market_id`.

    Unlike HyperLiquidOrderBookStore (snapshot-replace only), Lighter's
    `order_book` channel is snapshot+delta, so this store carries real delta-
    application logic (bisect-insert, price-keyed upsert/remove) ported from
    `argus.polymarket_direct.wss.OrderBookStore._update_order_book`, plus
    nonce-chain gap detection that neither Hyperliquid's nor Polymarket's store
    needs (see module docstring). The `ticker` channel is then overlaid onto
    the book's top level (Hyperliquid-shaped overlay) with a dedup short-
    circuit against the order_book delta path (Polymarket-shaped), so a
    top-of-book change already announced by `ticker` doesn't fire a second,
    redundant callback when the corresponding `order_book` delta lands.
    """

    def __init__(self, order_book_update_callback=None, resync_callback=None):
        self._market_id_to_order_book: dict = {}
        self._market_id_to_nonce: dict = {}  # market_id -> {'offset':..., 'nonce':...}
        self._market_id_to_ticker_cache: dict = {}  # market_id -> {'best_bid':..., 'best_ask':...}
        self._order_book_update_callback = order_book_update_callback
        # Called (from inside apply_message, without _dict_lock held) with a market_id
        # whose order_book delta failed the nonce-chain check. Wired by
        # LighterMarketDataWss to unsubscribe+resubscribe that market's order_book
        # channel and force a fresh snapshot -- see module docstring.
        self._resync_callback = resync_callback
        self._dict_lock = threading.Lock()
        self._last_msg_recv_ts: float = 0.0

    def forget(self, market_id: int) -> None:
        with self._dict_lock:
            self._market_id_to_order_book.pop(market_id, None)
            self._market_id_to_nonce.pop(market_id, None)
            self._market_id_to_ticker_cache.pop(market_id, None)

    def apply_message(self, message: str) -> None:
        self._last_msg_recv_ts = time.perf_counter()

        try:
            content = json.loads(message)
        except json.JSONDecodeError:
            logging.debug('Non-JSON message from Lighter Order Book WebSocket (ignored): "%s"', message)
            return

        if not isinstance(content, dict):
            return

        msg_type = content.get('type')
        # Confirmed live against wss://mainnet.zklighter.elliot.ai/stream (see
        # tests/lighter_wss_latency.py and docs/perf/lighter-market-data-parity-plan.md
        # Section 8): the FIRST order_book/ticker frame after subscribing arrives as
        # "subscribed/order_book" / "subscribed/ticker" and already carries the full
        # payload (snapshot for order_book) -- it is not a bare ack. Only frames after
        # that use "update/order_book" / "update/ticker". Both are dispatched by exact
        # type (not by "do we already have state" guessing) so a post-resync snapshot
        # is always handled as a snapshot regardless of stale local state.
        if msg_type in ('subscribed/order_book', 'update/order_book'):
            self._handle_order_book_update(content, is_snapshot=(msg_type == 'subscribed/order_book'))
        elif msg_type in ('subscribed/ticker', 'update/ticker'):
            self._handle_ticker(content)
        elif msg_type == 'connected':
            logging.info('Lighter WebSocket session established: %s', content.get('session_id'))
        elif msg_type == 'error':
            logging.warning('Lighter WebSocket error frame: %s', content)
        elif msg_type == 'unsubscribed' or (isinstance(msg_type, str) and msg_type.startswith('unsubscribed')):
            logging.info('Lighter WebSocket unsubscribe ack: %s', content)
        else:
            logging.debug('Unhandled Lighter WebSocket message type %r: %s', msg_type, content)
        # Other channels (trade, market_stats, candle, ...) aren't subscribed by
        # this store and are ignored -- see design doc Section 3 on scope discipline.

    ##############################################
    # order_book: snapshot + delta
    ##############################################

    @staticmethod
    def _market_id_from_channel(channel) -> "int | None":
        """Channel is e.g. "order_book:0" on inbound frames (colon -- confirmed live),
        though the subscribe/unsubscribe request itself uses a slash (both accepted by
        the server; see _send_subscribe_op). Handle either separator defensively."""
        if not isinstance(channel, str):
            return None
        for sep in (':', '/'):
            if sep in channel:
                try:
                    return int(channel.rsplit(sep, 1)[1])
                except ValueError:
                    return None
        return None

    def _handle_order_book_update(self, content: dict, is_snapshot: bool) -> None:
        market_id = self._market_id_from_channel(content.get('channel'))
        if market_id is None:
            logging.warning('Lighter order_book frame missing/unparseable channel: %s', content)
            return
        payload = content.get('order_book') if isinstance(content.get('order_book'), dict) else content
        # Top-level "timestamp" is milliseconds since epoch (matches Hyperliquid's/
        # Polymarket's convention); the nested payload's "last_updated_at" is
        # MICROSECONDS -- confirmed live -- so it must not be used as the callback
        # timestamp or downstream latency math (ms-based, e.g. tests/lighter_wss_latency.py)
        # would be off by 1000x.
        msg_timestamp = content.get('timestamp')

        if is_snapshot:
            self._handle_order_book_snapshot(market_id, payload, msg_timestamp)
        else:
            self._handle_order_book_delta(market_id, payload, msg_timestamp)

    def _handle_order_book_snapshot(self, market_id: int, payload: dict, msg_timestamp=None) -> None:
        """Full replace, sorted (bids desc, asks asc). Used on first sight of a market,
        and again after a forced resubscribe (Section 3/4 of the design doc)."""
        raw_bids = payload.get('bids') or []
        raw_asks = payload.get('asks') or []
        bid_sorted = sorted(raw_bids, key=lambda lvl: float(lvl['price']), reverse=True)
        ask_sorted = sorted(raw_asks, key=lambda lvl: float(lvl['price']))

        with self._dict_lock:
            self._market_id_to_order_book[market_id] = {'bids': bid_sorted, 'asks': ask_sorted}
            self._market_id_to_nonce[market_id] = {
                'offset': payload.get('offset'),
                'nonce': payload.get('nonce'),
            }

        self._fire_callback(market_id, msg_timestamp)

    def _handle_order_book_delta(self, market_id: int, payload: dict, msg_timestamp=None) -> None:
        begin_nonce = payload.get('begin_nonce')
        with self._dict_lock:
            nonce_state = self._market_id_to_nonce.get(market_id)
        last_nonce = nonce_state.get('nonce') if nonce_state else None

        if begin_nonce is not None and last_nonce is not None and begin_nonce != last_nonce:
            logging.warning(
                'Lighter order book desync for market_id %s: delta begin_nonce=%s does not chain from '
                'last nonce=%s -- dropping local book and forcing resubscribe.',
                market_id, begin_nonce, last_nonce,
            )
            self.forget(market_id)
            if self._resync_callback:
                try:
                    self._resync_callback(market_id)
                except Exception as e:
                    logging.warning('Lighter order book resync callback failed for market_id %s: %s', market_id, e)
            return

        bid_changes = payload.get('bids') or []
        ask_changes = payload.get('asks') or []
        # Check top-of-book-touch BEFORE applying the delta, against the pre-update
        # best level -- same ordering Polymarket's _is_top_of_book_change relies on.
        is_top_change = (
            self._is_top_of_book_change(market_id, bid_changes, is_bid=True)
            or self._is_top_of_book_change(market_id, ask_changes, is_bid=False)
        )

        with self._dict_lock:
            book = self._market_id_to_order_book.get(market_id)
            if book is None:
                # Raced with a forget()/resubscribe (e.g. a concurrent desync on this
                # market); drop the now-orphaned delta rather than resurrect a book.
                return
            for level in bid_changes:
                self._apply_level(book['bids'], level, is_bid=True)
            for level in ask_changes:
                self._apply_level(book['asks'], level, is_bid=False)
            self._market_id_to_nonce[market_id] = {
                'offset': payload.get('offset'),
                'nonce': payload.get('nonce'),
            }

        # After update: if this delta touched the top of book and the resulting best
        # bid/ask now matches what `ticker` already broadcast, suppress the callback --
        # same dedup as Polymarket's price_change/best_bid_ask short-circuit.
        if is_top_change and self._matches_ticker_cache(market_id):
            return

        self._fire_callback(market_id, msg_timestamp)

    @staticmethod
    def _apply_level(levels: list, level: dict, is_bid: bool) -> None:
        """Price-keyed upsert (or removal when size=="0"), bisect-inserting new price
        levels to keep sort order. Caller must hold `_dict_lock`.

        NOTE: this deliberately does NOT copy `OrderBookStore._update_order_book`'s
        insertion arithmetic verbatim (argus/polymarket_direct/wss.py) for the bid
        side. That code calls `bisect.bisect_left` directly on `levels`'s prices even
        though bids are stored descending, then "corrects" with `len(levels) - insert_idx`
        -- but `bisect` assumes an ASCENDING sequence, so bisecting a descending one
        does not produce a meaningful index (confirmed live while building this store:
        it silently corrupted bid ordering, caught by tests/lighter_wss_latency.py's
        book-invariant check). Bisecting the NEGATED prices (which is ascending when
        the source is descending) gives the correct insertion index directly, with no
        reflection needed."""
        price = level.get('price')
        size = level.get('size')
        if price is None or size is None:
            logging.warning('Malformed Lighter order_book level (missing price/size): %s', level)
            return

        idx = next((i for i, lvl in enumerate(levels) if lvl['price'] == price), None)

        if float(size) == 0:
            if idx is not None:
                levels.pop(idx)
            return

        if idx is not None:
            levels[idx]['size'] = size
            return

        new_level = {'price': price, 'size': size}
        price_float = float(price)
        if is_bid:
            insert_idx = bisect.bisect_left([-float(lvl['price']) for lvl in levels], -price_float)
        else:
            insert_idx = bisect.bisect_left([float(lvl['price']) for lvl in levels], price_float)
        levels.insert(insert_idx, new_level)

    def _is_top_of_book_change(self, market_id: int, changed_levels: list, is_bid: bool) -> bool:
        """True if any of `changed_levels` touches the current best level on this side.
        Called BEFORE the delta is applied, so this compares against the pre-update
        top of book -- the only case a `ticker` push could already cover."""
        if not changed_levels:
            return False
        with self._dict_lock:
            book = self._market_id_to_order_book.get(market_id)
            if not book:
                return False
            side = book['bids'] if is_bid else book['asks']
            if not side:
                return False
            best_price = side[0]['price']
        touched_prices = {lvl.get('price') for lvl in changed_levels}
        return best_price in touched_prices

    ##############################################
    # ticker: BBO fast path
    ##############################################

    def _handle_ticker(self, content: dict) -> None:
        market_id = self._market_id_from_channel(content.get('channel'))
        if market_id is None:
            logging.warning('Lighter ticker frame missing/unparseable channel: %s', content)
            return
        payload = content.get('ticker') if isinstance(content.get('ticker'), dict) else content
        # See _handle_order_book_update: top-level "timestamp" is ms, payload's
        # "last_updated_at" is microseconds -- use the top-level field for the callback.
        msg_timestamp = content.get('timestamp')

        best_ask = payload.get('a')
        best_bid = payload.get('b')

        with self._dict_lock:
            self._market_id_to_ticker_cache[market_id] = {
                'best_bid': best_bid.get('price') if isinstance(best_bid, dict) else None,
                'best_ask': best_ask.get('price') if isinstance(best_ask, dict) else None,
            }
            book = self._market_id_to_order_book.get(market_id)
            if book is None:
                book = {'bids': [], 'asks': []}
                self._market_id_to_order_book[market_id] = book
            book['bids'] = self._overlay_side(book.get('bids', []), best_bid, is_bid=True)
            book['asks'] = self._overlay_side(book.get('asks', []), best_ask, is_bid=False)

        # Fast path: fire immediately, same as HyperLiquidOrderBookStore._handle_bbo
        # and Polymarket's best_bid_ask handler returning early after firing.
        self._fire_callback(market_id, msg_timestamp)

    @staticmethod
    def _overlay_side(levels: list, best, is_bid: bool) -> list:
        if not isinstance(best, dict):
            return levels

        best_px = best.get('price')
        best_sz = best.get('size')
        if best_px is None or best_sz is None:
            logging.warning('Malformed Lighter ticker level (missing price/size): %s', best)
            return levels

        best_px_float = float(best_px)
        if is_bid:
            kept = [lvl for lvl in levels if float(lvl['price']) < best_px_float]
        else:
            kept = [lvl for lvl in levels if float(lvl['price']) > best_px_float]
        return [{'price': best_px, 'size': best_sz}] + kept

    def _matches_ticker_cache(self, market_id: int) -> bool:
        """True if the book's current best bid/ask matches the last `ticker` cache --
        i.e. `ticker` already broadcast a callback reflecting this exact top-of-book
        state, so the order_book-delta callback can be safely suppressed."""
        with self._dict_lock:
            cached = self._market_id_to_ticker_cache.get(market_id)
            if not cached:
                return False
            book = self._market_id_to_order_book.get(market_id)
            if not book:
                return False
            bids = book.get('bids') or []
            asks = book.get('asks') or []
            current_best_bid = bids[0]['price'] if bids else None
            current_best_ask = asks[0]['price'] if asks else None
            return current_best_bid == cached['best_bid'] and current_best_ask == cached['best_ask']

    ##############################################
    # Shared
    ##############################################

    def _fire_callback(self, market_id: int, timestamp) -> None:
        if not self._order_book_update_callback:
            return
        book = self._market_id_to_order_book.get(market_id)
        if book is None:
            return
        self._order_book_update_callback({
            market_id: book,
            'timestamp': timestamp if timestamp is not None else int(time.time() * 1000),
        })

    def order_book_for_market(self, market_id: int):
        return self._market_id_to_order_book.get(market_id, None)

    @property
    def order_books(self):
        return self._market_id_to_order_book

    @property
    def market_ids(self):
        return list(self._market_id_to_order_book.keys())


class LighterMarketDataWss(LighterFramingMixin, MarketDataWssBase):
    """
    Single-connection order book streamer for Lighter.

    Exposes the dispatcher-facing surface `LighterDispatcher` wires up
    (`subscribe_to_market`/`unsubscribe_from_market` -- keyed by integer
    `market_id`, not symbol; translation happens once at the dispatcher
    boundary -- `order_book_for_market`, `order_books`, `run`). Mirrors
    HyperLiquidMarketDataWss's shape (no shard pool -- Lighter has on the
    order of ~100 markets and no documented per-connection subscription cap,
    per the design doc's Section 3, so one connection is assumed sufficient
    until live testing shows otherwise). The reconnect/ping-pong skeleton and
    subscription restore come from `MarketDataWssBase`; the store is wired with
    a `resync_callback` so a nonce-chain gap forces a fresh `order_book`
    snapshot (see `_resubscribe_order_book`).
    """

    def __init__(self, order_book_update_callback=None):
        super().__init__(
            name="Lighter Order Book",
            url=WS_URL,
            env_prefix='LIGHTER',
            proxy_idx='LIGHTER',
            default_ping_interval_s=60,
        )
        self._store = LighterOrderBookStore(
            order_book_update_callback=order_book_update_callback,
            resync_callback=self._resubscribe_order_book,
        )

    ########################################
    # Subscription ops
    ########################################

    def _send_subscribe_op(self, key: int) -> None:
        self._ws.send(json.dumps({"type": "subscribe", "channel": f"order_book/{key}"}))
        self._ws.send(json.dumps({"type": "subscribe", "channel": f"ticker/{key}"}))

    def _send_unsubscribe_op(self, key: int) -> None:
        self._ws.send(json.dumps({"type": "unsubscribe", "channel": f"order_book/{key}"}))
        self._ws.send(json.dumps({"type": "unsubscribe", "channel": f"ticker/{key}"}))

    def _resubscribe_order_book(self, market_id: int) -> None:
        """Force a fresh order_book snapshot after a nonce-chain gap (design doc
        Section 3/4). Store state for this market was already dropped by the caller
        (LighterOrderBookStore._handle_order_book_delta) before invoking this --
        `ticker` stays subscribed throughout, only `order_book` is cycled."""
        try:
            self._ws.send(json.dumps({"type": "unsubscribe", "channel": f"order_book/{market_id}"}))
            self._ws.send(json.dumps({"type": "subscribe", "channel": f"order_book/{market_id}"}))
        except Exception as e:
            logging.warning('%s: error resubscribing order_book for market_id %s: %s', self._name, market_id, e)

    ########################################
    # Dispatcher-facing surface
    ########################################

    def subscribe_to_market(self, market_id: int) -> None:
        self._subscribe_to_key(market_id)

    def unsubscribe_from_market(self, market_id: int) -> None:
        self._unsubscribe_from_key(market_id)

    def order_book_for_market(self, market_id: int):
        return self._store.order_book_for_market(market_id)

    @property
    def market_ids(self):
        return self._store.market_ids


# --- account stream (orders + fills) -----------------------------------------
#
# Lighter's account channels live on the SAME public /stream websocket type as market data, but are
# deliberately put on their own connection (`LighterAccountWss`) for fault isolation, exactly like
# Hyperliquid's account stream: an account-stream failure must never take the order books down.
#
#   account_all_orders/{account_index}   -- auth token required, full lifecycle order updates
#   account_all_trades/{account_index}   -- no auth, fills
#   account_all_positions/{account_index} -- out of scope (no position push yet)
#
# The first frame after subscribing arrives with type `subscribed/<channel>` (not a bare ack) and is a
# baseline; later frames use `update/<channel>`. Inbound `channel` fields use a colon
# (`account_all_orders:0`) while the subscribe request uses a slash -- the same quirk the market-data
# store already codes around.

AccountUpdateCallback = Callable[[Union[_acct.OrderUpdate, _acct.Trade]], None]
"""Receives one `_acct.OrderUpdate` or one `_acct.Trade` per call."""

GapCallback = Callable[[int], None]
"""Receives `since_ms`: when the previous connection was lost (unix ms)."""

_ACCOUNT_ORDERS_CHANNEL = 'account_all_orders'
_ACCOUNT_TRADES_CHANNEL = 'account_all_trades'


class LighterAccountStream:
    """
    Pure parser/state for Lighter's account websocket (no socket, so it unit-tests offline). Mirrors the
    role `LighterOrderBookStore` plays for market data and `HyperLiquidAccountStream` for Hyperliquid.

    Two subscriptions feed it (both keyed by integer `market_index`, resolved to a symbol by the
    caller-supplied `symbol_for_market_id`):
      - `account_all_orders` (auth): frames carry `{"orders": {market_index: [Order, ...]}}`.
      - `account_all_trades` (no auth): frames carry `{"trades": {market_index: [Trade, ...]}}`.

    Snapshot handling: the first frame per channel (`subscribed/...`) is a BASELINE. Its orders are
    recorded into the dedup cache and its fills into the seen-`trade_id` set, but **nothing is emitted** -- a
    client that just connected must not see a burst of "new" orders/fills it never witnessed. Only later
    `update/...` frames emit. For orders that means deduping on
    `(order_index, status, remaining_base_amount, updated_at)`, which is correct whether the venue sends
    deltas (each changed order once) or periodic full sets (unchanged orders suppressed); for fills it means
    deduping on `trade_id_str` (Lighter re-sends the same fills in full-set frames). Both caches are bounded
    and evict oldest-first, because Lighter keeps ~1000 inactive orders and unbounded fill history.

    Neither channel replays history on (re)subscribe, so a reconnect can lose events. `note_disconnect()` /
    `begin_connection()` track that: once every required channel ack of a *re*connection is in,
    `gap_callback(since_ms)` fires exactly once. The first connection never gaps. If `account_all_orders`
    cannot be subscribed (no auth token, or the server rejects the token), `note_orders_unavailable()` drops
    it from the required set so a trades-only stream still reports gaps. As a backstop against an ack that
    never arrives for any reason, a reconnect also arms a `gap_timeout_s` timer that drops any still-unacked
    channel, so the gap can never be lost.

    Every record is delivered through its own callback call (never batched; see `AccountUpdate`) and every
    malformed frame/record/raising callback is logged and skipped: this runs on the websocket thread and must
    never kill it.
    """

    CHANNELS = (_ACCOUNT_ORDERS_CHANNEL, _ACCOUNT_TRADES_CHANNEL)
    #: Cap on each dedup cache. Orders: far more than any account's resting book; fills: a few minutes of
    #: busy trading, after which an evicted (very old) trade re-appearing would merely be a duplicate.
    MAX_TRACKED = 4096

    def __init__(self, account_index: int, symbol_for_market_id: Callable[[int], Optional[str]],
                 update_callback: AccountUpdateCallback, gap_callback: Optional[GapCallback] = None,
                 require_orders: bool = True, gap_timeout_s: float = 30.0):
        self._account_index = int(account_index)
        self._symbol_for_market_id = symbol_for_market_id
        self._update_callback = update_callback
        self._gap_callback = gap_callback
        self._require_orders = require_orders
        self._gap_timeout_s = float(gap_timeout_s)
        self._lock = threading.Lock()
        self._acked: set = set()
        self._gap_since_ms: Optional[int] = None
        self._gap_timeout_timer: Optional[threading.Timer] = None
        self._order_signatures: "OrderedDict[int, tuple]" = OrderedDict()
        self._seen_trade_ids: "OrderedDict[str, None]" = OrderedDict()

    ########################################
    # Connection / gap tracking
    ########################################

    def note_disconnect(self) -> None:
        """The socket dropped. Remember the *earliest* drop time of this outage (a failed reconnect
        attempt closes again before any ack, and must not move the gap's start forward), and arm the
        timeout that stops waiting on a channel whose ack never arrives."""
        with self._lock:
            if self._gap_since_ms is None:
                self._gap_since_ms = int(time.time() * 1000)
        self._arm_gap_timeout()

    def _arm_gap_timeout(self) -> None:
        timer = threading.Timer(self._gap_timeout_s, self._on_gap_timeout)
        timer.daemon = True
        with self._lock:
            old, self._gap_timeout_timer = self._gap_timeout_timer, timer
        if old is not None:
            old.cancel()
        timer.start()

    def _on_gap_timeout(self) -> None:
        """A reconnect did not get every required ack in time. Stop waiting on the auth-gated orders
        channel so the gap still fires on the trades ack (or immediately, if trades already acked)."""
        with self._lock:
            if self._gap_since_ms is None:
                return
            self._require_orders = False
        self._maybe_fire_gap()

    def begin_connection(self) -> None:
        """A socket just opened; the new connection has no acks yet."""
        with self._lock:
            self._acked = set()

    def note_orders_unavailable(self) -> None:
        """`account_all_orders` cannot be used (no auth token, or the server rejected it). Drop it from
        the required ack set so a trades-only stream still fires its reconnect `gap`; if the gap was already
        waiting on only this channel, fire it now."""
        with self._lock:
            self._require_orders = False
        self._maybe_fire_gap()

    def note_orders_available(self) -> None:
        """`account_all_orders` is (again) being subscribed -- e.g. a reconnect minted a token after an
        earlier failure. Restore it to the required ack set."""
        with self._lock:
            self._require_orders = True

    @property
    def _required_acks(self) -> set:
        return ({_ACCOUNT_ORDERS_CHANNEL, _ACCOUNT_TRADES_CHANNEL} if self._require_orders
                else {_ACCOUNT_TRADES_CHANNEL})

    def _maybe_fire_gap(self) -> None:
        """Fire `gap_callback(since_ms)` once, the first time every required channel of a *re*connection
        has acked. No-op on the first connection (`_gap_since_ms` is None)."""
        since_ms = None
        timer = None
        with self._lock:
            if self._gap_since_ms is not None and self._acked.issuperset(self._required_acks):
                since_ms, self._gap_since_ms = self._gap_since_ms, None
                timer, self._gap_timeout_timer = self._gap_timeout_timer, None
        if timer is not None:
            timer.cancel()
        if since_ms is not None and self._gap_callback:
            try:
                self._gap_callback(since_ms)
            except Exception:
                logging.exception('Lighter account gap callback raised')

    ########################################
    # Frame dispatch
    ########################################

    def apply_message(self, message: str) -> None:
        try:
            content = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            logging.warning('Non-JSON message from Lighter Account WebSocket (ignored): "%s"', message)
            return
        if not isinstance(content, dict):
            logging.warning('Unexpected Lighter Account WebSocket frame (ignored): %s', content)
            return

        msg_type = content.get('type')
        try:
            if msg_type in ('subscribed/' + _ACCOUNT_ORDERS_CHANNEL, 'update/' + _ACCOUNT_ORDERS_CHANNEL):
                # Record the ack BEFORE parsing the payload: a bad baseline must not stop the reconnect
                # gap from firing.
                if msg_type.startswith('subscribed/'):
                    self._handle_subscribed(_ACCOUNT_ORDERS_CHANNEL, content)
                self._handle_orders(content, is_snapshot=msg_type.startswith('subscribed/'))
            elif msg_type in ('subscribed/' + _ACCOUNT_TRADES_CHANNEL, 'update/' + _ACCOUNT_TRADES_CHANNEL):
                if msg_type.startswith('subscribed/'):
                    self._handle_subscribed(_ACCOUNT_TRADES_CHANNEL, content)
                self._handle_trades(content, is_snapshot=msg_type.startswith('subscribed/'))
            elif msg_type == 'error' or content.get('error') is not None:
                self._handle_error(content)
            elif msg_type == 'connected':
                logging.info('Lighter account WebSocket session established: %s', content.get('session_id'))
            elif isinstance(msg_type, str) and msg_type.startswith('unsubscribed'):
                logging.info('Lighter Account WebSocket unsubscribe ack: %s', content)
            else:
                logging.debug('Unhandled Lighter Account WebSocket message type %r: %s', msg_type, content)
        except Exception:
            logging.exception('Failed to process Lighter Account WebSocket frame: %s', message)

    def _handle_subscribed(self, channel: str, content: dict) -> None:
        logging.info('Lighter account WebSocket subscription ack for %s: %s', channel, content.get('channel'))
        with self._lock:
            self._acked.add(channel)
        self._maybe_fire_gap()

    def _handle_error(self, content: dict) -> None:
        logging.warning('Lighter Account WebSocket error frame: %s', content)
        error = content.get('error')
        message = error.get('message') if isinstance(error, dict) else error
        text = message.lower() if isinstance(message, str) else ""
        # Any auth/token-shaped error, or one naming the channel, means account_all_orders is unusable for
        # this connection; drop it from the required acks so the reconnect gap is not lost (Lighter's own
        # auth errors, e.g. "invalid auth: invalid deadline", do not always name the channel). A benign
        # "already subscribed"/duplicate reply to the periodic token refresh is not a failure.
        benign = any(word in text for word in ('already', 'duplicate', 'is subscribed', 'resubscrib'))
        looks_like_orders_failure = _ACCOUNT_ORDERS_CHANNEL in text or any(
            word in text for word in ('auth', 'token', 'deadline', 'unauthor')
        )
        if looks_like_orders_failure and not benign:
            logging.warning('Lighter account_all_orders was rejected; continuing with trades only')
            self.note_orders_unavailable()

    ########################################
    # Orders
    ########################################

    def _handle_orders(self, content: dict, is_snapshot: bool) -> None:
        market_map = content.get('orders')
        if not isinstance(market_map, dict):
            logging.warning('Lighter account_all_orders frame missing an "orders" mapping: %s', content)
            return
        for market_key, raw_orders in market_map.items():
            market_id = self._market_id(market_key)
            if market_id is None:
                logging.warning('Lighter account_all_orders frame has an unparseable market key %r', market_key)
                continue
            symbol = self._resolve_symbol(market_id)  # may be None, for an unresolved market
            for raw in raw_orders or []:
                # Per record so one malformed order does not swallow the rest of the frame.
                try:
                    order = _cls.LighterOrder.from_dict(raw)
                except Exception:
                    logging.exception('Malformed Lighter account order (skipped): %s', raw)
                    continue
                signature = self._order_signature(order)
                with self._lock:
                    previous = self._order_signatures.get(order.order_index)
                if is_snapshot or previous == signature:
                    with self._lock:
                        self._remember(self._order_signatures, order.order_index, signature)
                    continue
                if symbol is None:
                    # Baseline it anyway, so an unchanged re-send is not later mistaken for a new event
                    # once the symbol resolves (e.g. before the perpetual list has loaded).
                    with self._lock:
                        self._remember(self._order_signatures, order.order_index, signature)
                    continue
                try:
                    common = order.to_order_update_common(symbol)
                except Exception:
                    # Do NOT remember the signature: a later re-send should retry the conversion.
                    logging.exception('Failed to convert Lighter account order (skipped): %s', raw)
                    continue
                with self._lock:
                    self._remember(self._order_signatures, order.order_index, signature)
                self._deliver(common)

    @staticmethod
    def _order_signature(order: _cls.LighterOrder) -> tuple:
        """Everything whose change means a new lifecycle event worth pushing. `updated_at` alone would
        miss a status change the venue did not restamp; the status/remaining pair alone would miss a
        same-size re-open. Together they are specific enough to suppress unchanged re-sends in a full-set
        frame while still surfacing every real transition."""
        return (order.order_index, order.status, str(order.remaining_base_amount), order.updated_at)

    ########################################
    # Trades / fills
    ########################################

    def _handle_trades(self, content: dict, is_snapshot: bool) -> None:
        market_map = content.get('trades')
        if not isinstance(market_map, dict):
            logging.warning('Lighter account_all_trades frame missing a "trades" mapping: %s', content)
            return
        for market_key, raw_trades in market_map.items():
            market_id = self._market_id(market_key)
            if market_id is None:
                logging.warning('Lighter account_all_trades frame has an unparseable market key %r', market_key)
                continue
            symbol = self._resolve_symbol(market_id)  # may be None, for an unresolved market
            for raw in raw_trades or []:
                try:
                    trade = _cls.LighterTrade.from_dict(raw)
                except Exception:
                    logging.exception('Malformed Lighter account trade (skipped): %s', raw)
                    continue
                trade_id = trade.trade_id_str
                with self._lock:
                    already_seen = trade_id in self._seen_trade_ids
                if is_snapshot or already_seen or symbol is None:
                    # Snapshot trades and unresolved markets are seeded but never emitted; a snapshot
                    # in particular must not flood a freshly connected client.
                    with self._lock:
                        self._remember(self._seen_trade_ids, trade_id, None)
                    continue
                try:
                    common = trade.to_common(self._account_index, symbol)
                except Exception as e:
                    # `to_common` raises only when the trade belongs to neither side of this account,
                    # which a re-send cannot fix -- mark it seen (to avoid log spam) and keep going.
                    with self._lock:
                        self._remember(self._seen_trade_ids, trade_id, None)
                    logging.warning('Skipping Lighter account trade that cannot be converted: %s', e)
                    continue
                with self._lock:
                    self._remember(self._seen_trade_ids, trade_id, None)
                self._deliver(common)

    ########################################
    # Shared helpers
    ########################################

    def _deliver(self, record) -> None:
        try:
            self._update_callback(record)
        except Exception:
            logging.exception('Lighter account update callback raised for %s', record)

    @staticmethod
    def _market_id(market_key) -> Optional[int]:
        try:
            return int(market_key)
        except (TypeError, ValueError):
            return None

    def _resolve_symbol(self, market_id: int) -> Optional[str]:
        try:
            symbol = self._symbol_for_market_id(market_id)
        except Exception:
            logging.exception('Lighter account symbol resolver raised for market_id %s', market_id)
            return None
        if symbol is None:
            logging.warning('Lighter account update for unknown market_id %s; skipping', market_id)
        return symbol

    @staticmethod
    def _remember(cache: "OrderedDict", key, value) -> None:
        """Insert/refresh `key`, evicting oldest-first past `MAX_TRACKED`."""
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > LighterAccountStream.MAX_TRACKED:
            cache.popitem(last=False)


class LighterAccountWss(LighterFramingMixin, VenueWSSBase):
    """
    Lighter's account websocket: order lifecycle and fill events, on its own connection so a failure
    here can never take the order-book stream down (see the account-stream section comment above).

    `update_callback` receives one `_acct.OrderUpdate` / `_acct.Trade` per event; `gap_callback(since_ms)`
    fires once after a reconnect re-establishes the required subscriptions (see `LighterAccountStream`).
    Reconnect, ping/pong and the `LIGHTER_*` env knobs come from `VenueWSSBase`; the subscription set is
    fixed, so it is simply re-sent on every open, including reconnects. `account_all_trades` needs no auth;
    `account_all_orders` needs a short-lived native token, so when `auth_token_provider` is supplied it is
    minted on every open and refreshed on a timer at `token_refresh_fraction` of `token_deadline_s` (Lighter
    rejects a deadline much past ~6h). When it is None the stream runs trades-only.
    """

    #: How soon to retry a failed token refresh (a transient send/provider failure should not wait a full
    #: refresh interval, during which the current token could expire).
    TOKEN_REFRESH_RETRY_S = 60.0

    def __init__(self, account_index: int, auth_token_provider: Optional[Callable[[], str]],
                 symbol_for_market_id: Callable[[int], Optional[str]],
                 update_callback: AccountUpdateCallback, gap_callback: Optional[GapCallback] = None,
                 token_deadline_s: int = 6 * 3600, token_refresh_fraction: float = 0.8,
                 gap_timeout_s: float = 30.0):
        super().__init__(
            name="Lighter Account",
            url=WS_URL,
            env_prefix='LIGHTER',
            proxy_idx='LIGHTER',
            default_ping_interval_s=60,
        )
        self._account_index = int(account_index)
        self._auth_token_provider = auth_token_provider
        self._token_deadline_s = int(token_deadline_s)
        self._token_refresh_fraction = float(token_refresh_fraction)
        self._token_timer: Optional[threading.Timer] = None
        #: Bumped by every arm/cancel; a timer only re-arms itself if its generation is still current, so a
        #: reconnect (which arms a fresh timer) cannot leave an orphaned timer chain behind.
        self._token_generation = 0
        self._token_timer_lock = threading.Lock()
        self._stream = LighterAccountStream(
            account_index=self._account_index,
            symbol_for_market_id=symbol_for_market_id,
            update_callback=update_callback,
            gap_callback=gap_callback,
            require_orders=auth_token_provider is not None,
            gap_timeout_s=gap_timeout_s,
        )

    ########################################
    # Connection lifecycle
    ########################################

    def _on_open_impl(self):
        self._stream.begin_connection()
        if self._auth_token_provider is not None:
            try:
                self._ws.send(self._subscribe_orders_frame())
                self._stream.note_orders_available()
            except Exception:
                logging.exception('%s: failed to subscribe to account_all_orders; trades only', self._name)
                self._stream.note_orders_unavailable()
        else:
            self._stream.note_orders_unavailable()
        try:
            self._ws.send(self._subscribe_trades_frame())
        except Exception:
            logging.exception('%s: failed to subscribe to account_all_trades', self._name)
        self._arm_token_refresh()

    def _on_message_impl(self, message: str):
        self._stream.apply_message(message)

    def _on_reconnect_start(self):
        self._stream.note_disconnect()

    def _on_close_base(self, ws, close_status_code, close_msg):
        # Stop the refresh timer before the base queues a reconnect; the next open arms a fresh one.
        self._cancel_token_refresh()
        super()._on_close_base(ws, close_status_code, close_msg)

    ########################################
    # Subscribe frames + auth-token refresh
    ########################################

    def _subscribe_orders_frame(self) -> str:
        token = self._auth_token_provider()  # type: ignore[misc]  # guarded by the caller
        return json.dumps({
            "type": "subscribe",
            "channel": f"{_ACCOUNT_ORDERS_CHANNEL}/{self._account_index}",
            "auth": token,
        })

    def _subscribe_trades_frame(self) -> str:
        return json.dumps({
            "type": "subscribe",
            "channel": f"{_ACCOUNT_TRADES_CHANNEL}/{self._account_index}",
        })

    def _arm_token_refresh(self, delay: Optional[float] = None) -> None:
        if self._auth_token_provider is None:
            return
        if delay is None:
            delay = max(self._token_refresh_fraction * self._token_deadline_s, 1.0)
        with self._token_timer_lock:
            self._token_generation += 1
            generation = self._token_generation
            old, self._token_timer = self._token_timer, None
        if old is not None:
            old.cancel()
        timer = threading.Timer(delay, self._on_token_timer, args=(generation,))
        timer.daemon = True
        with self._token_timer_lock:
            if generation != self._token_generation:
                # A concurrent arm/cancel superseded us while we were building the timer; don't install it.
                return
            self._token_timer = timer
        timer.start()

    def _cancel_token_refresh(self) -> None:
        with self._token_timer_lock:
            self._token_generation += 1
            timer, self._token_timer = self._token_timer, None
        if timer is not None:
            timer.cancel()

    def _on_token_timer(self, generation: int) -> None:
        """The refresh timer fired. Re-send the orders subscription with a fresh token, then schedule the
        next refresh (or a short retry on failure). Does nothing if a newer arm/cancel superseded it or the
        socket was closed. Runs on the timer thread, so it never raises."""
        with self._token_timer_lock:
            if generation != self._token_generation or self._internally_closed:
                return
        refreshed = True
        try:
            self._ws.send(self._subscribe_orders_frame())
            logging.info('%s: refreshed the account_all_orders auth token', self._name)
        except Exception:
            refreshed = False
            logging.exception('%s: failed to refresh the account_all_orders token', self._name)
        with self._token_timer_lock:
            if generation != self._token_generation:
                return  # superseded while we were sending; let the newer timer own the schedule
        self._arm_token_refresh(None if refreshed else self.TOKEN_REFRESH_RETRY_S)
