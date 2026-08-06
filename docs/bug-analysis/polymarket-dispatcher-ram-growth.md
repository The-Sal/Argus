# Polymarket Dispatcher RAM Growth (August 2026)

## Symptom

The dispatcher's idle footprint had already been cut from 2.1 GB to ~600 MB by moving the
Gamma event set out of process into `argus-polymarket-db` (APDB). Despite that, a
long-running production process kept climbing:

| observation | value |
|---|---|
| host | Linux x86, 7.8 GB total RAM |
| uptime at inspection | 29 h 31 m |
| RSS | **5.0 GB** |
| swap consumed by the process | **1.1 GB** |
| host free memory | 327 MB |
| live threads | **782** |
| highest thread name seen | `Thread-34146` → ~34,000 threads created in 29.5 h |
| open fds / sockets | 86 / 70 (not an fd leak) |
| VSZ | 254 GB |

Assets had been scaled from 55 up to 157 over the run. The question was whether this was a
leak, allocator fragmentation, or just the cost of the extra assets.

It is a leak. Three of them, plus one allocator effect that amplifies all three.

## Investigation

Static reading of `argus/polymarket/`, `argus/polymarket_direct/`, and the `utils3`
dependency, followed by live inspection of the production process (PID 65462).

Per-object costs were **measured on the exact target interpreter** — CPython 3.14
free-threaded — not estimated, because free-threaded object layouts differ from the
default build (see "Python-specific memory behaviour" below).

| thing | measured cost |
|---|---|
| retained dead `threading.Thread` object | **4,853 B** |
| thread create/destroy churn residue (not retained) | ~204 B/thread |
| one `_updates` timestamp (40 B float + list slot + overallocation) | **67 B** |
| one order-book level `{'price','size'}` dict | 347 B |
| one `_asset_id_to_ticker` entry (77-char str key → tuple) | 342 B |
| one `PolymarketEvent` + 3 `Market`s | 10.9 KB |
| import baseline (`argus.polymarket_direct` + pandas + eth stack) | 143 MB |

### Live process forensics

Thread names on Linux are truncated to 15 bytes in `/proc/PID/task/*/comm`, but Python
formats them `Thread-N (target_name)`, so shorter thread numbers leak more of the target.
Cross-tabbing `comm` against `wchan`:

| count | comm | wchan | meaning |
|---:|---|---|---|
| 672 | `Thread-NNNNN (_` | `futex_do_wait` | parked on an Event |
| 49 | `Thread-NNNN (_d` | `futex_do_wait` | `_defer_restore_state` — name visible |
| 23 | `Thread-NNNNN (_` | `wait_woken` | socket recv/send |
| 8 | `Thread-NNNNN (_` | `ep_poll` | websocket |
| 7 | `Thread-NNN (_h` | `futex_do_wait` | `_handle_incoming_packets` on a lock |
| 5 | `OrderBookStoreT` | `futex_do_wait` | tick-size pool |

**721 of 782 threads were parked on a futex**, and the 3-digit sample identified the
function. That was the entry point to bug 1.

RSS by region, from `/proc/PID/smaps`:

```
2218 MB  +  837 MB  +  653 MB  +  446 MB  +  415 MB  +  136 MB   =  4.7 GB
```

Six large mimalloc arenas, not thread stacks. Read syscalls ran at 953/s baseline
(spiking to 3,608/s during the APDB refresh), which at ~2-3 reads per WebSocket frame is
roughly 380 frames/s — the number that makes bug 2's arithmetic land on 2.2 GB.

---

## Bug 1 — `_defer_restore_state` parks a thread forever on an orphaned Event

`argus/polymarket_direct/wss.py`, `PolyMarketOrderBookConn`.

### Before

```python
def _on_reconnect_start(self):
    self._reset_threading_events()   # rebinds wait_till_first_pong to a NEW Event
    self._defer_restore_state()

@runAsThread
def _defer_restore_state(self):
    self.wait_till_first_pong.wait()   # attribute read INSIDE the thread; no timeout
    ...replay roster...
```

and in the base class:

```python
def _reset_threading_events(self):
    self.wait_till_socket_open = threading.Event()
    self.wait_till_first_pong = threading.Event()
```

### The defect

`_reset_threading_events()` does not *clear* the Event. It **constructs a new one and
rebinds the attribute**. The spawned thread reads `self.wait_till_first_pong` when it
*runs*, not when it is spawned. Sequence:

1. Close #1 → `_on_reconnect_start` → Event `E1` created → thread `T1` spawned. `T1`
   evaluates `self.wait_till_first_pong` → gets `E1` → blocks in `E1.wait()`.
2. Close #2 arrives before the first PONG → `_reset_threading_events()` → `E2` replaces
   `E1` on the instance.
3. `E1` is now referenced **only** by `T1`'s own stack frame. No other code path can
   reach it, so nothing will ever call `E1.set()`.
4. `T1` blocks on `E1` forever. `Event.wait()` with no timeout is an unbounded
   `futex_do_wait` — there is no wakeup, no spurious return, no escape.

The trigger window is wide. PING is sent every 10 s (`PolymarketWSSBase.ping`), and
`wait_till_first_pong` is only set when a PONG comes back, so *any* two closes within
roughly 10 s of each other orphan a thread. `_on_close_base` sleeps 1 s and immediately
reconnects, so a flapping proxy produces exactly that pattern.

Measured: 721 parked threads in 29.5 h across 15 shards ≈ 24/hour.

### Why it costs memory, not just thread slots

Each parked thread is *live*, so it keeps:

- its 8 MB stack reservation (mostly untouched — virtual, contributes to the 254 GB VSZ,
  little to RSS), and
- **its own mimalloc heap**, which on a free-threaded build is per-thread and is only
  abandoned and made reclaimable when the thread exits. A parked thread's arena stays
  resident forever. Those are the ~21 MB arenas visible in the smaps output.

It also degrades the reconnect path itself: the threads that pile up are precisely the
ones responsible for replaying subscriptions after a drop, so the failure compounds when
the connection is already unhealthy.

### After

```python
def _on_reconnect_start(self):
    self._reset_threading_events()
    self._defer_restore_state(self.wait_till_first_pong)   # bind at spawn time

@runAsThread
def _defer_restore_state(self, pong_event: threading.Event):
    if not pong_event.wait(timeout=self._restore_state_timeout):
        logging.warning(...)
        return
    if self._internally_closed:
        return
    if pong_event is not self.wait_till_first_pong:
        return   # superseded by a newer reconnect; it owns the restore
    ...replay roster...
```

Three independent guarantees:

1. **Binding the Event as a parameter** makes the wait immune to the attribute swap. The
   value is evaluated at call time and captured in the `Thread`'s `args` tuple.
2. **The timeout** (`POLYMARKET_WS_RESTORE_TIMEOUT`, default 120 s) guarantees the thread
   exits even for a wedged socket. 120 s is deliberately generous: PING fires every 10 s
   and the ping/pong detector tears the socket down after 3 missed PONGs (~30 s), which
   spawns a fresh restore thread anyway — so this only fires for a socket that is wedged
   rather than merely slow.
3. **The identity check** means a superseded thread defers instead of double-replaying
   the roster.

---

## Bug 2 — `OrderBookStore._updates` grew without bound

`argus/polymarket_direct/wss.py`, `OrderBookStore`.

### Before

```python
self._updates: list[float] = []      # __init__
...
def apply_message(self, message: str) -> None:
    self._last_msg_recv_ts = time.perf_counter()
    self._updates.append(time.time())   # every frame, forever
```

Nothing ever trimmed it. The only consumers are `print_stats()` and
`_debug_print_stats_loop()`; the loop is never started and `print_stats` is reachable
only from the `__main__` demo at the bottom of the file. **In production these samples
were never read at all.**

### The arithmetic

67 B per frame × ~380 frames/s ≈ **2.2 GB/day** of permanently live objects. That matches
the 2.2 GB dominant arena in the smaps output.

The second-order effect is worse than the raw total. `apply_message` is called on **every
shard thread**, so the immortal floats are allocated into *each* thread's own mimalloc
arena. That is why prod showed 4.7 GB spread across six large arenas rather than one big
one, and why none of it was ever returned to the OS (see arena pinning below).

`print_stats` was also O(n²) over that unbounded list — at production sizes calling it
would have wedged the process long before OOM.

### After

```python
self._updates: deque[float] = deque(
    maxlen=int(os.environ.get('POLYMARKET_WS_STAT_SAMPLES', '4096'))
)
```

`deque(maxlen=...)` evicts from the head on overflow in O(1), so the append in
`apply_message` stays hot-path cheap and the window is hard-bounded.

`print_stats` rewritten from the O(n²) nested loop to an O(n) two-pointer sweep over a
sorted copy.

> **Note on the sort.** The sweep requires ascending input. `apply_message` runs
> concurrently on every shard thread and there is **no lock** around
> `read time.time() → append`, so two threads can interleave and leave the deque very
> slightly out of order. Without the sort the sweep silently overcounts — e.g. samples
> `[10, 0]` yield 2 instead of the correct 1. The old brute force was order-independent,
> so dropping the sort would have been a correctness regression. `O(n log n)` on a
> bounded 4096-sample window is still far cheaper than the `O(n²)` it replaced.

---

## Bug 3 — `utils3` retained a dead `Thread` object per received packet

Fixed **upstream**, not in this repo. `utils3` ≤ 0.2.3 had:

```python
_threads = []                                   # module-global

def _execute_async(func, *args, **kwargs):
    thread = threading.Thread(target=func, args=args, kwargs=kwargs, daemon=True)
    thread.start()
    _threads.append(thread)                     # never cleared
    return thread
```

`Server._builtin_on_connect` calls this for **every `client.recv(1024)`**, so every P1
control packet the dispatcher received — `ping`, `get_order_status`, `get_balance`,
`place_order`, `subscribe` — permanently retained a dead `Thread` object. The only reader
was `stop()`, which the dispatcher never calls.

~34,000 threads created in 29.5 h × 4,853 B ≈ **166 MB**.

`utils3` 1.0.0 (commit `b4aa027`) moves the bookkeeping to per-instance state that
self-cleans in a `finally`, force-closes lingering clients in `stop()`, and always closes
the client fd on disconnect. Resolved here by bumping `uv.lock` 0.2.3 → 1.0.0.

### Latent framing bug in the same loop (not yet fixed)

`recv(1024)` has **no reassembly buffer**, while `argus/protocol.py::encode_packet` allows
P1 packets up to 9999 bytes. Any inbound message over 1024 B arrives split across N recv
calls → N handler threads, and `decode_multiple_packets` raises `ValueError` on every
truncated chunk. Clients must keep P1 messages under 1024 B; between 1025 and 9999 they
fail *and* cost ~10× the thread churn.

---

## Python-specific memory behaviour

This process runs **CPython 3.14 free-threaded** (`.python-version` = `python3.14t`).
Several defaults differ from a standard GIL build in ways that made these leaks much more
expensive than the same code would be on 3.13.

### Object headers are larger

Free-threaded builds add per-object fields for biased reference counting — `ob_tid`,
`ob_ref_local`, `ob_ref_shared` — on top of `ob_type`. Consequences measured here:

| object | default build | free-threaded 3.14 |
|---|---:|---:|
| `float` | 24 B | **40 B** |

A `list` of N floats therefore costs `N × (40 + 8)` plus the list's ~12.5% growth
overallocation ≈ **67 B/sample**, versus ~33 B on a GIL build. Bug 2 was roughly twice as
expensive as the same code would have been on 3.13.

### The allocator is mimalloc, and heaps are per-thread

Free-threaded CPython does not use `pymalloc`. It allocates objects through **mimalloc**,
and each thread gets **its own heap**. Two consequences drove this incident:

- **Arena pinning.** mimalloc returns memory to the OS only when a whole page/segment is
  free. A long-lived object anywhere in a page keeps the entire page resident. Bug 2
  sprinkled immortal floats across every shard thread's arena, so the transient churn
  from the APDB refresh could never be released either — the immortal floats pinned the
  pages the transients would otherwise have vacated.
- **Parked threads keep their arenas.** A thread's heap is abandoned (and its pages made
  reclaimable) only when the thread exits. 721 permanently parked threads meant 721 live
  heaps. This is why bug 1 is a memory bug and not merely a thread-count bug.

Demonstrated directly on the target interpreter: allocate 5M floats (+334 MB RSS), free
them all, `gc.collect()` — **RSS does not drop**, and subsequent allocations reuse that
heap rather than growing it. Freeing Python objects does not return memory to the OS.
This is the answer to "is it fragmentation?": the fragmentation is real but it is a
*consequence* of the immortal objects, not an independent cause. Fix the leaks and it
stops mattering.

- **VSZ is not a leak signal here.** 254 GB of virtual address space is mimalloc
  *reserving* (not committing) arenas for 782 threads. Alarming in `ps`, meaningless for
  actual memory pressure. Read `VmRSS` from `/proc/PID/status`, or `Rss`/`Pss` from
  `smaps_rollup`.

### QSBR delays reclamation

Free-threaded CPython uses quiescent-state-based reclamation (`_PyMem_FreeDelayed`) for
memory that lock-free readers might still be touching — dict keys arrays, list storage.
Freed blocks sit on a deferred queue until every thread passes a quiescent state. A
process with hundreds of threads parked in long blocking waits advances that sequence
slowly, so reclamation lags. This inflates steady-state RSS in a way that looks like a
leak but is allocator accounting.

### Why a dead `Thread` object costs 4.8 KB

`Thread.run()` ends with `del self._target, self._args, self._kwargs`, which does
correctly release the socket and payload references. But that `del` also **collapses the
instance's key-sharing dict into a standalone one**. CPython gives instances of the same
class a shared keys object; deleting an attribute forces materialisation of a full,
private dict. Add the `_started` Event (which owns a `Condition`, a lock, and a
`deque` — a deque allocates a 64-slot block up front), the `_tstate_lock`, the
`_handle`, the name string, and the per-thread `_invoke_excepthook` closure with its
cells, and the corpse lands near 5 KB. Measured by comparing two identical 30,000-thread
runs that differ only in whether the `Thread` objects are appended to a list:
`drop` → +6.1 MB, `keep` → +145.6 MB.

### Thread churn is not free either

Even with the retention fixed, `utils3`'s `Server` still spawns **one thread per received
packet**. The churn alone leaves ~204 B/thread of unreturned allocator residue (~6 MB per
30,000 threads) and costs thread-creation latency on the P1 hot path. Not a leak, but a
candidate for a future change to a small `ThreadPoolExecutor` or to handling `on_recv`
inline on the existing per-client thread.

---

## Free-threading is silently disabled at runtime

Importing the dispatcher emits:

```
RuntimeWarning: The global interpreter lock (GIL) has been enabled to load module 'ckzg',
which has not declared that it can run safely without the GIL. To override this behavior
and keep the GIL disabled (at your own risk), run with PYTHON_GIL=0 or -Xgil=0.
```

**The wording suggests the GIL is enabled only for the duration of the import. It is
not — it is enabled permanently for the life of the process.** Verified:

```
$ .venv/bin/python3.14t -c "
import sys; print('start:', sys._is_gil_enabled())
import ckzg; print('after ckzg:', sys._is_gil_enabled())
import time; time.sleep(0.1); print('settled:', sys._is_gil_enabled())"
start:      False
after ckzg: True
settled:    True
```

Under PEP 703, a free-threaded build enables the GIL at runtime when it imports any
extension module that does not declare `Py_mod_gil = Py_MOD_GIL_NOT_USED`. That switch is
one-way for the process.

Import chain: `py_clob_client_v2` → `eth_account` → typed/blob transactions → `ckzg`
(2.1.7 at time of writing). Because it is reached through `py_clob_client_v2.client` at
dispatcher import time, **every production run has had the GIL on.**

Consequences for this codebase:

- The comment on `_build_pool` in `argus/polymarket/__init__.py` ("under 3.14t the work
  also runs truly in parallel since the sign path is CPU-bound") does not hold in prod.
  Order building is serialised.
- The memory costs above are *not* affected — object layout and mimalloc are properties of
  the free-threaded **build**, not of whether the GIL is currently engaged. The process
  still pays 40-byte floats and per-thread heaps while getting no parallelism.

### Forcing free-threading to stay enabled

Either of these makes CPython refuse to re-enable the GIL:

```bash
PYTHON_GIL=0 python3.14t runtime.py polymarket        # env var
python3.14t -X gil=0 runtime.py polymarket            # interpreter flag
```

Verified working end-to-end:

```
$ PYTHON_GIL=0 .venv/bin/python3.14t -c "
import sys, argus.polymarket_direct.rest
print('GIL enabled after full argus import:', sys._is_gil_enabled())"
GIL enabled after full argus import: False
```

**This is genuinely "at your own risk" and should not be flipped on blind.** The override
does not make `ckzg` thread-safe; it just stops CPython protecting you from it. Before
enabling it in production, establish that `ckzg` is either actually thread-safe or only
ever entered from one thread. In this codebase the eth/`ckzg` path is reached through
order signing, which `_build_pool` runs on up to 10 workers concurrently — so it is
**not** currently single-threaded, and flipping `PYTHON_GIL=0` today would move signing
into genuinely concurrent execution of a C extension that has not declared support for it.

Safer sequencing:

1. Confirm whether a newer `ckzg` (or `eth-account` pinning a newer one) ships wheels
   declaring `Py_MOD_GIL_NOT_USED`. If so, upgrade — the warning disappears and
   free-threading stays on with no override.
2. Failing that, decide whether the parallel signing win is worth the risk, and if so,
   serialise `ckzg` entry behind a lock before setting `PYTHON_GIL=0`.
3. If neither is attractive, note that running on the free-threaded build with the GIL
   forced on is the worst of both worlds — larger objects, per-thread heaps, no
   parallelism — and a standard 3.14 build would use less memory for identical behaviour.

---

## Fixes applied

| # | Change | File |
|---|---|---|
| 1 | Bind Event at spawn, bound the wait, guard against supersession | `argus/polymarket_direct/wss.py` |
| 2 | `_updates` → bounded `deque`; `print_stats` O(n²) → O(n log n) | `argus/polymarket_direct/wss.py` |
| 3 | `utils3` 0.2.3 → 1.0.0 (upstream fix for the retained-`Thread` leak) | `uv.lock` |

### New environment variables

| var | default | effect |
|---|---:|---|
| `POLYMARKET_WS_RESTORE_TIMEOUT` | `120` | seconds a restore thread waits for the first PONG before giving up |
| `POLYMARKET_WS_STAT_SAMPLES` | `4096` | retained WS-frame timestamp samples |

### Verification

Three reconnect scenarios exercised against the patched class with a stubbed
`_send_subscribe_op`:

| scenario | result |
|---|---|
| PONG arrives normally | roster replayed exactly once; 0 threads left |
| second reconnect before first PONG (the bug) | superseded thread exits on timeout instead of parking; roster replayed exactly once, not twice |
| wedged socket, no PONG at all | thread exits at the timeout; nothing replayed |

**0 leaked parked threads.** `_updates` holds at 4,096 after 50,000 appends. The sorted
two-pointer sweep matches the old brute force on 20,000 randomised unsorted trials and on
the `[10, 0]` regression case.

## Remaining work

- **Mapping caches still grow monotonically.** `_build_asset_id_to_ticker_mapping` in
  `argus/polymarket/__init__.py` does a *full* APDB walk and builds complete replacement
  dicts, then merges with `.update()` instead of swapping. Entries for resolved markets
  are never evicted — ~342 B/entry, a few MB/day. The swap must retain entries for
  currently-subscribed asset ids, or a market APDB has dropped mid-flight loses routing.
- **Prod refresh interval is 600 s**, so that full ~16k-event walk (10.9 KB/event) runs
  144×/day. Worth revisiting now that only the derived mappings are kept.
- **`_handle_subscribe` ordering** (`argus/polymarket/__init__.py`):
  `add_socket_to_subscription` runs *before* `market_data.subscribe_to_asset_id`. If the
  latter throws, the routing table keeps an entry for an asset with no feed.
- **P1 framing**: add a reassembly buffer to `utils3`'s `Server`, or raise the recv size.
- **Per-packet thread spawn** in `utils3`'s `Server` — see "Thread churn" above.
- **Dead weight**: `argus/polymarket/_mem_slim.py` is no longer imported anywhere, and
  `POLYMARKET_MEMORY_PRUNING` / `POLYMARKET_AOT_TICK_SIZE` are no longer read.
  `~/.argus/polymarket_cache.pkl` (431 MB on the prod box) is a pre-APDB leftover that
  nothing loads.
