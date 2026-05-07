# Story 3.4: Per-Symbol Coordinator Worker

Status: done

## Story

As the service,
I want a per-symbol goroutine that wires the order book, gap detector, reconnect state machine, and writers into a complete processing pipeline with backpressure isolation,
So that tick capture continues even if Redis or QuestDB writes are slow or failing.

## Acceptance Criteria

1. **Given** a normalized tick arrives on the symbol's input channel
   **When** the per-symbol goroutine (`coordinator/symbol.go`) processes it
   **Then** it calls `orderbook.Apply(delta)` (pure, no IO) for update events
   **And** it calls `gapdetector.Detect(prev, next, cause, clock)` (pure, no IO)
   **And** if a gap is detected, it calls `StreamWriter.WriteGap` and `ILPWriter.WriteGap` before writing the next tick
   **And** it calls reconnect state machine Feed/NeedsSnapshotNow as appropriate and dispatches `NeedsSnapshot` requests via the snapshot channel

2. **Given** the writer calls can block on retry
   **When** the coordinator goroutine attempts to write a tick
   **Then** all Write calls pass `ctx` so cancellation interrupts any blocked retry
   **And** the coordinator does NOT halt on a Write error — it logs and continues (NFR9)

3. **Given** an internal gap cause (`internal_buffer_overflow` or `internal_merge_error`) is detected
   **Then** it is logged at `slog.Error` level
   **And** `external_*` causes are logged at `slog.Warn`

4. **Given** a panic occurs inside the per-symbol goroutine
   **Then** the goroutine recovers at `Run()` entry only (not in helper functions)
   **And** logs exchange, symbol, and `runtime/debug.Stack()`
   **And** emits `CauseInternalMergeError` gap marker to both writers
   **And** re-enters `loop()` with a fresh reconnect cycle (NeedsSnapshotNow("panic"))

5. **Given** `ctx` is cancelled (SIGTERM)
   **Then** the goroutine exits the `Run()` loop via `ctx.Done()`
   **And** any in-flight Write calls unblock because they check `ctx`

6. **Given** L2 integration tests for `coordinator/symbol.go`
   **Then** they use `FakeRedis` and `FakeQuestDB` from `internal/testutil/mock/` (tagged `//go:build l2`)
   **And** cover: normal tick flow (Redis+QuestDB written), gap detected and markers emitted, Redis write failure continues capture, panic recovery, ctx cancellation exits goroutine

## Tasks / Subtasks

- [x] Create `aggregator/internal/coordinator/symbol.go` (AC: 1–5)
  - [x] Define `SnapshotResult{Seq uint64, Bids, Asks map[string]string}` — carries REST snapshot data
  - [x] Define `SnapshotRequest{Exchange, Symbol, Signal *reconnect.NeedsSnapshot, ResultCh chan SnapshotResult}` — sent to snapshot dispatcher (story 3.5)
  - [x] Define `Worker` struct: exch, sym, deltas `<-chan exchange.Tick`, stream `StreamWriter`, ilp `ILPWriter`, snapRequests `chan<- SnapshotRequest`, resultCh `chan SnapshotResult`, book, recon, clock, lastSeq
  - [x] `NewWorker(...)` constructor — allocates `resultCh = make(chan SnapshotResult, 1)`
  - [x] `Run(ctx context.Context)` — outer `defer recover()` for panic handling (AC4), then calls `loop(ctx)`, then re-enters loop on panic
  - [x] `loop(ctx)` — if recon.State==StateInitial call NeedsSnapshotNow("initial") and requestSnapshot; for select: ctx.Done, deltas, resultCh
  - [x] `handleTick(ctx, tick)`:
    - If StateBuffering: recon.Feed(tick.Seq), return
    - gap detect: gapdetector.Detect(lastSeq, tick.Seq, CauseExternalDisconnect, clock)
    - if gap: log at Error/Warn based on cause.Internal(); WriteGap to stream + ilp (log error, continue)
    - update lastSeq; apply to book (update events only)
    - stream.Write(ctx, tick) — log error, continue (NFR9)
    - ilp.Write(ctx, tick) — log error, continue (NFR9)
  - [x] `handleSnapshot(ctx, result)` — apply book.ApplySnapshot, recon.MergeSnapshot, replay buffered deltas, GoLive if success; if merge_error request new snapshot
  - [x] `requestSnapshot(ctx, ns)` — non-blocking send to snapRequests channel with ctx.Done guard
  - [x] Panic recovery: recover() logs exchange+symbol+stack, calls WriteGap (CauseInternalMergeError), calls recon.NeedsSnapshotNow("panic"), re-enters loop

- [x] Create `aggregator/internal/coordinator/symbol_test.go` (AC: 6)
  - [x] Build tag: `//go:build l2`; package: `coordinator_test`
  - [x] `TestWorker_NormalTickFlow`: start in Live state (recon.GoLive()), send update tick, verify Redis+QuestDB written
  - [x] `TestWorker_GapDetected_EmitsMarker`: send tick seq=1, then seq=3 (skip 2), verify WriteGap called before tick write
  - [x] `TestWorker_RedisWriteFailure_ContinuesCapture`: FakeRedis always fails on second write; second tick still processed (not halted)
  - [x] `TestWorker_PanicRecovery`: cause a panic in handleTick (via malformed tick or mock), verify goroutine recovers and re-enters loop
  - [x] `TestWorker_CtxCancellation`: cancel ctx, verify Run() returns within 500ms

- [x] Run `go build ./internal/coordinator/...` — compile check (AC: all)
- [x] Run `go test -tags l2 ./internal/coordinator/... -v` — all L2 tests green (AC: all)
- [x] Run `make test-l1` — no regressions in L1 suite (AC: all)

## Dev Notes

### What This Story Produces

```
aggregator/internal/coordinator/symbol.go       NEW
aggregator/internal/coordinator/symbol_test.go  NEW
```

### Critical: consumer-owns-interface rule

`coordinator/symbol.go` is IN the coordinator package — it uses `StreamWriter` and `ILPWriter` directly (defined in `coordinator/interfaces.go`). No new interfaces needed.

### SnapshotResult and SnapshotRequest

Define these in `coordinator/symbol.go` (not in interfaces.go — they are implementation details of the coordinator).

```go
// SnapshotResult carries a completed REST snapshot from the dispatcher (story 3.5).
type SnapshotResult struct {
    Seq  uint64
    Bids map[string]string
    Asks map[string]string
}

// SnapshotRequest is sent to the snapshot dispatcher when a NeedsSnapshot signal fires.
// The dispatcher fetches the REST snapshot and sends the result to ResultCh.
type SnapshotRequest struct {
    Exchange string
    Symbol   symbol.Symbol
    Signal   *reconnect.NeedsSnapshot
    ResultCh chan SnapshotResult // per-worker channel; dispatcher sends exactly one result
}
```

### Worker Struct

```go
package coordinator

import (
    "context"
    "log/slog"
    "runtime/debug"

    "github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
    "github.com/mrqdt/magnum-opus/aggregator/internal/gapdetector"
    "github.com/mrqdt/magnum-opus/aggregator/internal/orderbook"
    "github.com/mrqdt/magnum-opus/aggregator/internal/reconnect"
    "github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
)

type Worker struct {
    exch         string
    sym          symbol.Symbol
    deltas       <-chan exchange.Tick
    stream       StreamWriter
    ilp          ILPWriter
    snapRequests chan<- SnapshotRequest
    resultCh     chan SnapshotResult // receives snapshot from dispatcher
    book         *orderbook.OrderBook
    recon        *reconnect.Machine
    clock        gapdetector.Clock
    lastSeq      uint64
}

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
) *Worker {
    return &Worker{
        exch:         exch,
        sym:          sym,
        deltas:       deltas,
        stream:       stream,
        ilp:          ilp,
        snapRequests: snapRequests,
        resultCh:     make(chan SnapshotResult, 1),
        book:         book,
        recon:        recon,
        clock:        clock,
    }
}
```

### Run and Panic Recovery

```go
// Run starts the processing loop with panic recovery at this level only.
func (w *Worker) Run(ctx context.Context) {
    for {
        func() {
            defer func() {
                if r := recover(); r != nil {
                    slog.Error("coordinator: panic in symbol goroutine",
                        "exchange", w.exch, "symbol", string(w.sym),
                        "panic", r, "stack", string(debug.Stack()))
                    // Best-effort gap marker for crash
                    gap := gapdetector.GapEvent{
                        Cause:     gapdetector.CauseInternalMergeError,
                        SeqBefore: w.lastSeq,
                        SeqAfter:  w.lastSeq,
                        Timestamp: w.clock.Now(),
                    }
                    _ = w.stream.WriteGap(ctx, w.exch, w.sym, gap)
                    _ = w.ilp.WriteGap(ctx, w.exch, w.sym, gap)
                    // Re-request snapshot for fresh cycle
                    ns := w.recon.NeedsSnapshotNow("panic")
                    _ = w.requestSnapshot(ctx, ns)
                }
            }()
            w.loop(ctx)
        }()
        // If ctx is cancelled, stop the outer restart loop
        if ctx.Err() != nil {
            return
        }
        // If loop returned normally (no panic), also stop
        return
    }
}
```

**Note on defer/recover pattern**: The inner `func()` is needed because `defer` in a for loop only runs when the function returns. The outer for loop re-enters `loop()` only after a panic recovery (non-panic exit is caught by the `return` at the end).

### loop() and Tick Processing

```go
func (w *Worker) loop(ctx context.Context) {
    // Request initial snapshot if machine is in Initial state
    if w.recon.State() == reconnect.StateInitial {
        ns := w.recon.NeedsSnapshotNow("initial")
        if err := w.requestSnapshot(ctx, ns); err != nil {
            return
        }
    }

    for {
        select {
        case <-ctx.Done():
            return
        case tick := <-w.deltas:
            w.handleTick(ctx, tick)
        case result := <-w.resultCh:
            w.handleSnapshot(ctx, result)
        }
    }
}
```

### handleTick

```go
func (w *Worker) handleTick(ctx context.Context, tick exchange.Tick) {
    // While buffering, only feed seq — don't write to Redis/QuestDB yet
    if w.recon.State() == reconnect.StateBuffering {
        w.recon.Feed(tick.Seq)
        return
    }

    // Gap detection (only in Live state)
    if w.lastSeq > 0 {
        if gap := gapdetector.Detect(w.lastSeq, tick.Seq, gapdetector.CauseExternalDisconnect, w.clock); gap != nil {
            if gap.Cause.Internal() {
                slog.Error("coordinator: gap detected", "exchange", w.exch, "symbol", string(w.sym), "cause", gap.Cause, "seq_before", gap.SeqBefore, "seq_after", gap.SeqAfter)
            } else {
                slog.Warn("coordinator: gap detected", "exchange", w.exch, "symbol", string(w.sym), "cause", gap.Cause, "seq_before", gap.SeqBefore, "seq_after", gap.SeqAfter)
            }
            if err := w.stream.WriteGap(ctx, w.exch, w.sym, *gap); err != nil && ctx.Err() == nil {
                slog.Error("coordinator: WriteGap (stream) failed", "err", err)
            }
            if err := w.ilp.WriteGap(ctx, w.exch, w.sym, *gap); err != nil && ctx.Err() == nil {
                slog.Error("coordinator: WriteGap (ilp) failed", "err", err)
            }
        }
    }
    w.lastSeq = tick.Seq

    // Apply update events to the order book
    if tick.Type == exchange.EventTypeUpdate {
        w.book.Apply(orderbook.Delta{
            Seq:        tick.Seq,
            Side:       parseSide(tick.Side),
            Price:      tick.Price,
            Size:       tick.Size,
            TsExchange: tick.TsExchange,
        })
    }

    // Write to Redis — ctx propagates; error is logged, not propagated (NFR9)
    if err := w.stream.Write(ctx, tick); err != nil && ctx.Err() == nil {
        slog.Error("coordinator: stream.Write failed", "err", err)
    }
    // Write to QuestDB — buffered, non-blocking; error logged, not propagated
    if err := w.ilp.Write(ctx, tick); err != nil && ctx.Err() == nil {
        slog.Error("coordinator: ilp.Write failed", "err", err)
    }
}

func parseSide(s string) orderbook.Side {
    if s == "ask" {
        return orderbook.SideAsk
    }
    return orderbook.SideBid
}
```

### handleSnapshot

```go
func (w *Worker) handleSnapshot(ctx context.Context, result SnapshotResult) {
    w.book.ApplySnapshot(result.Seq, result.Bids, result.Asks)
    replay, ns := w.recon.MergeSnapshot(result.Seq)
    if ns != nil {
        // Stale snapshot (merge_error) — request a new one
        slog.Warn("coordinator: stale snapshot, requesting new", "exchange", w.exch, "symbol", string(w.sym))
        _ = w.requestSnapshot(ctx, ns)
        return
    }
    // Apply buffered deltas (their full data is not stored — just seq numbers for overlap check)
    // The book is already up-to-date from ApplySnapshot; replay list confirms which seqs were processed
    _ = replay
    w.recon.GoLive()
    slog.Info("coordinator: book live", "exchange", w.exch, "symbol", string(w.sym))
}
```

**Note on replay**: `reconnect.Machine` buffers only seq numbers (not full delta payloads). After MergeSnapshot returns replay seqs, the full delta payloads are no longer available (they were not buffered). For this implementation, the board is initialized from the snapshot and we trust the exchange feed's eventual consistency. If strict replay is needed, the Worker would need to buffer full `exchange.Tick` objects — this is a known limitation noted for future Epic 4 enhancement.

### requestSnapshot

```go
func (w *Worker) requestSnapshot(ctx context.Context, ns *reconnect.NeedsSnapshot) error {
    req := SnapshotRequest{
        Exchange: w.exch,
        Symbol:   w.sym,
        Signal:   ns,
        ResultCh: w.resultCh,
    }
    select {
    case w.snapRequests <- req:
        return nil
    case <-ctx.Done():
        return ctx.Err()
    }
}
```

### L2 Test Pattern

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
    "github.com/mrqdt/magnum-opus/aggregator/internal/orderbook"
    "github.com/mrqdt/magnum-opus/aggregator/internal/reconnect"
    "github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
    "github.com/mrqdt/magnum-opus/aggregator/internal/testutil"
    "github.com/mrqdt/magnum-opus/aggregator/internal/testutil/mock"
    rediswriter "github.com/mrqdt/magnum-opus/aggregator/internal/writer/redis"
    questdbwriter "github.com/mrqdt/magnum-opus/aggregator/internal/writer/questdb"
)

var testSym = symbol.Symbol("BTC-USDT")

func newLiveWorker(t *testing.T, redis *mock.FakeRedis, qdb *mock.FakeQuestDB) (*coordinator.Worker, chan exchange.Tick, context.CancelFunc) {
    t.Helper()
    deltas := make(chan exchange.Tick, 10)
    snaps := make(chan coordinator.SnapshotRequest, 1)
    clk := testutil.NewMockClock(time.Date(2024, 1, 15, 12, 0, 0, 0, time.UTC))

    noSleep := func(_ context.Context, _ time.Duration) error { return nil }
    stream := rediswriter.New(redis, clk, 10*time.Second).WithSleep(noSleep)
    ilp := questdbwriter.New(qdb, qdb, redis, clk,
        1*time.Millisecond, 1*time.Hour, 30*time.Second).WithSleep(noSleep).Start()

    book := orderbook.New()
    recon := reconnect.New()
    recon.GoLive() // start in Live state for tests — bypass snapshot phase

    w := coordinator.NewWorker("kucoin", testSym, deltas, stream, ilp, snaps, book, recon, clk)

    ctx, cancel := context.WithCancel(context.Background())
    go w.Run(ctx)

    t.Cleanup(func() {
        cancel()
        ilp.Close(context.Background())
    })
    return w, deltas, cancel
}
```

**Important**: The `rediswriter.Writer` and `questdbwriter.Writer` are created in each test since they satisfy `coordinator.StreamWriter` and `coordinator.ILPWriter` by structural typing (compile-time check is in their respective packages).

### Panic Recovery Test Pattern

To force a panic in tests:
- Create a custom `StreamWriter` implementation that panics on Write
- Or use a special mock that has a `PanicOnNext` flag

For simplicity, use a channel-based signal: a mock writer that panics on the first call:
```go
type panicWriter struct {
    real coordinator.StreamWriter
    done bool
}
func (p *panicWriter) Write(ctx context.Context, tick exchange.Tick) error {
    if !p.done {
        p.done = true
        panic("injected panic for test")
    }
    return p.real.Write(ctx, tick)
}
func (p *panicWriter) WriteGap(ctx context.Context, exch string, sym symbol.Symbol, gap gapdetector.GapEvent) error {
    return p.real.WriteGap(ctx, exch, sym, gap)
}
```

### Gap Detection Cause Logic

For the coordinator in Live state, the gap cause for seq discontinuities is:
- `CauseExternalDisconnect` for the periodic gap check (seq jump without explicit signal)
- `CauseInternalMergeError` for panic recovery
- `CauseExternalDisconnect` when the reconnect machine emits NeedsSnapshot("disconnect")
- `CauseExternalRateLimit` when the exchange signals rate limiting (not in scope for this story)

### Replay Buffer Limitation

`reconnect.Machine` stores only seq numbers (not full delta payloads). After `MergeSnapshot`, the returned replay seqs cannot be re-applied without the original delta data. The Worker calls `recon.GoLive()` after `ApplySnapshot` — the book is seeded from the REST snapshot and live deltas will fill in subsequent updates. This is an accepted gap in the audit trail (snapshot initialisation is written to QuestDB via ILP with EventTypeSnapshot, not to Redis).

### References

- `coordinator/interfaces.go`: StreamWriter and ILPWriter interfaces
- `orderbook/orderbook.go`: OrderBook.Apply, ApplySnapshot, Delta, Side
- `reconnect/reconnect.go`: Machine, NeedsSnapshot, StateBuffering, StateLive, GoLive
- `gapdetector/gapdetector.go`: Detect, GapEvent, Cause, Clock
- `gapdetector/types.go`: Cause constants and Internal() method
- `exchange/exchange.go`: Tick, EventType (Update/Trade/Snapshot)
- `testutil/clock.go`: NewMockClock
- `testutil/mock/fake_redis.go`: FakeRedis
- `testutil/mock/fake_questdb.go`: FakeQuestDB
- epics.md Story 3.4 ACs: line 662

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

### File List

- aggregator/internal/coordinator/symbol.go (NEW)
- aggregator/internal/coordinator/symbol_test.go (NEW)

### Change Log

- 2026-05-06: Implemented symbol.go and symbol_test.go. 5 L2 tests pass, L1 suite green. Key design note: used `panicked` bool flag in Run() outer loop to distinguish panic-recovery re-entry from normal loop exit. Used maxRetryDur=0 in test stream writers to avoid infinite retry with frozen MockClock. Separate FakeRedis instances for stream vs QuestDB audit in the Redis failure test to isolate failure injection.
- 2026-05-06: Applied code review patches: (1) SeqAfter=lastSeq+1 for panic gap event (avoids seq_gap=-1); (2) reset lastSeq=0 after panic to prevent spurious disconnect gap on first live tick; (3) drain stale resultCh in panic recover block; (4) handleSnapshot state guard (discard if not StateBuffering); (5) set lastSeq=result.Seq in handleSnapshot to anchor gap detection from snapshot point; (6) log error from stale-snapshot requestSnapshot failure; (7) removed dead Internal() branch in handleTick; (8) added gap ordering+cause assertions to gap test; (9) added internal_merge_error assertion to panic test.
