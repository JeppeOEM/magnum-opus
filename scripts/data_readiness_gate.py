#!/usr/bin/env python3
"""Gate: exits 0 when snapshot_1s data is ready for ML training, 1 otherwise.

Usage:
    python3 scripts/data_readiness_gate.py --symbol BTCUSDT --exchange bybit
    make check-data-ready SYMBOL=BTCUSDT EXCHANGE=bybit
"""
from __future__ import annotations

import argparse
import sys

import httpx

MIN_DAYS = 28
MIN_ROWS_PER_DAY = 80_000
MAX_NULL_PCT = 5.0
MAX_GAP_HOURS = 6.0

ML_COLUMNS = [
    "hawkes_intensity",
    "microprice_mid_delta",
    "cancel_bias",
    "trade_aggressiveness",
    "buy_vwap_deviation_bps",
    "sell_vwap_deviation_bps",
    "bid_depth_l3_close",
    "realized_vol",
    "ofi",
    "ofi_l1",
]


def fetch(url: str, sql: str) -> list[dict]:
    resp = httpx.get(url + "/exec", params={"query": sql}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    cols = [c["name"] for c in data["columns"]]
    return [dict(zip(cols, row)) for row in data["dataset"]]


def check(questdb_url: str, exchange: str, symbol: str) -> list[str]:
    failures: list[str] = []

    # Check 1: coverage — ≥MIN_DAYS days with ≥MIN_ROWS_PER_DAY rows
    rows = fetch(
        questdb_url,
        f"""
        SELECT cast(ts AS DATE) AS day, count() AS cnt
        FROM snapshot_1s
        WHERE exchange='{exchange}' AND symbol='{symbol}'
        AND ts > dateadd('d', -35, now())
        SAMPLE BY 1d
        ORDER BY day
        """,
    )
    good_days = [r for r in rows if r["cnt"] >= MIN_ROWS_PER_DAY]
    if len(good_days) < MIN_DAYS:
        failures.append(
            f"Insufficient coverage: {len(good_days)} days ≥ {MIN_ROWS_PER_DAY:,} rows "
            f"(need {MIN_DAYS})"
        )

    # Check 2: null rates per ML column (last 7 days)
    for col in ML_COLUMNS:
        rows_null = fetch(
            questdb_url,
            f"""
            SELECT count() AS total,
                   sum(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) AS nulls
            FROM snapshot_1s
            WHERE exchange='{exchange}' AND symbol='{symbol}'
            AND ts > dateadd('d', -7, now())
            """,
        )
        if rows_null and rows_null[0]["total"] > 0:
            null_pct = rows_null[0]["nulls"] / rows_null[0]["total"] * 100
            if null_pct > MAX_NULL_PCT:
                failures.append(
                    f"Column {col}: {null_pct:.1f}% null (max {MAX_NULL_PCT}%)"
                )

    # Check 3: no gap > MAX_GAP_HOURS in last MIN_DAYS+1 days
    rows_gap = fetch(
        questdb_url,
        f"""
        SELECT count() AS gap_count
        FROM (
            SELECT ts, LEAD(ts) OVER (ORDER BY ts) AS next_ts
            FROM snapshot_1s
            WHERE exchange='{exchange}' AND symbol='{symbol}'
            AND ts > dateadd('d', -{MIN_DAYS + 1}, now())
        )
        WHERE (next_ts - ts) / 1000000 > {int(MAX_GAP_HOURS * 3600)}
        """,
    )
    if rows_gap and rows_gap[0]["gap_count"] > 0:
        failures.append(
            f"{rows_gap[0]['gap_count']} gap(s) > {MAX_GAP_HOURS}h found "
            f"in last {MIN_DAYS + 1} days"
        )

    return failures


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Gate: exits 0 when data is ready for ML training, 1 otherwise."
    )
    parser.add_argument("--symbol", required=True, help="Symbol, e.g. BTCUSDT")
    parser.add_argument("--exchange", default="bybit", help="Exchange name (default: bybit)")
    parser.add_argument(
        "--questdb",
        default="http://localhost:9000",
        help="QuestDB HTTP base URL (default: http://localhost:9000)",
    )
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
