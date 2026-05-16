from __future__ import annotations

import math
from typing import Any

import backtrader as bt


class EquitySampler(bt.Observer):  # type: ignore[misc]
    """Records broker portfolio value every ``sample_every`` bars.

    Non-sampled bars receive NaN so the lines array stays aligned to bar count.
    Use ``collect_equity`` to extract only the recorded (non-NaN) points.
    """

    params = (("sample_every", 60),)
    lines = ("portfolio_value",)

    def next(self) -> None:
        if len(self) % self.p.sample_every == 0:
            self.lines.portfolio_value[0] = self._owner.broker.getvalue()
        else:
            self.lines.portfolio_value[0] = float("nan")


def collect_equity(strat: Any, sample_every: int) -> list[tuple[str, float]]:
    """Extract sampled (ts, portfolio_value) pairs from a completed strategy run.

    Iterates the strategy's data datetime line paired with the EquitySampler
    observer line; skips NaN values (non-sampled bars).

    Returns list of (iso_datetime_str, portfolio_value) tuples, chronological.
    """
    observer: EquitySampler | None = None
    for obs in strat.observers:
        if isinstance(obs, EquitySampler):
            observer = obs
            break
    if observer is None:
        return []

    result: list[tuple[str, float]] = []
    bar_count = len(observer.lines.portfolio_value.array)
    for i in range(bar_count):
        val = observer.lines.portfolio_value.array[i]
        if math.isnan(val):
            continue
        # Backtrader datetime line stores floats; bt.num2date converts to datetime
        try:
            dt_float = strat.data.datetime.array[i]
            dt = bt.num2date(dt_float)
            result.append((dt.isoformat(), val))
        except IndexError:
            pass
    return result
