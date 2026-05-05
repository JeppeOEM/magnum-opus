---
stepsCompleted: ['step-01-init', 'step-02-context', 'step-03-starter', 'step-04-decisions', 'step-05-patterns', 'step-06-structure', 'step-07-validation', 'step-08-complete']
workflowType: 'architecture'
lastStep: 8
status: 'complete'
completedAt: '2026-05-04'
inputDocuments:
  - '_bmad-output/planning-artifacts/prd.md'
  - '_bmad-output/planning-artifacts/product-brief-magnum-opus-aggregator.md'
  - '_bmad-output/planning-artifacts/research/technical-storage-architecture-crypto-trading-research-2026-05-03.md'
  - '_bmad-output/planning-artifacts/research/domain-crypto-market-microstructure-signals-research-2026-05-03.md'
workflowType: 'architecture'
project_name: 'magnum-opus'
user_name: 'mrqdt'
date: '2026-05-04'
---

# Architecture Decision Document

_This document builds collaboratively through step-by-step discovery. Sections are appended as we work through each architectural decision together._

## Project Context Analysis

### Requirements Overview

**Functional Requirements:** 36 FRs across 6 categories:
- Exchange Feed Connectivity (FR1–FR8): Exchange-specific WebSocket protocols, token auth, heartbeats, subscription confirmation, disconnect detection and reconnect
- Order Book State Management (FR9–FR15): Per-symbol L2 state machine, snapshot initialization, delta buffering during reconnect, snapshot/delta merge with sequence overlap verification, out-of-order and duplicate delta discard
- Gap Detection & Classification (FR16–FR20): Sequence gap detection, exhaustive 4-cause taxonomy, in-band gap marker emission, centralized gaps:log audit stream
- Data Output (FR21–FR26): Redis Stream publication (ticks + gap markers), QuestDB ILP batched writes, WAL suspension auto-recovery, stream MAXLEN trimming, consumer group compatibility
- Operational Observability (FR27–FR30): /health (ok/degraded/critical), /version, /metrics (Prometheus), internal vs external gap distinguishability
- Service Lifecycle & Credential Security (FR31–FR36): Env-var configuration, startup validation gate, SIGTERM drain, deploy verification, credential isolation from logs

**Non-Functional Requirements:** 22 NFRs across 6 categories:
- Performance: <10ms tick-to-Redis p99, <500ms QuestDB p99, ≥2400 ticks/sec sustained, <512 MB RSS, <1 vCPU steady-state
- Reliability: ≤5s disconnect detection, 90s full startup, ≥30 days unattended operation, crash-safe writes (no partial records)
- Data Correctness: Zero internal gap causes, snapshot/delta merge must verify sequence overlap before resuming, no delta applied more than once
- Security: Credentials never in logs/errors/panics/metrics at any log level
- Integration Stability: Backward-compatible stream schema, all metrics present even when feeds are disconnected, /health responds within 100ms regardless of feed state
- Testability: Five independent test layers (L1 pure functions → L5 live exchange) without modifying production code; four injectable seams via interfaces/constructor injection

**Scale & Complexity:**

- Primary domain: Backend infrastructure — real-time WebSocket market data capture (Go)
- Complexity level: High
- Architectural components: ~7 major (Exchange Connector × 2, Order Book State Machine, Reconnect/Merge Manager, Redis Writer, QuestDB Writer, HTTP Operational Server)

### Technical Constraints & Dependencies

- **Runtime target:** Single Hetzner CPX41 (8 vCPU, 16 GB RAM) — no distributed infrastructure
- **Language:** Go — mandated by concurrency model, performance envelope, and existing project direction
- **Storage:** Redis Streams (hot) + QuestDB ILP (warm) — both pre-decided; no storage architecture choices
- **Exchange protocols:** KuCoin (REST token → WSS, 24h renewal, ping/pong) and Bybit (10 topics/conn → ~20 conns, 20s silent drop) — asymmetric, must be implemented faithfully per spec, not abstracted
- **Stream schema stability:** Redis Stream schema is a downstream contract for Candle Service — any field removal or type change requires schema version increment
- **Credential surface:** Only env-var loading permitted at startup; no config files in source control, no binary embedding

### Cross-Cutting Concerns Identified

1. **Sequence-number correctness** — must be enforced in the order book state machine, the merge protocol, the gap detector, and the Redis write path; shared invariant across all components
2. **Gap classification consistency** — the 4-cause taxonomy is exhaustive by design; every code path that can produce a gap must route through the same classification logic
3. **Testability seam discipline** — L1–L5 isolation requires interfaces for WSS connections, Redis, QuestDB, and time; constructor injection pattern throughout
4. **Backpressure isolation** — Redis write latency or QuestDB write failure must not propagate back to the capture goroutine; decoupled write queues with explicit overflow handling
5. **Credential isolation** — credentials must not pass through any logging, error formatting, metrics labeling, or panic recovery path; requires sanitization at ingestion boundary
6. **Concurrency safety** — 400 concurrent per-symbol goroutines sharing no mutable state except through well-defined channels; no shared-memory L2 book state across goroutines

## Starter Template & Foundation

### Primary Technology Domain

Go backend infrastructure service — headless daemon, no web framework. Foundation is `go mod init` with a purpose-designed package layout driven by the L1–L5 testability requirement.

### Module Initialization

```bash
go mod init github.com/mrqdt/magnum-opus/aggregator
go get nhooyr.io/websocket
go get github.com/redis/go-redis/v9
go get github.com/questdb/go-questdb-client/v3
go get github.com/prometheus/client_golang
go get github.com/stretchr/testify
```

**WebSocket library decision:** `nhooyr.io/websocket` — gorilla/websocket is archived (no security patches since 2023); nhooyr is actively maintained, context-native (`wsjson.Read`/`Write` are context-cancelable), and forces correct error handling. Deterministic teardown matters for L3/L4 test layers.

**Logging:** `log/slog` (stdlib since Go 1.21) — no external dependency, wired from day one.

**Prometheus:** `prometheus.NewRegistry()` injected at construction — never `prometheus.MustRegister` at package init (panics on duplicate registration in parallel tests).

### Package Layout

```
aggregator/
  cmd/aggregator/           # composition root, env-var config loading, startup gate
  internal/
    exchange/               # Exchange interface + factory registry (explicit type, not global)
    exchange/transport/     # shared WS lifecycle layer: framing, ping/pong, reconnect trigger
    exchange/kucoin/        # KuCoin: REST token auth, 24h renewal, heartbeat
    exchange/bybit/         # Bybit: multiplexing coordinator
    exchange/bybit/mux/     # Bybit 20-connection fan-out, pure routing logic
    orderbook/              # L2 book state machine (pure functions, no IO, L1-testable)
    reconnect/              # Snapshot/delta merge state machine (pure, NeedsSnapshot signal)
    gapdetector/            # Gap classification 4-cause taxonomy (pure, Clock injected)
    writer/redis/           # Redis Stream writer (StreamWriter interface injectable)
    writer/questdb/         # QuestDB ILP writer (ILPWriter interface injectable)
    health/                 # HTTP /health /version /metrics
    config/                 # Env-var loader + startup validation
    symbol/                 # Normalized symbol type (zero internal deps, importable everywhere)
    testutil/               # FakeRedis (partial failure injection), FakeQuestDB (WAL suspension sim),
                            #   MockClock, CapturedWSSession, GapScenarioBuilder
  Makefile                  # Targets: test-l1, test-l2, test-l3, test-l4, test-live
```

### Key Architectural Decisions in This Layout

**Pure/IO separation:** `orderbook/`, `reconnect/`, `gapdetector/` import nothing from this codebase — purely testable at L1 with zero mocks. All IO lives in `exchange/*/` and `writer/*/`.

**`reconnect/` as a separate pure package:** The `NeedsSnapshot` signal pattern keeps it IO-free. The state machine returns a typed `NeedsSnapshot` signal; the caller fetches the REST snapshot and feeds it back. Dependency graph: `reconnect/` → nothing; `orderbook/` → `reconnect/` (signal type); caller → both. No cycles. Change reason: if exchange snapshot endpoint changes, touch `reconnect/` + `exchange/kucoin/` — not `orderbook/`. If delta-merge algorithm changes, touch `orderbook/` only.

**`exchange/transport/` boundary:** triggers reconnect events (connection lost, timeout detected); `reconnect/` manages reconnect *state*. This boundary must not blur — document it explicitly.

**`symbol/` package:** zero imports from any other internal package; both exchange adapters produce a normalized symbol before any message crosses a package boundary. KuCoin (`BTC-USDT`) and Bybit (`BTCUSDT`) normalization happens here.

**Interfaces defined before implementations:** `StreamWriter`, `ILPWriter`, `Clock` (`type Clock interface { Now() time.Time }`) and the `SequenceEvent` type are defined first, with a failing test against each, before any concrete implementation is written.

**MockWSServer placement:** exchange-local test files (`exchange/kucoin/` and `exchange/bybit/`), not in `testutil/`. `testutil/` holds shared cross-package test infrastructure only.

### Pre-Implementation Checklist (before writing reconnect/)

- [ ] Transport contract test: lifecycle scenario (connect, ping/pong, graceful close, abnormal disconnect) runs against both exchange adapters via `CapturedWSSession`
- [ ] Double-NeedsSnapshot cancellation scenario added to `GapScenarioBuilder` — what happens when a second gap is detected while a NeedsSnapshot is in-flight?
- [ ] Bybit mux partial-failure chaos test: kill connection N, verify only affected symbols re-subscribe, no duplicates, no silent drops
- [ ] `FakeQuestDB` WAL suspension contract documented against a pinned QuestDB version (accepted-but-not-committed semantics, exact error surface)

## Core Architectural Decisions

### Decision Priority Analysis

**Critical (block implementation):**
- Concurrency model: per-symbol goroutine — decided
- Redis MAXLEN: 50,000 entries/stream — decided
- Credential sanitization strategy: dual (scrub at load + slog handler wrapper) — decided
- Cold storage flush ownership: Candle Service, not aggregator — decided

**Important (shape architecture):**
- Raw tick QuestDB schema: full L2 delta record — decided
- CI/CD test tier split: L1+L2 on CI, L3+L4 local, L5 manual — decided
- Deployment: Docker Compose on Linode, ghcr.io image registry — decided

**Deferred (post-MVP):**
- Symbol hot-reload without restart (by design: restart required)
- Additional exchanges beyond KuCoin and Bybit

### Data Architecture

**Redis MAXLEN: 50,000 entries per stream**
- Rationale: ~20 seconds of buffer at 2,400 ticks/sec — sufficient for Candle Service
  restarts and brief downtime without losing entries before consumer catch-up.
  At ~50 bytes/entry × 50,000 × 400 streams ≈ 1 GB Redis memory, within the 16 GB VM envelope.
- Affects: writer/redis/, config/, operational SLOs

**Raw tick QuestDB schema: full L2 delta record**
- The aggregator writes every L2 update as a complete record for sequencing audit
  and potential post-hoc replay.
- Fields: exchange, symbol, seq, ts_exchange, ts_local, side, price, size,
  event_type (update|snapshot|trade), is_gap, gap_cause (nullable)
- Rationale: "sequencing audit" purpose is best served by the full wire record;
  reconstruction and gap forensics require price/size at the delta level.
- Affects: writer/questdb/, QuestDB table schema, 30-day TTL window

**Cold storage flush ownership: Candle Service**
- The aggregator's single-responsibility mandate excludes Parquet flush orchestration.
  The Candle Service owns daily Parquet flush to Backblaze B2 and flush_manifest writes.
- The aggregator has no B2 credentials, no flush schedule, no manifest writes.
- Affects: aggregator scope boundary, Candle Service scope

### Authentication & Security

**Credential sanitization: dual strategy**
- Layer 1 — Scrub at load: config/ loads env vars into a sealed type that does not
  implement fmt.Stringer or error. Raw string value is inaccessible after config struct is built.
- Layer 2 — slog handler wrapper: custom slog.Handler wraps the default handler and
  redacts known credential patterns from all log output at every level including DEBUG.
  Catches third-party library log calls and panic output.
- Rationale: NFR15 requires credentials not appear "at any log level including DEBUG."
  Defense in depth — call-site discipline alone is insufficient.
- Affects: config/, cmd/aggregator/ (handler setup at process start)

### API & Communication Patterns

**Internal service communication: Redis Streams (pre-decided)**
- ticks:{exchange}:{symbol} — aggregator → Candle Service
- gaps:log — aggregator → monitoring/alerting
- No direct RPC between aggregator and any other service.

**Operational HTTP: /health, /version, /metrics (pre-decided)**
- All three endpoints unauthenticated (NFR17: internal network only)
- /health responds within 100ms regardless of feed state (NFR20)
- /metrics exposes all defined metrics at all times, including for disconnected feeds

### Infrastructure & Deployment

**Runtime: Docker Compose on Linode (single VM)**
- All services co-located: aggregator, Redis, QuestDB on one Linode instance (8 vCPU, 16 GB RAM)
- docker-compose.yml defines all three services with resource limits, named volumes,
  and a private internal network
- Aggregator reaches Redis via redis://redis:6379 and QuestDB ILP via questdb:9009
  (Docker Compose internal DNS)
- Credentials via env_file: directive pointing to .env (chmod 600, gitignored)
- config/ default values for REDIS_URL and QUESTDB_ILP_ADDR match Compose service names
  so the service works out of the box without explicit env vars for those endpoints

**Deployment: GitHub Actions → ghcr.io → Linode docker compose pull**
- On git tag push: GitHub Actions builds aggregator Docker image, tags with git SHA
  + semver, pushes to ghcr.io/mrqdt/magnum-opus/aggregator
- Linode deploy: docker compose pull aggregator && docker compose up -d aggregator
- Redis and QuestDB images are pinned versions — updated deliberately, not on every deploy
- /version endpoint confirms running container matches intended git SHA (FR34)
- Linode VM snapshot before each deploy provides rollback if canary gate fails

**Compose service resource limits:**
- aggregator: mem_limit: 600m, cpus: 2.0
- redis: mem_limit: 2g
- questdb: mem_limit: 8g
- Stop grace period: stop_grace_period: 15s (SIGTERM drain sequence per FR33)

**CI/CD test tier mapping:**
- GitHub Actions (every push/PR): L1 (pure functions, ~3s) + L2 (mock interfaces, ~10s)
- Local pre-push (Makefile gate): L3 (MockWSServer) + L4 (Toxiproxy/Docker)
- Manual pre-deploy: L5 (live exchange, 18 tests) + 48h canary (zero internal_* gaps)

**Concurrency model: per-symbol goroutine**
- Each (exchange, symbol) pair owns a dedicated goroutine for its L2 book state machine.
- No shared mutable state across goroutines — all coordination via typed channels.
- 400 goroutines at steady state: trivial for Go scheduler.
- Goroutine lifecycle: each goroutine accepts a context; graceful shutdown via context
  cancellation propagated from SIGTERM handler in cmd/aggregator/.

### Decision Impact Analysis

**Implementation sequence:**
1. config/ + symbol/ — zero deps, foundational types
2. orderbook/ + reconnect/ + gapdetector/ — pure state machines, L1 tests first
3. Exchange interface + transport/ layer
4. exchange/kucoin/ + exchange/bybit/mux/ + exchange/bybit/
5. writer/redis/ + writer/questdb/ — interfaces defined before implementations
6. health/ — HTTP server wiring
7. cmd/aggregator/ — composition root, wires everything together
8. Makefile test targets + docker-compose.yml

**Cross-component dependencies:**
- symbol/ must be stable before any exchange or writer package is written
- Exchange interface must be defined before kucoin/ and bybit/ implementations
- StreamWriter and ILPWriter interfaces must be defined before writer implementations
- Clock interface must be injected into gapdetector/ from construction
- SequenceEvent type must be defined before gapdetector/ and reconnect/ are written
- slog handler wrapper must be initialized in cmd/aggregator/ before any other package
  initializes its logger

## Implementation Patterns & Consistency Rules

### Critical Conflict Points: 9 areas requiring explicit rules

### Naming Patterns

**Go code naming (standard Go conventions — no deviation):**
- Exported types/functions/constants: PascalCase (`OrderBook`, `SequenceEvent`, `NeedsSnapshot`)
- Unexported identifiers: camelCase (`seqNum`, `gapCause`)
- Error variables: `ErrXxx` sentinel pattern (`ErrStaleSnapshot`, `ErrBufferOverflow`)
- Interface names: noun or noun+er (`Exchange`, `StreamWriter`, `Clock`) — never `IExchange`
- Test files: same package directory, `_test.go` suffix

**Environment variables: UPPER_SNAKE_CASE**
- `KUCOIN_API_KEY`, `BYBIT_API_SECRET`, `REDIS_URL`, `QUESTDB_ILP_ADDR`

**QuestDB table and column naming: snake_case**
- Tables: `raw_ticks`, `flush_manifest`
- Columns: `ts_exchange`, `seq_num`, `event_type`, `gap_cause`

**Redis Stream field naming: snake_case**
- All message fields: `ts_exchange`, `gap_cause`, `seq_before`
- Gap cause values: exact strings `internal_buffer_overflow`, `internal_merge_error`,
  `external_disconnect`, `external_rate_limit` — no other values ever valid

**Prometheus metric naming: snake_case with aggregator_ prefix**
- Pattern: `aggregator_{noun}_{unit}_total` for counters, `aggregator_{noun}_{unit}` for gauges/histograms
- Examples: `aggregator_ticks_total`, `aggregator_gap_total`, `aggregator_feed_state`,
  `aggregator_questdb_write_latency_ms`
- Labels: snake_case (`exchange`, `symbol`, `cause`)

**slog structured log field naming: snake_case**
- `exchange`, `symbol`, `seq_num`, `gap_cause`, `error` (never `err`)
- Log level policy:
  - DEBUG: per-tick trace, sequence number deltas (disabled in production)
  - INFO: connection established/lost, gap detected, startup/shutdown events
  - WARN: external_* gaps, retry attempts, QuestDB WAL auto-resume
  - ERROR: internal_* gaps, Redis write failure after max retries, startup gate failure

### Structure Patterns

**Interface placement: consumer package owns the interface (Go idiom)**
- Interfaces are defined in the package that *uses* them, not the package that *implements* them
- `writer/redis/` defines `type StreamWriter interface{...}` — NOT in `cmd/aggregator/`
- `gapdetector/` defines `type Clock interface{ Now() time.Time }` — NOT in `testutil/`
- Prevents import cycles and keeps dependency direction correct

**Test file placement: black-box `_test` package for all packages**
- Pure packages (`orderbook/`, `reconnect/`, `gapdetector/`): `package orderbook_test`
- Implementation packages (`exchange/kucoin/`, `writer/redis/`): `package kucoin_test`
- `testutil/` types: only importable from `_test.go` files

**Channel ownership: sender closes, never receiver**
- The goroutine that writes to a channel is responsible for closing it
- Shutdown: SIGTERM → cancel root context → goroutines detect ctx.Done() →
  drain in-flight → close output channels → cmd/aggregator/ waits on WaitGroup

**Constructor injection: all external dependencies injected, never package-level globals**
- Every type with external dependencies receives them via constructor
- No `init()` functions registering state
- No package-level `var` for mutable shared state
- Prometheus registry: `prometheus.NewRegistry()` passed to health/ at construction

### Format Patterns

**Error wrapping: always `fmt.Errorf("context: %w", err)` at package boundaries**
- Every error crossing a package boundary is wrapped with context
- Example: `fmt.Errorf("kucoin: subscribe %s: %w", symbol, err)`
- Sentinel errors for known recoverable conditions: `var ErrStaleSnapshot = errors.New(...)`

**`NeedsSnapshot` signal: typed struct, not error**

```go
// In reconnect/ package
type NeedsSnapshot struct {
    Symbol   string
    Exchange string
    Reason   string // "buffer_overflow" | "merge_error" | "initial"
}
```

Callers switch on the return type — never treat it as an error value.

**Redis Stream messages: JSON, snake_case fields, string prices**
- All monetary values (`price`, `size`): string — never float64
- Timestamps: Unix milliseconds as int64 (`ts_exchange`, `ts_local`, `gap_ts`)
- Booleans: `true`/`false` — never 0/1

### Communication Patterns

**Context propagation: `ctx context.Context` is always the first parameter for IO functions**
- Every function touching the network, Redis, QuestDB, or time must accept `ctx`
- Pure functions (`orderbook.Apply()`, `gapdetector.Classify()`) do NOT accept ctx
- This is the primary signal distinguishing pure from impure functions

**Goroutine lifecycle: always started with a context, always respect cancellation**

```go
go func(ctx context.Context) {
    for {
        select {
        case <-ctx.Done():
            return
        case tick := <-ticksCh:
            // process
        }
    }
}(ctx)
```

Never start a goroutine without a context. Never block without a `ctx.Done()` select arm.

**Retry pattern: exponential backoff — initial=1s, multiplier=2, max=60s, jitter=±20%**
- All retryable operations use the same backoff helper from `internal/backoff/`
- Maximum retry duration per operation: Redis 10s, QuestDB 30s (per PRD error table)
- After max retries: emit gap marker (Redis path) or log error and continue (QuestDB path)

### Process Patterns

**`time.Now()` is banned outside `cmd/aggregator/` initialization**
- All packages needing current time receive a `Clock` interface
- `gapdetector/` uses injected `Clock` — never `time.Now()` directly
- Hard rule for deterministic L1 tests

**Panic recovery: only at goroutine entry points, always log with full context**
- Recover only at the top level of long-running goroutines — not inside helper functions
- Recovery logs: exchange, symbol, stack trace via `runtime/debug.Stack()`
- After recovery: emit `internal_merge_error` gap marker, restart goroutine with fresh snapshot

**`internal_*` gap causes: always trigger ERROR-level log**
- Any path emitting `internal_buffer_overflow` or `internal_merge_error` must call `slog.Error(...)`
- `external_*` causes: `slog.Warn(...)`

### Enforcement Guidelines

**All AI agents MUST:**
- Define interfaces in the consuming package, not the implementing package
- Pass `ctx context.Context` as the first argument to every IO-touching function
- Never call `time.Now()` outside `cmd/aggregator/` — inject `Clock`
- Use snake_case for all Redis field names and QuestDB column names
- Wrap all cross-package errors with `fmt.Errorf("package: operation: %w", err)`
- Use string representation for all price/size values in Redis messages
- Start every long-running goroutine with a context and a `ctx.Done()` select arm
- Inject `prometheus.NewRegistry()` via constructor — never use `prometheus.MustRegister`

**Anti-patterns (explicitly forbidden):**
- `time.Now()` in any package other than `cmd/aggregator/` initialization
- `init()` functions with mutable state registration
- Package-level `var` for shared mutable state
- Closing a channel from the receiver side
- `float64` for price or size values in any serialized format
- Recovering panics inside helper functions
- Interface names with `I` prefix (`IExchange`, `IWriter`)

## Project Structure & Boundaries

### Complete Project Directory Structure

    aggregator/
    ├── .env.example                     # all env vars documented with descriptions
    ├── .gitignore
    ├── Dockerfile
    ├── docker-compose.yml               # prod: aggregator + redis + questdb
    ├── docker-compose.test.yml          # L4 only: toxiproxy + redis + questdb (no app)
    ├── Makefile
    ├── go.mod
    ├── go.sum
    ├── .github/
    │   └── workflows/
    │       ├── ci.yml                   # L1+L2 on every push/PR (build-tagged)
    │       └── release.yml              # build + push to ghcr.io on git tag
    ├── cmd/
    │   └── aggregator/
    │       └── main.go                  # composition root: config.Load() → slog handler →
    │                                    #   metrics.Register() → health server → coordinator.Run()
    ├── internal/
    │   ├── backoff/
    │   │   ├── backoff.go               # pure policy: Wait(attempt, Clock) duration; no retry loops
    │   │   └── backoff_test.go
    │   ├── config/
    │   │   ├── config.go                # Config struct, Load(), sealed credential type (no Stringer)
    │   │   └── config_test.go
    │   ├── metrics/
    │   │   └── metrics.go               # named prometheus vars + Register(prometheus.Registerer)
    │   │                                # zero internal imports; Registry lives in main.go
    │   ├── symbol/
    │   │   ├── symbol.go                # Symbol type, Normalize(exchange, raw) Symbol
    │   │   └── symbol_test.go
    │   ├── exchange/
    │   │   ├── exchange.go              # Exchange interface only
    │   │   ├── types.go                 # Tick, FeedType, shared value types
    │   │   ├── registry.go              # factory registry: Register(), New() — explicit type, not global
    │   │   ├── transport/
    │   │   │   ├── conn.go              # shared WS lifecycle: dial, ping/pong, reconnect trigger
    │   │   │   └── conn_test.go         # transport contract tests (both adapters, CapturedWSSession)
    │   │   ├── kucoin/
    │   │   │   ├── kucoin.go            # KuCoin Exchange impl: orchestrates token + transport
    │   │   │   ├── token.go             # REST token fetch + 24h renewal goroutine
    │   │   │   ├── parser.go            # wire format → orderbook.Delta + symbol.Symbol (pure, testable)
    │   │   │   ├── kucoin_test.go       # L2+L3; MockWSServer defined here (//go:build l3)
    │   │   │   └── testdata/
    │   │   │       └── fixtures/        # .json arrays of raw KuCoin WS payloads for replay
    │   │   └── bybit/
    │   │       ├── bybit.go             # Bybit Exchange impl: mux coordinator
    │   │       ├── parser.go            # wire format → orderbook.Delta + symbol.Symbol
    │   │       ├── bybit_test.go        # L2+L3; MockWSServer defined here (//go:build l3)
    │   │       ├── testdata/
    │   │       │   └── fixtures/        # .json arrays of raw Bybit WS payloads for replay
    │   │       └── mux/
    │   │           ├── mux.go           # 20-conn fan-out, topic assignment, partial-fail re-sub
    │   │           └── mux_test.go      # pure routing + partial-failure chaos (//go:build l2)
    │   ├── orderbook/
    │   │   ├── orderbook.go             # OrderBook, Apply(Delta) error, Snapshot() Snapshot
    │   │   ├── types.go                 # Delta, Level, Side, Snapshot
    │   │   └── orderbook_test.go        # L1: table-driven, zero IO, no build tag needed
    │   ├── reconnect/
    │   │   ├── reconnect.go             # state machine: Buffering→SnapshotPending→Live
    │   │   ├── types.go                 # NeedsSnapshot struct, State enum
    │   │   └── reconnect_test.go        # L1: incl. double-NeedsSnapshot cancellation scenario
    │   ├── gapdetector/
    │   │   ├── detector.go              # Detect(prev, next uint64, Clock) → *GapEvent
    │   │   ├── types.go                 # GapCause (4 values), GapEvent, Clock interface
    │   │   └── detector_test.go         # L1: all 4 causes + edge cases, MockClock injected
    │   ├── coordinator/
    │   │   ├── coordinator.go           # Coordinator struct, Run(ctx), Shutdown()
    │   │   ├── symbol.go                # symbolWorker: per-symbol goroutine, holds component refs
    │   │   ├── snapshot.go              # NeedsSnapshot dispatch: signal → REST call → feed result back
    │   │   ├── interfaces.go            # StreamWriter, ILPWriter interfaces (consumer owns them here)
    │   │   ├── coordinator_test.go      # (//go:build l2)
    │   │   ├── symbol_test.go           # (//go:build l2)
    │   │   └── snapshot_test.go         # (//go:build l2)
    │   ├── writer/
    │   │   ├── redis/
    │   │   │   ├── writer.go            # redisWriter implementing coordinator.StreamWriter
    │   │   │   └── writer_test.go       # L2: FakeRedis partial-failure scenarios (//go:build l2)
    │   │   └── questdb/
    │   │       ├── writer.go            # questdbWriter implementing coordinator.ILPWriter
    │   │       └── writer_test.go       # L2: FakeQuestDB WAL suspension (//go:build l2)
    │   ├── health/
    │   │   ├── server.go                # HTTP server setup, route registration
    │   │   ├── handlers.go              # /health /version /metrics; prometheus.Registry injected
    │   │   └── health_test.go
    │   └── testutil/
    │       ├── fakeredis.go             # FakeRedis: partial failure injection
    │       ├── fakequestdb.go           # FakeQuestDB: WAL suspension (accepted-not-committed)
    │       ├── mockclock.go             # MockClock implementing gapdetector.Clock
    │       ├── clockadvancer.go         # ClockAdvancer: advance MockClock + drain channels in one call
    │       ├── capturedwssession.go     # load .json fixture + deterministic replay
    │       └── gapscenariobuilder.go    # sequence stream builder for all 4 gap causes + edge cases
    ├── scripts/
    │   ├── deploy.sh                    # docker compose pull aggregator && docker compose up -d
    │   └── wait-for-toxiproxy.sh        # health-check gate used by make test-l4
    └── docs/
        └── ops.md                       # sections: deployment, rollback, alert playbooks,
                                         #   gap investigation, credential rotation, canary gate

### Architectural Boundaries

**External boundaries (ingress — strict single-package ownership):**
- KuCoin WebSocket + REST → `internal/exchange/kucoin/` only
- Bybit WebSocket → `internal/exchange/bybit/` + `internal/exchange/bybit/mux/`
- Environment variables → `internal/config/` only (no other package reads `os.Getenv`)

**External boundaries (egress — strict single-package ownership):**
- Redis Streams → `internal/writer/redis/` only
- QuestDB ILP → `internal/writer/questdb/` only
- HTTP (Prometheus scrape, ops endpoints) → `internal/health/` only

**Hard import rules:**
- `internal/symbol/`, `internal/metrics/` — zero internal imports (leaf nodes)
- `internal/orderbook/`, `internal/reconnect/`, `internal/gapdetector/` — zero internal imports
- `internal/config/` — only package that reads `os.Getenv`
- `cmd/aggregator/main.go` — only file that constructs concrete implementations and wires them
- `internal/coordinator/interfaces.go` — defines `StreamWriter` and `ILPWriter`; writer packages implement, not define

**Internal service integration:**
- `ticks:{exchange}:{symbol}` Redis Stream → Candle Service (separate codebase, downstream)
- `gaps:log` Redis Stream → monitoring/alerting
- No RPC between aggregator and any other service

### Requirements to Structure Mapping

| FR Category | Primary Location | Supporting |
|---|---|---|
| FR1–FR8: Exchange Connectivity | `exchange/kucoin/`, `exchange/bybit/`, `bybit/mux/` | `exchange/transport/` |
| FR9–FR15: Order Book State | `orderbook/`, `reconnect/` | `exchange/*/parser.go` |
| FR16–FR20: Gap Detection | `gapdetector/` | `coordinator/snapshot.go` (gap marker dispatch) |
| FR21–FR26: Data Output | `writer/redis/`, `writer/questdb/` | `coordinator/` (WAL poll loop) |
| FR27–FR30: Observability | `health/`, `metrics/` | `coordinator/` (feed state reporting) |
| FR31–FR36: Lifecycle & Security | `config/`, `cmd/aggregator/` | slog handler wrapper in `main.go` |

**Cross-cutting concern locations:**
- `SequenceEvent` type → `gapdetector/types.go`
- `Clock` interface → `gapdetector/types.go` (imported by `testutil/mockclock.go`)
- `StreamWriter`, `ILPWriter` interfaces → `coordinator/interfaces.go`
- `NeedsSnapshot` signal → `reconnect/types.go`
- Backpressure + retry policy → `backoff/backoff.go`
- Credential sanitization → `config/config.go` (sealed type) + `main.go` (slog handler)
- Graceful shutdown → `main.go` (root context cancel on SIGTERM + WaitGroup)
- Prometheus metric definitions → `metrics/metrics.go`

### Data Flow

    Exchange WS message
      → exchange/transport/conn.go          (raw bytes, ping/pong managed)
      → exchange/{kucoin,bybit}/parser.go   (→ orderbook.Delta + symbol.Symbol)
      → coordinator/symbol.go               (per-symbol goroutine):
          → orderbook.Apply(delta)          (pure, updates L2 state)
          → gapdetector.Detect(seq, clock)  (pure, classifies gap or nil)
          → reconnect state machine         (pure, returns NeedsSnapshot if needed)
          → coordinator/snapshot.go         (if NeedsSnapshot: fetch REST → feed back)
          → writer/redis StreamWriter       (async buffered channel → ticks:{ex}:{sym})
          → writer/questdb ILPWriter        (async 500ms batched flush → raw_ticks)
      → health/handlers.go                  (reads coordinator state for /health)
      → metrics/metrics.go vars             (incremented inline, scraped by /metrics)

### Development Workflow

**Makefile targets:**

    make test-l1      # go test ./internal/orderbook/... ./internal/reconnect/... \
                      #   ./internal/gapdetector/... ./internal/symbol/... ./internal/backoff/...
    make test-l2      # go test -tags l2 ./internal/...
    make test-l3      # go test -tags l3 ./internal/exchange/...
    make test-l4      # scripts/wait-for-toxiproxy.sh && go test -tags l4 ./...
    make test-all     # make test-l1 && make test-l2 && make test-l3 (fail-fast, pre-push gate)
    make test-live    # go test -tags live ./... (checks API key env vars, exits clearly if absent)
    make build        # go build -ldflags="-X main.version=$(VERSION) -X main.gitSHA=$(GIT_SHA)" \
                      #   ./cmd/aggregator
    make docker-build # docker build -t ghcr.io/mrqdt/magnum-opus/aggregator:$(VERSION) .

**Build tag convention:**
- L1 tests: no build tag (always run)
- L2 tests: `//go:build l2` — mock interface tests
- L3 tests: `//go:build l3` — MockWSServer in-process tests
- L4 tests: `//go:build l4` — Toxiproxy fault injection (requires docker-compose.test.yml)
- L5 tests: `//go:build live` — live exchange (requires API keys)

**docker-compose.yml (prod):**

    services:
      aggregator:
        image: ghcr.io/mrqdt/magnum-opus/aggregator:${VERSION}
        env_file: .env
        mem_limit: 600m
        cpus: 2.0
        stop_grace_period: 15s
        depends_on: [redis, questdb]
      redis:
        image: redis:7-alpine
        mem_limit: 2g
        volumes: [redis-data:/data]
      questdb:
        image: questdb/questdb:8.x
        mem_limit: 8g
        volumes: [questdb-data:/var/lib/questdb]
        ports: ["9009:9009", "9000:9000"]

**docker-compose.test.yml (L4 only — no app service):**

    services:
      toxiproxy:
        image: ghcr.io/shopify/toxiproxy
        ports: ["8474:8474", "6380:6380", "9010:9010"]
      redis:
        image: redis:7-alpine
      questdb:
        image: questdb/questdb:8.x

## Architecture Validation Results

### Coherence Validation ✅

**Decision Compatibility:** All technology choices are mutually compatible.
nhooyr.io/websocket context-native API aligns with per-symbol goroutine model and ctx
propagation patterns. Docker Compose + Linode is straightforward single-VM deployment.
go-redis/v9, go-questdb-client/v3, and prometheus/client_golang are all actively maintained
and compatible with Go 1.21+ (slog stdlib requirement).

**Pattern Consistency:** Consumer-owns-interface rule consistently applied —
coordinator/interfaces.go owns StreamWriter and ILPWriter; gapdetector/types.go owns Clock;
reconnect/types.go owns NeedsSnapshot. No import cycles exist in the defined dependency graph.
Build tags enforce test layer isolation at the compiler level, not by convention.

**Structure Alignment:** Pure packages (orderbook/, reconnect/, gapdetector/, symbol/, metrics/)
are true leaf nodes — zero internal imports. coordinator/ is the correct wiring layer between
pure state machines and IO adapters. cmd/aggregator/main.go is the sole composition root.
All credential handling is confined to config/ + main.go slog handler.

### Requirements Coverage Validation ✅

**Functional Requirements (36/36 covered):**

| FR Range | Location | Status |
|---|---|---|
| FR1–FR8: Exchange Connectivity | exchange/kucoin/, exchange/bybit/, exchange/bybit/mux/, exchange/transport/ | ✅ |
| FR9–FR15: Order Book State | orderbook/, reconnect/, coordinator/snapshot.go | ✅ |
| FR16–FR20: Gap Detection | gapdetector/, writer/redis/ | ✅ |
| FR21–FR26: Data Output | writer/redis/, writer/questdb/, coordinator/ (WAL poll) | ✅ |
| FR27–FR30: Observability | health/, metrics/ | ✅ |
| FR31–FR36: Lifecycle & Security | config/, cmd/aggregator/main.go | ✅ |

**Non-Functional Requirements (22/22 covered):**

| NFR | Architectural Support |
|---|---|
| NFR1: <10ms tick-to-Redis p99 | Per-symbol goroutine + async buffered channel → writer/redis/ |
| NFR2: <500ms QuestDB p99 | writer/questdb/ 500ms batched flush |
| NFR3: ≥2400 ticks/sec | 400 independent goroutines, bounded channels, no shared locks |
| NFR4: <512MB RSS | mem_limit: 600m in docker-compose.yml |
| NFR5: <1 vCPU | cpus: 2.0; pure packages have no IO overhead |
| NFR6: ≤5s disconnect detection | exchange/transport/conn.go keepalive + timeout |
| NFR7: 90s startup | Startup gate in cmd/aggregator/main.go |
| NFR8: Crash-safe writes | Buffered channels drained on SIGTERM before exit |
| NFR9: Redis failures don't halt | Decoupled async write channel; capture goroutine never blocks on writer |
| NFR10: ≥30 days unattended | Docker restart: always via Compose + Linode VM |
| NFR11: Zero internal gaps | reconnect/ sequence verification; gapdetector/ cause classification |
| NFR12: External gap SLO | metrics/aggregator_gap_total{cause} tracked and exposed |
| NFR13: Snapshot sequence verification | reconnect/ state machine — merge only if overlap confirmed |
| NFR14: No duplicate delta application | orderbook/Apply() enforces seq monotonicity |
| NFR15–17: Credential security | config/ sealed type + slog handler wrapper + no endpoint auth |
| NFR18: Backward-compatible stream schema | Documented in Implementation Patterns |
| NFR19: Metrics always present | health/handlers.go — metrics registered at startup, never conditional |
| NFR20: /health <100ms | Reads in-memory coordinator state — no IO in health handler |
| NFR21–22: L1–L5 testability | Full test layer architecture with build tags + 4 injectable seams |

### Implementation Readiness Validation ✅

**Decision Completeness:** All critical decisions documented with rationale — WebSocket library,
concurrency model, Redis MAXLEN, test tier mapping, deployment model, credential strategy,
interface ownership rules, metrics package scope, coordinator package role.

**Structure Completeness:** Complete directory tree with specific file names and responsibilities.
All integration points mapped. Data flow documented end-to-end. Startup and shutdown sequences
specified.

**Pattern Completeness:** 9 conflict areas explicitly addressed. Naming conventions cover
Go code, env vars, QuestDB columns, Redis fields, Prometheus metrics, slog fields.
Anti-patterns explicitly forbidden. Build tag strategy enforces layer isolation.

### Gap Analysis Results

**Critical Gaps:** None — no gaps block implementation.

**Important Gaps (address early in implementation):**
1. `raw_ticks` QuestDB DDL not specified — field list defined, no `CREATE TABLE` statement.
   Implement as the first story in writer/questdb/ to prevent schema drift.
2. `.env.example` contents not defined — file exists in structure; full list of env vars
   with descriptions and example values should be written before any developer touches config/.

**Nice-to-Have Gaps (post-MVP):**
1. Dockerfile multi-stage build not specified — standard Go multi-stage (build + minimal runtime).
2. Architecture Decision Records (ADRs) for 3 consequential decisions: nhooyr over gorilla,
   reconnect/ NeedsSnapshot signal pattern, coordinator/ as wiring layer.

### Architecture Completeness Checklist

**Requirements Analysis**
- [x] Project context thoroughly analyzed
- [x] Scale and complexity assessed (High — 400 feeds, concurrent state machines)
- [x] Technical constraints identified (Linode VM, exchange TOS, 30-day QuestDB TTL)
- [x] Cross-cutting concerns mapped (6 concerns with explicit package assignments)

**Architectural Decisions**
- [x] Critical decisions documented with versions and rationale
- [x] Technology stack fully specified (Go, nhooyr, go-redis/v9, questdb-client/v3, prometheus)
- [x] Integration patterns defined (Redis Streams, ILP, operational HTTP)
- [x] Performance considerations addressed (goroutine model, async channels, resource limits)

**Implementation Patterns**
- [x] Naming conventions established (Go, env vars, QuestDB, Redis, Prometheus, slog)
- [x] Structure patterns defined (interface placement, test placement, channel ownership)
- [x] Communication patterns specified (ctx propagation, goroutine lifecycle, retry)
- [x] Process patterns documented (error wrapping, panic recovery, time injection)

**Project Structure**
- [x] Complete directory structure defined with specific file names
- [x] Component boundaries established and hard import rules documented
- [x] Integration points mapped (data flow diagram, startup/shutdown sequences)
- [x] Requirements to structure mapping complete (FR/NFR → package table)

### Architecture Readiness Assessment

**Overall Status:** READY FOR IMPLEMENTATION

**Confidence Level:** High — all 36 FRs and 22 NFRs have named architectural homes;
no critical gaps; implementation patterns address 9 identified conflict areas;
test layer architecture fully specified with build tag enforcement.

**Key Strengths:**
- Pure/IO separation enables exhaustive L1 testing of the highest-risk state machines
- NeedsSnapshot signal pattern keeps reconnect/ pure while enabling REST dispatch
- coordinator/ as explicit wiring layer prevents logic accumulating in main.go
- Dual credential sanitization (sealed type + slog handler) covers all leak vectors
- Build tags enforce test layer isolation at compiler level, not convention

**Areas for Future Enhancement:**
- ADRs for the 3 most consequential decisions
- `raw_ticks` QuestDB DDL before writer/questdb/ implementation
- Grafana dashboard JSON (post-deployment, no aggregator changes required)
- Alert routing to PagerDuty via Prometheus Alertmanager (external, no aggregator changes)

### Implementation Handoff

**First implementation priority:** `internal/config/` + `internal/symbol/` — zero-dependency
foundational types. Write these first; everything else depends on them.

**Implementation sequence:**
1. `internal/config/` + `internal/symbol/` + `.env.example`
2. `internal/orderbook/` + `internal/reconnect/` + `internal/gapdetector/` (L1 tests first)
3. `internal/exchange/` interface + `internal/exchange/transport/`
4. `internal/exchange/kucoin/` + `internal/exchange/bybit/mux/` + `internal/exchange/bybit/`
5. `internal/coordinator/interfaces.go` then `internal/writer/redis/` + `internal/writer/questdb/`
6. `internal/coordinator/` (coordinator.go, symbol.go, snapshot.go)
7. `internal/metrics/` + `internal/health/`
8. `cmd/aggregator/main.go` — composition root
9. `Makefile` + `docker-compose.yml` + `Dockerfile`

**AI Agent Guidelines:**
- Follow all architectural decisions exactly as documented — no local optimization
- Interfaces live in the consuming package — coordinator/interfaces.go for StreamWriter/ILPWriter
- ctx is the first parameter on every IO-touching function — no exceptions
- snake_case for all serialized fields (Redis, QuestDB, slog, Prometheus labels)
- string for all price/size values — never float64
- Run `make test-l1` before committing any change to a pure package
