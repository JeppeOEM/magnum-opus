---
stepsCompleted: ['step-01-document-discovery', 'step-02-prd-analysis', 'step-03-epic-coverage-validation', 'step-04-ux-alignment', 'step-05-epic-quality-review', 'step-06-final-assessment']
status: complete
date: '2026-05-08'
project: magnum-opus
documents:
  prd: '_bmad-output/planning-artifacts/prd.md'
  architecture: '_bmad-output/planning-artifacts/architecture.md'
  epics: '_bmad-output/planning-artifacts/epics.md'
  ux: null
---

# Implementation Readiness Assessment Report

**Date:** 2026-05-08
**Project:** magnum-opus

---

## PRD Analysis

### Functional Requirements (36 total)

**Exchange Feed Connectivity (FR1–FR8)**
- FR1: Authenticated WebSocket connection to KuCoin via REST token
- FR2: KuCoin token auto-renewal before 24h expiry
- FR3: KuCoin ping/pong heartbeat at exchange-required interval
- FR4: Bybit subscription distribution across multiple connections (10-topic limit)
- FR5: Subscription confirmation verification per symbol per exchange
- FR6: Subscribe to L2 order book + trade streams for up to 200 symbols per exchange
- FR7: Detect dropped WebSocket connection
- FR8: Reconnect and resume data capture without operator intervention

**Order Book State Management (FR9–FR15)**
- FR9: Maintain in-memory L2 order book per symbol via sequential delta updates
- FR10: Initialize L2 book from REST snapshot on first connect or after gap event
- FR11: Buffer incoming deltas while REST snapshot is in-flight
- FR12: Merge snapshot with buffered deltas (replay only deltas with seq > snapshot seq)
- FR13: Detect stale snapshot (snapshot seq < oldest buffered delta)
- FR14: Detect out-of-order or duplicate delta messages (seq ≤ last applied)
- FR15: Discard out-of-order and duplicate delta messages

**Gap Detection & Classification (FR16–FR20)**
- FR16: Detect sequence number gap when incoming seq is non-contiguous
- FR17: Classify each gap as exactly one of four causes: internal_buffer_overflow, internal_merge_error, external_disconnect, external_rate_limit
- FR18: Emit in-band gap marker to ticks:{exchange}:{symbol} stream with cause, seq_before, seq_after, timestamp
- FR19: Write every gap marker to gaps:log stream regardless of cause
- FR20: Distinguish internal_* from external_* gap causes in monitoring output

**Data Output (FR21–FR26)**
- FR21: Publish normalized L2 update events to ticks:{exchange}:{symbol} with stable, versioned schema
- FR22: Publish normalized trade events to ticks:{exchange}:{symbol}
- FR23: Write raw tick data to QuestDB via ILP in batched writes
- FR24: Detect QuestDB WAL suspension and auto-resume
- FR25: Trim Redis Streams to maximum entry count (MAXLEN)
- FR26: Downstream consumers can resume from last-ack'd position after restart

**Operational Observability (FR27–FR30)**
- FR27: /health endpoint with per-exchange connection state, 24h gap count, ok/degraded/critical status
- FR28: /version endpoint with git SHA and build timestamp
- FR29: /metrics Prometheus endpoint (tick rates, gap counts by cause, feed state, write latency)
- FR30: Distinguish internal_* vs external_* gap causes without inspecting logs

**Service Lifecycle & Configuration (FR31–FR34)**
- FR31: All configuration via environment variables
- FR32: Startup validation gate — exit non-zero if all feeds not confirmed within 90s
- FR33: Graceful SIGTERM shutdown draining in-flight writes
- FR34: Validate deployed binary matches intended git SHA via version endpoint

**Credential Security (FR35–FR36)**
- FR35: Load credentials exclusively from environment variables
- FR36: Credentials must not appear in logs, errors, or panic stack traces

### Non-Functional Requirements (22 total)

**Performance**
- NFR1: Tick-to-Redis latency < 10ms p99
- NFR2: Tick-to-QuestDB latency < 500ms p99 (batched)
- NFR3: Sustain ≥2,400 ticks/sec across 400 feeds
- NFR4: Memory < 512 MB RSS at steady state
- NFR5: CPU < 1 vCPU steady-state on Hetzner CPX41

**Reliability**
- NFR6: Detect and begin reconnecting within 5s of disconnect
- NFR7: Restore all subscriptions within 90s of clean restart
- NFR8: No silent data corruption on crash — all written records must be complete
- NFR9: QuestDB write failures must not halt feed processing
- NFR10: ≥30 days continuous operation without operator restart

**Data Correctness**
- NFR11: Zero internal_* gap causes under normal conditions
- NFR12: ≤1 external_* gap per symbol per 24h SLO
- NFR13: Snapshot/delta merge must verify sequence overlap before resuming
- NFR14: No delta applied more than once

**Security**
- NFR15: Credentials never in logs/errors/panics/metrics at any level including DEBUG
- NFR16: Credentials from env vars only — no hardcoded defaults, no committed config files
- NFR17: /version, /health, /metrics unauthenticated (internal network only)

**Integration Stability**
- NFR18: Redis Stream schema backward-compatible across versions
- NFR19: /metrics exposes all metrics at all times including disconnected feeds
- NFR20: /health responds within 100ms regardless of feed state

**Testability**
- NFR21: Five independent test layers (L1–L5) without modifying production code
- NFR22: Four injectable test seams substitutable via interfaces/constructor injection

### Additional Requirements & Constraints

- Redis Stream MAXLEN: PRD states "last 10,000 entries per stream" in the interface spec section — **⚠️ INCONSISTENCY: architecture.md specifies 50,000 entries**
- Journey 4 refers to "The Candle Service (Python)" — **⚠️ STALE: Candle Service is now Go**
- Candle Service correctness depends entirely on the aggregator's gap markers (SC5)
- 30-day QuestDB TTL creates hard recovery window for cold storage
- flush_manifest + gaps:log form the audit trail

---

## Epic Coverage Validation

### Coverage Matrix — Aggregator FRs (PRD FR1–FR36)

| FR | PRD Requirement | Epic Coverage | Status |
|---|---|---|---|
| FR1 | KuCoin authenticated WebSocket via REST token | Epic 2 / Story 2.2 | ✅ Covered |
| FR2 | KuCoin token auto-renewal before 24h expiry | Epic 2 / Story 2.3 | ✅ Covered |
| FR3 | KuCoin ping/pong heartbeat | Epic 2 / Story 2.2 | ✅ Covered |
| FR4 | Bybit 10-topics-per-connection distribution | Epic 2 / Story 2.4 | ✅ Covered |
| FR5 | Subscription confirmation per symbol | Epic 2 / Stories 2.2, 2.4 | ✅ Covered |
| FR6 | Subscribe up to 200 symbols per exchange | Epic 2 / Stories 2.2, 2.5 | ✅ Covered |
| FR7 | Detect dropped WebSocket connection | Epic 2 / Story 2.1 | ✅ Covered |
| FR8 | Reconnect and resume without operator intervention | Epic 2 / Story 2.1 | ✅ Covered |
| FR9 | In-memory L2 order book per symbol | Epic 1 / Story 1.4 | ✅ Covered |
| FR10 | Initialize book from REST snapshot | Epic 1 / Story 1.5 | ✅ Covered |
| FR11 | Buffer deltas while snapshot in-flight | Epic 1 / Story 1.5 | ✅ Covered |
| FR12 | Merge snapshot + buffered deltas (seq > snapshot) | Epic 1 / Story 1.5 | ✅ Covered |
| FR13 | Detect stale snapshot (seq < oldest buffered) | Epic 1 / Story 1.5 | ✅ Covered |
| FR14 | Detect out-of-order/duplicate deltas | Epic 1 / Story 1.4 | ✅ Covered |
| FR15 | Discard out-of-order/duplicate deltas | Epic 1 / Story 1.4 | ✅ Covered |
| FR16 | Detect sequence number gap | Epic 1 / Story 1.6 | ✅ Covered |
| FR17 | Classify gap into exactly one of 4 causes | Epic 1 / Story 1.6 | ✅ Covered |
| FR18 | Emit in-band gap marker to ticks stream | Epic 3 / Story 3.2 | ✅ Covered |
| FR19 | Write every gap marker to gaps:log | Epic 3 / Story 3.2 | ✅ Covered |
| FR20 | Distinguish internal_* vs external_* in monitoring | Epic 4 / Stories 4.1–4.2 | ✅ Covered |
| FR21 | Publish normalized L2 updates to Redis Streams | Epic 3 / Story 3.2 | ✅ Covered |
| FR22 | Publish normalized trade events to Redis Streams | Epic 3 / Story 3.2 | ✅ Covered |
| FR23 | Write raw tick data to QuestDB via ILP batched | Epic 3 / Story 3.3 | ✅ Covered |
| FR24 | Detect QuestDB WAL suspension + auto-resume | Epic 3 / Story 3.3 | ✅ Covered |
| FR25 | Trim Redis Streams to MAXLEN | Epic 3 / Story 3.2 | ✅ Covered |
| FR26 | Consumer group resume from last-ack position | Epic 3 / Story 3.1 | ✅ Covered |
| FR27 | /health endpoint (connection state, gap count, ok/degraded/critical) | Epic 4 / Story 4.2 | ✅ Covered |
| FR28 | /version endpoint (git SHA, build timestamp) | Epic 4 / Story 4.2 | ✅ Covered |
| FR29 | /metrics Prometheus endpoint | Epic 4 / Stories 4.1–4.2 | ✅ Covered |
| FR30 | Distinguish internal vs external gaps without logs | Epic 4 / Stories 4.1–4.2 | ✅ Covered |
| FR31 | Configuration via environment variables | Epic 1 / Story 1.2 | ✅ Covered |
| FR32 | Startup validation gate (90s timeout, exit non-zero) | Epic 4 / Story 4.3 | ✅ Covered |
| FR33 | Graceful SIGTERM shutdown with drain | Epic 4 / Story 4.3 | ✅ Covered |
| FR34 | Validate deployed binary via version endpoint | Epic 4 / Story 4.3 | ✅ Covered |
| FR35 | Load credentials exclusively from env vars | Epic 1 / Story 1.2 | ✅ Covered |
| FR36 | Credentials never in logs/errors/panics | Epic 1 / Story 1.2 | ✅ Covered |

### Coverage Statistics — Aggregator

- Total PRD FRs: 36 / Covered: 36 / **Coverage: 100%**
- Total PRD NFRs: 22 / Covered: 22 / **Coverage: 100%**

### Coverage Matrix — Candle Service CS-FRs (defined in epics.md)

| FR | Epic | Status |
|---|---|---|
| CS-FR1–CS-FR4, CS-FR18(OHLCV), CS-FR25–CS-FR28 | Epic 5 | ✅ In scope |
| CS-FR5–CS-FR10 | Epic 6 | ✅ In scope |
| CS-FR11–CS-FR17, CS-FR18(null rows) | Epic 7 | ✅ In scope |
| CS-FR19–CS-FR21 | Epic 8 | ✅ In scope |
| CS-FR22–CS-FR24 | Epic 9 | ✅ In scope |

### ⚠️ Gaps & Inconsistencies Found

**GAP-1 — MAXLEN inconsistency (PRD vs Architecture/Epics) [MEDIUM]**
PRD interface spec states "last 10,000 entries per stream." Architecture.md and Story 3.2 specify MAXLEN 50,000.
*Action: Align PRD to 50,000 — one source of truth.*

**GAP-2 — Deprecated WebSocket library in Story acceptance criteria [HIGH]**
project-context.md (updated 2026-05-06) mandates `github.com/coder/websocket v1.8.14`. Stories 1.1 and 2.1 acceptance criteria still reference `nhooyr.io/websocket` (deprecated). A dev agent following Story 1.1 literally will install the wrong library.
*Action: Update Stories 1.1 and 2.1 to reference `github.com/coder/websocket v1.8.14`.*

**GAP-3 — Candle Service story files not created [REQUIRED BEFORE DEV]**
Epics 5–9 are defined at epic-level only. No individual story files with acceptance criteria exist. epics.md frontmatter shows `candleServiceStepsCompleted: ['step-01-validate-prerequisites', 'step-02-design-epics']` — story creation (step-03) not complete.
*Action: Run `bmad-create-story` for Story 5-1 before Candle Service dev starts.*

**GAP-4 — PRD Journey 4 references Python Candle Service [LOW]**
Journey 4 describes "The Candle Service (Python) starts up." Stale after language change to Go. Not a requirement, but misleading.
*Action: Update Journey 4 prose in PRD.*

**GAP-5 — Candle Service NFRs not in PRD [LOW / INFORMATIONAL]**
CS-NFR1–CS-NFR8 are defined only in epics.md. PRD covers only the aggregator. Not blocking, but means the PRD does not describe full system non-functional requirements.
*Action: Optional — add a Candle Service NFR section to PRD for completeness.*

---

## UX Alignment Assessment

### UX Document Status

Not found — not applicable. magnum-opus is a headless backend daemon with no user interface. PRD explicitly states: "UX Design Requirements: N/A — magnum-opus aggregator is a headless backend daemon with no user interface." The operational surface (HTTP endpoints, Prometheus, logs) is developer/operator tooling, not a user-facing UI. No UX document is required.

---

## Epic Quality Review

### Epic Structure Assessment

#### Aggregator Epics (1–4) — COMPLETE AND IMPLEMENTED

| Epic | User Value Focus | Independence | Story ACs | Verdict |
|---|---|---|---|---|
| Epic 1: Project Foundation & Correctness Engine | Borderline technical — acceptable; delivers load-bearing correctness guarantee as explicit outcome | ✅ Standalone | ✅ Full GWT format | ✅ Pass |
| Epic 2: Exchange Feed Connectivity | ✅ "Operator can connect to KuCoin and Bybit" | ✅ Depends only on Epic 1 | ✅ Full GWT format | ✅ Pass |
| Epic 3: Data Capture Pipeline & Gap Guarantees | ✅ "Candle Service can read a complete Redis Stream" | ✅ Depends on Epics 1–2 | ✅ Full GWT format | ✅ Pass |
| Epic 4: Operational Readiness & Deployment | ✅ "Operator can monitor, deploy, validate" | ✅ Depends on Epics 1–3 | ✅ Full GWT format | ✅ Pass |

#### Candle Service Epics (5–9) — SPEC COMPLETE, NO STORY ACS YET

| Epic | User Value Focus | Independence | Story ACs | Verdict |
|---|---|---|---|---|
| Epic 5: Candle Service Foundation | ✅ "Operator can run Candle Service, see OHLCV rows" | ✅ Depends on Epics 1–4 (aggregator complete) | ❌ Epic-level notes only | 🟠 Stories needed |
| Epic 6: OB-Derived Features | ✅ "snapshot_1s rows contain all OB-derived fields" | ✅ Depends on Epic 5 | ❌ Epic-level notes only | 🟠 Stories needed |
| Epic 7: Trade & Quality Features | ✅ "Full 67-field schema populated" | ✅ Depends on Epics 5–6 | ❌ Epic-level notes only | 🟠 Stories needed |
| Epic 8: Multi-Timeframe Cascade & Redis Output | ✅ "Bots can subscribe to 1m–1w candles" | ✅ Depends on Epics 5–7 | ❌ Epic-level notes only | 🟠 Stories needed |
| Epic 9: Cold Storage & Operations | ✅ "Daily snapshots archived to B2" | ✅ Depends on Epic 5 | ❌ Epic-level notes only | 🟠 Stories needed |

### 🔴 Critical Violations

**QUAL-1 — Story 1.1 and Story 2.1 acceptance criteria reference deprecated WebSocket library**
Stories 1.1 and 2.1 specify `nhooyr.io/websocket` in acceptance criteria. project-context.md (the authoritative agent rules file, updated 2026-05-06) mandates `github.com/coder/websocket v1.8.14`. A dev agent executing Story 1.1 verbatim will install the wrong library, causing every test against `wsjson` to fail.
*Fix: Update Stories 1.1 and 2.1 acceptance criteria to `github.com/coder/websocket v1.8.14`.*

### 🟠 Major Issues

**QUAL-2 — No project-context.md for the Candle Service**
project-context.md covers the aggregator exclusively: module path `github.com/mrqdt/magnum-opus/aggregator`, aggregator package names, aggregator Prometheus prefix `aggregator_`, aggregator-specific forbidden libraries. A dev agent starting on Story 5-1 will have no authoritative rules for:
- Candle Service module path (`github.com/mrqdt/magnum-opus/candle-service`)
- Candle-specific packages (parquet-go, aws-sdk-go-v2)
- Prometheus prefix `candle_` not `aggregator_`
- `candle-service/` directory layout
*Fix: Update project-context.md to include a Candle Service section, or create a separate `candle-service/project-context.md`.*

**QUAL-3 — epics.md frontmatter shows candleServiceStatus: in-progress**
The frontmatter shows `candleServiceStatus: in-progress` and `candleServiceStepsCompleted: ['step-01-validate-prerequisites', 'step-02-design-epics']`. The Candle Service spec is now complete (all FRs defined, all review findings incorporated, architecture section added). Frontmatter is stale and may confuse tooling.
*Fix: Update frontmatter to `candleServiceStatus: complete` and add `'step-03-create-stories'` to completed steps (since the story titles are defined in sprint-status.yaml).*

### 🟡 Minor Concerns

**QUAL-4 — DB schema creation timing: flush_manifest deferred to Epic 9 implementation story**
Epic 9 notes: "`flush_manifest` DDL written in the same story as the first flush implementation." This follows the schema-first pattern correctly — but the flush_manifest schema is now fully defined in CS-FR23. Confirm the dev agent implementing Epic 9 Story 1 writes the DDL first before any flush code.
*No action needed — already specified correctly in CS-FR23.*

**QUAL-5 — MAXLEN inconsistency (duplicate of GAP-1)**
PRD: 10,000. Architecture + epics: 50,000. Already documented as GAP-1.

---

## Summary and Recommendations

### Overall Readiness Status

**NEEDS WORK — 2 fixes required before Candle Service dev starts, 1 fix before next aggregator story**

The aggregator (Epics 1–4) is complete and fully implemented. All 36 FRs and 22 NFRs are covered at 100%. The Candle Service specification is thorough and substantially improved by today's review work — language change to Go, all adversarial and edge case findings incorporated, architecture section added. However, two issues must be resolved before a dev agent can reliably execute Candle Service stories.

### All Issues Found — Priority Ordered

| ID | Severity | Description | Blocking? |
|---|---|---|---|
| QUAL-1 | 🔴 Critical | Stories 1.1 + 2.1 ACs reference `nhooyr.io/websocket` — deprecated; project-context.md mandates `github.com/coder/websocket v1.8.14` | Blocks any story touching transport layer |
| QUAL-2 | 🟠 Major | No project-context.md for Candle Service — dev agents have no authoritative rules for module path, packages, or naming conventions | Blocks Candle Service dev |
| GAP-3 | 🟠 Major | Candle Service story files not created — Epics 5–9 have epic-level notes only, no story acceptance criteria | Blocks Candle Service dev (expected — normal workflow) |
| GAP-1 | 🟡 Medium | MAXLEN inconsistency: PRD says 10,000; architecture + epics say 50,000 | Informational — fix before next aggregator work |
| QUAL-3 | 🟡 Minor | epics.md frontmatter `candleServiceStatus: in-progress` is stale | Documentation only |
| GAP-4 | 🟡 Minor | PRD Journey 4 refers to "Candle Service (Python)" — stale | Documentation only |
| GAP-5 | 🟢 Low | Candle Service NFRs not in PRD | Informational only |

### Recommended Next Steps

1. **Fix QUAL-1 now** — update Stories 1.1 and 2.1 acceptance criteria to `github.com/coder/websocket v1.8.14`. Single targeted edit, low risk, high importance since it's the foundation story.

2. **Create Candle Service project-context.md** — add a `candle-service/` section (or separate file) covering: module path, required packages, Prometheus prefix `candle_`, directory layout, and a pointer to the architecture.md Candle Service section. This is the most impactful prep work before dev.

3. **Run `bmad-create-story` for Story 5-1** — start the normal story creation cycle for the Candle Service. The epic spec is ready.

4. **Fix GAP-1 (MAXLEN)** — decide on 50,000 (architecture value) and update the PRD interface spec section to match. One-line fix.

5. **Fix QUAL-3** — update epics.md frontmatter `candleServiceStatus: complete`.

6. **Fix GAP-4** — update PRD Journey 4 to reference "Go Candle Service."

### Final Note

This assessment identified **7 issues** across **4 categories** (coverage gaps, quality violations, stale documentation, minor inconsistencies). The aggregator spec is production-quality with 100% FR/NFR coverage and detailed GWT acceptance criteria. The Candle Service spec is ready for story creation — all architectural gaps from today's adversarial and edge case reviews have been incorporated. The two blocking items (QUAL-1 and QUAL-2) are quick to fix and should be done before a dev agent picks up any Candle Service story.

**Report saved:** `_bmad-output/planning-artifacts/implementation-readiness-report-2026-05-08.md`
