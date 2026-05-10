from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot_service.bus.event_types import OrderFilled
from bot_service.exchange import ExchangeRESTError, OrderRequest, PlacedOrder
from bot_service.strategy.order_worker import OrderQueueWorker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_worker(
    max_position_pct: float = 0.10,
    portfolio_value_usd: float = 10_000.0,
    exchange_client: Any = None,
) -> OrderQueueWorker:
    if exchange_client is None:
        exchange_client = MagicMock()
    return OrderQueueWorker(
        strategy_name="test-strat",
        max_position_pct=max_position_pct,
        paper_trading=False,
        exchange_client=exchange_client,
        questdb_ilp_addr="localhost:9009",
        portfolio_value_usd=portfolio_value_usd,
    )


def _entry_req(symbol: str = "BTCUSDT", side: str = "buy", size: float = 0.01, limit_price: float = 30000.0) -> OrderRequest:
    return OrderRequest(
        strategy="test-strat",
        exchange="bybit",
        symbol=symbol,
        side=side,
        order_type="limit",
        order_role="entry",
        size=size,
        limit_price=limit_price,
    )


def _exit_req(symbol: str = "BTCUSDT", side: str = "sell") -> OrderRequest:
    return OrderRequest(
        strategy="test-strat",
        exchange="bybit",
        symbol=symbol,
        side=side,
        order_type="limit",
        order_role="exit",
        size=0.01,
        limit_price=31000.0,
    )


def _stop_req(symbol: str = "BTCUSDT", side: str = "sell") -> OrderRequest:
    return OrderRequest(
        strategy="test-strat",
        exchange="bybit",
        symbol=symbol,
        side=side,
        order_type="market",
        order_role="stop",
        size=0.01,
    )


def _placed(order_id: str = "oid-1", status: str = "placed") -> PlacedOrder:
    return PlacedOrder(
        order_id=order_id,
        client_order_id="",
        status=status,
        ts_exchange=1685000000000,
    )


def _fill(order_id: str = "oid-1") -> OrderFilled:
    return OrderFilled(
        order_id=order_id,
        exchange="bybit",
        symbol="BTCUSDT",
        side="buy",
        fill_price=30000.0,
        fill_size=0.01,
        fee=0.003,
        ts_exchange=1685000000100,
    )


# ---------------------------------------------------------------------------
# T1: Metric functions exist and increment
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_inc_order_queue_dedup_increments() -> None:
    import bot_service.metrics.prometheus as pm
    pm.get_registry()
    pm.inc_order_queue_dedup("strat-a", "BTCUSDT")  # must not raise


@pytest.mark.l1
def test_inc_risk_gate_block_increments() -> None:
    import bot_service.metrics.prometheus as pm
    pm.get_registry()
    pm.inc_risk_gate_block("strat-a", "ETHUSDT")  # must not raise


# ---------------------------------------------------------------------------
# T2: Entry dedup (AC1)
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_entry_dedup_discards_second_request() -> None:
    import bot_service.strategy.order_worker as ow_mod

    dedup_calls: list[tuple[str, str]] = []

    client = MagicMock()
    client.place_order = AsyncMock(return_value=_placed("oid-1"))
    worker = _make_worker(exchange_client=client)
    worker._write_order_event = AsyncMock()  # type: ignore[method-assign]

    # Patch dedup counter
    with patch.object(ow_mod, "inc_order_queue_dedup",
                      side_effect=lambda s, sym: dedup_calls.append((s, sym))):
        req1 = _entry_req()
        req2 = _entry_req()  # same symbol+side

        await worker._process(req1)
        await worker._process(req2)  # should be deduped

    assert client.place_order.call_count == 1
    assert len(dedup_calls) == 1
    assert dedup_calls[0] == ("test-strat", "BTCUSDT")


@pytest.mark.l1
async def test_exit_not_deduped_with_open_entry() -> None:
    client = MagicMock()
    client.place_order = AsyncMock(side_effect=[_placed("oid-1"), _placed("oid-2")])
    worker = _make_worker(exchange_client=client)
    worker._write_order_event = AsyncMock()  # type: ignore[method-assign]

    await worker._process(_entry_req())
    # Exit for same symbol — must NOT be deduped
    await worker._process(_exit_req())

    assert client.place_order.call_count == 2


@pytest.mark.l1
async def test_stop_not_deduped_with_open_entry() -> None:
    client = MagicMock()
    client.place_order = AsyncMock(side_effect=[_placed("oid-1"), _placed("oid-2")])
    worker = _make_worker(exchange_client=client)
    worker._write_order_event = AsyncMock()  # type: ignore[method-assign]

    await worker._process(_entry_req())
    await worker._process(_stop_req())

    assert client.place_order.call_count == 2


# ---------------------------------------------------------------------------
# T3: Risk gate (AC3)
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_risk_gate_blocks_oversized_order() -> None:
    import bot_service.strategy.order_worker as ow_mod

    risk_calls: list[tuple[str, str]] = []
    client = MagicMock()
    client.place_order = AsyncMock(return_value=_placed())

    # max_position_pct=0.10, portfolio=$10,000 → max=$1,000
    # order: size=1.0, limit_price=2000.0 → notional=$2,000 > $1,000
    worker = _make_worker(max_position_pct=0.10, portfolio_value_usd=10_000.0, exchange_client=client)
    worker._write_order_event = AsyncMock()  # type: ignore[method-assign]

    oversized = OrderRequest(
        strategy="test-strat",
        exchange="bybit",
        symbol="ETHUSDT",
        side="buy",
        order_type="limit",
        order_role="entry",
        size=1.0,
        limit_price=2000.0,  # notional = $2,000 > $1,000 limit
    )

    with patch.object(ow_mod, "inc_risk_gate_block",
                      side_effect=lambda s, sym: risk_calls.append((s, sym))):
        await worker._process(oversized)

    assert client.place_order.call_count == 0
    assert len(risk_calls) == 1
    assert risk_calls[0] == ("test-strat", "ETHUSDT")


@pytest.mark.l1
async def test_risk_gate_does_not_block_exit_orders() -> None:
    """Exit orders reduce position size; risk gate must never block them."""
    client = MagicMock()
    client.place_order = AsyncMock(side_effect=[_placed("oid-1"), _placed("oid-2")])
    # Tiny portfolio so any entry would hit gate, but exit must go through
    worker = _make_worker(max_position_pct=0.01, portfolio_value_usd=100.0, exchange_client=client)
    worker._write_order_event = AsyncMock()  # type: ignore[method-assign]

    # Simulate existing large position so exit would "exceed" if gated
    worker._position_notional["BTCUSDT"] = 200.0  # already over portfolio

    exit_order = OrderRequest(
        strategy="test-strat",
        exchange="bybit",
        symbol="BTCUSDT",
        side="sell",
        order_type="limit",
        order_role="exit",
        size=0.01,
        limit_price=30000.0,
    )
    await worker._process(exit_order)
    assert client.place_order.call_count == 1  # exit went through


@pytest.mark.l1
async def test_risk_gate_does_not_block_stop_orders() -> None:
    """Stop orders reduce position size; risk gate must never block them."""
    client = MagicMock()
    client.place_order = AsyncMock(return_value=_placed("oid-1"))
    worker = _make_worker(max_position_pct=0.01, portfolio_value_usd=100.0, exchange_client=client)
    worker._write_order_event = AsyncMock()  # type: ignore[method-assign]
    worker._position_notional["BTCUSDT"] = 200.0

    stop_order = OrderRequest(
        strategy="test-strat",
        exchange="bybit",
        symbol="BTCUSDT",
        side="sell",
        order_type="market",
        order_role="stop",
        size=0.01,
    )
    await worker._process(stop_order)
    assert client.place_order.call_count == 1


@pytest.mark.l1
async def test_risk_gate_allows_order_within_limit() -> None:
    client = MagicMock()
    client.place_order = AsyncMock(return_value=_placed())
    # max_position=$1,000; order notional=$300 → allowed
    worker = _make_worker(max_position_pct=0.10, portfolio_value_usd=10_000.0, exchange_client=client)
    worker._write_order_event = AsyncMock()  # type: ignore[method-assign]

    small_order = OrderRequest(
        strategy="test-strat",
        exchange="bybit",
        symbol="ETHUSDT",
        side="buy",
        order_type="limit",
        order_role="entry",
        size=0.1,
        limit_price=3000.0,  # notional=$300 < $1,000
    )
    await worker._process(small_order)
    assert client.place_order.call_count == 1


# ---------------------------------------------------------------------------
# T4: QuestDB writes for all outcomes (AC2)
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_placed_outcome_writes_placed_status() -> None:
    client = MagicMock()
    client.place_order = AsyncMock(return_value=_placed("oid-1", "placed"))
    worker = _make_worker(exchange_client=client)

    write_calls: list[dict[str, Any]] = []

    async def capture_write(**kwargs: Any) -> None:
        write_calls.append(kwargs)

    worker._write_order_event = capture_write  # type: ignore[method-assign]

    await worker._process(_entry_req())

    assert len(write_calls) == 1
    assert write_calls[0]["status"] == "placed"
    assert write_calls[0]["order_id"] == "oid-1"


@pytest.mark.l1
async def test_rejected_outcome_writes_rejected_status() -> None:
    client = MagicMock()
    client.place_order = AsyncMock(return_value=_placed("oid-rej", "rejected"))
    worker = _make_worker(exchange_client=client)

    write_calls: list[dict[str, Any]] = []

    async def capture_write(**kwargs: Any) -> None:
        write_calls.append(kwargs)

    worker._write_order_event = capture_write  # type: ignore[method-assign]

    await worker._process(_entry_req())

    assert len(write_calls) == 1
    assert write_calls[0]["status"] == "rejected"


@pytest.mark.l1
async def test_failed_outcome_writes_failed_status() -> None:
    client = MagicMock()
    client.place_order = AsyncMock(side_effect=ExchangeRESTError("timeout"))
    worker = _make_worker(exchange_client=client)

    write_calls: list[dict[str, Any]] = []

    async def capture_write(**kwargs: Any) -> None:
        write_calls.append(kwargs)

    worker._write_order_event = capture_write  # type: ignore[method-assign]

    await worker._process(_entry_req())

    assert len(write_calls) == 1
    assert write_calls[0]["status"] == "failed"


# ---------------------------------------------------------------------------
# T5: Crash-safety — QuestDB write BEFORE open_orders update (AC5)
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_questdb_write_happens_before_open_orders_update() -> None:
    client = MagicMock()
    client.place_order = AsyncMock(return_value=_placed("oid-1", "placed"))
    worker = _make_worker(exchange_client=client)

    open_orders_at_write_time: list[int] = []  # len of open_orders when write is called

    async def spy_write(**kwargs: Any) -> None:
        open_orders_at_write_time.append(len(worker.open_orders))

    worker._write_order_event = spy_write  # type: ignore[method-assign]

    await worker._process(_entry_req())

    # Write must have been called when open_orders was still empty
    assert open_orders_at_write_time == [0]
    # After _process completes, open_orders should have the order
    assert "oid-1" in worker.open_orders


# ---------------------------------------------------------------------------
# T6: Fill handling (AC4)
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_fill_removes_order_from_open_orders() -> None:
    client = MagicMock()
    client.place_order = AsyncMock(return_value=_placed("oid-1", "placed"))
    worker = _make_worker(exchange_client=client)
    worker._write_order_event = AsyncMock()  # type: ignore[method-assign]

    await worker._process(_entry_req())
    assert "oid-1" in worker.open_orders

    await worker.handle_fill(_fill("oid-1"))
    assert "oid-1" not in worker.open_orders


@pytest.mark.l1
async def test_fill_writes_filled_status() -> None:
    client = MagicMock()
    client.place_order = AsyncMock(return_value=_placed("oid-1", "placed"))
    worker = _make_worker(exchange_client=client)

    write_calls: list[dict[str, Any]] = []

    async def capture_write(**kwargs: Any) -> None:
        write_calls.append(kwargs)

    worker._write_order_event = capture_write  # type: ignore[method-assign]

    await worker._process(_entry_req())
    write_calls.clear()  # clear the 'placed' write

    await worker.handle_fill(_fill("oid-1"))

    assert len(write_calls) == 1
    assert write_calls[0]["status"] == "filled"
    assert write_calls[0]["filled_size"] == pytest.approx(0.01)


@pytest.mark.l1
async def test_fill_dedup_second_fill_ignored() -> None:
    client = MagicMock()
    client.place_order = AsyncMock(return_value=_placed("oid-1", "placed"))
    worker = _make_worker(exchange_client=client)

    write_calls: list[dict[str, Any]] = []

    async def capture_write(**kwargs: Any) -> None:
        write_calls.append(kwargs)

    worker._write_order_event = capture_write  # type: ignore[method-assign]

    await worker._process(_entry_req())
    write_calls.clear()

    await worker.handle_fill(_fill("oid-1"))
    await worker.handle_fill(_fill("oid-1"))  # duplicate

    assert len(write_calls) == 1  # only one fill processed


@pytest.mark.l1
async def test_fill_for_unknown_order_does_not_crash() -> None:
    worker = _make_worker()
    worker._write_order_event = AsyncMock()  # type: ignore[method-assign]
    # Should log warning but not raise
    await worker.handle_fill(_fill("unknown-order-id"))


# ---------------------------------------------------------------------------
# T7: restore_open_order supports crash-recovery dedup (AC5)
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_restore_open_order_prevents_duplicate_entry() -> None:
    """Simulate restart: restore_open_order called before run(), then dedup blocks same entry."""
    client = MagicMock()
    client.place_order = AsyncMock(return_value=_placed("oid-new"))
    worker = _make_worker(exchange_client=client)
    worker._write_order_event = AsyncMock()  # type: ignore[method-assign]

    req = _entry_req()
    placed = _placed("oid-1")
    worker.restore_open_order("oid-1", req, placed)

    # Attempt to place same entry again — must be deduped
    await worker._process(req)
    assert client.place_order.call_count == 0
