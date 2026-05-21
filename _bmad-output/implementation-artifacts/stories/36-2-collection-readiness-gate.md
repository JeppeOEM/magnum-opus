---
id: 36-2
title: Collection readiness gate
epic: 36
status: ready-for-dev
---

# Story 36-2: Collection Readiness Gate

## Context

Before ML training starts (Epic 37), data must meet minimum quality thresholds: 28 days coverage, sufficient row density, low null rate. This script is the formal gate — exits 0 when ready, exits 1 with a clear message when not. Can be called from Makefile before `make train`.

## What to build

### `scripts/data_readiness_gate.py`

```python
#!/usr/bin/env python3
"""Gate: exits 0 when snapshot_1s data is ready for ML training, 1 otherwise."""
from __future__ import annotations

import argparse
import sys

import httpx

MIN_DAYS          = 28
MIN_ROWS_PER_DAY  = 80_000
MAX_NULL_PCT      = 5.0
MAX_GAP_HOURS     = 6.0

ML_COLUMNS = [
    "hawkes_intensity", "microprice_mid_delta", "cancel_bias",
    "trade_aggressiveness", "buy_vwap_deviation_bps", "sell_vwap_deviation_bps",
    "bid_depth_l3_close", "realized_vol", "ofi", "ofi_l1",
]

def fetch(url: str, sql: str) -> list[dict]:
    resp = httpx.get(url + "/exec", params={"query": sql}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    cols = [c["name"] for c in data["columns"]]
    return [dict(zip(cols, row)) for row in data["dataset"]]

def check(questdb_url: str, exchange: str, symbol: str) -> list[str]:
    failures: list[str] = []

    # Check 1: coverage days with sufficient rows
    rows = fetch(questdb_url, f"""
        SELECT cast(ts AS DATE) AS day, count() AS cnt
        FROM snapshot_1s
        WHERE exchange='{exchange}' AND symbol='{symbol}'
        AND ts > dateadd('d', -35, now())
        SAMPLE BY 1d
        ORDER BY day
    """)
    good_days = [r for r in rows if r["cnt"] >= MIN_ROWS_PER_DAY]
    if len(good_days) < MIN_DAYS:
        failures.append(
            f"Insufficient coverage: {len(good_days)} days ≥ {MIN_ROWS_PER_DAY:,} rows "
            f"(need {MIN_DAYS})"
        )

    # Check 2: null rates
    for col in ML_COLUMNS:
        rows_null = fetch(questdb_url, f"""
            SELECT count() AS total,
                   sum(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) AS nulls
            FROM snapshot_1s
            WHERE exchange='{exchange}' AND symbol='{symbol}'
            AND ts > dateadd('d', -7, now())
        """)
        if rows_null and rows_null[0]["total"] > 0:
            null_pct = rows_null[0]["nulls"] / rows_null[0]["total"] * 100
            if null_pct > MAX_NULL_PCT:
                failures.append(f"Column {col}: {null_pct:.1f}% null (max {MAX_NULL_PCT}%)")

    # Check 3: no gap > MAX_GAP_HOURS
    rows_gap = fetch(questdb_url, f"""
        SELECT count() AS gap_count
        FROM (
            SELECT ts, LEAD(ts) OVER (ORDER BY ts) AS next_ts
            FROM snapshot_1s
            WHERE exchange='{exchange}' AND symbol='{symbol}'
            AND ts > dateadd('d', -{MIN_DAYS + 1}, now())
        )
        WHERE (next_ts - ts) / 1000000 > {int(MAX_GAP_HOURS * 3600)}
    """)
    if rows_gap and rows_gap[0]["gap_count"] > 0:
        failures.append(
            f"{rows_gap[0]['gap_count']} gap(s) > {MAX_GAP_HOURS}h found in last {MIN_DAYS+1} days"
        )

    return failures

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--exchange", default="bybit")
    parser.add_argument("--questdb", default="http://localhost:9000")
    args = parser.parse_args()

    try:
        failures = check(args.questdb, args.exchange, args.symbol)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        sys.exit(1)

    print(f"READY: {args.exchange}:{args.symbol} — data quality gate passed")
    sys.exit(0)
```

### `Makefile` — add target at repo root

```makefile
check-data-ready:
	python3 scripts/data_readiness_gate.py --symbol $(SYMBOL) --exchange $(EXCHANGE)
```

Usage: `make check-data-ready SYMBOL=BTCUSDT EXCHANGE=bybit`

### `docs/ml.md` — new file

```markdown
# ML Service

## Data Collection Requirement

The ML training pipeline requires ≥28 days of quality `snapshot_1s` data from the candle service.

**Wait time**: ~30 days after deploying Epic 35.

**Before training**, run the readiness gate:
\```bash
make check-data-ready SYMBOL=BTCUSDT EXCHANGE=bybit
\```

Exits 0 when all conditions are met:
- ≥28 days with ≥80,000 rows each
- <5% null rate for all ML-critical columns
- No gap > 6 hours in the last 29 days

**Monitoring collection** (run anytime):
\```bash
python3 scripts/check_data_quality.py --symbol BTCUSDT --exchange bybit
\```
```

## Acceptance Criteria

1. `python3 scripts/data_readiness_gate.py --symbol BTCUSDT --exchange bybit` exits 0 when all conditions pass.
2. Exits 1 with `FAIL: <reason>` printed for each failing condition (multiple failures shown).
3. Condition 1: ≥28 calendar days with ≥80,000 rows each in last 35 days.
4. Condition 2: <5% null rate per ML column over last 7 days.
5. Condition 3: no single gap > 6 hours in last 29 days.
6. `make check-data-ready SYMBOL=BTCUSDT` invokes the gate.
7. `docs/ml.md` documents the 30-day collection requirement and gate usage.

## Dev Notes

- This is a blocking gate before Epic 37 work begins — it will fail for ~30 days after Epic 35 deployment.
- The QuestDB LEAD() window function is available from QuestDB 8.x — confirmed in project (using 8.2.1).
