"""L2 tests for BusManager — Redis consumer and event router.

Uses fakeredis (shared FakeServer) so no Docker is required.  All tests are
marked @pytest.mark.l2.
"""

from __future__ import annotations

import asyncio
import os
import signal
import threading
import time
from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import fakeredis
import pytest
import redis as redis_lib

from bot_service.bus.event_bus import BusManager, StrategyHandle
from bot_service.bus.event_types import BarClose, BusEvent, GapMarker


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

STREAM_KEY = "candles:close:kucoin:BTCUSDT:1s"
GROUP = "bot-service"


@pytest.fixture()
def fake_server() -> fakeredis.FakeServer:
    """Return a single in-process FakeServer shared across all clients."""
    return fakeredis.FakeServer()


@pytest.fixture()
def publisher(fake_server: fakeredis.FakeServer) -> fakeredis.FakeRedis:
    """Redis client used to publish events in tests (simulates upstream services)."""
    return fakeredis.FakeRedis(server=fake_server)


def _make_bar_entry(n: int = 1) -> dict[str, str]:
    """Return a minimal BarClose Redis stream entry dict."""
    return {
        "ts": str(1_000_000 + n),
        "open": "100.0",
        "high": "101.0",
        "low": "99.0",
        "close": "100.5",
        "volume": "10.0",
        "quote_volume": "1000.0",
        "trade_count": "5",
        "is_complete": "true",
    }


def _make_strategy(
    name: str,
    maxsize: int = 200,
    loop: asyncio.AbstractEventLoop | None = None,
) -> tuple[StrategyHandle, asyncio.AbstractEventLoop]:
    """Create a StrategyHandle with a fresh event loop running in its own thread.

    Returns (handle, loop).  The loop is started via asyncio.new_event_loop()
    and run_forever() in a daemon thread; the ready_event is set once the loop
    is running.
    """
    actual_loop = loop if loop is not None else asyncio.new_event_loop()
    queue: asyncio.Queue[BusEvent] = asyncio.Queue(maxsize=maxsize)
    ready = threading.Event()

    def _run_loop() -> None:
        asyncio.set_event_loop(actual_loop)
        actual_loop.call_soon(ready.set)  # signal readiness once loop starts
        actual_loop.run_forever()

    t = threading.Thread(target=_run_loop, daemon=True, name=f"strategy-{name}")
    t.start()

    handle = StrategyHandle(
        name=name,
        thread=t,
        loop=actual_loop,
        queue=queue,
        ready_event=ready,
    )
    return handle, actual_loop


def _drain_queue(
    queue: asyncio.Queue[BusEvent],
    loop: asyncio.AbstractEventLoop,
    expected: int,
    timeout: float = 5.0,
) -> list[BusEvent]:
    """Block until ``expected`` items are in the queue (or timeout), then drain."""
    deadline = time.monotonic() + timeout
    while queue.qsize() < expected and time.monotonic() < deadline:
        time.sleep(0.05)
    # Collect whatever arrived.
    items: list[BusEvent] = []
    future = asyncio.run_coroutine_threadsafe(_drain_async(queue), loop)
    items = future.result(timeout=2.0)
    return items


async def _drain_async(queue: asyncio.Queue[BusEvent]) -> list[BusEvent]:
    items: list[BusEvent] = []
    while True:
        try:
            items.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return items


@pytest.fixture()
def patched_manager(
    fake_server: fakeredis.FakeServer,
) -> Generator[BusManager, None, None]:
    """BusManager with redis.from_url patched to use fakeredis."""

    def _fake_from_url(url: str, **kwargs: Any) -> fakeredis.FakeRedis:
        return fakeredis.FakeRedis(server=fake_server)

    manager = BusManager(queue_max_depth=200)
    manager.add_stream(STREAM_KEY)
    with patch("bot_service.bus.event_bus.redis.from_url", side_effect=_fake_from_url):
        yield manager


# ---------------------------------------------------------------------------
# L2 Tests
# ---------------------------------------------------------------------------


@pytest.mark.l2
def test_register_after_start_raises(patched_manager: BusManager) -> None:
    """BusManager.register() raises RuntimeError when called after start()."""
    handle, loop = _make_strategy("s1")
    patched_manager.register(handle)
    patched_manager.start()
    try:
        handle2, loop2 = _make_strategy("s2")
        with pytest.raises(RuntimeError, match="start"):
            patched_manager.register(handle2)
    finally:
        patched_manager.stop()
        loop.call_soon_threadsafe(loop.stop)
        handle.thread.join(timeout=2.0)
        loop.close()


@pytest.mark.l2
def test_delivers_10_events_to_2_strategies(
    fake_server: fakeredis.FakeServer,
    publisher: fakeredis.FakeRedis,
) -> None:
    """Register 2 strategies, publish 10 events, assert all 10 delivered to each."""

    def _fake_from_url(url: str, **kwargs: Any) -> fakeredis.FakeRedis:
        return fakeredis.FakeRedis(server=fake_server)

    handle_a, loop_a = _make_strategy("alpha", maxsize=50)
    handle_b, loop_b = _make_strategy("beta", maxsize=50)

    manager = BusManager(queue_max_depth=50)
    manager.add_stream(STREAM_KEY)
    manager.register(handle_a)
    manager.register(handle_b)

    with patch("bot_service.bus.event_bus.redis.from_url", side_effect=_fake_from_url):
        manager.start()
        # Give the bus-manager thread time to set up the consumer group.
        time.sleep(0.2)

        # Publish 10 events.
        for i in range(10):
            publisher.xadd(STREAM_KEY, _make_bar_entry(i))

        # Both strategies should receive all 10.
        events_a = _drain_queue(handle_a.queue, loop_a, expected=10, timeout=6.0)
        events_b = _drain_queue(handle_b.queue, loop_b, expected=10, timeout=6.0)

        manager.stop()

    assert len(events_a) == 10, f"alpha got {len(events_a)} events, expected 10"
    assert len(events_b) == 10, f"beta got {len(events_b)} events, expected 10"

    # Every event must be a BarClose parsed from candles:close:*.
    for ev in events_a:
        assert isinstance(ev, BarClose), f"unexpected event type: {type(ev)}"
    for ev in events_b:
        assert isinstance(ev, BarClose), f"unexpected event type: {type(ev)}"

    # Clean up loops.
    loop_a.call_soon_threadsafe(loop_a.stop)
    loop_b.call_soon_threadsafe(loop_b.stop)
    handle_a.thread.join(timeout=2.0)
    handle_b.thread.join(timeout=2.0)
    loop_a.close()
    loop_b.close()


@pytest.mark.l2
def test_queue_overflow_drops_oldest_and_appends_gap_marker(
    fake_server: fakeredis.FakeServer,
    publisher: fakeredis.FakeRedis,
) -> None:
    """Flooding the queue beyond maxsize: 2 oldest dropped, GapMarker then event appended.

    Scenario:
    - Queue maxsize = 3 = queue_max_depth.
    - Pre-fill the queue with 3 BarClose events (A, B, C) via run_coroutine_threadsafe.
    - Then route one more BarClose (D) through _deliver().
    Expected outcome after _deliver(D):
    - Queue [A, B, C] hits max_depth; A dropped (room for GapMarker), B dropped (room for D).
    - GapMarker is enqueued, D is enqueued.
    - Final queue contents (in order): [C, GapMarker, D].
    """

    def _fake_from_url(url: str, **kwargs: Any) -> fakeredis.FakeRedis:
        return fakeredis.FakeRedis(server=fake_server)

    maxsize = 3
    handle, loop = _make_strategy("overflow-test", maxsize=maxsize)

    manager = BusManager(queue_max_depth=maxsize)
    manager.add_stream(STREAM_KEY)
    manager.register(handle)

    with patch("bot_service.bus.event_bus.redis.from_url", side_effect=_fake_from_url):
        manager.start()
        # Wait for bus-manager and strategy loop to be ready.
        time.sleep(0.2)

        def _make_bar(n: int) -> BarClose:
            return BarClose(
                exchange="kucoin",
                symbol="BTCUSDT",
                tf="1s",
                ts=n,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                volume=10.0,
                quote_volume=1000.0,
                trade_count=5,
                is_complete=True,
            )

        # Pre-fill the queue to capacity using the strategy's own loop.
        bar_a = _make_bar(1)
        bar_b = _make_bar(2)
        bar_c = _make_bar(3)
        bar_d = _make_bar(4)

        for bar in (bar_a, bar_b, bar_c):
            fut = asyncio.run_coroutine_threadsafe(handle.queue.put(bar), loop)
            fut.result(timeout=2.0)

        assert handle.queue.qsize() == 3, "pre-fill failed"

        # Call _deliver directly (no Redis needed for this sub-test).
        manager._deliver(handle, bar_d)

        # Allow the event loop to process the scheduled puts.
        time.sleep(0.2)

        manager.stop()

    # Collect everything.
    future = asyncio.run_coroutine_threadsafe(_drain_async(handle.queue), loop)
    contents = future.result(timeout=2.0)

    # After _deliver: 2 drops (A, B) then [C, GapMarker, D] in the queue.
    assert len(contents) == 3, f"expected 3 items [C, GapMarker, D], got {len(contents)}: {contents}"

    assert isinstance(contents[0], BarClose), f"expected BarClose at [0], got {type(contents[0])}"
    assert contents[0].ts == 3  # bar_c survived

    gap_marker = contents[1]
    assert isinstance(gap_marker, GapMarker), f"expected GapMarker at [1], got {type(gap_marker)}"
    assert gap_marker.gap_cause == "queue_overflow"

    last_event = contents[2]
    assert isinstance(last_event, BarClose), f"expected BarClose at [2], got {type(last_event)}"
    assert last_event.ts == 4  # bar_d

    loop.call_soon_threadsafe(loop.stop)
    handle.thread.join(timeout=2.0)
    loop.close()


@pytest.mark.l2
def test_overflow_queue_still_full_after_gap_drop(
    fake_server: fakeredis.FakeServer,
) -> None:
    """maxsize=1 edge case: 2 drops on an empty queue leaves room for GapMarker then event.

    With maxsize=1 and overflow: 1st drop removes bar_x (queue empty), 2nd drop is a
    no-op (QueueEmpty caught).  GapMarker fills the slot; bar_y awaits.  The interim
    _drain_queue drains GapMarker, bar_y's put completes.  Final queue contains bar_y.
    """

    def _fake_from_url(url: str, **kwargs: Any) -> fakeredis.FakeRedis:
        return fakeredis.FakeRedis(server=fake_server)

    maxsize = 1
    handle, loop = _make_strategy("tiny-queue", maxsize=maxsize)

    manager = BusManager(queue_max_depth=maxsize)
    manager.add_stream(STREAM_KEY)
    manager.register(handle)

    with patch("bot_service.bus.event_bus.redis.from_url", side_effect=_fake_from_url):
        manager.start()
        time.sleep(0.2)

        def _make_bar(n: int) -> BarClose:
            return BarClose(
                exchange="kucoin",
                symbol="BTCUSDT",
                tf="1s",
                ts=n,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                volume=10.0,
                quote_volume=1000.0,
                trade_count=5,
                is_complete=True,
            )

        bar_x = _make_bar(10)
        bar_y = _make_bar(20)

        # Fill queue to capacity (1 item).
        fut = asyncio.run_coroutine_threadsafe(handle.queue.put(bar_x), loop)
        fut.result(timeout=2.0)
        assert handle.queue.qsize() == 1

        # Deliver a new event — queue full, 2 drops, GapMarker, then bar_y.
        manager._deliver(handle, bar_y)

        # Drain the GapMarker (frees the single slot so bar_y's put can complete).
        first_batch = _drain_queue(handle.queue, loop, expected=1, timeout=3.0)
        # bar_y's put() now has space; wait for it before stopping.
        second_batch = _drain_queue(handle.queue, loop, expected=1, timeout=3.0)
        manager.stop()

    all_items = first_batch + second_batch

    # bar_x (ts=10) was dropped; a GapMarker was emitted; bar_y (ts=20) was delivered.
    bar_closes = [e for e in all_items if isinstance(e, BarClose)]
    gap_markers = [e for e in all_items if isinstance(e, GapMarker)]

    assert len(gap_markers) == 1, f"expected 1 GapMarker, got {gap_markers}"
    assert gap_markers[0].gap_cause == "queue_overflow"
    assert len(bar_closes) == 1, f"expected 1 BarClose (bar_y), got {bar_closes}"
    assert bar_closes[0].ts == 20, "bar_y (ts=20) should be the surviving event"

    loop.call_soon_threadsafe(loop.stop)
    handle.thread.join(timeout=2.0)
    loop.close()


@pytest.mark.l2
def test_ready_event_timeout_skips_strategy_but_continues(
    fake_server: fakeredis.FakeServer,
    publisher: fakeredis.FakeRedis,
) -> None:
    """Strategy whose ready_event is never set is skipped; manager still runs."""

    def _fake_from_url(url: str, **kwargs: Any) -> fakeredis.FakeRedis:
        return fakeredis.FakeRedis(server=fake_server)

    # This handle's ready_event is never set → BusManager skips it after timeout.
    never_ready_event = threading.Event()
    loop_never: asyncio.AbstractEventLoop = asyncio.new_event_loop()
    queue_never: asyncio.Queue[BusEvent] = asyncio.Queue(maxsize=100)
    t_never = threading.Thread(
        target=lambda: None, daemon=True, name="never-ready"
    )
    t_never.start()
    handle_never = StrategyHandle(
        name="never",
        thread=t_never,
        loop=loop_never,
        queue=queue_never,
        ready_event=never_ready_event,
    )

    # A normally-ready strategy.
    handle_ok, loop_ok = _make_strategy("ok")

    manager = BusManager(queue_max_depth=100)
    manager.add_stream(STREAM_KEY)
    manager.register(handle_never)
    manager.register(handle_ok)

    # Patch subscribe timeout to 1s so the test is fast.
    import bot_service.config as cfg_mod

    original_get_settings = cfg_mod.get_settings

    class _FakeSettings:
        redis_url: str = "redis://localhost:6379"
        bot_consumer_group: str = "bot-service"
        bot_queue_max_depth: int = 100
        bot_subscribe_timeout_s: int = 1  # fast timeout for this test

    with (
        patch("bot_service.bus.event_bus.redis.from_url", side_effect=_fake_from_url),
        patch("bot_service.bus.event_bus.get_settings", return_value=_FakeSettings()),
    ):
        manager.start()
        # Wait for the 1-second subscribe timeout + some slack.
        time.sleep(1.5)
        # Publish one event for the ready strategy.
        publisher.xadd(STREAM_KEY, _make_bar_entry(99))
        events_ok = _drain_queue(handle_ok.queue, loop_ok, expected=1, timeout=5.0)
        manager.stop()

    # The ready strategy got the event.
    assert len(events_ok) >= 1
    # The never-ready strategy got nothing (its loop was never started).
    assert queue_never.qsize() == 0

    loop_ok.call_soon_threadsafe(loop_ok.stop)
    handle_ok.thread.join(timeout=2.0)
    loop_ok.close()
    loop_never.close()


@pytest.mark.l2
def test_persistent_failure_sends_sigterm(
    fake_server: fakeredis.FakeServer,
) -> None:
    """After 3 consecutive backoff-cap hits, bus manager sends SIGTERM (AC-6)."""

    def _fake_from_url(url: str, **kwargs: Any) -> fakeredis.FakeRedis:
        client = fakeredis.FakeRedis(server=fake_server)
        # Shadow xreadgroup so every read raises a connection error.
        def _fail(*args: Any, **kw: Any) -> None:
            raise redis_lib.exceptions.ConnectionError("simulated Redis failure")

        client.xreadgroup = _fail  # type: ignore[method-assign]
        return client

    handle, loop = _make_strategy("sigterm-test")
    manager = BusManager(queue_max_depth=100)
    manager.add_stream(STREAM_KEY)
    manager.register(handle)

    # Speed up the backoff so the test completes in < 1 s.
    with (
        patch("bot_service.bus.event_bus.redis.from_url", side_effect=_fake_from_url),
        patch("bot_service.bus.event_bus._BACKOFF_CAP", 0.05),
        patch("bot_service.bus.event_bus._BACKOFF_INITIAL", 0.05),
        patch("bot_service.bus.event_bus.os.kill") as mock_kill,
    ):
        manager.start()
        # 3 cap hits × ~0.05 s backoff; allow 3 s for thread scheduling overhead.
        deadline = time.monotonic() + 3.0
        while not mock_kill.called and time.monotonic() < deadline:
            time.sleep(0.05)
        manager.stop()

    assert mock_kill.called, "os.kill was never invoked — SIGTERM not sent"
    mock_kill.assert_called_with(os.getpid(), signal.SIGTERM)

    loop.call_soon_threadsafe(loop.stop)
    handle.thread.join(timeout=2.0)
    loop.close()


# ---------------------------------------------------------------------------
# Story 26-1: pubsub reconnect loop and stop unblocking
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_pubsub_thread_stops_within_2s_after_stop() -> None:
    """After stop(), pubsub thread exits within 2s even with no messages (AC: stop unblocking)."""
    mock_r = MagicMock()
    mock_ps = MagicMock()
    mock_r.pubsub.return_value = mock_ps
    mock_ps.get_message.return_value = None  # simulate silence

    class _FakeSettings:
        redis_url: str = "redis://localhost:6379"
        bot_consumer_group: str = "bot-service"
        bot_queue_max_depth: int = 100
        bot_subscribe_timeout_s: int = 5

    with (
        patch("bot_service.bus.event_bus.redis.from_url", return_value=mock_r),
        patch("bot_service.bus.event_bus.get_settings", return_value=_FakeSettings()),
    ):
        manager = BusManager()
        manager.start()
        time.sleep(0.05)
        manager.stop()

    assert not manager._pubsub_thread.is_alive()


@pytest.mark.l1
def test_pubsub_reconnects_after_redis_error() -> None:
    """A Redis error causes reconnect, not permanent exit (AC: reconnect on error)."""
    call_count = 0
    reconnected = threading.Event()

    def _make_redis(url: str, **kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        mock_r = MagicMock()
        mock_ps = MagicMock()
        mock_r.pubsub.return_value = mock_ps
        if call_count == 1:
            mock_ps.psubscribe.side_effect = Exception("connection refused")
        else:
            reconnected.set()
            mock_ps.get_message.return_value = None
        return mock_r

    class _FakeSettings:
        redis_url: str = "redis://localhost:6379"
        bot_consumer_group: str = "bot-service"
        bot_queue_max_depth: int = 100
        bot_subscribe_timeout_s: int = 5

    with (
        patch("bot_service.bus.event_bus.redis.from_url", side_effect=_make_redis),
        patch("bot_service.bus.event_bus.get_settings", return_value=_FakeSettings()),
    ):
        manager = BusManager()
        manager.start()
        reconnected.wait(timeout=3.0)
        manager.stop()

    assert reconnected.is_set(), "pubsub thread did not reconnect after error"
