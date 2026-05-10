# Story 11.2: QuestDB Schema — order_events & order_alerts

Status: done

## Story

As mrqdt,
I want both QuestDB tables created idempotently at service startup via REST,
so that the schema exists before any write path runs and re-deployments are safe.

## Acceptance Criteria

1. `apply_schema(questdb_http_addr: str) -> None` in `bot_service/persistence/schema.py` issues `CREATE TABLE IF NOT EXISTS order_events` with the exact DDL from project-context.md via `POST /exec`.
2. Same call also creates `order_alerts` with the exact DDL from project-context.md.
3. Both `CREATE TABLE IF NOT EXISTS` calls are idempotent — calling `apply_schema` twice on tables that already exist succeeds without error.
4. If QuestDB is unreachable (connection refused or non-2xx response), `apply_schema` logs ERROR with the failure reason and raises `SchemaApplyError`; the caller (main.py, Story 11.7) must treat this as a fatal startup error.
5. L2 test using a real QuestDB container: call `apply_schema` twice, assert both tables exist with correct column types; call with unreachable QuestDB, assert `SchemaApplyError` raised.

## Tasks / Subtasks

- [x] Write `bot_service/persistence/schema.py` (AC: 1–4)
  - [x] Define `class SchemaApplyError(Exception): pass`
  - [x] Implement `apply_schema(questdb_http_addr: str) -> None`
  - [x] `order_events` DDL — exact columns from project-context.md
  - [x] `order_alerts` DDL — exact columns from project-context.md
  - [x] Use `httpx.Client` (sync, not async — called at startup before event loop) for `GET /exec`
  - [x] Log ERROR + raise `SchemaApplyError` on non-2xx or connection error
- [x] Write L2 test `tests/test_schema.py` (AC: 3, 5)
  - [x] Fixture: real QuestDB container via `testcontainers` or `docker` SDK; skip if Docker unavailable
  - [x] Test: `apply_schema` twice → no error, tables exist
  - [x] Test: unreachable QuestDB → `SchemaApplyError`
  - [x] Mark all tests `@pytest.mark.l2`

## Dev Notes

### Exact DDL — order_events

```sql
CREATE TABLE IF NOT EXISTS order_events (
    ts                  TIMESTAMP,
    order_id            SYMBOL,
    client_order_id     SYMBOL,
    strategy            SYMBOL,
    exchange            SYMBOL,
    symbol              SYMBOL,
    market_type         SYMBOL,
    side                SYMBOL,
    order_type          SYMBOL,
    status              SYMBOL,
    limit_price         DOUBLE,
    stop_price          DOUBLE,
    take_profit_price   DOUBLE,
    requested_size      DOUBLE,
    filled_size         DOUBLE,
    remaining_size      DOUBLE,
    avg_fill_price      DOUBLE,
    fee                 DOUBLE,
    fee_currency        SYMBOL,
    realized_pnl        DOUBLE,
    slippage            DOUBLE,
    position_size_after DOUBLE,
    signal_type         SYMBOL,
    paper_trading       BOOLEAN,
    backtest            BOOLEAN,
    ts_placed           TIMESTAMP,
    ts_exchange         TIMESTAMP
) TIMESTAMP(ts) PARTITION BY DAY WAL;
```

### Exact DDL — order_alerts

```sql
CREATE TABLE IF NOT EXISTS order_alerts (
    ts           TIMESTAMP,
    order_id     SYMBOL,
    strategy     SYMBOL,
    alert_type   SYMBOL,
    detail       STRING,
    resolved     BOOLEAN
) TIMESTAMP(ts) PARTITION BY DAY WAL;
```

### Implementation Pattern

QuestDB REST API endpoint: `POST {questdb_http_addr}/exec` with form body `query=<DDL>`.

```python
from __future__ import annotations

import httpx
import structlog

log = structlog.get_logger()

class SchemaApplyError(Exception):
    pass

_DDL_ORDER_EVENTS = """
CREATE TABLE IF NOT EXISTS order_events (
    ts                  TIMESTAMP,
    ...
) TIMESTAMP(ts) PARTITION BY DAY WAL;
"""

_DDL_ORDER_ALERTS = """
CREATE TABLE IF NOT EXISTS order_alerts (
    ts           TIMESTAMP,
    ...
) TIMESTAMP(ts) PARTITION BY DAY WAL;
"""

def apply_schema(questdb_http_addr: str) -> None:
    for ddl in [_DDL_ORDER_EVENTS, _DDL_ORDER_ALERTS]:
        try:
            resp = httpx.post(
                f"{questdb_http_addr}/exec",
                data={"query": ddl},
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("schema_apply_failed", error=str(exc))
            raise SchemaApplyError(str(exc)) from exc
```

Note: use `httpx.Client` synchronously (not `httpx.AsyncClient`) — `apply_schema` is called at process startup before any asyncio event loop is running.

### Type Annotation Rules

- All function signatures fully annotated including `-> None`
- `from __future__ import annotations` as first line
- No `Optional`, no `Union` — use `X | None`
- `Any` forbidden without inline comment

### Test Pattern (L2 — real QuestDB)

Use `testcontainers-python` (`pip install testcontainers`) or skip if Docker is not available:

```python
import pytest
import httpx
from testcontainers.core.container import DockerContainer

@pytest.mark.l2
def test_apply_schema_idempotent() -> None:
    with DockerContainer("questdb/questdb:8.2.1").with_exposed_ports(9000) as qdb:
        host = qdb.get_container_host_ip()
        port = qdb.get_exposed_port(9000)
        addr = f"http://{host}:{port}"
        # wait for QuestDB to be ready
        import time; time.sleep(5)
        apply_schema(addr)
        apply_schema(addr)  # idempotent — must not raise
        # verify tables exist
        resp = httpx.get(f"{addr}/exec", params={"query": "SHOW TABLES"})
        tables = [row[0] for row in resp.json()["dataset"]]
        assert "order_events" in tables
        assert "order_alerts" in tables

@pytest.mark.l2
def test_apply_schema_unreachable() -> None:
    with pytest.raises(SchemaApplyError):
        apply_schema("http://localhost:19999")  # nothing listening
```

Add `testcontainers` to `requirements-dev.txt`.

### What Already Exists (Story 11.1 output)

- `bot_service/persistence/__init__.py` — empty stub, already has `from __future__ import annotations`
- `bot_service/config.py` — `get_settings()`, `Settings` with `questdb_http_addr: str`
- `pyproject.toml` — mypy strict + pytest marks already configured
- `Makefile` — `typecheck`, `test-l2` targets already exist

### What NOT to Build

- Do NOT implement ILP writes here — that belongs to the order worker (Story 12.3)
- Do NOT implement `ALTER TABLE` migration logic — this story is DDL creation only
- Do NOT import or call `get_settings()` inside `apply_schema` — the caller passes `questdb_http_addr` explicitly so the function is testable without env vars

### Project Structure Notes

- File goes in `bot_service/persistence/schema.py`
- Tests go in `bot-service/tests/test_schema.py`
- `SchemaApplyError` is defined in `schema.py` itself — do not create a separate exceptions module yet

### References

- [Source: bot-service/project-context.md § QuestDB Tables]
- [Source: _bmad-output/planning-artifacts/epics-bot.md § Story 11.2]

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

None — implementation completed without blocking issues.

### Completion Notes List

- **API method correction:** The story's implementation pattern showed `POST /exec` with form body, but QuestDB 8.2.1 only accepts `GET /exec` with query params for DDL statements (POST returns "Method not supported"). Updated `schema.py` to use `httpx.Client.get()` with `params={"query": ddl}`.
- **Error detection:** QuestDB returns HTTP 200 even for DDL errors, with `{"error": "..."}` in the JSON body. Added explicit check for `"error"` key in the response body to raise `SchemaApplyError` correctly.
- **Health endpoint absent:** QuestDB 8.2.1 has no `/health` endpoint (returns 404). The L2 test readiness probe uses `GET /exec?query=SHOW TABLES` instead, which returns 200 + valid JSON once the server is ready.
- **Reserved word quoting:** `column` is a reserved SQL keyword in QuestDB. The `table_columns()` query in the L2 tests uses `SELECT "column", type FROM table_columns(...)` with double-quoted `column`.
- `testcontainers` installed in `.venv`, version 4.14.2.
- `mypy --strict bot_service/` passes with zero errors (15 source files).
- L1: 8 passed, no regressions.
- L2: 4 passed — idempotency, order_events columns, order_alerts columns, unreachable error.

### File List

- `bot_service/persistence/schema.py` — new file; `SchemaApplyError` + `apply_schema()`
- `bot-service/tests/test_schema.py` — new file; 4 L2 tests
- `bot-service/requirements-dev.txt` — added `testcontainers>=4.7.0`
