#!/usr/bin/env python3
"""validate-contract.py — pipeline ↔ trading repo contract validator.
Usage: python3 scripts/validate-contract.py [--redis-url URL] [--questdb-url URL] [--symbols exchange:SYM,...]
Exit 0 = pass. Exit 1 = fail. Run before deploying either repo."""
import argparse, sys, time
from typing import NamedTuple
try:
    import redis as redis_lib
except ImportError:
    sys.exit("ERROR: pip install redis")
try:
    import requests
except ImportError:
    sys.exit("ERROR: pip install requests")

DEFAULT_SYMBOLS = [("kucoin", "BTC-USDT")]
# candles:close includes timeframe suffix — check 1m as representative closed-bar stream.
# candles:ob is currently unused (OB features written to QuestDB snapshot_1s instead).
STREAM_KEY_PATTERNS = [
    "ticks:{exchange}:{symbol}",
    "candles:close:{exchange}:{symbol}:1m",
]
EXPECTED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "snapshot_1s": [
        ("ts", "TIMESTAMP"), ("exchange", "SYMBOL"), ("symbol", "SYMBOL"),
        ("open", "DOUBLE"), ("high", "DOUBLE"), ("low", "DOUBLE"), ("close", "DOUBLE"),
        ("volume", "DOUBLE"), ("trade_count", "INT"), ("buy_volume", "DOUBLE"),
        ("sell_volume", "DOUBLE"), ("gap_count", "INT"), ("is_partial", "BOOLEAN"),
    ],
    "snapshot_1m":  [("ts","TIMESTAMP"),("exchange","SYMBOL"),("symbol","SYMBOL"),
                     ("open","DOUBLE"),("high","DOUBLE"),("low","DOUBLE"),("close","DOUBLE"),("volume","DOUBLE")],
    "snapshot_15m": [("ts","TIMESTAMP"),("exchange","SYMBOL"),("symbol","SYMBOL"),
                     ("open","DOUBLE"),("high","DOUBLE"),("low","DOUBLE"),("close","DOUBLE"),("volume","DOUBLE")],
}
FRESHNESS_S = 60

class Result(NamedTuple):
    passed: bool; warn: bool; message: str

results: list[Result] = []
def ok(m):   results.append(Result(True,  False, m)); print(f"  ✓ {m}")
def warn(m): results.append(Result(True,  True,  m)); print(f"  ⚠ {m}")
def fail(m): results.append(Result(False, False, m)); print(f"  ✗ {m}")

def check_streams(redis_url: str, symbols: list[tuple[str, str]]) -> None:
    print("\nRedis stream checks:")
    try:
        r = redis_lib.from_url(redis_url, socket_connect_timeout=5, decode_responses=True)
        r.ping()
    except Exception as e:
        fail(f"Redis connection failed: {e}"); return
    now = int(time.time())
    for exchange, symbol in symbols:
        for pat in STREAM_KEY_PATTERNS:
            key = pat.format(exchange=exchange, symbol=symbol)
            try:
                length = r.xlen(key)
            except Exception as e:
                fail(f"XLEN {key}: {e}"); continue
            if length == 0:
                fail(f"MISSING or EMPTY: {key}"); continue
            try:
                entries = r.xrevrange(key, count=1)
                if not entries:
                    fail(f"{key} — XREVRANGE returned nothing despite XLEN={length} (corrupted stream)"); continue
                ts_ms = int(entries[0][0].split("-")[0])
                age_s = now - ts_ms // 1000
                if age_s > FRESHNESS_S:
                    warn(f"{key} — stale ({age_s}s ago)")
                else:
                    ok(f"{key} — active ({age_s}s ago, {length} entries)")
            except (ValueError, IndexError) as e:
                warn(f"{key} — ID parse error: {e}")
            except Exception as e:
                warn(f"{key} — freshness check failed: {e}")

def get_columns(url: str, table: str) -> dict[str, str] | None | str:
    """Returns column dict, None if table missing, or error string on HTTP/network error."""
    try:
        r = requests.get(f"{url}/exec",
            params={"query": f"table_columns('{table}')"}, timeout=5)
        r.raise_for_status()
        data = r.json()
        if "dataset" not in data:
            return None  # table does not exist
        return {row[0]: row[1] for row in data["dataset"]}
    except requests.HTTPError as e:
        return f"HTTP {e.response.status_code}"
    except Exception as e:
        return str(e)

def check_tables(questdb_url: str) -> None:
    print("\nQuestDB table checks:")
    try:
        requests.get(f"{questdb_url}/exec", params={"query": "SELECT 1"}, timeout=5).raise_for_status()
    except Exception as e:
        fail(f"QuestDB connection failed: {e}"); return
    for table, expected in EXPECTED_COLUMNS.items():
        actual = get_columns(questdb_url, table)
        if isinstance(actual, str):
            fail(f"{table} — query error ({actual})"); continue
        if actual is None:
            fail(f"MISSING TABLE: {table}"); continue
        table_ok = True
        for col, typ in expected:
            if col not in actual:
                fail(f"MISSING COLUMN: {table}.{col} ({typ})"); table_ok = False
            elif actual[col] != typ:
                fail(f"TYPE MISMATCH: {table}.{col} expected {typ} got {actual[col]}"); table_ok = False
        if table_ok:
            ok(f"{table} — {len(expected)}/{len(expected)} required columns present")

def parse_symbols(raw: str) -> list[tuple[str, str]]:
    out = []
    for part in raw.split(","):
        part = part.strip()
        if ":" not in part:
            print(f"WARNING: skipping '{part}' (expected exchange:symbol)"); continue
        e, s = part.split(":", 1)
        out.append((e.strip(), s.strip()))
    return out

def main() -> None:
    import os
    p = argparse.ArgumentParser(description="Validate pipeline ↔ trading contract")
    p.add_argument("--redis-url",    default=os.environ.get("REDIS_URL",    "redis://localhost:6379"))
    p.add_argument("--questdb-url",  default=os.environ.get("QUESTDB_URL",  "http://localhost:9000"))
    p.add_argument("--symbols",      default=None)
    args = p.parse_args()
    symbols = parse_symbols(args.symbols) if args.symbols else DEFAULT_SYMBOLS
    print(f"Contract validator — pipeline ↔ trading")
    print(f"  Redis:   {args.redis_url}")
    print(f"  QuestDB: {args.questdb_url}")
    print(f"  Symbols: {', '.join(f'{e}:{s}' for e, s in symbols)}")
    check_streams(args.redis_url, symbols)
    check_tables(args.questdb_url)
    failures = [r for r in results if not r.passed]
    warnings = [r for r in results if r.passed and r.warn]
    print()
    if failures:
        print(f"CONTRACT INVALID — {len(failures)} check(s) failed, {len(warnings)} warning(s)")
        sys.exit(1)
    print(f"CONTRACT VALID — all checks passed" + (f" ({len(warnings)} warning(s))" if warnings else ""))

if __name__ == "__main__":
    main()
