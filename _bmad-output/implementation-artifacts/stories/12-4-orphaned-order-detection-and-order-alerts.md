# Story 12.4: Orphaned Order Detection & order_alerts

## Status: done

## Story

**As** mrqdt,
**I want** orphaned exchange orders (present on exchange, absent from QuestDB) detected and written to order_alerts without auto-cancellation,
**so that** no exchange position is silently ignored and the operator is always notified.

## Acceptance Criteria

- **AC1:** Given startup reconciliation cross-referencing exchange open orders against QuestDB `order_events`, when an order is found on the exchange with no matching `order_id` in QuestDB (no row with that order_id), then it is written to the `order_alerts` table with `alert_type='orphaned_order'` and a detail string containing the exchange, symbol, order_id, and current quantity; `bot_orphaned_order_total{exchange}` is incremented; the order is NEVER auto-cancelled.

- **AC2:** Given an orphaned order written to `order_alerts`, the alert is queryable in QuestDB with `WHERE alert_type = 'orphaned_order'`.

- **AC3:** Given the same orphaned order appearing across multiple reconciliation runs (not yet resolved by operator), a new `order_alerts` row is written each time — no deduplication; the service must not silently suppress repeated alerts.

## Tasks / Subtasks

- [x] T1: Add `inc_orphaned_order` metric to `bot_service/metrics/prometheus.py`
  - [x] T1.1: Write failing test for the new metric function
  - [x] T1.2: Implement `bot_orphaned_order_total{exchange}` counter

- [x] T2: Implement `detect_orphaned_orders` function in `bot_service/strategy/reconciliation.py`
  - [x] T2.1: Write failing tests: orphan found → `order_alerts` written + counter; no orphan → no write
  - [x] T2.2: Caller provides `known_order_ids` set (pre-queried from QuestDB)
  - [x] T2.3: Compare against exchange `get_open_orders("")` response (empty string = all symbols)
  - [x] T2.4: For each orphan: write `order_alerts` row + increment counter + log.warning with detail
  - [x] T2.5: No cancel REST call made — verified by test assertion

- [x] T3: 6/6 L1 tests green, 112 total, mypy clean

## Senior Developer Review (AI)

**Date:** 2026-05-10  
**Outcome:** Changes Requested → all resolved

### Action Items

- [x] **[High]** P1: `symbol=""` convention undocumented — added explicit docstring note that empty string means all symbols and both clients honour it
- [x] **[High]** P2: `_extract_ilp_addr` fragile for bare hostnames — removed entirely; function now accepts `questdb_ilp_addr` directly
- [x] **[Med]** P3: `detail` not included in log.warning — added `detail=detail` field

### Deferred

- D1: `time.time()` in `_sync_write_alert` — not banned in Python bot-service (CLAUDE.md restriction applies to Go services only)
- D2: No test for `symbol=""` returning all orders — this is a contract between the caller and REST clients; covered by story 12-1 tests for the REST clients themselves

## Dev Notes

### Architecture

This is NOT part of `OrderQueueWorker` — it is a standalone reconciliation function called at startup, before the event loop starts strategy processing. Epic 13 will expand this into a full reconciliation module; this story delivers just the orphaned-order detection slice.

**Function signature:**
```python
async def detect_orphaned_orders(
    exchange_client: ExchangeClient,
    questdb_http_addr: str,
    strategy_name: str,
) -> None:
```

### QuestDB query

Query for known order_ids with non-terminal status:
```sql
SELECT order_id FROM order_events WHERE status = 'placed'
```

Use httpx async GET to `/exec?query=...`. Parse the `dataset` field (list of rows, each row is a list with one element). Collect as `set[str]`.

### order_alerts ILP write

Use `questdb.ingress.Sender.from_conf()` same as `order_worker._sync_ilp_write`. Table schema:
```sql
order_alerts (ts TIMESTAMP, order_id SYMBOL, strategy SYMBOL, alert_type SYMBOL, detail STRING, resolved BOOLEAN)
```

Write `resolved=False`. The `detail` field is a plain string: `f"exchange={exchange} symbol={order.symbol} qty={order.size}"`.

### No auto-cancel invariant

The function must never call `exchange_client.cancel_order()`. Unit test verifies this by asserting `cancel_order` is never called on a mock client.

### Existing files to read

- `bot_service/exchange/__init__.py` — `ExchangeClient` protocol, `OpenOrder` dataclass
- `bot_service/persistence/schema.py` — `order_alerts` DDL (for column names)
- `bot_service/metrics/prometheus.py` — follow existing counter pattern

### Test plan (L1)

1. Mock exchange returns one open order; mock QuestDB returns empty known_ids → `order_alerts` write called, counter incremented, cancel NOT called
2. Mock exchange returns one open order; mock QuestDB returns that same order_id in known_ids → no write, no counter, no cancel
3. Multiple exchange orders, some orphaned and some known → only orphaned ones trigger write

## File List

- NEW: `bot-service/bot_service/strategy/reconciliation.py`
- NEW: `bot-service/tests/test_reconciliation.py`
- UPDATED: `bot-service/bot_service/metrics/prometheus.py` — added `inc_orphaned_order`

## Dev Agent Record

### Completion Notes

All review patches applied. 6 L1 tests, 112 total, mypy clean.

## Change Log

| Date | Change |
|------|--------|
| 2026-05-10 | Story created |
