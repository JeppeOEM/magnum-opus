# Story 5.4: Redis Consumer and Gap Parser

Status: done

## Story

As a developer,
I want `internal/symbol/`, config extensions, and `internal/consumer/` implemented with full Redis XREADGROUP reading, message parsing, gap dedup, and correct XACK semantics,
so that the service can read normalized ticks from Redis and dispatch parsed events to downstream interfaces (orderbook and accumulator stubs) with correct at-least-once safety.

## Acceptance Criteria

1. `internal/symbol/` exports a `Symbol` type wrapping `(exchange, raw string)` with a `String()` method returning `exchange:raw`; `New(exchange, raw string) Symbol` is the only constructor; package has zero internal imports.
2. `internal/config/` extended with: `RedisURL string`, `SymbolsKuCoin []string`, `SymbolsByBit []string`, `ConsumerGroup string`, `ColdStartBufferSize int`, `QuestDBILPAddr string`; all read from env vars with correct defaults per Naming Conventions section.
3. `.env.example` in `candle-service/` documents every env var with default and description; no secret values present.
4. `internal/consumer/` defines interfaces `BookApplier` (Apply tick/snapshot/gap) and `AccumulatorApplier` (Flush, Reset) consumed by the consumer loop — interfaces defined in `consumer/`, not in the implementing packages.
5. Consumer reads from `ticks:{exchange}:{symbol}` via `XREADGROUP` using consumer group `cfg.ConsumerGroup`. On first connect (group create), starts at `$`; on restart resumes from last-ack'd position.
6. On first connect: if stream length > 45,000 entries, emits a `GapMarker` with `gap_cause=stream_overflow` before consuming any messages.
7. XACK is sent **before** forwarding the event to downstream interfaces — if downstream panics, the message is not redelivered and will not double-count `gap_count`.
8. Gap markers are deduped via Redis `ZSET` `candle:gap_dedup:{exchange}:{symbol}` keyed on `(exchange, symbol, seq_before, seq_after, gap_cause)` with score=unix_ms; ZSET bounded to 10,000 entries via `ZREMRANGEBYRANK` after each add. Duplicate gap markers are XACK'd and discarded without dispatching downstream.
9. Zero-volume trades (`size == "0"`) are XACK'd and discarded with a DEBUG log — not forwarded to downstream.
10. Unknown message types are XACK'd with a WARN log and continue — consumer loop never stalls on unknown types.
11. Per-symbol dedup set of processed message IDs within the current 1-second window prevents OFI inflation on redelivery. Set is cleared on each `BarClose` signal (delivered via a typed channel from `cmd/candle/` ticker goroutine, capacity 1, non-blocking send from ticker side).
12. `go test ./...` and `go test -tags l2 ./...` pass with zero failures; L1 tests cover message parsing and gap dedup logic; L2 tests use `FakeRedis` (httptest or in-process) to verify the XREADGROUP loop, XACK ordering, and gap emission on overflow.

## Tasks / Subtasks

- [x] Create `internal/symbol/` package (AC: 1)
  - [x] Define `Symbol` type and `New()` constructor
  - [x] Add `String() string` returning `exchange:raw`
  - [x] L1 tests: `TestSymbol_String`, `TestSymbol_Fields`

- [x] Extend `internal/config/` (AC: 2)
  - [x] Add `RedisURL`, `SymbolsKuCoin`, `SymbolsByBit`, `ConsumerGroup`, `ColdStartBufferSize`, `QuestDBILPAddr`
  - [x] Parse `SYMBOLS_KUCOIN` and `SYMBOLS_BYBIT` as comma-separated lists
  - [x] Add tests for new fields and list parsing

- [x] Create `.env.example` (AC: 3)
  - [x] Document all env vars: REDIS_URL, QUESTDB_HTTP_ADDR, QUESTDB_ILP_ADDR, SYMBOLS_KUCOIN, SYMBOLS_BYBIT, CANDLE_SLOT, CANDLE_SERVICE_PORT, CANDLE_CONSUMER_GROUP, COLD_START_BUFFER_SIZE, LOG_LEVEL, SHUTDOWN_TIMEOUT_S, BLOCK_TRADE_WINDOW, BLOCK_TRADE_MIN_SAMPLE, CANDLE_STREAM_MAXLEN, CANDLE_PARTIAL_PUBLISH_MS, QUESTDB_ILP_FLUSH_MS

- [x] Create `internal/consumer/` package (AC: 4–12)
  - [x] Define `EventType` constants: `EventTick`, `EventGap`, `EventSnapshot`, `EventUnknown`
  - [x] Define `Tick`, `GapMarker`, `SnapshotEvent` structs (see Dev Notes)
  - [x] Define `BookApplier` and `AccumulatorApplier` interfaces
  - [x] Define `BarCloseSignal` channel type (capacity 1, non-blocking send in ticker goroutine)
  - [x] Implement `parseMessage(fields map[string]interface{}) ParsedMessage` (pure, L1-testable)
  - [x] Implement gap dedup ZSET logic
  - [x] Implement startup MAXLEN check
  - [x] Implement XREADGROUP loop: XACK before dispatch, zero-vol discard, dedup set, unknown-type handling
  - [x] L1 tests: parseMessage for tick/gap/snapshot/unknown/zero-vol
  - [x] L2 tests: consumer loop with FakeRedis (XACK ordering, gap overflow, dedup)

- [x] Run test suite (AC: 12)
  - [x] `go test ./...` passes
  - [x] `go test -tags l2 ./...` passes

## Dev Notes

### `internal/symbol/` Package

```go
package symbol

type Symbol struct {
    Exchange string
    Raw      string
}

func New(exchange, raw string) Symbol {
    return Symbol{Exchange: exchange, Raw: raw}
}

func (s Symbol) String() string {
    return s.Exchange + ":" + s.Raw
}
```

No internal imports. No `Stringer` on Config types — only on Symbol.

### Config Extensions

```go
type Config struct {
    // existing fields...
    Slot            string
    ServicePort     string
    ShutdownTimeout time.Duration
    LogLevel        string
    QuestDBHTTPAddr string
    // new fields:
    RedisURL            string
    SymbolsKuCoin       []string
    SymbolsByBit        []string
    ConsumerGroup       string
    ColdStartBufferSize int
    QuestDBILPAddr      string
}
```

Parsing:
```go
RedisURL:            getEnv("REDIS_URL", "redis://localhost:6379"),
SymbolsKuCoin:       parseSymbolList("SYMBOLS_KUCOIN"),
SymbolsByBit:        parseSymbolList("SYMBOLS_BYBIT"),
ConsumerGroup:       getEnv("CANDLE_CONSUMER_GROUP", "candle-service"),
ColdStartBufferSize: getEnvInt("COLD_START_BUFFER_SIZE", 10000),
QuestDBILPAddr:      getEnv("QUESTDB_ILP_ADDR", "localhost:9009"),
```

`parseSymbolList` splits by comma, trims spaces, drops empty strings.

### Redis Message Schema

Messages on `ticks:{exchange}:{symbol}` have these fields (all string values per Redis stream format):

**Tick event** (`event_type=tick`):
```
event_type  tick
seq         <int64 as string>
ts          <unix_ms as string>
price       <decimal string>
size        <decimal string>
side        buy|sell
level       <int, 0=trade, 1..N=book level>
```

**Gap marker** (`event_type=gap`):
```
event_type  gap
seq_before  <int64 as string>
seq_after   <int64 as string>
gap_cause   external_disconnect|snapshot_timeout|seq_discontinuity|feed_restart
gap_ts      <unix_ms as string>
exchange    <string>
symbol      <string>
```

**Snapshot event** (`event_type=snapshot`):
```
event_type  snapshot
seq         <int64 as string>
ts          <unix_ms as string>
```
(snapshot body is reconstructed by the OB state machine from exchange feed — not embedded in stream)

**Unknown**: any `event_type` not in the above set.

### ParsedMessage

```go
type ParsedMessage struct {
    Type     EventType
    Tick     *Tick        // non-nil when Type==EventTick
    Gap      *GapMarker   // non-nil when Type==EventGap
    Snapshot *SnapshotEvent // non-nil when Type==EventSnapshot
}

type Tick struct {
    Seq   int64
    TsMs  int64
    Price string // keep as string — never float64
    Size  string // keep as string — never float64
    Side  string // "buy" or "sell"
    Level int    // 0=trade, >0=book level
}

type GapMarker struct {
    SeqBefore int64
    SeqAfter  int64
    GapCause  string
    GapTsMs   int64
    Exchange  string
    Symbol    string
}

type SnapshotEvent struct {
    Seq  int64
    TsMs int64
}
```

### Consumer Interfaces

Defined in `consumer/` package, not in implementing packages:

```go
// BookApplier is implemented by internal/orderbook (Story 5-5).
type BookApplier interface {
    ApplyTick(t Tick) error
    ApplySnapshot(s SnapshotEvent) error
    ApplyGap(g GapMarker)
}

// AccumulatorApplier is implemented by internal/accumulator (Story 5-6).
type AccumulatorApplier interface {
    Flush(isPartial bool) error  // writes bar to QuestDB
    Reset()                      // clears OFI state and prior-book-state
}
```

### XACK Before Dispatch Rule

```
1. Read message from XREADGROUP
2. Parse message type
3. Check dedup: if already in per-second ID set → XACK and continue
4. XACK the message
5. Check if already in gap dedup ZSET (for gap markers) → continue without dispatch
6. Dispatch to BookApplier / AccumulatorApplier
```

This ordering means a panic in BookApplier or AccumulatorApplier after XACK will NOT cause redelivery. This is intentional — see Upsert Semantics in project-context.md.

### Gap Dedup ZSET

Key: `candle:gap_dedup:{exchange}:{symbol}`
Score: current Unix milliseconds
Member: `{seq_before}:{seq_after}:{gap_cause}` (all five dedup fields compressed to string key)

After each ZADD:
```
ZREMRANGEBYRANK candle:gap_dedup:{exchange}:{symbol} 0 -10001
```
(removes all entries beyond 10,000 most recent)

Check before dispatch:
```
ZSCORE candle:gap_dedup:{exchange}:{symbol} "{member}"
```
If score exists → duplicate, discard.

### Startup MAXLEN Check

On consumer group creation (first connect):
```
XLEN ticks:{exchange}:{symbol}
```
If result > 45,000 → emit GapMarker with gap_cause=stream_overflow before processing any messages.

Use `XGROUP CREATE ... $ MKSTREAM` to create the group starting from tip.

On restart (group already exists): resume from `>` (last-ack'd position) — no MAXLEN check needed.

### FakeRedis for L2 Tests

Use `github.com/alicebob/miniredis/v2` — an in-process Redis-compatible test server. No external container needed for L2 tests. Import with `//go:build l2`.

```go
mr, _ := miniredis.Run()
rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
```

`miniredis` supports `XADD`, `XREADGROUP`, `XACK`, `XLEN`, `ZADD`, `ZSCORE`, `ZREMRANGEBYRANK` — sufficient for all consumer L2 tests.

### Consumer Loop Structure

```go
func (c *Consumer) runSymbol(ctx context.Context, sym Symbol, book BookApplier, acc AccumulatorApplier) error {
    // 1. ensure consumer group (XGROUP CREATE $)
    // 2. MAXLEN check on first connect
    // 3. loop:
    //    a. XREADGROUP COUNT 100 BLOCK 1000ms
    //    b. for each message: parse → dedup check → XACK → dispatch
    //    c. select on ctx.Done()
}
```

One goroutine per `(exchange, symbol)` as per project-context.md concurrency model.

### BarClose Channel

```go
type BarCloseSig struct{}
// Per-symbol channel in cmd/candle/main.go:
barClose := make(chan BarCloseSig, 1)
// Ticker goroutine (non-blocking send):
select {
case barClose <- BarCloseSig{}:
default:
    // increment candle_bar_close_dropped_total
}
```

The consumer loop selects on barClose to clear the per-second ID dedup set and trigger bar flush via AccumulatorApplier.Flush().

### Zero-Volume Trade Discard

```go
if tick.Level == 0 && tick.Size == "0" {
    // XACK already done; DEBUG log; continue
}
```

Level==0 means trade event (not OB update). Zero-size OB updates are handled by BookApplier (zero-size delta = remove level).

### Existing Files Being Modified

**`internal/config/config.go`** — add 6 new fields and their env var readers. Do NOT break existing fields or tests.

**`internal/config/config_test.go`** — extend `TestLoad_Defaults` and `TestLoad_EnvOverrides` for new fields.

### Dependencies

`github.com/redis/go-redis/v9` — already in go.mod (confirmed from Story 5-1 setup).
`github.com/alicebob/miniredis/v2` — add to go.mod for L2 tests only.

Verify these are present before implementing:
```
grep -E "go-redis|miniredis" candle-service/go.mod
```

If `miniredis` is absent, add it:
```
go get github.com/alicebob/miniredis/v2
```

### References

- [project-context.md#Redis Stream Contracts] — message schema
- [project-context.md#Critical Behavioral Rules] — XACK order, zero-vol discard, dedup
- [project-context.md#Concurrency Model] — goroutine-per-symbol, single ticker
- [project-context.md#Naming Conventions] — env vars, metrics prefix
- [epics.md#Epic 5 Story 4] — gap dedup ZSET, startup MAXLEN, snapshot event behavior

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

### Completion Notes List

- `internal/symbol/` created as a zero-import leaf package with `Symbol{Exchange, Raw}` and `String()`.
- `internal/config/` extended with 6 fields; `parseSymbolList` uses comma-separator per `.env.example`.
- `.env.example` already existed (Story 5-1); updated SYMBOLS lines from space-separated to comma-separated.
- `internal/consumer/` created with `messages.go` (types, `parseMessage`), `consumer.go` (XREADGROUP loop, interfaces, gap dedup ZSET, MAXLEN check).
- `streamOverflowThresh` made configurable in Consumer struct (default 45,000) to allow low-threshold testing without inserting 45,000+ stream entries.
- go-redis v9 + miniredis compatibility: use `Protocol: 2` (RESP2) in test clients to avoid push notification goroutine complications.
- Consumer group created with `0-0` in tests (read all history) to ensure messages added before group creation are visible; production code uses `$` (tip-of-stream) for real startup behavior.

### File List

- candle-service/internal/symbol/symbol.go (new)
- candle-service/internal/symbol/symbol_test.go (new)
- candle-service/internal/config/config.go (modified)
- candle-service/internal/config/config_test.go (modified)
- candle-service/.env.example (modified — comma-separated symbols)
- candle-service/internal/consumer/messages.go (new)
- candle-service/internal/consumer/consumer.go (new)
- candle-service/internal/consumer/messages_test.go (new)
- candle-service/internal/consumer/consumer_l2_test.go (new)
- candle-service/go.mod (modified — added miniredis/v2)
- candle-service/go.sum (modified)

## Change Log

- 2026-05-08: Story implemented
