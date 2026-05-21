---
id: 36-1
title: Data quality monitoring script
epic: 36
status: ready-for-dev
---

# Story 36-1: Data Quality Monitoring Script

## Context

After deploying Epic 35, the system collects ~30 days of enriched `snapshot_1s` data before ML training can begin. This script gives visibility into collection progress — row counts per day, null rates for ML-critical columns, and date gaps.

## What to build

### `scripts/check_data_quality.py`

```python
#!/usr/bin/env python3
"""Inspect snapshot_1s data quality for ML readiness."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

import httpx

ML_COLUMNS = [
    "hawkes_intensity", "microprice_mid_delta", "cancel_bias",
    "trade_aggressiveness", "buy_vwap_deviation_bps", "sell_vwap_deviation_bps",
    "bid_depth_l3_close", "realized_vol", "ofi", "ofi_l1", "spread_mean",
]

def fetch(url: str, sql: str) -> list[dict]:
    resp = httpx.get(url + "/exec", params={"query": sql}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    cols = [c["name"] for c in data["columns"]]
    return [dict(zip(cols, row)) for row in data["dataset"]]

def run(questdb_url: str, exchange: str, symbol: str) -> None:
    print(f"\n=== Data Quality: {exchange}:{symbol} ===\n")

    # Total row count
    rows = fetch(questdb_url, f"SELECT count() FROM snapshot_1s WHERE exchange='{exchange}' AND symbol='{symbol}'")
    total = rows[0]["count"] if rows else 0
    print(f"Total rows: {total:,}")

    # Rows per day (last 35 days)
    rows = fetch(questdb_url, f"""
        SELECT cast(ts AS DATE) AS day, count() AS cnt
        FROM snapshot_1s
        WHERE exchange='{exchange}' AND symbol='{symbol}'
        AND ts > dateadd('d', -35, now())
        SAMPLE BY 1d
        ORDER BY day
    """)
    print(f"\nRows per day (last 35 days):")
    for r in rows:
        bar = "█" * min(40, r["cnt"] // 2000)
        flag = " ⚠" if r["cnt"] < 80000 else ""
        print(f"  {r['day']}  {r['cnt']:>7,}  {bar}{flag}")

    # Null rates for ML columns
    print(f"\nNull rates for ML-critical columns:")
    for col in ML_COLUMNS:
        try:
            rows = fetch(questdb_url, f"""
                SELECT count() AS total,
                       sum(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) AS nulls
                FROM snapshot_1s
                WHERE exchange='{exchange}' AND symbol='{symbol}'
                AND ts > dateadd('d', -7, now())
            """)
            if rows and rows[0]["total"] > 0:
                null_pct = rows[0]["nulls"] / rows[0]["total"] * 100
                flag = " ⚠" if null_pct > 5 else ""
                print(f"  {col:<35} {null_pct:5.1f}%{flag}")
            else:
                print(f"  {col:<35}  N/A (no data)")
        except Exception as e:
            print(f"  {col:<35}  ERROR: {e}")

    # Date gaps > 1 hour
    print(f"\nDate gaps > 1 hour (last 7 days):")
    rows = fetch(questdb_url, f"""
        SELECT ts, next_ts,
               (next_ts - ts) / 1000000 AS gap_seconds
        FROM (
            SELECT ts, LEAD(ts) OVER (ORDER BY ts) AS next_ts
            FROM snapshot_1s
            WHERE exchange='{exchange}' AND symbol='{symbol}'
            AND ts > dateadd('d', -7, now())
        )
        WHERE (next_ts - ts) / 1000000 > 3600
        ORDER BY ts
    """)
    if not rows:
        print("  No gaps > 1 hour ✓")
    for r in rows:
        gap_h = r["gap_seconds"] / 3600
        print(f"  {r['ts']}  →  {r['next_ts']}  ({gap_h:.1f}h)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--exchange", default="bybit")
    parser.add_argument("--questdb", default="http://localhost:9000")
    args = parser.parse_args()
    try:
        run(args.questdb, args.exchange, args.symbol)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
```

## Acceptance Criteria

1. `python3 scripts/check_data_quality.py --symbol BTCUSDT --exchange bybit` runs without error against a live QuestDB.
2. Output includes: total row count, per-day row counts for last 35 days, null rates for all 11 ML columns, date gaps > 1 hour.
3. Days with < 80,000 rows flagged with `⚠`.
4. Columns with > 5% null rate flagged with `⚠`.
5. When QuestDB is unreachable, prints error to stderr and exits with code 1.
6. `--questdb` flag overrides the default URL.

## Dev Notes

- Uses `httpx` (sync) for simplicity — this is a CLI script, not a service.
- `httpx` is already in bot-service deps; add to `scripts/requirements.txt` if not present.
- QuestDB HTTP API returns results in `{columns, dataset}` JSON format.
- The gap query uses `LEAD()` window function — available in QuestDB 8.2.1.
