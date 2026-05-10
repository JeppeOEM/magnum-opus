from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot_service.bus.event_types import OrderFilled
from bot_service.exchange.bybit.ws_private import BybitPrivateFeed
from bot_service.exchange.kucoin.ws_private import KuCoinPrivateFeed


# ---------------------------------------------------------------------------
# Silence detection — KuCoin
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_kucoin_is_silent_true_after_threshold() -> None:
    feed = KuCoinPrivateFeed()
    feed._last_msg_ts = time.monotonic() - 11.0
    assert feed._is_silent(timeout_s=10.0) is True


@pytest.mark.l1
def test_kucoin_is_silent_false_within_threshold() -> None:
    feed = KuCoinPrivateFeed()
    feed._last_msg_ts = time.monotonic() - 5.0
    assert feed._is_silent(timeout_s=10.0) is False


@pytest.mark.l1
def test_kucoin_is_silent_false_on_fresh_init() -> None:
    feed = KuCoinPrivateFeed()
    # _last_msg_ts is set to time.monotonic() in __init__
    assert feed._is_silent(timeout_s=10.0) is False


# ---------------------------------------------------------------------------
# Silence detection — Bybit
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_bybit_is_silent_true_after_threshold() -> None:
    feed = BybitPrivateFeed()
    feed._last_msg_ts = time.monotonic() - 31.0
    assert feed._is_silent(timeout_s=30.0) is True


@pytest.mark.l1
def test_bybit_is_silent_false_within_threshold() -> None:
    feed = BybitPrivateFeed()
    feed._last_msg_ts = time.monotonic() - 20.0
    assert feed._is_silent(timeout_s=30.0) is False


# ---------------------------------------------------------------------------
# KuCoin _parse_fill
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_kucoin_parse_fill_returns_order_filled_for_fill() -> None:
    feed = KuCoinPrivateFeed()
    data: dict[str, Any] = {
        "orderId": "abc123",
        "symbol": "XBTUSDTM",
        "type": "filled",
        "side": "buy",
        "matchSize": "0.001",
        "matchPrice": "30000.5",
        "fee": "0.001",
        "tradeTime": 1685000000000,
    }
    result = feed._parse_fill(data)
    assert result is not None
    assert result.order_id == "abc123"
    assert result.symbol == "XBTUSDTM"
    assert result.fill_price == pytest.approx(30000.5)
    assert result.fill_size == pytest.approx(0.001)
    assert result.ts_exchange == 1685000000000


@pytest.mark.l1
def test_kucoin_parse_fill_returns_none_for_open_status() -> None:
    feed = KuCoinPrivateFeed()
    result = feed._parse_fill({"type": "open", "orderId": "abc"})
    assert result is None


@pytest.mark.l1
def test_kucoin_parse_fill_returns_none_for_match_status() -> None:
    feed = KuCoinPrivateFeed()
    result = feed._parse_fill({"type": "match", "orderId": "abc"})
    assert result is None


@pytest.mark.l1
def test_kucoin_parse_fill_returns_none_for_update_status() -> None:
    feed = KuCoinPrivateFeed()
    result = feed._parse_fill({"type": "update", "orderId": "abc"})
    assert result is None


# ---------------------------------------------------------------------------
# Bybit _parse_fills
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_bybit_parse_fills_returns_fill_for_filled_order() -> None:
    feed = BybitPrivateFeed()
    data: dict[str, Any] = {
        "topic": "order",
        "data": [
            {
                "orderId": "xyz789",
                "symbol": "BTCUSDT",
                "side": "Buy",
                "orderStatus": "Filled",
                "cumExecQty": "0.001",
                "avgPrice": "30000.5",
                "cumExecFee": "0.003",
                "updatedTime": "1685000000000",
            }
        ],
    }
    results = feed._parse_fills(data)
    assert len(results) == 1
    assert results[0].order_id == "xyz789"
    assert results[0].side == "buy"
    assert results[0].fill_price == pytest.approx(30000.5)
    assert results[0].ts_exchange == 1685000000000


@pytest.mark.l1
def test_bybit_parse_fills_returns_fill_for_partially_filled() -> None:
    feed = BybitPrivateFeed()
    data: dict[str, Any] = {
        "topic": "order",
        "data": [{"orderId": "abc", "symbol": "ETHUSDT", "side": "Sell",
                  "orderStatus": "PartiallyFilled", "cumExecQty": "0.5",
                  "avgPrice": "2000", "cumExecFee": "0.001", "updatedTime": "1685000000001"}],
    }
    results = feed._parse_fills(data)
    assert len(results) == 1


@pytest.mark.l1
def test_bybit_parse_fills_empty_for_new_status() -> None:
    feed = BybitPrivateFeed()
    data: dict[str, Any] = {
        "topic": "order",
        "data": [{"orderId": "abc", "orderStatus": "New"}],
    }
    assert feed._parse_fills(data) == []


@pytest.mark.l1
def test_bybit_parse_fills_empty_for_non_order_topic() -> None:
    feed = BybitPrivateFeed()
    data: dict[str, Any] = {"topic": "position", "data": [{"symbol": "BTCUSDT"}]}
    assert feed._parse_fills(data) == []


@pytest.mark.l1
def test_bybit_parse_fills_multiple_fills_in_one_message() -> None:
    feed = BybitPrivateFeed()
    data: dict[str, Any] = {
        "topic": "order",
        "data": [
            {"orderId": "f1", "symbol": "BTCUSDT", "side": "Buy", "orderStatus": "Filled",
             "cumExecQty": "0.001", "avgPrice": "30000", "cumExecFee": "0", "updatedTime": "1"},
            {"orderId": "f2", "symbol": "ETHUSDT", "side": "Sell", "orderStatus": "Filled",
             "cumExecQty": "0.5", "avgPrice": "2000", "cumExecFee": "0", "updatedTime": "2"},
        ],
    }
    results = feed._parse_fills(data)
    assert len(results) == 2


# ---------------------------------------------------------------------------
# Fill deduplication — KuCoin
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_kucoin_fill_dedup_blocks_second_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    import bot_service.exchange.kucoin.ws_private as kucoin_ws

    dedup_calls: list[str] = []
    monkeypatch.setattr(kucoin_ws, "inc_fill_dedup", lambda exchange: dedup_calls.append(exchange))

    received: list[OrderFilled] = []

    async def on_fill(f: OrderFilled) -> None:
        received.append(f)

    feed = KuCoinPrivateFeed()
    fill = OrderFilled(
        order_id="dup-order-1",
        exchange="kucoin",
        symbol="XBTUSDTM",
        side="buy",
        fill_price=30000.0,
        fill_size=0.001,
        fee=0.001,
        ts_exchange=1685000000000,
    )
    await feed._handle_ws_fill(fill, on_fill)
    await feed._handle_ws_fill(fill, on_fill)  # duplicate

    assert len(received) == 1
    assert len(dedup_calls) == 1
    assert dedup_calls[0] == "kucoin"


@pytest.mark.l1
async def test_kucoin_different_order_ids_both_delivered() -> None:
    received: list[OrderFilled] = []

    async def on_fill(f: OrderFilled) -> None:
        received.append(f)

    feed = KuCoinPrivateFeed()
    fill_a = OrderFilled(order_id="order-a", exchange="kucoin", symbol="XBTUSDTM",
                         side="buy", fill_price=30000.0, fill_size=0.001, fee=0.0,
                         ts_exchange=1685000000000)
    fill_b = OrderFilled(order_id="order-b", exchange="kucoin", symbol="XBTUSDTM",
                         side="sell", fill_price=30001.0, fill_size=0.001, fee=0.0,
                         ts_exchange=1685000000001)
    await feed._handle_ws_fill(fill_a, on_fill)
    await feed._handle_ws_fill(fill_b, on_fill)

    assert len(received) == 2


# ---------------------------------------------------------------------------
# Fill deduplication — Bybit
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_bybit_fill_dedup_blocks_second_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    import bot_service.exchange.bybit.ws_private as bybit_ws

    dedup_calls: list[str] = []
    monkeypatch.setattr(bybit_ws, "inc_fill_dedup", lambda exchange: dedup_calls.append(exchange))

    received: list[OrderFilled] = []

    async def on_fill(f: OrderFilled) -> None:
        received.append(f)

    feed = BybitPrivateFeed()
    fill = OrderFilled(
        order_id="dup-bybit-1",
        exchange="bybit",
        symbol="BTCUSDT",
        side="buy",
        fill_price=30000.0,
        fill_size=0.001,
        fee=0.003,
        ts_exchange=1685000000000,
    )
    await feed._handle_ws_fill(fill, on_fill)
    await feed._handle_ws_fill(fill, on_fill)

    assert len(received) == 1
    assert dedup_calls == ["bybit"]


# ---------------------------------------------------------------------------
# L2: Fallback activation — KuCoin
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_bybit_different_order_ids_both_delivered() -> None:
    received: list[OrderFilled] = []

    async def on_fill(f: OrderFilled) -> None:
        received.append(f)

    feed = BybitPrivateFeed()
    fill_a = OrderFilled(order_id="bybit-order-a", exchange="bybit", symbol="BTCUSDT",
                         side="buy", fill_price=30000.0, fill_size=0.001, fee=0.0,
                         ts_exchange=1685000000000)
    fill_b = OrderFilled(order_id="bybit-order-b", exchange="bybit", symbol="ETHUSDT",
                         side="sell", fill_price=2000.0, fill_size=0.1, fee=0.0,
                         ts_exchange=1685000000001)
    await feed._handle_ws_fill(fill_a, on_fill)
    await feed._handle_ws_fill(fill_b, on_fill)

    assert len(received) == 2


@pytest.mark.l2
async def test_kucoin_fallback_activates_on_silence(monkeypatch: pytest.MonkeyPatch) -> None:
    import bot_service.exchange.kucoin.ws_private as kucoin_ws

    gauge_values: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        kucoin_ws, "set_ws_fallback_active",
        lambda exchange, active: gauge_values.append((exchange, active))
    )

    feed = KuCoinPrivateFeed()
    # Inject stale timestamp so feed is already silent
    feed._last_msg_ts = time.monotonic() - 15.0

    mock_rest = MagicMock()
    mock_rest.get_open_orders = AsyncMock(return_value=[])

    stop = asyncio.Event()
    on_fill_mock = AsyncMock()

    task = asyncio.create_task(
        feed._run_liveness_checker(
            timeout_s=10.0,
            rest_client=mock_rest,
            on_fill=on_fill_mock,
            stop_event=stop,
        )
    )
    # Wait for one liveness check cycle
    await asyncio.sleep(1.5)
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)

    assert ("kucoin", True) in gauge_values


@pytest.mark.l2
async def test_kucoin_fallback_deactivates_when_ws_resumes(monkeypatch: pytest.MonkeyPatch) -> None:
    import bot_service.exchange.kucoin.ws_private as kucoin_ws

    gauge_values: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        kucoin_ws, "set_ws_fallback_active",
        lambda exchange, active: gauge_values.append((exchange, active))
    )

    feed = KuCoinPrivateFeed()
    # Start silent
    feed._last_msg_ts = time.monotonic() - 15.0

    mock_rest = MagicMock()
    mock_rest.get_open_orders = AsyncMock(return_value=[])

    stop = asyncio.Event()
    on_fill_mock = AsyncMock()

    task = asyncio.create_task(
        feed._run_liveness_checker(
            timeout_s=10.0,
            rest_client=mock_rest,
            on_fill=on_fill_mock,
            stop_event=stop,
        )
    )
    # Wait for fallback to activate
    await asyncio.sleep(1.5)

    # Simulate WS message arriving — update timestamp
    feed._last_msg_ts = time.monotonic()

    # Wait for deactivation check
    await asyncio.sleep(1.5)
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)

    assert ("kucoin", True) in gauge_values
    assert ("kucoin", False) in gauge_values
    # True came before False
    true_idx = next(i for i, v in enumerate(gauge_values) if v == ("kucoin", True))
    false_idx = next(i for i, v in enumerate(gauge_values) if v == ("kucoin", False))
    assert true_idx < false_idx


# ---------------------------------------------------------------------------
# L2: Fallback activation — Bybit
# ---------------------------------------------------------------------------


@pytest.mark.l2
async def test_bybit_fallback_activates_on_silence(monkeypatch: pytest.MonkeyPatch) -> None:
    import bot_service.exchange.bybit.ws_private as bybit_ws

    gauge_values: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        bybit_ws, "set_ws_fallback_active",
        lambda exchange, active: gauge_values.append((exchange, active))
    )

    feed = BybitPrivateFeed()
    feed._last_msg_ts = time.monotonic() - 35.0

    mock_rest = MagicMock()
    mock_rest.get_open_orders = AsyncMock(return_value=[])

    stop = asyncio.Event()

    task = asyncio.create_task(
        feed._run_liveness_checker(
            timeout_s=30.0,
            rest_client=mock_rest,
            on_fill=AsyncMock(),
            stop_event=stop,
        )
    )
    await asyncio.sleep(1.5)
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)

    assert ("bybit", True) in gauge_values
