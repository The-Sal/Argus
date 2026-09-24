"""
Live end-to-end order lifecycle for Hyperliquid (real orders, real money -- pennies).

Modeled on tests/test_poly_dispatcher_order_lifecycle.py but driven through the REST/exchange
clients directly (no dispatcher needed). Only a major (BTC or ETH) is ever traded.

  A. deep resting order   place a GTC buy at 40% of mark, sized to clear the venue's $10
                          minimum order value; confirm it rests, cancel it by oid, confirm
                          lifecycle "canceled"; repeat with a cloid. It can never fill.
  B. full cycle           buy ~$11 with a marketable IOC limit, confirm the position and the
                          balance, sell it back with a reduce-only IOC, confirm flat, and report
                          the round-trip cost (spread + fees).

Safety: notional is capped (MAX_NOTIONAL_USD); the `finally` block cancels anything still
resting and closes any leftover position, so an exception mid-run does not leave exposure.
It refuses to run without --go.

    env PYTHONPATH=. uv run python tests/hyper_order_lifecycle.py --go [ETH|BTC]
"""
import os
import sys
import time
from decimal import Decimal, ROUND_CEILING
from typing import Optional

from argus._argus_utils import load_dotenv
from argus.perpetuals.hyper.exchange import HyperLiquidExchange
from argus.perpetuals.hyper.rest import HyperLiquidRest

ALLOWED_COINS = ("ETH", "BTC")
TARGET_NOTIONAL_USD = Decimal("11")      # venue minimum order value is $10
MAX_NOTIONAL_USD = Decimal("12")         # hard cap on anything we place
DEEP_FRACTION = Decimal("0.4")           # resting buy at 40% of mark: cannot be reached by a fill
SLIPPAGE = Decimal("0.01")               # marketable IOC limit is 1% through mark


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    sys.exit(1)


def size_for(notional: Decimal, price: Decimal, sz_decimals: int) -> Decimal:
    """Smallest size (rounded up to the venue's step) worth at least `notional` at `price`."""
    step = Decimal(1).scaleb(-sz_decimals)
    size = (notional / price).quantize(step, rounding=ROUND_CEILING)
    if size * price > MAX_NOTIONAL_USD * Decimal("1.05"):
        fail(f"size {size} @ {price} exceeds the ${MAX_NOTIONAL_USD} notional cap")
    return size


def position_size(rest: HyperLiquidRest, coin: str) -> Decimal:
    for p in rest.get_positions(dex=""):
        if p.name == coin:
            return p.signed_size
    return Decimal(0)


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--go"]
    if "--go" not in sys.argv:
        fail("places REAL orders; re-run with --go to confirm")
    coin = args[0].upper() if args else "ETH"
    if coin not in ALLOWED_COINS:
        fail(f"only {ALLOWED_COINS} may be traded, got {coin!r}")

    load_dotenv()
    wallet = os.environ["HYPERLIQUID_WALLET_ADDRESS"]
    key = os.environ["HYPERLIQUID_PRIVATE_KEY"]
    rest = HyperLiquidRest(wallet, key)
    ex = HyperLiquidExchange(wallet, key)

    perp = next(p for p in rest.get_perpetuals_for_dex("").perpetuals if p.name == coin)
    mark = perp.context.mark_px
    decimals = perp.asset.szDecimals or 0
    if not mark or mark <= 0:
        fail(f"no usable mark for {coin}")

    before = rest.get_account_balance()
    print(f"account mode={before.account_mode} value={before.account_value} available={before.available_balance}")
    print(f"{coin} mark={mark} szDecimals={decimals}")
    if before.available_balance < TARGET_NOTIONAL_USD / 5:
        fail("not enough available balance to place the orders")
    if position_size(rest, coin) != 0:
        fail(f"{coin} already has an open position; refusing to trade on top of it")

    resting_oids: list = []
    try:
        # --- A. deep resting order: place -> rests -> cancel (oid), then again with cloid -------
        deep_px = ex.round_price(coin, mark * DEEP_FRACTION)
        deep_sz = size_for(TARGET_NOTIONAL_USD, deep_px, decimals)
        print(f"\nA1. deep GTC buy {deep_sz} {coin} @ {deep_px} (notional {deep_sz * deep_px})")
        placed = ex.place_order(coin, "buy", deep_px, deep_sz, tif="Gtc")
        if not placed.ok or placed.oid is None:
            fail(f"deep order rejected: {placed.error}")
        resting_oids.append(placed.oid)
        status = rest.get_order_status_detail(placed.oid)
        if status.status != "open":
            fail(f"deep order not resting: lifecycle={status.status!r}")
        listed = [o.order_id for o in rest.get_open_orders(dex="")]
        if str(placed.oid) not in listed:
            fail("resting order missing from get_open_orders")
        print(f"    resting oid={placed.oid}; visible in get_open_orders")
        canceled = ex.cancel_by_oid(coin, placed.oid)
        if not canceled.ok:
            fail(f"cancel failed: {canceled.to_dict()}")
        resting_oids.remove(placed.oid)
        final = rest.get_order_status_detail(placed.oid)
        if final.status != "canceled":
            fail(f"expected 'canceled', got {final.status!r}")
        print(f"    canceled; lifecycle={final.status}")

        time.sleep(0.5)
        cloid = "0x" + "cd" * 16
        print(f"A2. deep GTC buy with cloid {cloid}")
        by_cloid = ex.place_order(coin, "buy", deep_px, deep_sz, tif="Gtc", cloid=cloid)
        if not by_cloid.ok or by_cloid.oid is None:
            fail(f"cloid order rejected: {by_cloid.error}")
        resting_oids.append(by_cloid.oid)
        if rest.get_order_status_detail(cloid).order is None:
            fail("order not retrievable by cloid")
        canceled = ex.cancel_by_cloid(coin, cloid)
        if not canceled.ok:
            fail(f"cancel by cloid failed: {canceled.to_dict()}")
        resting_oids.remove(by_cloid.oid)
        print(f"    canceled by cloid: {canceled.to_dict()}")

        # --- B. full cycle: marketable buy -> position -> reduce-only sell -> flat --------------
        time.sleep(0.5)
        buy_sz = size_for(TARGET_NOTIONAL_USD, mark, decimals)
        buy_px = mark * (1 + SLIPPAGE)
        print(f"\nB1. IOC buy {buy_sz} {coin} @ <= {ex.round_price(coin, buy_px)} (~${buy_sz * mark})")
        bought = ex.place_order(coin, "buy", buy_px, buy_sz, tif="Ioc")
        if not bought.ok:
            fail(f"buy rejected: {bought.error}")
        print(f"    {bought.to_dict()}")
        if bought.status != "filled":
            fail(f"IOC buy did not fill: {bought.to_dict()}")

        time.sleep(1.0)
        held = position_size(rest, coin)
        mid = rest.get_account_balance()
        print(f"B2. position {held} {coin}; balance value={mid.account_value} margin_used={mid.total_margin_used} "
              f"notional={mid.total_position_notional}")
        if held <= 0:
            fail(f"expected a long {coin} position after the fill, got {held}")

        print(f"B3. IOC reduce-only sell {held} {coin}")
        sold = ex.place_order(coin, "sell", mark * (1 - SLIPPAGE), held, tif="Ioc", reduce_only=True)
        print(f"    {sold.to_dict()}")
        if not sold.ok or sold.status != "filled":
            fail(f"sell did not fill: {sold.to_dict()}")

        time.sleep(1.0)
        left = position_size(rest, coin)
        if left != 0:
            fail(f"position not flat after sell: {left}")
        print("B4. position is flat")
    finally:
        # Best-effort cleanup so a failure mid-run never leaves exposure or resting orders.
        for oid in resting_oids:
            ex.cancel_by_oid(coin, oid)
        leftover = position_size(rest, coin)
        if leftover != 0:
            print(f"CLEANUP: closing leftover {leftover} {coin}")
            side = "sell" if leftover > 0 else "buy"
            px = mark * (1 - SLIPPAGE) if leftover > 0 else mark * (1 + SLIPPAGE)
            ex.place_order(coin, side, px, abs(leftover), tif="Ioc", reduce_only=True)

    after = rest.get_account_balance()
    trades = rest.get_recent_trades(2)
    fees = sum((t.fee for t in trades), Decimal(0))
    print(f"\nbalance before={before.account_value} after={after.account_value} "
          f"(round-trip cost {before.account_value - after.account_value}); fees on last 2 fills={fees}")
    print("OK -- place / rest / cancel (oid + cloid) and buy -> sell full cycle round-tripped on the live venue")


if __name__ == "__main__":
    main()
