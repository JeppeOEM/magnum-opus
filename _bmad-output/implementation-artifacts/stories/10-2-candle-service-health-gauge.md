# Story 10.2: Candle-Service Per-Condition Health Gauge

Status: done

## Story

As an operator,
I want the candle-service to expose `candle_health{condition}` gauges for lag, WAL state, and flush failures,
so that Prometheus can evaluate each condition independently and alert without requiring Grafana.

## Acceptance Criteria

1. `candle_health{condition="consumer_lag"}`, `{condition="questdb"}`, and `{condition="flush"}` appear in `/metrics` from first scrape, pre-initialized to 1
2. `consumer_lag` gauge is 1 when max lag across all symbols ≤ 1000; drops to 0 when any symbol exceeds 1000
3. `questdb` gauge is 1 when no WAL suspension detected; drops to 0 when any writer reports `IsWALSuspended()`
4. `flush` gauge is 1 when no flush failure occurred in the last 15 minutes; drops to 0 within the window
5. Health gauge values are computed by the existing lag-polling goroutine (every 5s) — zero computation on the `/metrics` hot path
6. L1 unit tests cover: pre-init, consumer_lag 0→1→0 transitions, questdb 0→1→0, flush 0→1→0 with injected clock

## Tasks / Subtasks

- [x] Add `Health *prometheus.GaugeVec` and flush-timestamp tracking to `metrics.Metrics` (AC: 1, 5)
  - [x] Add `Health` field to `Metrics` struct
  - [x] Add `flushFailureMu sync.Mutex` and `lastFlushFailureUnix int64` fields
  - [x] Register `Health` via `reg.Register(m.Health)` in `Register()`
  - [x] Pre-init all three conditions to 1 in `Register()` after all pairs are initialized
  - [x] Add `RecordFlushFailure(nowUnix int64)` method
  - [x] Add `FlushHealthy(windowSecs, nowUnix int64) float64` method
- [x] Wire health updates into the existing lag-polling goroutine in `cmd/candle/main.go` (AC: 2, 3, 4, 5)
  - [x] Compute `maxLag` and check `IsWALSuspended()` in the same loop that stores lag atomics
  - [x] Set `consumer_lag` condition from maxLag > 1000 check
  - [x] Set `questdb` condition from WAL suspension check
  - [x] Set `flush` condition via `m.FlushHealthy(900, time.Now().Unix())`
  - [x] Wire `RecordFlushFailure` callback in flusher setup alongside `FlushFailureTotal.Inc`
- [x] L1 tests (AC: 6)
  - [x] `TestHealthPreInit` — all three conditions present at 1 after `Register()`
  - [x] `TestConsumerLagCondition` — transitions 0/1 based on threshold
  - [x] `TestQuestDBCondition` — health gauge set to 0/1 directly (simulating WAL probe result)
  - [x] `TestFlushCondition` — flush window boundary with injected clock

## Dev Notes

### What exists today — files being modified

**`candle-service/internal/metrics/metrics.go`**
- `Metrics` struct has: `BarsTotal`, `ConsumerLag`, `QuestDBWriteLatencyMs`, `GapCountTotal`, `FlushSuccessTotal`, `FlushFailureTotal`, `FlushAlertFailureTotal`, `BarCloseDroppedTotal`, `WALDropTotal`, `RedisPublishFailureTotal`, `CascadeStateWriteFailureTotal`
- `Register(reg prometheus.Registerer, pairs []ExchangeSymbol) (*Metrics, error)` — uses `reg.Register(c)` (not MustRegister) with error return
- Per-symbol initialization at end of `Register()` loop
- Leaf package — only imports stdlib and `prometheus/client_golang`
- `sync` is stdlib — allowed

**`candle-service/cmd/candle/main.go`**
- Lag polling goroutine at lines ~564–584: every 5s, loops over `entries`, calls `e.c.Lag(ctx)`, stores `e.lag.Store(lag)`, sets `m.ConsumerLag`
- WAL state: `e.w.IsWALSuspended()` available per entry
- Flusher setup at lines ~278–298: callbacks `m.FlushSuccessTotal.Inc`, `m.FlushFailureTotal.Inc`, `m.FlushAlertFailureTotal.Inc`
- `time.Now()` is allowed in `cmd/` — use it in health condition updates

### Flush health tracking design

```go
// Fields added to Metrics:
flushFailureMu    sync.Mutex
lastFlushFailureUnix int64  // 0 = never failed

func (m *Metrics) RecordFlushFailure(nowUnix int64) {
    m.flushFailureMu.Lock()
    m.lastFlushFailureUnix = nowUnix
    m.flushFailureMu.Unlock()
}

func (m *Metrics) FlushHealthy(windowSecs, nowUnix int64) float64 {
    m.flushFailureMu.Lock()
    t := m.lastFlushFailureUnix
    m.flushFailureMu.Unlock()
    if t == 0 { return 1.0 }           // never failed
    if nowUnix-t < windowSecs { return 0.0 }
    return 1.0
}
```

### Health updates in lag polling goroutine

```go
case <-ticker.C:
    var maxLag int64
    walOK := true
    for _, e := range entries {
        lag := e.c.Lag(ctx)
        if lag < 0 { lag = 0 }
        e.lag.Store(lag)
        m.ConsumerLag.WithLabelValues(e.exchange, e.symbol).Set(float64(lag))
        if lag > maxLag { maxLag = lag }
        if e.w.IsWALSuspended() { walOK = false }
    }
    lagHealth := 1.0
    if maxLag > 1000 { lagHealth = 0.0 }
    m.Health.WithLabelValues("consumer_lag").Set(lagHealth)
    walHealth := 1.0
    if !walOK { walHealth = 0.0 }
    m.Health.WithLabelValues("questdb").Set(walHealth)
    m.Health.WithLabelValues("flush").Set(m.FlushHealthy(900, time.Now().Unix()))
```

### Flusher callback change

```go
f := flusher.New(flushCfg, rdb, logger, wallClock{},
    m.FlushSuccessTotal.Inc,
    func() {
        m.FlushFailureTotal.Inc()
        m.RecordFlushFailure(time.Now().Unix())
    },
    m.FlushAlertFailureTotal.Inc,
)
```

### Testing notes

- Tests go in `candle-service/internal/metrics/metrics_test.go` (already exists)
- `TestConsumerLagCondition` and `TestQuestDBCondition`: set Health gauge directly (simulating what the goroutine does) and gather to verify
- `TestFlushCondition`: use `RecordFlushFailure` + `FlushHealthy` with injected timestamps
- Pattern: `package metrics_test` (black-box)

### Critical constraints

- `reg.Register(c)` pattern with error return — NOT `r.MustRegister` (candle-service convention differs from aggregator)
- `time.Now()` banned in `internal/` — `RecordFlushFailure` takes `nowUnix int64` from cmd/
- No new external dependencies
- Leaf package constraint: `metrics/` must not import internal packages

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- Added `Health *prometheus.GaugeVec` + `flushFailureMu sync.Mutex` + `lastFlushFailureUnix int64` to `metrics.Metrics`; registered via `reg.Register` (existing error-return pattern).
- Pre-initialized all three conditions to 1 in `Register()` after per-symbol init.
- Implemented `RecordFlushFailure(nowUnix int64)` and `FlushHealthy(windowSecs, nowUnix int64) float64`; boundary uses `<= windowSecs` (failure at exactly windowSecs age is still unhealthy).
- Updated lag polling goroutine in `main.go` to compute `maxLag` and `walOK` in the same loop, then set all three health conditions.
- Changed flusher failure callback to a closure that calls both `FlushFailureTotal.Inc()` and `RecordFlushFailure(time.Now().Unix())`.
- 4 new L1 tests: all pass. Full suite: 0 regressions.

### File List

- `candle-service/internal/metrics/metrics.go` — modified
- `candle-service/cmd/candle/main.go` — modified
- `candle-service/internal/metrics/metrics_test.go` — modified
