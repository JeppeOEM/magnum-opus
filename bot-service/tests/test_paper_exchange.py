from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot_service.bus.event_types import OrderFilled
from bot_service.exchange import OrderRequest, PlacedOrder
from bot_service.exchange.paper import PaperExchangeClient


def _make_client(
    on_fill: Callable[[OrderFilled], Awaitable[None]] | None = None,
    redis_client: object | None = None,
    exchange: str = "bybit",
    latency_min_ms: int = 0,
    latency_max_ms: int = 1,
    slippage_bps: int = 5,
) -> PaperExchangeClient:
    if on_fill is None:
        on_fill = AsyncMock()
    if redis_client is None:
        redis_client = AsyncMock()
    return PaperExchangeClient(
        exchange=exchange,
        redis_client=redis_client,  # type: ignore[arg-type]
        on_fill=on_fill,
        latency_min_ms=latency_min_ms,
        latency_max_ms=latency_max_ms,
        slippage_bps=slippage_bps,
    )


def _limit_buy(symbol: str = "BTCUSDT", limit_price: float = 30000.0, size: float = 0.01) -> OrderRequest:
    return OrderRequest(
        strategy="test",
        exchange="bybit",
        symbol=symbol,
        side="buy",
        order_type="limit",
        order_role="entry",
        size=size,
        limit_price=limit_price,
        paper_trading=True,
    )


def _limit_sell(symbol: str = "BTCUSDT", limit_price: float = 31000.0, size: float = 0.01) -> OrderRequest:
    return OrderRequest(
        strategy="test",
        exchange="bybit",
        symbol=symbol,
        side="sell",
        order_type="limit",
        order_role="exit",
        size=size,
        limit_price=limit_price,
        paper_trading=True,
    )


def _market_buy(symbol: str = "BTCUSDT", size: float = 0.01) -> OrderRequest:
    return OrderRequest(
        strategy="test",
        exchange="bybit",
        symbol=symbol,
        side="buy",
        order_type="market",
        order_role="entry",
        size=size,
        paper_trading=True,
    )


# ---------------------------------------------------------------------------
# place_order returns PlacedOrder immediately
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_place_order_returns_placed_immediately() -> None:
    fills: list[OrderFilled] = []

    async def on_fill(f: OrderFilled) -> None:
        fills.append(f)

    redis = AsyncMock()
    redis.xrevrange = AsyncMock(return_value=[])
    client = _make_client(on_fill=on_fill, redis_client=redis)

    result = await client.place_order(_limit_buy())
    assert isinstance(result, PlacedOrder)
    assert result.status == "placed"


# ---------------------------------------------------------------------------
# Limit buy: tick at or below limit price → fills at limit price
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_limit_buy_fills_when_tick_below_limit() -> None:
    fills: list[OrderFilled] = []

    async def on_fill(f: OrderFilled) -> None:
        fills.append(f)

    redis = AsyncMock()
    # Tick at 29900 < limit 30000 → crossing
    redis.xrevrange = AsyncMock(return_value=[
        (b"1685-1", {b"type": b"tick", b"price": b"29900.0", b"side": b"buy", b"size": b"0.001", b"ts": b"1685000000000"}),
    ])

    client = _make_client(on_fill=on_fill, redis_client=redis)
    await client.place_order(_limit_buy(limit_price=30000.0))
    # Allow background fill task to complete
    await asyncio.sleep(0.05)

    assert len(fills) == 1
    assert fills[0].fill_price == pytest.approx(30000.0)


# ---------------------------------------------------------------------------
# Limit buy: all ticks above limit → still fills (optimistic paper trading)
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_limit_buy_fills_at_limit_when_no_crossing_tick() -> None:
    fills: list[OrderFilled] = []

    async def on_fill(f: OrderFilled) -> None:
        fills.append(f)

    redis = AsyncMock()
    # Ticks at 30100 > limit 30000 → no crossing, but fills anyway at limit
    redis.xrevrange = AsyncMock(return_value=[
        (b"1685-1", {b"type": b"tick", b"price": b"30100.0", b"side": b"sell", b"size": b"0.001", b"ts": b"1685000000000"}),
    ])

    client = _make_client(on_fill=on_fill, redis_client=redis)
    await client.place_order(_limit_buy(limit_price=30000.0))
    await asyncio.sleep(0.05)

    assert len(fills) == 1
    assert fills[0].fill_price == pytest.approx(30000.0)


# ---------------------------------------------------------------------------
# Limit order with no ticks → fills at limit price + WARN logged
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_limit_buy_no_ticks_fills_at_limit_and_warns() -> None:
    fills: list[OrderFilled] = []

    async def on_fill(f: OrderFilled) -> None:
        fills.append(f)

    redis = AsyncMock()
    redis.xrevrange = AsyncMock(return_value=[])  # empty stream

    client = _make_client(on_fill=on_fill, redis_client=redis)

    with patch("bot_service.exchange.paper.log") as mock_log:
        await client.place_order(_limit_buy(limit_price=30000.0))
        await asyncio.sleep(0.05)

    assert len(fills) == 1
    assert fills[0].fill_price == pytest.approx(30000.0)
    # Verify WARN was logged for no ticks
    mock_log.warning.assert_called()
    warn_calls = [str(c) for c in mock_log.warning.call_args_list]
    assert any("paper_fill_no_ticks" in s for s in warn_calls)


# ---------------------------------------------------------------------------
# Limit sell: tick at or above limit price → fills
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_limit_sell_fills_when_tick_above_limit() -> None:
    fills: list[OrderFilled] = []

    async def on_fill(f: OrderFilled) -> None:
        fills.append(f)

    redis = AsyncMock()
    # Tick at 31500 > limit 31000 → crossing for sell
    redis.xrevrange = AsyncMock(return_value=[
        (b"1685-1", {b"type": b"tick", b"price": b"31500.0", b"side": b"sell", b"size": b"0.001", b"ts": b"1685000000000"}),
    ])

    client = _make_client(on_fill=on_fill, redis_client=redis)
    await client.place_order(_limit_sell(limit_price=31000.0))
    await asyncio.sleep(0.05)

    assert len(fills) == 1
    assert fills[0].fill_price == pytest.approx(31000.0)


# ---------------------------------------------------------------------------
# Market buy: fills at mid_price + slippage
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_market_buy_fills_at_mid_plus_slippage() -> None:
    fills: list[OrderFilled] = []

    async def on_fill(f: OrderFilled) -> None:
        fills.append(f)

    redis = AsyncMock()
    # OB snapshot: best_bid=29990, best_ask=30010 → mid=30000
    redis.xrevrange = AsyncMock(return_value=[
        (b"1685-1", {b"best_bid": b"29990.0", b"best_ask": b"30010.0", b"mid_price": b"30000.0", b"ts": b"1685000000000"}),
    ])

    client = _make_client(on_fill=on_fill, redis_client=redis, slippage_bps=10)
    await client.place_order(_market_buy())
    await asyncio.sleep(0.05)

    assert len(fills) == 1
    # buy: mid * (1 + 10/10000) = 30000 * 1.001 = 30030.0
    assert fills[0].fill_price == pytest.approx(30030.0, rel=1e-4)


# ---------------------------------------------------------------------------
# Market buy: no OB snapshot → fills at 0.0 + WARN
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_market_buy_no_ob_snapshot_warns_and_fills() -> None:
    fills: list[OrderFilled] = []

    async def on_fill(f: OrderFilled) -> None:
        fills.append(f)

    redis = AsyncMock()
    redis.xrevrange = AsyncMock(return_value=[])  # no OB snapshot

    client = _make_client(on_fill=on_fill, redis_client=redis)

    with patch("bot_service.exchange.paper.log") as mock_log:
        await client.place_order(_market_buy())
        await asyncio.sleep(0.05)

    assert len(fills) == 1
    mock_log.warning.assert_called()


# ---------------------------------------------------------------------------
# Market sell: fills at mid_price - slippage
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_market_sell_fills_at_mid_minus_slippage() -> None:
    fills: list[OrderFilled] = []

    async def on_fill(f: OrderFilled) -> None:
        fills.append(f)

    redis = AsyncMock()
    redis.xrevrange = AsyncMock(return_value=[
        (b"1685-1", {b"best_bid": b"29990.0", b"best_ask": b"30010.0", b"mid_price": b"30000.0", b"ts": b"1685000000000"}),
    ])

    client = _make_client(on_fill=on_fill, redis_client=redis, slippage_bps=10)
    req = OrderRequest(
        strategy="test", exchange="bybit", symbol="BTCUSDT", side="sell",
        order_type="market", order_role="exit", size=0.01, paper_trading=True,
    )
    await client.place_order(req)
    await asyncio.sleep(0.05)

    assert len(fills) == 1
    # sell: mid * (1 - 10/10000) = 30000 * 0.999 = 29970.0
    assert fills[0].fill_price == pytest.approx(29970.0, rel=1e-4)


# ---------------------------------------------------------------------------
# cancel_order and get_open_orders are no-ops
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_cancel_order_is_noop() -> None:
    client = _make_client()
    await client.cancel_order("any-id", "BTCUSDT")  # must not raise


@pytest.mark.l1
async def test_get_open_orders_returns_empty() -> None:
    client = _make_client()
    orders = await client.get_open_orders(symbol="")
    assert orders == []
