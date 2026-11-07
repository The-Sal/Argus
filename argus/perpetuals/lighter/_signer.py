"""
Minimal `ctypes` wrapper around Lighter's native signer library.

This vendors the Apache-2.0 signer shared library (under `signers/`) and exposes just the
calls Argus needs, instead of depending on `lighter-sdk`. The SDK is a thin `ctypes` wrapper
over this same library plus an async OpenAPI client; it also pins `urllib3<2.1.0` (Argus runs
`urllib3==2.7.0`) and pulls in `aiohttp`, neither of which we want for a synchronous
dispatcher.

The library is stateful: `create_client` registers an API key for an `(api_key_index,
account_index)` pair, and the `sign_*` functions then use that registration. All signing is
LOCAL -- it performs no network I/O (submission is a separate `sendTx` POST, done by
`argus.perpetuals.lighter.exchange`). The one exception worth knowing about is
`check_client`, which the loader does not call; the dispatcher may use it if it wants to
verify the key is bound before trading.

ABI notes (mirrored from `lighter/signer_client.py` @ `lighter-sdk==1.1.6`):
  - Every `sign_*` returns a `_SignedTxResponse`; `txInfo` is the JSON body to POST,
    `txHash` the venue's predicted hash, and `err` non-null on failure (in which case the
    other pointers are still freed).
  - `CreateAuthToken(deadline, api_key_index, account_index)` returns
    `"{expiry_unix}:{account_index}:{api_key_index}:{hex}"` -- the token the account
    websocket wants in its subscribe frame's `auth` field. `deadline` is an absolute unix
    expiry, not a duration.
  - `order_expiry`: GTT/post-only require a non-zero expiry (`-1` selects the signer's
    28-day default); IOC uses `0`. `0` on a GTT order is rejected by the signer.
  - Chain id: 304 mainnet, 300 testnet (the SDK derives it from the base URL).
"""
import os
import time
import ctypes
import platform
import threading
from typing import Optional, Tuple
from argus.perpetuals.lighter import _errors as _ers



# --- ctypes structures (must match the native headers exactly) ---------------

class _ApiKeyResponse(ctypes.Structure):
    _fields_ = [
        ('privateKey', ctypes.c_void_p),
        ('publicKey', ctypes.c_void_p),
        ('err', ctypes.c_void_p),
    ]


class _StrOrErr(ctypes.Structure):
    _fields_ = [('str', ctypes.c_void_p), ('err', ctypes.c_void_p)]


class _SignedTxResponse(ctypes.Structure):
    _fields_ = [
        ('txType', ctypes.c_uint8),
        ('txInfo', ctypes.c_void_p),
        ('txHash', ctypes.c_void_p),
        ('messageToSign', ctypes.c_void_p),
        ('err', ctypes.c_void_p),
    ]


# --- platform loader ---------------------------------------------------------

_SIGNER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'signers')

#: (system, normalized arch) -> bundled library file name. Windows is deliberately not
#: vendored (the signer dll is Windows-only
#: and the deployment targets are macOS/Linux).
_LIBRARIES = {
    ('darwin', 'arm64'): 'lighter-signer-darwin-arm64.dylib',
    ('darwin', 'amd64'): 'lighter-signer-darwin-amd64.dylib',
    ('linux', 'amd64'): 'lighter-signer-linux-amd64.so',
    ('linux', 'arm64'): 'lighter-signer-linux-arm64.so',
}


def _normalized_arch(machine: str) -> str:
    machine = (machine or '').lower()
    if machine in ('amd64', 'x86_64', 'x64'):
        return 'amd64'
    if machine in ('arm64', 'aarch64'):
        return 'arm64'
    return machine


_signer = None
_signer_lock = threading.Lock()


def get_signer():
    """Load (once, thread-safely) and return the native signer library with every argtype pinned."""
    global _signer
    if _signer is not None:
        return _signer
    with _signer_lock:
        if _signer is not None:
            return _signer
        _signer = _load_signer()
        return _signer


def _load_signer():
    system = platform.system().lower()
    arch = _normalized_arch(platform.machine())
    filename = _LIBRARIES.get((system, arch))
    if filename is None:
        raise RuntimeError(
            f"Lighter's native signer is not bundled for {platform.system()}/{platform.machine()}. "
            f"Supported: macOS (arm64/x86_64) and Linux (x86_64/arm64)."
        )
    path = os.path.join(_SIGNER_DIR, filename)
    if not os.path.exists(path):
        raise RuntimeError(f"Lighter signer library is missing: {path}")

    signer = ctypes.CDLL(path)

    signer.GenerateAPIKey.argtypes = []
    signer.GenerateAPIKey.restype = _ApiKeyResponse

    signer.CreateClient.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_longlong]
    signer.CreateClient.restype = ctypes.c_void_p

    signer.CheckClient.argtypes = [ctypes.c_int, ctypes.c_longlong]
    signer.CheckClient.restype = ctypes.c_void_p

    signer.SignCreateOrder.argtypes = [
        ctypes.c_int, ctypes.c_longlong, ctypes.c_longlong, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, ctypes.c_longlong, ctypes.c_longlong, ctypes.c_int, ctypes.c_int, ctypes.c_uint8,
        ctypes.c_uint8, ctypes.c_uint8, ctypes.c_longlong, ctypes.c_int, ctypes.c_longlong,
    ]
    signer.SignCreateOrder.restype = _SignedTxResponse

    signer.SignCancelOrder.argtypes = [
        ctypes.c_int, ctypes.c_longlong, ctypes.c_uint8, ctypes.c_longlong, ctypes.c_int, ctypes.c_longlong,
    ]
    signer.SignCancelOrder.restype = _SignedTxResponse

    signer.SignCancelAllOrders.argtypes = [
        ctypes.c_int, ctypes.c_longlong, ctypes.c_int, ctypes.c_uint8, ctypes.c_longlong, ctypes.c_int, ctypes.c_longlong,
    ]
    signer.SignCancelAllOrders.restype = _SignedTxResponse

    signer.SignUpdateLeverage.argtypes = [
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint8, ctypes.c_longlong, ctypes.c_int, ctypes.c_longlong,
    ]
    signer.SignUpdateLeverage.restype = _SignedTxResponse

    signer.CreateAuthToken.argtypes = [ctypes.c_longlong, ctypes.c_int, ctypes.c_longlong]
    signer.CreateAuthToken.restype = _StrOrErr

    signer.Free.argtypes = [ctypes.c_void_p]
    signer.Free.restype = None

    return signer


def _decode_and_free(ptr) -> Optional[str]:
    """Decode a C string pointer and free it with the signer's own `Free` (critical on Windows,
    harmless elsewhere -- the allocator must match)."""
    if not ptr:
        return None
    try:
        c_str = ctypes.cast(ptr, ctypes.c_char_p).value
        try:
            return c_str.decode('utf-8') if c_str is not None else None
        except UnicodeDecodeError:
            return None
    finally:
        get_signer().Free(ptr)


def _decode_tx(result: _SignedTxResponse) -> Tuple[int, str, str]:
    """Turn a `_SignedTxResponse` into `(tx_type, tx_info_json, tx_hash)`, raising `SignerError`
    on a non-null `err`. Frees every pointer regardless."""
    err = _decode_and_free(result.err)
    tx_info = _decode_and_free(result.txInfo)
    tx_hash = _decode_and_free(result.txHash)
    _decode_and_free(result.messageToSign)
    if err:
        raise _ers.SignerError(err)
    if not tx_info or not tx_hash:
        raise _ers.SignerError("Signer returned no tx info/hash and no error")
    return int(result.txType), tx_info, tx_hash


def _strip_0x(value: str) -> str:
    return value[2:] if value.startswith('0x') else value


# --- API-key helpers ---------------------------------------------------------

def generate_api_key() -> Tuple[Optional[str], Optional[str]]:
    """Generate a fresh API key pair `(private_key, public_key)`, or raise `SignerError`."""
    result = get_signer().GenerateAPIKey()
    private_key = _decode_and_free(result.privateKey)
    public_key = _decode_and_free(result.publicKey)
    err = _decode_and_free(result.err)
    if err:
        raise _ers.SignerError(err)
    return private_key, public_key


def create_client(url: str, private_key: str, chain_id: int, api_key_index: int, account_index: int) -> None:
    """Register `(api_key_index, account_index)` with the signer for `private_key`. Raises
    `SignerError` if the key is malformed or the account index is rejected locally."""
    err_ptr = get_signer().CreateClient(
        url.encode('utf-8'), _strip_0x(private_key).encode('utf-8'), chain_id, api_key_index, account_index,
    )
    err = _decode_and_free(err_ptr)
    if err:
        raise _ers.SignerError(err)


def check_client(api_key_index: int, account_index: int) -> Optional[str]:
    """Ask the signer whether the registered key matches the venue. Returns an error string, or
    None when the key is bound correctly. NOTE: this is the one signer call that may do I/O."""
    return _decode_and_free(get_signer().CheckClient(api_key_index, account_index))


# --- signing -----------------------------------------------------------------

def sign_create_order(
    market_index: int,
    client_order_index: int,
    base_amount: int,
    price: int,
    is_ask: bool,
    order_type: int,
    time_in_force: int,
    reduce_only: bool,
    trigger_price: int,
    order_expiry: int,
    nonce: int,
    api_key_index: int,
    account_index: int,
    *,
    skip_nonce: int = 0,
    integrator_account_index: int = 0,
    integrator_taker_fee: int = 0,
    integrator_maker_fee: int = 0,
    self_trade_behavior_mode: int = 0,
    self_trade_equality_mode: int = 0,
) -> Tuple[int, str, str]:
    """Sign one create-order transaction locally. `base_amount`/`price` are the venue's scaled
    integers (see `exchange._scale`). Returns `(tx_type, tx_info_json, tx_hash)`."""
    result = get_signer().SignCreateOrder(
        market_index, client_order_index, base_amount, price, int(bool(is_ask)), order_type, time_in_force,
        int(bool(reduce_only)), trigger_price, order_expiry, integrator_account_index, integrator_taker_fee,
        integrator_maker_fee, self_trade_behavior_mode, self_trade_equality_mode, skip_nonce, nonce,
        api_key_index, account_index,
    )
    return _decode_tx(result)


def sign_cancel_order(
    market_index: int, order_index: int, nonce: int, api_key_index: int, account_index: int, *, skip_nonce: int = 0,
) -> Tuple[int, str, str]:
    """Sign one cancel-order transaction (by venue `order_index`)."""
    result = get_signer().SignCancelOrder(market_index, order_index, skip_nonce, nonce, api_key_index, account_index)
    return _decode_tx(result)


def sign_cancel_all_orders(
    time_in_force: int, timestamp_ms: int, cancel_all_market_index: int, nonce: int, api_key_index: int,
    account_index: int, *, skip_nonce: int = 0,
) -> Tuple[int, str, str]:
    """Sign a cancel-all transaction. `time_in_force` 0 = immediate, 1 = scheduled, 2 = abort;
    `cancel_all_market_index` 255 (`NIL_MARKET_INDEX`) means every market."""
    result = get_signer().SignCancelAllOrders(
        time_in_force, timestamp_ms, cancel_all_market_index, skip_nonce, nonce, api_key_index, account_index,
    )
    return _decode_tx(result)


def sign_update_leverage(
    market_index: int, initial_margin_fraction: int, margin_mode: int, nonce: int, api_key_index: int,
    account_index: int, *, skip_nonce: int = 0,
) -> Tuple[int, str, str]:
    """Sign an update-leverage transaction. `initial_margin_fraction` is ``10000 // leverage``
    (e.g. 10x -> 1000); `margin_mode` 0 = cross, 1 = isolated."""
    result = get_signer().SignUpdateLeverage(
        market_index, initial_margin_fraction, margin_mode, skip_nonce, nonce, api_key_index, account_index,
    )
    return _decode_tx(result)


def create_auth_token(deadline_s: int, api_key_index: int, account_index: int, *, now: Optional[int] = None) -> str:
    """Mint an auth token for the account websocket. `deadline_s` is a DURATION in seconds
    (e.g. 8 * 3600 for an 8-hour token); it is added to `now`/current time to form the absolute
    expiry the signer embeds. Returns `"{expiry_unix}:{account}:{api_key}:{hex}"`."""
    timestamp = int(time.time()) if now is None else int(now)
    result = get_signer().CreateAuthToken(int(deadline_s) + timestamp, api_key_index, account_index)
    token = _decode_and_free(result.str)
    err = _decode_and_free(result.err)
    if err:
        raise _ers.SignerError(err)
    if not token:
        raise _ers.SignerError("Signer returned no auth token and no error")
    return token


# --- tx type constants (native signer returns these) -------------------------

TX_TYPE_CREATE_ORDER = 14
TX_TYPE_CANCEL_ORDER = 15
TX_TYPE_CANCEL_ALL_ORDERS = 16
TX_TYPE_UPDATE_LEVERAGE = 20

#: `NIL_MARKET_INDEX`: "every market" on cancel-all.
NIL_MARKET_INDEX = 255

#: Order expiry sentinels.
DEFAULT_28_DAY_ORDER_EXPIRY = -1
DEFAULT_IOC_EXPIRY = 0
