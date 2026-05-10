from __future__ import annotations

import json
import pathlib

import pandas as pd
import pytest

from bot_service.backtest.commission import KuCoinCommissionInfo
from bot_service.backtest.fee_impact import FeeImpactReport, run_fee_impact_check


@pytest.mark.l1
def test_passes_for_large_edge(tmp_path: pathlib.Path) -> None:
    signals = pd.Series([0.05] * 100)
    ci = KuCoinCommissionInfo(is_maker=False)
    report = run_fee_impact_check("TestStrategy", signals, ci, 5.0, output_dir=tmp_path)
    assert report.passes is True
    assert report.margin > 0


@pytest.mark.l1
def test_fails_for_insufficient_edge(tmp_path: pathlib.Path) -> None:
    signals = pd.Series([0.001] * 100)
    ci = KuCoinCommissionInfo(is_maker=False)
    report = run_fee_impact_check("TestStrategy", signals, ci, 5.0, output_dir=tmp_path)
    assert report.passes is False
    assert report.margin < 0


@pytest.mark.l1
def test_report_written_to_correct_path(tmp_path: pathlib.Path) -> None:
    signals = pd.Series([0.05] * 50)
    ci = KuCoinCommissionInfo(is_maker=False)
    run_fee_impact_check("MyBot", signals, ci, 0.0, output_dir=tmp_path)
    report_path = tmp_path / "MyBot" / "fee_impact.json"
    assert report_path.exists()
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert set(data.keys()) == {"passes", "required_edge", "mean_signal_edge", "margin"}
    assert isinstance(data["passes"], bool)
    assert isinstance(data["required_edge"], float)
    assert isinstance(data["mean_signal_edge"], float)
    assert isinstance(data["margin"], float)


@pytest.mark.l1
def test_empty_signals_raises(tmp_path: pathlib.Path) -> None:
    ci = KuCoinCommissionInfo(is_maker=False)
    with pytest.raises(ValueError, match="no valid values"):
        run_fee_impact_check("TestStrategy", pd.Series(dtype=float), ci, 0.0, output_dir=tmp_path)


@pytest.mark.l1
def test_negative_slippage_raises(tmp_path: pathlib.Path) -> None:
    ci = KuCoinCommissionInfo(is_maker=False)
    with pytest.raises(ValueError, match="non-negative"):
        run_fee_impact_check("TestStrategy", pd.Series([0.05] * 10), ci, -1.0, output_dir=tmp_path)
