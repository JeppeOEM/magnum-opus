# Story R.4: Contract Validation Script

Status: done

## Story

As a developer deploying either the pipeline or consumer repos,
I want a lightweight script that validates the Redis stream keys and QuestDB tables
match what both repos expect,
so that a silent breaking change (renamed key, dropped column) is caught before it causes a data gap or broken bot.

## Context

The interface between `magnum-opus-pipeline` (Repo 1) and `magnum-opus-trading` (Repo 2) is:

**Redis stream keys** (pipeline writes, consumers read):
- `ticks:{exchange}:{symbol}` — raw tick stream from aggregator
- `candles:close:{exchange}:{symbol}` — closed OHLCV bars per timeframe
- `candles:ob:{exchange}:{symbol}` — order book feature bars

**QuestDB tables** (pipeline writes, consumers query):
- `snapshot_1s` — 1-second OHLCV bars with 67 microstructure features
- `snapshot_1m` — 1-minute bars
- `snapshot_15m` — 15-minute bars

If the pipeline renames a Redis key or drops a QuestDB column, consumers break silently.
This script catches that before deploy.

## Acceptance Criteria

### AC 1 — Script location and language

1. Script lives at `scripts/validate-contract.py` in **both** repos (identical file).
2. Python 3.9+ with only stdlib + `redis` + `requests` dependencies (already available in both environments).
3. Executable: `python3 scripts/validate-contract.py --redis-url redis://localhost:6379 --questdb-url http://localhost:9000`
4. Exit code 0 = all checks pass. Exit code 1 = one or more checks failed. Human-readable output.

### AC 2 — Redis stream checks

5. For each expected stream key, the script checks:
   - Stream exists (`XLEN > 0` OR `XINFO STREAM` returns without error)
   - Stream has received a message in the last 60 seconds (checks the last entry's timestamp ID)
6. Expected keys are defined as a list in the script (configurable via a `--symbols` flag):
   ```python
   EXPECTED_STREAMS = [
       "ticks:kucoin:BTC-USDT",
       "ticks:bybit:BTCUSDT",
       "candles:close:kucoin:BTC-USDT",
       "candles:ob:kucoin:BTC-USDT",
       # ... etc
   ]
   ```
7. The `--symbols` flag takes comma-separated `exchange:symbol` pairs and overrides the defaults.
8. Missing streams → FAIL with message: `"MISSING: ticks:kucoin:BTC-USDT not found in Redis"`
9. Stale streams (no message in 60s) → WARN (not FAIL) — the pipeline may have just started.

### AC 3 — QuestDB table checks

10. For each expected table, the script queries `SELECT column_name, column_type FROM information_schema.columns WHERE table_name = '{table}'` via QuestDB HTTP `/exec`.
11. Expected columns are defined per table. At minimum check these exist:
    ```python
    EXPECTED_COLUMNS = {
        "snapshot_1s": [
            ("ts", "TIMESTAMP"),
            ("exchange", "SYMBOL"),
            ("symbol", "SYMBOL"),
            ("open", "DOUBLE"),
            ("high", "DOUBLE"),
            ("low", "DOUBLE"),
            ("close", "DOUBLE"),
            ("volume", "DOUBLE"),
            ("trade_count", "LONG"),
        ],
        "snapshot_1m": [("ts", "TIMESTAMP"), ("exchange", "SYMBOL"), ("symbol", "SYMBOL"), ("open", "DOUBLE")],
        "snapshot_15m": [("ts", "TIMESTAMP"), ("exchange", "SYMBOL"), ("symbol", "SYMBOL"), ("open", "DOUBLE")],
    }
    ```
12. Missing table → FAIL.
13. Missing column → FAIL with message: `"MISSING COLUMN: snapshot_1s.trade_count (LONG) not found"`.
14. Wrong column type → FAIL with message: `"TYPE MISMATCH: snapshot_1s.volume expected DOUBLE got FLOAT"`.
15. Extra columns → OK (additive changes are backwards compatible).

### AC 4 — Output format

16. On success:
    ```
    ✓ Redis: ticks:kucoin:BTC-USDT (active, last message 2s ago)
    ✓ Redis: candles:close:kucoin:BTC-USDT (active, last message 1s ago)
    ✓ QuestDB: snapshot_1s (9/9 required columns present)
    ✓ QuestDB: snapshot_1m (4/4 required columns present)
    ✓ QuestDB: snapshot_15m (4/4 required columns present)
    
    CONTRACT VALID — all checks passed
    ```
17. On failure:
    ```
    ✓ Redis: ticks:kucoin:BTC-USDT (active, last message 2s ago)
    ✗ Redis: candles:close:kucoin:BTC-USDT MISSING
    ✓ QuestDB: snapshot_1s (9/9 required columns present)
    ✗ QuestDB: snapshot_1s.realized_vol TYPE MISMATCH: expected DOUBLE got FLOAT
    
    CONTRACT INVALID — 2 check(s) failed
    ```

### AC 5 — CI integration (both repos)

18. In `magnum-opus-pipeline` CI (`.github/workflows/ci.yml`), add a job:
    ```yaml
    contract-check:
      runs-on: ubuntu-latest
      # Only runs against a real pipeline — skip in CI where Redis/QuestDB not available
      if: false  # placeholder; enable when self-hosted runner or Docker service is available
    ```
19. In `magnum-opus-trading` CI, same placeholder.
20. The script is runnable locally as a pre-deploy check: `python3 scripts/validate-contract.py` before `make deploy` in Repo 1, or before `make up` in Repo 2.
21. Document in both repos' READMEs: "Run `python3 scripts/validate-contract.py` to verify the pipeline contract before deploying."

### AC 6 — Kept simple — no framework

22. No pytest, no contract testing framework (Pact, etc.). Plain Python script with `sys.exit()`.
23. Total script length < 150 lines.
24. Works without any configuration file — all defaults are in the script itself.

## Dev Notes

### QuestDB column type query

```python
import requests

def get_questdb_columns(url: str, table: str) -> dict[str, str]:
    query = f"SELECT column, type FROM table_columns('{table}')"
    r = requests.get(f"{url}/exec", params={"query": query}, timeout=5)
    r.raise_for_status()
    data = r.json()
    return {row[0]: row[1] for row in data.get("dataset", [])}
```

Note: QuestDB uses `table_columns()` function, not `information_schema.columns`. The query above is correct for QuestDB.

### Redis stream freshness check

```python
import redis, time

def check_stream_freshness(r: redis.Redis, key: str) -> tuple[bool, int]:
    """Returns (is_fresh, seconds_ago). is_fresh=True if last message < 60s ago."""
    entries = r.xrevrange(key, count=1)
    if not entries:
        return False, -1
    last_id = entries[0][0]  # b'1716748800000-0'
    ts_ms = int(last_id.decode().split('-')[0])
    seconds_ago = int(time.time()) - ts_ms // 1000
    return seconds_ago < 60, seconds_ago
```

### Where to maintain the expected columns list

The `EXPECTED_COLUMNS` dict in the script is the authoritative contract definition. When adding a new column to `snapshot_1s` in the pipeline, update the script in **both repos** in the same PR. This is the low-tech equivalent of a schema registry — one file, two repos, both updated together.

## Tasks / Subtasks

- [ ] Write `scripts/validate-contract.py` (AC 1–4)
- [ ] Test locally against running pipeline: `python3 scripts/validate-contract.py`
- [ ] Test failure case: temporarily rename a Redis key or comment out a column check
- [ ] Copy script to `magnum-opus-trading/scripts/validate-contract.py` (identical)
- [ ] Add CI placeholder jobs to both repos (AC 5)
- [ ] Document in both repos' READMEs (AC 5)
