"""Micro-momentum test strategy — for validating the backtest pipeline.

Works on any timeframe (1s, 1m, 15m) with as few as 4 bars.

Entry / exit logic
------------------
- BUY on the first bullish bar (close > open) when flat.
- CLOSE after ``hold_bars`` bars.  Default: 3.

Gap / null bars are **removed** from the feed before the strategy sees them
(QuestDBFeed drops NaN rows), so every bar the strategy receives has valid
OHLCV data — no NaN guards needed.

With hold_bars=3 the strategy generates one completed trade per ~4 bars,
which works even on freshly-collected or highly-fragmented data.

Not for live trading — this is intentionally simplistic.
"""
from __future__ import annotations

import backtrader as bt

STRATEGY_GROUP = "Test"
STRATEGY_TAGS = ["test", "micro-momentum", "1s", "1m", "pipeline"]

_SYMBOL = "BTC-USDT"
_TF = "1s"


class MicroMomentumTest(bt.Strategy):
    """Buy on bullish bar, hold for N *valid* bars, then close.

    Parameters
    ----------
    hold_bars : int
        How many non-NaN bars to hold a position.  Default 3.
    """

    params = (
        ("hold_bars", 3),
        ("position_pct", 0.05),  # fraction of portfolio per trade
    )

    def __init__(self) -> None:
        self.order: bt.Order | None = None
        self.bars_held: int = 0

    def next(self) -> None:
        # Wait for any pending order to fill.
        if self.order:
            return

        close = self.data.close[0]
        open_ = self.data.open[0]

        if not self.position:
            # Enter on any bullish bar using a fraction of available cash.
            if close > open_:
                cash = self.broker.getcash()
                size = (cash * self.p.position_pct) / close
                if size > 0:
                    self.order = self.buy(size=size)
                    self.bars_held = 0
        else:
            self.bars_held += 1
            if self.bars_held >= self.p.hold_bars:
                self.order = self.close()

    def notify_order(self, order: bt.Order) -> None:  # type: ignore[override]
        if order.status in (
            order.Completed,
            order.Canceled,
            order.Margin,
            order.Rejected,
        ):
            self.order = None
