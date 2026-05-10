from __future__ import annotations

import structlog
import httpx

log = structlog.get_logger()


class SchemaApplyError(Exception):
    pass


_DDL_ORDER_EVENTS = """
CREATE TABLE IF NOT EXISTS order_events (
    ts                  TIMESTAMP,
    order_id            SYMBOL,
    client_order_id     SYMBOL,
    strategy            SYMBOL,
    exchange            SYMBOL,
    symbol              SYMBOL,
    market_type         SYMBOL,
    side                SYMBOL,
    order_type          SYMBOL,
    status              SYMBOL,
    limit_price         DOUBLE,
    stop_price          DOUBLE,
    take_profit_price   DOUBLE,
    requested_size      DOUBLE,
    filled_size         DOUBLE,
    remaining_size      DOUBLE,
    avg_fill_price      DOUBLE,
    fee                 DOUBLE,
    fee_currency        SYMBOL,
    realized_pnl        DOUBLE,
    slippage            DOUBLE,
    position_size_after DOUBLE,
    signal_type         SYMBOL,
    paper_trading       BOOLEAN,
    backtest            BOOLEAN,
    ts_placed           TIMESTAMP,
    ts_exchange         TIMESTAMP
) TIMESTAMP(ts) PARTITION BY DAY WAL;
"""

_DDL_ORDER_ALERTS = """
CREATE TABLE IF NOT EXISTS order_alerts (
    ts           TIMESTAMP,
    order_id     SYMBOL,
    strategy     SYMBOL,
    alert_type   SYMBOL,
    detail       STRING,
    resolved     BOOLEAN
) TIMESTAMP(ts) PARTITION BY DAY WAL;
"""

_DDLS: list[tuple[str, str]] = [
    ("order_events", _DDL_ORDER_EVENTS),
    ("order_alerts", _DDL_ORDER_ALERTS),
]


def apply_schema(questdb_http_addr: str) -> None:
    """Create both QuestDB tables idempotently via the REST /exec endpoint.

    Parameters
    ----------
    questdb_http_addr:
        Base URL of the QuestDB HTTP API, e.g. ``"http://localhost:9000"``.

    Raises
    ------
    SchemaApplyError
        If QuestDB is unreachable or returns a non-2xx response for either DDL.
    """
    with httpx.Client(timeout=30.0) as client:
        for table_name, ddl in _DDLS:
            try:
                resp = client.get(
                    f"{questdb_http_addr}/exec",
                    params={"query": ddl},
                )
                resp.raise_for_status()
                body = resp.json()
                # QuestDB returns {"error": "..."} with HTTP 200 on DDL errors
                if "error" in body:
                    raise SchemaApplyError(
                        f"QuestDB DDL error for {table_name}: {body['error']}"
                    )
            except SchemaApplyError:
                raise
            except httpx.HTTPError as exc:
                log.error(
                    "schema_apply_failed",
                    table=table_name,
                    error=str(exc),
                )
                raise SchemaApplyError(str(exc)) from exc
