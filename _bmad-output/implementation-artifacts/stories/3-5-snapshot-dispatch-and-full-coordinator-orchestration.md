# Story 3.5: Snapshot Dispatch & Full Coordinator Orchestration

Status: done

## Story

As the service,
I want a coordinator that manages the lifecycle of all per-symbol goroutines, dispatches snapshot requests when `NeedsSnapshot` is signalled, and drains all in-flight writes on shutdown,
So that the full pipeline from exchange feed to Redis/QuestDB runs as a single coherent unit.

## Acceptance Criteria

1. **Given** `coordinator.Run(ctx)` is called
   **Then** it starts one per-symbol goroutine for each configured (exchange, symbol) pair
   **And** manages a `sync.WaitGroup` for all goroutines
   **And** passes the root `ctx` to every goroutine so cancellation propagates on SIGTERM

2. **Given** a per-symbol goroutine emits a `NeedsSnapshot` signal
   **When** `coordinator/snapshot.go` receives it via the shared `snapRequests` channel
   **Then** it fetches the REST snapshot from the exchange (exchange-specific endpoint)
   **And** feeds the snapshot back to the symbol goroutine's reconnect state machine via `Worker.resultCh`
   **And** the symbol goroutine resumes the live feed only after sequence overlap is verified

3. **Given** the root `ctx` is cancelled (SIGTERM)
   **When** `coordinator.Shutdown()` is called
   **Then** all per-symbol goroutines detect `ctx.Done()` and exit
   **And** in-flight tick writes to Redis are drained (max 5 seconds) before the process exits
   **And** the QuestDB ILP buffer is flushed (`ilp.Close()`)
   **And** `WaitGroup.Wait()` blocks until all goroutines have exited

4. **Given** a REST snapshot fetch is in-flight when SIGTERM arrives
   **Then** the snapshot fetch context is cancelled immediately (it shares the root `ctx`)
   **And** the symbol goroutine detects cancellation, emits a best-effort `external_disconnect` gap marker, and exits cleanly
   **And** total shutdown completes within Docker's `stop_grace_period: 15s`

5. **Given** end-to-end L2 integration test
   **Then** it exercises: startup NeedsSnapshot → snapshot fetched → GoLive → tick flow → Redis write → gap detected → gap marker written
   **And** uses `FakeRedis`, `FakeQuestDB`, `MockClock`, and `FakeSnapshotFetcher` from `internal/testutil/mock/` (tagged `//go:build l2`)
   **And** verifies no duplicate gap markers are emitted on a tick-write retry scenario

## Tasks / Subtasks

- [x] Modify `aggregator/internal/coordinator/symbol.go` (AC: 4)
  - [x] In `loop()`, when `ctx.Done()` fires while `recon.State() == StateBuffering`: emit best-effort `CauseExternalDisconnect` gap marker using `context.WithTimeout(context.Background(), 500ms)` before returning
  - [x] Add test `TestWorker_CtxCancel_StateBuffering_EmitsGapMarker` to `symbol_test.go` (AC: 4)

- [x] Create `aggregator/internal/coordinator/coordinator.go` (AC: 1, 3)
  - [x] Define `SnapshotFetcher` interface: `FetchSnapshot(ctx, exch string, sym symbol.Symbol) (SnapshotResult, error)`
  - [x] Define `ExchangeConfig` struct: `Adapter exchange.Exchange`, `Symbols []symbol.Symbol`
  - [x] Define `Coordinator` struct: `exchanges []ExchangeConfig`, `stream StreamWriter`, `ilp ILPWriter`, `fetcher SnapshotFetcher`, `clock gapdetector.Clock`, `entries []symbolEntry`, `snapReqs chan SnapshotRequest`, `wg sync.WaitGroup`, `sleepFn func(ctx, d) error`
  - [x] Define unexported `symbolEntry` struct: `exchange string`, `sym symbol.Symbol`, `deltas chan exchange.Tick`, `worker *Worker`
  - [x] Implement `New(exchanges []ExchangeConfig, stream StreamWriter, ilp ILPWriter, fetcher SnapshotFetcher, clock gapdetector.Clock) *Coordinator` — creates `snapReqs` channel, per-symbol `deltas` channels, Workers; `snapReqs` cap = total symbols + 16
  - [x] Implement `WithSleep(fn func(ctx context.Context, d time.Duration) error) *Coordinator` — for test injection
  - [x] Implement `Run(ctx context.Context)` — starts snapshot dispatcher goroutine, per-exchange tick-fanout goroutines, per-exchange signal-drain goroutines, per-symbol Worker goroutines (all added to wg)
  - [x] Implement `Shutdown()` — calls `wg.Wait()`, then `ilp.Close()` with 5-second background context
  - [x] Implement `runTickFanout(ctx, exchange.Exchange, map[symbol.Symbol]chan exchange.Tick)` — reads `Ticks()`, non-blocking send to per-symbol channel (drop on full + `slog.Warn`)
  - [x] Implement `runSignalDrain(ctx, exchange.Exchange)` — reads `Signals()`, logs `SignalNeedsSnapshot` with exchange/symbol/reason; no routing in this story (gap detection handles reconnect)

- [x] Create `aggregator/internal/coordinator/snapshot.go` (AC: 2)
  - [x] Implement `runSnapshotDispatcher(ctx context.Context)` — selects on `snapReqs` and `ctx.Done()`; for each request calls `fetchWithRetry`; sends result to `req.ResultCh` (non-blocking send with `ctx.Done()` fallback)
  - [x] Implement `fetchWithRetry(ctx, SnapshotRequest) (SnapshotResult, error)` — retry loop with `backoff.Duration(attempt, c.clock)` + `c.sleepFn`; returns `ctx.Err()` when ctx cancelled

- [x] Create `aggregator/internal/exchange/kucoin/snapshot.go` (AC: 2)
  - [x] Define `OrderBookFetcher` struct: `adapter *Adapter` (accesses unexported `apiBase` + `httpClient` — same package)
  - [x] Implement `NewOrderBookFetcher(a *Adapter) *OrderBookFetcher`
  - [x] Implement `FetchSnapshot(ctx, exch string, sym symbol.Symbol) (coordinator.SnapshotResult, error)` — `GET {apiBase}/api/v1/market/orderbook/level2_100?symbol={sym}` (public endpoint, no auth); parse `data.sequence` (string→uint64), `data.bids`, `data.asks` as `[][]string`; body limit 1 MB
  - [x] Define unexported `kucoinOrderBookResp` for JSON parsing
  - [x] Add compile-time check: `var _ coordinator.SnapshotFetcher = (*OrderBookFetcher)(nil)` — note: interface includes `exch` param; pass-through (KuCoin fetcher ignores it)

- [x] Create `aggregator/internal/exchange/bybit/snapshot.go` (AC: 2)
  - [x] Define `SnapshotFetcher` struct: `baseURL string`, `httpClient *http.Client`
  - [x] Implement `NewSnapshotFetcher(httpClient *http.Client) *SnapshotFetcher` — defaults to `http.DefaultClient`, `baseURL = "https://api.bybit.com"`
  - [x] Implement `FetchSnapshot(ctx, exch string, sym symbol.Symbol) (coordinator.SnapshotResult, error)` — bybit symbol: `strings.ReplaceAll(string(sym), "-", "")` → `BTCUSDT`; `GET {baseURL}/v5/market/orderbook?category=linear&symbol={rawSym}&limit=200`; parse `result.seq` (uint64), `result.b`, `result.a` as `[][]string`; body limit 1 MB
  - [x] Define unexported `bybitOrderBookResp` for JSON parsing
  - [x] Add compile-time check: `var _ coordinator.SnapshotFetcher = (*SnapshotFetcher)(nil)`

- [x] Create `aggregator/internal/testutil/mock/fake_snapshot_fetcher.go` (AC: 5, //go:build l2)
  - [x] `FakeSnapshotFetcher` struct: `mu sync.Mutex`, `results map[string]coordinator.SnapshotResult`, `callCount int`, `calledCh chan struct{}` (closed on first call via `sync.Once`)
  - [x] `NewFakeSnapshotFetcher(results map[string]coordinator.SnapshotResult) *FakeSnapshotFetcher`
  - [x] `FetchSnapshot(ctx, exch, sym)` — increments callCount, closes calledCh on first call, returns configured result or `fmt.Errorf("no result for %s:%s", exch, sym)`
  - [x] `CallCount() int`, `WaitCalled(timeout time.Duration) bool` (blocks on calledCh)

- [x] Create `aggregator/internal/coordinator/coordinator_test.go` (AC: 5, //go:build l2)
  - [x] Define inline `fakeExchange` satisfying `exchange.Exchange` (Name, Connect, Subscribe, Ticks, Signals, Close)
  - [x] `TestCoordinator_E2E_TickFlowGapSnapshot`: see test pattern in Dev Notes
  - [x] `TestCoordinator_Shutdown_ExitsCleanly`: start coordinator, cancel ctx, call Shutdown(), verify returns within 2s

- [x] Create `aggregator/internal/coordinator/snapshot_test.go` (AC: 2, //go:build l2)
  - [x] `TestSnapshotDispatcher_SuccessPath`: snapshot request arrives, FakeSnapshotFetcher returns immediately, result arrives in `req.ResultCh`
  - [x] `TestSnapshotDispatcher_RetryOnFetchFailure`: fetcher fails twice then succeeds (using failure counter); verify result still delivered; use instant `sleepFn` to skip backoff delay

- [x] Build + test
  - [x] `go build ./internal/coordinator/... ./internal/exchange/kucoin/... ./internal/exchange/bybit/...`
  - [x] `go test -tags l2 ./internal/coordinator/... -v` — all L2 tests green
  - [x] `go test ./internal/coordinator/... -v` — L1 (modified symbol_test.go) green
  - [x] `make test-l1` — no regressions

## Dev Notes

### What This Story Produces

```
aggregator/internal/coordinator/coordinator.go   NEW
aggregator/internal/coordinator/snapshot.go      NEW
aggregator/internal/coordinator/coordinator_test.go  NEW (l2)
aggregator/internal/coordinator/snapshot_test.go     NEW (l2)
aggregator/internal/coordinator/symbol.go        MODIFIED (shutdown gap marker)
aggregator/internal/coordinator/symbol_test.go   MODIFIED (one new test)
aggregator/internal/exchange/kucoin/snapshot.go  NEW
aggregator/internal/exchange/bybit/snapshot.go   NEW
aggregator/internal/testutil/mock/fake_snapshot_fetcher.go  NEW (l2)
```

### Symbol.go Modification: Shutdown Gap Marker

In `loop()`, the `case <-ctx.Done():` branch currently just returns. Add a best-effort gap marker when the Worker is in `StateBuffering` (snapshot in-flight) at shutdown time:

```go
case <-ctx.Done():
    if w.recon.State() == reconnect.StateBuffering {
        shutCtx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
        defer cancel()
        gap := gapdetector.GapEvent{
            Cause:     gapdetector.CauseExternalDisconnect,
            SeqBefore: w.lastSeq,
            SeqAfter:  w.lastSeq + 1,
            Timestamp: w.clock.Now(),
        }
        _ = w.stream.WriteGap(shutCtx, w.exch, w.sym, gap)
        _ = w.ilp.WriteGap(shutCtx, w.exch, w.sym, gap)
    }
    return
```

**Why**: When SIGTERM fires while a snapshot fetch is in-flight, the symbol goroutine exits without any record that data is missing. The gap marker gives downstream the signal that this symbol's audit trail is incomplete at this timestamp.

**Invariant**: `w.lastSeq == 0` is valid (no live ticks received yet) — the gap SeqBefore=0, SeqAfter=1 produces `seq_gap=0` which is the same sentinel as the panic recovery path. This is acceptable.

### coordinator.go: Core Structure

```go
package coordinator

import (
    "context"
    "fmt"
    "log/slog"
    "sync"
    "time"

    "github.com/mrqdt/magnum-opus/aggregator/internal/backoff"
    "github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
    "github.com/mrqdt/magnum-opus/aggregator/internal/gapdetector"
    "github.com/mrqdt/magnum-opus/aggregator/internal/orderbook"
    "github.com/mrqdt/magnum-opus/aggregator/internal/reconnect"
    "github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
)

// SnapshotFetcher fetches a REST order book snapshot for the given exchange and symbol.
// Defined here (coordinator is the consumer); implementations live in exchange/{kucoin,bybit}/.
type SnapshotFetcher interface {
    FetchSnapshot(ctx context.Context, exch string, sym symbol.Symbol) (SnapshotResult, error)
}

// ExchangeConfig pairs an exchange adapter with its configured symbols.
type ExchangeConfig struct {
    Adapter exchange.Exchange
    Symbols []symbol.Symbol
}

// Coordinator manages the lifecycle of all per-symbol workers.
// Create with New, then call Run(ctx) in a goroutine, then Shutdown() after ctx is cancelled.
type Coordinator struct {
    exchanges []ExchangeConfig
    stream    StreamWriter
    ilp       ILPWriter
    fetcher   SnapshotFetcher
    clock     gapdetector.Clock

    entries  []symbolEntry
    snapReqs chan SnapshotRequest
    wg       sync.WaitGroup
    sleepFn  func(ctx context.Context, d time.Duration) error
}

type symbolEntry struct {
    exchange string
    sym      symbol.Symbol
    deltas   chan exchange.Tick
    worker   *Worker
}

const deltaChanCap = 256 // per-symbol; absorbs bursts without blocking fanout

// New constructs a Coordinator. Creates one Worker and one deltas channel per (exchange, symbol).
func New(
    exchanges []ExchangeConfig,
    stream StreamWriter,
    ilp ILPWriter,
    fetcher SnapshotFetcher,
    clock gapdetector.Clock,
) *Coordinator {
    totalSymbols := 0
    for _, ec := range exchanges {
        totalSymbols += len(ec.Symbols)
    }
    snapReqs := make(chan SnapshotRequest, totalSymbols+16)

    entries := make([]symbolEntry, 0, totalSymbols)
    for _, ec := range exchanges {
        for _, sym := range ec.Symbols {
            deltas := make(chan exchange.Tick, deltaChanCap)
            w := NewWorker(
                ec.Adapter.Name(), sym, deltas,
                stream, ilp, snapReqs,
                orderbook.New(), reconnect.New(), clock,
            )
            entries = append(entries, symbolEntry{
                exchange: ec.Adapter.Name(),
                sym:      sym,
                deltas:   deltas,
                worker:   w,
            })
        }
    }
    return &Coordinator{
        exchanges: exchanges,
        stream:    stream,
        ilp:       ilp,
        fetcher:   fetcher,
        clock:     clock,
        entries:   entries,
        snapReqs:  snapReqs,
        sleepFn:   sleepWithContext,
    }
}

// WithSleep overrides the backoff sleep function. Used in tests to skip real delays.
func (c *Coordinator) WithSleep(fn func(ctx context.Context, d time.Duration) error) *Coordinator {
    c.sleepFn = fn
    return c
}

// Run starts all worker goroutines and the snapshot dispatcher. Returns when ctx is cancelled.
// Call Shutdown() after Run returns to wait for all goroutines and flush the ILP buffer.
func (c *Coordinator) Run(ctx context.Context) {
    // Snapshot dispatcher
    c.wg.Add(1)
    go func() {
        defer c.wg.Done()
        c.runSnapshotDispatcher(ctx)
    }()

    for _, ec := range c.exchanges {
        // Build symbol→channel map for this exchange's fanout
        symMap := make(map[symbol.Symbol]chan exchange.Tick, len(ec.Symbols))
        for _, e := range c.entries {
            if e.exchange == ec.Adapter.Name() {
                symMap[e.sym] = e.deltas
            }
        }

        // Fan-out goroutine: routes ticks from the shared exchange channel to per-symbol channels
        c.wg.Add(1)
        adapter := ec.Adapter
        go func() {
            defer c.wg.Done()
            c.runTickFanout(ctx, adapter, symMap)
        }()

        // Signal drain goroutine: reads lifecycle signals (NeedsSnapshot from exchange adapter)
        // and logs them. Gap detection in symbol.go handles reconnect without explicit signal routing.
        c.wg.Add(1)
        go func() {
            defer c.wg.Done()
            c.runSignalDrain(ctx, adapter)
        }()

        // Per-symbol worker goroutines
        for _, e := range c.entries {
            if e.exchange != ec.Adapter.Name() {
                continue
            }
            w := e.worker
            c.wg.Add(1)
            go func() {
                defer c.wg.Done()
                w.Run(ctx)
            }()
        }
    }
}

// Shutdown waits for all goroutines to exit, then flushes the QuestDB ILP buffer.
// Must be called after ctx is cancelled (i.e., after Run returns or after cancel()).
func (c *Coordinator) Shutdown() {
    c.wg.Wait()
    shutCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
    defer cancel()
    if err := c.ilp.Close(shutCtx); err != nil {
        slog.Error("coordinator: ILP writer close failed", "err", err)
    }
}

// runTickFanout routes ticks from the exchange's shared Ticks() channel to per-symbol
// delta channels. Non-blocking send — drops the tick if the symbol's channel is full
// (the Worker will detect the resulting seq gap and request a snapshot).
func (c *Coordinator) runTickFanout(ctx context.Context, exch exchange.Exchange, symMap map[symbol.Symbol]chan exchange.Tick) {
    ticksCh := exch.Ticks()
    for {
        select {
        case <-ctx.Done():
            return
        case tick, ok := <-ticksCh:
            if !ok {
                return
            }
            if ch, found := symMap[tick.Symbol]; found {
                select {
                case ch <- tick:
                default:
                    slog.Warn("coordinator: tick dropped — symbol channel full",
                        "exchange", exch.Name(), "symbol", string(tick.Symbol), "seq", tick.Seq)
                }
            }
        }
    }
}

// runSignalDrain reads exchange lifecycle signals and logs them.
// The coordinator does not route signals to Workers in this story — seq-gap detection
// in handleTick already drives reconnect. Explicit signal routing is deferred work.
func (c *Coordinator) runSignalDrain(ctx context.Context, exch exchange.Exchange) {
    sigsCh := exch.Signals()
    for {
        select {
        case <-ctx.Done():
            return
        case sig, ok := <-sigsCh:
            if !ok {
                return
            }
            if sig.Type == exchange.SignalNeedsSnapshot {
                slog.Info("coordinator: exchange signalled NeedsSnapshot (gap detection will handle reconnect)",
                    "exchange", exch.Name(), "symbol", string(sig.Symbol), "reason", sig.Reason)
            }
        }
    }
}

func sleepWithContext(ctx context.Context, d time.Duration) error {
    select {
    case <-time.After(d):
        return nil
    case <-ctx.Done():
        return ctx.Err()
    }
}
```

### snapshot.go: Dispatcher

```go
package coordinator

import (
    "context"
    "log/slog"

    "github.com/mrqdt/magnum-opus/aggregator/internal/backoff"
    "github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
)

// runSnapshotDispatcher reads SnapshotRequests from the shared snapReqs channel,
// fetches the REST snapshot via the configured SnapshotFetcher, and delivers the
// result to the Worker's resultCh. Processes one request at a time — serialized
// dispatching is acceptable at ≤200 symbols scale.
func (c *Coordinator) runSnapshotDispatcher(ctx context.Context) {
    for {
        select {
        case <-ctx.Done():
            return
        case req := <-c.snapReqs:
            result, err := c.fetchWithRetry(ctx, req)
            if err != nil {
                // ctx was cancelled — exit cleanly
                return
            }
            select {
            case req.ResultCh <- result:
            case <-ctx.Done():
                return
            }
        }
    }
}

// fetchWithRetry calls FetchSnapshot with exponential backoff until success or ctx cancellation.
func (c *Coordinator) fetchWithRetry(ctx context.Context, req SnapshotRequest) (SnapshotResult, error) {
    for attempt := 0; ; attempt++ {
        result, err := c.fetcher.FetchSnapshot(ctx, req.Exchange, req.Symbol)
        if err == nil {
            return result, nil
        }
        if ctx.Err() != nil {
            return SnapshotResult{}, ctx.Err()
        }
        slog.Error("coordinator: snapshot fetch failed, retrying",
            "exchange", req.Exchange, "symbol", string(req.Symbol),
            "attempt", attempt+1, "err", err)
        d := backoff.Duration(attempt, c.clock)
        if err := c.sleepFn(ctx, d); err != nil {
            return SnapshotResult{}, err
        }
    }
}
```

### exchange/kucoin/snapshot.go

```go
package kucoin

import (
    "context"
    "encoding/json"
    "fmt"
    "io"
    "net/http"
    "net/url"
    "strconv"

    "github.com/mrqdt/magnum-opus/aggregator/internal/coordinator"
    "github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
)

// Compile-time proof that OrderBookFetcher satisfies coordinator.SnapshotFetcher.
var _ coordinator.SnapshotFetcher = (*OrderBookFetcher)(nil)

// OrderBookFetcher fetches L2 order book snapshots from KuCoin REST API.
// Lives in package kucoin to access adapter.apiBase and adapter.httpClient (unexported).
type OrderBookFetcher struct {
    adapter *Adapter
}

// NewOrderBookFetcher creates a fetcher backed by the adapter's HTTP client and API base URL.
// This allows test overrides (adapter.apiBase is set to a test server URL in kucoin tests).
func NewOrderBookFetcher(a *Adapter) *OrderBookFetcher {
    return &OrderBookFetcher{adapter: a}
}

const orderBookPath = "/api/v1/market/orderbook/level2_100"

// FetchSnapshot fetches the top-100 level-2 order book from KuCoin REST.
// The public endpoint requires no authentication.
func (f *OrderBookFetcher) FetchSnapshot(ctx context.Context, _ string, sym symbol.Symbol) (coordinator.SnapshotResult, error) {
    rawURL := f.adapter.apiBase + orderBookPath + "?symbol=" + url.QueryEscape(string(sym))
    req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
    if err != nil {
        return coordinator.SnapshotResult{}, fmt.Errorf("kucoin: build orderbook request: %w", err)
    }

    resp, err := f.adapter.httpClient.Do(req)
    if err != nil {
        return coordinator.SnapshotResult{}, fmt.Errorf("kucoin: orderbook request: %w", err)
    }
    defer resp.Body.Close()

    if resp.StatusCode == http.StatusTooManyRequests {
        return coordinator.SnapshotResult{}, fmt.Errorf("kucoin: orderbook rate limited (429)")
    }
    if resp.StatusCode != http.StatusOK {
        return coordinator.SnapshotResult{}, fmt.Errorf("kucoin: orderbook status %d", resp.StatusCode)
    }

    raw, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
    if err != nil {
        return coordinator.SnapshotResult{}, fmt.Errorf("kucoin: read orderbook body: %w", err)
    }

    var apiResp kucoinOrderBookResp
    if err := json.Unmarshal(raw, &apiResp); err != nil {
        return coordinator.SnapshotResult{}, fmt.Errorf("kucoin: parse orderbook: %w", err)
    }
    if apiResp.Code != "200000" {
        return coordinator.SnapshotResult{}, fmt.Errorf("kucoin: orderbook api code %s", apiResp.Code)
    }

    seq, err := strconv.ParseUint(apiResp.Data.Sequence, 10, 64)
    if err != nil {
        return coordinator.SnapshotResult{}, fmt.Errorf("kucoin: parse sequence %q: %w", apiResp.Data.Sequence, err)
    }

    bids := make(map[string]string, len(apiResp.Data.Bids))
    for _, level := range apiResp.Data.Bids {
        if len(level) == 2 {
            bids[level[0]] = level[1]
        }
    }
    asks := make(map[string]string, len(apiResp.Data.Asks))
    for _, level := range apiResp.Data.Asks {
        if len(level) == 2 {
            asks[level[0]] = level[1]
        }
    }

    return coordinator.SnapshotResult{Seq: seq, Bids: bids, Asks: asks}, nil
}

type kucoinOrderBookResp struct {
    Code string `json:"code"`
    Data struct {
        Sequence string     `json:"sequence"`
        Time     int64      `json:"time"`
        Bids     [][]string `json:"bids"`
        Asks     [][]string `json:"asks"`
    } `json:"data"`
}
```

### exchange/bybit/snapshot.go

```go
package bybit

import (
    "context"
    "encoding/json"
    "fmt"
    "io"
    "net/http"
    "strings"

    "github.com/mrqdt/magnum-opus/aggregator/internal/coordinator"
    "github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
)

var _ coordinator.SnapshotFetcher = (*SnapshotFetcher)(nil)

const defaultBybitAPIBase = "https://api.bybit.com"

// SnapshotFetcher fetches L2 order book snapshots from Bybit REST API.
// Standalone struct (no dependency on bybit.Adapter) — Bybit REST is public, no auth needed.
type SnapshotFetcher struct {
    baseURL    string
    httpClient *http.Client
}

// NewSnapshotFetcher creates a Bybit snapshot fetcher. httpClient defaults to http.DefaultClient.
func NewSnapshotFetcher(httpClient *http.Client) *SnapshotFetcher {
    if httpClient == nil {
        httpClient = http.DefaultClient
    }
    return &SnapshotFetcher{baseURL: defaultBybitAPIBase, httpClient: httpClient}
}

// FetchSnapshot fetches the top-200 level-2 order book from Bybit REST.
func (f *SnapshotFetcher) FetchSnapshot(ctx context.Context, _ string, sym symbol.Symbol) (coordinator.SnapshotResult, error) {
    // Bybit symbol format: "BTCUSDT" (canonical "BTC-USDT" → remove dash)
    rawSym := strings.ReplaceAll(string(sym), "-", "")
    rawURL := fmt.Sprintf("%s/v5/market/orderbook?category=linear&symbol=%s&limit=200", f.baseURL, rawSym)

    req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
    if err != nil {
        return coordinator.SnapshotResult{}, fmt.Errorf("bybit: build orderbook request: %w", err)
    }

    resp, err := f.httpClient.Do(req)
    if err != nil {
        return coordinator.SnapshotResult{}, fmt.Errorf("bybit: orderbook request: %w", err)
    }
    defer resp.Body.Close()

    if resp.StatusCode == http.StatusTooManyRequests {
        return coordinator.SnapshotResult{}, fmt.Errorf("bybit: orderbook rate limited (429)")
    }
    if resp.StatusCode != http.StatusOK {
        return coordinator.SnapshotResult{}, fmt.Errorf("bybit: orderbook status %d", resp.StatusCode)
    }

    raw, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
    if err != nil {
        return coordinator.SnapshotResult{}, fmt.Errorf("bybit: read orderbook body: %w", err)
    }

    var apiResp bybitOrderBookResp
    if err := json.Unmarshal(raw, &apiResp); err != nil {
        return coordinator.SnapshotResult{}, fmt.Errorf("bybit: parse orderbook: %w", err)
    }
    if apiResp.RetCode != 0 {
        return coordinator.SnapshotResult{}, fmt.Errorf("bybit: orderbook retCode %d: %s", apiResp.RetCode, apiResp.RetMsg)
    }

    bids := make(map[string]string, len(apiResp.Result.B))
    for _, level := range apiResp.Result.B {
        if len(level) == 2 {
            bids[level[0]] = level[1]
        }
    }
    asks := make(map[string]string, len(apiResp.Result.A))
    for _, level := range apiResp.Result.A {
        if len(level) == 2 {
            asks[level[0]] = level[1]
        }
    }

    return coordinator.SnapshotResult{Seq: apiResp.Result.Seq, Bids: bids, Asks: asks}, nil
}

type bybitOrderBookResp struct {
    RetCode int    `json:"retCode"`
    RetMsg  string `json:"retMsg"`
    Result  struct {
        S   string     `json:"s"`
        B   [][]string `json:"b"`
        A   [][]string `json:"a"`
        Seq uint64     `json:"seq"`
    } `json:"result"`
}
```

### FakeSnapshotFetcher (testutil/mock/fake_snapshot_fetcher.go)

```go
//go:build l2

package mock

import (
    "context"
    "fmt"
    "sync"
    "time"

    "github.com/mrqdt/magnum-opus/aggregator/internal/coordinator"
    "github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
)

// FakeSnapshotFetcher is a configurable snapshot fetcher for L2 tests.
// Results are keyed by "exchange:symbol" string.
type FakeSnapshotFetcher struct {
    mu        sync.Mutex
    results   map[string]coordinator.SnapshotResult
    callCount int
    once      sync.Once
    calledCh  chan struct{}
    // FailFirst, if > 0, causes FetchSnapshot to error for the first N calls.
    FailFirst int
}

func NewFakeSnapshotFetcher(results map[string]coordinator.SnapshotResult) *FakeSnapshotFetcher {
    return &FakeSnapshotFetcher{
        results:  results,
        calledCh: make(chan struct{}),
    }
}

func (f *FakeSnapshotFetcher) FetchSnapshot(_ context.Context, exch string, sym symbol.Symbol) (coordinator.SnapshotResult, error) {
    f.mu.Lock()
    f.callCount++
    count := f.callCount
    fail := f.FailFirst
    f.mu.Unlock()

    f.once.Do(func() { close(f.calledCh) })

    if fail > 0 && count <= fail {
        return coordinator.SnapshotResult{}, fmt.Errorf("fake: injected failure (call %d of %d)", count, fail)
    }

    f.mu.Lock()
    result, ok := f.results[exch+":"+string(sym)]
    f.mu.Unlock()
    if !ok {
        return coordinator.SnapshotResult{}, fmt.Errorf("fake: no result configured for %s:%s", exch, sym)
    }
    return result, nil
}

func (f *FakeSnapshotFetcher) CallCount() int {
    f.mu.Lock()
    defer f.mu.Unlock()
    return f.callCount
}

// WaitCalled blocks until FetchSnapshot is called at least once or the timeout elapses.
func (f *FakeSnapshotFetcher) WaitCalled(timeout time.Duration) bool {
    select {
    case <-f.calledCh:
        return true
    case <-time.After(timeout):
        return false
    }
}
```

### coordinator_test.go: E2E Test Pattern

The primary challenge is coordinating goroutine startup and snapshot processing before sending test ticks. Use `WaitCalled` then a short `time.Sleep(25ms)` to allow the Worker goroutine to process the snapshot result (resultCh is buffered, but the Worker select schedules in the next iteration):

```go
//go:build l2

package coordinator_test

import (
    "context"
    "testing"
    "time"

    "github.com/stretchr/testify/require"

    "github.com/mrqdt/magnum-opus/aggregator/internal/coordinator"
    "github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
    "github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
    "github.com/mrqdt/magnum-opus/aggregator/internal/testutil"
    "github.com/mrqdt/magnum-opus/aggregator/internal/testutil/mock"
    rediswriter "github.com/mrqdt/magnum-opus/aggregator/internal/writer/redis"
    questdbwriter "github.com/mrqdt/magnum-opus/aggregator/internal/writer/questdb"
)

// fakeExchange satisfies exchange.Exchange for coordinator L2 tests.
type fakeExchange struct {
    name    string
    ticksCh chan exchange.Tick
    sigsCh  chan exchange.Signal
}

func newFakeExchange(name string) *fakeExchange {
    return &fakeExchange{
        name:    name,
        ticksCh: make(chan exchange.Tick, 256),
        sigsCh:  make(chan exchange.Signal, 16),
    }
}

func (f *fakeExchange) Name() string                                    { return f.name }
func (f *fakeExchange) Connect(_ context.Context) error                 { return nil }
func (f *fakeExchange) Subscribe(_ []string, _ []exchange.FeedType) error { return nil }
func (f *fakeExchange) Ticks() <-chan exchange.Tick                      { return f.ticksCh }
func (f *fakeExchange) Signals() <-chan exchange.Signal                  { return f.sigsCh }
func (f *fakeExchange) Close() error                                     { return nil }

func TestCoordinator_E2E_TickFlowGapSnapshot(t *testing.T) {
    testSym := symbol.Symbol("BTC-USDT")
    clk := testutil.NewMockClock(time.Date(2024, 1, 15, 12, 0, 0, 0, time.UTC))

    fakeRedis := mock.NewFakeRedis()
    fakeQDB := mock.NewFakeQuestDB()

    noSleep := func(_ context.Context, _ time.Duration) error { return nil }
    stream := rediswriter.New(fakeRedis, clk, 0).WithSleep(noSleep)
    ilp := questdbwriter.New(fakeQDB, fakeQDB, fakeRedis, clk,
        1*time.Millisecond, 1*time.Hour, 0).WithSleep(noSleep).Start()

    fakeFetcher := mock.NewFakeSnapshotFetcher(map[string]coordinator.SnapshotResult{
        "kucoin:BTC-USDT": {Seq: 0, Bids: map[string]string{}, Asks: map[string]string{}},
    })

    fakeExch := newFakeExchange("kucoin")

    coord := coordinator.New(
        []coordinator.ExchangeConfig{{Adapter: fakeExch, Symbols: []symbol.Symbol{testSym}}},
        stream, ilp, fakeFetcher, clk,
    ).WithSleep(noSleep)

    ctx, cancel := context.WithCancel(context.Background())
    t.Cleanup(func() {
        cancel()
        coord.Shutdown()
        ilp.Close(context.Background())
    })

    go coord.Run(ctx)

    // Wait for initial snapshot to be fetched (Worker started, went to StateBuffering, requestSnapshot fired)
    require.True(t, fakeFetcher.WaitCalled(2*time.Second), "snapshot not fetched within 2s")

    // Allow Worker goroutine to receive result from resultCh and call GoLive
    // (resultCh is buffered=1; dispatcher sends immediately after fetcher returns;
    //  Worker processes it in next select iteration — 25ms is > 1 goroutine round-trip)
    time.Sleep(25 * time.Millisecond)

    streamKey := "ticks:kucoin:BTC-USDT"

    // Send ticks in Live state — verify written to Redis
    makeTick := func(seq uint64) exchange.Tick {
        return exchange.Tick{
            Exchange: "kucoin", Symbol: testSym, Seq: seq,
            Type: exchange.EventTypeUpdate, Price: "100.00", Size: "1.0",
            TsExchange: clk.Now().UnixNano(), TsLocal: clk.Now().UnixNano(),
        }
    }

    fakeExch.ticksCh <- makeTick(1)
    fakeExch.ticksCh <- makeTick(2)

    require.Eventually(t, func() bool {
        return fakeRedis.Len(streamKey) >= 2
    }, time.Second, time.Millisecond, "expected 2 tick entries in Redis")

    // Send tick with gap (seq=5, skipping 3,4) — gap marker must precede the tick
    fakeExch.ticksCh <- makeTick(5)

    require.Eventually(t, func() bool {
        return fakeRedis.Len(streamKey) >= 4 // 2 ticks + 1 gap marker + 1 tick
    }, time.Second, time.Millisecond, "expected gap marker + tick in Redis")

    entries := fakeRedis.Entries(streamKey)
    // Find gap marker
    gapCount := 0
    for _, e := range entries {
        if e.Fields["type"] == "gap" {
            gapCount++
            require.Equal(t, "external_disconnect", e.Fields["gap_cause"])
            require.Equal(t, "2", e.Fields["seq_before"])
            require.Equal(t, "5", e.Fields["seq_after"])
        }
    }
    require.Equal(t, 1, gapCount, "expected exactly 1 gap marker (no duplicates)")
}

func TestCoordinator_Shutdown_ExitsCleanly(t *testing.T) {
    clk := testutil.NewMockClock(time.Date(2024, 1, 15, 12, 0, 0, 0, time.UTC))
    testSym := symbol.Symbol("ETH-USDT")

    fakeRedis := mock.NewFakeRedis()
    fakeQDB := mock.NewFakeQuestDB()
    noSleep := func(_ context.Context, _ time.Duration) error { return nil }
    stream := rediswriter.New(fakeRedis, clk, 0).WithSleep(noSleep)
    ilp := questdbwriter.New(fakeQDB, fakeQDB, fakeRedis, clk,
        1*time.Millisecond, 1*time.Hour, 0).WithSleep(noSleep).Start()

    fakeFetcher := mock.NewFakeSnapshotFetcher(map[string]coordinator.SnapshotResult{
        "kucoin:ETH-USDT": {Seq: 0},
    })
    fakeExch := newFakeExchange("kucoin")

    coord := coordinator.New(
        []coordinator.ExchangeConfig{{Adapter: fakeExch, Symbols: []symbol.Symbol{testSym}}},
        stream, ilp, fakeFetcher, clk,
    ).WithSleep(noSleep)

    ctx, cancel := context.WithCancel(context.Background())

    go coord.Run(ctx)
    require.True(t, fakeFetcher.WaitCalled(2*time.Second))

    cancel()

    done := make(chan struct{})
    go func() {
        coord.Shutdown()
        ilp.Close(context.Background())
        close(done)
    }()

    select {
    case <-done:
        // clean exit
    case <-time.After(3 * time.Second):
        t.Fatal("Shutdown did not complete within 3s")
    }
}
```

### snapshot_test.go: Dispatcher Tests

```go
//go:build l2

package coordinator_test

import (
    "context"
    "testing"
    "time"

    "github.com/stretchr/testify/require"

    "github.com/mrqdt/magnum-opus/aggregator/internal/coordinator"
    "github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
    "github.com/mrqdt/magnum-opus/aggregator/internal/testutil"
    "github.com/mrqdt/magnum-opus/aggregator/internal/testutil/mock"
)

func TestSnapshotDispatcher_SuccessPath(t *testing.T) {
    sym := symbol.Symbol("BTC-USDT")
    clk := testutil.NewMockClock(time.Now())

    want := coordinator.SnapshotResult{Seq: 42, Bids: map[string]string{"100": "1"}, Asks: map[string]string{"101": "2"}}
    fetcher := mock.NewFakeSnapshotFetcher(map[string]coordinator.SnapshotResult{"kucoin:BTC-USDT": want})

    // Build a minimal coordinator just to invoke the dispatcher
    fakeRedis := mock.NewFakeRedis()
    fakeQDB := mock.NewFakeQuestDB()
    noSleep := func(_ context.Context, _ time.Duration) error { return nil }
    // Use a no-op stream/ilp since we only test snapshot dispatch
    stream := rediswriter.New(fakeRedis, clk, 0).WithSleep(noSleep)
    ilp := questdbwriter.New(fakeQDB, fakeQDB, fakeRedis, clk, 1*time.Millisecond, 1*time.Hour, 0).WithSleep(noSleep).Start()
    t.Cleanup(func() { ilp.Close(context.Background()) })

    coord := coordinator.New(
        []coordinator.ExchangeConfig{{Adapter: newFakeExchange("kucoin"), Symbols: []symbol.Symbol{sym}}},
        stream, ilp, fetcher, clk,
    ).WithSleep(noSleep)

    ctx, cancel := context.WithCancel(context.Background())
    t.Cleanup(cancel)

    // Extract the internal snapReqs channel by starting the coordinator
    go coord.Run(ctx)

    // The snapshot dispatcher starts and the Worker immediately sends a SnapshotRequest (StateInitial)
    require.True(t, fetcher.WaitCalled(time.Second), "dispatcher did not call fetcher")
    require.Equal(t, 1, fetcher.CallCount())
}

func TestSnapshotDispatcher_RetryOnFetchFailure(t *testing.T) {
    sym := symbol.Symbol("BTC-USDT")
    clk := testutil.NewMockClock(time.Now())

    want := coordinator.SnapshotResult{Seq: 10}
    fetcher := mock.NewFakeSnapshotFetcher(map[string]coordinator.SnapshotResult{"kucoin:BTC-USDT": want})
    fetcher.FailFirst = 2  // fail first 2 calls, succeed on 3rd

    fakeRedis := mock.NewFakeRedis()
    fakeQDB := mock.NewFakeQuestDB()
    noSleep := func(_ context.Context, _ time.Duration) error { return nil }
    stream := rediswriter.New(fakeRedis, clk, 0).WithSleep(noSleep)
    ilp := questdbwriter.New(fakeQDB, fakeQDB, fakeRedis, clk, 1*time.Millisecond, 1*time.Hour, 0).WithSleep(noSleep).Start()
    t.Cleanup(func() { ilp.Close(context.Background()) })

    coord := coordinator.New(
        []coordinator.ExchangeConfig{{Adapter: newFakeExchange("kucoin"), Symbols: []symbol.Symbol{sym}}},
        stream, ilp, fetcher, clk,
    ).WithSleep(noSleep)

    ctx, cancel := context.WithCancel(context.Background())
    t.Cleanup(cancel)

    go coord.Run(ctx)

    // Dispatcher must retry and eventually succeed (3rd call)
    require.Eventually(t, func() bool {
        return fetcher.CallCount() >= 3
    }, 2*time.Second, time.Millisecond, "dispatcher did not retry to 3 calls")
}
```

### Known Design Decisions & Limitations

**exchange.Signals() not routed to Workers (story 3.5 scope):**
`runSignalDrain` reads and logs signals but does not route them. This is safe because seq-gap detection in `handleTick` (Live state) will catch discontinuities on the next tick after a reconnect and trigger `requestSnapshot` via the gap → NeedsSnapshotNow path. The Worker only gets "stuck in StateLive with stale book" if the exchange disconnects and NEVER sends another tick. In practice, exchange reconnects resume ticks within seconds. Signal routing (immediate transition to Buffering on disconnect) is a latency optimization that can be added in Epic 4.

**Serialized snapshot dispatcher:**
One goroutine processes snapshot requests sequentially. If one exchange's REST endpoint is slow, other symbol snapshot requests are queued. At 200 symbols (not all requesting snapshots simultaneously), this is acceptable. A concurrent dispatcher per-symbol would require more complex coordination and is not worth the complexity at this scale.

**Replay buffer limitation (inherited from story 3.4):**
`reconnect.Machine` buffers only seq numbers, not full tick payloads. After `handleSnapshot` → `GoLive`, `lastSeq` is set to `result.Seq`. If any delta with seq ∈ (result.Seq, first-post-GoLive-tick) was buffered (seq-fed) but its payload was already consumed from the deltas channel, there will be a seq gap on the first post-GoLive tick. In practice: if the snapshot seq is fresh and the merge window is tight, this gap is 0 or 1 seqs. The gap is marked in the Redis stream and QuestDB audit trail.

**`time.Sleep(25ms)` in E2E test:**
This is the concession to goroutine scheduling: after `WaitCalled` confirms the fetcher was invoked, the dispatcher sends to `resultCh` (non-blocking, buffered). The Worker's goroutine must then schedule, receive from `resultCh`, and call `GoLive`. 25ms is 25,000× more than a typical goroutine context switch on a modern system. Flakiness here would indicate a systemic scheduling starvation issue unrelated to the test.

**KuCoin REST endpoint (no auth):**
`/api/v1/market/orderbook/level2_100` is a public endpoint — does not require the signed auth headers used in the token fetch path. Verify with `curl https://api.kucoin.com/api/v1/market/orderbook/level2_100?symbol=BTC-USDT`.

**Bybit REST endpoint (no auth):**
`/v5/market/orderbook?category=linear` is public. Note: `category=linear` for perpetual futures (matching `wss://stream.bybit.com/v5/public/linear`). If spot symbols are added later, the category would need to be dynamic.

### Import Graph (no cycles)

```
coordinator → exchange, reconnect, orderbook, gapdetector, symbol, backoff
kucoin       → coordinator (NEW — for SnapshotResult + SnapshotFetcher interface)
bybit        → coordinator (NEW)
main.go      → coordinator, kucoin, bybit (wires them together in story 4.3)
```

No cycles introduced. `coordinator` does not import `kucoin` or `bybit`.

### References

- `coordinator/interfaces.go`: StreamWriter, ILPWriter interfaces
- `coordinator/symbol.go`: Worker, SnapshotRequest, SnapshotResult, NewWorker
- `reconnect/reconnect.go`: StateInitial, StateBuffering, StateLive, NeedsSnapshotNow, MergeSnapshot, GoLive
- `exchange/exchange.go`: Exchange interface, Tick, Signal, SignalNeedsSnapshot, FeedType
- `symbol/symbol.go`: Symbol type, normalization
- `backoff/backoff.go`: Duration(attempt, clock)
- `gapdetector/types.go`: GapEvent, CauseExternalDisconnect, Clock
- `orderbook/orderbook.go`: New(), ApplySnapshot()
- `testutil/clock.go`: NewMockClock
- `testutil/mock/fake_redis.go`: FakeRedis
- `testutil/mock/fake_questdb.go`: FakeQuestDB
- `writer/redis/writer.go`: New, WithSleep pattern
- `writer/questdb/writer.go`: New, WithSleep pattern, Start, Close
- architecture.md §Coordinator lines 483–489, §Data Flow lines 565–572, §Shutdown line 313
- epics.md §Story 3.5 lines 699+

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

- **Missing `time` import in symbol.go**: `context.WithTimeout` + `time.Millisecond` used in new shutdown gap marker branch; added `"time"` to the import block.
- **Unused `ctx` in harness**: `newCoordinatorHarness` initially returned only `cancelFn`, leaving `ctx` as an unused variable (Go compile error). Fixed by returning `(coord, fakeExch, fakeRedis, ctx context.Context, cancel context.CancelFunc)` and updating all callers.
- **Double ilp.Close() panic**: `coord.Shutdown()` calls `ilp.Close()` internally; test cleanup also called it separately → `panic: close of closed channel`. Fixed by removing all explicit `ilp.Close()` calls from test cleanups — coordinator owns the ILP lifecycle.
- **Missing `exchange` import in snapshot_test.go**: `exchange_tick()` returns `exchange.Tick` but the exchange package was not imported. Fixed by adding the import.

### Completion Notes List

- Implemented `coordinator.go`: Coordinator struct, New(), Run(), Shutdown(), runTickFanout() (non-blocking send + drop warning), runSignalDrain() (log only), WithSleep() injection, sleepWithContext() helper.
- Implemented `snapshot.go`: runSnapshotDispatcher() with serialized request processing, fetchWithRetry() with exponential backoff via backoff.Duration().
- Modified `symbol.go`: Added shutdown gap marker in ctx.Done case when Worker is in StateBuffering — uses 500ms background context for best-effort write.
- Created `kucoin/snapshot.go`: OrderBookFetcher backed by Adapter's unexported httpClient/apiBase; parses sequence as string→uint64; compile-time interface check.
- Created `bybit/snapshot.go`: Standalone SnapshotFetcher (no Adapter dependency); converts "BTC-USDT"→"BTCUSDT"; compile-time interface check.
- Created `mock/fake_snapshot_fetcher.go`: WaitCalled() via sync.Once channel close; FailFirst field for retry injection; CallCount() under mutex.
- All 13 L2 coordinator tests pass; all 6 L1 packages pass (100% coverage on orderbook, reconnect, gapdetector).
- ilp.Close() ownership: coordinator.Shutdown() is the sole owner. Tests must NOT call ilp.Close() separately.

### File List

- aggregator/internal/coordinator/coordinator.go (NEW)
- aggregator/internal/coordinator/snapshot.go (NEW)
- aggregator/internal/coordinator/coordinator_test.go (NEW, l2)
- aggregator/internal/coordinator/snapshot_test.go (NEW, l2)
- aggregator/internal/coordinator/symbol.go (MODIFIED)
- aggregator/internal/coordinator/symbol_test.go (MODIFIED)
- aggregator/internal/exchange/kucoin/snapshot.go (NEW)
- aggregator/internal/exchange/bybit/snapshot.go (NEW)
- aggregator/internal/testutil/mock/fake_snapshot_fetcher.go (NEW, l2)

### Review Findings

- [x] [Review][Defer] AC3 Redis drain not implemented — Workers pass cancelled root `ctx` to `stream.Write()`; in-flight Redis writes abort immediately on SIGTERM. Restart gap marker covers the boundary; lost tick falls inside the restart gap. Wire proper drain in story 4.3 (service composition root). — deferred, address in story 4.3

- [x] [Review][Patch] `defer cancel()` inside loop body — replaced with explicit `cancel()` after WriteGap calls [coordinator/symbol.go]

- [x] [Review][Patch] `time.After` goroutine leak in `sleepWithContext` — replaced with `time.NewTimer(d)` + `defer t.Stop()` [coordinator/coordinator.go]

- [x] [Review][Patch] `Shutdown()` not idempotent — guarded with `sync.Once` [coordinator/coordinator.go]

- [x] [Review][Patch] Stale snapshot path skips gap marker emission — added `WriteGap(CauseInternalMergeError)` in the `ns != nil` branch of `handleSnapshot` [coordinator/symbol.go]

- [x] [Review][Patch] Missing test: AC5 no duplicate gap markers on tick-write retry — added `TestCoordinator_E2E_NoGapDuplicate_OnWriteRetry` [coordinator/coordinator_test.go]

- [x] [Review][Defer] Stale snapshot sequencing hazard after panic recovery — when panic fires during StateBuffering, recovery enqueues a second SnapshotRequest; first result is consumed, second sits in resultCh and is consumed by the next snapshot request cycle with potentially stale data [coordinator/snapshot.go] — deferred, complex architectural issue (per-request ResultCh redesign), bounded consequence, low probability
- [x] [Review][Defer] `parseSide` silent default to `SideBid` for unknown values [coordinator/symbol.go] — deferred, pre-existing from story 3.4
- [x] [Review][Defer] Gap marker write errors intentionally discarded via `_ =` (best-effort semantics per NFR9) [coordinator/symbol.go] — deferred, by design
- [x] [Review][Defer] Fixed-index test assertions in `TestWorker_GapDetected_EmitsMarker` [coordinator/symbol_test.go] — deferred, pre-existing from story 3.4
- [x] [Review][Defer] Replay buffer not applied after snapshot merge (acknowledged design limitation in Dev Notes) [coordinator/symbol.go] — deferred, pre-existing from story 3.4
- [x] [Review][Defer] `time.Sleep(25ms)` synchronization in E2E tests — explicitly documented trade-off in Dev Notes [coordinator/coordinator_test.go] — deferred, acknowledged design choice
- [x] [Review][Defer] `Shutdown()` called before `Run()` leaves ILP writer in broken state — API contract issue [coordinator/coordinator.go] — deferred, low practical risk
- [x] [Review][Defer] Unknown-symbol tick drop unlogged in `runTickFanout` (observability gap) [coordinator/coordinator.go] — deferred, minor
- [x] [Review][Defer] len != 2 snapshot entries silently skipped — correct for current API format [exchange/kucoin/snapshot.go, exchange/bybit/snapshot.go] — deferred, no current API returns triples
- [x] [Review][Defer] One extra HTTP call when ctx already cancelled at entry to `fetchWithRetry` [coordinator/snapshot.go] — deferred, minimal shutdown impact
- [x] [Review][Defer] `seq_gap=0` when `lastSeq=0` on shutdown gap marker — acknowledged in Dev Notes [coordinator/symbol.go] — deferred, known semantic limitation

## Change Log

- 2026-05-07: Story implemented — coordinator orchestration, snapshot dispatch with retry, kucoin/bybit REST fetchers, FakeSnapshotFetcher mock, 13 L2 tests + 1 new L1 test all green. Status → review.
- 2026-05-07: Code review complete — 1 decision-needed, 5 patches, 11 deferred, 7 dismissed.
