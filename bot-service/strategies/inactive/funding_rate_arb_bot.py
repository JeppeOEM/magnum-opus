from __future__ import annotations

import structlog

STRATEGY_GROUP = "Arbitrage"
STRATEGY_TAGS = ["funding-rate", "arb", "paper", "bybit", "btc"]

from bot_service.bus.event_types import FundingRate
from bot_service.exchange import OrderRequest
from bot_service.strategy.base import BaseStrategy
from bot_service.strategy.signals.funding_rate_arb import funding_rate_arb_signal

log = structlog.get_logger()

_EXCHANGE = "bybit"
_SYMBOL = "BTCUSDT"
_THRESHOLD_BPS = 10.0


class FundingRateArbBot(BaseStrategy):
    """Short on high positive funding rate, long on high negative funding rate.

    Paper mode only. Subscribes to funding:{exchange}:{symbol} stream.
    No bar data required — reacts purely to funding rate events.
    """

    @property
    def min_lookback(self) -> int:
        return 1

    @property
    def max_position_pct(self) -> float:
        return 0.02

    @property
    def stop_loss_pct(self) -> float:
        return 0.05

    @property
    def paper_trading(self) -> bool:
        return True

    @property
    def bus_timeout_seconds(self) -> int:
        return 300

    @property
    def close_on_bus_timeout(self) -> bool:
        return True

    def subscribe(self) -> None:
        self.register_funding_rate_handler(_EXCHANGE, _SYMBOL)

    def handle_funding_rate(self, event: FundingRate) -> None:
        result = funding_rate_arb_signal(event.funding_rate, _THRESHOLD_BPS)
        log.info(
            "funding_rate_arb_signal",
            strategy=self._name,
            exchange=event.exchange,
            symbol=event.symbol,
            funding_rate=event.funding_rate,
            action=result.action,
            confidence=result.confidence,
            reason=result.reason,
        )
        if result.action == "hold":
            return
        if self._order_worker is None:
            log.warning("order_worker_not_injected_dropping_order", strategy=self._name)
            return
        req = OrderRequest(
            strategy=self._name,
            exchange=self._exchange or _EXCHANGE,
            symbol=event.symbol,
            side=result.action,
            order_type="market",
            order_role="entry",
            size=self.max_position_pct,
            paper_trading=self.paper_trading,
        )
        self._order_worker.post(req)  # type: ignore[attr-defined]
