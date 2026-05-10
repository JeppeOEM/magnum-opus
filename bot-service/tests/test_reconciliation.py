from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot_service.exchange import OpenOrder
from bot_service.strategy.reconciliation import detect_orphaned_orders


def _open_order(order_id: str = "oid-1", symbol: str = "BTCUSDT", exchange: str = "bybit") -> OpenOrder:
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


# ---------------------------------------------------------------------------
# T1: Metric function
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_inc_orphaned_order_increments() -> None:
    import bot_service.metrics.prometheus as pm
    pm.get_registry()
    pm.inc_orphaned_order("bybit")  # must not raise


# ---------------------------------------------------------------------------
# T2: detect_orphaned_orders
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_orphan_found_writes_alert_and_increments(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exchange has an open order not in QuestDB → alert written, counter incremented, no cancel."""
    import bot_service.strategy.reconciliation as rec_mod

    orphan_calls: list[str] = []
    monkeypatch.setattr(rec_mod, "inc_orphaned_order",
                        lambda exc: orphan_calls.append(exc))

    client = MagicMock()
    client.place_order = AsyncMock()
    client.cancel_order = AsyncMock()
    client.get_open_orders = AsyncMock(return_value=[_open_order("unknown-oid")])

    write_calls: list[dict] = []

    async def fake_write_alert(**kwargs):  # type: ignore[no-untyped-def]
        write_calls.append(kwargs)

    with patch.object(rec_mod, "_write_order_alert", fake_write_alert):
        # known_ids (from QuestDB) does NOT contain "unknown-oid"
        await detect_orphaned_orders(
            exchange_client=client,
            exchange="bybit",
            questdb_ilp_addr="localhost:9009",
            strategy_name="test-strat",
            known_order_ids=set(),
        )

    assert len(write_calls) == 1
    assert write_calls[0]["alert_type"] == "orphaned_order"
    assert "unknown-oid" in write_calls[0]["detail"]
    assert len(orphan_calls) == 1
    assert orphan_calls[0] == "bybit"
    client.cancel_order.assert_not_called()


@pytest.mark.l1
async def test_known_order_not_treated_as_orphan() -> None:
    """Exchange order whose order_id is in QuestDB → no alert, no counter."""
    import bot_service.strategy.reconciliation as rec_mod

    client = MagicMock()
    client.cancel_order = AsyncMock()
    client.get_open_orders = AsyncMock(return_value=[_open_order("known-oid")])

    orphan_calls: list[str] = []

    with patch.object(rec_mod, "inc_orphaned_order",
                      side_effect=lambda exc: orphan_calls.append(exc)):
        with patch.object(rec_mod, "_write_order_alert", AsyncMock()) as mock_write:
            await detect_orphaned_orders(
                exchange_client=client,
                exchange="bybit",
                questdb_ilp_addr="localhost:9009",
                strategy_name="test-strat",
                known_order_ids={"known-oid"},
            )
            mock_write.assert_not_called()

    assert len(orphan_calls) == 0
    client.cancel_order.assert_not_called()


@pytest.mark.l1
async def test_mixed_orders_only_orphans_trigger_alert() -> None:
    """3 exchange orders: 1 orphan, 2 known → only 1 alert."""
    import bot_service.strategy.reconciliation as rec_mod

    client = MagicMock()
    client.cancel_order = AsyncMock()
    client.get_open_orders = AsyncMock(return_value=[
        _open_order("orphan-1"),
        _open_order("known-1"),
        _open_order("known-2"),
    ])

    write_calls: list[dict] = []

    async def fake_write(**kwargs):  # type: ignore[no-untyped-def]
        write_calls.append(kwargs)

    with patch.object(rec_mod, "inc_orphaned_order", MagicMock()):
        with patch.object(rec_mod, "_write_order_alert", fake_write):
            await detect_orphaned_orders(
                exchange_client=client,
                exchange="bybit",
                questdb_ilp_addr="localhost:9009",
                strategy_name="test-strat",
                known_order_ids={"known-1", "known-2"},
            )

    assert len(write_calls) == 1
    assert "orphan-1" in write_calls[0]["detail"]
    client.cancel_order.assert_not_called()


@pytest.mark.l1
async def test_repeated_reconciliation_writes_new_alert_each_time() -> None:
    """Same orphan across two reconciliation runs → two alert writes (no dedup)."""
    import bot_service.strategy.reconciliation as rec_mod

    client = MagicMock()
    client.cancel_order = AsyncMock()
    client.get_open_orders = AsyncMock(return_value=[_open_order("orphan-1")])

    write_calls: list[dict] = []

    async def fake_write(**kwargs):  # type: ignore[no-untyped-def]
        write_calls.append(kwargs)

    kwargs = dict(
        exchange_client=client,
        exchange="bybit",
        questdb_ilp_addr="localhost:9009",
        strategy_name="test-strat",
        known_order_ids=set(),
    )

    with patch.object(rec_mod, "inc_orphaned_order", MagicMock()):
        with patch.object(rec_mod, "_write_order_alert", fake_write):
            await detect_orphaned_orders(**kwargs)
            await detect_orphaned_orders(**kwargs)

    assert len(write_calls) == 2


@pytest.mark.l1
async def test_no_exchange_orders_no_alerts() -> None:
    """Empty exchange order list → no alerts."""
    import bot_service.strategy.reconciliation as rec_mod

    client = MagicMock()
    client.cancel_order = AsyncMock()
    client.get_open_orders = AsyncMock(return_value=[])

    with patch.object(rec_mod, "inc_orphaned_order", MagicMock()) as mock_metric:
        with patch.object(rec_mod, "_write_order_alert", AsyncMock()) as mock_write:
            await detect_orphaned_orders(
                exchange_client=client,
                exchange="bybit",
                questdb_ilp_addr="localhost:9009",
                strategy_name="test-strat",
                known_order_ids=set(),
            )
            mock_write.assert_not_called()
            mock_metric.assert_not_called()
