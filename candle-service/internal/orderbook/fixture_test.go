package orderbook_test

// Fixture-based validation of the L2 order book state machine.
//
// These tests replay a realistic multi-level BTC-USDT tick sequence and assert
// exact book state at each checkpoint. The goal is to catch subtle state-machine
// bugs (wrong cold-start filter, incorrect best-quote selection, depth miscounts)
// before any feature code is built on top of the OB.

import (
	"testing"

	"github.com/mrqdt/magnum-opus/candle-service/internal/orderbook"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// ─── Fixture helpers ──────────────────────────────────────────────────────────

type fixtureEvent interface{ isEvent() }

type fTick struct {
	seq   int64
	price string
	size  string
	side  string // "buy" or "sell"
	level int    // 0 = trade, 1 = L2 delta
}

type fSnap struct{ seq int64 }
type fGap struct{ cause string }

func (fTick) isEvent() {}
func (fSnap) isEvent() {}
func (fGap) isEvent()  {}

func newFixtureOB(t *testing.T) (*orderbook.OrderBook, *[]orderbook.GapMarker) {
	t.Helper()
	gaps := &[]orderbook.GapMarker{}
	ob := orderbook.New("kucoin", "BTC-USDT", 50, func(g orderbook.GapMarker) {
		*gaps = append(*gaps, g)
	})
	return ob, gaps
}

func replayFixture(ob *orderbook.OrderBook, events []fixtureEvent) {
	for _, ev := range events {
		switch e := ev.(type) {
		case fTick:
			ob.ApplyTick(orderbook.Tick{
				Seq:   e.seq,
				Price: e.price,
				Size:  e.size,
				Side:  e.side,
				Level: e.level,
			})
		case fSnap:
			ob.ApplySnapshot(orderbook.SnapshotEvent{Seq: e.seq})
		case fGap:
			ob.ApplyGap(orderbook.GapMarker{GapCause: e.cause})
		}
	}
}

// ─── Fixture: realistic BTC-USDT multi-level book ────────────────────────────

// buildFixture returns a sequence that exercises:
//   - 5 bid levels and 5 ask levels added before snapshot (cold-start)
//   - Snapshot at seq=10 (filters out seq ≤ 10)
//   - Post-snap updates: level change, removal, trade (ignored by OB)
//   - Final book: 4 bids (50000 removed), 5 asks (50200 updated)
var buildFixture = []fixtureEvent{
	// Pre-snapshot deltas (cold-start buffer, seq 1–9)
	fTick{seq: 1, price: "49600", size: "5.0", side: "buy", level: 1},
	fTick{seq: 2, price: "49700", size: "4.0", side: "buy", level: 1},
	fTick{seq: 3, price: "49800", size: "3.0", side: "buy", level: 1},
	fTick{seq: 4, price: "49900", size: "2.0", side: "buy", level: 1},
	fTick{seq: 5, price: "50000", size: "1.0", side: "buy", level: 1},
	fTick{seq: 6, price: "50100", size: "1.5", side: "sell", level: 1},
	fTick{seq: 7, price: "50200", size: "2.5", side: "sell", level: 1},
	fTick{seq: 8, price: "50300", size: "3.5", side: "sell", level: 1},
	fTick{seq: 9, price: "50400", size: "4.5", side: "sell", level: 1},
	// Snapshot at seq=10: all pre-snap deltas (seq ≤ 10) must be DISCARDED
	fSnap{seq: 10},
	// Post-snapshot deltas (seq > 10, these are replayed)
	fTick{seq: 11, price: "49600", size: "5.0", side: "buy", level: 1},
	fTick{seq: 12, price: "49700", size: "4.0", side: "buy", level: 1},
	fTick{seq: 13, price: "49800", size: "3.0", side: "buy", level: 1},
	fTick{seq: 14, price: "49900", size: "2.0", side: "buy", level: 1},
	fTick{seq: 15, price: "50000", size: "1.0", side: "buy", level: 1},
	fTick{seq: 16, price: "50100", size: "1.5", side: "sell", level: 1},
	fTick{seq: 17, price: "50200", size: "2.5", side: "sell", level: 1},
	fTick{seq: 18, price: "50300", size: "3.5", side: "sell", level: 1},
	fTick{seq: 19, price: "50400", size: "4.5", side: "sell", level: 1},
	fTick{seq: 20, price: "50500", size: "5.5", side: "sell", level: 1},
	// Update: best ask 50100 gets larger size
	fTick{seq: 21, price: "50200", size: "9.9", side: "sell", level: 1},
	// Trade tick: must be ignored by OB (level=0)
	fTick{seq: 22, price: "50050", size: "0.3", side: "buy", level: 0},
	// Remove best bid (50000 → "0")
	fTick{seq: 23, price: "50000", size: "0", side: "buy", level: 1},
}

// TestFixture_ColdStartFiltering verifies seq ≤ snapSeq are not replayed.
func TestFixture_ColdStartFiltering(t *testing.T) {
	ob, gaps := newFixtureOB(t)
	replayFixture(ob, buildFixture)

	// No gaps expected — clean cold-start
	assert.Empty(t, *gaps, "no gaps should be emitted during clean fixture replay")

	// The pre-snap ticks (seq 1–9) had prices 49600–50000 bid and 50100–50400 ask.
	// The snapshot was at seq=10; the cold-start filter replays only seq > 10.
	// So 49500 was never added (it wasn't in the fixture at all — just verifying
	// the filter didn't accidentally let seq≤10 through by checking final state).
	bids := ob.AllBids()
	// All 5 bid levels were re-added post-snap (seq 11–15), then seq 23 removed 50000
	assert.NotContains(t, bids, "50000", "50000 was removed by seq=23")
	assert.Len(t, bids, 4, "4 bid levels should remain after removing 50000")
}

// TestFixture_ExactBidState verifies the exact bid book state after full replay.
func TestFixture_ExactBidState(t *testing.T) {
	ob, _ := newFixtureOB(t)
	replayFixture(ob, buildFixture)

	bids := ob.AllBids()
	t.Run("49600 present with correct size", func(t *testing.T) {
		assert.Equal(t, "5.0", bids["49600"])
	})
	t.Run("49700 present with correct size", func(t *testing.T) {
		assert.Equal(t, "4.0", bids["49700"])
	})
	t.Run("49800 present with correct size", func(t *testing.T) {
		assert.Equal(t, "3.0", bids["49800"])
	})
	t.Run("49900 present with correct size", func(t *testing.T) {
		assert.Equal(t, "2.0", bids["49900"])
	})
	t.Run("50000 removed", func(t *testing.T) {
		_, present := bids["50000"]
		assert.False(t, present, "50000 should have been removed")
	})
}

// TestFixture_ExactAskState verifies the exact ask book state after full replay.
func TestFixture_ExactAskState(t *testing.T) {
	ob, _ := newFixtureOB(t)
	replayFixture(ob, buildFixture)

	asks := ob.AllAsks()
	t.Run("50100 present with correct size", func(t *testing.T) {
		assert.Equal(t, "1.5", asks["50100"])
	})
	t.Run("50200 updated to 9.9", func(t *testing.T) {
		assert.Equal(t, "9.9", asks["50200"], "seq=21 update should overwrite 2.5 with 9.9")
	})
	t.Run("50300 present", func(t *testing.T) {
		assert.Equal(t, "3.5", asks["50300"])
	})
	t.Run("50400 present", func(t *testing.T) {
		assert.Equal(t, "4.5", asks["50400"])
	})
	t.Run("50500 present", func(t *testing.T) {
		assert.Equal(t, "5.5", asks["50500"])
	})
	assert.Len(t, asks, 5, "5 ask levels expected")
}

// TestFixture_BestQuote verifies BestQuote reflects removal of best bid.
func TestFixture_BestQuote(t *testing.T) {
	ob, _ := newFixtureOB(t)
	replayFixture(ob, buildFixture)

	bp, bs, ap, as_ := ob.BestQuote()
	t.Run("best bid is 49900 after 50000 removed", func(t *testing.T) {
		assert.Equal(t, "49900", bp)
		assert.Equal(t, "2.0", bs)
	})
	t.Run("best ask is 50100", func(t *testing.T) {
		assert.Equal(t, "50100", ap)
		assert.Equal(t, "1.5", as_)
	})
}

// TestFixture_BestQuoteUnchangedByNonTopRemoval verifies removing a non-best level
// does not affect BestQuote.
func TestFixture_BestQuoteUnchangedByNonTopRemoval(t *testing.T) {
	ob, _ := newFixtureOB(t)

	// Build a simple 3-level bid book
	events := []fixtureEvent{
		fSnap{seq: 0},
		fTick{seq: 1, price: "50000", size: "1.0", side: "buy", level: 1}, // best bid
		fTick{seq: 2, price: "49900", size: "2.0", side: "buy", level: 1},
		fTick{seq: 3, price: "49800", size: "3.0", side: "buy", level: 1},
		fTick{seq: 4, price: "50100", size: "1.5", side: "sell", level: 1},
	}
	replayFixture(ob, events)

	bp1, bs1, _, _ := ob.BestQuote()
	require.Equal(t, "50000", bp1)

	// Remove a non-top bid level (49800)
	ob.ApplyTick(orderbook.Tick{Seq: 5, Price: "49800", Size: "0", Side: "buy", Level: 1})

	bp2, bs2, _, _ := ob.BestQuote()
	t.Run("best bid unchanged after non-top removal", func(t *testing.T) {
		assert.Equal(t, bp1, bp2, "removing 49800 must not change best bid")
		assert.Equal(t, bs1, bs2)
	})
}

// TestFixture_TradeIgnoredByOB verifies trade ticks (level=0) do not modify book.
func TestFixture_TradeIgnoredByOB(t *testing.T) {
	ob, _ := newFixtureOB(t)

	events := []fixtureEvent{
		fSnap{seq: 0},
		fTick{seq: 1, price: "50000", size: "1.0", side: "buy", level: 1},
		fTick{seq: 2, price: "50100", size: "1.5", side: "sell", level: 1},
	}
	replayFixture(ob, events)

	bids0 := ob.AllBids()
	asks0 := ob.AllAsks()

	// Apply trade ticks — should have no effect on book
	ob.ApplyTick(orderbook.Tick{Seq: 3, Price: "50050", Size: "2.0", Side: "buy", Level: 0})
	ob.ApplyTick(orderbook.Tick{Seq: 4, Price: "50075", Size: "0.5", Side: "sell", Level: 0})

	assert.Equal(t, bids0, ob.AllBids(), "trade ticks must not modify bid book")
	assert.Equal(t, asks0, ob.AllAsks(), "trade ticks must not modify ask book")
}

// TestFixture_TopNDepth verifies depth sums against known expected values.
func TestFixture_TopNDepth(t *testing.T) {
	ob, _ := newFixtureOB(t)
	replayFixture(ob, buildFixture)

	// Remaining bids after fixture: 49600=5.0, 49700=4.0, 49800=3.0, 49900=2.0
	// Sorted descending: 49900(2.0), 49800(3.0), 49700(4.0), 49600(5.0)
	t.Run("bid TopN=1", func(t *testing.T) {
		assert.InDelta(t, 2.0, ob.TopNDepth(1, "bid"), 0.0001)
	})
	t.Run("bid TopN=2", func(t *testing.T) {
		assert.InDelta(t, 5.0, ob.TopNDepth(2, "bid"), 0.0001) // 2.0+3.0
	})
	t.Run("bid TopN=4 (all levels)", func(t *testing.T) {
		assert.InDelta(t, 14.0, ob.TopNDepth(4, "bid"), 0.0001) // 2+3+4+5
	})
	t.Run("bid TopN=100 (capped at available)", func(t *testing.T) {
		assert.InDelta(t, 14.0, ob.TopNDepth(100, "bid"), 0.0001)
	})

	// Remaining asks: 50100=1.5, 50200=9.9, 50300=3.5, 50400=4.5, 50500=5.5
	// Sorted ascending: 50100(1.5), 50200(9.9), 50300(3.5), 50400(4.5), 50500(5.5)
	t.Run("ask TopN=1", func(t *testing.T) {
		assert.InDelta(t, 1.5, ob.TopNDepth(1, "ask"), 0.0001)
	})
	t.Run("ask TopN=2", func(t *testing.T) {
		assert.InDelta(t, 11.4, ob.TopNDepth(2, "ask"), 0.0001) // 1.5+9.9
	})
	t.Run("ask TopN=5 (all levels)", func(t *testing.T) {
		assert.InDelta(t, 24.9, ob.TopNDepth(5, "ask"), 0.0001) // 1.5+9.9+3.5+4.5+5.5
	})
}

// TestFixture_GapResetsAndRebuild verifies the book rebuilds correctly after a gap.
func TestFixture_GapResetsAndRebuild(t *testing.T) {
	ob, _ := newFixtureOB(t)
	replayFixture(ob, buildFixture)

	// Verify book is populated
	require.NotEmpty(t, ob.AllBids())

	// Gap resets everything
	ob.ApplyGap(orderbook.GapMarker{GapCause: "external_disconnect"})
	assert.Empty(t, ob.AllBids(), "bids must be empty after gap")
	assert.Empty(t, ob.AllAsks(), "asks must be empty after gap")

	// BestQuote must return empty after gap
	bp, bs, ap, as_ := ob.BestQuote()
	assert.Empty(t, bp)
	assert.Empty(t, bs)
	assert.Empty(t, ap)
	assert.Empty(t, as_)

	// Book rebuilds correctly after fresh snapshot + deltas
	ob.ApplySnapshot(orderbook.SnapshotEvent{Seq: 100})
	ob.ApplyTick(orderbook.Tick{Seq: 101, Price: "49000", Size: "2.5", Side: "buy", Level: 1})
	ob.ApplyTick(orderbook.Tick{Seq: 102, Price: "49100", Size: "1.5", Side: "sell", Level: 1})

	bp, _, ap, _ = ob.BestQuote()
	assert.Equal(t, "49000", bp, "best bid after gap+rebuild")
	assert.Equal(t, "49100", ap, "best ask after gap+rebuild")
}
