# Story 5.5: L2 Order Book State Machine

Status: done

## Story

As a developer,
I want `internal/orderbook/` implemented as a pure, zero-IO L2 order book state machine satisfying the `BookApplier` interface from `consumer/`,
so that the consumer can apply ticks, snapshots, and gaps to maintain per-symbol book state without any IO coupling.

## Acceptance Criteria

1. `internal/orderbook/` implements `consumer.BookApplier` (ApplyTick, ApplySnapshot, ApplyGap) — interface satisfied via structural typing, no import of `consumer/`.
2. `OrderBook.ApplyTick` handles OB delta ticks: level > 0 updates the book; size == "0" removes the level; zero-size delta for unknown level is a no-op (no panic, no new level created).
3. `OrderBook.ApplyTick` handles trade ticks: level == 0 ticks are ignored by the book (trade data belongs to accumulator).
4. `OrderBook.ApplySnapshot` resets the book to an empty state (book is ready to receive fresh deltas from seq > snapshot.seq).
5. Cold-start delta buffering: deltas arriving before the first snapshot are buffered up to `coldStartBufferSize`. Buffer overflow (≥ coldStartBufferSize) emits a `GapMarker{GapCause: "cold_start_buffer_overflow"}` via the provided gap emitter function and clears the buffer. On first snapshot: replay buffered deltas with seq > snapshot.seq; discard others. Exchange seq reset heuristic: if `snapshot.seq < max(buffered_delta.seq) / 2`, treat all buffered deltas as stale, discard, emit `gap_cause=seq_reset`. Second snapshot during replay stops replay, resets, emits `gap_cause=snapshot_superseded`.
6. `BestQuote()` returns the current best bid and best ask as (price string, size string) pairs; returns ("", "", "", "") if book is empty on either side.
7. `TopNDepth(n int, side string)` returns the sum of sizes for the top N price levels on the given side ("bid" or "ask"); returns 0 if the book has fewer than N levels (use available depth — no error).
8. `go test ./...` and `go test -tags l2 ./...` pass with zero failures; L1 tests cover all state machine transitions using pure inputs (no Redis, no IO, no time.Now).

## Tasks / Subtasks

- [ ] Create `internal/orderbook/orderbook.go` (AC: 1–7)
  - [ ] Define `Level{Price string, Size string}` (price and size as strings per serialization rules)
  - [ ] Define `OrderBook` struct with bids/asks maps, cold-start buffer, snapshot-received flag
  - [ ] Implement `ApplyTick(t consumer.Tick)` — OB delta and trade routing
  - [ ] Implement `ApplySnapshot(s consumer.SnapshotEvent)` with cold-start replay logic
  - [ ] Implement `ApplyGap(g consumer.GapMarker)` — resets book state
  - [ ] Implement `BestQuote() (bestBidPrice, bestBidSize, bestAskPrice, bestAskSize string)`
  - [ ] Implement `TopNDepth(n int, side string) float64`
  - [ ] Implement cold-start buffer: buffer, overflow detection, seq-reset heuristic, snapshot_superseded handling

- [ ] Create `internal/orderbook/orderbook_test.go` (AC: 8)
  - [ ] TestApplyTick_UpdatesLevel
  - [ ] TestApplyTick_RemovesLevelOnZeroSize
  - [ ] TestApplyTick_ZeroSizeUnknownLevel_NoOp
  - [ ] TestApplyTick_TradeIgnored
  - [ ] TestApplySnapshot_ResetsBook
  - [ ] TestBestQuote_EmptyBook
  - [ ] TestBestQuote_AfterUpdates
  - [ ] TestTopNDepth_FewerLevelsThanN
  - [ ] TestColdStart_BufferReplay
  - [ ] TestColdStart_BufferOverflow_EmitsGap
  - [ ] TestColdStart_SeqReset_EmitsGap
  - [ ] TestColdStart_SecondSnapshot_EmitsGap

- [ ] Run test suite (AC: 8)
  - [ ] `go test ./...` passes
  - [ ] `go test -tags l2 ./...` passes

## Dev Notes

### Package Constraints

- Zero internal imports except `internal/consumer` for the Tick/GapMarker/SnapshotEvent types (imported for type reuse, not interface satisfaction)
- Actually to avoid circular imports: `orderbook/` must NOT import `consumer/` — it defines its own local types compatible with consumer via structural typing. Use a local `GapEmitter` func type for gap emission.
- No IO, no time.Now(), no goroutines. Pure state machine.

### Type Definitions

Since `orderbook/` cannot import `consumer/` (would create a cycle: consumer imports orderbook), define local compatible types or accept the consumer types as parameters via interface. The cleanest approach:

Copy the relevant type fields into local structs OR define the methods to accept the same field values (not the consumer.* types). Best approach: **define local structs** that mirror consumer types, and the consumer passes values directly.

```go
// Tick mirrors consumer.Tick fields needed by the OB.
type Tick struct {
    Seq   int64
    TsMs  int64
    Price string
    Size  string
    Side  string
    Level int
}

type SnapshotEvent struct {
    Seq  int64
    TsMs int64
}

type GapMarker struct {
    SeqBefore int64
    SeqAfter  int64
    GapCause  string
    GapTsMs   int64
    Exchange  string
    Symbol    string
}

// GapEmitter is called when the OB needs to emit a synthetic gap.
type GapEmitter func(GapMarker)
```

The consumer then converts consumer.Tick → orderbook.Tick before calling ApplyTick. This avoids any import cycle.

### OrderBook Struct

```go
type OrderBook struct {
    exchange string
    symbol   string
    bids     map[string]string // price → size (string decimal)
    asks     map[string]string // price → size
    
    // cold-start buffer
    coldBuf       []Tick
    maxColdBuf    int
    maxColdSeq    int64 // max seq seen in cold buffer
    snapshotSeen  bool
    snapSeq       int64
    
    emitGap GapEmitter
}

func New(exchange, symbol string, coldBufSize int, emitGap GapEmitter) *OrderBook
```

### Price Level Operations

All prices stored as string keys in maps. Comparing prices for best bid/ask requires converting to float64 for comparison. Use `strconv.ParseFloat(price, 64)`. Return the level with the highest float value as best bid, lowest as best ask.

### Cold-Start Sequence

```
State: snapshotSeen=false
  ApplyTick(t where t.Level > 0):
    buffer t
    update maxColdSeq
    if len(coldBuf) >= maxColdBuf:
      emitGap(cold_start_buffer_overflow)
      coldBuf = nil
  
  ApplySnapshot(s):
    snapshotSeen = true
    snapSeq = s.Seq
    // seq reset check
    if snapSeq < maxColdSeq / 2 (and maxColdSeq > 0):
      emitGap(seq_reset)
      coldBuf = nil
      bids, asks = {}, {}
      return
    // replay: apply buffered deltas with seq > snapSeq
    for each tick in coldBuf:
      if tick.Seq > snapSeq: applyDelta(tick)
    coldBuf = nil
    bids, asks = {} (reset first, THEN replay — snapshot starts clean)
```

Wait, correct order: reset book first, then replay:
```
ApplySnapshot(s):
    bids, asks = {}, {}   // start clean
    snapshotSeen = true
    snapSeq = s.Seq
    if seq reset heuristic: emitGap(seq_reset); coldBuf=nil; return
    for each tick in coldBuf where tick.Seq > snapSeq:
      applyDelta(tick)
    coldBuf = nil
```

### Second Snapshot During Cold-Start Replay

In the replay loop, if `ApplySnapshot` is called again while `snapshotSeen` is already true but we're still in replay — this can't happen in a single-goroutine design (replay is synchronous). The "second snapshot during replay" case means: a second snapshot arrives in the cold buffer BEFORE the first snapshot is processed. Handle: drain the cold buffer looking for snapshot events; if found, stop replay, emit `snapshot_superseded`, reset, apply the later snapshot.

Since SnapshotEvent is in a separate method, not in the cold buffer, the second snapshot arrives as a separate `ApplySnapshot` call. The consumer calls `ApplySnapshot` again. At that point `snapshotSeen` is true; treat it as a gap + reinit:
```go
if c.snapshotSeen:
    // second snapshot: emit gap, full reinit
    c.emitGap(GapMarker{GapCause: "snapshot_superseded"})
    c.bids, c.asks = {}, {}
    c.snapSeq = s.Seq
    return
```

### ApplyGap

Resets book state (clears bids, asks, cold buffer, resets snapshotSeen to false). OFI state is reset by the accumulator (consumer calls acc.Reset()).

### BestQuote

Iterate all keys in bids map, find max price as best bid. Iterate all keys in asks map, find min price as best ask. Return ("", "", "", "") if either map is empty.

### TopNDepth

For bids: sort prices descending, sum top N sizes. For asks: sort prices ascending, sum top N sizes. If fewer than N levels exist, sum all available (no error).

Parse price strings to float64 for sorting. Parse size strings to float64 for summing. Return 0 on parse errors.

### Import Note

`orderbook/` imports only stdlib packages: `strconv`, `sort`. No internal imports. The consumer package's types are mirrored locally to avoid cycles.

Consumer wires the two: `consumer.BookApplier` interface is:
```go
type BookApplier interface {
    ApplyTick(t Tick)
    ApplySnapshot(s SnapshotEvent)
    ApplyGap(g GapMarker)
}
```

And orderbook satisfies this if its method signatures match the **consumer** types. But since orderbook defines its own local types, the consumer must use an adapter/wrapper. The simplest fix: make the consumer call adapter functions that convert consumer.* → orderbook.* types. This is a thin wrapper in `cmd/candle/main.go` or a small adapter struct.

Alternatively, define orderbook to accept the consumer types directly. This requires orderbook to import consumer — which creates a cycle since consumer imports orderbook indirectly via BookApplier.

**Resolution: define a thin adapter in `internal/consumer/` that wraps `*orderbook.OrderBook` and implements `BookApplier` by converting types.** This adapter lives in consumer/ and handles the type conversion, so orderbook stays pure.

### References

- [project-context.md#Critical Behavioral Rules] — zero-size delta, cold-start buffer, OFI reset on gap
- [project-context.md#Forbidden Anti-Patterns] — no sync.Mutex on OrderBook
- [epics.md#Epic 5 Story 5] — cold-start buffer size, buffer overflow gap cause

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- `internal/orderbook/` created as a pure zero-IO state machine with local type mirrors (Tick, SnapshotEvent, GapMarker) to avoid circular imports with `consumer/`.
- Cold-start buffer replays deltas with seq > snapSeq on first snapshot; overflow emits `cold_start_buffer_overflow` gap.
- Seq reset heuristic: if snapSeq < maxColdSeq/2, discard buffer and emit `seq_reset` gap.
- Second snapshot emits `snapshot_superseded` gap and reinits book state.
- BestQuote returns ("","","","") when either side is empty — tests fixed to populate both sides.
- TopNDepth sorts by price float (desc for bids, asc for asks) and sums top N sizes.
- No stdlib imports beyond `sort`, `strconv`, `time`.

### File List

- candle-service/internal/orderbook/orderbook.go (new)
- candle-service/internal/orderbook/orderbook_test.go (new)

## Change Log

- 2026-05-08: Story implemented
