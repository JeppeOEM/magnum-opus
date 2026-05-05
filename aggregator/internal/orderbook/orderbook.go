// Package orderbook implements a pure L2 order book state machine.
// No IO, no imports from other internal packages — testable at L1 with zero mocks.
package orderbook

// Side represents the bid or ask side of the order book.
type Side uint8

const (
	SideBid Side = iota
	SideAsk
)

// Delta is a single L2 order book update from the exchange.
// price and size are strings to preserve exact wire precision (never float64).
type Delta struct {
	Seq        uint64
	Side       Side
	Price      string // exact string from wire, e.g. "29500.50"
	Size       string // "0" means remove this price level
	TsExchange int64  // nanosecond unix timestamp from exchange
}

// Level represents a single price level in the book.
type Level struct {
	Price string
	Size  string
}

// Snapshot is a point-in-time copy of the full book state.
// Mutations to Snapshot do not affect the OrderBook.
type Snapshot struct {
	Seq  uint64
	Bids map[string]string // price → size
	Asks map[string]string // price → size
}

// OrderBook maintains an in-memory L2 order book for one symbol.
// Not safe for concurrent use — each symbol has its own goroutine.
type OrderBook struct {
	lastSeq uint64
	bids    map[string]string
	asks    map[string]string
}

// New returns an initialized, empty OrderBook.
func New() *OrderBook {
	return &OrderBook{
		bids: make(map[string]string),
		asks: make(map[string]string),
	}
}

// Apply applies a delta to the book.
//
// Out-of-order and duplicate deltas (seq ≤ lastSeq) are silently discarded
// per FR14/FR15 — no error is returned for either case.
//
// A size of "0" removes the price level from the book.
func (ob *OrderBook) Apply(d Delta) {
	if d.Seq <= ob.lastSeq {
		// Duplicate (==) or out-of-order (<): discard silently.
		return
	}
	// P1: unknown Side values are silently discarded — never route to a default side.
	// P2: empty Price would create an unreachable phantom map key — discard.
	if d.Side != SideBid && d.Side != SideAsk {
		return
	}
	if d.Price == "" {
		return
	}
	ob.lastSeq = d.Seq

	var levels map[string]string
	if d.Side == SideBid {
		levels = ob.bids
	} else {
		levels = ob.asks
	}

	if d.Size == "0" || d.Size == "" {
		delete(levels, d.Price)
	} else {
		levels[d.Price] = d.Size
	}
}

// Reset clears all book state and resets the sequence counter.
// Called when a snapshot is being applied after a reconnect.
func (ob *OrderBook) Reset() {
	ob.lastSeq = 0
	ob.bids = make(map[string]string)
	ob.asks = make(map[string]string)
}

// ApplySnapshot replaces the full book state with a REST snapshot.
// seq is the sequence number of the snapshot.
func (ob *OrderBook) ApplySnapshot(seq uint64, bids, asks map[string]string) {
	ob.lastSeq = seq
	ob.bids = make(map[string]string, len(bids))
	for p, s := range bids {
		ob.bids[p] = s
	}
	ob.asks = make(map[string]string, len(asks))
	for p, s := range asks {
		ob.asks[p] = s
	}
}

// LastSeq returns the sequence number of the last applied update.
func (ob *OrderBook) LastSeq() uint64 { return ob.lastSeq }

// Snapshot returns a deep-copy of the current book state.
// The returned Snapshot is independent — mutations do not affect the book.
func (ob *OrderBook) Snapshot() Snapshot {
	bids := make(map[string]string, len(ob.bids))
	asks := make(map[string]string, len(ob.asks))
	for p, s := range ob.bids {
		bids[p] = s
	}
	for p, s := range ob.asks {
		asks[p] = s
	}
	return Snapshot{
		Seq:  ob.lastSeq,
		Bids: bids,
		Asks: asks,
	}
}
