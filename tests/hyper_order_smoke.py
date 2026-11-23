"""
Live round-trip smoke test for Hyperliquid order execution.

Submits real (but deliberately market-safe) orders on the configured account:

  1. reads the mark price of BTC from the unsigned info endpoint,
  2. places a GTC buy at ~40% of mark for the minimum size -- far enough away that it
     cannot fill, so nothing is ever at risk,
  3. verifies via orderStatus that it is resting on the book,
  4. cancels it by oid and verifies the lifecycle is now "canceled",
  5. places a second order with a cloid and cancels it by cloid.

Nothing here is mocked: this is the only check that exercises real signing against the
venue, so run it whenever the signing scheme, wire shape, or asset-id resolution changes.
A bad signature comes back as an err envelope and fails step 2; a malformed action fails
with a 4xx before that.

    env PYTHONPATH=. uv run python tests/hyper_order_smoke.py

The account must hold enough collateral to meet the venue's minimum order value ($10 at
the time of writing); the test reports a funding shortfall distinctly and exits non-zero
without leaving anything on the book. Orders are placed far below market and canceled
within the same run, so a funded account is never actually put at risk.
"""
import os
import sys
import time
from decimal import Decimal

from argus._argus_utils import load_dotenv
from argus.perpetuals.hyper.exchange import HyperLiquidExchange
from argus.perpetuals.hyper.rest import HyperLiquidRest

COIN = "BTC"
#: Buy at this fraction of mark: deep enough that it cannot be crossed by a fill. 0.4
#: leaves ample room below any plausible wick, so the order only ever rests.
SAFE_LIMIT_FRACTION = Decimal("0.4")


def _fail(message: str) -> None:
    print(f"FAIL: {message}")
    sys.exit(1)


def _place_or_explain(exchange: HyperLiquidExchange, price: Decimal, size: Decimal, cloid=None):
    """Place a safe resting order; exit with a clear funding message if the venue refuses."""
    placed = exchange.place_order(COIN, "buy", str(price), str(size), tif="Gtc", cloid=cloid)
    if not placed.ok:
        if "minimum value" in (placed.error or "") or "margin" in (placed.error or "").lower():
            print(f"SKIP: account cannot fund the venue's minimum order ({placed.error}).")
            print("      Fund the wallet and re-run to exercise the full place -> cancel round trip.")
            sys.exit(2)
        _fail(f"place_order was rejected: {placed.error}")
    if placed.oid is None:
        _fail(f"place_order returned no oid: {placed}")
    return placed


def main() -> None:
    load_dotenv()
    wallet = os.environ["HYPERLIQUID_WALLET_ADDRESS"]
    key = os.environ["HYPERLIQUID_PRIVATE_KEY"]

    rest = HyperLiquidRest(wallet, key)
    exchange = HyperLiquidExchange(wallet, key)

    perps = {p.name: p for p in rest.get_all_perpetuals()}
    perp = perps.get(COIN)
    if perp is None:
        _fail(f"{COIN} not found in the perpetuals index")
    mark = perp.context.mark_px
    if mark is None or mark <= 0:
        _fail(f"no usable mark price for {COIN}: {mark}")
    decimals = perp.asset.szDecimals or 0
    limit_px = exchange.round_price(COIN, mark * SAFE_LIMIT_FRACTION)
    size = Decimal(1).scaleb(-decimals)

    print(f"{COIN} mark={mark} szDecimals={decimals} -> placing buy {size} @ {limit_px}")
    print(f"asset id on the wire: {exchange.resolve_asset_id(COIN)}")

    # --- 1-4. place by oid -> confirm resting -> cancel -> confirm canceled ------
    placed = _place_or_explain(exchange, limit_px, size)
    print(f"1. placed: oid={placed.oid} status={placed.status}")
    try:
        status = rest.get_order_status_detail(placed.oid)
        if status.order is None or status.status != "open":
            _fail(f"order {placed.oid} is not resting right after placement (status={status.status!r})")
        print(f"2. resting: lifecycle={status.status} coin={status.order.coin} "
              f"limitPx={status.order.limit_px} sz={status.order.sz}")

        canceled = exchange.cancel_by_oid(COIN, placed.oid)
        if not canceled.ok:
            _fail(f"cancel_by_oid failed for {placed.oid}: {canceled.to_dict()}")
        print(f"3. canceled: {canceled.to_dict()}")

        final = rest.get_order_status_detail(placed.oid)
        if final.status != "canceled":
            _fail(f"expected lifecycle 'canceled' after cancel, got {final.status!r}")
        print(f"4. confirmed canceled: lifecycle={final.status}")
    except Exception:
        exchange.cancel_by_oid(COIN, placed.oid)  # best-effort cleanup
        raise

    # --- 5. cloid round trip ----------------------------------------------------
    time.sleep(0.5)  # distinct nonce
    cloid = "0x" + "ab" * 16
    by_cloid = _place_or_explain(exchange, limit_px, size, cloid=cloid)
    print(f"5a. placed with cloid: oid={by_cloid.oid} cloid={cloid}")
    try:
        status = rest.get_order_status_detail(cloid)
        if status.order is None or status.order.cloid != cloid:
            _fail(f"order placed with cloid {cloid} is not retrievable by cloid")
        canceled = exchange.cancel_by_cloid(COIN, cloid)
        if not canceled.ok:
            _fail(f"cancel_by_cloid failed for {cloid}: {canceled.to_dict()}")
        print(f"5b. canceled by cloid: {canceled.to_dict()}")
    except Exception:
        exchange.cancel_by_cloid(COIN, cloid)  # best-effort cleanup
        raise

    print("\nOK -- place / cancel-by-oid / cancel-by-cloid all round-tripped against the live venue")


if __name__ == "__main__":
    main()
