"""
Al Ramz authentication (IB-style Chromium login) -> ``ALRAMZ_SOCKET_SESSION``.
================================================================================

``feed.alramz.ae`` is gated by an opaque ``SocketSession`` token that only the logged-in
``webtrade.alramz.ae`` app ever sees. The login itself cannot be replayed with plain HTTP: the app
encrypts the credentials client-side, runs an invisible reCAPTCHA v3 (``grecaptcha.execute``) and
often requires an OTP / 2FA step. So, exactly like ``argus.ib.set_auth``, we drive a real (not
headless) Chromium, let it do all of that, and read the token back out.

The token is not a cookie and not always in storage, so we recover it from the one place it must
appear: the network. Chrome DevTools performance logs capture the app's negotiate/WebSocket URLs,
each of which carries ``?…&SocketSession=<token>``. As a fallback we scan cookies, ``localStorage``,
``sessionStorage``, the DOM and the page HTML for a long hex token.

Usage
-----
Credentials are read through the shared secure env loader (`load_dotenv()`), so they may live in a
plaintext `.env` **or** in an encrypted `.env.enc.se` decrypted just-in-time by SDist:

    ALRAMZ_USERNAME=...   # or ALRAMZ_USER / USERNAME
    ALRAMZ_PASSWORD=...   # or ALRAMZ_PASS / PASSWORD

    python -m argus.alramz.set_auth   # opens Chromium, completes login (OTP manually if asked),
                                      # then persists ALRAMZ_SOCKET_SESSION: back into .env.enc.se
                                      # when the secure loader is active, else into plaintext .env

The dispatcher's interactive menu also exposes this as "Refresh Authentication (Chromium)".
"""

from __future__ import annotations

import os
import re
import json
import time
import traceback

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import WebDriverException

from argus._argus_utils import load_dotenv, SECURE_ENV_VAR_LOADER

SITE_ROOT = "https://webtrade.alramz.ae"
LOGIN_URL = f"{SITE_ROOT}/"
FEED_HOST = "feed.alramz.ae"

#: `SocketSession` observed live is 488 hex chars; require a comfortably long hex run so we never
#: mistake an unrelated query param for it.
_TOKEN_RE = re.compile(r"SocketSession=([0-9A-Fa-f]{100,})")
_BARE_TOKEN_RE = re.compile(r"\b([0-9A-Fa-f]{200,})\b")

_USERNAME_SEL = "#txtUserId"
_PASSWORD_SEL = "#txtLoginPassword"
_LOGIN_BTN_SEL = "#btnValidateLogin"
_OTP_SEL = "#txtOTP0"

#: Cookie-consent / interstitial buttons seen across Al Ramz's public pages; all best-effort.
_CONSENT_SELECTORS = (
    "#btn_accept_cookies",
    "button#onetrust-accept-btn-handler",
    "button[aria-label='Accept all']",
    "button[aria-label='Accept All']",
    ".cc-allow",
    "#accept-cookie",
)


def _find_socket_session(driver: webdriver.Chrome, perf_logs: list | None = None) -> str | None:
    """Search Chrome performance logs, then cookies/storage/DOM, for the SocketSession token."""
    # 1. Network: the app's negotiate XHR and the WebSocket upgrade both contain it.
    if perf_logs is None:
        try:
            perf_logs = driver.get_log("performance")
        except WebDriverException:
            perf_logs = []
    for entry in perf_logs:
        match = _TOKEN_RE.search(entry.get("message", ""))
        if match:
            return match.group(1)

    # 2. Cookies (named, or a long bare hex value).
    try:
        for cookie in driver.get_cookies():
            if cookie.get("name") == "SocketSession" and cookie.get("value"):
                return cookie["value"]
            if _BARE_TOKEN_RE.fullmatch(cookie.get("value", "")):
                return cookie["value"]
    except WebDriverException:
        pass

    # 3. Web storage + document.cookie + page HTML.
    try:
        blob = driver.execute_script(
            "return JSON.stringify({"
            "ls: Object.assign({}, window.localStorage),"
            "ss: Object.assign({}, window.sessionStorage),"
            "cookie: document.cookie});"
        ) or ""
    except WebDriverException:
        blob = ""
    match = _BARE_TOKEN_RE.search(blob)
    if match:
        return match.group(1)
    try:
        match = _BARE_TOKEN_RE.search(driver.page_source or "")
        if match:
            return match.group(1)
    except WebDriverException:
        pass
    return None


def _accept_consent(driver: webdriver.Chrome) -> None:
    for selector in _CONSENT_SELECTORS:
        try:
            button = driver.find_element(By.CSS_SELECTOR, selector)
            if button.is_displayed():
                button.click()
                time.sleep(0.5)
                return
        except WebDriverException:
            continue


def get_socket_session(wait_seconds: int = 240) -> str | None:
    """
    Open Chromium, log in to ``webtrade.alramz.ae`` and return the ``SocketSession`` token.

    Credentials come from ``ALRAMZ_USERNAME``/``ALRAMZ_PASSWORD`` (falling back to
    ``USERNAME``/``PASSWORD``). The browser is deliberately non-headless so you can solve an OTP,
    consent or CAPTCHA challenge by hand; we poll until login completes or `wait_seconds` elapses.
    """
    load_dotenv()  # secure-aware: decrypts .env.enc.se just-in-time when SDist is present
    username = os.environ.get("ALRAMZ_USERNAME") or os.environ.get("ALRAMZ_USER") or os.environ.get("USERNAME")
    password = os.environ.get("ALRAMZ_PASSWORD") or os.environ.get("ALRAMZ_PASS") or os.environ.get("PASSWORD")
    if not username:
        username = input("Al Ramz username: ").strip()
    if not password:
        import getpass
        password = getpass.getpass("Al Ramz password: ")
    if not username or not password:
        raise RuntimeError("Username/password are required to authenticate.")

    options = webdriver.ChromeOptions()
    # Performance logs are how we recover the token from the negotiate/WebSocket URL.
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    binary = os.environ.get("ALRAMZ_CHROME_BINARY")
    if binary:
        options.binary_location = binary
    profile = os.environ.get("ALRAMZ_CHROME_PROFILE")
    if profile:
        # Persistent profile so a later debug run can reuse the login (no OTP).
        options.add_argument(f"--user-data-dir={profile}")
        options.add_argument("--profile-directory=Default")

    driver = webdriver.Chrome(options=options)
    try:
        driver.get(LOGIN_URL)
        WebDriverWait(driver, 15).until(lambda d: d.execute_script("return document.readyState") == "complete")
        _accept_consent(driver)

        # Fill the form unless the browser already holds a session (fields absent).
        try:
            driver.find_element(By.CSS_SELECTOR, _USERNAME_SEL).send_keys(username)
            driver.find_element(By.CSS_SELECTOR, _PASSWORD_SEL).send_keys(password)
            driver.find_element(By.CSS_SELECTOR, _LOGIN_BTN_SEL).click()
            print("Submitted credentials. Complete any OTP / CAPTCHA challenge in the Chromium window...")
        except WebDriverException:
            print("Login form not found -- assuming an existing session; looking for the feed token...")

        # Wait for login to complete. We do NOT read the performance log here: `get_log` drains
        # Chrome's buffer, and the negotiate URL (which carries the token) must survive until the
        # final scan. OTP keeps the URL on the login page until the user finishes it, so polling the
        # URL alone is enough to wait for both password-only and 2FA logins.
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            try:
                url = (driver.current_url or "").rstrip("/")
            except WebDriverException:
                break
            if url and url not in (SITE_ROOT, f"{SITE_ROOT}/OnBoarding", f"{SITE_ROOT}/Login") \
                    and "/onboarding/login" not in url.lower():
                break
            time.sleep(1)

        # Nudge the app to open the SignalR connection (it starts on the market/screener pages).
        try:
            if "/screener" not in (driver.current_url or ""):
                driver.get(f"{SITE_ROOT}/screener/index")
        except WebDriverException:
            pass
        time.sleep(8)

        # Drain the performance log once (get_log clears it) so both the token scan and any debug
        # dump see the same negotiate/WebSocket URLs.
        try:
            perf_logs = driver.get_log("performance")
        except WebDriverException:
            perf_logs = []
        token = _find_socket_session(driver, perf_logs)
        if not token:
            print("Could not find SocketSession in the browser session.")
        if os.environ.get("ALRAMZ_AUTH_DEBUG"):
            _dump_debug(driver, perf_logs)
        return token
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def _dump_debug(driver: webdriver.Chrome, perf_logs: list) -> None:
    """Write the browser's cookies + full network/perf log to `alramz_auth_debug.json`."""
    dump = {
        "url": driver.current_url,
        "cookies": driver.get_cookies(),
        "performance": perf_logs,
    }
    with open("alramz_auth_debug.json", "w") as f:
        json.dump(dump, f, indent=1)
    feed_urls = sorted({m for e in perf_logs for m in re.findall(r'https://feed\.alramz\.ae[^"]*', e.get("message", ""))})
    print(f"[debug] wrote alramz_auth_debug.json ({len(perf_logs)} perf entries, {len(dump['cookies'])} cookies)")
    for u in feed_urls[:10]:
        print("[debug] feed URL:", u[:200])


def _set_dotenv_key(key: str, value: str) -> None:
    """Upsert ``KEY='value'`` in the plaintext ``.env`` at the CWD."""
    try:
        with open(".env", "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []
    found = False
    with open(".env", "w") as f:
        for line in lines:
            if re.match(rf"\s*{re.escape(key)}\s*=", line):
                f.write(f"{key}='{value}'\n")
                found = True
            else:
                f.write(line)
        if not found:
            if lines and not lines[-1].endswith("\n"):
                f.write("\n")
            f.write(f"{key}='{value}'\n")


def _persist_env_var(key: str, value: str) -> None:
    """
    Persist a secret through the *secure* env loader when it is active.

    When ``SECURE_ENV_VAR_LOADER`` is active, ``load_dotenv()`` already decrypted ``.env.enc.se``
    to ``.env`` and deleted the plaintext. Writing to a fresh plaintext ``.env`` here would both
    leak the secret and be lost on the next decrypt, so instead we decrypt the encrypted store
    again, upsert the key, and re-encrypt it (``encrypt_env`` also removes the plaintext).
    """
    loader = SECURE_ENV_VAR_LOADER
    if not loader.active:
        _set_dotenv_key(key, value)
        return
    if not os.path.exists(".env"):
        loader.decrypt_env()
    try:
        _set_dotenv_key(key, value)
        loader.encrypt_env()
    finally:
        # Never leave a plaintext copy of the secrets on disk in the secure context.
        try:
            os.remove(".env")
        except FileNotFoundError:
            pass


def update_socket_session(write_env: bool = True) -> str | None:
    """Login via Chromium, inject ``ALRAMZ_SOCKET_SESSION`` into the environment (and persist it)."""
    load_dotenv()
    print("Launching Chromium for Al Ramz authentication...")
    token = get_socket_session()
    if not token:
        print("Authentication did not yield a SocketSession.")
        return None
    os.environ["ALRAMZ_SOCKET_SESSION"] = token
    print(f"SocketSession obtained ({len(token)} chars); injected into the environment.")

    if write_env:
        if SECURE_ENV_VAR_LOADER.active:
            print("Writing ALRAMZ_SOCKET_SESSION to the encrypted .env.enc.se ...")
        else:
            print("Writing ALRAMZ_SOCKET_SESSION to .env ...")
        _persist_env_var("ALRAMZ_SOCKET_SESSION", token)
    return token


if __name__ == "__main__":
    try:
        update_socket_session(write_env=True)
    except Exception:
        traceback.print_exc()
