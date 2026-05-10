"""L1 + L2 tests for Story 13.2: FileWatcher, dynamic BusManager registration, strategy event loop.

L1 tests (no IO, mocked external calls): T1.3, T2.5, T3.9, T4.2
L2 integration test (real filesystem, thread-level): T6
"""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot_service.bus.event_bus import BusManager, StrategyHandle
from bot_service.bus.event_types import BarClose, BusEvent, GapMarker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BAR_ENTRY: dict[str, str] = {
    "ts": "1000001",
    "open": "100.0",
    "high": "101.0",
    "low": "99.0",
    "close": "100.5",
    "volume": "10.0",
    "quote_volume": "1000.0",
    "trade_count": "5",
    "is_complete": "true",
}

_STREAM_KEY = "candles:close:bybit:BTCUSDT:1s"


def _make_handle(name: str, maxsize: int = 100) -> tuple[StrategyHandle, asyncio.AbstractEventLoop]:
    """Create a StrategyHandle with a live event loop in a daemon thread."""
    loop = asyncio.new_event_loop()
    queue: asyncio.Queue[BusEvent] = asyncio.Queue(maxsize=maxsize)
    ready = threading.Event()
    ready.set()

    t = threading.Thread(target=loop.run_forever, daemon=True, name=f"test-{name}")
    t.start()

    handle = StrategyHandle(name=name, thread=t, loop=loop, queue=queue, ready_event=ready)
    return handle, loop


def _minimal_strategy_src(class_name: str) -> str:
    return f"""\
from __future__ import annotations
from bot_service.strategy.base import BaseStrategy
from bot_service.config import Settings

class {class_name}(BaseStrategy):
    @property
    def min_lookback(self) -> int:
        return 5

    @property
    def max_position_pct(self) -> float:
        return 0.1

    @property
    def stop_loss_pct(self) -> float:
        return 0.02

    @property
    def paper_trading(self) -> bool:
        return True

    @property
    def bus_timeout_seconds(self) -> int:
        return 300

    @property
    def close_on_bus_timeout(self) -> bool:
        return False

    def subscribe(self) -> None:
        pass
"""


# ---------------------------------------------------------------------------
# T1.3: inc_strategy_load_failure metric smoke test
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_inc_strategy_load_failure_increments() -> None:
    import bot_service.metrics.prometheus as pm

    pm.get_registry()
    pm.inc_strategy_load_failure("bad_strat.py", "ImportError")  # must not raise


# ---------------------------------------------------------------------------
# T2.5: BusManager dynamic_register / dynamic_deregister
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_bus_manager_dynamic_register_routes_after_start() -> None:
    handle, loop = _make_handle("dyn_strat")

    bus = BusManager(queue_max_depth=100)
    bus.start()

    try:
        bus.dynamic_register(handle)
        bus._route(_STREAM_KEY, _BAR_ENTRY)

        # Give event loop time to process the threadsafe put
        fut = asyncio.run_coroutine_threadsafe(handle.queue.get(), loop)
        event = fut.result(timeout=2.0)
        assert isinstance(event, BarClose)
        assert event.symbol == "BTCUSDT"
    finally:
        bus.stop()
        loop.call_soon_threadsafe(loop.stop)


@pytest.mark.l1
def test_bus_manager_dynamic_deregister_stops_routing() -> None:
    handle, loop = _make_handle("dyn_strat2")

    bus = BusManager(queue_max_depth=100)
    bus.start()

    try:
        bus.dynamic_register(handle)
        bus.dynamic_deregister(handle.name)

        bus._route(_STREAM_KEY, _BAR_ENTRY)

        # Small sleep to allow any pending threadsafe coroutines to execute
        time.sleep(0.05)
        assert handle.queue.qsize() == 0
    finally:
        bus.stop()
        loop.call_soon_threadsafe(loop.stop)


# ---------------------------------------------------------------------------
# T3.9: _import_strategy_class and _scan_directory
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_import_strategy_class_valid(tmp_path: Path) -> None:
    from bot_service.strategy.registry import _import_strategy_class

    f = tmp_path / "valid_strat.py"
    f.write_text(_minimal_strategy_src("ValidStrategy"))

    cls = _import_strategy_class(f)
    assert cls is not None
    assert cls.__name__ == "ValidStrategy"


@pytest.mark.l1
def test_import_strategy_class_syntax_error(tmp_path: Path) -> None:
    from bot_service.strategy.registry import _import_strategy_class

    f = tmp_path / "bad_strat.py"
    f.write_text("def broken(:\n")  # intentional syntax error

    cls = _import_strategy_class(f)
    assert cls is None


@pytest.mark.l1
def test_import_strategy_class_no_base_class(tmp_path: Path) -> None:
    from bot_service.strategy.registry import _import_strategy_class

    f = tmp_path / "plain_strat.py"
    f.write_text("class PlainClass:\n    pass\n")

    cls = _import_strategy_class(f)
    assert cls is None


@pytest.mark.l1
def test_scan_directory_duplicate_class_rejected(tmp_path: Path) -> None:
    """Two files with the same class name → empty result, counter incremented twice."""
    from bot_service.strategy.registry import _scan_directory

    (tmp_path / "strat_a.py").write_text(_minimal_strategy_src("DupStrategy"))
    (tmp_path / "strat_b.py").write_text(_minimal_strategy_src("DupStrategy"))

    counter_calls: list[tuple[str, str]] = []

    import bot_service.strategy.registry as reg_mod

    orig = reg_mod.inc_strategy_load_failure

    def _capture(filename: str, reason: str) -> None:
        counter_calls.append((filename, reason))
        orig(filename, reason)

    with patch.object(reg_mod, "inc_strategy_load_failure", _capture):
        result = _scan_directory(tmp_path)

    assert result == {}
    dup_calls = [(f, r) for f, r in counter_calls if r == "duplicate_class_name"]
    assert len(dup_calls) == 2


@pytest.mark.l1
def test_scan_directory_one_valid_one_invalid(tmp_path: Path) -> None:
    """One valid file + one syntax-error file → only valid class in result (AC6)."""
    from bot_service.strategy.registry import _scan_directory

    (tmp_path / "good.py").write_text(_minimal_strategy_src("GoodStrategy"))
    (tmp_path / "bad.py").write_text("def broken(:\n")

    result = _scan_directory(tmp_path)

    assert set(result.keys()) == {"GoodStrategy"}


# ---------------------------------------------------------------------------
# T4.2: run_strategy_event_loop
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_event_loop_processes_bar_close() -> None:
    """BarClose put into queue → strategy.on_bar called."""
    from bot_service.strategy.registry import run_strategy_event_loop

    stop_event = threading.Event()
    ready_event = threading.Event()
    queue: asyncio.Queue[BusEvent] = asyncio.Queue()

    on_bar_calls: list[BarClose] = []
    handle_gap_calls: list[GapMarker] = []

    strategy = MagicMock()
    strategy._settings.bot_subscribe_timeout_s = 5
    strategy.on_bar.side_effect = lambda ev: on_bar_calls.append(ev)
    strategy.handle_gap.side_effect = lambda ev: handle_gap_calls.append(ev)

    # Stop after first ack_heartbeat call (which happens after on_bar)
    strategy.ack_heartbeat.side_effect = lambda: stop_event.set()

    bar = BarClose(
        exchange="bybit",
        symbol="BTCUSDT",
        tf="1s",
        ts=1000001,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=10.0,
        quote_volume=1000.0,
        trade_count=5,
        is_complete=True,
    )
    await queue.put(bar)

    await asyncio.wait_for(
        run_strategy_event_loop(strategy, queue, stop_event, ready_event),
        timeout=3.0,
    )

    assert len(on_bar_calls) == 1
    assert on_bar_calls[0] == bar
    assert ready_event.is_set()


@pytest.mark.l1
async def test_event_loop_exits_on_stop_event() -> None:
    """stop_event set → event loop exits within 2 seconds."""
    from bot_service.strategy.registry import run_strategy_event_loop

    stop_event = threading.Event()
    ready_event = threading.Event()
    queue: asyncio.Queue[BusEvent] = asyncio.Queue()

    strategy = MagicMock()
    strategy._settings.bot_subscribe_timeout_s = 5

    stop_event.set()  # set before loop starts → exits on first check

    await asyncio.wait_for(
        run_strategy_event_loop(strategy, queue, stop_event, ready_event),
        timeout=2.0,
    )
    # Must complete without timeout


# ---------------------------------------------------------------------------
# T6: L2 hot-reload integration test
# ---------------------------------------------------------------------------


@pytest.mark.l2
async def test_hot_reload_stops_old_thread_starts_new(tmp_path: Path) -> None:
    """Write V1 strategy → initial_scan creates handle; overwrite with V2 → old thread stops, new handle registered."""
    from bot_service.strategy.registry import FileWatcher
    from bot_service.config import get_settings

    strategies_dir = tmp_path / "strategies" / "active"
    strategies_dir.mkdir(parents=True)

    # V1 strategy
    strat_file = strategies_dir / "mystrat.py"
    strat_file.write_text(_minimal_strategy_src("MyStrat"))

    settings = get_settings()

    # Patch bot_strategies_dir so FileWatcher uses our tmp dir
    import bot_service.config as cfg_mod
    from unittest.mock import PropertyMock

    mock_settings = MagicMock(wraps=settings)
    mock_settings.bot_strategies_dir = str(strategies_dir)
    mock_settings.bot_subscribe_timeout_s = 5
    mock_settings.bot_shutdown_timeout_s = 5
    mock_settings.bot_reconciliation_timeout_s = 5
    mock_settings.bot_queue_max_depth = 100
    mock_settings.bot_portfolio_value_usd = 10000.0

    mock_exchange = MagicMock()
    mock_exchange.get_open_orders = AsyncMock(return_value=[])

    bus = BusManager(queue_max_depth=100)

    with patch(
        "bot_service.strategy.registry.run_startup_reconciliation",
        new=AsyncMock(),
    ):
        fw = FileWatcher(
            bus_manager=bus,
            exchange_client=mock_exchange,
            exchange="bybit",
            settings=mock_settings,  # type: ignore[arg-type]
            questdb_http_addr="http://localhost:9000",
            questdb_ilp_addr="localhost:9009",
        )

        # Initial scan: registers handle pre-start
        stream_keys = await fw.initial_scan()

        assert "MyStrat" in fw._loaded
        old_handle = fw._loaded["MyStrat"][1]
        old_thread = old_handle.thread

        bus.start()

        # Give the strategy thread time to reach subscribe()
        await asyncio.sleep(0.1)

        # Overwrite with a different class body (same class name, mtime changes)
        time.sleep(0.01)  # ensure mtime is different on fast filesystems
        strat_file.write_text(_minimal_strategy_src("MyStrat") + "\n# v2\n")
        # Touch the file to ensure mtime change
        strat_file.touch()

        await asyncio.sleep(0.05)

        # Trigger rescan directly
        await fw._rescan()

        # Old thread should have stopped
        old_thread.join(timeout=6.0)
        assert not old_thread.is_alive(), "Old strategy thread should have stopped"

        # New handle should be in fw._loaded with a live thread
        assert "MyStrat" in fw._loaded
        new_handle = fw._loaded["MyStrat"][1]
        assert new_handle is not old_handle

        bus.stop()
        fw.stop_all()
