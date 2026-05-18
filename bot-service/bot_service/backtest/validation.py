from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from typing import Any

import backtrader as bt
import numpy as np
import pandas as pd

from bot_service.backtest.fee_impact import FeeImpactReport


@dataclass(frozen=True)
class FoldResult:
    fold: int
    sharpe: float
    max_drawdown: float
    pnl_degradation: float
    oos_pnl: float = 0.0


@dataclass(frozen=True)
class WalkForwardReport:
    folds: tuple[FoldResult, ...]
    mean_sharpe: float
    mean_drawdown: float
    mean_degradation: float


@dataclass(frozen=True)
class StressWindowResult:
    name: str
    start: str
    end: str
    sharpe: float
    max_drawdown: float


@dataclass(frozen=True)
class StressTestReport:
    windows: tuple[StressWindowResult, ...]


@dataclass
class ValidationReport:
    passes: bool
    fee_gate_ran: bool
    fee_gate_passed: bool | None
    walk_forward_ran: bool
    stress_test_ran: bool
    monte_carlo_ran: bool
    mean_sharpe: float
    mean_drawdown: float
    mean_degradation: float
    monte_carlo_5th_pct: float
    sharpe_ok: bool
    drawdown_ok: bool
    degradation_ok: bool
    monte_carlo_ok: bool
    stress_drawdown_ok: bool
    worst_stress_drawdown: float
    min_sharpe: float
    max_drawdown_threshold: float
    max_degradation: float
    stress_max_drawdown_threshold: float

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), default=str)


def _partition_dataframe(
    df: pd.DataFrame,
    n_splits: int,
) -> list[tuple[pd.DataFrame, pd.DataFrame]]:
    """Split df into n_splits IS/OOS fold pairs using expanding window.

    fold_size = len(df) // (n_splits + 1)
    Fold k (0-indexed): IS = df[:fold_size*(k+1)], OOS = df[fold_size*(k+1):fold_size*(k+2)]
    """
    n = len(df)
    fold_size = n // (n_splits + 1)
    if fold_size == 0:
        raise ValueError(
            f"DataFrame too small for n_splits={n_splits}: need at least {n_splits + 1} rows, got {n}"
        )
    folds = []
    for k in range(n_splits):
        is_end = fold_size * (k + 1)
        oos_end = fold_size * (k + 2)
        folds.append((df.iloc[:is_end], df.iloc[is_end:oos_end]))
    return folds


def _run_cerebro_full(
    strategy_cls: type,
    df: pd.DataFrame,
    commission_info: Any | None,
    starting_cash: float,
) -> tuple[float, float, float]:
    """Run cerebro on df, return (sharpe, max_drawdown_ratio, net_pnl)."""
    cerebro = bt.Cerebro()
    feed = bt.feeds.PandasData(dataname=df)
    cerebro.adddata(feed)
    cerebro.addstrategy(strategy_cls)
    cerebro.broker.setcash(starting_cash)
    if commission_info is not None:
        cerebro.broker.addcommissioninfo(commission_info)
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe", timeframe=bt.TimeFrame.Days)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    results = cerebro.run()
    strat = results[0]

    sharpe_analysis = strat.analyzers.sharpe.get_analysis()
    sharpe = sharpe_analysis.get("sharperatio") or 0.0

    dd_analysis = strat.analyzers.drawdown.get_analysis()
    max_dd_pct = dd_analysis.get("max", {}).get("drawdown", 0.0)
    max_dd = max_dd_pct / 100.0

    trades_analysis = strat.analyzers.trades.get_analysis()
    total_closed = trades_analysis.get("total", {}).get("closed", 0)
    net_pnl = (
        trades_analysis.get("pnl", {}).get("net", {}).get("total", 0.0)
        if total_closed > 0
        else 0.0
    )
    return float(sharpe), float(max_dd), float(net_pnl)


def _run_cerebro_pnl(
    strategy_cls: type,
    df: pd.DataFrame,
    commission_info: Any | None,
    starting_cash: float,
) -> float:
    """Run cerebro on df, return only net P&L."""
    _, _, pnl = _run_cerebro_full(strategy_cls, df, commission_info, starting_cash)
    return pnl


def run_walk_forward(
    strategy_cls: type,
    feed: Any,
    n_splits: int = 3,
    commission_info: Any | None = None,
    starting_cash: float = 10_000.0,
) -> WalkForwardReport:
    df: pd.DataFrame = feed.p.dataname
    folds_data = _partition_dataframe(df, n_splits)
    fold_results = []
    for fold_idx, (is_df, oos_df) in enumerate(folds_data):
        is_pnl = _run_cerebro_pnl(strategy_cls, is_df, commission_info, starting_cash)
        oos_sharpe, oos_dd, oos_pnl = _run_cerebro_full(
            strategy_cls, oos_df, commission_info, starting_cash
        )
        degradation = (1.0 - oos_pnl / is_pnl) if is_pnl != 0.0 else 0.0
        fold_results.append(
            FoldResult(
                fold=fold_idx,
                sharpe=oos_sharpe,
                max_drawdown=oos_dd,
                pnl_degradation=degradation,
                oos_pnl=oos_pnl,
            )
        )
    folds_tuple = tuple(fold_results)
    n = len(folds_tuple)
    mean_sharpe = sum(f.sharpe for f in folds_tuple) / n if n else 0.0
    mean_drawdown = sum(f.max_drawdown for f in folds_tuple) / n if n else 0.0
    mean_degradation = sum(f.pnl_degradation for f in folds_tuple) / n if n else 0.0
    return WalkForwardReport(
        folds=folds_tuple,
        mean_sharpe=mean_sharpe,
        mean_drawdown=mean_drawdown,
        mean_degradation=mean_degradation,
    )


def run_stress_test(
    strategy_cls: type,
    feed: Any,
    windows: list[tuple[str, str, str]] | None = None,
    commission_info: Any | None = None,
    starting_cash: float = 10_000.0,
) -> StressTestReport:
    """Run strategy on caller-defined stress windows.

    windows: list of (name, start_date, end_date) tuples; dates as ISO strings (e.g. "2024-01-05").
    Returns StressTestReport with per-window Sharpe and max drawdown.
    """
    df: pd.DataFrame = feed.p.dataname
    if windows is None:
        windows = []

    window_results = []
    for name, start, end in windows:
        ts_start = pd.Timestamp(start, tz="UTC")
        ts_end = pd.Timestamp(end, tz="UTC")
        mask = (df.index >= ts_start) & (df.index <= ts_end)
        window_df = df.loc[mask]
        if window_df.empty:
            window_results.append(
                StressWindowResult(name=name, start=start, end=end, sharpe=0.0, max_drawdown=0.0)
            )
            continue
        sharpe, max_dd, _ = _run_cerebro_full(strategy_cls, window_df, commission_info, starting_cash)
        window_results.append(
            StressWindowResult(name=name, start=start, end=end, sharpe=sharpe, max_drawdown=max_dd)
        )
    return StressTestReport(windows=tuple(window_results))


def run_monte_carlo(
    trade_pnls: list[float] | pd.Series,
    n_shuffles: int = 10_000,
    seed: int | None = None,
) -> float:
    """Bootstrap-resample trade P&L n_shuffles times; return 5th-percentile path P&L.

    Samples N trades with replacement per path (bootstrap), so each simulation
    represents a plausible alternative sequence of outcomes drawn from the
    empirical distribution.  Unlike simple shuffling (which is commutative and
    always sums to the same total), bootstrap resampling produces genuinely
    different path totals and captures tail risk.
    """
    pnls = np.array(list(trade_pnls), dtype=float)
    if len(pnls) == 0 or n_shuffles == 0:
        return 0.0
    rng = np.random.default_rng(seed)
    samples = rng.choice(pnls, size=(n_shuffles, len(pnls)), replace=True)
    path_totals = samples.sum(axis=1)
    return float(np.percentile(path_totals, 5))


def generate_validation_report(
    walk_forward: WalkForwardReport | None,
    stress: StressTestReport | None,
    monte_carlo_pct5: float | None,
    fee_gate: FeeImpactReport | None = None,
    min_sharpe: float = 1.0,
    max_drawdown_threshold: float = 0.15,
    max_degradation: float = 0.30,
    stress_max_drawdown_threshold: float = 0.30,
) -> ValidationReport:
    fee_gate_ran = fee_gate is not None
    fee_gate_passed: bool | None = fee_gate.passes if fee_gate is not None else None

    # Stress test drawdown gate — worst window must not exceed threshold
    worst_stress_dd = (
        max((w.max_drawdown for w in stress.windows), default=0.0)
        if stress is not None
        else 0.0
    )
    stress_drawdown_ok = worst_stress_dd <= stress_max_drawdown_threshold

    if fee_gate_ran and not fee_gate_passed:
        return ValidationReport(
            passes=False,
            fee_gate_ran=True,
            fee_gate_passed=False,
            walk_forward_ran=False,
            stress_test_ran=stress is not None,
            monte_carlo_ran=False,
            mean_sharpe=0.0,
            mean_drawdown=0.0,
            mean_degradation=0.0,
            monte_carlo_5th_pct=0.0,
            sharpe_ok=False,
            drawdown_ok=False,
            degradation_ok=False,
            monte_carlo_ok=False,
            stress_drawdown_ok=stress_drawdown_ok,
            worst_stress_drawdown=worst_stress_dd,
            min_sharpe=min_sharpe,
            max_drawdown_threshold=max_drawdown_threshold,
            max_degradation=max_degradation,
            stress_max_drawdown_threshold=stress_max_drawdown_threshold,
        )

    mean_sharpe = walk_forward.mean_sharpe if walk_forward else 0.0
    mean_drawdown = walk_forward.mean_drawdown if walk_forward else 0.0
    mean_degradation = walk_forward.mean_degradation if walk_forward else 0.0
    mc_pct5 = monte_carlo_pct5 if monte_carlo_pct5 is not None else 0.0

    sharpe_ok = mean_sharpe >= min_sharpe
    drawdown_ok = mean_drawdown <= max_drawdown_threshold
    degradation_ok = mean_degradation <= max_degradation
    monte_carlo_ok = mc_pct5 > 0.0

    return ValidationReport(
        passes=all([sharpe_ok, drawdown_ok, degradation_ok, monte_carlo_ok, stress_drawdown_ok]),
        fee_gate_ran=fee_gate_ran,
        fee_gate_passed=fee_gate_passed,
        walk_forward_ran=walk_forward is not None,
        stress_test_ran=stress is not None,
        monte_carlo_ran=monte_carlo_pct5 is not None,
        mean_sharpe=mean_sharpe,
        mean_drawdown=mean_drawdown,
        mean_degradation=mean_degradation,
        monte_carlo_5th_pct=mc_pct5,
        sharpe_ok=sharpe_ok,
        drawdown_ok=drawdown_ok,
        degradation_ok=degradation_ok,
        monte_carlo_ok=monte_carlo_ok,
        stress_drawdown_ok=stress_drawdown_ok,
        worst_stress_drawdown=worst_stress_dd,
        min_sharpe=min_sharpe,
        max_drawdown_threshold=max_drawdown_threshold,
        max_degradation=max_degradation,
        stress_max_drawdown_threshold=stress_max_drawdown_threshold,
    )
