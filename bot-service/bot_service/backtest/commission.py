from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import structlog

import backtrader as bt

log = structlog.get_logger()


class KuCoinCommissionInfo(bt.CommissionInfo):  # type: ignore[misc]
    """KuCoin spot fee model: maker and taker both default to 0.1%.

    Set is_maker=True for strategies that primarily rest limit orders.
    """

    params = (
        ("maker_rate", 0.001),
        ("taker_rate", 0.001),
        ("is_maker", False),
    )

    def _getcommission(self, size: Any, price: Any, pseudoexec: Any) -> float:
        rate: float = self.p.maker_rate if self.p.is_maker else self.p.taker_rate
        return float(abs(size) * price * rate)


class BybitCommissionInfo(bt.CommissionInfo):  # type: ignore[misc]
    """Bybit fee model: maker rebate −0.01%, taker fee 0.06%.

    Funding rate costs are NOT applied via _getcommission (which only fires on
    order fills). Call get_funding_cost() in strategy.next() at 8-hour boundaries
    and deduct the result from broker cash.
    """

    params = (
        ("maker_rate", -0.0001),
        ("taker_rate", 0.0006),
        ("is_maker", False),
    )

    def __init__(self, funding_rates: pd.Series | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._funding_rates = funding_rates

    def _getcommission(self, size: Any, price: Any, pseudoexec: Any) -> float:
        rate: float = self.p.maker_rate if self.p.is_maker else self.p.taker_rate
        return float(abs(size) * price * rate)

    def get_funding_cost(
        self,
        timestamp: datetime,
        position_size: float,
        mark_price: float,
    ) -> float:
        """Return position_size × mark_price × funding_rate at timestamp.

        Uses pd.Series.asof() — returns last rate at or before timestamp.
        Returns 0.0 and logs WARN if no rate is available.
        """
        if self._funding_rates is None or self._funding_rates.empty:
            return 0.0
        try:
            rate = self._funding_rates.asof(pd.Timestamp(timestamp))
            if pd.isna(rate):
                log.warning("funding_rate_not_found", timestamp=str(timestamp))
                return 0.0
            return float(position_size * mark_price * float(rate))
        except (KeyError, TypeError):
            log.warning("funding_rate_not_found", timestamp=str(timestamp))
            return 0.0
