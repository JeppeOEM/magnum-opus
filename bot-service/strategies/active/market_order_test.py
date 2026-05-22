"""Market-order test strategy — exercises the full market order pipeline at 1s cadence.

What this tests
---------------
- Strategy loading by FileWatcher
- _on_candles1s() pub/sub delivery (candles1s:{exchange}:{symbol} channel)
- Market buy (entry) and market sell (exit) via OrderRequest
- OrderQueueWorker → PaperExchangeClient fill simulation
- order_events persistence to QuestDB
- Dashboard bots page (leaderboard, recent trades, PnL)

Delivery mechanism: Redis pub/sub on candles1s:* — fires every second.
No register_bar_handler; no Redis stream subscription needed.

Cycle: enter on first bar when flat → hold HOLD_BARS seconds → exit → repeat.
HOLD_BARS = 5 → buy + sell every ~6 seconds, ~10 trades/minute.

Move to strategies/inactive once you have confirmed the full market-order
flow works end-to-end with live bots.
"""
from __future__ import annotations

import time

import pandas as pd
import structlog

STRATEGY_GROUP = "Test"
STRATEGY_TAGS = ["test", "market-order", "1s", "pipeline", "pubsub"]

from bot_service.exchange import OrderRequest
from bot_service.strategy.base import BaseStrategy

log = structlog.get_logger()

_SYMBOL = "BTC-USDT"  # KuCoin symbol
_TF = "1s"            # delivery timeframe (pub/sub channel)
_HOLD_BARS = 5        # hold N seconds then close; cycle = N+1 s ≈ 6 s


class MarketOrderTest(BaseStrategy):
    """Buy-every-bar test bot using market orders at 1-second cadence.

    Receives bars via the candles1s pub/sub channel (fires every second).
    Enters immediately when flat; exits after HOLD_BARS bars.
    """

    @property
    def min_lookback(self) -> int:
        return 1  # no lookback needed — we trade immediately

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
        self._entry_side: str | None = None  # "buy" when in position, None when flat

    def subscribe(self) -> None:
        self._primary_symbol = _SYMBOL  # used by get_strategy_details() for dashboard display
        self._primary_tf = _TF

    # ── Pub/sub 1s bar handler ────────────────────────────────────────────────

    def _on_candles1s(self, payload: dict) -> None:
        if payload.get("symbol") != _SYMBOL:
            return

        self._last_event_ts = time.time()  # keep bus-timeout watchdog satisfied

        if self._entry_side is None:
            # Flat — enter immediately with a market buy
            self._post("buy", "entry")
            self._entry_side = "buy"
            self._bars_held = 0
        else:
            self._bars_held += 1
            if self._bars_held >= _HOLD_BARS:
                self._post("sell", "exit")
                self._entry_side = None

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
        log.info(
            "market_order_test_order",
            strategy=self._name,
            side=side,
            role=role,
        )
