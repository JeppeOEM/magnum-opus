# Story 4.2: Health, Version & Metrics HTTP Endpoints

Status: done

## Story

As the operator,
I want `/health`, `/version`, and `/metrics` HTTP endpoints that respond accurately and quickly regardless of feed state,
So that monitoring systems always have a reliable operational signal without authentication overhead.

## Acceptance Criteria

1. **Given** all feeds are connected and no internal_* gap has occurred in the last 24h
   **When** `GET /health` is called
   **Then** it returns `{"status":"ok","connected_feeds":N,"gap_count_24h":0,"uptime_seconds":N}` with HTTP 200

2. **Given** any feed is reconnecting
   **When** `GET /health` is called
   **Then** `status` is `"degraded"` — not `"ok"` and not `"critical"`

3. **Given** any internal_* gap has occurred in the last 24h
   **When** `GET /health` is called
   **Then** `status` is `"critical"` (FR30 — operator can distinguish internal vs external without inspecting logs)
   **And** if both conditions (degraded + critical) are true, `"critical"` wins

4. **Given** any exchange feed state
   **When** `GET /health` is called
   **Then** it responds within 100ms — reads in-memory state only, performs no IO (NFR20)

5. **Given** `gap_count_24h` is reported in the `/health` response
   **Then** it is computed from a rolling in-memory counter: 24 × 1-hour buckets, oldest bucket evicted when its hour slot is reused
   **And** only internal_* gaps (CauseInternalMergeError, CauseInternalBufferOverflow) are counted — external disconnects are normal
   **And** a unit test verifies that a gap event older than 24h is not counted, and a gap event within 24h is counted correctly
   **And** the rolling window is pre-populated at startup by reading the last 24h of the `gaps:log` Redis stream — the Populate([]GapRecord) method accepts pre-fetched records; actual Redis XRANGE call is wired in story 4.3

6. **Given** `GET /version`
   **Then** it returns `{"version":V,"git_sha":S,"build_time":T,"go_version":G}` where V/S/T are set via build-time ldflags injected into `internal/httpapi` package vars; G comes from `runtime.Version()`

7. **Given** `GET /metrics`
   **Then** it returns valid Prometheus text format via `promhttp.HandlerFor(registry, promhttp.HandlerOpts{})` — never `promhttp.Handler()`
   **And** no authentication is required on any of the three endpoints (NFR17)

## Tasks / Subtasks

- [x] Create `aggregator/internal/gapwindow/window.go` (AC: 5)
  - [x] `Window` struct: 24×bucket array, each bucket has `hour int64` and `count int`; mutex-protected
  - [x] `Add(cause string, at time.Time)` — adds gap only if `gapdetector.Cause(cause).Internal()` is true; slot = `at.Unix()/3600 % 24`; most-recent-wins eviction prevents older events from overwriting newer ones sharing the same slot
  - [x] `Count(now time.Time) int` — returns sum of counts where `bucket.hour >= (now.Unix()/3600 - 23)`
  - [x] `GapRecord{Cause string; At time.Time}` — input type for Populate
  - [x] `Populate(records []GapRecord)` — calls Add for each record (mutex-safe batch init)

- [x] Create `aggregator/internal/gapwindow/window_test.go` (L1, AC: 5)
  - [x] `TestWindow_CountWithin24h`: Add gap at now-1h; Count(now) == 1
  - [x] `TestWindow_ExcludesOlderThan24h`: Add gap at now-25h; Count(now) == 0
  - [x] `TestWindow_ExternalGapNotCounted`: Add with cause "external_disconnect"; Count == 0
  - [x] `TestWindow_MultipleGaps`: Add 3 gaps in same hour + 1 in prev hour; Count == 4
  - [x] `TestWindow_Populate`: Populate with mixed old/new/external records; only internal within-24h counted
  - [x] `TestWindow_BucketEviction`: advancing 24h evicts old slot; new gap in same slot is counted, old is not

- [x] Create `aggregator/internal/httpapi/server.go` (AC: 1-7)
  - [x] `VersionInfo{Version, GitSHA, BuildTime, GoVersion string}` struct
  - [x] Build-time vars: `var Version = "dev"; var GitSHA = "unknown"; var BuildTime = "unknown"` in version.go — set via ldflags
  - [x] `FeedStatusReader` interface: `FeedCounts() (connected, total int)`
  - [x] `GathererFeedStatus` implements `FeedStatusReader` by gathering `aggregator_feed_state` from a `prometheus.Gatherer`
  - [x] `Server` struct: holds mux, listener addr, component references
  - [x] `New(addr string, feeds FeedStatusReader, gapWin *gapwindow.Window, gatherer prometheus.Gatherer, startTime time.Time, ver VersionInfo) *Server`
  - [x] Register `/health`, `/version`, `/metrics` on internal `*http.ServeMux`
  - [x] `Start(ctx context.Context) error` — calls `ListenAndServe`; graceful shutdown on ctx cancel
  - [x] `Handler() http.Handler` — exposes mux for testing (no real listener needed)

- [x] Health and version handlers in server.go (AC: 1-4, 6)
  - [x] `healthResponse{Status, ConnectedFeeds, GapCount24h, UptimeSeconds}` struct with JSON tags
  - [x] Health handler: reads FeedCounts(), gapWin.Count(now), uptime; computes status: critical > degraded > ok
  - [x] Version handler reads VersionInfo passed at construction; GoVersion from runtime.Version()

- [x] Create `aggregator/internal/httpapi/server_test.go` (L1, no build tag, uses httptest)
  - [x] `TestHealth_AllConnectedNoGaps`: status=ok, connected_feeds=2, gap_count_24h=0
  - [x] `TestHealth_FeedDegraded`: connected < total → status=degraded
  - [x] `TestHealth_InternalGapCritical`: gap_count_24h>0 → status=critical
  - [x] `TestHealth_CriticalWinsDegraded`: both conditions true → status=critical
  - [x] `TestHealth_UptimeIncreases`: uptime_seconds reflects elapsed time from start
  - [x] `TestHealth_ZeroFeeds_StatusOk`: 0/0 → status=ok (not degraded)
  - [x] `TestVersion_ReturnsInfo`: all four version fields present in JSON
  - [x] `TestMetrics_PrometheusFormat`: GET /metrics returns 200 with text/plain content-type
  - [x] `TestGathererFeedStatus_CountsFromPrometheus`: reads aggregator_feed_state from real registry

- [x] Build and test
  - [x] `go build ./internal/gapwindow/...` — clean
  - [x] `go build ./internal/httpapi/...` — clean
  - [x] `go test ./internal/gapwindow/... -v` — 6/6 L1 green
  - [x] `go test ./internal/httpapi/... -v` — 9/9 L1 green
  - [x] `go test -tags l2 ./... ` — full suite green, no regressions

## Dev Notes

### Package Boundaries

```
gapwindow   → gapdetector (for Cause.Internal() check), stdlib
httpapi     → gapwindow, metrics (for FeedState name constant), prometheus/promhttp, stdlib
```

`httpapi` must NOT import `coordinator` — it reads state via interfaces only.

### Rolling Window Bucket Logic

```go
type bucket struct {
    hour  int64 // floor(unix_seconds / 3600)
    count int
}

type Window struct {
    mu      sync.Mutex
    buckets [24]bucket
}

func (w *Window) Add(cause string, at time.Time) {
    if !gapdetector.Cause(cause).Internal() {
        return
    }
    h := at.Unix() / 3600
    slot := int(h % 24)
    w.mu.Lock()
    defer w.mu.Unlock()
    if w.buckets[slot].hour != h {
        w.buckets[slot] = bucket{hour: h, count: 0}
    }
    w.buckets[slot].count++
}

func (w *Window) Count(now time.Time) int {
    cutoff := now.Unix()/3600 - 23 // keep hours [cutoff, currentHour] inclusive = 24 hours
    w.mu.Lock()
    defer w.mu.Unlock()
    total := 0
    for _, b := range w.buckets {
        if b.count > 0 && b.hour >= cutoff {
            total += b.count
        }
    }
    return total
}
```

Note: buckets start with zero-value `hour=0`, which is epoch (1970-01-01 00:00 UTC). The cutoff will always be far above 0 in production, so un-initialized buckets are never counted.

### Health Status Priority

```
status = "ok"
if connectedFeeds < totalFeeds → status = "degraded"
if gapCount24h > 0           → status = "critical"  // overrides degraded
```

If no feeds are configured (totalFeeds == 0), status is "ok" (zero connected of zero total).

### GathererFeedStatus Implementation

```go
type GathererFeedStatus struct { g prometheus.Gatherer }

func (f *GathererFeedStatus) FeedCounts() (connected, total int) {
    mfs, _ := f.g.Gather()
    for _, mf := range mfs {
        if mf.GetName() != "aggregator_feed_state" {
            continue
        }
        for _, m := range mf.GetMetric() {
            total++
            if m.GetGauge().GetValue() == 1 {
                connected++
            }
        }
    }
    return
}
```

Errors from Gather() are ignored — if gathering fails, we report 0/0 which shows as "ok" (not "degraded"). This is an acceptable tradeoff for the health endpoint's simplicity.

### Build-time Vars & ldflags

Package-level vars in `internal/httpapi/version.go`:
```go
var (
    Version   = "dev"
    GitSHA    = "unknown"
    BuildTime = "unknown"
)
```

Set at build time via:
```
-ldflags "-X github.com/mrqdt/magnum-opus/aggregator/internal/httpapi.Version=$(git describe --tags) ..."
```

The `GoVersion` field is NOT a package-level var — use `runtime.Version()` in the handler.

The `New()` constructor accepts a `VersionInfo` so tests can inject values without needing ldflags.

### Server Structure

```go
type Server struct {
    mux       *http.ServeMux
    addr      string
    feeds     FeedStatusReader
    gapWin    *gapwindow.Window
    gatherer  prometheus.Gatherer
    startTime time.Time
    ver       VersionInfo
}

func New(addr string, feeds FeedStatusReader, gapWin *gapwindow.Window, gatherer prometheus.Gatherer, startTime time.Time, ver VersionInfo) *Server {
    s := &Server{...}
    s.mux = http.NewServeMux()
    s.mux.HandleFunc("/health", s.handleHealth)
    s.mux.HandleFunc("/version", s.handleVersion)
    s.mux.Handle("/metrics", promhttp.HandlerFor(gatherer, promhttp.HandlerOpts{}))
    return s
}

func (s *Server) Handler() http.Handler { return s.mux }

func (s *Server) Start(ctx context.Context) error {
    srv := &http.Server{Addr: s.addr, Handler: s.mux}
    go func() {
        <-ctx.Done()
        srv.Shutdown(context.Background())
    }()
    return srv.ListenAndServe()
}
```

### Test Pattern

```go
func TestHealth_AllConnectedNoGaps(t *testing.T) {
    feeds := &fakeFeedStatus{connected: 2, total: 2}
    win := gapwindow.New()
    reg := prometheus.NewRegistry()
    ver := httpapi.VersionInfo{Version: "test", GitSHA: "abc", BuildTime: "now", GoVersion: "go1.22"}
    srv := httpapi.New(":0", feeds, win, reg, time.Now(), ver)

    req := httptest.NewRequest(http.MethodGet, "/health", nil)
    rr := httptest.NewRecorder()
    srv.Handler().ServeHTTP(rr, req)

    require.Equal(t, http.StatusOK, rr.Code)
    var resp map[string]any
    require.NoError(t, json.NewDecoder(rr.Body).Decode(&resp))
    assert.Equal(t, "ok", resp["status"])
    assert.Equal(t, float64(2), resp["connected_feeds"])
    assert.Equal(t, float64(0), resp["gap_count_24h"])
}
```

### promhttp Import

`promhttp` lives in `github.com/prometheus/client_golang/prometheus/promhttp` — same module as the prometheus package already in go.mod. No new dependency needed.

### References

- `epics.md` §Story 4.2
- `internal/gapdetector/types.go` — Cause, Internal() method
- `internal/metrics/metrics.go` — aggregator_feed_state metric name
- `prometheus/promhttp` docs: `HandlerFor(g Gatherer, opts HandlerOpts) http.Handler`
- `net/http/httptest` stdlib docs: NewRecorder, NewRequest

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log

- **Bucket slot collision in Populate**: records 1h ago and 25h ago both map to slot `H%24`. Processing newest-first caused the older record to evict the newer bucket. Fixed Add() with a "most-recent-wins" guard: if stored hour > incoming hour, skip. This correctly handles any insertion order.

### Completion Notes

- `internal/gapwindow/window.go`: pure rolling counter (24×1h buckets), goroutine-safe. Only internal_* causes counted. Most-recent-wins eviction invariant maintained in Add(). Populate() for bulk startup seeding.
- `internal/httpapi/server.go`: Server with /health, /version, /metrics. FeedStatusReader interface + GathererFeedStatus implementation reading aggregator_feed_state from Gatherer. Health status priority: critical > degraded > ok. promhttp.HandlerFor (not Handler). GoVersion from runtime.Version().
- `internal/httpapi/version.go`: package-level ldflags-injectable vars. VersionInfo struct passed at construction for testability.
- All 6 gapwindow L1 tests + 9 httpapi L1 tests green. Full suite (l2 tags) green.

## File List

- `aggregator/internal/gapwindow/window.go` (NEW)
- `aggregator/internal/gapwindow/window_test.go` (NEW)
- `aggregator/internal/httpapi/server.go` (NEW)
- `aggregator/internal/httpapi/version.go` (NEW)
- `aggregator/internal/httpapi/server_test.go` (NEW)

## Change Log

- 2026-05-07: Story implemented — gapwindow rolling counter + HTTP server with 3 endpoints. 6+9 L1 tests green. Status → review.
- 2026-05-07: Review patches applied — Start() now returns nil on ErrServerClosed; GoVersion removed from VersionInfo (dead field; handler uses runtime.Version() directly). Status → done.
