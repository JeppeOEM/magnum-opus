"""Limit-order test strategy — exercises the limit order code path at 1s cadence.

What this tests
---------------
- limit_price field on OrderRequest at 1-second frequency
- PaperExchangeClient._resolve_limit_price (optimistic fill at limit_price)
- OrderQueueWorker handling of limit vs market orders
- order_events rows with order_type = "limit" in QuestDB
- Dashboard bots page rendering of limit-order fills

Delivery mechanism: Redis pub/sub on candles1s:* — fires every second.
No register_bar_handler; no Redis stream subscription needed.

Cycle: limit buy at (close - offset) when flat → hold HOLD_BARS seconds →
limit sell at (close + offset) → repeat.  HOLD_BARS = 5 → ~6 s per cycle.

In paper mode fills are always optimistic (limit_price regardless of crossing),
so the bot generates fills at the expected cadence.

Move to strategies/inactive once you have confirmed limit orders flow end-to-end.
"""
from __future__ import annotations

import time

import pandas as pd
import structlog

STRATEGY_GROUP = "Test"
STRATEGY_TAGS = ["test", "limit-order", "1s", "pipeline", "pubsub"]

from bot_service.exchange import OrderRequest
from bot_service.strategy.base import BaseStrategy

log = structlog.get_logger()

_SYMBOL = "BTC-USDT"  # KuCoin symbol
_TF = "1s"            # delivery timeframe (pub/sub channel)
_HOLD_BARS = 5        # hold N seconds then close; cycle = N+1 s ≈ 6 s
_OFFSET_PCT = 0.0005  # 0.05 % price offset — buy below close, sell above close


class LimitOrderTest(BaseStrategy):
    """Buy-every-bar test bot using limit orders at 1-second cadence.

    Sets limit_price to close ± _OFFSET_PCT. Paper mode fills optimistically;
    live mode fills within a tick or two.
    """

    @property
    def min_lookback(self) -> int:
        return 1

    @property
    def max_position_pct(self) -> float:
        return 0.01  # 1 % of portfolio

    @property
    def stop_loss_pct(self) -> float:
        return 0.05  # not used; required by abstract base

    @property
    def paper_trading(self) -> bool:
        return True

    @property
    def bus_timeout_seconds(self) -> int:
        return 120

    @property
    def close_on_bus_timeout(self) -> bool:
        return False

    @property
    def orderbook_mode(self) -> str:
        return "snapshot_1s"  # enables _on_candles1s pub/sub delivery

    def __init__(self, name: str, settings: object) -> None:
        super().__init__(name, settings)  # type: ignore[arg-type]
        self._bars_held: int = 0
        self._entry_side: str | None = None  # "buy" when long, None when flat

    def subscribe(self) -> None:
        self._primary_symbol = _SYMBOL  # used by get_strategy_details() for dashboard display
        self._primary_tf = _TF

    # ── Pub/sub 1s bar handler ────────────────────────────────────────────────

    def _on_candles1s(self, payload: dict) -> None:
        if payload.get("symbol") != _SYMBOL:
            return

        self._last_event_ts = time.time()

        close = float(payload.get("close") or 0.0)
        if close == 0.0:
            return

        if self._entry_side is None:
            limit_price = round(close * (1.0 - _OFFSET_PCT), 2)
            self._post_limit("buy", "entry", limit_price)
            self._entry_side = "buy"
            self._bars_held = 0
        else:
            self._bars_held += 1
            if self._bars_held >= _HOLD_BARS:
                limit_price = round(close * (1.0 + _OFFSET_PCT), 2)
                self._post_limit("sell", "exit", limit_price)
                self._entry_side = None

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _post_limit(self, side: str, role: str, limit_price: float) -> None:
        req = OrderRequest(
            strategy=self._name,
            exchange=self._exchange,
            symbol=_SYMBOL,
            side=side,
            order_type="limit",
            order_role=role,
            size=self.max_position_pct,
            limit_price=limit_price,
            paper_trading=self.paper_trading,
        )
        self._order_worker.post(req)  # type: ignore[attr-defined]
        log.info(
            "limit_order_test_order",
            strategy=self._name,
            side=side,
            role=role,
            limit_price=limit_price,
        )
