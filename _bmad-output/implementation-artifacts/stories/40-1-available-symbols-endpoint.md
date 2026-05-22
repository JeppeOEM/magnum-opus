# Story 40.1: Add `/backtest/available-symbols` Endpoint to Bot-Service

Status: done

## Story

As a dashboard user running a backtest,
I want the dashboard to know which (exchange, symbol) pairs have data in QuestDB,
So that I can choose from real symbols rather than guessing or typing.

## Background

Currently `layout_backtest.py` uses a free-text `dbc.Input` for the symbol field. The user must type "BTCUSDT" manually without any indication of what data is actually available. The fix requires a new API endpoint on `bot-service` that queries QuestDB for distinct symbols with coverage data, plus a corresponding data-layer helper in the dashboard.

## Acceptance Criteria

### AC 1 — New endpoint: `GET /backtest/available-symbols`

1. `bot_service/main.py` adds a `GET /backtest/available-symbols` route.
2. Accepts an optional query parameter `exchange: str | None = Query(default=None)`.
3. Executes the following QuestDB query (or equivalent):
   ```sql
   SELECT exchange, symbol, min(ts) as min_ts, max(ts) as max_ts, count() as row_count
   FROM snapshot_1s
   WHERE exchange = '<exchange>'   -- omitted when exchange param is None
   GROUP BY exchange, symbol
   ORDER BY exchange, symbol
   ```
4. Returns a JSON array of objects with keys: `exchange`, `symbol`, `min_ts`, `max_ts`, `row_count`.
   Example:
   ```json
   [
     {"exchange": "bybit", "symbol": "BTCUSDT", "min_ts": "2026-01-01T00:00:00Z", "max_ts": "2026-05-01T00:00:00Z", "row_count": 11232000},
     {"exchange": "bybit", "symbol": "ETHUSDT", "min_ts": "2026-01-01T00:00:00Z", "max_ts": "2026-05-01T00:00:00Z", "row_count": 11231850}
   ]
   ```
5. Returns `[]` (empty array) if `snapshot_1s` table does not exist or has no rows — never raises HTTP 500 for a missing table.
6. Uses `_IDENT_RE` validation on the `exchange` parameter (same pattern as existing backtest routes) — returns HTTP 400 for invalid exchange values.
7. Uses `httpx.get` with `timeout=10.0` for the QuestDB call (consistent with existing `/backtest/runs` endpoint pattern).

### AC 2 — Dashboard data layer: `fetch_available_symbols`

8. `dashboard/backtest_data.py` adds:
   ```python
   def fetch_available_symbols(exchange: str | None = None) -> list[dict[str, Any]]:
   ```
9. Calls `GET {BOT_SERVICE_URL}/backtest/available-symbols` with optional `exchange` query param.
10. Returns the parsed JSON array on success; returns `[]` on any exception (consistent with other helpers in the file that catch exceptions and log warnings).
11. Logs `"fetch_available_symbols_failed"` at WARNING on exception (consistent with existing log keys in the module).

### AC 3 — Tests

12. `bot-service/tests/test_main.py` adds at least 2 tests for the new endpoint:
    - **`test_available_symbols_no_exchange`**: mock httpx to return a valid QuestDB response with two symbols (no exchange filter); assert endpoint returns both symbols with correct keys.
    - **`test_available_symbols_empty_table`**: mock httpx to return `{"columns": [], "dataset": []}` (empty QuestDB result); assert endpoint returns `[]`.
    - **`test_available_symbols_invalid_exchange`**: call with `exchange="../../etc/passwd"`; assert HTTP 400.
13. `dashboard/test_data.py` adds or extends existing backtest data tests with `test_fetch_available_symbols_success` and `test_fetch_available_symbols_failure` (mocked requests).

## Tasks / Subtasks

- [x] Add `GET /backtest/available-symbols` endpoint to `bot_service/main.py` (AC 1)
  - [x] Add route with optional `exchange` query param
  - [x] Build QuestDB SQL with optional WHERE clause
  - [x] Handle empty/missing table gracefully (return `[]`)
  - [x] Validate exchange param with `_IDENT_RE`
- [x] Add `fetch_available_symbols()` to `dashboard/backtest_data.py` (AC 2)
  - [x] Call `GET /backtest/available-symbols` with optional `exchange` param
  - [x] Return `[]` and log warning on exception
- [x] Add tests (AC 3)
  - [x] `test_available_symbols_no_exchange`, `test_available_symbols_empty_table`, `test_available_symbols_invalid_exchange` in `test_main.py` (6 tests total added)
  - [x] `test_fetch_available_symbols_success`, `test_fetch_available_symbols_failure` in `dashboard/test_backtest_data.py` (5 tests added)

## Dev Agent Record

### Completion Notes

All 3 ACs satisfied:
- `bot_service/main.py`: added `GET /backtest/available-symbols` endpoint with optional `exchange` query param, QuestDB GROUP BY query, graceful `[]` on missing table/error, `_IDENT_RE` validation returning HTTP 400 on invalid exchange
- `dashboard/backtest_data.py`: added `fetch_available_symbols(exchange?)` — calls `/backtest/available-symbols`, returns `[]` + logs on exception
- Tests: 6 tests in `bot-service/tests/test_main.py` (no filter, empty table, invalid exchange, missing table, unreachable, WHERE clause capture); 5 tests in `dashboard/test_backtest_data.py` (success, no exchange, exchange forwarded, failure, http error) — all pass; 37/37 total test_main.py suite passing with no regressions

### File List

- `bot-service/bot_service/main.py` — added `GET /backtest/available-symbols` endpoint
- `bot-service/tests/test_main.py` — added 6 tests for the new endpoint
- `dashboard/backtest_data.py` — added `fetch_available_symbols()` helper
- `dashboard/test_backtest_data.py` — new file with 5 tests for `fetch_available_symbols`

### Change Log

- 2026-05-22: Story 40.1 implemented — `/backtest/available-symbols` endpoint + `fetch_available_symbols` dashboard helper + tests

## Dev Notes

### QuestDB `snapshot_1s` schema

The `snapshot_1s` table has `ts` (TIMESTAMP, designated), `exchange` (SYMBOL), `symbol` (SYMBOL). The GROUP BY query is supported in QuestDB's SQL dialect.

If `snapshot_1s` does not yet exist (fresh deployment), QuestDB returns:
```json
{"error": "table 'snapshot_1s' does not exist"}
```
The endpoint must detect this and return `[]` instead of propagating the error.

### Pattern for existing `/backtest/runs` endpoint

The existing `/backtest/runs` endpoint in `main.py` uses `httpx.get` + `resp.raise_for_status()` + manual column extraction. Follow the same pattern:
```python
@app.get("/backtest/available-symbols")
def available_symbols(exchange: str | None = Query(default=None)) -> list[dict[str, Any]]:
    if exchange is not None and not _IDENT_RE.match(exchange):
        raise HTTPException(status_code=400, detail="Invalid exchange")
    settings = get_settings()
    where = f" WHERE exchange = '{exchange}'" if exchange else ""
    query = (
        f"SELECT exchange, symbol, min(ts) as min_ts, max(ts) as max_ts, count() as row_count "
        f"FROM snapshot_1s{where} GROUP BY exchange, symbol ORDER BY exchange, symbol"
    )
    try:
        resp = httpx.get(f"{settings.questdb_http_addr}/exec", params={"query": query}, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            return []  # table missing or other QuestDB error
        cols = [c["name"] for c in data.get("columns", [])]
        return [dict(zip(cols, row)) for row in data.get("dataset", [])]
    except Exception as exc:
        log.warning("available_symbols_query_failed", error=str(exc))
        return []
```

### `fetch_available_symbols` in `backtest_data.py`

```python
def fetch_available_symbols(exchange: str | None = None) -> list[dict[str, Any]]:
    params = {}
    if exchange:
        params["exchange"] = exchange
    try:
        resp = requests.get(f"{BOT_SERVICE_URL}/backtest/available-symbols", params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("fetch_available_symbols_failed: %s", exc)
        return []
```
