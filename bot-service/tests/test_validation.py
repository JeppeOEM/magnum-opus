from __future__ import annotations

import json

import pandas as pd
import pytest

from bot_service.backtest.fee_impact import FeeImpactReport
from bot_service.backtest.validation import (
    ValidationReport,
    _partition_dataframe,
    generate_validation_report,
    run_monte_carlo,
)


def _make_df(n: int) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="s", tz="UTC")
    return pd.DataFrame({"close": range(n)}, index=idx)


# ── Partition tests ───────────────────────────────────────────────────────────


@pytest.mark.l1
def test_partition_dataframe_fold_sizes() -> None:
    df = _make_df(100)
    folds = _partition_dataframe(df, n_splits=3)
    assert len(folds) == 3
    # fold_size = 100 // 4 = 25
    is0, oos0 = folds[0]
    assert len(is0) == 25
    assert len(oos0) == 25

    is1, oos1 = folds[1]
    assert len(is1) == 50
    assert len(oos1) == 25

    is2, oos2 = folds[2]
    assert len(is2) == 75
    assert len(oos2) == 25


@pytest.mark.l1
def test_partition_dataframe_index_preserved() -> None:
    df = _make_df(100)
    folds = _partition_dataframe(df, n_splits=2)
    is0, oos0 = folds[0]
    # Original index is preserved (not reset)
    assert is0.index[0] == df.index[0]
    assert oos0.index[0] == df.index[len(is0)]


# ── Monte Carlo tests ─────────────────────────────────────────────────────────


@pytest.mark.l1
def test_monte_carlo_constant_pnl() -> None:
    # All trades are +1.0, any shuffle sums to 10.0 — 5th pct must be 10.0
    result = run_monte_carlo([1.0] * 10, n_shuffles=100, seed=42)
    assert result == pytest.approx(10.0)


@pytest.mark.l1
def test_monte_carlo_mixed_pnl_fifth_pct_negative() -> None:
    # -5.0 dominates, 5th percentile should be negative
    result = run_monte_carlo([1.0, -5.0], n_shuffles=1000, seed=0)
    assert result < 0.0


# ── ValidationReport tests ────────────────────────────────────────────────────


def _make_walk_forward(
    sharpe: float = 1.5,
    drawdown: float = 0.10,
    degradation: float = 0.20,
) -> object:
    """Minimal WalkForwardReport-like object for testing generate_validation_report."""
    from bot_service.backtest.validation import FoldResult, WalkForwardReport

    fold = FoldResult(fold=0, sharpe=sharpe, max_drawdown=drawdown, pnl_degradation=degradation)
    return WalkForwardReport(
        folds=(fold,),
        mean_sharpe=sharpe,
        mean_drawdown=drawdown,
        mean_degradation=degradation,
    )


@pytest.mark.l1
def test_validation_report_passes_when_all_thresholds_met() -> None:
    wf = _make_walk_forward(sharpe=1.5, drawdown=0.10, degradation=0.20)
    report = generate_validation_report(
        walk_forward=wf,  # type: ignore[arg-type]
        stress=None,
        monte_carlo_pct5=5.0,
        fee_gate=None,
    )
    assert report.passes is True
    assert report.sharpe_ok is True
    assert report.drawdown_ok is True
    assert report.degradation_ok is True
    assert report.monte_carlo_ok is True


@pytest.mark.l1
def test_validation_report_fails_when_sharpe_below_threshold() -> None:
    wf = _make_walk_forward(sharpe=0.5, drawdown=0.10, degradation=0.20)
    report = generate_validation_report(
        walk_forward=wf,  # type: ignore[arg-type]
        stress=None,
        monte_carlo_pct5=5.0,
        fee_gate=None,
    )
    assert report.passes is False
    assert report.sharpe_ok is False


@pytest.mark.l1
def test_validation_report_fee_gate_failed_skips_rest() -> None:
    fee_gate = FeeImpactReport(
        passes=False,
        required_edge=0.005,
        mean_signal_edge=0.001,
        margin=-0.004,
    )
    report = generate_validation_report(
        walk_forward=None,
        stress=None,
        monte_carlo_pct5=None,
        fee_gate=fee_gate,
    )
    assert report.passes is False
    assert report.fee_gate_ran is True
    assert report.fee_gate_passed is False
    assert report.walk_forward_ran is False
    assert report.stress_test_ran is False
    assert report.monte_carlo_ran is False


@pytest.mark.l1
def test_validation_report_json_roundtrip() -> None:
    report = ValidationReport(
        passes=True,
        fee_gate_ran=False,
        fee_gate_passed=None,
        walk_forward_ran=True,
        stress_test_ran=False,
        monte_carlo_ran=True,
        mean_sharpe=1.5,
        mean_drawdown=0.10,
        mean_degradation=0.20,
        monte_carlo_5th_pct=5.0,
        sharpe_ok=True,
        drawdown_ok=True,
        degradation_ok=True,
        monte_carlo_ok=True,
        stress_drawdown_ok=True,
        worst_stress_drawdown=0.0,
        min_sharpe=1.0,
        max_drawdown_threshold=0.15,
        max_degradation=0.30,
        stress_max_drawdown_threshold=0.30,
    )
    json_str = report.to_json()
    data = json.loads(json_str)
    assert data["passes"] is True
    assert data["mean_sharpe"] == pytest.approx(1.5)
    assert data["fee_gate_passed"] is None
    assert data["monte_carlo_5th_pct"] == pytest.approx(5.0)
    assert data["stress_drawdown_ok"] is True
    assert data["worst_stress_drawdown"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Story 29-2: stress test drawdown gates ValidationReport.passes
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_stress_drawdown_exceeds_threshold_fails_passes() -> None:
    """Worst stress window drawdown above threshold → passes=False, stress_drawdown_ok=False."""
    from bot_service.backtest.validation import StressTestReport, StressWindowResult

    wf = _make_walk_forward(sharpe=2.0, drawdown=0.05, degradation=0.10)
    stress = StressTestReport(windows=(
        StressWindowResult(name="luna", start="2022-05-01", end="2022-05-31",
                           sharpe=0.5, max_drawdown=0.60),  # exceeds 0.30
    ))
    report = generate_validation_report(
        walk_forward=wf,  # type: ignore[arg-type]
        stress=stress,
        monte_carlo_pct5=10.0,
        fee_gate=None,
        stress_max_drawdown_threshold=0.30,
    )
    assert report.passes is False
    assert report.stress_drawdown_ok is False
    assert report.worst_stress_drawdown == pytest.approx(0.60)


@pytest.mark.l1
def test_stress_drawdown_under_threshold_does_not_fail() -> None:
    """Worst stress window drawdown below threshold → stress_drawdown_ok=True."""
    from bot_service.backtest.validation import StressTestReport, StressWindowResult

    wf = _make_walk_forward(sharpe=2.0, drawdown=0.05, degradation=0.10)
    stress = StressTestReport(windows=(
        StressWindowResult(name="luna", start="2022-05-01", end="2022-05-31",
                           sharpe=0.5, max_drawdown=0.20),  # under 0.30
    ))
    report = generate_validation_report(
        walk_forward=wf,  # type: ignore[arg-type]
        stress=stress,
        monte_carlo_pct5=10.0,
        fee_gate=None,
        min_sharpe=0.5,  # lower threshold so walk-forward passes
        stress_max_drawdown_threshold=0.30,
    )
    assert report.stress_drawdown_ok is True
    assert report.worst_stress_drawdown == pytest.approx(0.20)


@pytest.mark.l1
def test_no_stress_run_does_not_fail_passes() -> None:
    """When stress=None, stress_drawdown_ok=True (not penalised for not running)."""
    wf = _make_walk_forward(sharpe=2.0, drawdown=0.05, degradation=0.10)
    report = generate_validation_report(
        walk_forward=wf,  # type: ignore[arg-type]
        stress=None,
        monte_carlo_pct5=10.0,
        fee_gate=None,
        min_sharpe=0.5,
    )
    assert report.stress_drawdown_ok is True
    assert report.worst_stress_drawdown == pytest.approx(0.0)
