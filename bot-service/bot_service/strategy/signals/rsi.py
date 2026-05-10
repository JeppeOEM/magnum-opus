from __future__ import annotations

import pandas as pd

from bot_service.strategy.signals import HOLD_INSUFFICIENT, SignalResult


def rsi_signal(
    df: pd.DataFrame,
    period: int = 14,
    oversold: float = 30.0,
    overbought: float = 70.0,
) -> SignalResult:
    """Buy when RSI crosses up through oversold; sell when it crosses down through overbought.

    Reads the pre-computed ``RSI_{period}`` column appended by pandas-ta via
    ``BaseStrategy.add_indicators``.  Never raises; all bad-input paths return
    HOLD_INSUFFICIENT.
    """
    col = f"RSI_{period}"
    if col not in df.columns:
        return HOLD_INSUFFICIENT
    if len(df) < period + 1:
        return HOLD_INSUFFICIENT

    rsi = df[col]
    if rsi.isna().all():
        return HOLD_INSUFFICIENT

    prev = rsi.iloc[-2]
    curr = rsi.iloc[-1]
    if pd.isna(prev) or pd.isna(curr):
        return HOLD_INSUFFICIENT

    # Crossed up through oversold threshold: oversold recovery → buy
    if prev < oversold <= curr:
        confidence = min((curr - prev) / (oversold + 1e-9), 1.0)
        return SignalResult(action="buy", confidence=confidence, reason="rsi_oversold_recovery")

    # Crossed down through overbought threshold: overbought reversal → sell
    if prev > overbought >= curr:
        confidence = min((prev - curr) / (100.0 - overbought + 1e-9), 1.0)
        return SignalResult(action="sell", confidence=confidence, reason="rsi_overbought_reversal")

    return SignalResult(action="hold", confidence=0.0, reason="rsi_no_cross")
