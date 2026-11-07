"""
Al Ramz feed data shapes, wire decoders and the Protocol-2 converter.
=====================================================================

`feed.alramz.ae` (SignalR 2.1, hub ``integrationhub``) pushes pipe-delimited groups of
``key=value`` comma-pairs. The same envelope is reused by every stream, differing only in which
keys are present:

- ``SubscribeSymbol``               -- top-of-book quote + session stats, **partial** (only changed
                                       keys are sent, so updates must be merged into a last-known
                                       quote). Identity: ``1``=exchange, ``2``=ISIN.
- ``SubscribeIndex``                -- index quote. Identity: ``1``=exchange, ``2``=index code.
- ``SubscribeMarketDepthByPrice``   -- full aggregated book. Identity: ``1``=exchange, ``2``=ISIN;
                                       ``500``=depth N; bid price ``501+4k`` / bid size ``502+4k``;
                                       ask price ``503+4k`` / ask size ``504+4k`` (k=0..N-1),
                                       zero-padded, bids descending / asks ascending.

This module is deliberately network-free: everything here is exercised by `tests/test_alramz.py`
against frames captured from the real feed (``webtrade.alramz.ae.har``).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime

from argus.perpetuals.shared._classes import P2OrderBookConvertClass

# ---------------------------------------------------------------------------
# Wire decoding
# ---------------------------------------------------------------------------

#: Symbol/quote field key -> our name. Keys not listed here are kept verbatim in `.extra`
#: (they are session statistics we do not need for P2: 17/18/19/20/21/22/23).
SYMBOL_FIELDS = {
    "1": "exchange",
    "2": "isin",
    "3": "open",
    "4": "high",
    "5": "low",
    "6": "prev_close",
    "7": "volume",
    "8": "last",
    "9": "change",
    "10": "change_pct",
    "11": "bid",
    "12": "bid_size",
    "13": "ask",
    "14": "ask_size",
    "15": "trades",
    "16": "turnover",
    "24": "timestamp",
}

#: Index field key -> our name.
INDEX_FIELDS = {
    "1": "exchange",
    "2": "index",
    "3": "open",
    "4": "high",
    "5": "low",
    "6": "prev_close",
    "7": "volume",
    "8": "last",
    "9": "change",
    "10": "change_pct",
    "15": "trades",
    "16": "turnover",
    "24": "timestamp",
    "25": "advancing",
    "26": "declining",
    "27": "unchanged",
}

#: Identity fields that must always be present in a symbol/index group.
_SYMBOL_IDENTITY = ("1", "2")


def parse_group(group: str) -> dict:
    """
    Parse one pipe-group (``"1=NSDQ,2=US46090E1038,8=685.33"``) into a ``{key: value}`` dict.

    Empty tokens are ignored. Values keep their original string form (prices are decimals on the
    wire; converting to float is the caller's decision).
    """
    out = {}
    for pair in group.split(","):
        if "=" not in pair:
            continue
        key, _, value = pair.partition("=")
        key = key.strip()
        if key:
            out[key] = value.strip()
    return out


def parse_groups(raw: str) -> list:
    """
    Split an Al Ramz argument (``"a|b|c"`` or ``"|a|b"``) into a list of parsed group dicts,
    skipping empty groups. The leading ``|`` the app sometimes sends is therefore harmless.
    """
    return [parse_group(g) for g in (raw or "").split("|") if g.strip()]


_ISO_FRAC_RE = re.compile(r"(\.\d{6})\d+")


def iso_to_ms(value: str) -> int | None:
    """
    Convert the feed's ISO-8601 timestamp (e.g. ``2026-11-04T08:38:37.1840486+04:00``) to epoch
    milliseconds. Returns ``None`` if it cannot be parsed. The feed emits 7 fractional digits,
    which ``datetime.fromisoformat`` truncates tolerantly, but older interpreters are stricter, so
    we clamp to microseconds first.
    """
    if not value:
        return None
    try:
        cleaned = _ISO_FRAC_RE.sub(r"\1", value.strip())
        return int(datetime.fromisoformat(cleaned).timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def _num(value, default: float = 0.0) -> float:
    """Decimal-string -> float, defensively (the feed occasionally sends ``""``)."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Symbol (top-of-book) quote
# ---------------------------------------------------------------------------


@dataclass
class SymbolQuote:
    """
    Last-known top-of-book quote for one ``EXCHANGE,ISIN`` symbol.

    ``SubscribeSymbol`` pushes are **partial**: a frame may carry only ``11``/``13`` (bid/ask), only
    ``8`` (last), or a full snapshot. `update()` merges by key, so the object always reflects the
    full last-known state -- exactly the merge TradingView's dispatcher does for partial qsd frames.
    """

    exchange: str = ""
    isin: str = ""
    open: str = "0"
    high: str = "0"
    low: str = "0"
    prev_close: str = "0"
    volume: str = "0"
    last: str = "0"
    change: str = "0"
    change_pct: str = "0"
    bid: str = "0"
    bid_size: str = "0"
    ask: str = "0"
    ask_size: str = "0"
    trades: str = "0"
    turnover: str = "0"
    timestamp: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def symbol(self) -> str:
        return f"{self.exchange},{self.isin}"

    @classmethod
    def from_group(cls, kv: dict) -> "SymbolQuote":
        quote = cls()
        quote.update(kv)
        return quote

    def update(self, kv: dict) -> bool:
        """
        Merge one decoded group into this quote. Returns ``True`` if the identity is present (i.e.
        the group legitimately belongs to a symbol). Unknown keys accumulate in `extra`.
        """
        if not kv.get("1") or not kv.get("2"):
            return False
        for key, value in kv.items():
            name = SYMBOL_FIELDS.get(key)
            if name is None:
                self.extra[key] = value
            else:
                setattr(self, name, value)
        return True

    def timestamp_ms(self) -> int | None:
        return iso_to_ms(self.timestamp)

    def order_book(self) -> dict:
        """
        Synthesize the level-1 book from the quote fields (``11``/``12``/``13``/``14``). A side is
        omitted when its price is missing/zero, matching the shared P2 convention (missing levels
        become ``0,0`` and are padded by the converter).
        """
        bids, asks = [], []
        if _num(self.bid) > 0:
            bids.append({"price": self.bid, "size": self.bid_size or "0"})
        if _num(self.ask) > 0:
            asks.append({"price": self.ask, "size": self.ask_size or "0"})
        return {"bids": bids, "asks": asks}

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "isin": self.isin,
            "bid": self.bid,
            "bid_size": self.bid_size,
            "ask": self.ask,
            "ask_size": self.ask_size,
            "last": self.last,
            "change": self.change,
            "change_pct": self.change_pct,
            "volume": self.volume,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Index quote
# ---------------------------------------------------------------------------


@dataclass
class IndexQuote:
    """Last-known quote for one index (``EXCHANGE,INDEXCODE``), merged the same way as `SymbolQuote`."""

    exchange: str = ""
    index: str = ""
    open: str = "0"
    high: str = "0"
    low: str = "0"
    prev_close: str = "0"
    volume: str = "0"
    last: str = "0"
    change: str = "0"
    change_pct: str = "0"
    trades: str = "0"
    turnover: str = "0"
    timestamp: str = ""
    advancing: str = ""
    declining: str = ""
    unchanged: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def symbol(self) -> str:
        return f"{self.exchange},{self.index}"

    @classmethod
    def from_group(cls, kv: dict) -> "IndexQuote":
        quote = cls()
        quote.update(kv)
        return quote

    def update(self, kv: dict) -> bool:
        if not kv.get("1") or not kv.get("2"):
            return False
        for key, value in kv.items():
            name = INDEX_FIELDS.get(key)
            if name is None:
                self.extra[key] = value
            else:
                setattr(self, name, value)
        return True

    def timestamp_ms(self) -> int | None:
        return iso_to_ms(self.timestamp)

    def to_dict(self) -> dict:
        return {
            "index": self.symbol,
            "exchange": self.exchange,
            "code": self.index,
            "last": self.last,
            "change": self.change,
            "change_pct": self.change_pct,
            "volume": self.volume,
            "advancing": self.advancing,
            "declining": self.declining,
            "unchanged": self.unchanged,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Full-depth book
# ---------------------------------------------------------------------------

#: bid/ask price keys are interleaved with stride 4: bid=501+4k, ask=503+4k (sizes +1).
_BID_PRICE_BASE = 501
_ASK_PRICE_BASE = 503


@dataclass
class DepthBook:
    """
    Aggregated market depth for one ``EXCHANGE,ISIN`` symbol, as pushed by
    ``SubscribeMarketDepthByPrice``. Unlike `SymbolQuote` these frames are complete snapshots, so
    each one replaces the stored book wholesale.
    """

    exchange: str = ""
    isin: str = ""
    depth: int = 0
    bids: list = field(default_factory=list)  # [{"price": str, "size": str}, ...] best-first
    asks: list = field(default_factory=list)
    timestamp: str = ""

    @property
    def symbol(self) -> str:
        return f"{self.exchange},{self.isin}"

    @classmethod
    def from_group(cls, kv: dict) -> "DepthBook | None":
        """Build a book from one decoded depth group; ``None`` if the group is not a valid book."""
        if not kv.get("1") or not kv.get("2"):
            return None
        try:
            depth = int(kv.get("500", "0"))
        except (TypeError, ValueError):
            return None
        book = cls(exchange=kv["1"], isin=kv["2"], depth=depth, timestamp=kv.get("24", ""))
        for k in range(depth):
            bid_price = kv.get(str(_BID_PRICE_BASE + 4 * k))
            ask_price = kv.get(str(_ASK_PRICE_BASE + 4 * k))
            if _num(bid_price) > 0:
                book.bids.append({"price": bid_price, "size": kv.get(str(_BID_PRICE_BASE + 4 * k + 1), "0")})
            if _num(ask_price) > 0:
                book.asks.append({"price": ask_price, "size": kv.get(str(_ASK_PRICE_BASE + 4 * k + 1), "0")})
        return book

    def order_book(self) -> dict:
        return {"bids": list(self.bids), "asks": list(self.asks)}


def merge_order_books(depth: dict | None, quote: dict | None, top_of_book: dict | None) -> dict:
    """
    Combine a full-depth book with a (possibly partial) top-of-book overlay into one book, the way
    Hyperliquid overlays ``bbo`` onto its ``l2Book`` snapshot.

    Precedence: an explicit depth book wins for levels 2+; the freshest top-of-book best bid/ask
    (``top_of_book``) replaces depth's level 1 when present and not crossed. ``quote`` is the
    symbol-quote fallback used only when no depth book exists at all (top-of-book-only symbols).
    """
    base = depth or quote or {"bids": [], "asks": []}
    bids = [dict(level) for level in base.get("bids", [])]
    asks = [dict(level) for level in base.get("asks", [])]

    if top_of_book:
        overlay = top_of_book
        bid = (overlay.get("bids") or [None])[0]
        ask = (overlay.get("asks") or [None])[0]
        if bid:
            if bids:
                bids[0] = dict(bid)
            else:
                bids = [dict(bid)]
        if ask:
            if asks:
                asks[0] = dict(ask)
            else:
                asks = [dict(ask)]

    return {"bids": bids, "asks": asks}


# ---------------------------------------------------------------------------
# Protocol-2 converter
# ---------------------------------------------------------------------------


class AlRamzP2ConvertClass(P2OrderBookConvertClass):
    """
    Duck-typed adapter for ``argus.protocol.transmit_mkt_data_with_protocol_2``.

    P2 CSV layout (identical to the shared perpetuals order-book format, so existing clients
    decode it unchanged)::

        bid1_px,bid1_sz, bid2_px,bid2_sz, ... ask1_px,ask1_sz, ... <exchange_ts_ms>,<server_ts_s>

    ``order_book_depth`` levels are always emitted; missing levels render as ``0,0``. That is what
    lets a top-of-book-only symbol and a full-depth symbol share one packet shape -- a top-of-book
    subscription simply fills level 1 and zero-pads the rest.
    """

    def __init__(self, symbol: str, book: dict, order_book_depth: int, timestamp_ms: int | None = None):
        market_data = {
            symbol: {"bids": list(book.get("bids", [])), "asks": list(book.get("asks", []))},
            "timestamp": timestamp_ms if timestamp_ms is not None else int(time.time() * 1000),
        }
        super().__init__(
            symbol=symbol,
            lookup_key=symbol,
            market_data=market_data,
            order_book_depth=order_book_depth,
        )

    @classmethod
    def build(cls, symbol: str, book: dict, order_book_depth: int, timestamp_ms: int | None = None) -> "AlRamzP2ConvertClass":
        return cls(symbol=symbol, book=book, order_book_depth=order_book_depth, timestamp_ms=timestamp_ms)

    #: Field order clients decode with (`Protocol2Parser(decoding_order=...)`); level pairs expand
    #: to 2*order_book_depth entries, then the two timestamps.
    @staticmethod
    def field_order(order_book_depth: int) -> list:
        fields = []
        for i in range(order_book_depth):
            fields += [f"bid{i + 1}", f"bid{i + 1}_size"]
        for i in range(order_book_depth):
            fields += [f"ask{i + 1}", f"ask{i + 1}_size"]
        fields += ["exchange_ts_ms", "transmission_time"]
        return fields
