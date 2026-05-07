---
stepsCompleted: ['step-01-validate-prerequisites', 'step-02-design-epics', 'step-03-create-stories', 'step-03-advanced-elicitation', 'step-04-final-validation']
status: complete
inputDocuments:
  - '_bmad-output/planning-artifacts/prd.md'
  - '_bmad-output/planning-artifacts/architecture.md'
candleServiceStepsCompleted: ['step-01-validate-prerequisites', 'step-02-design-epics']
candleServiceStatus: in-progress
candleServiceInputDocuments:
  - '_bmad-output/planning-artifacts/prd.md'
  - '_bmad-output/planning-artifacts/architecture.md'
  - 'docs/data-contract.md'
---

# magnum-opus - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for magnum-opus, decomposing the requirements from the PRD and Architecture into implementable stories.

## Requirements Inventory

### Functional Requirements

**Exchange Feed Connectivity**
- FR1: The service can establish an authenticated WebSocket connection to KuCoin using a token obtained from the KuCoin REST API
- FR2: The service can renew its KuCoin WebSocket authentication token automatically before the 24-hour expiry window elapses
- FR3: The service can maintain a KuCoin connection heartbeat (ping/pong) at the exchange-required interval to prevent silent connection drops
- FR4: The service can distribute Bybit symbol subscriptions across multiple concurrent WebSocket connections to respect the 10-topics-per-connection limit
- FR5: The service can verify subscription confirmation for each symbol on each exchange before treating that feed as active
- FR6: The service can subscribe to L2 order book and trade event streams for up to 200 symbols per exchange simultaneously
- FR7: The service can detect when a WebSocket connection has been dropped by the exchange or the network
- FR8: The service can reconnect to an exchange feed after a disconnection and resume data capture without operator intervention

**Order Book State Management**
- FR9: The service can maintain an in-memory L2 order book per symbol by applying sequential delta updates from the exchange feed
- FR10: The service can initialize a symbol's L2 order book from a REST snapshot when the WebSocket feed is first established or after a gap event
- FR11: The service can buffer incoming delta messages for a symbol while a REST snapshot request for that symbol is in flight
- FR12: The service can merge a received snapshot with its corresponding buffered deltas by replaying only deltas whose sequence numbers are greater than the snapshot's sequence number
- FR13: The service can detect that a received snapshot's sequence number is lower than the oldest buffered delta, indicating the merge window has been missed (stale snapshot)
- FR14: The service can detect incoming delta messages with sequence numbers at or below the last applied sequence number (out-of-order or duplicate)
- FR15: The service can discard out-of-order and duplicate delta messages without applying them to book state

**Gap Detection & Classification**
- FR16: The service can detect a sequence number gap in a symbol's message stream when an incoming sequence number is non-contiguous with the last applied sequence number
- FR17: The service can classify each detected gap as exactly one of four causes: `internal_buffer_overflow`, `internal_merge_error`, `external_disconnect`, or `external_rate_limit`
- FR18: The service can emit an in-band gap marker into the same `ticks:{exchange}:{symbol}` Redis Stream as tick data, carrying `gap_cause`, `seq_before`, `seq_after`, and gap timestamp
- FR19: The service can write every emitted gap marker to the `gaps:log` Redis Stream regardless of gap cause
- FR20: The service can distinguish `internal_*` gap causes (bugs in this service) from `external_*` gap causes (exchange behaviour) in its monitoring output

**Data Output**
- FR21: The service can publish normalized L2 update events to `ticks:{exchange}:{symbol}` Redis Streams with a stable, versioned schema
- FR22: The service can publish normalized trade events to `ticks:{exchange}:{symbol}` Redis Streams alongside L2 updates
- FR23: The service can write raw tick data to QuestDB via ILP in batched writes
- FR24: The service can detect QuestDB WAL suspension and automatically issue a resume command without operator intervention
- FR25: The service can bound Redis Stream memory usage by trimming streams to a maximum entry count
- FR26: Downstream consumers can read from `ticks:{exchange}:{symbol}` streams using Redis consumer groups and resume from their last-acknowledged position after a restart

**Operational Observability**
- FR27: The operator can query the service's health status including per-exchange connection state, 24-hour gap count, and an overall status value of `ok`, `degraded`, or `critical`
- FR28: The operator can query the deployed binary's version, git SHA, and build timestamp
- FR29: The operator can scrape a Prometheus metrics endpoint exposing per-symbol tick rates, gap counts by cause, feed connection state, and write latency distributions
- FR30: The operator can identify whether the aggregator has been a source of data loss (any `internal_*` gap) versus whether a gap was caused by exchange behaviour (`external_*`) without inspecting logs

**Service Lifecycle & Configuration**
- FR31: The operator can configure all exchange credentials, connection parameters, and symbol lists via environment variables without modifying source code or compiled binary
- FR32: The service can perform a startup validation gate: if all feeds have not confirmed subscriptions within 90 seconds, exit with a non-zero status
- FR33: The operator can trigger a graceful shutdown via SIGTERM that drains in-flight tick writes and flushes buffered QuestDB writes before the process exits
- FR34: The operator can validate that the running binary on a deployment target matches the intended git SHA via a version endpoint and build tooling

**Credential Security**
- FR35: The service can load exchange API credentials exclusively from environment variables at runtime
- FR36: The service can prevent API credentials from appearing in log output, error messages, or panic stack traces

### NonFunctional Requirements

**Performance**
- NFR1: Tick-to-Redis Stream write latency must be < 10ms at p99
- NFR2: Tick-to-QuestDB write latency must be < 500ms at p99 (batched writes)
- NFR3: The service must sustain a minimum of 2,400 ticks/second across 400 simultaneous feeds without dropping messages
- NFR4: Steady-state memory footprint must remain below 512 MB RSS with all 400 feeds active
- NFR5: Steady-state CPU utilization must remain below 1 vCPU on a Hetzner CPX41 during normal operation

**Reliability**
- NFR6: The service must automatically detect and begin reconnecting to a dropped feed within 5 seconds of the disconnect event
- NFR7: The service must restore all feed subscriptions within 90 seconds of a clean process restart
- NFR8: A process crash must not produce silent data corruption — any record written before the crash must be complete and well-formed
- NFR9: QuestDB write failures must not halt feed processing — the service must continue capturing ticks to Redis Streams even when QuestDB writes are failing or retrying
- NFR10: The service must operate continuously for ≥30 days without requiring an operator-initiated restart under normal exchange conditions

**Data Correctness**
- NFR11: The service must produce zero `internal_*` gap causes under normal operating conditions
- NFR12: The external gap SLO is ≤1 `external_*` gap per symbol per 24-hour period
- NFR13: The snapshot/delta merge must verify sequence overlap before resuming the live feed
- NFR14: No delta message may be applied to L2 book state more than once

**Security**
- NFR15: Exchange API credentials must not appear in log output, error messages, panic stack traces, or Prometheus metric label values at any log level including DEBUG
- NFR16: Credentials must be loaded exclusively from environment variables at process startup
- NFR17: The `/version`, `/health`, and `/metrics` endpoints must not require authentication

**Integration Stability**
- NFR18: The Redis Stream schema for tick messages and gap markers must be backward-compatible across service versions
- NFR19: The Prometheus `/metrics` endpoint must expose all defined metrics at all times, including when feeds are disconnected
- NFR20: The `/health` endpoint must respond within 100ms regardless of exchange feed connection state

**Testability**
- NFR21: The service must be testable at five independent layers (L1–L5) without modifying production code paths
- NFR22: The four injectable test seams (MockWSServer, FakeRedis/Toxiproxy, FakeQuestDB, MockClock) must be substitutable via interfaces or constructor injection

### Additional Requirements

Architecture-derived requirements that directly affect implementation scope:

- **ARC1:** Greenfield Go module — initialize with `go mod init github.com/mrqdt/magnum-opus/aggregator` and pin specific library versions: `nhooyr.io/websocket`, `github.com/redis/go-redis/v9`, `github.com/questdb/go-questdb-client/v3`, `github.com/prometheus/client_golang`, `github.com/stretchr/testify`
- **ARC2:** `.env.example` must document all environment variables with descriptions and example values — must be written before `internal/config/` implementation begins
- **ARC3:** `raw_ticks` QuestDB `CREATE TABLE` DDL must be written as the first story in `writer/questdb/` to prevent schema drift — field list: exchange, symbol, seq, ts_exchange, ts_local, side, price (string), size (string), event_type, is_gap, gap_cause (nullable)
- **ARC4:** Implementation sequence is ordered and must not be reordered: (1) config/+symbol/, (2) orderbook/+reconnect/+gapdetector/ with L1 tests, (3) exchange interface+transport/, (4) kucoin/+bybit/mux/+bybit/, (5) coordinator/interfaces.go then writer/redis/+writer/questdb/, (6) coordinator/, (7) metrics/+health/, (8) cmd/aggregator/main.go, (9) Makefile+docker-compose.yml+Dockerfile
- **ARC5:** Five-layer test architecture enforced via Go build tags: L1 (no tag), L2 (`//go:build l2`), L3 (`//go:build l3`), L4 (`//go:build l4`), L5 (`//go:build live`) — Makefile targets: test-l1, test-l2, test-l3, test-l4, test-all, test-live
- **ARC6:** CI/CD pipeline: GitHub Actions runs L1+L2 on every push/PR; `release.yml` builds multi-stage Docker image and pushes to `ghcr.io/mrqdt/magnum-opus/aggregator` on git tag push
- **ARC7:** Docker Compose deployment on Linode (8 vCPU, 16 GB RAM) with resource limits — aggregator: mem_limit 600m/cpus 2.0; redis: 2g; questdb: 8g; stop_grace_period: 15s
- **ARC8:** Linode VM snapshot before each deploy for 5-minute rollback
- **ARC9:** Credential sanitizing `slog.Handler` wrapper must be wired in `main.go` before any other package initializes its logger — covers DEBUG output and third-party library log calls
- **ARC10:** Dockerfile multi-stage build required: build stage (Go toolchain) + minimal runtime stage — not yet specified in architecture, must be authored as part of deployment story
- **ARC11:** All external dependencies must be actively maintained at time of adoption and on every future dependency update. Criteria: not archived, has had a commit within the last 12 months, has an active maintainer. The specific choice of `nhooyr.io/websocket` over the archived `gorilla/websocket` is the canonical example of this rule. Before adding any new dependency, verify active maintenance status. Pinned versions (e.g. QuestDB image tag) must be updated deliberately — not left on `:latest` — but must be periodically reviewed for security patches.

### UX Design Requirements

N/A — magnum-opus aggregator is a headless backend daemon with no user interface.

### FR Coverage Map

| FR | Epic | Area |
|---|---|---|
| FR1–FR8 | Epic 2 | Exchange feed connectivity (KuCoin auth/heartbeat/renewal, Bybit mux, subscription confirmation, reconnect) |
| FR9–FR15 | Epic 1 | Order book state machine (pure: apply delta, snapshot init, buffer, merge, dedup) |
| FR16–FR17 | Epic 1 | Gap detection and 4-cause classification (pure: gapdetector/) |
| FR18–FR19 | Epic 3 | In-band gap marker emission (coordinator + writer/redis) |
| FR20 | Epic 4 | Internal vs external gap visibility in monitoring output (/health, /metrics) |
| FR21–FR26 | Epic 3 | Redis Streams (ticks + gap markers), QuestDB ILP, MAXLEN trimming, consumer groups |
| FR27–FR30 | Epic 4 | /health, /version, /metrics, internal vs external gap distinguishability |
| FR31, FR35–FR36 | Epic 1 | Env-var config loading, credential isolation from logs/errors/panics |
| FR32–FR34 | Epic 4 | Startup validation gate, SIGTERM drain, deploy verification via /version |

## Epic List

### Epic 1: Project Foundation & Correctness Engine
The developer has a compiling Go module, full test infrastructure, and the three pure state machines — order book, reconnect/merge, and gap detection — proven exhaustively correct at L1 before any IO is written. This is the load-bearing correctness guarantee for the entire system.
**FRs covered:** FR9, FR10, FR11, FR12, FR13, FR14, FR15, FR16, FR17, FR31, FR35, FR36
**ARCs covered:** ARC1, ARC2, ARC5, ARC11
**NFRs covered:** NFR11, NFR12, NFR13, NFR14, NFR15, NFR16, NFR21, NFR22

### Epic 2: Exchange Feed Connectivity
The operator can connect to KuCoin (REST token auth, 24h renewal, ping/pong heartbeat) and Bybit (20-connection mux for 200 symbols), verify subscription confirmation per symbol, and have automatic reconnect trigger on disconnect — with L2/L3 tests covering exchange-specific failure modes.
**FRs covered:** FR1, FR2, FR3, FR4, FR5, FR6, FR7, FR8
**NFRs covered:** NFR6, NFR7

### Epic 3: Data Capture Pipeline & Gap Guarantees
The Candle Service can read a complete Redis Stream with normalized ticks and in-band gap markers at the correct sequence; QuestDB has a raw_ticks audit trail with WAL auto-recovery; and the coordinator wires all state machines into a functioning pipeline with backpressure isolation and retry policy.
**FRs covered:** FR18, FR19, FR21, FR22, FR23, FR24, FR25, FR26
**ARCs covered:** ARC3
**NFRs covered:** NFR1, NFR2, NFR3, NFR4, NFR8, NFR9, NFR18

### Epic 4: Operational Readiness & Deployment
The operator can monitor the service via /health (ok/degraded/critical), /version, and /metrics (Prometheus); deploy via Docker Compose + GitHub Actions to Linode; validate the running binary against the intended git SHA; and roll back in 5 minutes via VM snapshot.
**FRs covered:** FR20, FR27, FR28, FR29, FR30, FR32, FR33, FR34
**ARCs covered:** ARC4, ARC6, ARC7, ARC8, ARC9, ARC10
**NFRs covered:** NFR5, NFR10, NFR17, NFR19, NFR20

---

## Epic 1: Project Foundation & Correctness Engine

The developer has a compiling Go module, full test infrastructure, and the three pure state machines — order book, reconnect/merge, and gap detection — proven exhaustively correct at L1 before any IO is written. This is the load-bearing correctness guarantee for the entire system.

### Story 1.1: Go Module Initialization & Dependency Verification

As the developer,
I want to initialize the Go module with all verified, actively maintained dependencies,
So that the project compiles from day one and no archived or unmaintained library can enter the codebase.

**Acceptance Criteria:**

**Given** an empty project directory
**When** `go mod init github.com/mrqdt/magnum-opus/aggregator` is run and all dependencies are added
**Then** `go build ./...` succeeds with zero errors
**And** `go.mod` declares `go 1.21` minimum
**And** the following dependencies are pinned to specific versions: `nhooyr.io/websocket`, `github.com/redis/go-redis/v9`, `github.com/questdb/go-questdb-client/v3`, `github.com/prometheus/client_golang`, `github.com/stretchr/testify`
**And** `gorilla/websocket` does NOT appear in `go.mod` or `go.sum`
**And** each dependency has had a commit within the last 12 months (verified at time of pinning, noted in a comment in go.mod or a DEPS.md note)
**And** `go.mod` is annotated with a `// verified: YYYY-MM-DD` comment for each direct external dependency so drift is visible in git diff
**And** `make check-deps` (implemented in Story 1.7) is listed in the DEPS.md as the re-verification command to run before any dependency update

### Story 1.2: Configuration Loading & Credential Isolation

As the operator,
I want all service parameters loaded from environment variables with a credential-safe sealed type,
So that no API key, secret, or passphrase can ever appear in logs, error messages, or panic output.

**Acceptance Criteria:**

**Given** all required environment variables are set
**When** `config.Load()` is called
**Then** a valid `Config` struct is returned with all fields populated
**And** the credential fields are a sealed type with no `fmt.Stringer` or `error` implementation
**And** the raw string value of any credential is inaccessible after `Config` is constructed

**Given** one or more required environment variables are missing
**When** `config.Load()` is called
**Then** it returns an error listing exactly which variables are absent
**And** the process exits non-zero when called from `cmd/aggregator/`

**Given** the `.env.example` file
**Then** every environment variable accepted by `config.Load()` is documented with description and example value
**And** `.env` is listed in `.gitignore`

**Given** a credential value is accidentally passed to `fmt.Sprintf` or `slog`
**Then** the sealed type produces no output containing the raw credential value (verified by unit test attempting to log the config struct)

**Given** the credential-sanitizing slog handler is not yet wired (Epics 1–3 run before Story 4.3)
**Then** a lightweight redaction stub is registered in `internal/testutil/` from this story onwards — active in all test binaries and interim builds, not only in the production binary
**And** this stub is a minimal `slog.Handler` wrapper that redacts strings matching known credential patterns (API key format regex) from all log output

### Story 1.3: Symbol Normalization Package

As the service,
I want a canonical Symbol type that normalizes exchange-specific formats to a common representation,
So that KuCoin (`BTC-USDT`) and Bybit (`BTCUSDT`) produce the same value everywhere downstream.

**Acceptance Criteria:**

**Given** a KuCoin raw symbol string `"BTC-USDT"`
**When** `symbol.Normalize("kucoin", "BTC-USDT")` is called
**Then** it returns the canonical Symbol value

**Given** a Bybit raw symbol string `"BTCUSDT"` for the same pair
**When** `symbol.Normalize("bybit", "BTCUSDT")` is called
**Then** it returns the identical canonical Symbol value as the KuCoin case

**Given** the `symbol` package
**Then** it has zero imports from any other internal package
**And** `make test-l1` passes all symbol tests

### Story 1.4: L2 Order Book State Machine

As the service,
I want a pure order book that applies sequential delta updates and enforces strict sequence monotonicity,
So that out-of-order and duplicate deltas are silently discarded without corrupting the book state.

**Acceptance Criteria:**

**Given** an initialized `OrderBook` and a valid delta with seq N
**When** `Apply(delta)` is called
**Then** the book's bid/ask levels are updated correctly per the delta's side, price, and size
**And** the last-applied sequence number advances to N

**Given** an `OrderBook` with last-applied seq N
**When** `Apply(delta)` is called with seq == N (exact duplicate)
**Then** the delta is discarded without modifying book state — this is a distinct table-driven test row from the out-of-order case below

**Given** an `OrderBook` with last-applied seq N
**When** `Apply(delta)` is called with seq < N (out-of-order, from before last applied)
**Then** the delta is discarded without modifying book state
**And** no error is returned for either discard case (discard is always silent per FR15)

**Given** an `OrderBook`
**When** `Snapshot()` is called
**Then** it returns the complete current bid and ask level map with the current sequence number
**And** the returned snapshot is a value copy — mutations to it do not affect the book

**Given** the `orderbook` package
**Then** it has zero imports from any other internal package
**And** `make test-l1` runs with ≥95% branch coverage for `orderbook/` enforced by `-coverprofile` in the Makefile target

### Story 1.5: Reconnect & Snapshot/Delta Merge State Machine

As the service,
I want a pure reconnect state machine that buffers deltas during snapshot fetch and verifies sequence overlap before completing the merge,
So that reconnect events produce no data loss and stale snapshots are detected before they corrupt book state.

**Acceptance Criteria:**

**Given** the state machine in `Buffering` state receiving deltas
**When** a snapshot arrives with seq S that falls within the buffered delta range
**Then** the state machine transitions to `Live` and returns only the deltas with seq > S for replay
**And** no `NeedsSnapshot` signal is returned

**Given** the state machine in `Buffering` state
**When** a snapshot arrives with seq S lower than the oldest buffered delta (stale snapshot)
**Then** the state machine returns a `NeedsSnapshot` signal with `Reason: "merge_error"`
**And** a fresh snapshot cycle must be initiated by the caller

**Given** the state machine in `SnapshotPending` state
**When** a second gap is detected before the first snapshot arrives (double-NeedsSnapshot)
**Then** the first `NeedsSnapshot` signal is cancelled and a new one is returned
**And** deltas buffered during the first wait remain in the buffer for the new merge
**And** the new merge validates sequence bounds against the *new* snapshot's seq — not the first snapshot's seq — so stale deltas from before the second gap are not silently applied

**Given** an `OrderBook` with no prior state (cold start — first connection, not a reconnect)
**When** the first `NeedsSnapshot` signal is emitted and the REST snapshot is applied
**Then** this initial snapshot initialization path is tested as a distinct L1 scenario from the reconnect-merge path
**And** the test verifies the state machine starts in the correct initial state and transitions cleanly to `Live` after the first snapshot

**Given** a sequence number near the rollover boundary (e.g., `uint64` max)
**When** `Apply` is called with a delta whose seq wraps to 0
**Then** the rollover is handled correctly and does not produce a false gap detection
**And** this scenario is covered by an explicit L1 test fixture in `reconnect_test.go`

**Given** `CapturedWSSession` fixtures
**Then** there are at least two distinct reconnect fixtures: one where the snapshot arrives before all deltas are buffered (snapshot-arrives-early) and one where the snapshot arrives after additional deltas have accumulated (snapshot-arrives-late)

**Given** the `reconnect` package
**Then** it has zero imports from any other internal package
**And** `make test-l1` runs with ≥95% branch coverage for `reconnect/`

### Story 1.6: Gap Detection & 4-Cause Classification

As the service,
I want a pure gap detector that classifies every sequence discontinuity into exactly one of four exhaustive causes,
So that consumers can always determine whether a gap was a bug in this service or an exchange-side event.

**Acceptance Criteria:**

**Given** two contiguous sequence numbers (prev=N, next=N+1)
**When** `Detect(prev, next, clock)` is called
**Then** it returns `nil` (no gap)

**Given** a non-contiguous sequence with cause `external_disconnect`
**When** `Detect` is called with appropriate context
**Then** it returns a `GapEvent` with `Cause: external_disconnect`, correct `seq_before`, `seq_after`, and timestamp from the injected `Clock`

**Given** each of the four gap causes: `internal_buffer_overflow`, `internal_merge_error`, `external_disconnect`, `external_rate_limit`
**Then** each has a corresponding L1 test using `GapScenarioBuilder` that exercises its detection path
**And** no fifth cause value is ever produced
**And** the classification rules distinguishing `external_rate_limit` from `external_disconnect` are explicitly documented in `gapdetector/types.go` as a code comment (e.g., rate-limit cause is signalled by the exchange via a specific message type or HTTP 429 response, not by connection drop — the heuristic is stated so tests can verify the correct signal triggers the correct cause)

**Given** `time.Now()` is called anywhere inside `gapdetector/`
**Then** the build fails (enforced by a grep check in the Makefile `test-l1` target)

**Given** the `gapdetector` package
**Then** it has zero imports from any other internal package
**And** `make test-l1` runs with ≥95% branch coverage for `gapdetector/`

### Story 1.7: Test Infrastructure & L1 Coverage Gate

As the developer,
I want a split test infrastructure (pure L1 helpers separate from L2 mock fakes) and a Makefile `test-l1` target that enforces a 95% branch coverage floor,
So that Epic 2 cannot begin on unverified state machine logic and L2 build tags never leak into L1 test runs.

**Acceptance Criteria:**

**Given** `internal/testutil/`
**Then** it contains only L1-safe pure helpers: `MockClock`, `ClockAdvancer`, `GapScenarioBuilder`, `CapturedWSSession`
**And** none of these files carry a `//go:build l2` or higher tag
**And** no file in `internal/testutil/` imports `github.com/redis/go-redis` or `github.com/questdb`

**Given** `internal/testutil/mock/`
**Then** it contains `FakeRedis` (partial failure injection) and `FakeQuestDB` (WAL suspension simulation)
**And** every file carries `//go:build l2`
**And** `make test-l1` does NOT compile or run anything from `internal/testutil/mock/`

**Given** `make test-l1` is run
**Then** it runs `go test ./internal/orderbook/... ./internal/reconnect/... ./internal/gapdetector/... ./internal/symbol/... ./internal/backoff/... ./internal/config/...`
**And** it generates a coverage profile and fails if branch coverage for `orderbook/`, `reconnect/`, or `gapdetector/` is below 95%
**And** it runs a grep check that rejects any call to `time.Now()` or `time.Sleep()` outside `cmd/aggregator/`

**Given** `internal/backoff/`
**Then** it implements exponential backoff (initial=1s, multiplier=2, max=60s, jitter=±20%) as a pure `Wait(attempt int, clock Clock) time.Duration` function with no retry loops
**And** it accepts an injected `Clock` interface and never calls `time.Now()` directly

**Given** `make check-deps` target in the Makefile
**Then** it runs `go list -m -json all` and verifies each direct dependency in `go.mod` has had a real (non-bot) commit within the last 12 months
**And** it is included in `ci.yml` so dependency health is checked on every PR
**And** it fails the build with a clear message listing stale dependencies so the developer can evaluate and update or document an exception

**Given** the credential redaction stub in `internal/testutil/`
**Then** it is a minimal `slog.Handler` wrapper that redacts strings matching credential patterns from all log output
**And** it is registered as the default slog handler in a `TestMain` function in at least one test file per package that logs during tests
**And** this ensures credential redaction is active throughout Epics 1–3 before Story 4.3 wires the production handler

---

## Epic 2: Exchange Feed Connectivity

The operator can connect to KuCoin (REST token auth, 24h renewal, ping/pong heartbeat) and Bybit (20-connection mux for 200 symbols), verify subscription confirmation per symbol, and have automatic reconnect trigger on disconnect — with L2/L3 tests covering exchange-specific failure modes.

### Story 2.1: Exchange Interface & WebSocket Transport Layer

As the service,
I want a shared WebSocket transport layer that handles connection lifecycle, framing, and reconnect triggering,
So that exchange-specific adapters focus on protocol semantics rather than connection management.

**Acceptance Criteria:**

**Given** `internal/exchange/exchange.go`
**Then** it defines the `Exchange` interface only — no implementations
**And** interface names follow noun/noun+er pattern (`Exchange`, not `IExchange`)
**And** the interface includes methods for connecting, subscribing, receiving events, and graceful shutdown

**Given** `internal/exchange/transport/conn.go`
**Then** it uses `nhooyr.io/websocket` exclusively — `gorilla/websocket` must not appear anywhere
**And** read/write use `wsjson.Read(ctx, conn, &v)` / `wsjson.Write(ctx, conn, v)`
**And** graceful teardown cancels context first, then calls `conn.Close(StatusNormalClosure, "")`

**Given** a WebSocket connection that has been silently dropped
**When** the transport's keepalive detects no pong within the timeout window
**Then** a reconnect event is triggered within 5 seconds (NFR6)
**And** the reconnect event propagates to the owning exchange adapter via a **buffered** channel (minimum buffer size 1) — an unbuffered channel would silently drop the event if the receiver is busy when the send fires
**And** if the buffered channel is full (receiver already has a pending reconnect), the send is skipped — not blocked — and the existing pending reconnect is sufficient

**Given** `ctx` is cancelled
**When** the transport goroutine receives `ctx.Done()`
**Then** it exits cleanly without blocking
**And** every `for-select` loop has `case <-ctx.Done(): return` as a peer case

### Story 2.2: KuCoin WebSocket Feed Adapter

As the service,
I want a KuCoin exchange adapter that obtains a REST WebSocket token, maintains the connection with heartbeats, and parses L2 updates and trade events into normalized Deltas,
So that KuCoin market data flows correctly into the system from first connection through normal operation.

**Acceptance Criteria:**

**Given** the service starts with valid KuCoin credentials
**When** the KuCoin adapter connects
**Then** it fetches a WebSocket token from the KuCoin REST API before opening the WebSocket connection
**And** the connection is established within the token's 30-second validity window

**Given** the adapter is connected
**Then** it sends ping/pong at the exchange-required interval (FR3)
**And** if no pong is received within the timeout, transport triggers a reconnect

**Given** the adapter subscribes to symbols
**When** subscription confirmation messages arrive (which KuCoin may send as batch confirmations, not per-symbol)
**Then** each symbol is individually resolved from the batch and marked confirmed — a batch confirmation does not mark unconfirmed symbols as active
**And** a symbol with no confirmation within 30 seconds is logged at ERROR and retried
**And** a spurious confirmation for a symbol that was not subscribed is logged at WARN and discarded — it does not mark any symbol as active

**Given** up to 200 symbols are configured for KuCoin
**When** the adapter subscribes
**Then** all 200 subscriptions are sent and confirmations tracked — the adapter is tested with a fixture representing exactly 200 symbols to verify no off-by-one or channel capacity issues at the limit

**Given** a KuCoin L2 update message arrives on the wire
**When** `parser.go` processes it
**Then** it produces a normalized `orderbook.Delta` with `Symbol`, `seq`, `side`, `price` (string), `size` (string), and `ts_exchange`
**And** `price` and `size` are always string — never `float64`
**And** `parser.go` is a pure function tested at L3 using `.json` fixture files in `exchange/kucoin/testdata/fixtures/`

**Given** a disconnect occurs
**When** the adapter detects it via transport reconnect event
**Then** it emits a `NeedsSnapshot` signal to the coordinator channel and begins re-subscribing
**And** re-subscription completes within 90 seconds of a clean reconnect (NFR7)

**Given** `MockWSServer` for L3 tests
**Then** it is defined in `exchange/kucoin/kucoin_test.go` with `//go:build l3` — NOT in `internal/testutil/`

### Story 2.3: KuCoin Token Auto-Renewal

As the service,
I want the KuCoin WebSocket token to renew automatically before the 24-hour expiry window,
So that the feed never silently drops due to token expiry during sustained operation.

**Acceptance Criteria:**

**Given** a KuCoin token was obtained at startup
**When** 23.5 hours have elapsed (tested via injected `Clock` advancing with `MockClock`)
**Then** a fresh token is fetched from the KuCoin REST API before the current token expires

**Given** the token renewal request fails
**When** all retries via `internal/backoff/` are exhausted
**Then** the adapter closes the existing connection and re-authenticates from scratch (new token fetch → new WebSocket connection)
**And** no gap marker is emitted if the subsequent snapshot/delta merge succeeds

**Given** the renewal goroutine in `token.go`
**Then** it uses the injected `Clock` interface — never `time.Now()` directly
**And** it has `case <-ctx.Done(): return` so it exits cleanly on SIGTERM

**Given** token renewal is in progress (new token fetched, not yet applied to the connection)
**When** a WebSocket read or write occurs concurrently on the existing connection
**Then** the existing token remains valid until the new token is applied — the renewal is atomic from the connection's perspective
**And** no goroutine reads a partially-updated token value (access to the current token is protected by a mutex or channel handoff)

### Story 2.4: Bybit Connection Multiplexer

As the service,
I want a Bybit connection multiplexer that distributes up to 200 symbols across ~20 concurrent WebSocket connections,
So that Bybit's 10-topics-per-connection limit is respected without violating exchange TOS.

**Acceptance Criteria:**

**Given** 200 Bybit symbols are configured
**When** the multiplexer initializes
**Then** it creates `ceil(200/10) = 20` WebSocket connections
**And** each connection carries exactly ≤10 symbol subscriptions

**Given** the multiplexer is running with 20 connections
**When** one connection is dropped
**Then** only the symbols assigned to that connection re-subscribe
**And** symbols on the other 19 connections are unaffected with no duplicate subscriptions produced

**Given** subscription confirmation is expected per topic
**When** the multiplexer sends subscriptions
**Then** it waits for per-topic confirmation before marking that symbol active (FR5)
**And** an unconfirmed subscription after 30 seconds is logged at ERROR

**Given** two or more connections drop simultaneously
**When** the mux handles concurrent failure
**Then** all affected symbols receive `NeedsSnapshot` signals — no symbol is silently dropped from the routing table
**And** symbols on surviving connections are completely unaffected — no duplicate subscriptions, no missing symbols
**And** this concurrent-failure scenario is an explicit L2 test case

**Given** `mux.go`
**Then** it is a pure routing concern — transport connections are injected, not created internally
**And** routing logic is covered by L2 tests using injected fakes (no live connections required)
**And** `aggregator_feed_state` for every mux-managed symbol transitions to 0 within 5 seconds of that symbol's connection dropping — silence about a dropped symbol is never ambiguous

### Story 2.5: Bybit WebSocket Feed Adapter

As the service,
I want a Bybit exchange adapter that coordinates the connection mux, sends pings every ≤10 seconds, and parses L2 updates and trade events into normalized Deltas,
So that Bybit market data flows correctly across all 20 connections.

**Acceptance Criteria:**

**Given** the Bybit adapter is connected
**Then** it sends `{"op":"ping"}` every ≤10 seconds on each connection
**And** if no pong is received within 20 seconds, the transport triggers a reconnect for that connection

**Given** a Bybit L2 update message arrives on the wire
**When** `parser.go` processes it
**Then** it produces a normalized `orderbook.Delta` using the identical schema as KuCoin output
**And** `parser.go` is pure — tested at L3 via `.json` fixture files in `exchange/bybit/testdata/fixtures/`

**Given** a disconnect occurs on one of the 20 Bybit connections
**When** the adapter detects it
**Then** it emits `NeedsSnapshot` signals only for the symbols on that connection
**And** the other 19 connections continue uninterrupted
**And** reconnect and re-subscription completes within 90 seconds (NFR7)

**Given** `MockWSServer` for L3 tests
**Then** it is defined in `exchange/bybit/bybit_test.go` with `//go:build l3` — NOT in `internal/testutil/`

**Given** 20 connections are active and ping is sent on each ≤10s
**When** the Go scheduler is under load (simulated with `runtime.GOMAXPROCS` and concurrent goroutines in the L3 test)
**Then** no connection exceeds the 20-second pong timeout due to scheduler delay
**And** the ping goroutine for each connection uses `time.NewTicker` with a fixed interval — not cumulative sleep — so scheduler delays do not compound

---

## Epic 3: Data Capture Pipeline & Gap Guarantees

The Candle Service can read a complete Redis Stream with normalized ticks and in-band gap markers at the correct sequence; QuestDB has a raw_ticks audit trail with WAL auto-recovery; and the coordinator wires all state machines into a functioning pipeline with backpressure isolation and retry policy.

### Story 3.1: Coordinator Interface Definitions & Stream Schema

As the service,
I want the StreamWriter and ILPWriter interfaces defined as the authoritative downstream contract, and the raw_ticks QuestDB DDL written before any implementation begins,
So that schema drift is prevented and both writer implementations can be built and tested in parallel against a stable interface.

**Acceptance Criteria:**

**Given** `internal/coordinator/interfaces.go`
**Then** it defines `StreamWriter` and `ILPWriter` interfaces — owned by the consuming package (coordinator), not the implementing packages
**And** interface names follow noun/noun+er pattern
**And** `ctx context.Context` is the first parameter on every method that touches IO

**Given** the Redis Stream schema
**Then** tick message fields are documented: `type`, `exchange`, `symbol`, `seq`, `ts_exchange`, `ts_local`, `side`, `price` (string), `size` (string), `event`
**And** gap marker fields are documented: `type`, `exchange`, `symbol`, `gap_ts`, `gap_cause`, `seq_before`, `seq_after`
**And** `gap_cause` accepts exactly four values: `internal_buffer_overflow`, `internal_merge_error`, `external_disconnect`, `external_rate_limit` — no others

**Given** the QuestDB `raw_ticks` table
**Then** the `CREATE TABLE` DDL is written and committed before any `writer/questdb/` implementation code
**And** DDL fields match exactly: `exchange` (symbol), `symbol` (symbol), `seq` (long), `ts_exchange` (timestamp), `ts_local` (timestamp), `side` (symbol), `price` (string), `size` (string), `event_type` (symbol: `update`|`snapshot`|`trade`), `is_gap` (boolean), `gap_cause` (symbol, nullable)
**And** the DDL uses `CREATE TABLE IF NOT EXISTS raw_ticks` — idempotent so re-running on an existing deployment does not error
**And** the DDL is stored in `writer/questdb/schema.sql` and applied via migration on first startup

**Given** a new service version adds a field to the Redis Stream tick message schema
**Then** the new field is appended — existing fields are never renamed, retyped, or removed
**And** the schema version is documented in `internal/coordinator/interfaces.go` as a comment so breaking changes are detectable in code review
**And** the `StreamWriter` interface version comment is updated whenever the schema changes (NFR18)

### Story 3.2: Redis Stream Writer

As the service,
I want a Redis Stream writer that publishes tick messages and in-band gap markers with MAXLEN enforcement,
So that the Candle Service receives a complete, bounded stream with no silent gaps and no unbounded memory growth.

**Acceptance Criteria:**

**Given** a normalized tick event is ready to publish
**When** `StreamWriter.Write(ctx, tick)` is called
**Then** it executes `XADD ticks:{exchange}:{symbol} MAXLEN ~ 50000 * field value ...` with all tick fields
**And** `price` and `size` are written as strings — never numeric types
**And** timestamps are written as Unix milliseconds (int64)

**Given** a gap event is detected
**When** the coordinator calls `StreamWriter.WriteGap(ctx, gap)`
**Then** it writes the gap marker to `ticks:{exchange}:{symbol}` in-band (same stream as ticks) (FR18)
**And** it also writes the gap marker to `gaps:log` with `seq_gap` derived field (FR19)
**And** both writes use `MAXLEN ~ 50000` — never omitted

**Given** Redis is temporarily unavailable
**When** a write fails
**Then** it retries with `internal/backoff/` up to 10 seconds
**And** after max retries, it emits a gap marker and continues — it does NOT halt the capture goroutine (NFR9)

**Given** an L2 update event arrives
**When** `StreamWriter.Write(ctx, tick)` is called
**Then** the tick is written with `event_type: "update"` field in the Redis Stream entry

**Given** a trade event arrives
**When** `StreamWriter.Write(ctx, tick)` is called
**Then** the tick is written with `event_type: "trade"` field — explicitly distinct from `"update"` and verified as a separate L2 test case (FR22)

**Given** a Redis Stream consumer reads via `XREADGROUP`
**Then** the stream entries produced by this writer are compatible with consumer group reads — verified by a L2 test that performs an actual `XREADGROUP` call via `FakeRedis` and confirms the consumer can acknowledge and resume from its last position (FR26)

**Given** `FakeRedis` partial failure injection in L2 tests
**Then** the following scenarios are explicitly covered: write succeeds, write fails once then recovers, gap marker write fails after tick write succeeds
**And** duplicate gap markers on recovery are **acceptable** — the Candle Service is the deduplication boundary; the aggregator does not deduplicate gap markers
**And** this contract (aggregator may emit duplicate gap markers on retry) is documented in `coordinator/interfaces.go`
**And** `go-redis/v9` is used — `go-redis/v8` must not appear in imports

**Given** `writer/redis/`
**Then** `StreamWriter` interface is consumed here, not defined here — definition lives in `coordinator/interfaces.go`

### Story 3.3: QuestDB ILP Writer

As the service,
I want a QuestDB ILP writer that batches raw tick writes in 500ms windows and automatically resumes WAL suspension,
So that every tick has a complete sequencing audit trail without blocking the capture goroutine on write confirmation.

**Acceptance Criteria:**

**Given** a tick event arrives
**When** `ILPWriter.Write(ctx, tick)` is called
**Then** the tick is buffered — not immediately sent to QuestDB
**And** the sender goroutine owns a single ILP sender behind a channel (not goroutine-safe — single sender enforced)

**Given** the 500ms flush timer fires
**When** `sender.Flush(ctx)` is called
**Then** all buffered ticks are sent to QuestDB via ILP over TCP port 9009
**And** a flush returning nil does not guarantee rows are queryable (WAL accepted-not-committed — do not assert row presence immediately after flush in tests)

**Given** QuestDB WAL is suspended
**When** the writer polls `wal_tables()` every 30 seconds and detects suspension
**Then** it automatically issues `ALTER TABLE raw_ticks RESUME WAL` (FR24)
**And** the WAL state is exposed as a Prometheus metric
**And** the poll and resume are logged at WARN level

**Given** a QuestDB ILP write fails
**When** retried with `internal/backoff/` up to 30 seconds
**Then** after max retries, it logs at ERROR and continues — it does NOT propagate up to the capture goroutine (NFR9)
**And** `github.com/questdb/go-questdb-client/v3` is used — ILP connects to TCP port 9009, not HTTP port 9000

**Given** `FakeQuestDB` WAL suspension simulation in L2 tests
**Then** the accepted-not-committed semantics are explicitly tested (flush succeeds but rows not queryable)
**And** WAL suspension detection and auto-resume are covered as a distinct test scenario

**Given** `sender.Flush(ctx)` returns nil (accepted)
**Before** WAL commits the rows to queryable storage
**When** the process crashes
**Then** this data loss is detectable via a lightweight audit trail: before each flush the writer publishes a `flush-begin` log entry to a Redis key `questdb:flush:pending` with row count and timestamp; after successful flush it publishes `flush-complete` to the same key
**And** a monitoring alert fires if `questdb:flush:pending` has a begin entry with no corresponding complete entry within 60 seconds
**And** this flush audit is tested in L2: simulate flush-begin followed by process restart and verify the pending entry is detectable

### Story 3.4: Per-Symbol Coordinator Worker

As the service,
I want a per-symbol goroutine that wires the order book, gap detector, reconnect state machine, and writers into a complete processing pipeline with backpressure isolation,
So that tick capture continues even if Redis or QuestDB writes are slow or failing.

**Acceptance Criteria:**

**Given** a normalized `orderbook.Delta` arrives on the symbol's input channel
**When** the per-symbol goroutine (`coordinator/symbol.go`) processes it
**Then** it calls `orderbook.Apply(delta)` (pure, no IO)
**And** it calls `gapdetector.Detect(prev, next, clock)` (pure, no IO)
**And** if a gap is detected, it calls `StreamWriter.WriteGap` before writing the next tick
**And** it calls `reconnect` state machine logic and dispatches `NeedsSnapshot` if returned

**Given** the Redis writer channel is full (backpressure)
**When** the coordinator goroutine attempts to send a tick
**Then** it uses a `select` with `case <-ctx.Done(): return ctx.Err()` — it never blocks unconditionally
**And** the capture goroutine is not halted by downstream write pressure (NFR9)

**Given** an internal gap cause (`internal_buffer_overflow` or `internal_merge_error`) is detected
**Then** it is logged at `slog.Error` level (never WARN)
**And** `external_*` causes are logged at `slog.Warn`

**Given** a panic occurs inside the per-symbol goroutine
**Then** the goroutine recovers at its entry point only (not in helper functions)
**And** logs exchange, symbol, and `runtime/debug.Stack()`
**And** emits `internal_merge_error` gap marker, then re-enters the goroutine loop with a fresh snapshot cycle

**Given** SIGTERM arrives while the symbol goroutine has events in its output channel
**Then** the goroutine exits via `ctx.Done()` and any events remaining in the buffered channel are drained by the writer goroutine up to the 5-second drain window
**And** events that cannot be drained within 5 seconds are acknowledged as lost — this is documented as the acceptable data loss bound on SIGTERM (not zero-loss, but bounded and detectable via the QuestDB flush audit in Story 3.3)

**Given** L2 integration tests for `coordinator/symbol.go`
**Then** they use `FakeRedis` and `FakeQuestDB` from `internal/testutil/mock/` (tagged `//go:build l2`)
**And** cover: normal tick flow, gap detected and marker emitted, Redis failure continues capture, panic recovery, SIGTERM with in-flight events (verifies drain completes within 5s for a realistic event volume)

### Story 3.5: Snapshot Dispatch & Full Coordinator Orchestration

As the service,
I want a coordinator that manages the lifecycle of all per-symbol goroutines, dispatches snapshot requests when `NeedsSnapshot` is signalled, and drains all in-flight writes on shutdown,
So that the full pipeline from exchange feed to Redis/QuestDB runs as a single coherent unit.

**Acceptance Criteria:**

**Given** `coordinator.Run(ctx)` is called
**Then** it starts one per-symbol goroutine for each configured (exchange, symbol) pair
**And** manages a `sync.WaitGroup` for all goroutines
**And** passes the root `ctx` to every goroutine so cancellation propagates on SIGTERM

**Given** a per-symbol goroutine emits a `NeedsSnapshot` signal
**When** `coordinator/snapshot.go` receives it
**Then** it fetches the REST snapshot from the exchange (exchange-specific endpoint)
**And** feeds the snapshot back to the symbol goroutine's reconnect state machine
**And** the symbol goroutine resumes the live feed only after sequence overlap is verified

**Given** the root `ctx` is cancelled (SIGTERM)
**When** `coordinator.Shutdown()` is called
**Then** all per-symbol goroutines detect `ctx.Done()` and exit
**And** in-flight tick writes to Redis are drained (max 5 seconds) before the process exits
**And** the QuestDB ILP buffer is flushed
**And** `WaitGroup.Wait()` blocks until all goroutines have exited

**Given** a REST snapshot fetch is in-flight when SIGTERM arrives
**Then** the snapshot fetch context is cancelled immediately (it shares the root `ctx`) — it does not block shutdown
**And** the symbol goroutine detects cancellation, emits an `external_disconnect` gap marker for the in-progress symbol, and exits cleanly
**And** total shutdown completes within Docker's `stop_grace_period: 15s` even with snapshot fetches in-flight

**Given** end-to-end L2 integration test
**Then** it exercises: tick flow → Redis write → gap detected → gap marker written → NeedsSnapshot → snapshot fetched → merge → resume
**And** uses `FakeRedis`, `FakeQuestDB`, and `MockClock` from `internal/testutil/mock/` (tagged `//go:build l2`)
**And** verifies no duplicate gap markers are emitted on Redis write failure + recovery

---

## Epic 4: Operational Readiness & Deployment

The operator can monitor the service via /health (ok/degraded/critical), /version, and /metrics (Prometheus); deploy via Docker Compose + GitHub Actions to Linode; validate the running binary against the intended git SHA; and roll back in 5 minutes via VM snapshot.

### Story 4.1: Prometheus Metrics Registry

As the operator,
I want all Prometheus metrics defined at startup and always present in scrape responses,
So that monitoring dashboards never show missing series for disconnected or idle feeds.

**Acceptance Criteria:**

**Given** `internal/metrics/metrics.go`
**Then** it defines all named Prometheus vars: `aggregator_ticks_total{exchange,symbol}`, `aggregator_gap_total{exchange,symbol,cause}`, `aggregator_feed_state{exchange,symbol}`, `aggregator_consumer_lag_ms{exchange,symbol}`, `aggregator_questdb_write_latency_ms`
**And** it exports a `Register(prometheus.Registerer)` function — never `prometheus.MustRegister` or `prometheus.DefaultRegisterer`
**And** `internal/metrics/` has zero imports from any other internal package (leaf node)

**Given** a feed is disconnected
**When** `/metrics` is scraped
**Then** `aggregator_feed_state{exchange="bybit",symbol="BTCUSDT"}` returns `0` — the metric is present, not absent (NFR19)

**Given** an `internal_*` gap is detected
**When** the counter is incremented in `coordinator/symbol.go`
**Then** `aggregator_gap_total{cause="internal_merge_error"}` increments
**And** this counter increment path is tested at L2 using `FakeRedis` + `FakeQuestDB` (not deferred to Epic 4's HTTP wiring)

**Given** all configured (exchange, symbol) pairs are known at startup
**When** the coordinator initializes
**Then** `aggregator_feed_state` and `aggregator_ticks_total` are pre-initialized to zero for every (exchange, symbol) label combination before any feed connects
**And** a scrape immediately after startup returns all expected label combinations — no symbol is absent from `/metrics` because it hasn't connected yet (NFR19)

**Given** `prometheus.NewRegistry()` is constructed
**Then** it is passed to `internal/health/` via constructor injection — never accessed as a global

### Story 4.2: Health, Version & Metrics HTTP Endpoints

As the operator,
I want `/health`, `/version`, and `/metrics` HTTP endpoints that respond accurately and quickly regardless of feed state,
So that monitoring systems always have a reliable operational signal without authentication overhead.

**Acceptance Criteria:**

**Given** all feeds are connected and no `internal_*` gap has occurred in the last 24h
**When** `GET /health` is called
**Then** it returns `{"status":"ok","connected_feeds":N,"gap_count_24h":0,"uptime_seconds":N}` with HTTP 200

**Given** any feed is reconnecting
**When** `GET /health` is called
**Then** `status` is `"degraded"` — not `"ok"` and not `"critical"`

**Given** any `internal_*` gap has occurred in the last 24h
**When** `GET /health` is called
**Then** `status` is `"critical"` (FR30 — operator can distinguish internal vs external without inspecting logs)

**Given** any exchange feed state
**When** `GET /health` is called
**Then** it responds within 100ms — it reads in-memory coordinator state, performs no IO (NFR20)

**Given** `gap_count_24h` is reported in the `/health` response
**Then** it is computed from a rolling in-memory counter that resets the oldest bucket every hour (24 × 1h buckets) — not a cumulative counter from process start
**And** a unit test verifies that a gap event older than 24h is not counted, and a gap event within 24h is counted correctly
**And** the rolling window survives a process restart via the `gaps:log` Redis stream (read on startup to pre-populate the counter from the last 24h of entries)

**Given** `GET /version`
**Then** it returns `version`, `git_sha`, `build_time`, and `go_version` populated via build-time `-ldflags`

**Given** `GET /metrics`
**Then** it returns valid Prometheus text format via `promhttp.HandlerFor(registry, promhttp.HandlerOpts{})` — never `promhttp.Handler()`
**And** no authentication is required on any of the three endpoints (NFR17)

### Story 4.3: Service Composition Root & Credential-Sanitizing Logger

As the operator,
I want `cmd/aggregator/main.go` to wire all components with the credential-sanitizing slog handler initialized first and a startup gate that exits non-zero on subscription failure,
So that the binary is deployable, credential-safe at all log levels, and self-validates before serving traffic.

**Acceptance Criteria:**

**Given** `cmd/aggregator/main.go`
**Then** it is the sole composition root — no other package instantiates concrete implementations
**And** the credential-sanitizing `slog.Handler` wrapper is the first thing initialized, before any other package logs anything (ARC9)
**And** the slog handler redacts known credential patterns from all output at every level including DEBUG
**And** no package in the codebase uses `init()` for logging — enforced by a grep check in `make test-l1` that fails if any `init()` function contains a `slog.` or `log.` call

**Given** the credential-sanitizing slog handler
**Then** it is verified to be active in the **test binary** as well as the production binary — a test in `cmd/aggregator/` confirms the handler is registered before any test log output is produced
**And** this closes the credential redaction gap identified in Epics 1–3 (the testutil stub from Story 1.7 handles those epics; this story verifies the production handler replaces it correctly)

**Given** the credential-sanitizing slog handler
**Then** it is a custom `slog.Handler` implementing all four methods: `Enabled`, `Handle`, `WithAttrs`, `WithGroup`
**And** a unit test verifies that a log call containing a raw API key value produces redacted output

**Given** all required environment variables are present and the service starts
**When** all feeds confirm subscriptions
**Then** the `/health` endpoint transitions from `"starting"` to `"ok"` within 90 seconds (NFR7)

**Given** any feed fails to confirm subscription within 90 seconds of startup
**When** the startup gate fires
**Then** the process exits with a non-zero status (FR32)
**And** the failure is logged at ERROR with the list of unconfirmed symbols

**Given** SIGTERM is received
**When** the shutdown sequence runs
**Then** root context is cancelled → goroutines detect `ctx.Done()` → in-flight Redis writes drain (max 5s) → QuestDB ILP buffer flushed → WebSocket connections closed → process exits 0 (FR33)

### Story 4.4: Dockerfile & Docker Compose Deployment

As the operator,
I want a multi-stage Dockerfile and Docker Compose configuration for production and L4 testing,
So that the service runs in a reproducible environment with resource limits and rollback capability.

**Acceptance Criteria:**

**Given** the `Dockerfile`
**Then** it uses a multi-stage build: Go toolchain build stage → minimal runtime stage (no Go toolchain in final image)
**And** the final image runs as a non-root user
**And** build-time `-ldflags` inject `version`, `gitSHA`, and `buildTime` into the binary

**Given** `docker-compose.yml` (production)
**Then** it defines three services: `aggregator`, `redis:7-alpine`, `questdb/questdb:8.2.1` (pinned tag — never `:latest` or `:8.x`)
**And** resource limits are set: aggregator `mem_limit: 600m`, `cpus: 2.0`; redis `mem_limit: 2g`; questdb `mem_limit: 8g`
**And** `stop_grace_period: 15s` is set on the aggregator service
**And** aggregator reaches Redis via `redis://redis:6379` and QuestDB via `questdb:9009` (Docker Compose internal DNS)
**And** credentials are loaded via `env_file: .env` — `.env` has `chmod 600` and is gitignored

**Given** `docker-compose.test.yml` (L4 only)
**Then** it defines toxiproxy, redis, and questdb — no aggregator service
**And** toxiproxy is used to inject network partitions for L4 fault injection tests

**Given** `docs/ops.md`
**Then** it documents: deployment procedure, pre-deploy VM snapshot steps, rollback procedure via Linode VM snapshot (5-minute target), credential rotation steps

### Story 4.5: CI/CD Pipeline & L4 Fault Injection Tests

As the operator,
I want a GitHub Actions CI/CD pipeline and complete Makefile that enforces the test gate at every push and automates image publishing on release,
So that no unverified code reaches the registry and deployments are reproducible.

**Acceptance Criteria:**

**Given** `.github/workflows/ci.yml`
**Then** it runs on every push and PR: `make test-l1` then `make test-l2`
**And** L1+L2 completes in under 60 seconds total
**And** a failed L1 or L2 blocks merge

**Given** `.github/workflows/release.yml`
**Then** it triggers on git tag push **only if the tagged commit has a passing `ci.yml` run** — implemented via `workflow_run` trigger or a required status check, so a broken commit cannot be tagged and published
**And** builds the Docker image with `go build -ldflags="-X main.version=$(VERSION) -X main.gitSHA=$(GIT_SHA) -X main.buildTime=$(BUILD_TIME)"`
**And** pushes to `ghcr.io/mrqdt/magnum-opus/aggregator` tagged with both the git SHA and the semver tag

**Given** `make test-l4`
**Then** it runs `scripts/wait-for-toxiproxy.sh` health-check gate before executing tests
**And** L4 tests cover: Redis TCP partition (write fails, gap marker emitted, capture continues), QuestDB TCP partition (write fails, capture unaffected), network flap (disconnect + reconnect + merge)
**And** L4 tests are tagged `//go:build l4` and require `docker-compose.test.yml` to be running

**Given** the complete Makefile
**Then** it defines: `test-l1`, `test-l2`, `test-l3`, `test-l4`, `test-all` (l1+l2+l3 fail-fast), `test-live`, `build`, `docker-build`, `verify-versions`
**And** `make verify-versions` confirms the SHA reported by `/version` on a running instance matches the intended git SHA

### Story 4.6: Live Exchange Tests

As the operator,
I want L5 live exchange tests,
So that every production deployment is validated against real exchange data before being tagged as a release.

**Acceptance Criteria:**

**Given** `make test-live`
**Then** it runs 18 L5 tests tagged `//go:build live` against actual KuCoin and Bybit WebSocket feeds
**And** if `KUCOIN_API_KEY` or `BYBIT_API_KEY` environment variables are absent, it exits with a clear message — not a confusing auth error
**And** all 18 tests pass before deploying

**Given** a new version is deployed to Linode
**Then** the operator monitors `/health`, `/metrics`, and logs for regressions
**And** the rollback procedure is documented in `docs/ops.md`: revert to Linode VM snapshot → verify `/version` returns previous SHA → confirm feeds reconnect within 90 seconds

**Given** no regressions are observed
**Then** the operator tags the release in git and the previous VM snapshot is retained for at least 7 days

---

# Candle Service — Epic Breakdown

## Overview

This section documents the epic and story breakdown for the Python Candle Service, the second major component of magnum-opus. It reads normalized ticks from Redis Streams produced by the Go aggregator and computes 1-second OHLCV + microstructure aggregates, multi-timeframe candles, and OB feature snapshots.

Input documents: `prd.md`, `architecture.md`, `docs/data-contract.md`

## Candle Service Requirements Inventory

### Functional Requirements

**Stream Reading**
- CS-FR1: Read `ticks:{exchange}:{symbol}` streams via Redis consumer group; on first connect start from current position (`$`); on restart resume from last-ack'd position
- CS-FR2: Parse both `type=tick` and `type=gap` entries from the stream
- CS-FR3: Deduplicate gap markers on `(seq_before, seq_after, gap_cause)` — aggregator may emit retries

**L2 Order Book Maintenance**
- CS-FR25: Maintain in-memory L2 order book per symbol: `event_type=snapshot` resets state, `event_type=update` applies deltas
- CS-FR27: On `event_type=snapshot`: immediately flush the current 1-second accumulator and reinitialize OB state from the snapshot data

**1-Second Aggregation**
- CS-FR4: Compute 1-second OHLCV bars (open, high, low, close, volume, quote_volume, trade_count, twap)
- CS-FR5: Track mid-price path per second (mid_price_open, mid_price_high, mid_price_low, vwmp)
- CS-FR6: Compute spread features per second (spread_high, spread_low, spread_mean, effective_spread)
- CS-FR7: Capture L2 OB state at open and close of each second (best_bid, best_ask, depth at L1/L2/top10/total for both open and close snapshots)
- CS-FR8: Compute book shape features (weighted_bid_price, weighted_ask_price)
- CS-FR9: Compute market impact features (depth_to_1pct_bid, depth_to_1pct_ask)
- CS-FR10: Compute OFI features (ofi: full-book, ofi_l1: L1-only)
- CS-FR11: Classify trades by aggressor side (`side=bid` → buy, `side=ask` → sell); accumulate buy_volume, buy_count
- CS-FR12: Classify block trades using rolling 99th-percentile threshold over last N trades per symbol (N = `BLOCK_TRADE_WINDOW`, default 1000); accumulate block_buy_volume, block_sell_volume
- CS-FR13: Compute trade distribution features (max_trade_size, large_bid_orders, large_ask_orders, first_trade_offset_ms, last_trade_offset_ms, trade_clustering as Gini coefficient of inter-trade intervals, max_consecutive_run)
- CS-FR14: Compute volatility features (realized_vol, realized_skewness, uptick_count, downtick_count)
- CS-FR15: Compute OB activity features (bid/ask_order_arrivals, bid/ask_cancel_count, ob_modify_count, avg_bid/ask_order_size, best_bid/ask_changes, quote_stuff_ratio)
- CS-FR16: Compute trade microstructure features (trade_sign_autocorr, inter_trade_interval_std_ms, num_trade_price_levels)
- CS-FR17: Track bar quality fields: gap_count (incremented per gap marker in window), bar_count

**Data Output**
- CS-FR18: Write completed 1-second bars to QuestDB `snapshot_1s` via ILP; for empty seconds write a null row (ts/exchange/symbol populated, all computed fields null)
- CS-FR19: Cascade 1-second bars to 1m, 5m, 15m, 1h, 4h, 1d, 1w OHLCV timeframes
- CS-FR20: Publish to `candles:{exchange}:{symbol}:{tf}` on every accumulator update (`is_complete: false`) and on bar close (`is_complete: true`); publish weekly bars also to `candles:1w:{exchange}:{symbol}` alias
- CS-FR21: Publish 1-second OB feature snapshots to `ob_features:{exchange}:{symbol}` on bar close

**Cold Storage**
- CS-FR22: Perform daily Parquet flush of `snapshot_1s` to Backblaze B2 in Hive-partitioned format (zstd compressed)
- CS-FR23: Write `flush_manifest` record to QuestDB after each flush attempt (success or failure, with row count, B2 path, error message if failed)
- CS-FR24: Publish to `alerts:flush_failure` Redis stream on daily flush failure

**Configuration**
- CS-FR26: Load all configuration from env vars: `REDIS_URL`, `QUESTDB_ILP_ADDR`, `SYMBOLS_KUCOIN`, `SYMBOLS_BYBIT`, `B2_*` credentials, `BLOCK_TRADE_WINDOW`, `LOG_LEVEL`

### Non-Functional Requirements

- CS-NFR1: Bar publish latency ≤2s from second boundary to QuestDB write
- CS-NFR2: Sustained write rate: ~400 rows/sec (200 symbols × 2 exchanges)
- CS-NFR3: Zero silent gap corruption: every gap marker in a window must increment gap_count
- CS-NFR4: On restart, replay all unacknowledged stream entries without double-counting bars
- CS-NFR5: Daily B2 flush must complete within 4 hours of the day boundary
- CS-NFR6: All credentials (Redis, QuestDB, B2) loaded exclusively from env vars
- CS-NFR7: Service containerized and co-deployed via docker-compose alongside aggregator
- CS-NFR8: Testable at minimum two layers: pure-function unit tests + integration tests using `fakeredis` (PyPI) and test-container QuestDB

### FR Coverage Map

| FR | Epic | Description |
|---|---|---|
| CS-FR1 | 5 | Redis consumer group, startup position |
| CS-FR2 | 5 | Tick/gap message parsing |
| CS-FR3 | 5 | Gap marker deduplication |
| CS-FR25 | 5 | In-memory L2 OB maintenance |
| CS-FR26 | 5 | Env-var configuration |
| CS-FR27 | 5 | Snapshot flush + OB reinit |
| CS-FR4 | 5 | 1s OHLCV computation |
| CS-FR18 (OHLCV write) | 5 | QuestDB ILP write — OHLCV fields only; full DDL created from day one |
| CS-FR5 | 6 | Mid-price path features |
| CS-FR6 | 6 | Spread features |
| CS-FR7 | 6 | OB state at open/close |
| CS-FR8 | 6 | Book shape features |
| CS-FR9 | 6 | Market impact features |
| CS-FR10 | 6 | OFI features |
| CS-FR11 | 7 | Trade direction classification |
| CS-FR12 | 7 | Block trade rolling percentile |
| CS-FR13 | 7 | Trade distribution features |
| CS-FR14 | 7 | Volatility features |
| CS-FR15 | 7 | OB activity features |
| CS-FR16 | 7 | Trade microstructure features |
| CS-FR17 | 7 | Gap count + bar quality |
| CS-FR18 (null rows) | 7 | Empty second null-row behaviour |
| CS-FR19 | 8 | Timeframe cascade engine |
| CS-FR20 | 8 | Redis candle stream output (time-based partial updates) |
| CS-FR21 | 8 | Redis OB feature snapshot output |
| CS-FR22 | 9 | Daily B2 Parquet flush |
| CS-FR23 | 9 | flush_manifest QuestDB write (DDL created in same story) |
| CS-FR24 | 9 | alerts:flush_failure publish |

## Candle Service Epic List

- Epic 5: Candle Service Foundation
- Epic 6: OB-Derived Features
- Epic 7: Trade & Quality Features
- Epic 8: Multi-Timeframe Cascade & Redis Output
- Epic 9: Cold Storage & Operations

---

## Epic 5: Candle Service Foundation

The operator can run the Candle Service alongside the aggregator, confirm it reads from Redis, and see basic OHLCV rows appearing in `snapshot_1s` in QuestDB.

**FRs covered:** CS-FR1, CS-FR2, CS-FR3, CS-FR4, CS-FR18 (OHLCV fields), CS-FR25, CS-FR26, CS-FR27

**Implementation notes:**
- Story 1: `snapshot_1s` CREATE TABLE DDL — full 67-column schema, all non-identity columns nullable. Written before any accumulator code. No migrations ever.
- Story 2: Python project setup — `candle-service/` directory, pyproject.toml, Dockerfile, docker-compose update (add candle-service service)
- Story 3: Redis consumer — consumer group per symbol, parse tick/gap messages, dedup gap markers, on snapshot event flush accumulator + reinit OB state (CS-FR27)
- Story 4: L2 OB state machine — in-memory per symbol, snapshot resets, update applies deltas (CS-FR25)
- Story 5: 1s OHLCV accumulator + QuestDB ILP writer — wall-clock second boundaries, write 8 OHLCV fields, leave remaining 59 columns null

**Done when:** `docker-compose up` starts the service, it connects to Redis, and QuestDB shows OHLCV rows in `snapshot_1s` for all configured symbols.

---

## Epic 6: OB-Derived Features

The `snapshot_1s` rows contain all order-book-derived fields: mid-price path, spread, depth at open/close, book shape, market impact, and OFI.

**FRs covered:** CS-FR5, CS-FR6, CS-FR7, CS-FR8, CS-FR9, CS-FR10

**Implementation notes:**
- All features in this epic are derived from the L2 OB state machine built in Epic 5
- Validation story recommended first: confirm OB state machine produces correct books against known tick fixtures before computing features from it
- OFI requires tracking book state changes across ticks (not just snapshots) — needs careful delta tracking in the accumulator

**Done when:** QuestDB `snapshot_1s` rows contain populated mid_price_*, spread_*, bid/ask_depth_*, weighted_*_price, depth_to_1pct_*, ofi, ofi_l1 fields.

---

## Epic 7: Trade & Quality Features

The `snapshot_1s` rows contain all trade-derived fields and quality metadata. The 67-field schema is fully populated for all active seconds.

**FRs covered:** CS-FR11, CS-FR12, CS-FR13, CS-FR14, CS-FR15, CS-FR16, CS-FR17, CS-FR18 (null-row behaviour for empty seconds)

**Implementation notes:**
- All features in this epic are derived from trade events (`event_type=trade`) in the tick stream — independent of the OB state machine
- CS-FR12 block trade threshold: rolling 99th-percentile over last N trades per symbol (N = `BLOCK_TRADE_WINDOW`, default 1000). Stateful with edge cases — deserves its own story, not bundled with trade flow
- Empty-second null-row: when no ticks arrive in a second, write a row with ts/exchange/symbol and all computed fields null
- gap_count increments once per gap marker received in the window; bar_count always increments

**Done when:** QuestDB `snapshot_1s` rows contain all 67 fields populated for active seconds; empty seconds produce null rows; gap markers correctly increment gap_count.

---

## Epic 8: Multi-Timeframe Cascade & Redis Output

Bots can subscribe to 1m–1w candles via Redis streams. Both in-progress and closed bars are published. OB feature snapshots are available for real-time signal consumers.

**FRs covered:** CS-FR19, CS-FR20, CS-FR21

**Implementation notes:**
- Cascade engine: 1s → 1m → 5m → 15m → 1h → 4h → 1d → 1w. Clock boundary owned by an asyncio timer loop, NOT the Redis consumer (prevents off-by-one bar assignment bugs)
- **Partial bar publish cadence: time-based (every 250ms per symbol per active timeframe), NOT on every tick.** Publishing on every tick at 2,400/sec × 400 symbols × 8 timeframes = ~7.7M Redis writes/minute — unsustainable. Cadence is configurable via env var
- Weekly bars also published to `candles:1w:{exchange}:{symbol}` alias
- OB feature snapshots published to `ob_features:{exchange}:{symbol}` on each 1s bar close

**Done when:** Redis streams receive candle messages at all 8 timeframes; bots can filter on `is_complete` to choose closed-bar-only or live-update behaviour; OB feature snapshots appear in `ob_features:*` streams.

---

## Epic 9: Cold Storage & Operations

Daily snapshots of `snapshot_1s` are archived to Backblaze B2. Flush failures are alerted. The complete system runs in production via docker-compose.

**FRs covered:** CS-FR22, CS-FR23, CS-FR24

**Implementation notes:**
- `flush_manifest` CREATE TABLE DDL written in the same story as the first flush implementation — schema-first, no migrations
- Daily Parquet flush: zstd compressed, Hive-partitioned by date, runs at configurable daily time
- Flush must complete within 4 hours of day boundary (CS-NFR5)
- On failure: write flush_manifest row with error field populated AND publish to `alerts:flush_failure`

**Done when:** QuestDB data older than 30 days is present in B2; flush_manifest shows successful flush records; a simulated failure produces an entry in both flush_manifest and alerts:flush_failure.
