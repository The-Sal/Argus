"""
Live end-to-end smoke test of the Hyperliquid `account_update` push (real orders, pennies of risk).

Runs a real HyperLiquidDispatcher on a local port, connects a plain client socket that only sends
`products_version` (never `subscribe`), then drives the venue and asserts the pushes that arrive:

  1. deep resting GTC buy -> `order` open, cancel it -> `order` canceled (same oid)
  2. IOC buy then reduce-only IOC sell (~$11) -> `fill` events (and whatever `order` events accompany them,
     printed so the shapes are documented); asserts exactly one push per tid (no duplicates)
  3. force-close the account websocket -> exactly one `gap` push after it resubscribes

Only ETH/BTC are traded; the `finally` cancels anything resting and flattens any leftover position.
Refuses to run without --go.

    env PYTHONPATH=. uv run python tests/hyper_account_stream_smoke.py --go [ETH|BTC]
"""
import json
import os
import socket
import sys
import time

from argus import protocol
from argus._argus_utils import load_dotenv
from argus.perpetuals.hyper import HyperLiquidDispatcher
from argus.perpetuals.hyper.exchange import HyperLiquidExchange
from argus.perpetuals.hyper.rest import HyperLiquidRest

sys.path.insert(0, os.path.dirname(__file__))
from hyper_order_lifecycle import size_for, position_size, fail, SLIPPAGE, DEEP_FRACTION, TARGET_NOTIONAL_USD  # noqa: E402

PORT = 9973


class Client:
    """Minimal P1 client: sends one request, then collects every `account_update` push."""

    def __init__(self):
        self.sock = socket.create_connection(("localhost", PORT))
        self.sock.settimeout(0.2)
        self.buf = b""
        self.updates = []

    def send(self, action, data=None):
        msg = {"action": action, "data": data, "correlation_id": f"smoke-{time.time_ns()}"}
        self.sock.sendall(protocol.encode_packet(json.dumps(msg).encode()))

    def pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                fail("dispatcher closed the client socket")
            self.buf += chunk
            try:
                packets = protocol.decode_multiple_packets(self.buf)
            except ValueError:
                continue  # partial frame, wait for more
            self.buf = b""
            for p in packets:
                msg = json.loads(p)
                if msg.get("action") == "account_update":
                    self.updates.append(msg["data"])

    def wait_for(self, pred, timeout, what):
        end = time.time() + timeout
        while time.time() < end:
            self.pump(0.3)
            hit = [u for u in self.updates if pred(u)]
            if hit:
                return hit[0]
        fail(f"timed out waiting for {what}; got {json.dumps(self.updates, indent=1)[:3000]}")


def is_order(u, oid, status):
    return u["event"] == "order" and u["order"]["order_id"] == str(oid) and u["order"]["status"] == status


def main():
    if "--go" not in sys.argv:
        fail("places REAL orders; re-run with --go to confirm")
    args = [a for a in sys.argv[1:] if a != "--go"]
    coin = args[0].upper() if args else "ETH"
    if coin not in ("ETH", "BTC"):
        fail("only ETH/BTC may be traded")

    load_dotenv()
    wallet, key = os.environ["HYPERLIQUID_WALLET_ADDRESS"], os.environ["HYPERLIQUID_PRIVATE_KEY"]
    rest, ex = HyperLiquidRest(wallet, key), HyperLiquidExchange(wallet, key)
    if position_size(rest, coin) != 0:
        fail(f"{coin} already has a position")

    disp = HyperLiquidDispatcher(wallet, key, port=PORT)
    disp.run()
    time.sleep(2)
    client = Client()
    client.send("products_version")  # the only thing this client ever sends: no subscribe
    disp.account_updates.wait_till_socket_open.wait(10)
    time.sleep(2)  # let both subscriptions ack

    perp = next(p for p in rest.get_perpetuals_for_dex("").perpetuals if p.name == coin)
    mark, decimals = perp.context.mark_px, perp.asset.szDecimals or 0
    resting = []
    try:
        px = ex.round_price(coin, mark * DEEP_FRACTION)
        sz = size_for(TARGET_NOTIONAL_USD, px, decimals)
        print(f"1. deep GTC buy {sz} {coin} @ {px}")
        placed = ex.place_order(coin, "buy", px, sz, tif="Gtc", cloid="0x" + "ab" * 16)
        if not placed.ok:
            fail(f"rejected: {placed.error}")
        resting.append(placed.oid)
        opened = client.wait_for(lambda u: is_order(u, placed.oid, "open"), 8, "order open")
        print("   open push:", json.dumps(opened["order"])[:400])
        ex.cancel_by_oid(coin, placed.oid)
        resting.clear()
        client.wait_for(lambda u: is_order(u, placed.oid, "canceled"), 8, "order canceled")
        print("   canceled push received")

        print("2. IOC buy then reduce-only sell")
        n0 = len(client.updates)
        buy_sz = size_for(TARGET_NOTIONAL_USD, mark, decimals)
        bought = ex.place_order(coin, "buy", mark * (1 + SLIPPAGE), buy_sz, tif="Ioc")
        if not bought.ok or bought.status != "filled":
            fail(f"buy: {bought.to_dict()}")
        client.wait_for(lambda u: u["event"] == "fill" and u["trade"]["order_id"] == str(bought.oid), 8, "buy fill")
        time.sleep(1)
        held = position_size(rest, coin)
        sold = ex.place_order(coin, "sell", mark * (1 - SLIPPAGE), held, tif="Ioc", reduce_only=True)
        if not sold.ok or sold.status != "filled":
            fail(f"sell: {sold.to_dict()}")
        client.wait_for(lambda u: u["event"] == "fill" and u["trade"]["order_id"] == str(sold.oid), 8, "sell fill")
        client.pump(2)
        new = client.updates[n0:]
        print(f"   {len(new)} pushes:")
        for u in new:
            brief = u["order"]["status"] if u["event"] == "order" else f'tid={u["trade"]["trade_id"]}'
            print("    ", u["event"], brief, u.get("order", u.get("trade", {})).get("order_id"))
        tids = [u["trade"]["trade_id"] for u in new if u["event"] == "fill"]
        if len(tids) != len(set(tids)):
            fail(f"duplicate fill pushes: {tids}")
        print("   no duplicate fills")

        print("3. kill the account websocket")
        n1 = len(client.updates)
        disp.account_updates._ws.close()
        client.wait_for(lambda u: u["event"] == "gap", 30, "gap")
        client.pump(2)
        gaps = [u for u in client.updates[n1:] if u["event"] == "gap"]
        if len(gaps) != 1:
            fail(f"expected exactly one gap, got {gaps}")
        print("   gap:", gaps[0])
        replayed = [u for u in client.updates[n1:] if u["event"] != "gap"]
        print(f"   {len(replayed)} non-gap pushes after reconnect (replay check; expected 0)")
    finally:
        for oid in resting:
            ex.cancel_by_oid(coin, oid)
        left = position_size(rest, coin)
        if left != 0:
            print(f"CLEANUP: closing {left} {coin}")
            px = mark * (1 - SLIPPAGE) if left > 0 else mark * (1 + SLIPPAGE)
            ex.place_order(coin, "sell" if left > 0 else "buy", px, abs(left), tif="Ioc", reduce_only=True)
    print("OK")
    os._exit(0)


if __name__ == "__main__":
    main()
