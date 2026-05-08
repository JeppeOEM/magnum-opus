# Story 5.7: Observability — Health, Version, Metrics

Status: done

## Story

As a developer/operator,
I want `/health`, `/version`, and `/metrics` endpoints wired with a Prometheus custom registry and a credential-redacting slog handler,
so that Epic 5 is fully operational: the service can be monitored, deployed, and validated with blue-green tooling.

## Acceptance Criteria

1. `internal/metrics/` exports `Register(reg prometheus.Registerer, pairs []ExchangeSymbol) *Metrics` and `type ExchangeSymbol struct { Exchange, Symbol string }`. The `Metrics` struct holds all named vars below. At call time, all per-symbol gauges and counters are initialized to 0 for every supplied pair. Zero internal imports except stdlib and `github.com/prometheus/client_golang`. Never use `prometheus.DefaultRegisterer` or `promauto`.

2. Required metrics in `*Metrics`:
   - `BarsTotal` — `CounterVec{exchange,symbol}`, name `candle_bars_total`
   - `ConsumerLag` — `GaugeVec{exchange,symbol}`, name `candle_consumer_lag`, clamped to `max(0, v)` before `Set`; log WARN once per symbol per hour on negative raw value
   - `QuestDBWriteLatencyMs` — `Histogram`, name `candle_questdb_write_latency_ms`, buckets `[1, 5, 10, 25, 50, 100, 250, 500, 1000]`
   - `GapCountTotal` — `CounterVec{exchange,symbol}`, name `candle_gap_count_total`
   - `FlushSuccessTotal` — `Counter` (no labels), name `candle_flush_success_total`
   - `FlushFailureTotal` — `Counter` (no labels), name `candle_flush_failure_total`
   - `FlushAlertFailureTotal` — `Counter` (no labels), name `candle_flush_alert_failure_total`
   - `BarCloseDroppedTotal` — `CounterVec{exchange,symbol}`, name `candle_bar_close_dropped_total`
   - `WALDropTotal` — `Counter` (no labels), name `candle_wal_drop_total`
   - `RedisPublishFailureTotal` — `CounterVec{exchange,symbol}`, name `candle_redis_publish_failure_total`
   - `CascadeStateWriteFailureTotal` — `CounterVec{exchange,symbol}`, name `candle_cascade_state_write_failure_total`

3. `internal/health/health.go` updated: `New(slot, version, gitSHA, buildTime string, state StateFunc) *Server`. `StateFunc = func() HealthState`. `HealthState = struct{ ConsumerLagMax int64; QuestDBWriteState string; ShadowLag int64 }`. `Server` gains a `/metrics` handler: accepts a `prometheus.Gatherer` via `WithMetrics(g prometheus.Gatherer)` method called after construction. `/health` derives `status` from `HealthState`: ok → all nominal; degraded → lag >1000 OR WAL suspended; critical → lag >10000. Must respond in <100ms (state func must never block for long).

4. `/version` response body: `{"version":"...","git_sha":"...","build_time":"...","go_version":"..."}`. `go_version` is `runtime.Version()`. All four fields always present. `git_sha`, `build_time`, `version` injected via `-ldflags` as before.

5. `cmd/candle/main.go` updated:
   - Add a `redactHandler` wrapping the JSON slog handler. It redacts attribute values whose keys include substrings `password`, `b2_access`, `b2_secret`, `secret_key`; Redis URL values are partially redacted (scheme+host retained, password masked). Wired **before** all other logging.
   - Replace the two `promauto.NewCounter` declarations (`barCloseDropped`, `walDropTotal`) with metric vars from `metrics.Register(reg, pairs)`.
   - Create `prometheus.NewRegistry()` (never `DefaultRegisterer`). Pass registry to `metrics.Register`. Wire `/metrics` handler via `healthSrv.WithMetrics(reg)`.
   - Pass `StateFunc` to health server `New()`. The `StateFunc` reads lag/WAL state from atomic vars updated by background goroutines (see AC 6–7).
   - Update `barClose` fan-out to increment `m.BarCloseDroppedTotal.WithLabelValues(p.exchange, p.symbol)` (label-aware) instead of the old no-label counter.
   - Update WAL drop counter wiring: `w.SetWALDropCounter(m.WALDropTotal.Inc)`.

6. Consumer lag tracking: add `Lag() int64` method to `consumer.Consumer`. It calls `rdb.XPending(ctx, c.streamKey, c.consumerGroup)` and returns `pending.Count`. Called periodically in a background goroutine in `cmd/candle/main.go` (every 5 seconds), result stored in a `sync/atomic int64` per symbol. The `StateFunc` reads the max lag across all symbols from these atomics.

7. WAL suspended state: add `IsWALSuspended() bool` method to `questdb.Writer`. Returns `w.walSuspended.Load()`. The health `StateFunc` in `cmd/candle/main.go` reads this from each `*questdb.Writer` and returns `"suspended"` if any writer is suspended, else `"ok"`.

8. L1 tests for `internal/metrics/` — `go test ./...` passes. Required test coverage:
   - All 11 metrics are registered without error
   - All per-symbol metrics initialized to 0 for configured pairs at startup
   - `ConsumerLag.Set` with negative value clamps to 0 (via wrapper; test via `prometheus.Gatherer`)
   - Re-calling `Register` with same registry returns an error (duplicate registration caught)

9. `internal/health/` tests updated — `go test ./...` passes. Required:
   - `/health` returns `{"status":"ok"}` when StateFunc returns zero values
   - `/health` returns `"degraded"` when consumer_lag_max=5000
   - `/health` returns `"critical"` when consumer_lag_max=15000
   - `/health` returns `"degraded"` when questdb_write_state="suspended"
   - `/version` returns all four fields including `go_version`
   - `/metrics` returns HTTP 200 with `Content-Type: text/plain` (Prometheus scrape format)

10. `go test ./...` and `go test -tags l2 ./...` pass with zero failures. No regressions in existing tests (consumer L2, writer/questdb L2, health L1).

## Tasks / Subtasks

- [ ] Create `internal/metrics/metrics.go` (AC: 1–2)
  - [ ] Define `ExchangeSymbol` struct and `Register` function
  - [ ] Declare `Metrics` struct with all 11 metric vars
  - [ ] Initialize all per-symbol metrics to 0 in `Register`
  - [ ] Use `prometheus.NewRegistry()` approach — accept `prometheus.Registerer` as parameter
  - [ ] L1 tests: all metrics register, per-symbol init, negative lag clamp, duplicate registration error

- [ ] Update `internal/health/health.go` (AC: 3–4)
  - [ ] Add `StateFunc` type and `HealthState` struct
  - [ ] Update `New()` signature to accept slot, version, gitSHA, buildTime, StateFunc
  - [ ] Add `WithMetrics(g prometheus.Gatherer)` method
  - [ ] Register `/metrics` route in `WithMetrics`
  - [ ] Implement `/health` status derivation logic from HealthState
  - [ ] Update `/version` to return all 4 fields with `runtime.Version()`
  - [ ] Update existing `health_test.go` to match new `New()` signature

- [ ] Add `IsWALSuspended()` to `questdb.Writer` (AC: 7)
  - [ ] Add `func (w *Writer) IsWALSuspended() bool { return w.walSuspended.Load() }`
  - [ ] No new tests needed — already covered by existing L2 tests

- [ ] Add `Lag()` method to `consumer.Consumer` (AC: 6)
  - [ ] Add `func (c *Consumer) Lag(ctx context.Context) int64` calling `rdb.XPending`
  - [ ] Return `pending.Count`; on error return 0 and log warn

- [ ] Update `cmd/candle/main.go` (AC: 5–7)
  - [ ] Add `redactHandler` wrapping JSON slog handler (credential redaction)
  - [ ] Create `prometheus.NewRegistry()`
  - [ ] Call `metrics.Register(reg, pairs)` with all configured (exchange,symbol) pairs
  - [ ] Update `barClose` fan-out to use labeled `m.BarCloseDroppedTotal.WithLabelValues(e,s).Inc()`
  - [ ] Update WAL drop counter: `w.SetWALDropCounter(m.WALDropTotal.Inc)`
  - [ ] Launch lag-polling goroutine (every 5s) storing result in atomic per symbol
  - [ ] Implement `StateFunc` reading max lag + WAL state
  - [ ] Update `health.New()` call with new signature
  - [ ] Call `healthSrv.WithMetrics(reg)` after construction
  - [ ] Remove old `promauto` imports and declarations

- [ ] Run full test suite (AC: 10)
  - [ ] `go test ./...` passes
  - [ ] `go test -tags l2 ./...` passes

### Review Findings

- [ ] [Review][Patch] HTTP handler data race — `httpServer.Handler` mutated after `ListenAndServe()` starts; request goroutines read Handler concurrently [cmd/candle/main.go:253]
- [ ] [Review][Patch] `redactAttr` does not recurse into `slog.GroupValue` — credentials nested in slog.Group pass through unredacted [cmd/candle/main.go:417]
- [ ] [Review][Patch] `QuestDBWriteLatencyMs` histogram registered but never observed — emits empty histogram every scrape [internal/metrics/metrics.go:47]
- [ ] [Review][Patch] `TestMetrics_PerSymbolInitialized` only verifies BarsTotal — other per-symbol metrics not checked [internal/metrics/metrics_test.go:42]
- [x] [Review][Defer] `Consumer.Lag()` untested — simple method, not critical for Epic 5 Foundation — deferred, pre-existing
- [x] [Review][Defer] `Lag()` counts all consumers in group not just this instance — by design, out of scope for single-slot operation — deferred, pre-existing

## Dev Notes

### Forbidden: DefaultRegisterer and promauto

From project-context.md: **always `prometheus.NewRegistry()`; never `prometheus.MustRegister`, `prometheus.DefaultRegisterer`, `promhttp.Handler()`**.

The existing `main.go` currently uses `promauto.NewCounter` — this MUST be replaced. `promauto` registers with `prometheus.DefaultRegisterer` (the global registry). `promauto` is forbidden.

Instead: `prometheus.NewRegistry()` → pass to `metrics.Register(reg, pairs)` → `healthSrv.WithMetrics(reg)` → `promhttp.HandlerFor(reg, promhttp.HandlerOpts{})`.

### metrics.Register Pattern

```go
package metrics

import (
    "github.com/prometheus/client_golang/prometheus"
)

type ExchangeSymbol struct {
    Exchange string
    Symbol   string
}

type Metrics struct {
    BarsTotal                     *prometheus.CounterVec
    ConsumerLag                   *prometheus.GaugeVec
    QuestDBWriteLatencyMs         prometheus.Histogram
    GapCountTotal                 *prometheus.CounterVec
    FlushSuccessTotal             prometheus.Counter
    FlushFailureTotal             prometheus.Counter
    FlushAlertFailureTotal        prometheus.Counter
    BarCloseDroppedTotal          *prometheus.CounterVec
    WALDropTotal                  prometheus.Counter
    RedisPublishFailureTotal      *prometheus.CounterVec
    CascadeStateWriteFailureTotal *prometheus.CounterVec
}

func Register(reg prometheus.Registerer, pairs []ExchangeSymbol) (*Metrics, error) {
    m := &Metrics{
        BarsTotal: prometheus.NewCounterVec(prometheus.CounterOpts{
            Name: "candle_bars_total",
            Help: "Total bars written per (exchange, symbol).",
        }, []string{"exchange", "symbol"}),
        // ... all others
    }

    collectors := []prometheus.Collector{
        m.BarsTotal,
        m.ConsumerLag,
        m.QuestDBWriteLatencyMs,
        // ...
    }
    for _, c := range collectors {
        if err := reg.Register(c); err != nil {
            return nil, fmt.Errorf("metrics: register: %w", err)
        }
    }

    // Initialize per-symbol metrics to 0 (ensures configured-but-silent symbols appear in /metrics)
    for _, p := range pairs {
        m.BarsTotal.WithLabelValues(p.Exchange, p.Symbol)      // touch = creates with 0
        m.ConsumerLag.WithLabelValues(p.Exchange, p.Symbol).Set(0)
        m.GapCountTotal.WithLabelValues(p.Exchange, p.Symbol)
        m.BarCloseDroppedTotal.WithLabelValues(p.Exchange, p.Symbol)
        m.RedisPublishFailureTotal.WithLabelValues(p.Exchange, p.Symbol)
        m.CascadeStateWriteFailureTotal.WithLabelValues(p.Exchange, p.Symbol)
    }

    return m, nil
}
```

**Do NOT** make `Register` return only `*Metrics` without an error — duplicate registration panics in tests without the error path.

### ConsumerLag clamping

The `ConsumerLag` GaugeVec should have a helper wrapper or the `cmd/candle/main.go` goroutine should always call `max(0, lag)` before `Set`. Log WARN (once per symbol per hour, use a `map[string]time.Time` rate-limiter) if the raw value is negative.

```go
lag := c.Lag(ctx)
if lag < 0 {
    // rate-limited warn
    lag = 0
}
m.ConsumerLag.WithLabelValues(exchange, symbol).Set(float64(lag))
```

### health.Server Updated API

```go
// StateFunc provides dynamic health state. Must return quickly (< 1ms).
type StateFunc func() HealthState

type HealthState struct {
    ConsumerLagMax    int64
    QuestDBWriteState string // "ok" or "suspended"
    ShadowLag         int64  // 0 after promotion; for deploy gate
}

func New(slot, version, gitSHA, buildTime string, state StateFunc) *Server

// WithMetrics wires the /metrics endpoint. Must be called before the server starts.
func (s *Server) WithMetrics(g prometheus.Gatherer)
```

`/health` status derivation:
```go
func deriveStatus(lag int64, walState string) string {
    if lag > 10_000 {
        return "critical"
    }
    if lag > 1_000 || walState == "suspended" {
        return "degraded"
    }
    return "ok"
}
```

### /version Response

```go
import "runtime"

func (s *Server) handleVersion(w http.ResponseWriter, _ *http.Request) {
    w.Header().Set("Content-Type", "application/json")
    _ = json.NewEncoder(w).Encode(map[string]string{
        "version":    s.version,
        "git_sha":    s.gitSHA,
        "build_time": s.buildTime,
        "go_version": runtime.Version(),
    })
}
```

`git_sha` and `build_time` are injected via `-ldflags` in the Makefile/Dockerfile as `main.gitSHA` and `main.buildTime`. Pass them through to health.New.

### /metrics Handler

```go
func (s *Server) WithMetrics(g prometheus.Gatherer) {
    s.mux.Handle("/metrics", promhttp.HandlerFor(g, promhttp.HandlerOpts{}))
}
```

Import: `"github.com/prometheus/client_golang/prometheus/promhttp"`.

### Credential Redaction Handler

```go
// redactHandler wraps a slog.Handler and redacts sensitive attribute values.
type redactHandler struct {
    base slog.Handler
}

var sensitiveKeys = []string{"password", "b2_access", "b2_secret", "secret_key"}

func (h *redactHandler) Handle(ctx context.Context, r slog.Record) error {
    var redacted slog.Record
    redacted = r.Clone()
    redacted.Attrs(func(a slog.Attr) bool {
        // can't replace in-place; build new record
        return true
    })
    // Simpler: scan and replace by building new record
    newRec := slog.NewRecord(r.Time, r.Level, r.Message, r.PC)
    r.Attrs(func(a slog.Attr) bool {
        newRec.AddAttrs(redactAttr(a))
        return true
    })
    return h.base.Handle(ctx, newRec)
}

func redactAttr(a slog.Attr) slog.Attr {
    key := strings.ToLower(a.Key)
    for _, s := range sensitiveKeys {
        if strings.Contains(key, s) {
            return slog.String(a.Key, "[REDACTED]")
        }
    }
    // Partial redact for Redis URL with password
    if strings.Contains(key, "redis_url") || strings.Contains(key, "url") {
        v := a.Value.String()
        if u, err := url.Parse(v); err == nil && u.User != nil {
            if _, hasPwd := u.User.Password(); hasPwd {
                u.User = url.UserPassword(u.User.Username(), "[REDACTED]")
                return slog.String(a.Key, u.String())
            }
        }
    }
    return a
}

func (h *redactHandler) Enabled(ctx context.Context, level slog.Level) bool {
    return h.base.Enabled(ctx, level)
}
func (h *redactHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
    redacted := make([]slog.Attr, len(attrs))
    for i, a := range attrs {
        redacted[i] = redactAttr(a)
    }
    return &redactHandler{base: h.base.WithAttrs(redacted)}
}
func (h *redactHandler) WithGroup(name string) slog.Handler {
    return &redactHandler{base: h.base.WithGroup(name)}
}
```

**Wiring order in main.go:** The `redactHandler` wraps the JSON handler. The `slotHandler` wraps the redactHandler. Chain: `slotHandler → redactHandler → slog.NewJSONHandler`.

### Consumer Lag Method

```go
// Lag queries the current pending count for this consumer's group.
// Returns 0 on error (non-fatal; lag is advisory only).
func (c *Consumer) Lag(ctx context.Context) int64 {
    info, err := c.rdb.XPending(ctx, c.streamKey, c.consumerGroup).Result()
    if err != nil {
        c.logger.WarnContext(ctx, "consumer: lag query failed", "error", err)
        return 0
    }
    return info.Count
}
```

### main.go Lag Polling Goroutine

```go
// One atomic per symbol, updated every 5 seconds.
type lagStore struct {
    lags []atomic.Int64   // indexed by symbolIdx
}

// Background goroutine
go func() {
    ticker := time.NewTicker(5 * time.Second)
    defer ticker.Stop()
    for {
        select {
        case <-ctx.Done():
            return
        case <-ticker.C:
            for i, c := range consumers {
                lag := c.Lag(ctx)
                lagStore.lags[i].Store(lag)
            }
        }
    }
}()

// StateFunc reads max lag + WAL state
stateFunc := func() health.HealthState {
    var maxLag int64
    for i := range lagStore.lags {
        if l := lagStore.lags[i].Load(); l > maxLag {
            maxLag = l
        }
    }
    walState := "ok"
    for _, w := range writers {
        if w.IsWALSuspended() {
            walState = "suspended"
            break
        }
    }
    return health.HealthState{
        ConsumerLagMax:    maxLag,
        QuestDBWriteState: walState,
        ShadowLag:         0,
    }
}
```

### Import Group Ordering (must match goimports convention)

```go
import (
    "context"
    "log/slog"
    // ... stdlib

    "github.com/prometheus/client_golang/prometheus"
    "github.com/prometheus/client_golang/prometheus/promhttp"
    // ... external

    "github.com/mrqdt/magnum-opus/candle-service/internal/config"
    // ... internal
)
```

### Existing Tests That Must Continue Passing

`internal/health/health_test.go` uses `health.New("blue", "dev")` — this will break when the signature changes to 5 params. You MUST update these tests. The new call form for tests:

```go
noopState := func() health.HealthState { return health.HealthState{} }
srv := health.New("blue", "dev", "abc123", "2026-01-01", noopState)
```

`internal/consumer/consumer_l2_test.go` uses `newTestConsumer` which calls `consumer.New(...)` — Lag() is a new method, not interface-breaking. No existing tests break.

### Files Changed Summary

| File | Change |
|------|--------|
| `internal/metrics/metrics.go` | NEW |
| `internal/metrics/metrics_test.go` | NEW |
| `internal/health/health.go` | UPDATE — new signature, StateFunc, /metrics |
| `internal/health/health_test.go` | UPDATE — new signature in all test helpers |
| `internal/writer/questdb/writer.go` | UPDATE — add `IsWALSuspended() bool` |
| `internal/consumer/consumer.go` | UPDATE — add `Lag(ctx) int64` |
| `cmd/candle/main.go` | UPDATE — registry, metrics, redact, lag polling |

### No New External Dependencies

Everything needed is already in `go.mod`:
- `github.com/prometheus/client_golang` — metrics + promhttp

### Prometheus Registry Passing Pattern

`prometheus.NewRegistry()` returns `*prometheus.Registry` which implements both `prometheus.Registerer` and `prometheus.Gatherer`. Pass `Registerer` to `metrics.Register()`, pass `Gatherer` to `healthSrv.WithMetrics()`. Both use the same underlying `*prometheus.Registry`.

```go
reg := prometheus.NewRegistry()
m, err := metrics.Register(reg, pairs)
// ...
healthSrv.WithMetrics(reg) // reg satisfies prometheus.Gatherer
```

### Test Pattern for metrics L1

```go
func TestMetrics_AllRegistered(t *testing.T) {
    reg := prometheus.NewRegistry()
    pairs := []metrics.ExchangeSymbol{{"kucoin", "BTC-USDT"}}
    m, err := metrics.Register(reg, pairs)
    require.NoError(t, err)
    require.NotNil(t, m)

    // Gather should not error
    mfs, err := reg.Gather()
    require.NoError(t, err)
    assert.NotEmpty(t, mfs)
}

func TestMetrics_PerSymbolInitialized(t *testing.T) {
    reg := prometheus.NewRegistry()
    pairs := []metrics.ExchangeSymbol{{"kucoin", "BTC-USDT"}, {"bybit", "ETH-USDT"}}
    m, err := metrics.Register(reg, pairs)
    require.NoError(t, err)

    mfs, _ := reg.Gather()
    metricNames := map[string]bool{}
    for _, mf := range mfs {
        for _, metric := range mf.GetMetric() {
            metricNames[mf.GetName()] = true
            _ = metric
        }
    }
    assert.True(t, metricNames["candle_bars_total"])
    assert.True(t, metricNames["candle_consumer_lag"])
}
```

### Change Log

- 2026-05-08: Story created for Epic 5 Story 7 — observability (health, version, metrics)
