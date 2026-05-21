---
id: 32-2
title: Pre-deploy backtest validation gate script
epic: 32
status: done
---

# Story 32-2: Pre-deploy backtest validation gate script

## Context

`docs/ops.md` (lines 189–191) documents a manual pre-deploy check that requires operators to enumerate strategy names and run Python one-liners. If a strategy name is misspelled or a new strategy is added without updating the list, the check silently passes for unchecked strategies. This story replaces the manual check with a script that auto-discovers all `_results/*/validation_report.json` and `_results/*/fee_impact.json` files, reports all failures at once, and exits non-zero if any fail.

## What to build

### `bot-service/scripts/check_validations.py`

```python
#!/usr/bin/env python3
"""Pre-deploy validation gate. Run from bot-service/ directory.

Scans _results/*/validation_report.json and _results/*/fee_impact.json.
Prints a summary and exits 1 if any strategy fails.
"""
import json
import sys
from pathlib import Path

def check(results_dir: Path) -> int:
    failures = []
    checked = 0
    for report_path in sorted(results_dir.glob("*/validation_report.json")):
        strategy = report_path.parent.name
        data = json.loads(report_path.read_text())
        passes = data.get("passes", False)
        checked += 1
        if passes:
            print(f"  [OK]   {strategy}  validation_report")
        else:
            print(f"  [FAIL] {strategy}  validation_report", file=sys.stderr)
            failures.append(f"{strategy}: validation_report")

    for report_path in sorted(results_dir.glob("*/fee_impact.json")):
        strategy = report_path.parent.name
        data = json.loads(report_path.read_text())
        passes = data.get("passes", False)
        checked += 1
        if passes:
            print(f"  [OK]   {strategy}  fee_impact")
        else:
            print(f"  [FAIL] {strategy}  fee_impact", file=sys.stderr)
            failures.append(f"{strategy}: fee_impact")

    if checked == 0:
        print("WARNING: no validation reports found in _results/ — run backtests first", file=sys.stderr)
        return 1

    if failures:
        print(f"\n{len(failures)} gate(s) FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print(f"\n{checked} gate(s) passed.")
    return 0

if __name__ == "__main__":
    results_dir = Path("_results")
    if not results_dir.is_dir():
        print(f"ERROR: {results_dir.absolute()} not found. Run from bot-service/ directory.", file=sys.stderr)
        sys.exit(1)
    sys.exit(check(results_dir))
```

### `bot-service/Makefile` — add `validate-check` target

```makefile
validate-check:
	python3 scripts/check_validations.py
```

### `docs/ops.md` — replace manual one-liner with script reference

Replace the loop at lines 189–191 with:
```
   cd bot-service && make validate-check
```

## Acceptance Criteria

1. `python3 scripts/check_validations.py` from `bot-service/` exits 0 when all `_results/*/validation_report.json` have `passes: true`.
2. Exits 1 when any report has `passes: false`.
3. Exits 1 with a clear warning when `_results/` contains no reports (prevents silent green on empty directory).
4. Reports ALL failures in a single run (not just the first).
5. `make validate-check` target exists in the Makefile.
6. `docs/ops.md` references `make validate-check` instead of the manual Python one-liner.

## Dev Notes

- Script auto-discovers reports — no hardcoded strategy names.
- Run from `bot-service/` directory (same as all other `make` targets).
- The `_results/` directory is created by the backtest CLI / REST API.

## Review Findings

- [x] [Review][Patch] No JSON exception handling — `json.loads()` raises `JSONDecodeError` on corrupt files, exits with Python traceback instead of structured failure [`bot-service/scripts/check_validations.py:17,28`]
- [x] [Review][Patch] ops.md `cd bot-service &&` contradicts preamble — preamble says "run from bot-service/"; `cd bot-service` would enter a nested dir [`docs/ops.md:189`]
- [x] [Review][Defer] Partial report: strategy with only one file type silently passes — design choice; spec says auto-discover, not require both types per strategy [`check_validations.py`]
- [x] [Review][Defer] `checked == 0` guard doesn't detect dirs with no JSON files — minor edge case not in spec [`check_validations.py:37`]
- [x] [Review][Defer] Stale result files missing new `stress_drawdown_ok` field — pre-existing, not caused by this change [`_results/`]
- [x] [Review][Defer] Script not executable — shebang present but `chmod +x` not set; Makefile invokes via `python3` so low impact [`scripts/check_validations.py`]

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### File List

- `bot-service/scripts/check_validations.py`
- `bot-service/Makefile`
- `docs/ops.md`
