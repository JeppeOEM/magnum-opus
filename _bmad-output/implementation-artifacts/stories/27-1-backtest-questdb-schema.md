---
id: 27-1
title: Backtest QuestDB schema — strategy_snapshots, backtest_runs, backtest_equity
epic: 27
status: ready-for-dev
---

# Story 27-1: Backtest QuestDB schema

## Context

Three new tables are needed to persist strategy versioning and backtest results:
- `strategy_snapshots` — one row per (strategy_name, hash); stores code snapshot
- `backtest_runs` — one row per execution; stores aggregated metrics
- `backtest_equity` — sampled equity curve points per run (separate table for queryability)

## What to build

### `bot_service/persistence/schema.py`

Append three DDL strings and add them to `_DDLS`:

```python
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
```

Add all three to `_DDLS`.

## Acceptance Criteria

- `apply_schema()` creates all three tables without error on a fresh QuestDB.
- Second call to `apply_schema()` is idempotent (IF NOT EXISTS).
- Unit test: mock httpx, assert all three table names appear in the DDL requests.

## Files
- `bot-service/bot_service/persistence/schema.py`
- `bot-service/tests/test_schema.py` (extend)
