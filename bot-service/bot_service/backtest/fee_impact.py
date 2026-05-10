from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class FeeImpactReport:
    passes: bool
    required_edge: float
    mean_signal_edge: float
    margin: float  # mean_signal_edge - required_edge


def fee_impact_gate(
    strategy_signals: pd.Series,
    commission_info: object,
    expected_slippage_bps: float,
) -> FeeImpactReport:
    """Gate: mean signal edge must exceed round-trip fee + slippage cost.

    required_edge = 2 × (taker_fee_rate + slippage_bps / 10_000)
    strategy_signals: per-trade expected edge as a fraction of trade value.
    """
    if expected_slippage_bps < 0:
        raise ValueError(f"expected_slippage_bps must be non-negative, got {expected_slippage_bps}")
    if strategy_signals.empty or strategy_signals.isna().all():
        raise ValueError("strategy_signals has no valid values")
    params = getattr(commission_info, "p", None)
    taker_rate = float(getattr(params, "taker_rate", 0.0))
    required_edge = 2.0 * (taker_rate + expected_slippage_bps / 10_000.0)
    mean_signal_edge = float(strategy_signals.mean())
    margin = mean_signal_edge - required_edge
    return FeeImpactReport(
        passes=mean_signal_edge > required_edge,
        required_edge=required_edge,
        mean_signal_edge=mean_signal_edge,
        margin=margin,
    )
