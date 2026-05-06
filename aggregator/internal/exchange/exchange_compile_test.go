// Package exchange_test provides compile-time and L1 structural checks for the exchange package.
// ATDD scaffold for Story 2.1: Exchange Interface & WebSocket Transport Layer.
package exchange_test

import (
	"testing"

	"github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
)

// TestExchangeInterfaceNaming verifies naming conventions from the architecture.
// AC: "interface names follow noun/noun+er pattern (Exchange, not IExchange)"
// AC: "exchange.go defines the Exchange interface only — no implementations"
func TestExchangeInterfaceNaming(t *testing.T) {
	t.Skip("ATDD scaffold — activate when story 2.1 implementation is complete")

	// Verify the Exchange interface is exported and follows noun naming.
	// This is a compile-time check: if Exchange doesn't exist, the build fails.
	var _ exchange.Exchange

	// Verify the associated types exist with correct names (no I-prefix).
	var _ exchange.Tick
	var _ exchange.Signal
	var _ exchange.FeedType
	var _ exchange.EventType
}

// TestEventTypeStringValues verifies the EventType.String() values match the Redis schema.
// These values are downstream contracts — changing them breaks the Candle Service.
// AC: event_type values must be "update", "trade" (from QuestDB schema)
func TestEventTypeStringValues(t *testing.T) {
	t.Skip("ATDD scaffold — activate when story 2.1 implementation is complete")

	tests := []struct {
		name     string
		et       exchange.EventType
		wantStr  string
	}{
		{"update", exchange.EventTypeUpdate, "update"},
		{"trade", exchange.EventTypeTrade, "trade"},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := tc.et.String(); got != tc.wantStr {
				t.Errorf("EventType(%d).String() = %q, want %q", tc.et, got, tc.wantStr)
			}
		})
	}
}

// TestTickFieldTypes documents the required Tick field types at compile time.
// AC: "price and size are always string — never float64"
// This test fails to compile if Price or Size are changed to non-string types.
func TestTickFieldTypes(t *testing.T) {
	t.Skip("ATDD scaffold — activate when story 2.1 implementation is complete")

	// Compile-time field type assertion: assigning string literals must compile.
	_ = exchange.Tick{
		Price: "29500.50",  // must be string
		Size:  "0.001",     // must be string
		Side:  "bid",       // "bid" | "ask" | ""
	}
}
