# Story 13.2: File Watcher & Strategy Auto-Load

## Status: done

## Story

**As** mrqdt,
**I want** strategies to be auto-loaded from `strategies/active/` and stopped when moved out, without manual process management,
**so that** deploying a new strategy requires only dropping a `.py` file into the directory.

## Acceptance Criteria

- **AC1:** Given a `.py` file placed in `strategies/active/`, when the file watcher detects it (scan interval: `BOT_FILEWATCHER_INTERVAL_S`, default 60s), then it imports the file via `importlib` in an isolated try/except; if import succeeds and the class name is unique across all loaded strategies, spawns a strategy thread; if import raises `ImportError` or `SyntaxError`, logs ERROR with filename and exception, skips the file, and continues loading other files.

- **AC2:** Given two `.py` files in `strategies/active/` that define a class with the same name, when the file watcher scans, then BOTH files are rejected; `bot_strategy_load_failure_total` is incremented twice (once per file); neither class is loaded; uniqueness is checked across ALL files in each scan cycle, not only on newly added files.

- **AC3:** Given a `.py` file moved out of `strategies/active/` (or deleted), when the file watcher detects the removal, then it sends a stop signal to the corresponding strategy thread and waits up to `BOT_SHUTDOWN_TIMEOUT_S` for clean exit; logs INFO with strategy name and stop reason.

- **AC4:** Given a `.py` file in `strategies/active/` that is updated in-place (same filename, new content), when the file watcher detects the modification (via mtime change), then the hot-reload sequence runs strictly: (1) send stop signal to existing thread and wait up to `BOT_SHUTDOWN_TIMEOUT_S`, (2) call `importlib.invalidate_caches()` and remove the module from `sys.modules`, (3) import the new module, (4) run `run_startup_reconciliation` for the new instance, (5) spawn a new thread; if the existing thread does not stop within the timeout, force-stop via `thread._stop()` (last resort) and log CRITICAL before proceeding.

- **AC5:** Given the service starts up, when the file watcher runs its initial scan, then: (1) all valid strategy files are imported, reconciled, and their StrategyHandles registered with BusManager BEFORE `BusManager.start()` is called; no strategy event loop receives events before reconciliation completes; (2) after initial load, BusManager starts; (3) the file watcher enters a periodic polling loop (every `BOT_FILEWATCHER_INTERVAL_S`) for hot-reload and removal.

- **AC6:** Given each strategy file import, when imported via `importlib`, then it is wrapped in its own try/except; an error in one file does NOT prevent other files from loading in the same scan cycle.

- **AC7:** Given a strategy thread is started, when the strategy's event loop runs, then: (1) it calls `strategy.run_subscribe(timeout_s)` first, (2) sets `ready_event` once subscribe completes so BusManager starts routing, (3) processes `BarClose` events via `strategy.on_bar()` and `GapMarker` events via `strategy.handle_gap()`, (4) calls `strategy.ack_heartbeat()` on each event processed, and (5) exits cleanly when the stop signal fires.

## Tasks / Subtasks

- [x] T1: Add `bot_strategies_dir` to Settings and `bot_strategy_load_failure_total` metric
  - [x] T1.1: Add `bot_strategies_dir: str = "strategies/active"` to `Settings` in `config.py`
  - [x] T1.2: Add `bot_strategy_load_failure_total{filename, reason}` Counter to `metrics/prometheus.py` with function `inc_strategy_load_failure(filename: str, reason: str) -> None`
  - [x] T1.3: Write L1 test for `inc_strategy_load_failure`

- [x] T2: Add dynamic registration to BusManager
  - [x] T2.1: Add `_handles_lock: threading.RLock` to `BusManager.__init__`
  - [x] T2.2: Add `dynamic_register(handle: StrategyHandle) -> None` — acquires lock, inserts into `_handles`; can be called after `start()`
  - [x] T2.3: Add `dynamic_deregister(name: str) -> None` — acquires lock, removes from `_handles`
  - [x] T2.4: Update `_route` to acquire lock around `list(self._handles.values())` snapshot
  - [x] T2.5: Write L1 tests: dynamic_register after start routes events to new handle; dynamic_deregister after start stops routing

- [x] T3: Implement `FileWatcher` in `bot_service/strategy/registry.py`
  - [x] T3.1: Implement `_import_strategy_class(path: Path) -> type[BaseStrategy] | None` — loads module via `importlib.util.spec_from_file_location`, finds unique BaseStrategy subclass, returns None + logs ERROR on any exception
  - [x] T3.2: Implement `_scan_directory(strategies_dir: Path) -> dict[str, tuple[Path, type[BaseStrategy]]]` — returns `{class_name: (path, cls)}` for all valid, unique files; calls `inc_strategy_load_failure` twice on duplicate class names (once per file)
  - [x] T3.3: Implement `_create_handle_and_thread(strategy: BaseStrategy, stop_event: threading.Event) -> StrategyHandle` — creates event loop, queue, thread; the thread runs the strategy event loop (see T4)
  - [x] T3.4: Implement `FileWatcher` class with `__init__(bus_manager, exchange_client, settings, ...)` and `async run()` coroutine
  - [x] T3.5: `FileWatcher.run()`: initial scan → reconcile all → register all with BusManager (pre-start) → return stream_keys to caller; then loop every `BOT_FILEWATCHER_INTERVAL_S` for changes
  - [x] T3.6: Implement `_load_new(path, cls)` — reconcile → create handle → `bus_manager.dynamic_register()`
  - [x] T3.7: Implement `_unload(class_name, reason)` — send stop, wait, `bus_manager.dynamic_deregister()`
  - [x] T3.8: Implement `_hot_reload(class_name, path, new_cls)` — unload old → invalidate_caches + sys.modules cleanup → load new
  - [x] T3.9: Write L1 tests (see Test Coverage section)

- [x] T4: Implement strategy event loop runner
  - [x] T4.1: Implement `async run_strategy_event_loop(strategy, queue, stop_event, ready_event)` in `registry.py` — calls `strategy.run_subscribe()`, sets ready_event, processes events from queue, calls `ack_heartbeat()` on each event
  - [x] T4.2: Write L1 test: event loop processes BarClose → on_bar called; GapMarker → handle_gap called; stop_event → exits

- [x] T5: Wire FileWatcher into `main.py` lifespan
  - [x] T5.1: Instantiate `FileWatcher` in lifespan step 4b
  - [x] T5.2: Call `stream_keys = await file_watcher.initial_scan()` — registers all strategies with `_bus_manager` and returns needed stream keys
  - [x] T5.3: Call `_bus_manager.add_stream(key)` for each key BEFORE `_bus_manager.start()`
  - [x] T5.4: After `_bus_manager.start()`, launch `asyncio.create_task(file_watcher.run_loop())` for periodic scanning
  - [x] T5.5: In teardown (after yield): cancel the file_watcher task; stop all loaded strategy threads

- [x] T6: L2 hot-reload integration test
  - [x] T6.1: Write a minimal valid strategy file to a temp `strategies/active/` dir; run initial scan; assert StrategyHandle created
  - [x] T6.2: Update strategy file in-place (new class logic); trigger rescan; assert old thread stopped, new handle registered

- [x] T7: All tests pass — mypy --strict clean

### Review Findings

- [x] [Review][Patch] sys.modules leaks partially-initialized module on exec_module failure [registry.py:_import_strategy_class]
- [x] [Review][Patch] thread._stop() removed in Python 3.9+ — AttributeError on Python 3.12 [registry.py:_stop_thread]
- [x] [Review][Patch] Post-start strategy stream keys silently dropped — unregistered keys not warned [registry.py:_load_new]
- [x] [Review][Patch] run_subscribe exception leaves ready_event unset with no error log [registry.py:run_strategy_event_loop]
- [x] [Review][Patch] strategy._loop never set — _schedule_history_retry silently no-ops [registry.py:_create_handle_and_thread]
- [x] [Review][Defer] register() race with _handles_lock — false positive; pre-start only [event_bus.py:register] — deferred, pre-existing
- [x] [Review][Defer] AC5 ready_event wait — false positive; BusManager._run() already waits [event_bus.py:_run] — deferred, pre-existing
- [x] [Review][Defer] AC4 sys.modules in hot_reload — false positive; _import_strategy_class pops at entry [registry.py:_hot_reload] — deferred, pre-existing

## Dev Notes

### What already exists (do not reinvent)

- `bot_service/strategy/registry.py`: stub file exists with comment "Stub — populated in Story 13.2". THIS is where FileWatcher lives.
- `bot_service/bus/event_bus.py`: `BusManager`, `StrategyHandle` — read the full file before touching. `register()` and `add_stream()` raise `RuntimeError` after `start()`. You must add `dynamic_register`/`dynamic_deregister` for hot-reload (T2).
- `bot_service/strategy/base.py`: `BaseStrategy` — has `run_subscribe(timeout_s)`, `start_heartbeat()`, `ack_heartbeat()`, `on_bar()`, `handle_gap()`. The strategy event loop (T4.1) must call these in the correct order.
- `bot_service/strategy/reconciliation.py`: `run_startup_reconciliation(strategy, order_worker, exchange_client, exchange, questdb_http_addr, questdb_ilp_addr, timeout_s)` — call this per-strategy after import, before registering with BusManager.
- `bot_service/strategy/order_worker.py`: `OrderQueueWorker` — must be instantiated per strategy (each strategy has its own worker).
- `Settings` in `config.py`: `bot_filewatcher_interval_s: int = 60`, `bot_shutdown_timeout_s: int = 30`, `bot_reconciliation_timeout_s: int = 120`, `bot_subscribe_timeout_s: int = 30`. Do NOT recreate these — they exist.

### Creating a StrategyHandle (from test_bus_manager.py pattern)

The exact pattern used by existing tests — copy it for `_create_handle_and_thread`:

```python
loop = asyncio.new_event_loop()
queue: asyncio.Queue[BusEvent] = asyncio.Queue(maxsize=settings.bot_queue_max_depth)
stop_event = threading.Event()
ready = threading.Event()

def _run_loop() -> None:
    asyncio.set_event_loop(loop)
    loop.run_until_complete(
        run_strategy_event_loop(strategy, queue, stop_event, ready)
    )

t = threading.Thread(target=_run_loop, daemon=True, name=f"strategy-{strategy._name}")
t.start()

handle = StrategyHandle(
    name=strategy._name,
    thread=t,
    loop=loop,
    queue=queue,
    ready_event=ready,
)
```

### Strategy event loop (T4.1)

The strategy event loop body — implement in `registry.py`:

```python
async def run_strategy_event_loop(
    strategy: BaseStrategy,
    queue: asyncio.Queue[BusEvent],
    stop_event: threading.Event,
    ready_event: threading.Event,
) -> None:
    strategy.run_subscribe(timeout_s=strategy._settings.bot_subscribe_timeout_s)
    strategy.start_heartbeat()
    ready_event.set()
    while not stop_event.is_set():
        try:
            event = await asyncio.wait_for(queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            strategy.ack_heartbeat()
            continue
        if isinstance(event, BarClose):
            strategy.on_bar(event)
        elif isinstance(event, GapMarker):
            strategy.handle_gap(event)
        strategy.ack_heartbeat()
```

### Importing a strategy file

Use `importlib.util.spec_from_file_location` (not `importlib.import_module`) since files are on the filesystem, not in a package:

```python
import importlib.util
import sys

def _import_strategy_class(path: Path) -> type[BaseStrategy] | None:
    module_name = f"_strategy_{path.stem}"
    # Remove stale module from a previous load (hot-reload safety)
    sys.modules.pop(module_name, None)
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot create spec for {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        # Find BaseStrategy subclasses defined in this module (not imported ones)
        candidates = [
            cls for cls in vars(module).values()
            if isinstance(cls, type)
            and issubclass(cls, BaseStrategy)
            and cls is not BaseStrategy
            and cls.__module__ == module_name
        ]
        if len(candidates) == 0:
            log.error("strategy_file_no_class", filename=str(path))
            return None
        if len(candidates) > 1:
            log.error("strategy_file_multiple_classes", filename=str(path), count=len(candidates))
            return None
        return candidates[0]
    except Exception as exc:
        log.error("strategy_file_import_error", filename=str(path), error=str(exc))
        inc_strategy_load_failure(path.name, type(exc).__name__)
        return None
```

### Duplicate class name detection (AC2)

Uniqueness is checked ACROSS ALL files in each scan cycle. The check must happen AFTER importing all files, not per-file:

```python
# After importing all: find duplicates
class_counts: dict[str, int] = {}
for class_name in candidates_by_file.values():
    class_counts[class_name] = class_counts.get(class_name, 0) + 1
duplicates = {name for name, count in class_counts.items() if count > 1}
# Reject ALL files that have a duplicate class name
for path, class_name in candidates_by_file.items():
    if class_name in duplicates:
        inc_strategy_load_failure(path.name, "duplicate_class_name")
        log.error("strategy_class_name_duplicate", filename=str(path), class_name=class_name)
```

### Hot-reload: sys.modules cleanup

When hot-reloading, the old module must be purged:

```python
importlib.invalidate_caches()
module_name = f"_strategy_{path.stem}"
sys.modules.pop(module_name, None)
```

Then reimport using `_import_strategy_class(path)`.

### Stream key registration

BusManager needs stream keys registered BEFORE `start()`. For each strategy's subscribed symbols, the stream keys follow the pattern from CLAUDE.md:
- `candles:close:{exchange}:{symbol}:{tf}` for bar closes
- `candles:ob:{exchange}:{symbol}` for order-book features

After `subscribe()` is called, `strategy._bar_handlers.keys()` gives `(symbol, tf)` tuples. Build stream keys from these plus any `strategy._managed_positions` symbols (added by reconciliation):

```python
stream_keys: set[str] = set()
for (symbol, tf) in strategy._bar_handlers:
    stream_keys.add(f"candles:close:{exchange}:{symbol}:{tf}")
    stream_keys.add(f"candles:ob:{exchange}:{symbol}")
for symbol in strategy._managed_positions:
    stream_keys.add(f"candles:close:{exchange}:{symbol}:1s")
    stream_keys.add(f"candles:ob:{exchange}:{symbol}")
```

### Thread stop protocol (AC3, AC4)

```python
def _stop_thread(handle: StrategyHandle, stop_event: threading.Event, timeout_s: float) -> None:
    stop_event.set()
    handle.thread.join(timeout=timeout_s)
    if handle.thread.is_alive():
        log.critical("strategy_thread_force_stop", strategy=handle.name)
        handle.thread._stop()  # type: ignore[attr-defined]  # last resort
```

`thread._stop()` is a CPython internal — always log CRITICAL before using it.

### BusManager dynamic_register (T2)

Add `threading.RLock` to `__init__` (not `threading.Lock` — RLock allows the same thread to acquire it multiple times, safe for `_route`). Update `_route` to snapshot handles under the lock:

```python
def _route(self, stream_key: str, entry: dict[str, str]) -> None:
    event = parse_stream_entry(stream_key, entry)
    if event is None:
        return
    with self._handles_lock:
        handles = list(self._handles.values())
    for handle in handles:
        self._deliver(handle, event)
```

`dynamic_register` and `dynamic_deregister` acquire the same lock. This is safe because `_deliver` does not hold the lock.

### OrderQueueWorker instantiation

Each strategy needs its own `OrderQueueWorker`. Check `order_worker.py` for its `__init__` signature before implementing — do NOT assume the signature, read the file first.

### mypy --strict discipline

- `thread._stop()` requires `# type: ignore[attr-defined]`
- `spec.loader.exec_module(module)` requires `# type: ignore[union-attr]`
- All new functions must have complete type annotations
- `BusEvent` from `bot_service.bus.event_types` is the union type for queue events

### Metrics

`bot_strategy_load_failure_total{filename, reason}` — `reason` values: `ImportError`, `SyntaxError`, `duplicate_class_name`, `no_class_found`, `multiple_classes`. Always use the exception type name as `reason` for import errors.

## Test Coverage

### L1 unit tests

1. `test_inc_strategy_load_failure_increments` — smoke test for new metric
2. `test_bus_manager_dynamic_register_routes_after_start` — start BusManager, dynamic_register a new handle, send event, assert delivered
3. `test_bus_manager_dynamic_deregister_stops_routing` — register, start, deregister, send event, assert NOT delivered
4. `test_import_strategy_class_valid` — write a valid strategy .py to tmp dir, assert class returned
5. `test_import_strategy_class_syntax_error` — write invalid syntax .py, assert None returned + ERROR logged
6. `test_import_strategy_class_no_base_class` — write .py with plain class (not BaseStrategy subclass), assert None
7. `test_scan_directory_duplicate_class_rejected` — two files with same class name → empty result, counter incremented twice
8. `test_scan_directory_one_valid_one_invalid` — one valid, one syntax-error → only valid class in result (AC6)
9. `test_event_loop_processes_bar_close` — mock strategy, run event loop with BarClose event, assert on_bar called
10. `test_event_loop_exits_on_stop_event` — start loop, set stop_event, assert exits within 2s

### L2 integration test

11. `test_hot_reload_stops_old_thread_starts_new` — write strategy V1 to tmp dir, initial_scan → handle created; overwrite with V2, trigger rescan → old thread joins, new handle registered with updated class

## Senior Developer Review (AI)

**Date:** (to be filled after review)
**Outcome:** (to be filled)

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- T1: Added `bot_strategies_dir` and `bot_exchange` to Settings; added `inc_strategy_load_failure` Counter to prometheus.py using established lazy-init pattern.
- T2: Added `_handles_lock: threading.RLock` to BusManager; `dynamic_register`/`dynamic_deregister` acquire lock; `_route` snapshots handles under lock; `_run` also snapshots initial handles under lock for thread safety.
- T3/T4: Implemented complete `registry.py` — `run_strategy_event_loop`, `_import_strategy_class`, `_scan_directory`, `_create_handle_and_thread`, `_stop_thread`, `FileWatcher` class with `initial_scan`, `run_loop`, `_rescan`, `_load_new`, `_unload`, `_hot_reload`, `stop_all`.
- T5: Rewrote main.py lifespan to create FileWatcher, call `initial_scan()`, add stream keys, start BusManager, then launch `run_loop()` task. Teardown cancels watcher task and calls `stop_all()`. Exchange client selection from `bot_exchange` setting.
- T6: L2 hot-reload test uses tmp_path, mocked reconciliation, real strategy file write + overwrite + touch to trigger mtime change. All 11 tests pass (145 total).
- T7: 145 tests pass, mypy --strict clean (27 source files).

### File List

- bot_service/config.py (UPDATE — add `bot_strategies_dir`, `bot_exchange`)
- bot_service/metrics/prometheus.py (UPDATE — add `bot_strategy_load_failure_total`, `inc_strategy_load_failure`)
- bot_service/bus/event_bus.py (UPDATE — add `_handles_lock`, `dynamic_register`, `dynamic_deregister`; lock in `_route` and `_run`)
- bot_service/strategy/registry.py (REWRITE — `FileWatcher`, `run_strategy_event_loop`, `_import_strategy_class`, `_scan_directory`, `_create_handle_and_thread`, `_stop_thread`)
- bot_service/main.py (UPDATE — wire FileWatcher into lifespan steps 4b/4c/4d/4e + teardown)
- tests/test_file_watcher.py (CREATE — 10 L1 + 1 L2 tests)
