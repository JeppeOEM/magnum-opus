"""L1 unit tests for FileWatcher watchdog (Story 13.3).

Tests cover:
- Crash detection (watch_loop identifies dead threads with stop_event not set)
- Metric call order before backoff sleep (AC1)
- Backoff sequence 5→10→30→60→60 (AC2)
- Restart count persistence across crashes (AC2)
- Intentional stop is ignored by watchdog (AC1 negative case)
- _rescan skips strategies in _pending_restart (AC5)
- Import failure during restart does not crash the watchdog (AC2 edge case)
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from bot_service.bus.event_bus import BusManager, StrategyHandle
from bot_service.strategy.registry import FileWatcher, _BACKOFF_SEQUENCE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_strategy_src(class_name: str) -> str:
    return f"""\
from bot_service.strategy.base import BaseStrategy

class {class_name}(BaseStrategy):
    min_lookback = 1
    max_position_pct = 0.05
    stop_loss_pct = 0.02
    paper_trading = True
    bus_timeout_seconds = 300
    close_on_bus_timeout = False
    def subscribe(self): pass
"""


def _make_settings(**overrides: Any) -> MagicMock:
    s = MagicMock()
    s.bot_strategies_dir = "/tmp/strategies"
    s.bot_filewatcher_interval_s = 60
    s.bot_shutdown_timeout_s = 5
    s.bot_reconciliation_timeout_s = 10
    s.bot_subscribe_timeout_s = 10
    s.bot_queue_max_depth = 100
    s.bot_portfolio_value_usd = 10_000.0
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _make_dead_handle(name: str) -> tuple[StrategyHandle, threading.Event]:
    """Return a StrategyHandle whose thread has already exited plus its stop_event."""
    loop = asyncio.new_event_loop()
    queue: asyncio.Queue[Any] = asyncio.Queue()
    ready = threading.Event()
    stop_event = threading.Event()

    # Thread that exits immediately
    t = threading.Thread(target=lambda: None, daemon=True, name=f"strategy-{name}")
    t.start()
    t.join()  # ensure it's dead

    handle = StrategyHandle(name=name, thread=t, loop=loop, queue=queue, ready_event=ready)
    return handle, stop_event


def _make_file_watcher(tmp_path: Path) -> FileWatcher:
    settings = _make_settings(bot_strategies_dir=str(tmp_path))
    bus = MagicMock(spec=BusManager)
    bus._stream_keys = []
    exchange_client = MagicMock()
    return FileWatcher(
        bus_manager=bus,
        exchange_client=exchange_client,
        exchange="bybit",
        settings=settings,
        questdb_http_addr="http://localhost:9000",
        questdb_ilp_addr="localhost:9009",
    )


# ---------------------------------------------------------------------------
# T7.1: watch_loop detects crashed thread and calls _handle_crash
# ---------------------------------------------------------------------------

async def test_watchdog_detects_crash_calls_handle_crash(tmp_path: Path) -> None:
    fw = _make_file_watcher(tmp_path)
    handle, stop_event = _make_dead_handle("CrashedStrat")
    fw._loaded["CrashedStrat"] = (tmp_path / "crashed.py", handle, stop_event, 0.0)

    crash_calls: list[str] = []

    async def fake_handle_crash(class_name: str) -> None:
        crash_calls.append(class_name)

    fw._handle_crash = fake_handle_crash  # type: ignore[method-assign]

    # Run watch_loop for one iteration by mocking asyncio.sleep then cancelling
    async def one_tick_sleep(duration: float) -> None:
        pass  # don't actually sleep

    call_count = 0

    async def patched_sleep(duration: float) -> None:
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise asyncio.CancelledError()

    with patch("bot_service.strategy.registry.asyncio.sleep", side_effect=patched_sleep):
        with pytest.raises(asyncio.CancelledError):
            await fw.watch_loop()

    assert "CrashedStrat" in crash_calls


# ---------------------------------------------------------------------------
# T7.2: reset_gauges called BEFORE sleep in _handle_crash
# ---------------------------------------------------------------------------

async def test_handle_crash_resets_metrics_before_sleep(tmp_path: Path) -> None:
    fw = _make_file_watcher(tmp_path)
    strat_file = tmp_path / "s.py"
    strat_file.write_text(_minimal_strategy_src("MyStrat"))

    handle, stop_event = _make_dead_handle("MyStrat")
    fw._loaded["MyStrat"] = (strat_file, handle, stop_event, 0.0)

    call_order: list[str] = []

    async def fake_sleep(s: float) -> None:
        call_order.append("sleep")

    with patch("bot_service.strategy.registry.reset_strategy_gauges",
               side_effect=lambda s: call_order.append("reset")), \
         patch("bot_service.strategy.registry.inc_strategy_restart",
               side_effect=lambda s: call_order.append("inc_restart")), \
         patch("bot_service.strategy.registry.set_strategy_backoff_seconds",
               side_effect=lambda s, v: call_order.append("set_backoff")), \
         patch("bot_service.strategy.registry.asyncio.sleep", side_effect=fake_sleep), \
         patch("bot_service.strategy.registry.run_startup_reconciliation",
               new_callable=AsyncMock), \
         patch("bot_service.strategy.registry._import_strategy_class",
               return_value=None):  # don't actually restart
        await fw._handle_crash("MyStrat")

    assert "reset" in call_order
    assert "sleep" in call_order
    assert call_order.index("reset") < call_order.index("sleep")
    assert call_order.index("inc_restart") < call_order.index("sleep")
    assert call_order.index("set_backoff") < call_order.index("sleep")


# ---------------------------------------------------------------------------
# T7.3: Backoff sequence 5 → 10 → 30 → 60 → 60 (cap)
# ---------------------------------------------------------------------------

async def test_handle_crash_backoff_sequence(tmp_path: Path) -> None:
    fw = _make_file_watcher(tmp_path)
    strat_file = tmp_path / "s.py"
    strat_file.write_text(_minimal_strategy_src("SeqStrat"))

    sleep_durations: list[float] = []

    async def capture_sleep(s: float) -> None:
        sleep_durations.append(s)

    async def fake_reconcile(**kwargs: Any) -> None:
        pass

    with patch("bot_service.strategy.registry.asyncio.sleep", side_effect=capture_sleep), \
         patch("bot_service.strategy.registry.reset_strategy_gauges"), \
         patch("bot_service.strategy.registry.inc_strategy_restart"), \
         patch("bot_service.strategy.registry.set_strategy_backoff_seconds"), \
         patch("bot_service.strategy.registry.run_startup_reconciliation",
               new_callable=AsyncMock), \
         patch("bot_service.strategy.registry._import_strategy_class",
               return_value=None):

        for i in range(5):
            handle, stop_event = _make_dead_handle("SeqStrat")
            fw._loaded["SeqStrat"] = (strat_file, handle, stop_event, 0.0)
            await fw._handle_crash("SeqStrat")

    assert sleep_durations == [5.0, 10.0, 30.0, 60.0, 60.0]


# ---------------------------------------------------------------------------
# T7.4: Restart count persists across restarts
# ---------------------------------------------------------------------------

async def test_handle_crash_restart_count_persists(tmp_path: Path) -> None:
    fw = _make_file_watcher(tmp_path)
    strat_file = tmp_path / "s.py"
    strat_file.write_text(_minimal_strategy_src("PersistStrat"))

    with patch("bot_service.strategy.registry.asyncio.sleep", new_callable=AsyncMock), \
         patch("bot_service.strategy.registry.reset_strategy_gauges"), \
         patch("bot_service.strategy.registry.inc_strategy_restart"), \
         patch("bot_service.strategy.registry.set_strategy_backoff_seconds"), \
         patch("bot_service.strategy.registry.run_startup_reconciliation",
               new_callable=AsyncMock), \
         patch("bot_service.strategy.registry._import_strategy_class", return_value=None):

        handle, stop_event = _make_dead_handle("PersistStrat")
        fw._loaded["PersistStrat"] = (strat_file, handle, stop_event, 0.0)
        await fw._handle_crash("PersistStrat")

        handle2, stop_event2 = _make_dead_handle("PersistStrat")
        fw._loaded["PersistStrat"] = (strat_file, handle2, stop_event2, 0.0)
        await fw._handle_crash("PersistStrat")

    _, restart_count, _ = fw._backoff["PersistStrat"]
    assert restart_count == 2


# ---------------------------------------------------------------------------
# T7.5: Intentional stop (stop_event set) is NOT treated as crash
# ---------------------------------------------------------------------------

async def test_watchdog_ignores_intentional_stop(tmp_path: Path) -> None:
    fw = _make_file_watcher(tmp_path)
    handle, stop_event = _make_dead_handle("IntentionalStop")
    stop_event.set()  # intentionally stopped
    fw._loaded["IntentionalStop"] = (tmp_path / "s.py", handle, stop_event, 0.0)

    crash_calls: list[str] = []

    async def fake_handle_crash(class_name: str) -> None:
        crash_calls.append(class_name)

    fw._handle_crash = fake_handle_crash  # type: ignore[method-assign]

    call_count = 0

    async def patched_sleep(duration: float) -> None:
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise asyncio.CancelledError()

    with patch("bot_service.strategy.registry.asyncio.sleep", side_effect=patched_sleep):
        with pytest.raises(asyncio.CancelledError):
            await fw.watch_loop()

    assert "IntentionalStop" not in crash_calls


# ---------------------------------------------------------------------------
# T7.6: _rescan skips strategies in _pending_restart
# ---------------------------------------------------------------------------

async def test_rescan_skips_pending_restart(tmp_path: Path) -> None:
    fw = _make_file_watcher(tmp_path)
    strat_file = tmp_path / "pendingstrat.py"
    strat_file.write_text(_minimal_strategy_src("PendingStrat"))

    # Mark as pending restart (watchdog is managing it)
    fw._pending_restart.add("PendingStrat")

    load_new_calls: list[str] = []

    original_load_new = fw._load_new

    async def spy_load_new(class_name: str, *args: Any, **kwargs: Any) -> Any:
        load_new_calls.append(class_name)
        return set()

    fw._load_new = spy_load_new  # type: ignore[method-assign]

    await fw._rescan()

    assert "PendingStrat" not in load_new_calls


# ---------------------------------------------------------------------------
# T7.7: Import failure during restart does not add strategy back to _loaded
# ---------------------------------------------------------------------------

async def test_handle_crash_import_failure_does_not_restart(tmp_path: Path) -> None:
    fw = _make_file_watcher(tmp_path)
    strat_file = tmp_path / "gone.py"
    # File doesn't exist — import will fail

    handle, stop_event = _make_dead_handle("GoneStrat")
    fw._loaded["GoneStrat"] = (strat_file, handle, stop_event, 0.0)

    with patch("bot_service.strategy.registry.asyncio.sleep", new_callable=AsyncMock), \
         patch("bot_service.strategy.registry.reset_strategy_gauges"), \
         patch("bot_service.strategy.registry.inc_strategy_restart"), \
         patch("bot_service.strategy.registry.set_strategy_backoff_seconds"), \
         patch("bot_service.strategy.registry._import_strategy_class", return_value=None):
        await fw._handle_crash("GoneStrat")

    assert "GoneStrat" not in fw._loaded
    # _pending_restart must be cleaned up even on failure
    assert "GoneStrat" not in fw._pending_restart


# ---------------------------------------------------------------------------
# Story 26-2: deprovision_pubsub called in crash path
# ---------------------------------------------------------------------------


async def test_handle_crash_calls_deprovision_pubsub_before_deregister(tmp_path: Path) -> None:
    """_handle_crash must call deprovision_pubsub before dynamic_deregister (26-2)."""
    fw = _make_file_watcher(tmp_path)
    strat_file = tmp_path / "s.py"
    strat_file.write_text(_minimal_strategy_src("PubSubStrat"))

    handle, stop_event = _make_dead_handle("PubSubStrat")
    fw._loaded["PubSubStrat"] = (strat_file, handle, stop_event, 0.0)

    call_order: list[str] = []

    fw._bus_manager.deprovision_pubsub.side_effect = lambda n: call_order.append(f"deprovision:{n}")
    fw._bus_manager.dynamic_deregister.side_effect = lambda n: call_order.append(f"deregister:{n}")

    with patch("bot_service.strategy.registry.asyncio.sleep", new_callable=AsyncMock), \
         patch("bot_service.strategy.registry.reset_strategy_gauges"), \
         patch("bot_service.strategy.registry.inc_strategy_restart"), \
         patch("bot_service.strategy.registry.set_strategy_backoff_seconds"), \
         patch("bot_service.strategy.registry.run_startup_reconciliation",
               new_callable=AsyncMock), \
         patch("bot_service.strategy.registry._import_strategy_class", return_value=None):
        await fw._handle_crash("PubSubStrat")

    assert "deprovision:PubSubStrat" in call_order
    assert "deregister:PubSubStrat" in call_order
    assert call_order.index("deprovision:PubSubStrat") < call_order.index("deregister:PubSubStrat")


def test_stop_all_calls_deprovision_pubsub(tmp_path: Path) -> None:
    """stop_all must call deprovision_pubsub for every loaded strategy (26-2)."""
    fw = _make_file_watcher(tmp_path)
    strat_file = tmp_path / "s.py"
    strat_file.write_text(_minimal_strategy_src("StopAllStrat"))

    loop = asyncio.new_event_loop()
    queue: asyncio.Queue[Any] = asyncio.Queue()
    ready = threading.Event()
    stop_event = threading.Event()
    t = threading.Thread(target=lambda: None, daemon=True)
    t.start()
    t.join()
    handle = StrategyHandle(name="StopAllStrat", thread=t, loop=loop, queue=queue, ready_event=ready)
    fw._loaded["StopAllStrat"] = (strat_file, handle, stop_event, 0.0)

    deprovisioned: list[str] = []
    fw._bus_manager.deprovision_pubsub.side_effect = lambda n: deprovisioned.append(n)

    fw.stop_all()

    assert "StopAllStrat" in deprovisioned
    loop.close()
