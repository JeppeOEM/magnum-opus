---
id: 26-1
title: pubsub reconnect loop and stop unblocking
epic: 26
status: ready-for-dev
---

# Story 26-1: pubsub reconnect loop and stop unblocking

## Context

**D-17-4-1** — `_run_pubsub` exits permanently on any Redis error. A single network blip kills pub/sub forever until the process restarts.

**D-17-4-4** — `ps.listen()` blocks indefinitely. `stop()` sets `_stop_event` but the thread remains blocked until the next message arrives. `join(timeout=5s)` silently times out in quiet periods.

## What to build

### `bot_service/bus/event_bus.py` — `_run_pubsub`

Replace the current single-attempt with a reconnect loop matching `_consume_loop`'s exponential backoff pattern:

```python
def _run_pubsub(self) -> None:
    backoff = 1.0
    while not self._stop_event.is_set():
        r = redis.from_url(get_settings().redis_url)
        ps = r.pubsub()
        try:
            ps.psubscribe("orderbook:*", "candles1s:*")
            for raw_msg in ps.listen():
                if self._stop_event.is_set():
                    return
                if raw_msg["type"] != "pmessage":
                    continue
                self._dispatch_pubsub(...)
            backoff = 1.0
        except Exception as exc:
            if self._stop_event.is_set():
                return
            log.error("pubsub_thread_error", error=str(exc))
            self._stop_event.wait(timeout=min(backoff, 30.0))
            backoff = min(backoff * 2, 30.0)
        finally:
            try:
                ps.unsubscribe()  # unblocks listen() iterator
                ps.close()
            except Exception:
                pass
            r.close()
```

### `bot_service/bus/event_bus.py` — `stop`

After setting `_stop_event`, call the pubsub unsubscribe to unblock the listener:

```python
def stop(self) -> None:
    self._stop_event.set()
    # Wake the pubsub listener so it exits promptly
    try:
        r = redis.from_url(get_settings().redis_url)
        ps = r.pubsub()
        ps.punsubscribe("orderbook:*", "candles1s:*")
        ps.close()
        r.close()
    except Exception:
        pass
```

## Acceptance Criteria

- A Redis exception in `_run_pubsub` causes a reconnect after backoff, not a permanent exit.
- After `stop()`, the pubsub thread exits within 2 seconds even with no messages arriving.
- Backoff starts at 1s, doubles per failure, caps at 30s.
- Unit tests: reconnect after error (mock Redis raising on first call, succeeding on second), stop exits thread promptly.

## Files
- `bot-service/bot_service/bus/event_bus.py`
- `bot-service/tests/test_event_bus.py` (create or extend)
