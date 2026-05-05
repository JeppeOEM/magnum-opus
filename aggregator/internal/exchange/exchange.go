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
)

// String returns the Redis stream field value for the event type.
func (e EventType) String() string {
	switch e {
	case EventTypeUpdate:
		return "update"
	case EventTypeTrade:
		return "trade"
	default:
		return "unknown"
	}
}

// Tick is a normalized market data event produced by exchange adapters.
// price and size are always strings — never float64 — to preserve exact wire precision.
type Tick struct {
	Exchange   string
	Symbol     symbol.Symbol
	Seq        uint64
	TsExchange int64     // Unix nanoseconds from the exchange timestamp field
	TsLocal    int64     // Unix nanoseconds at receipt (time.Now().UnixNano())
	Side       string    // "bid" or "ask"; empty string for trade events
	Price      string    // exact string from wire, e.g. "29500.50"
	Size       string    // exact string from wire; "0" means level removed (OB updates only)
	Type       EventType
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

	// Ticks returns the channel on which normalized Tick events are delivered.
	// The channel is closed when the adapter shuts down.
	Ticks() <-chan Tick

	// Signals returns the channel on which lifecycle signals (NeedsSnapshot) are delivered.
	// The coordinator reads this to trigger REST snapshot fetches.
	Signals() <-chan Signal

	// Close triggers a graceful shutdown and waits for all goroutines to exit.
	Close() error
}
