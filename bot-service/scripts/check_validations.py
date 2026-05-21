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
        try:
            data = json.loads(report_path.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            print(f"  [FAIL] {strategy}  validation_report  (corrupt: {exc})", file=sys.stderr)
            failures.append(f"{strategy}: validation_report (corrupt)")
            checked += 1
            continue
        passes = data.get("passes", False)
        checked += 1
        if passes:
            print(f"  [OK]   {strategy}  validation_report")
        else:
            print(f"  [FAIL] {strategy}  validation_report", file=sys.stderr)
            failures.append(f"{strategy}: validation_report")

    for report_path in sorted(results_dir.glob("*/fee_impact.json")):
        strategy = report_path.parent.name
        try:
            data = json.loads(report_path.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            print(f"  [FAIL] {strategy}  fee_impact  (corrupt: {exc})", file=sys.stderr)
            failures.append(f"{strategy}: fee_impact (corrupt)")
            checked += 1
            continue
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
