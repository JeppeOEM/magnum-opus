"""pandas-ta test strategy — EMA-directed trades at 1s cadence via pub/sub.

What this tests
---------------
- _on_candles1s() pub/sub at 1-second frequency
- pandas-ta EMA computation on a manually-maintained rolling close list
- Both buy-entry / sell-entry code paths (direction alternates with EMA cross)
- High-volume order stream through OrderQueueWorker
- Dashboard bots page with rapid trade count

Delivery mechanism: Redis pub/sub on candles1s:* — fires every second.
No register_bar_handler; pandas-ta is invoked directly in _on_candles1s.

Signal: EMA(EMA_LEN) on close prices.
  - close > EMA → bullish → buy entry when flat
  - close ≤ EMA → bearish → sell entry when flat
After HOLD_BARS seconds the position is force-closed regardless of direction.

HOLD_BARS = 5 → full cycle ≈ 6 s, ~10 trades/minute.

Move to strategies/inactive once you have confirmed:
  - pandas-ta runs correctly on rolling pub/sub data
  - Both long and short entries reach the exchange layer
  - order_events rows appear in QuestDB at high cadence
"""
from __future__ import annotations

import time

import pandas as pd
import structlog

STRATEGY_GROUP = "Test"
STRATEGY_TAGS = ["test", "pandas-ta", "ema", "1s", "pipeline", "pubsub", "high-freq"]

from bot_service.exchange import OrderRequest
from bot_service.strategy.base import BaseStrategy

log = structlog.get_logger()

_SYMBOL = "BTC-USDT"  # KuCoin symbol
_TF = "1s"            # delivery timeframe (pub/sub channel)
_EMA_LEN = 3          # short EMA — flips direction often at 1s granularity
_HOLD_BARS = 5        # force-exit after N bars; cycle = N+1 s ≈ 6 s
_MAX_HISTORY = 60     # rolling close buffer (cap at 60 to avoid unbounded growth)


class PandasTaTest(BaseStrategy):
    """EMA(3)-directed test bot at 1-second cadence via candles1s pub/sub.

    Maintains a rolling list of close prices, computes EMA(3) with pandas-ta
    on every bar, and enters in the direction of the current EMA cross.
    Exits after exactly HOLD_BARS seconds.
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
        self._close_buf: list[float] = []   # rolling close prices for EMA
        self._bars_held: int = 0
        self._entry_side: str | None = None  # "buy" or "sell" when in position

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

        # Maintain rolling close buffer
        self._close_buf.append(close)
        if len(self._close_buf) > _MAX_HISTORY:
            self._close_buf = self._close_buf[-_MAX_HISTORY:]

        # Need at least EMA_LEN + 1 bars before trading
        if len(self._close_buf) < _EMA_LEN + 1:
            return

        # Compute EMA using pandas-ta
        df = pd.DataFrame({"close": self._close_buf})
        df.ta.ema(length=_EMA_LEN, append=True)  # appends EMA_{_EMA_LEN} column
        last = df.iloc[-1]
        ema = last.get(f"EMA_{_EMA_LEN}")
        if ema is None or pd.isna(ema):
            return

        bullish = close > ema

        if self._entry_side is None:
            # Enter immediately — direction from EMA
            side = "buy" if bullish else "sell"
            self._post(side, "entry")
            self._entry_side = side
            self._bars_held = 0
            log.info(
                "pandas_ta_test_entry",
                strategy=self._name,
                side=side,
                close=round(close, 2),
                ema=round(float(ema), 2),
            )
        else:
            self._bars_held += 1
            if self._bars_held >= _HOLD_BARS:
                exit_side = "sell" if self._entry_side == "buy" else "buy"
                self._post(exit_side, "exit")
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
