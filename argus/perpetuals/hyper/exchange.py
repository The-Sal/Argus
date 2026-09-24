"""
Signed order execution for Hyperliquid (the `exchange` endpoint).

This module is to *trading* what `argus.perpetuals.hyper.rest` is to reads: the one place
that knows how to sign and submit account actions to Hyperliquid. It complements
`HyperLiquidRest` (unsigned public `info` reads) with the wallet's write surface --
placing limit orders and canceling them. Order modification is deliberately not part of
this surface (see the dispatcher's routing table).

Note that Hyperliquid has no immediate "cancel all" action: the only bulk cancel is
``scheduleCancel`` (a one-shot trigger at least 5s in the future, max 10/day), which this
module deliberately does not expose.

Protocol
--------
Every action is POSTed to ``<base>/exchange`` as

    {
        "action": <the action dict, exactly as packed for signing>,
        "nonce": <unix ms>,
        "signature": {"r": "0x..", "s": "0x..", "v": 27|28},
        "vaultAddress": null,
        "expiresAfter": null
    }

and is EIP-712 signed with the wallet's private key under Hyperliquid's L1 ("phantom
agent") scheme:

1. The action dict is msgpack-packed **in its own key order**, then an 8-byte big-endian
   nonce, a vault tag (0x00 when no vault) and -- only if set -- a 0x00-prefixed
   8-byte big-endian ``expiresAfter`` are appended. The whole blob is keccak-256 hashed.
2. That hash becomes the ``connectionId`` of a synthetic ``Agent`` struct
   (``{"source": "a"|"b", "connectionId": <hash>}`` -- "a" for mainnet, "b" for testnet),
   signed under the Exchange domain (chainId 1337, zero verifying contract: these actions
   are verified off-chain, so the domain identifies the protocol, not a contract).

Because the server recomputes step 1 from the JSON it receives, the dict we pack is also
the dict we send -- key order is part of the signature. All action builders below insert
keys in one fixed order for exactly that reason.

Asset ids
---------
The wire's ``a`` / ``asset`` fields take a *global* asset id: default-dex coins are their
raw universe index, and each builder-deployed (HIP-3) dex owns a 10000-wide slot starting
at 110000, assigned in the order the ``perpDexs`` info endpoint returns them (its first
entry is null for the default dex). ``resolve_asset_id`` maps "BTC" / "xyz:AAPL" to that
id, caching ``meta`` and ``perpDexs`` briefly so a burst of orders costs at most one
extra info call per dex.

Responses
---------
A successful submission answers ``{"status": "ok", "response": <action result>}``; a
rejected one (bad signature, insufficient margin at the venue level, ...) answers
``{"status": "err", "response": "<message>"}`` and is raised as
``_errors.ExchangeActionError``. Order placement additionally reports per-order outcomes
inside ``response["data"]["statuses"]`` (resting / filled / error) -- a venue-level
rejection of one order, e.g. insufficient margin, comes back that way with the HTTP call
itself still "ok". See `_classes.OrderPlacementResult`.
"""
import time
import msgpack
from decimal import Decimal
from eth_utils import keccak
from eth_account import Account
from utils3.networking import Session
from typing import Any, Dict, Optional
from eth_account.messages import encode_typed_data
from argus.perpetuals.hyper import _errors as _ers
from argus.perpetuals.hyper import _classes as _cls
from argus.perpetuals.shared import _errors as _shared_ers



MAINNET_API_URL = "https://api.hyperliquid.xyz"

#: EIP-712 domain for L1 (exchange) actions.
_L1_DOMAIN = {
    "name": "Exchange",
    "version": "1",
    "chainId": 1337,
    "verifyingContract": "0x0000000000000000000000000000000000000000",
}

_L1_TYPES = {
    "Agent": [
        {"name": "source", "type": "string"},
        {"name": "connectionId", "type": "bytes32"},
    ],
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
}

#: First asset id of the HIP-3 (builder-deployed) perp dex space, and the width of each
#: dex's slot. Spot assets live at 10000+; default-dex perps are raw universe indices.
_HIP3_ASSET_OFFSET = 110_000
_HIP3_ASSET_SLOT_WIDTH = 10_000

#: Time-in-force values the exchange accepts on limit orders ("Alo" = auction limit order).
VALID_TIFS = ("Gtc", "Ioc", "Alo")


def _split_coin(coin: str) -> tuple:
    """Split a venue coin name into (dex, bare) -- ("", "BTC") or ("xyz", "AAPL")."""
    if ":" in coin:
        dex, bare = coin.split(":", 1)
        return dex, bare
    return "", coin


def _to_wire_number(value: Any) -> str:
    """
    Render a price/size for the exchange wire: at most 8 decimal places, trailing zeros
    stripped ("50000.50" -> "50000.5", "100" stays "100"). Accepts int/float/str/Decimal;
    floats go through ``str()`` first so binary-float artifacts don't leak onto the wire.
    Raises if rounding to 8 places would change the value.
    """
    if isinstance(value, str):
        d = Decimal(value)
    elif isinstance(value, (int, Decimal)):
        d = Decimal(value)
    else:
        d = Decimal(str(value))
    if d != d.quantize(Decimal("0.00000001")):
        raise _ers.HyperLiquidError(f"price/size {value!r} has more than 8 decimal places")
    rendered = f"{d.normalize():f}"
    return "0" if rendered == "-0" else rendered


def validate_cloid(cloid: str) -> str:
    """
    A client order id is a 0x-prefixed 16-byte hex string (32 hex chars). Returns it
    unchanged, or raises -- a malformed cloid must fail here rather than surface later as
    an opaque venue rejection.
    """
    if not isinstance(cloid, str) or not cloid.startswith("0x") or len(cloid) != 34:
        raise _ers.HyperLiquidError(f"Invalid cloid {cloid!r}: expected 0x + 32 hex chars (16 bytes)")
    try:
        int(cloid[2:], 16)
    except ValueError:
        raise _ers.HyperLiquidError(f"Invalid cloid {cloid!r}: not hex")
    return cloid


class HyperLiquidExchange:
    """
    Signed client for Hyperliquid's `exchange` endpoint: place limit orders, cancel by
    oid or cloid, cancel all.

    Complements `HyperLiquidRest`, which holds the same wallet credentials but only signs
    nothing (its reads are unsigned). Instantiate one per wallet; it is safe to share
    across threads for the meta caches (they are TTL-guarded, worst case a redundant
    fetch) -- order submission itself is a single HTTP POST.

    :param wallet_address: The account's *master* address (the same one that signs).
    :param private_key: Hex private key of `wallet_address`.
    :param base_url: Mainnet by default; point at the testnet API to trade test funds.
        The EIP-712 "source" tag ("a"/"b") is derived from it, so a mainnet key cannot be
        replayed on testnet and vice versa.
    :param meta_ttl_s: How long resolved universes/dex slots are cached (seconds).
    """

    def __init__(
        self,
        wallet_address: str,
        private_key: str,
        base_url: str = MAINNET_API_URL,
        meta_ttl_s: float = 60.0,
    ):
        self.wallet_address = wallet_address
        self.account = Account.from_key(private_key)
        self.base_url = base_url.rstrip("/")
        self.is_mainnet = self.base_url == MAINNET_API_URL
        self.session = Session()
        self.session.headers = {"Content-Type": "application/json"}
        self._meta_ttl_s = meta_ttl_s
        # dex name -> (monotonic fetch time, [coin names in universe order])
        self._universe_cache: Dict[str, tuple] = {}
        # (monotonic fetch time, {dex name: asset-id offset}); "" is always 0
        self._asset_offset_cache: Optional[tuple] = None

    # --- info lookups (for asset-id resolution) ----------------------------

    def _info(self, body: dict) -> Any:
        return self.session.post(url=self.base_url + "/info", json=body).json()

    def _universe(self, dex: str) -> Dict[str, int]:
        """The dex's universe as an insertion-ordered {coin name: szDecimals} map; the
        position of a name in it is the asset's index within that dex's asset space."""
        now = time.monotonic()
        cached = self._universe_cache.get(dex)
        if cached is not None and now - cached[0] < self._meta_ttl_s:
            return cached[1]
        meta = self._info({"type": "meta", "dex": dex})
        universe = {asset["name"]: int(asset["szDecimals"]) for asset in meta.get("universe", [])}
        self._universe_cache[dex] = (now, universe)
        return universe

    def _asset_offsets(self) -> Dict[str, int]:
        now = time.monotonic()
        if self._asset_offset_cache is not None and now - self._asset_offset_cache[0] < self._meta_ttl_s:
            return self._asset_offset_cache[1]
        response = self._info({"type": "perpDexs"})
        offsets: Dict[str, int] = {"": 0}
        for i, entry in enumerate(entry for entry in response if entry is not None):
            offsets[entry["name"]] = _HIP3_ASSET_OFFSET + i * _HIP3_ASSET_SLOT_WIDTH
        self._asset_offset_cache = (now, offsets)
        return offsets

    def resolve_asset_id(self, coin: str) -> int:
        """
        The global asset id for `coin` -- what the wire's ``a`` / ``asset`` fields take.
        `coin` is the venue's own name for the asset: bare for the default dex ("BTC") and
        dex-prefixed for a HIP-3 dex ("xyz:AAPL"), exactly as `meta` reports it. Raises
        `InvalidCoinError` for an unknown coin or dex.
        """
        dex, bare = _split_coin(coin)
        universe = self._universe(dex)
        # HIP-3 `meta` names are already dex-prefixed; default-dex names are bare. Accept
        # either spelling ("xyz:AAPL" or, for the default dex, "BTC").
        key = coin if coin in universe else bare
        if key not in universe:
            raise _shared_ers.InvalidCoinError(
                f"Coin {coin!r} is not in the universe of dex {dex or '(default)'!r}"
            )
        offsets = self._asset_offsets()
        if dex not in offsets:
            raise _shared_ers.InvalidCoinError(f"Unknown HIP-3 dex {dex!r} for coin {coin!r}")
        return offsets[dex] + list(universe).index(key)

    def sz_decimals(self, coin: str) -> int:
        """The coin's size precision (number of decimal places the venue accepts for size)."""
        dex, bare = _split_coin(coin)
        universe = self._universe(dex)
        key = coin if coin in universe else bare
        if key not in universe:
            raise _shared_ers.InvalidCoinError(
                f"Coin {coin!r} is not in the universe of dex {dex or '(default)'!r}"
            )
        return universe[key]

    def round_price(self, coin: str, price: Any, is_spot: bool = False) -> Decimal:
        """
        Round `price` to the venue's tick rules: 5 significant figures, then at most
        ``6 - szDecimals`` decimal places for perps (``8 - szDecimals`` for spot). The
        venue rejects anything finer-grained ("Order has invalid price"), so limit prices
        must go through this before they reach the wire.
        """
        rounded = float(f"{float(price):.5g}")
        decimals = (8 if is_spot else 6) - self.sz_decimals(coin)
        return Decimal(repr(round(rounded, decimals)))

    # --- signing -------------------------------------------------------------

    @staticmethod
    def action_hash(
        action: dict,
        nonce: int,
        vault_address: Optional[str] = None,
        expires_after: Optional[int] = None,
    ) -> bytes:
        """
        Step 1 of the L1 signing scheme: keccak-256 over msgpack(action) + 8-byte BE
        nonce + vault tag (+ 0x00-prefixed 8-byte BE expiresAfter when set). Exposed as a
        plain static method so tests can pin the vector without a wallet.
        """
        data = msgpack.packb(action)
        data += nonce.to_bytes(8, "big")
        if vault_address is None:
            data += b"\x00"
        else:
            hex_part = vault_address[2:] if vault_address.startswith("0x") else vault_address
            data += b"\x01" + bytes.fromhex(hex_part)
        if expires_after is not None:
            data += b"\x00" + expires_after.to_bytes(8, "big")
        return keccak(data)

    def sign_action(self, action: dict, nonce: int) -> Dict[str, Any]:
        """
        Steps 1-2: hash the action and EIP-712-sign the resulting phantom-agent struct.
        Returns {"r", "s", "v"} in the hex/int shape the exchange endpoint expects.
        """
        h = self.action_hash(action, nonce)
        payload = {
            "domain": _L1_DOMAIN,
            "types": _L1_TYPES,
            "primaryType": "Agent",
            "message": {"source": "a" if self.is_mainnet else "b", "connectionId": h},
        }
        structured = encode_typed_data(full_message=payload)
        signed = self.account.sign_message(structured)
        # Fixed-width 32-byte hex: eth_utils.to_hex(int) drops leading zero bytes (r < 2**248
        # is ~1/256 per signature), and the venue expects a 32-byte r/s.
        return {
            "r": "0x" + format(signed["r"], "064x"),
            "s": "0x" + format(signed["s"], "064x"),
            "v": signed["v"],
        }

    def _post_action(self, action: dict) -> Any:
        """Sign `action` with a fresh ms nonce, POST it, and unwrap the ok/err envelope."""
        nonce = int(time.time() * 1000)
        payload = {
            "action": action,
            "nonce": nonce,
            "signature": self.sign_action(action, nonce),
            "vaultAddress": None,
            "expiresAfter": None,
        }
        response = self.session.post(url=self.base_url + "/exchange", json=payload).json()
        if not isinstance(response, dict) or response.get("status") != "ok":
            message = response.get("response") if isinstance(response, dict) else str(response)
            raise _ers.ExchangeActionError(
                f"Hyperliquid rejected action {action.get('type')!r}: {message}"
            )
        return response["response"]

    # --- trading ---------------------------------------------------------------

    def place_order(
        self,
        coin: str,
        side: str,
        price: Any,
        size: Any,
        tif: str = "Gtc",
        reduce_only: bool = False,
        cloid: Optional[str] = None,
    ) -> _cls.OrderPlacementResult:
        """
        Place one limit order. `side` is "buy" or "sell"; `price` is the limit price (it is
        rounded to the venue's tick rules -- 5 significant figures and at most
        ``6 - szDecimals`` decimals -- before submission) and `size` is in coins; `tif` is
        "Gtc", "Ioc" or "Alo"; `reduce_only` closes instead of opens; `cloid` is an optional
        client order id. Returns the per-order outcome -- a venue-level rejection (e.g.
        insufficient margin) is reported in the result's `error` field, not raised.
        """
        side_norm = side.lower()
        if side_norm not in ("buy", "sell"):
            raise _ers.HyperLiquidError(f"Invalid side {side!r}: expected 'buy' or 'sell'")
        if tif not in VALID_TIFS:
            raise _ers.HyperLiquidError(f"Invalid order type {tif!r}: expected one of {list(VALID_TIFS)}")

        wire_order = {
            "a": self.resolve_asset_id(coin),
            "b": side_norm == "buy",
            "p": _to_wire_number(self.round_price(coin, price)),
            "s": _to_wire_number(size),
            "r": bool(reduce_only),
            "t": {"limit": {"tif": tif}},
        }
        if cloid is not None:
            wire_order["c"] = validate_cloid(cloid)
        # Key order below is part of the signature (see module docstring).
        action = {
            "type": "order",
            "orders": [wire_order],
            "grouping": "na",
        }
        response = self._post_action(action)
        return _cls.OrderPlacementResult.from_response(coin, response)

    def cancel_by_oid(self, coin: str, oid: int) -> _cls.CancelResult:
        """Cancel one order by its venue id. The venue only lists oids it actually canceled."""
        action = {
            "type": "cancel",
            "cancels": [{"a": self.resolve_asset_id(coin), "o": int(oid)}],
        }
        response = self._post_action(action)
        return _cls.CancelResult.from_response(coin, response, requested_oids=[int(oid)])

    def cancel_by_cloid(self, coin: str, cloid: str) -> _cls.CancelResult:
        """Cancel one order by its client order id (0x + 32 hex chars)."""
        action = {
            "type": "cancelByCloid",
            "cancels": [{"asset": self.resolve_asset_id(coin), "cloid": validate_cloid(cloid)}],
        }
        response = self._post_action(action)
        return _cls.CancelResult.from_response(coin, response)


def _demo() -> None:
    """Manual smoke check: python -m argus.perpetuals.hyper.exchange (READ-ONLY -- signs a
    throwaway action and verifies it recovers to the configured wallet; submits nothing)."""
    import os

    from argus._argus_utils import load_dotenv

    load_dotenv()
    exchange = HyperLiquidExchange(
        wallet_address=os.environ["HYPERLIQUID_WALLET_ADDRESS"],
        private_key=os.environ["HYPERLIQUID_PRIVATE_KEY"],
    )
    action = {"type": "cancel", "cancels": [{"a": 0, "o": 1}]}
    signature = exchange.sign_action(action, nonce=1_700_000_000_000)
    recovered = Account.recover_message(
        encode_typed_data(full_message={
            "domain": _L1_DOMAIN,
            "types": _L1_TYPES,
            "primaryType": "Agent",
            "message": {"source": "a" if exchange.is_mainnet else "b",
                        "connectionId": exchange.action_hash(action, 1_700_000_000_000)},
        }),
        vrs=[signature["v"], signature["r"], signature["s"]],
    )
    print(f"signed action recovers to: {recovered}")
    print(f"configured wallet:         {exchange.wallet_address}")
    assert recovered.lower() == exchange.wallet_address.lower(), "signature does not recover to the wallet!"
    print("OK -- signing verified (nothing was submitted)")


if __name__ == "__main__":
    _demo()
