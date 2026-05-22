from __future__ import annotations

import asyncio
import re
import time
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from questdb.ingress import Sender, TimestampNanos

from bot_service.exchange import ExchangeClient, OpenOrder, OrderRequest, PlacedOrder
from bot_service.metrics.prometheus import inc_orphaned_order

if TYPE_CHECKING:
    from bot_service.strategy.base import BaseStrategy
    from bot_service.strategy.order_worker import OrderQueueWorker

log = structlog.get_logger()

# Only these characters are safe to interpolate into QuestDB SQL (no single quotes, no injectors).
_STRATEGY_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


async def detect_orphaned_orders(
    exchange_client: ExchangeClient,
    exchange: str,
    questdb_ilp_addr: str,
    strategy_name: str,
    known_order_ids: set[str],
    prefetched_orders: list[OpenOrder] | None = None,
) -> None:
    """Detect orders present on exchange but absent from QuestDB; write order_alerts.

    Called at startup before strategy event loop begins. Never auto-cancels.

    Parameters
    ----------
    exchange_client:
        Live exchange REST client. `get_open_orders(symbol="")` must return ALL open
        orders across all symbols — both KuCoin and Bybit REST clients honour this
        convention (empty string = no symbol filter).
    exchange:
        Exchange name label (e.g. "bybit", "kucoin").
    questdb_ilp_addr:
        QuestDB ILP TCP address, e.g. "localhost:9009".
    strategy_name:
        Strategy name for the alert row.
    known_order_ids:
        Set of order_id values known to QuestDB (non-terminal rows). Caller builds this
        by querying QuestDB before calling this function.
    prefetched_orders:
        If provided, use this list instead of calling get_open_orders() — avoids a
        second REST round-trip when the caller already has the list.
    """
    if prefetched_orders is not None:
        open_orders = prefetched_orders
    else:
        open_orders = await exchange_client.get_open_orders(symbol="")

    for order in open_orders:
        if order.order_id in known_order_ids:
            continue
        inc_orphaned_order(exchange)
        detail = (
            f"exchange={exchange} symbol={order.symbol} "
            f"order_id={order.order_id} qty={order.size}"
        )
        log.warning(
            "orphaned_order_detected",
            exchange=exchange,
            strategy=strategy_name,
            order_id=order.order_id,
            symbol=order.symbol,
            detail=detail,
        )
        await _write_order_alert(
            questdb_ilp_addr=questdb_ilp_addr,
            order_id=order.order_id,
            strategy=strategy_name,
            alert_type="orphaned_order",
            detail=detail,
        )


async def _write_order_alert(
    questdb_ilp_addr: str,
    order_id: str,
    strategy: str,
    alert_type: str,
    detail: str,
) -> None:
    """Fire-and-forget ILP write to order_alerts. Logs error but never raises."""
    try:
        await asyncio.to_thread(
            _sync_write_alert,
            questdb_ilp_addr,
            order_id,
            strategy,
            alert_type,
            detail,
        )
    except Exception as exc:
        log.error(
            "order_alert_ilp_write_failed",
            alert_type=alert_type,
            order_id=order_id,
            error=str(exc),
        )


def _sync_write_alert(
    questdb_ilp_addr: str,
    order_id: str,
    strategy: str,
    alert_type: str,
    detail: str,
) -> None:
    host, port_str = questdb_ilp_addr.split(":")
    ts_ns = TimestampNanos(int(time.time() * 1e9))
    symbols: dict[str, Any] = {
        "order_id": order_id,
        "strategy": strategy,
        "alert_type": alert_type,
    }
    columns: dict[str, Any] = {
        "detail": detail,
        "resolved": False,
    }
    with Sender.from_conf(f"tcp::addr={host}:{port_str};") as sender:
        sender.row("order_alerts", symbols=symbols, columns=columns, at=ts_ns)
        sender.flush()


def query_questdb_nonterminal_orders(
    questdb_http_addr: str, strategy_name: str
) -> list[dict[str, Any]] | None:
    """Query QuestDB for non-terminal order_events rows for a specific strategy.

    Returns list of row dicts with keys: order_id, client_order_id, symbol, side,
    order_type, requested_size, limit_price, signal_type, status.
    Returns None on any error (logs WARNING — caller must treat as degraded state).
    Returns [] when the query succeeds but no non-terminal orders exist.
    """
    if not _STRATEGY_NAME_RE.match(strategy_name):
        log.error("reconciliation_invalid_strategy_name", strategy=strategy_name)
        return None
    # Use LATEST ON to get the most-recent status per order_id, then filter out
    # terminal statuses.  This avoids re-importing "placed" rows that already
    # have a corresponding "filled" row (QuestDB is append-only: both rows exist
    # but the latest status for a filled order is "filled", not "placed").
    query = (
        "SELECT order_id, client_order_id, symbol, side, order_type, requested_size, "
        "limit_price, signal_type, status "
        "FROM ("
        "SELECT order_id, client_order_id, symbol, side, order_type, requested_size, "
        "limit_price, signal_type, status, ts "
        "FROM order_events "
        f"WHERE strategy = '{strategy_name}' "
        "LATEST ON ts PARTITION BY order_id"
        ") "
        "WHERE status NOT IN ('filled','cancelled','rejected','failed') "
        "ORDER BY ts DESC"
    )
    try:
        resp = httpx.get(
            f"{questdb_http_addr}/exec",
            params={"query": query},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        cols = [c["name"] for c in data.get("columns", [])]
        rows = data.get("dataset", [])
        return [dict(zip(cols, row)) for row in rows]
    except Exception as exc:
        log.warning(
            "reconciliation_questdb_query_failed",
            strategy=strategy_name,
            error=str(exc),
        )
        return None


async def _retry_reconciliation_background(
    strategy_name: str,
    unconfirmed_order_ids: set[str],
    exchange_client: ExchangeClient,
    retry_interval_s: float = 60.0,
) -> None:
    """Re-confirm unconfirmed orders against exchange on a 60-second loop.

    Loops until all orders are confirmed or the set is empty. Body wrapped in
    try/except so exceptions never silently kill the task (Epic 12 retro discipline).
    """
    remaining = set(unconfirmed_order_ids)
    while remaining:
        await asyncio.sleep(retry_interval_s)
        try:
            exchange_open = await exchange_client.get_open_orders(symbol="")
            exchange_ids: set[str] = {o.order_id for o in exchange_open}
            confirmed = remaining & exchange_ids
            for oid in confirmed:
                log.info(
                    "reconciliation_retry_confirmed",
                    strategy=strategy_name,
                    order_id=oid,
                )
            remaining -= confirmed
            if remaining:
                log.info(
                    "reconciliation_retry_pending",
                    strategy=strategy_name,
                    pending_count=len(remaining),
                )
        except Exception as exc:
            log.error(
                "reconciliation_retry_failed",
                strategy=strategy_name,
                error=str(exc),
            )


async def run_startup_reconciliation(
    strategy: "BaseStrategy",
    order_worker: "OrderQueueWorker",
    exchange_client: ExchangeClient,
    exchange: str,
    questdb_http_addr: str,
    questdb_ilp_addr: str,
    timeout_s: float,
) -> None:
    """Reconcile open positions and non-terminal orders against exchange REST at startup.

    Sequence (AC1):
    1. Query QuestDB for non-terminal orders (strategy-scoped)
    2. Query exchange REST for all open orders (with timeout); skipped for paper trading
    3. Cross-reference: for each QuestDB non-terminal order, if exchange confirms it
       is still open → restore into order_worker; if exchange REST times out → use
       QuestDB-only state (degraded mode) and schedule 60-second background retry (AC3)
    4. Populate strategy._managed_positions for any symbols not covered by subscribe()

    Never raises — on failure, logs CRITICAL and degrades gracefully.
    """
    strategy_name = strategy._name

    # Step 1: query QuestDB non-terminal orders (sync, via thread)
    questdb_result = await asyncio.to_thread(
        query_questdb_nonterminal_orders,
        questdb_http_addr,
        strategy_name,
    )
    questdb_failed = questdb_result is None
    questdb_rows: list[dict[str, Any]] = questdb_result if questdb_result is not None else []
    questdb_order_ids: set[str] = {r["order_id"] for r in questdb_rows}

    if questdb_failed:
        log.critical("reconciliation_questdb_unavailable", strategy=strategy_name)

    # Step 2: query exchange REST with timeout (skipped in paper trading mode)
    paper_trading = strategy.paper_trading
    exchange_open: list[OpenOrder] = []
    exchange_failed = False

    if paper_trading:
        log.info("reconciliation_paper_trading_skip_exchange", strategy=strategy_name)
    else:
        try:
            exchange_open = await asyncio.wait_for(
                exchange_client.get_open_orders(symbol=""),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            exchange_failed = True
            log.critical(
                "reconciliation_timeout",
                strategy=strategy_name,
                timeout_s=timeout_s,
            )
        except Exception as exc:
            exchange_failed = True
            log.critical(
                "reconciliation_exchange_query_failed",
                strategy=strategy_name,
                error=str(exc),
            )

    # Step 3: cross-reference and restore open_orders
    if paper_trading:
        # Paper trading: no live exchange credentials; restore QuestDB state without
        # "unconfirmed" semantics — this is the expected path, not a degraded one.
        for row in questdb_rows:
            req, placed = _build_from_questdb_row(row, strategy_name)
            order_worker.restore_open_order(row["order_id"], req, placed)
            strategy._open_positions[row["symbol"]] = float(row.get("requested_size", 0.0))
            log.info(
                "reconciliation_paper_order_restored",
                strategy=strategy_name,
                order_id=row["order_id"],
                symbol=row["symbol"],
            )
    elif exchange_failed or questdb_failed:
        # Degraded mode: restore from QuestDB only, mark as unconfirmed (AC3)
        for row in questdb_rows:
            req, placed = _build_from_questdb_row(row, strategy_name)
            order_worker.restore_open_order(row["order_id"], req, placed)
            strategy._open_positions[row["symbol"]] = float(row.get("requested_size", 0.0))
            log.warning(
                "reconciliation_position_unconfirmed",
                strategy=strategy_name,
                order_id=row["order_id"],
                symbol=row["symbol"],
            )
        # AC3: schedule background retry every 60s until exchange confirms
        if questdb_rows:
            asyncio.create_task(
                _retry_reconciliation_background(
                    strategy_name=strategy_name,
                    unconfirmed_order_ids=questdb_order_ids,
                    exchange_client=exchange_client,
                )
            )
    else:
        # Normal mode: restore only orders confirmed on exchange
        exchange_ids: set[str] = {o.order_id for o in exchange_open}
        for row in questdb_rows:
            order_id = row["order_id"]
            if order_id in exchange_ids:
                req, placed = _build_from_questdb_row(row, strategy_name)
                order_worker.restore_open_order(order_id, req, placed)
                strategy._open_positions[row["symbol"]] = float(row.get("requested_size", 0.0))
                log.info(
                    "reconciliation_order_restored",
                    strategy=strategy_name,
                    order_id=order_id,
                    symbol=row["symbol"],
                )
            else:
                log.warning(
                    "reconciliation_questdb_order_not_on_exchange",
                    strategy=strategy_name,
                    order_id=order_id,
                    symbol=row["symbol"],
                )

        # Detect orphaned exchange orders; pass prefetched list to avoid second REST call (F6)
        try:
            await detect_orphaned_orders(
                exchange_client=exchange_client,
                exchange=exchange,
                questdb_ilp_addr=questdb_ilp_addr,
                strategy_name=strategy_name,
                known_order_ids=questdb_order_ids,
                prefetched_orders=exchange_open,
            )
        except Exception as exc:
            log.error(
                "reconciliation_orphan_detection_failed",
                strategy=strategy_name,
                error=str(exc),
            )

    # Step 4: force-subscribe — symbols with open positions not covered by subscribe()
    subscribed_symbols: set[str] = {sym for sym, _tf in strategy._bar_handlers}
    position_symbols: set[str] = {r["symbol"] for r in questdb_rows}
    for sym in position_symbols:
        if sym not in subscribed_symbols:
            strategy._managed_positions.add(sym)
            log.info(
                "reconciliation_force_subscribe",
                strategy=strategy_name,
                symbol=sym,
            )


def _build_from_questdb_row(
    row: dict[str, Any],
    strategy_name: str,
) -> tuple[OrderRequest, PlacedOrder]:
    """Build OrderRequest + PlacedOrder from a QuestDB non-terminal row."""
    req = OrderRequest(
        strategy=strategy_name,
        exchange="",           # exchange not stored in order_events, unknown at restore time
        symbol=str(row.get("symbol", "")),
        side=str(row.get("side", "")),
        order_type=str(row.get("order_type", "")),
        order_role=str(row.get("signal_type", "entry")),  # signal_type in DDL maps to order_role
        size=float(row.get("requested_size", 0.0)),
        limit_price=float(row["limit_price"]) if row.get("limit_price") is not None else None,
        client_order_id=str(row.get("client_order_id", "")),
    )
    placed = PlacedOrder(
        order_id=str(row.get("order_id", "")),
        client_order_id=str(row.get("client_order_id", "")),
        status="placed",
        ts_exchange=0,  # not available from QuestDB query; sentinel value
    )
    return req, placed
