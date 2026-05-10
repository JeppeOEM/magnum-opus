"""L2 integration test — startup reconciliation with real QuestDB.

Simulates the crash-recovery scenario from Story 13.1 AC4:
  1. Write a 'placed' order_events row directly to QuestDB (bypassing in-memory state,
     simulating state after a crash where the process died before updating open_orders)
  2. Call run_startup_reconciliation() with a mock exchange client that confirms the order
  3. Assert restore_open_order() called with correct order_id; no place_order() issued
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

testcontainers = pytest.importorskip("testcontainers", reason="testcontainers not installed")

from testcontainers.core.container import DockerContainer  # noqa: E402

from bot_service.exchange import OpenOrder
from bot_service.persistence.schema import apply_schema
from bot_service.strategy.reconciliation import run_startup_reconciliation

_QUESTDB_IMAGE = "questdb/questdb:8.2.1"


# ── Fixtures ────────────────────────────────────────────────────────────────


def _wait_for_questdb(addr: str, timeout: int = 60) -> None:
    """Poll QuestDB /exec until it responds (mirrors test_schema.py pattern)."""
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
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
    raise TimeoutError(f"QuestDB at {addr} did not become ready within {timeout}s")


def _wait_for_row(base_url: str, order_id: str, timeout_s: float = 15.0) -> None:
    """Poll until the placed order_events row is visible (WAL async commit)."""
    query = f"SELECT order_id FROM order_events WHERE order_id = '{order_id}'"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{base_url}/exec", params={"query": query}, timeout=2.0)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("dataset"):
                    return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    raise TimeoutError(f"order_id {order_id!r} not visible in QuestDB within {timeout_s}s")


@pytest.fixture(scope="module")
def questdb_addrs() -> tuple[str, str]:  # type: ignore[return]
    """Start a real QuestDB container; yield (http_addr, ilp_addr)."""
    container = DockerContainer(_QUESTDB_IMAGE).with_exposed_ports(9000, 9009)
    with container:
        host = container.get_container_host_ip()
        http_port = container.get_exposed_port(9000)
        ilp_port = container.get_exposed_port(9009)
        http_addr = f"http://{host}:{http_port}"
        ilp_addr = f"{host}:{ilp_port}"
        _wait_for_questdb(http_addr, timeout=60)
        apply_schema(http_addr)
        yield http_addr, ilp_addr  # type: ignore[misc]


# ── Helpers ────────────────────────────────────────────────────────────────


def _write_placed_order(
    ilp_addr: str,
    order_id: str,
    strategy: str,
    symbol: str,
    side: str = "buy",
    order_type: str = "limit",
    size: float = 0.01,
    limit_price: float = 30000.0,
) -> None:
    """Write a status='placed' row directly to QuestDB via ILP (simulates crash-state)."""
    from questdb.ingress import Sender, TimestampNanos

    host, port_str = ilp_addr.split(":")
    ts_ns = TimestampNanos(int(time.time() * 1e9))
    with Sender.from_conf(f"tcp::addr={host}:{port_str};") as sender:
        sender.row(
            "order_events",
            symbols={
                "order_id": order_id,
                "client_order_id": f"client-{order_id}",
                "strategy": strategy,
                "exchange": "bybit",
                "symbol": symbol,
                "market_type": "spot",
                "side": side,
                "order_type": order_type,
                "status": "placed",
                "fee_currency": "USDT",
                "signal_type": "entry",
            },
            columns={
                "limit_price": limit_price,
                "stop_price": 0.0,
                "take_profit_price": 0.0,
                "requested_size": size,
                "filled_size": 0.0,
                "remaining_size": size,
                "avg_fill_price": 0.0,
                "fee": 0.0,
                "realized_pnl": 0.0,
                "slippage": 0.0,
                "position_size_after": 0.0,
                "paper_trading": False,
                "backtest": False,
                "ts_placed": int(time.time() * 1_000_000),
                "ts_exchange": 0,
            },
            at=ts_ns,
        )
        sender.flush()


def _open_order_fixture(order_id: str, symbol: str = "BTCUSDT") -> OpenOrder:
    return OpenOrder(
        order_id=order_id,
        symbol=symbol,
        side="buy",
        order_type="limit",
        size=0.01,
        limit_price=30000.0,
        status="open",
        ts_placed=1685000000000,
    )


# ── L2 test ────────────────────────────────────────────────────────────────


@pytest.mark.l2
async def test_reconciliation_restores_placed_order_after_crash(
    questdb_addrs: tuple[str, str],
) -> None:
    """L2: crash-state simulation — placed row in QuestDB → reconciliation restores it."""
    questdb_http, questdb_ilp = questdb_addrs
    order_id = f"crash-oid-{int(time.time())}"
    strategy_name = "crashteststrat"  # no hyphens for SYMBOL safety

    # Write crash-state row (simulates what OrderQueueWorker wrote before the crash)
    _write_placed_order(
        ilp_addr=questdb_ilp,
        order_id=order_id,
        strategy=strategy_name,
        symbol="BTCUSDT",
    )

    # Wait for WAL commit
    _wait_for_row(questdb_http, order_id, timeout_s=15)

    # Set up mock exchange client that confirms the order is still open
    exchange_client = MagicMock()
    exchange_client.get_open_orders = AsyncMock(
        return_value=[_open_order_fixture(order_id, "BTCUSDT")]
    )
    exchange_client.place_order = AsyncMock()

    # Set up mock order worker
    order_worker = MagicMock()
    order_worker.restore_open_order = MagicMock()

    from bot_service.strategy.base import BaseStrategy
    from bot_service.config import Settings

    class _Stub(BaseStrategy):
        @property
        def min_lookback(self) -> int:
            return 1

        @property
        def max_position_pct(self) -> float:
            return 0.1

        @property
        def stop_loss_pct(self) -> float:
            return 0.02

        @property
        def paper_trading(self) -> bool:
            return False

        @property
        def bus_timeout_seconds(self) -> int:
            return 300

        @property
        def close_on_bus_timeout(self) -> bool:
            return False

        def subscribe(self) -> None:
            pass

    strategy = _Stub(strategy_name, Settings())

    # Run startup reconciliation (the crash-recovery path)
    await run_startup_reconciliation(
        strategy=strategy,
        order_worker=order_worker,
        exchange_client=exchange_client,
        exchange="bybit",
        questdb_http_addr=questdb_http,
        questdb_ilp_addr=questdb_ilp,
        timeout_s=10.0,
    )

    # restore_open_order must be called exactly once with the correct order_id
    order_worker.restore_open_order.assert_called_once()
    restored_order_id = order_worker.restore_open_order.call_args[0][0]
    assert restored_order_id == order_id, (
        f"Expected restore_open_order with {order_id!r}, got {restored_order_id!r}"
    )

    # No duplicate order placed on exchange
    exchange_client.place_order.assert_not_called()
