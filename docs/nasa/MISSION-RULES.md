# MISSION RULES — magnum-opus

Pre-decided rules for the most likely operational scenarios. These are decisions made
in advance, when thinking clearly, so operators do not need to improvise under pressure.

Inspired by NASA's mission rule documents: "a rule is a decision already made."

---

## MR-01: Startup failure exits immediately, not silently

**Rule:** If the aggregator or candle-service fails its startup gate (feeds not live,
migrations failed, config invalid), the process **exits with code 1**. It does not
continue in a degraded state.

**Rationale:** A silent partial start is worse than a hard crash. If the aggregator
starts but one exchange fails, the candle-service and bot-service will produce bars
with half the data — corrupting strategy decisions silently.

**Implementation:** `runStartupGate` returns error → `os.Exit(1)`.
STARTUP_TIMEOUT_SEC defaults to 90 seconds. Increase if exchange connectivity is slow.

**Exception:** SIGTERM during the startup gate is treated as a clean exit (not failure).
This allows graceful rollouts: old container can be killed during new container startup.

---

## MR-02: On bus timeout, strategies must close positions or log and do nothing

**Rule:** When the bot-service strategy has not received any events for `bus_timeout_seconds`,
it MUST either:
1. Close all open positions (set `close_on_bus_timeout=True`), or
2. Do nothing but log the timeout (`close_on_bus_timeout=False`)

**No third option.** A strategy that "tries to close but fails silently" is forbidden.

**Rationale:** A silent position during a bus timeout is an unmanaged risk. The pre-decision
is: either the strategy is safe to hold (False) or it isn't (True). Make this decision at
strategy design time, not at 3am during an outage.

**Implementation:** `BaseStrategy._on_bus_timeout` → `_emergency_close_symbol`.
Emergency close loops until success (no timeout). Uses daemon thread — won't block shutdown.

---

## MR-03: Never ignore an internal gap cause

**Rule:** Any log message with `cause=internal_merge_error` or `cause=internal_buffer_overflow`
in `aggregator_gap_total` must be investigated. Do not treat as noise.

**Rationale:** External gaps (`external_disconnect`, `external_rate_limit`) are exchange
behavior. Internal gaps are bugs in this service. Even one internal_merge_error means the
orderbook was corrupt for that symbol during the gap window.

**Operational threshold:** `increase(aggregator_gap_total{cause=~"internal_.*"}[1h]) > 0` → alert.

---

## MR-04: Blue-green deploy only promotes when shadow lag < 100

**Rule:** The incoming candle-service slot must have a shadow lag of less than 100 messages
before `POST /promote` is called.

**Rationale:** A slot promoted with high lag will write bars built on stale book state.
These bars have `gap_count=0` (no gap marker was received) but are computed from incomplete
tick history. They are the worst kind of data corruption: silent.

**Implementation:** `scripts/deploy-candle.sh` polls `shadow_lag` from the `/healthz` endpoint.
If lag does not reach < 100 within 5 minutes, the deploy is aborted.

---

## MR-05: Data loss on XACK-before-dispatch is acceptable

**Rule:** The candle-service consumer ACKs before dispatching. A process crash between
ACK and dispatch means that tick is permanently lost. This is the accepted design.

**Rationale:** The alternative (ACK after dispatch) would cause double-count on restart
within the same second window. The dedup set only covers one second. Double-counted ticks
would produce bars with inflated trade counts, volumes, and OFI — silent corruption worse
than a single lost tick.

**Mitigation:** XAUTOCLAIM on promotion recovers messages the *old* consumer had in its
PEL. New consumer starts from a clean accumulator. One partial bar may be incomplete;
this is reflected in `gap_count`.

---

## MR-06: Never modify the QuestDB ILP write path to add acknowledgment

**Rule:** QuestDB ILP writes are fire-and-forget. Do not add retry-with-backpressure
to the hot ILP path. Errors are logged but not propagated.

**Rationale:** ILP batching at 1ms flushes means the aggregator and candle-service can
sustain high tick rates without blocking on database writes. Adding blocking writes would
directly limit tick throughput and create backpressure that propagates to the exchange WebSocket.

**Alternative:** For data quality concerns, query QuestDB to verify rows exist after a
period — don't instrument the write path.

---

## MR-07: Emergency positions are the responsibility of the last strategy that opened them

**Rule:** When a strategy is stopped (process exit, hot-reload removal), any open positions
it held are flagged as "unmanaged" in the next startup's reconciliation. The operator must
manually decide to: (a) close them via exchange UI, or (b) add the position to the new
strategy's managed_positions list.

**Rationale:** Automatic forced-close on strategy removal risks closing a position that the
operator intentionally wants to hold. The reconciliation system surfaces the decision; it
does not make it.

**Implementation:** `reconciliation.py` flags unmanaged positions with `log.critical("reconciliation_critical", ...)`.

---

## MR-08: QuestDB WAL suspension is auto-recovered; manual intervention only for sustained suspension

**Rule:** The aggregator's QuestDB writer automatically issues `RESUME WAL` when it detects
WAL suspension. If WAL suspension repeats more than 3 times in an hour, do not continue
auto-resuming — investigate QuestDB memory and disk.

**Rationale:** Auto-resume is safe for transient OOM events (e.g., large query ran during peak).
Repeated suspension indicates systemic resource exhaustion; continuing to resume masks the
underlying problem and risks data corruption.

**Log signal:** `"questdb: WAL suspended — issuing RESUME WAL"` appears once per suspension.
Count occurrences in Loki to detect repeat events.

---

## MR-09: CI must pass before any merge to main

**Rule:** No code is merged to `main` without all CI checks passing:
- `go test ./...` for both aggregator and candle-service
- `pytest` for bot-service
- `scripts/audit-docs.sh`
- Build artifacts compile cleanly

**Rationale:** A broken aggregator in production causes all candle bars to have `gap_count > 0`
until manually restarted. A broken bot-service strategy may have open positions.

**Exception:** Documentation-only changes may skip `go test` and `pytest` if no `.go` or `.py`
files are modified. `audit-docs.sh` still runs.

---

## MR-10: Credentials must never appear in any structured log

**Rule:** API keys, secrets, and passphrases must NEVER appear in any log line, even in
debug mode.

**Implementation:** Aggregator uses `Credential` type with `Format` override. Bot-service
uses `pydantic.SecretStr` with `redact_credentials` structlog processor.

**Verification:** `grep -r 'api_key\|api_secret\|passphrase' logs/ | grep -v REDACTED`
should return empty. Run this after any new log statement that might include credentials.

**In case of leak:** Rotate credentials immediately. Treat any logs that reached Loki as
potentially indexed — assume exposure and act accordingly.
