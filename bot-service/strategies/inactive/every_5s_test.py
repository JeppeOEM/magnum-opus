"""Every-5-seconds test strategy — exercises the full backtest pipeline.

Buys every ``trade_every`` bars (default 10) using a fixed fraction of cash.
Holds for ``hold_bars`` (default 5) then closes.

On 1 s data this produces a trade every ~10 seconds, generating enough entries
to populate all the backtest metrics, the equity curve, and the trade list.

Not for live trading — intentionally mechanical to stress-test the pipeline.
"""
from __future__ import annotations

import backtrader as bt

STRATEGY_GROUP = "Test"
STRATEGY_TAGS = ["test", "every-5s", "1s", "pipeline"]

_SYMBOL = "BTC-USDT"
_TF = "1s"


class Every5sTest(bt.Strategy):
    """Mechanical buy-every-N-bars strategy for pipeline testing.

    Parameters
    ----------
    trade_every : int
        Enter a new position every ``trade_every`` bars (when flat).  Default 10.
    hold_bars : int
        Hold the position for this many bars before closing.  Default 5.
    position_pct : float
        Fraction of available cash to allocate per trade.  Default 0.02 (2 %).
    """

    params = (
        ("trade_every", 10),
        ("hold_bars", 5),
        ("position_pct", 0.02),
    )

    def __init__(self) -> None:
        self.order: bt.Order | None = None
        self.bar_count: int = 0
        self.bars_held: int = 0

    def next(self) -> None:
        # Wait for any pending order.
        if self.order:
            return

        self.bar_count += 1

        if not self.position:
            # Enter on every trade_every-th bar.
            if self.bar_count % self.p.trade_every == 0:
                close = self.data.close[0]
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
