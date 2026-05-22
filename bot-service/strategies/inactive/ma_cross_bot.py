from __future__ import annotations

import pandas as pd
import structlog

STRATEGY_GROUP = "Trend Following"
STRATEGY_TAGS = ["ma", "crossover", "paper", "bybit", "btc"]

from bot_service.exchange import OrderRequest
from bot_service.strategy.base import BaseStrategy
from bot_service.strategy.signals.ma_cross import compute_ma_cross_signal
from bot_service.bus.event_types import GapMarker

log = structlog.get_logger()

_SYMBOL = "BTCUSDT"
_TF = "1m"


class MACrossBot(BaseStrategy):
    """EMA crossover baseline strategy — paper mode only."""

    @property
    def min_lookback(self) -> int:
        return 50

    @property
    def max_position_pct(self) -> float:
        return 0.05

    @property
    def stop_loss_pct(self) -> float:
        return 0.03

    @property
    def paper_trading(self) -> bool:
        return True

    @property
    def bus_timeout_seconds(self) -> int:
        return 300

    @property
    def close_on_bus_timeout(self) -> bool:
        return False

    @property
    def orderbook_mode(self) -> str:
        return "none"

    def __init__(self, name: str, settings: object) -> None:
        super().__init__(name, settings)  # type: ignore[arg-type]
        self._current_side: str | None = None

    def subscribe(self) -> None:
        self.get_history(_SYMBOL, _TF, self.min_lookback)
        self.register_bar_handler(_SYMBOL, _TF, self._on_bar)

    def handle_gap(self, gap: GapMarker) -> None:
        self._current_side = None
        super().handle_gap(gap)

    def _post(self, side: str, role: str) -> None:
        req = OrderRequest(
            strategy=self._name,
            exchange=self._exchange,
            symbol=_SYMBOL,
            side=side,
            order_type="market",
            order_role=role,
            size=self.max_position_pct,
            paper_trading=self.paper_trading,
        )
        self._order_worker.post(req)  # type: ignore[attr-defined]

    def _on_bar(self, df: pd.DataFrame) -> None:
        if self._signal_invalid.get(_SYMBOL, False):
            return
        result = compute_ma_cross_signal(df, fast=20, slow=50)
        if result.action == "hold":
            return

        action = result.action  # "buy" or "sell"
        target_side = "long" if action == "buy" else "short"

        if self._current_side == target_side:
            return  # already in the desired position

        if not self._exchange:
            log.warning("exchange_not_injected_dropping_order", strategy=self._name, action=action)
            return
        if self._order_worker is None:
            log.warning("order_worker_not_injected_dropping_order", strategy=self._name, action=action)
            return

        log.info(
            "ma_cross_bot_signal",
            action=action,
            confidence=result.confidence,
            reason=result.reason,
            current_side=self._current_side,
        )

        # Close opposite position first
        if self._current_side == "long":
            self._post("sell", "exit")
            self._current_side = None
        elif self._current_side == "short":
            self._post("buy", "exit")
            self._current_side = None

        # Open new position
        entry_side = "buy" if action == "buy" else "sell"
        self._post(entry_side, "entry")
        self._current_side = target_side
