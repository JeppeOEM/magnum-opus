# Story 3.2: Redis Stream Writer

Status: done

## Story

As the service,
I want a Redis Stream writer that publishes tick messages and in-band gap markers with MAXLEN enforcement,
so that the Candle Service receives a complete, bounded stream with no silent gaps and no unbounded memory growth.

## Acceptance Criteria

1. **Given** a normalized tick event is ready to publish
   **When** `StreamWriter.Write(ctx, tick)` is called
   **Then** it executes `XADD ticks:{exchange}:{symbol} MAXLEN ~ 50000 * field value ...` with all tick fields
   **And** `price` and `size` are written as strings — never numeric types
   **And** timestamps are written as Unix milliseconds (int64, divided by 1_000_000 from nanosecond source)

2. **Given** a gap event is detected
   **When** the coordinator calls `StreamWriter.WriteGap(ctx, gap)`
   **Then** it writes the gap marker to `ticks:{exchange}:{symbol}` in-band (same stream as ticks)
   **And** it also writes the gap marker to `gaps:log` with the `seq_gap` derived field
   **And** both XADD calls use `MAXLEN ~ 50000` — never omitted

3. **Given** Redis is temporarily unavailable
   **When** a write fails
   **Then** it retries with `internal/backoff/` up to 10 seconds total
   **And** after max retries, it emits a gap marker and continues — it does NOT halt the capture goroutine (NFR9)

4. **Given** an L2 update event arrives
   **When** `StreamWriter.Write(ctx, tick)` is called
   **Then** the tick is written with `event_type: "update"` field

5. **Given** a trade event arrives
   **When** `StreamWriter.Write(ctx, tick)` is called
   **Then** the tick is written with `event_type: "trade"` — verified as a separate L2 test case

6. **Given** a Redis Stream consumer reads via `XREADGROUP`
   **Then** the stream entries are compatible with consumer group reads — verified by an L2 test that performs an actual `XREADGROUP` call via `FakeRedis` and confirms acknowledgement and resume

7. **Given** `FakeRedis` partial failure injection in L2 tests
   **Then** the following scenarios are explicitly covered: write succeeds, write fails once then recovers, gap marker write fails after tick write succeeds
   **And** duplicate gap markers on recovery are acceptable (Candle Service is dedup boundary)
   **And** `go-redis/v9` is used — `go-redis/v8` must not appear in imports

8. **Given** `writer/redis/`
   **Then** `StreamWriter` interface is consumed here, not defined here — definition lives in `coordinator/interfaces.go`

## Tasks / Subtasks

- [x] Add `github.com/redis/go-redis/v9` to `aggregator/go.mod` (AC: 7)
  - [x] Run `go get github.com/redis/go-redis/v9` in `aggregator/`
  - [x] Verify `go-redis/v8` does NOT appear anywhere in go.mod or imports

- [x] Expand `aggregator/internal/testutil/mock/fake_redis.go` (AC: 6, 7)
  - [x] Add `sync.Mutex` to `FakeRedis` struct — current stub is goroutine-unsafe
  - [x] Lock mutex in XADD before reading/writing `calls` and `streams`
  - [x] Add `XREADGROUP(group, consumer, stream string) ([]FakeEntry, error)` method
  - [x] Add `XACK(stream, group, id string) error` method
  - [x] Add `CreateGroup(stream, group string) error` method (creates group at `$` position)
  - [x] Maintain last-delivered position per (group, stream) so XREADGROUP advances correctly

- [x] Create `aggregator/internal/writer/redis/writer.go` (AC: 1, 2, 3, 4, 5, 8)
  - [x] Define `RedisClient` interface consumed by `Writer` (XADD method only — adapter pattern for go-redis)
  - [x] Implement `Writer` struct satisfying `coordinator.StreamWriter`
  - [x] Implement `Write(ctx, tick)`: build tick fields map, XADD to `ticks:{exchange}:{symbol}` with `MAXLEN ~ 50000`
  - [x] Convert `tick.TsExchange` and `tick.TsLocal` from nanoseconds to milliseconds (÷ 1_000_000)
  - [x] Implement `WriteGap(ctx, exch, sym, gap)`: XADD gap marker to `ticks:{exchange}:{symbol}`, then XADD to `gaps:log`; include `seq_gap` on `gaps:log` only
  - [x] Implement retry loop using `backoff.Duration` — cap total elapsed at 10s; on exhaustion emit a gap marker and return
  - [x] Use `type:tick` / `type:gap` field to discriminate entries in the same stream

- [x] Create `aggregator/internal/writer/redis/writer_test.go` (AC: 1–8)
  - [x] Build tag: `//go:build l2`; package: `redis_test`
  - [x] TestWrite_UpdateTick: verify all tick fields, timestamps in ms, MAXLEN ~ present
  - [x] TestWrite_TradeTick: verify `event_type:"trade"` — separate test from update
  - [x] TestWrite_GapMarker: verify in-band write to ticks stream AND gaps:log with seq_gap
  - [x] TestWrite_RetryOnFailure: FakeRedis.FailFirst=1, verify retry succeeds
  - [x] TestWrite_ExhaustedRetry_EmitsGap: alwaysFail client, maxRetryDur=0, verify gap marker emitted
  - [x] TestXREADGROUP_CompatibleEntries: create group, write tick, XREADGROUP reads it, XACK, verify position advances
  - [x] TestWriteGap_DuplicateOnRetry: FailFirst=1 during WriteGap, verify retry produces entry (duplicate acceptable)

- [x] Run `go build ./internal/writer/redis/...` — compile check (AC: all)
- [x] Run `go test -tags l2 ./internal/writer/redis/... -v` — all L2 tests green (AC: all)
- [x] Run `make test-l1` — no regressions in L1 suite (AC: all)

## Dev Notes

### What This Story Produces

```
aggregator/internal/writer/redis/writer.go          NEW
aggregator/internal/writer/redis/writer_test.go     NEW
aggregator/internal/testutil/mock/fake_redis.go     UPDATED (add mutex, XREADGROUP, XACK, CreateGroup)
aggregator/go.mod                                   UPDATED (add go-redis/v9)
aggregator/go.sum                                   UPDATED (auto-generated)
```

### Critical: go-redis Version

Use `github.com/redis/go-redis/v9` (note: `redis/` not `go-redis/`). This is the canonical v9 path.

```go
import "github.com/redis/go-redis/v9"
```

**Never import** `github.com/go-redis/redis/v8` or any v8 path — this is an AC hard requirement. Run `grep -r "go-redis/redis" .` after implementation to confirm.

The XADD with approximate MAXLEN looks like:

```go
rdb.XAdd(ctx, &redis.XAddArgs{
    Stream: streamKey,
    MaxLen: 50000,
    Approx: true,
    Values: fields,
})
```

### Adapter Pattern (RedisClient Interface)

Define a minimal `RedisClient` interface in `writer.go` so the implementation is testable without a real Redis:

```go
type RedisClient interface {
    XAdd(ctx context.Context, args *redis.XAddArgs) *redis.StringCmd
}
```

`FakeRedis` will NOT implement this interface directly — the L2 tests use a thin adapter or the writer is designed with a functional adapter. Alternatively, define `RedisClient` as:

```go
type RedisClient interface {
    XAdd(ctx context.Context, stream string, fields map[string]interface{}, maxLen int64) (string, error)
}
```

And have a real adapter wrapping `*redis.Client` and `FakeRedis` implementing the same narrow interface. This keeps `FakeRedis` independent of the go-redis types. **This is the preferred approach** — FakeRedis stays pure and doesn't import go-redis.

### Writer Struct

```go
// package redis — not "writer" (name collision with stdlib); file is internal/writer/redis/writer.go

package redis

import (
    "context"
    "fmt"
    "time"

    "github.com/mrqdt/magnum-opus/aggregator/internal/backoff"
    "github.com/mrqdt/magnum-opus/aggregator/internal/coordinator"
    "github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
    "github.com/mrqdt/magnum-opus/aggregator/internal/gapdetector"
    "github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
)

// RedisClient is the narrow interface this writer requires.
// The real implementation passes *redis.Client wrapped by RealClient.
type RedisClient interface {
    XAdd(ctx context.Context, stream string, fields map[string]interface{}, maxLen int64) (string, error)
}

// Writer publishes normalized tick events and gap markers to Redis Streams.
// It satisfies coordinator.StreamWriter.
type Writer struct {
    client  RedisClient
    clock   backoff.Clock
    maxRetryDur time.Duration
}

// New returns a Writer using the given client and clock.
// maxRetryDur caps total retry time; pass 10*time.Second for production.
func New(client RedisClient, clock backoff.Clock, maxRetryDur time.Duration) *Writer {
    return &Writer{client: client, clock: clock, maxRetryDur: maxRetryDur}
}
```

Verify at compile time: `var _ coordinator.StreamWriter = (*Writer)(nil)`

### Stream Key Format

```go
ticksKey := fmt.Sprintf("ticks:%s:%s", tick.Exchange, tick.Symbol.String())
gapsKey := "gaps:log"
```

### Tick Fields Map

```go
fields := map[string]interface{}{
    "type":        "tick",
    "exchange":    tick.Exchange,
    "symbol":      tick.Symbol.String(),
    "seq":         strconv.FormatUint(tick.Seq, 10),
    "ts_exchange": strconv.FormatInt(tick.TsExchange/1_000_000, 10),
    "ts_local":    strconv.FormatInt(tick.TsLocal/1_000_000, 10),
    "side":        tick.Side,
    "price":       tick.Price,
    "size":        tick.Size,
    "event_type":  tick.Type.String(),
}
```

All values are strings. `price` and `size` pass through from `Tick` unchanged (already strings from wire). `seq`, timestamps are `strconv.Format*` strings. **Never** use `float64` or numeric types.

### Gap Marker Fields

In-band gap marker written to `ticks:{exchange}:{symbol}`:
```go
inband := map[string]interface{}{
    "type":       "gap",
    "exchange":   exch,
    "symbol":     sym.String(),
    "gap_ts":     strconv.FormatInt(gap.Timestamp.UnixMilli(), 10),
    "gap_cause":  string(gap.Cause),
    "seq_before": strconv.FormatUint(gap.SeqBefore, 10),
    "seq_after":  strconv.FormatUint(gap.SeqAfter, 10),
}
```

`gaps:log` entry additionally includes:
```go
gapsLog := // copy inband, then add:
    "seq_gap": strconv.FormatInt(int64(gap.SeqAfter)-int64(gap.SeqBefore)-1, 10),
```

Note: uint64 underflow is a pre-existing deferred issue (SeqBefore > SeqAfter). Cast to int64 and format — don't add guard logic here.

### Retry Policy

```go
func (w *Writer) xaddWithRetry(ctx context.Context, stream string, fields map[string]interface{}) error {
    deadline := w.clock.Now().Add(w.maxRetryDur)
    for attempt := 0; ; attempt++ {
        _, err := w.client.XAdd(ctx, stream, fields, 50000)
        if err == nil {
            return nil
        }
        if w.clock.Now().After(deadline) {
            return err
        }
        d := backoff.Duration(attempt, w.clock)
        // sleep d — caller must handle. Use time.Sleep or a passed-in sleep func.
        // For testability, inject a sleep function or use a channel-based ticker.
        select {
        case <-ctx.Done():
            return ctx.Err()
        case <-time.After(d): // acceptable here — production path only
        }
    }
}
```

**Important**: `time.After` is acceptable in the writer implementation (not in L1-scoped packages). This writer is IO-bound and not tested at L1. L2 tests use `FakeRedis.FailAfter` with a short `maxRetryDur` to exercise retry paths quickly.

After `xaddWithRetry` exhausts retries for a tick write, the writer calls its own `WriteGap` with a synthetic `gapdetector.GapEvent{Cause: gapdetector.CauseExternalDisconnect}` — this may also fail, which is silently dropped (best-effort sentinel).

### FakeRedis Expansion

Current stub has no mutex and no XREADGROUP. The expansion must:

1. Add `mu sync.Mutex` to the struct
2. Lock in `XADD` before touching `calls` or `streams`
3. Add `groups map[string]map[string]int` — `groups[stream][group]` = last-delivered index
4. Implement `CreateGroup(stream, group string) error` — sets `groups[stream][group] = len(streams[stream])`
5. Implement `XREADGROUP(group, consumer, stream string) ([]FakeEntry, error)` — returns entries from `last+1` onwards, updates position
6. Implement `XACK(stream, group, id string) error` — no-op for test purposes (position already advanced on read)

The existing `XADD` signature stays unchanged — tests that call it currently must not break.

### L2 Test Pattern

```go
//go:build l2

package redis_test

import (
    "context"
    "testing"
    "time"

    "github.com/stretchr/testify/require"

    rediswriter "github.com/mrqdt/magnum-opus/aggregator/internal/writer/redis"
    "github.com/mrqdt/magnum-opus/aggregator/internal/testutil/mock"
    "github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
    "github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
)

func TestWrite_UpdateTick(t *testing.T) {
    fake := mock.NewFakeRedis()
    w := rediswriter.New(fake, mock.NewFakeClock(time.Now()), 10*time.Second)
    tick := exchange.Tick{
        Exchange: "kucoin", Symbol: symbol.MustParse("BTC-USDT"),
        Seq: 42, TsExchange: 1_700_000_000_000_000_000, TsLocal: 1_700_000_000_100_000_000,
        Side: "bid", Price: "29500.50", Size: "0.01", Type: exchange.EventTypeUpdate,
    }
    require.NoError(t, w.Write(context.Background(), tick))
    entries := fake.Entries("ticks:kucoin:BTC-USDT")
    require.Len(t, entries, 1)
    f := entries[0].Fields
    require.Equal(t, "tick", f["type"])
    require.Equal(t, "update", f["event_type"])
    require.Equal(t, "1700000000000", f["ts_exchange"]) // ns ÷ 1_000_000
    require.Equal(t, "29500.50", f["price"])
}
```

Use `mock.NewFakeClock` from `internal/testutil/mock/clock.go` (already exists) for a deterministic clock.

### FakeRedis Adapter

The `Writer` takes a `RedisClient` interface. `FakeRedis` implements this interface:

```go
// FakeRedis must implement RedisClient:
// XAdd(ctx context.Context, stream string, fields map[string]interface{}, maxLen int64) (string, error)
```

Add this method to `FakeRedis` in `fake_redis.go`:

```go
func (f *FakeRedis) XAdd(_ context.Context, stream string, fields map[string]interface{}, _ int64) (string, error) {
    f.mu.Lock()
    defer f.mu.Unlock()
    f.calls++
    if f.FailAfter > 0 && f.calls > f.FailAfter {
        return "", errFakeFailure
    }
    id := generateID(f.calls)
    entry := FakeEntry{ID: id, Fields: make(map[string]string, len(fields))}
    for k, v := range fields {
        entry.Fields[k] = fmt.Sprint(v)
    }
    f.streams[stream] = append(f.streams[stream], entry)
    return id, nil
}
```

Note the signature change: old `XADD(stream string, fields map[string]string)` → new `XAdd(ctx, stream, fields map[string]interface{}, maxLen)`. The old `XADD` method can be removed if unused, or kept as a wrapper.

### Clock in Testutil

`mock.NewFakeClock` already exists at `aggregator/internal/testutil/mock/clock.go`. Use it in L2 tests to satisfy `backoff.Clock`. No new clock implementation needed.

### make test-l1 and the time.Now() Ban

The `time.After` call in `writer.go` is fine — the time.Now() ban grep-scans `./internal/...` for `time.Now()` calls, not `time.After`. However, confirm the writer does NOT call `time.Now()` directly; it uses `clock.Now()` for backoff seeding.

### MAXLEN ~ Semantics

`MAXLEN ~` (approximate) means Redis may retain slightly more than 50,000 entries for performance. This is intentional — always use approximate, never exact. In the `XAddArgs` struct: `Approx: true, MaxLen: 50000`.

In `FakeRedis.XAdd`, the `maxLen` parameter is accepted but not enforced — the fake does not trim streams. This is correct for test purposes.

### Consumer Group Compatibility (AC 6)

The L2 test for XREADGROUP compatibility:
1. Call `fake.CreateGroup("ticks:kucoin:BTC-USDT", "candle-service")`
2. Write a tick
3. Call `fake.XREADGROUP("candle-service", "consumer-1", "ticks:kucoin:BTC-USDT")`
4. Verify one entry returned with correct fields
5. Call `fake.XACK(...)` — should succeed
6. Call `XREADGROUP` again — should return 0 entries (position advanced)

### Compile-Time Interface Check

Add to `writer.go`:
```go
var _ coordinator.StreamWriter = (*Writer)(nil)
```

### Package Name Warning

The package directory is `internal/writer/redis/` but the Go package name must be `package redis`. This collides with the `github.com/redis/go-redis/v9` import alias. Resolve by aliasing go-redis:

```go
import (
    goredis "github.com/redis/go-redis/v9"
)
```

Then the real adapter (wrapping `*goredis.Client`) lives in this same file. The `RedisClient` interface avoids importing go-redis types in the narrow interface — only the `RealClient` adapter struct imports go-redis.

### References

- Interface definitions: `aggregator/internal/coordinator/interfaces.go`
- Backoff function: `aggregator/internal/backoff/backoff.go` — `Duration(attempt int, clock Clock) time.Duration`
- FakeRedis stub to expand: `aggregator/internal/testutil/mock/fake_redis.go`
- FakeQuestDB (reference implementation pattern): `aggregator/internal/testutil/mock/fake_questdb.go`
- Clock fake: `aggregator/internal/testutil/mock/clock.go`
- exchange.Tick fields: `aggregator/internal/exchange/exchange.go`
- gapdetector.GapEvent: `aggregator/internal/gapdetector/types.go`
- epics.md Story 3.2 ACs: `_bmad-output/planning-artifacts/epics.md` line 577

### Review Findings

- [x] [Review][Patch] Move go-redis/v9 to direct require block in go.mod — currently `// indirect` despite writer.go importing it directly; `go mod tidy` would diverge [go.mod]
- [x] [Review][Patch] Fix ctx cancellation blocked during sleepFn — after cancel, goroutine can block up to 60s in `time.Sleep`; change sleepFn signature to `func(context.Context, time.Duration) error` and use timer+select in production path [internal/writer/redis/writer.go:xaddWithRetry]
- [x] [Review][Patch] Deep copy FakeEntry.Fields in Entries() and XREADGROUP() — shallow copy shares map pointer; test mutations corrupt fake's internal stream state [internal/testutil/mock/fake_redis.go]
- [x] [Review][Patch] Add missing L2 test for gaps:log write failure (AC7 third scenario) — FailAfter=1 causes ticks stream write to succeed then gaps:log write to fail [internal/writer/redis/writer_test.go]
- [x] [Review][Patch] Improve TestWrite_ExhaustedRetry_EmitsGap assertion and name — assertion proves gap write was attempted but cannot verify emission (alwaysFailRedis); add comment clarifying intent [internal/writer/redis/writer_test.go]
- [x] [Review][Defer] seq_gap=-1 when Write() emits best-effort gap with SeqBefore=SeqAfter=tick.Seq — no better sequence info available at failure site; downstream receives -1 which is semantically ambiguous [internal/writer/redis/writer.go:83-88]
- [x] [Review][Defer] Gap write error swallowed silently in Write() — best-effort by design per NFR9; no logging for gap marker failure; observability gap pre-existing in design [internal/writer/redis/writer.go:83]
- [x] [Review][Defer] FailFirst/FailAfter share global calls counter across all streams — non-obvious semantics for multi-stream tests; test utility design issue [internal/testutil/mock/fake_redis.go]
- [x] [Review][Defer] XACK no-op / Redis PEL semantics not modeled — FakeRedis advances position on read not on ACK; real Redis would re-deliver unACKed entries on consumer restart [internal/testutil/mock/fake_redis.go]
- [x] [Review][Defer] Production wiring of 10s retry cap not enforced — New() accepts arbitrary maxRetryDur; no production instantiation yet (story 4.3 wires cmd/aggregator)

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

- `time.After` in xaddWithRetry caused test hangs with static mock clock — replaced with injectable `sleepFn` (defaults to `time.Sleep`, overridden to no-op in tests)
- `FailAfter` semantics are "allow N then fail"; added `FailFirst` field for "fail first N calls" to support proper retry test coverage

### Completion Notes List

- Added `go-redis/v9` (v9.19.0) to go.mod; verified no v8 imports
- Rewrote `fake_redis.go`: added `sync.Mutex`, `FailFirst` field, `CreateGroup`/`XREADGROUP`/`XACK` methods, `ResetCalls`; changed XAdd signature to `(ctx, stream, map[string]any, maxLen)` matching `RedisClient` interface
- Created `writer/redis/writer.go`: `RedisClient` interface, `RealClient` adapter (wraps `*goredis.Client`), `Writer` struct with injectable `sleepFn`; compile-time `coordinator.StreamWriter` check; Write + WriteGap + xaddWithRetry with `backoff.Duration` and `!clock.Now().Before(deadline)` exhaustion logic
- Created `writer/redis/writer_test.go`: 7 L2 tests — all pass: UpdateTick (field validation + ms timestamps), TradeTick (event_type:"trade" distinct), GapMarker (in-band + gaps:log with seq_gap), RetryOnFailure (FailFirst=1), ExhaustedRetry_EmitsGap (alwaysFailRedis + maxRetryDur=0), XREADGROUP compatibility, DuplicateOnRetry (acceptable per contract)
- `make test-l1` green: all 6 L1 packages pass at 100%/91%/78%/88%

### File List

- `aggregator/internal/writer/redis/writer.go` — new
- `aggregator/internal/writer/redis/writer_test.go` — new
- `aggregator/internal/testutil/mock/fake_redis.go` — updated (mutex, FailFirst, XREADGROUP, XACK, CreateGroup)
- `aggregator/go.mod` — updated (go-redis/v9 v9.19.0)
- `aggregator/go.sum` — updated
- `_bmad-output/implementation-artifacts/stories/3-2-redis-stream-writer.md` — updated
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — updated

### Change Log

- Implemented story 3.2: Redis Stream Writer with retry, gap markers, MAXLEN enforcement, XREADGROUP compatibility, 7 L2 tests green (Date: 2026-05-06)
