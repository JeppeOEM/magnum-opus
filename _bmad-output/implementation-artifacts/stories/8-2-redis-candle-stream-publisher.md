# Story 8.2: Redis Candle Stream Publisher

Status: done

## Story

As the candle service,
I want to publish in-progress and completed higher-timeframe OHLCV bars to Redis Streams at ≤250ms cadence (partial) and on bar close (complete),
so that bots can subscribe to `candles:{exchange}:{symbol}:{tf}` for live partial updates and `candles:close:{exchange}:{symbol}:{tf}` for bar-completion events without polling QuestDB.

## Acceptance Criteria

### AC 1 — `internal/writer/redis/` package

1. A new package lives at `candle-service/internal/writer/redis/` with package name `redis`.
2. Exports `Publisher` struct with constructor:
   ```go
   func New(rdb *goredis.Client, exchange, symbol string, maxLen, closeMaxLen int64) *Publisher
   ```
   where `goredis` is the aliased import for `github.com/redis/go-redis/v9` inside this package.
3. `Publisher.SetFailureCounter(fn func())` injects a callback for `candle_redis_publish_failure_total` increments.
4. Zero IO beyond `*goredis.Client` calls. No `time.Now()`, no goroutines, no `init()`.

### AC 2 — Stream field serialization

5. A `cascade.Bar` is serialized to `map[string]any` with exactly these keys and values:

   | Key | Value |
   |-----|-------|
   | `ts` | `strconv.FormatInt(bar.OpenTs, 10)` — Unix ms of bar open |
   | `exchange` | `bar.Exchange` |
   | `symbol` | `bar.Symbol` |
   | `tf` | `string(bar.TF)` — e.g. `"1m"`, `"5m"` |
   | `open` | `""` when nil; `strconv.FormatFloat(*bar.Open, 'f', -1, 64)` when set |
   | `high` | same nil convention |
   | `low` | same nil convention |
   | `close` | same nil convention |
   | `volume` | `strconv.FormatFloat(bar.Volume, 'f', -1, 64)` |
   | `quote_vol` | `strconv.FormatFloat(bar.QuoteVol, 'f', -1, 64)` |
   | `trade_count` | `strconv.Itoa(bar.TradeCount)` |
   | `bar_count` | `strconv.Itoa(bar.BarCount)` |
   | `gap_count` | `strconv.Itoa(bar.GapCount)` |
   | `is_complete` | `"true"` or `"false"` |

6. `XAddArgs.Approx = true` — approximate MAXLEN for efficiency (`~` operator); `XAddArgs.MaxLen = maxLen` or `closeMaxLen` as appropriate.

### AC 3 — Closed bar publishing

7. `func (p *Publisher) PublishBars(ctx context.Context, bars []cascade.Bar)` — for each bar in `bars` (already-closed cascade bars from `engine.Fold()`):
   - XADD to `candles:{exchange}:{symbol}:{tf}` with MAXLEN `p.maxLen`, fields from AC 2 with `is_complete: "true"`.
   - XADD to `candles:close:{exchange}:{symbol}:{tf}` with MAXLEN `p.closeMaxLen`, same fields.
   - Each XADD is a separate call. On failure: log ERROR with exchange/symbol/tf/error; call `failureCounter` if set; **continue** — never block or return early.
8. **No weekly alias.** The key `candles:1w:{exchange}:{symbol}` (alias format) is never written. Only `candles:{exchange}:{symbol}:1w` and `candles:close:{exchange}:{symbol}:1w` are valid.

### AC 4 — Partial bar publishing

9. `func (p *Publisher) PublishPartialBar(ctx context.Context, bar cascade.Bar)` — publishes a bar with `is_complete: "false"`:
   - XADD to `candles:{exchange}:{symbol}:{tf}` with MAXLEN `p.maxLen` only.
   - **Never** writes to `candles:close:` stream.
10. If `bar.OpenTs == 0` (accumulator not yet seeded — no data since last reset), silently skip — do not call XADD.

### AC 5 — Consumer: `PartialPublishSig` and `PartialPublisher` interfaces

11. `consumer` package exports:
    ```go
    type PartialPublishSig struct{}

    // PartialPublisher is implemented by the accWriter wrapper in cmd/candle.
    // Called from the consumer goroutine — safe to call cascade.Engine.CurrentBar().
    type PartialPublisher interface {
        PublishCascadePartial(ctx context.Context)
    }
    ```
12. `Consumer` gains a builder method (does NOT change `New()` signature):
    ```go
    func (c *Consumer) WithPartialPublish(ch <-chan PartialPublishSig, publisher PartialPublisher) *Consumer
    ```
    Stores `ch` as `c.partialPublish` and `publisher` as `c.partial`. Returns `c` for chaining.
13. `Consumer` gains an unexported field `hasNewTicks bool`. Set to `true` whenever `EventTick` is processed in `handleMessage`. Reset to `false` after each `PublishCascadePartial` call.
14. In `Consumer.Run()` main select loop, add case:
    ```go
    case <-c.partialPublish:
        if c.hasNewTicks && c.partial != nil {
            c.partial.PublishCascadePartial(ctx)
            c.hasNewTicks = false
        }
    ```
    If `c.partialPublish` is nil, the case is never selected (a nil channel blocks forever).

### AC 6 — XReadGroup block duration

15. Consumer's `XReadGroup` block timeout is changed from `time.Second` to `250*time.Millisecond`. This is an internal implementation constant — not configurable via consumer API. Rationale: the 1s block was a historical default; 250ms enables sub-second responsiveness to both `BarClose` and `PartialPublish` signals without measurable Redis overhead.

### AC 7 — Config addition

16. `internal/config/Config` gains:
    ```go
    CandlePartialPublishMs int // CANDLE_PARTIAL_PUBLISH_MS, default 250
    ```
    Added via `getEnvInt("CANDLE_PARTIAL_PUBLISH_MS", 250)` in `Load()`.

### AC 8 — `accWriter` wired as `PartialPublisher`

17. `accWriter` in `cmd/candle/main.go` gains `publisher *rediswriter.Publisher` field.
18. `accWriter` implements `consumer.PartialPublisher`:
    ```go
    func (aw *accWriter) PublishCascadePartial(ctx context.Context) {
        for _, tf := range cascade.AllTFs {
            bar := aw.engine.CurrentBar(tf)
            if bar.OpenTs == 0 {
                continue
            }
            aw.publisher.PublishPartialBar(ctx, bar)
        }
    }
    ```
19. `accWriter.Flush()` updated: in the `!isPartial` block, after `persistCascadeHashes(closedBars)`:
    ```go
    if aw.publisher != nil {
        aw.publisher.PublishBars(aw.ctx, closedBars)
    }
    ```
    `aw.acc.BarReset()` follows (unchanged). `ob_features` publishing (step 5 per project-context) is story 8-3 — do not add it here.

### AC 9 — 250ms ticker goroutine in main.go

20. Each `symbolEntry` gains `partialPublish chan consumer.PartialPublishSig` (capacity 1, same pattern as `barClose`).
21. After Phase 3 (consumer goroutines started), a second ticker goroutine fires every `cfg.CandlePartialPublishMs` milliseconds:
    ```go
    go func() {
        ticker := time.NewTicker(time.Duration(cfg.CandlePartialPublishMs) * time.Millisecond)
        defer ticker.Stop()
        for {
            select {
            case <-ctx.Done():
                return
            case <-ticker.C:
                for _, e := range entries {
                    select {
                    case e.partialPublish <- consumer.PartialPublishSig{}:
                    default: // drop — consumer is busy; no counter needed
                    }
                }
            }
        }
    }()
    ```
22. `consumer.New()` call in Phase 1 updated to chain `WithPartialPublish`:
    ```go
    c := consumer.New(..., barClose, logger).WithPartialPublish(partialPublish, aw)
    ```
    where `partialPublish` is the per-symbol channel from the symbolEntry being built.
23. Publisher is created per symbol in Phase 1:
    ```go
    pub := rediswriter.New(rdb, p.exchange, p.symbol,
        int64(cfg.CandleStreamMaxLen), int64(cfg.CandleCloseStreamMaxLen))
    pub.SetFailureCounter(func() {
        m.RedisPublishFailureTotal.WithLabelValues(exchange, symbol).Inc()
    })
    ```
    `exchange` and `symbol` must be captured from `p.exchange`/`p.symbol` before the loop variable changes (same pattern as cascadeFailureCounter).

### AC 10 — L2 tests for `internal/writer/redis/`

24. `internal/writer/redis/publisher_l2_test.go` (build tag `l2`, package `redis_test`) covers using `miniredis`:
    - `TestPublishBars_ClosedBar`: after `PublishBars` with one closed bar, `XLEN candles:kucoin:BTC-USDT:1m` == 1, `XLEN candles:close:kucoin:BTC-USDT:1m` == 1; `is_complete` field == `"true"` in both.
    - `TestPublishBars_NilOHLC`: a bar with nil Open/High/Low/Close writes `""` not `"<nil>"` or `"0"`.
    - `TestPublishPartialBar_OnlyPartialStream`: after `PublishPartialBar`, `candles:close:` stream does NOT exist; `candles:` stream has 1 entry with `is_complete: "false"`.
    - `TestPublishPartialBar_ZeroOpenTs`: bar with `OpenTs == 0` → no XADD called (streams remain empty).
    - `TestPublishBars_MAXLEN`: publish 3 bars with `maxLen=2`; miniredis XLEN ≤ 2+1 (approximate trim) or exactly 2 if miniredis applies strict MAXLEN.
    - `TestPublishBars_FailureCounter`: stop miniredis server, call `PublishBars`, verify failureCounter called ≥ 1 time.

### AC 11 — Build and test gates

25. `go build ./...` compiles clean, no unused imports or variables.
26. `make test-l1` passes (no new L1 tests — writer/redis has IO dependencies).
27. `make test-l2` passes with all new L2 tests.

---

## Dev Notes

### Package structure

```
internal/writer/redis/
  publisher.go           # Publisher struct, New(), SetFailureCounter(), PublishBars(), PublishPartialBar()
  publisher_l2_test.go   # L2 tests (build tag l2)
```

Inside `publisher.go`, the package name is `redis`. In `main.go`, import it as:
```go
rediswriter "github.com/mrqdt/magnum-opus/candle-service/internal/writer/redis"
```

Inside `publisher.go`, import go-redis with alias to avoid shadowing the package name:
```go
import (
    goredis "github.com/redis/go-redis/v9"
)
```

### No import cycles

`writer/redis/` imports `cascade/` (a leaf node — zero internal imports beyond accumulator). This is valid. Do NOT import `consumer/`, `accumulator/`, `metrics/`, or `health/` from `writer/redis/`.

Dependency direction: `writer/redis/` ← imported by `cmd/candle/main.go` only.

### XAddArgs exact form

```go
_, err := p.rdb.XAdd(ctx, &goredis.XAddArgs{
    Stream: "candles:" + p.exchange + ":" + p.symbol + ":" + string(bar.TF),
    MaxLen: p.maxLen,
    Approx: true,
    Values: barFields(bar, true), // true = isComplete
}).Result()
```

### Consumer.Run() select pattern — nil channel safety

A nil `<-chan` blocks forever. If `WithPartialPublish()` is never called, `c.partialPublish` remains nil and `case <-c.partialPublish:` is safely skipped. Existing L2 tests that don't call `WithPartialPublish()` continue to work unchanged.

The four cases in the select loop order (matching current code pattern):
```go
for {
    select {
    case <-ctx.Done():
        return ctx.Err()
    case <-c.barClose:
        c.handleBarClose(ctx)
        c.seenIDs = make(map[string]struct{})
    case <-c.partialPublish:
        if c.hasNewTicks && c.partial != nil {
            c.partial.PublishCascadePartial(ctx)
            c.hasNewTicks = false
        }
    default:
    }
    // XReadGroup follows...
}
```

### XReadGroup block timeout change (250ms)

Current `consumer.go` line:
```go
Block: time.Second,
```
Change to:
```go
Block: 250 * time.Millisecond,
```
This increases Redis round-trip frequency during quiet periods from ~1/s to ~4/s per symbol. At the scale of this service (few symbols), this is negligible. The existing consumer L2 tests use `Block: X` via the Consumer struct — since this is an internal constant, tests are unaffected.

### Flush sequence (from project-context.md) — story 8-2 adds step 4

Current (after 8-1):
```
(1) acc.SetCloseDepth()
(2) bar := acc.CurrentBar(tsSecMs, false)
(3) w.WriteBar(ctx, bar)           ← QuestDB
(4) [empty — 8-2 fills this]
(5) [empty — 8-3 fills this]
(6) acc.BarReset()
```

After 8-2:
```
(1) acc.SetCloseDepth()
(2) bar := acc.CurrentBar(tsSecMs, false)
(3) w.WriteBar(ctx, bar)           ← QuestDB
    engine.Fold(bar, tsSecMs)      ← cascade fold (from 8-1)
    persistCascadeHashes(...)      ← HASH write (from 8-1)
(4) publisher.PublishBars(ctx, closedBars) ← candles: + candles:close: (NEW 8-2)
(5) [ob_features — story 8-3]
(6) acc.BarReset()
```

**Do not add ob_features publishing in this story.**

### Partial publish idle-symbol semantics

`hasNewTicks` tracks whether any `EventTick` was processed since the last `PartialPublishSig`. It is:
- Set to `true` in `handleMessage` when `parsed.Type == EventTick`
- Reset to `false` after `PublishCascadePartial(ctx)` is called
- NOT reset on `EventGap` or `EventSnapshot` (those don't generate cascade data)

During quiet periods (no new ticks in 250ms window), `hasNewTicks` remains `false` and `PublishCascadePartial` is NOT called — no stale message is published to the candle streams.

During active periods, even if the cascade state hasn't changed (multiple 250ms windows within one 1s bar), the partial publish fires on each 250ms window that has new ticks — giving downstream subscribers a signal that the market is still active.

### Why publish at 250ms granularity if cascade only changes at 1s?

Partial cascade bars reflect the accumulated OHLCV from the start of the current TF window. The values DON'T change within a second. However, publishing at 250ms cadence (when ticks arrive) tells downstream bots: "this symbol has active ticks AND here is the latest cascade bar state." The `is_complete: "false"` message acts as both a liveness signal and a state refresh.

### Failure counter callback closure

```go
exchange, symbol := p.exchange, p.symbol  // capture before loop increment
pub.SetFailureCounter(func() {
    m.RedisPublishFailureTotal.WithLabelValues(exchange, symbol).Inc()
})
```

This is the same pattern as `cascadeFailureCounter` in 8-1. Must capture `exchange` and `symbol` as local variables, not use `p.exchange`/`p.symbol` directly in the closure (loop variable capture bug).

### Consumer.New() caller in main.go — update to WithPartialPublish

Current Phase 1 call:
```go
c := consumer.New(rdb, cfg.ConsumerGroup,
    cfg.Slot+"-"+p.exchange+"-"+p.symbol,
    p.exchange, p.symbol,
    obAdapter, aw, barClose, logger)
```

Updated:
```go
partialPublish := make(chan consumer.PartialPublishSig, 1)
c := consumer.New(rdb, cfg.ConsumerGroup,
    cfg.Slot+"-"+p.exchange+"-"+p.symbol,
    p.exchange, p.symbol,
    obAdapter, aw, barClose, logger).
    WithPartialPublish(partialPublish, aw)
```

Then add `partialPublish` to the `symbolEntry` struct and store it:
```go
e := &symbolEntry{
    // ... existing fields ...
    partialPublish: partialPublish,
}
```

### symbolEntry struct update

Add to `symbolEntry`:
```go
partialPublish chan consumer.PartialPublishSig
```

The 250ms ticker goroutine fans out to `e.partialPublish` for all entries (same fan-out pattern as `barClose`).

### miniredis import in L2 tests

The existing L2 test for consumer already imports `github.com/alicebob/miniredis/v2`. The `publisher_l2_test.go` should use the same library. Check `go.mod` that this dependency is already declared before using it — do NOT add a new dependency without confirming.

Pattern for miniredis setup:
```go
import (
    "github.com/alicebob/miniredis/v2"
    goredis "github.com/redis/go-redis/v9"
)

func setupMiniredis(t *testing.T) *goredis.Client {
    t.Helper()
    mr := miniredis.RunT(t)
    rdb := goredis.NewClient(&goredis.Options{Addr: mr.Addr()})
    t.Cleanup(func() { rdb.Close() })
    return rdb
}
```

For the `TestPublishBars_FailureCounter` test: use `mr.Close()` to shut down the server after publisher creation but before calling `PublishBars`, then assert failureCounter was called.

### Stream key format examples

```
candles:kucoin:BTC-USDT:1m     ← partial+close stream
candles:close:kucoin:BTC-USDT:1m  ← close-only stream
candles:bybit:BTCUSDT:4h
candles:close:bybit:BTCUSDT:1w
```

Never: `candles:1w:kucoin:BTC-USDT` (weekly alias — forbidden).

### Existing consumer L2 tests — no change required

`consumer_l2_test.go` uses `consumer.New(...)` with 9 positional params and uses `stubAcc` which does NOT implement `PartialPublisher`. Since `WithPartialPublish()` is a builder method (not part of `New()`), existing tests compile unchanged. The `PartialPublisher` case in the select loop is never triggered when `partialPublish` is nil.

### config_test.go — add CandlePartialPublishMs assertion

`internal/config/config_test.go` should have a test asserting `CandlePartialPublishMs` defaults to 250 when env var is unset. Check the existing test pattern in that file and add accordingly.

### Backoff for XADD failures

No backoff on XADD failures — the operation is fire-and-forget per project-context.md: "If any fails after backoff retries, log ERROR, increment candle_redis_publish_failure_total, and continue." The "after backoff retries" applies to the cascade HASH writes (story 8-1) but NOT to candle stream XADD. XADD failures are single-attempt — log ERROR, count, continue. The bar-close flow must not be delayed by Redis stream write failures.

### L1 ban: no time.Now() in writer/redis/

`writer/redis/publisher.go` must not call `time.Now()` — it doesn't need to; timestamps come from `bar.OpenTs` (already set by the cascade engine from the 1s bar's tsSecMs).

---

## Tasks / Subtasks

- [x] Create `internal/writer/redis/publisher.go` with `Publisher` struct, `New()`, `SetFailureCounter()`, `PublishBars()`, `PublishPartialBar()` (AC 1–4)
  - [x] Implement `barFields(bar cascade.Bar, isComplete bool) map[string]any` private helper
  - [x] Implement `PublishBars()` — XADD to both streams on bar close, log+count+continue on failure
  - [x] Implement `PublishPartialBar()` — XADD to `candles:` only, skip if `bar.OpenTs == 0`
- [x] Add `PartialPublishSig`, `PartialPublisher` types to `internal/consumer/consumer.go` (AC 5)
- [x] Add `WithPartialPublish()` builder, `hasNewTicks` field, partial-publish select case, `hasNewTicks=true` in handleMessage, XReadGroup block 250ms (AC 5, 6)
- [x] Add `CandlePartialPublishMs` to `internal/config/config.go` and `config_test.go` (AC 7)
- [x] Update `accWriter` in `cmd/candle/main.go`: add `publisher` field, implement `PublishCascadePartial()`, update `Flush()` (AC 8)
- [x] Wire Phase 1 in main.go: create publisher per symbol, `SetFailureCounter`, call `WithPartialPublish` (AC 9)
- [x] Add `partialPublish` field to `symbolEntry`, add 250ms ticker goroutine after Phase 3 (AC 9)
- [x] Create `internal/writer/redis/publisher_l2_test.go` with all 6 L2 test cases (AC 10)
- [x] `go build ./...` and `make test-l1` pass (AC 11)
- [x] `make test-l2` passes (AC 11)

---

## Dev Agent Record

### Completion Notes

All 11 ACs implemented and verified. Key decisions:

- `Publisher` uses `goredis.Cmdable` interface (not `*goredis.Client`) for testability — allows miniredis `*goredis.Client` to satisfy the field without an interface wrapper.
- `barFields` returns `map[string]any` (matching go-redis XAdd Values field); nil float64 → `""` not `"0"`.
- `WithPartialPublish()` builder pattern preserves `consumer.New()` signature unchanged — existing L2 tests need no modification.
- XReadGroup block changed from 1s → 250ms: more frequent select-loop iterations during quiet periods, enabling ≤250ms responsiveness to both `BarClose` and `PartialPublish` signals.
- `hasNewTicks` tracks per-250ms-window activity, guarding against stale repeat publishes.
- No weekly alias key `candles:1w:...` is ever written — verified by `TestPublishBars_MultipleTimeframes`.
- `make test-l1` and `make test-l2` both pass clean with no regressions.

### File List

- `candle-service/internal/writer/redis/publisher.go` — new
- `candle-service/internal/writer/redis/publisher_l2_test.go` — new
- `candle-service/internal/consumer/consumer.go` — modified (PartialPublishSig, PartialPublisher, WithPartialPublish, hasNewTicks, 250ms block)
- `candle-service/internal/config/config.go` — modified (CandlePartialPublishMs)
- `candle-service/internal/config/config_test.go` — modified (default assertions for new field)
- `candle-service/cmd/candle/main.go` — modified (publisher field, PublishCascadePartial, Flush wiring, 250ms ticker, partialPublish channel per symbol)

### Change Log

- 2026-05-08: Story implemented. Created writer/redis Publisher with XADD for candles+candles:close streams. Added PartialPublishSig/PartialPublisher to consumer, WithPartialPublish builder, hasNewTicks flag, 250ms XReadGroup block. Wired 250ms ticker goroutine and per-symbol partialPublish channels in main.go.
