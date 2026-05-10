# Bot Service — Design Decisions

This document records intentional architectural choices that are not obvious from the code
and would otherwise be re-litigated by future implementers.

---

## Cross-Strategy Position Concentration

**Decision:** Two independent strategies are allowed to hold the same symbol simultaneously
with no portfolio-level position cap enforcement.

**Rationale:** Strategies run in isolated threads with no shared mutable state. Enforcing
cross-strategy position limits would require a shared state bus, introducing:
- Shared-memory locking between strategy threads
- A coordination layer that couples otherwise-independent signal computations
- Additional failure modes (one strategy blocks another on lock contention)

This coupling is inconsistent with the thread-isolation model that makes individual
strategy crashes self-contained (Story 13.3 watchdog).

**Implication:** If two strategies each have `max_position_pct = 0.10` and both hold
BTCUSDT longs, the combined exposure is 20% of portfolio value with no system-level
enforcement. mrqdt accepts this concentration risk as an explicit design choice.

**Do NOT add cross-strategy caps** unless the thread-isolation model is replaced with a
shared event loop and a central position ledger. The correct place for portfolio-level
limits is an external risk service — not within the bot-service process.

[Source: Epic 13 implementation notes, Gap U]

---

## Reconciliation Sequence on Startup

**Decision:** Startup reconciliation runs before strategy threads start and before
BusManager begins routing events.

**Rationale:** Ensures `OrderQueueWorker.open_orders` is fully populated before any
signal handler can fire. A strategy that acts on a BarClose before reconciliation
completes would not know about existing positions and could double-enter.

**Sequence (mandatory):**
1. `run_startup_reconciliation()` for each strategy
2. `BusManager.start()`
3. File watcher spawns strategy threads

[Source: Story 13.1 AC1, Epic 13 implementation notes]
