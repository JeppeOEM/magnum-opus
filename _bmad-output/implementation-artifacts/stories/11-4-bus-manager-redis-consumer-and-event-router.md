# Story 11.4: Bus Manager — Redis Consumer & Event Router

Status: done

## Story

As mrqdt,
I want a Bus Manager that reads from Redis XREADGROUP, routes events to per-strategy queues with drop-oldest overflow and synthetic GapMarker, and handles transient Redis errors with exponential backoff,
so that events are delivered reliably and queue overflow never silently drops data.

## Acceptance Criteria

1. `StrategyHandle` is `@dataclass(frozen=True)` with fields `name: str`, `thread: threading.Thread`, `loop: asyncio.AbstractEventLoop`, `queue: asyncio.Queue[BusEvent]`.
2. `BusManager.register(handle)` raises `RuntimeError` if called after `BusManager.start()`.
3. Bus Manager waits for each strategy's `threading.Event` readiness signal before routing events to it; strategies not ready within `BOT_SUBSCRIBE_TIMEOUT_S` are logged WARN and skipped.
4. On queue full: drops OLDEST item, increments `bot_queue_drop_total{strategy}`, logs WARN, then enqueues a synthetic `GapMarker(gap_cause="queue_overflow", ...)` (drop-oldest again if still full).
5. Transient Redis errors: exponential backoff starting 1s, doubling, capped at 60s; each retry logs WARN with attempt count and error.
6. Persistent failure (backoff cap hit 3× consecutively): logs CRITICAL then calls `os.kill(os.getpid(), signal.SIGTERM)`.
7. Event delivery uses `loop.run_coroutine_threadsafe(queue.put(event), loop)` — never blocks the Bus Manager thread on put.
8. Consumer group name from `Settings.bot_consumer_group`; consumer name is `{hostname}-{pid}`.
9. L2 test with real Redis: register 2 strategies, publish 10 events, assert all 10 delivered; flood queue to max depth, assert oldest dropped and GapMarker appended.

## Tasks / Subtasks

- [ ] Implement `StrategyHandle` dataclass in `bot_service/bus/event_bus.py` (AC: 1)
- [ ] Implement `BusManager` class (AC: 2–8)
  - [ ] `register(handle: StrategyHandle) -> None` — raises `RuntimeError` after start
  - [ ] `start() -> None` — spawns daemon thread, waits for strategy readiness events
  - [ ] `stop() -> None` — sets shutdown flag, joins thread
  - [ ] Main loop: `XREADGROUP` with block=1000ms, route via `parse_stream_entry`, deliver
  - [ ] Queue overflow: drop-oldest, increment counter, enqueue synthetic GapMarker
  - [ ] Exponential backoff on Redis errors with SIGTERM on persistent failure
- [ ] Write `bot_service/metrics/prometheus.py` with initial counters (AC: 4)
  - [ ] `bot_queue_drop_total` counter with `strategy` label
  - [ ] Use `prometheus_client.CollectorRegistry()` (never default registry)
  - [ ] `get_registry() -> CollectorRegistry` singleton
- [ ] Write L2 tests `tests/test_bus_manager.py` (AC: 9)

## Dev Notes

### StrategyHandle & BusManager Structure

```python
from __future__ import annotations

import asyncio
import os
import signal
import socket
import threading
import time
from dataclasses import dataclass

import redis.asyncio as aioredis
import structlog

from bot_service.bus.event_types import BusEvent, GapMarker, parse_stream_entry
from bot_service.config import get_settings
from bot_service.metrics.prometheus import inc_queue_drop

log = structlog.get_logger()

@dataclass(frozen=True)
class StrategyHandle:
    name: str
    thread: threading.Thread
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[BusEvent]
    ready_event: threading.Event   # set by strategy thread when loop is running


class BusManager:
    def __init__(self) -> None:
        self._handles: dict[str, StrategyHandle] = {}
        self._started = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def register(self, handle: StrategyHandle) -> None:
        if self._started:
            raise RuntimeError("Cannot register after BusManager.start()")
        self._handles[handle.name] = handle

    def start(self) -> None:
        self._started = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="bus-manager")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        settings = get_settings()
        # Wait for strategy readiness
        timeout = settings.bot_subscribe_timeout_s
        for name, handle in self._handles.items():
            if not handle.ready_event.wait(timeout=timeout):
                log.warning("strategy_not_ready_skipped", strategy=name, timeout_s=timeout)
        self._consume_loop()

    def _consume_loop(self) -> None:
        ...  # see Backoff & XREADGROUP section below

    def _route(self, stream_key: str, entry: dict[str, str]) -> None:
        event = parse_stream_entry(stream_key, entry)
        if event is None:
            return
        for handle in self._handles.values():
            self._deliver(handle, event)

    def _deliver(self, handle: StrategyHandle, event: BusEvent) -> None:
        queue = handle.queue
        settings = get_settings()
        if queue.qsize() >= settings.bot_queue_max_depth:
            # Drop oldest
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            inc_queue_drop(handle.name)
            log.warning("queue_drop_oldest", strategy=handle.name)
            # Enqueue synthetic GapMarker
            gap = GapMarker(exchange="", symbol="", gap_cause="queue_overflow", ts=int(time.time() * 1000))
            self._enqueue(handle, gap)
        self._enqueue(handle, event)

    def _enqueue(self, handle: StrategyHandle, event: BusEvent) -> None:
        asyncio.run_coroutine_threadsafe(handle.queue.put(event), handle.loop)
```

### XREADGROUP + Backoff Pattern

```python
def _consume_loop(self) -> None:
    settings = get_settings()
    consumer_name = f"{socket.gethostname()}-{os.getpid()}"
    group = settings.bot_consumer_group
    # Collect all stream keys from strategy subscriptions
    # For now route all configured streams
    streams = {
        f"candles:close:*": ">",
        f"ob_features:*": ">",
        f"ticks:*": ">",
        f"funding:*": ">",
    }
    # Use a sync Redis client in the bus manager thread
    import redis as sync_redis
    r = sync_redis.from_url(settings.redis_url)

    backoff = 1.0
    consecutive_failures = 0

    while not self._stop_event.is_set():
        try:
            # XREADGROUP with 1s block timeout
            results = r.xreadgroup(group, consumer_name, streams, count=100, block=1000)
            backoff = 1.0
            consecutive_failures = 0
            if not results:
                continue
            for stream_key_bytes, messages in results:
                stream_key = stream_key_bytes.decode()
                for msg_id, entry in messages:
                    str_entry = {k.decode(): v.decode() for k, v in entry.items()}
                    self._route(stream_key, str_entry)
                    r.xack(stream_key_bytes, group, msg_id)
        except Exception as exc:
            consecutive_failures += 1
            log.warning("bus_manager_redis_error", attempt=consecutive_failures, error=str(exc))
            if backoff >= 60.0 and consecutive_failures >= 3:
                log.critical("bus_manager_persistent_failure", error=str(exc))
                os.kill(os.getpid(), signal.SIGTERM)
                return
            time.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
```

**Important:** `XREADGROUP` with glob patterns is not supported by Redis — you must subscribe to specific stream keys. The actual stream keys to subscribe to depend on which symbols/exchanges are configured. The Bus Manager should read `SYMBOLS_KUCOIN` and `SYMBOLS_BYBIT` from Settings to build the stream list, OR subscribe to the streams as they are discovered from strategy `subscribe()` calls. For this story, keep it simple: the Bus Manager subscribes to a configurable list of streams. The exact subscription mechanism can be refined in a later story — for this story the L2 test creates specific streams and verifies routing.

**Note on XREADGROUP and consumer groups:** Before using XREADGROUP, the consumer group must be created with `XGROUP CREATE stream group $ MKSTREAM`. The Bus Manager should call `XGROUP CREATE` for each stream it subscribes to (idempotent: catch `BUSYGROUP` error which means the group already exists).

### Prometheus Metrics Setup

Create `bot_service/metrics/prometheus.py`:

```python
from __future__ import annotations
from prometheus_client import CollectorRegistry, Counter

_registry: CollectorRegistry | None = None

def get_registry() -> CollectorRegistry:
    global _registry
    if _registry is None:
        _registry = CollectorRegistry()
    return _registry

_queue_drop: Counter | None = None

def inc_queue_drop(strategy: str) -> None:
    global _queue_drop
    if _queue_drop is None:
        _queue_drop = Counter(
            "bot_queue_drop_total",
            "Events dropped from strategy queue (oldest evicted)",
            ["strategy"],
            registry=get_registry(),
        )
    _queue_drop.labels(strategy=strategy).inc()
```

### What Already Exists

- `bot_service/bus/event_bus.py` — stub, REPLACE entirely
- `bot_service/bus/event_types.py` — `BusEvent`, `GapMarker`, `parse_stream_entry` fully implemented (Story 11.3)
- `bot_service/config.py` — `Settings` with all bot_* env vars including `bot_consumer_group`, `bot_queue_max_depth`, `bot_subscribe_timeout_s`
- `bot_service/metrics/prometheus.py` — stub, implement in this story

### L2 Test Pattern

```python
@pytest.mark.l2
def test_bus_manager_delivers_events(redis_client: ...) -> None:
    # Create a strategy with a real asyncio queue
    loop = asyncio.new_event_loop()
    queue: asyncio.Queue[BusEvent] = asyncio.Queue(maxsize=100)
    ready = threading.Event()
    ready.set()  # immediately ready

    handle = StrategyHandle(name="test", thread=threading.current_thread(),
                            loop=loop, queue=queue, ready_event=ready)

    manager = BusManager()
    manager.register(handle)
    manager.start()

    # Publish events to Redis, wait for delivery, assert
    ...
    manager.stop()
    loop.close()
```

Use `fakeredis` for L2 tests if real Redis is not available — add `fakeredis` to `requirements-dev.txt`.

### Critical Constraints (project-context.md)

- `asyncio.Queue` must be bounded (`maxsize=BOT_QUEUE_MAX_DEPTH`) — do NOT create unbounded queues
- Drop OLDEST not newest on overflow
- After drop: immediately post synthetic `GapMarker(gap_cause="queue_overflow")`
- `run_coroutine_threadsafe` — never `loop.call_soon_threadsafe` for queue puts
- Bus Manager must be a daemon thread so its death triggers the watchdog, not a hang
- `register()` after `start()` → `RuntimeError` (enforces routing table immutability, BS-FR5)
- Consumer name: `{hostname}-{pid}` to prevent consumer ID collisions

### References

- [Source: bot-service/project-context.md § Concurrency Model]
- [Source: bot-service/project-context.md § Strategy Queue — Bounded, GapMarker on Drop]
- [Source: _bmad-output/planning-artifacts/epics-bot.md § Story 11.4]

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- `_deliver` calls `get_settings()` lazily (only if `queue_max_depth` not passed as constructor arg); tests pass `queue_max_depth` explicitly to avoid credential env requirements.
- Overflow drop semantics changed to always drop 2 items (for GapMarker + real event) when `maxsize == queue_max_depth`; the conditional second drop was dead code.
- `conftest.py` created to set fake credential env vars and clear lru_cache between tests.
- Loop teardown: `handle.thread.join()` required before `loop.close()` to avoid "Cannot close a running event loop".

### File List

- bot_service/bus/event_bus.py
- bot_service/metrics/prometheus.py
- tests/test_bus_manager.py
- tests/conftest.py
