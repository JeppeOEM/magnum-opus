# Story 6.1: OB State Machine Fixture Validation

Status: done

## Story

As a developer,
I want the L2 order-book state machine validated against realistic multi-level tick fixtures,
so that Epic 6 feature computation is built on a demonstrably correct book — if the OB has a subtle bug, every downstream field is silently wrong with no signal to detect it.

## Acceptance Criteria

1. A fixture test file `internal/orderbook/fixture_test.go` replays a realistic multi-level BTC-USDT tick sequence (≥ 20 ticks across bid and ask sides) and asserts the exact resulting book state (specific price levels and sizes) after each significant event.

2. The fixture covers all OB state transitions in one contiguous sequence:
   - Cold-start buffering: ticks arrive before snapshot
   - Snapshot arrival: buffered deltas replayed correctly; seq-filtered (only seq > snapSeq replayed)
   - Level additions, updates, and removals on both sides
   - Best-quote stability: removing a non-top level does NOT change BestQuote; removing the top level advances to the next level
   - TopNDepth correctness: after a known multi-level book, TopNDepth(1), TopNDepth(2), TopNDepth(5), and total depth match computed expected values

3. `internal/orderbook/orderbook.go` gains `AllBids() map[string]string` and `AllAsks() map[string]string` read-only accessors (returns a **copy** of the internal map) so fixture tests can assert exact book state beyond BestQuote. These accessors are also used by feature-computation functions in later stories (6-3, 6-4).

4. `go test ./internal/orderbook/...` passes with zero failures and no data races (`-race` flag).

## Tasks / Subtasks

- [ ] Add `AllBids()` and `AllAsks()` accessors to `internal/orderbook/orderbook.go` (AC: 3)
  - [ ] Return `map[string]string` copies of `ob.bids` and `ob.asks`
  - [ ] Add a test in `orderbook_test.go` confirming the returned map is a copy (mutation does not affect internal state)

- [ ] Write fixture test `internal/orderbook/fixture_test.go` (AC: 1, 2, 4)
  - [ ] Define inline fixture: ≥ 20 ticks (bid adds, ask adds, bid update, ask update, bid remove, ask remove, trade ignored)
  - [ ] Replay cold-start sequence: ticks at seqs 1-10 before snapshot, snapshot at seq 5, assert only seq > 5 replayed
  - [ ] Assert exact AllBids()/AllAsks() state after replay
  - [ ] Assert BestQuote is unaffected by removing a non-top level
  - [ ] Assert BestQuote advances correctly after removing the top bid level
  - [ ] Assert TopNDepth(1, "bid"), TopNDepth(2, "bid"), TopNDepth(5, "bid"), TopNDepth(100, "bid") against computed expected values

- [ ] Run tests with race detector: `go test -race ./internal/orderbook/...` (AC: 4)

## Dev Notes

### Context
Epic 6 builds all OB-derived features. This story is the gate: if the OB has subtle correctness issues (e.g., wrong cold-start filter, incorrect BestQuote under multi-level updates), every feature in 6-2 through 6-4 will be silently wrong.

The existing `orderbook_test.go` has adequate unit tests for individual operations. This story adds **fixture validation** — a longer, realistic sequence that exercises the full state machine as it behaves in production, with assertions on exact book state.

### Existing OB API
```
orderbook.New(exchange, symbol string, coldBufSize int, emitGap GapEmitter) *OrderBook
ob.ApplyTick(t Tick)
ob.ApplySnapshot(s SnapshotEvent)
ob.ApplyGap(g GapMarker)
ob.BestQuote() (bestBidPrice, bestBidSize, bestAskPrice, bestAskSize string)
ob.TopNDepth(n int, side string) float64
```

### AllBids/AllAsks must return copies
The feature computation in 6-3/6-4 will iterate the returned map. It must not hold a reference to the internal map — a caller that modifies the return value must not corrupt OB state.

### Fixture design
The fixture should represent a realistic KuCoin BTC-USDT scenario:
- Prices around 50000 USDT
- Multiple price levels on each side (at least 5 per side)
- Mix of adds, updates (different size at same price), removals (size="0")
- At least one trade tick to confirm it is ignored by the OB
- Cold-start sequence: ticks with seq 1-10 arrive before snapshot at seq 5

Example structure (in-test, not a file):
```go
type tick struct{ seq int64; price, size, side string; level int }
type snapshot struct{ seq int64 }
```

### Depth expected values
After building a known book (e.g., 5 bid levels with sizes [1.0, 2.0, 3.0, 4.0, 5.0] at prices [50000, 49900, 49800, 49700, 49600]):
- TopNDepth(1, "bid") = 1.0 (best bid only: price 50000, size 1.0)
- TopNDepth(2, "bid") = 3.0 (50000 + 49900)
- TopNDepth(5, "bid") = 15.0 (sum all 5)
- TopNDepth(100, "bid") = 15.0 (capped at available levels)

### No new packages
All work is in `internal/orderbook/`. Zero new dependencies.

### Test naming
Follow existing convention: `TestFixture_<scenario>`. Use `t.Run(name, ...)` subtests for each assertion within the fixture.

## Dev Agent Record

### Completion Notes
<!-- agent fills in when done -->

### File List
<!-- agent fills in when done -->

### Change Log
<!-- agent fills in when done -->
