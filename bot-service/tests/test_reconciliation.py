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


# ===========================================================================
# Story 13.1 — Startup Reconciliation Tests
# ===========================================================================

# ---------------------------------------------------------------------------
# T1.3 — BaseStrategy._managed_positions exists
# ---------------------------------------------------------------------------

@pytest.mark.l1
def test_base_strategy_has_managed_positions() -> None:
    """BaseStrategy.__init__ initialises _managed_positions as empty set."""
    from bot_service.strategy.base import BaseStrategy
    from bot_service.config import Settings

    class _Stub(BaseStrategy):
        @property
        def min_lookback(self) -> int: return 1
        @property
        def max_position_pct(self) -> float: return 0.1
        @property
        def stop_loss_pct(self) -> float: return 0.02
        @property
        def paper_trading(self) -> bool: return True
        @property
        def bus_timeout_seconds(self) -> int: return 300
        @property
        def close_on_bus_timeout(self) -> bool: return False
        def subscribe(self) -> None: pass

    s = _Stub("s1", Settings())
    assert hasattr(s, "_managed_positions")
    assert isinstance(s._managed_positions, set)
    assert len(s._managed_positions) == 0


# ---------------------------------------------------------------------------
# T1.3 — New Prometheus metrics
# ---------------------------------------------------------------------------

@pytest.mark.l1
def test_inc_strategy_restart_increments() -> None:
    """inc_strategy_restart must not raise and must increment the counter."""
    import bot_service.metrics.prometheus as pm
    pm.get_registry()
    pm.inc_strategy_restart("test-strat")  # must not raise


@pytest.mark.l1
def test_set_strategy_backoff_seconds() -> None:
    """set_strategy_backoff_seconds must not raise."""
    import bot_service.metrics.prometheus as pm
    pm.get_registry()
    pm.set_strategy_backoff_seconds("test-strat", 30.0)  # must not raise


# ---------------------------------------------------------------------------
# T2.1 — query_questdb_nonterminal_orders
# ---------------------------------------------------------------------------

@pytest.mark.l1
def test_query_nonterminal_returns_placed_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns list of dicts for non-terminal (placed) rows."""
    import bot_service.strategy.reconciliation as rec_mod
    import httpx

    fake_response = {
        "columns": [
            {"name": "order_id"},
            {"name": "client_order_id"},
            {"name": "symbol"},
            {"name": "side"},
            {"name": "order_type"},
            {"name": "requested_size"},
            {"name": "limit_price"},
            {"name": "status"},
        ],
        "dataset": [
            ["oid-1", "coid-1", "BTCUSDT", "buy", "limit", 0.01, 30000.0, "placed"],
        ],
    }

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        req = httpx.Request("GET", url)
        return httpx.Response(200, json=fake_response, request=req)

    monkeypatch.setattr(httpx, "get", fake_get)
    rows = rec_mod.query_questdb_nonterminal_orders("http://localhost:9000", "test-strat")
    assert len(rows) == 1
    assert rows[0]["order_id"] == "oid-1"
    assert rows[0]["status"] == "placed"


@pytest.mark.l1
def test_query_nonterminal_excludes_terminal_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Terminal (filled/cancelled) rows are excluded by SQL — empty dataset → empty list."""
    import bot_service.strategy.reconciliation as rec_mod
    import httpx

    fake_response = {"columns": [{"name": "order_id"}, {"name": "status"}], "dataset": []}

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        req = httpx.Request("GET", url)
        return httpx.Response(200, json=fake_response, request=req)

    monkeypatch.setattr(httpx, "get", fake_get)
    rows = rec_mod.query_questdb_nonterminal_orders("http://localhost:9000", "test-strat")
    assert rows == []


@pytest.mark.l1
def test_query_nonterminal_returns_none_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP error → returns None (not []), logs WARNING so caller can detect degraded state."""
    import bot_service.strategy.reconciliation as rec_mod
    import httpx

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", fake_get)
    rows = rec_mod.query_questdb_nonterminal_orders("http://localhost:9000", "test-strat")
    assert rows is None


# ---------------------------------------------------------------------------
# T2.1 — run_startup_reconciliation
# ---------------------------------------------------------------------------

@pytest.mark.l1
async def test_reconciliation_populates_open_orders_from_questdb(monkeypatch: pytest.MonkeyPatch) -> None:
    """QuestDB has a placed order that also exists on exchange → restore_open_order called."""
    import bot_service.strategy.reconciliation as rec_mod

    # Mock QuestDB query
    monkeypatch.setattr(
        rec_mod,
        "query_questdb_nonterminal_orders",
        lambda *a, **kw: [
            {
                "order_id": "oid-1", "client_order_id": "coid-1",
                "symbol": "BTCUSDT", "side": "buy", "order_type": "limit",
                "requested_size": 0.01, "limit_price": 30000.0, "status": "placed",
            }
        ],
    )

    # Mock exchange client
    exchange_client = MagicMock()
    exchange_client.get_open_orders = AsyncMock(
        return_value=[_open_order("oid-1", symbol="BTCUSDT")]
    )

    # Mock OrderQueueWorker
    order_worker = MagicMock()
    order_worker.restore_open_order = MagicMock()

    from bot_service.strategy.base import BaseStrategy
    from bot_service.config import Settings

    class _Stub(BaseStrategy):
        @property
        def min_lookback(self) -> int: return 1
        @property
        def max_position_pct(self) -> float: return 0.1
        @property
        def stop_loss_pct(self) -> float: return 0.02
        @property
        def paper_trading(self) -> bool: return False
        @property
        def bus_timeout_seconds(self) -> int: return 300
        @property
        def close_on_bus_timeout(self) -> bool: return False
        def subscribe(self) -> None: pass

    strategy = _Stub("test-strat", Settings())

    await rec_mod.run_startup_reconciliation(
        strategy=strategy,
        order_worker=order_worker,
        exchange_client=exchange_client,
        exchange="bybit",
        questdb_http_addr="http://localhost:9000",
        questdb_ilp_addr="localhost:9009",
        timeout_s=5.0,
    )

    order_worker.restore_open_order.assert_called_once()
    call_kwargs = order_worker.restore_open_order.call_args
    assert call_kwargs[0][0] == "oid-1"  # order_id


@pytest.mark.l1
async def test_reconciliation_force_subscribe_adds_managed_position(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strategy subscribed to ETH only, BTC open position → _managed_positions contains BTCUSDT."""
    import bot_service.strategy.reconciliation as rec_mod

    monkeypatch.setattr(
        rec_mod,
        "query_questdb_nonterminal_orders",
        lambda *a, **kw: [
            {
                "order_id": "oid-btc", "client_order_id": "coid-btc",
                "symbol": "BTCUSDT", "side": "buy", "order_type": "limit",
                "requested_size": 0.01, "limit_price": 30000.0, "status": "placed",
            }
        ],
    )

    exchange_client = MagicMock()
    exchange_client.get_open_orders = AsyncMock(
        return_value=[_open_order("oid-btc", symbol="BTCUSDT")]
    )
    order_worker = MagicMock()
    order_worker.restore_open_order = MagicMock()

    from bot_service.strategy.base import BaseStrategy
    from bot_service.config import Settings

    class _EthStrategy(BaseStrategy):
        @property
        def min_lookback(self) -> int: return 1
        @property
        def max_position_pct(self) -> float: return 0.1
        @property
        def stop_loss_pct(self) -> float: return 0.02
        @property
        def paper_trading(self) -> bool: return False
        @property
        def bus_timeout_seconds(self) -> int: return 300
        @property
        def close_on_bus_timeout(self) -> bool: return False

        def subscribe(self) -> None:
            # Only subscribes to ETH — BTC is NOT registered
            self.register_bar_handler("ETHUSDT", "1s", lambda df: None)

    strategy = _EthStrategy("eth-strat", Settings())
    strategy.subscribe()  # populate _bar_handlers with ETHUSDT only

    await rec_mod.run_startup_reconciliation(
        strategy=strategy,
        order_worker=order_worker,
        exchange_client=exchange_client,
        exchange="bybit",
        questdb_http_addr="http://localhost:9000",
        questdb_ilp_addr="localhost:9009",
        timeout_s=5.0,
    )

    assert "BTCUSDT" in strategy._managed_positions


@pytest.mark.l1
async def test_reconciliation_timeout_uses_questdb_only_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exchange REST hangs past timeout → CRITICAL logged, QuestDB-derived restore_open_order called."""
    import asyncio
    import bot_service.strategy.reconciliation as rec_mod

    monkeypatch.setattr(
        rec_mod,
        "query_questdb_nonterminal_orders",
        lambda *a, **kw: [
            {
                "order_id": "oid-timeout", "client_order_id": "coid-t",
                "symbol": "BTCUSDT", "side": "buy", "order_type": "limit",
                "requested_size": 0.01, "limit_price": 30000.0, "status": "placed",
            }
        ],
    )

    async def _hang(symbol: str) -> list:  # type: ignore[return]
        await asyncio.sleep(999)

    exchange_client = MagicMock()
    exchange_client.get_open_orders = _hang
    order_worker = MagicMock()
    order_worker.restore_open_order = MagicMock()

    from bot_service.strategy.base import BaseStrategy
    from bot_service.config import Settings

    class _Stub(BaseStrategy):
        @property
        def min_lookback(self) -> int: return 1
        @property
        def max_position_pct(self) -> float: return 0.1
        @property
        def stop_loss_pct(self) -> float: return 0.02
        @property
        def paper_trading(self) -> bool: return False
        @property
        def bus_timeout_seconds(self) -> int: return 300
        @property
        def close_on_bus_timeout(self) -> bool: return False
        def subscribe(self) -> None: pass

    strategy = _Stub("t-strat", Settings())

    await rec_mod.run_startup_reconciliation(
        strategy=strategy,
        order_worker=order_worker,
        exchange_client=exchange_client,
        exchange="bybit",
        questdb_http_addr="http://localhost:9000",
        questdb_ilp_addr="localhost:9009",
        timeout_s=0.01,  # very short timeout
    )

    # Should have restored from QuestDB even without exchange confirmation
    order_worker.restore_open_order.assert_called_once()
    call_kwargs = order_worker.restore_open_order.call_args
    assert call_kwargs[0][0] == "oid-timeout"


@pytest.mark.l1
async def test_reconciliation_no_duplicate_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """QuestDB has placed order, exchange confirms it → restore_open_order called; place_order NOT called."""
    import bot_service.strategy.reconciliation as rec_mod

    monkeypatch.setattr(
        rec_mod,
        "query_questdb_nonterminal_orders",
        lambda *a, **kw: [
            {
                "order_id": "oid-X", "client_order_id": "coid-X",
                "symbol": "BTCUSDT", "side": "buy", "order_type": "limit",
                "requested_size": 0.01, "limit_price": 30000.0, "status": "placed",
            }
        ],
    )

    exchange_client = MagicMock()
    exchange_client.get_open_orders = AsyncMock(
        return_value=[_open_order("oid-X", symbol="BTCUSDT")]
    )
    exchange_client.place_order = AsyncMock()
    order_worker = MagicMock()
    order_worker.restore_open_order = MagicMock()

    from bot_service.strategy.base import BaseStrategy
    from bot_service.config import Settings

    class _Stub(BaseStrategy):
        @property
        def min_lookback(self) -> int: return 1
        @property
        def max_position_pct(self) -> float: return 0.1
        @property
        def stop_loss_pct(self) -> float: return 0.02
        @property
        def paper_trading(self) -> bool: return False
        @property
        def bus_timeout_seconds(self) -> int: return 300
        @property
        def close_on_bus_timeout(self) -> bool: return False
        def subscribe(self) -> None: pass

    strategy = _Stub("s", Settings())

    await rec_mod.run_startup_reconciliation(
        strategy=strategy,
        order_worker=order_worker,
        exchange_client=exchange_client,
        exchange="bybit",
        questdb_http_addr="http://localhost:9000",
        questdb_ilp_addr="localhost:9009",
        timeout_s=5.0,
    )

    order_worker.restore_open_order.assert_called_once()
    exchange_client.place_order.assert_not_called()


@pytest.mark.l1
async def test_cross_strategy_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two strategies each have 1 placed order → each restore_open_order called only for its own order."""
    import bot_service.strategy.reconciliation as rec_mod

    # Strategy A: order oid-A
    def _query_A(questdb_http_addr: str, strategy_name: str) -> list:
        if strategy_name == "strat-A":
            return [{"order_id": "oid-A", "client_order_id": "c-A", "symbol": "BTCUSDT",
                     "side": "buy", "order_type": "limit", "requested_size": 0.01, "limit_price": 30000.0, "status": "placed"}]
        return [{"order_id": "oid-B", "client_order_id": "c-B", "symbol": "ETHUSDT",
                 "side": "buy", "order_type": "limit", "requested_size": 0.1, "limit_price": 2000.0, "status": "placed"}]

    monkeypatch.setattr(rec_mod, "query_questdb_nonterminal_orders", _query_A)

    exchange_client = MagicMock()
    exchange_client.get_open_orders = AsyncMock(return_value=[
        _open_order("oid-A", symbol="BTCUSDT"),
        _open_order("oid-B", symbol="ETHUSDT"),
    ])

    order_worker_A = MagicMock()
    order_worker_A.restore_open_order = MagicMock()
    order_worker_B = MagicMock()
    order_worker_B.restore_open_order = MagicMock()

    from bot_service.strategy.base import BaseStrategy
    from bot_service.config import Settings

    class _Stub(BaseStrategy):
        @property
        def min_lookback(self) -> int: return 1
        @property
        def max_position_pct(self) -> float: return 0.1
        @property
        def stop_loss_pct(self) -> float: return 0.02
        @property
        def paper_trading(self) -> bool: return False
        @property
        def bus_timeout_seconds(self) -> int: return 300
        @property
        def close_on_bus_timeout(self) -> bool: return False
        def subscribe(self) -> None: pass

    strat_A = _Stub("strat-A", Settings())
    strat_B = _Stub("strat-B", Settings())

    for strat, worker in [(strat_A, order_worker_A), (strat_B, order_worker_B)]:
        await rec_mod.run_startup_reconciliation(
            strategy=strat,
            order_worker=worker,
            exchange_client=exchange_client,
            exchange="bybit",
            questdb_http_addr="http://localhost:9000",
            questdb_ilp_addr="localhost:9009",
            timeout_s=5.0,
        )

    # Each worker gets its own order only
    order_worker_A.restore_open_order.assert_called_once()
    assert order_worker_A.restore_open_order.call_args[0][0] == "oid-A"
    order_worker_B.restore_open_order.assert_called_once()
    assert order_worker_B.restore_open_order.call_args[0][0] == "oid-B"
