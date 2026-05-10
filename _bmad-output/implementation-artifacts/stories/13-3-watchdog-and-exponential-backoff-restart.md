# Story 13.3: Watchdog & Exponential Backoff Restart

## Status: done

## Story

**As** mrqdt,
**I want** a watchdog that detects crashed strategy threads and restarts them with exponential backoff,
**so that** a single strategy crash is self-healing without operator intervention.

## Acceptance Criteria

- **AC1:** Given the watchdog monitoring all registered strategy threads, when `thread.is_alive()` returns `False` for a strategy thread AND the thread's `stop_event` is NOT set (unintentional crash, not an explicit unload), then the watchdog: logs ERROR with strategy name and restart count; resets Prometheus gauges for that strategy to sentinel values (0) synchronously before the backoff sleep; increments `bot_strategy_restart_total{strategy}`; sets `bot_strategy_backoff_seconds{strategy}` to the current backoff duration.

- **AC2:** Given a crashed strategy thread, when the watchdog restarts it, then the restart backoff is keyed by strategy NAME (the class name string), not by thread object; backoff sequence: 5s → 10s → 30s → 60s → 60s (cap); restart count and `last_restart_ts` persist in the watchdog registry (`FileWatcher._backoff`) for the process lifetime; a new thread inherits the existing backoff state.

- **AC3:** Given a strategy that restarts after a crash, when the new thread starts, then `run_startup_reconciliation` is called for that strategy before the new thread's event loop starts accepting events (this is guaranteed because `_load_new` already calls reconciliation).

- **AC4:** Given `bot_strategy_restart_total{strategy}` counter and `bot_strategy_backoff_seconds{strategy}` gauge (both already exist in `prometheus.py` from Story 13.2), when a restart occurs, then the counter is incremented and the gauge is set to the current backoff duration before the backoff sleep.

- **AC5:** Given the watchdog loop running alongside the file-watcher loop, when a strategy is in the backoff-restart window (popped from `_loaded` but not yet restarted), then `FileWatcher._rescan()` does NOT attempt to load it again (prevented by `_pending_restart` set).

## Tasks / Subtasks

- [x] T1: Add `reset_strategy_gauges` placeholder to `prometheus.py` (AC1)
  - [x] T1.1: Add `reset_strategy_gauges(strategy: str) -> None` to `metrics/prometheus.py` — no-op placeholder body with comment that Story 16.1 fills in the actual gauge resets (`bot_position_size`, `bot_unrealized_pnl`, `bot_drawdown`, `bot_consumer_lag`)

- [x] T2: Add watchdog state and backoff constants to `FileWatcher` (AC2)
  - [x] T2.1: Add `_BACKOFF_SEQUENCE: list[float] = [5.0, 10.0, 30.0, 60.0]` module-level constant in `registry.py`
  - [x] T2.2: Add `self._backoff: dict[str, tuple[float, int, float]] = {}` to `FileWatcher.__init__` — maps `class_name → (current_backoff_s, restart_count, last_restart_ts)`
  - [x] T2.3: Add `self._pending_restart: set[str] = set()` to `FileWatcher.__init__`

- [x] T3: Implement `_handle_crash` in `FileWatcher` (AC1, AC2, AC3)
  - [x] T3.1: Implemented `async def _handle_crash(self, class_name: str) -> None` with full crash handling sequence

- [x] T4: Implement `watch_loop` in `FileWatcher` (AC1, AC2)
  - [x] T4.1: Implemented `async def watch_loop(self) -> None` — 5-second poll, detects dead threads with stop_event not set

- [x] T5: Update `_rescan` to skip `_pending_restart` (AC5)
  - [x] T5.1: `_rescan` now uses `current_names - loaded_names - self._pending_restart` for new strategy detection

- [x] T6: Wire `watch_loop` into `main.py` lifespan (AC1)
  - [x] T6.1: Added `_watchdog_task = asyncio.create_task(_file_watcher.watch_loop())` after `_bus_manager.start()`
  - [x] T6.2: Added `_watchdog_task.cancel()` + await in teardown

- [x] T7: Write L1 tests in `tests/test_watchdog.py` (all ACs)
  - [x] T7.1: `test_watchdog_detects_crash_calls_handle_crash`
  - [x] T7.2: `test_handle_crash_resets_metrics_before_sleep`
  - [x] T7.3: `test_handle_crash_backoff_sequence`
  - [x] T7.4: `test_handle_crash_restart_count_persists`
  - [x] T7.5: `test_watchdog_ignores_intentional_stop`
  - [x] T7.6: `test_rescan_skips_pending_restart`
  - [x] T7.7: `test_handle_crash_import_failure_does_not_restart`

- [x] T8: All tests pass — mypy --strict clean

### Review Findings

- [x] [Review][Patch] watch_loop no try/except around _handle_crash — any exception permanently kills watchdog [registry.py:watch_loop]
- [x] [Review][Patch] CancelledError during backoff sleep leaves _pending_restart stale — needs try/finally [registry.py:_handle_crash]
- [x] [Review][Defer] Sequential restart of simultaneous crashes causes additive delays — design choice [registry.py:watch_loop] — deferred, pre-existing design
- [x] [Review][Defer] bot_strategy_backoff_seconds not reset to 0 after successful restart — UX gap [registry.py:_handle_crash] — deferred, spec doesn't require
- [x] [Review][Defer] No explicit AC3 ordering test (reconciliation before thread) — coverage gap [tests/test_watchdog.py] — deferred, implementation is correct
- [x] [Review][Defer] Old asyncio event loop not closed after crash — ResourceWarning [registry.py:_handle_crash] — deferred, not a correctness issue
- [x] [Review][Defer] Watchdog poll interval hardcoded 5s — not from settings [registry.py:watch_loop] — deferred, untunable without code change

## Dev Notes

### What already exists (do not reinvent)

**`bot_service/strategy/registry.py`** — read the COMPLETE file before touching. Key existing state:
- `FileWatcher._loaded: dict[str, tuple[Path, StrategyHandle, threading.Event, float]]` — `class_name → (path, handle, stop_event, mtime)`. The `stop_event` is the signal used by `_stop_thread` to intentionally stop a thread. Watchdog uses `stop_event.is_set()` to distinguish crash (unintentional, stop_event NOT set) from unload (intentional, stop_event IS set).
- `_import_strategy_class(path)` — already implemented; returns `type[BaseStrategy] | None`
- `_load_new(class_name, path, cls, pre_start=False)` — already handles reconciliation, thread spawn, and BusManager registration. Watchdog calls this for restart.
- `_stop_thread` — already handles `thread.join(timeout)` with CRITICAL log on timeout.

**`bot_service/metrics/prometheus.py`** — already has `inc_strategy_restart(strategy)` and `set_strategy_backoff_seconds(strategy, seconds)` implemented (added in Story 13.2). Do NOT add these again. Only add `reset_strategy_gauges(strategy)`.

**`bot_service/main.py`** — already has `_watcher_task = asyncio.create_task(_file_watcher.run_loop())` in the lifespan. Add `_watchdog_task` in the same block. Teardown already cancels `_watcher_task` with `await _watcher_task` in try/except CancelledError — follow the same pattern for `_watchdog_task`.

### Backoff sequence implementation

The sequence 5 → 10 → 30 → 60 → 60 is stored as:
```python
_BACKOFF_SEQUENCE = [5.0, 10.0, 30.0, 60.0]
```

Current backoff index = `min(restart_count, len(_BACKOFF_SEQUENCE) - 1)`. After a crash:
- restart_count 0 → use `_BACKOFF_SEQUENCE[0]` = 5s
- restart_count 1 → use `_BACKOFF_SEQUENCE[1]` = 10s
- restart_count 2 → use `_BACKOFF_SEQUENCE[2]` = 30s
- restart_count 3+ → use `_BACKOFF_SEQUENCE[3]` = 60s (cap)

`_backoff` dict value is `(current_backoff_s, restart_count, last_restart_ts)` where `last_restart_ts = time.time()`.

### Crash vs. intentional stop — the stop_event distinction

This is the critical invariant:
```python
# Crash: thread died without stop_event being set
crashed = not handle.thread.is_alive() and not stop_event.is_set()

# Intentional stop (unload, hot-reload): stop_event.set() was called by _stop_thread
intentional = stop_event.is_set()
```

The watchdog MUST check BOTH conditions. If stop_event is set, the thread dying is expected and should NOT trigger a restart.

### _pending_restart prevents double-restart

When `_handle_crash` is called, it immediately pops from `_loaded` and adds to `_pending_restart`. During the `await asyncio.sleep(backoff_s)`, the asyncio event loop can run other coroutines — specifically `run_loop → _rescan`. Without `_pending_restart`, `_rescan` would see the strategy file still on disk, not in `_loaded`, and try to load it as a new strategy, bypassing the watchdog backoff.

```python
# In _rescan (T5):
for class_name in current_names - loaded_names - self._pending_restart:
    path, cls = valid[class_name]
    await self._load_new(class_name, path, cls, pre_start=False)
```

`_handle_crash` removes from `_pending_restart` only AFTER `_load_new` completes (or fails).

### reset_strategy_gauges is a forward stub

The per-strategy gauges (`bot_position_size`, `bot_unrealized_pnl`, `bot_drawdown`, `bot_consumer_lag`) are defined in Story 16.1. For this story, add a no-op:

```python
def reset_strategy_gauges(strategy: str) -> None:
    """Reset per-strategy position/P&L gauges to 0 on watchdog crash detection.

    Called synchronously before the backoff sleep (AC1). Actual gauge resets
    implemented in Story 16.1 when bot_position_size etc. are added.
    """
```

Story 16.1 will update this function body — do NOT implement the actual gauge resets now.

### imports needed in registry.py

Add to the existing imports:
```python
import time
from bot_service.metrics.prometheus import (
    inc_strategy_load_failure,
    inc_strategy_restart,
    reset_strategy_gauges,
    set_strategy_backoff_seconds,
)
```

The `inc_strategy_restart` and `set_strategy_backoff_seconds` imports are new additions for this story.

### main.py teardown pattern (copy exactly)

```python
# Existing pattern for _watcher_task:
_watcher_task.cancel()
try:
    await _watcher_task
except asyncio.CancelledError:
    pass

# Add same pattern for _watchdog_task:
_watchdog_task.cancel()
try:
    await _watchdog_task
except asyncio.CancelledError:
    pass
```

### Testing approach for async watchdog

The `watch_loop` is an infinite async loop. Tests use one of two patterns:

**Pattern A** — test `_handle_crash` directly (bypasses the loop):
```python
# Manually set up _loaded with a dead thread, call _handle_crash directly
await file_watcher._handle_crash("MyStrategy")
# Assert side effects
```

**Pattern B** — test `watch_loop` via asyncio.wait_for with short timeout:
```python
# Set up a dead thread in _loaded, monkeypatch asyncio.sleep to return immediately
# wrap in asyncio.wait_for with timeout 1.0
```

Pattern A is preferred for unit tests — it's more direct and avoids timing issues.

For `test_handle_crash_resets_metrics_before_sleep`, use `unittest.mock.patch` and capture call order:
```python
call_order = []
with patch("bot_service.strategy.registry.reset_strategy_gauges", side_effect=lambda s: call_order.append("reset")):
with patch("bot_service.strategy.registry.asyncio.sleep", new_callable=AsyncMock, side_effect=lambda s: call_order.append("sleep")):
    await file_watcher._handle_crash("MyStrategy")
assert call_order.index("reset") < call_order.index("sleep")
```

### mypy --strict compliance

- `_backoff: dict[str, tuple[float, int, float]]` — all three elements typed
- `_pending_restart: set[str]` — typed
- `time.time()` returns `float` — consistent with tuple type
- `watch_loop` and `_handle_crash` need full return type annotation `-> None`
- `reset_strategy_gauges` must have `-> None` annotation

### Minimal strategy file for tests

```python
def _minimal_strategy_src(class_name: str) -> str:
    return f"""
from bot_service.strategy.base import BaseStrategy

class {class_name}(BaseStrategy):
    min_lookback = 1
    max_position_pct = 0.05
    stop_loss_pct = 0.02
    paper_trading = True
    def subscribe(self): pass
"""
```

(same helper used in test_file_watcher.py — import or duplicate as needed)

## Test Coverage

### L1 unit tests in `tests/test_watchdog.py`

1. `test_watchdog_detects_crash_calls_handle_crash` — dead thread (stop_event not set) in `_loaded`; verify `_handle_crash` is triggered
2. `test_handle_crash_resets_metrics_before_sleep` — verify call order: reset_gauges → inc_restart → set_backoff → sleep
3. `test_handle_crash_backoff_sequence` — 5 consecutive crashes → verify sleeps: 5, 10, 30, 60, 60
4. `test_handle_crash_restart_count_persists` — crash twice → `_backoff[name]` restart_count=2
5. `test_watchdog_ignores_intentional_stop` — stop_event set → no restart triggered
6. `test_rescan_skips_pending_restart` — name in `_pending_restart` → `_load_new` not called by `_rescan`
7. `test_handle_crash_import_failure_does_not_restart` — import returns None → strategy not re-added to `_loaded`

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- T1: Added `reset_strategy_gauges(strategy)` no-op to prometheus.py with forward-reference comment for Story 16.1.
- T2: Added `_BACKOFF_SEQUENCE = [5.0, 10.0, 30.0, 60.0]` module constant; `_backoff` and `_pending_restart` instance attributes to `FileWatcher.__init__`.
- T3: Implemented `_handle_crash` — pops from `_loaded`, adds to `_pending_restart`, resets metrics before sleep, sleeps backoff, advances backoff state, re-imports, calls `_load_new`. Cleans up `_pending_restart` on both success and failure paths.
- T4: Implemented `watch_loop` — 5-second poll loop, detects `not alive and not stop_event.is_set()` as crash condition.
- T5: Updated `_rescan` to use `current_names - loaded_names - self._pending_restart` to prevent double-restart race.
- T6: Wired `_watchdog_task` in `main.py` lifespan; teardown cancels it alongside `_watcher_task`.
- T7: 7 L1 tests in `tests/test_watchdog.py` — all pass. Tests cover crash detection, metric call order (reset before sleep), backoff sequence 5→10→30→60→60, restart count persistence, intentional-stop ignore, pending_restart skip in rescan, import failure cleanup.
- T8: 152 tests pass (145 pre-existing + 7 new), mypy --strict clean (27 source files).

### File List

- bot_service/metrics/prometheus.py (UPDATE — add `reset_strategy_gauges` no-op placeholder)
- bot_service/strategy/registry.py (UPDATE — add `_BACKOFF_SEQUENCE`, `_backoff`, `_pending_restart` to FileWatcher; add `watch_loop`, `_handle_crash`; update `_rescan` to skip `_pending_restart`; add `import time` and new metric imports)
- bot_service/main.py (UPDATE — add `_watchdog_task` create + cancel in lifespan)
- tests/test_watchdog.py (CREATE — 7 L1 unit tests)
