# Story 3.3: QuestDB ILP Writer

Status: done

## Story

As the service,
I want a QuestDB ILP writer that batches raw tick writes in 500ms windows and automatically resumes WAL suspension,
so that every tick has a complete sequencing audit trail without blocking the capture goroutine on write confirmation.

## Acceptance Criteria

1. **Given** a tick event arrives
   **When** `ILPWriter.Write(ctx, tick)` is called
   **Then** the tick is buffered — not immediately sent to QuestDB
   **And** the sender goroutine owns a single ILP sender behind a channel (not goroutine-safe — single sender enforced)

2. **Given** the 500ms flush timer fires
   **When** `sender.Flush(ctx)` is called
   **Then** all buffered ticks are sent to QuestDB via ILP over TCP port 9009
   **And** a flush returning nil does not guarantee rows are queryable (WAL accepted-not-committed — do not assert row presence immediately after flush in tests)

3. **Given** QuestDB WAL is suspended
   **When** the writer polls every 30 seconds and detects suspension
   **Then** it automatically issues a resume command (FR24)
   **And** the poll and resume are logged at WARN level

4. **Given** a QuestDB ILP Flush fails
   **When** retried with `internal/backoff/` up to 30 seconds
   **Then** after max retries, it logs at ERROR and continues — it does NOT propagate up to the capture goroutine (NFR9)
   **And** `github.com/questdb/go-questdb-client/v3` is used — ILP connects to TCP port 9009

5. **Given** `FakeQuestDB` WAL suspension simulation in L2 tests
   **Then** the accepted-not-committed semantics are explicitly tested
   **And** WAL suspension detection and auto-resume are covered as a distinct test scenario

6. **Given** `sender.Flush(ctx)` returns nil (accepted)
   **When** before WAL commits the rows to queryable storage
   **Then** a flush audit trail is published to Redis stream `questdb:flush:pending`:
   before each flush → `flush-begin` entry with row_count and timestamp;
   after successful flush → `flush-complete` entry with timestamp
   **And** this is tested in L2: flush-begin written before flush, flush-complete after

7. **Given** `writer/questdb/`
   **Then** `ILPWriter` interface is consumed here, not defined here — definition lives in `coordinator/interfaces.go`

## Tasks / Subtasks

- [x] Add `github.com/questdb/go-questdb-client/v3` to `aggregator/go.mod` (AC: 4)
  - [x] Run `go get github.com/questdb/go-questdb-client/v3` in `aggregator/`
  - [x] Verify it appears in the direct require block in go.mod (not indirect)

- [x] Update `aggregator/internal/testutil/mock/fake_questdb.go` (AC: 5)
  - [x] Update `Flush() error` → `Flush(ctx context.Context) error` (ctx ignored, WAL error simulated)
  - [x] Add `Close(ctx context.Context) error` method (no-op for fake)
  - [x] Add `IsWALSuspended(ctx context.Context) (bool, error)` — returns `walSuspend` field
  - [x] Add `WALResume(ctx context.Context) error` — sets `walSuspend = false` (name: WALResume to avoid collision with test-helper `ResumeWAL()`)

- [x] Create `aggregator/internal/writer/questdb/writer.go` (AC: 1–7)
  - [x] Define `ILPSender` interface: `Write(table string, fields map[string]any) error`, `Flush(ctx context.Context) error`, `Close(ctx context.Context) error`
  - [x] Define `WALChecker` interface: `IsWALSuspended(ctx context.Context) (bool, error)`, `ResumeWAL(ctx context.Context) error`
  - [x] Define `FlushAuditor` interface: `XAdd(ctx context.Context, stream string, fields map[string]any, maxLen int64) (string, error)` — matches FakeRedis and RealClient.XAdd signature
  - [x] Implement `Writer` struct satisfying `coordinator.ILPWriter`
  - [x] Add `var _ coordinator.ILPWriter = (*Writer)(nil)` compile-time check
  - [x] `New(sender ILPSender, wal WALChecker, auditor FlushAuditor, clock backoff.Clock, flushInterval, walPollInterval, maxFlushRetryDur time.Duration) *Writer` — starts sender goroutine
  - [x] `Write(ctx, tick)`: build fields map, send rowMsg to channel (select with ctx.Done for backpressure)
  - [x] `WriteGap(ctx, exch, sym, gap)`: build gap fields (is_gap=true, gap_cause set), send rowMsg
  - [x] `Close(ctx)`: signal sender goroutine to stop, wait for it to drain and flush
  - [x] Sender goroutine: flushTicker + walTicker + channel read loop
  - [x] `doFlush(ctx)`: track `pendingCount`; XADD flush-begin; retry Flush(ctx) with backoff up to maxFlushRetryDur; on success XADD flush-complete; on exhaustion log ERROR and continue
  - [x] `checkWAL(ctx)`: call IsWALSuspended; if true log WARN, call ResumeWAL, log result
  - [x] `RealSender` struct wrapping `qdb.LineSender` — implements ILPSender
  - [x] `RealWALChecker` struct with `httpAddr string` — implements WALChecker via HTTP to port 9000
  - [x] `ApplySchema(ctx context.Context, questdbHTTPAddr string) error` — standalone function applying schema.sql DDL via HTTP GET /exec to port 9000

- [x] Create `aggregator/internal/writer/questdb/writer_test.go` (AC: 1–7)
  - [x] Build tag: `//go:build l2`; package: `questdb_test`
  - [x] `TestWrite_Buffered`: Write tick → row in FakeQuestDB before flush, CommittedCount==0
  - [x] `TestFlush_SendsRows`: Write tick → trigger flush → CommittedCount==1
  - [x] `TestFlush_WALSuspension_AutoResumes`: SuspendWAL → flush fails → WAL poller detects → WALResume called → subsequent flush succeeds
  - [x] `TestFlush_Retry_ExhaustionContinues`: always-fail sender, maxFlushRetryDur=0 → flush fails, Write continues accepting ticks (NFR9 — does not block or propagate error)
  - [x] `TestFlush_AuditTrail`: FakeRedis captures flush-begin before flush, flush-complete after flush — stream `questdb:flush:pending`
  - [x] `TestClose_FlushesRemaining`: Write tick, Close → tick committed (final flush on Close)

- [x] Run `go build ./internal/writer/questdb/...` — compile check (AC: all)
- [x] Run `go test -tags l2 ./internal/writer/questdb/... -v` — all L2 tests green (AC: all)
- [x] Run `make test-l1` — no regressions in L1 suite (AC: all)

## Dev Notes

### What This Story Produces

```
aggregator/internal/writer/questdb/writer.go        NEW
aggregator/internal/writer/questdb/writer_test.go   NEW
aggregator/internal/testutil/mock/fake_questdb.go   UPDATED (Flush ctx, Close, IsWALSuspended, WALResume)
aggregator/go.mod                                   UPDATED (add go-questdb-client/v3)
aggregator/go.sum                                   UPDATED (auto-generated)
```

### Critical: ILPWriter Interface Location

`ILPWriter` is **defined in `coordinator/interfaces.go`** (the consumer-owns-interface rule). Do NOT define it in `writer/questdb/`. The three internal interfaces (`ILPSender`, `WALChecker`, `FlushAuditor`) are implementation-local to `writer/questdb/writer.go`.

Compile-time check (add to writer.go):
```go
var _ coordinator.ILPWriter = (*Writer)(nil)
```

### go-questdb-client/v3 API

After `go get github.com/questdb/go-questdb-client/v3`, the ILP sender is created:
```go
import qdb "github.com/questdb/go-questdb-client/v3"

sender, err := qdb.NewLineSender(ctx,
    qdb.WithTcp(),
    qdb.WithAddress("localhost:9009"),
)
```

Rows are written using the fluent API. The designated timestamp for `raw_ticks` is `ts_exchange`:
```go
err = sender.Table("raw_ticks").
    Symbol("exchange", tick.Exchange).
    Symbol("symbol", tick.Symbol.String()).
    Int64Column("seq", int64(tick.Seq)).
    TimestampColumn("ts_exchange", time.Unix(0, tick.TsExchange).UTC()).
    TimestampColumn("ts_local", time.Unix(0, tick.TsLocal).UTC()).
    Symbol("side", tick.Side).
    StringColumn("price", tick.Price).
    StringColumn("size", tick.Size).
    Symbol("event_type", tick.Type.String()).
    BoolColumn("is_gap", false).
    At(ctx, time.Unix(0, tick.TsExchange).UTC())
```

For gap markers: `is_gap=true`, `gap_cause=string(gap.Cause)`, other tick fields may be zero-values.

Flush sends buffered rows:
```go
err = sender.Flush(ctx)
```

Close releases the connection:
```go
sender.Close(ctx)
```

**Important**: The real ILP sender is NOT goroutine-safe — it must only be used from the sender goroutine. This is enforced by the channel-based design.

### ILPSender Interface (in writer.go — not coordinator/interfaces.go)

```go
// ILPSender abstracts the QuestDB ILP TCP connection.
// FakeQuestDB (updated) and RealSender both satisfy this.
type ILPSender interface {
    Write(table string, fields map[string]any) error
    Flush(ctx context.Context) error
    Close(ctx context.Context) error
}
```

`RealSender` wraps `qdb.LineSender` and translates the generic fields map to the fluent ILP API for the `raw_ticks` schema:
```go
type RealSender struct {
    sender *qdb.LineSender
}

func (r *RealSender) Write(table string, fields map[string]any) error {
    s := r.sender.Table(table)
    // SYMBOL columns (low-cardinality strings)
    for _, col := range []string{"exchange", "symbol", "side", "event_type", "gap_cause"} {
        if v, ok := fields[col].(string); ok {
            s = s.Symbol(col, v)
        }
    }
    // INT64 columns
    if seq, ok := fields["seq"].(int64); ok {
        s = s.Int64Column("seq", seq)
    }
    // TIMESTAMP columns (int64 nanoseconds → time.Time)
    if ts, ok := fields["ts_exchange"].(int64); ok {
        s = s.TimestampColumn("ts_exchange", time.Unix(0, ts).UTC())
    }
    if ts, ok := fields["ts_local"].(int64); ok {
        s = s.TimestampColumn("ts_local", time.Unix(0, ts).UTC())
    }
    // STRING columns (exact wire strings)
    for _, col := range []string{"price", "size"} {
        if v, ok := fields[col].(string); ok {
            s = s.StringColumn(col, v)
        }
    }
    // BOOLEAN columns
    if isGap, ok := fields["is_gap"].(bool); ok {
        s = s.BoolColumn("is_gap", isGap)
    }
    // Designated timestamp = ts_exchange
    tsNano, _ := fields["ts_exchange"].(int64)
    _, err := s.At(context.Background(), time.Unix(0, tsNano).UTC())
    return err
}
```

**Verify the exact v3 API** — `s.At()` return type changed between minor versions. Run `go doc github.com/questdb/go-questdb-client/v3.LineSender` after `go get` to confirm signatures.

### WALChecker Interface (in writer.go)

```go
type WALChecker interface {
    IsWALSuspended(ctx context.Context) (bool, error)
    ResumeWAL(ctx context.Context) error
}
```

Production `RealWALChecker` calls the QuestDB HTTP REST API:
```go
type RealWALChecker struct {
    httpAddr string // e.g. "localhost:9000"
    client   *http.Client
}

func (r *RealWALChecker) IsWALSuspended(ctx context.Context) (bool, error) {
    url := fmt.Sprintf("http://%s/exec?query=wal_tables()", r.httpAddr)
    // GET url, parse JSON response for suspended=true entries
    // QuestDB returns {"columns":[...],"dataset":[[table, writerTxn, writerLagTxns, sequencerTxns, suspended],...]}
    // Check if any row for "raw_ticks" has suspended=true
}

func (r *RealWALChecker) ResumeWAL(ctx context.Context) error {
    url := fmt.Sprintf("http://%s/exec?query=ALTER+TABLE+raw_ticks+RESUME+WAL", r.httpAddr)
    // GET url, expect 200
}
```

For L2 tests, `FakeQuestDB` implements `WALChecker` (add `IsWALSuspended` and `WALResume` methods — see FakeQuestDB update section).

### FlushAuditor Interface (in writer.go)

```go
type FlushAuditor interface {
    XAdd(ctx context.Context, stream string, fields map[string]any, maxLen int64) (string, error)
}
```

This is the same signature as `RedisClient.XAdd` in `writer/redis/writer.go`. Both `FakeRedis` and `RealClient` already satisfy this.

### Writer Struct and Constructor

```go
type rowMsg struct {
    table  string
    fields map[string]any
}

type Writer struct {
    sender          ILPSender
    wal             WALChecker
    auditor         FlushAuditor
    clock           backoff.Clock
    flushInterval   time.Duration
    walInterval     time.Duration
    maxFlushRetryDur time.Duration
    rows            chan rowMsg
    stop            chan struct{}
    done            chan struct{}
    pendingCount    int // tracked by sender goroutine only — no mutex needed
}

func New(
    sender ILPSender,
    wal WALChecker,
    auditor FlushAuditor,
    clock backoff.Clock,
    flushInterval, walInterval, maxFlushRetryDur time.Duration,
) *Writer {
    w := &Writer{
        sender:           sender,
        wal:              wal,
        auditor:          auditor,
        clock:            clock,
        flushInterval:    flushInterval,
        walInterval:      walInterval,
        maxFlushRetryDur: maxFlushRetryDur,
        rows:             make(chan rowMsg, 1000),
        stop:             make(chan struct{}),
        done:             make(chan struct{}),
    }
    go w.run(context.Background()) // sender goroutine starts immediately
    return w
}
```

Production call (story 4.3):
```go
w := questdbwriter.New(realSender, realWAL, redisRealClient, clock,
    500*time.Millisecond, 30*time.Second, 30*time.Second)
```

### Write and WriteGap

```go
func (w *Writer) Write(ctx context.Context, tick exchange.Tick) error {
    fields := map[string]any{
        "exchange":    tick.Exchange,
        "symbol":      tick.Symbol.String(),
        "seq":         int64(tick.Seq),
        "ts_exchange": tick.TsExchange,  // nanoseconds — RealSender converts
        "ts_local":    tick.TsLocal,
        "side":        tick.Side,
        "price":       tick.Price,
        "size":        tick.Size,
        "event_type":  tick.Type.String(),
        "is_gap":      false,
        "gap_cause":   "",
    }
    select {
    case w.rows <- rowMsg{table: "raw_ticks", fields: fields}:
        return nil
    case <-ctx.Done():
        return ctx.Err()
    }
}

func (w *Writer) WriteGap(ctx context.Context, exch string, sym symbol.Symbol, gap gapdetector.GapEvent) error {
    fields := map[string]any{
        "exchange":    exch,
        "symbol":      sym.String(),
        "seq":         int64(gap.SeqAfter),
        "ts_exchange": gap.Timestamp.UnixNano(),
        "ts_local":    gap.Timestamp.UnixNano(),
        "side":        "",
        "price":       "0",
        "size":        "0",
        "event_type":  "gap",
        "is_gap":      true,
        "gap_cause":   string(gap.Cause),
    }
    select {
    case w.rows <- rowMsg{table: "raw_ticks", fields: fields}:
        return nil
    case <-ctx.Done():
        return ctx.Err()
    }
}

func (w *Writer) Close(ctx context.Context) error {
    close(w.stop)
    select {
    case <-w.done:
        return nil
    case <-ctx.Done():
        return ctx.Err()
    }
}
```

### Sender Goroutine

```go
func (w *Writer) run(_ context.Context) {
    defer close(w.done)
    flushTicker := time.NewTicker(w.flushInterval)
    walTicker := time.NewTicker(w.walInterval)
    defer flushTicker.Stop()
    defer walTicker.Stop()

    for {
        select {
        case <-w.stop:
            // drain remaining rows
            for {
                select {
                case msg := <-w.rows:
                    _ = w.sender.Write(msg.table, msg.fields)
                    w.pendingCount++
                default:
                    goto drained
                }
            }
        drained:
            w.doFlush(context.Background())
            _ = w.sender.Close(context.Background())
            return

        case msg := <-w.rows:
            _ = w.sender.Write(msg.table, msg.fields)
            w.pendingCount++

        case <-flushTicker.C:
            if w.pendingCount > 0 {
                w.doFlush(context.Background())
            }

        case <-walTicker.C:
            w.checkWAL(context.Background())
        }
    }
}
```

**Note**: `sender.Write()` errors are silently dropped here — the ILP sender buffers rows in memory and the error (if any) surfaces on `Flush()`. This matches QuestDB ILP client semantics.

### doFlush

```go
func (w *Writer) doFlush(ctx context.Context) {
    count := w.pendingCount
    w.pendingCount = 0

    // Flush-begin audit entry
    _ = w.auditor.XAdd(ctx, "questdb:flush:pending", map[string]any{
        "type":      "flush-begin",
        "row_count": strconv.Itoa(count),
        "ts":        strconv.FormatInt(w.clock.Now().UnixMilli(), 10),
    }, 10000)

    // Retry Flush up to maxFlushRetryDur
    deadline := w.clock.Now().Add(w.maxFlushRetryDur)
    for attempt := 0; ; attempt++ {
        err := w.sender.Flush(ctx)
        if err == nil {
            break
        }
        if !w.clock.Now().Before(deadline) {
            slog.Error("questdb flush exhausted retries", "err", err, "rows", count)
            return // NFR9: do not propagate, continue capture
        }
        _ = sleepWithContext(ctx, backoff.Duration(attempt, w.clock))
    }

    // Flush-complete audit entry
    _ = w.auditor.XAdd(ctx, "questdb:flush:pending", map[string]any{
        "type": "flush-complete",
        "ts":   strconv.FormatInt(w.clock.Now().UnixMilli(), 10),
    }, 10000)
}
```

Use the same `sleepWithContext` pattern from `writer/redis/writer.go` — timer+select for proper ctx cancellation. Copy or extract the function. Do NOT import from the redis writer package — duplicate if needed, or move to a shared internal package (defer this to a later refactor).

For tests, inject a `sleepFn` via a method `WithSleep(fn func(ctx context.Context, d time.Duration) error) *Writer` and use `noSleep` in tests (same pattern as redis writer).

### checkWAL

```go
func (w *Writer) checkWAL(ctx context.Context) {
    suspended, err := w.wal.IsWALSuspended(ctx)
    if err != nil {
        slog.Warn("questdb wal check failed", "err", err)
        return
    }
    if !suspended {
        return
    }
    slog.Warn("questdb WAL suspended, issuing RESUME")
    if err := w.wal.ResumeWAL(ctx); err != nil {
        slog.Warn("questdb RESUME WAL failed", "err", err)
        return
    }
    slog.Warn("questdb WAL resumed successfully")
}
```

### FakeQuestDB Updates

Current `fake_questdb.go` has `Flush() error` without ctx and no `Close` or `WALChecker` methods. Updates:

```go
// Change Flush signature — ctx ignored, WAL suspension simulated by error return
func (f *FakeQuestDB) Flush(_ context.Context) error {
    f.mu.Lock()
    defer f.mu.Unlock()
    if f.walSuspend {
        return errWALSuspended
    }
    f.committed += len(f.rows)
    if f.SuspendAfter > 0 && f.committed >= f.SuspendAfter {
        f.walSuspend = true
    }
    f.rows = f.rows[:0]
    return nil
}

// Add Close — no-op for fake
func (f *FakeQuestDB) Close(_ context.Context) error { return nil }

// Add WALChecker implementation
func (f *FakeQuestDB) IsWALSuspended(_ context.Context) (bool, error) {
    f.mu.Lock()
    defer f.mu.Unlock()
    return f.walSuspend, nil
}

// WALResume is the WALChecker interface method (distinct from test-helper SuspendWAL/ResumeWAL)
func (f *FakeQuestDB) WALResume(_ context.Context) error {
    f.mu.Lock()
    defer f.mu.Unlock()
    f.walSuspend = false
    return nil
}
```

Wait — `WALChecker.ResumeWAL` vs `FakeQuestDB.WALResume`: the interface method is `ResumeWAL(ctx) error` but FakeQuestDB already has `ResumeWAL()` (no ctx, used directly in tests). To avoid collision, rename the interface method... actually, the existing `ResumeWAL()` is a test helper (not in any interface). We can safely add `ResumeWAL(ctx context.Context) error` as the interface method — Go allows methods with different signatures on the same type (they're different methods). However this is confusing.

**Resolution**: In `fake_questdb.go`, rename the existing test-helper `ResumeWAL() void` to `ForceResumeWAL()` and add `ResumeWAL(ctx context.Context) error` as the WALChecker implementation. Also rename `SuspendWAL()` to `ForceSuspendWAL()` for consistency. Test code that directly calls these helpers uses the `Force*` names.

### L2 Test Design

```go
//go:build l2

package questdb_test

import (
    "context"
    "testing"
    "time"

    "github.com/stretchr/testify/require"

    "github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
    "github.com/mrqdt/magnum-opus/aggregator/internal/gapdetector"
    "github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
    "github.com/mrqdt/magnum-opus/aggregator/internal/testutil"
    "github.com/mrqdt/magnum-opus/aggregator/internal/testutil/mock"
    questdbwriter "github.com/mrqdt/magnum-opus/aggregator/internal/writer/questdb"
)

var (
    testTime = time.Date(2024, 1, 15, 12, 0, 0, 0, time.UTC)
    testSym  = symbol.Symbol("BTC-USDT")
    noSleep  = func(_ context.Context, _ time.Duration) error { return nil }
)

func newWriter(qdb *mock.FakeQuestDB, redis *mock.FakeRedis) *questdbwriter.Writer {
    clk := testutil.NewMockClock(testTime)
    // 1ms flush and WAL intervals so tests trigger them immediately without real sleeps
    return questdbwriter.New(qdb, qdb, redis, clk,
        1*time.Millisecond, 1*time.Millisecond, 30*time.Second).WithSleep(noSleep)
}
```

For WAL auto-resume test:
```go
func TestFlush_WALSuspension_AutoResumes(t *testing.T) {
    qdb := mock.NewFakeQuestDB()
    redis := mock.NewFakeRedis()
    w := newWriter(qdb, redis)
    defer w.Close(context.Background())

    qdb.ForceSuspendWAL()

    // Write a tick
    require.NoError(t, w.Write(context.Background(), makeTick()))

    // Wait for WAL poller to detect suspension and auto-resume
    time.Sleep(50 * time.Millisecond)

    // WAL should be resumed — subsequent flush succeeds
    require.False(t, qdb.IsWALSuspendedUnsafe()) // test helper (no ctx)
}
```

For flush audit trail test:
```go
func TestFlush_AuditTrail(t *testing.T) {
    qdb := mock.NewFakeQuestDB()
    redis := mock.NewFakeRedis()
    w := newWriter(qdb, redis)

    require.NoError(t, w.Write(context.Background(), makeTick()))
    time.Sleep(50 * time.Millisecond) // wait for flush

    entries := redis.Entries("questdb:flush:pending")
    require.GreaterOrEqual(t, len(entries), 2)
    // First entry is flush-begin
    require.Equal(t, "flush-begin", entries[0].Fields["type"])
    require.Equal(t, "1", entries[0].Fields["row_count"])
    // Last entry is flush-complete
    last := entries[len(entries)-1]
    require.Equal(t, "flush-complete", last.Fields["type"])

    require.NoError(t, w.Close(context.Background()))
}
```

**Note on timing**: The `time.Sleep(50*time.Millisecond)` approach is acceptable in L2 tests since flushInterval is set to 1ms. If this proves flaky, use a retry/poll loop with `require.Eventually`.

### ApplySchema Helper

```go
// ApplySchema applies the raw_ticks DDL via QuestDB HTTP REST /exec endpoint.
// Called once on service startup (story 4.3 wires this).
// questdbHTTPAddr e.g. "localhost:9000"
func ApplySchema(ctx context.Context, questdbHTTPAddr string) error {
    ddl := `CREATE TABLE IF NOT EXISTS raw_ticks (
        exchange    SYMBOL,
        symbol      SYMBOL,
        seq         LONG,
        ts_exchange TIMESTAMP,
        ts_local    TIMESTAMP,
        side        SYMBOL,
        price       STRING,
        size        STRING,
        event_type  SYMBOL,
        is_gap      BOOLEAN,
        gap_cause   SYMBOL
    ) TIMESTAMP(ts_exchange) PARTITION BY DAY WAL`
    u := fmt.Sprintf("http://%s/exec?query=%s", questdbHTTPAddr, url.QueryEscape(ddl))
    req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
    if err != nil {
        return err
    }
    resp, err := http.DefaultClient.Do(req)
    if err != nil {
        return err
    }
    defer resp.Body.Close()
    if resp.StatusCode != http.StatusOK {
        return fmt.Errorf("questdb ApplySchema: HTTP %d", resp.StatusCode)
    }
    return nil
}
```

This function is not tested in L2 (it requires a real QuestDB). L4 tests will cover it.

### Retry Policy for Flush

Retry uses `backoff.Duration(attempt, clock)` same as redis writer. Key difference: `maxFlushRetryDur` is 30 seconds for QuestDB (vs 10s for Redis). After exhaustion, log ERROR and return — never propagate to the capture goroutine.

```go
deadline := w.clock.Now().Add(w.maxFlushRetryDur)
for attempt := 0; ; attempt++ {
    err := w.sender.Flush(ctx)
    if err == nil {
        break
    }
    if !w.clock.Now().Before(deadline) {
        slog.Error("questdb: flush retry exhausted", "attempt", attempt, "err", err)
        return
    }
    _ = w.sleepFn(ctx, backoff.Duration(attempt, w.clock))
}
```

### Package Name

The package directory is `internal/writer/questdb/` but the Go package name is `package questdb`. No collision with `go-questdb-client` since that library is imported under an alias:
```go
import qdb "github.com/questdb/go-questdb-client/v3"
```

### References

- Interface definitions: `aggregator/internal/coordinator/interfaces.go`
- Schema DDL: `aggregator/internal/writer/questdb/schema.sql`
- FakeQuestDB: `aggregator/internal/testutil/mock/fake_questdb.go`
- FakeRedis (FlushAuditor): `aggregator/internal/testutil/mock/fake_redis.go`
- Redis Writer (pattern reference): `aggregator/internal/writer/redis/writer.go`
- Backoff: `aggregator/internal/backoff/backoff.go`
- MockClock: `aggregator/internal/testutil/clock.go`
- epics.md Story 3.3 ACs: `_bmad-output/planning-artifacts/epics.md` line 622

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

- FakeQuestDB.Write needed ctx added (to satisfy ILPSender interface); FakeQuestDB.ResumeWAL() (no-ctx test helper) renamed to a ctx-bearing version that satisfies WALChecker; SuspendWAL() kept as test helper
- WAL auto-resume wired into doFlush retry loop (not only periodic WAL ticker) to avoid goroutine blocking problem: while doFlush is retrying, sender goroutine can't process walTicker events

### Completion Notes List

- Added go-questdb-client/v3 (v3.2.0) to go.mod direct require block
- Updated fake_questdb.go: Flush/Close now take ctx; added IsWALSuspended(ctx)/ResumeWAL(ctx) satisfying WALChecker; SuspendWAL() remains as test-only helper
- Created writer/questdb/writer.go: ILPSender/WALChecker/FlushAuditor interfaces; Writer.Start() separates goroutine launch from construction (race-free); sender goroutine with flushTicker+walTicker; doFlush resets pendingCount only on success; checkWAL integrated into flush retry loop; WithWALSuspendedMetric callback (AC3); RealSender adapter; RealWALChecker with JSON count parsing; ApplySchema with timeout client
- Created writer/questdb/writer_test.go: 10 L2 tests all pass — Buffered, SendsRows, TradeTick, WALAutoResume+metric, RetryExhaustion (NFR9), AuditTrail, CloseFlushes, WriteGap, DanglingBegin (AC6), WALAcceptedNotCommitted
- make test-l1: 6 L1 packages green, no regressions
- Review patches applied: IsWALSuspended JSON parsing (count>0 check), SQL fix (SELECT * FROM), Start() method prevents WithSleep race, pendingCount reset on success only, ApplySchema timeout client, onWALSuspended callback (AC3), DanglingBegin test (AC6)

### File List

- `aggregator/internal/writer/questdb/writer.go` — new
- `aggregator/internal/writer/questdb/writer_test.go` — new
- `aggregator/internal/testutil/mock/fake_questdb.go` — updated (Flush/Close ctx, IsWALSuspended, ResumeWAL)
- `aggregator/go.mod` — updated (go-questdb-client/v3 v3.2.0)
- `aggregator/go.sum` — updated
- `_bmad-output/implementation-artifacts/stories/3-3-questdb-ilp-writer.md` — updated
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — updated

### Review Findings

- [x] [Review][Patch] Fix RealWALChecker.IsWALSuspended — broken len(body)>50 heuristic; parse JSON count field; empty result body is also >50 bytes on QuestDB [internal/writer/questdb/writer.go:RealWALChecker]
- [x] [Review][Patch] Fix invalid SQL in IsWALSuspended — add `SELECT * FROM` prefix; raw `wal_tables() WHERE...` is not valid QuestDB SQL [internal/writer/questdb/writer.go:RealWALChecker]
- [x] [Review][Patch] Add WAL metric callback (AC3) — `WithWALSuspendedMetric(fn func()) *Writer`; called in checkWAL when suspended; tested in TestFlush_WALSuspension_AutoResumes [internal/writer/questdb/writer.go]
- [x] [Review][Patch] Fix pendingCount reset — was reset before Flush loop; if exhausted, subsequent ticks triggered flush with wrong row_count; now reset only on Flush success [internal/writer/questdb/writer.go:doFlush]
- [x] [Review][Patch] Fix WithSleep data race — split New() construction from Start(); goroutine launches in Start() after all configuration; test pattern: .WithSleep(noSleep).Start() [internal/writer/questdb/writer.go]
- [x] [Review][Patch] Fix ApplySchema http.DefaultClient — no timeout blocks indefinitely on unreachable QuestDB; replaced with &http.Client{Timeout: 10*time.Second} [internal/writer/questdb/writer.go:ApplySchema]
- [x] [Review][Patch] Add TestFlush_DanglingBegin test (AC6) — verifies flush-begin present without flush-complete when flush exhausts; proves the crash-recovery monitoring scenario [internal/writer/questdb/writer_test.go]
- [x] [Review][Defer] WithSleep/WithWALSuspendedMetric race eliminated by Start() pattern; goroutine-past-Close() timeout overshoot is acceptable overrun in shutdown path
- [x] [Review][Defer] side="" and gap_cause="" stored as QuestDB null (SYMBOL column omits write when empty); downstream queries must use `IS NULL` not `= ''`; documented in schema.sql
- [x] [Review][Defer] pendingCount incremented even when RealSender.Write (At()) fails — error logged, count off by 1; At() errors indicate broken connection and Flush() will also fail [internal/writer/questdb/writer.go:run]

### Change Log

- Implemented story 3.3: QuestDB ILP Writer with 500ms flush window, WAL auto-resume, flush audit trail, retry policy, 9 L2 tests green (Date: 2026-05-06)
- Applied code review patches: JSON WAL check, AC3 metric callback, pendingCount fix, Start() race-free pattern, ApplySchema timeout, DanglingBegin test — 10 L2 tests green (Date: 2026-05-06)
