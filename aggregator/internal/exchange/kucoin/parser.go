// Package kucoin implements the KuCoin exchange adapter.
// parser.go is a pure module: no IO, no time.Now() calls, no global state.
package kucoin

import (
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/mrqdt/magnum-opus/aggregator/internal/orderbook"
	"github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
)

// wireMessage is the top-level KuCoin WebSocket envelope.
// All incoming frames are decoded into this struct first.
type wireMessage struct {
	ID      string          `json:"id"`
	Type    string          `json:"type"`
	Topic   string          `json:"topic"`
	Subject string          `json:"subject"`
	Data    json.RawMessage `json:"data"`
}

// ParsedUpdate is the output of parseL2Update: a batch of order book deltas
// for a single symbol. Each Delta carries the exact sequence number from the
// level tuple — price and size are always strings, never float64.
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
	Side       string // "bid" (buy) or "ask" (sell)
	TsExchange int64  // Unix nanoseconds
}

// parseL2Update parses a KuCoin /market/level2 message.
//
// Wire format (data field):
//
//	{
//	  "sequenceStart": 100,
//	  "sequenceEnd": 101,
//	  "changes": {
//	    "bids": [["29500.50", "1.5", 100]],   // [price, size, seq]
//	    "asks": [["29501.00", "0", 101]]
//	  },
//	  "time": 1620000000000   // milliseconds
//	}
//
// size "0" means remove that price level from the book.
func parseL2Update(msg wireMessage) (ParsedUpdate, error) {
	sym := symbolFromTopic(msg.Topic)

	var data struct {
		Changes struct {
			Bids []json.RawMessage `json:"bids"`
			Asks []json.RawMessage `json:"asks"`
		} `json:"changes"`
		Time int64 `json:"time"` // milliseconds UTC
	}
	if err := json.Unmarshal(msg.Data, &data); err != nil {
		return ParsedUpdate{}, fmt.Errorf("parse l2 data: %w", err)
	}

	tsNano := data.Time * int64(time.Millisecond)

	var deltas []orderbook.Delta
	if err := parseLevels(data.Changes.Bids, orderbook.SideBid, tsNano, &deltas); err != nil {
		return ParsedUpdate{}, err
	}
	if err := parseLevels(data.Changes.Asks, orderbook.SideAsk, tsNano, &deltas); err != nil {
		return ParsedUpdate{}, err
	}

	return ParsedUpdate{Symbol: sym, Deltas: deltas}, nil
}

// parseLevels decodes KuCoin [price, size, seq] tuples and appends Deltas.
func parseLevels(raws []json.RawMessage, side orderbook.Side, tsNano int64, out *[]orderbook.Delta) error {
	for i, raw := range raws {
		var tuple [3]json.RawMessage
		if err := json.Unmarshal(raw, &tuple); err != nil {
			return fmt.Errorf("level %d: unmarshal tuple: %w", i, err)
		}

		var price, size string
		if err := json.Unmarshal(tuple[0], &price); err != nil {
			return fmt.Errorf("level %d: price: %w", i, err)
		}
		if err := json.Unmarshal(tuple[1], &size); err != nil {
			return fmt.Errorf("level %d: size: %w", i, err)
		}
		var seqRaw int64
		if err := json.Unmarshal(tuple[2], &seqRaw); err != nil {
			return fmt.Errorf("level %d: seq: %w", i, err)
		}

		*out = append(*out, orderbook.Delta{
			Seq:        uint64(seqRaw),
			Side:       side,
			Price:      price,
			Size:       size,
			TsExchange: tsNano,
		})
	}
	return nil
}

// parseTrade parses a KuCoin /market/match message.
//
// Wire format (data field):
//
//	{
//	  "sequence":    "1627940716440",
//	  "price":       "29500.00",
//	  "size":        "0.1",
//	  "side":        "buy",              // "buy" → "bid", "sell" → "ask"
//	  "time":        "1620000000000000000",  // nanoseconds as string
//	  "tradeId":     "...",
//	  "symbol":      "BTC-USDT"
//	}
func parseTrade(msg wireMessage) (ParsedTrade, error) {
	sym := symbolFromTopic(msg.Topic)

	var data struct {
		Sequence string `json:"sequence"`
		Price    string `json:"price"`
		Size     string `json:"size"`
		Side     string `json:"side"` // "buy" or "sell"
		Time     string `json:"time"` // nanosecond timestamp as string
	}
	if err := json.Unmarshal(msg.Data, &data); err != nil {
		return ParsedTrade{}, fmt.Errorf("parse trade data: %w", err)
	}

	seq, err := strconv.ParseUint(data.Sequence, 10, 64)
	if err != nil {
		return ParsedTrade{}, fmt.Errorf("parse sequence %q: %w", data.Sequence, err)
	}
	ts, err := strconv.ParseInt(data.Time, 10, 64)
	if err != nil {
		return ParsedTrade{}, fmt.Errorf("parse time %q: %w", data.Time, err)
	}

	var side string
	switch data.Side {
	case "buy":
		side = "bid"
	case "sell":
		side = "ask"
	default:
		return ParsedTrade{}, fmt.Errorf("unknown trade side %q", data.Side)
	}

	return ParsedTrade{
		Symbol:     sym,
		Seq:        seq,
		Price:      data.Price,
		Size:       data.Size,
		Side:       side,
		TsExchange: ts,
	}, nil
}

// symbolFromTopic extracts and normalises the symbol from a KuCoin topic string.
// "/market/level2:BTC-USDT" → Symbol("BTC-USDT")
func symbolFromTopic(topic string) symbol.Symbol {
	if i := strings.LastIndex(topic, ":"); i >= 0 {
		return symbol.Normalize("kucoin", topic[i+1:])
	}
	return symbol.Symbol(strings.ToUpper(topic))
}

// isL2Topic returns true for /market/level2 topics.
func isL2Topic(topic string) bool {
	return strings.HasPrefix(topic, "/market/level2:")
}

// isTradesTopic returns true for /market/match topics.
func isTradesTopic(topic string) bool {
	return strings.HasPrefix(topic, "/market/match:")
}
