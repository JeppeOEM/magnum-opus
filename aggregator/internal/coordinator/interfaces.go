// Package coordinator defines the interfaces that downstream writer implementations
// must satisfy. Interfaces are defined here (the consumer) rather than in the
// implementing packages — this is the consumer-owns-interface rule (Go idiom).
package coordinator

import (
	"context"

	"github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
	"github.com/mrqdt/magnum-opus/aggregator/internal/gapdetector"
	"github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
)

// StreamWriter publishes normalized tick events and gap markers to Redis Streams.
//
// Schema version: 1
//
// Redis Stream key pattern: ticks:{exchange}:{symbol}
//
// Tick message fields (XADD ticks:{exchange}:{symbol}):
//
//	type        string  "tick" — discriminates from gap markers in the same stream
//	exchange    string  "kucoin" | "bybit"
//	symbol      string  canonical symbol (symbol.Symbol.String())
//	seq         string  exchange.Tick.Seq (uint64 formatted as decimal)
//	ts_exchange string  Tick.TsExchange / 1_000_000 — Unix milliseconds (Tick stores nanoseconds)
//	ts_local    string  Tick.TsLocal    / 1_000_000 — Unix milliseconds (Tick stores nanoseconds)
//	side        string  "bid" | "ask" — empty string for trade events
//	price       string  exact wire string, e.g. "29500.50" — never float64
//	size        string  exact wire string — never float64
//	event_type  string  Tick.Type.String() → "update" | "trade" | "snapshot"
//
// Gap marker fields (written in-band to ticks:{exchange}:{symbol} AND to gaps:log):
//
//	type        string  "gap" — discriminates from ticks in the same stream
//	exchange    string  same as tick
//	symbol      string  same as tick
//	gap_ts      string  gap.Timestamp.UnixMilli() — Unix milliseconds
//	gap_cause   string  one of four exact values: "internal_buffer_overflow" |
//	                    "internal_merge_error" | "external_disconnect" | "external_rate_limit"
//	seq_before  string  gap.SeqBefore (uint64 as decimal)
//	seq_after   string  gap.SeqAfter  (uint64 as decimal)
//
// gaps:log stream additionally includes:
//
//	seq_gap     string  gap.SeqAfter - gap.SeqBefore - 1 (number of missing sequence numbers)
//
// Schema invariants (NFR18):
//   - Fields are append-only: never rename, retype, or remove an existing field.
//   - Increment the schema version comment above when any field is added.
//
// Duplicate-gap-marker contract: on a Write retry after a Redis failure a duplicate
// gap marker MAY be emitted. This is acceptable — the Candle Service is the
// deduplication boundary. This writer does NOT deduplicate gap markers on recovery.
type StreamWriter interface {
	// Write publishes a normalized tick event to ticks:{exchange}:{symbol}.
	Write(ctx context.Context, tick exchange.Tick) error

	// WriteGap publishes an in-band gap marker to ticks:{exchange}:{symbol}
	// and also appends the marker to gaps:log.
	WriteGap(ctx context.Context, exch string, sym symbol.Symbol, gap gapdetector.GapEvent) error
}

// ILPWriter buffers tick events for batched QuestDB ILP writes.
// The implementation owns the flush timer (500ms window) and the single ILP sender.
// Callers must call Close when done to flush the final batch before process exit.
//
// QuestDB schema: see internal/writer/questdb/schema.sql (raw_ticks, schema version 1).
// Field invariants match StreamWriter above — see that doc for the append-only rule.
type ILPWriter interface {
	// Write buffers a tick for the next flush cycle. It does not block waiting
	// for QuestDB acknowledgement. ctx is checked on Write for backpressure when
	// the internal buffer channel is full.
	Write(ctx context.Context, tick exchange.Tick) error

	// WriteGap buffers a gap marker row for the next flush cycle.
	// Gap markers populate is_gap=true and gap_cause in raw_ticks so the QuestDB
	// audit trail includes gap events alongside real ticks.
	WriteGap(ctx context.Context, exch string, sym symbol.Symbol, gap gapdetector.GapEvent) error

	// Close flushes any buffered ticks and gap markers and releases the ILP sender.
	Close(ctx context.Context) error
}
