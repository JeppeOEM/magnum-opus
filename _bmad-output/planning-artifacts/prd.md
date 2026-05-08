---
stepsCompleted: ['step-01-init', 'step-02-discovery', 'step-02b-vision', 'step-02c-executive-summary', 'step-04-journeys', 'step-05-domain', 'step-06-innovation', 'step-07-project-type', 'step-08-scoping', 'step-09-functional', 'step-10-nonfunctional', 'step-11-polish']
releaseMode: single-release
inputDocuments:
  - '_bmad-output/planning-artifacts/product-brief-magnum-opus-aggregator.md'
  - '_bmad-output/brainstorming/brainstorming-session-2026-05-03-1200.md'
  - '_bmad-output/planning-artifacts/research/technical-storage-architecture-crypto-trading-research-2026-05-03.md'
  - '_bmad-output/planning-artifacts/research/domain-crypto-market-microstructure-signals-research-2026-05-03.md'
workflowType: 'prd'
classification:
  projectType: 'backend_infrastructure_service'
  domain: 'fintech_crypto'
  complexity: 'high'
  projectContext: 'greenfield'
---

# Product Requirements Document - magnum-opus

**Author:** mrqdt
**Date:** 2026-05-04

## Executive Summary

The Go Aggregation Service is the data foundation layer of the magnum-opus algorithmic trading system. It connects to KuCoin and Bybit WebSocket feeds, maintains live L2 order book state for up to 200 symbols per exchange, and writes normalized tick data to Redis Streams and QuestDB with sequence-level gap detection as a first-class invariant. It has one responsibility: capture every tick that can be captured, and explicitly flag every tick that cannot.

This service is the only component in magnum-opus that touches raw exchange feeds. The accuracy of every downstream computation — 67-field 1-second aggregates including OFI, trade sign autocorrelation, block volumes, and order arrival counts — depends entirely on tick completeness. A 200ms reconnect gap does not merely shift a bar boundary; it systematically corrupts field values in ways that depend on which specific ticks were missed. Correctness at the tick level is not a quality-of-life concern — it is the load-bearing requirement for every signal and model built on top.

### What Makes This Special

Three properties distinguish this service from general-purpose trading frameworks and DIY collectors:

**In-band gap markers that distinguish failure modes.** When the stream falls silent, consumers cannot determine whether no trades occurred, a sequence gap happened, or the aggregator crashed — three states requiring completely different responses. Gap markers travel in the same Redis Stream as real ticks, carrying `gap_cause` (internal vs external), `seq_before`, and `seq_after`. Silence is ambiguous; in-band markers are not. All gap detection and cause classification is centralised at the source, eliminating staleness detection logic from every downstream consumer.

**Snapshot/delta merge on reconnect that prevents gaps from existing.** On disconnect, incoming deltas are buffered while a fresh snapshot is requested. On snapshot receipt, sequence overlap is verified and buffered deltas are replayed in order before the live feed resumes. The reconnect window produces no gap marker because no data is lost. Simpler approaches (reset on reconnect, skip buffering) trade engineering simplicity for systematic field corruption in every 1-second aggregate that spans a reconnect event.

**Exchange-specific correctness, not exchange abstraction.** KuCoin requires a REST token fetch before WebSocket connect, 24h token renewal, and ping/pong heartbeat management. Bybit enforces a 10-topic-per-connection limit requiring ~20 multiplexed connections for 200 symbols. General frameworks abstract these details away; this service implements them correctly because correctness is the constraint, not portability.

The constraint combination this service satisfies — self-hosted, cost-zero, correctness-first, exchange-specific protocol handling — is not met by any existing open-source solution or affordable commercial vendor.

## Project Classification

- **Project Type:** Backend infrastructure data pipeline (Go)
- **Domain:** Fintech — cryptocurrency exchange market data
- **Complexity:** High — real-time WebSocket protocols, exchange-specific quirks, sequence number gap detection, snapshot/delta merge race conditions, TDD-first correctness mandate
- **Project Context:** Greenfield
- **Primary User:** mrqdt (sole developer and operator)

## Success Criteria

The service is successful when the following conditions hold continuously in production:

- **SC1: Zero internal data loss.** The service has produced zero `internal_*` gap causes since the last deployment. Any single `internal_*` gap event is a correctness regression requiring immediate investigation.
- **SC2: Exchange gap SLO met.** `external_*` gap rate is ≤1 per symbol per 24-hour period across both exchanges. Sustained exceedance indicates an exchange connectivity problem, not a service defect.
- **SC3: Startup reliability.** All configured feeds connect and confirm subscriptions within 90 seconds on ≥99% of process starts.
- **SC4: Sustained operation.** The service runs ≥30 consecutive days without an operator-initiated restart under normal exchange conditions.
- **SC5: Consumer independence.** The Candle Service operates correctly using only Redis Stream contents — no out-of-band coordination, no sequence tracking, no staleness detection required on its side.
- **SC6: Deployment confidence.** Every production deployment passes L1+L2+L3 tests locally and L5 live exchange tests (18 tests). After deploy, metrics and logs are monitored for regressions before the release is tagged.
- **SC7: Performance envelope.** Tick-to-Redis latency stays below 10ms at p99 and memory stays below 512 MB RSS at steady state across 400 active feeds.

## User Journeys

### Journey 1: mrqdt — The Quiet Morning (Happy Path)

**Opening scene.** It's 08:30 UTC. mrqdt opens a browser tab to check the Grafana dashboard before the European session heats up. The aggregator has been running for 11 hours. 200 KuCoin symbols, 200 Bybit symbols, all connected.

**Rising action.** He scans two panels: gap count per symbol (all zeros since midnight) and the tick ingestion rate (steady ~2,400 ticks/second across both exchanges). He runs `make health` from the terminal — the `/health` endpoint returns `{"status":"ok","connected_feeds":400,"gap_count_24h":0}`. The Redis Stream consumer lag for the Candle Service shows under 50ms. Everything is boring. That's the goal.

**Climax.** He notices Bybit's ETHUSDT tick rate dipped for 40 seconds at 04:17 UTC. He pulls the `gaps:log` stream — empty for that symbol at that time. The aggregator performed a silent snapshot/delta merge: the exchange sent an orderly reconnect, buffered the deltas, verified sequence overlap, and resumed without emitting a gap marker. No data was lost. No alert fired.

**Resolution.** mrqdt closes the dashboard. The aggregator requires zero intervention. He spends his morning on Candle Service development, not babysitting WebSocket connections.

---

### Journey 2: mrqdt — 3am Wake-Up (Gap Incident)

**Opening scene.** 03:14 UTC. A PagerDuty alert fires: `gap_cause=external_disconnect, symbol=BTCUSDT, exchange=bybit, seq_gap=1847`. mrqdt's phone buzzes. He opens his laptop.

**Rising action.** He checks `/health` — Bybit WebSocket for BTCUSDT shows `state: reconnecting`. This was a true external disconnect (exchange-side), not an internal buffer overflow. The aggregator correctly classified it and emitted an in-band gap marker before attempting reconnect. The Candle Service, reading the gap marker in-band, has already flagged the BTCUSDT 1-second bar spanning that gap as `has_gap=true`. No silent corruption.

**Climax.** mrqdt checks the `gaps:log` entry: `{exchange:"bybit", symbol:"BTCUSDT", gap_ts:1746230041, gap_cause:"external_disconnect", seq_before:98234701, seq_after:98236548, seq_gap:1847}`. The sequence gap is ~1847 events — a 2-second outage on Bybit's side. He checks the Bybit status page: scheduled maintenance notice he missed. This is an external gap. Not his bug.

**Resolution.** By 03:19, the feed is back. `/health` shows `state: connected`. The Candle Service dashboard shows gap-affected bars marked `has_gap=true`, downstream bots have already detected the flag and reduced position sizing for BTCUSDT per their degraded-mode logic. No manual intervention needed. He goes back to sleep.

---

### Journey 3: mrqdt — Shipping a New Version (Deployment)

**Opening scene.** mrqdt has implemented exchange-specific rate limit handling for KuCoin's new REST endpoint throttling. Tests pass locally: L1 in 3s, L2 in 12s, L3 in 45s, L4 Docker in 8 minutes. He pushes to main.

**Rising action.** GitHub Actions runs L1+L2 (26 tests, ~45 seconds, free tier). Green. He runs `make test-l4` locally — L4 integration suite with FakeQuestDB WAL suspension tests and Toxiproxy network partition tests. Green. He then runs `make test-live` — L5 live exchange tests connecting to actual KuCoin and Bybit. 18 tests. All pass.

**Climax.** He deploys to the Hetzner CPX41 VM. The aggregator starts, authenticates with KuCoin (REST token fetch), subscribes to 200 KuCoin symbols and ~20 Bybit connections for 200 symbols. Within 90 seconds all feeds report `connected`. He runs `make verify-versions` — confirmed the deployed binary matches the intended git SHA.

**Resolution.** He monitors `/health`, `/metrics`, and logs. Gap count stays within the external-only SLO (≤1/symbol/24h for external causes, zero internal causes). No `internal_*` gaps. He tags the release.

---

### Journey 4: Candle Service — Reading the Stream (Integration Consumer)

**Opening scene.** The Go Candle Service starts up after a 4-minute maintenance window. It has missed ~240 seconds of ticks per symbol. It begins reading from its last-acknowledged position in `ticks:bybit:BTCUSDT` via its Redis consumer group.

**Rising action.** The Candle Service reads ticks in order. At one position it encounters a gap marker: `{type:"gap", gap_cause:"external_disconnect", seq_before:98234701, seq_after:98236548}`. It does not need to query any external system to understand this — the cause and sequence bounds are in the message itself. It marks the bars that span this gap as `has_gap=true`.

**Climax.** After the gap marker, real ticks resume at the correct sequence number. The Candle Service continues accumulating normally. The gap marker was informational — the aggregator has already performed all gap detection work. The Candle Service contains zero staleness detection logic, zero sequence tracking logic, zero exchange-specific reconnect handling. It simply reads, computes, and writes.

**Resolution.** The BTCUSDT 1-second bars for the gap window are written to QuestDB with `has_gap=true`. Downstream bots read this flag and apply degraded-mode position sizing for one bar. No silent corruption. The Candle Service's correctness depends entirely on the aggregator having emitted correct gap markers — which is why tick-level correctness is the load-bearing requirement.

## Domain-Specific Requirements

### Exchange API Compliance

KuCoin and Bybit both publish API Terms of Service that govern programmatic access. Violations result in API key revocation, IP bans, or account suspension — any of which takes the entire trading system offline.

**KuCoin constraints:**
- WebSocket connections require a REST-obtained token (30-second validity window for the initial connect); the token itself is valid for 24 hours and must be renewed before expiry
- Private topics require separate authenticated connections from public market data
- Ping/pong heartbeat must be sent within the exchange's timeout window or the connection is silently dropped
- No published hard limit on concurrent public connections, but aggressive reconnection triggers throttling

**Bybit constraints:**
- Maximum 10 topics per WebSocket connection — 200 symbols requires ~20 concurrent connections
- Subscription confirmation must be received for each topic; unconfirmed subscriptions produce no data and no error
- WebSocket connection is silently dropped after 20 seconds without a ping; must send `{"op":"ping"}` every ≤10 seconds

**Compliance requirement:** The service must implement exchange-specific protocol behaviour (not a generic WebSocket layer) and must never exceed per-exchange connection or subscription limits. Exceeding limits is not retried automatically — it constitutes a violation that can result in account action.

### Market Data Correctness Constraints

Cryptocurrency market data has a time dimension that creates correctness constraints absent in most software domains.

- **Sequence numbers are the ground truth.** Bybit and KuCoin both provide sequence numbers on L2 update messages. A gap in sequence numbers means real market events were missed — the L2 order book state is incorrect and will remain incorrect until a fresh snapshot is taken.
- **Snapshot/delta merge is a domain-required operation.** On reconnect, a REST snapshot must be obtained and deltas buffered in parallel. The sequence number where the snapshot was taken defines which buffered deltas to replay. This is not a performance optimisation — it is the only correct way to reconnect without corrupting the L2 book.
- **Out-of-order deltas are invalid.** Exchanges occasionally re-deliver messages. The service must detect and discard messages with sequence numbers ≤ the last applied sequence, not apply them again.
- **Stale snapshot detection.** If the snapshot sequence number is lower than the oldest buffered delta, the snapshot arrived too late and the merge cannot be verified. A gap marker must be emitted and a fresh snapshot requested.

### Security Requirements

API credentials (KuCoin and Bybit API keys and secrets) grant trading permissions, not just market data access. Credential compromise can result in financial loss.

- API keys must be loaded from environment variables or a secrets manager — never hardcoded or committed to source control
- Keys must not appear in logs, error messages, or panic output
- The service binary must not embed credentials at build time
- On Hetzner VMs: credentials stored in `.env` files with `chmod 600`, not in `/etc/environment` or world-readable locations

No encryption-at-rest requirement for market data itself — it is public information. The credential security boundary is the only financially sensitive surface.

### Audit and Data Integrity

The `flush_manifest` table (QuestDB) and `gaps:log` stream (Redis) together form the audit trail for tick completeness.

- Every gap event must be recorded in `gaps:log` with cause, sequence bounds, and timestamp — regardless of cause classification
- Every daily Parquet flush must be recorded in `flush_manifest` with row count, B2 path, verification status, and error message if failed
- Failed flushes must not be silently retried without a manifest entry — the manifest is the record of what data is in cold storage
- The 30-day QuestDB TTL creates a hard recovery window: a gap in cold storage not corrected within 30 days is unrecoverable

## Innovation & Novel Patterns

### Detected Innovation Areas

**Innovation 1: In-Band Gap Markers with Cause Classification**

Every existing market data collector treats gaps the same way: the stream stops, then resumes. What happened during the silence is never recorded in the stream — consumers must implement their own staleness detection, heartbeat monitoring, and gap inference logic independently.

This service inverts that. Gap markers are first-class stream citizens that travel in the same Redis Stream as tick data, carrying `gap_cause` (one of four exhaustive values: `internal_buffer_overflow`, `internal_merge_error`, `external_disconnect`, `external_rate_limit`), `seq_before`, and `seq_after`. A consumer reading the stream can determine whether zero trades occurred, a gap happened, or the aggregator crashed — three states requiring completely different downstream responses — from the stream contents alone, with no out-of-band coordination.

The cause classification eliminates staleness detection and gap inference logic from every downstream consumer permanently. `internal_*` causes signal bugs in this service (critical alerts); `external_*` causes are exchange behaviour (warning-level, expected at some frequency).

**Innovation 2: Snapshot/Delta Merge as the Reconnect Protocol**

The standard reconnect approach in WebSocket market data systems is: on disconnect, subscribe again, request a fresh snapshot, throw away any in-flight data, wait for snapshot, resume. This produces a gap of at least one snapshot round-trip time (~200–500ms) for every reconnect event.

This service treats the reconnect window as the primary engineering challenge. On disconnect: buffer incoming deltas immediately. On snapshot receipt: verify that the snapshot sequence number falls within the buffered delta range. Replay only deltas with sequence numbers > snapshot. Resume live feed at the correct sequence position. The reconnect window produces no gap because no data is lost.

The technical novelty is the sequence overlap verification — if the snapshot arrives with a sequence number lower than the oldest buffered delta, the merge window has been missed, and a gap marker must be emitted before requesting a fresh snapshot. This edge case is the actual hard problem that most implementations silently fail.

**Innovation 3: Constraint-Based Architecture Over Framework Abstraction**

General-purpose WebSocket frameworks abstract exchange differences behind a common interface. The abstractions work until the exchange does something the abstraction didn't anticipate — then the gap is silent and the data is wrong.

This service takes the opposite position: implement each exchange's protocol exactly as specified, test exchange-specific failure modes explicitly, and treat portability as a non-goal. The innovation is the constraint combination — self-hosted + cost-zero + correctness-first + exchange-specific fidelity — which is not satisfied by any existing solution.

### Market Context & Competitive Landscape

Commercial market data vendors (Refinitiv, Bloomberg Terminal API, CoinAPI) solve the correctness problem but cost $500–$5,000/month and abstract away exchange specifics behind a unified API. DIY WebSocket clients built on general frameworks (ccxt, websockets library) avoid the cost but trade correctness for portability — they reset on reconnect and have no gap detection. The niche this service occupies — self-hosted, cost-zero, correctness-first, exchange-specific — requires building what doesn't exist.

A 200ms gap doesn't shift a bar boundary in a predictable way. It corrupts 67 computed fields including order flow imbalance, trade sign autocorrelation, block volume thresholds, and order arrival counts — in ways that depend on which specific ticks were missed. There is no post-hoc correction. The reconnect protocol is the correctness invariant, and it must be correct at the implementation level.

### Validation Approach

- **L3 mock WebSocket tests** validate the snapshot/delta merge protocol under all branch conditions: snapshot arrives before deltas, snapshot arrives mid-buffer, snapshot arrives after buffer overflow, out-of-order delta detection, duplicate sequence number discard
- **L4 fault injection tests** (Toxiproxy) validate reconnect behaviour under real network partition conditions
- **L5 live exchange tests** validate that the service correctly processes actual KuCoin and Bybit WebSocket messages — real sequence numbers, real heartbeat timing, real subscription confirmation patterns
- **`gaps:log` audit trail** is the continuous validation signal in production: zero `internal_*` entries means the innovation is working; any `internal_*` entry is a regression signal

### Risk Mitigation

| Risk | Mitigation |
|---|---|
| Exchange changes sequence number format | Sequence number parsing is exchange-specific and tested against real fixtures refreshed every 24h |
| Buffer overflow during high-volatility reconnect | `internal_buffer_overflow` gap cause emitted, fresh snapshot cycle started — correctness preserved, gap recorded |
| Snapshot sequence number arrives stale (below oldest buffered delta) | Detected at merge-time; gap marker emitted, new snapshot requested — no silent corruption |
| Bybit subscription confirmation not received | Tested explicitly in L3; service must verify subscription before marking feed `connected` |

## Interface Specifications & Technical Contracts

### Project-Type Overview

The Go Aggregation Service is a long-running daemon with no interactive user interface. Its "API surface" is threefold: the Redis Streams it writes to (primary consumer interface), the HTTP operational endpoints it exposes (`/health`, `/version`, Prometheus metrics), and the configuration it accepts at startup. All three must be stable contracts — downstream services (Candle Service, monitoring stack) depend on them.

### Output Interface Specification

**Redis Streams — Tick Data**

Stream key: `ticks:{exchange}:{symbol}` (e.g. `ticks:bybit:BTCUSDT`)

Tick message schema (NDJSON, published as Redis stream entry):
```json
{
  "type": "tick",
  "exchange": "bybit",
  "symbol": "BTCUSDT",
  "seq": 98234701,
  "ts_exchange": 1746230040123,
  "ts_local": 1746230040145,
  "side": "bid",
  "price": "67234.50",
  "size": "0.142",
  "event": "update"
}
```

Gap marker schema (same stream, same consumer group):
```json
{
  "type": "gap",
  "exchange": "bybit",
  "symbol": "BTCUSDT",
  "gap_ts": 1746230041000,
  "gap_cause": "external_disconnect",
  "seq_before": 98234700,
  "seq_after": 98236548
}
```

`gap_cause` is one of four exhaustive values: `internal_buffer_overflow`, `internal_merge_error`, `external_disconnect`, `external_rate_limit`. No other values are valid.

Stream retention: Redis Streams trimmed by length (MAXLEN). Target retention: last 50,000 entries per stream (~20 seconds of buffer at 2,400 ticks/sec; sufficient for Candle Service consumer lag recovery without unbounded memory growth; ~1 GB Redis memory across 400 streams at ~50 bytes/entry).

**`gaps:log` Redis Stream**

Stream key: `gaps:log`

Every gap marker emitted (regardless of cause) is also written to `gaps:log` for centralized audit and alerting. Schema matches the gap marker above plus `seq_gap` (derived: `seq_after - seq_before`).

**QuestDB ILP Write Interface**

The service writes raw L2 tick data to QuestDB via ILP (TCP port 9009) for sequencing audit. Writes are batched in 500ms windows. QuestDB WAL suspension is detected by polling `wal_tables()` every 30s; suspended tables trigger `ALTER TABLE RESUME WAL` automatically. The Candle Service (not the aggregator) is responsible for computing and writing `snapshot_1s` aggregates.

### Operational Endpoints

**`GET /health`**

```json
{
  "status": "ok",
  "connected_feeds": 400,
  "feeds": {
    "kucoin": {"connected": 200, "reconnecting": 0, "state": "ok"},
    "bybit": {"connected": 200, "reconnecting": 0, "state": "ok"}
  },
  "gap_count_24h": 0,
  "uptime_seconds": 39600
}
```

`status` is `degraded` if any feed is reconnecting, `critical` if any `internal_*` gap has occurred in the last 24h.

**`GET /version`**

```json
{
  "version": "1.2.3",
  "git_sha": "a3f9b21",
  "build_time": "2026-05-04T08:00:00Z",
  "go_version": "go1.22.3"
}
```

**`GET /metrics`** (Prometheus)

- `aggregator_ticks_total{exchange, symbol}` — cumulative tick count
- `aggregator_gap_total{exchange, symbol, cause}` — cumulative gap count by cause
- `aggregator_feed_state{exchange, symbol}` — 1=connected, 0=reconnecting
- `aggregator_consumer_lag_ms{exchange, symbol}` — Redis Stream consumer lag
- `aggregator_questdb_write_latency_ms` — ILP write latency histogram

### Configuration Model

All configuration via environment variables. No config file required.

```
KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE
BYBIT_API_KEY, BYBIT_API_SECRET
REDIS_URL=redis://localhost:6379
QUESTDB_ILP_ADDR=localhost:9009
SYMBOLS_KUCOIN=BTC-USDT,ETH-USDT,...   # comma-separated, up to 200
SYMBOLS_BYBIT=BTCUSDT,ETHUSDT,...       # comma-separated, up to 200
LOG_LEVEL=info
```

Symbol lists are loaded at startup. Changing symbols requires a restart.

### Startup & Shutdown Behaviour

**Startup sequence:**
1. Validate all required environment variables — fail fast if missing
2. Fetch KuCoin REST WebSocket token
3. Open WebSocket connections (KuCoin and Bybit)
4. Send subscription requests for all symbols
5. Wait for subscription confirmations (timeout: 30s per batch)
6. Begin serving `/health` — status `starting` until all feeds confirm, then `ok`
7. Begin writing to Redis Streams and QuestDB

All feeds must confirm within 90 seconds of startup or the service exits non-zero.

**Shutdown sequence (SIGTERM):**
1. Stop accepting new WebSocket messages
2. Drain in-flight tick writes to Redis Streams (max 5s)
3. Flush QuestDB ILP buffer
4. Close WebSocket connections
5. Exit 0

### Error Classification & Recovery

| Error | Classification | Recovery |
|---|---|---|
| WebSocket disconnect (exchange or network) | `external_disconnect` | Buffer deltas, request snapshot, merge, resume |
| Sequence gap after merge (buffer overflow) | `internal_buffer_overflow` | Emit gap marker, fresh snapshot cycle |
| Sequence gap after merge (logic error) | `internal_merge_error` | Emit gap marker, fresh snapshot cycle, critical alert |
| QuestDB ILP write failure | Transient | Retry with backoff up to 30s, log and continue |
| QuestDB WAL suspended | Operational | Auto-resume via `ALTER TABLE RESUME WAL` |
| Redis write failure | Transient | Retry with backoff up to 10s, then emit gap marker |
| KuCoin token expiry | Scheduled | Renew every 23.5h; if renewal fails, reconnect with fresh token |

## Project Scoping

### Strategy & Philosophy

**Approach:** Correctness-first single release. All defined capabilities ship together because they form a single correctness guarantee — no subset delivers meaningful value without the others. The minimum viable version of this service is one that captures every tick it can and explicitly flags every tick it cannot; anything less is a different (weaker) product.

**Resource requirements:** Solo developer (mrqdt). Deployment target: Hetzner CPX41 (~€26/month). No external dependencies beyond exchange APIs, Redis, and QuestDB.

### Complete Feature Set

**Core User Journeys Supported:** All four journeys (happy path monitoring, gap incident response, deployment/canary, Candle Service integration). All FR1–FR36 are in scope for this release.

**Nice-to-Have (lower implementation priority):**
- Grafana dashboard JSON configuration (post-deployment, no aggregator changes required)
- Alert routing to PagerDuty (implementable via Prometheus Alertmanager externally)
- Structured JSON log output for log aggregation

**Explicitly out of scope:**
- Support for exchanges beyond KuCoin and Bybit
- Symbol hot-reload without restart (restart-required by design)
- Trade signal computation (belongs to Candle Service)

### Risk Mitigation Strategy

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Snapshot/delta merge race condition | Medium | High | L3 mock WS tests cover all 6 branch conditions; L4 Toxiproxy tests cover real network partition |
| Bybit 20-connection state management | Medium | Medium | Each connection manages its own 10-topic slice independently |
| KuCoin token renewal timing edge case | Low | High | Renew at 23.5h; if renewal fails, close and re-authenticate from scratch |
| QuestDB WAL silent suspension | Low | Medium | Poll `wal_tables()` every 30s; auto-resume; Prometheus metric for WAL state |
| Delta buffer overflow during high-volatility reconnect | Low | Low | `internal_buffer_overflow` gap cause emitted — correct behavior, not silent failure |

**Resource:** Solo developer. Mitigated by TDD-first approach, Hetzner VM snapshots for 5-minute rollback, and post-deploy metrics/log monitoring. Infrastructure cost ~€27/month total.

## Functional Requirements

### Exchange Feed Connectivity

- **FR1:** The service can establish an authenticated WebSocket connection to KuCoin using a token obtained from the KuCoin REST API
- **FR2:** The service can renew its KuCoin WebSocket authentication token automatically before the 24-hour expiry window elapses
- **FR3:** The service can maintain a KuCoin connection heartbeat (ping/pong) at the exchange-required interval to prevent silent connection drops
- **FR4:** The service can distribute Bybit symbol subscriptions across multiple concurrent WebSocket connections to respect the 10-topics-per-connection limit
- **FR5:** The service can verify subscription confirmation for each symbol on each exchange before treating that feed as active
- **FR6:** The service can subscribe to L2 order book and trade event streams for up to 200 symbols per exchange simultaneously
- **FR7:** The service can detect when a WebSocket connection has been dropped by the exchange or the network
- **FR8:** The service can reconnect to an exchange feed after a disconnection and resume data capture without operator intervention

### Order Book State Management

- **FR9:** The service can maintain an in-memory L2 order book per symbol by applying sequential delta updates from the exchange feed
- **FR10:** The service can initialize a symbol's L2 order book from a REST snapshot when the WebSocket feed is first established or after a gap event
- **FR11:** The service can buffer incoming delta messages for a symbol while a REST snapshot request for that symbol is in flight
- **FR12:** The service can merge a received snapshot with its corresponding buffered deltas by replaying only deltas whose sequence numbers are greater than the snapshot's sequence number
- **FR13:** The service can detect that a received snapshot's sequence number is lower than the oldest buffered delta, indicating the merge window has been missed (stale snapshot)
- **FR14:** The service can detect incoming delta messages with sequence numbers at or below the last applied sequence number (out-of-order or duplicate)
- **FR15:** The service can discard out-of-order and duplicate delta messages without applying them to book state

### Gap Detection & Classification

- **FR16:** The service can detect a sequence number gap in a symbol's message stream when an incoming sequence number is non-contiguous with the last applied sequence number
- **FR17:** The service can classify each detected gap as exactly one of four causes: `internal_buffer_overflow`, `internal_merge_error`, `external_disconnect`, or `external_rate_limit`
- **FR18:** The service can emit an in-band gap marker into the same `ticks:{exchange}:{symbol}` Redis Stream as tick data, carrying `gap_cause`, `seq_before`, `seq_after`, and gap timestamp
- **FR19:** The service can write every emitted gap marker to the `gaps:log` Redis Stream regardless of gap cause
- **FR20:** The service can distinguish `internal_*` gap causes (bugs in this service) from `external_*` gap causes (exchange behaviour) in its monitoring output

### Data Output

- **FR21:** The service can publish normalized L2 update events to `ticks:{exchange}:{symbol}` Redis Streams with a stable, versioned schema
- **FR22:** The service can publish normalized trade events to `ticks:{exchange}:{symbol}` Redis Streams alongside L2 updates
- **FR23:** The service can write raw tick data to QuestDB via ILP in batched writes
- **FR24:** The service can detect QuestDB WAL suspension and automatically issue a resume command without operator intervention
- **FR25:** The service can bound Redis Stream memory usage by trimming streams to a maximum entry count
- **FR26:** Downstream consumers can read from `ticks:{exchange}:{symbol}` streams using Redis consumer groups and resume from their last-acknowledged position after a restart

### Operational Observability

- **FR27:** The operator can query the service's health status including per-exchange connection state, 24-hour gap count, and an overall status value of `ok`, `degraded`, or `critical`
- **FR28:** The operator can query the deployed binary's version, git SHA, and build timestamp
- **FR29:** The operator can scrape a Prometheus metrics endpoint exposing per-symbol tick rates, gap counts by cause, feed connection state, and write latency distributions
- **FR30:** The operator can identify whether the aggregator has been a source of data loss (any `internal_*` gap) versus whether a gap was caused by exchange behaviour (`external_*`) without inspecting logs

### Service Lifecycle & Configuration

- **FR31:** The operator can configure all exchange credentials, connection parameters, and symbol lists via environment variables without modifying source code or compiled binary
- **FR32:** The service can perform a startup validation gate: if all feeds have not confirmed subscriptions within 90 seconds, exit with a non-zero status
- **FR33:** The operator can trigger a graceful shutdown via SIGTERM that drains in-flight tick writes and flushes buffered QuestDB writes before the process exits
- **FR34:** The operator can validate that the running binary on a deployment target matches the intended git SHA via a version endpoint and build tooling

### Credential Security

- **FR35:** The service can load exchange API credentials exclusively from environment variables at runtime
- **FR36:** The service can prevent API credentials from appearing in log output, error messages, or panic stack traces

## Non-Functional Requirements

### Performance

- **NFR1:** Tick-to-Redis Stream write latency must be < 10ms at p99 (measured from WebSocket message receipt to Redis XADD completion)
- **NFR2:** Tick-to-QuestDB write latency must be < 500ms at p99 (batched writes; individual ticks need not be written immediately)
- **NFR3:** The service must sustain a minimum of 2,400 ticks/second across 400 simultaneous feeds without dropping messages
- **NFR4:** Steady-state memory footprint must remain below 512 MB RSS with all 400 feeds active
- **NFR5:** Steady-state CPU utilization must remain below 1 vCPU on a Hetzner CPX41 during normal operation

### Reliability

- **NFR6:** The service must automatically detect and begin reconnecting to a dropped feed within 5 seconds of the disconnect event, without operator intervention
- **NFR7:** The service must restore all feed subscriptions within 90 seconds of a clean process restart
- **NFR8:** A process crash must not produce silent data corruption — any tick or gap marker written to Redis Streams or QuestDB before the crash must be a complete, well-formed record
- **NFR9:** QuestDB write failures must not halt feed processing — the service must continue capturing ticks to Redis Streams even when QuestDB writes are failing or retrying
- **NFR10:** The service must operate continuously for ≥30 days without requiring an operator-initiated restart under normal exchange conditions

### Data Correctness

- **NFR11:** The service must produce zero `internal_*` gap causes under normal operating conditions — any `internal_*` gap event is a defect that requires investigation and is treated as a correctness regression
- **NFR12:** The external gap SLO is ≤1 `external_*` gap per symbol per 24-hour period — sustained exceedance indicates an exchange connectivity problem warranting investigation
- **NFR13:** The snapshot/delta merge must verify sequence overlap before resuming the live feed — an unverified merge is not acceptable
- **NFR14:** No delta message may be applied to L2 book state more than once — any message with sequence number ≤ last applied sequence must be discarded, not processed

### Security

- **NFR15:** Exchange API credentials must not appear in log output, error messages, panic stack traces, or Prometheus metric label values at any log level including DEBUG
- **NFR16:** Credentials must be loaded exclusively from environment variables at process startup — hardcoded defaults, config files committed to version control, and binary-embedded credentials are prohibited
- **NFR17:** The `/version`, `/health`, and `/metrics` endpoints must not require authentication (internal network only) — no credentials are exposed via these endpoints

### Integration Stability

- **NFR18:** The Redis Stream schema for tick messages and gap markers must be backward-compatible across service versions — new fields may be added, but existing field names and types must not change without a schema version increment
- **NFR19:** The Prometheus `/metrics` endpoint must expose all defined metrics at all times, including when feeds are disconnected — a disconnected feed must produce a metric value (e.g. `aggregator_feed_state=0`), not a missing metric
- **NFR20:** The `/health` endpoint must respond within 100ms regardless of exchange feed connection state

### Testability

- **NFR21:** The service must be testable at five independent layers (L1 pure functions, L2 state machine, L3 mock WebSocket, L4 fault injection, L5 live exchange) without modifying production code paths
- **NFR22:** The four injectable test seams (MockWSServer, FakeRedis/Toxiproxy, FakeQuestDB, MockClock) must be substitutable via interfaces or constructor injection — the production code must not reference test implementations directly
