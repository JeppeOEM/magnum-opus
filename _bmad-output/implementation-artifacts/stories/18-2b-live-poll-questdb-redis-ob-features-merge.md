# Story 18.2b: Data Layer — Live Poll + ob_features Stream

Status: done

## Story

As a developer,
I want the data layer to poll QuestDB every second for new candles and tail the Redis ob_features stream,
So that all panels update live without the dashboard reloading.

**Pre-conditions:** Story 18.2a complete (`candle-store`, `last-ts` dcc.Store, symbol dropdown wired).

## Acceptance Criteria

**AC 1 — Live QuestDB poll appends to candle-store**
- `dcc.Interval(id='live-interval', interval=1000)` fires every second
- Callback queries `snapshot_1s` for rows with `ts > last_known_ts`
- New rows appended to `candle-store` (no duplicates — dedup on `ts`)
- `last-ts` updated to the maximum `ts` in the store after append

**AC 2 — ob_features Redis stream tailing**
- When interval fires, `XREAD COUNT 100 STREAMS ob_features:{exchange}:{symbol} {cursor_id}` is called
- New entries appended to `ob-store` (`id='ob-store'`)
- Cursor ID advanced to the latest entry ID returned

**AC 3 — ADR-18-02 cursor seeding**
- On symbol first load (symbol change trigger): `XREVRANGE ob_features:{exchange}:{symbol} + - COUNT 1` to get latest entry ID
- Cursor stored in `dcc.Store(id='ob-cursor', data='0')`
- If stream is empty, cursor stays `'0'` (reads from beginning)

**AC 4 — Symbol change resets both stores**
- When user selects new symbol: `candle-store=[]`, `ob-store=[]`, `ob-cursor='0'`
- History fetch (18.2a callback) fires for new symbol
- `dcc.Loading` spinners display until store repopulates

**AC 5 — Redis unreachable handled**
- When XREAD fails: error logged; `ob-store` retains existing data; panels do not crash

**AC 6 — New stores in layout.py**
- `dcc.Store(id='ob-store', data=[])` added to layout
- `dcc.Store(id='ob-cursor', data='0')` added to layout

**AC 7 — `data.py` Redis helper**
- `fetch_ob_snapshot(exchange: str, symbol: str, redis_url: str) -> str` returns latest ob_features entry ID (or `'0'` if stream empty)
- `fetch_ob_live(exchange: str, symbol: str, cursor_id: str, redis_url: str) -> tuple[list[dict], str]` returns (new_entries, new_cursor)
- Both handle Redis connection errors (return empty/unchanged, log error)

**AC 8 — `fetch_new_candles` in data.py**
- `fetch_new_candles(exchange: str, symbol: str, last_ts: str, questdb_url: str) -> list[dict]` queries `snapshot_1s WHERE ts > '{last_ts}'`
- Returns rows in ascending `ts` order
- Returns `[]` on empty result or error

## Tasks / Subtasks

- [ ] Task 1: Update `dashboard/data.py` — add Redis helpers and `fetch_new_candles`
  - [ ] 1.1 Add `fetch_new_candles(exchange, symbol, last_ts, questdb_url) -> list[dict]`
  - [ ] 1.2 Add `fetch_ob_snapshot(exchange, symbol, redis_url) -> str` (cursor seed)
  - [ ] 1.3 Add `fetch_ob_live(exchange, symbol, cursor_id, redis_url) -> tuple[list[dict], str]`

- [ ] Task 2: Update `dashboard/layout.py`
  - [ ] 2.1 Add `dcc.Store(id='ob-store', data=[])`
  - [ ] 2.2 Add `dcc.Store(id='ob-cursor', data='0')`
  - [ ] 2.3 Add `dcc.Interval(id='live-interval', interval=1000, n_intervals=0)`

- [ ] Task 3: Update `dashboard/callbacks.py`
  - [ ] 3.1 Add `live_update` callback: Input=live-interval, State=candle-store+last-ts+symbol+ob-cursor, Outputs=candle-store+last-ts+ob-store+ob-cursor
  - [ ] 3.2 Update `update_candle_store` (symbol change) to also reset ob-store+ob-cursor and seed ob cursor

- [ ] Task 4: Verify
  - [ ] 4.1 Container starts and `/_dash-layout` returns 200
  - [ ] 4.2 No import errors

## Dev Notes

### Redis stream key pattern

Stream key: `ob_features:{exchange}:{symbol}` — e.g. `ob_features:kucoin:BTC-USDT`

This matches the pattern from CLAUDE.md: `candles:ob:{exchange}:{symbol}` is the candle OB features stream; the `ob_features` stream comes from the aggregator (not candle service). Confirm by checking the aggregator code — aggregator publishes `ob_features:{exchange}:{symbol}` via Redis pubsub in epic 17. The dashboard reads this stream.

### Redis client initialisation

Use `redis.from_url(redis_url)` — matches the pattern in bot-service. Don't create a per-request connection; create a module-level client:

```python
import redis as redis_lib

_redis_client = redis_lib.from_url(os.environ.get("REDIS_URL", "redis://redis:6379"))
```

Note: `redis` PyPI package is already in `requirements.txt` as `redis==7.4.0`.

### fetch_new_candles — timestamp comparison

QuestDB `ts` column is a TIMESTAMP. The REST API returns it as an ISO 8601 string like `"2026-05-11T00:00:01.000000Z"`. The `last_ts` stored in `last-ts` store is the same format.

Use the string directly in the SQL WHERE clause — ISO 8601 sorts lexicographically, so `WHERE ts > '2026-05-11T00:00:01.000000Z'` is valid QuestDB SQL.

Apply the same `_SAFE_IDENT` validation for exchange and symbol. For `last_ts`, validate it matches the ISO 8601 pattern before interpolating.

```python
_SAFE_TS = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z$')

def fetch_new_candles(exchange: str, symbol: str, last_ts: str, questdb_url: str) -> list[dict]:
    if not _SAFE_IDENT.match(exchange) or not _SAFE_IDENT.match(symbol):
        logger.error("fetch_new_candles: unsafe exchange=%r symbol=%r", exchange, symbol)
        return []
    if last_ts and not _SAFE_TS.match(last_ts):
        logger.error("fetch_new_candles: unsafe last_ts=%r", last_ts)
        return []
    query = (
        f"SELECT * FROM snapshot_1s "
        f"WHERE exchange='{exchange}' AND symbol='{symbol}' AND ts > '{last_ts}' "
        f"ORDER BY ts ASC LIMIT 100"
    )
    # ... same try/except pattern as fetch_history
```

Note: Use `ORDER BY ts ASC LIMIT 100` (not DESC) here — we want newest additions in order, and at 1s intervals we expect at most a few rows.

### fetch_ob_snapshot — cursor seeding

```python
def fetch_ob_snapshot(exchange: str, symbol: str) -> str:
    stream_key = f"ob_features:{exchange}:{symbol}"
    try:
        entries = _redis_client.xrevrange(stream_key, count=1)
        if entries:
            return entries[0][0].decode() if isinstance(entries[0][0], bytes) else entries[0][0]
        return '0'
    except redis_lib.RedisError as e:
        logger.error("ob_features cursor seed failed %s: %s", stream_key, e)
        return '0'
```

Note: redis-py 7.x returns entry IDs as strings (not bytes) when using `redis.from_url`. But be safe and handle both.

### fetch_ob_live — live XREAD

```python
def fetch_ob_live(exchange: str, symbol: str, cursor_id: str) -> tuple[list[dict], str]:
    stream_key = f"ob_features:{exchange}:{symbol}"
    try:
        result = _redis_client.xread({stream_key: cursor_id}, count=100)
        if not result:
            return [], cursor_id
        entries_raw = result[0][1]  # [(id, {field: value}), ...]
        entries = []
        new_cursor = cursor_id
        for entry_id, fields in entries_raw:
            entry_id_str = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
            row = {k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
                   for k, v in fields.items()}
            row["_id"] = entry_id_str
            entries.append(row)
            new_cursor = entry_id_str
        return entries, new_cursor
    except redis_lib.RedisError as e:
        logger.error("ob_features XREAD failed %s cursor=%s: %s", stream_key, cursor_id, e)
        return [], cursor_id
```

### callbacks.py — live_update callback

The live_update callback uses `State` (not `Input`) for the current store values so it can append rather than replace:

```python
from dash import Input, Output, State, callback, no_update
import data

@callback(
    Output("candle-store", "data"),
    Output("last-ts", "data"),
    Output("ob-store", "data"),
    Output("ob-cursor", "data"),
    Input("live-interval", "n_intervals"),
    State("candle-store", "data"),
    State("last-ts", "data"),
    State("symbol-dropdown", "value"),
    State("ob-cursor", "data"),
)
def live_update(n, candle_rows, last_ts, selected, ob_cursor):
    if not selected or not last_ts:
        return no_update, no_update, no_update, no_update
    exchange, symbol = selected.split(":", 1)
    # QuestDB new candles
    new_rows = data.fetch_new_candles(exchange, symbol, last_ts, _QUESTDB_URL)
    if new_rows:
        candle_rows = (candle_rows or []) + new_rows
        last_ts = new_rows[-1]["ts"]
    # Redis ob_features
    new_ob, new_cursor = data.fetch_ob_live(exchange, symbol, ob_cursor or '0')
    ob_rows = (ob_store or []) + new_ob  # BUG: ob_store not in scope — fix below
    ...
```

**Correct form (use State for ob-store too):**

```python
@callback(
    Output("candle-store", "data"),
    Output("last-ts", "data"),
    Output("ob-store", "data"),
    Output("ob-cursor", "data"),
    Input("live-interval", "n_intervals"),
    State("candle-store", "data"),
    State("last-ts", "data"),
    State("symbol-dropdown", "value"),
    State("ob-store", "data"),
    State("ob-cursor", "data"),
)
def live_update(n, candle_rows, last_ts, selected, ob_rows, ob_cursor):
    if not selected or not last_ts:
        return no_update, no_update, no_update, no_update
    exchange, symbol = selected.split(":", 1)
    new_rows = data.fetch_new_candles(exchange, symbol, last_ts, _QUESTDB_URL)
    if new_rows:
        candle_rows = (candle_rows or []) + new_rows
        last_ts = new_rows[-1]["ts"]
    new_ob, new_cursor = data.fetch_ob_live(exchange, symbol, ob_cursor or '0')
    ob_rows = (ob_rows or []) + new_ob
    return candle_rows, last_ts, ob_rows, new_cursor
```

### Symbol change callback update

Update `update_candle_store` in callbacks.py to also return ob-store and ob-cursor:

```python
@callback(
    Output("candle-store", "data"),
    Output("last-ts", "data"),
    Output("ob-store", "data"),
    Output("ob-cursor", "data"),
    Input("symbol-dropdown", "value"),
)
def update_candle_store(selected):
    if not selected:
        return [], None, [], '0'
    exchange, symbol = selected.split(":", 1)
    rows = data.fetch_history(exchange, symbol, _QUESTDB_URL)
    max_ts = rows[-1]["ts"] if rows else None  # rows is ascending (newest last) — see fetch_history
    ob_cursor = data.fetch_ob_snapshot(exchange, symbol)
    return rows, max_ts, [], ob_cursor
```

This seeds the ob cursor on symbol change and clears ob-store so old ob data doesn't linger.

### Deduplication

Live poll XREAD uses `cursor_id` — Redis returns only entries strictly after cursor. No explicit deduplication needed for ob-store.

For candle-store: `fetch_new_candles` uses `WHERE ts > last_ts` — strictly greater, no overlap. No deduplication needed.

### Store growth / memory

`candle-store` grows by ~1 row/second unbounded. `ob-store` grows by ~1 entry/second. For a demo dashboard this is acceptable. A trim step (keep last N rows) is deferred to a future story.

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

### File List

- `dashboard/data.py` — UPDATED (fetch_new_candles, fetch_ob_snapshot, fetch_ob_live, module-level Redis client)
- `dashboard/callbacks.py` — UPDATED (live_update callback, ob-cursor-symbol guard)
- `dashboard/layout.py` — UPDATED (ob-store, ob-cursor, ob-cursor-symbol, live-interval)

### Review Findings

- [x] [Review][Patch] P1: live_update returned real data on every tick when nothing changed — changed to use no_update for unchanged outputs [`callbacks.py`] — applied
- [x] [Review][Patch] P2: Race window on rapid symbol change — added ob-cursor-symbol sentinel store; live_update skips ob fetch if cursor not yet seeded for current symbol [`callbacks.py`, `layout.py`] — applied
- [x] [Review][Patch] P3: fetch_new_candles None last_ts short-circuited safety check — added explicit `if not last_ts: return []` guard before _SAFE_TS check [`data.py`] — applied
- [x] [Review][Defer] _redis_client module-level singleton needs per-worker reinit if gunicorn multi-worker is ever used — deferred, current deployment is single-process
- [x] [Review][Defer] _SAFE_TS regex rejects whole-second timestamps (no decimal) — QuestDB consistently returns microseconds; deferred
