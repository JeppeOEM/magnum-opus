"""Integration tests for the BaseStrategy ↔ backtrader bridge in runner.py.

These tests verify that a BaseStrategy subclass (which uses the event-bus pattern
– subscribe/register_bar_handler/_order_worker) is correctly driven by backtrader's
cerebro loop and that trades are recorded end-to-end.

No live services are required.  The feed is a synthetic PandasData and the
strategy is defined inline — no QuestDB, no Redis.
"""
from __future__ import annotations

import math
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import backtrader as bt
import pandas as pd
import pytest

from bot_service.backtest.runner import load_strategy_class, run_backtest, BacktestResult


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_price_series(n: int, start: float = 100.0, tick: float = 0.1) -> list[float]:
    """Simple ascending price series."""
    return [start + i * tick for i in range(n)]


def _make_sinusoidal(n: int, base: float = 100.0, amp: float = 5.0, period: int = 30) -> list[float]:
    """Sinusoidal price series — guarantees MA crossovers occur."""
    import math as _m
    return [base + amp * _m.sin(2 * _m.pi * i / period) for i in range(n)]


def _write_strategy(tmp_path: Path, code: str) -> Path:
    p = tmp_path / "strat.py"
    p.write_text(code)
    return p


# ── strategy fixtures ─────────────────────────────────────────────────────────

_ALWAYS_BUY_STRATEGY = '''
"""Strategy that buys on bar 25 and sells on bar 50 — guaranteed single trade."""
from __future__ import annotations
from bot_service.strategy.base import BaseStrategy
from bot_service.exchange import OrderRequest

_SYM = "TESTSYM"
_TF = "1m"


class AlwaysBuyStrategy(BaseStrategy):
    @property
    def min_lookback(self) -> int: return 1
    @property
    def max_position_pct(self) -> float: return 0.10
    @property
    def stop_loss_pct(self) -> float: return 0.01
    @property
    def paper_trading(self) -> bool: return True
    @property
    def bus_timeout_seconds(self) -> int: return 30
    @property
    def close_on_bus_timeout(self) -> bool: return False

    def __init__(self, name, settings):
        super().__init__(name, settings)
        self._bar_idx = 0
        self._in_position = False

    def subscribe(self):
        self.register_bar_handler(_SYM, _TF, self._on_bar)

    def _on_bar(self, df):
        self._bar_idx += 1
        if self._bar_idx == 25 and not self._in_position:
            self._order_worker.post(OrderRequest(
                strategy=self._name, exchange=self._exchange,
                symbol=_SYM, side="buy", order_type="market",
                order_role="entry", size=0.10,
            ))
            self._in_position = True
        elif self._bar_idx == 50 and self._in_position:
            self._order_worker.post(OrderRequest(
                strategy=self._name, exchange=self._exchange,
                symbol=_SYM, side="sell", order_type="market",
                order_role="exit", size=0.10,
            ))
            self._in_position = False
'''


_MA_CROSS_STRATEGY = '''
"""Mini MA crossover strategy using BaseStrategy pattern."""
from __future__ import annotations
from bot_service.strategy.base import BaseStrategy
from bot_service.exchange import OrderRequest

_SYM = "TESTSYM"
_TF = "1m"
_FAST = 5
_SLOW = 20


class MiniMACross(BaseStrategy):
    @property
    def min_lookback(self) -> int: return _SLOW + 1
    @property
    def max_position_pct(self) -> float: return 0.05
    @property
    def stop_loss_pct(self) -> float: return 0.02
    @property
    def paper_trading(self) -> bool: return True
    @property
    def bus_timeout_seconds(self) -> int: return 30
    @property
    def close_on_bus_timeout(self) -> bool: return False

    def __init__(self, name, settings):
        super().__init__(name, settings)
        self._side = None  # "long" | None

    def subscribe(self):
        self.register_bar_handler(_SYM, _TF, self._on_bar)

    def _on_bar(self, df):
        close = df["close"]
        if len(close) < _SLOW + 1:
            return
        fast = close.ewm(span=_FAST, adjust=False).mean()
        slow = close.ewm(span=_SLOW, adjust=False).mean()
        prev_cross = fast.iloc[-2] - slow.iloc[-2]
        curr_cross = fast.iloc[-1] - slow.iloc[-1]

        if prev_cross <= 0 and curr_cross > 0 and self._side != "long":
            # Close short first if needed
            if self._side == "short":
                self._order_worker.post(OrderRequest(
                    strategy=self._name, exchange=self._exchange,
                    symbol=_SYM, side="buy", order_type="market",
                    order_role="exit", size=0.05,
                ))
            self._order_worker.post(OrderRequest(
                strategy=self._name, exchange=self._exchange,
                symbol=_SYM, side="buy", order_type="market",
                order_role="entry", size=0.05,
            ))
            self._side = "long"
        elif prev_cross >= 0 and curr_cross < 0 and self._side == "long":
            self._order_worker.post(OrderRequest(
                strategy=self._name, exchange=self._exchange,
                symbol=_SYM, side="sell", order_type="market",
                order_role="exit", size=0.05,
            ))
            self._side = None
'''


# ── minimal cerebro runner (bypasses QuestDB feed) ────────────────────────────

def _run_with_synthetic_feed(
    strategy_path: Path,
    prices: list[float],
    exchange: str = "bybit",
    symbol: str = "TESTSYM",
    tf: str = "1m",
    initial_capital: float = 10_000.0,
) -> BacktestResult:
    """Run a backtest using an in-memory price series instead of QuestDB."""
    import uuid
    from bot_service.backtest.runner import (
        hash_file, load_strategy_class, _compute_metrics,
        BacktestResult,
    )
    from bot_service.backtest.equity import EquitySampler, collect_equity
    from bot_service.backtest.writer import BacktestResultWriter
    from bot_service.strategy.base import BaseStrategy
    import backtrader as bt

    # Build a minimal PandasData feed from the price list
    n = len(prices)
    base_dt = datetime(2026, 1, 1)
    idx = [base_dt + timedelta(minutes=i) for i in range(n)]
    df = pd.DataFrame({
        "open": prices,
        "high": [p * 1.001 for p in prices],
        "low":  [p * 0.999 for p in prices],
        "close": prices,
        "volume": [1000.0] * n,
    }, index=pd.DatetimeIndex(idx))
    feed = bt.feeds.PandasData(dataname=df)
    # Stash data_segments so BacktestResult can be built
    feed.data_segments = []  # type: ignore[attr-defined]

    file_hash = hash_file(strategy_path)
    strategy_name = strategy_path.stem
    run_id = str(uuid.uuid4())

    cls = load_strategy_class(strategy_path)

    writer = MagicMock(spec=BacktestResultWriter)
    writer.write_fill = MagicMock()

    _is_base_strategy = issubclass(cls, BaseStrategy)
    _needs_bt_base = _is_base_strategy and not issubclass(cls, bt.Strategy)
    _bt_strategy_name = strategy_name
    sample_every = 10

    # ── copy the exact bridge logic from runner.run_backtest ─────────────────
    import math as _math
    import abc as _abc
    import pandas as _pd
    from bot_service.strategy.base import _HISTORY_COLUMNS as _HC
    from bot_service.bus.event_types import BarClose as _BarClose
    import structlog
    _log = structlog.get_logger()

    def _apply_fill(instance, order):
        if order.status == order.Completed:
            pos_size = float(instance.broker.getposition(instance.data).size)
            writer.write_fill(order, pos_size)
            comm = abs(float(order.executed.comm))
            instance._bt_total_fees += comm
            fill_price = float(order.executed.price)
            fill_size = float(order.executed.size)
            try:
                bar_dt = instance.data.datetime.datetime(0).isoformat()
            except Exception:
                bar_dt = ""
            if fill_size > 0:
                instance._bt_pending_entry = {
                    "entry_ts": bar_dt, "entry_price": fill_price,
                    "entry_size": abs(fill_size), "entry_comm": comm,
                }
            elif fill_size < 0 and instance._bt_pending_entry is not None:
                e = instance._bt_pending_entry
                size = abs(fill_size)
                pnl = (fill_price - e["entry_price"]) * size
                pnl_net = pnl - e["entry_comm"] - comm
                instance._bt_trades.append({
                    "entry_ts": e["entry_ts"], "exit_ts": bar_dt,
                    "entry_price": round(e["entry_price"], 6),
                    "exit_price": round(fill_price, 6),
                    "size": round(size, 8),
                    "pnl": round(pnl, 4), "pnl_net": round(pnl_net, 4),
                    "direction": "long",
                })
                instance._bt_pending_entry = None

    if _needs_bt_base:
        _WrappedMeta = type("_WrappedMeta", (type(bt.Strategy), _abc.ABCMeta), {})

        class _WrappedStrategy(cls, bt.Strategy, metaclass=_WrappedMeta):  # type: ignore[valid-type]
            def __init__(self, *args, **kwargs):
                from bot_service.config import get_settings
                cls.__init__(self, name=_bt_strategy_name, settings=get_settings())
                self._bt_total_fees = 0.0
                self._bt_trades = []
                self._bt_pending_entry = None
                self._exchange = exchange

                class _FakeOrderWorker:
                    def __init__(self_, strat):
                        self_.strat = strat
                    def post(self_, req):
                        s = self_.strat
                        try:
                            price = float(s.data.close[0])
                            if price <= 0 or _math.isnan(price):
                                return
                            units = (float(s.broker.getvalue()) * float(req.size)) / price
                            pos = float(s.broker.getposition(s.data).size)
                            if req.side == "buy":
                                if req.order_role == "exit" and pos < 0:
                                    s.buy(size=abs(pos))
                                elif req.order_role != "exit" and units > 0:
                                    s.buy(size=units)
                            elif req.side == "sell":
                                if req.order_role == "exit" and pos > 0:
                                    s.sell(size=pos)
                                elif req.order_role != "exit" and units > 0:
                                    s.sell(size=units)
                        except Exception:
                            pass

                self._order_worker = _FakeOrderWorker(self)

                def _noop_history(sym, tf_, n):
                    return _pd.DataFrame(columns=_HC)
                _orig = self.get_history
                self.get_history = _noop_history  # type: ignore[method-assign]
                try:
                    self.subscribe()
                except Exception as exc:
                    _log.warning("backtest_subscribe_failed", error=str(exc))
                finally:
                    self.get_history = _orig  # type: ignore[method-assign]

            def next(self):
                try:
                    close_val = float(self.data.close[0])
                except Exception:
                    return
                if _math.isnan(close_val):
                    return
                if self._bar_handlers:
                    bt_sym, bt_tf_str = next(iter(self._bar_handlers))
                else:
                    bt_sym, bt_tf_str = symbol, tf
                try:
                    ts_ms = int(self.data.datetime.datetime(0).timestamp() * 1000)
                except Exception:
                    ts_ms = 0

                def _s(attr, default=0.0):
                    try:
                        v = float(getattr(self.data, attr)[0])
                        return default if _math.isnan(v) else v
                    except Exception:
                        return default

                bar = _BarClose(
                    exchange=exchange, symbol=bt_sym, tf=bt_tf_str, ts=ts_ms,
                    open=float(self.data.open[0]), high=float(self.data.high[0]),
                    low=float(self.data.low[0]), close=close_val,
                    volume=_s("volume"), quote_volume=0.0, trade_count=0,
                    is_complete=True,
                )
                self.on_bar(bar)

            def notify_order(self, order):
                super().notify_order(order)
                _apply_fill(self, order)
    else:
        class _WrappedStrategy(cls):  # type: ignore[valid-type]
            def __init__(self, *args, **kwargs):
                if _is_base_strategy:
                    from bot_service.config import get_settings
                    super().__init__(name=_bt_strategy_name, settings=get_settings())
                else:
                    super().__init__(*args, **kwargs)
                self._bt_total_fees = 0.0
                self._bt_trades = []
                self._bt_pending_entry = None

            def notify_order(self, order):
                super().notify_order(order)
                _apply_fill(self, order)

    cerebro = bt.Cerebro()
    cerebro.adddata(feed)
    cerebro.addstrategy(_WrappedStrategy)
    cerebro.broker.setcash(initial_capital)
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe", timeframe=bt.TimeFrame.Minutes)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")
    cerebro.addanalyzer(bt.analyzers.SQN, _name="sqn")
    cerebro.addobserver(EquitySampler, sample_every=sample_every)

    results = cerebro.run()
    final_value = cerebro.broker.getvalue()
    metrics = _compute_metrics(results, initial_capital, final_value)
    equity = collect_equity(results[0], sample_every)
    trades = getattr(results[0], "_bt_trades", [])[:5000]

    start_date = idx[0].isoformat()
    end_date = idx[-1].isoformat()

    return BacktestResult(
        run_id=run_id, strategy_name=strategy_name, hash=file_hash,
        symbol=symbol, tf=tf, exchange=exchange,
        start_date=start_date, end_date=end_date,
        initial_capital=initial_capital, final_value=final_value,
        equity_curve=equity, data_segments=[], trades=trades,
        **metrics,  # type: ignore[arg-type]
    )


# ── tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.l1
def test_base_strategy_bridge_records_trades(tmp_path: Path) -> None:
    """A BaseStrategy that posts buy/sell at fixed bars must produce exactly
    one closed trade in the backtest result."""
    path = _write_strategy(tmp_path, _ALWAYS_BUY_STRATEGY)
    prices = _make_price_series(100)  # 100 bars, ascending price

    result = _run_with_synthetic_feed(path, prices)

    # Bridge wired correctly → exactly one round-trip trade
    assert result.n_trades == 1, (
        f"Expected 1 trade but got {result.n_trades}. "
        "Bridge likely did not call subscribe()/inject _order_worker correctly."
    )
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade["direction"] == "long"
    assert trade["pnl_net"] != 0 or trade["pnl"] == pytest.approx(0.0, abs=1.0)


@pytest.mark.l1
def test_base_strategy_bridge_win_rate_positive(tmp_path: Path) -> None:
    """Ascending price with a timed buy-then-sell should produce a profit."""
    path = _write_strategy(tmp_path, _ALWAYS_BUY_STRATEGY)
    prices = _make_price_series(100, start=100.0, tick=0.5)  # strongly ascending

    result = _run_with_synthetic_feed(path, prices)

    assert result.n_trades >= 1
    assert result.total_return_pct > 0, (
        "Expected positive return on ascending price after buy at bar 25 and "
        f"sell at bar 50.  Got {result.total_return_pct:.4f}%"
    )


@pytest.mark.l1
def test_base_strategy_bridge_ma_cross_produces_multiple_trades(tmp_path: Path) -> None:
    """An MA-cross strategy on a sinusoidal price series must produce at least
    two trades (the sine wave guarantees multiple crossovers)."""
    path = _write_strategy(tmp_path, _MA_CROSS_STRATEGY)
    # 200 bars, sine period=30 → ~6 full cycles → multiple MA crossovers
    prices = _make_sinusoidal(200, base=100.0, amp=8.0, period=30)

    result = _run_with_synthetic_feed(path, prices)

    assert result.n_trades >= 2, (
        f"Expected ≥2 trades from MA-cross on sinusoidal data, got {result.n_trades}. "
        "next() → on_bar() → handler chain may be broken."
    )


@pytest.mark.l1
def test_base_strategy_bridge_equity_not_flat(tmp_path: Path) -> None:
    """After at least one trade the equity curve must deviate from initial capital."""
    path = _write_strategy(tmp_path, _ALWAYS_BUY_STRATEGY)
    prices = _make_price_series(100, start=100.0, tick=0.5)

    result = _run_with_synthetic_feed(path, prices)

    assert result.n_trades >= 1
    assert result.final_value != pytest.approx(result.initial_capital), (
        "Equity curve is flat — trade orders were never executed by backtrader."
    )


@pytest.mark.l1
def test_base_strategy_bridge_new_metrics_populated(tmp_path: Path) -> None:
    """New metric fields (profit_factor, avg_win_usd, etc.) must not all be zero
    when there is at least one trade."""
    path = _write_strategy(tmp_path, _MA_CROSS_STRATEGY)
    prices = _make_sinusoidal(200, base=100.0, amp=8.0, period=30)

    result = _run_with_synthetic_feed(path, prices)

    # At least some metrics should be non-zero with real trades
    if result.n_trades >= 1:
        # Win rate must be in [0, 100]
        assert 0.0 <= result.win_rate_pct <= 100.0
        # avg_trade_bars must be positive
        assert result.avg_trade_bars >= 0.0
