from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot_service.bus.event_types import OrderFilled
from bot_service.exchange import ExchangeRESTError, OrderRequest, PlacedOrder
from bot_service.strategy.circuit_breaker import DailyLossCircuitBreaker
from bot_service.strategy.order_worker import OrderQueueWorker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_worker(
    max_position_pct: float = 0.10,
    portfolio_value_usd: float = 10_000.0,
    exchange_client: Any = None,
    max_order_notional_usd: float = 0.0,
    circuit_breaker: DailyLossCircuitBreaker | None = None,
    on_circuit_breaker_trip: Any = None,
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
        max_order_notional_usd=max_order_notional_usd,
        circuit_breaker=circuit_breaker,
        on_circuit_breaker_trip=on_circuit_breaker_trip,
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


# ---------------------------------------------------------------------------
# T8: Hard limit (24-3)
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_hard_limit_blocks_oversized_order() -> None:
    client = MagicMock()
    client.place_order = AsyncMock(return_value=_placed())
    # Hard limit: $500; order notional = size=0.1 × limit=6000 = $600 > $500
    worker = _make_worker(exchange_client=client, max_order_notional_usd=500.0)
    worker._write_order_event = AsyncMock()  # type: ignore[method-assign]

    req = OrderRequest(
        strategy="test-strat",
        exchange="bybit",
        symbol="ETHUSDT",
        side="buy",
        order_type="limit",
        order_role="entry",
        size=0.1,
        limit_price=6000.0,
    )
    await worker._process(req)
    assert client.place_order.call_count == 0


@pytest.mark.l1
async def test_hard_limit_disabled_allows_same_order() -> None:
    client = MagicMock()
    client.place_order = AsyncMock(return_value=_placed())
    # max_order_notional_usd=0 → disabled; same order should pass
    worker = _make_worker(
        exchange_client=client,
        max_position_pct=1.0,
        portfolio_value_usd=100_000.0,
        max_order_notional_usd=0.0,
    )
    worker._write_order_event = AsyncMock()  # type: ignore[method-assign]

    req = OrderRequest(
        strategy="test-strat",
        exchange="bybit",
        symbol="ETHUSDT",
        side="buy",
        order_type="limit",
        order_role="entry",
        size=0.1,
        limit_price=6000.0,
    )
    await worker._process(req)
    assert client.place_order.call_count == 1


@pytest.mark.l1
async def test_hard_limit_exempt_for_exit_orders() -> None:
    client = MagicMock()
    client.place_order = AsyncMock(return_value=_placed())
    # Hard limit very small; exit should still go through
    worker = _make_worker(exchange_client=client, max_order_notional_usd=1.0)
    worker._write_order_event = AsyncMock()  # type: ignore[method-assign]

    exit_req = OrderRequest(
        strategy="test-strat",
        exchange="bybit",
        symbol="BTCUSDT",
        side="sell",
        order_type="limit",
        order_role="exit",
        size=0.01,
        limit_price=30000.0,  # notional $300 >> $1 cap
    )
    await worker._process(exit_req)
    assert client.place_order.call_count == 1


# ---------------------------------------------------------------------------
# T9: restore_open_order supports crash-recovery dedup (AC5)
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


# ---------------------------------------------------------------------------
# T10: Cost-basis tracking — Story 25-1 + 25-2
# ---------------------------------------------------------------------------


def test_update_position_buy_opens() -> None:
    w = _make_worker()
    pnl = w._update_position("BTCUSDT", "buy", 1.0, 50_000.0)
    assert pnl == 0.0
    assert w._position_qty["BTCUSDT"] == 1.0
    assert w._position_avg_price["BTCUSDT"] == 50_000.0


def test_update_position_buy_increases_avg() -> None:
    w = _make_worker()
    w._update_position("BTCUSDT", "buy", 1.0, 50_000.0)
    w._update_position("BTCUSDT", "buy", 1.0, 52_000.0)
    assert w._position_qty["BTCUSDT"] == 2.0
    assert w._position_avg_price["BTCUSDT"] == pytest.approx(51_000.0)


def test_update_position_sell_profitable() -> None:
    w = _make_worker()
    w._update_position("BTCUSDT", "buy", 1.0, 50_000.0)
    w._update_position("BTCUSDT", "buy", 1.0, 52_000.0)
    pnl = w._update_position("BTCUSDT", "sell", 1.0, 54_000.0)
    assert pnl == pytest.approx(3_000.0)
    assert w._position_qty["BTCUSDT"] == 1.0


def test_update_position_sell_at_loss_clears() -> None:
    w = _make_worker()
    w._update_position("BTCUSDT", "buy", 1.0, 50_000.0)
    w._update_position("BTCUSDT", "buy", 1.0, 52_000.0)
    w._update_position("BTCUSDT", "sell", 1.0, 54_000.0)
    pnl = w._update_position("BTCUSDT", "sell", 1.0, 48_000.0)
    assert pnl == pytest.approx(-3_000.0)
    assert w._position_qty["BTCUSDT"] == 0.0
    assert w._position_avg_price["BTCUSDT"] == 0.0


@pytest.mark.l1
async def test_handle_fill_writes_realized_pnl() -> None:
    """Sell fill after profitable buy writes non-zero realized_pnl."""
    worker = _make_worker()

    buy_req = _entry_req(side="buy", size=1.0, limit_price=50_000.0)
    buy_placed = _placed("oid-buy")
    worker.open_orders["oid-buy"] = (buy_req, buy_placed)
    worker._update_position("BTCUSDT", "buy", 1.0, 50_000.0)

    sell_req = _exit_req(side="sell")
    sell_req = OrderRequest(
        strategy="test-strat",
        exchange="bybit",
        symbol="BTCUSDT",
        side="sell",
        order_type="limit",
        order_role="exit",
        size=1.0,
        limit_price=54_000.0,
    )
    sell_placed = _placed("oid-sell")
    worker.open_orders["oid-sell"] = (sell_req, sell_placed)

    written_fields: list[dict] = []

    async def capture(**kwargs):  # type: ignore[misc]
        written_fields.append(kwargs)

    worker._write_order_event = capture  # type: ignore[method-assign]

    fill = OrderFilled(
        order_id="oid-sell",
        exchange="bybit",
        symbol="BTCUSDT",
        side="sell",
        fill_size=1.0,
        fill_price=54_000.0,
        fee=0.0,
        ts_exchange=0,
    )
    await worker.handle_fill(fill)

    assert len(written_fields) == 1
    assert written_fields[0]["realized_pnl"] == pytest.approx(4_000.0)


@pytest.mark.l1
async def test_handle_fill_slippage_computed() -> None:
    worker = _make_worker()
    buy_req = _entry_req(side="buy", size=1.0, limit_price=50_000.0)
    buy_placed = _placed("oid-b")
    worker.open_orders["oid-b"] = (buy_req, buy_placed)

    written_fields: list[dict] = []

    async def capture(**kwargs):  # type: ignore[misc]
        written_fields.append(kwargs)

    worker._write_order_event = capture  # type: ignore[method-assign]

    fill = OrderFilled(
        order_id="oid-b",
        exchange="bybit",
        symbol="BTCUSDT",
        side="buy",
        fill_size=1.0,
        fill_price=50_050.0,  # 50 pts of positive slippage on a buy
        fee=0.0,
        ts_exchange=0,
    )
    await worker.handle_fill(fill)
    # slippage = (50050 - 50000) * 1.0 = 50.0
    assert written_fields[0]["slippage"] == pytest.approx(50.0)


@pytest.mark.l1
async def test_handle_fill_opening_buy_pnl_zero() -> None:
    worker = _make_worker()
    req = _entry_req(side="buy", size=1.0, limit_price=50_000.0)
    placed = _placed("oid-b2")
    worker.open_orders["oid-b2"] = (req, placed)

    written_fields: list[dict] = []

    async def capture(**kwargs):  # type: ignore[misc]
        written_fields.append(kwargs)

    worker._write_order_event = capture  # type: ignore[method-assign]

    fill = OrderFilled(
        order_id="oid-b2",
        exchange="bybit",
        symbol="BTCUSDT",
        side="buy",
        fill_size=1.0,
        fill_price=50_000.0,
        fee=0.0,
        ts_exchange=0,
    )
    await worker.handle_fill(fill)
    assert written_fields[0]["realized_pnl"] == pytest.approx(0.0)


@pytest.mark.l1
async def test_circuit_breaker_receives_realized_pnl() -> None:
    cb = DailyLossCircuitBreaker(limit_usd=1_000.0)
    trip_called: list[bool] = []
    worker = _make_worker(
        circuit_breaker=cb,
        on_circuit_breaker_trip=lambda: trip_called.append(True),
    )
    # Seed a position so the sell has P&L
    worker._update_position("BTCUSDT", "buy", 1.0, 50_000.0)

    req = OrderRequest(
        strategy="test-strat",
        exchange="bybit",
        symbol="BTCUSDT",
        side="sell",
        order_type="limit",
        order_role="exit",
        size=1.0,
        limit_price=48_000.0,
    )
    placed = _placed("oid-cb")
    worker.open_orders["oid-cb"] = (req, placed)
    worker._write_order_event = AsyncMock()  # type: ignore[method-assign]

    fill = OrderFilled(
        order_id="oid-cb",
        exchange="bybit",
        symbol="BTCUSDT",
        side="sell",
        fill_size=1.0,
        fill_price=48_000.0,
        fee=0.0,
        ts_exchange=0,
    )
    await worker.handle_fill(fill)
    # realized_pnl = -2000; exceeds limit_usd=1000
    assert cb.is_tripped()
    assert trip_called == [True]


# ---------------------------------------------------------------------------
# T11: restore_position — Story 25-1 (P4 patch)
# ---------------------------------------------------------------------------


def test_restore_position_sets_state() -> None:
    w = _make_worker()
    w.restore_position("BTCUSDT", qty=2.0, avg_price=51_000.0)
    assert w._position_qty["BTCUSDT"] == 2.0
    assert w._position_avg_price["BTCUSDT"] == 51_000.0


def test_restore_position_then_sell_uses_restored_avg() -> None:
    w = _make_worker()
    w.restore_position("BTCUSDT", qty=1.0, avg_price=50_000.0)
    pnl = w._update_position("BTCUSDT", "sell", 1.0, 53_000.0)
    assert pnl == pytest.approx(3_000.0)
    assert w._position_qty["BTCUSDT"] == 0.0


@pytest.mark.l1
async def test_handle_fill_loss_close_writes_negative_pnl() -> None:
    """Sell fill at a loss writes realized_pnl < 0 to order_events (AC 25-2)."""
    worker = _make_worker()
    worker._update_position("BTCUSDT", "buy", 1.0, 50_000.0)

    req = OrderRequest(
        strategy="test-strat",
        exchange="bybit",
        symbol="BTCUSDT",
        side="sell",
        order_type="limit",
        order_role="exit",
        size=1.0,
        limit_price=48_000.0,
    )
    placed = _placed("oid-loss")
    worker.open_orders["oid-loss"] = (req, placed)

    written_fields: list[dict] = []

    async def capture(**kwargs):  # type: ignore[misc]
        written_fields.append(kwargs)

    worker._write_order_event = capture  # type: ignore[method-assign]

    fill = OrderFilled(
        order_id="oid-loss",
        exchange="bybit",
        symbol="BTCUSDT",
        side="sell",
        fill_size=1.0,
        fill_price=48_000.0,
        fee=0.0,
        ts_exchange=0,
    )
    await worker.handle_fill(fill)

    assert len(written_fields) == 1
    assert written_fields[0]["realized_pnl"] == pytest.approx(-2_000.0)
    assert written_fields[0]["realized_pnl"] < 0


@pytest.mark.l1
async def test_circuit_breaker_writes_correct_pnl_to_order_events() -> None:
    """Circuit breaker integration: the exact realized_pnl value must be written (P5 patch)."""
    cb = DailyLossCircuitBreaker(limit_usd=5_000.0)  # high limit — won't trip
    worker = _make_worker(circuit_breaker=cb)
    worker._update_position("BTCUSDT", "buy", 1.0, 50_000.0)

    req = OrderRequest(
        strategy="test-strat",
        exchange="bybit",
        symbol="BTCUSDT",
        side="sell",
        order_type="limit",
        order_role="exit",
        size=1.0,
        limit_price=52_000.0,
    )
    placed = _placed("oid-pnl-check")
    worker.open_orders["oid-pnl-check"] = (req, placed)

    written_fields: list[dict] = []

    async def capture(**kwargs):  # type: ignore[misc]
        written_fields.append(kwargs)

    worker._write_order_event = capture  # type: ignore[method-assign]

    fill = OrderFilled(
        order_id="oid-pnl-check",
        exchange="bybit",
        symbol="BTCUSDT",
        side="sell",
        fill_size=1.0,
        fill_price=52_000.0,
        fee=0.0,
        ts_exchange=0,
    )
    await worker.handle_fill(fill)
    # realized_pnl = (52000 - 50000) * 1.0 = 2000
    assert written_fields[0]["realized_pnl"] == pytest.approx(2_000.0)
    # circuit breaker also received the correct value
    assert cb.daily_pnl == pytest.approx(2_000.0)
