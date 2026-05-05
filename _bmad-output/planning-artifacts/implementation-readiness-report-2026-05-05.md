---
stepsCompleted: ['step-01-document-discovery', 'step-02-prd-analysis', 'step-03-epic-coverage-validation', 'step-04-ux-alignment', 'step-05-epic-quality-review', 'step-06-final-assessment']
status: complete
assessedBy: 'bmad-check-implementation-readiness'
date: '2026-05-05'
---

# Implementation Readiness Assessment Report

**Date:** 2026-05-05
**Project:** magnum-opus
**Assessor:** BMad Implementation Readiness Workflow

---

## Document Inventory

| Document | File | Status |
|---|---|---|
| PRD | `planning-artifacts/prd.md` | ✅ Complete |
| Architecture | `planning-artifacts/architecture.md` | ✅ Complete |
| Epics & Stories | `planning-artifacts/epics.md` | ✅ Complete |
| UX Design | N/A — headless backend service | N/A |

---

## PRD Analysis

### Functional Requirements

FR1: The service can establish an authenticated WebSocket connection to KuCoin using a token obtained from the KuCoin REST API
FR2: The service can renew its KuCoin WebSocket authentication token automatically before the 24-hour expiry window elapses
FR3: The service can maintain a KuCoin connection heartbeat (ping/pong) at the exchange-required interval to prevent silent connection drops
FR4: The service can distribute Bybit symbol subscriptions across multiple concurrent WebSocket connections to respect the 10-topics-per-connection limit
FR5: The service can verify subscription confirmation for each symbol on each exchange before treating that feed as active
FR6: The service can subscribe to L2 order book and trade event streams for up to 200 symbols per exchange simultaneously
FR7: The service can detect when a WebSocket connection has been dropped by the exchange or the network
FR8: The service can reconnect to an exchange feed after a disconnection and resume data capture without operator intervention
FR9: The service can maintain an in-memory L2 order book per symbol by applying sequential delta updates from the exchange feed
FR10: The service can initialize a symbol's L2 order book from a REST snapshot when the WebSocket feed is first established or after a gap event
FR11: The service can buffer incoming delta messages for a symbol while a REST snapshot request for that symbol is in flight
FR12: The service can merge a received snapshot with its corresponding buffered deltas by replaying only deltas whose sequence numbers are greater than the snapshot's sequence number
FR13: The service can detect that a received snapshot's sequence number is lower than the oldest buffered delta, indicating the merge window has been missed (stale snapshot)
FR14: The service can detect incoming delta messages with sequence numbers at or below the last applied sequence number (out-of-order or duplicate)
FR15: The service can discard out-of-order and duplicate delta messages without applying them to book state
FR16: The service can detect a sequence number gap in a symbol's message stream when an incoming sequence number is non-contiguous with the last applied sequence number
FR17: The service can classify each detected gap as exactly one of four causes: `internal_buffer_overflow`, `internal_merge_error`, `external_disconnect`, or `external_rate_limit`
FR18: The service can emit an in-band gap marker into the same `ticks:{exchange}:{symbol}` Redis Stream as tick data, carrying `gap_cause`, `seq_before`, `seq_after`, and gap timestamp
FR19: The service can write every emitted gap marker to the `gaps:log` Redis Stream regardless of gap cause
FR20: The service can distinguish `internal_*` gap causes (bugs in this service) from `external_*` gap causes (exchange behaviour) in its monitoring output
FR21: The service can publish normalized L2 update events to `ticks:{exchange}:{symbol}` Redis Streams with a stable, versioned schema
FR22: The service can publish normalized trade events to `ticks:{exchange}:{symbol}` Redis Streams alongside L2 updates
FR23: The service can write raw tick data to QuestDB via ILP in batched writes
FR24: The service can detect QuestDB WAL suspension and automatically issue a resume command without operator intervention
FR25: The service can bound Redis Stream memory usage by trimming streams to a maximum entry count
FR26: Downstream consumers can read from `ticks:{exchange}:{symbol}` streams using Redis consumer groups and resume from their last-acknowledged position after a restart
FR27: The operator can query the service's health status including per-exchange connection state, 24-hour gap count, and an overall status value of `ok`, `degraded`, or `critical`
FR28: The operator can query the deployed binary's version, git SHA, and build timestamp
FR29: The operator can scrape a Prometheus metrics endpoint exposing per-symbol tick rates, gap counts by cause, feed connection state, and write latency distributions
FR30: The operator can identify whether the aggregator has been a source of data loss (any `internal_*` gap) versus whether a gap was caused by exchange behaviour (`external_*`) without inspecting logs
FR31: The operator can configure all exchange credentials, connection parameters, and symbol lists via environment variables without modifying source code or compiled binary
FR32: The service can perform a startup validation gate: if all feeds have not confirmed subscriptions within 90 seconds, exit with a non-zero status
FR33: The operator can trigger a graceful shutdown via SIGTERM that drains in-flight tick writes and flushes buffered QuestDB writes before the process exits
FR34: The operator can validate that the running binary on a deployment target matches the intended git SHA via a version endpoint and build tooling
FR35: The service can load exchange API credentials exclusively from environment variables at runtime
FR36: The service can prevent API credentials from appearing in log output, error messages, or panic stack traces

**Total FRs: 36**

### Non-Functional Requirements

NFR1: Tick-to-Redis Stream write latency must be < 10ms at p99 (measured from WebSocket message receipt to Redis XADD completion)
NFR2: Tick-to-QuestDB write latency must be < 500ms at p99 (batched writes; individual ticks need not be written immediately)
NFR3: The service must sustain a minimum of 2,400 ticks/second across 400 simultaneous feeds without dropping messages
NFR4: Steady-state memory footprint must remain below 512 MB RSS with all 400 feeds active
NFR5: Steady-state CPU utilization must remain below 1 vCPU on a Hetzner CPX41 during normal operation
NFR6: The service must automatically detect and begin reconnecting to a dropped feed within 5 seconds of the disconnect event, without operator intervention
NFR7: The service must restore all feed subscriptions within 90 seconds of a clean process restart
NFR8: A process crash must not produce silent data corruption — any tick or gap marker written to Redis Streams or QuestDB before the crash must be a complete, well-formed record
NFR9: QuestDB write failures must not halt feed processing — the service must continue capturing ticks to Redis Streams even when QuestDB writes are failing or retrying
NFR10: The service must operate continuously for ≥30 days without requiring an operator-initiated restart under normal exchange conditions
NFR11: The service must produce zero `internal_*` gap causes under normal operating conditions — any `internal_*` gap event is a defect that requires investigation and is treated as a correctness regression
NFR12: The external gap SLO is ≤1 `external_*` gap per symbol per 24-hour period — sustained exceedance indicates an exchange connectivity problem warranting investigation
NFR13: The snapshot/delta merge must verify sequence overlap before resuming the live feed — an unverified merge is not acceptable
NFR14: No delta message may be applied to L2 book state more than once — any message with sequence number ≤ last applied sequence must be discarded, not processed
NFR15: Exchange API credentials must not appear in log output, error messages, panic stack traces, or Prometheus metric label values at any log level including DEBUG
NFR16: Credentials must be loaded exclusively from environment variables at process startup — hardcoded defaults, config files committed to version control, and binary-embedded credentials are prohibited
NFR17: The `/version`, `/health`, and `/metrics` endpoints must not require authentication (internal network only) — no credentials are exposed via these endpoints
NFR18: The Redis Stream schema for tick messages and gap markers must be backward-compatible across service versions — new fields may be added, but existing field names and types must not change without a schema version increment
NFR19: The Prometheus `/metrics` endpoint must expose all defined metrics at all times, including when feeds are disconnected — a disconnected feed must produce a metric value (e.g. `aggregator_feed_state=0`), not a missing metric
NFR20: The `/health` endpoint must respond within 100ms regardless of exchange feed connection state
NFR21: The service must be testable at five independent layers (L1 pure functions, L2 state machine, L3 mock WebSocket, L4 fault injection, L5 live exchange) without modifying production code paths
NFR22: The four injectable test seams (MockWSServer, FakeRedis/Toxiproxy, FakeQuestDB, MockClock) must be substitutable via interfaces or constructor injection — the production code must not reference test implementations directly

**Total NFRs: 22**

### Additional Requirements (Architecture-Derived)

- **ARC1:** Go module initialization with pinned, maintained dependencies (nhooyr.io/websocket over archived gorilla/websocket)
- **ARC2:** `.env.example` documenting all env vars must precede `internal/config/` implementation
- **ARC3:** `raw_ticks` QuestDB DDL committed before any `writer/questdb/` implementation code
- **ARC4:** Implementation sequence is strictly ordered (config → orderbook → exchange → writers → coordinator → metrics → main → infra)
- **ARC5:** Five-layer test architecture enforced via Go build tags (L1 no tag through L5 `live`)
- **ARC6:** GitHub Actions CI/CD — L1+L2 on push/PR; image push to ghcr.io on tag
- **ARC7:** Docker Compose on Linode with defined resource limits (aggregator 600m/2.0 CPUs)
- **ARC8:** Linode VM snapshot before each deploy for 5-minute rollback capability
- **ARC9:** Credential-sanitizing slog.Handler initialized first in main.go before any other logging
- **ARC10:** Multi-stage Dockerfile (build + minimal runtime stage)
- **ARC11:** All external dependencies must be actively maintained at adoption time; verified via `make check-deps`

### PRD Completeness Assessment

The PRD is **exceptionally complete** for a greenfield infrastructure service. All 36 FRs are numbered, unambiguous, and independently testable. All 22 NFRs carry specific measurable thresholds (p99 latencies, memory bounds, uptime targets). The error classification table and startup/shutdown sequence are detailed to the implementation level. No ambiguous or hand-wavy requirements detected.

---

## Epic Coverage Validation

### Coverage Matrix

| FR | PRD Requirement (abbreviated) | Epic Coverage | Story | Status |
|---|---|---|---|---|
| FR1 | KuCoin authenticated WebSocket via REST token | Epic 2 | Story 2.2 | ✓ Covered |
| FR2 | KuCoin token auto-renewal before 24h expiry | Epic 2 | Story 2.3 | ✓ Covered |
| FR3 | KuCoin ping/pong heartbeat | Epic 2 | Story 2.2 | ✓ Covered |
| FR4 | Bybit 10-topics/connection distribution | Epic 2 | Story 2.4 | ✓ Covered |
| FR5 | Subscription confirmation per symbol | Epic 2 | Stories 2.2, 2.4, 2.5 | ✓ Covered |
| FR6 | Subscribe to 200 symbols per exchange | Epic 2 | Stories 2.2, 2.5 | ✓ Covered |
| FR7 | Detect WebSocket connection drop | Epic 2 | Story 2.1 | ✓ Covered |
| FR8 | Reconnect and resume without intervention | Epic 2 | Stories 2.2, 2.5 | ✓ Covered |
| FR9 | In-memory L2 order book via sequential deltas | Epic 1 | Story 1.4 | ✓ Covered |
| FR10 | Initialize L2 book from REST snapshot | Epic 1 | Story 1.5 | ✓ Covered |
| FR11 | Buffer deltas during snapshot fetch | Epic 1 | Story 1.5 | ✓ Covered |
| FR12 | Merge snapshot + buffered deltas (seq > snapshot.seq) | Epic 1 | Story 1.5 | ✓ Covered |
| FR13 | Detect stale snapshot (snapshot.seq < oldest buffered delta) | Epic 1 | Story 1.5 | ✓ Covered |
| FR14 | Detect out-of-order/duplicate deltas (seq ≤ last applied) | Epic 1 | Story 1.4 | ✓ Covered |
| FR15 | Discard out-of-order/duplicate deltas silently | Epic 1 | Story 1.4 | ✓ Covered |
| FR16 | Detect sequence number gap | Epic 1 | Story 1.6 | ✓ Covered |
| FR17 | Classify gap into exactly one of four causes | Epic 1 | Story 1.6 | ✓ Covered |
| FR18 | Emit in-band gap marker to `ticks:{exchange}:{symbol}` | Epic 3 | Story 3.2 | ✓ Covered |
| FR19 | Write every gap marker to `gaps:log` stream | Epic 3 | Story 3.2 | ✓ Covered |
| FR20 | Distinguish internal_* vs external_* in monitoring | Epic 4 | Story 4.2 | ✓ Covered |
| FR21 | Publish normalized L2 updates to Redis Streams | Epic 3 | Story 3.2 | ✓ Covered |
| FR22 | Publish normalized trade events to Redis Streams | Epic 3 | Story 3.2 | ✓ Covered |
| FR23 | Write raw ticks to QuestDB via ILP batched writes | Epic 3 | Story 3.3 | ✓ Covered |
| FR24 | Detect QuestDB WAL suspension and auto-resume | Epic 3 | Story 3.3 | ✓ Covered |
| FR25 | Bound Redis Stream memory via MAXLEN trimming | Epic 3 | Story 3.2 | ✓ Covered |
| FR26 | Downstream consumer group reads + resume after restart | Epic 3 | Story 3.2 | ✓ Covered |
| FR27 | /health endpoint (ok/degraded/critical, gap count) | Epic 4 | Story 4.2 | ✓ Covered |
| FR28 | /version endpoint (version, git SHA, build time) | Epic 4 | Story 4.2 | ✓ Covered |
| FR29 | /metrics Prometheus endpoint | Epic 4 | Stories 4.1, 4.2 | ✓ Covered |
| FR30 | Internal vs external gap distinguishable without log inspection | Epic 4 | Story 4.2 | ✓ Covered |
| FR31 | All config via environment variables | Epic 1 | Story 1.2 | ✓ Covered |
| FR32 | Startup validation gate (exit non-zero if not ready in 90s) | Epic 4 | Story 4.3 | ✓ Covered |
| FR33 | Graceful SIGTERM shutdown with drain | Epic 4 | Story 4.3 | ✓ Covered |
| FR34 | Binary version verification via /version and build tooling | Epic 4 | Stories 4.2, 4.5 | ✓ Covered |
| FR35 | Credentials from env vars only | Epic 1 | Story 1.2 | ✓ Covered |
| FR36 | Credentials never in logs/errors/panics | Epic 1 | Stories 1.2, 4.3 | ✓ Covered |

### Missing Requirements

**None.** All 36 FRs have traceable implementation paths in the epics.

### NFR Coverage Check

| NFR | Category | Epic | Story | Status |
|---|---|---|---|---|
| NFR1 | <10ms tick-to-Redis p99 | Epic 3 | Story 3.4 | ✓ Covered |
| NFR2 | <500ms QuestDB p99 | Epic 3 | Story 3.3 | ✓ Covered |
| NFR3 | ≥2400 ticks/sec sustained | Epic 3 | Story 3.4 | ✓ Covered |
| NFR4 | <512 MB RSS | Epic 3 | Story 3.4 | ✓ Covered |
| NFR5 | <1 vCPU steady-state | Epic 4 | Story 4.4 | ✓ Covered |
| NFR6 | ≤5s reconnect detection | Epic 2 | Story 2.1 | ✓ Covered |
| NFR7 | 90s full startup | Epic 2 | Stories 2.2, 2.5 | ✓ Covered |
| NFR8 | Crash-safe writes (no partial records) | Epic 3 | Story 3.3 | ✓ Covered |
| NFR9 | QuestDB failure doesn't halt capture | Epic 3 | Stories 3.3, 3.4 | ✓ Covered |
| NFR10 | ≥30 days unattended operation | Epic 4 | Story 4.6 | ✓ Covered |
| NFR11 | Zero internal_* gaps under normal conditions | Epic 1 | Story 1.6 | ✓ Covered |
| NFR12 | External gap SLO ≤1/symbol/24h | Epic 1 | Story 1.6 | ✓ Covered |
| NFR13 | Sequence overlap verification before resuming | Epic 1 | Story 1.5 | ✓ Covered |
| NFR14 | No delta applied more than once | Epic 1 | Story 1.4 | ✓ Covered |
| NFR15 | Credentials not in logs at any level | Epic 1 | Stories 1.2, 1.7, 4.3 | ✓ Covered |
| NFR16 | Env-var-only credential loading | Epic 1 | Story 1.2 | ✓ Covered |
| NFR17 | Endpoints require no authentication | Epic 4 | Story 4.2 | ✓ Covered |
| NFR18 | Backward-compatible Redis Stream schema | Epic 3 | Story 3.1 | ✓ Covered |
| NFR19 | All metrics present when feeds disconnected | Epic 4 | Story 4.1 | ✓ Covered |
| NFR20 | /health responds within 100ms | Epic 4 | Story 4.2 | ✓ Covered |
| NFR21 | Five-layer testability (L1–L5) | Epic 1 | Story 1.7 | ✓ Covered |
| NFR22 | Injectable seams via interfaces/constructor injection | Epic 1 | Stories 1.7, 2.1 | ✓ Covered |

### Coverage Statistics

- Total PRD FRs: **36**
- FRs covered in epics: **36**
- Coverage percentage: **100%**
- Total PRD NFRs: **22**
- NFRs covered in epics: **22**
- NFR coverage: **100%**

---

## UX Alignment Assessment

### UX Document Status

**Not Found — N/A by design.**

The magnum-opus aggregator is a headless backend daemon with no user interface. The PRD explicitly states: "Project Type: Backend infrastructure data pipeline" with no UI components. The only operator-facing surfaces are:
- HTTP operational endpoints (`/health`, `/version`, `/metrics`) — machine-readable JSON/text for monitoring systems
- Logging output — structured slog output for log aggregation
- Docker Compose + Makefile — operator tooling, not a UI

### Alignment Issues

None. The absence of a UX document is correct and expected.

### Warnings

None. No UX is implied anywhere in the PRD, Architecture, or Epics. The four user journeys in the PRD describe operator workflows (monitoring, incident response, deployment, Candle Service integration) — these are documented in `docs/ops.md` per Story 4.4 and Story 4.6, not in a UX document.

---

## Epic Quality Review

### Epic 1: Project Foundation & Correctness Engine

#### User Value Assessment

⚠️ **Minor Observation:** The epic title "Project Foundation & Correctness Engine" has a technical infrastructure flavor. However, the framing is justified for this domain:

- The value statement is: *"the developer has a compiling Go module, full test infrastructure, and the three pure state machines proven exhaustively correct before any IO is written"*
- For a correctness-first data capture service, the state machines (orderbook, reconnect, gapdetector) ARE the core product behavior — not scaffolding. Their correctness directly determines whether the downstream Candle Service produces valid signals.
- The epic cannot be split without violating the architecture's implementation sequence constraint (ARC4).
- This is an acceptable pattern for greenfield infrastructure services where the correctness engine is the primary product artifact.

**Verdict:** Acceptable for this domain. Not a violation.

#### Epic Independence

✅ Epic 1 stands completely alone — no external dependencies. All 7 stories build on each other within the epic.

#### Greenfield Setup Check

✅ Story 1.1 is "Go Module Initialization & Dependency Verification" — correct greenfield initialization story at position 1.1.
✅ Story 1.7 establishes the Makefile and test infrastructure.

#### Story Dependency Analysis

Within-epic sequential dependencies (all acceptable):
- Story 1.2 → builds on 1.1 (module exists)
- Story 1.3 → builds on 1.1
- Story 1.4 → builds on 1.1
- Story 1.5 → builds on 1.4 (uses OrderBook state)
- Story 1.6 → builds on 1.1
- Story 1.7 → validates and enforces patterns from 1.2–1.6

⚠️ **Minor Concern:** Story 1.1 AC states: "`make check-deps` (implemented in Story 1.7) is listed in the DEPS.md as the re-verification command." This is a documentation forward reference (not an implementation dependency) — Story 1.1 is fully completable without Story 1.7 being done. The DEPS.md entry is documentation of intent, not a code dependency. Story 1.1 can be marked complete when the module is initialized and deps are pinned; the `check-deps` target itself is Story 1.7's responsibility.

**Remediation:** None required — the AC is clear that it's a documentation reference, not a blocking dependency.

#### AC Quality Check

✅ All ACs use Given/When/Then format
✅ All ACs include error conditions (missing env vars, discard cases, stale snapshots)
✅ Story 1.4 explicitly distinguishes duplicate vs out-of-order as separate test rows — edge case coverage is specific
✅ Story 1.5 covers the double-NeedsSnapshot edge case and sequence rollover boundary
✅ Story 1.6 specifies documentation of the `external_rate_limit` vs `external_disconnect` heuristic in code comments
✅ Story 1.7 specifies the testutil/ split (pure L1 helpers separate from L2 mock/ fakes) and the `//go:build l2` boundary

**Best Practices Compliance:**
- [x] Epic delivers user value (correctness guarantee)
- [x] Epic can function independently
- [x] Stories appropriately sized (each is 1–3 days of work)
- [x] No cross-epic forward dependencies
- [x] No premature database/schema creation
- [x] Clear acceptance criteria
- [x] Traceability to FRs maintained

---

### Epic 2: Exchange Feed Connectivity

#### User Value Assessment

✅ Clear operator value: *"The operator can connect to KuCoin and Bybit, verify subscription confirmation per symbol, and have automatic reconnect trigger on disconnect."* This is the first time real exchange data flows — concrete user-visible outcome.

#### Epic Independence

✅ Epic 2 depends only on Epic 1 output (pure state machines + test infrastructure). No dependency on Epic 3 or Epic 4.

#### Story Dependency Analysis

Within-epic sequential dependencies (all acceptable):
- Story 2.1 → transport layer (prerequisite for all exchange adapters)
- Story 2.2 → KuCoin adapter (depends on 2.1 transport)
- Story 2.3 → KuCoin token renewal (depends on 2.2 adapter existing)
- Story 2.4 → Bybit mux (depends on 2.1 transport, can parallel with 2.2)
- Story 2.5 → Bybit adapter (depends on 2.4 mux)

No cross-epic forward dependencies detected. ✅

#### Notable AC Strengths

✅ Story 2.1 specifies the reconnect channel must be **buffered** (minimum buffer 1) with rationale — prevents silent event drop on busy receiver
✅ Story 2.2 handles KuCoin batch subscription confirmations correctly — a batch confirmation does not mark unconfirmed symbols active
✅ Story 2.2 specifies the 200-symbol fixture test to catch off-by-one at scale
✅ Story 2.4 covers the concurrent-failure scenario (two connections drop simultaneously) as an explicit L2 test case — this was identified as a gap during advanced elicitation
✅ Story 2.5 specifies `time.NewTicker` (not cumulative sleep) for ping intervals under scheduler load — subtle correctness detail

**Best Practices Compliance:**
- [x] Epic delivers user value
- [x] Epic can function independently (with Epic 1)
- [x] Stories appropriately sized
- [x] No forward dependencies
- [x] Clear acceptance criteria with edge cases
- [x] Traceability to FRs maintained

---

### Epic 3: Data Capture Pipeline & Gap Guarantees

#### User Value Assessment

✅ Clear consumer value: *"The Candle Service can read a complete Redis Stream with normalized ticks and in-band gap markers at the correct sequence."* This is the primary downstream integration milestone — the service becomes useful to its actual consumer.

#### Epic Independence

✅ Epic 3 depends on Epics 1 and 2 (state machines + exchange feeds). No dependency on Epic 4.

#### Story Dependency Analysis

Within-epic sequential dependencies (all acceptable):
- Story 3.1 → interfaces and schema (prerequisite for all writers)
- Story 3.2 → Redis Stream writer (depends on 3.1 interface)
- Story 3.3 → QuestDB writer (depends on 3.1 interface, can parallel with 3.2)
- Story 3.4 → per-symbol coordinator (depends on 3.1, 3.2, 3.3)
- Story 3.5 → full coordinator orchestration (depends on all of 3.1–3.4)

No cross-epic forward dependencies detected. ✅

#### Database/Schema Creation Timing

✅ Story 3.1 establishes the QuestDB `raw_ticks` DDL and Redis schema as the first story in Epic 3 — before any writer implementation. ARC3 compliance confirmed.
✅ DDL stored in `writer/questdb/schema.sql`, applied idempotently via `CREATE TABLE IF NOT EXISTS`.

#### Notable AC Strengths

✅ Story 3.2 specifies the duplicate gap marker contract explicitly — aggregator may emit duplicates on retry; Candle Service is the deduplication boundary. This prevents an implementation ambiguity that could cause incorrect behavior.
✅ Story 3.2 covers trade events (`event_type: "trade"`) as a distinct L2 test case, not bundled with update events.
✅ Story 3.3 addresses the QuestDB WAL crash data loss problem via a `questdb:flush:pending` audit trail in Redis — identified during pre-mortem analysis.
✅ Story 3.4 specifies the exact SIGTERM data loss bound (5s drain window, documented as acceptable) rather than claiming zero-loss guarantees.
✅ Story 3.5 covers the snapshot-fetch-during-shutdown edge case: snapshot context cancelled immediately, graceful exit within 15s `stop_grace_period`.

**Best Practices Compliance:**
- [x] Epic delivers user value (consumer-facing stream ready)
- [x] Epic can function independently (with Epics 1 and 2)
- [x] Stories appropriately sized
- [x] No forward dependencies
- [x] Schema created at first use (Story 3.1 before any writer)
- [x] Clear acceptance criteria
- [x] Traceability to FRs maintained

---

### Epic 4: Operational Readiness & Deployment

#### User Value Assessment

✅ Clear operator value: *"The operator can monitor the service, deploy reproducibly, validate the running binary, pass a 48-hour canary gate, and roll back in 5 minutes."* This is the full production-ready milestone.

#### Epic Independence

✅ Epic 4 is the integration epic — correctly depends on Epics 1, 2, and 3. No circular dependencies.

#### Story Dependency Analysis

Within-epic sequential dependencies (all acceptable):
- Story 4.1 → Prometheus metrics registry (prerequisite for HTTP wiring)
- Story 4.2 → HTTP endpoints (depends on 4.1 registry)
- Story 4.3 → composition root (depends on 4.1, 4.2; wires all components)
- Story 4.4 → Dockerfile + Docker Compose (depends on 4.3 binary existing)
- Story 4.5 → CI/CD pipeline (depends on 4.4 Dockerfile)
- Story 4.6 → live tests + canary gate (depends on 4.4/4.5 deployment)

No cross-epic forward dependencies detected. ✅

#### Notable AC Strengths

✅ Story 4.1 specifies pre-initialization of all metrics to zero for every (exchange, symbol) pair before any feed connects — prevents missing series on first scrape (NFR19 compliance).
✅ Story 4.2 specifies the `gap_count_24h` rolling window implementation as 24×1h buckets — explicit about it being non-cumulative and surviving restarts via `gaps:log` replay. Identified during advanced elicitation.
✅ Story 4.3 verifies the credential-sanitizing handler is active in test binaries as well as the production binary — closes the gap from Epics 1–3 where only the testutil stub was active.
✅ Story 4.5 specifies the `release.yml` `workflow_run` trigger so a broken commit cannot be tagged and published — identified during pre-mortem analysis.
✅ Story 4.6 documents specific canary gate pass criteria (zero `internal_*` gaps, external rate ≤1/symbol/24h, status never `critical`) before implementation begins.

**Best Practices Compliance:**
- [x] Epic delivers user value (operational readiness)
- [x] Epic can function independently (integration layer)
- [x] Stories appropriately sized
- [x] No forward dependencies
- [x] Clear acceptance criteria with measurable outcomes
- [x] Traceability to FRs maintained

---

### Best Practices Compliance Summary

| Check | Epic 1 | Epic 2 | Epic 3 | Epic 4 |
|---|---|---|---|---|
| Epic delivers user value | ⚠️ Acceptable | ✅ | ✅ | ✅ |
| Epic can function independently | ✅ | ✅ | ✅ | ✅ |
| Stories appropriately sized | ✅ | ✅ | ✅ | ✅ |
| No cross-epic forward dependencies | ✅ | ✅ | ✅ | ✅ |
| Database/schema created at first use | ✅ | N/A | ✅ | N/A |
| Clear, specific acceptance criteria | ✅ | ✅ | ✅ | ✅ |
| Error conditions covered in ACs | ✅ | ✅ | ✅ | ✅ |
| Traceability to FRs maintained | ✅ | ✅ | ✅ | ✅ |
| Greenfield setup at Epic 1 Story 1 | ✅ | N/A | N/A | N/A |

---

## Summary and Recommendations

### Overall Readiness Status

## ✅ READY FOR IMPLEMENTATION

### Findings Summary

| Severity | Count | Description |
|---|---|---|
| 🔴 Critical | 0 | No critical violations |
| 🟠 Major | 0 | No major issues |
| 🟡 Minor | 2 | Observations, not blockers |

### Minor Observations

**Observation 1: Epic 1 title has infrastructure flavor**
- Epic 1 is titled "Project Foundation & Correctness Engine" — sounds like a technical milestone
- **Assessment:** Acceptable for this domain. The pure state machines (orderbook, reconnect, gapdetector) are the primary correctness artifacts, not scaffolding. Their exhaustive L1 verification is the load-bearing guarantee for the entire system.
- **Action:** None required.

**Observation 2: Story 1.1 documentation forward reference to Story 1.7**
- Story 1.1 AC mentions "`make check-deps` (implemented in Story 1.7) is listed in DEPS.md"
- **Assessment:** This is a documentation intent note, not an implementation dependency. Story 1.1 is completable without Story 1.7 being done.
- **Action:** None required. The AC is clear about the relationship.

### Confidence Indicators

The planning artifacts for this project are exceptionally well-prepared:

1. **Requirement quality:** Every FR is independently testable with a specific, binary observable outcome. No vague requirements detected.
2. **AC specificity:** Stories contain precise edge case coverage that reflects domain expertise — double-NeedsSnapshot handling, stale snapshot detection, sequence rollover, concurrent mux failure, QuestDB WAL crash detection, rolling 24h windows surviving restart.
3. **Dependency chain integrity:** The Epic 1 → 2 → 3 → 4 sequence is clean with no circular or cross-epic forward dependencies.
4. **Test architecture:** The 5-layer test architecture (L1–L5) with explicit build tags, infrastructure split (testutil/ vs testutil/mock/), and coverage gates provides strong correctness guarantees at each layer.
5. **Pre-mortem hardening:** Three advanced elicitation rounds (Failure Mode Analysis, Self-Consistency Validation, Pre-mortem Analysis) identified and addressed 6 non-obvious gaps — the stories reflect this depth.
6. **Security by design:** Credential isolation is multi-layered (sealed type + slog handler stub in Epics 1–3 + production handler in Epic 4) and verified by tests at each layer.

### Recommended Next Steps

1. **Begin implementation with Story 1.1** — Go module initialization. Follow the implementation sequence defined in ARC4 exactly.
2. **Do not reorder epics** — the Epic 1 → 2 → 3 → 4 sequence enforces correctness guarantees before IO is introduced. Pure state machines (Epic 1) must be fully tested before exchange adapters (Epic 2) are built.
3. **Run L1 tests before starting Epic 2** — `make test-l1` with 95% branch coverage gate must pass for `orderbook/`, `reconnect/`, and `gapdetector/` before any exchange code is written.
4. **Write `raw_ticks` DDL as the first artifact in Epic 3** — Story 3.1 must precede all writer implementations to prevent schema drift.
5. **Document canary gate criteria in `docs/ops.md` before Epic 4 implementation begins** — Story 4.6 AC requires this.

### Final Note

This assessment found **2 minor observations** across 1 category (epic structure). Both are acceptable for this domain and require no remediation. The planning artifacts are complete, internally consistent, and ready for a developer to begin implementation immediately.

The project context document (`_bmad-output/project-context.md`) provides additional implementation rules that complement the epics. The developer agent should read both `project-context.md` and this epics document before writing any code.

---

*Assessment completed: 2026-05-05*
*Workflow: bmad-check-implementation-readiness*
*Status: READY FOR IMPLEMENTATION*
