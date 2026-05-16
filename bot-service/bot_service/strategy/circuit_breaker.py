from __future__ import annotations

import threading
from datetime import date


class DailyLossCircuitBreaker:
    """Service-level daily loss limit circuit breaker. Thread-safe.

    Disabled when limit_usd == 0.0. Records realized PnL from fills and trips
    when cumulative daily losses exceed abs(limit_usd). Resets at UTC midnight.
    """

    def __init__(self, limit_usd: float) -> None:
        self._limit_usd = limit_usd
        self._daily_pnl: float = 0.0
        self._tracking_date: date | None = None
        self._lock = threading.Lock()

    @property
    def daily_pnl(self) -> float:
        with self._lock:
            return self._daily_pnl

    def record_pnl(self, realized_pnl: float) -> None:
        with self._lock:
            self._daily_pnl += realized_pnl

    def is_tripped(self) -> bool:
        if self._limit_usd == 0.0:
            return False
        with self._lock:
            return self._daily_pnl < -abs(self._limit_usd)

    def reset_for_new_day(self, today: date) -> None:
        with self._lock:
            if today != self._tracking_date:
                self._daily_pnl = 0.0
                self._tracking_date = today
