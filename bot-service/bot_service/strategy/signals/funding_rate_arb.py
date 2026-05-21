from __future__ import annotations

from bot_service.strategy.signals import SignalResult


def funding_rate_arb_signal(
    funding_rate: float, threshold_bps: float = 10.0
) -> SignalResult:
    """Signal based on perpetual funding rate vs threshold.

    Positive funding → longs pay shorts → go short to collect.
    Negative funding → shorts pay longs → go long to collect.
    """
    if threshold_bps <= 0:
        return SignalResult(action="hold", confidence=0.0, reason="invalid_threshold")
    threshold = threshold_bps / 10_000
    if funding_rate > threshold:
        confidence = min(funding_rate / threshold, 1.0)
        return SignalResult(action="sell", confidence=confidence, reason="high_positive_funding")
    if funding_rate < -threshold:
        confidence = min(abs(funding_rate) / threshold, 1.0)
        return SignalResult(action="buy", confidence=confidence, reason="high_negative_funding")
    return SignalResult(action="hold", confidence=0.0, reason="rate_below_threshold")
