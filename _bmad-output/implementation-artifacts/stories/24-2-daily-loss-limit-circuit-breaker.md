---
id: 24-2
title: Daily loss limit circuit breaker
epic: 24
status: ready-for-dev
---

# Story 24-2: Daily loss limit circuit breaker

## Context

No automated protection if a strategy loses more than a configured daily amount. Need a service-level circuit breaker that stops all strategies when cumulative daily realized losses exceed a threshold.

**Note:** `realized_pnl` in `order_events` is always 0.0 until Epic 25. The circuit breaker should be wired now and become effective once Epic 25 lands. For now it tracks `realized_pnl` as reported (0.0), which means it fires only when the logic is proven correct.

## What to build

### `bot_service/config.py`

Add `daily_loss_limit_usd: float = 0.0` to `Settings`. When `0.0`, the circuit breaker is disabled.

### `bot_service/strategy/circuit_breaker.py` (new file)

```python
class DailyLossCircuitBreaker:
    def __init__(self, limit_usd: float) -> None: ...
    def record_pnl(self, realized_pnl: float) -> None: ...
    def is_tripped(self) -> bool: ...
    def reset_for_new_day(self, today: date) -> None: ...  # idempotent, date-keyed
```

- `record_pnl` adds `realized_pnl` to daily accumulator.
- `is_tripped` returns True if `daily_pnl < -abs(limit_usd)` and `limit_usd != 0`.
- `reset_for_new_day` resets accumulator when called with a date != the current tracking date (UTC).

### `bot_service/strategy/order_worker.py`

Inject `DailyLossCircuitBreaker` (optional, defaults to disabled). In `handle_fill`, after writing the order event:
```python
if self._circuit_breaker:
    self._circuit_breaker.record_pnl(realized_pnl)
    if self._circuit_breaker.is_tripped():
        log.critical("daily_loss_limit_tripped", limit=..., daily_pnl=...)
        # signal stop via injected callback
```

Add `on_circuit_breaker_trip: Callable[[], None] | None` injected parameter. The registry wires this to `file_watcher.stop_all()`.

### `bot_service/main.py`

Wire the circuit breaker callback in lifespan: `order_worker_factory = lambda ...: OrderQueueWorker(..., on_circuit_breaker_trip=_file_watcher.stop_all)`.

### `GET /health`

Add `circuit_breaker_tripped: bool` field.

## Acceptance Criteria

- With `DAILY_LOSS_LIMIT_USD=100`, after recording -101 in PnL, `is_tripped()` returns True.
- Trip fires `on_circuit_breaker_trip` callback exactly once.
- `reset_for_new_day` with next UTC date resets the accumulator and un-trips.
- `DAILY_LOSS_LIMIT_USD=0` keeps breaker permanently disabled regardless of PnL.
- Unit tests: trip threshold, no-trip below threshold, day reset, disabled mode.

## Files
- `bot-service/bot_service/config.py`
- `bot-service/bot_service/strategy/circuit_breaker.py` (new)
- `bot-service/bot_service/strategy/order_worker.py`
- `bot-service/bot_service/main.py`
- `bot-service/tests/test_circuit_breaker.py` (new)
