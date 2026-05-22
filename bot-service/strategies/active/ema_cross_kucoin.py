"""EMA crossover strategy — KuCoin BTC-USDT, paper trading.

Entry logic:
  - BUY  when the fast EMA (9-period) crosses above the slow EMA (21-period).
  - SELL when the fast EMA crosses below the slow EMA.

Flip logic: closes the opposite leg first, then opens the new direction.
Paper mode only — no real orders are sent.
"""
from __future__ import annotations

import pandas as pd
import structlog

STRATEGY_GROUP = "Trend Following"
STRATEGY_TAGS = ["ema", "crossover", "paper", "kucoin", "btc"]

from bot_service.bus.event_types import GapMarker
from bot_service.exchange import OrderRequest
from bot_service.strategy.base import BaseStrategy
from bot_service.strategy.signals.ma_cross import compute_ma_cross_signal

log = structlog.get_logger()

_SYMBOL = "BTC-USDT"   # KuCoin spot symbol; matches aggregator config.yaml
_TF = "1m"
_FAST = 9
_SLOW = 21


class EmaCrossKucoin(BaseStrategy):
    """EMA 9/21 crossover — KuCoin BTC-USDT, paper trading."""

    @property
    def min_lookback(self) -> int:
        return _SLOW + 1  # need at least slow+1 bars for a valid crossover

    @property
    def max_position_pct(self) -> float:
        return 0.05  # 5 % of portfolio per order

    @property
    def stop_loss_pct(self) -> float:
        return 0.02  # 2 % hard stop

    @property
    def paper_trading(self) -> bool:
        return True

    @property
    def bus_timeout_seconds(self) -> int:
        return 300  # 5-minute silence triggers timeout

    @property
    def close_on_bus_timeout(self) -> bool:
        return False

    @property
    def orderbook_mode(self) -> str:
        return "none"

    def __init__(self, name: str, settings: object) -> None:
        super().__init__(name, settings)  # type: ignore[arg-type]
        self._current_side: str | None = None  # "long" | "short" | None

    def subscribe(self) -> None:
        self.get_history(_SYMBOL, _TF, self.min_lookback)
        self.register_bar_handler(_SYMBOL, _TF, self._on_bar)

    def handle_gap(self, gap: GapMarker) -> None:
        self._current_side = None
        super().handle_gap(gap)

    # ── Internal helpers ──────────────────────────────────────────────────────

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

        result = compute_ma_cross_signal(df, fast=_FAST, slow=_SLOW)
        if result.action == "hold":
            return

        target_side = "long" if result.action == "buy" else "short"
        if self._current_side == target_side:
            return  # already positioned correctly

        if not self._exchange:
            log.warning(
                "exchange_not_injected_dropping_order",
                strategy=self._name,
                action=result.action,
            )
            return
        if self._order_worker is None:
            log.warning(
                "order_worker_not_injected_dropping_order",
                strategy=self._name,
                action=result.action,
            )
            return

        log.info(
            "ema_cross_signal",
            strategy=self._name,
            action=result.action,
            confidence=round(result.confidence, 4),
            reason=result.reason,
            current_side=self._current_side,
            target_side=target_side,
            symbol=_SYMBOL,
            tf=_TF,
            fast=_FAST,
            slow=_SLOW,
        )

        # Flip: close opposite first
        if self._current_side == "long":
            self._post("sell", "exit")
            self._current_side = None
        elif self._current_side == "short":
            self._post("buy", "exit")
            self._current_side = None

        # Open new leg
        self._post("buy" if result.action == "buy" else "sell", "entry")
        self._current_side = target_side
