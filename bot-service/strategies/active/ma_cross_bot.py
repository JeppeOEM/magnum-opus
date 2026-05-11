from __future__ import annotations

import pandas as pd
import structlog

from bot_service.exchange import OrderRequest
from bot_service.strategy.base import BaseStrategy
from bot_service.strategy.signals.ma_cross import compute_ma_cross_signal

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

    def subscribe(self) -> None:
        self.get_history(_SYMBOL, _TF, self.min_lookback)
        self.register_bar_handler(_SYMBOL, _TF, self._on_bar)

    def _on_bar(self, df: pd.DataFrame) -> None:
        if self._signal_invalid.get(_SYMBOL, False):
            return
        result = compute_ma_cross_signal(df, fast=20, slow=50)
        if result.action == "hold":
            return
        side = "buy" if result.action == "buy" else "sell"
        if not self._exchange:
            log.warning("exchange_not_injected_dropping_order", strategy=self._name, side=side)
            return
        req = OrderRequest(
            strategy=self._name,
            exchange=self._exchange,
            symbol=_SYMBOL,
            side=side,
            order_type="market",
            order_role="entry",
            size=self.max_position_pct,
            paper_trading=self.paper_trading,
        )
        log.info(
            "ma_cross_bot_signal",
            side=side,
            confidence=result.confidence,
            reason=result.reason,
        )
        if self._order_worker is None:
            log.warning("order_worker_not_injected_dropping_order", strategy=self._name, side=side)
            return
        self._order_worker.post(req)  # type: ignore[attr-defined]
