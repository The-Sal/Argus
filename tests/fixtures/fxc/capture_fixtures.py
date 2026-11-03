"""
One-off capture of live IBKR ForecastEx REST responses into JSON fixtures.

These fixtures let the unit tests run completely offline so we do not hammer
IBKR endpoints.  Run this ONLY when the upstream API shape changes and you need
to refresh the fixtures.

Usage:
    python tests/fixtures/fxc/capture_fixtures.py

It reads IB_COOKIE from .env, throttles each request, and writes JSON files
next to this script.  Keep the number of requests tiny.
"""
import json
import os
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
BASE = "https://www.interactivebrokers.com/tws.proxy/forecasttrader"
THROTTLE_SECONDS = 0.5


def load_cookie():
    env = {}
    with open(os.path.join(ROOT, ".env")) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    cookie = env.get("IB_COOKIE")
    if not cookie:
        sys.exit("IB_COOKIE missing from .env")
    return cookie


def main():
    session = requests.Session()
    session.headers.update({
        "Cookie": load_cookie(),
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    })

    def get(name, path):
        time.sleep(THROTTLE_SECONDS)
        url = f"{BASE}{path}"
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        with open(os.path.join(HERE, name), "w") as fh:
            json.dump(data, fh, indent=1)
        print(f"captured {name} <- {path}")
        return data

    get("category_tree.json", "/category/tree")
    # FF: large, multi-expiry market.  USIP: small market.
    get("market_FF.json", "/contract/market?underlyingConid=658663572&exchange=FORECASTX")
    market_ff = json.load(open(os.path.join(HERE, "market_FF.json")))
    get("rules_FF.json", "/contract/rules?conid=" + str(market_ff["contracts"][0]["conid"]))
    get("market_USIP.json", "/contract/market?underlyingConid=751047561&exchange=FORECASTX")
    get("prediction_list_FF.json", "/prediction/contract/list?conid=658663572&exchange=FORECASTX")
    get("search_USIP.json", "/contract/search?prefix=USIP")
    # mass/details on one yes/no pair from USIP; update conids if expired.
    tree = json.load(open(os.path.join(HERE, "market_USIP.json")))
    conids = [c["conid"] for c in tree.get("contracts", [])[:4]]
    if conids:
        get("mass_details_USIP.json", "/contract/mass/details?conids=" + ",".join(str(c) for c in conids))


if __name__ == "__main__":
    main()
