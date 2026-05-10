from __future__ import annotations

import time
from typing import Any

import structlog
from questdb.ingress import Sender, TimestampNanos

from bot_service.exchange import ExchangeClient
from bot_service.metrics.prometheus import inc_orphaned_order

log = structlog.get_logger()


async def detect_orphaned_orders(
    exchange_client: ExchangeClient,
    exchange: str,
    questdb_ilp_addr: str,
    strategy_name: str,
    known_order_ids: set[str],
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
    """
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
    import asyncio

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
