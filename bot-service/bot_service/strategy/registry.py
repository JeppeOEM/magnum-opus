from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import structlog

from bot_service.bus.event_bus import BusManager, StrategyHandle
from bot_service.bus.event_types import BarClose, BusEvent, FundingRate, GapMarker
from bot_service.config import Settings
from bot_service.exchange import ExchangeClient
from bot_service.metrics.prometheus import (
    inc_strategy_load_failure,
    inc_strategy_restart,
    reset_strategy_gauges,
    set_strategy_backoff_seconds,
)
from bot_service.strategy.base import BaseStrategy
from bot_service.strategy.circuit_breaker import DailyLossCircuitBreaker
from bot_service.strategy.order_worker import OrderQueueWorker
from bot_service.strategy.reconciliation import run_startup_reconciliation

# Backoff durations for watchdog crash restarts: 5s → 10s → 30s → 60s (cap).
_BACKOFF_SEQUENCE: list[float] = [5.0, 10.0, 30.0, 60.0]

log = structlog.get_logger()


async def run_strategy_event_loop(
    strategy: BaseStrategy,
    queue: asyncio.Queue[BusEvent],
    stop_event: threading.Event,
    ready_event: threading.Event,
) -> None:
    """Strategy event loop: subscribe → heartbeat → route events until stop.

    Called inside the strategy's dedicated asyncio event loop thread.
    """
    try:
        strategy.run_subscribe(timeout_s=float(strategy._settings.bot_subscribe_timeout_s))
        strategy.start_heartbeat()
    except Exception as exc:
        log.error(
            "strategy_subscribe_failed",
            strategy=strategy._name,
            error=str(exc),
        )
        return
    ready_event.set()
    while not stop_event.is_set():
        try:
            event = await asyncio.wait_for(queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            strategy.ack_heartbeat()
            continue
        if isinstance(event, BarClose):
            strategy.on_bar(event)
        elif isinstance(event, GapMarker):
            strategy.handle_gap(event)
        elif isinstance(event, FundingRate):
            strategy._last_event_ts = time.time()
            strategy.handle_funding_rate(event)
        strategy.ack_heartbeat()


def _import_strategy_class(path: Path) -> type[BaseStrategy] | None:
    """Load a .py file and return the single BaseStrategy subclass it defines.

    Returns None and logs ERROR on any import failure, syntax error, or if the
    file defines zero or more than one BaseStrategy subclass.
    """
    module_name = f"_strategy_{path.stem}"
    sys.modules.pop(module_name, None)
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot create spec for {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        candidates = [
            cls
            for cls in vars(module).values()
            if isinstance(cls, type)
            and issubclass(cls, BaseStrategy)
            and cls is not BaseStrategy
            and cls.__module__ == module_name
        ]
        if len(candidates) == 0:
            log.error("strategy_file_no_class", filename=str(path))
            inc_strategy_load_failure(path.name, "no_class_found")
            return None
        if len(candidates) > 1:
            log.error(
                "strategy_file_multiple_classes",
                filename=str(path),
                count=len(candidates),
            )
            inc_strategy_load_failure(path.name, "multiple_classes")
            return None
        return candidates[0]
    except Exception as exc:
        sys.modules.pop(module_name, None)
        log.error("strategy_file_import_error", filename=str(path), error=str(exc))
        inc_strategy_load_failure(path.name, type(exc).__name__)
        return None


def _scan_directory(
    strategies_dir: Path,
) -> dict[str, tuple[Path, type[BaseStrategy]]]:
    """Scan strategies_dir for valid, uniquely-named BaseStrategy subclasses.

    Returns {class_name: (path, cls)} for all valid, non-duplicate files.
    Duplicate class names (same name in multiple files) cause both files to be
    rejected and inc_strategy_load_failure called once per offending file.
    """
    if not strategies_dir.is_dir():
        log.warning("strategies_dir_not_found", path=str(strategies_dir))
        return {}

    # First pass: import all .py files
    candidates: dict[Path, tuple[str, type[BaseStrategy]]] = {}
    for py_file in sorted(strategies_dir.glob("*.py")):
        cls = _import_strategy_class(py_file)
        if cls is not None:
            candidates[py_file] = (cls.__name__, cls)

    # Second pass: detect duplicate class names across all files
    class_counts: dict[str, int] = {}
    for _, (class_name, _) in candidates.items():
        class_counts[class_name] = class_counts.get(class_name, 0) + 1
    duplicates = {name for name, count in class_counts.items() if count > 1}

    result: dict[str, tuple[Path, type[BaseStrategy]]] = {}
    for path, (class_name, cls) in candidates.items():
        if class_name in duplicates:
            inc_strategy_load_failure(path.name, "duplicate_class_name")
            log.error(
                "strategy_class_name_duplicate",
                filename=str(path),
                class_name=class_name,
            )
        else:
            result[class_name] = (path, cls)

    return result


def _create_handle_and_thread(
    strategy: BaseStrategy,
    stop_event: threading.Event,
    queue_max_depth: int = 1000,
) -> StrategyHandle:
    """Create a StrategyHandle with a dedicated asyncio event loop thread.

    The thread runs ``run_strategy_event_loop`` to completion. The handle's
    ``ready_event`` is set once ``subscribe()`` completes inside the thread.
    """
    loop = asyncio.new_event_loop()
    strategy._loop = loop
    queue: asyncio.Queue[BusEvent] = asyncio.Queue(maxsize=queue_max_depth)
    ready_event = threading.Event()

    def _run_loop() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(
            run_strategy_event_loop(strategy, queue, stop_event, ready_event)
        )

    t = threading.Thread(
        target=_run_loop,
        daemon=True,
        name=f"strategy-{strategy._name}",
    )
    t.start()

    return StrategyHandle(
        name=strategy._name,
        thread=t,
        loop=loop,
        queue=queue,
        ready_event=ready_event,
    )


def _stop_thread(
    handle: StrategyHandle,
    stop_event: threading.Event,
    timeout_s: float,
) -> None:
    """Signal strategy thread to stop and wait up to timeout_s for clean exit."""
    stop_event.set()
    handle.thread.join(timeout=timeout_s)
    if handle.thread.is_alive():
        # Thread did not exit within timeout. thread._stop() was removed in
        # Python 3.9 — log CRITICAL and leave it; the thread is daemon=True
        # so it will be reaped when the process exits.
        log.critical("strategy_thread_force_stop", strategy=handle.name)


class FileWatcher:
    """Watches strategies_dir for .py changes and hot-reloads strategies.

    Lifecycle:
    1. ``initial_scan()`` — loads all strategies, runs reconciliation, registers
       handles with BusManager pre-start; returns required stream keys.
    2. Caller calls ``bus_manager.add_stream(key)`` for each returned key, then
       ``bus_manager.start()``.
    3. ``run_loop()`` — polls every ``bot_filewatcher_interval_s`` for changes
       (new files, removed files, in-place modifications via mtime).
    4. ``stop_all()`` — called during teardown to stop all strategy threads.
    """

    def __init__(
        self,
        bus_manager: BusManager,
        exchange_client: ExchangeClient,
        exchange: str,
        settings: Settings,
        questdb_http_addr: str,
        questdb_ilp_addr: str,
        circuit_breaker: DailyLossCircuitBreaker | None = None,
        on_circuit_breaker_trip: Callable[[], None] | None = None,
    ) -> None:
        self._bus_manager = bus_manager
        self._exchange_client = exchange_client
        self._exchange = exchange
        self._settings = settings
        self._questdb_http_addr = questdb_http_addr
        self._questdb_ilp_addr = questdb_ilp_addr
        self._circuit_breaker = circuit_breaker
        self._on_circuit_breaker_trip = on_circuit_breaker_trip
        self._strategies_dir = Path(settings.bot_strategies_dir)
        # class_name → (path, handle, stop_event, mtime)
        self._loaded: dict[str, tuple[Path, StrategyHandle, threading.Event, float]] = {}
        # class_name → (current_backoff_s, restart_count, last_restart_ts)
        self._backoff: dict[str, tuple[float, int, float]] = {}
        # names currently in watchdog backoff — _rescan skips these to avoid double-load
        self._pending_restart: set[str] = set()

    async def initial_scan(self) -> set[str]:
        """Load all valid strategy files; register handles with BusManager (pre-start).

        Returns the union of stream keys needed by all loaded strategies so
        the caller can call ``bus_manager.add_stream`` before ``start()``.
        """
        valid = _scan_directory(self._strategies_dir)
        stream_keys: set[str] = set()
        for class_name, (path, cls) in valid.items():
            keys = await self._load_new(class_name, path, cls, pre_start=True)
            stream_keys.update(keys)
        return stream_keys

    async def run_loop(self) -> None:
        """Poll every ``bot_filewatcher_interval_s`` for strategy file changes."""
        while True:
            await asyncio.sleep(float(self._settings.bot_filewatcher_interval_s))
            await self._rescan()

    async def _rescan(self) -> None:
        """Detect new, removed, or modified strategy files and react."""
        valid = _scan_directory(self._strategies_dir)
        current_names = set(valid.keys())
        loaded_names = set(self._loaded.keys())

        for class_name in loaded_names - current_names:
            await self._unload(class_name, reason="file_removed")

        for class_name in current_names - loaded_names - self._pending_restart:
            path, cls = valid[class_name]
            await self._load_new(class_name, path, cls, pre_start=False)

        for class_name in current_names & loaded_names:
            path, cls = valid[class_name]
            old_path, _, _, old_mtime = self._loaded[class_name]
            if old_path == path:
                try:
                    new_mtime = path.stat().st_mtime
                except OSError:
                    continue
                if new_mtime != old_mtime:
                    await self._hot_reload(class_name, path, cls)

    async def _load_new(
        self,
        class_name: str,
        path: Path,
        cls: type[BaseStrategy],
        pre_start: bool = False,
    ) -> set[str]:
        """Instantiate, reconcile, spawn thread, and register with BusManager."""
        try:
            strategy = cls(name=class_name, settings=self._settings)
            order_worker = OrderQueueWorker(
                strategy_name=class_name,
                max_position_pct=strategy.max_position_pct,
                paper_trading=strategy.paper_trading,
                exchange_client=self._exchange_client,
                questdb_ilp_addr=self._questdb_ilp_addr,
                portfolio_value_usd=self._settings.bot_portfolio_value_usd,
                max_order_notional_usd=self._settings.max_order_notional_usd,
                circuit_breaker=self._circuit_breaker,
                on_circuit_breaker_trip=self._on_circuit_breaker_trip,
            )
            await run_startup_reconciliation(
                strategy=strategy,
                order_worker=order_worker,
                exchange_client=self._exchange_client,
                exchange=self._exchange,
                questdb_http_addr=self._questdb_http_addr,
                questdb_ilp_addr=self._questdb_ilp_addr,
                timeout_s=float(self._settings.bot_reconciliation_timeout_s),
            )
            strategy._exchange_client = self._exchange_client
            strategy._exchange = self._exchange
            strategy._order_worker = order_worker
            stop_event = threading.Event()
            handle = _create_handle_and_thread(
                strategy,
                stop_event,
                queue_max_depth=self._settings.bot_queue_max_depth,
            )

            mode = strategy.orderbook_mode
            if mode != "none":
                self._bus_manager.provision_pubsub(
                    class_name, mode, strategy._on_orderbook, strategy._on_candles1s
                )

            stream_keys: set[str] = set()
            for symbol, tf in strategy._bar_handlers:
                stream_keys.add(f"candles:close:{self._exchange}:{symbol}:{tf}")
                stream_keys.add(f"candles:ob:{self._exchange}:{symbol}")
            for symbol in strategy._managed_positions:
                stream_keys.add(f"candles:close:{self._exchange}:{symbol}:1s")
                stream_keys.add(f"candles:ob:{self._exchange}:{symbol}")
            stream_keys.update(strategy._funding_stream_keys)

            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            self._loaded[class_name] = (path, handle, stop_event, mtime)

            if pre_start:
                self._bus_manager.register(handle)
            else:
                self._bus_manager.dynamic_register(handle)
                unregistered = stream_keys - set(self._bus_manager._stream_keys)
                if unregistered:
                    log.warning(
                        "strategy_stream_keys_not_subscribed",
                        strategy=class_name,
                        unregistered=sorted(unregistered),
                    )

            log.info("strategy_loaded", strategy=class_name, path=str(path))
            return stream_keys
        except Exception as exc:
            log.error("strategy_load_error", strategy=class_name, error=str(exc))
            return set()

    async def _unload(self, class_name: str, reason: str) -> None:
        """Stop strategy thread and remove from BusManager."""
        if class_name not in self._loaded:
            return
        _, handle, stop_event, _ = self._loaded.pop(class_name)
        self._bus_manager.deprovision_pubsub(class_name)
        _stop_thread(handle, stop_event, float(self._settings.bot_shutdown_timeout_s))
        self._bus_manager.dynamic_deregister(class_name)
        log.info("strategy_unloaded", strategy=class_name, reason=reason)

    async def _hot_reload(
        self,
        class_name: str,
        path: Path,
        new_cls: type[BaseStrategy],
    ) -> None:
        """Stop old strategy instance and start a new one from the updated class."""
        log.info("strategy_hot_reload", strategy=class_name, path=str(path))
        await self._unload(class_name, reason="hot_reload")
        importlib.invalidate_caches()
        await self._load_new(class_name, path, new_cls, pre_start=False)

    async def watch_loop(self) -> None:
        """Watchdog: poll every 5 s for crashed (unintentionally dead) strategy threads."""
        while True:
            await asyncio.sleep(5.0)
            crashed = [
                name
                for name, (_, handle, stop_event, _) in list(self._loaded.items())
                if not handle.thread.is_alive() and not stop_event.is_set()
            ]
            for class_name in crashed:
                try:
                    await self._handle_crash(class_name)
                except Exception as exc:
                    log.error(
                        "watchdog_crash_handler_failed",
                        strategy=class_name,
                        error=str(exc),
                    )

    async def _handle_crash(self, class_name: str) -> None:
        """Detect a crashed thread: reset metrics, backoff, reconcile, restart."""
        entry = self._loaded.pop(class_name, None)
        if entry is None:
            return
        path, _, _, _ = entry
        self._pending_restart.add(class_name)

        try:
            backoff_s, restart_count, _ = self._backoff.get(
                class_name, (_BACKOFF_SEQUENCE[0], 0, 0.0)
            )

            log.error(
                "strategy_thread_crashed",
                strategy=class_name,
                restart_count=restart_count,
            )

            # Reset per-strategy Prometheus gauges BEFORE backoff sleep (AC1).
            reset_strategy_gauges(class_name)
            inc_strategy_restart(class_name)
            set_strategy_backoff_seconds(class_name, backoff_s)

            self._bus_manager.deprovision_pubsub(class_name)
            self._bus_manager.dynamic_deregister(class_name)

            await asyncio.sleep(backoff_s)

            # Advance backoff for next crash (capped at last entry).
            next_backoff = _BACKOFF_SEQUENCE[
                min(restart_count + 1, len(_BACKOFF_SEQUENCE) - 1)
            ]
            self._backoff[class_name] = (next_backoff, restart_count + 1, time.time())

            new_cls = _import_strategy_class(path)
            if new_cls is None:
                log.error("strategy_restart_import_failed", strategy=class_name)
                return

            await self._load_new(class_name, path, new_cls, pre_start=False)
        finally:
            # Always clean up _pending_restart — covers CancelledError and exceptions.
            self._pending_restart.discard(class_name)

    def get_strategy_statuses(self) -> dict[str, str]:
        """Return in-memory strategy health: "running", "restarting", or "stopped"."""
        result: dict[str, str] = {}
        for name, (_, handle, stop_event, _) in self._loaded.items():
            if name in self._pending_restart:
                result[name] = "restarting"
            elif handle.thread.is_alive() and not stop_event.is_set():
                result[name] = "running"
            else:
                result[name] = "stopped"
        for name in self._pending_restart:
            if name not in result:
                result[name] = "restarting"
        return result

    def stop_all(self) -> None:
        """Stop all loaded strategy threads. Called during service teardown."""
        for class_name in list(self._loaded.keys()):
            self._bus_manager.deprovision_pubsub(class_name)
            _, handle, stop_event, _ = self._loaded.pop(class_name)
            _stop_thread(handle, stop_event, float(self._settings.bot_shutdown_timeout_s))
            self._bus_manager.dynamic_deregister(class_name)
