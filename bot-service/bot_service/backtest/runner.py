from __future__ import annotations

import abc as _abc
import hashlib
import importlib.util
import math
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import backtrader as bt
import structlog

from bot_service.backtest.commission import BybitCommissionInfo, KuCoinCommissionInfo
from bot_service.backtest.equity import EquitySampler, collect_equity
from bot_service.backtest.feeds import QuestDBFeed
from bot_service.backtest.writer import BacktestResultWriter
from bot_service.strategy.base import BaseStrategy, _HISTORY_COLUMNS

log = structlog.get_logger()

_COMMISSION_MAP: dict[str, Any] = {
    "bybit": BybitCommissionInfo,
    "kucoin": KuCoinCommissionInfo,
}


@dataclass
class BacktestResult:
    run_id: str
    strategy_name: str
    hash: str
    symbol: str
    tf: str
    exchange: str
    start_date: str
    end_date: str
    initial_capital: float
    final_value: float
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    n_trades: int
    win_rate_pct: float
    avg_pnl_per_trade: float
    total_fees_usd: float
    passes_fee_gate: bool
    # Extended metrics
    annualized_return_pct: float = 0.0
    sqn: float = 0.0
    max_drawdown_usd: float = 0.0
    max_drawdown_duration_bars: int = 0
    profit_factor: float = 0.0
    avg_win_usd: float = 0.0
    avg_loss_usd: float = 0.0
    best_trade_usd: float = 0.0
    worst_trade_usd: float = 0.0
    max_consec_wins: int = 0
    max_consec_losses: int = 0
    avg_trade_bars: float = 0.0
    n_long_trades: int = 0
    n_short_trades: int = 0
    equity_curve: list[tuple[str, float]] = field(default_factory=list)
    # Contiguous segments of valid data found in the query window.
    # Each dict: {"start": ISO_str, "end": ISO_str, "bars": int}
    # Empty list means the whole window was usable (no gaps detected).
    data_segments: list[dict] = field(default_factory=list)
    # Per-trade list: each dict has entry_ts, exit_ts, entry_price, exit_price,
    # size, pnl, pnl_net, direction.  Capped at 5 000 entries.
    trades: list[dict] = field(default_factory=list)


def hash_file(path: Path) -> str:
    """SHA-256 of file bytes; first 16 hex characters."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def load_strategy_class(path: Path) -> type:
    """Load a .py file and return the single strategy class it defines.

    Accepts two strategy styles:
    - ``bt.Strategy`` subclass  — backtest-only, full backtrader interface available.
    - ``BaseStrategy`` subclass — live/backtest dual-mode (must also implement
      a backtrader-compatible ``next()`` method when used for backtesting).

    Raises ValueError if zero or more than one qualifying class is found, or
    on import error.
    """
    module_name = f"_bt_strategy_{path.stem}_{hash_file(path)}"
    sys.modules.pop(module_name, None)
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Cannot create spec for {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        candidates = [
            cls
            for cls in vars(module).values()
            if isinstance(cls, type)
            and (issubclass(cls, BaseStrategy) or issubclass(cls, bt.Strategy))
            and cls is not BaseStrategy
            and cls is not bt.Strategy
            and cls.__module__ == module_name
        ]
        if len(candidates) == 0:
            raise ValueError(
                f"No strategy class found in {path} — "
                "define exactly one bt.Strategy or BaseStrategy subclass"
            )
        if len(candidates) > 1:
            raise ValueError(
                f"Multiple strategy classes in {path}: "
                + ", ".join(c.__name__ for c in candidates)
            )
        cls = candidates[0]
        sys.modules.pop(module_name, None)
        return cls
    except (ValueError, AttributeError):
        raise
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise ValueError(f"Import error for {path}: {exc}") from exc


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Coerce to float; return default if None, NaN, or inf."""
    try:
        f = float(val)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return default


def _compute_metrics(
    results: list[Any],
    initial_capital: float,
    final_value: float,
) -> dict[str, float | int | bool]:
    strat = results[0]

    # ── Sharpe Ratio ─────────────────────────────────────────────────────────
    # Can return None (no trades) or NaN (insufficient returns).
    # NaN is truthy in Python, so `val or 0.0` does NOT replace it; guard explicitly.
    sharpe = 0.0
    if hasattr(strat.analyzers, "sharpe"):
        sr = strat.analyzers.sharpe.get_analysis()
        sharpe = _safe_float(sr.get("sharperatio"))

    # ── DrawDown ──────────────────────────────────────────────────────────────
    # dict.get(key, default) returns None when the key EXISTS with value None;
    # the default only fires for missing keys.  Guard with `or 0.0`.
    max_dd = 0.0
    max_dd_usd = 0.0
    max_dd_bars = 0
    if hasattr(strat.analyzers, "drawdown"):
        dd = strat.analyzers.drawdown.get_analysis()
        max_dd = _safe_float(dd.get("max", {}).get("drawdown"))
        max_dd_usd = _safe_float(dd.get("max", {}).get("moneydown"))
        max_dd_bars = int(dd.get("max", {}).get("len") or 0)

    # ── TradeAnalyzer ─────────────────────────────────────────────────────────
    n_trades = 0
    win_rate = 0.0
    avg_pnl = 0.0
    profit_factor = 0.0
    avg_win = 0.0
    avg_loss = 0.0
    best_trade = 0.0
    worst_trade = 0.0
    max_consec_wins = 0
    max_consec_losses = 0
    avg_trade_bars = 0.0
    n_long = 0
    n_short = 0

    if hasattr(strat.analyzers, "trades"):
        ta = strat.analyzers.trades.get_analysis()
        total_closed = int(ta.get("total", {}).get("closed") or 0)
        n_trades = total_closed
        won = int(ta.get("won", {}).get("total") or 0)
        lost = int(ta.get("lost", {}).get("total") or 0)
        win_rate = (won / total_closed * 100.0) if total_closed > 0 else 0.0

        # PnL averages
        pnl_net = ta.get("pnl", {}).get("net") or {}
        total_pnl = _safe_float(pnl_net.get("total"))
        avg_pnl = total_pnl / total_closed if total_closed > 0 else 0.0

        # Profit factor = gross wins / |gross losses|
        won_pnl_total = _safe_float(ta.get("won", {}).get("pnl", {}).get("total"))
        lost_pnl_total = abs(_safe_float(ta.get("lost", {}).get("pnl", {}).get("total")))
        if lost_pnl_total > 0:
            profit_factor = round(won_pnl_total / lost_pnl_total, 4)

        # Per-trade averages / extremes
        avg_win = _safe_float(ta.get("won", {}).get("pnl", {}).get("average"))
        avg_loss = _safe_float(ta.get("lost", {}).get("pnl", {}).get("average"))
        best_trade = _safe_float(ta.get("won", {}).get("pnl", {}).get("max"))
        worst_trade = _safe_float(ta.get("lost", {}).get("pnl", {}).get("max"))

        # Streaks
        max_consec_wins = int(ta.get("streak", {}).get("won", {}).get("longest") or 0)
        max_consec_losses = int(ta.get("streak", {}).get("lost", {}).get("longest") or 0)

        # Average bars per trade (ta["len"] is a flat dict: {"total": N, "average": X, ...})
        avg_trade_bars = _safe_float(ta.get("len", {}).get("average"))

        # Long / short breakdown
        n_long = int(ta.get("long", {}).get("total") or 0)
        n_short = int(ta.get("short", {}).get("total") or 0)

    # ── Returns (annualised) ──────────────────────────────────────────────────
    ann_return = 0.0
    if hasattr(strat.analyzers, "returns"):
        ann_return = _safe_float(strat.analyzers.returns.get_analysis().get("rnorm100"))

    # ── SQN (Van Tharp System Quality Number) ────────────────────────────────
    sqn_val = 0.0
    if hasattr(strat.analyzers, "sqn"):
        sqn_val = _safe_float(strat.analyzers.sqn.get_analysis().get("sqn"))

    # ── Fees ──────────────────────────────────────────────────────────────────
    total_fees = 0.0
    if hasattr(strat, "_bt_total_fees"):
        total_fees = float(strat._bt_total_fees)

    total_return_pct = ((final_value - initial_capital) / initial_capital) * 100.0
    # Fee gate: total fees < 1% of initial capital
    passes_fee_gate = total_fees < initial_capital * 0.01

    return {
        "total_return_pct": round(total_return_pct, 4),
        "sharpe_ratio": round(sharpe, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "n_trades": n_trades,
        "win_rate_pct": round(win_rate, 4),
        "avg_pnl_per_trade": round(avg_pnl, 4),
        "total_fees_usd": round(total_fees, 4),
        "passes_fee_gate": passes_fee_gate,
        # Extended
        "annualized_return_pct": round(ann_return, 4),
        "sqn": round(sqn_val, 4),
        "max_drawdown_usd": round(max_dd_usd, 4),
        "max_drawdown_duration_bars": max_dd_bars,
        "profit_factor": profit_factor,
        "avg_win_usd": round(avg_win, 4),
        "avg_loss_usd": round(avg_loss, 4),
        "best_trade_usd": round(best_trade, 4),
        "worst_trade_usd": round(worst_trade, 4),
        "max_consec_wins": max_consec_wins,
        "max_consec_losses": max_consec_losses,
        "avg_trade_bars": round(avg_trade_bars, 2),
        "n_long_trades": n_long,
        "n_short_trades": n_short,
    }


def run_backtest(
    path: Path,
    symbol: str,
    tf: str,
    exchange: str,
    start_date: str,
    end_date: str,
    initial_capital: float,
    questdb_http_addr: str,
    questdb_ilp_addr: str,
    sample_every: int = 60,
) -> BacktestResult:
    """Run a full backtest and return a BacktestResult.

    Supports two strategy patterns:

    1. **Pure bt.Strategy subclasses** — no bridge needed; backtrader calls
       ``next()`` directly and the strategy uses ``self.buy()``/``self.sell()``.

    2. **BaseStrategy subclasses** — live/backtest dual-mode strategies that
       use the event-bus pattern (``subscribe()`` → ``register_bar_handler()``
       → handler → ``_order_worker.post(OrderRequest)``).  The bridge in
       ``_WrappedStrategy`` translates each backtrader bar into a ``BarClose``
       event and each ``OrderRequest`` into a ``self.buy()``/``self.sell()``
       call so that strategies run unmodified.
    """
    from datetime import datetime, timezone
    import pandas as _pd
    from bot_service.bus.event_types import BarClose as _BarClose

    file_hash = hash_file(path)
    strategy_name = path.stem
    run_id = str(uuid.uuid4())

    start_dt = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc)

    cls = load_strategy_class(path)

    feed = QuestDBFeed(
        questdb_http_addr=questdb_http_addr,
        exchange=exchange,
        symbol=symbol,
        start=start_dt,
        end=end_dt,
        tf=tf,
    )

    writer = BacktestResultWriter(
        questdb_ilp_addr=questdb_ilp_addr,
        strategy_name=strategy_name,
        exchange=exchange,
        symbol=symbol,
    )

    # Detect strategy style
    _is_base_strategy = issubclass(cls, BaseStrategy)
    # True when the user's class inherits BaseStrategy but NOT bt.Strategy.
    # In this case backtrader's MetaStrategy hasn't run, so _addobserver and
    # other bt internals are missing — we must include bt.Strategy in the MRO.
    _needs_bt_base = _is_base_strategy and not issubclass(cls, bt.Strategy)
    _bt_strategy_name = strategy_name

    def _apply_fill(instance: Any, order: Any) -> None:
        """Shared fill handler — records fees and per-trade entry/exit."""
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

            if fill_size > 0:  # BUY → open long
                instance._bt_pending_entry = {
                    "entry_ts": bar_dt,
                    "entry_price": fill_price,
                    "entry_size": abs(fill_size),
                    "entry_comm": comm,
                }
            elif fill_size < 0 and instance._bt_pending_entry is not None:  # SELL → close long
                e = instance._bt_pending_entry
                size = abs(fill_size)
                pnl = (fill_price - e["entry_price"]) * size
                pnl_net = pnl - e["entry_comm"] - comm
                instance._bt_trades.append({
                    "entry_ts": e["entry_ts"],
                    "exit_ts": bar_dt,
                    "entry_price": round(e["entry_price"], 6),
                    "exit_price": round(fill_price, 6),
                    "size": round(size, 8),
                    "pnl": round(pnl, 4),
                    "pnl_net": round(pnl_net, 4),
                    "direction": "long",
                })
                instance._bt_pending_entry = None

    if _needs_bt_base:
        # ── BaseStrategy subclass that doesn't already inherit bt.Strategy ──
        # Create a combined metaclass to satisfy both ABCMeta and MetaStrategy.
        _WrappedMeta = type("_WrappedMeta", (type(bt.Strategy), _abc.ABCMeta), {})

        class _WrappedStrategy(cls, bt.Strategy, metaclass=_WrappedMeta):  # type: ignore[valid-type]
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                from bot_service.config import get_settings
                cls.__init__(self, name=_bt_strategy_name, settings=get_settings())
                self._bt_total_fees: float = 0.0
                self._bt_trades: list[dict] = []
                self._bt_pending_entry: dict | None = None
                # Inject the exchange name so on_bar handlers don't bail out.
                self._exchange = exchange

                # Fake order worker: translates OrderRequest → bt buy/sell calls.
                class _FakeOrderWorker:
                    def __init__(self_, strat: Any) -> None:
                        self_.strat = strat

                    def post(self_, req: Any) -> None:
                        s = self_.strat
                        try:
                            price = float(s.data.close[0])
                            if price <= 0 or math.isnan(price):
                                return
                            units = (float(s.broker.getvalue()) * float(req.size)) / price
                            pos = float(s.broker.getposition(s.data).size)
                            if req.side == "buy":
                                if req.order_role == "exit" and pos < 0:
                                    s.buy(size=abs(pos))       # cover short
                                elif req.order_role != "exit" and units > 0:
                                    s.buy(size=units)          # open long
                            elif req.side == "sell":
                                if req.order_role == "exit" and pos > 0:
                                    s.sell(size=pos)           # close long
                                elif req.order_role != "exit" and units > 0:
                                    s.sell(size=units)         # open short
                        except Exception:
                            pass

                self._order_worker = _FakeOrderWorker(self)

                # Patch get_history to return an empty DataFrame so subscribe()
                # does not make HTTP calls to QuestDB during backtesting.
                def _noop_history(sym: str, tf_: str, n: int) -> _pd.DataFrame:
                    return _pd.DataFrame(columns=_HISTORY_COLUMNS)

                _orig_get_history = self.get_history
                self.get_history = _noop_history  # type: ignore[method-assign]
                try:
                    self.subscribe()
                except Exception as exc:
                    log.warning("backtest_subscribe_failed", error=str(exc))
                finally:
                    self.get_history = _orig_get_history  # type: ignore[method-assign]

            def next(self) -> None:
                """Drive the strategy via the event-bus path on each bar."""
                try:
                    close_val = float(self.data.close[0])
                except Exception:
                    return
                if math.isnan(close_val):
                    return

                # Use the key the strategy registered under (e.g. "BTC-USDT"/"1m")
                # rather than the run_backtest params — handles format differences.
                if self._bar_handlers:
                    bt_sym, bt_tf_str = next(iter(self._bar_handlers))
                else:
                    bt_sym, bt_tf_str = symbol, tf

                try:
                    ts_ms = int(self.data.datetime.datetime(0).timestamp() * 1000)
                except Exception:
                    ts_ms = 0

                def _s(attr: str, default: float = 0.0) -> float:
                    try:
                        v = float(getattr(self.data, attr)[0])
                        return default if math.isnan(v) else v
                    except Exception:
                        return default

                bar = _BarClose(
                    exchange=exchange,
                    symbol=bt_sym,
                    tf=bt_tf_str,
                    ts=ts_ms,
                    open=float(self.data.open[0]),
                    high=float(self.data.high[0]),
                    low=float(self.data.low[0]),
                    close=close_val,
                    volume=_s("volume"),
                    quote_volume=_s("quote_volume"),
                    trade_count=int(_s("trade_count")),
                    is_complete=True,
                )
                self.on_bar(bar)

            def notify_order(self, order: Any) -> None:
                super().notify_order(order)
                _apply_fill(self, order)

    else:
        # ── Pure bt.Strategy or already inherits bt.Strategy ─────────────────
        class _WrappedStrategy(cls):  # type: ignore[valid-type]
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                if _is_base_strategy:
                    from bot_service.config import get_settings
                    super().__init__(name=_bt_strategy_name, settings=get_settings())
                else:
                    super().__init__(*args, **kwargs)
                self._bt_total_fees: float = 0.0
                self._bt_trades: list[dict] = []
                self._bt_pending_entry: dict | None = None

            def notify_order(self, order: Any) -> None:
                super().notify_order(order)
                _apply_fill(self, order)

    commission_cls = _COMMISSION_MAP.get(exchange.lower())

    cerebro = bt.Cerebro()
    cerebro.adddata(feed)
    cerebro.addstrategy(_WrappedStrategy)
    cerebro.broker.setcash(initial_capital)
    if commission_cls is not None:
        cerebro.broker.addcommissioninfo(commission_cls())
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe", timeframe=bt.TimeFrame.Minutes)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")
    cerebro.addanalyzer(bt.analyzers.SQN, _name="sqn")
    cerebro.addobserver(EquitySampler, sample_every=sample_every)

    log.info("backtest_started", strategy=strategy_name, symbol=symbol, tf=tf,
             start=start_date, end=end_date)

    results = cerebro.run()
    final_value = cerebro.broker.getvalue()
    metrics = _compute_metrics(results, initial_capital, final_value)
    equity = collect_equity(results[0], sample_every)
    # Cap trades at 5 000 to keep the in-memory result compact.
    trades: list[dict] = getattr(results[0], "_bt_trades", [])[:5000]

    log.info("backtest_finished", strategy=strategy_name, run_id=run_id,
             total_return_pct=metrics["total_return_pct"], n_trades=metrics["n_trades"])

    return BacktestResult(
        run_id=run_id,
        strategy_name=strategy_name,
        hash=file_hash,
        symbol=symbol,
        tf=tf,
        exchange=exchange,
        start_date=start_date,
        end_date=end_date,
        initial_capital=initial_capital,
        final_value=final_value,
        equity_curve=equity,
        data_segments=feed.data_segments,
        trades=trades,
        **metrics,  # type: ignore[arg-type]
    )
