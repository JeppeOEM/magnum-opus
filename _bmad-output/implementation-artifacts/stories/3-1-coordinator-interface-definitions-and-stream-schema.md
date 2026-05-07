# Story 3.1: Coordinator Interface Definitions & Stream Schema

Status: done

## Story

As the service,
I want the StreamWriter and ILPWriter interfaces defined as the authoritative downstream contract, and the raw_ticks QuestDB DDL written before any implementation begins,
so that schema drift is prevented and both writer implementations (3.2, 3.3) can be built and tested in parallel against a stable interface.

## Acceptance Criteria

1. **Given** `internal/coordinator/interfaces.go`
   **Then** it defines `StreamWriter` and `ILPWriter` interfaces — owned by the consuming package (coordinator), not the implementing packages
   **And** interface names follow noun/noun+er pattern
   **And** `ctx context.Context` is the first parameter on every method that touches IO

2. **Given** the Redis Stream schema
   **Then** tick message fields are documented: `type`, `exchange`, `symbol`, `seq`, `ts_exchange`, `ts_local`, `side`, `price` (string), `size` (string), `event_type`
   **And** gap marker fields are documented: `type`, `exchange`, `symbol`, `gap_ts`, `gap_cause`, `seq_before`, `seq_after`
   **And** `gap_cause` accepts exactly four values: `internal_buffer_overflow`, `internal_merge_error`, `external_disconnect`, `external_rate_limit` — no others

3. **Given** the QuestDB `raw_ticks` table
   **Then** the `CREATE TABLE` DDL is written and committed before any `writer/questdb/` implementation code
   **And** DDL fields match exactly: `exchange` (symbol), `symbol` (symbol), `seq` (long), `ts_exchange` (timestamp), `ts_local` (timestamp), `side` (symbol), `price` (string), `size` (string), `event_type` (symbol: `update`|`snapshot`|`trade`), `is_gap` (boolean), `gap_cause` (symbol, nullable)
   **And** the DDL uses `CREATE TABLE IF NOT EXISTS raw_ticks` — idempotent so re-running on an existing deployment does not error
   **And** the DDL is stored in `writer/questdb/schema.sql` and applied via migration on first startup

4. **Given** a new service version adds a field to the Redis Stream tick message schema
   **Then** the new field is appended — existing fields are never renamed, retyped, or removed
   **And** the schema version is documented in `internal/coordinator/interfaces.go` as a comment so breaking changes are detectable in code review
   **And** the `StreamWriter` interface version comment is updated whenever the schema changes (NFR18)

## Tasks / Subtasks

- [x] Create `aggregator/internal/coordinator/interfaces.go` (AC: 1, 2, 4)
  - [x] Define `StreamWriter` interface with `Write` and `WriteGap` methods
  - [x] Define `ILPWriter` interface with `Write` and `Close` methods
  - [x] Add schema version comment (v1) to `StreamWriter`
  - [x] Document Redis Stream tick message schema as embedded comment block
  - [x] Document Redis Stream gap marker schema as embedded comment block
  - [x] Document duplicate-gap-marker contract (Candle Service is dedup boundary)

- [x] Create `aggregator/internal/writer/questdb/schema.sql` (AC: 3)
  - [x] Write full `CREATE TABLE IF NOT EXISTS raw_ticks (...)` DDL with all 11 fields
  - [x] Include `TIMESTAMP(ts_exchange) PARTITION BY DAY WAL` for QuestDB time-series optimization
  - [x] Add comment block documenting field semantics and gap_cause nullable contract

- [x] Create `aggregator/internal/coordinator/interfaces_test.go` (AC: 1, 2)
  - [x] Compile-time checks: `StreamWriter` and `ILPWriter` interfaces exist with correct names
  - [x] Compile-time check: `WriteGap` method signature accepts `gapdetector.GapEvent`
  - [x] Verify `GapCause` string values match the four Redis field constants

- [x] Run `make test-l1` to verify new package compiles and time.Now() ban is satisfied (AC: all)

## Dev Notes

### What This Story Produces (All New Files)

This story is **interfaces + DDL only** — no implementation code. Three files:

```
aggregator/internal/coordinator/interfaces.go        NEW
aggregator/internal/coordinator/interfaces_test.go   NEW
aggregator/internal/writer/questdb/schema.sql        NEW
```

The `coordinator/` and `writer/questdb/` directories do NOT exist yet. Create them.

### Interface Signatures (Exact)

```go
// package coordinator

// StreamWriter publishes normalized tick events and gap markers to Redis Streams.
// Schema version: 1
//
// Schema invariants (NFR18):
//   - Fields are append-only: never rename, retype, or remove an existing field.
//   - Increment the schema version comment when any field is added.
//
// Duplicate-gap-marker contract: on a Write retry after Redis failure, a duplicate
// gap marker MAY be emitted to the ticks stream. This is acceptable — the Candle
// Service is the deduplication boundary. This writer does NOT deduplicate on recovery.
type StreamWriter interface {
    // Write publishes a normalized tick event to ticks:{exchange}:{symbol}.
    Write(ctx context.Context, tick exchange.Tick) error

    // WriteGap publishes an in-band gap marker to ticks:{exchange}:{symbol}
    // and also appends to gaps:log.
    WriteGap(ctx context.Context, exch string, sym symbol.Symbol, gap gapdetector.GapEvent) error
}

// ILPWriter buffers tick events for batched QuestDB ILP writes.
// Callers must call Close when done to flush the final batch before process exit.
type ILPWriter interface {
    // Write buffers a tick for the next flush cycle.
    Write(ctx context.Context, tick exchange.Tick) error

    // Close flushes any buffered ticks and releases the ILP sender.
    Close(ctx context.Context) error
}
```

**Import requirements for `interfaces.go`:**
```go
import (
    "context"

    "github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
    "github.com/mrqdt/magnum-opus/aggregator/internal/gapdetector"
    "github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
)
```

### Redis Stream Schema (Document in Interfaces File)

Document both schemas as block comments in `interfaces.go`.

**Tick message** (written to `ticks:{exchange}:{symbol}` via `XADD`):

| Field | Type | Value / Notes |
|-------|------|---------------|
| `type` | string | `"tick"` — discriminates from gap markers in same stream |
| `exchange` | string | `"kucoin"` or `"bybit"` |
| `symbol` | string | canonical symbol from `symbol.Symbol.String()` |
| `seq` | string (int64) | `exchange.Tick.Seq` |
| `ts_exchange` | string (int64) | `Tick.TsExchange / 1_000_000` — **milliseconds**, NOT nanoseconds |
| `ts_local` | string (int64) | `Tick.TsLocal / 1_000_000` — **milliseconds**, NOT nanoseconds |
| `side` | string | `"bid"` or `"ask"` — empty string for trade events |
| `price` | string | exact string from wire, e.g. `"29500.50"` — never float64 |
| `size` | string | exact string from wire — never float64 |
| `event_type` | string | `"update"` or `"trade"` from `Tick.Type.String()` |

**Gap marker** (written in-band to `ticks:{exchange}:{symbol}` AND to `gaps:log`):

| Field | Type | Value / Notes |
|-------|------|---------------|
| `type` | string | `"gap"` — discriminates from ticks in same stream |
| `exchange` | string | same as tick |
| `symbol` | string | same as tick |
| `gap_ts` | string (int64) | `gap.Timestamp.UnixMilli()` — milliseconds |
| `gap_cause` | string | one of four exact values (see `gapdetector.Cause`) |
| `seq_before` | string (uint64) | `gap.SeqBefore` |
| `seq_after` | string (uint64) | `gap.SeqAfter` |

For the `gaps:log` stream only, also include:
| `seq_gap` | string (int64) | derived: `gap.SeqAfter - gap.SeqBefore - 1` (missing sequence count) |

**Critical: Timestamp conversion.** `exchange.Tick.TsExchange` and `Tick.TsLocal` are Unix **nanoseconds** (populated by exchange adapters with `clk.Now().UnixNano()` and exchange wire timestamps converted to ns). Redis stores **milliseconds**. Writer must divide by `1_000_000`.

### QuestDB schema.sql

```sql
-- raw_ticks: complete L2 delta audit trail written by the aggregator.
-- event_type: 'update' (L2 delta), 'snapshot' (book initialisation), 'trade'
-- gap_cause: nullable — non-null only when is_gap = true
-- Idempotent: IF NOT EXISTS ensures safe re-run on existing deployment.
CREATE TABLE IF NOT EXISTS raw_ticks (
    exchange   SYMBOL,
    symbol     SYMBOL,
    seq        LONG,
    ts_exchange TIMESTAMP,
    ts_local   TIMESTAMP,
    side       SYMBOL,
    price      STRING,
    size       STRING,
    event_type SYMBOL,
    is_gap     BOOLEAN,
    gap_cause  SYMBOL
) TIMESTAMP(ts_exchange) PARTITION BY DAY WAL;
```

Store this verbatim in `aggregator/internal/writer/questdb/schema.sql`.

The `writer/questdb/writer.go` (Story 3.3) will apply this DDL on startup via a REST query to QuestDB port 9000 (`/exec` HTTP endpoint, not port 9009 ILP). Do NOT implement that application logic now — just write the SQL file.

### Known GapEvent Structure (from Epic 1)

`gapdetector.GapEvent` already exists at `internal/gapdetector/types.go`:

```go
type GapEvent struct {
    Cause     Cause      // string type: "internal_buffer_overflow" | "internal_merge_error" |
                         //              "external_disconnect" | "external_rate_limit"
    SeqBefore uint64
    SeqAfter  uint64
    Timestamp time.Time
}

// Cause.Internal() returns true for internal_* causes (service bugs)
```

`gapdetector.Cause` is a `string` type — use `string(gap.Cause)` to get the Redis field value.

### Known Exchange.Tick Structure (from Epic 2)

`exchange.Tick` at `internal/exchange/exchange.go`:

```go
type Tick struct {
    Exchange   string
    Symbol     symbol.Symbol
    Seq        uint64
    TsExchange int64      // Unix NANOSECONDS — must convert to ms for Redis/QuestDB
    TsLocal    int64      // Unix NANOSECONDS — must convert to ms for Redis/QuestDB
    Side       string     // "bid" or "ask"; empty for trade events
    Price      string     // exact wire string, e.g. "29500.50"
    Size       string     // exact wire string
    Type       EventType  // EventTypeUpdate or EventTypeTrade
}

// EventType.String() returns "update" or "trade"
```

### Consumer-Owns-Interface Rule (Architecture Invariant)

The `StreamWriter` and `ILPWriter` interfaces MUST be defined in `coordinator/interfaces.go` — NOT in `writer/redis/` or `writer/questdb/`. This is a hard architectural invariant from the architecture document. The implementing packages (`writer/redis/`, `writer/questdb/`) will satisfy these interfaces but must not define them.

### Test File Pattern

Follow the pattern in `internal/exchange/exchange_compile_test.go`:

```go
//go:build !l2 && !l3  // no build tag — L1 compile check
package coordinator_test

import (
    "testing"
    "github.com/mrqdt/magnum-opus/aggregator/internal/coordinator"
    "github.com/mrqdt/magnum-opus/aggregator/internal/gapdetector"
)

func TestInterfacesExist(t *testing.T) {
    var _ coordinator.StreamWriter  // compile-time: interface must exist
    var _ coordinator.ILPWriter     // compile-time: interface must exist
}

func TestGapCauseValuesMatchSchema(t *testing.T) {
    // Verify the four cause strings match what the Redis schema documents.
    // These are downstream contracts — changing them breaks the Candle Service.
    cases := []struct{ cause gapdetector.Cause; want string }{
        {gapdetector.CauseInternalBufferOverflow, "internal_buffer_overflow"},
        {gapdetector.CauseInternalMergeError,     "internal_merge_error"},
        {gapdetector.CauseExternalDisconnect,     "external_disconnect"},
        {gapdetector.CauseExternalRateLimit,      "external_rate_limit"},
    }
    for _, c := range cases {
        if string(c.cause) != c.want {
            t.Errorf("Cause %v = %q, want %q", c.cause, string(c.cause), c.want)
        }
    }
}
```

### make test-l1 Scope

The `test-l1` target does NOT include `coordinator/` — it only scans `orderbook`, `reconnect`, `gapdetector`, `symbol`, `backoff`, `config`. However, `make test-l1` has a time.Now() ban that grep-scans `./internal/...` including any new `coordinator/` files. Ensure `interfaces.go` does not call `time.Now()` (interface definitions never do, but confirm).

A standard `go build ./...` or `go vet ./...` will catch compile errors. Run `go build ./internal/coordinator/...` after creating the files.

### Project Structure Notes

- `coordinator/` is a new top-level internal package — no existing files to preserve
- `writer/questdb/` is a new directory — no existing files
- `writer/redis/` is also new but not touched by this story
- No `Makefile` changes needed for this story
- `test-l1` does not add `coordinator` to its package list — that's correct; Story 3.4 may add it when L2 tests are written

### References

- Interface placement rule: [Source: architecture.md#Structure Patterns] — "consumer package owns the interface"
- Redis schema: [Source: epics.md#Story 3.1 AC] and [Source: epics.md#Story 3.2 AC]
- QuestDB DDL fields: [Source: epics.md#Story 3.1 AC] — exact field list
- Timestamp nanoseconds: [Source: exchange/exchange.go] — `TsExchange int64 // Unix nanoseconds`
- GapEvent type: [Source: gapdetector/types.go]
- Tick type: [Source: exchange/exchange.go]
- Duplicate gap marker contract: [Source: epics.md#Story 3.2 AC]

### Review Findings

- [x] [Review][Patch] Add `EventTypeSnapshot` constant to `exchange/exchange.go` and document "snapshot" in StreamWriter event_type comment — DDL requires update|snapshot|trade but EventType only had update|trade [internal/exchange/exchange.go, internal/coordinator/interfaces.go]
- [x] [Review][Patch] Add `WriteGap` method to `ILPWriter` — is_gap/gap_cause columns in raw_ticks are unreachable without it [internal/coordinator/interfaces.go]
- [x] [Review][Patch] Add DDL comment on `seq LONG` acknowledging uint64→int64 cast constraint — silent corruption for values >2^63 [internal/writer/questdb/schema.sql]
- [x] [Review][Patch] Fix `ts_local` comment in schema.sql: "nanosecond precision" is misleading — source is ns but ILP writes ms [internal/writer/questdb/schema.sql]
- [x] [Review][Patch] Add schema version reference comment to `ILPWriter` (AC4 covers both interfaces) [internal/coordinator/interfaces.go]
- [x] [Review][Defer] GapEvent.SeqBefore >= SeqAfter produces uint64 underflow in seq_gap formula — pre-existing in gapdetector (Epic 1)
- [x] [Review][Defer] gapdetector.Detect fires on next < prev producing SeqBefore > SeqAfter — pre-existing behavior (Epic 1)
- [x] [Review][Defer] ts_exchange can be zero/epoch from malformed exchange timestamp — pre-existing parser issue (Epic 2)
- [x] [Review][Defer] FakeRedis not goroutine-safe (no mutex on calls/streams/FailAfter) — pre-existing in testutil (Epic 1)
- [x] [Review][Defer] Bybit parseTrade silently drops all but first trade in a multi-trade frame — pre-existing in parser.go (Story 2.5)
- [x] [Review][Defer] price/size STRING unbounded — parser validation is upstream responsibility, not schema's

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

(none — straightforward interface-only implementation with no runtime surprises)

### Completion Notes List

- Created `coordinator/interfaces.go`: `StreamWriter` (Write + WriteGap) and `ILPWriter` (Write + Close) interfaces with full Redis schema documentation embedded as godoc comments, schema version v1 comment, and duplicate-gap-marker contract
- Created `writer/questdb/schema.sql`: idempotent `CREATE TABLE IF NOT EXISTS raw_ticks` DDL with all 11 fields, `TIMESTAMP(ts_exchange) PARTITION BY DAY WAL`, and inline field-semantics comments
- Created `coordinator/interfaces_test.go`: 3 L1 tests — compile-time existence checks for both interfaces, 4-value GapCause string contract test (downstream schema contract), and Internal() classification test
- `make test-l1` green: all existing L1 suites pass (100% on orderbook/reconnect/gapdetector), time.Now() ban satisfied across new `coordinator/` files
- `go test ./internal/coordinator/...`: 3/3 tests PASS

### File List

- `aggregator/internal/coordinator/interfaces.go` — new
- `aggregator/internal/coordinator/interfaces_test.go` — new
- `aggregator/internal/writer/questdb/schema.sql` — new
- `_bmad-output/implementation-artifacts/stories/3-1-coordinator-interface-definitions-and-stream-schema.md` — updated
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — updated

### Change Log

- Implemented story 3.1: coordinator interfaces (StreamWriter, ILPWriter) and QuestDB raw_ticks DDL (Date: 2026-05-06)
- Applied 5 code-review patches: EventTypeSnapshot added to exchange.go; WriteGap added to ILPWriter; seq LONG cast comment; ts_local precision comment fix; ILPWriter schema version reference (Date: 2026-05-06)
