from __future__ import annotations

import math

import pandas as pd

from bot_service.strategy.signals import HOLD_INSUFFICIENT, SignalResult


def ofi_signal(
    df: pd.DataFrame, lookback: int = 30, threshold: float = 0.6
) -> SignalResult:
    """Return buy/sell/hold based on rolling z-score of the OFI column.

    Never raises; all bad-input paths return HOLD_INSUFFICIENT.
    """
    if lookback < 2:
        return HOLD_INSUFFICIENT
    if "ofi" not in df.columns:
        return HOLD_INSUFFICIENT
    # Guard against trailing NaN — stale non-null data must not produce signals.
    if pd.isna(df["ofi"].iloc[-1]):
        return HOLD_INSUFFICIENT

    ofi = df["ofi"].dropna()
    if len(ofi) < lookback:
        return HOLD_INSUFFICIENT

    recent = ofi.iloc[-lookback:]
    # Replace Inf values that would produce NaN in mean/std
    recent = recent.replace([math.inf, -math.inf], float("nan")).dropna()
    if len(recent) < lookback:
        return HOLD_INSUFFICIENT

    mean = recent.mean()
    std = recent.std()

    # ddof=1 std of a 1-element series is NaN; guard handles both zero and NaN.
    if not (std > 0):
        return SignalResult(action="hold", confidence=0.0, reason="zero_variance")

    z = float((ofi.iloc[-1] - mean) / std)
    confidence = min(abs(z) / (threshold * 2), 1.0)

    if z > threshold:
        return SignalResult(action="buy", confidence=confidence, reason=f"ofi_z={z:.2f}")
    if z < -threshold:
        return SignalResult(action="sell", confidence=confidence, reason=f"ofi_z={z:.2f}")
    return SignalResult(action="hold", confidence=confidence, reason=f"ofi_z={z:.2f}")
