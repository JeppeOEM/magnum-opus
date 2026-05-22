from __future__ import annotations

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
from bot_service.strategy.base import BaseStrategy

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
    equity_curve: list[tuple[str, float]] = field(default_factory=list)
    # Contiguous segments of valid data found in the query window.
    # Each dict: {"start": ISO_str, "end": ISO_str, "bars": int}
    # Empty list means the whole window was usable (no gaps detected).
    data_segments: list[dict] = field(default_factory=list)


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


def _compute_metrics(
    results: list[Any],
    initial_capital: float,
    final_value: float,
) -> dict[str, float | int | bool]:
    strat = results[0]

    # SharpeRatio — can return None (no trades) or NaN (insufficient returns).
    # NaN is truthy in Python, so `val or 0.0` does NOT replace it; guard explicitly.
    sharpe = 0.0
    if hasattr(strat.analyzers, "sharpe"):
        sr = strat.analyzers.sharpe.get_analysis()
        _raw_sharpe = sr.get("sharperatio")
        if _raw_sharpe is not None and not (isinstance(_raw_sharpe, float) and math.isnan(_raw_sharpe)):
            sharpe = float(_raw_sharpe)

    # DrawDown
    # Note: backtrader's DrawDown analyzer can return None for "drawdown" when
    # there are no bars with a drawdown (e.g. equity only goes up, or no trades).
    # dict.get(key, default) returns None when the key EXISTS with value None;
    # the default only fires for missing keys.  Guard with `or 0.0`.
    max_dd = 0.0
    if hasattr(strat.analyzers, "drawdown"):
        dd = strat.analyzers.drawdown.get_analysis()
        max_dd = float(dd.get("max", {}).get("drawdown") or 0.0)

    # TradeAnalyzer
    n_trades = 0
    win_rate = 0.0
    avg_pnl = 0.0
    total_fees = 0.0
    if hasattr(strat.analyzers, "trades"):
        ta = strat.analyzers.trades.get_analysis()
        total_closed = int(ta.get("total", {}).get("closed") or 0)
        n_trades = total_closed
        won = int(ta.get("won", {}).get("total") or 0)
        win_rate = (won / total_closed * 100.0) if total_closed > 0 else 0.0
        pnl_net = ta.get("pnl", {}).get("net") or {}
        total_pnl = float(pnl_net.get("total") or 0.0)
        avg_pnl = total_pnl / total_closed if total_closed > 0 else 0.0

    # Fees: sum from completed orders
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

    Does NOT write strategy_snapshots or backtest_runs to QuestDB — that is
    the responsibility of the REST layer (story 27-4). Order fills are written
    to order_events (backtest=true) via BacktestResultWriter as before.
    """
    from datetime import datetime, timezone

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

    # Wrap strategy to track fees and hook BacktestResultWriter
    class _WrappedStrategy(cls):  # type: ignore[valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._bt_total_fees: float = 0.0

        def notify_order(self, order: Any) -> None:
            super().notify_order(order)
            if order.status == order.Completed:
                pos_size = float(self.broker.getposition(self.data).size)
                writer.write_fill(order, pos_size)
                self._bt_total_fees += abs(float(order.executed.comm))

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
    cerebro.addobserver(EquitySampler, sample_every=sample_every)

    log.info("backtest_started", strategy=strategy_name, symbol=symbol, tf=tf,
             start=start_date, end=end_date)

    results = cerebro.run()
    final_value = cerebro.broker.getvalue()
    metrics = _compute_metrics(results, initial_capital, final_value)
    equity = collect_equity(results[0], sample_every)

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
        **{k: v for k, v in metrics.items() if k != "equity_curve"},  # type: ignore[arg-type]
    )
