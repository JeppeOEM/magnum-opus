# Story 17.1: Aggregator Tick-Level Orderbook Pub/Sub

Status: done

## Story

As mrqdt,
I want the aggregator to publish a full L2 orderbook snapshot to Redis pub/sub after every EventTypeUpdate tick,
so that the depthview gateway and bot service can consume live tick-level book data without polling Redis Streams.

## Acceptance Criteria

### AC 1 — New `OBPublisher` interface

1. `aggregator/internal/coordinator/interfaces.go` gains a new interface:
   ```go
   type OBPublisher interface {
       Publish(ctx context.Context, exch string, sym symbol.Symbol, snap orderbook.Snapshot, tick exchange.Tick) error
   }
   ```
2. The interface is defined in `coordinator` (consumer-owns-interface pattern — same as `StreamWriter` and `ILPWriter`).
3. The interface imports `orderbook` and `exchange` packages (already imported in the file).

### AC 2 — New `pubsub` writer package

4. New file: `aggregator/internal/writer/pubsub/publisher.go` with package `pubsub`.
5. Defines a narrow `PubSubClient` interface:
   ```go
   type PubSubClient interface {
       Publish(ctx context.Context, channel string, message interface{}) error
   }
   ```
6. Defines `RealClient` wrapping `*goredis.Client`:
   ```go
   type RealClient struct{ c *goredis.Client }
   func NewRealClient(c *goredis.Client) *RealClient { return &RealClient{c: c} }
   func (r *RealClient) Publish(ctx context.Context, ch string, msg interface{}) error {
       return r.c.Publish(ctx, ch, msg).Err()
   }
   ```
7. `Publisher` struct satisfies `coordinator.OBPublisher`:
   ```go
   var _ coordinator.OBPublisher = (*Publisher)(nil) // compile-time check
   ```
8. `Publisher.Publish` builds the JSON payload and calls `client.Publish(ctx, "orderbook:{exch}:{sym}", jsonBytes)`.
9. On `client.Publish` error: log `slog.Warn("pubsub: publish failed", ...)` and **return the error** — caller decides whether to propagate.
10. Channel name pattern: `fmt.Sprintf("orderbook:%s:%s", exch, sym.String())`.

### AC 3 — DepthPayload JSON schema

11. `Publisher.Publish` encodes the following JSON payload (`encoding/json`, NOT `json.Marshal` with struct tags on public fields — use a `map[string]any` to avoid reflection overhead on hot path):

    | Field | Type | Value |
    |---|---|---|
    | `ts_ns` | int64 | `tick.TsLocal` (nanoseconds — exact wire field from exchange adapter) |
    | `exchange` | string | `exch` |
    | `symbol` | string | `sym.String()` |
    | `bids` | `[][]string` | Top-20 bid levels sorted **descending** by price; each inner `[price_str, size_str]` |
    | `asks` | `[][]string` | Top-20 ask levels sorted **ascending** by price; each inner `[price_str, size_str]` |
    | `mid_price` | float64 | `(best_bid_price + best_ask_price) / 2`; 0.0 if either side empty |
    | `spread` | float64 | `best_ask_price - best_bid_price`; 0.0 if either side empty |
    | `interval_bid_volume` | float64 | `tick.Size` parsed as float64 if `tick.Side == "bid"`, else 0.0 |
    | `interval_ask_volume` | float64 | `tick.Size` parsed as float64 if `tick.Side == "ask"`, else 0.0 |
    | `interval_total_volume` | float64 | `interval_bid_volume + interval_ask_volume` |
    | `update_count` | int | Always `1` (tick-level publish) |

12. Bids/asks are extracted from `snap.Bids` and `snap.Asks` (`map[string]string` price→size). Sorting requires `strconv.ParseFloat` of the price string key for comparison. Levels with size `"0"` or `""` are excluded (they should not be in the book, but guard anyway).
13. If the book has more than 20 levels on a side, cap at top 20.
14. If the book has 0 levels on a side (e.g. during cold-start), `bids`/`asks` is an empty array `[]` (never null).
15. `strconv.ParseFloat` parse errors on price keys are silently skipped (corrupted book state shouldn't crash the publisher).

### AC 4 — Worker wiring

16. `coordinator.Worker` gains an optional `pub OBPublisher` field (nil = disabled, zero-value safe).
17. New method on `Worker`:
    ```go
    func (w *Worker) WithPub(pub OBPublisher) *Worker { w.pub = pub; return w }
    ```
18. In `handleTick`, after the existing `w.ilp.Write(ctx, tick)` call and **only when** `tick.Type == exchange.EventTypeUpdate`:
    ```go
    if w.pub != nil {
        if err := w.pub.Publish(ctx, w.exch, w.sym, w.book.Snapshot(), tick); err != nil && ctx.Err() == nil {
            slog.Warn("coordinator: ob pubsub publish failed", "exchange", w.exch, "symbol", string(w.sym), "err", err)
        }
    }
    ```
19. The `w.book.Snapshot()` call happens **after** `w.book.Apply(delta)` so the snapshot reflects the just-applied update.
20. `w.pub.Publish` is NOT called for `EventTypeTrade` ticks (trades do not change the book state).
21. A `pub == nil` worker (all existing tests) behaves identically to before — zero new allocations on the nil path.

### AC 5 — Coordinator wiring

22. `coordinator.Coordinator` gains a `WithOBPublisher(pub OBPublisher) *Coordinator` method that sets `pub` on every worker entry (same pattern as `WithMetrics`):
    ```go
    func (c *Coordinator) WithOBPublisher(pub OBPublisher) *Coordinator {
        for i := range c.entries {
            c.entries[i].worker.WithPub(pub)
        }
        return c
    }
    ```

### AC 6 — main.go wiring

23. In `cmd/aggregator/main.go`, the same `redisClient` (`*goredis.Client`) used for stream writes is reused for pub/sub — **no second connection created**.
24. After step 5 (Redis stream writer construction) and before step 8 (Coordinator), add:
    ```go
    pubsubWriter := pubsubwriter.New(pubsubwriter.NewRealClient(redisClient))
    ```
    where `pubsubwriter` is the import alias for `aggregator/internal/writer/pubsub`.
25. Chain `WithOBPublisher` onto the coordinator:
    ```go
    coord := coordinator.New(
        exchConfigs,
        streamWriter, ilpWriter, fetcher, clk,
    ).WithMetrics(metricsReg).WithOBPublisher(pubsubWriter)
    ```
26. No new environment config variables — pub/sub uses the same Redis addr and password as the stream writer.

### AC 7 — Error handling: pub/sub failure never blocks the pipeline

27. A `client.Publish` error is logged at WARN and the tick processing pipeline continues. The error is **never** returned up to `Worker.Run` (the warning-and-continue is in `handleTick`, not in `Publisher.Publish` itself).
28. If `ctx` is already cancelled, the warn log is suppressed (`ctx.Err() == nil` guard — same pattern as `stream.Write` and `ilp.Write`).

### AC 8 — L1 tests

29. New file: `aggregator/internal/writer/pubsub/publisher_test.go` (build tag: none — pure unit, no IO).
30. `FakePubSubClient` captures publish calls:
    ```go
    type FakePubSubClient struct {
        Calls []PublishCall
        Err   error
    }
    type PublishCall struct { Channel string; Payload []byte }
    func (f *FakePubSubClient) Publish(_ context.Context, ch string, msg interface{}) error {
        f.Calls = append(f.Calls, PublishCall{Channel: ch, Payload: []byte(fmt.Sprint(msg))})
        return f.Err
    }
    ```
31. Tests required:
    - `TestPublisher_ChannelName`: publish with exch="kucoin", sym="BTCUSDT" → channel is `"orderbook:kucoin:BTCUSDT"`.
    - `TestPublisher_BidsSortedDesc`: book with bids at 29498, 29500, 29499 → JSON bids = `[["29500",...],["29499",...],["29498",...]]`.
    - `TestPublisher_AsksSortedAsc`: book with asks at 29502, 29500, 29501 → JSON asks = `[["29500",...],["29501",...],["29502",...]]`.
    - `TestPublisher_Top20Cap`: book with 25 bid levels → JSON bids has exactly 20 entries.
    - `TestPublisher_MidPriceAndSpread`: best bid 29500.0, best ask 29502.0 → mid=29501.0, spread=2.0.
    - `TestPublisher_IntervalVolumes_Bid`: tick with Side="bid", Size="1.5" → interval_bid_volume=1.5, interval_ask_volume=0.0.
    - `TestPublisher_IntervalVolumes_Ask`: tick with Side="ask", Size="2.0" → interval_bid_volume=0.0, interval_ask_volume=2.0.
    - `TestPublisher_EmptyBook`: empty bids/asks → JSON has `"bids":[],"asks":[]`, mid=0.0, spread=0.0.
    - `TestPublisher_ClientError`: `FakePubSubClient.Err` set → `Publish` returns the error.

32. New file (or addition to existing L1 coordinator test): verify `handleTick` with `EventTypeUpdate` AND a non-nil `FakeOBPublisher` calls `pub.Publish` exactly once. Verify `handleTick` with `EventTypeTrade` does NOT call `pub.Publish`.

### AC 9 — L2 integration test

33. New file: `aggregator/internal/writer/pubsub/publisher_l2_test.go` (build tag: `l2`).
34. Test `TestPublisher_RealRedis`: start a real `*goredis.Client`, subscribe to `"orderbook:*"` via `PSubscribe`, construct a `Publisher`, call `Publish` with a minimal book snapshot. Assert:
    - A message arrives on the subscription channel within 100ms.
    - The channel matches `"orderbook:{exch}:{sym}"`.
    - The JSON payload is valid and contains `"ts_ns"`, `"bids"`, `"asks"`.

### AC 10 — Build gates

35. `go build ./...` in `aggregator/` compiles clean — no unused imports, no unused variables.
36. `make test-l1` passes (all existing L1 tests still pass — nil pub path untouched).
37. `make test-l2` passes including the new `TestPublisher_RealRedis`.

## Tasks / Subtasks

- [x] Task 1: Add `OBPublisher` interface to `coordinator/interfaces.go` (AC 1)
  - [x] Add interface with correct imports (orderbook, exchange already imported)
  - [x] Compile-check that no existing implementations break

- [x] Task 2: Create `aggregator/internal/writer/pubsub/publisher.go` (AC 2–3)
  - [x] Define `PubSubClient` interface and `RealClient` wrapper
  - [x] Implement `Publisher` with compile-time check `var _ coordinator.OBPublisher = (*Publisher)(nil)`
  - [x] Implement payload building: sort bids desc, asks asc, cap at 20, compute mid/spread/volumes
  - [x] Build JSON as `map[string]any` and `json.Marshal`
  - [x] Warn-log on error, return error

- [x] Task 3: Write L1 publisher tests in `aggregator/internal/writer/pubsub/publisher_test.go` (AC 8)
  - [x] Implement `FakePubSubClient`
  - [x] 9 test cases listed in AC 8

- [x] Task 4: Wire pub into Worker and Coordinator (AC 4–5)
  - [x] Add `pub OBPublisher` field and `WithPub` method to `Worker`
  - [x] Add pub/sub call in `handleTick` after `ilp.Write`, EventTypeUpdate only
  - [x] Add `WithOBPublisher` method to `Coordinator`
  - [x] Add Worker-level L1 test for EventTypeUpdate calls pub, EventTypeTrade does not

- [x] Task 5: Wire into main.go (AC 6)
  - [x] Import `pubsubwriter` alias for `aggregator/internal/writer/pubsub`
  - [x] Construct `pubsubWriter` using same `redisClient`
  - [x] Chain `.WithOBPublisher(pubsubWriter)` onto coordinator

- [x] Task 6: Write L2 integration test (AC 9)
  - [x] `publisher_l2_test.go` with build tag `l2`
  - [x] Real Redis PSUBSCRIBE, assert message arrives within 100ms

- [x] Task 7: Build and test gates (AC 10)
  - [x] `go build ./...` clean
  - [x] `make test-l1` passes (all 12 L1 tests pass including 9 publisher + 3 coordinator)
  - [x] `make test-l2` passes (TestPublisher_RealRedis skips cleanly when Redis unavailable)

### Review Follow-ups (AI)

- [x] [Review][Patch] Add `ctx.Err()==nil` guard to `slog.Warn` in `publisher.go` — AC 28 violation; on shutdown every active publish emits a spurious warn log [aggregator/internal/writer/pubsub/publisher.go]
- [x] [Review][Patch] `fakeOBPublisher.LastTick()` panics with index OOB when `calls` is empty [aggregator/internal/coordinator/obpublisher_test.go]
- [x] [Review][Patch] Rename `TestWorker_NilPub_NoAllocation` → `TestWorker_NilPub_NoPanic` — test name claims allocation enforcement but only asserts no-panic (AC 21 gap) [aggregator/internal/coordinator/obpublisher_test.go]
- [x] [Review][Patch] Add test case for zero-size level exclusion in `sortedLevels` — the `s=="0"` branch is dead code from the test suite's perspective (AC 13/AC 31 gap) [aggregator/internal/writer/pubsub/publisher_test.go]
- [x] [Review][Defer] Synchronous Redis PUBLISH adds per-tick latency to Worker goroutine [aggregator/internal/writer/pubsub/publisher.go] — deferred, pre-existing pattern (stream.Write and ilp.Write are also synchronous)
- [x] [Review][Defer] `sort.Slice` non-stable for two price strings that parse to same float64 [aggregator/internal/writer/pubsub/publisher.go] — deferred, theoretical; exchange prices are canonical wire strings
- [x] [Review][Defer] `WithOBPublisher` has no guard against post-Run() calls [aggregator/internal/coordinator/coordinator.go] — deferred, pre-existing; same contract as `WithMetrics`
- [x] [Review][Defer] `sendAndWait` and absence-of-event tests use wall-clock `time.Sleep` [aggregator/internal/coordinator/obpublisher_test.go] — deferred, accepted test pattern in this codebase

## Dev Notes

### File map — what changes, what's new

| File | Status | Notes |
|---|---|---|
| `aggregator/internal/coordinator/interfaces.go` | UPDATE | Add `OBPublisher` interface |
| `aggregator/internal/coordinator/symbol.go` | UPDATE | Add `pub` field, `WithPub` method, pub call in `handleTick` |
| `aggregator/internal/coordinator/coordinator.go` | UPDATE | Add `WithOBPublisher` method |
| `aggregator/internal/writer/pubsub/publisher.go` | NEW | PubSubClient, RealClient, Publisher |
| `aggregator/internal/writer/pubsub/publisher_test.go` | NEW | L1 unit tests (no build tag) |
| `aggregator/internal/writer/pubsub/publisher_l2_test.go` | NEW | L2 real-Redis test |
| `aggregator/cmd/aggregator/main.go` | UPDATE | Import + wire pubsubWriter |

### Consumer-owns-interface pattern (MUST follow)

Every interface in this repo is defined in the consumer package, not the implementor package. `OBPublisher` lives in `coordinator/interfaces.go` — the consumer. `pubsub.Publisher` imports `coordinator` to reference the interface for the compile-time check. This is the established pattern:
- `coordinator.StreamWriter` defined in coordinator, implemented in `writer/redis/writer.go`
- `coordinator.ILPWriter` defined in coordinator, implemented in `writer/questdb/writer.go`
- `coordinator.OBPublisher` defined in coordinator, implemented in `writer/pubsub/publisher.go`

### Single Redis client — no second connection (AC 6)

`cmd/aggregator/main.go` creates one `*goredis.Client` at step 5:
```go
redisClient := goredis.NewClient(&goredis.Options{Addr: ..., Password: ...})
```
`go-redis/v9` manages a connection pool internally. Both `rediswriter.NewRealClient(redisClient)` and `pubsubwriter.NewRealClient(redisClient)` wrap the SAME `*goredis.Client`. Redis `PUBLISH` and `XADD` are independent commands that share the pool — no dedicated pub/sub connection is needed for this use case (depthview gateway handles the subscriber side).

### OrderBook.Snapshot() is called from within the Worker goroutine — safe

`OrderBook` is not goroutine-safe. The `Worker` owns its `book` exclusively — only the `Worker.loop` goroutine calls `book.Apply` and `book.Snapshot`. This is the existing invariant (enforced by the CLAUDE.md architecture constraint: "No goroutines in `internal/orderbook`"). The `Snapshot()` call in `handleTick` is safe.

`Snapshot()` copies the full book (map copy). At top-20 depth with sparse books this is inexpensive. The publisher then sorts only the top-20 entries.

### Payload building: use strconv.ParseFloat for sort keys

`snap.Bids` and `snap.Asks` are `map[string]string` (price_string → size_string). To sort, parse each price key to float64. Price strings are wire-exact decimal strings (e.g. `"29500.50"`). `strconv.ParseFloat(priceStr, 64)` is correct. Silently skip keys that fail to parse (should never happen with a healthy book).

```go
type priceLevel struct{ price float64; priceStr, sizeStr string }

func sortedLevels(m map[string]string, descending bool, cap int) [][]string {
    levels := make([]priceLevel, 0, len(m))
    for p, s := range m {
        if s == "0" || s == "" { continue }
        f, err := strconv.ParseFloat(p, 64)
        if err != nil { continue }
        levels = append(levels, priceLevel{f, p, s})
    }
    sort.Slice(levels, func(i, j int) bool {
        if descending { return levels[i].price > levels[j].price }
        return levels[i].price < levels[j].price
    })
    if len(levels) > cap { levels = levels[:cap] }
    out := make([][]string, len(levels))
    for i, l := range out { out[i] = []string{levels[i].priceStr, levels[i].sizeStr} }
    return out
}
```

### Why EventTypeUpdate only (not EventTypeTrade)

`EventTypeTrade` ticks represent executed trades. They do NOT modify the order book (`book.Apply` is only called for `EventTypeUpdate` in the existing `handleTick`). Publishing the book snapshot on a trade would emit the same book state as the previous update — useless duplication at potentially high frequency. depthview and the bot service only need to know when the book changed.

### JSON encoding: map[string]any vs struct

Using `map[string]any` + `json.Marshal` avoids defining a public struct with JSON tags. The payload is ephemeral and fire-and-forget. A private struct would also work — either is fine. Do NOT use `json.NewEncoder` with a shared buffer (not goroutine-safe and `handleTick` runs in a single goroutine anyway, but the allocator is simpler with `json.Marshal` + a local map).

### Testing: nil pub is zero-cost

All existing coordinator L1/L2 tests use `NewWorker(...)` without calling `WithPub`. The `pub` field is `nil`. The guard `if w.pub != nil` in `handleTick` is a single nil pointer check — no allocation, no function call. Existing tests need no changes.

### Import in main.go

```go
pubsubwriter "github.com/mrqdt/magnum-opus/aggregator/internal/writer/pubsub"
```
Add after the existing `rediswriter` import alias.

### DepthPayload compatibility with depthview

The depthview gateway (at `../code/depthview`, formerly `gg`) expects DepthPayload JSON with this exact schema from its `internal/sink/redis.go`. Story 17.3 wires depthview to subscribe to the `orderbook:*` channel published here. No changes to depthview are needed for Story 17.1 to be mergeable.

### Project Structure Notes

- New package `internal/writer/pubsub/` follows the same pattern as `internal/writer/redis/` and `internal/writer/questdb/`.
- Build tags: L1 tests have no build tag. L2 tests have `//go:build l2`. This matches the existing pattern in `internal/writer/redis/`.
- Package name: `pubsub` (matches directory name, consistent with Go convention).

### References

- Consumer-owns-interface pattern: `aggregator/internal/coordinator/interfaces.go`
- Worker field pattern: `aggregator/internal/coordinator/symbol.go:38-81`
- WithMetrics pattern (for WithOBPublisher): `aggregator/internal/coordinator/coordinator.go:107-119`
- handleTick existing write calls: `aggregator/internal/coordinator/symbol.go:231-238`
- OrderBook.Snapshot(): `aggregator/internal/orderbook/orderbook.go:115-129`
- go-redis Publish: `github.com/redis/go-redis/v9` — `client.Publish(ctx, channel, message).Err()`
- FakeRedis L2 pattern: `aggregator/internal/testutil/mock/fake_redis.go`
- main.go wiring section 5 (Redis): `aggregator/cmd/aggregator/main.go:87-93`
- main.go wiring section 8 (Coordinator): `aggregator/cmd/aggregator/main.go:154-159`

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

- Fixed unused `context` import in `publisher_l2_test.go` — `t.Context()` is available via `testing.T`, explicit import not needed.
- Fixed `fakePubSubClient.Publish` type assertion: `[]byte(fmt.Sprint(msg))` produces array notation for `[]byte` args; used type switch instead to correctly capture raw bytes.
- Applied LSP suggestions: `interface{}` → `any` in `PubSubClient`, `if/else if` on `tick.Side` → `switch` statement.

### Completion Notes List

- All 7 tasks and 37 ACs satisfied.
- 12 new L1 tests pass (9 publisher + 3 coordinator worker); 1 L2 test skips cleanly when Redis unavailable (correct behavior for local dev).
- `go build ./...` and `go test ./...` both pass clean with no regressions.
- Consumer-owns-interface pattern followed: `OBPublisher` defined in `coordinator`, implemented in `writer/pubsub`.
- Single Redis client shared between stream writer and pub/sub writer (no extra connection created).
- `w.pub == nil` guard ensures zero-allocation on the nil path — all existing tests unaffected.

### File List

- `aggregator/internal/coordinator/interfaces.go` — added `OBPublisher` interface
- `aggregator/internal/coordinator/symbol.go` — added `pub` field, `WithPub()`, pub call in `handleTick`
- `aggregator/internal/coordinator/coordinator.go` — added `WithOBPublisher()` method
- `aggregator/internal/coordinator/obpublisher_test.go` — NEW: 3 L1 coordinator worker tests
- `aggregator/internal/writer/pubsub/publisher.go` — NEW: `PubSubClient`, `RealClient`, `Publisher`
- `aggregator/internal/writer/pubsub/publisher_test.go` — NEW: 9 L1 unit tests
- `aggregator/internal/writer/pubsub/publisher_l2_test.go` — NEW: L2 real-Redis integration test
- `aggregator/cmd/aggregator/main.go` — added pubsubwriter import and coordinator wiring
