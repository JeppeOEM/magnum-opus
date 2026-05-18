---
id: 29-3
title: Validation suite REST endpoint
epic: 29
status: ready-for-dev
---

# Story 29-3: Validation suite REST endpoint

## Context

`run_walk_forward`, `run_stress_test`, `run_monte_carlo`, and `generate_validation_report` are fully implemented in `bot_service/backtest/validation.py` but are not callable from the REST API or dashboard. This story exposes them via a new `POST /backtest/validate` endpoint so operators can validate a strategy before live deployment without using the CLI.

## What to build

### `bot_service/main.py`

Add `BacktestValidateRequest` model:

```python
class BacktestValidateRequest(BaseModel):
    strategy_name: str
    symbol: str
    exchange: str = "bybit"
    timeframe: str = "1s"
    start_date: str  # ISO date e.g. "2024-01-01"
    end_date: str    # ISO date e.g. "2024-06-01"
    n_splits: int = 3
    starting_cash: float = 10_000.0
    min_sharpe: float = 1.0
    max_drawdown_threshold: float = 0.15
    max_degradation: float = 0.30
    stress_max_drawdown_threshold: float = 0.30
    stress_windows: list[tuple[str, str, str]] = []  # [(name, start, end), ...]
```

Add `POST /backtest/validate` endpoint that runs asynchronously (same pattern as `/backtest/run`):

1. Validates that `strategy_name` exists in the strategies directory.
2. Builds a `QuestDBFeed` from QuestDB for `symbol/exchange/timeframe` between `start_date` and `end_date`.
3. Runs `run_walk_forward(strategy_cls, feed, n_splits, ...)`.
4. Runs `run_stress_test(strategy_cls, feed, stress_windows, ...)` if `stress_windows` is non-empty.
5. Extracts trade PnL from the walk-forward OOS folds and runs `run_monte_carlo(trade_pnls, 10_000)`.
6. Calls `generate_validation_report(walk_forward, stress, mc_pct5, ...)`.
7. Returns `ValidationReport.to_json()`.

Store validation results in `_validation_results: OrderedDict[str, dict[str, Any]]` (same pattern as `_backtest_results`, max 50 entries).

Add `GET /backtest/validate/{run_id}` to poll for validation status.

The endpoint returns immediately with `{"run_id": "<uuid>", "status": "running"}` and computes asynchronously.

### Trade PnL extraction for Monte Carlo

After walk-forward runs, extract trade-level P&L from the OOS folds using `TradeAnalyzer`:

```python
# After _run_cerebro_full for OOS fold
# TradeAnalyzer.pnl.net.total is aggregate; for Monte Carlo we need per-trade PnL
# Use a simple approach: collect per-trade P&L via a custom Analyzer
```

Add a `_TradeListAnalyzer(bt.Analyzer)` that accumulates per-trade net P&L values into a list; use it in `_run_cerebro_full` when `collect_trades=True`.

## Acceptance Criteria

- `POST /backtest/validate` returns `{"run_id": "...", "status": "running"}` within 200ms.
- `GET /backtest/validate/{run_id}` returns `{"status": "running"}` while in progress and `{"status": "done", "result": {...}}` when complete.
- When the strategy passes all gates, `result.passes == true`.
- When a stress window exceeds `stress_max_drawdown_threshold`, `result.passes == false` and `stress_drawdown_ok == false`.
- If `strategy_name` is not found in strategies directory, endpoint returns 404.
- Unit tests: mock QuestDBFeed construction; assert `run_walk_forward` and `generate_validation_report` called with correct parameters.

## Files
- `bot-service/bot_service/main.py`
- `bot-service/bot_service/backtest/validation.py` (add `_TradeListAnalyzer`)
- `bot-service/tests/test_validation.py` (extend)
