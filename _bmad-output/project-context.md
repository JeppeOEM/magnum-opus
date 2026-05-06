---
project_name: magnum-opus
user_name: mrqdt
date: '2026-05-05'
sections_completed: ['technology-stack', 'language-specific-rules', 'testing-rules', 'code-quality-style', 'critical-rules']
status: complete
optimized_for_llm: true
---

# Project Context: magnum-opus Aggregator

_Critical implementation rules for AI agents. Each rule here is non-obvious — things that cause silent failures or wrong behavior if missed. Read before writing any code._

---

## Technology Stack & Versions

- **Language:** Go 1.21+ required — `log/slog` is stdlib from 1.21; `go.mod` must declare `go 1.21` minimum; use latest 1.21.x patch (not 1.21.0)
- **WebSocket:** `github.com/coder/websocket` v1.8.14 — NOT `gorilla/websocket` (archived) or `nhooyr.io/websocket` (deprecated; coder fork is the maintained successor); read/write via `wsjson.Read(ctx, conn, &v)` / `wsjson.Write(ctx, conn, v)` from `github.com/coder/websocket/wsjson`; graceful teardown: cancel context first, then call `conn.Close(websocket.StatusNormalClosure, "")` (sends close frame); use `conn.CloseNow()` when connection is already dead and you need a fast no-handshake close
- **Redis client:** `github.com/redis/go-redis/v9` — org changed from `go-redis` to `redis`; do NOT import `github.com/go-redis/go-redis/v8`; set `MaxLen` on every `XAdd` — never rely on global stream config
- **QuestDB client:** `github.com/questdb/go-questdb-client/v3` — ILP over TCP port **9009**, not HTTP port 9000; NOT goroutine-safe — `writer/questdb/` owns a single sender behind a channel; call `sender.Flush(ctx)` on a **500ms timer**; flush returning nil does not mean rows are queryable (WAL accepted-not-committed)
- **Metrics:** `github.com/prometheus/client_golang` — always constructor-injected `prometheus.NewRegistry()`; never `prometheus.MustRegister` or `prometheus.DefaultRegisterer`; use `promhttp.HandlerFor(registry, promhttp.HandlerOpts{})` — NOT `promhttp.Handler()`
- **Logging:** `log/slog` (stdlib); custom `slog.Handler` must implement all 4 methods: `Enabled`, `Handle`, `WithAttrs`, `WithGroup`
- **Testing:** `github.com/stretchr/testify`
- **Retry/backoff:** `internal/backoff/` (custom internal package) — do NOT add an external backoff dependency
- **Redis image:** `redis:7-alpine` — do not test against Redis 6 (Stream semantics differ)
- **QuestDB image:** pin to a specific tag (e.g. `questdb/questdb:8.2.1`) — do not use `:latest` or `:8.x`; WAL semantics are 8.x-specific
- **Docker Compose internal DNS:** `config/` defaults must use `redis:6379` and `questdb:9009` — not `localhost`
- **Go module path:** `github.com/mrqdt/magnum-opus/aggregator`

---

## Language-Specific Rules

### Interface Design
- Interfaces defined in the **consuming** package, not the implementing package:
  - `coordinator/interfaces.go` owns `StreamWriter` and `ILPWriter`
  - `gapdetector/types.go` owns its `Clock`; other packages needing time define their own compatible `Clock` interface — Go structural typing satisfies it without importing gapdetector
  - `reconnect/types.go` owns `NeedsSnapshot`
- Interface names: noun or noun+er (`Exchange`, `StreamWriter`, `Clock`) — never `IExchange`, `IWriter`

### Package Dependency Direction (no cycles tolerated)
- Leaf nodes (zero internal imports): `symbol/`, `metrics/`, `backoff/`, `orderbook/`, `reconnect/`, `gapdetector/`
- `config/` — only package that reads `os.Getenv`
- `coordinator/` — imports exchange/, orderbook/, reconnect/, gapdetector/, writer/*; nothing imports coordinator/
- `cmd/aggregator/main.go` — sole composition root; no other package imports cmd/

### Context Propagation
- `ctx context.Context` is the **first parameter** on every IO-touching function
- Pure functions (`orderbook.Apply()`, `gapdetector.Classify()`) do NOT accept ctx — the signal distinguishing pure from impure
- Every `for-select` loop must have `case <-ctx.Done(): return` as a **peer case**, not a nested check

### Blocking Channel Sends
- Never block unconditionally in a goroutine — always pair with ctx.Done():
  ```go
  select {
  case outCh <- event:
  case <-ctx.Done():
      return ctx.Err()
  }
  ```
  Omitting this causes goroutine leaks when a downstream consumer exits first.

### Time & Sleep Injection
- `time.Now()` **and** `time.Sleep()` are banned outside `cmd/aggregator/` initialization
- Each package needing time defines its own `type Clock interface { Now() time.Time }` — structural typing satisfies all implementations without cross-package imports
- `internal/backoff/` accepts a `Clock`; callers never call `time.Sleep()` directly
- `testutil/mockclock.go` satisfies all Clock interfaces via structural typing

### Error Handling
- All errors crossing a package boundary: `fmt.Errorf("package: operation: %w", err)`
- Sentinel errors: `var ErrStaleSnapshot = errors.New(...)`
- `NeedsSnapshot` is a typed struct signal — NOT an error:
  ```go
  // nil = proceed normally, non-nil = caller must fetch REST snapshot and re-feed
  func (sm *StateMachine) Apply(delta Delta) (*NeedsSnapshot, error)
  ```

### Constructor Injection & Shared State
- All external dependencies injected via constructor — no `init()`, no package-level `var` for mutable state
- `OrderBook` is owned by a single goroutine — no `sync.Mutex` on OrderBook; mutations only through the owning goroutine

### ILP Write Failure
- QuestDB ILP writes are fire-and-forget on the hot path — do NOT block the capture goroutine on write confirmation
- On TCP write error: log ERROR, continue — never propagate up to the WebSocket goroutine

### Goroutine Lifecycle & Ownership
- **Ownership:** exchange packages own feed-reading goroutines; coordinator owns per-symbol goroutines
- Channel ownership: sender closes, never receiver
- Shutdown sequence: SIGTERM → cancel root context → goroutines detect `ctx.Done()` → drain in-flight → close output channels → `WaitGroup.Wait()`

### Panic Recovery
- Recover only at goroutine entry points, never inside helper functions
- Mechanism: `for { }` loop with `defer recover()` inside the goroutine — not an external supervisor
- After recovery: log exchange + symbol + `runtime/debug.Stack()`, emit `internal_merge_error` gap marker, re-enter loop

### Naming Conventions
- Exported: `PascalCase` — `OrderBook`, `SequenceEvent`, `NeedsSnapshot`
- Unexported: `camelCase` — `seqNum`, `gapCause`
- Error sentinels: `ErrXxx` — `ErrStaleSnapshot`, `ErrBufferOverflow`

---

## Testing Rules

### Test Layer Architecture (build tags enforce isolation)

| Layer | Build Tag | Scope | Runs |
|---|---|---|---|
| L1 | *(none)* | Pure functions — orderbook/, reconnect/, gapdetector/, symbol/, backoff/ | CI + always |
| L2 | `//go:build l2` | Mock interfaces — FakeRedis, FakeQuestDB, coordinator wiring | CI |
| L3 | `//go:build l3` | MockWSServer in-process — exchange/kucoin/, exchange/bybit/ | Local pre-push |
| L4 | `//go:build l4` | Toxiproxy fault injection — requires docker-compose.test.yml | Local pre-push |
| L5 | `//go:build live` | Live exchange — requires API key env vars | Manual pre-deploy |

### Makefile Targets
- `make test-l1` / `make test-l2` / `make test-l3` / `make test-l4` / `make test-live`
- `make test-all` — l1 + l2 + l3, fail-fast (pre-push gate)

### Test Infrastructure (`testutil/` — only importable from `_test.go` files)
- `FakeRedis` — partial failure injection
- `FakeQuestDB` — WAL suspension (accepted-not-committed semantics, pinned to 8.x behavior)
- `MockClock` / `ClockAdvancer` — satisfies all Clock interfaces via structural typing
- `CapturedWSSession` — deterministic replay of `.json` fixture files
- `GapScenarioBuilder` — sequence stream builder for all 4 gap causes
- `MockWSServer` is exchange-local (`exchange/kucoin/`, `exchange/bybit/`) — NOT in testutil/

### Test Conventions
- Default: `package foo_test` (black-box); use `package foo` (white-box) only when testing unexported invariants — document why at file top
- Every test starting a goroutine must use `t.Cleanup` to cancel context and drain WaitGroup:
  ```go
  ctx, cancel := context.WithCancel(context.Background())
  t.Cleanup(func() { cancel(); wg.Wait() })
  ```
- Table-driven structure required: `[]struct{ name string; ... }{}` with `t.Run(tc.name, ...)`
- All test helpers calling `t.Fatal`/`t.Error` must call `t.Helper()` first
- Wire fixtures live in `exchange/{kucoin,bybit}/testdata/fixtures/` — never hardcode payloads inline

---

## Code Quality & Style Rules

### Naming by Layer
- **Redis Stream fields:** `snake_case` — `ts_exchange`, `gap_cause`, `seq_before`
- **QuestDB table/columns:** `snake_case` — table `raw_ticks`, columns `ts_exchange`, `seq_num`, `event_type`, `gap_cause`
- **Prometheus:** `aggregator_` prefix + `snake_case`; counters: `aggregator_{noun}_{unit}_total`; labels: `exchange`, `symbol`, `cause`
- **slog fields:** `snake_case`; use `error` not `err`
- **Env vars:** `UPPER_SNAKE_CASE` — `KUCOIN_API_KEY`, `BYBIT_API_SECRET`, `REDIS_URL`, `QUESTDB_ILP_ADDR`

### Serialization Value Types
- Price and size: **always string** — never `float64` in Redis, QuestDB, or JSON
- Timestamps: Unix milliseconds as `int64`
- Booleans: `true`/`false` — never `0`/`1`

### Gap Cause Values (exhaustive — exactly these 4, no others)
- `internal_buffer_overflow` → `slog.Error`
- `internal_merge_error` → `slog.Error`
- `external_disconnect` → `slog.Warn`
- `external_rate_limit` → `slog.Warn`

### Log Level Policy
- `DEBUG`: per-tick trace, sequence deltas (off in production)
- `INFO`: connection events, gap detected, startup/shutdown
- `WARN`: external gaps, retries, QuestDB WAL auto-resume
- `ERROR`: internal gaps, Redis write failure after max retries, startup gate failure

### Credential Rules
- Credentials never in logs/errors/panics/metrics at any level including DEBUG
- `config/` sealed type has no `fmt.Stringer` or `error` implementation
- slog handler wrapper in `main.go` redacts credential patterns from all output

### Retry Policy (uniform)
- Backoff: initial=1s, multiplier=2, max=60s, jitter=±20% — via `internal/backoff/`
- Max duration: Redis 10s, QuestDB 30s
- After max retries: emit gap marker (Redis) or log ERROR + continue (QuestDB)

---

## Critical Don't-Miss Rules

### Forbidden Anti-Patterns
- `time.Now()` or `time.Sleep()` outside `cmd/aggregator/` initialization
- `init()` with mutable state; package-level `var` for shared mutable state
- Closing a channel from the receiver side
- `float64` for price/size in any serialized format
- `sync.Mutex` on `OrderBook`
- Panic recovery inside helper functions
- `I`-prefix interfaces (`IExchange`, `IWriter`)
- `prometheus.MustRegister`, `prometheus.DefaultRegisterer`, `promhttp.Handler()`
- `gorilla/websocket`, `nhooyr.io/websocket` (deprecated), `github.com/go-redis/go-redis/v8`, any external backoff library
- `os.Getenv` outside `internal/config/`
- `:latest` or `:8.x` QuestDB image tags

### Redis Stream Schema (downstream contract)
- Keys: `ticks:{exchange}:{symbol}`, `gaps:log`
- `MAXLEN` 50,000 on every `XAdd` — never omit
- Field removal or type change requires schema version increment (Candle Service is downstream)

### QuestDB Schema
- Table: `raw_ticks`
- Fields: `exchange`, `symbol`, `seq`, `ts_exchange`, `ts_local`, `side`, `price` (string), `size` (string), `event_type` (`update`|`snapshot`|`trade`), `is_gap`, `gap_cause` (nullable)
- Write the `CREATE TABLE` DDL before implementing `writer/questdb/` to prevent schema drift

### Implementation Sequence (do not reorder)
1. `internal/config/` + `internal/symbol/` + `.env.example`
2. `internal/orderbook/` + `internal/reconnect/` + `internal/gapdetector/` — L1 tests first
3. `internal/exchange/` interface + `internal/exchange/transport/`
4. `internal/exchange/kucoin/` + `internal/exchange/bybit/mux/` + `internal/exchange/bybit/`
5. `internal/coordinator/interfaces.go` → `internal/writer/redis/` + `internal/writer/questdb/`
6. `internal/coordinator/` (coordinator.go, symbol.go, snapshot.go)
7. `internal/metrics/` + `internal/health/`
8. `cmd/aggregator/main.go` — composition root
9. `Makefile` + `docker-compose.yml` + `Dockerfile`

---

## Usage Guidelines

**For AI agents:** Read this file before writing any code. Follow all rules exactly. When in doubt, prefer the more restrictive interpretation. Flag any rule that conflicts with a framework or library default.

**For humans:** Keep this file lean. Update when stack or patterns change. Remove rules that become obvious over time.

_Last updated: 2026-05-06 — WebSocket library updated from nhooyr.io/websocket (deprecated) to github.com/coder/websocket v1.8.14_
