from __future__ import annotations

from bot_service.strategy.signals import SignalResult


def funding_rate_arb_signal(
    funding_rate: float, threshold_bps: float = 10.0
) -> SignalResult:
    """Stub — Epic 15 will implement the full funding rate arb logic."""
    return SignalResult(action="hold", confidence=0.0, reason="not_implemented")
