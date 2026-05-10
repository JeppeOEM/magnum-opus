from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SignalResult:
    action: Literal["buy", "sell", "hold"]
    confidence: float
    reason: str

    def __post_init__(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be in [0.0, 1.0], got {self.confidence}")


HOLD_INSUFFICIENT = SignalResult(action="hold", confidence=0.0, reason="insufficient_data")
