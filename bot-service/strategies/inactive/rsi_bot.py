from __future__ import annotations

import pandas as pd
import structlog

from bot_service.exchange import OrderRequest
from bot_service.strategy.base import BaseStrategy
from bot_service.strategy.signals.rsi import rsi_signal

log = structlog.get_logger()

_SYMBOL = "BTCUSDT"
_TF = "1m"
_RSI_PERIOD = 14
_OVERSOLD = 30.0
_OVERBOUGHT = 70.0
_RSI_COL = f"RSI_{_RSI_PERIOD}"


class RSIBot(BaseStrategy):
    """RSI crossover strategy — paper trading only.

    Buys when RSI crosses up through 30 (oversold recovery).
    Sells when RSI crosses down through 70 (overbought reversal).
    """

    @property
    def min_lookback(self) -> int:
        return _RSI_PERIOD + 6  # warmup + one crossover detection buffer

    @property
    def max_position_pct(self) -> float:
        return 0.05

    @property
    def stop_loss_pct(self) -> float:
        return 0.02

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

    def add_indicators(self, df: pd.DataFrame) -> None:
        df.ta.rsi(length=_RSI_PERIOD, append=True)  # type: ignore[attr-defined]

    def subscribe(self) -> None:
        self.get_history(_SYMBOL, _TF, self.min_lookback)
        self.register_bar_handler(_SYMBOL, _TF, self._on_bar)

    def _on_bar(self, df: pd.DataFrame) -> None:
        if self._signal_invalid.get(_SYMBOL, False):
            return

        result = rsi_signal(df, period=_RSI_PERIOD, oversold=_OVERSOLD, overbought=_OVERBOUGHT)

        current_rsi = float(df[_RSI_COL].iloc[-1]) if _RSI_COL in df.columns else float("nan")

        if result.action == "hold":
            log.debug(
                "rsi_bot_hold",
                rsi=round(current_rsi, 2),
                reason=result.reason,
            )
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
            "rsi_bot_signal",
            side=side,
            rsi=round(current_rsi, 2),
            confidence=round(result.confidence, 3),
            reason=result.reason,
            symbol=_SYMBOL,
        )
        if self._order_worker is None:
            log.warning(
                "order_worker_not_injected_dropping_order",
                strategy=self._name,
                side=side,
            )
            return
        self._order_worker.post(req)  # type: ignore[attr-defined]
