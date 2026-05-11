# Story 17.4: Bot Service Orderbook Pub/Sub Subscription

Status: done

## Story

As mrqdt,
I want bot strategies to declare their orderbook subscription mode in config and have the bus manager provision the corresponding Redis pub/sub subscription,
so that microstructure strategies can react to tick-level book updates without polling, while signal strategies continue using 1s candle data.

## Acceptance Criteria

### AC 1 — `orderbook_mode` property on `BaseStrategy`

1. `BaseStrategy` defines a non-abstract property `orderbook_mode` returning `"none"` by default (overridable by subclasses).
2. Valid values: `"none"`, `"snapshot_1s"`, `"full_stream"`, `"both"`.
3. `BaseStrategy.__init__` validates `self.orderbook_mode` immediately on construction; raises `ValueError` if the value is not in the valid set.

### AC 2 — No-op callbacks on `BaseStrategy`

4. `BaseStrategy` defines `_on_orderbook(self, payload: dict[str, Any]) -> None` — no-op; overridden by microstructure strategies.
5. `BaseStrategy` defines `_on_candles1s(self, payload: dict[str, Any]) -> None` — no-op; overridden by strategies that need 1s pub/sub features.

### AC 3 — Bus manager provisions pub/sub for `full_stream` / `both`

6. When a strategy with `orderbook_mode = "full_stream"` or `"both"` is loaded, `BusManager.provision_pubsub` registers its `_on_orderbook` callback.
7. For each Redis pub/sub message on channel `orderbook:{exchange}:{symbol}`, the bus manager JSON-decodes the payload and calls each registered `_on_orderbook(payload: dict)` callback directly.

### AC 4 — Bus manager provisions pub/sub for `snapshot_1s` / `both`

8. When a strategy with `orderbook_mode = "snapshot_1s"` or `"both"` is loaded, `BusManager.provision_pubsub` registers its `_on_candles1s` callback.
9. For each Redis pub/sub message on channel `candles1s:{exchange}:{symbol}`, the bus manager JSON-decodes the payload and calls each registered `_on_candles1s(payload: dict)` callback directly.

### AC 5 — No subscription when `orderbook_mode = "none"`

10. Strategies with `orderbook_mode = "none"` have no pub/sub callbacks registered; existing Redis stream bar-handler behaviour is unchanged.

### AC 6 — JSON parse error handling

11. If a pub/sub message payload is malformed JSON, the bus manager logs at ERROR with the raw channel name and raw data; the strategy thread is unaffected; `bot_nan_guard_total` is NOT incremented.

### AC 7 — Existing strategies declare `orderbook_mode = "none"`

12. `OFIBot`, `MACrossBot`, and `RSIBot` each declare `@property def orderbook_mode(self) -> str: return "none"`.

### AC 8 — Test coverage (L1)

13. `tests/test_ob_pubsub.py` with `@pytest.mark.l1`:
    - `provision_pubsub` registers `_on_orderbook` callback when mode is `"full_stream"`.
    - No ob callback registered when mode is `"none"`.
    - `BaseStrategy.__init__` raises `ValueError` for an invalid mode string.
    - `BusManager._dispatch_pubsub` logs ERROR on malformed JSON and does not raise.

## Tasks / Subtasks

- [ ] Task 1: Extend `BaseStrategy` (AC 1, 2)
  - [ ] Add `_VALID_OB_MODES: frozenset[str]` class constant
  - [ ] Add `orderbook_mode` property returning `"none"`
  - [ ] Validate `self.orderbook_mode` in `__init__` — `ValueError` on invalid value
  - [ ] Add `_on_orderbook(self, payload: dict[str, Any]) -> None` no-op
  - [ ] Add `_on_candles1s(self, payload: dict[str, Any]) -> None` no-op

- [ ] Task 2: Extend `BusManager` (AC 3, 4, 5, 6)
  - [ ] Add `_pubsub_ob_callbacks: dict[str, Callable[[dict[str, Any]], None]]`
  - [ ] Add `_pubsub_c1s_callbacks: dict[str, Callable[[dict[str, Any]], None]]`
  - [ ] Add `_pubsub_lock: threading.RLock`
  - [ ] Add `_pubsub_thread: threading.Thread | None`
  - [ ] Add `provision_pubsub(name, mode, ob_cb, c1s_cb)` method
  - [ ] Add `deprovision_pubsub(name)` method
  - [ ] Modify `start()` to spawn `_pubsub_thread` (daemon, name `"bus-pubsub"`)
  - [ ] Modify `stop()` to join `_pubsub_thread` (up to 5 s)
  - [ ] Add `_run_pubsub()` — Redis PSUBSCRIBE loop with JSON parse error handling
  - [ ] Add `_dispatch_pubsub(channel, raw_data)` — JSON decode + route to callbacks

- [ ] Task 3: Wire `FileWatcher._load_new` (AC 3, 4, 5)
  - [ ] After `_create_handle_and_thread`, read `strategy.orderbook_mode`
  - [ ] Call `_bus_manager.provision_pubsub(class_name, mode, strategy._on_orderbook, strategy._on_candles1s)`
  - [ ] (only if mode != "none")

- [ ] Task 4: Wire `FileWatcher._unload` (AC 3, 4)
  - [ ] Call `_bus_manager.deprovision_pubsub(class_name)` before `dynamic_deregister`

- [ ] Task 5: Update existing strategies (AC 7)
  - [ ] `strategies/active/ofi_bot.py` — add `orderbook_mode = "none"` property
  - [ ] `strategies/active/ma_cross_bot.py` — add `orderbook_mode = "none"` property
  - [ ] `strategies/active/rsi_bot.py` — add `orderbook_mode = "none"` property

- [ ] Task 6: L1 tests (AC 8)
  - [ ] `tests/test_ob_pubsub.py` with 4 test functions

## Dev Notes

### Files to touch

| File | Change |
|---|---|
| `bot_service/strategy/base.py` | NEW property + validation + 2 no-op methods |
| `bot_service/bus/event_bus.py` | NEW pub/sub state + 4 methods + modify start/stop |
| `bot_service/strategy/registry.py` | Wire provision/deprovision in `_load_new` / `_unload` |
| `strategies/active/ofi_bot.py` | Add `orderbook_mode` property |
| `strategies/active/ma_cross_bot.py` | Add `orderbook_mode` property |
| `strategies/active/rsi_bot.py` | Add `orderbook_mode` property |
| `tests/test_ob_pubsub.py` | NEW L1 test file |

### `BaseStrategy` changes — exact additions

Add to the class body (before `__init__`):
```python
_VALID_OB_MODES: frozenset[str] = frozenset({"none", "snapshot_1s", "full_stream", "both"})

@property
def orderbook_mode(self) -> str:
    return "none"

def _on_orderbook(self, payload: dict[str, Any]) -> None:
    """Called from the BusManager pub/sub thread for each orderbook message.

    Override in microstructure strategies. Must be thread-safe.
    """

def _on_candles1s(self, payload: dict[str, Any]) -> None:
    """Called from the BusManager pub/sub thread for each candles1s message.

    Override in strategies that need 1s pub/sub features. Must be thread-safe.
    """
```

Add to `__init__` (after all existing attribute assignments):
```python
mode = self.orderbook_mode
if mode not in self._VALID_OB_MODES:
    raise ValueError(
        f"Invalid orderbook_mode {mode!r}; must be one of {sorted(self._VALID_OB_MODES)}"
    )
```

Add `from typing import Any` if not already imported. `Any` is used here at the boundary where pub/sub payload is a raw `dict` from `json.loads` — justified.

### `BusManager` changes — new state and methods

Add to `__init__`:
```python
self._pubsub_ob_callbacks: dict[str, Callable[[dict[str, Any]], None]] = {}
self._pubsub_c1s_callbacks: dict[str, Callable[[dict[str, Any]], None]] = {}
self._pubsub_lock: threading.RLock = threading.RLock()
self._pubsub_thread: threading.Thread | None = None
```

New methods:
```python
def provision_pubsub(
    self,
    name: str,
    mode: str,
    ob_callback: Callable[[dict[str, Any]], None],
    candles1s_callback: Callable[[dict[str, Any]], None],
) -> None:
    """Register pub/sub callbacks for a strategy. Thread-safe."""
    with self._pubsub_lock:
        if mode in ("full_stream", "both"):
            self._pubsub_ob_callbacks[name] = ob_callback
        if mode in ("snapshot_1s", "both"):
            self._pubsub_c1s_callbacks[name] = candles1s_callback

def deprovision_pubsub(self, name: str) -> None:
    """Remove pub/sub callbacks for a strategy. Thread-safe. No-op if not registered."""
    with self._pubsub_lock:
        self._pubsub_ob_callbacks.pop(name, None)
        self._pubsub_c1s_callbacks.pop(name, None)
```

Modify `start()` — add after spawning `_thread`:
```python
self._pubsub_thread = threading.Thread(
    target=self._run_pubsub, daemon=True, name="bus-pubsub"
)
self._pubsub_thread.start()
```

Modify `stop()` — add after joining `_thread`:
```python
if self._pubsub_thread is not None:
    self._pubsub_thread.join(timeout=5.0)
```

New methods:
```python
def _run_pubsub(self) -> None:
    """Subscribe to orderbook:* and candles1s:* Redis pub/sub patterns and dispatch."""
    settings = get_settings()
    r: redis.Redis[bytes] = redis.from_url(settings.redis_url)
    ps = r.pubsub()
    try:
        ps.psubscribe("orderbook:*", "candles1s:*")
        for raw_msg in ps.listen():
            if self._stop_event.is_set():
                break
            if raw_msg["type"] != "pmessage":
                continue
            channel_raw = raw_msg["channel"]
            channel: str = channel_raw.decode() if isinstance(channel_raw, bytes) else channel_raw
            self._dispatch_pubsub(channel, raw_msg["data"])
    finally:
        ps.close()

def _dispatch_pubsub(self, channel: str, raw: bytes | str) -> None:
    """JSON-decode raw pub/sub data and route to registered callbacks."""
    raw_str: str = raw.decode() if isinstance(raw, bytes) else raw
    try:
        payload: dict[str, Any] = json.loads(raw_str)
    except json.JSONDecodeError as exc:
        log.error(
            "pubsub_json_parse_error",
            channel=channel,
            raw=raw_str[:200],
            error=str(exc),
        )
        return
    if channel.startswith("orderbook:"):
        with self._pubsub_lock:
            callbacks = list(self._pubsub_ob_callbacks.values())
        for cb in callbacks:
            cb(payload)
    elif channel.startswith("candles1s:"):
        with self._pubsub_lock:
            callbacks = list(self._pubsub_c1s_callbacks.values())
        for cb in callbacks:
            cb(payload)
```

Add `import json` to `event_bus.py` imports. Add `from typing import Any` if not present.

**Required import additions in `event_bus.py`:**
```python
import json
from collections.abc import Callable
from typing import Any
```

### `FileWatcher._load_new` change

After `_create_handle_and_thread(...)` and BEFORE `stream_keys` computation, add:
```python
mode = strategy.orderbook_mode
if mode != "none":
    if pre_start:
        self._bus_manager.provision_pubsub(
            class_name, mode, strategy._on_orderbook, strategy._on_candles1s
        )
    else:
        self._bus_manager.provision_pubsub(
            class_name, mode, strategy._on_orderbook, strategy._on_candles1s
        )
```

Since both branches are identical, simplify to:
```python
mode = strategy.orderbook_mode
if mode != "none":
    self._bus_manager.provision_pubsub(
        class_name, mode, strategy._on_orderbook, strategy._on_candles1s
    )
```

### `FileWatcher._unload` change

After `self._loaded.pop(class_name)` and before `_stop_thread`:
```python
self._bus_manager.deprovision_pubsub(class_name)
```

Do this BEFORE `dynamic_deregister` to maintain order consistency.

### Thread-safety note for `_on_orderbook` / `_on_candles1s`

These methods are called from the `bus-pubsub` thread, NOT the strategy's asyncio event loop thread. The base no-op implementations are safe. Subclasses that override and need to modify strategy state must either:
- Use only thread-safe data structures (e.g., `threading.Lock`-protected dicts)
- Or schedule via `self._loop.call_soon_threadsafe(self._do_work, payload)` to deliver on the strategy's event loop

### Existing strategy property pattern

All three existing strategies use the same pattern:
```python
@property
def orderbook_mode(self) -> str:
    return "none"
```

Add this immediately after `close_on_bus_timeout` in each strategy file. Return type annotation is required (`-> str`).

### L1 test structure (`tests/test_ob_pubsub.py`)

```python
"""L1 tests for orderbook pub/sub provisioning."""
from __future__ import annotations
import json
import threading
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock, patch
import pytest
from bot_service.bus.event_bus import BusManager
from bot_service.strategy.base import BaseStrategy

# ---- helpers ----

def _noop_ob(payload: dict[str, Any]) -> None: ...
def _noop_c1s(payload: dict[str, Any]) -> None: ...


@pytest.mark.l1
def test_provision_pubsub_full_stream_registers_ob_callback() -> None:
    mgr = BusManager()
    mgr.provision_pubsub("s1", "full_stream", _noop_ob, _noop_c1s)
    assert "s1" in mgr._pubsub_ob_callbacks
    assert "s1" not in mgr._pubsub_c1s_callbacks


@pytest.mark.l1
def test_provision_pubsub_none_registers_no_callbacks() -> None:
    mgr = BusManager()
    # FileWatcher only calls provision_pubsub for non-"none" modes —
    # verify no callbacks are present after a fresh BusManager with no provisions
    assert "s1" not in mgr._pubsub_ob_callbacks
    assert "s1" not in mgr._pubsub_c1s_callbacks


@pytest.mark.l1
def test_base_strategy_invalid_mode_raises() -> None:
    class BadStrategy(BaseStrategy):
        @property def min_lookback(self) -> int: return 1
        @property def max_position_pct(self) -> float: return 0.1
        @property def stop_loss_pct(self) -> float: return 0.05
        @property def paper_trading(self) -> bool: return True
        @property def bus_timeout_seconds(self) -> int: return 30
        @property def close_on_bus_timeout(self) -> bool: return False
        @property def orderbook_mode(self) -> str: return "invalid_mode"
        def subscribe(self) -> None: ...

    from bot_service.config import Settings
    with pytest.raises(ValueError, match="invalid_mode"):
        BadStrategy(name="bad", settings=Settings())


@pytest.mark.l1
def test_dispatch_pubsub_malformed_json_logs_error_does_not_raise(caplog: Any) -> None:
    import logging
    mgr = BusManager()
    received: list[dict[str, Any]] = []
    mgr.provision_pubsub("s1", "full_stream", received.append, _noop_c1s)

    with caplog.at_level(logging.ERROR):
        mgr._dispatch_pubsub("orderbook:kucoin:BTCUSDT", b"not json {{{")

    assert any("pubsub_json_parse_error" in r.message for r in caplog.records)
    assert received == []
```

### Import additions per file

**`bot_service/strategy/base.py`** — ensure `from typing import Any` is present (for `dict[str, Any]` annotations).

**`bot_service/bus/event_bus.py`** — add:
```python
import json
from collections.abc import Callable
from typing import Any
```

### `run_strategy_event_loop` — no change needed

The pub/sub callbacks are called on the `bus-pubsub` thread, not via the asyncio queue. `run_strategy_event_loop` in `registry.py` does not need modification.

### What NOT to implement

- Do NOT add pub/sub channels to `BusManager._stream_keys` — those are for XREADGROUP streams only
- Do NOT modify `StrategyHandle` — it remains a frozen dataclass with 5 fields
- Do NOT add new Prometheus metrics — this story adds no metrics
- Do NOT add new config fields — `redis_url` from `Settings` is sufficient
- Do NOT implement any actual microstructure strategy that uses `orderbook_mode != "none"` — that is for a future story
- Do NOT implement reconnect logic for `_run_pubsub` — this is a single-user local service; reconnect is a future hardening item

### Review Findings

- [x] [Review][Patch] Callback exceptions uncaught in `_dispatch_pubsub` — exception from any callback propagates out and kills the pub/sub thread permanently [bot_service/bus/event_bus.py]
- [x] [Review][Patch] `_run_pubsub` leaks Redis connection — `ps.close()` does not close the underlying `Redis` client; add `r.close()` in the finally block [bot_service/bus/event_bus.py]
- [x] [Review][Patch] `test_provision_pubsub_none_registers_no_callbacks` is a trivial assertion — test should call `provision_pubsub("s1", "none", ...)` and then assert no callbacks were registered [tests/test_ob_pubsub.py]
- [x] [Review][Defer] No reconnect in `_run_pubsub` — single Redis error kills pub/sub; explicitly out of scope per story spec [bot_service/bus/event_bus.py] — deferred
- [x] [Review][Defer] `_handle_crash` doesn't call `deprovision_pubsub` — callbacks leak on crash path; noted in dev notes; all current strategies are mode="none" [bot_service/strategy/registry.py] — deferred
- [x] [Review][Defer] `stop_all()` doesn't call `deprovision_pubsub` — same risk as _handle_crash; no current impact [bot_service/strategy/registry.py] — deferred
- [x] [Review][Defer] `ps.listen()` blocks indefinitely; stop needs to unsubscribe to unblock — daemon thread acceptable for single-user service; clean unsubscribe is a future hardening item [bot_service/bus/event_bus.py] — deferred
- [x] [Review][Defer] TOCTOU gap: deprovisioned strategy could receive one more callback after deprovision — snapshot-under-lock-iterate-outside is correct pattern; no impact with current no-op callbacks [bot_service/bus/event_bus.py] — deferred

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Completion Notes List

- All 6 tasks implemented; 264 tests pass (4 new L1 tests added).
- Added `except Exception` guard in `_run_pubsub` (no reconnect logic) to prevent unhandled thread exception warnings when Redis is unavailable in tests.
- `structlog.testing.capture_logs()` used for log assertion in test instead of `caplog` (no stdlib logging configured in test env).
- `_handle_crash` in `registry.py` does NOT call `deprovision_pubsub` (story scope only requires `_unload`; all existing strategies have mode="none" so no leak risk currently).

### File List

- `bot_service/strategy/base.py` — UPDATE
- `bot_service/bus/event_bus.py` — UPDATE
- `bot_service/strategy/registry.py` — UPDATE
- `strategies/active/ofi_bot.py` — UPDATE
- `strategies/active/ma_cross_bot.py` — UPDATE
- `strategies/active/rsi_bot.py` — UPDATE
- `tests/test_ob_pubsub.py` — NEW
