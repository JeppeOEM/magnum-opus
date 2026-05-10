from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from bot_service.backtest.commission import BybitCommissionInfo, KuCoinCommissionInfo
from bot_service.backtest.fee_impact import FeeImpactReport, fee_impact_gate

# ── KuCoinCommissionInfo ──────────────────────────────────────────────────────


@pytest.mark.l1
def test_kucoin_taker_fee() -> None:
    ci = KuCoinCommissionInfo(is_maker=False)
    result = ci._getcommission(10, 100.0, False)
    assert result == pytest.approx(1.0)  # 10 × 100 × 0.001


@pytest.mark.l1
def test_kucoin_maker_fee() -> None:
    ci = KuCoinCommissionInfo(is_maker=True)
    result = ci._getcommission(10, 100.0, False)
    assert result == pytest.approx(1.0)  # maker_rate == taker_rate for KuCoin


@pytest.mark.l1
def test_kucoin_configurable_rates() -> None:
    ci = KuCoinCommissionInfo(taker_rate=0.002, is_maker=False)
    result = ci._getcommission(5, 200.0, False)
    assert result == pytest.approx(2.0)  # 5 × 200 × 0.002


# ── BybitCommissionInfo ───────────────────────────────────────────────────────


@pytest.mark.l1
def test_bybit_taker_fee() -> None:
    ci = BybitCommissionInfo(is_maker=False)
    result = ci._getcommission(5, 200.0, False)
    assert result == pytest.approx(0.6)  # 5 × 200 × 0.0006


@pytest.mark.l1
def test_bybit_maker_rebate() -> None:
    ci = BybitCommissionInfo(is_maker=True)
    result = ci._getcommission(5, 200.0, False)
    assert result == pytest.approx(-0.1)  # 5 × 200 × (-0.0001)


@pytest.mark.l1
def test_bybit_funding_cost_applied() -> None:
    ts = datetime(2024, 1, 1, 16, 0, 0, tzinfo=timezone.utc)
    rates = pd.Series(
        [0.0001],
        index=pd.DatetimeIndex([pd.Timestamp("2024-01-01 16:00:00", tz="UTC")]),
    )
    ci = BybitCommissionInfo(funding_rates=rates)
    cost = ci.get_funding_cost(ts, 10.0, 50000.0)
    assert cost == pytest.approx(50.0)  # 10 × 50000 × 0.0001


@pytest.mark.l1
def test_bybit_funding_cost_missing_returns_zero() -> None:
    ci = BybitCommissionInfo()  # no funding_rates
    ts = datetime(2024, 1, 1, 16, 0, 0, tzinfo=timezone.utc)
    cost = ci.get_funding_cost(ts, 10.0, 50000.0)
    assert cost == 0.0


@pytest.mark.l1
def test_bybit_funding_cost_before_series_returns_zero() -> None:
    ts = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)  # before any rate entry
    rates = pd.Series(
        [0.0001],
        index=pd.DatetimeIndex([pd.Timestamp("2024-01-01 08:00:00", tz="UTC")]),
    )
    ci = BybitCommissionInfo(funding_rates=rates)
    cost = ci.get_funding_cost(ts, 10.0, 50000.0)
    assert cost == 0.0  # asof returns NaN before first entry


# ── FeeImpactReport / fee_impact_gate ────────────────────────────────────────


@pytest.mark.l1
def test_fee_impact_gate_passes() -> None:
    ci = KuCoinCommissionInfo()  # taker_rate=0.001
    signals = pd.Series([0.005, 0.006, 0.004])  # mean=0.005
    report = fee_impact_gate(signals, ci, expected_slippage_bps=2.0)
    # required = 2 × (0.001 + 0.0002) = 0.0024
    assert report.passes is True
    assert report.required_edge == pytest.approx(0.0024)
    assert report.mean_signal_edge == pytest.approx(0.005)
    assert report.margin == pytest.approx(0.005 - 0.0024)


@pytest.mark.l1
def test_fee_impact_gate_fails() -> None:
    ci = BybitCommissionInfo()  # taker_rate=0.0006
    signals = pd.Series([0.001, 0.001, 0.001])  # mean=0.001
    report = fee_impact_gate(signals, ci, expected_slippage_bps=3.0)
    # required = 2 × (0.0006 + 0.0003) = 0.0018
    assert report.passes is False
    assert report.margin < 0.0


@pytest.mark.l1
def test_fee_impact_report_is_frozen() -> None:
    report = FeeImpactReport(passes=True, required_edge=0.002, mean_signal_edge=0.005, margin=0.003)
    with pytest.raises((AttributeError, TypeError)):
        report.passes = False  # type: ignore[misc]


@pytest.mark.l1
def test_fee_impact_gate_raises_on_empty_signals() -> None:
    ci = KuCoinCommissionInfo()
    with pytest.raises(ValueError, match="no valid values"):
        fee_impact_gate(pd.Series([], dtype=float), ci, expected_slippage_bps=2.0)


@pytest.mark.l1
def test_fee_impact_gate_raises_on_all_nan_signals() -> None:
    ci = KuCoinCommissionInfo()
    with pytest.raises(ValueError, match="no valid values"):
        fee_impact_gate(pd.Series([float("nan"), float("nan")]), ci, expected_slippage_bps=2.0)


@pytest.mark.l1
def test_fee_impact_gate_raises_on_negative_slippage() -> None:
    ci = KuCoinCommissionInfo()
    with pytest.raises(ValueError, match="non-negative"):
        fee_impact_gate(pd.Series([0.005]), ci, expected_slippage_bps=-1.0)


@pytest.mark.l1
def test_bybit_funding_warn_logged(capsys: pytest.CaptureFixture[str]) -> None:
    """AC2: structlog WARN is emitted when funding rate is unavailable for a timestamp."""
    ts = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)  # before first entry
    rates = pd.Series(
        [0.0001],
        index=pd.DatetimeIndex([pd.Timestamp("2024-01-01 08:00:00", tz="UTC")]),
    )
    ci = BybitCommissionInfo(funding_rates=rates)
    cost = ci.get_funding_cost(ts, 10.0, 50000.0)
    captured = capsys.readouterr()
    assert cost == 0.0
    assert "funding_rate_not_found" in captured.out
