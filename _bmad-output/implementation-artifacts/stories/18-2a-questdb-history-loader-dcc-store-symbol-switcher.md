# Story 18.2a: Data Layer — QuestDB History Loader

Status: done

## Story

As a developer,
I want a data module that fetches historical candles from QuestDB and stores them in dcc.Store,
So that panel stories have a clean DataFrame to build against.

**Pre-conditions:** Story 18.1 complete (dashboard/ scaffold, layout.py, app.py with BASE_FIGURE).

## Acceptance Criteria

**AC 1 — QuestDB history fetch on page load**
- When page loads with `kucoin:BTC-USDT` selected, the app queries:
  `GET http://questdb:9000/exec?query=SELECT * FROM snapshot_1s WHERE exchange='kucoin' AND symbol='BTC-USDT' ORDER BY ts DESC LIMIT 500`
- `dcc.Store(id='candle-store')` is populated with a JSON-serialised list of row dicts in ascending `ts` order

**AC 2 — Symbol switch clears and refetches**
- When user selects a different symbol, `candle-store` is set to `[]` immediately
- `dcc.Loading` wrappers show spinners while refetch is in progress
- New QuestDB query fires for the selected `(exchange, symbol)` pair
- Store repopulates with new symbol's history

**AC 3 — Empty result handled**
- When QuestDB returns zero rows: `candle-store` is set to `[]`; panels render empty state without crash

**AC 4 — QuestDB unreachable handled**
- When HTTP request fails: error is logged; `candle-store` remains `[]`; app does not crash

**AC 5 — Data module `data.py`**
- `dashboard/data.py` exports `fetch_history(exchange: str, symbol: str, questdb_url: str) -> list[dict]`
- Pure function: takes URL, returns list of row dicts sorted ascending by `ts`
- Handles empty result set (returns `[]`)
- Handles HTTP errors (returns `[]`, logs error)
- QuestDB response format: `{"dataset": [[...values...]], "columns": [{"name": "..."}, ...]}` — converts to list of `{column_name: value}` dicts

**AC 6 — `dcc.Store` declarations in layout.py**
- `layout.py` gains `dcc.Store(id='candle-store', data=[])` declared in the layout
- Also `dcc.Store(id='last-ts', data=None)` for tracking the latest timestamp (used in 18.2b)
- Stores placed within the `dbc.Container` but outside visible panel rows

**AC 7 — Callback in callbacks.py**
- `callbacks.py` defines callback `update_candle_store(symbol)` triggered by `Input("symbol-dropdown", "value")`
- Output: `Output("candle-store", "data"), Output("last-ts", "data")`
- Calls `data.fetch_history(exchange, symbol, os.environ.get("QUESTDB_HTTP_ADDR", "http://questdb:9000"))`
- Returns `(rows, max_ts)` where `max_ts` is the maximum `ts` value in the result (or `None` if empty)

## Tasks / Subtasks

- [ ] Task 1: Create `dashboard/data.py`
  - [ ] 1.1 Implement `fetch_history(exchange, symbol, questdb_url) -> list[dict]`
  - [ ] 1.2 Handle empty dataset (return `[]`)
  - [ ] 1.3 Handle request exception (log, return `[]`)

- [ ] Task 2: Update `dashboard/layout.py`
  - [ ] 2.1 Add `dcc.Store(id='candle-store', data=[])` to layout
  - [ ] 2.2 Add `dcc.Store(id='last-ts', data=None)` to layout

- [ ] Task 3: Update `dashboard/callbacks.py`
  - [ ] 3.1 Import `app` from `app`, import `data`, import `os`
  - [ ] 3.2 Add `update_candle_store` callback: Input="symbol-dropdown" value, Outputs="candle-store" data + "last-ts" data
  - [ ] 3.3 Parse symbol string `"exchange:symbol"` format

- [ ] Task 4: Verify
  - [ ] 4.1 Container starts and `/_dash-layout` returns 200
  - [ ] 4.2 Selecting a symbol populates candle-store (verify via browser dev tools or `/_dash-update-component`)

## Dev Notes

### QuestDB REST response format

```json
{
  "columns": [{"name": "ts", "type": "TIMESTAMP"}, {"name": "exchange", "type": "SYMBOL"}, ...],
  "dataset": [["2026-05-11T00:00:01.000000Z", "kucoin", "BTC-USDT", 67000.0, ...], ...],
  "count": 500,
  "query": "SELECT * FROM snapshot_1s ..."
}
```

Convert to list of dicts:
```python
cols = [c["name"] for c in resp["columns"]]
rows = [dict(zip(cols, row)) for row in resp["dataset"]]
```

QuestDB returns rows in `ORDER BY ts DESC` — reverse for ascending before storing:
```python
rows.reverse()
```

### data.py implementation

```python
import logging
import requests

logger = logging.getLogger(__name__)

def fetch_history(exchange: str, symbol: str, questdb_url: str, limit: int = 500) -> list[dict]:
    query = (
        f"SELECT * FROM snapshot_1s "
        f"WHERE exchange='{exchange}' AND symbol='{symbol}' "
        f"ORDER BY ts DESC LIMIT {limit}"
    )
    try:
        resp = requests.get(f"{questdb_url}/exec", params={"query": query}, timeout=10)
        resp.raise_for_status()
        body = resp.json()
    except Exception as e:
        logger.error("QuestDB history fetch failed: %s", e)
        return []

    cols = [c["name"] for c in body.get("columns", [])]
    rows = [dict(zip(cols, row)) for row in body.get("dataset", [])]
    rows.reverse()  # DESC → ASC
    return rows
```

**Security note:** `exchange` and `symbol` values come from a fixed dropdown — they are not user-entered free text. The dropdown options in `layout.py` are a closed set (`kucoin:BTC-USDT`, `kucoin:ETH-USDT`, `bybit:BTC-USDT`). However, f-string SQL construction is a code smell. If the symbol list ever becomes user-editable, switch to parameterised queries. For now this is acceptable.

### callbacks.py pattern

```python
import os
from dash import Input, Output, callback
from app import app  # noqa: F401 — needed for @app.callback if ever used; here we use @callback
import data

@callback(
    Output("candle-store", "data"),
    Output("last-ts", "data"),
    Input("symbol-dropdown", "value"),
)
def update_candle_store(selected):
    if not selected:
        return [], None
    exchange, symbol = selected.split(":", 1)
    questdb_url = os.environ.get("QUESTDB_HTTP_ADDR", "http://questdb:9000")
    rows = data.fetch_history(exchange, symbol, questdb_url)
    max_ts = rows[-1]["ts"] if rows else None
    return rows, max_ts
```

**Note:** Use `@callback` (module-level, Dash 2.x pattern) rather than `@app.callback`. This avoids needing to import `app` and eliminates circular import risk entirely. The `from app import app` import in the callback module is NOT needed when using `@callback`.

### layout.py update — dcc.Store placement

Add stores to the Container after existing rows:

```python
# dcc.Store components — data layer, not visible
dcc.Store(id="candle-store", data=[]),
dcc.Store(id="last-ts", data=None),
```

Place at end of the Container's children list.

### Symbol parsing convention

The dropdown `value` is `"exchange:symbol"` (e.g. `"kucoin:BTC-USDT"`). All callbacks that need exchange and symbol parse it as:
```python
exchange, symbol = selected.split(":", 1)
```
This `split(":", 1)` handles symbols that contain colons (none currently, but safe).

### max_ts type from QuestDB

QuestDB returns timestamp columns as ISO strings like `"2026-05-11T00:00:01.000000Z"`. Store `max_ts` as this string — `last-ts` store holds a string or `None`. The 18.2b live poll will append rows with `ts > max_ts` using string comparison (ISO 8601 sorts lexicographically).

### Error logging in Docker

Python's `logging` module writes to stderr by default; Docker captures stderr in container logs. Use `logging.basicConfig(level=logging.INFO)` in `app.py` so log messages appear in `docker compose logs dashboard`.

Add to `app.py`:
```python
import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
```

### What this story does NOT do
- No live polling (18.2b)
- No ob_features Redis stream (18.2b)
- No chart rendering (18.3–18.6)
- No symbol change loading state management beyond Dash's default `dcc.Loading` behaviour

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

### File List

- `dashboard/data.py` — NEW
- `dashboard/callbacks.py` — UPDATED (history loader callback)
- `dashboard/layout.py` — UPDATED (dcc.Store declarations)
- `dashboard/app.py` — UPDATED (logging.basicConfig)
- `dashboard/Dockerfile` — UPDATED (data.py copy)

### Review Findings

- [x] [Review][Patch] P1: SQL injection via unvalidated exchange/symbol format strings — added `_SAFE_IDENT` regex guard before query construction [`data.py`] — applied
- [x] [Review][Patch] P2: `json.JSONDecodeError` silently swallowed by broad `except Exception` — split into `requests.RequestException` + `ValueError` handlers [`data.py`] — applied
- [x] [Review][Note] `rows[-1]["ts"]` ordering contract — added comment clarifying rows is ascending (newest last) [`callbacks.py`] — applied
- [x] [Review][Defer] `QUESTDB_HTTP_ADDR` env lookup per-callback — moved to module-level constant [`callbacks.py`] — applied (trivial, done now)
- [x] [Review][Defer] `SELECT *` risks silent breakage on schema evolution — deferred, acceptable for Epic 18
- [x] [Review][Defer] `logging.basicConfig` could no-op if libs configure logging first — `force=True` option noted, deferred
