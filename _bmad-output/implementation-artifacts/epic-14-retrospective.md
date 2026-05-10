# Epic 14 Retrospective: Backtrader Backtesting — Validate Before Deploying

**Date:** 2026-05-10
**Epic Status:** Done (all 5 stories complete)

## Stories Completed

| Story | Title | Tests Added | Key Outcome |
|-------|-------|-------------|-------------|
| 14.1 | Shared Signal Layer — Pure Functions | 13 L1 | Three pure signal functions (MA cross, OFI, funding-rate arb) with `SignalResult(direction, confidence)` typed output |
| 14.2 | QuestDBFeed — 67-Feature Backtrader Data Feed | 7 L1 | `QuestDBFeed` wraps HTTP `/exec` query into `bt.feeds.PandasData`; 79 columns incl. 67 microstructure features; gap rows replaced with NaN |
| 14.3 | Custom CommissionInfo — Exchange Fee Models | 11 L1 | `KuCoinCommission` / `BybitCommission` with per-trade fee; `FundingCommission` with `asof()` funding rate lookup; `fee_impact_gate` threshold check |
| 14.4 | Walk-Forward, Stress Test & Monte Carlo Harnesses | 8 L1 | Expanding-window walk-forward; Monte Carlo 5th-percentile; `ValidationReport` with passes/fails gate; caller-supplied stress windows |
| 14.5 | Backtest Results Persistence | 4 L2 | `BacktestResultWriter` ILP writer; `run_backtest_and_persist` cerebro wrapper; backtest rows isolated with `backtest=true` flag; CRITICAL log on failure |

**Total tests added:** 39 L1 + 4 L2 (175+ at epic completion)

---

## What Went Well

### 1. The `notify_order` injection pattern is clean and non-invasive
`run_backtest_and_persist` uses a dynamic inner class that subclasses the user's strategy and overrides `notify_order`, capturing `writer` and `last_bar_ts` via closure. User strategies never know they're being wrapped — no interface changes, no base class requirements. This is the correct Python pattern for this problem and it required no `# type: ignore` gymnastics beyond one `[misc]` on the class declaration.

### 2. Adversarial review caught a class of bug (inverted gate) that unit tests cannot catch
Story 14.4's `mean_degradation <= 0.30` passed when OOS P&L was only 30% of IS P&L — exactly backwards. The unit tests passed because they only verified that the gate produced *some* result, not that it correctly discriminated bad strategies. The semantic error was caught by the review's AC re-read, not by test execution. This underscores the value of adversarial review for threshold / inequality logic.

### 3. `calendar.timegm()` for Backtrader UTC timestamps is the only correct approach
`bt.num2date()` returns naive datetimes representing UTC. `datetime.timestamp()` interprets naive datetimes in local time — silently wrong on any machine not in UTC. `calendar.timegm()` interprets the struct_time as UTC unconditionally. This was caught in review (14.5) and is now documented for any future Backtrader integrations.

### 4. Removing hardcoded stress windows was the right call
The initial implementation hardcoded LUNA collapse and FTX collapse as stress windows. The user correctly identified that these date ranges lie outside the available historical data — the stress test would silently produce no results for those windows. Removing them and making the window list a caller argument is the right interface: stress windows depend on what data the operator actually has.

### 5. Testcontainers with two-port exposure works cleanly
The QuestDB fixture (`DockerContainer.with_exposed_ports(9000, 9009)`) confirms that testcontainers-python's variadic API accepts both ports in a single call. The `_query_rows` polling pattern (0.5s sleep loop up to 10s) correctly handles ILP's async commit semantics — no test flakiness observed.

### 6. `bool` NaN columns in 14.2 required an explicit fix before Backtrader accepted the feed
Backtrader's `PandasData` loader rejects `NaN` in boolean columns. The `QuestDBFeed._fill_gap_rows()` replacement with `False` for boolean NaN was found during implementation of 14.2 (before review). Catching it in the unit tests (test with a gap row in the fixture) was the right level to test this.

---

## What Could Be Improved

### 1. Monte Carlo sum-of-shuffles is mathematically equivalent to unshuffled sum
`sum(shuffled_pnls)` equals `sum(original_pnls)` for any permutation — addition is commutative. Every Monte Carlo trial returns the same value. The 5th-percentile is just total P&L. This is deferred (D-14-4-2) but should be fixed in a future story: the correct approach is bootstrap resampling *with replacement* (samples N trades with replacement, computing path-dependent cumulative returns). The spec defined the current behavior explicitly; the deficiency was documented but not corrected.

### 2. `realized_pnl` is always 0.0 in persistence
Backtrader does not expose realized P&L per fill in `notify_order`. The buy/sell pairing required to compute realized P&L requires tracking cost basis across fills — a non-trivial stateful computation. Left as 0.0 for now (D-14-5-2). For Grafana P&L attribution, this field is currently useless in backtest mode.

### 3. `_sync_ilp_write` opens a new TCP connection per fill
Every completed order creates a new TCP connection (`Sender.from_conf(...)` inside the method). For large backtests with thousands of fills this produces thousands of sequential connects. Deferred (D-14-5-1) but should be fixed before any production-scale backtest: open the `Sender` once per `run_backtest_and_persist` call and reuse across all `write_fill` calls.

### 4. Degradation semantics required a re-read of the threshold definition
The inverted degradation gate (`oos_pnl / is_pnl <= 0.30` was incorrect; correct form is `1.0 - oos_pnl / is_pnl <= 0.30`) suggests the threshold definition in the story was ambiguous. Future stories that define numeric gates should state the gate in prose: "passes when OOS retains at least X% of IS P&L" — not just a formula, since the formula direction is easy to invert.

### 5. Stress test results are informational only — they do not gate `passes`
`StressTestReport` entries are recorded in `ValidationReport.stress_tests` but do not contribute to `ValidationReport.passes`. A strategy that catastrophically underperforms in a stress window still gets `passes=True`. This is a correctness gap (D-14-4-1). Future work: add a max-drawdown threshold for stress windows; exceed it → `passes=False`.

### 6. The `_partition_dataframe` fold_size=0 guard is a late addition
The initial `_partition_dataframe` implementation did not guard against `fold_size == 0` (DataFrame smaller than `n_splits + 1` rows). This would produce an empty folds list and silent success with zero validations. The ValueError guard was added during review. Lesson: validate at partition-time, not just at the call site.

---

## Deferred Work Inventory

| Item | Source | Deferral Reason |
|------|---------|----------------|
| Monte Carlo should use bootstrap resampling with replacement | 14.4 Finding | Spec-defined behavior; commutative sum is a known limitation |
| Stress test drawdown threshold gate | 14.4 Finding | Caller must supply threshold; spec did not define one |
| TCP connection reuse in `_sync_ilp_write` | 14.5 Finding | Performance: acceptable for small backtests |
| `realized_pnl = 0.0` — cost-basis tracking not implemented | 14.5 Finding | Requires buy/sell pairing state across fills |
| tz-naive / tz-aware mismatch in `get_funding_cost` silently returns 0.0 | 14.3 Finding | `except TypeError` masks root cause |
| Duplicate timestamps in funding_rates not validated | 14.3 Finding | `asof()` silently picks last duplicate |

---

## Architecture Patterns Established

1. **Signal layer**: pure functions `(df: pd.DataFrame, params: dict) -> SignalResult`; no side effects; testable with synthetic DataFrames
2. **Feed layer**: `QuestDBFeed(http_addr, exchange, symbol, timeframe, lookback_bars)` → `bt.feeds.PandasData`; gap rows filled with NaN; `InsufficientHistoryError` on too-short result
3. **Commission layer**: `bt.CommInfoBase` subclasses per exchange; `getsize()` → fee; `FundingCommission` wraps funding-rate `pd.Series` with `asof()` lookup
4. **Validation harness**: `run_walk_forward(strategy_cls, df, n_splits)` → `ValidationReport`; `run_monte_carlo(pnls, n_shuffles)` → float; `run_stress_test(strategy_cls, feed, windows)` → `list[StressTestReport]`
5. **Persistence**: `BacktestResultWriter` + `run_backtest_and_persist`; inner class wraps user strategy; ILP designated timestamp = backtest event time; `backtest=true` flag isolates from live rows
6. **Backtest CI gate**: `ValidationReport.passes` is the single boolean the CI pipeline should check before promoting a strategy to paper trading
