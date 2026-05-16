from __future__ import annotations

from datetime import date

import pytest

from bot_service.strategy.circuit_breaker import DailyLossCircuitBreaker


@pytest.mark.l1
def test_trip_when_loss_exceeds_limit() -> None:
    cb = DailyLossCircuitBreaker(limit_usd=100.0)
    cb.record_pnl(-101.0)
    assert cb.is_tripped() is True


@pytest.mark.l1
def test_no_trip_when_loss_below_limit() -> None:
    cb = DailyLossCircuitBreaker(limit_usd=100.0)
    cb.record_pnl(-99.0)
    assert cb.is_tripped() is False


@pytest.mark.l1
def test_no_trip_on_exactly_limit() -> None:
    cb = DailyLossCircuitBreaker(limit_usd=100.0)
    cb.record_pnl(-100.0)
    assert cb.is_tripped() is False


@pytest.mark.l1
def test_disabled_when_limit_zero() -> None:
    cb = DailyLossCircuitBreaker(limit_usd=0.0)
    cb.record_pnl(-999999.0)
    assert cb.is_tripped() is False


@pytest.mark.l1
def test_reset_for_new_day_clears_accumulator() -> None:
    cb = DailyLossCircuitBreaker(limit_usd=100.0)
    cb.record_pnl(-101.0)
    assert cb.is_tripped() is True

    tomorrow = date(2026, 5, 17)
    cb.reset_for_new_day(tomorrow)
    assert cb.is_tripped() is False
    assert cb.daily_pnl == 0.0


@pytest.mark.l1
def test_reset_for_same_day_is_idempotent() -> None:
    cb = DailyLossCircuitBreaker(limit_usd=100.0)
    cb.record_pnl(-101.0)
    today = date(2026, 5, 16)
    cb.reset_for_new_day(today)
    cb.reset_for_new_day(today)
    assert cb.is_tripped() is False

    # second reset with same date does NOT re-arm
    cb2 = DailyLossCircuitBreaker(limit_usd=100.0)
    cb2.record_pnl(-50.0)
    cb2.reset_for_new_day(today)
    cb2.record_pnl(-60.0)  # accumulated on new day
    # limit 100, -60 < -100 is False
    assert cb2.is_tripped() is False

    cb2.reset_for_new_day(today)  # same day — no reset
    assert cb2.daily_pnl == -60.0


@pytest.mark.l1
def test_cumulative_pnl_accumulates_across_fills() -> None:
    cb = DailyLossCircuitBreaker(limit_usd=50.0)
    cb.record_pnl(-20.0)
    cb.record_pnl(-20.0)
    assert cb.is_tripped() is False
    cb.record_pnl(-11.0)
    assert cb.is_tripped() is True
