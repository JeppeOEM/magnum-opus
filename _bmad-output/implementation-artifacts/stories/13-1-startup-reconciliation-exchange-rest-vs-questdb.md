# Story 13.1: Startup Reconciliation — Exchange REST vs QuestDB

## Status: done

## Story

**As** mrqdt,
**I want** the service to reconcile open positions and non-terminal orders against exchange REST before any strategy starts,
**so that** no position is silently lost after a crash and strategies always start from accurate state.

## Acceptance Criteria

- **AC1:** Given the service starting up, when reconciliation runs, then the sequence is strictly: (1) query exchange REST for all open positions and non-terminal orders for all configured symbols, (2) cross-reference against QuestDB `order_events` WHERE status NOT IN ('filled','cancelled','rejected','failed'), (3) populate `self.open_orders` and position state for all strategies, (4) THEN start the file watcher and spawn strategy threads; no strategy event loop may receive events before step 3 completes or the timeout is reached.

- **AC2:** Given positions found in reconciliation for symbols not in the current strategy's `subscribe()` selections, when force-subscription runs, then those symbols are added to `_managed_positions` and events for them are routed to the strategy regardless of the `subscribe()` selection; strategies cannot filter out `_managed_positions` symbols.

- **AC3:** Given reconciliation does not complete within `BOT_RECONCILIATION_TIMEOUT_S` (120s default), when the timeout elapses, then the service logs CRITICAL with the failure reason; uses QuestDB-derived state only for `self.open_orders` and position tracking; marks all recovered positions as `unconfirmed`; continues starting strategies with best-available state; starts a background retry every 60 seconds until exchange confirmation is received.

- **AC4:** Given a process crash that occurred after `status='placed'` was written to QuestDB but before `self.open_orders` was updated in-memory, when reconciliation runs on next startup, then the `placed` order is found in QuestDB non-terminal orders, cross-referenced against exchange, and `self.open_orders` is populated correctly via `OrderQueueWorker.restore_open_order()`; no duplicate order is placed.

- **AC5:** Given cross-strategy position tracking, when two independent strategies both hold BTCUSDT long positions, then each strategy's `self.open_orders` tracks only its own orders; there is no shared position ledger across strategies; combined exposure is accepted as an architectural constraint.

## Tasks / Subtasks

- [x] T1: Add `_managed_positions: set[str]` to `BaseStrategy` and related metrics
  - [x] T1.1: Add `_managed_positions: set[str] = set()` to `BaseStrategy.__init__`
  - [x] T1.2: Add `bot_strategy_restart_total{strategy}` Counter and `bot_strategy_backoff_seconds{strategy}` Gauge to `metrics/prometheus.py`
  - [x] T1.3: Write failing L1 tests for the two new metric functions

- [x] T2: Expand `bot_service/strategy/reconciliation.py` with startup reconciliation
  - [x] T2.1: Write failing L1 tests (8 L1 tests)
  - [x] T2.2: Add `query_questdb_nonterminal_orders` — uses `requested_size` not `size` (QuestDB treats `size` as a function keyword)
  - [x] T2.3: Add `run_startup_reconciliation` async function
  - [x] T2.4: Implement force-subscribe via `strategy._managed_positions`
  - [x] T2.5: Implement timeout path — log CRITICAL, use QuestDB-only state
  - [x] T2.6: Call `order_worker.restore_open_order` for matched orders
  - [x] T2.7: Write `bot-service/DESIGN.md` with cross-strategy isolation decision

- [x] T3: Update `bot_service/main.py` lifespan to call reconciliation before bus start
  - [x] T3.1: Restructure lifespan — BusManager created before reconciliation hook comment, started after
  - [x] T3.2: Skip exchange REST in paper_trading mode (handled inside `run_startup_reconciliation`)

- [x] T4: L2 crash-recovery integration test
  - [x] T4.1: L2 test with real QuestDB (testcontainers) — crash-state row → reconciliation → restore_open_order called; no place_order

- [x] T5: All tests pass — 134 L1+L2 green, mypy --strict clean

## Dev Notes

### What already exists (do not reinvent)

- `bot_service/strategy/reconciliation.py`: has `detect_orphaned_orders()` — reads exchange open orders and writes `order_alerts` for unknowns. Story 13.1 expands this module; do NOT replace `detect_orphaned_orders`, extend alongside it.
- `OrderQueueWorker.restore_open_order(order_id, req, placed)`: already implemented in `bot_service/strategy/order_worker.py`. This is the exact hook reconciliation must call to repopulate layer-1 open_orders state. Read that method before implementing.
- `ExchangeClient.get_open_orders(symbol="")`: `symbol=""` returns ALL open orders across all symbols — both KuCoin and Bybit REST clients honour this convention. Confirmed in Story 12.4 docstring.
- `bot_reconciliation_timeout_s: int = 120` and `bot_filewatcher_interval_s: int = 60` are already in `Settings` (`config.py`).
- `bot_service/persistence/schema.py`: `order_events` DDL has all the columns you'll query. Non-terminal statuses are everything NOT IN `('filled','cancelled','rejected','failed')` — this matches the `placed` status written by `OrderQueueWorker._place_and_persist`.

### Architecture: reconciliation sequence

The reconciliation function must be called in `main.py` lifespan BEFORE any strategy thread starts and BEFORE `BusManager.start()` dispatches events to strategy queues. The current `main.py` starts BusManager immediately in Step 4 — restructure to:

```
Step 3: apply schema
Step 4: run_startup_reconciliation()  ← new step
Step 5: start BusManager
Step 6: file watcher spawns strategy threads (Story 13.2)
```

Because `BusManager.register()` and `BusManager.add_stream()` both raise `RuntimeError` if called after `start()`, the reconciliation step must complete before `BusManager.start()`.

### QuestDB query for non-terminal orders

Use `httpx` (sync, not async — reconciliation is called from async context but QuestDB HTTP is fine via `asyncio.to_thread`):

```python
query = (
    "SELECT order_id, client_order_id, symbol, side, order_type, size, limit_price, status "
    "FROM order_events "
    f"WHERE status NOT IN ('filled','cancelled','rejected','failed') "
    f"AND strategy = '{strategy_name}' "
    "ORDER BY ts DESC"
)
resp = httpx.get(f"{questdb_http_addr}/exec", params={"query": query}, timeout=10.0)
data = resp.json()
rows = data.get("dataset", [])
cols = [c["name"] for c in data.get("columns", [])]
return [dict(zip(cols, row)) for row in rows]
```

This is the same pattern as `BaseStrategy._query_questdb` — be consistent.

### Force-subscribe implementation

After reconciliation, for each strategy:
1. Inspect the symbols that have open positions (from QuestDB or exchange REST)
2. Compare against the strategy's registered `_bar_handlers` dict (keys are `(symbol, tf)` tuples)
3. Symbols NOT covered by any registered handler → add to `strategy._managed_positions`
4. Return the complete set of stream keys needed (existing subscriptions + force-subscribe additions) to the caller (main.py or file watcher)

`_managed_positions` is a `set[str]` — just symbol strings, not (symbol, tf) tuples. The BusManager routes ALL event types (BarClose, GapMarker) for those symbols.

The BusManager event routing is currently "all registered strategies receive all events". Force-subscribe only matters for BusManager stream key registration — if the stream key isn't in `_stream_keys`, the BusManager never reads that stream. So the key invariant is: before `BusManager.start()`, ensure every `_managed_positions` symbol has its stream keys added via `add_stream()`.

Stream key pattern (from CLAUDE.md): `candles:close:{exchange}:{symbol}:{tf}` and `candles:ob:{exchange}:{symbol}`. For a managed position symbol, at minimum add the 1s stream: `candles:close:{exchange}:{symbol}:1s`.

### Timeout and degraded-mode path (AC3)

```python
try:
    exchange_orders = await asyncio.wait_for(
        exchange_client.get_open_orders(symbol=""),
        timeout=float(settings.bot_reconciliation_timeout_s),
    )
except asyncio.TimeoutError:
    log.critical("reconciliation_timeout", strategy=strategy_name, timeout_s=...)
    # mark positions as unconfirmed — use QuestDB-only state
    # schedule background retry every 60s
```

For background retry: use `asyncio.create_task()` on the loop AFTER strategies start. The retry coroutine must wrap its body in `try/except Exception` (Epic 12 retro lesson: background tasks silently swallow exceptions).

### restore_open_order call

From `order_worker.py` signature:
```python
def restore_open_order(self, order_id: str, req: OrderRequest, placed: PlacedOrder) -> None:
```

Build `OrderRequest` from QuestDB row fields. `PlacedOrder` needs `order_id`, `client_order_id`, `status="placed"`, `ts_exchange=0` (not available from QuestDB — use 0 as sentinel). Do NOT call `place_order` — that would create a new exchange order.

### DESIGN.md for cross-strategy position isolation (AC5)

Create `bot-service/DESIGN.md` if it does not exist. Add a section "Cross-Strategy Position Concentration" explaining the intentional design: two strategies can both hold the same symbol simultaneously, combined exposure is not enforced at the portfolio level, and why (thread-isolation model, no shared state bus). This is Gap U from the epic implementation notes.

### ILP write review checklist (Epic 12 retro action item)

This story does NOT add new ILP writes beyond what `detect_orphaned_orders` already does. However, if any ILP write is touched, cross-reference every DDL column against the `symbols`/`columns` dicts before marking done.

### asyncio background task discipline (Epic 12 retro action item)

Any `asyncio.create_task()` in this story MUST wrap its body in `try/except Exception` with a named error log (`log.error("reconciliation_retry_failed", ...)`).

## Test Coverage

### L1 unit tests (no IO, mock all external calls)

1. `test_query_questdb_nonterminal_orders_returns_placed_rows` — mock httpx GET, response has `status=placed` rows for strategy → returned in list
2. `test_query_questdb_nonterminal_orders_excludes_terminal` — response has `status=filled` rows → excluded
3. `test_query_questdb_nonterminal_orders_returns_empty_on_http_error` — httpx raises → returns [], logs WARNING
4. `test_reconciliation_populates_open_orders_from_questdb` — QuestDB returns 1 placed order, exchange REST (mocked) also returns that order_id → `restore_open_order` called with correct args
5. `test_reconciliation_force_subscribe_adds_managed_position` — strategy subscribed to ETH only (no BTC bar handler), QuestDB shows BTC open position → `strategy._managed_positions` contains "BTCUSDT"
6. `test_reconciliation_timeout_uses_questdb_only_state` — exchange REST mock hangs for >timeout → logs CRITICAL, uses QuestDB state, marks position as unconfirmed (assert log + restore_open_order still called)
7. `test_reconciliation_no_duplicate_order` — QuestDB shows `status=placed` order_id X, exchange REST returns order_id X as open → `restore_open_order` called once for X; no `place_order` called
8. `test_cross_strategy_isolation` — two strategies each with 1 placed order in QuestDB → each strategy's `restore_open_order` called only for its own order (strategy_name filter)

### L2 integration test (real QuestDB via testcontainers)

The L2 test does NOT need a process kill/restart cycle — the crash is simulated by writing the QuestDB row directly (bypassing the in-memory state), then calling reconciliation fresh:

```python
# Arrange: write a 'placed' row directly to QuestDB (simulating state after crash)
#   Use Sender.from_conf() to write order_events row with status='placed'
# Act: call run_startup_reconciliation() with a mock exchange client
#   that returns the same order_id in get_open_orders()
# Assert: restore_open_order() called with correct order_id + req args
#         no place_order() call on exchange client
```

Use `testcontainers` (already in `requirements-dev.txt` if not, add it). Pattern:
```python
from testcontainers.core.container import DockerContainer
questdb_container = DockerContainer("questdb/questdb:8.2.1")...
```

Check whether `testcontainers` is already available before adding to requirements.

## Senior Developer Review (AI)

**Date:** 2026-05-10
**Outcome:** Approved — all 8 patch findings applied, 2 deferred; 134 tests green, mypy clean

### Review Findings

- [x] [Review][Patch] AC3 background retry not implemented — added `_retry_reconciliation_background` scheduled via `asyncio.create_task()` in degraded mode; loops every 60s with try/except [reconciliation.py]
- [x] [Review][Patch] detect_orphaned_orders exception propagates — wrapped `await detect_orphaned_orders(...)` in try/except Exception with named error log [reconciliation.py]
- [x] [Review][Patch] SQL injection via f-string strategy_name — added `_STRATEGY_NAME_RE` allowlist `[A-Za-z0-9_-]+`; returns None on invalid name [reconciliation.py]
- [x] [Review][Patch] Docstring lists "size" but dict key is "requested_size" — fixed docstring; also added `signal_type` to SELECT [reconciliation.py]
- [x] [Review][Patch] QuestDB query failure silently returns [] — changed return type to `list | None`; None signals failure; caller checks `questdb_failed` and degrades [reconciliation.py]
- [x] [Review][Patch] detect_orphaned_orders makes a second get_open_orders call — added `prefetched_orders` param; `run_startup_reconciliation` passes `exchange_open` directly [reconciliation.py]
- [x] [Review][Patch] order_role hardcoded "entry" — SELECT now includes `signal_type`; `_build_from_questdb_row` uses `row.get("signal_type", "entry")` [reconciliation.py]
- [x] [Review][Patch] paper_trading sets timed_out=True — refactored to separate `paper_trading` and `exchange_failed` flags; paper trading uses `reconciliation_paper_order_restored` log [reconciliation.py]
- [x] [Review][Defer] exchange="" in restored OrderRequest — fill events for restored orders write blank exchange to QuestDB; dev notes acknowledge exchange is unknown at restore time [reconciliation.py:286] — deferred, pre-existing design constraint
- [x] [Review][Defer] ts_exchange=0 sentinel — downstream latency calculations would be nonsensical; dev notes explicitly state 0 as sentinel [reconciliation.py:296] — deferred, pre-existing design constraint

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

### File List

- bot_service/strategy/base.py (UPDATE — add `_managed_positions`)
- bot_service/strategy/reconciliation.py (UPDATE — add `query_questdb_nonterminal_orders`, `run_startup_reconciliation`)
- bot_service/main.py (UPDATE — call reconciliation in lifespan before BusManager.start)
- bot_service/metrics/prometheus.py (UPDATE — add `bot_strategy_restart_total`, `bot_strategy_backoff_seconds`)
- bot-service/DESIGN.md (CREATE — cross-strategy isolation design decision)
- tests/test_reconciliation.py (UPDATE — add 8 L1 tests)
- tests/test_startup_reconciliation.py (CREATE — L2 integration test)
- requirements-dev.txt (UPDATE if testcontainers not present)
