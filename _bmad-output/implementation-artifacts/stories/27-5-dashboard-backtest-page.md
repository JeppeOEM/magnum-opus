---
id: 27-5
title: Dashboard backtest page
epic: 27
status: ready-for-dev
---

# Story 27-5: Dashboard backtest page

## Context

New `/backtests` Dash page. Lets the user select a strategy, view its source code,
configure a backtest, run it (with live status), view results, and browse history
grouped by strategy version (hash).

## What to build

### `dashboard/backtest_data.py` (new)

HTTP helpers (all use env vars for addresses):
- `BOT_SERVICE_URL = os.environ.get("BOT_SERVICE_ADDR", "http://bot-service:8090")`
- `QUESTDB_URL = os.environ.get("QUESTDB_HTTP_ADDR", "http://questdb:9000")`

Functions:
- `fetch_strategies() -> list[dict]` — GET /strategies
- `submit_backtest(strategy_name, symbol, tf, exchange, start_date, end_date, capital, sample_every=60) -> dict` — POST /backtest/run
- `poll_run_status(run_id: str) -> dict` — GET /backtest/run/{run_id}
- `fetch_run_history(strategy_name: str | None = None, hash_filter: str | None = None, limit: int = 200) -> pd.DataFrame` — QuestDB SELECT from backtest_runs
- `fetch_equity_curve(run_id: str) -> pd.DataFrame` — QuestDB SELECT from backtest_equity WHERE run_id=...

### `dashboard/layout_backtest.py` (new)

```
┌─────────────────────────────────────────────────────────────┐
│  Strategy [dropdown]  Exchange [dropdown]  Symbol [input]    │
│  TF [dropdown]  Start [date]  End [date]  Capital [$input]   │
│                                       [▶ Run Backtest]       │
├──────────────────────────┬──────────────────────────────────┤
│ Strategy Code            │ Last Run Metrics                  │
│ <html.Pre id=code-view>  │  Return: +X%   Sharpe: X.X       │
│                          │  Drawdown: X%  Trades: N         │
│                          │  Win rate: X%  Fee gate: ✓/✗     │
│                          │  <equity curve line chart>        │
├──────────────────────────┴──────────────────────────────────┤
│ Run History                                                   │
│ [All versions ▼] [Filter by hash ▼]                          │
│ run_at | hash | strategy | return% | sharpe | drawdown | ... │
└─────────────────────────────────────────────────────────────┘
```

IDs used: `backtest-strategy-dd`, `backtest-exchange-dd`, `backtest-symbol-inp`,
`backtest-tf-dd`, `backtest-start-date`, `backtest-end-date`, `backtest-capital-inp`,
`backtest-run-btn`, `backtest-status-div`, `backtest-code-view`, `backtest-metrics-div`,
`backtest-equity-chart`, `backtest-history-table`, `backtest-hash-filter-dd`,
`backtest-poll-interval`, `backtest-run-id-store` (dcc.Store).

### `dashboard/callbacks_backtest.py` (new)

Callbacks:
1. `on_strategy_select(strategy_name)` → populate code viewer + load history table
2. `on_run_click(n_clicks, strategy_name, exchange, symbol, tf, start, end, capital)` → call `submit_backtest`, store run_id in dcc.Store, activate polling interval
3. `on_poll(n_intervals, run_id)` → call `poll_run_status`; if done: update metrics div + equity chart + history table, stop interval; if failed: show error
4. `on_hash_filter(hash_val, strategy_name)` → filter history table

### `dashboard/app.py`
Add `import callbacks_backtest  # noqa: F401, E402` after existing imports.

### `dashboard/layout.py`
Add "Backtests" nav link alongside existing nav items.

### `dashboard/Dockerfile`
Add `backtest_data.py layout_backtest.py callbacks_backtest.py` to COPY line.

## Acceptance Criteria

- `/backtests` page loads without errors.
- Strategy dropdown populated from `GET /strategies`.
- Clicking a strategy shows its source code.
- Run button submits POST and shows "Running..." status.
- Polling interval fires until status = done/failed.
- On done: metrics cards and equity curve populate.
- History table shows all previous runs; hash filter narrows rows.
- No errors when bot-service is unreachable (show "unavailable" message).

## Files
- `dashboard/backtest_data.py` (new)
- `dashboard/layout_backtest.py` (new)
- `dashboard/callbacks_backtest.py` (new)
- `dashboard/app.py`
- `dashboard/layout.py`
- `dashboard/Dockerfile`
