// Package orderbook maintains a per-symbol L2 order book state machine.
// Pure: zero IO, no time.Now(), no goroutines. Single-goroutine ownership.
// Types mirror consumer.* to avoid import cycles — the consumer package
// provides thin adapters to satisfy its BookApplier interface.
package orderbook

import (
	"math"
	"sort"
	"strconv"
)

// Tick mirrors consumer.Tick fields needed by the OB.
type Tick struct {
	Seq   int64
	TsMs  int64
	Price string
	Size  string
	Side  string
	Level int
}

// SnapshotEvent mirrors consumer.SnapshotEvent.
type SnapshotEvent struct {
	Seq  int64
	TsMs int64
}

// GapMarker mirrors consumer.GapMarker.
type GapMarker struct {
	SeqBefore int64
	SeqAfter  int64
	GapCause  string
	GapTsMs   int64
	Exchange  string
	Symbol    string
}

// GapEmitter is called when the OB needs to emit a synthetic gap event.
type GapEmitter func(GapMarker)

// OrderBook maintains a per-symbol L2 book.
type OrderBook struct {
	exchange string
	symbol   string

	bids map[string]string // price → size
	asks map[string]string // price → size

	snapshotSeen bool
	snapSeq      int64

	coldBuf    []Tick
	maxColdBuf int
	maxColdSeq int64 // max seq seen in cold buffer

	emitGap GapEmitter
}

// New creates an OrderBook.
func New(exchange, symbol string, coldBufSize int, emitGap GapEmitter) *OrderBook {
	if emitGap == nil {
		emitGap = func(GapMarker) {}
	}
	return &OrderBook{
		exchange:   exchange,
		symbol:     symbol,
		bids:       make(map[string]string),
		asks:       make(map[string]string),
		maxColdBuf: coldBufSize,
		emitGap:    emitGap,
	}
}

// ApplyTick handles OB delta ticks (Level > 0) and ignores trade ticks (Level == 0).
func (ob *OrderBook) ApplyTick(t Tick) {
	if t.Level == 0 {
		// Trade tick — belongs to accumulator, not the OB.
		return
	}

	if !ob.snapshotSeen {
		ob.bufferDelta(t)
		return
	}

	ob.applyDelta(t)
}

// ApplySnapshot resets the book and replays buffered cold-start deltas.
func (ob *OrderBook) ApplySnapshot(s SnapshotEvent) {
	if ob.snapshotSeen {
		// Second snapshot: emit gap, full reinit.
		ob.emitGap(GapMarker{
			SeqBefore: ob.snapSeq,
			SeqAfter:  s.Seq,
			GapCause:  "snapshot_superseded",
			Exchange:  ob.exchange,
			Symbol:    ob.symbol,
			GapTsMs:   s.TsMs,
		})
		ob.bids = make(map[string]string)
		ob.asks = make(map[string]string)
		ob.snapSeq = s.Seq
		ob.coldBuf = nil
		ob.maxColdSeq = 0
		return
	}

	ob.snapshotSeen = true
	ob.snapSeq = s.Seq

	// Seq reset heuristic: if the snapshot seq is much lower than the max buffered
	// delta seq, the exchange reset its sequence counter (e.g. Bybit reconnect).
	if ob.maxColdSeq > 0 && s.Seq < ob.maxColdSeq/2 {
		ob.emitGap(GapMarker{
			SeqBefore: ob.maxColdSeq,
			SeqAfter:  s.Seq,
			GapCause:  "seq_reset",
			Exchange:  ob.exchange,
			Symbol:    ob.symbol,
			GapTsMs:   s.TsMs,
		})
		ob.snapshotSeen = false
		ob.snapSeq = 0
		ob.coldBuf = nil
		ob.maxColdSeq = 0
		ob.bids = make(map[string]string)
		ob.asks = make(map[string]string)
		return
	}

	// Start clean, then replay buffered deltas with seq > snapSeq.
	ob.bids = make(map[string]string)
	ob.asks = make(map[string]string)
	for _, t := range ob.coldBuf {
		if t.Seq > ob.snapSeq {
			ob.applyDelta(t)
		}
	}
	ob.coldBuf = nil
	ob.maxColdSeq = 0
}

// ApplyGap resets the book and cold-start buffer.
func (ob *OrderBook) ApplyGap(g GapMarker) {
	ob.bids = make(map[string]string)
	ob.asks = make(map[string]string)
	ob.coldBuf = nil
	ob.maxColdSeq = 0
	ob.snapshotSeen = false
	ob.snapSeq = 0
}

// BestQuote returns the best bid and ask prices and sizes.
// Returns ("", "", "", "") if either side of the book is empty.
func (ob *OrderBook) BestQuote() (bestBidPrice, bestBidSize, bestAskPrice, bestAskSize string) {
	if len(ob.bids) == 0 || len(ob.asks) == 0 {
		return "", "", "", ""
	}

	bestBidVal := math.Inf(-1)
	for price, size := range ob.bids {
		f, err := strconv.ParseFloat(price, 64)
		if err != nil {
			continue
		}
		if f > bestBidVal {
			bestBidVal = f
			bestBidPrice = price
			bestBidSize = size
		}
	}

	bestAskVal := math.Inf(1)
	for price, size := range ob.asks {
		f, err := strconv.ParseFloat(price, 64)
		if err != nil {
			continue
		}
		if f < bestAskVal {
			bestAskVal = f
			bestAskPrice = price
			bestAskSize = size
		}
	}

	if bestBidPrice == "" || bestAskPrice == "" {
		return "", "", "", ""
	}
	return bestBidPrice, bestBidSize, bestAskPrice, bestAskSize
}

// TopNDepth returns the sum of sizes for the top N price levels on the given side.
// Side must be "bid" or "ask". Returns 0 for invalid side, empty book, or unparseable sizes.
// If fewer than N levels are available, sums all available levels.
func (ob *OrderBook) TopNDepth(n int, side string) float64 {
	var m map[string]string
	switch side {
	case "bid":
		m = ob.bids
	case "ask":
		m = ob.asks
	default:
		return 0
	}

	type priceLevel struct {
		price float64
		size  float64
	}

	levels := make([]priceLevel, 0, len(m))
	for price, size := range m {
		p, err := strconv.ParseFloat(price, 64)
		if err != nil {
			continue
		}
		s, err := strconv.ParseFloat(size, 64)
		if err != nil {
			continue
		}
		levels = append(levels, priceLevel{p, s})
	}

	if side == "bid" {
		sort.Slice(levels, func(i, j int) bool { return levels[i].price > levels[j].price })
	} else {
		sort.Slice(levels, func(i, j int) bool { return levels[i].price < levels[j].price })
	}

	count := n
	if count > len(levels) {
		count = len(levels)
	}
	total := 0.0
	for i := 0; i < count; i++ {
		total += levels[i].size
	}
	return total
}

// AllBids returns a copy of the current bid book (price → size).
// The caller may freely mutate the returned map — it does not affect OB state.
func (ob *OrderBook) AllBids() map[string]string {
	out := make(map[string]string, len(ob.bids))
	for k, v := range ob.bids {
		out[k] = v
	}
	return out
}

// AllAsks returns a copy of the current ask book (price → size).
// The caller may freely mutate the returned map — it does not affect OB state.
func (ob *OrderBook) AllAsks() map[string]string {
	out := make(map[string]string, len(ob.asks))
	for k, v := range ob.asks {
		out[k] = v
	}
	return out
}

// HasLevel returns true if the given side ("buy"/"sell") has a non-zero level at price.
// Must be called BEFORE ApplyTick to get pre-update state for OBEventKind derivation.
func (ob *OrderBook) HasLevel(side, price string) bool {
	if side == "buy" {
		_, ok := ob.bids[price]
		return ok
	}
	_, ok := ob.asks[price]
	return ok
}

// applyDelta applies a single OB update to bids or asks.
func (ob *OrderBook) applyDelta(t Tick) {
	var m map[string]string
	if t.Side == "buy" {
		m = ob.bids
	} else {
		m = ob.asks
	}

	if t.Size == "0" {
		// Zero size = remove level. No-op if level not present.
		delete(m, t.Price)
		return
	}
	m[t.Price] = t.Size
}

// bufferDelta adds a delta to the cold-start buffer.
func (ob *OrderBook) bufferDelta(t Tick) {
	if t.Seq > ob.maxColdSeq {
		ob.maxColdSeq = t.Seq
	}
	ob.coldBuf = append(ob.coldBuf, t)

	if len(ob.coldBuf) >= ob.maxColdBuf {
		ob.emitGap(GapMarker{
			SeqBefore: ob.maxColdSeq,
			GapCause:  "cold_start_buffer_overflow",
			Exchange:  ob.exchange,
			Symbol:    ob.symbol,
			GapTsMs:   t.TsMs,
		})
		ob.coldBuf = nil
		ob.maxColdSeq = 0
	}
}
