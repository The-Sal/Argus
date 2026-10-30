"""
Live probe: does re-sending `subscribe` on `account_all_orders` with a fresh auth token actually refresh the
token, and what happens when a token expires? (Lighter's docs say nothing about either; `LighterAccountWss`
refreshes by re-subscribing at 80% of the token deadline, so this settles whether that design works.)

READ-ONLY: it never places, changes or cancels anything. It only opens a websocket, subscribes with tokens
minted from your API key, and prints every frame it receives with a timestamp.

Phases (each frame is printed as `[t+secs] <frame>`):
  1. subscribe to account_all_orders with token A                      -> expect `subscribed/account_all_orders`
  2. after --resub-after seconds, subscribe AGAIN with fresh token B   -> record the server's reply to a duplicate
     (ack? error? silence?) -- this is the behaviour `_handle_error`'s "benign duplicate" list assumes
  3. wait for token A's deadline to pass (--short-deadline seconds, default 90) while the connection stays up,
     then keep listening --after-expiry seconds -> does the server push an error / close the socket / keep
     streaming (i.e. did token B replace A)? Frames only arrive when the account has order activity, so run this
     while something is touching the account, or rely on the server's error/close behaviour.

    env PYTHONPATH=. uv run python tests/lighter_token_refresh_probe.py --go [--short-deadline 90]

The signer must accept the short deadline; if it (or the server) rejects it, the printed error is itself the
answer for how short a token can be. Refuses to run without --go.
"""
import argparse
import json
import os
import sys
import threading
import time

from websocket import WebSocketApp

from argus._argus_utils import load_dotenv
from argus.perpetuals.lighter.exchange import LighterExchange
from argus.perpetuals.lighter.wss import WS_URL, _ACCOUNT_ORDERS_CHANNEL


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--go", action="store_true", help="required: acknowledge this opens a live websocket")
    parser.add_argument("--short-deadline", type=int, default=90, help="deadline (s) of the first token")
    parser.add_argument("--resub-after", type=int, default=20, help="seconds before the duplicate subscribe")
    parser.add_argument("--after-expiry", type=int, default=60, help="seconds to keep listening past expiry")
    args = parser.parse_args()
    if not args.go:
        print("Refusing to run without --go (opens a live websocket; read-only).")
        return 2

    load_dotenv()
    try:
        account_index = int(os.environ["LIGHTER_ACC_INDEX"])
        api_key_index = int(os.environ["LIGHTER_API_INDEX"])
        private_key = os.environ["LIGHTER_PRIVATE_KEY"]
    except KeyError as e:
        print(f"Missing env var {e}: need LIGHTER_ACC_INDEX, LIGHTER_API_INDEX, LIGHTER_PRIVATE_KEY")
        return 2

    exchange = LighterExchange(account_index, api_key_index, private_key)
    start = time.time()

    def log(message):
        print(f"[t+{time.time() - start:6.1f}] {message}", flush=True)

    def subscribe(ws, deadline_s, label):
        frame = json.dumps({
            "type": "subscribe",
            "channel": f"{_ACCOUNT_ORDERS_CHANNEL}/{account_index}",
            "auth": exchange.auth_token(deadline_s),
        })
        ws.send(frame)
        log(f"SENT subscribe with token {label} (deadline {deadline_s}s)")

    closed = threading.Event()

    def on_open(ws):
        log("socket open")
        subscribe(ws, args.short_deadline, "A")

        def second_phase():
            time.sleep(args.resub_after)
            try:
                subscribe(ws, 6 * 3600, "B")
            except Exception as e:
                log(f"duplicate subscribe failed to send: {e!r}")

        threading.Thread(target=second_phase, daemon=True).start()

        def end_of_run():
            time.sleep(args.short_deadline + args.after_expiry)
            log("probe finished; closing")
            ws.close()

        threading.Thread(target=end_of_run, daemon=True).start()

    def on_message(ws, message):
        parsed = None
        try:
            parsed = json.loads(message)
        except Exception:
            pass
        if isinstance(parsed, dict) and parsed.get("type") == "ping":
            ws.send(json.dumps({"type": "pong"}))      # keep the connection alive; don't log the noise
            return
        log(f"RECV {message[:500]}")

    def on_error(ws, error):
        log(f"ERROR {error!r}")

    def on_close(ws, code, reason):
        log(f"CLOSED code={code} reason={reason!r}")
        closed.set()

    ws = WebSocketApp(WS_URL, on_open=on_open, on_message=on_message, on_error=on_error, on_close=on_close)
    thread = threading.Thread(target=lambda: ws.run_forever(ping_interval=60, ping_timeout=20), daemon=True)
    thread.start()
    closed.wait(timeout=args.short_deadline + args.after_expiry + 30)
    print("\nInterpretation guide:\n"
          "  - reply to the duplicate subscribe is an error naming 'already'/'duplicate'  -> refresh-by-resubscribe "
          "is NOT supported; mint tokens and reconnect instead.\n"
          "  - duplicate is acked (`subscribed/...` again) and the stream survives token A's expiry -> "
          "refresh-by-resubscribe works.\n"
          "  - an auth error / close arrives at token A's expiry even after B was sent -> B did not replace A.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
