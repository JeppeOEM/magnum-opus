from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import redis
import structlog

from bot_service.bus.event_types import BusEvent, GapMarker, parse_stream_entry
from bot_service.config import get_settings
from bot_service.metrics.prometheus import inc_queue_drop, set_consumer_lag

log: structlog.stdlib.BoundLogger = structlog.get_logger()


# How long to wait for a single XREADGROUP call (milliseconds).
_XREAD_BLOCK_MS = 1000

# Exponential backoff parameters for Redis errors.
_BACKOFF_INITIAL = 1.0
_BACKOFF_MULTIPLIER = 2.0
_BACKOFF_CAP = 60.0
# How many consecutive times the cap must be hit before SIGTERM.
_BACKOFF_CAP_LIMIT = 3


@dataclass(frozen=True)
class StrategyHandle:
    """Immutable reference to a running strategy thread.

    ``ready_event`` is set by the strategy thread once its asyncio event loop
    is running and the queue is accepting events.  Bus Manager waits for this
    before routing events to the strategy.
    """

    name: str
    thread: threading.Thread
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[BusEvent]
    ready_event: threading.Event


class BusManager:
    """Reads all Redis streams via XREADGROUP and routes events to per-strategy queues.

    Usage::

        manager = BusManager()
        manager.add_stream("candles:close:kucoin:BTCUSDT:1s")
        manager.register(handle_a)
        manager.register(handle_b)
        manager.start()
        # ... running ...
        manager.stop()
    """

    _handles: dict[str, StrategyHandle]
    _stream_keys: list[str]
    _started: bool
    _stop_event: threading.Event
    _thread: threading.Thread | None

    def __init__(self, queue_max_depth: int | None = None) -> None:
        self._queue_max_depth_override = queue_max_depth
        self._handles = {}
        self._handles_lock = threading.RLock()
        self._stream_keys = []
        self._started = False
        self._stop_event = threading.Event()
        self._thread = None
        self._pubsub_ob_callbacks: dict[str, Callable[[dict[str, Any]], None]] = {}
        self._pubsub_c1s_callbacks: dict[str, Callable[[dict[str, Any]], None]] = {}
        self._pubsub_lock: threading.RLock = threading.RLock()
        self._pubsub_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_stream(self, stream_key: str) -> None:
        """Add a Redis stream key to subscribe to.

        Must be called before :meth:`start`.
        """
        if self._started:
            raise RuntimeError(
                "Cannot add a stream after BusManager.start() has been called."
            )
        if stream_key not in self._stream_keys:
            self._stream_keys.append(stream_key)

    def register(self, handle: StrategyHandle) -> None:
        """Register a strategy handle.

        Raises ``RuntimeError`` if called after :meth:`start`.
        """
        if self._started:
            raise RuntimeError(
                "Cannot register a strategy handle after BusManager.start() has been called."
            )
        self._handles[handle.name] = handle

    def dynamic_register(self, handle: StrategyHandle) -> None:
        """Register a strategy handle after :meth:`start` (hot-reload path).

        Thread-safe: acquires :attr:`_handles_lock` before mutating.
        """
        with self._handles_lock:
            self._handles[handle.name] = handle

    def dynamic_deregister(self, name: str) -> None:
        """Remove a strategy handle after :meth:`start` (hot-reload path).

        Thread-safe: acquires :attr:`_handles_lock` before mutating. No-op if
        ``name`` is not currently registered.
        """
        with self._handles_lock:
            self._handles.pop(name, None)

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

    def start(self) -> None:
        """Spawn the daemon consumer thread and begin routing events."""
        self._started = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="bus-manager"
        )
        self._thread.start()
        self._pubsub_thread = threading.Thread(
            target=self._run_pubsub, daemon=True, name="bus-pubsub"
        )
        self._pubsub_thread.start()

    def stop(self) -> None:
        """Signal the consumer thread to stop and wait up to 5 s for it to exit."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        if self._pubsub_thread is not None:
            self._pubsub_thread.join(timeout=5.0)


    def is_alive(self) -> bool:
        """Return True if the bus-manager thread is still running."""
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Internal — main thread body
    # ------------------------------------------------------------------

    def _run(self) -> None:
        try:
            settings = get_settings()
            timeout = settings.bot_subscribe_timeout_s

            # Wait for every strategy's ready_event before consuming.
            with self._handles_lock:
                initial_handles = list(self._handles.items())
            for name, handle in initial_handles:
                if not handle.ready_event.wait(timeout=float(timeout)):
                    log.warning(
                        "strategy_not_ready_skipped",
                        strategy=name,
                        timeout_s=timeout,
                    )

            self._consume_loop()
        except Exception as exc:
            log.critical("bus_manager_unhandled_error", error=str(exc))
            os.kill(os.getpid(), signal.SIGTERM)

    def _consume_loop(self) -> None:  # noqa: C901 — complexity justified by AC
        settings = get_settings()
        consumer_name = f"{socket.gethostname()}-{os.getpid()}"
        group = settings.bot_consumer_group
        stream_keys = self._stream_keys

        if not stream_keys:
            # Nothing to consume — wait until stop is requested.
            self._stop_event.wait()
            return

        r: redis.Redis[bytes] = redis.from_url(settings.redis_url)

        # Ensure the consumer group exists for every stream key (idempotent).
        self._ensure_consumer_groups(r, stream_keys, group)

        # Dict passed to XREADGROUP: key → ">" (only new/undelivered messages).
        streams_arg: dict[str | bytes, str | bytes] = {k: ">" for k in stream_keys}

        backoff = _BACKOFF_INITIAL
        consecutive_cap_hits = 0

        while not self._stop_event.is_set():
            try:
                raw = r.xreadgroup(
                    group,
                    consumer_name,
                    streams_arg,
                    count=100,
                    block=_XREAD_BLOCK_MS,
                )
                results: list[tuple[bytes, list[tuple[bytes, dict[bytes, bytes]]]]] = raw or []
                # Successful read — reset backoff state.
                backoff = _BACKOFF_INITIAL
                consecutive_cap_hits = 0

                for stream_key_bytes, messages in results:
                    stream_key = stream_key_bytes.decode()
                    for msg_id, raw_entry in messages:
                        str_entry: dict[str, str] = {
                            k.decode(): v.decode() for k, v in raw_entry.items()
                        }
                        self._route(stream_key, str_entry)
                        r.xack(stream_key_bytes, group, msg_id)  # type: ignore[no-untyped-call]  # redis-py stubs missing xack return type

                # Emit queue depth as consumer lag for every registered strategy.
                # Runs on every successful poll (including empty) so Prometheus always
                # has a live time series for each active strategy.
                with self._handles_lock:
                    lag_snapshot = [(h.name, h.queue.qsize()) for h in self._handles.values()]
                for _name, _size in lag_snapshot:
                    set_consumer_lag(_name, _size)

            except Exception as exc:
                log.warning(
                    "bus_manager_redis_error",
                    attempt=backoff,
                    error=str(exc),
                )

                if backoff >= _BACKOFF_CAP:
                    consecutive_cap_hits += 1
                else:
                    consecutive_cap_hits = 0

                if consecutive_cap_hits >= _BACKOFF_CAP_LIMIT:
                    log.critical(
                        "bus_manager_persistent_failure",
                        error=str(exc),
                    )
                    os.kill(os.getpid(), signal.SIGTERM)
                    return

                # Sleep then apply multiplier (capped), but respect stop_event.
                self._stop_event.wait(timeout=backoff)
                backoff = min(backoff * _BACKOFF_MULTIPLIER, _BACKOFF_CAP)

    # ------------------------------------------------------------------
    # Internal — routing & delivery helpers
    # ------------------------------------------------------------------

    def _route(self, stream_key: str, entry: dict[str, str]) -> None:
        """Parse ``entry`` and deliver the resulting event to every strategy."""
        event = parse_stream_entry(stream_key, entry)
        if event is None:
            return
        with self._handles_lock:
            handles = list(self._handles.values())
        for handle in handles:
            self._deliver(handle, event)

    def _deliver(self, handle: StrategyHandle, event: BusEvent) -> None:
        """Deliver ``event`` to ``handle.queue``, dropping oldest on overflow.

        Drop-oldest semantics (AC-4):
        1. If the queue is at capacity, drop the 2 oldest items (one slot for
           the GapMarker, one for the real event) and increment the drop counter
           for each item actually removed.
        2. Enqueue a synthetic ``GapMarker(gap_cause="queue_overflow")``.
        3. Enqueue the real event.

        Two drops are always attempted when overflow is detected.  If the queue
        holds fewer than 2 items (e.g. maxsize=1), the second ``get_nowait``
        silently no-ops so the GapMarker still occupies the freed slot.
        """
        queue = handle.queue
        max_depth = (
            self._queue_max_depth_override
            if self._queue_max_depth_override is not None
            else get_settings().bot_queue_max_depth
        )

        if queue.qsize() >= max_depth:
            self._drop_oldest(handle)  # make room for GapMarker
            gap = GapMarker(
                exchange="",
                symbol="",
                gap_cause="queue_overflow",
                ts=int(time.time() * 1000),
            )
            self._drop_oldest(handle)  # make room for real event
            self._enqueue(handle, gap)

        self._enqueue(handle, event)

    def _drop_oldest(self, handle: StrategyHandle) -> None:
        """Remove the oldest item from ``handle.queue`` and record the drop metric.

        Metric and log are emitted only when an item was actually removed.
        If the queue is already empty the call is a silent no-op.
        """
        try:
            # get_nowait() is used here from the bus-manager thread.
            # asyncio.Queue is not thread-safe in general, but get_nowait()
            # on CPython is safe to call cross-thread when the item is already
            # in the internal deque (no await involved).  The dropped item is
            # intentionally discarded.
            handle.queue.get_nowait()
            inc_queue_drop(handle.name)
            log.warning("queue_drop_oldest", strategy=handle.name)
        except asyncio.QueueEmpty:
            pass

    def _enqueue(self, handle: StrategyHandle, event: BusEvent) -> None:
        """Schedule ``event`` onto ``handle.queue`` from the bus-manager thread.

        Uses ``run_coroutine_threadsafe`` as required by AC-7.  A RuntimeError
        (closed event loop) is logged immediately; other failures are silent
        because the awaiting put() coroutine will complete once the consumer
        drains space — no data is lost.
        """
        try:
            asyncio.run_coroutine_threadsafe(handle.queue.put(event), handle.loop)
        except RuntimeError as exc:
            log.error("enqueue_failed_loop_closed", strategy=handle.name, error=str(exc))

    # ------------------------------------------------------------------
    # Internal — helpers
    # ------------------------------------------------------------------

    def _run_pubsub(self) -> None:
        """Subscribe to orderbook:* and candles1s:* with exponential-backoff reconnect."""
        backoff = 1.0
        while not self._stop_event.is_set():
            r: redis.Redis[bytes] = redis.from_url(get_settings().redis_url)
            ps = r.pubsub()
            try:
                ps.psubscribe("orderbook:*", "candles1s:*")
                backoff = 1.0  # connection established — reset before polling
                while not self._stop_event.is_set():
                    raw_msg = ps.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if raw_msg is None:
                        continue
                    if raw_msg["type"] != "pmessage":
                        continue
                    channel_raw = raw_msg["channel"]
                    channel: str = channel_raw.decode() if isinstance(channel_raw, bytes) else channel_raw
                    self._dispatch_pubsub(channel, raw_msg["data"])
            except Exception as exc:
                if self._stop_event.is_set():
                    return
                log.error("pubsub_thread_error", error=str(exc))
                self._stop_event.wait(timeout=min(backoff, 30.0))
                backoff = min(backoff * 2, 30.0)
            finally:
                try:
                    ps.close()
                except Exception:
                    pass
                try:
                    r.close()
                except Exception:
                    pass

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
                try:
                    cb(payload)
                except Exception as exc:
                    log.error("pubsub_callback_error", channel=channel, error=str(exc))
        elif channel.startswith("candles1s:"):
            with self._pubsub_lock:
                callbacks = list(self._pubsub_c1s_callbacks.values())
            for cb in callbacks:
                try:
                    cb(payload)
                except Exception as exc:
                    log.error("pubsub_callback_error", channel=channel, error=str(exc))

    @staticmethod
    def _ensure_consumer_groups(
        r: redis.Redis[bytes],
        stream_keys: list[str],
        group: str,
    ) -> None:
        """Create the consumer group for each stream key if it doesn't exist.

        Uses ``XGROUP CREATE ... MKSTREAM`` so the stream is created even if
        it has not yet received any messages.  The ``BUSYGROUP`` error is
        caught and silently ignored — the group already exists.
        """
        for key in stream_keys:
            try:
                r.xgroup_create(key, group, id="$", mkstream=True)
            except redis.exceptions.ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise
