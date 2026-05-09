# Story 10.1: Aggregator Per-Condition Health Gauge

Status: done

## Story

As an operator,
I want the aggregator to expose `aggregator_health{condition}` gauges for feeds and gaps,
so that Prometheus can evaluate each condition independently and alert without requiring Grafana to be running.

## Acceptance Criteria

1. `aggregator_health{condition="feeds"}` and `aggregator_health{condition="gaps"}` appear in `/metrics` from first scrape, pre-initialized to 1
2. `feeds` gauge is 1 when all configured feed_state gauges are 1; drops to 0 when any feed_state is 0
3. `gaps` gauge is 1 when no gap events occurred in the last 5 minutes; drops to 0 when any gap fires within that window
4. Health gauge values are computed by a background goroutine, not at scrape time — zero computation on the `/metrics` hot path
5. L1 unit tests cover: pre-init, feeds 0→1→0 transitions, gaps 0→1→0 transitions with clock injection

## Tasks / Subtasks

- [x] Add `Health *prometheus.GaugeVec` and gap-timestamp tracking to `metrics.Registry` (AC: 1, 4)
  - [x] Add `Health` field to `Registry` struct
  - [x] Register with `[]string{"condition"}` label
  - [x] Pre-init both conditions to 1 in `PreInit()`
  - [x] Add `RecordGap(exchange, symbol string, nowUnix int64)` — stores timestamp in `sync.Mutex`-guarded map
  - [x] Add `GapHealthy(windowSecs, nowUnix int64) float64` — returns 1 if no entry within window, else 0
- [x] Wire `RecordGap` in coordinator symbol.go (AC: 3)
  - [x] Call `w.metrics.RecordGap(w.exch, string(w.sym), w.clock.Now().Unix())` at every `GapTotal.Inc()` call site (4 locations)
- [x] Start health-update goroutine in `cmd/aggregator/main.go` (AC: 2, 3, 4)
  - [x] Goroutine polls every 5s using existing `time.Ticker`
  - [x] Reads `FeedState` values via `promReg.Gather()` — computes `min(feed_state)` across all symbols
  - [x] Sets `reg.Health.WithLabelValues("feeds").Set(minFeedState)`
  - [x] Calls `reg.GapHealthy(300, time.Now().Unix())` and sets `reg.Health.WithLabelValues("gaps").Set(result)`
  - [x] Goroutine exits on `ctx.Done()`
- [x] L1 tests (AC: 5)
  - [x] `TestHealthPreInit` — both conditions present and == 1 after `PreInit`
  - [x] `TestFeedsCondition` — feeds gauge transitions correctly with mock gatherer
  - [x] `TestGapsCondition` — gaps gauge transitions with injected clock timestamps
  - [x] `TestGapHealthyWindow` — boundary: gap at exactly cutoff == 0, gap at cutoff+1s == 1

## Dev Notes

### What exists today — files being modified

**`aggregator/internal/metrics/metrics.go`**
- `Registry` struct has: `TicksTotal`, `GapTotal`, `FeedState`, `ConsumerLagMs`, `QuestDBWriteLatencyMs`
- `New(r prometheus.Registerer) *Registry` — all metrics created and registered here
- `PreInit(pairs []SymbolKey)` — pre-initializes all label series to 0 before feeds connect; call `reg.Health.WithLabelValues("feeds").Set(1)` and `reg.Health.WithLabelValues("gaps").Set(1)` here (not 0 — health starts healthy)
- `gapCauses` — 4 cause values used in PreInit loop
- This is a **leaf package** — imports only `github.com/prometheus/client_golang/prometheus`; must stay that way. `sync` is stdlib — allowed.

**`aggregator/internal/coordinator/symbol.go`**
- `Worker` struct has `metrics *metrics.Registry` field (nil-safe — all calls guarded by `if w.metrics != nil`)
- `FeedState.Set(1)` called at line 278 — only call site; FeedState starts at 0 (PreInit) and goes to 1 on GoLive
- `GapTotal.Inc()` called at 3 sites:
  - Line 105: panic recovery → `CauseInternalMergeError`
  - Line 187: gap event in main delta loop → various causes
  - Line 205: gap event alternate path → various causes
  - Line 261: another internal merge error
- `w.clock.Now()` is available — use it to get the current timestamp for `RecordGap`
- nil-guard pattern: `if w.metrics != nil { w.metrics.GapTotal... }` — follow the same pattern for `RecordGap`

**`aggregator/cmd/aggregator/main.go`**
- `promReg` is `*prometheus.Registry` (the gatherer) — already used by `GathererFeedStatus.FeedCounts()`
- `GathererFeedStatus.FeedCounts()` at line 46–58 in `httpapi/server.go` already shows the pattern for reading `aggregator_feed_state` from the gatherer — reuse this logic in the health goroutine
- `time.Now()` and `time.Ticker` are allowed in `cmd/` — use them here
- Shutdown pattern: all goroutines listen on `ctx.Done()` — follow same pattern

### Gap timestamp tracking design

```go
// In metrics.Registry — add these fields:
lastGapMu   sync.Mutex          // protects lastGapTimes
lastGapTimes map[string]int64   // key = "exchange/symbol", value = Unix seconds

// RecordGap records that a gap occurred now for the given (exchange, symbol).
// nowUnix is time.Now().Unix() from the caller (clock injection pattern).
func (reg *Registry) RecordGap(exchange, symbol string, nowUnix int64) {
    key := exchange + "/" + symbol
    reg.lastGapMu.Lock()
    reg.lastGapTimes[key] = nowUnix
    reg.lastGapMu.Unlock()
}

// GapHealthy returns 1.0 if no gap occurred within windowSecs, 0.0 otherwise.
func (reg *Registry) GapHealthy(windowSecs, nowUnix int64) float64 {
    cutoff := nowUnix - windowSecs
    reg.lastGapMu.Lock()
    defer reg.lastGapMu.Unlock()
    for _, t := range reg.lastGapTimes {
        if t >= cutoff {
            return 0.0
        }
    }
    return 1.0
}
```

Initialize `lastGapTimes: make(map[string]int64)` in `New()`.

### Health goroutine in main.go

```go
// Start health-update goroutine after metrics and coordinator are wired.
go func() {
    ticker := time.NewTicker(5 * time.Second)
    defer ticker.Stop()
    for {
        select {
        case <-ctx.Done():
            return
        case <-ticker.C:
            // feeds condition
            minFeed := computeMinFeedState(promReg)
            reg.Health.WithLabelValues("feeds").Set(minFeed)
            // gaps condition
            reg.Health.WithLabelValues("gaps").Set(reg.GapHealthy(300, time.Now().Unix()))
        }
    }
}()

func computeMinFeedState(g prometheus.Gatherer) float64 {
    mfs, _ := g.Gather()
    min := 1.0
    for _, mf := range mfs {
        if mf.GetName() != "aggregator_feed_state" {
            continue
        }
        for _, m := range mf.GetMetric() {
            if v := m.GetGauge().GetValue(); v < min {
                min = v
            }
        }
    }
    return min
}
```

This function can live in `cmd/aggregator/main.go` (not in `internal/` — `time.Now()` is called by the goroutine, not in the helper).

### Coordinator symbol.go — RecordGap call sites

Find all 4 `GapTotal.Inc()` call sites and add `RecordGap` alongside each:

```go
// Example — line 105 (panic recovery):
if w.metrics != nil {
    w.metrics.GapTotal.WithLabelValues(w.exch, string(w.sym), string(gapdetector.CauseInternalMergeError)).Inc()
    w.metrics.RecordGap(w.exch, string(w.sym), w.clock.Now().Unix())
}

// Example — line 187 (gap event):
if w.metrics != nil {
    w.metrics.GapTotal.WithLabelValues(w.exch, string(w.sym), string(gap.Cause)).Inc()
    w.metrics.RecordGap(w.exch, string(w.sym), w.clock.Now().Unix())
}
```

Follow the same nil-guard pattern. `w.clock` is already available — use `.Now().Unix()`.

### Testing notes

- L1 tests go in `aggregator/internal/metrics/metrics_test.go` (already exists — check existing test patterns first)
- `TestGapsCondition` uses a fake `nowUnix` value — no real time.Now() needed
- `TestFeedsCondition` needs a fake gatherer — look at how `httpapi/server_test.go` fakes the gatherer (uses a `prometheus.NewRegistry()` with pre-set values)
- Keep tests in `package metrics_test` (black-box) unless testing unexported internals

### Critical constraints (project-context.md)

- `prometheus.NewRegistry()` always — `reg` passed into `New()` is already a custom registry ✓
- `MustRegister` is forbidden — use `r.MustRegister(...)` only because `r` is the injected custom registry; this is the existing pattern ✓
- `time.Now()` banned in `internal/` — `RecordGap` takes `nowUnix int64` from caller ✓
- `sync.Mutex` allowed (only `sync.Mutex` on `OrderBook` is banned) ✓
- Leaf package constraint: `metrics/` must not import any internal package — `sync` is stdlib ✓
- nil-guard pattern for `w.metrics` already established in symbol.go — maintain it ✓

### References

- [Source: aggregator/internal/metrics/metrics.go] — Registry struct, PreInit, New
- [Source: aggregator/internal/coordinator/symbol.go#L104-L106, L186-L188, L204-L206, L260-L262] — GapTotal.Inc() call sites
- [Source: aggregator/internal/coordinator/symbol.go#L277-L279] — FeedState.Set(1) call site
- [Source: aggregator/internal/httpapi/server.go#L45-L59] — GathererFeedStatus.FeedCounts() — pattern for reading feed_state from gatherer
- [Source: aggregator/cmd/aggregator/main.go#L163] — promReg usage
- [Source: _bmad-output/planning-artifacts/architecture-monitoring.md#Section 3] — Health gauge design
- [Source: _bmad-output/planning-artifacts/epics-monitoring.md#Story 10.1] — Acceptance criteria

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- Added `Health *prometheus.GaugeVec` + `lastGapMu sync.Mutex` + `lastGapTimes map[string]int64` to `metrics.Registry`; registered with `r.MustRegister`; pre-initialized both conditions to 1 in `PreInit()`.
- Implemented `RecordGap(exchange, symbol string, nowUnix int64)` and `GapHealthy(windowSecs, nowUnix int64) float64` on `Registry`; `sync.Mutex` used (not `sync.Map`) to allow iteration in `GapHealthy`.
- Wired `RecordGap` at all 4 `GapTotal.Inc()` call sites in `coordinator/symbol.go` following the existing nil-guard pattern.
- Added health-update goroutine in `cmd/aggregator/main.go` polling every 5s; added `computeMinFeedState(prometheus.Gatherer) float64` helper in the same file.
- 4 new L1 tests: `TestHealthPreInit`, `TestFeedsCondition`, `TestGapsCondition`, `TestGapHealthyWindow` — all pass. Full suite: 0 regressions.
- Note: story said `sync.Map` but spec code used `sync.Mutex` + plain map; used Mutex (as in spec code) because `GapHealthy` iterates all entries — `sync.Map.Range` would work but Mutex+map is simpler and the spec code matches.

### File List

- `aggregator/internal/metrics/metrics.go` — modified
- `aggregator/internal/coordinator/symbol.go` — modified
- `aggregator/cmd/aggregator/main.go` — modified
- `aggregator/internal/metrics/metrics_test.go` — modified
