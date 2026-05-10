# Story 14.4: Walk-Forward, Stress Test & Monte Carlo Harnesses

## Status: done

## Story

**As** mrqdt,
**I want** automated validation harnesses with structured go/no-go reports,
**so that** no strategy is paper-deployed without passing quantitative validation gates.

## Acceptance Criteria

- **AC1:** Given `bot_service/backtest/validation.py` with `run_walk_forward(strategy_cls, feed, n_splits, ...)`, when executed, then it partitions the dataset into `n_splits` in-sample / out-of-sample folds (expanding window); runs Backtrader on each fold; computes Sharpe ratio, max drawdown, and P&L degradation (out-of-sample P&L as fraction of in-sample P&L) for each fold; returns a `WalkForwardReport` dataclass.

- **AC2:** Given `run_stress_test(strategy_cls, feed)`, when executed, then it runs the strategy on three named stress windows: LUNA collapse (`2022-05-05` to `2022-05-15`), FTX collapse (`2022-11-07` to `2022-11-14`), and a configurable flash crash window; returns a `StressTestReport` dataclass with per-window Sharpe and drawdown.

- **AC3:** Given `run_monte_carlo(trade_pnls, n_shuffles=10_000)`, when executed, then it shuffles the sequence `n_shuffles` times, computing sum P&L for each shuffle; returns the 5th-percentile P&L across all shuffles.

- **AC4:** Given `generate_validation_report(walk_forward, stress, monte_carlo_pct5) -> ValidationReport`, when executed, then it produces a `ValidationReport` with `passes: bool` that is `True` only when ALL hold: Sharpe >= 1.0, max drawdown <= 0.15, P&L degradation <= 0.30 (OOS P&L >= 70% of IS), Monte Carlo 5th-pct > 0; the report is serializable to JSON via `report.to_json()`; thresholds are constructor parameters with the above as defaults.

- **AC5:** Given the fee impact gate (Story 14.3) and the validation report, when a strategy is being evaluated, then fee impact analysis runs FIRST; if it fails, walk-forward and stress tests are skipped; the validation report records which gates were run and their outcomes.

## Tasks / Subtasks

- [x] T1: Implement `_partition_dataframe` and `WalkForwardReport` dataclass
  - [x] T1.1: Write failing L1 tests for fold partitioning (expanding window)
  - [x] T1.2: Implement `_partition_dataframe(df, n_splits)` → `list[tuple[pd.DataFrame, pd.DataFrame]]`
  - [x] T1.3: Implement `WalkForwardReport` and `FoldResult` frozen dataclasses
  - [x] T1.4: Tests pass

- [x] T2: Implement `run_walk_forward` using backtrader cerebro
  - [x] T2.1: Implement `run_walk_forward(strategy_cls, feed, n_splits, commission_info)` using `_partition_dataframe` + cerebro per fold
  - [x] T2.2: Extract Sharpe, DrawDown, net P&L from analyzers; handle None Sharpe (no trades → 0.0)
  - [x] T2.3: Compute pnl_degradation = oos_pnl / is_pnl if is_pnl != 0 else 0.0

- [x] T3: Implement `StressTestReport` and `run_stress_test`
  - [x] T3.1: Implement `StressWindowResult` and `StressTestReport` frozen dataclasses
  - [x] T3.2: Implement `run_stress_test(strategy_cls, feed, flash_crash_start, flash_crash_end)` with 3 named windows

- [x] T4: Implement `run_monte_carlo` (pure function — L1 testable)
  - [x] T4.1: Write failing L1 tests for 5th-percentile computation on known distribution
  - [x] T4.2: Implement `run_monte_carlo(trade_pnls, n_shuffles=10_000)` using `np.random.default_rng`
  - [x] T4.3: Tests pass

- [x] T5: Implement `ValidationReport` and `generate_validation_report`
  - [x] T5.1: Write failing L1 tests — passes=False when any threshold fails; JSON round-trip
  - [x] T5.2: Implement `ValidationReport` dataclass with `to_json()` method
  - [x] T5.3: Implement `generate_validation_report` with threshold parameters and gate tracking
  - [x] T5.4: Tests pass

- [x] T6: Run full test suite — 0 regressions, mypy --strict clean

## Dev Notes

### File locations

- **CREATE** `bot_service/backtest/validation.py`
- **CREATE** `bot_service/tests/test_validation.py`
- Do NOT modify `commission.py`, `fee_impact.py`, `feeds.py`, or `strategy/signals/`
- `numpy` is NOT in pyproject.toml — check before using. Use `random` stdlib if not available.

### Checking numpy availability

```bash
python3 -c "import numpy; print(numpy.__version__)"
```

If not available, use `random.shuffle` in a loop for Monte Carlo instead.

### Backtrader analyzer APIs (verified in backtrader 1.9.78)

```python
# SharpeRatio
cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe", timeframe=bt.TimeFrame.Days)
sharpe_analysis = strat.analyzers.sharpe.get_analysis()
sharpe = sharpe_analysis.get("sharperatio") or 0.0  # can be None if no trades

# DrawDown — returns percentage value (15.0 means 15%, NOT 0.15)
cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
dd_analysis = strat.analyzers.drawdown.get_analysis()
max_dd_pct = dd_analysis["max"]["drawdown"]  # e.g. 15.0 for 15%
max_dd = max_dd_pct / 100.0  # convert to ratio [0, 1]

# TradeAnalyzer — net P&L for all closed trades
cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
trades_analysis = strat.analyzers.trades.get_analysis()
# If no trades: analysis only has {'total': {'total': 0}}
total_closed = trades_analysis.get("total", {}).get("closed", 0)
net_pnl = trades_analysis.get("pnl", {}).get("net", {}).get("total", 0.0) if total_closed > 0 else 0.0
```

**Key gotcha:** `SharpeRatio.get_analysis()['sharperatio']` returns `None` (not 0.0) when there are no trades or insufficient data. Always guard with `or 0.0`.

**DrawDown returns percentage, not decimal.** `max.drawdown` of 15.0 = 15% drawdown. Divide by 100 to get decimal ratio for comparison against the 0.15 threshold.

### `_partition_dataframe` — expanding window walk-forward

```python
def _partition_dataframe(
    df: pd.DataFrame,
    n_splits: int,
) -> list[tuple[pd.DataFrame, pd.DataFrame]]:
    """Split df into n_splits IS/OOS fold pairs using expanding window.

    fold_size = len(df) // (n_splits + 1)
    Fold k (0-indexed): IS = df[:fold_size*(k+1)], OOS = df[fold_size*(k+1):fold_size*(k+2)]
    """
    n = len(df)
    fold_size = n // (n_splits + 1)
    folds = []
    for k in range(n_splits):
        is_end = fold_size * (k + 1)
        oos_end = fold_size * (k + 2)
        in_sample = df.iloc[:is_end]
        out_of_sample = df.iloc[is_end:oos_end]
        folds.append((in_sample, out_of_sample))
    return folds
```

**L1 test approach:** Pass a 100-row DataFrame with `n_splits=3`. Assert:
- `fold_size = 100 // 4 = 25`
- Fold 0: IS = 25 rows, OOS = 25 rows
- Fold 1: IS = 50 rows, OOS = 25 rows
- Fold 2: IS = 75 rows, OOS = 25 rows

### `run_walk_forward` implementation

```python
def run_walk_forward(
    strategy_cls: type,
    feed: Any,  # bt.feeds.PandasData — has .p.dataname (pd.DataFrame)
    n_splits: int = 3,
    commission_info: Any | None = None,
    starting_cash: float = 10_000.0,
) -> WalkForwardReport:
    df: pd.DataFrame = feed.p.dataname
    folds = _partition_dataframe(df, n_splits)
    results = []
    for fold_idx, (is_df, oos_df) in enumerate(folds):
        is_pnl = _run_cerebro(strategy_cls, is_df, commission_info, starting_cash)
        oos_sharpe, oos_dd, oos_pnl = _run_cerebro_full(strategy_cls, oos_df, commission_info, starting_cash)
        degradation = oos_pnl / is_pnl if is_pnl != 0.0 else 0.0
        results.append(FoldResult(
            fold=fold_idx,
            sharpe=oos_sharpe,
            max_drawdown=oos_dd,
            pnl_degradation=degradation,
        ))
    ...
```

**Separate IS and OOS runs:** The IS run is used only to compute `is_pnl` (for degradation). The OOS run provides Sharpe, drawdown, and `oos_pnl`. Both use the same `starting_cash` so results are comparable.

### `run_monte_carlo` — pure function

```python
import numpy as np  # or use random stdlib
import numpy.typing as npt

def run_monte_carlo(
    trade_pnls: list[float] | pd.Series,
    n_shuffles: int = 10_000,
    seed: int | None = None,
) -> float:
    """Shuffle trade P&L sequence n_shuffles times, return 5th-percentile sum P&L."""
    pnls = list(trade_pnls)
    if not pnls:
        return 0.0
    rng = np.random.default_rng(seed)  # seeded for reproducible tests
    shuffled_totals = []
    for _ in range(n_shuffles):
        shuffled = pnls.copy()
        rng.shuffle(shuffled)  # type: ignore[arg-type]
        shuffled_totals.append(sum(shuffled))
    return float(np.percentile(shuffled_totals, 5))
```

**L1 test:** Pass `[1.0] * 10` with `n_shuffles=100, seed=42`. The 5th percentile should be 10.0 (all trades are +1.0 so any shuffle sums to 10.0).

Pass `[1.0, -10.0]` with enough shuffles. The 5th percentile will be negative (the -10.0 dominates).

### `ValidationReport` dataclass

```python
from __future__ import annotations
import dataclasses
import json
from dataclasses import dataclass

@dataclass
class ValidationReport:
    passes: bool
    # Gates run tracking
    fee_gate_ran: bool
    fee_gate_passed: bool | None  # None = not run
    walk_forward_ran: bool
    stress_test_ran: bool
    monte_carlo_ran: bool
    # Aggregated metric summaries
    mean_sharpe: float
    mean_drawdown: float
    mean_degradation: float
    monte_carlo_5th_pct: float
    # Individual threshold checks
    sharpe_ok: bool
    drawdown_ok: bool
    degradation_ok: bool
    monte_carlo_ok: bool
    # Thresholds used
    min_sharpe: float
    max_drawdown_threshold: float
    max_degradation: float

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), default=str)
```

**NOT frozen** — `passes` is set after all checks; `to_json` uses `dataclasses.asdict` which handles all primitive types. The `default=str` handles any non-serializable objects defensively.

### `generate_validation_report` — threshold logic

```python
def generate_validation_report(
    walk_forward: WalkForwardReport | None,
    stress: StressTestReport | None,
    monte_carlo_pct5: float | None,
    fee_gate: FeeImpactReport | None = None,
    min_sharpe: float = 1.0,
    max_drawdown_threshold: float = 0.15,
    max_degradation: float = 0.30,
) -> ValidationReport:
    fee_gate_ran = fee_gate is not None
    fee_gate_passed = fee_gate.passes if fee_gate_ran else None

    # If fee gate ran and failed → skip validation
    if fee_gate_ran and not fee_gate_passed:
        return ValidationReport(
            passes=False,
            fee_gate_ran=True, fee_gate_passed=False,
            walk_forward_ran=False, stress_test_ran=False, monte_carlo_ran=False,
            mean_sharpe=0.0, mean_drawdown=0.0, mean_degradation=0.0,
            monte_carlo_5th_pct=0.0,
            sharpe_ok=False, drawdown_ok=False, degradation_ok=False, monte_carlo_ok=False,
            min_sharpe=min_sharpe, max_drawdown_threshold=max_drawdown_threshold,
            max_degradation=max_degradation,
        )

    mean_sharpe = walk_forward.mean_sharpe if walk_forward else 0.0
    mean_drawdown = walk_forward.mean_drawdown if walk_forward else 0.0
    mean_degradation = walk_forward.mean_degradation if walk_forward else 0.0
    mc_pct5 = monte_carlo_pct5 if monte_carlo_pct5 is not None else 0.0

    sharpe_ok = mean_sharpe >= min_sharpe
    drawdown_ok = mean_drawdown <= max_drawdown_threshold
    degradation_ok = mean_degradation <= max_degradation
    monte_carlo_ok = mc_pct5 > 0.0

    return ValidationReport(
        passes=all([sharpe_ok, drawdown_ok, degradation_ok, monte_carlo_ok]),
        fee_gate_ran=fee_gate_ran, fee_gate_passed=fee_gate_passed,
        walk_forward_ran=walk_forward is not None,
        stress_test_ran=stress is not None,
        monte_carlo_ran=monte_carlo_pct5 is not None,
        mean_sharpe=mean_sharpe, mean_drawdown=mean_drawdown,
        mean_degradation=mean_degradation, monte_carlo_5th_pct=mc_pct5,
        sharpe_ok=sharpe_ok, drawdown_ok=drawdown_ok,
        degradation_ok=degradation_ok, monte_carlo_ok=monte_carlo_ok,
        min_sharpe=min_sharpe, max_drawdown_threshold=max_drawdown_threshold,
        max_degradation=max_degradation,
    )
```

### `StressTestReport` — named windows

```python
STRESS_WINDOWS = [
    ("luna_collapse", "2022-05-05", "2022-05-15"),
    ("ftx_collapse", "2022-11-07", "2022-11-14"),
]

@dataclass(frozen=True)
class StressWindowResult:
    name: str
    start: str
    end: str
    sharpe: float
    max_drawdown: float

@dataclass(frozen=True)
class StressTestReport:
    windows: tuple[StressWindowResult, ...]
```

`run_stress_test` accepts optional `flash_crash_start` and `flash_crash_end` parameters (both `str | None`). If provided, adds a third window named `"flash_crash"`.

For each window, filter `feed.p.dataname` by the timestamp range, then run cerebro on the slice.

### mypy --strict notes

- `from typing import Any` — use for `strategy_cls: type` (or `type[Any]`) and `feed: Any`
- `numpy` may not have stubs — add `[[tool.mypy.overrides]] module = "numpy" \n ignore_missing_imports = true` if needed
- `FeeImpactReport` import: `from bot_service.backtest.fee_impact import FeeImpactReport`
- `ValidationReport` is NOT frozen (needs mutable construction pattern above); use `@dataclass` not `@dataclass(frozen=True)`

### Learnings from Stories 14.2 and 14.3

- Verify backtrader API signatures by inspection (`inspect.getsource`) — don't assume from docs
- `bt.CommissionInfo._getcommission` takes `(size, price, pseudoexec)` NOT 10 params
- `DrawDown` returns percentage (15.0), not decimal (0.15) — always divide by 100
- `SharpeRatio` can return `None` — always guard with `or 0.0`
- structlog uses its own logging pipeline, not stdlib logging — use `capsys` in tests, not `caplog`
- numpy may not be installed in the venv — check first

## Test Coverage

All tests `@pytest.mark.l1`. Target: 8 tests in `tests/test_validation.py`.

### Partition tests (2)
1. `test_partition_dataframe_fold_sizes` — 100-row df, n_splits=3 → fold_size=25; IS[0]=25 rows, OOS[0]=25; IS[1]=50 rows, OOS[1]=25; IS[2]=75 rows, OOS[2]=25
2. `test_partition_dataframe_index_preserved` — partition preserves original DatetimeIndex (no reset)

### Monte Carlo tests (2)
3. `test_monte_carlo_constant_pnl` — `[1.0]*10`, n_shuffles=100, seed=42 → 5th pct = 10.0
4. `test_monte_carlo_mixed_pnl_fifth_pct_negative` — `[1.0, -5.0]`, n_shuffles=1000, seed=0 → 5th pct < 0

### ValidationReport tests (3)
5. `test_validation_report_passes_when_all_thresholds_met` — sharpe=1.5, dd=0.10, deg=0.20, mc=5.0 → passes=True
6. `test_validation_report_fails_when_sharpe_below_threshold` — sharpe=0.5 → passes=False, sharpe_ok=False
7. `test_validation_report_fee_gate_failed_skips_rest` — pass fee_gate with passes=False → walk_forward_ran=False, passes=False
8. `test_validation_report_json_roundtrip` — `report.to_json()` → `json.loads()` → all fields preserved

## Senior Developer Review (AI)

**Date:** 2026-05-10 | **Outcome:** Changes Requested → All patches applied

### Action Items

- [x] [High] Degradation gate semantics inverted — `oos_pnl/is_pnl <= 0.30` passed when OOS earned only 30% of IS; fixed to `1.0 - oos_pnl/is_pnl` (fraction of IS P&L lost); gate `<= 0.30` now correctly passes when OOS retains >= 70% of IS
- [x] [High] `dd_analysis["max"]["drawdown"]` KeyError when no trades occur — fixed to `.get("max", {}).get("drawdown", 0.0)`
- [x] [High] `n_shuffles=0` crashes via empty list to `np.percentile` — fixed with early return
- [x] [Med] `fold_size=0` when df has fewer rows than `n_splits+1` — added `ValueError` guard
- [x] [Med] Stress window naive string vs tz-aware DatetimeIndex comparison — fixed to `pd.Timestamp(start, tz="UTC")`
- [x] [User] Removed hardcoded LUNA/FTX stress windows; `run_stress_test` now takes caller-provided `windows: list[tuple[str, str, str]] | None` — user will define windows once data is collected
- [x] [Defer] Stress test results not gating `passes` (by design — informational only) → D-14-4-1
- [x] [Defer] `sum(shuffled)` is commutative — shuffle is spec-defined behavior, known limitation → D-14-4-2

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Completion Notes

- `_partition_dataframe`: expanding window, fold_size = n // (n_splits+1); index preserved (iloc slicing)
- `run_walk_forward`: separate IS and OOS cerebro runs; DrawDown /100 for ratio; Sharpe guarded with `or 0.0`
- `run_stress_test`: string date filtering via boolean mask on df.index; empty window → 0.0 metrics
- `run_monte_carlo`: numpy 1.26 `default_rng`; seeded for reproducible tests; list.copy() + sum()
- `generate_validation_report`: fee_gate short-circuit when failed; `fee_gate.passes if fee_gate is not None else None` for mypy
- `ValidationReport`: NOT frozen (mutable construction); `to_json()` uses `dataclasses.asdict()` + `json.dumps(default=str)`
- mypy note: backtrader import needs no `# type: ignore` (ignore_errors=true covers it); index comparison needs no ignore
- 8 L1 tests; 164 total green; mypy --strict clean (6 files in backtest/)

### File List

- `bot_service/backtest/validation.py` (CREATE)
- `bot_service/tests/test_validation.py` (CREATE)
