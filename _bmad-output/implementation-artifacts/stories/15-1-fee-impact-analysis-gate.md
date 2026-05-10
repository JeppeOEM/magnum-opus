# Story 15.1: Fee Impact Analysis Gate — Prerequisite for All Strategies

Status: done

## Story

As mrqdt,
I want fee impact analysis run and passing before any strategy proceeds to backtesting or paper deployment,
so that no compute is wasted validating strategies with insufficient edge to cover costs.

## Acceptance Criteria

- **AC1:** Given a strategy's signal function in `strategy/signals/`, when `run_fee_impact_check(strategy_name, signals, commission_info, expected_slippage_bps)` is called with a representative sample of historical signal edges, then the `FeeImpactReport` is written to `bot-service/_results/{strategy_name}/fee_impact.json`; if `passes=False`, the story for that strategy is blocked and cannot proceed to walk-forward validation or paper deployment.

- **AC2:** Given a strategy that fails the fee impact gate, when the failure is documented, then the strategy `.py` file is placed in `strategies/inactive/` (not `strategies/active/`); a comment at the top of the file records the fee impact result and the date evaluated.

- **AC3:** Given a `FeeImpactReport` that passes, when the next story (walk-forward validation or paper deployment) is started, then it reads `fee_impact.json` as a prerequisite artifact; if the file is absent or `passes=False`, the story acceptance criteria cannot be met.

- **AC4:** Given the unit test suite, when run, then: `fee_impact_gate` passes for a strategy with synthetic edge 0.05 (≫ 2x KuCoin taker fee 0.002); fails for synthetic edge 0.001 (< 2x fee); `run_fee_impact_check` writes valid JSON to the expected path; `FeeImpactReport` JSON includes `passes`, `required_edge`, `mean_signal_edge`, `margin` fields.

## Tasks / Subtasks

- [x] T1: Implement `run_fee_impact_check` helper in `bot_service/backtest/fee_impact.py` (AC1, AC4)
  - [x] T1.1: Add `run_fee_impact_check(strategy_name, signals, commission_info, slippage_bps, output_dir)` that calls `fee_impact_gate()` then writes JSON to `{output_dir}/{strategy_name}/fee_impact.json` (create dirs if needed)
  - [x] T1.2: JSON serialization: use `dataclasses.asdict()` on `FeeImpactReport`; write with `json.dumps(indent=2)` + UTF-8

- [x] T2: Unit tests in `tests/test_fee_impact_gate.py` (AC4)
  - [x] T2.1: `test_passes_for_large_edge` — `signals = pd.Series([0.05]*100)`, KuCoinCommissionInfo(is_maker=False), slippage=5 bps → `report.passes=True`, margin > 0
  - [x] T2.2: `test_fails_for_insufficient_edge` — `signals = pd.Series([0.001]*100)`, KuCoinCommissionInfo taker → `report.passes=False`
  - [x] T2.3: `test_report_written_to_correct_path` — call `run_fee_impact_check`, assert `_results/TestStrategy/fee_impact.json` exists, JSON is valid, all 4 fields present
  - [x] T2.4: `test_empty_signals_raises` — `pd.Series(dtype=float)` → `ValueError`
  - [x] T2.5: `test_negative_slippage_raises` — `expected_slippage_bps=-1` → `ValueError`

- [x] T3: Create `_results/.gitkeep` so the directory is tracked but results are gitignored (AC1)
  - [x] T3.1: Add `_results/` to `bot-service/.gitignore`
  - [x] T3.2: Create `bot-service/_results/.gitkeep`

- [x] T4: Run full test suite — no regressions (AC4)
  ```bash
  cd bot-service && python -m pytest tests/ -m "not l2" -v
  ```

### Review Findings

- [x] [Review][Defer] `commission_info` without `.p` attribute silently defaults `taker_rate=0.0`, making gate always pass [bot_service/backtest/fee_impact.py] — deferred, pre-existing in `fee_impact_gate` (Epic 14)
- [x] [Review][Defer] Bybit `maker_rate=-0.0001` causes `required_edge < 0` → gate vacuously passes any positive-mean strategy [bot_service/backtest/fee_impact.py] — deferred, pre-existing in `fee_impact_gate`
- [x] [Review][Defer] Mixed-NaN series with ≥1 non-NaN passes guard but produces single-sample mean with no variance context [bot_service/backtest/fee_impact.py] — deferred, pre-existing in `fee_impact_gate`

## Dev Notes

### What already exists (do NOT reimplement)

`bot_service/backtest/fee_impact.py` already contains:
- `FeeImpactReport` — frozen dataclass with `passes: bool`, `required_edge: float`, `mean_signal_edge: float`, `margin: float`
- `fee_impact_gate(strategy_signals, commission_info, expected_slippage_bps) -> FeeImpactReport` — fully working; raises `ValueError` for empty/all-NaN signals or negative slippage

Signal functions already exist in `bot_service/strategy/signals/`:
- `ofi_signal.ofi_signal(df, lookback, threshold) -> SignalResult`
- `ma_cross.ma_cross_signal(df, fast, slow) -> SignalResult`
- `funding_rate_arb.funding_rate_arb_signal()` — stub only (BLOCKED)

Tests for existing signals: `tests/test_signals.py` — do not touch.
Tests for commission/fee_impact: `tests/test_commission.py` — covers `KuCoinCommissionInfo`, `BybitCommissionInfo`, and basic `fee_impact_gate`; do NOT duplicate those tests.

### What to add

Only one new function `run_fee_impact_check` + its tests. Keep the change minimal.

```python
import dataclasses
import json
import pathlib

def run_fee_impact_check(
    strategy_name: str,
    signals: pd.Series,
    commission_info: object,
    expected_slippage_bps: float,
    output_dir: str | pathlib.Path = "_results",
) -> FeeImpactReport:
    """Run fee impact gate and persist result to JSON.

    Creates output_dir/strategy_name/fee_impact.json.
    Returns the FeeImpactReport (pass or fail).
    """
    report = fee_impact_gate(signals, commission_info, expected_slippage_bps)
    out = pathlib.Path(output_dir) / strategy_name / "fee_impact.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dataclasses.asdict(report), indent=2), encoding="utf-8")
    return report
```

### JSON output format

```json
{
  "passes": true,
  "required_edge": 0.00207,
  "mean_signal_edge": 0.05,
  "margin": 0.04793
}
```

### Strategies directory convention (AC2 — manual process)

When a strategy fails:
1. The `.py` file goes in `bot-service/strategies/inactive/` (not `strategies/active/`)
2. Add at top of file:
```python
# FEE IMPACT GATE: FAILED 2026-05-10
# required_edge=0.00207, mean_signal_edge=0.001, margin=-0.00107
# Strategy cannot proceed to paper deployment until edge is improved.
```

The `strategies/active/` and `strategies/inactive/` directories already exist (empty). No code needed to automate placement.

### Testing conventions

- All new tests must use `@pytest.mark.l1` decorator
- Test file: `bot-service/tests/test_fee_impact_gate.py`
- Use `tmp_path` pytest fixture for file-writing tests (not hardcoded paths)
- Do NOT import `conftest.py` fixtures that spin up Docker — L1 tests are pure

### File locations

- **UPDATE** `bot_service/backtest/fee_impact.py` — add `run_fee_impact_check`
- **CREATE** `tests/test_fee_impact_gate.py`
- **CREATE** `bot-service/_results/.gitkeep`
- **UPDATE** `bot-service/.gitignore` — add `_results/` entry (keep `.gitkeep`)

### CommissionInfo usage in tests

Use `KuCoinCommissionInfo` from `bot_service.backtest.commission` — it already exists and has `p.taker_rate = 0.001`. `fee_impact_gate` reads `getattr(commission_info, "p", None)` then `getattr(params, "taker_rate", 0.0)`.

### References

- Epic 15 story 15.1 spec: `_bmad-output/planning-artifacts/epics-bot.md:853-875`
- `fee_impact_gate` implementation: `bot_service/backtest/fee_impact.py`
- `FeeImpactReport` dataclass: `bot_service/backtest/fee_impact.py`
- Commission classes: `bot_service/backtest/commission.py`
- Existing fee impact tests: `tests/test_commission.py`

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- Added `run_fee_impact_check()` to `bot_service/backtest/fee_impact.py`: calls `fee_impact_gate()` + serializes `FeeImpactReport` to `{output_dir}/{strategy_name}/fee_impact.json` via `dataclasses.asdict()` + `json.dumps(indent=2)`
- Created `tests/test_fee_impact_gate.py` with 5 L1 tests covering pass, fail, file write, empty signals, negative slippage — all pass
- Created `bot-service/.gitignore` and `bot-service/_results/.gitkeep`
- Full suite: 196 passed, 0 regressions

### File List

- bot-service/bot_service/backtest/fee_impact.py (updated — added `run_fee_impact_check`)
- bot-service/tests/test_fee_impact_gate.py (created)
- bot-service/.gitignore (created)
- bot-service/_results/.gitkeep (created)
