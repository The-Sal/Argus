# HyperLiquid + Lighter Dispatcher Concurrency

`HyperLiquidDispatcher` (`argus/perpetuals/hyper/__init__.py`) and `LighterDispatcher`
(`argus/perpetuals/lighter/__init__.py`) are meant to run side by side, each as its own
`runtime.py` process on its own port:

```bash
python runtime.py hyperliquid --port 9972 &
python runtime.py lighter --port 9974 &
```

Running them concurrently is safe today. Every piece of shared infrastructure is either
per-instance or per-process, default ports already differ (`9972` vs `9974` — see
`argus/perpetuals/hyper/__init__.py:70` and `argus/perpetuals/lighter/__init__.py:37`), and
neither dispatcher's REST client or credentials overlap with the other's. There is exactly one
real hazard: a startup-time race in encrypted `.env` loading (see
[Secure-env decrypt race](#secure-env-decrypt-race) below). Everything else below documents why
the rest of the stack doesn't need coordination, and what would break if that changed.

## Launch model

`runtime.py` (`runtime.py:22-135`) takes a single `target` positional arg (`argparse` `choices`),
so one process only ever runs one dispatcher — concurrency between HyperLiquid and Lighter always
means two separate OS processes, never two dispatcher instances sharing an interpreter. There is
no code path today that instantiates both dispatchers in the same process (the "Perpetuals
Multi-Exchange Dispatcher" mentioned in `HyperLiquidDispatcher`'s docstring,
`argus/perpetuals/hyper/__init__.py:29-31`, doesn't exist yet). This document covers both the
current separate-process reality and what an in-process combinator would need to account for if
it's ever built.

## Instance state carries no cross-dispatcher risk

Both dispatchers inherit `BaseDispatcher` (`argus/perpetuals/shared/__init__.py:44`), which is
purely instance-scoped, so nothing here needs synchronization between HyperLiquid and Lighter:

| Component | Scope | Notes |
|---|---|---|
| `Server` (`utils3.networking.sockets.Server`) | Per-instance socket, bound to `(host, port)` in `__init__` | Each dispatcher opens its own `socket.socket(...)`; `SO_REUSEADDR` is set but the bind still fails loudly with `OSError: [Errno 48] Address already in use` if two dispatchers are pointed at the same port — this is the only thing enforcing "different ports" |
| `RoutingHelper` (`argus/_argus_utils.py:268`) | Per-instance `_sockets`, `_market_data_routing_table`, `_order_subscriptions`, `_sendall_locks`, guarded by a per-instance `threading.Lock` | No cross-dispatcher sharing possible |
| `CorrelationIDChecker` (`argus/_argus_utils.py:422`) | Per-instance `OrderedDict` + `threading.Lock`, created fresh in `BaseDispatcher.__init__` | HyperLiquid and Lighter each enforce correlation-ID uniqueness independently — a correlation ID reused across both dispatchers is fine, they don't share the seen-ID set |
| `routing_table` | Per-instance dict built in each dispatcher's `__init__` | No shared registry |
| `pi = PrintInterface(...)` module-level instances | One per module (`'HyperLiquid'`, `'Lighter'`), each wraps its own `Notification()` | Only used for prefixed `print()`/system notifications, not synchronization |
| `@runAsThread run()` (`utils3/__init__.py:163`) | Plain thread-spawn decorator, no shared state | Each dispatcher's `run()` spins its own daemon thread running `run_server()` |

The one **process-global** (not cross-process) piece of shared state is
`utils3.networking.sockets._threads` (`.venv/.../utils3/networking/sockets.py:10`), a module-level
list every `Server` instance's `_execute_async` appends daemon threads to. It only matters if both
dispatchers ever ran inside the same process: `Server.stop()` asserts *every* thread in that
global list is dead, not just the calling server's own threads, so stopping one in-process
dispatcher while the other still has live client threads would fail that assertion. That's not
reachable today, since each dispatcher is its own process (separate `_threads` list per
interpreter) and neither `hyperliquid` nor `lighter` runtime target calls `.stop()` today — see
`runtime.py:112-133`.

## Exchange-specific state doesn't overlap either

- **HyperLiquid**: reads `HYPERLIQUID_WALLET_ADDRESS` / `HYPERLIQUID_PRIVATE_KEY` from
  `os.environ` at construction time (`argus/perpetuals/hyper/__init__.py:93-96`), then builds a
  `HyperLiquidRest` client and eagerly fetches `get_all_perpetuals()`.
- **Lighter**: takes no credentials — `LighterRest()` is unauthenticated / public-REST-only
  (`argus/perpetuals/lighter/__init__.py:60-61`).

No overlapping environment variable names, no shared REST client, no shared rate limiter between
the two. Each dispatcher's outbound REST calls go to a different host, so there's no cross-talk
there either.

## Secure-env decrypt race

`runtime.py:46` calls `secure_load_dotenv()` (aliased from `argus._argus_utils.load_dotenv`,
`argus/__init__.py:17`) before dispatching to any target. This routes through the process-global
`EnvLoader` singleton `_ENV_VAR_LOADER` (`argus/_argus_utils.py:481-587`), guarded by a
module-level `_LOADED_ALREADY` flag (`argus/_argus_utils.py:477`) — a flag that is **per-process**,
so it does nothing to coordinate two separate `runtime.py` invocations.

**Impact:** if the project directory contains an encrypted `.env.enc.se` (secure-enclave mode
active, `self._active = True`) and the HyperLiquid and Lighter processes both launch from that
same working directory at close to the same time (e.g. dispatched by the same shell script or
supervisor), both independently see `_LOADED_ALREADY == False` and race through
`EnvLoader.load_env()`'s decrypt → load → delete cycle against the *same* `.env` file:

1. Decrypts `.env.enc.se` → plaintext `.env` in the CWD (`decrypt_env()`,
   `argus/_argus_utils.py:514-530`) — both processes do this; idempotent, harmless on its own.
2. Loads `.env` via `python-dotenv`. Whichever process's `dotenv_load_dotenv(dotenv_path=".env")`
   runs *after* the other process's step 3 (below) has already deleted the file loads nothing —
   `python-dotenv` treats a missing file as "nothing loaded" rather than raising, so that process
   silently proceeds with `HYPERLIQUID_WALLET_ADDRESS` / `HYPERLIQUID_PRIVATE_KEY` (or whatever
   else `.env` was carrying) absent from `os.environ`. For HyperLiquid specifically this surfaces
   as a `KeyError` at `argus/perpetuals/hyper/__init__.py:94` or `:96`.
3. Deletes `.env` (`os.remove(".env")`, `argus/_argus_utils.py:578-582`). Whichever process calls
   this second, after the first already removed the file, hits a
   `FileNotFoundError("... state of the system was corrupted")` — a misleading error for what is
   actually just a launch-ordering race, not real state corruption.

This only bites when `.env.enc.se` is present and active (secure-enclave decrypt mode). With a
plain `.env` file, `load_env()` calls `_dotenv_load_dotenv()` with no path override and never
deletes anything, so concurrent reads of `.env` from two processes are safe.

**Mitigation if launching both from an automated script**: stagger the two `runtime.py` launches
(even a ~1s delay is enough for the first process's decrypt/load/delete cycle to finish before the
second starts), or drop an already-decrypted `.env` in place beforehand so neither process needs
to touch the encrypted flow at start time.

## Cache: isolated today, would collide if that changed

Both exchanges already have a cache (`argus/cache_sys/__init__.py`'s `FastCache` +
`DomainCache`), and each uses its **own dedicated file**, not the shared default:

```python
# argus/perpetuals/hyper/rest.py:9
_HL_CACHE = _DomainCache('hyperliquid', FastCache(cache_file="~/.argus/hyperliquid_cache.pkl"))

# argus/perpetuals/lighter/rest.py:7
_LIGHTER_CACHE = _DomainCache('lighter', FastCache(cache_file="~/.argus/lighter_cache.pkl"))
```

This is the same isolation pattern Polymarket uses (`~/.argus/polymarket_cache.pkl`, separate
from the shared `~/.argus/capital_cache.pkl` used by IB/Capital.com/Binance —
`docs/system/CACHE.md:146-157`). Only `HyperLiquidRest.get_all_perpetuals()`
(`argus/perpetuals/hyper/rest.py:50-60`, 24h expiration) is cached today; Lighter's `rest.py`
defines `_LIGHTER_CACHE` but doesn't yet apply `cache_decorator` to any method. Because each
exchange writes to its own file, **HyperLiquid and Lighter running concurrently have zero cache
file contention with each other right now.**

That isolation is load-bearing, because `FastCache` itself has no cross-process protection at
all:

- `self._write_lock` is a `threading.Lock` — it only synchronizes threads *inside one
  interpreter*. Two separate `runtime.py` processes each get their own lock object; neither
  knows the other exists.
- `save_cache()` (`argus/cache_sys/__init__.py:66-107`) does `os.rename(cache_file,
  backup_file)` then reopens the (now-recreated) path and `pickle.dump()`s the process's
  **entire in-memory `self.cache` dict** — every domain that process has ever loaded or
  touched, not just its own.
- Each process lazily loads its own full snapshot of the file on first access
  (`ensure_loaded()` / `load_cache()`, `argus/cache_sys/__init__.py:38-64`) and never refreshes
  it from disk again before writing.
- There is no `flock`/OS-level file lock anywhere in `argus/cache_sys/` or
  `argus/cache_sys/compatibility.py` — writers don't coordinate, they just clobber.

**Impact if HyperLiquid/Lighter ever reused the shared default `CACHE`
(`~/.argus/capital_cache.pkl`)**: this would be a regression to exactly the failure mode
`docs/system/CACHE.md:434-441` and `:508-518` already warn about for running multiple Argus
instances at once ("cache corruption," "multiple instances still risky (avoid)") — newly applied
to two exchange dispatchers that are *expected* to run side by side:

- **Lost updates, not key collisions.** `DomainCache` partitions by domain string
  (`'hyperliquid'` vs `'lighter'` vs `'capital_com.api.resolve_symbol'`), so there's no key
  clash inside the dict itself. The problem is at the file level: whichever process calls
  `save_cache()` last wins outright, silently overwriting the on-disk file with only the
  domains its own in-memory snapshot happened to contain — including reverting or dropping
  domains that a different process (Lighter, HyperLiquid, or IB/Capital.com/Binance, since
  they'd now share the same file) wrote in the meantime.
- **Non-atomic backup rotation.** The `.bak` swap (`os.remove` + `os.rename`,
  `argus/cache_sys/__init__.py:73-76`) isn't synchronized across processes either, so the
  safety net itself can get overwritten by the other process mid-race — exactly when you'd
  need it to recover from a bad write.
- **Actual corruption is possible, not just hypothetical.** If two processes' `open(file,
  'wb')` + `pickle.dump()` calls physically overlap, the result can be a truncated/malformed
  pickle — the `pickle.UnpicklingError` / `EOFError` symptoms already documented in
  `docs/system/CACHE.md:452-457` as known cache-corruption failure modes.
- Retrying after a corrupt read isn't graceful either: `FastCache.load_cache()`
  (`argus/cache_sys/__init__.py:56-62`) raises a bare `ValueError` on
  `UnpicklingError`/`EOFError` with no automatic fallback to `.bak` — a human has to run the
  cache CLI (`python -m argus.cache_utils`) to restore it.

**Impact if a new exchange copied the existing per-exchange-file pattern**: none — that's what
HyperLiquid and Lighter already do. The only way to hit the collision above without touching the
shared `CACHE` is running **two instances of the *same* exchange** concurrently (e.g. two
`hyperliquid` dispatchers on different `--port`s) — both would still target the single
`hyperliquid_cache.pkl`, since the file is keyed per-exchange, not per-port or per-process.

## Summary

| Concern | Concurrent-safe? |
|---|---|
| TCP port binding | Yes, as long as `--port` differs (defaults already differ: 9972 vs 9974) |
| Dispatcher instance state (routing tables, sockets, correlation IDs) | Yes — fully per-instance |
| REST clients / credentials | Yes — disjoint env vars, disjoint upstream hosts |
| `utils3.networking.sockets._threads` global | Yes across processes (separate interpreters); would need attention only if both dispatchers were ever hosted in one process |
| Secure `.env.enc.se` decrypt-load-delete cycle | **No** — real race if both processes launch from the same CWD at ~the same time with secure-enclave mode active; not an issue with a plain `.env` |
| Cache (current: dedicated `hyperliquid_cache.pkl` / `lighter_cache.pkl`) | Yes, as implemented today — no shared file |
| Cache (hypothetical: reusing shared `capital_cache.pkl`) | **No** — `FastCache` has no cross-process locking; would reintroduce lost-update / corruption risk already documented in `docs/system/CACHE.md` |

## Hyperliquid websocket budget

`HyperLiquidDispatcher` now opens two websocket connections to `wss://api.hyperliquid.xyz/ws`: the order-book
stream and the account-update stream (`HyperLiquidAccountWss`, see
[`account_update`](../hyperliquid/DISPATCHER.md#account_update)). Hyperliquid limits connections and unique
subscribed users per IP (10 each); the account stream uses one of each, for the master wallet only. Run no more
than a handful of Hyperliquid dispatcher processes from one IP.

## Lighter websocket budget

`LighterDispatcher` likewise opens two websocket connections to `wss://mainnet.zklighter.elliot.ai/stream`: the
order-book stream and the account-update stream (`LighterAccountWss`, see
[`account_update`](../lighter/DISPATCHER.md#account_update)). Lighter allows 255 connections and 500
subscriptions per connection, so the second connection is negligible. The account stream's `account_all_orders`
channel uses a short-lived native auth token minted from the API key (refreshed before expiry);
`account_all_trades` needs no auth.
