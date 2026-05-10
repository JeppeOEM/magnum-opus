# Epic 13 Retrospective: Strategy Lifecycle & Crash Safety

**Date:** 2026-05-10
**Epic Status:** Done (all 4 stories complete)

## Stories Completed

| Story | Title | Tests Added | Key Outcome |
|-------|-------|-------------|-------------|
| 13.1 | Startup Reconciliation — Exchange REST vs QuestDB | 6 L1 | Crash-recovery state machine: QuestDB → exchange cross-reference → restore open_orders |
| 13.2 | File Watcher & Strategy Auto-Load | 7 L1 + 1 L2 | Hot-reload pipeline: glob → importlib → StrategyHandle → BusManager dynamic registration |
| 13.3 | Watchdog & Exponential Backoff Restart | 7 L1 | Crash detection (stop_event invariant) + backoff 5→10→30→60s capped |
| 13.4 | Heartbeat Emergency Close | 5 L1 | Bus-silence detection + per-symbol daemon thread emergency market-sells |

**Total tests added:** 26 L1, 1 L2 (157 total at epic completion)

---

## What Went Well

### 1. The `stop_event` invariant caught a subtle race immediately
The crash-vs-intentional-stop distinction (Story 13.3) — `not thread.is_alive() and not stop_event.is_set()` — is a non-obvious invariant that could have silently double-restarted intentionally-stopped strategies. Capturing it in AC1 and testing it explicitly (T7.5) prevented this class of bug from ever shipping.

### 2. `_pending_restart` prevented the double-load race before it could bite
The asyncio cooperative scheduling gap between `_loaded.pop` and `_pending_restart.add` was identified during design (not during testing). Because there is no `await` between these two operations, they are effectively atomic — `_rescan` cannot interleave. This reasoning was verified, not assumed.

### 3. Code review caught three High-severity bugs in Story 13.4 before merge
The thread explosion bug (Finding 1) and double market-sell risk (Finding 2) in `_emergency_close` would have been catastrophic in production: sustained bus silence would spawn hundreds of redundant REST market-sell calls for the same position. The paper-trading guard (Finding 3) prevented silent data inconsistency between `_open_positions` and `order_worker.open_orders`. All three were caught by adversarial review before any commit reached main.

### 4. The registry `_load_new` is the correct injection point for all per-strategy config
`strategy._loop`, `strategy._exchange_client`, and `strategy._exchange` are all set in `_load_new` before `_create_handle_and_thread`. This pattern — inject before thread start, thread reads at startup — is clean and race-free. Future stories that need to inject additional dependencies into strategies should follow this pattern.

### 5. Reconciliation as the startup safety net scales correctly
Story 13.1's reconciliation runs on every watchdog restart (not just cold-start). This means crash-recovery is automatic: after any crash + backoff, the new strategy thread gets a fresh QuestDB state snapshot. The 120s timeout + degraded-mode fallback prevents a flaky exchange REST endpoint from blocking strategy restart indefinitely.

---

## What Could Be Improved

### 1. `_open_positions` is frozen at startup — live position tracking is absent
`BaseStrategy._open_positions` is populated at reconciliation and updated only on emergency close success. If a strategy opens a new position mid-session via `order_worker`, `_open_positions` is never updated. This means:
- A new position opened during a session won't be emergency-closed on bus silence
- A position partially closed by normal exit logic will have a stale (too-large) size in `_open_positions`

**Deferred to:** Epic 16 (position tracking metrics). When `bot_position_size` gauge is added, `_open_positions` should be wired to the same data source.

### 2. Watchdog poll interval (5s) is hardcoded
`watch_loop` sleeps 5 seconds between polls — not configurable from `Settings`. For high-frequency strategies where a 5-second detection lag matters, this is a gap. Deferred as a known design limitation (noted in Story 13.3 review findings).

### 3. `bot_strategy_backoff_seconds` gauge is never reset to 0 after successful restart
After a strategy restarts successfully, `bot_strategy_backoff_seconds` still shows the backoff that was used. Operators looking at Grafana cannot distinguish "strategy restarted cleanly, backoff was 30s" from "strategy is currently in 30s backoff." A reset-to-zero on successful restart would close this UX gap.

### 4. The `requested_size` vs `size` field name trap
In Story 13.4, the initial reconciliation code used `row["size"]` to populate `_open_positions`. The actual QuestDB query column is `requested_size`. This would have caused `KeyError` at runtime on any strategy with open positions at startup. Caught during implementation by reading the query column names before writing. **Lesson:** read the actual QuestDB query SELECT list (not just the `_build_from_questdb_row` function) when accessing row fields.

### 5. Test flakiness introduced by daemon thread timing
Story 13.4's initial tests used `time.sleep(0.2s)` to synchronize with daemon threads. Code review (Finding 5) correctly flagged this as unreliable on loaded CI runners. Replaced with `threading.Event.wait(timeout=2.0)`. **Pattern to follow:** daemon thread tests must use event-based synchronization, never fixed sleeps.

### 6. Abstract property enforcement does not apply to test stubs
`bus_timeout_seconds` and `close_on_bus_timeout` are enforced by mypy --strict for production code, but `_minimal_strategy_src` (used in dynamically-loaded test strategies) uses class-level attributes. Python allows class-level attributes to satisfy abstract property constraints at runtime but mypy would catch the difference in production code. Test stubs are a known gap — they work at runtime but wouldn't pass mypy if checked.

---

## Deferred Work Inventory

| Item | Source | Deferral Reason |
|------|---------|----------------|
| `_open_positions` stale after live trading | 13.4 Finding 4 | Epic 16 position tracking |
| Watchdog poll interval hardcoded 5s | 13.3 Finding | No spec requirement for configurability |
| `bot_strategy_backoff_seconds` not reset after successful restart | 13.3 Finding | UX gap, not a correctness issue |
| Old asyncio event loop not closed after crash | 13.3 Finding | ResourceWarning only, daemon thread ownership |
| No explicit reconciliation-before-thread ordering test | 13.3 Finding | Implementation is correct; test would be integration-level |
| `exchange=""` in restored OrderRequest | 13.1 Finding | Design constraint; exchange unknown at restore time |
| `ts_exchange=0` sentinel in restored PlacedOrder | 13.1 Finding | 0 is documented sentinel |
| Sequential crash restart additive delay | 13.3 Finding | Design choice; concurrent restart more complex |

---

## Architecture Patterns Established

1. **Strategy thread lifecycle**: `_load_new` → reconciliation → inject deps → `_create_handle_and_thread` → `run_strategy_event_loop` (subscribe → heartbeat start → event loop)
2. **Crash detection**: `not thread.is_alive() and not stop_event.is_set()` — both conditions required
3. **`_pending_restart` prevents double-load**: set before `await sleep`, cleared in `finally`
4. **Per-strategy daemon threads**: freeze detection (`_heartbeat_loop`), bus silence (`_bus_timeout_loop`), emergency close (per-symbol) — all `daemon=True`
5. **`asyncio.run()` from OS thread**: safe when no event loop exists in that thread; used for emergency REST closes
6. **In-flight tracking lock**: prevents thread explosion when a condition persists across multiple poll cycles
