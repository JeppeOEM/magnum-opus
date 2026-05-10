from __future__ import annotations

import pandas as pd

from bot_service.strategy.signals import HOLD_INSUFFICIENT, SignalResult


def ma_cross_signal(
    df: pd.DataFrame, fast: int = 20, slow: int = 50
) -> SignalResult:
    """Return buy/sell/hold based on EMA crossover.

    Uses pure pandas ewm() — no pandas_ta import so the function is importable
    with no framework side-effects.  Never raises; all bad-input paths return
    HOLD_INSUFFICIENT.
    """
    if fast < 1 or slow < 1:
        return HOLD_INSUFFICIENT
    if "close" not in df.columns:
        return HOLD_INSUFFICIENT
    # Require slow+1 rows so prev/curr comparison is always valid and the slow
    # EMA has had at least `slow` bars to warm up.
    if len(df) < slow + 1:
        return HOLD_INSUFFICIENT

    close = df["close"]
    if close.isna().all():
        return HOLD_INSUFFICIENT

    fast_ema = close.ewm(span=fast, adjust=False).mean()
    slow_ema = close.ewm(span=slow, adjust=False).mean()

    if fast_ema.isna().iloc[-1] or slow_ema.isna().iloc[-1]:
        return HOLD_INSUFFICIENT

    prev_fast = fast_ema.iloc[-2]
    prev_slow = slow_ema.iloc[-2]
    curr_fast = fast_ema.iloc[-1]
    curr_slow = slow_ema.iloc[-1]

    gap = abs(curr_fast - curr_slow)
    confidence = min(gap / (curr_slow * 0.01 + 1e-9), 1.0) if curr_slow != 0 else 0.0

    crossed_up = prev_fast <= prev_slow and curr_fast > curr_slow
    crossed_down = prev_fast >= prev_slow and curr_fast < curr_slow

    if crossed_up:
        return SignalResult(action="buy", confidence=confidence, reason="ma_cross_up")
    if crossed_down:
        return SignalResult(action="sell", confidence=confidence, reason="ma_cross_down")
    return SignalResult(action="hold", confidence=confidence, reason="no_cross")


# Public alias used by MACrossBot — do NOT copy logic
compute_ma_cross_signal = ma_cross_signal
