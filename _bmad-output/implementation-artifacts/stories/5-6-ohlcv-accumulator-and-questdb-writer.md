# Story 5.6: OHLCV Accumulator and QuestDB Writer

Status: done

## Story

As a developer,
I want `internal/accumulator/`, `internal/features/`, `internal/writer/questdb/`, and `internal/backoff/` implemented and wired into `cmd/candle/main.go`,
so that the service computes 1-second OHLCV bars from Redis ticks and writes them to QuestDB `snapshot_1s` — making the Epic 5 "Done when" observable.

## Acceptance Criteria

1. `internal/features/` exports `OFIDelta(prev, curr BestQuote) float64` as a pure stateless function (Cont et al. 2014 formula: Δ_bid − Δ_ask). Returns 0.0 if either quote is empty. `BestQuote{BidPrice, BidSize, AskPrice, AskSize string}` defined in this package.

2. `internal/accumulator/` implements a pure 1-second OHLCV state machine with Clock injection. `Apply(price, size, side string, prevQuote, currQuote features.BestQuote)` updates open/high/low/close/volume/quote_volume/trade_count/twap and OB best-quote open/close fields. `IncrementGap()` increments gap_count. `Reset()` clears all bar state, OFI accumulator, and prior-book-state. `CurrentBar(tsSecMs int64, isPartial bool) Bar` returns an immutable snapshot. No IO, no goroutines, no time.Now().

3. `internal/backoff/` exports `New(initial, max time.Duration, multiplier float64, jitter float64) *Backoff` and `(*Backoff).Next(attempt int) time.Duration`. Jitter is ±jitter fraction of the base delay. `attempt=0` returns `initial`. Capped at `max`. Tests cover attempt=0, mid-range, cap, and jitter bounds. No goroutines, no IO.

4. `internal/writer/questdb/` exports `Writer` that writes `accumulator.Bar` rows to QuestDB `snapshot_1s` via ILP (`github.com/questdb/go-questdb-client/v3`, TCP port from `QuestDBILPAddr`). `WriteBar(ctx, bar)` is the primary write method. Single ILP sender owned by `Writer` — not goroutine-safe; caller (consumer goroutine) owns it. ILP flush interval: `QUESTDB_ILP_FLUSH_MS` (default 500ms, stored in Config). OHLCV + best-quote-open/close fields written; all other columns left unset (written as null by ILP). `is_partial` and `gap_count` and `bar_count` written on every bar. Quality: if `bar.TradeCount == 0`, trade/price fields (open/high/low/close/volume/quote_volume/twap) are omitted from the ILP row (written as null) — OB fields are always written if present.

5. WAL auto-recovery: `Writer` runs a goroutine that polls QuestDB REST `GET /exec?query=wal_tables()` every `WAL_PROBE_INTERVAL_S` seconds (default 5) when the last successful write is older than 10 seconds. If the response JSON contains `"suspended":true` for `snapshot_1s`, issues `ALTER TABLE snapshot_1s RESUME WAL` via `GET /exec?query=ALTER+TABLE+snapshot_1s+RESUME+WAL`. On resume success, drains the WAL buffer in order. Probe goroutine exits when ctx is cancelled.

6. WAL buffer: during WAL suspension, `WriteBar` enqueues the bar to a bounded in-memory ring buffer of size `WAL_BUFFER_SIZE` (default 10,000). When the buffer is full, the oldest entry is dropped and `candle_wal_drop_total` counter is incremented. Buffer is drained in order after WAL resume. Buffer is part of `Writer` internal state — callers do not observe the buffer.

7. ILP write retry with exponential backoff: on transient ILP flush error, `Writer` retries using `internal/backoff/` with initial=1s, max=30s, multiplier=2.0, jitter=20%. Retries stop when ctx is cancelled. Permanent errors (e.g., bad table schema) are not retried — detect via response status.

8. `internal/consumer/consumer.go` — extend `BookApplier` with `BestQuote() (bidPrice, bidSize, askPrice, askSize string)` and `AccumulatorApplier` with `Apply(price, size, side string, prevBid, prevBidSz, prevAsk, prevAskSz, currBid, currBidSz, currAsk, currAskSz string)`. Update `handleMessage` to capture pre/post best quotes and call `acc.Apply` for each tick. Add `internal/consumer/obadapter.go` — adapter struct that wraps `*orderbook.OrderBook` and satisfies `BookApplier` with type conversion between `consumer.{Tick,SnapshotEvent,GapMarker}` ↔ `orderbook.{Tick,SnapshotEvent,GapMarker}`.

9. `cmd/candle/main.go` — wire one per-symbol consumer goroutine + shared `time.Ticker` (one ticker for the whole process, not one per symbol). For each symbol: create `orderbook.OrderBook`, `accumulator.Accumulator`, `writer/questdb.Writer`, consumer's `obAdapter`, an `accWriter` wrapper implementing `AccumulatorApplier{Apply, Flush, Reset}`, and a buffered `chan consumer.BarCloseSig` (capacity 1). The ticker goroutine fans out on all `barClose` channels with non-blocking send; drops increment `candle_bar_close_dropped_total`. Graceful shutdown: cancel root ctx → consumer goroutines drain → flush in-flight accumulator with `isPartial=true` → close ILP sender.

10. `internal/config/config.go` — add `WALProbeIntervalS int` (env `WAL_PROBE_INTERVAL_S`, default 5), `WALBufferSize int` (env `WAL_BUFFER_SIZE`, default 10000), `QuestDBILPFlushMs int` (env `QUESTDB_ILP_FLUSH_MS`, default 500). Do not break existing tests.

11. `go test ./...` and `go test -tags l2 ./...` pass with zero failures. L1 tests: `accumulator/`, `features/`, `backoff/` with MockClock. L2 tests: `writer/questdb/` with `testutil.FakeQuestDB` (httptest server) for WAL probe + resume scenarios and write path.

## Tasks / Subtasks

- [ ] Create `internal/features/` (AC: 1)
  - [ ] Define `BestQuote{BidPrice, BidSize, AskPrice, AskSize string}` in features package
  - [ ] Implement `OFIDelta(prev, curr BestQuote) float64` per Cont et al. 2014
  - [ ] L1 tests: OFIDelta with bid/ask changes, empty quotes, price-level transitions

- [ ] Create `internal/backoff/` (AC: 3)
  - [ ] `Backoff` struct with initial, max, multiplier, jitter fields
  - [ ] `New(initial, max time.Duration, multiplier, jitter float64) *Backoff`
  - [ ] `Next(attempt int) time.Duration` — capped, jittered
  - [ ] L1 tests: attempt=0, mid-range, cap, jitter bounds (within ±jitter fraction)

- [ ] Create `internal/accumulator/` (AC: 2)
  - [ ] Define `Clock interface { Now() time.Time }`
  - [ ] Define `Bar` struct (all fields documented in Dev Notes)
  - [ ] `New(exchange, symbol string, clk Clock) *Accumulator`
  - [ ] `Apply(price, size, side string, prevQuote, currQuote features.BestQuote)`
  - [ ] `IncrementGap()`
  - [ ] `CurrentBar(tsSecMs int64, isPartial bool) Bar`
  - [ ] `Reset()` — clears bar state, OFI running sum, prior-book-state
  - [ ] L1 tests with MockClock (see Dev Notes for required test list)

- [ ] Create `internal/testutil/mockclock.go` (needed by accumulator tests)
  - [ ] `MockClock` struct with `Set(t time.Time)`, `Now() time.Time`
  - [ ] Satisfies `accumulator.Clock` via structural typing

- [ ] Create `internal/writer/questdb/` (AC: 4–7)
  - [ ] `Writer` struct with ILP sender, WAL buffer, backoff
  - [ ] `New(ctx context.Context, ilpAddr, httpAddr string, cfg WriterConfig) (*Writer, error)`
  - [ ] `WriteBar(ctx context.Context, bar accumulator.Bar) error`
  - [ ] WAL probe goroutine (AC: 5)
  - [ ] WAL buffer ring buffer with overflow drop + counter (AC: 6)
  - [ ] ILP retry with backoff (AC: 7)
  - [ ] `Close(ctx context.Context) error` — flush ILP, stop probe goroutine
  - [ ] L2 tests with FakeQuestDB httptest server (see Dev Notes)

- [ ] Create `internal/testutil/fakequestdb.go` (needed by writer/questdb L2 tests)
  - [ ] `FakeQuestDB` httptest server stub
  - [ ] Records `/exec` queries received
  - [ ] Configurable `SuspendWAL()` / `ResumeWAL()` to simulate WAL states
  - [ ] Returns correct JSON for `wal_tables()` query and `RESUME WAL` command

- [ ] Update `internal/consumer/consumer.go` (AC: 8)
  - [ ] Extend `BookApplier` — add `BestQuote() (bidPrice, bidSize, askPrice, askSize string)`
  - [ ] Extend `AccumulatorApplier` — add `Apply(price, size, side string, prevBid, prevBidSz, prevAsk, prevAskSz, currBid, currBidSz, currAsk, currAskSz string)`
  - [ ] Update `handleMessage` case EventTick: capture pre/post BestQuote, call acc.Apply
  - [ ] Ensure existing L2 tests still pass (they use stub BookApplier/AccumulatorApplier)

- [ ] Create `internal/consumer/obadapter.go` (AC: 8)
  - [ ] `OBAdapter` struct wrapping `*orderbook.OrderBook`
  - [ ] Implement all `BookApplier` methods with type conversion
  - [ ] `BestQuote()` delegates to `ob.BestQuote()`
  - [ ] L1 test: adapter converts all fields correctly for each method

- [ ] Update `internal/config/config.go` (AC: 10)
  - [ ] Add `WALProbeIntervalS int`, `WALBufferSize int`, `QuestDBILPFlushMs int`
  - [ ] Update existing config tests — add defaults assertions

- [ ] Update `cmd/candle/main.go` (AC: 9)
  - [ ] Import Redis client, connect with `redis.ParseURL(cfg.RedisURL)`
  - [ ] For each configured symbol: create `*orderbook.OrderBook`, `*accumulator.Accumulator`, `*questdb.Writer`, `OBAdapter`, `accWriter`, barClose channel
  - [ ] Launch one goroutine per symbol: `consumer.New(...).Run(ctx)`
  - [ ] Launch one shared ticker goroutine: `time.NewTicker(time.Second)` → fan out to all barClose channels
  - [ ] Graceful shutdown: cancel ctx, wait for goroutines, flush accumulators, close writers

- [ ] Run test suite (AC: 11)
  - [ ] `go test ./...` passes
  - [ ] `go test -tags l2 ./...` passes

## Dev Notes

### Package Dependency Direction

Leaf packages (no internal imports): `features/`, `backoff/`, `orderbook/`, `accumulator/` (imports `features/` only), `testutil/`

Allowed imports per package:
- `features/` → stdlib only
- `backoff/` → stdlib only
- `accumulator/` → `features/` + stdlib only
- `writer/questdb/` → `accumulator/`, `backoff/`, `metrics/` + stdlib + external libs
- `consumer/` → `orderbook/` (via obadapter), `writer/questdb/`, `accumulator/` — NO, wait: consumer defines interfaces; the adapter wires orderbook. Actually consumer can import orderbook for the adapter.
- `cmd/candle/main.go` → all internal packages (composition root)

**DO NOT** import `consumer/` from `accumulator/` or `orderbook/`. The dependency arrow always points inward toward leaf packages.

### features.BestQuote and OFIDelta

```go
package features

// BestQuote holds L2 OB best bid/ask at a point in time.
type BestQuote struct {
    BidPrice string
    BidSize  string
    AskPrice string
    AskSize  string
}

// OFIDelta computes the per-tick order flow imbalance delta per Cont et al. (2014).
// Δ_bid = change in best-bid quantity if best-bid price is unchanged, else 0.
// Δ_ask = change in best-ask quantity if best-ask price is unchanged, else 0.
// Returns 0 if either prev or curr has empty BidPrice or AskPrice.
func OFIDelta(prev, curr BestQuote) float64 {
    if prev.BidPrice == "" || prev.AskPrice == "" ||
        curr.BidPrice == "" || curr.AskPrice == "" {
        return 0
    }
    prevBidP, _ := strconv.ParseFloat(prev.BidPrice, 64)
    currBidP, _ := strconv.ParseFloat(curr.BidPrice, 64)
    prevBidQ, _ := strconv.ParseFloat(prev.BidSize, 64)
    currBidQ, _ := strconv.ParseFloat(curr.BidSize, 64)
    prevAskP, _ := strconv.ParseFloat(prev.AskPrice, 64)
    currAskP, _ := strconv.ParseFloat(curr.AskPrice, 64)
    prevAskQ, _ := strconv.ParseFloat(prev.AskSize, 64)
    currAskQ, _ := strconv.ParseFloat(curr.AskSize, 64)

    var deltaBid, deltaAsk float64
    if currBidP == prevBidP {
        deltaBid = currBidQ - prevBidQ
    } else if currBidP > prevBidP {
        deltaBid = currBidQ // new price level raised: add full quantity
    } // else bid price dropped — prior level displaced; Δ_bid = 0

    if currAskP == prevAskP {
        deltaAsk = currAskQ - prevAskQ
    } else if currAskP < prevAskP {
        deltaAsk = currAskQ // new price level lowered: add full quantity
    } // else ask price rose — prior level displaced; Δ_ask = 0

    return deltaBid - deltaAsk
}
```

### accumulator.Bar Fields (OHLCV scope only)

```go
// Bar is an immutable snapshot of the current second's state.
type Bar struct {
    TsSecMs     int64   // Unix ms of the second boundary (floor to 1000ms)
    Exchange    string
    Symbol      string

    // OHLCV — nil means no trades this second (written as null to QuestDB)
    Open        *float64
    High        *float64
    Low         *float64
    Close       *float64
    Volume      *float64
    QuoteVolume *float64
    TradeCount  int
    TWAP        *float64  // nil if Volume==0 (avoid division by zero)

    // OB best quotes open (first tick of second) and close (last tick/bar-close)
    BestBidOpen *float64  // nil if OB had no valid quote at first tick
    BestAskOpen *float64
    BestBid     *float64  // nil if OB had no valid quote at bar close
    BestAsk     *float64

    // OFI — always written (0 if no ticks)
    OFI         float64

    // Quality
    IsPartial bool
    GapCount  int
    BarCount  int
}
```

**Key invariant:** If `TradeCount == 0`, `Open/High/Low/Close/Volume/QuoteVolume/TWAP` MUST be nil. The QuestDB writer must NOT write these columns for zero-trade bars.

### accumulator.Accumulator internal state

```go
type Accumulator struct {
    exchange string
    symbol   string
    clk      Clock     // injected — never call time.Now() directly

    // OHLCV
    open, high, low, close float64
    volumeSum   float64
    quoteVolSum float64
    twapNumer   float64  // sum(price * size) — divide by volumeSum for TWAP
    tradeCount  int

    // OB quote tracking (float64 for computation)
    bestBidOpen, bestAskOpen float64
    bestBid, bestAsk         float64
    hasOpenQuote, hasCloseQuote bool

    // OFI running sum
    ofiSum float64

    // Quality
    gapCount int
    barCount int
}
```

`Apply` rules:
- Only process ticks with `side == "buy"` or `side == "sell"` (trade classification) — OB delta ticks with Level > 0 are NOT trade ticks and do NOT update OHLCV. Wait: the Level field is NOT passed to the accumulator. The consumer calls `acc.Apply` for ALL ticks that reach this point. Looking at consumer.go: tick goes to `book.ApplyTick` AND `acc.Apply`. But OB delta ticks (Level>0) should NOT update OHLCV. **Only trade ticks (Level==0) update OHLCV.** The `Apply` signature must include enough info to distinguish trade vs OB ticks.

**Revised Apply signature:**

```go
// Apply records a single tick. isTrade=true for trade ticks (Level==0), false for OB deltas.
// prevQuote/currQuote are the OB best quotes before and after the tick.
// OB delta ticks update the OB best-quote tracking but NOT OHLCV.
// Trade ticks update OHLCV and OB best-quote tracking.
func (a *Accumulator) Apply(price, size string, isTrade bool, prevQuote, currQuote features.BestQuote)
```

Actually this is cleaner. Let me revise the consumer interface accordingly.

**Consumer `AccumulatorApplier` interface — revised:**

```go
type AccumulatorApplier interface {
    Apply(price, size string, isTrade bool,
          prevBid, prevBidSz, prevAsk, prevAskSz,
          currBid, currBidSz, currAsk, currAskSz string)
    Flush(isPartial bool) error
    Reset()
}
```

In `handleMessage`:
```go
case EventTick:
    prevBid, prevBidSz, prevAsk, prevAskSz := c.book.BestQuote()
    c.book.ApplyTick(*parsed.Tick)
    currBid, currBidSz, currAsk, currAskSz := c.book.BestQuote()
    isTrade := parsed.Tick.Level == 0
    c.acc.Apply(parsed.Tick.Price, parsed.Tick.Size, isTrade,
        prevBid, prevBidSz, prevAsk, prevAskSz,
        currBid, currBidSz, currAsk, currAskSz)
    c.tickCount++
```

Wait, `tickCount` was used to guard the zero-tick partial flush. With this change, ALL ticks (OB and trade) increment tickCount. That's correct — if any tick arrives (OB delta or trade), we have updates worth flushing.

### accumulator Apply logic detail

```go
func (a *Accumulator) Apply(price, size string, isTrade bool, 
    prevQuote, currQuote features.BestQuote) {

    // Always update OFI (both trade and OB delta ticks contribute)
    a.ofiSum += features.OFIDelta(prevQuote, currQuote)

    // Always update OB close quote (last known state)
    if currQuote.BidPrice != "" {
        bid, _ := strconv.ParseFloat(currQuote.BidPrice, 64)
        ask, _ := strconv.ParseFloat(currQuote.AskPrice, 64)
        a.bestBid = bid
        a.bestAsk = ask
        a.hasCloseQuote = true
        if !a.hasOpenQuote {
            a.bestBidOpen = bid
            a.bestAskOpen = ask
            a.hasOpenQuote = true
        }
    }

    if !isTrade {
        return // OB delta — OFI + OB quote updated; OHLCV not updated
    }

    // Trade tick: update OHLCV
    p, err := strconv.ParseFloat(price, 64)
    if err != nil { return }
    s, err := strconv.ParseFloat(size, 64)
    if err != nil { return }

    if a.tradeCount == 0 {
        a.open = p
        a.high = p
        a.low = p
    } else {
        if p > a.high { a.high = p }
        if p < a.low  { a.low = p }
    }
    a.close = p
    a.volumeSum += s
    a.quoteVolSum += p * s
    a.twapNumer += p * s
    a.tradeCount++
}
```

### TWAP computation

`TWAP = twapNumer / volumeSum`. If `volumeSum == 0`, TWAP is nil. This is the same as volume-weighted average price using trade size as weight.

### Writer.WriteBar ILP construction

```go
// Only write non-null columns. The ILP sender omits columns that aren't set — 
// QuestDB WAL fills them as null.

row := sender.Table("snapshot_1s").
    Symbol("exchange", bar.Exchange).
    Symbol("symbol", bar.Symbol)

if bar.Open != nil     { row = row.Float64Column("open", *bar.Open) }
if bar.High != nil     { row = row.Float64Column("high", *bar.High) }
// ... etc for all nullable fields
row = row.Int64Column("trade_count", int64(bar.TradeCount))
row = row.Int64Column("gap_count", int64(bar.GapCount))
row = row.Int64Column("bar_count", int64(bar.BarCount))
row = row.BoolColumn("is_partial", bar.IsPartial)
row = row.Float64Column("ofi", bar.OFI)

err = row.At(ctx, time.UnixMilli(bar.TsSecMs))
```

**Important:** `trade_count`, `gap_count`, `bar_count` are always written (INT columns). `is_partial` is always written. `ofi` is always written.

### Writer.WriteBar WAL-suspended path

```go
func (w *Writer) WriteBar(ctx context.Context, bar accumulator.Bar) error {
    if w.walSuspended.Load() {
        // During suspension: enqueue to ring buffer
        w.walBuf.Enqueue(bar) // drops oldest if full, increments candle_wal_drop_total
        return nil
    }
    return w.writeWithRetry(ctx, bar)
}
```

`walSuspended` is an `atomic.Bool`. The WAL probe goroutine sets it when suspension detected, clears it after successful resume+drain.

### WAL probe goroutine

```go
func (w *Writer) runWALProbe(ctx context.Context) {
    ticker := time.NewTicker(w.probeInterval)
    defer ticker.Stop()
    for {
        select {
        case <-ctx.Done():
            return
        case <-ticker.C:
            if time.Since(w.lastWriteTime.Load()) > 10*time.Second {
                w.probeAndResume(ctx)
            }
        }
    }
}
```

WAL probe REST call: `GET http://{questdbHTTPAddr}/exec?query=wal_tables()`

Expected response structure (abridged):
```json
{
  "dataset": [
    ["snapshot_1s", false, "ACTIVE", null, null]
  ]
}
```
Column indices: 0=table_name, 1=suspended (bool), 2=writer_txn, 3=pending_pending_area, 4=pending_committed

If `dataset[i][1] == true` where `dataset[i][0] == "snapshot_1s"` → suspended.

Resume command: `GET http://{questdbHTTPAddr}/exec?query=ALTER%20TABLE%20snapshot_1s%20RESUME%20WAL`

After resume: drain WAL buffer oldest-first via `writeWithRetry`.

### QuestDB client v3 API

```go
import qdb "github.com/questdb/go-questdb-client/v3"

// Create sender (TCP ILP)
sender, err := qdb.NewLineSender(ctx, qdb.WithTcp(ilpAddr))
// or: qdb.LineSenderFromConf(ctx, "tcp::addr="+ilpAddr+";")

// Write a row (fluent builder):
err = sender.Table("snapshot_1s").
    Symbol("exchange", "kucoin").
    Symbol("symbol", "BTC-USDT").
    Float64Column("open", 50000.0).
    Int64Column("trade_count", 42).
    BoolColumn("is_partial", false).
    At(ctx, time.UnixMilli(tsMs))

// Flush buffered rows to QuestDB:
err = sender.Flush(ctx)

// Close gracefully:
err = sender.Close(ctx)
```

**ILP is NOT goroutine-safe.** One sender per Writer, owned by the writer goroutine (or called only from the consumer goroutine). Do NOT share across goroutines.

**WAL accepted-not-committed:** A successful `Flush` does NOT mean rows are immediately queryable. Do not assert row presence in L2 tests immediately after flush.

### FakeQuestDB for L2 tests

```go
// testutil/fakequestdb.go
// //go:build l2

type FakeQuestDB struct {
    srv      *httptest.Server
    mu       sync.Mutex
    queries  []string
    suspended bool
}

func NewFakeQuestDB() *FakeQuestDB {
    fq := &FakeQuestDB{}
    mux := http.NewServeMux()
    mux.HandleFunc("/exec", fq.handleExec)
    fq.srv = httptest.NewServer(mux)
    return fq
}

func (fq *FakeQuestDB) Addr() string { return fq.srv.Listener.Addr().String() }
func (fq *FakeQuestDB) Close()       { fq.srv.Close() }
func (fq *FakeQuestDB) SetSuspended(v bool) { fq.mu.Lock(); fq.suspended = v; fq.mu.Unlock() }
func (fq *FakeQuestDB) Queries() []string   { fq.mu.Lock(); defer fq.mu.Unlock(); return fq.queries }

func (fq *FakeQuestDB) handleExec(w http.ResponseWriter, r *http.Request) {
    q := r.URL.Query().Get("query")
    fq.mu.Lock()
    fq.queries = append(fq.queries, q)
    susp := fq.suspended
    fq.mu.Unlock()

    if strings.Contains(q, "wal_tables()") {
        suspended := 0
        if susp { suspended = 1 }
        // Return correct wal_tables() format
        fmt.Fprintf(w, `{"dataset":[["snapshot_1s",%v,"ACTIVE",null,null]]}`, susp)
        return
    }
    if strings.Contains(q, "RESUME WAL") {
        fq.SetSuspended(false)
        fmt.Fprint(w, `{"ddl":"OK"}`)
        return
    }
    w.WriteHeader(http.StatusOK)
    fmt.Fprint(w, `{}`)
}
```

### Consumer interface extensions

In `consumer/consumer.go`, the extended interfaces are:

```go
type BookApplier interface {
    ApplyTick(t Tick)
    ApplySnapshot(s SnapshotEvent)
    ApplyGap(g GapMarker)
    BestQuote() (bidPrice, bidSize, askPrice, askSize string)
}

type AccumulatorApplier interface {
    Apply(price, size string, isTrade bool,
          prevBid, prevBidSz, prevAsk, prevAskSz,
          currBid, currBidSz, currAsk, currAskSz string)
    Flush(isPartial bool) error
    Reset()
}
```

**IMPORTANT:** The existing L2 consumer tests (`consumer_l2_test.go`) use stub structs implementing the OLD interfaces. After extending, you MUST update those stub structs to add `BestQuote()` returning `("","","","")` and `Apply(...)` as a no-op. Check every test stub carefully — failing to add these methods will cause compile errors.

### OBAdapter

```go
// consumer/obadapter.go
package consumer

import "github.com/mrqdt/magnum-opus/candle-service/internal/orderbook"

// OBAdapter wraps *orderbook.OrderBook to satisfy BookApplier.
// Handles type conversion between consumer.* and orderbook.* types.
type OBAdapter struct {
    ob *orderbook.OrderBook
}

func NewOBAdapter(ob *orderbook.OrderBook) *OBAdapter {
    return &OBAdapter{ob: ob}
}

func (a *OBAdapter) ApplyTick(t Tick) {
    a.ob.ApplyTick(orderbook.Tick{
        Seq: t.Seq, TsMs: t.TsMs, Price: t.Price,
        Size: t.Size, Side: t.Side, Level: t.Level,
    })
}
func (a *OBAdapter) ApplySnapshot(s SnapshotEvent) {
    a.ob.ApplySnapshot(orderbook.SnapshotEvent{Seq: s.Seq, TsMs: s.TsMs})
}
func (a *OBAdapter) ApplyGap(g GapMarker) {
    a.ob.ApplyGap(orderbook.GapMarker{
        SeqBefore: g.SeqBefore, SeqAfter: g.SeqAfter,
        GapCause: g.GapCause, GapTsMs: g.GapTsMs,
        Exchange: g.Exchange, Symbol: g.Symbol,
    })
}
func (a *OBAdapter) BestQuote() (string, string, string, string) {
    return a.ob.BestQuote()
}
```

### accWriter wrapper in cmd/candle/main.go

The consumer's `AccumulatorApplier.Flush` writes to QuestDB, but `accumulator.Accumulator` is pure. The bridge is:

```go
// cmd/candle/main.go
type accWriter struct {
    acc     *accumulator.Accumulator
    writer  *questdb.Writer
    tsSecMs func() int64 // returns current second boundary in ms
}

func (aw *accWriter) Apply(price, size string, isTrade bool,
    prevBid, prevBidSz, prevAsk, prevAskSz,
    currBid, currBidSz, currAsk, currAskSz string) {
    aw.acc.Apply(price, size, isTrade,
        features.BestQuote{BidPrice: prevBid, BidSize: prevBidSz, AskPrice: prevAsk, AskSize: prevAskSz},
        features.BestQuote{BidPrice: currBid, BidSize: currBidSz, AskPrice: currAsk, AskSize: currAskSz})
}

func (aw *accWriter) Flush(isPartial bool) error {
    bar := aw.acc.CurrentBar(aw.tsSecMs(), isPartial)
    return aw.writer.WriteBar(context.Background(), bar)
}

func (aw *accWriter) Reset() {
    aw.acc.Reset()
}
```

`tsSecMs()` returns `time.Now().Truncate(time.Second).UnixMilli()` — this IS allowed in `cmd/candle/main.go` (composition root).

### Ticker goroutine in main.go

```go
ticker := time.NewTicker(time.Second)
defer ticker.Stop()
go func() {
    for {
        select {
        case <-ctx.Done():
            return
        case <-ticker.C:
            for _, ch := range barCloseChans {
                select {
                case ch <- consumer.BarCloseSig{}:
                default:
                    // increment candle_bar_close_dropped_total for this symbol
                }
            }
        }
    }
}()
```

### Accumulator L1 tests (required)

Use `testutil.MockClock`. Tests must cover:
- `TestAccumulator_Apply_TradeUpdatesOHLCV` — open/high/low/close/volume updated
- `TestAccumulator_Apply_OBDeltaDoesNotUpdateOHLCV` — OB delta (isTrade=false) only updates OFI
- `TestAccumulator_Apply_OBQuoteOpenCapture` — first valid OB quote captured as open
- `TestAccumulator_Apply_TWAP` — TWAP = sum(price*size)/volume
- `TestAccumulator_CurrentBar_ZeroTrades_NilFields` — no trade ticks → nil OHLCV fields
- `TestAccumulator_IncrementGap` — gap_count incremented
- `TestAccumulator_Reset_ClearsAll` — all fields reset to zero after Reset()
- `TestAccumulator_Apply_OFIDelta` — OFI sum accumulated across ticks

### Required L2 tests for writer/questdb

Build tag: `//go:build l2`

- `TestWriter_WriteBar_Success` — row written via ILP (use real QuestDB client connecting to miniquestdb or just assert sender.Flush called)
- `TestWriter_WAL_SuspendAndResume` — WAL probe detects suspension, issues RESUME WAL, drains buffer
- `TestWriter_WAL_BufferOverflow_DropsOldest` — buffer full → oldest bar dropped → counter incremented
- `TestWriter_WAL_Probe_NoBadWrite_WhenNotStale` — probe goroutine does NOT fire if last write < 10s ago

**Note:** For L2 tests, use `FakeQuestDB` for the HTTP probe side. For ILP writes themselves, use a `net.Listen("tcp", "127.0.0.1:0")` server that accepts connections but discards data — or mock the writer's flush channel. The ILP client connects to TCP; `net.Listen` + `go io.Copy(io.Discard, conn)` is sufficient for unit testing the ILP path.

### Writer Config struct

```go
// writer/questdb/writer.go
type WriterConfig struct {
    ILPAddr          string
    HTTPAddr         string
    FlushInterval    time.Duration
    WALProbeInterval time.Duration
    WALBufferSize    int
}
```

### What NOT to implement in this story

The following are explicitly deferred to Epics 6 and beyond. Do NOT implement:
- OB depth features (bid_depth_*, ask_depth_*, weighted_*, depth_to_1pct_*)
- Block trade classification (block_buy_volume, block_sell_volume, BLOCK_TRADE_WINDOW)
- Trade distribution features (trade_clustering, max_consecutive_run, etc.)
- Volatility features (realized_vol, realized_skewness)
- OB activity features (bid_order_arrivals, etc.)
- Trade microstructure features (trade_sign_autocorr, etc.)
- Mid-price path features (mid_price_*, vwmp, spread_*)
- `internal/cascade/` package
- `internal/writer/redis/` package
- Cascade accumulator persistence (Redis HASH writes)
- Full OFI (full-book OFI only needs ofi_l1 for now, and it's all written as 0 — stub)

### Existing Files Modified

**`internal/consumer/consumer.go`**: extend `BookApplier` and `AccumulatorApplier` interfaces, update `handleMessage` case EventTick. Preserve all existing logic — XACK ordering, gap dedup, snapshot flush, zero-vol discard.

**`internal/consumer/consumer_l2_test.go`**: update stub structs to implement new methods. All stubs that implement `BookApplier` need `BestQuote() (string, string, string, string)` returning `("","","","")`. All stubs implementing `AccumulatorApplier` need `Apply(...)` as no-op.

**`internal/config/config.go`**: add 3 new fields only. No field renames, no existing field changes.

**`cmd/candle/main.go`**: currently just runs migrations + starts HTTP server. This story adds Redis connection, per-symbol goroutines, and the ticker. The existing migration + HTTP server code must be preserved.

### Config env vars added in this story

```
WAL_PROBE_INTERVAL_S   int    default 5      WAL suspension check interval in seconds
WAL_BUFFER_SIZE        int    default 10000  Max rows buffered during WAL suspension
QUESTDB_ILP_FLUSH_MS   int    default 500    ILP batch flush interval in milliseconds
```

### File List (new + modified)

New files:
- `candle-service/internal/features/features.go`
- `candle-service/internal/features/features_test.go`
- `candle-service/internal/backoff/backoff.go`
- `candle-service/internal/backoff/backoff_test.go`
- `candle-service/internal/accumulator/accumulator.go`
- `candle-service/internal/accumulator/accumulator_test.go`
- `candle-service/internal/testutil/mockclock.go`
- `candle-service/internal/testutil/fakequestdb.go` (build tag l2)
- `candle-service/internal/consumer/obadapter.go`
- `candle-service/internal/consumer/obadapter_test.go`
- `candle-service/internal/writer/questdb/writer.go`
- `candle-service/internal/writer/questdb/writer_l2_test.go`

Modified files:
- `candle-service/internal/consumer/consumer.go` (interface extensions + handleMessage update)
- `candle-service/internal/consumer/consumer_l2_test.go` (stub updates only)
- `candle-service/internal/config/config.go` (3 new fields)
- `candle-service/internal/config/config_test.go` (assertions for new defaults)
- `candle-service/cmd/candle/main.go` (Redis + goroutines + ticker wiring)

### References

- [candle-service/project-context.md#OFI State Location] — OFI running sum in accumulator/, pure delta in features/
- [candle-service/project-context.md#Idle Second Field Semantics] — OB fields carried, trade fields null
- [candle-service/project-context.md#Partial Bar on Snapshot Flush] — zero-tick guard
- [candle-service/project-context.md#Forbidden Anti-Patterns] — no time.Now() outside cmd/, no mutex on accumulator
- [candle-service/project-context.md#Upsert Semantics] — crash-restart writes same bar twice, that is correct
- [epics.md#Epic 5 Story 6] — WAL probe interval, buffer size, ILP retry parameters

## Dev Agent Record

### Agent Model Used

_To be filled by dev agent_

### Debug Log References

### Completion Notes List

### File List

## Change Log

- 2026-05-08: Story created
