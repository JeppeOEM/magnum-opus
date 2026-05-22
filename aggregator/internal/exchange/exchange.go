// Package exchange defines the Exchange interface and associated types.
// All exchange adapters implement this interface; no concrete implementations live here.
package exchange

import (
	"context"

	"github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
)

// FeedType specifies which market data feeds to subscribe to.
type FeedType uint8

const (
	// FeedTypeOrderBook subscribes to L2 order book delta updates.
	FeedTypeOrderBook FeedType = iota
	// FeedTypeTrade subscribes to trade execution events.
	FeedTypeTrade
)

// EventType classifies the market data event carried in a Tick.
type EventType uint8

const (
	// EventTypeUpdate is an L2 order book delta ("update" in the Redis schema).
	EventTypeUpdate EventType = iota
	// EventTypeTrade is a trade execution event ("trade" in the Redis schema).
	EventTypeTrade
	// EventTypeSnapshot is an order book snapshot written to QuestDB when the
	// coordinator initialises or re-initialises a symbol's order book from a REST
	// snapshot. Snapshot events are not published to Redis Streams — they go to
	// raw_ticks only so the audit trail is complete.
	EventTypeSnapshot
)

// String returns the Redis stream / QuestDB field value for the event type.
func (e EventType) String() string {
	switch e {
	case EventTypeUpdate:
		return "update"
	case EventTypeTrade:
		return "trade"
	case EventTypeSnapshot:
		return "snapshot"
	default:
		return "unknown"
	}
}

// Tick is a normalized market data event produced by exchange adapters.
// price and size are always strings — never float64 — to preserve exact wire precision.
type Tick struct {
	Exchange   string
	Symbol     symbol.Symbol
	Seq        uint64    // per-level-change sequence (orderbook dedup)
	TsExchange int64     // Unix nanoseconds from the exchange timestamp field
	TsLocal    int64     // Unix nanoseconds at receipt, captured by the exchange adapter
	Side       string    // "bid" or "ask"; empty string for trade events
	Price      string    // exact string from wire, e.g. "29500.50"
	Size       string    // exact string from wire; "0" means level removed (OB updates only)
	Type       EventType

	// Message-level sequence numbers for gap detection.
	// Exchanges like KuCoin assign a global sequence to each internal event; per-level
	// sequences within one message are non-consecutive. Gap detection must use the
	// message boundary (sequenceStart/sequenceEnd) rather than per-level sequences.
	// Both are 0 for exchanges that use per-level sequences for gap detection (e.g. Bybit).
	MsgSeqStart uint64 // sequenceStart of this message; non-zero only on the first delta
	MsgSeqEnd   uint64 // sequenceEnd of this message; set on every delta in the message
}

// SignalType classifies a lifecycle event emitted by an exchange adapter.
type SignalType uint8

const (
	// SignalNeedsSnapshot means the adapter detected a disconnect or gap and
	// requires a REST snapshot to re-initialise the order book for that symbol.
	SignalNeedsSnapshot SignalType = iota
)

// Signal is a lifecycle event delivered on the Signals() channel.
// The coordinator reads these to drive snapshot fetch and reconnect.Machine transitions.
type Signal struct {
	Symbol symbol.Symbol
	Type   SignalType
	Reason string // "initial", "disconnect", "merge_error"
}

// Exchange is the interface implemented by each exchange adapter.
// New exchanges are added by implementing this interface and registering
// a constructor: map[string]func(cfg ExchangeCfg) Exchange.
type Exchange interface {
	// Name returns the canonical lowercase exchange name ("kucoin", "bybit").
	Name() string

	// Connect establishes WebSocket connection(s) and starts background goroutines.
	// ctx controls the full adapter lifetime — cancelling it shuts the adapter down.
	Connect(ctx context.Context) error

	// Subscribe sends subscription requests for the given raw exchange-format symbols
	// and feed types. Must be called after Connect. symbols are raw strings as they
	// appear in the exchange's API (e.g. "BTC-USDT" for KuCoin, "BTCUSDT" for Bybit).
	Subscribe(symbols []string, feeds []FeedType) error

	// AddSymbols subscribes to additional symbols on the live connection without
	// replacing or re-sending existing subscriptions. Appends to the internal symbol
	// list so reconnects include the new symbols. Safe to call while the adapter is
	// running. Returns an error if the exchange does not support dynamic addition.
	AddSymbols(symbols []string, feeds []FeedType) error

	// Ticks returns the channel on which normalized Tick events are delivered.
	// The channel is closed when the adapter shuts down.
	Ticks() <-chan Tick

	// Signals returns the channel on which lifecycle signals (NeedsSnapshot) are delivered.
	// The coordinator reads this to trigger REST snapshot fetches.
	Signals() <-chan Signal

	// Close triggers a graceful shutdown and waits for all goroutines to exit.
	Close() error
}
