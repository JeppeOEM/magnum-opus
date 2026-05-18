---
id: 29-2
title: Stress test max-drawdown threshold gates ValidationReport.passes
epic: 29
status: ready-for-dev
---

# Story 29-2: Stress test max-drawdown threshold gates ValidationReport.passes

## Context

Fixes D-14-4-1. `StressTestReport` is recorded in `ValidationReport` (stress_test_ran=True) but per-window Sharpe/drawdown values do not contribute to `ValidationReport.passes`. The stress test is currently informational only. The fix adds a configurable max-drawdown threshold for stress windows; if any window's max_drawdown exceeds the threshold, `passes=False`.

## What to build

### `bot_service/backtest/validation.py`

Add `stress_drawdown_ok: bool` to `ValidationReport`:

```python
@dataclass
class ValidationReport:
    passes: bool
    ...
    stress_drawdown_ok: bool  # new field
```

In `generate_validation_report`, add `stress_max_drawdown_threshold` parameter (default `0.30`):

```python
def generate_validation_report(
    walk_forward: WalkForwardReport | None,
    stress: StressTestReport | None,
    monte_carlo_pct5: float | None,
    fee_gate: FeeImpactReport | None = None,
    min_sharpe: float = 1.0,
    max_drawdown_threshold: float = 0.15,
    max_degradation: float = 0.30,
    stress_max_drawdown_threshold: float = 0.30,  # new
) -> ValidationReport:
```

Compute `stress_drawdown_ok`:

```python
if stress is not None:
    worst_stress_dd = max((w.max_drawdown for w in stress.windows), default=0.0)
    stress_drawdown_ok = worst_stress_dd <= stress_max_drawdown_threshold
else:
    stress_drawdown_ok = True  # not ran → not blocking
```

Include in `passes`:
```python
passes=all([sharpe_ok, drawdown_ok, degradation_ok, monte_carlo_ok, stress_drawdown_ok]),
```

Also add `worst_stress_drawdown: float` to `ValidationReport` for reporting purposes.

## Acceptance Criteria

- A strategy with worst stress window drawdown=0.50 and threshold=0.30 → `stress_drawdown_ok=False`, `passes=False`.
- A strategy with worst stress window drawdown=0.10 and threshold=0.30 → `stress_drawdown_ok=True`.
- When `stress=None` (not run), `stress_drawdown_ok=True` (no penalty for not running).
- `ValidationReport.to_json()` includes `stress_drawdown_ok` and `worst_stress_drawdown`.
- Unit tests: (1) stress window exceeds threshold → passes=False, (2) stress window under threshold → does not affect passes, (3) no stress run → passes not affected by stress field.
- Existing `test_validation.py` tests still pass (new fields default to True when stress=None).

## Files
- `bot-service/bot_service/backtest/validation.py`
- `bot-service/tests/test_validation.py` (extend)
