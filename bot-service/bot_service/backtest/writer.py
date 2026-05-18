from __future__ import annotations

import calendar
import time
import uuid
from typing import Any

import backtrader as bt
import structlog
from questdb.ingress import Sender, TimestampNanos

log = structlog.get_logger()


class _CostBasisTracker:
    """FIFO cost-basis tracker for backtest fills (same algorithm as order_worker)."""

    def __init__(self) -> None:
        self._qty: dict[str, float] = {}
        self._avg: dict[str, float] = {}

    def record(self, symbol: str, side: str, qty: float, price: float) -> float:
        """Return realized_pnl for this fill (0 for opening fills)."""
        cur_qty = self._qty.get(symbol, 0.0)
        cur_avg = self._avg.get(symbol, 0.0)
        if side == "buy":
            new_qty = cur_qty + qty
            self._avg[symbol] = (
                (cur_avg * cur_qty + price * qty) / new_qty if new_qty else 0.0
            )
            self._qty[symbol] = new_qty
            return 0.0
        closed = min(qty, cur_qty)
        realized = closed * (price - cur_avg) if cur_qty > 0 else 0.0
        self._qty[symbol] = max(0.0, cur_qty - closed)
        if self._qty[symbol] == 0.0:
            self._avg[symbol] = 0.0
        return realized


_EXECTYPE_MAP = {
    bt.Order.Market: "market",
    bt.Order.Close: "market",
    bt.Order.Limit: "limit",
    bt.Order.Stop: "stop",
    bt.Order.StopLimit: "stop_limit",
    bt.Order.StopTrail: "stop_trail",
    bt.Order.StopTrailLimit: "stop_trail_limit",
}


class BacktestResultWriter:
    def __init__(
        self,
        questdb_ilp_addr: str,
        strategy_name: str,
        exchange: str,
        symbol: str,
        fee_currency: str = "USDT",
    ) -> None:
        self._questdb_ilp_addr = questdb_ilp_addr
        self._strategy_name = strategy_name
        self._exchange = exchange
        self._symbol = symbol
        self._fee_currency = fee_currency
        self._tracker = _CostBasisTracker()
        self._sender: Sender | None = None

    def open(self) -> None:
        """Open the QuestDB ILP TCP connection. No-op if already open."""
        if self._sender is not None:
            return
        host, _, port_str = self._questdb_ilp_addr.partition(":")
        port_str = port_str or "9009"
        self._sender = Sender.from_conf(f"tcp::addr={host}:{port_str};")
        self._sender.establish()

    def close(self) -> None:
        """Flush and close the QuestDB ILP TCP connection. Safe to call multiple times."""
        if self._sender is None:
            return
        try:
            self._sender.flush()
            self._sender.close()
        except Exception as exc:
            log.warning("backtest_ilp_close_failed", error=str(exc))
        finally:
            self._sender = None

    def __enter__(self) -> BacktestResultWriter:
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def write_fill(self, order: Any, position_size_after: float) -> None:
        created_dt = bt.num2date(order.created.dt)
        executed_dt = bt.num2date(order.executed.dt)
        # calendar.timegm interprets naive struct_time as UTC (correct: bt.num2date returns naive UTC)
        ts_placed_us = calendar.timegm(created_dt.timetuple()) * 1_000_000
        ts_exchange_us = calendar.timegm(executed_dt.timetuple()) * 1_000_000

        side = "buy" if order.isbuy() else "sell"
        order_type = _EXECTYPE_MAP.get(order.exectype, "market")

        # Assign stop/limit price fields by order type
        if order_type == "limit":
            limit_price = float(order.created.price)
            stop_price = 0.0
        elif order_type in ("stop", "stop_trail"):
            stop_price = float(order.created.price)
            limit_price = 0.0
        elif order_type in ("stop_limit", "stop_trail_limit"):
            stop_price = float(order.created.price)
            limit_price = float(getattr(order.created, "pricelimit", 0.0))
        else:  # market / close
            limit_price = 0.0
            stop_price = 0.0

        qty = abs(float(order.executed.size))
        price = float(order.executed.price)
        realized_pnl = self._tracker.record(self._symbol, side, qty, price)
        self._sync_ilp_write({
            "order_id": str(order.ref),
            "client_order_id": str(uuid.uuid4()),
            "strategy": self._strategy_name,
            "exchange": self._exchange,
            "symbol": self._symbol,
            "market_type": "spot",
            "side": side,
            "order_type": order_type,
            "status": "filled",
            "limit_price": limit_price,
            "stop_price": stop_price,
            "take_profit_price": 0.0,
            "requested_size": abs(float(order.created.size)),
            "filled_size": qty,
            "remaining_size": 0.0,
            "avg_fill_price": price,
            "fee": abs(float(order.executed.comm)),
            "fee_currency": self._fee_currency,
            "realized_pnl": realized_pnl,
            "slippage": 0.0,
            "position_size_after": float(position_size_after),
            "signal_type": "",
            "paper_trading": False,
            "backtest": True,
            "ts_placed": ts_placed_us,
            "ts_exchange": ts_exchange_us,
        })

    def _sync_ilp_write(self, fields: dict[str, Any]) -> None:
        """Fire-and-forget ILP write to order_events using the shared sender.

        Logs CRITICAL on failure. If sender is not open, logs CRITICAL and returns.
        """
        if self._sender is None:
            log.critical(
                "backtest_ilp_write_without_open",
                strategy=fields.get("strategy", ""),
            )
            return

        # Use backtest event time as designated timestamp so time-range Grafana queries work
        ts_exchange_us = int(fields.get("ts_exchange", 0))
        ts_at = (
            TimestampNanos(ts_exchange_us * 1000)
            if ts_exchange_us > 0
            else TimestampNanos(int(time.time() * 1e9))
        )

        symbols = {
            "order_id": str(fields.get("order_id", "")),
            "client_order_id": str(fields.get("client_order_id", "")),
            "strategy": str(fields.get("strategy", "")),
            "exchange": str(fields.get("exchange", "")),
            "symbol": str(fields.get("symbol", "")),
            "market_type": str(fields.get("market_type", "")),
            "side": str(fields.get("side", "")),
            "order_type": str(fields.get("order_type", "")),
            "status": str(fields.get("status", "")),
            "fee_currency": str(fields.get("fee_currency", "")),
            "signal_type": str(fields.get("signal_type", "")),
        }
        columns: dict[str, Any] = {
            "limit_price": float(fields.get("limit_price", 0.0)),
            "stop_price": float(fields.get("stop_price", 0.0)),
            "take_profit_price": float(fields.get("take_profit_price", 0.0)),
            "requested_size": float(fields.get("requested_size", 0.0)),
            "filled_size": float(fields.get("filled_size", 0.0)),
            "remaining_size": float(fields.get("remaining_size", 0.0)),
            "avg_fill_price": float(fields.get("avg_fill_price", 0.0)),
            "fee": float(fields.get("fee", 0.0)),
            "realized_pnl": float(fields.get("realized_pnl", 0.0)),
            "slippage": float(fields.get("slippage", 0.0)),
            "position_size_after": float(fields.get("position_size_after", 0.0)),
            "paper_trading": bool(fields.get("paper_trading", False)),
            "backtest": bool(fields.get("backtest", False)),
            "ts_placed": int(fields.get("ts_placed", 0)),
            "ts_exchange": int(fields.get("ts_exchange", 0)),
        }
        try:
            self._sender.row("order_events", symbols=symbols, columns=columns, at=ts_at)
            self._sender.flush()
        except Exception as exc:
            log.critical(
                "backtest_ilp_write_failed",
                strategy=fields.get("strategy", ""),
                error=str(exc),
            )


def run_backtest_and_persist(
    strategy_cls: type,
    feed: Any,
    writer: BacktestResultWriter,
    commission_info: Any | None = None,
    starting_cash: float = 10_000.0,
) -> list[Any]:
    """Run backtest and write each completed order to QuestDB via writer.

    Opens the writer's TCP connection if not already open; closes it on exit.
    Exceptions from strategy.next() are logged CRITICAL with last_bar_ts then re-raised.
    Rows already written remain in QuestDB with no rollback.
    """
    last_bar_ts: list[str] = [""]

    class _StrategyWithWriter(strategy_cls):  # type: ignore[misc]
        def next(self) -> None:
            last_bar_ts[0] = str(bt.num2date(self.data.datetime[0]))
            super().next()

        def notify_order(self, order: Any) -> None:
            super().notify_order(order)
            if order.status == order.Completed:
                pos_size = float(self.broker.getposition(self.data).size)
                writer.write_fill(order, pos_size)

    cerebro = bt.Cerebro()
    cerebro.adddata(feed)
    cerebro.addstrategy(_StrategyWithWriter)
    cerebro.broker.setcash(starting_cash)
    if commission_info is not None:
        cerebro.broker.addcommissioninfo(commission_info)

    with writer:
        try:
            return cerebro.run()  # type: ignore[no-any-return]
        except Exception as exc:
            log.critical(
                "backtest_run_failed",
                strategy=writer._strategy_name,
                last_bar_ts=last_bar_ts[0],
                error=str(exc),
            )
            raise
