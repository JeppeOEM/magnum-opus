# Story 4.1: Prometheus Metrics Registry

Status: done

## Story

As the operator,
I want all Prometheus metrics defined at startup and always present in scrape responses,
So that monitoring dashboards never show missing series for disconnected or idle feeds.

## Acceptance Criteria

1. **Given** `internal/metrics/metrics.go`
   **Then** it defines all named Prometheus vars: `aggregator_ticks_total{exchange,symbol}`, `aggregator_gap_total{exchange,symbol,cause}`, `aggregator_feed_state{exchange,symbol}`, `aggregator_consumer_lag_ms{exchange,symbol}`, `aggregator_questdb_write_latency_ms`
   **And** it exports a `New(prometheus.Registerer) *Registry` constructor — never `prometheus.MustRegister` or `prometheus.DefaultRegisterer`
   **And** `internal/metrics/` has zero imports from any other internal package (leaf node)

2. **Given** a feed is disconnected
   **When** `/metrics` is scraped
   **Then** `aggregator_feed_state{exchange="bybit",symbol="BTCUSDT"}` returns `0` — the metric is present, not absent (NFR19)
   (Enforced by `PreInit` — pre-initialized to zero before any feed connects)

3. **Given** an `internal_*` gap is detected
   **When** the counter is incremented in `coordinator/symbol.go`
   **Then** `aggregator_gap_total{cause="internal_merge_error"}` increments
   **And** this counter increment path is tested at L2 using `FakeRedis` + `FakeQuestDB` (not deferred to Epic 4's HTTP wiring)

4. **Given** all configured (exchange, symbol) pairs are known at startup
   **When** the coordinator initializes
   **Then** `aggregator_feed_state` and `aggregator_ticks_total` are pre-initialized to zero for every (exchange, symbol) label combination before any feed connects
   **And** a scrape immediately after startup returns all expected label combinations — no symbol is absent from `/metrics` because it hasn't connected yet (NFR19)

5. **Given** `prometheus.NewRegistry()` is constructed
   **Then** it is passed to `internal/metrics/` via `metrics.New(reg)` — never accessed as a global
   (Story 4.2 passes the same `*prometheus.Registry` to the `/metrics` HTTP handler)

## Tasks / Subtasks

- [x] Add `prometheus/client_golang` to `go.mod`
  - [x] `cd aggregator && go get github.com/prometheus/client_golang/prometheus`
  - [x] Verify active maintenance: not archived, recent commits — canonical example of ARC11

- [x] Create `aggregator/internal/metrics/metrics.go` (AC: 1, 2, 4, 5)
  - [x] Define `SymbolKey` struct: `Exchange string`, `Symbol string` — avoids importing `internal/symbol`
  - [x] Define `Registry` struct with all 5 metric fields:
    - `TicksTotal *prometheus.CounterVec` — labels: exchange, symbol
    - `GapTotal *prometheus.CounterVec` — labels: exchange, symbol, cause
    - `FeedState *prometheus.GaugeVec` — labels: exchange, symbol
    - `ConsumerLagMs *prometheus.GaugeVec` — labels: exchange, symbol
    - `QuestDBWriteLatencyMs prometheus.Histogram` — no labels
  - [x] Implement `New(r prometheus.Registerer) *Registry` — creates all metrics, calls `r.MustRegister(...)`, returns populated `*Registry`
  - [x] Implement `(reg *Registry) PreInit(pairs []SymbolKey)` — calls `.With(labels)` on `TicksTotal`, `GapTotal` (for each of 4 causes), `FeedState`, `ConsumerLagMs` to force label series into existence before any feed connects

- [x] Create `aggregator/internal/metrics/metrics_test.go` (L1, AC: 1, 2, 4)
  - [x] `TestRegistry_AllMetricsRegistered`: verify all 5 distinct metric descriptors are registered
  - [x] `TestRegistry_PreInit_LabelsPresent`: verify feed_state and ticks_total series exist at 0 after PreInit
  - [x] Compile-time guarantee: `New()` never references `prometheus.DefaultRegisterer` — enforced by constructor signature requiring `prometheus.Registerer`

- [x] Modify `aggregator/internal/coordinator/symbol.go` (AC: 3, 4)
  - [x] Add `metrics *metrics.Registry` field to `Worker` struct (nil-safe)
  - [x] Add `met *metrics.Registry` parameter to `NewWorker` constructor
  - [x] In `handleTick`: `TicksTotal.Inc()` on successful stream.Write; `GapTotal.Inc()` + `FeedState.Set(0)` on gap detected
  - [x] In `Run()` panic recovery: `GapTotal.Inc()` for CauseInternalMergeError
  - [x] In `handleSnapshot()` stale path: `GapTotal.Inc()` for CauseInternalMergeError
  - [x] In `handleSnapshot()` GoLive path: `FeedState.Set(1)` after GoLive

- [x] Modify `aggregator/internal/coordinator/coordinator.go` (AC: 4)
  - [x] Add `metricsReg *metrics.Registry` field to `Coordinator` struct
  - [x] Add `WithMetrics(reg *metrics.Registry) *Coordinator` — calls `reg.PreInit(pairs)` and wires registry into all workers
  - [x] `NewWorker` call in `New()` passes `nil` (metrics wired via `WithMetrics`)

- [x] Add L2 test for gap_total counter increment (AC: 3) — `TestWorker_GapTotal_IncrementedOnGap` in `coordinator/symbol_test.go`
  - [x] Uses `prometheus.NewRegistry()`, `metrics.New(reg)`, passed to `NewWorker`
  - [x] Verifies `external_disconnect` == 1 after seq gap
  - [x] Verifies `internal_merge_error` == 0 (no spurious increment)
  - [x] Verifies `ticks_total` >= 2 after two successful writes

- [x] Build and test
  - [x] `go build ./internal/metrics/...` — clean
  - [x] `go build ./internal/coordinator/...` — clean
  - [x] `go test ./internal/metrics/... -v` — 2/2 L1 tests green
  - [x] `go test -tags l2 ./internal/coordinator/... -v` — 15/15 L2 tests green
  - [x] `make test-l1` — 6/6 L1 packages green, 100% coverage on core packages

## Dev Notes

### Review Findings

- [x] [Review][Patch] TicksTotal increments on ctx-cancelled write — `else if` fires when `stream.Write` fails due to ctx cancellation; resolved by making TicksTotal unconditional (D1 resolved: counts pipeline-processed ticks, not Redis acks) [coordinator/symbol.go:handleTick]
- [x] [Review][Patch] TestRegistry_AllMetricsRegistered tests descriptors, not registration — Describe() works without MustRegister; fixed with r.Gather()-based assertion [metrics/metrics_test.go]
- [x] [Review][Patch] AC3 L2 test does not exercise internal_merge_error path — TestWorker_GapTotal_IncrementedOnGap only sends external seq gap; added panic-recovery test for internal_merge_error [coordinator/symbol_test.go]
- [x] [Review][Patch] FeedState.Set(0) on seq gap misrepresents connectivity — seq gaps in StateLive don't physically disconnect the feed; removed [coordinator/symbol.go:handleTick]
- [x] [Review][Defer] metricsReg field stored in Coordinator but never read [coordinator/coordinator.go] — deferred, will be needed if coordinator-level metrics (e.g. dispatch latency) are added
- [x] [Review][Defer] WithMetrics after Run() is an unsynchronized data race [coordinator/coordinator.go:WithMetrics] — deferred, consistent with existing WithSleep pattern; doc comment is the contract
- [x] [Review][Defer] gapCauses is a mutable package-level var slice [metrics/metrics.go] — deferred, package-private and never mutated in practice
- [x] [Review][Defer] ConsumerLagMs has no writer in this story — pre-initialized but permanently zero [coordinator/] — deferred, will be wired in story 4.2/4.3

### What This Story Produces

```
aggregator/internal/metrics/metrics.go      NEW
aggregator/internal/metrics/metrics_test.go NEW (L1)
aggregator/internal/coordinator/symbol.go   MODIFIED (metrics field + increments)
aggregator/internal/coordinator/coordinator.go  MODIFIED (WithMetrics + PreInit)
aggregator/go.mod                           MODIFIED (add prometheus/client_golang)
aggregator/go.sum                           MODIFIED
```

### Prometheus Client API Quickref

```go
// CounterVec: monotonically increasing counter
ticks := prometheus.NewCounterVec(prometheus.CounterOpts{
    Name: "aggregator_ticks_total",
    Help: "Total ticks processed per exchange and symbol.",
}, []string{"exchange", "symbol"})

// GaugeVec: arbitrary value
feedState := prometheus.NewGaugeVec(prometheus.GaugeOpts{
    Name: "aggregator_feed_state",
    Help: "Feed connectivity state: 1=connected, 0=disconnected.",
}, []string{"exchange", "symbol"})

// Histogram: single metric (no label variation for write latency)
writeLatency := prometheus.NewHistogram(prometheus.HistogramOpts{
    Name:    "aggregator_questdb_write_latency_ms",
    Help:    "QuestDB ILP write latency in milliseconds.",
    Buckets: prometheus.DefBuckets,
})

// Registration: always via injected registerer, never global
r := prometheus.NewRegistry()
r.MustRegister(ticks, feedState, writeLatency)

// Pre-initialization: force label series into existence
ticks.WithLabelValues("bybit", "BTC-USDT")   // creates series at 0

// Gathering metrics in tests:
mfs, err := r.Gather()
require.NoError(t, err)
```

### metrics.Registry: Leaf Node Constraint

`internal/metrics` must import ONLY:
- `github.com/prometheus/client_golang/prometheus`
- stdlib

No imports from `internal/coordinator`, `internal/symbol`, `internal/exchange`, etc.
Use `SymbolKey{Exchange, Symbol string}` for pre-init pairs — raw strings.

### FeedState Semantics

`aggregator_feed_state` gauge:
- `0` = disconnected / initial (pre-init value, no action needed)
- `1` = connected and live (set when Worker enters StateLive on first tick after GoLive)

The set-to-1 call belongs in `handleTick` when `w.lastSeq == 0` and we're about to update `w.lastSeq` for the first time after GoLive. Alternatively, set to 1 in `handleSnapshot` after `w.recon.GoLive()` — cleaner because GoLive is the state transition event.

Recommended: set `FeedState.Set(1)` in `handleSnapshot` after the `w.recon.GoLive()` call.
Set `FeedState.Set(0)` when gap is detected (exchange reconnect in progress).

### nil-Safe Pattern for Worker

All metric calls in `symbol.go` must be nil-safe so existing L2 tests don't need to pass a registry:

```go
func (w *Worker) incGap(exch, sym, cause string) {
    if w.metrics != nil {
        w.metrics.GapTotal.WithLabelValues(exch, sym, cause).Inc()
    }
}
```

Keep inline if only called once; extract helper only if called 3+ times.

### gapdetector.Cause as String

`gapdetector.GapCause` is a string type. Cast directly: `string(gap.Cause)`.
The four valid values are:
- `"internal_buffer_overflow"`, `"internal_merge_error"` — internal causes
- `"external_disconnect"`, `"external_rate_limit"` — external causes

For PreInit, call GapTotal.WithLabelValues for all 4 cause values to pre-init them.

### Verifying Counter Value in L2 Test

Use `prometheus.Gatherer.Gather()` to read metric values in tests:

```go
import (
    dto "github.com/prometheus/client_model/go"
    "github.com/prometheus/client_golang/prometheus"
)

func gatherCounter(t *testing.T, g prometheus.Gatherer, name string, labels map[string]string) float64 {
    t.Helper()
    mfs, err := g.Gather()
    require.NoError(t, err)
    for _, mf := range mfs {
        if mf.GetName() != name {
            continue
        }
        for _, m := range mf.GetMetric() {
            if labelsMatch(m.GetLabel(), labels) {
                return m.GetCounter().GetValue()
            }
        }
    }
    t.Fatalf("metric %s{%v} not found", name, labels)
    return 0
}

func labelsMatch(got []*dto.LabelPair, want map[string]string) bool {
    for _, lp := range got {
        if v, ok := want[lp.GetName()]; ok && v != lp.GetValue() {
            return false
        }
    }
    return true
}
```

Or use `github.com/prometheus/client_golang/prometheus/testutil` package (part of the same module, no new dependency):
```go
import "github.com/prometheus/client_golang/prometheus/testutil"
count := testutil.ToFloat64(reg.GapTotal.WithLabelValues("kucoin", "BTC-USDT", "external_disconnect"))
require.Equal(t, float64(1), count)
```

`testutil.ToFloat64` is the simplest approach for a single labeled metric.

### L2 Test: Worker + Metrics Integration

The test must set up the Worker in GoLive state before sending the gap-triggering tick. Use the same FakeSnapshotFetcher pattern from coordinator_test.go. The test needs the coordinator running to dispatch the snapshot, OR it can bypass the coordinator and inject the SnapshotResult directly via the resultCh (Worker's resultCh is an unexported field — use the coordinator harness).

Simpler approach: use the existing coordinator L2 test scaffold (`TestCoordinator_E2E_TickFlowGapSnapshot` pattern) with a non-nil metrics registry, then after the gap tick, gather and assert.

Or drive the Worker in isolation via `fakeExch.ticksCh` by putting it through the snapshot phase first (WaitCalled + 25ms sleep + send ticks with gap). Reuse the harness from coordinator_test.go.

### Import Graph After This Story

```
metrics        → prometheus/client_golang (NEW leaf)
coordinator    → metrics (NEW)
symbol.go      → metrics (NEW — for metric label constants)
main.go        → metrics, coordinator (story 4.3)
```

No cycles introduced. `metrics` does not import any internal package.

### Dependency Verification (ARC11)

Before adding `prometheus/client_golang`:
- Repo: github.com/prometheus/client_golang
- Last commit: verify not archived, active in 2025
- Version to pin: latest stable (v1.19.x or v1.20.x as of 2026)
- Run `make check-deps` after adding

### coordinator.go: WithMetrics and PreInit

```go
// WithMetrics enables Prometheus metric emission. Call before Run().
// Pre-initializes all per-symbol label series so they appear in scrapes immediately.
func (c *Coordinator) WithMetrics(reg *metrics.Registry) *Coordinator {
    c.metrics = reg
    pairs := make([]metrics.SymbolKey, 0, len(c.entries))
    for _, e := range c.entries {
        pairs = append(pairs, metrics.SymbolKey{Exchange: e.exchange, Symbol: string(e.sym)})
    }
    reg.PreInit(pairs)
    // Wire metrics into workers
    for i := range c.entries {
        c.entries[i].worker.metrics = reg
    }
    return c
}
```

### symbol.go: Updated NewWorker Signature

`NewWorker` gains a `metrics *metrics.Registry` parameter (passed as nil for existing L2 tests that don't need it):

```go
func NewWorker(
    exch string,
    sym symbol.Symbol,
    deltas <-chan exchange.Tick,
    stream StreamWriter,
    ilp ILPWriter,
    snapRequests chan<- SnapshotRequest,
    book *orderbook.OrderBook,
    recon *reconnect.Machine,
    clock gapdetector.Clock,
    met *metrics.Registry,   // NEW — pass nil to opt out
) *Worker {
    return &Worker{
        ...,
        metrics: met,
    }
}
```

All existing callers in `coordinator.go` pass `nil` until `WithMetrics` is called. The `WithMetrics` method then wires the registry into all worker fields after construction.

### References

- `coordinator/symbol.go` — Worker struct, handleTick, handleSnapshot, Run panic recovery
- `coordinator/coordinator.go` — New(), entries slice, WithSleep pattern (same pattern for WithMetrics)
- `coordinator/coordinator_test.go` — E2E test harness (fakeExchange, WaitCalled pattern)
- `gapdetector/types.go` — GapCause string type, 4 cause constants
- `epics.md` §Story 4.1 lines 741–769
- `prometheus/client_golang` docs: prometheus.NewRegistry(), CounterVec, GaugeVec, Histogram, testutil.ToFloat64
- ARC11 (active-dependency rule): check maintenance before adding prometheus/client_golang

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log

- **Missing go.sum entry for testutil subpackage**: `prometheus/client_golang/prometheus/testutil` needed `kylelemons/godebug` in go.sum; fixed with `go get github.com/prometheus/client_golang/prometheus/testutil@v1.23.2`.
- **Import alias conflict**: renamed internal `testutil` import to `clockutil` in `symbol_test.go` to avoid collision with the Prometheus `testutil` package added for `ToFloat64`.
- **Existing NewWorker callers**: `symbol_test.go` had 5 call sites that needed `nil` appended for the new `met *metrics.Registry` param; fixed via sed.

### Completion Notes

- `internal/metrics/metrics.go`: leaf package (zero internal imports), defines 5 metrics, `New(prometheus.Registerer)` constructor, `PreInit([]SymbolKey)` pre-initializes all per-symbol series including all 4 gap causes.
- `internal/metrics/metrics_test.go`: 2 L1 tests — descriptor count check (5 distinct) and PreInit label-presence check for feed_state and ticks_total at 0.
- `coordinator/symbol.go`: `metrics *metrics.Registry` field (nil-safe), increments wired at: gap detected (GapTotal + FeedState=0), GoLive (FeedState=1), panic recovery (GapTotal internal_merge_error), stale snapshot (GapTotal internal_merge_error), successful tick write (TicksTotal).
- `coordinator/coordinator.go`: `WithMetrics(reg)` calls `PreInit` on the full symbol list from `entries`, then wires `reg` into each Worker field.
- `coordinator/symbol_test.go`: new `TestWorker_GapTotal_IncrementedOnGap` (L2) asserts external_disconnect==1, internal_merge_error==0, ticks_total>=2 using `prometheus/testutil.ToFloat64`.
- All tests: 2 L1 metrics + 15 L2 coordinator + 6 L1 packages (100% core coverage) — all green.
- prometheus/client_golang v1.23.2 — ARC11 verified: not archived, last push 2026-05-06.

## File List

- `aggregator/internal/metrics/metrics.go` (NEW)
- `aggregator/internal/metrics/metrics_test.go` (NEW)
- `aggregator/internal/coordinator/symbol.go` (MODIFIED — metrics field, NewWorker param, metric increments)
- `aggregator/internal/coordinator/coordinator.go` (MODIFIED — metricsReg field, WithMetrics method, NewWorker nil arg)
- `aggregator/internal/coordinator/symbol_test.go` (MODIFIED — nil metrics arg on all NewWorker calls, new L2 test, import aliases)
- `aggregator/go.mod` (MODIFIED — prometheus/client_golang v1.23.2 added)
- `aggregator/go.sum` (MODIFIED)

## Change Log

- 2026-05-07: Story implemented — metrics registry package, coordinator wiring, L2 counter test. 15 L2 + 6 L1 packages green. Status → review.
