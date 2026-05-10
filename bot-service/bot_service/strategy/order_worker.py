from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

import structlog
from questdb.ingress import Sender, TimestampNanos

from bot_service.bus.event_types import OrderFilled
from bot_service.exchange import ExchangeClient, ExchangeRESTError, OrderRequest, PlacedOrder
from bot_service.metrics.prometheus import inc_order_queue_dedup, inc_risk_gate_block

log = structlog.get_logger()


class OrderQueueWorker:
    """Asyncio order queue worker with three-layer state and crash-safe persistence.

    Three layers:
    1. In-memory: open_orders (order_id → (req, placed)) + _position_notional
    2. QuestDB: order_events table via ILP — append-only, fire-and-forget
    3. Crash recovery: QuestDB write of 'placed' happens BEFORE open_orders update
       so Epic 13 reconciliation can repopulate layer 1 from QuestDB non-terminal orders.
    """

    def __init__(
        self,
        strategy_name: str,
        max_position_pct: float,
        paper_trading: bool,
        exchange_client: ExchangeClient,
        questdb_ilp_addr: str,
        portfolio_value_usd: float,
    ) -> None:
        self._strategy_name = strategy_name
        self._max_position_pct = max_position_pct
        self._paper_trading = paper_trading
        self._client = exchange_client
        self._questdb_ilp_addr = questdb_ilp_addr
        self._portfolio_value_usd = portfolio_value_usd
        self._queue: asyncio.Queue[OrderRequest] = asyncio.Queue()
        # order_id → (OrderRequest, PlacedOrder)
        self.open_orders: dict[str, tuple[OrderRequest, PlacedOrder]] = {}
        # symbol → notional USD position size
        self._position_notional: dict[str, float] = {}
        # dedup for fill events arriving from multiple paths; bounded to avoid memory leak
        self._seen_fill_ids: set[str] = set()
        self._seen_fill_ids_order: deque[str] = deque(maxlen=10_000)

    # ---- Public interface ------------------------------------------------

    def post(self, req: OrderRequest) -> None:
        """Post an order request to the queue (non-blocking)."""
        self._queue.put_nowait(req)

    def restore_open_order(self, order_id: str, req: OrderRequest, placed: PlacedOrder) -> None:
        """Restore an open order from reconciliation (Epic 13). Called before run()."""
        self.open_orders[order_id] = (req, placed)
        notional = req.size * (req.limit_price or 0.0)
        self._position_notional[req.symbol] = (
            self._position_notional.get(req.symbol, 0.0) + notional
        )

    async def handle_fill(self, fill: OrderFilled) -> None:
        """Process a fill event (from WS feed or REST fallback)."""
        if fill.order_id in self._seen_fill_ids:
            return
        # Evict oldest when at capacity before adding new entry
        if len(self._seen_fill_ids_order) == self._seen_fill_ids_order.maxlen:
            evicted = self._seen_fill_ids_order[0]  # deque maxlen auto-evicts on append
            self._seen_fill_ids.discard(evicted)
        self._seen_fill_ids.add(fill.order_id)
        self._seen_fill_ids_order.append(fill.order_id)

        if fill.order_id not in self.open_orders:
            log.warning(
                "fill_for_unknown_order",
                strategy=self._strategy_name,
                order_id=fill.order_id,
                exchange=fill.exchange,
            )
            return

        req, placed = self.open_orders[fill.order_id]

        notional = req.size * (req.limit_price or fill.fill_price)
        self._position_notional[req.symbol] = max(
            0.0,
            self._position_notional.get(req.symbol, 0.0) - notional,
        )
        del self.open_orders[fill.order_id]

        await self._write_order_event(
            order_id=fill.order_id,
            client_order_id=placed.client_order_id,
            strategy=req.strategy,
            exchange=fill.exchange,
            symbol=fill.symbol,
            side=fill.side,
            order_type=req.order_type,
            status="filled",
            limit_price=float(req.limit_price or 0.0),
            stop_price=float(req.stop_price or 0.0),
            take_profit_price=float(req.take_profit_price or 0.0),
            requested_size=float(req.size),
            filled_size=float(fill.fill_size),
            remaining_size=0.0,
            avg_fill_price=float(fill.fill_price),
            fee=float(fill.fee),
            realized_pnl=0.0,
            slippage=0.0,
            position_size_after=float(self._position_notional.get(fill.symbol, 0.0)),
            paper_trading=self._paper_trading,
            backtest=False,
            ts_placed=placed.ts_exchange * 1000,   # ms → µs for QuestDB TIMESTAMP
            ts_exchange=fill.ts_exchange * 1000,   # ms → µs for QuestDB TIMESTAMP
        )

    async def run(self, stop_event: asyncio.Event) -> None:
        """Consume queue until stop_event is set."""
        while not stop_event.is_set():
            try:
                req = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            await self._process(req)

    # ---- Internal processing --------------------------------------------

    async def _process(self, req: OrderRequest) -> None:
        """Route one OrderRequest through dedup → risk gate → REST client."""
        if self._is_entry_dedup(req):
            inc_order_queue_dedup(self._strategy_name, req.symbol)
            log.debug(
                "order_dedup_discarded",
                strategy=self._strategy_name,
                symbol=req.symbol,
                side=req.side,
            )
            return

        if self._exceeds_risk_gate(req):
            current = self._position_notional.get(req.symbol, 0.0)
            projected = req.size * (req.limit_price or 0.0)
            inc_risk_gate_block(self._strategy_name, req.symbol)
            log.warning(
                "order_risk_gate_blocked",
                strategy=self._strategy_name,
                symbol=req.symbol,
                position_notional=current + projected,
                max_allowed=self._max_position_pct * self._portfolio_value_usd,
            )
            return

        await self._place_and_persist(req)

    def _is_entry_dedup(self, req: OrderRequest) -> bool:
        """Return True if an open entry order already exists for (symbol, side)."""
        if req.order_role != "entry":
            return False
        for _oid, (open_req, _placed) in self.open_orders.items():
            if (
                open_req.symbol == req.symbol
                and open_req.side == req.side
                and open_req.order_role == "entry"
            ):
                return True
        return False

    def _exceeds_risk_gate(self, req: OrderRequest) -> bool:
        """Return True if the order would push position above max_position_pct * portfolio.

        Exit and stop orders reduce position size and are never blocked by the risk gate.
        Market entry orders with no limit_price pass the gate with 0 notional — a known
        limitation; callers should use limit orders for entry when risk enforcement matters.
        """
        if req.order_role in {"exit", "stop"}:
            return False
        projected_notional = req.size * (req.limit_price or 0.0)
        current_notional = self._position_notional.get(req.symbol, 0.0)
        max_allowed = self._max_position_pct * self._portfolio_value_usd
        return (current_notional + projected_notional) > max_allowed

    async def _place_and_persist(self, req: OrderRequest) -> None:
        """Call REST client, write QuestDB (BEFORE open_orders update for crash safety)."""
        ts_now_us = int(time.time() * 1_000_000)
        base: dict[str, Any] = dict(
            strategy=req.strategy,
            exchange=req.exchange,
            symbol=req.symbol,
            side=req.side,
            order_type=req.order_type,
            limit_price=float(req.limit_price or 0.0),
            stop_price=float(req.stop_price or 0.0),
            take_profit_price=float(req.take_profit_price or 0.0),
            requested_size=float(req.size),
            filled_size=0.0,
            remaining_size=float(req.size),
            avg_fill_price=0.0,
            fee=0.0,
            realized_pnl=0.0,
            slippage=0.0,
            position_size_after=0.0,
            paper_trading=self._paper_trading,
            backtest=False,
            ts_placed=ts_now_us,
        )

        try:
            placed = await self._client.place_order(req)
        except ExchangeRESTError as exc:
            await self._write_order_event(
                **base,
                order_id="",
                client_order_id=req.client_order_id,
                status="failed",
                ts_exchange=0,
            )
            log.warning(
                "order_place_failed",
                strategy=self._strategy_name,
                symbol=req.symbol,
                error=str(exc),
            )
            return

        if placed.status == "rejected":
            await self._write_order_event(
                **base,
                order_id=placed.order_id,
                client_order_id=placed.client_order_id,
                status="rejected",
                ts_exchange=placed.ts_exchange * 1000,  # ms → µs
            )
            return

        # Write BEFORE updating open_orders — crash-safety invariant (AC5)
        await self._write_order_event(
            **base,
            order_id=placed.order_id,
            client_order_id=placed.client_order_id,
            status="placed",
            ts_exchange=placed.ts_exchange * 1000,  # ms → µs
        )
        self.open_orders[placed.order_id] = (req, placed)
        notional = req.size * (req.limit_price or 0.0)
        self._position_notional[req.symbol] = (
            self._position_notional.get(req.symbol, 0.0) + notional
        )

    # ---- QuestDB ILP write ----------------------------------------------

    async def _write_order_event(self, **fields: Any) -> None:
        """Fire-and-forget ILP write to order_events. Logs error but never raises."""
        try:
            await asyncio.to_thread(self._sync_ilp_write, fields)
        except Exception as exc:
            log.error(
                "order_event_ilp_write_failed",
                strategy=self._strategy_name,
                error=str(exc),
            )

    def _sync_ilp_write(self, fields: dict[str, Any]) -> None:
        host, port_str = self._questdb_ilp_addr.split(":")
        ts_ns = TimestampNanos(int(time.time() * 1e9))

        symbols = {
            "order_id": str(fields.get("order_id", "")),
            "client_order_id": str(fields.get("client_order_id", "")),
            "strategy": str(fields.get("strategy", "")),
            "exchange": str(fields.get("exchange", "")),
            "symbol": str(fields.get("symbol", "")),
            "market_type": str(fields.get("market_type", "")),
            "side": str(fields.get("side", "")),
            "order_type": str(fields.get("order_type", "")),
            "status": str(fields.get("status", "")),
            "fee_currency": str(fields.get("fee_currency", "")),
            "signal_type": str(fields.get("signal_type", "")),
        }
        columns: dict[str, Any] = {
            "limit_price": float(fields.get("limit_price", 0.0)),
            "stop_price": float(fields.get("stop_price", 0.0)),
            "take_profit_price": float(fields.get("take_profit_price", 0.0)),
            "requested_size": float(fields.get("requested_size", 0.0)),
            "filled_size": float(fields.get("filled_size", 0.0)),
            "remaining_size": float(fields.get("remaining_size", 0.0)),
            "avg_fill_price": float(fields.get("avg_fill_price", 0.0)),
            "fee": float(fields.get("fee", 0.0)),
            "realized_pnl": float(fields.get("realized_pnl", 0.0)),
            "slippage": float(fields.get("slippage", 0.0)),
            "position_size_after": float(fields.get("position_size_after", 0.0)),
            "paper_trading": bool(fields.get("paper_trading", False)),
            "backtest": bool(fields.get("backtest", False)),
            # TIMESTAMP columns (microseconds — QuestDB ILP expects int64 µs for non-designated timestamps)
            "ts_placed": int(fields.get("ts_placed", 0)),
            "ts_exchange": int(fields.get("ts_exchange", 0)),
        }

        with Sender.from_conf(f"tcp::addr={host}:{port_str};") as sender:
            sender.row("order_events", symbols=symbols, columns=columns, at=ts_ns)
            sender.flush()
