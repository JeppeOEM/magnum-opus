from __future__ import annotations

import asyncio
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import redis
import structlog

from bot_service.bus.event_types import OrderFilled
from bot_service.exchange import OpenOrder, OrderRequest, PlacedOrder

log = structlog.get_logger()


class PaperExchangeClient:
    """Simulated exchange client for paper trading.

    ``place_order`` returns a ``PlacedOrder`` immediately, then schedules a
    background task that waits a random simulated latency, reads a live tick
    price from Redis (synchronously via :func:`asyncio.to_thread` to avoid
    event-loop binding issues), and calls ``on_fill`` with an
    :class:`~bot_service.bus.event_types.OrderFilled` event.

    Satisfies the :class:`~bot_service.exchange.ExchangeClient` protocol.

    Parameters
    ----------
    redis_url:
        Sync-redis URL (e.g. ``"redis://redis:6379"``).  A fresh connection is
        opened (and closed) for every price lookup so that the client is safe
        to use from any asyncio event loop.
    """

    def __init__(
        self,
        exchange: str,
        redis_url: str,
        on_fill: Callable[[OrderFilled], Awaitable[None]],
        latency_min_ms: int = 50,
        latency_max_ms: int = 250,
        slippage_bps: int = 5,
    ) -> None:
        self._exchange = exchange
        self._redis_url = redis_url
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

    async def get_recent_fills(self, symbol: str, since_ms: int) -> list[OrderFilled]:
        """Return empty list — paper client tracks no external fill state."""
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
        """Fill at limit_price (optimistic — ignores whether the price was crossed)."""
        return float(req.limit_price) if req.limit_price is not None else 0.0

    async def _resolve_market_price(self, req: OrderRequest) -> float:
        """Read the most recent 1-minute candle close; return price ± slippage.

        Uses a short-lived *synchronous* Redis connection (via
        :func:`asyncio.to_thread`) so the client is safe to call from any
        asyncio event loop without event-loop binding issues.

        The 1-minute candle stream (``candles:close:{exchange}:{symbol}:1m``)
        is preferred because its ``close`` field is a true trade price and not
        an order-book level.  Falls back to ``candles:ob:`` then ``ticks:``
        streams if the candle stream is empty.
        """
        exchange = self._exchange
        symbol = req.symbol
        redis_url = self._redis_url

        # Use the 1-minute candle close as the primary price source.
        # The tick stream deliberately is NOT used here — it contains order-book
        # levels at all price depths (including deep bids far below market) and
        # would produce unreliable fill prices.
        streams_and_fields: list[tuple[str, list[str]]] = [
            (f"candles:close:{exchange}:{symbol}:1m", ["close"]),
            (f"candles:ob:{exchange}:{symbol}", ["mid_price"]),
        ]

        def _sync_fetch() -> float:
            r: redis.Redis[str] = redis.from_url(redis_url, decode_responses=True)
            try:
                for stream_key, field_names in streams_and_fields:
                    entries = r.xrevrange(stream_key, "+", "-", count=1)
                    if not entries:
                        continue
                    _eid, fields = entries[0]
                    for fn in field_names:
                        raw = fields.get(fn)
                        if raw:
                            try:
                                val = float(raw)
                                if val > 0.0:
                                    return val
                            except (ValueError, TypeError):
                                pass
            finally:
                r.close()
            return 0.0

        price = await asyncio.to_thread(_sync_fetch)

        if price == 0.0:
            log.warning("paper_fill_no_price", exchange=exchange, symbol=symbol)
            return 0.0

        slippage = self._slippage_bps / 10_000.0
        return price * (1.0 + slippage) if req.side == "buy" else price * (1.0 - slippage)
