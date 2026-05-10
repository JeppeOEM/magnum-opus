"""L1 unit tests for BaseStrategy bus-timeout heartbeat (Story 13.4).

Tests cover:
- Bus timeout + close_on_bus_timeout=True → place_order called via asyncio.run
- Bus timeout + close_on_bus_timeout=False → no place_order call
- inc_heartbeat_timeout incremented on any timeout
- Emergency close failure → logs CRITICAL, retries after sleep
- Multiple positions → each close attempted independently (no suppression)
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from bot_service.strategy.base import BaseStrategy
from bot_service.config import Settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _BusStrat(BaseStrategy):
    @property
    def min_lookback(self) -> int:
        return 1

    @property
    def max_position_pct(self) -> float:
        return 0.05

    @property
    def stop_loss_pct(self) -> float:
        return 0.02

    @property
    def paper_trading(self) -> bool:
        return True

    @property
    def bus_timeout_seconds(self) -> int:
        return 60

    @property
    def close_on_bus_timeout(self) -> bool:
        return True

    def subscribe(self) -> None:
        pass


class _LogOnlyStrat(_BusStrat):
    @property
    def close_on_bus_timeout(self) -> bool:
        return False


class _LiveCloseStrat(_BusStrat):
    """Non-paper variant for testing the actual REST close path."""
    @property
    def paper_trading(self) -> bool:
        return False


def _make_strategy(close: bool = True) -> _BusStrat | _LogOnlyStrat:
    s: _BusStrat | _LogOnlyStrat
    if close:
        s = _BusStrat("test-strat", Settings())
    else:
        s = _LogOnlyStrat("log-only-strat", Settings())
    return s


def _make_live_strategy() -> _LiveCloseStrat:
    return _LiveCloseStrat("live-strat", Settings())


# ---------------------------------------------------------------------------
# T10.1: bus timeout + close=True → asyncio.run (place_order) called
# ---------------------------------------------------------------------------

def test_bus_timeout_close_on_true_calls_rest() -> None:
    """_on_bus_timeout with close_on_bus_timeout=True spawns thread that calls asyncio.run."""
    strategy = _make_live_strategy()
    strategy._exchange_client = MagicMock()
    strategy._exchange = "bybit"
    strategy._open_positions = {"BTCUSDT": 0.001}

    run_calls: list[Any] = []
    ran_event = threading.Event()

    def fake_run(coro: Any) -> Any:
        run_calls.append(coro)
        coro.close()
        ran_event.set()
        return MagicMock()

    with patch("bot_service.strategy.base.asyncio.run", side_effect=fake_run):
        strategy._on_bus_timeout(elapsed=65.0)
        assert ran_event.wait(timeout=2.0), "asyncio.run was never called within 2s"

    assert len(run_calls) > 0


# ---------------------------------------------------------------------------
# T10.2: bus timeout + close=False → no place_order called
# ---------------------------------------------------------------------------

def test_bus_timeout_close_on_false_no_rest() -> None:
    """_on_bus_timeout with close_on_bus_timeout=False does NOT call asyncio.run."""
    strategy = _make_strategy(close=False)
    strategy._exchange_client = MagicMock()
    strategy._exchange = "bybit"
    strategy._open_positions = {"BTCUSDT": 0.001}

    with patch("bot_service.strategy.base.asyncio.run") as mock_run:
        strategy._on_bus_timeout(elapsed=65.0)
        time.sleep(0.1)

    mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# T10.3: inc_heartbeat_timeout incremented on any timeout
# ---------------------------------------------------------------------------

def test_bus_timeout_increments_counter() -> None:
    """_on_bus_timeout always increments inc_heartbeat_timeout regardless of close mode."""
    strategy = _make_strategy(close=False)
    strategy._open_positions = {}

    with patch("bot_service.strategy.base.inc_heartbeat_timeout") as mock_inc:
        strategy._on_bus_timeout(elapsed=65.0)

    mock_inc.assert_called_once_with(strategy._name)


# ---------------------------------------------------------------------------
# T10.4: emergency close retry on failure
# ---------------------------------------------------------------------------

def test_emergency_close_retry_on_failure() -> None:
    """_emergency_close_symbol retries after failure and logs CRITICAL."""
    strategy = _make_live_strategy()
    strategy._exchange_client = MagicMock()
    strategy._exchange = "bybit"
    strategy._open_positions = {"BTCUSDT": 0.001}

    call_count = 0

    def fake_run(coro: Any) -> Any:
        nonlocal call_count
        call_count += 1
        coro.close()
        if call_count < 2:
            raise RuntimeError("network error")
        return MagicMock()

    sleep_calls: list[float] = []

    with patch("bot_service.strategy.base.asyncio.run", side_effect=fake_run), \
         patch("bot_service.strategy.base.time.sleep", side_effect=lambda s: sleep_calls.append(s)):
        strategy._emergency_close_symbol("BTCUSDT", 0.001)

    assert call_count == 2
    assert 5.0 in sleep_calls


# ---------------------------------------------------------------------------
# T10.5: multiple positions → all close attempts made (no suppression)
# ---------------------------------------------------------------------------

def test_emergency_close_multiple_positions_no_suppression() -> None:
    """_on_bus_timeout spawns a thread per position; both symbols are attempted."""
    strategy = _make_live_strategy()
    strategy._exchange_client = MagicMock()
    strategy._exchange = "bybit"
    strategy._open_positions = {"BTCUSDT": 0.001, "ETHUSDT": 0.01}

    closed_symbols: list[str] = []
    lock = threading.Lock()
    all_done = threading.Event()
    expected_count = 2

    def fake_run(coro: Any) -> Any:
        coro.close()
        return MagicMock()

    original_emergency_close = strategy._emergency_close_symbol

    def spy_close(symbol: str, qty: float) -> None:
        original_emergency_close(symbol, qty)
        with lock:
            closed_symbols.append(symbol)
            if len(closed_symbols) >= expected_count:
                all_done.set()

    strategy._emergency_close_symbol = spy_close  # type: ignore[method-assign]

    with patch("bot_service.strategy.base.asyncio.run", side_effect=fake_run):
        strategy._on_bus_timeout(elapsed=65.0)
        assert all_done.wait(timeout=2.0), f"only {closed_symbols} closed within 2s"

    assert "BTCUSDT" in closed_symbols
    assert "ETHUSDT" in closed_symbols
