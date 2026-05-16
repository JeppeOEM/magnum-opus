---
id: 24-1
title: Emergency stop REST endpoint
epic: 24
status: ready-for-dev
---

# Story 24-1: Emergency stop REST endpoint

## Context

No way to halt all bots from outside the process. Operators need a single POST to stop all running strategies immediately without restarting the service.

## What to build

### `bot_service/main.py`

Add `POST /stop-all` endpoint:
```python
@app.post("/stop-all")
def stop_all() -> dict[str, str]:
    _file_watcher.stop_all()
    return {"status": "stopped"}
```

`_file_watcher` is already accessible in module scope (set during lifespan startup). The existing `stop_all()` on `FileWatcher` (registry.py) stops all threads and deregisters from the bus.

### `bot_service/strategy/registry.py` — `stop_all`

The existing `stop_all()` calls `dynamic_deregister` but not `deprovision_pubsub`. This leaks pub/sub callbacks (D-17-4-3). Fix in this story:
```python
def stop_all(self) -> None:
    for class_name in list(self._loaded.keys()):
        self._bus_manager.deprovision_pubsub(class_name)   # add this
        _, handle, stop_event, _ = self._loaded.pop(class_name)
        _stop_thread(handle, stop_event, float(self._settings.bot_shutdown_timeout_s))
        self._bus_manager.dynamic_deregister(class_name)
```

## Acceptance Criteria

- `POST /stop-all` returns `{"status": "stopped"}` with HTTP 200.
- After the call, `GET /health` still returns 200 (service stays alive).
- `get_strategy_statuses()` returns empty dict or all-stopped after call.
- `deprovision_pubsub` is called for each strategy before stopping the thread.
- Test: mock FileWatcher; assert `stop_all()` was called when endpoint hits.

## Files
- `bot-service/bot_service/main.py`
- `bot-service/bot_service/strategy/registry.py`
- `bot-service/tests/test_main.py` (create or extend)
