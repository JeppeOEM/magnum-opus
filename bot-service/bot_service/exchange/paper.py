from __future__ import annotations

import asyncio
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from bot_service.bus.event_types import OrderFilled
from bot_service.exchange import OpenOrder, OrderRequest, PlacedOrder

log = structlog.get_logger()


class PaperExchangeClient:
    """Simulated exchange client for paper trading.

    `place_order` returns a PlacedOrder immediately, then schedules a background
    task that waits a random latency, reads the Redis tick/OB stream, and calls
    `on_fill` with an OrderFilled event.

    Satisfies the ExchangeClient protocol (place_order, cancel_order, get_open_orders).
    """

    def __init__(
        self,
        exchange: str,
        redis_client: Any,
        on_fill: Callable[[OrderFilled], Awaitable[None]],
        latency_min_ms: int = 50,
        latency_max_ms: int = 250,
        slippage_bps: int = 5,
    ) -> None:
        self._exchange = exchange
        self._redis = redis_client
        self._on_fill = on_fill
        self._latency_min_ms = latency_min_ms
        self._latency_max_ms = latency_max_ms
        self._slippage_bps = slippage_bps

    async def place_order(self, req: OrderRequest) -> PlacedOrder:
        """Return PlacedOrder immediately; schedule fill simulation as background task."""
        order_id = str(uuid.uuid4())
        placed = PlacedOrder(
            order_id=order_id,
            client_order_id=req.client_order_id,
            status="placed",
            ts_exchange=int(time.time() * 1000),
        )
        asyncio.create_task(self._simulate_fill(req, placed))
        return placed

    async def cancel_order(self, order_id: str, symbol: str) -> None:
        """No-op — paper orders have no real exchange state."""

    async def get_open_orders(self, symbol: str) -> list[OpenOrder]:
        """Return empty list — paper client tracks no external state."""
        return []

    # ---- Fill simulation ------------------------------------------------

    async def _simulate_fill(self, req: OrderRequest, placed: PlacedOrder) -> None:
        try:
            latency_ms = random.uniform(self._latency_min_ms, self._latency_max_ms)
            await asyncio.sleep(latency_ms / 1000.0)

            if req.order_type == "market":
                fill_price = await self._resolve_market_price(req)
            else:
                fill_price = await self._resolve_limit_price(req, placed.order_id)

            fill = OrderFilled(
                order_id=placed.order_id,
                exchange=self._exchange,
                symbol=req.symbol,
                side=req.side,
                fill_price=fill_price,
                fill_size=req.size,
                fee=0.0,
                ts_exchange=int(time.time() * 1000),
            )
            await self._on_fill(fill)
        except Exception as exc:
            log.error(
                "paper_fill_task_failed",
                exchange=self._exchange,
                order_id=placed.order_id,
                symbol=req.symbol,
                error=str(exc),
            )

    async def _resolve_limit_price(self, req: OrderRequest, order_id: str) -> float:
        """Read recent ticks; fill at limit_price regardless of crossing (optimistic)."""
        limit_price = req.limit_price if req.limit_price is not None else 0.0
        stream_key = f"ticks:{self._exchange}:{req.symbol}"

        try:
            entries: list[Any] = await self._redis.xrevrange(stream_key, "+", "-", count=100)
        except Exception as exc:
            log.warning("paper_fill_redis_error", symbol=req.symbol, error=str(exc))
            entries = []

        if not entries:
            log.warning(
                "paper_fill_no_ticks",
                exchange=self._exchange,
                symbol=req.symbol,
                order_id=order_id,
            )
            return limit_price

        # Fill optimistically at limit price regardless of whether any tick crossed
        return limit_price

    async def _resolve_market_price(self, req: OrderRequest) -> float:
        """Read most recent OB snapshot; return mid ± slippage."""
        stream_key = f"candles:ob:{self._exchange}:{req.symbol}"

        try:
            entries: list[Any] = await self._redis.xrevrange(stream_key, "+", "-", count=1)
        except Exception as exc:
            log.warning("paper_fill_redis_error", symbol=req.symbol, error=str(exc))
            entries = []

        if not entries:
            log.warning(
                "paper_fill_no_ob_snapshot",
                exchange=self._exchange,
                symbol=req.symbol,
            )
            return 0.0

        _entry_id, fields = entries[0]
        try:
            mid_price = float(fields.get(b"mid_price") or fields.get("mid_price") or 0.0)
        except (ValueError, TypeError):
            log.warning("paper_fill_bad_ob_data", symbol=req.symbol)
            return 0.0

        if mid_price == 0.0:
            log.warning("paper_fill_zero_mid_price", symbol=req.symbol)
            return 0.0

        slippage = self._slippage_bps / 10_000.0
        if req.side == "buy":
            return mid_price * (1.0 + slippage)
        return mid_price * (1.0 - slippage)
