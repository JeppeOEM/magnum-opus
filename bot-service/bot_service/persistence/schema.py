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

_DDL_STRATEGY_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS strategy_snapshots (
    strategy_name   SYMBOL CAPACITY 64,
    hash            SYMBOL CAPACITY 256,
    code            STRING,
    first_seen      TIMESTAMP
) TIMESTAMP(first_seen) PARTITION BY MONTH WAL;
"""

_DDL_BACKTEST_RUNS = """
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id              SYMBOL CAPACITY 1024,
    strategy_name       SYMBOL CAPACITY 64,
    hash                SYMBOL CAPACITY 256,
    symbol              SYMBOL CAPACITY 64,
    tf                  SYMBOL CAPACITY 16,
    exchange            SYMBOL CAPACITY 32,
    start_date          SYMBOL CAPACITY 32,
    end_date            SYMBOL CAPACITY 32,
    initial_capital     DOUBLE,
    final_value         DOUBLE,
    total_return_pct    DOUBLE,
    sharpe_ratio        DOUBLE,
    max_drawdown_pct    DOUBLE,
    n_trades            INT,
    win_rate_pct        DOUBLE,
    avg_pnl_per_trade   DOUBLE,
    total_fees_usd      DOUBLE,
    passes_fee_gate     BOOLEAN,
    run_at              TIMESTAMP
) TIMESTAMP(run_at) PARTITION BY MONTH WAL;
"""

_DDL_BACKTEST_EQUITY = """
CREATE TABLE IF NOT EXISTS backtest_equity (
    run_id          SYMBOL CAPACITY 1024,
    strategy_name   SYMBOL CAPACITY 64,
    bar_ts          TIMESTAMP,
    portfolio_value DOUBLE,
    run_at          TIMESTAMP
) TIMESTAMP(bar_ts) PARTITION BY MONTH WAL;
"""

_DDLS: list[tuple[str, str]] = [
    ("order_events", _DDL_ORDER_EVENTS),
    ("order_alerts", _DDL_ORDER_ALERTS),
    ("strategy_snapshots", _DDL_STRATEGY_SNAPSHOTS),
    ("backtest_runs", _DDL_BACKTEST_RUNS),
    ("backtest_equity", _DDL_BACKTEST_EQUITY),
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
