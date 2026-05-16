---
id: 27-2
title: Equity observer and backtest runner
epic: 27
status: ready-for-dev
---

# Story 27-2: Equity observer and backtest runner

## Context

Need a Backtrader observer that samples portfolio value every N bars, and a runner
module that ties together: hash file → load class → run cerebro → collect equity →
compute metrics → return structured result. This is the core backtest engine used
by both the CLI (27-3) and REST API (27-4).

## What to build

### `bot_service/backtest/equity.py` (new)

```python
import backtrader as bt

class EquitySampler(bt.Observer):
    """Samples broker portfolio value every `sample_every` bars."""
    params = (("sample_every", 60),)
    lines = ("portfolio_value",)

    def next(self) -> None:
        if len(self) % self.p.sample_every == 0:
            self.lines.portfolio_value[0] = self._owner.broker.getvalue()
        else:
            self.lines.portfolio_value[0] = float("nan")
```

Also expose `collect_equity(strat_instance) -> list[tuple[str, float]]`:
reads the observer's recorded values (skip NaN), returns `[(iso_datetime, value), ...]`.

### `bot_service/backtest/runner.py` (new)

```python
@dataclass
class BacktestResult:
    run_id: str
    strategy_name: str
    hash: str
    symbol: str
    tf: str
    exchange: str
    start_date: str
    end_date: str
    initial_capital: float
    final_value: float
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    n_trades: int
    win_rate_pct: float
    avg_pnl_per_trade: float
    total_fees_usd: float
    passes_fee_gate: bool
    equity_curve: list[tuple[str, float]]  # (iso_datetime, portfolio_value)
```

Functions:
- `hash_file(path: Path) -> str` — SHA-256 of file bytes, first 16 hex chars
- `load_strategy_class(path: Path) -> type` — importlib load (reuse pattern from registry.py); raises `ValueError` if zero or multiple BaseStrategy subclasses found
- `compute_metrics(cerebro_result, initial_capital) -> dict` — extract return%, sharpe, drawdown, n_trades, win_rate, fees from Backtrader analyzer results
- `run_backtest(path, symbol, tf, exchange, start, end, initial_capital, questdb_http_addr, questdb_ilp_addr, sample_every=60) -> BacktestResult`

`run_backtest` steps:
1. `hash = hash_file(path)`
2. `cls = load_strategy_class(path)`
3. Build `QuestDBFeed` (or multi-TF feed from story 27-3)
4. Add analyzers: `SharpeRatio`, `DrawDown`, `TradeAnalyzer`
5. Add `EquitySampler` observer
6. Add `BacktestResultWriter` (existing writer.py)
7. `results = cerebro.run()`
8. Collect equity from observer
9. Compute metrics
10. Return `BacktestResult`

Persistence of `strategy_snapshots` and `backtest_runs` rows is done in story 27-4 (REST layer), not here. `runner.py` is pure compute — no QuestDB writes except via `BacktestResultWriter` for fills.

## Acceptance Criteria

- `hash_file` returns a 16-char hex string; same file → same hash; changed file → different hash.
- `EquitySampler` records one non-NaN value every `sample_every` bars.
- `collect_equity` returns only non-NaN values as (datetime_str, float) tuples.
- `load_strategy_class` raises `ValueError` on files with no/multiple strategy classes.
- Unit tests: hash determinism, equity sampling interval, collect_equity skips NaN.

## Files
- `bot-service/bot_service/backtest/equity.py` (new)
- `bot-service/bot_service/backtest/runner.py` (new)
- `bot-service/tests/test_backtest_runner.py` (new)
