"""
Signed order execution for Lighter (the `sendTx` endpoint).

This module is to *trading* what `argus.perpetuals.lighter.rest` is to reads: the one
place that knows how to scale, sign and submit account actions to Lighter. It complements
`LighterRest` (unsigned public/account reads) with the write surface -- placing limit
orders (singly or in batches), canceling them (singly, in batches, or all at once) and
setting per-market leverage. Order modification is deliberately not part of this surface.

Protocol
--------
Unlike Hyperliquid, Lighter does NOT EIP-712-sign in Python. The action is signed by the
vendored native signer (`_signer.py`, loaded with `ctypes`), which returns the transaction
type plus a JSON `tx_info` body. That body is then POSTed as a form field:

    POST <base>/api/v1/sendTx       form: tx_type=<int>, tx_info=<json string>
    POST <base>/api/v1/sendTxBatch  form: tx_types=<json int array>, tx_infos=<json str array>

A `code: 200` means the sequencer ACCEPTED the transaction, not that it executed:
"the sequencer could still reject it if parameters are not set properly". There is no
synchronous per-order status (Hyperliquid returns `{oid, status}`), so `place_order`
answers `status: "submitted"` and confirmation comes from the account stream /
`get_orders` by `client_order_index` (Lighter's cloid analog) or `order_index`.

Nonces
------
Lighter keys replay protection on `(account_index, api_key_index)`, is strictly
sequential, and caps at 2^48-1. The first use of a key fetches
`GET /api/v1/nextNonce` lazily; after that the counter is handed out locally under a
process-wide lock. A batch must use ONE key with CONSECUTIVE nonces, so `place_orders`
allocates a block atomically. Nonces are not persisted -- the first use after a restart
re-reads the server, which self-heals.

Scaling
-------
`BaseAmount = size * 10**size_decimals`, `Price = price * 10**price_decimals`, per
market, from `LighterRest.get_markets()` (`Market.size_decimals` / `price_decimals` /
`min_base_amount` / `min_quote_amount`). Prices are rounded half-even and sizes rounded
down to the market's decimals before scaling, so a caller can pass arbitrary precision.
"""
import json
import time
import functools
import itertools
import threading
from utils3.networking import Session
from argus.perpetuals.lighter import _signer
from typing import Any, Dict, List, Optional, Tuple
from argus.perpetuals.lighter import _errors as _ers
from argus.perpetuals.lighter import _classes as _cls
from argus.perpetuals.lighter.rest import LighterRest
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_EVEN
from argus.perpetuals.shared import errors as _shared_ers

from argus.perpetuals.shared._classes import (
    BatchCancelResult,
    CancelOutcome,
    OrderRequest,
    PositionLeverage,
)


MAINNET_API_URL = 'https://mainnet.zklighter.elliot.ai'

#: Our cap on orders per batch. The venue's documented cap is 50; we stay at it.
MAX_BATCH_SIZE = 50

#: Transactions per REST request. The venue allows up to MAX_BATCH_SIZE in one batch but documents
#: only ~15 as safe over REST (larger payloads belong on the websocket `jsonapi/sendtxbatch`), so
#: bigger batches are submitted as several consecutive REST requests.
REST_BATCH_CHUNK = 15

#: How long (seconds) the market list is trusted before it is re-fetched. Decimals, minimums and
#: leverage caps can change and new markets can list while a dispatcher runs.
MARKET_CACHE_TTL_S = 300.0

#: How long (seconds) a leverage read is reused for the order path (see `read_leverage`). OFF by default:
#: the read is the roll-back target, so it is fresh unless an operator opts in because the weight-300
#: `account` call is eating their rate limit (see LIGHTER_LEVERAGE_CACHE_TTL_S).
LEVERAGE_CACHE_TTL_S = 0.0

#: order_type 0 == LIMIT (see `_signer`; stop/TWAP are out of parity scope).
ORDER_TYPE_LIMIT = 0

#: (time_in_force, order_expiry) per wire TIF spelling. GTT/post-only need a non-zero expiry;
#: `-1` selects the signer's 28-day default. IOC uses 0.
_TIF = {
    'Gtc': (1, _signer.DEFAULT_28_DAY_ORDER_EXPIRY),
    'Ioc': (0, _signer.DEFAULT_IOC_EXPIRY),
    'Alo': (2, _signer.DEFAULT_28_DAY_ORDER_EXPIRY),
}

_CROSS_MARGIN_MODE = 0
_ISOLATED_MARGIN_MODE = 1

_MAX_CLIENT_ORDER_INDEX = (1 << 48) - 1

# One process-wide nonce allocator per (base_url, account_index, api_key_index); see module docstring.
_nonce_lock = threading.Lock()
_last_nonce: Dict[Tuple[str, int, int], int] = {}

# Lighter requires a key's nonces to reach the sequencer in order, and a batch to be consecutive.
# Allocation is cheap but the sign+POST is not, so every write holds this lock from allocation through
# submission (via `_serialized`). Without it two threads could send N+1 before N and the venue would
# reject N+1 as an invalid nonce.
_submit_lock = threading.Lock()


def _serialized(method):
    """Serialize a write method's allocate -> sign -> submit as one critical section (market metadata
    is warmed first, outside the lock)."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        # Refresh market metadata BEFORE taking the process-wide lock so a slow REST call never
        # blocks every other write.
        self._ensure_markets()
        with _submit_lock:
            return method(self, *args, **kwargs)
    return wrapper

# Client order indexes are uint48 and must be unique across all markets. Seed from the clock so a
# fresh process does not reuse the low indexes an earlier process may still have resting.
_client_order_index = itertools.count(int(time.time() * 1000) & _MAX_CLIENT_ORDER_INDEX)


def _next_client_order_index() -> int:
    value = next(_client_order_index) & _MAX_CLIENT_ORDER_INDEX
    return value or next(_client_order_index) & _MAX_CLIENT_ORDER_INDEX   # 0 is reserved ("unset")


def _chain_id_for(base_url: str, chain_id: Optional[int]) -> int:
    """The SDK's chain-id derivation: 304 mainnet, 300 testnet, 466324 rh mainnet."""
    if chain_id is not None:
        return chain_id
    if 'mainnet.zklighter' in base_url:
        return 304
    if 'testnet.zklighter' in base_url:
        return 300
    if 'api.rh.lighter' in base_url:
        return 466324
    if 'api.rh-testnet.lighter' in base_url:
        return 300
    return 304


class LighterExchange:
    """
    Signed client for Lighter's `sendTx` endpoint.

    Complements `LighterRest`, which holds the same account credentials but never signs. Instantiate
    one per `(account_index, api_key_index)`; the native signer registration and the nonce counter are
    keyed accordingly, so it is safe to share across dispatcher threads.

    :param account_index: The account's Lighter index (e.g. 1).
    :param api_key_index: The API key slot the venue has bound to `api_key_private_key`.
    :param api_key_private_key: The API key private key (NOT the L1 wallet key -- leverage and
        orders are both signed with the API key).
    :param base_url: Mainnet by default; point at testnet to trade test funds.
    :param chain_id: Override the chain id derived from `base_url`.
    :param rest: The unsigned REST client used for market metadata and leverage read-back. When
        omitted, a `LighterRest(account_index=...)` is created.
    """

    def __init__(
        self,
        account_index: int,
        api_key_index: int,
        api_key_private_key: str,
        base_url: str = MAINNET_API_URL,
        chain_id: Optional[int] = None,
        rest: Optional[LighterRest] = None,
        market_cache_ttl_s: float = MARKET_CACHE_TTL_S,
        leverage_cache_ttl_s: float = LEVERAGE_CACHE_TTL_S,
    ):
        self.account_index = int(account_index)
        self.api_key_index = int(api_key_index)
        self.base_url = base_url.rstrip('/')
        self.chain_id = _chain_id_for(self.base_url, chain_id)
        self.rest = rest if rest is not None else LighterRest(account_index=self.account_index)
        self.session = Session()
        # No Content-Type header: requests sets application/x-www-form-urlencoded for `data=`,
        # which is what sendTx/sendTxBatch expect.
        self._perp_by_symbol: Dict[str, _cls.Perpetual] = {}
        self._market_cache_ttl_s = float(market_cache_ttl_s)
        self._markets_loaded_at = 0.0
        self._markets_lock = threading.Lock()
        self._leverage_cache_ttl_s = float(leverage_cache_ttl_s)
        self._leverage_cache: Dict[str, Tuple[float, PositionLeverage]] = {}

        _signer.create_client(
            self.base_url, api_key_private_key, self.chain_id, self.api_key_index, self.account_index,
        )

    # --- market metadata -----------------------------------------------------

    def _ensure_markets(self) -> None:
        """Load the market list, and re-load it once `market_cache_ttl_s` has passed. An unknown coin
        does NOT trigger a re-fetch (that would turn a typo into a REST call per retry); it simply waits
        for the next TTL refresh. A failed refresh keeps serving the previous list."""
        if self._perp_by_symbol and time.time() - self._markets_loaded_at < self._market_cache_ttl_s:
            return
        with self._markets_lock:
            if self._perp_by_symbol and time.time() - self._markets_loaded_at < self._market_cache_ttl_s:
                return
            try:
                markets = {p.name: p for p in self.rest.get_markets()}
            except Exception:
                if not self._perp_by_symbol:
                    raise
                # Stale data beats no data; retry after a short back-off instead of on every call.
                self._markets_loaded_at = time.time() - self._market_cache_ttl_s + 30.0
                return
            self._perp_by_symbol = markets
            self._markets_loaded_at = time.time()

    def _perpetual(self, coin: str) -> _cls.Perpetual:
        """The `Perpetual` for `coin` from the (TTL-refreshed) market cache."""
        self._ensure_markets()
        perp = self._perp_by_symbol.get(coin)
        if perp is None:
            raise _shared_ers.InvalidCoinError(f"Coin {coin!r} is not a valid perpetual on Lighter")
        return perp

    def max_leverage(self, coin: str) -> int:
        """The market's maximum leverage, derived from its minimum initial-margin fraction."""
        market = self._perpetual(coin).market
        imf = market.min_initial_margin_fraction or market.default_initial_margin_fraction
        if not imf:
            raise _ers.LighterError(f"{coin} reports a zero initial-margin fraction; cannot derive max leverage")
        return max(1, int(Decimal(10000) / Decimal(imf)))

    def allowed_margin_modes(self, coin: str) -> List[str]:
        """Cross and isolated, unless the market is isolated-only (market_flags bit 0)."""
        market = self._perpetual(coin).market
        return ['isolated'] if (market.market_flags & 1) else ['cross', 'isolated']

    # --- nonces --------------------------------------------------------------

    def _fetch_next_nonce(self) -> int:
        raw = self.session.get(
            url=f'{self.base_url}/api/v1/nextNonce',
            params={'account_index': self.account_index, 'api_key_index': self.api_key_index},
        )
        self._raise_if_rate_limited(raw, '/api/v1/nextNonce')
        response = raw.json()
        if not isinstance(response, dict) or response.get('code') not in (None, 200):
            raise _ers.ExchangeActionError(f"Could not fetch nextNonce: {response!r}")
        return int(response['nonce'])

    def _allocate_nonces(self, count: int) -> List[int]:
        """Reserve `count` consecutive nonces for this key. The first call fetches `nextNonce` once."""
        key = (self.base_url, self.account_index, self.api_key_index)
        with _nonce_lock:
            if key not in _last_nonce:
                _last_nonce[key] = self._fetch_next_nonce() - 1
            start = _last_nonce[key] + 1
            _last_nonce[key] += count
            return list(range(start, start + count))

    def _release_nonces(self, count: int) -> None:
        """Give back `count` nonces after a LOCAL signing failure (nothing was submitted). Only safe
        because `_serialized` holds `_submit_lock` across the whole allocate -> sign -> submit sequence."""
        key = (self.base_url, self.account_index, self.api_key_index)
        with _nonce_lock:
            if key in _last_nonce:
                _last_nonce[key] = max(_last_nonce[key] - count, 0)

    def _reset_nonce(self) -> None:
        """Drop the cached counter for this key so the next allocation re-reads `nextNonce`. Used after
        a submission whose fate is uncertain (an HTTP error or a venue rejection): the server tells us
        the authoritative next nonce on the re-read, which self-heals either way."""
        key = (self.base_url, self.account_index, self.api_key_index)
        with _nonce_lock:
            _last_nonce.pop(key, None)

    # --- submission ----------------------------------------------------------

    @staticmethod
    def _raise_if_rate_limited(raw, path: str) -> None:
        """HTTP 429 means Lighter refused the request before processing it: a definitive failure, and
        one a caller can act on (back off), unlike a bare JSON-decode error."""
        if getattr(raw, 'status_code', None) != 429:
            return
        retry_after = None
        try:
            retry_after = raw.headers.get('Retry-After')
        except Exception:
            pass
        hint = f"; retry after {retry_after}s" if retry_after else ""
        raise _ers.RateLimitError(f"Lighter rate limit hit on {path} (HTTP 429){hint}")

    def _post(self, path: str, data: dict) -> dict:
        try:
            raw = self.session.post(url=f'{self.base_url}{path}', data=data)
            self._raise_if_rate_limited(raw, path)
            response = raw.json()
        except Exception:
            # The request may or may not have reached the venue: force a nextNonce re-read before the
            # next submission rather than trusting our local counter.
            self._reset_nonce()
            raise
        if not isinstance(response, dict) or response.get('code') != 200:
            self._reset_nonce()
            if isinstance(response, dict):
                raise _ers.ExchangeActionError(
                    f"Lighter rejected {path}: code={response.get('code')} {response.get('message')!r}"
                )
            raise _ers.ExchangeActionError(f"Lighter rejected {path}: non-JSON response {response!r}")
        return response

    def _send_tx(self, tx_type: int, tx_info: str) -> dict:
        return self._post('/api/v1/sendTx', {'tx_type': tx_type, 'tx_info': tx_info})

    def _send_tx_batch(self, tx_types: List[int], tx_infos: List[str]) -> dict:
        if len(tx_types) != len(tx_infos):
            raise _ers.LighterError("tx_types and tx_infos must have the same length")
        if not tx_types:
            raise _ers.LighterError("sendTxBatch needs at least one transaction")
        return self._post('/api/v1/sendTxBatch', {
            'tx_types': json.dumps(tx_types), 'tx_infos': json.dumps(tx_infos),
        })

    # --- order scaling -------------------------------------------------------

    def _prepare_order(self, request: OrderRequest) -> Tuple[_cls.Market, int, int, int, int, Decimal, Decimal]:
        """
        Validate `request` and scale it to the venue's integers.

        Returns `(market, base_amount, price_int, time_in_force, order_expiry, price, size)`. Sizes
        round DOWN (never exceed what the caller asked for) and prices round half-even; both then
        have to satisfy `min_base_amount` / `min_quote_amount`.
        """
        perp = self._perpetual(request.coin)
        market = perp.market
        side = str(request.side).lower()
        if side not in ('buy', 'sell'):
            raise _ers.SignerError(f"Invalid side {request.side!r}: expected 'buy' or 'sell'")
        if request.tif not in _TIF:
            raise _ers.SignerError(f"Invalid order type {request.tif!r}: expected one of {sorted(_TIF)}")

        size = Decimal(str(request.size)).quantize(Decimal(1).scaleb(-market.size_decimals), rounding=ROUND_DOWN)
        price = Decimal(str(request.price)).quantize(Decimal(1).scaleb(-market.price_decimals),
                                                    rounding=ROUND_HALF_EVEN)
        if size <= 0 or price <= 0:
            raise _ers.SignerError(f"Order size and price must be positive (size={size}, price={price})")
        if size < market.min_base_amount:
            raise _ers.SignerError(f"Order size {size} is below the market minimum {market.min_base_amount}")
        if size * price < market.min_quote_amount:
            raise _ers.SignerError(
                f"Order notional {size * price} is below the market minimum {market.min_quote_amount}"
            )
        base_amount = int(size * (10 ** market.size_decimals))
        price_int = int(price * (10 ** market.price_decimals))
        # The native ABI takes price as a C int; reject rather than let ctypes silently truncate.
        if not -(2 ** 31) <= price_int < 2 ** 31:
            raise _ers.SignerError(f"Scaled price {price_int} does not fit in a 32-bit signed integer")
        time_in_force, order_expiry = _TIF[request.tif]
        return market, base_amount, price_int, time_in_force, order_expiry, price, size

    def _resolve_client_order_index(self, cloid: Optional[Any]) -> int:
        """`cloid` is Lighter's `client_order_index` (uint48). Allocate one when the caller omits it."""
        if cloid is None:
            return _next_client_order_index()
        if isinstance(cloid, bool):
            raise _ers.SignerError(f"Invalid cloid {cloid!r}: expected a uint48 client order index")
        try:
            value = int(cloid)
        except (TypeError, ValueError):
            raise _ers.SignerError(f"Invalid cloid {cloid!r}: expected a uint48 client order index")
        # 0 is how Lighter reports an order with NO client index (e.g. placed from the web UI), so an
        # order sent with 0 could never be referenced by it again.
        if not 1 <= value <= _MAX_CLIENT_ORDER_INDEX:
            raise _ers.SignerError(
                f"Invalid cloid {value}: must be in 1..{_MAX_CLIENT_ORDER_INDEX} "
                f"(0 means 'no client order index'; omit cloid to have one allocated)"
            )
        return value

    # --- trading -------------------------------------------------------------

    @_serialized
    def place_order(self, coin: str, side: str, price: Any, size: Any, tif: str = 'Gtc',
                    reduce_only: bool = False, cloid: Optional[Any] = None) -> _cls.LighterOrderPlacementResult:
        """
        Sign and submit one limit order. Returns a result whose `status` is always "submitted" (see
        module docstring); a venue-level envelope rejection raises `ExchangeActionError`.
        """
        request = OrderRequest(coin=coin, side=side, price=price, size=size, tif=tif,
                               reduce_only=reduce_only, cloid=cloid)
        client_order_index = self._resolve_client_order_index(cloid)
        market, base_amount, price_int, tf, expiry, rounded_price, _size = self._prepare_order(request)
        nonce = self._allocate_nonces(1)[0]
        try:
            tx_type, tx_info, tx_hash = _signer.sign_create_order(
                market_index=market.market_id,
                client_order_index=client_order_index,
                base_amount=base_amount,
                price=price_int,
                is_ask=str(side).lower() == 'sell',
                order_type=ORDER_TYPE_LIMIT,
                time_in_force=tf,
                reduce_only=reduce_only,
                trigger_price=0,
                order_expiry=expiry,
                nonce=nonce,
                api_key_index=self.api_key_index,
                account_index=self.account_index,
            )
        except Exception:
            self._release_nonces(1)
            raise
        self._send_tx(tx_type, tx_info)
        return _cls.LighterOrderPlacementResult(
            coin=coin, client_order_index=client_order_index, tx_hash=tx_hash, status='submitted',
            price=rounded_price, requested_price=Decimal(str(price)),
        )

    @_serialized
    def place_orders(self, requests: List[OrderRequest]) -> List[_cls.LighterOrderPlacementResult]:
        """
        Sign and submit several limit orders sharing ONE API key and consecutive nonces, via
        `sendTxBatch`. Everything is validated before anything is signed, so one bad item raises and
        nothing is sent. Results are positional; each has `status: "submitted"`.
        """
        if not requests:
            raise _ers.LighterError("place_orders needs at least one order")
        if len(requests) > MAX_BATCH_SIZE:
            raise _ers.LighterError(f"Too many orders in one batch: {len(requests)} > {MAX_BATCH_SIZE}")
        prepared = []  # (request, client_order_index, market, base_amount, price_int, tf, expiry, rounded_price)
        for request in requests:
            client_order_index = self._resolve_client_order_index(request.cloid)
            market, base_amount, price_int, tf, expiry, rounded_price, _size = self._prepare_order(request)
            prepared.append((request, client_order_index, market, base_amount, price_int, tf, expiry, rounded_price))
        client_indexes = [p[1] for p in prepared]
        if len(set(client_indexes)) != len(client_indexes):
            raise _ers.SignerError("Duplicate client order index within the batch")

        nonces = self._allocate_nonces(len(prepared))
        tx_types: List[int] = []
        tx_infos: List[str] = []
        signer_hashes: List[str] = []
        try:
            for nonce, (request, client_order_index, market, base_amount, price_int, tf, expiry, _price) in zip(nonces, prepared):
                tx_type, tx_info, signer_hash = _signer.sign_create_order(
                    market_index=market.market_id,
                    client_order_index=client_order_index,
                    base_amount=base_amount,
                    price=price_int,
                    is_ask=str(request.side).lower() == 'sell',
                    order_type=ORDER_TYPE_LIMIT,
                    time_in_force=tf,
                    reduce_only=request.reduce_only,
                    trigger_price=0,
                    order_expiry=expiry,
                    nonce=nonce,
                    api_key_index=self.api_key_index,
                    account_index=self.account_index,
                )
                tx_types.append(tx_type)
                tx_infos.append(tx_info)
                signer_hashes.append(signer_hash)
        except Exception:
            self._release_nonces(len(prepared))
            raise
        # Submit in REST-sized chunks, in order, under the lock (nonces are consecutive across chunks).
        # A rejected FIRST chunk raises: nothing was accepted. A rejection of a LATER chunk cannot undo the
        # earlier ones, so the unsent orders come back as per-order errors and the caller (the shared
        # mixin) keeps leverage on any market that did get an order accepted. A transport failure on a
        # later chunk is ambiguous and propagates as-is.
        hashes: List[Optional[str]] = []
        errors: Dict[int, str] = {}
        for start in range(0, len(prepared), REST_BATCH_CHUNK):
            end = min(start + REST_BATCH_CHUNK, len(prepared))
            try:
                hashes.extend(self._submit_chunk(tx_types[start:end], tx_infos[start:end], signer_hashes[start:end]))
            except _ers.ExchangeActionError as e:
                if start == 0:
                    raise
                for i in range(start, len(prepared)):
                    errors[i] = f"not submitted: {e}"
                hashes.extend([None] * (len(prepared) - start))
                break
        return [
            _cls.LighterOrderPlacementResult(
                coin=request.coin, client_order_index=client_order_index,
                tx_hash=hashes[i], status='rejected' if i in errors else 'submitted', error=errors.get(i),
                price=price, requested_price=Decimal(str(request.price)),
            )
            for i, (request, client_order_index, _m, _b, _p, _t, _e, price) in enumerate(prepared)
        ]

    def _submit_chunk(self, tx_types: List[int], tx_infos: List[str], signer_hashes: List[str]) -> List[str]:
        """POST one chunk (one tx via `sendTx`, several via `sendTxBatch`); returns one tx hash per tx."""
        if len(tx_types) > 1:
            response = self._send_tx_batch(tx_types, tx_infos)
            # `sendTxBatch` answers `tx_hash` as a LIST; the single-order path uses a scalar.
            hashes = response.get('tx_hash') if isinstance(response.get('tx_hash'), list) else []
        else:
            response = self._send_tx(tx_types[0], tx_infos[0])
            hashes = [response.get('tx_hash') or signer_hashes[0]]
        return hashes if len(hashes) == len(tx_types) else list(signer_hashes)

    @_serialized
    def cancel_order_index(self, coin: str, order_index: int) -> _cls.LighterCancelResult:
        """Cancel one order by its venue `order_index`. `status` of the result is implied "submitted"."""
        market = self._perpetual(coin).market
        order_index = int(order_index)
        nonce = self._allocate_nonces(1)[0]
        try:
            tx_type, tx_info, tx_hash = _signer.sign_cancel_order(
                market_index=market.market_id, order_index=order_index, nonce=nonce,
                api_key_index=self.api_key_index, account_index=self.account_index,
            )
        except Exception:
            self._release_nonces(1)
            raise
        self._send_tx(tx_type, tx_info)
        return _cls.LighterCancelResult(coin=coin, canceled_order_indexes=[order_index], tx_hashes=[tx_hash])

    def cancel_order(self, coin: str, kind: str, identifier: Any) -> _cls.LighterCancelResult:
        """Cancel one order named by `kind` (`order_index` | `order_id` | `client_order_index`). A kind other
        than `order_index` is resolved through the open orders first (one REST read, outside the lock)."""
        order_index = self.resolve_order_index(kind, identifier)
        if order_index is None:
            raise _shared_ers.InvalidCoinError(f"No open order with {kind} {identifier} on {coin}")
        return self.cancel_order_index(coin, order_index)

    def cancel_many(self, items: List[Tuple[str, str, Any]]) -> BatchCancelResult:
        """
        Cancel several orders. `items` are `(coin, kind, identifier)` triples; `kind` is `"order_index"`
        (used as-is), `"order_id"` or `"client_order_index"` (both resolved through ONE open-orders read,
        each against its own id space so a client index can never match another order's id). An item that
        cannot be resolved, or names an unknown coin, is an error outcome -- it does not fail the batch.
        Resolved items are sent in REST-sized chunks; outcomes are optimistic (a successful `sendTx` marks
        the item ok) and a rejected chunk marks just that chunk's items as errors.
        """
        if not items:
            raise _ers.LighterError("cancel_many needs at least one order")
        maps = self._open_order_maps() if any(kind != 'order_index' for _c, kind, _i in items) else None
        outcomes: List[Optional[CancelOutcome]] = [None] * len(items)
        sendable: List[Tuple[int, str, int, int]] = []  # (item position, coin, market_id, order_index)
        for position, (coin, kind, identifier) in enumerate(items):
            try:
                market = self._perpetual(coin).market
            except _shared_ers.InvalidCoinError as e:
                outcomes[position] = CancelOutcome(str(identifier), coin, False, str(e))
                continue
            order_index = self.resolve_order_index(kind, identifier, maps)
            if order_index is None:
                outcomes[position] = CancelOutcome(str(identifier), coin, False,
                                                   f"no open order with {kind} {identifier}")
                continue
            sendable.append((position, coin, market.market_id, order_index))
        if sendable:
            sent = self._send_cancels([(coin, market_id, order_index) for _p, coin, market_id, order_index in sendable])
            for (position, _c, _m, _o), outcome in zip(sendable, sent):
                outcomes[position] = outcome
        return BatchCancelResult(outcomes=outcomes)

    @_serialized
    def _send_cancels(self, prepared: List[Tuple[str, int, int]]) -> List[CancelOutcome]:
        """Sign and submit `(coin, market_id, order_index)` cancels, REST_BATCH_CHUNK per request. Nonces are
        allocated per chunk (a failed chunk resets the counter, so a pre-signed next chunk would be stale)."""
        outcomes: List[CancelOutcome] = []

        def failed(chunk, reason):
            return [CancelOutcome(str(order_index), coin, False, reason) for coin, _m, order_index in chunk]

        for start in range(0, len(prepared), REST_BATCH_CHUNK):
            chunk = prepared[start:start + REST_BATCH_CHUNK]
            nonces = self._allocate_nonces(len(chunk))
            tx_types: List[int] = []
            tx_infos: List[str] = []
            try:
                for nonce, (_coin, market_id, order_index) in zip(nonces, chunk):
                    tx_type, tx_info, _hash = _signer.sign_cancel_order(
                        market_index=market_id, order_index=order_index, nonce=nonce,
                        api_key_index=self.api_key_index, account_index=self.account_index,
                    )
                    tx_types.append(tx_type)
                    tx_infos.append(tx_info)
            except Exception as e:
                self._release_nonces(len(chunk))
                if start == 0:
                    raise
                outcomes.extend(failed(prepared[start:], f"not submitted: {e}"))
                break
            try:
                self._submit_chunk(tx_types, tx_infos, ['' for _ in tx_types])
            except _ers.RateLimitError as e:
                # More requests would only be refused too: report the rest as not submitted.
                outcomes.extend(failed(prepared[start:], str(e)))
                break
            except Exception as e:
                outcomes.extend(failed(chunk, str(e)))
                continue
            outcomes.extend(CancelOutcome(str(order_index), coin, True) for coin, _m, order_index in chunk)
        return outcomes

    @_serialized
    def cancel_all(self, market_index: Optional[int] = None) -> dict:
        """
        Cancel every resting order via Lighter's NATIVE immediate cancel-all (transaction type 16),
        optionally scoped to one `market_index` (None = all markets). This is a genuine improvement over
        Hyperliquid, which has no immediate cancel-all and sweeps client-side. Only acceptance is
        synchronous, so the result reports `status: "submitted"`.
        """
        target = _signer.NIL_MARKET_INDEX if market_index is None else int(market_index)
        nonce = self._allocate_nonces(1)[0]
        try:
            # Immediate cancel-all (TIF 0) must carry a NIL timestamp (0): the signer rejects a
            # non-zero time for it ("CancelAllTime should be nil"). Scheduled (1) is out of scope.
            tx_type, tx_info, tx_hash = _signer.sign_cancel_all_orders(
                time_in_force=0, timestamp_ms=0, cancel_all_market_index=target,
                nonce=nonce, api_key_index=self.api_key_index, account_index=self.account_index,
            )
        except Exception:
            self._release_nonces(1)
            raise
        self._send_tx(tx_type, tx_info)
        return {'status': 'submitted', 'txHash': tx_hash, 'marketIndex': target}

    @_serialized
    def update_leverage(self, coin: str, leverage: int, margin_mode: str = 'cross') -> None:
        """
        Set the leverage (and margin mode) used for NEW positions on `coin`. `leverage` must be an
        integer 1..`max_leverage`; `margin_mode` is "cross" or "isolated". The wire takes the initial
        margin FRACTION `10000 // leverage`. The venue may reject lowering leverage / switching mode
        with an open position (`ExchangeActionError`).
        """
        if isinstance(leverage, bool) or not isinstance(leverage, int):
            raise _ers.SignerError(f"Invalid leverage {leverage!r}: expected an integer")
        maximum = self.max_leverage(coin)
        if not 1 <= leverage <= maximum:
            raise _ers.SignerError(f"Invalid leverage {leverage}: {coin} allows 1..{maximum}")
        if margin_mode not in ('cross', 'isolated'):
            raise _ers.SignerError(f"Invalid margin_mode {margin_mode!r}: expected 'cross' or 'isolated'")
        if margin_mode == 'cross' and 'cross' not in self.allowed_margin_modes(coin):
            raise _ers.SignerError(f"{coin} is isolated-only; it cannot use cross margin")
        market = self._perpetual(coin).market
        mode = _ISOLATED_MARGIN_MODE if margin_mode == 'isolated' else _CROSS_MARGIN_MODE
        nonce = self._allocate_nonces(1)[0]
        try:
            tx_type, tx_info, _hash = _signer.sign_update_leverage(
                market_index=market.market_id, initial_margin_fraction=10000 // leverage, margin_mode=mode,
                nonce=nonce, api_key_index=self.api_key_index, account_index=self.account_index,
            )
        except Exception:
            self._release_nonces(1)
            raise
        try:
            self._send_tx(tx_type, tx_info)
        except Exception:
            self._leverage_cache.pop(coin, None)   # fate unknown: never serve a guess
            raise
        self._leverage_cache[coin] = (time.time(), PositionLeverage(type=margin_mode, value=leverage))

    def read_leverage(self, coin: str, fresh: bool = False) -> PositionLeverage:
        """
        The leverage in force for the account on `coin` -- the roll-back target for the shared trading
        mixin. Lighter persists per-market margin settings, so a market the account has traded but is
        flat in still reports a row (we read with `active_only=False`). A market the account has NEVER
        touched has no row, so its market default applies (`Market.default_initial_margin_fraction`).

        The read is a weight-300 `account` call, which rate-limited accounts may want to economise. With
        `leverage_cache_ttl_s > 0` (opt-in; default 0 = always read) a read is reused for that long; our own
        `update_leverage` refreshes it and a failed one evicts it. A change made outside this process (the
        web UI) can then go unseen for up to that long -- and the read is the roll-back target -- so keep it
        short. `fresh=True` (as `get_leverage` uses) always forces a read.
        """
        cached = self._leverage_cache.get(coin)
        if not fresh and cached is not None and time.time() - cached[0] < self._leverage_cache_ttl_s:
            return cached[1]
        leverage = self._read_leverage_uncached(coin)
        self._leverage_cache[coin] = (time.time(), leverage)
        return leverage

    def _read_leverage_uncached(self, coin: str) -> PositionLeverage:
        market = self._perpetual(coin).market
        summary = self.rest.get_account(active_only=False)
        for position in summary.positions:
            if position.market_id != market.market_id:
                continue
            if position.initial_margin_fraction and position.initial_margin_fraction > 0:
                leverage = self._leverage_from_position_fraction(coin, position.initial_margin_fraction)
                mode = 'isolated' if position.margin_mode == _ISOLATED_MARGIN_MODE else 'cross'
                return PositionLeverage(type=mode, value=leverage)
        default_imf = market.default_initial_margin_fraction or market.min_initial_margin_fraction
        leverage = max(1, int(Decimal(10000) / Decimal(default_imf))) if default_imf else 20
        return PositionLeverage(type='cross', value=leverage)

    def _leverage_from_position_fraction(self, coin: str, fraction: Decimal) -> int:
        """
        Convert an `AccountPosition.initial_margin_fraction` to an integer leverage.

        The account endpoint reports this field as a PERCENTAGE (the repo's recorded fixtures show
        `"10.00"` == 10% == 10x), whereas the market's `default_initial_margin_fraction` /
        `min_initial_margin_fraction` are basis points (live BTC: 500 == 5% == 20x, which is how the
        native `update_leverage` wire frame encodes it too). Rather than trust either unit blindly, take
        the basis-point reading when it falls inside the market's valid leverage range and the percentage
        reading otherwise. For every leverage the venue offers (1x..~50x) the two ranges do not overlap,
        so this is unambiguous. Feeding the wrong unit back as a roll-back target would otherwise send an
        out-of-range leverage and trip the fatal contingency.
        """
        maximum = self.max_leverage(coin)
        as_basis_points = int(Decimal(10000) / fraction)
        if 1 <= as_basis_points <= maximum:
            return as_basis_points
        return max(1, int(Decimal(100) / fraction))

    def auth_token(self, deadline_s: int = 8 * 3600) -> str:
        """Mint an account-websocket auth token valid for `deadline_s` seconds from now."""
        return _signer.create_auth_token(deadline_s, self.api_key_index, self.account_index)

    # --- internal helpers ----------------------------------------------------

    def _open_order_maps(self) -> Tuple[Dict[str, int], Dict[str, int]]:
        """One REST read of the open orders, as `(by_order_id, by_client_order_index)` -> `order_index`.
        Kept as two maps on purpose: both ids are integers and can coincide across different orders."""
        by_order_id: Dict[str, int] = {}
        by_client_index: Dict[str, int] = {}
        for order in self.rest.get_account_active_orders():
            order_index = int(order.order_index)
            order_id = getattr(order, 'order_id', None)
            if order_id is not None:
                by_order_id[str(order_id)] = order_index
            by_client_index[str(order.client_order_index)] = order_index
        return by_order_id, by_client_index

    def resolve_order_index(self, kind: str, identifier: Any,
                            maps: Optional[Tuple[Dict[str, int], Dict[str, int]]] = None) -> Optional[int]:
        """Turn a caller identifier into a venue `order_index`, or None when no open order matches. An
        `order_index` is used as-is; `order_id` / `client_order_index` are looked up (pass `maps` from
        `_open_order_maps` to reuse one read across many items)."""
        if kind == 'order_index':
            return int(identifier)
        if kind not in ('order_id', 'client_order_index'):
            raise _ers.LighterError(f"Unknown cancel id kind {kind!r}")
        by_order_id, by_client_index = maps if maps is not None else self._open_order_maps()
        return (by_order_id if kind == 'order_id' else by_client_index).get(str(identifier))
