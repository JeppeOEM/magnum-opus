---
id: 27-4
title: Bot-service backtest REST API
epic: 27
status: ready-for-dev
---

# Story 27-4: Bot-service backtest REST API

## Context

Dashboard runs in a separate Docker container. It cannot import bot-service code
directly. Four REST endpoints expose backtest functionality over HTTP. Runs execute
in asyncio background tasks so the dashboard can fire multiple in parallel.

## What to build

### `bot_service/main.py` — 4 new endpoints

Module-level task/result state:
```python
_backtest_tasks:  dict[str, asyncio.Task]  = {}   # run_id → task
_backtest_results: dict[str, dict]         = {}   # run_id → {status, result?, error?}
```

**`GET /strategies`**
Scans `settings.bot_strategies_dir` for `*.py` files. For each file:
- Read content, compute `hash_file(path)` (from runner.py)
- Return list of `{name, hash, code}` (name = stem, no `.py`)

```json
[{"name": "OFIBot", "hash": "abc123def456", "code": "from __future__..."}]
```

**`POST /backtest/run`**
Body (JSON):
```json
{
  "strategy_name": "OFIBot",
  "symbol": "BTCUSDT",
  "tf": "1s",
  "exchange": "bybit",
  "start_date": "2026-01-01",
  "end_date": "2026-02-01",
  "initial_capital": 10000.0,
  "sample_every": 60
}
```
- Generate `run_id = str(uuid4())`
- Launch `asyncio.create_task(_run_backtest_task(run_id, ...))`
- Store `_backtest_results[run_id] = {"status": "running"}`
- Return `{"run_id": run_id, "status": "running"}`

`_run_backtest_task` (async, runs in thread via `asyncio.to_thread`):
1. Call `runner.run_backtest(...)`
2. Upsert `strategy_snapshots` if (name, hash) not already present (check via SELECT)
3. Insert `backtest_runs` row via ILP
4. Insert `backtest_equity` rows via ILP (one row per equity point)
5. On success: `_backtest_results[run_id] = {"status": "done", "result": metrics_dict}`
6. On error: `_backtest_results[run_id] = {"status": "failed", "error": str(exc)}`

**`GET /backtest/run/{run_id}`**
Return `_backtest_results.get(run_id)` or 404.
```json
{"run_id": "...", "status": "done", "result": {...metrics...}}
```

**`GET /backtest/runs`**
Query params: `strategy_name` (optional), `hash` (optional), `limit` (default 100).
Issues SELECT against `backtest_runs` in QuestDB, returns list.

### `bot_service/config.py`
Add `strategies_dir` already exists as `bot_strategies_dir`. No change needed.

## Acceptance Criteria

- `GET /strategies` returns at least the two files in `strategies/active/`.
- `POST /backtest/run` returns 200 with `run_id` and `status: "running"` immediately.
- `GET /backtest/run/{run_id}` returns 404 for unknown run_id.
- Multiple concurrent POSTs each get distinct run_ids.
- Unit tests: mock runner.run_backtest, assert task created, assert result stored on completion.

## Files
- `bot-service/bot_service/main.py`
- `bot-service/bot_service/backtest/runner.py` (used)
- `bot-service/tests/test_main.py` (extend)
