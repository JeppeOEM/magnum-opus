// Package bybit implements the Bybit exchange adapter.
// parser.go is a pure module: no IO, no time.Now() calls, no global state.
package bybit

import (
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/mrqdt/magnum-opus/aggregator/internal/orderbook"
	"github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
)

// ParsedUpdate is the output of parseL2Update: a batch of order book deltas
// for a single symbol. Each Delta carries the update sequence number from the
// outer envelope — price and size are always strings, never float64.
type ParsedUpdate struct {
	Symbol symbol.Symbol
	Deltas []orderbook.Delta
}

// ParsedTrade is the output of parseTrade.
type ParsedTrade struct {
	Symbol     symbol.Symbol
	Seq        uint64
	Price      string // exact string from wire
	Size       string // exact string from wire
	Side       string // "bid" (Buy) or "ask" (Sell)
	TsExchange int64  // Unix nanoseconds
}

// parseL2Update parses a Bybit orderbook.1 message.
//
// Wire format (data field):
//
//	{
//	  "s": "BTCUSDT",
//	  "b": [["price", "qty"]],   // [price, size] tuples — no per-level seq
//	  "a": [["price", "qty"]],
//	  "u": 1001                  // update ID — shared across all levels in this message
//	}
//
// tsMs is the outer envelope "ts" field in milliseconds.
// size "0" means remove that price level from the book.
func parseL2Update(topic string, tsMs int64, data json.RawMessage) (ParsedUpdate, error) {
	sym := symFromTopic(topic)

	var d struct {
		S string     `json:"s"`
		B [][2]string `json:"b"`
		A [][2]string `json:"a"`
		U uint64     `json:"u"`
	}
	if err := json.Unmarshal(data, &d); err != nil {
		return ParsedUpdate{}, fmt.Errorf("bybit: parse l2 data: %w", err)
	}

	tsNano := tsMs * int64(time.Millisecond)

	deltas := make([]orderbook.Delta, 0, len(d.B)+len(d.A))
	for i, level := range d.B {
		if level[0] == "" {
			return ParsedUpdate{}, fmt.Errorf("bybit: bid level %d: empty price", i)
		}
		deltas = append(deltas, orderbook.Delta{
			Seq:        d.U,
			Side:       orderbook.SideBid,
			Price:      level[0],
			Size:       level[1],
			TsExchange: tsNano,
		})
	}
	for i, level := range d.A {
		if level[0] == "" {
			return ParsedUpdate{}, fmt.Errorf("bybit: ask level %d: empty price", i)
		}
		deltas = append(deltas, orderbook.Delta{
			Seq:        d.U,
			Side:       orderbook.SideAsk,
			Price:      level[0],
			Size:       level[1],
			TsExchange: tsNano,
		})
	}

	return ParsedUpdate{
		Symbol: symbol.Normalize("bybit", sym),
		Deltas: deltas,
	}, nil
}

// parseTrade parses a Bybit publicTrade message.
//
// Wire format (data field — array):
//
//	[{
//	  "T": 1683016009123,  // ms
//	  "p": "29501.50",
//	  "v": "0.001",
//	  "S": "Buy",          // "Buy" → "bid", "Sell" → "ask"
//	  "i": "trade-id"
//	}]
//
// Only the first element is parsed; batch trade messages are a future concern.
func parseTrade(topic string, data json.RawMessage) (ParsedTrade, error) {
	sym := symFromTopic(topic)

	var trades []struct {
		T int64  `json:"T"` // milliseconds
		P string `json:"p"` // price
		V string `json:"v"` // size
		S string `json:"S"` // "Buy" or "Sell"
	}
	if err := json.Unmarshal(data, &trades); err != nil {
		return ParsedTrade{}, fmt.Errorf("bybit: parse trade data: %w", err)
	}
	if len(trades) == 0 {
		return ParsedTrade{}, fmt.Errorf("bybit: empty trade array")
	}

	t := trades[0]

	var side string
	switch t.S {
	case "Buy":
		side = "bid"
	case "Sell":
		side = "ask"
	default:
		return ParsedTrade{}, fmt.Errorf("bybit: unknown trade side %q", t.S)
	}

	return ParsedTrade{
		Symbol:     symbol.Normalize("bybit", sym),
		Seq:        0, // Bybit trade messages carry no sequence number
		Price:      t.P,
		Size:       t.V,
		Side:       side,
		TsExchange: t.T * int64(time.Millisecond),
	}, nil
}

// symFromTopic extracts the raw symbol from a Bybit topic string.
// "orderbook.1.BTCUSDT" → "BTCUSDT"
// "publicTrade.BTCUSDT" → "BTCUSDT"
func symFromTopic(topic string) string {
	if i := strings.LastIndex(topic, "."); i >= 0 {
		return topic[i+1:]
	}
	return topic
}

// isL2Topic returns true for orderbook topics.
func isL2Topic(topic string) bool {
	return strings.HasPrefix(topic, "orderbook.")
}

// isTradesTopic returns true for publicTrade topics.
func isTradesTopic(topic string) bool {
	return strings.HasPrefix(topic, "publicTrade.")
}
