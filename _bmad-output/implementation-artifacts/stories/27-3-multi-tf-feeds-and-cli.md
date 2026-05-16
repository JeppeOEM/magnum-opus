---
id: 27-3
title: Multi-TF feeds and CLI entry point
epic: 27
status: ready-for-dev
---

# Story 27-3: Multi-TF feeds and CLI entry point

## Context

`feeds.py` currently only supports `snapshot_1s`. Strategies running on 1m or 15m
bars need feeds from `snapshot_1m` / `snapshot_15m`. The same `_TF_TABLE` mapping
already in `base.py` governs this. Also need a CLI so engineers can run backtests
from the terminal without starting the full service.

## What to build

### `bot_service/backtest/feeds.py` — multi-TF support

Add `_TF_TABLE` mapping (copy from `base.py`; avoid cross-package import to keep
backtest package self-contained):

```python
_TF_TABLE: dict[str, str] = {
    "1s": "snapshot_1s",
    "1m": "snapshot_1m",
    "5m": "snapshot_1m",
    "15m": "snapshot_15m",
    "1h": "snapshot_15m",
    "4h": "snapshot_15m",
    "1d": "snapshot_15m",
    "1w": "snapshot_15m",
}
```

Extract `_fetch_snapshot_1s` → `_fetch_snapshot(table, ...)` generic helper that takes
`table` as a parameter. Existing `_fetch_snapshot_1s` becomes a thin wrapper calling
`_fetch_snapshot("snapshot_1s", ...)`.

Update `QuestDBFeed.__init__` to accept `tf: str = "1s"` parameter and derive `table`
from `_TF_TABLE`. Keep `_CUSTOM_LINES` unchanged (same columns exist in all three tables).

### `bot_service/backtest/cli.py` (new)

```
python -m bot_service.backtest.cli \
    --strategy OFIBot \
    --symbol BTCUSDT \
    --tf 1s \
    --start 2026-01-01 \
    --end 2026-02-01 \
    --exchange bybit \
    [--capital 10000] \
    [--sample-every 60]
```

Uses `runner.run_backtest()` and prints JSON result to stdout. Does NOT write to
QuestDB (that's the REST layer's job). Exits 0 on success, 1 on error.

## Acceptance Criteria

- `QuestDBFeed` with `tf="1m"` queries `snapshot_1m`; with `tf="15m"` queries `snapshot_15m`.
- Unknown TF falls back to `snapshot_1s`.
- CLI `--help` works; missing required args exit non-zero.
- Unit tests: TF→table routing for 1s, 1m, 15m, unknown.

## Files
- `bot-service/bot_service/backtest/feeds.py`
- `bot-service/bot_service/backtest/cli.py` (new)
- `bot-service/tests/test_feeds.py` (extend)
