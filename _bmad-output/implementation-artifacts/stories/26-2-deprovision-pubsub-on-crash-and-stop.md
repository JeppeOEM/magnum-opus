---
id: 26-2
title: deprovision_pubsub on crash and stop_all
epic: 26
status: ready-for-dev
---

# Story 26-2: deprovision_pubsub on crash and stop_all

## Context

**D-17-4-2** — `_handle_crash` calls `dynamic_deregister` but not `deprovision_pubsub`. Pub/sub callbacks for the crashed strategy leak; the old instance can receive messages during the backoff window.

**D-17-4-3** — `stop_all` calls `dynamic_deregister` but not `deprovision_pubsub`. Same leak on service teardown.

Story 24-1 partially fixed stop_all; this story adds the crash path.

## What to build

### `bot_service/strategy/registry.py` — `_handle_crash`

Add `self._bus_manager.deprovision_pubsub(class_name)` before `dynamic_deregister`:

```python
async def _handle_crash(self, class_name: str) -> None:
    entry = self._loaded.pop(class_name, None)
    if entry is None:
        return
    ...
    self._bus_manager.deprovision_pubsub(class_name)   # ADD
    self._bus_manager.dynamic_deregister(class_name)
    ...
```

### Verify stop_all already fixed (from 24-1)

Confirm `stop_all` calls `deprovision_pubsub` before `dynamic_deregister` (24-1 added this). If not present, add it here.

## Acceptance Criteria

- After a strategy crash, `deprovision_pubsub(name)` is called before `dynamic_deregister(name)`.
- After `stop_all`, all pub/sub callbacks are cleared.
- Unit tests: mock bus_manager; assert deprovision_pubsub called in crash path and stop_all path.

## Files
- `bot-service/bot_service/strategy/registry.py`
- `bot-service/tests/test_registry.py` (create or extend)
