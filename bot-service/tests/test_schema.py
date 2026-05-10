from __future__ import annotations

import time

import httpx
import pytest

from bot_service.persistence.schema import SchemaApplyError, apply_schema

# Skip the entire module gracefully when testcontainers or Docker is unavailable.
testcontainers = pytest.importorskip("testcontainers", reason="testcontainers not installed")

from testcontainers.core.container import DockerContainer  # noqa: E402


def _wait_for_questdb(addr: str, timeout: int = 30) -> None:
    """Poll the QuestDB /exec endpoint until it responds with a valid JSON body.

    QuestDB 8.2.1 does not expose a /health endpoint — we use SHOW TABLES as a
    lightweight liveness probe instead.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = httpx.get(
                f"{addr}/exec",
                params={"query": "SHOW TABLES"},
                timeout=2.0,
            )
            if resp.status_code == 200 and "dataset" in resp.json():
                return
        except (httpx.HTTPError, Exception):  # noqa: BLE001
            pass
        time.sleep(1)
    raise TimeoutError(f"QuestDB at {addr} did not become ready within {timeout}s")


@pytest.fixture(scope="module")
def questdb_addr() -> str:  # type: ignore[return]
    """Start a real QuestDB container and return its HTTP base URL."""
    with DockerContainer("questdb/questdb:8.2.1").with_exposed_ports(9000) as qdb:
        host = qdb.get_container_host_ip()
        port = qdb.get_exposed_port(9000)
        addr = f"http://{host}:{port}"
        _wait_for_questdb(addr, timeout=60)
        yield addr  # type: ignore[misc]


@pytest.mark.l2
def test_apply_schema_idempotent(questdb_addr: str) -> None:
    """Calling apply_schema twice succeeds without error and both tables exist."""
    apply_schema(questdb_addr)
    apply_schema(questdb_addr)  # second call must not raise

    resp = httpx.get(f"{questdb_addr}/exec", params={"query": "SHOW TABLES"})
    resp.raise_for_status()
    tables = [row[0] for row in resp.json()["dataset"]]
    assert "order_events" in tables
    assert "order_alerts" in tables


@pytest.mark.l2
def test_apply_schema_order_events_columns(questdb_addr: str) -> None:
    """order_events has the correct column names and types after apply_schema."""
    apply_schema(questdb_addr)

    resp = httpx.get(
        f"{questdb_addr}/exec",
        params={"query": 'SELECT "column", type FROM table_columns(\'order_events\')'},
    )
    resp.raise_for_status()
    body = resp.json()
    col_types: dict[str, str] = {row[0]: row[1] for row in body["dataset"]}

    expected: dict[str, str] = {
        "ts": "TIMESTAMP",
        "order_id": "SYMBOL",
        "client_order_id": "SYMBOL",
        "strategy": "SYMBOL",
        "exchange": "SYMBOL",
        "symbol": "SYMBOL",
        "market_type": "SYMBOL",
        "side": "SYMBOL",
        "order_type": "SYMBOL",
        "status": "SYMBOL",
        "limit_price": "DOUBLE",
        "stop_price": "DOUBLE",
        "take_profit_price": "DOUBLE",
        "requested_size": "DOUBLE",
        "filled_size": "DOUBLE",
        "remaining_size": "DOUBLE",
        "avg_fill_price": "DOUBLE",
        "fee": "DOUBLE",
        "fee_currency": "SYMBOL",
        "realized_pnl": "DOUBLE",
        "slippage": "DOUBLE",
        "position_size_after": "DOUBLE",
        "signal_type": "SYMBOL",
        "paper_trading": "BOOLEAN",
        "backtest": "BOOLEAN",
        "ts_placed": "TIMESTAMP",
        "ts_exchange": "TIMESTAMP",
    }
    for col, expected_type in expected.items():
        assert col in col_types, f"Missing column: {col}"
        assert col_types[col] == expected_type, (
            f"Column {col}: expected {expected_type}, got {col_types[col]}"
        )


@pytest.mark.l2
def test_apply_schema_order_alerts_columns(questdb_addr: str) -> None:
    """order_alerts has the correct column names and types after apply_schema."""
    apply_schema(questdb_addr)

    resp = httpx.get(
        f"{questdb_addr}/exec",
        params={"query": 'SELECT "column", type FROM table_columns(\'order_alerts\')'},
    )
    resp.raise_for_status()
    body = resp.json()
    col_types: dict[str, str] = {row[0]: row[1] for row in body["dataset"]}

    expected: dict[str, str] = {
        "ts": "TIMESTAMP",
        "order_id": "SYMBOL",
        "strategy": "SYMBOL",
        "alert_type": "SYMBOL",
        "detail": "STRING",
        "resolved": "BOOLEAN",
    }
    for col, expected_type in expected.items():
        assert col in col_types, f"Missing column: {col}"
        assert col_types[col] == expected_type, (
            f"Column {col}: expected {expected_type}, got {col_types[col]}"
        )


@pytest.mark.l2
def test_apply_schema_unreachable() -> None:
    """apply_schema raises SchemaApplyError when QuestDB is unreachable."""
    with pytest.raises(SchemaApplyError):
        apply_schema("http://localhost:19999")  # nothing listening here
