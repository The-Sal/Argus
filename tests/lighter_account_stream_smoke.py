"""
Live end-to-end smoke test of the Lighter `account_update` push (REAL orders, pennies of risk).

Starts a real `LighterDispatcher` in-process, connects a plain P1 client that only sends
`products_version` (never `subscribe`), then drives the venue and asserts the pushes arrive:

  1. a far-below-market GTC buy at the leverage already in force -> `order` open + `order` canceled
  2. one forced account-websocket close -> exactly one `gap` push after it resubscribes
  3. `finally`: `cancel_all_orders` on BTC, then `get_orders` proves zero resting orders

No leverage is ever changed (the current value is re-applied) and every order is swept. Refuses to run
without `--go`.

    env PYTHONPATH=. uv run python tests/lighter_account_stream_smoke.py --go
"""
import json
import math
import os
import socket
import sys
import time

from argus import protocol
from argus._argus_utils import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))

PORT = 19975
COIN = "BTC"
SAFE_LIMIT_FRACTION = 0.4  # buy at 40% of mark: cannot be crossed, so it only ever rests


class Client:
    """Minimal P1 client: sends requests, collects account_update pushes, matches responses by corr id."""

    def __init__(self, port):
        self.sock = socket.create_connection(("localhost", port))
        self.sock.settimeout(0.2)
        self.buf = b""
        self.updates = []
        self._responses = {}

    def _drain(self):
        try:
            chunk = self.sock.recv(65536)
        except socket.timeout:
            return
        if not chunk:
            raise ConnectionError("dispatcher closed the client socket")
        self.buf += chunk
        try:
            packets = protocol.decode_multiple_packets(self.buf)
        except ValueError:
            return  # partial frame, wait for more
        self.buf = b""
        for packet in packets:
            msg = json.loads(packet)
            if msg.get("action") == "account_update":
                self.updates.append(msg["data"])
            elif msg.get("action") in ("response", "error") and msg.get("correlation_id"):
                self._responses[msg["correlation_id"]] = msg

    def pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self._drain()

    def request(self, action, data=None, timeout=15.0):
        correlation_id = f"acct-{time.time_ns()}"
        message = {"action": action, "data": data, "correlation_id": correlation_id}
        self.sock.sendall(protocol.encode_packet(json.dumps(message).encode()))
        end = time.time() + timeout
        while time.time() < end:
            if correlation_id in self._responses:
                return self._responses.pop(correlation_id)
            self._drain()
        raise TimeoutError(f"no response for {action}")

    def wait_for(self, predicate, timeout, what):
        end = time.time() + timeout
        while time.time() < end:
            self._drain()
            hit = [u for u in self.updates if predicate(u)]
            if hit:
                return hit[0]
        raise AssertionError(f"timed out waiting for {what}; got {json.dumps(self.updates, indent=1)[:3000]}")


def fail(message):
    print("FAIL:", message)
    sys.exit(1)


def main():
    if "--go" not in sys.argv:
        fail("places REAL orders; re-run with --go to confirm")

    load_dotenv()
    from argus.perpetuals.lighter import LighterDispatcher

    dispatcher = LighterDispatcher(host="localhost", port=PORT)
    dispatcher.run()
    print(f"dispatcher started on {PORT}; account stream = {dispatcher.account_updates}")
    if dispatcher.account_updates is None:
        fail("the account stream did not start (see log above)")
    dispatcher.account_updates.wait_till_socket_open.wait(15)

    deadline = time.time() + 30
    while time.time() < deadline and len(dispatcher.account_updates._stream._acked) < 2:
        time.sleep(0.3)
    print("account channels acked:", dispatcher.account_updates._stream._acked)

    client = Client(PORT)
    version = client.request("products_version")
    print("products_version:", version.get("data"))
    client.pump(0.5)  # register the socket and let the pushes flow

    info = client.request("market_info", {"symbol": COIN})["data"]["perpetual"]
    market, context = info["market"], info["context"]
    leverage = client.request("get_leverage", {"coin": COIN})["data"]["leverage"]
    mark = float(context["mark_price"])
    px = round(mark * SAFE_LIMIT_FRACTION, int(market["price_decimals"]))
    unit = 10 ** -int(market["size_decimals"])
    size = f"{max(math.ceil(float(market['min_quote_amount']) / px / unit) * unit, float(market['min_base_amount'])):.{int(market['size_decimals'])}f}"
    cloid = int(time.time() * 1000) % (1 << 40)
    print(f"placing GTC buy {size} {COIN} @ {px} at {leverage['value']}x {leverage['type']} (cloid={cloid})")

    oid = None
    try:
        placed = client.request("place_order", {
            "coin": COIN, "side": "buy", "price": str(px), "size": size,
            "leverage": leverage["value"], "margin_mode": leverage["type"],
            "order_type": "GTC", "cloid": cloid,
        })
        if placed.get("error"):
            fail(f"place_order rejected: {placed}")
        print("place_order accepted:", placed["data"])

        opened = client.wait_for(
            lambda u: u["event"] == "order" and u["order"]["status"] == "open" and u["order"]["name"] == COIN,
            15, "order open push",
        )
        oid = opened["order"]["order_id"]
        print("open push:", json.dumps(opened["order"])[:400])

        canceled_req = client.request("cancel_order", {"order_id": f"c:{cloid}", "coin": COIN})
        if canceled_req.get("error"):
            fail(f"cancel_order rejected: {canceled_req}")
        canceled = client.wait_for(
            lambda u: (u["event"] == "order" and u["order"]["order_id"] == oid
                       and u["order"]["status"] in ("canceled", "canceled-post-only")),
            15, "order canceled push",
        )
        print("canceled push:", json.dumps(canceled["order"])[:400])

        before = len(client.updates)
        print("force-closing the account websocket ...")
        dispatcher.account_updates._ws.close()
        gap = client.wait_for(lambda u: u["event"] == "gap", 30, "gap push")
        client.pump(2)
        gaps = [u for u in client.updates[before:] if u["event"] == "gap"]
        if len(gaps) != 1:
            fail(f"expected exactly one gap, got {gaps}")
        print("gap push:", gap)
    finally:
        client.request("cancel_all_orders", {"coin": COIN})
        deadline = time.time() + 20
        resting = []
        while time.time() < deadline:
            resting = client.request("get_orders")["data"]["orders"]
            if not resting:
                break
            time.sleep(0.5)
        print(f"resting orders after sweep: {len(resting)}")
        if resting:
            fail(f"{len(resting)} order(s) still resting: {[o['order_id'] for o in resting]}")

    print("LIVE ACCOUNT STREAM SMOKE OK")
    os._exit(0)


if __name__ == "__main__":
    main()