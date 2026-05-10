# Story 14.5: Backtest Results Persistence

## Status: done

## Story

**As** mrqdt,
**I want** backtest order events written to the same QuestDB table as live and paper results,
**so that** strategy performance across all modes is queryable in a single Grafana panel.

## Acceptance Criteria

- **AC1:** Given a Backtrader run completing with order fill events, when results are persisted, then each completed order is written to QuestDB `order_events` via ILP with `backtest=true`, `paper_trading=false`; all columns (strategy, symbol, exchange, side, order_type, requested_size, filled_size, avg_fill_price, fee, ts_placed, ts_exchange) are populated from the Backtrader `Order` object; `order_id` is the string of `order.ref`.

- **AC2:** Given a Grafana query against `order_events`, when filtered with `WHERE backtest = true AND strategy = 'OFIBot'`, then only backtest rows for OFIBot appear; live rows (`backtest=false, paper_trading=false`) and paper rows (`paper_trading=true`) are excluded — the boolean flags are correctly stored and queryable.

- **AC3:** Given a backtest run that raises an exception in Backtrader `next()`, when the exception propagates, then any rows already written to `order_events` remain (no rollback); the failure is logged CRITICAL via structlog with `strategy`, `last_bar_ts`, and `error` fields; the partial run is identifiable in QuestDB by its `ts_exchange` range.

- **AC4:** Given `run_backtest_and_persist(strategy_cls, feed, commission_info, writer, starting_cash)`, when called, it creates a cerebro instance with a writer-injected strategy wrapper that calls `writer.write_fill(order, pos_size)` on each `order.Completed` notification; returns the strategy result list from `cerebro.run()`.

## Tasks / Subtasks

- [x] T1: Implement `BacktestResultWriter` in `bot_service/backtest/writer.py`
  - [x] T1.1: Implement `BacktestResultWriter.__init__(questdb_ilp_addr, strategy_name, exchange, symbol, fee_currency)`
  - [x] T1.2: Implement `write_fill(order, position_size_after)` — extract all fields from Backtrader `Order` object; call `_sync_ilp_write`
  - [x] T1.3: Implement `_sync_ilp_write(fields)` — mirrors `OrderQueueWorker._sync_ilp_write`; fire-and-forget; logs CRITICAL on failure

- [x] T2: Implement `run_backtest_and_persist` in `bot_service/backtest/writer.py`
  - [x] T2.1: Implement `_make_persisting_strategy(base_cls, writer)` — dynamic subclass with `notify_order` override
  - [x] T2.2: Implement `run_backtest_and_persist(strategy_cls, feed, commission_info, writer, starting_cash)` — cerebro wrapper that uses persisting strategy

- [x] T3: L2 integration test in `tests/test_backtest_persistence.py`
  - [x] T3.1: `questdb_container` fixture — DockerContainer with ports 9000 (HTTP) and 9009 (ILP) exposed; polls until ready; applies schema
  - [x] T3.2: `test_backtest_events_written_with_backtest_flag` — run synthetic backtest, assert rows in `order_events` with `backtest=true`
  - [x] T3.3: `test_backtest_rows_isolated_from_live_rows` — assert `WHERE backtest=false` returns 0 rows after backtest run
  - [x] T3.4: `test_exception_in_strategy_leaves_partial_rows` — raise in `next()` on bar 3, assert bars 1-2 rows remain in QuestDB

- [x] T4: Run full test suite — L1 regressions zero; mypy --strict clean

## Dev Notes

### File locations

- **CREATE** `bot_service/backtest/writer.py`
- **CREATE** `bot_service/tests/test_backtest_persistence.py`
- Do NOT modify `validation.py`, `commission.py`, `feeds.py`, or `order_worker.py`
- Test file goes in `bot-service/tests/` (top-level tests dir, same as `test_schema.py`)

### `BacktestResultWriter` — sync ILP writer

Mirrors `OrderQueueWorker._sync_ilp_write` exactly. Synchronous (no async) — backtest runs in a loop, not an async context.

```python
from __future__ import annotations

import time
import uuid
from typing import Any

import backtrader as bt  # type: ignore[import]
import structlog
from questdb.ingress import Sender, TimestampNanos

log = structlog.get_logger()


class BacktestResultWriter:
    def __init__(
        self,
        questdb_ilp_addr: str,  # "host:9009"
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

    def write_fill(self, order: Any, position_size_after: float) -> None:
        """Write a completed order fill to QuestDB order_events."""
        import calendar

        # Convert Backtrader num-date to UTC microseconds
        created_dt = bt.num2date(order.created.dt)
        executed_dt = bt.num2date(order.executed.dt)
        ts_placed_us = calendar.timegm(created_dt.timetuple()) * 1_000_000
        ts_exchange_us = calendar.timegm(executed_dt.timetuple()) * 1_000_000

        side = "buy" if order.isbuy() else "sell"
        exectype_map = {
            bt.Order.Market: "market",
            bt.Order.Limit: "limit",
            bt.Order.Stop: "stop",
            bt.Order.StopLimit: "stop_limit",
        }
        order_type = exectype_map.get(order.exectype, "market")
        limit_price = float(order.created.price) if order_type != "market" else 0.0

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
            "stop_price": 0.0,
            "take_profit_price": 0.0,
            "requested_size": abs(float(order.created.size)),
            "filled_size": abs(float(order.executed.size)),
            "remaining_size": 0.0,
            "avg_fill_price": float(order.executed.price),
            "fee": abs(float(order.executed.comm)),
            "fee_currency": self._fee_currency,
            "realized_pnl": 0.0,
            "slippage": 0.0,
            "position_size_after": float(position_size_after),
            "signal_type": "",
            "paper_trading": False,
            "backtest": True,
            "ts_placed": ts_placed_us,
            "ts_exchange": ts_exchange_us,
        })

    def _sync_ilp_write(self, fields: dict[str, Any]) -> None:
        """Fire-and-forget ILP write. Logs CRITICAL on failure."""
        host, port_str = self._questdb_ilp_addr.split(":")
        ts_ns = TimestampNanos(int(time.time() * 1e9))
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
            with Sender.from_conf(f"tcp::addr={host}:{port_str};") as sender:
                sender.row("order_events", symbols=symbols, columns=columns, at=ts_ns)
                sender.flush()
        except Exception as exc:
            log.critical(
                "backtest_ilp_write_failed",
                strategy=fields.get("strategy", ""),
                error=str(exc),
            )
```

**`order.executed.comm` is negative for BUY fills in some commission models** — use `abs()` to always store positive fee.

**`order.created.size` is negative for SELL orders** in backtrader — use `abs()` for `requested_size` and `filled_size`.

### `run_backtest_and_persist` — cerebro runner

```python
def _make_persisting_strategy(base_cls: type, writer: BacktestResultWriter) -> type:
    class _StrategyWithWriter(base_cls):  # type: ignore[valid-type]
        def notify_order(self, order: Any) -> None:
            super().notify_order(order)  # type: ignore[misc]
            if order.status == order.Completed:
                pos_size = float(self.broker.getposition(self.data).size)  # type: ignore[misc]
                writer.write_fill(order, pos_size)
    return _StrategyWithWriter


def run_backtest_and_persist(
    strategy_cls: type,
    feed: Any,
    writer: BacktestResultWriter,
    commission_info: Any | None = None,
    starting_cash: float = 10_000.0,
) -> list[Any]:
    """Run backtest and write each completed order to QuestDB via writer.

    Returns cerebro results list. Exceptions from strategy.next() propagate
    after any rows already written remain in QuestDB (no rollback).
    """
    persisting_cls = _make_persisting_strategy(strategy_cls, writer)
    cerebro = bt.Cerebro()
    cerebro.adddata(feed)
    cerebro.addstrategy(persisting_cls)
    cerebro.broker.setcash(starting_cash)
    if commission_info is not None:
        cerebro.broker.addcommissioninfo(commission_info)
    return cerebro.run()  # type: ignore[no-any-return]
```

### L2 test setup — testcontainers with two ports

```python
from testcontainers.core.container import DockerContainer

@pytest.fixture(scope="module")
def questdb_container() -> Generator[dict[str, str], None, None]:
    """Start QuestDB and return http_addr and ilp_addr."""
    with (
        DockerContainer("questdb/questdb:8.2.1")
        .with_exposed_ports(9000, 9009) as qdb
    ):
        host = qdb.get_container_host_ip()
        http_port = qdb.get_exposed_port(9000)
        ilp_port = qdb.get_exposed_port(9009)
        http_addr = f"http://{host}:{http_port}"
        ilp_addr = f"{host}:{ilp_port}"
        _wait_for_questdb(http_addr, timeout=60)
        apply_schema(http_addr)
        yield {"http": http_addr, "ilp": ilp_addr}
```

**Port mapping**: `qdb.get_exposed_port(9009)` returns the host-mapped port for ILP TCP.

**Do NOT expose 9009 via `with_exposed_ports(9000).with_exposed_ports(9009)`** — backtrader uses one call: `.with_exposed_ports(9000, 9009)` (testcontainers-python accepts `*args`).

### Synthetic L2 test strategy

For L2 tests, use a minimal strategy that makes a predictable buy on bar 1 and sell on bar 5:

```python
class _SimpleBuyStrategy(bt.Strategy):
    def __init__(self) -> None:
        self._bar = 0

    def next(self) -> None:
        self._bar += 1
        if self._bar == 1:
            self.buy(size=1)
        elif self._bar == 5:
            self.sell(size=1)
```

For AC3 test (exception mid-run), use a strategy that raises on bar 3:

```python
class _CrashStrategy(bt.Strategy):
    def __init__(self) -> None:
        self._bar = 0

    def next(self) -> None:
        self._bar += 1
        if self._bar == 1:
            self.buy(size=1)
        elif self._bar == 3:
            raise RuntimeError("synthetic crash for test")
```

### Synthetic feed for L2 tests

Use 10 bars of 1-second OHLCV data:

```python
import pandas as pd
import backtrader as bt

def _make_feed(n: int = 10) -> bt.feeds.PandasData:
    idx = pd.date_range("2024-01-01", periods=n, freq="s", tz="UTC")
    df = pd.DataFrame({
        "open": [100.0] * n,
        "high": [101.0] * n,
        "low": [99.0] * n,
        "close": [100.0] * n,
        "volume": [1000.0] * n,
    }, index=idx)
    return bt.feeds.PandasData(dataname=df)
```

### Querying QuestDB after write

Poll QuestDB HTTP exec endpoint; ILP writes are async-committed — wait up to 5s for rows to appear:

```python
import time
import httpx

def _query_rows(http_addr: str, sql: str, timeout: int = 5) -> list[dict]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = httpx.get(f"{http_addr}/exec", params={"query": sql}, timeout=2.0)
        resp.raise_for_status()
        data = resp.json()
        if data.get("count", 0) > 0:
            cols = [c["name"] for c in data["columns"]]
            return [dict(zip(cols, row)) for row in data["dataset"]]
        time.sleep(0.5)
    return []
```

**Use `LIMIT 100` in SQL queries** to avoid large result sets.

### mypy --strict notes

- `from typing import Any` for all backtrader types
- `import backtrader as bt` — no `# type: ignore` needed (ignore_errors=true covers it)
- `_make_persisting_strategy` returns `type` which requires `# type: ignore[valid-type]` on class body
- `structlog.get_logger()` at module level
- `calendar.timegm()` returns `int` — no cast needed
- `questdb.ingress.Sender` and `TimestampNanos` — check if stubs need `# type: ignore[import]`

### Learnings from Stories 14.2–14.4

- backtrader imports need no `# type: ignore[import]` (ignore_errors=true)
- `order.executed.comm` and `order.created.size` can be negative — always `abs()`
- L2 tests use `@pytest.mark.l2`, `scope="module"` fixtures, and `pytest.importorskip("testcontainers")`
- `bt.num2date()` returns naive datetime representing UTC — use `calendar.timegm()` not `datetime.timestamp()`
- ILP port 9009 must be exposed in the testcontainer alongside HTTP port 9000
- QuestDB commits ILP writes asynchronously — poll after write with timeout

## Test Coverage

All tests `@pytest.mark.l2`. Target: 4 tests in `tests/test_backtest_persistence.py`.

### L2 integration tests (4)
1. `test_backtest_events_written_with_backtest_flag` — run `_SimpleBuyStrategy` (1 buy + 1 sell), assert 2 rows in `order_events` with `backtest=true`, `paper_trading=false`
2. `test_backtest_rows_isolated_from_live_rows` — query `WHERE backtest=false`, assert 0 rows returned (backtest does not contaminate live namespace)
3. `test_fill_fields_populated` — assert `avg_fill_price > 0`, `filled_size > 0`, `strategy = 'TestBot'`, `side IN ('buy', 'sell')`
4. `test_exception_in_strategy_leaves_partial_rows` — `_CrashStrategy` raises on bar 3 after 1 buy; assert the buy row exists in QuestDB despite exception

## Senior Developer Review (AI)

**Date:** 2026-05-10
**Outcome:** Changes Requested — 4 patches applied

### Action Items

- [x] [HIGH] AC3 violated — no CRITICAL log on `cerebro.run()` exception; `last_bar_ts` never tracked; re-raised exception unidentifiable in logs. Fixed: `last_bar_ts: list[str] = [""]` closure in `next()` override; `try/except` around `cerebro.run()` with `log.critical("backtest_run_failed", strategy=..., last_bar_ts=..., error=...)`.
- [x] [MED] Designated timestamp `at=` used wall-clock time; Grafana time-range queries on backtest data would return rows only in "now" bucket, not the simulated period. Fixed: `ts_at = TimestampNanos(ts_exchange_us * 1000)` when `ts_exchange_us > 0`.
- [x] [MED] `stop_price` and `limit_price` incorrectly assigned for Stop/StopLimit orders — `order.created.price` is the stop trigger for Stop orders, not a limit. Fixed: if/elif/else by `order_type` with `pricelimit` for stop-limit price.
- [x] [LOW] `_EXECTYPE_MAP` missing `StopTrail` and `StopTrailLimit` — unknown exec types silently fell through to "market". Fixed: added entries mapping to "stop_trail" and "stop_trail_limit".

All 4 patches applied; mypy --strict clean; 4 L2 tests pass.

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Completion Notes

- `BacktestResultWriter._sync_ilp_write`: mirrors `OrderQueueWorker._sync_ilp_write`; `symbols` dict + `columns` dict; `Sender.from_conf` TCP; logs CRITICAL on failure
- `write_fill`: uses `calendar.timegm()` (not `datetime.timestamp()`) for UTC timestamp — `bt.num2date()` returns naive UTC datetimes
- `order.created.size`/`order.executed.comm` are negative for sells — always `abs()`
- `_make_persisting_strategy`: class factory with `notify_order` override; `# type: ignore[misc]` on subclass of `Any`
- `testcontainers` added to mypy overrides in pyproject.toml (`ignore_missing_imports = true`)
- L2 tests poll QuestDB HTTP with 0.5s sleep loop; ILP writes are async-committed (not immediately visible)
- 4 L2 tests; 164 L1 green; mypy --strict clean

### File List

- `bot_service/backtest/writer.py` (CREATE)
- `bot_service/tests/test_backtest_persistence.py` (CREATE)
- `pyproject.toml` (UPDATE — added testcontainers mypy override)
