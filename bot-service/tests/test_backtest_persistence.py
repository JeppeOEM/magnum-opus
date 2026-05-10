from __future__ import annotations

import time
from collections.abc import Generator
from typing import Any

import backtrader as bt
import httpx
import pandas as pd
import pytest

testcontainers = pytest.importorskip("testcontainers", reason="testcontainers not installed")

from testcontainers.core.container import DockerContainer  # noqa: E402

from bot_service.backtest.writer import BacktestResultWriter, run_backtest_and_persist
from bot_service.persistence.schema import apply_schema


# ── Helpers ───────────────────────────────────────────────────────────────────


def _wait_for_questdb(addr: str, timeout: int = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{addr}/exec", params={"query": "SHOW TABLES"}, timeout=2.0)
            if resp.status_code == 200 and "dataset" in resp.json():
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
    raise TimeoutError(f"QuestDB at {addr} did not become ready within {timeout}s")


def _query_rows(http_addr: str, sql: str, timeout: int = 10) -> list[dict[str, Any]]:
    """Poll QuestDB until rows appear or timeout."""
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


def _make_feed(n: int = 10) -> Any:
    idx = pd.date_range("2024-01-01", periods=n, freq="s", tz="UTC")
    df = pd.DataFrame(
        {
            "open": [100.0] * n,
            "high": [101.0] * n,
            "low": [99.0] * n,
            "close": [100.0] * n,
            "volume": [1000.0] * n,
        },
        index=idx,
    )
    return bt.feeds.PandasData(dataname=df)


# ── Strategies ────────────────────────────────────────────────────────────────


class _SimpleBuyStrategy(bt.Strategy):  # type: ignore[misc]
    def __init__(self) -> None:
        self._bar = 0

    def next(self) -> None:
        self._bar += 1
        if self._bar == 1:
            self.buy(size=1)
        elif self._bar == 5:
            self.sell(size=1)


class _CrashStrategy(bt.Strategy):  # type: ignore[misc]
    def __init__(self) -> None:
        self._bar = 0

    def next(self) -> None:
        self._bar += 1
        if self._bar == 1:
            self.buy(size=1)
        elif self._bar == 3:
            raise RuntimeError("synthetic crash for test")


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def questdb_container() -> Generator[dict[str, str], None, None]:
    with DockerContainer("questdb/questdb:8.2.1").with_exposed_ports(9000, 9009) as qdb:
        host = qdb.get_container_host_ip()
        http_port = qdb.get_exposed_port(9000)
        ilp_port = qdb.get_exposed_port(9009)
        http_addr = f"http://{host}:{http_port}"
        ilp_addr = f"{host}:{ilp_port}"
        _wait_for_questdb(http_addr, timeout=60)
        apply_schema(http_addr)
        yield {"http": http_addr, "ilp": ilp_addr}


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.l2
def test_backtest_events_written_with_backtest_flag(questdb_container: dict[str, str]) -> None:
    writer = BacktestResultWriter(
        questdb_ilp_addr=questdb_container["ilp"],
        strategy_name="TestBot",
        exchange="kucoin",
        symbol="BTCUSDT",
    )
    run_backtest_and_persist(_SimpleBuyStrategy, _make_feed(), writer)

    rows = _query_rows(
        questdb_container["http"],
        "SELECT backtest, paper_trading, strategy FROM order_events WHERE strategy='TestBot' LIMIT 100",
    )
    assert len(rows) == 2, f"expected 2 order events, got {len(rows)}"
    for row in rows:
        assert row["backtest"] is True
        assert row["paper_trading"] is False


@pytest.mark.l2
def test_backtest_rows_isolated_from_live_rows(questdb_container: dict[str, str]) -> None:
    rows = _query_rows(
        questdb_container["http"],
        "SELECT * FROM order_events WHERE backtest=false LIMIT 100",
        timeout=3,
    )
    assert rows == [], f"expected no live rows, found {rows}"


@pytest.mark.l2
def test_fill_fields_populated(questdb_container: dict[str, str]) -> None:
    rows = _query_rows(
        questdb_container["http"],
        "SELECT side, avg_fill_price, filled_size, strategy FROM order_events WHERE strategy='TestBot' LIMIT 100",
    )
    assert len(rows) == 2
    sides = {row["side"] for row in rows}
    assert sides == {"buy", "sell"}
    for row in rows:
        assert float(row["avg_fill_price"]) > 0.0
        assert float(row["filled_size"]) > 0.0
        assert row["strategy"] == "TestBot"


@pytest.mark.l2
def test_exception_in_strategy_leaves_partial_rows(questdb_container: dict[str, str]) -> None:
    writer = BacktestResultWriter(
        questdb_ilp_addr=questdb_container["ilp"],
        strategy_name="CrashBot",
        exchange="kucoin",
        symbol="BTCUSDT",
    )
    with pytest.raises(RuntimeError, match="synthetic crash"):
        run_backtest_and_persist(_CrashStrategy, _make_feed(), writer)

    rows = _query_rows(
        questdb_container["http"],
        "SELECT backtest FROM order_events WHERE strategy='CrashBot' LIMIT 100",
    )
    assert len(rows) >= 1, "buy row written before crash must persist"
    assert all(row["backtest"] is True for row in rows)
