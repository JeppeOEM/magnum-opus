package orderbook_test

import (
	"testing"

	"github.com/mrqdt/magnum-opus/candle-service/internal/orderbook"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func newOB(t *testing.T) (*orderbook.OrderBook, *[]orderbook.GapMarker) {
	t.Helper()
	gaps := &[]orderbook.GapMarker{}
	ob := orderbook.New("kucoin", "BTC-USDT", 10, func(g orderbook.GapMarker) {
		*gaps = append(*gaps, g)
	})
	return ob, gaps
}

func snap(seq int64) orderbook.SnapshotEvent { return orderbook.SnapshotEvent{Seq: seq} }

func bidTick(seq int64, price, size string) orderbook.Tick {
	return orderbook.Tick{Seq: seq, Price: price, Size: size, Side: "buy", Level: 1}
}

func askTick(seq int64, price, size string) orderbook.Tick {
	return orderbook.Tick{Seq: seq, Price: price, Size: size, Side: "sell", Level: 1}
}

func tradeTick(seq int64, price, size string) orderbook.Tick {
	return orderbook.Tick{Seq: seq, Price: price, Size: size, Side: "buy", Level: 0}
}

// ─── Basic delta operations ───────────────────────────────────────────────────

func TestApplyTick_UpdatesLevel(t *testing.T) {
	ob, _ := newOB(t)
	ob.ApplySnapshot(snap(0))
	ob.ApplyTick(bidTick(1, "50000", "1.5"))
	ob.ApplyTick(askTick(2, "50100", "0.5"))
	_, size, _, _ := ob.BestQuote()
	assert.Equal(t, "1.5", size)
}

func TestApplyTick_RemovesLevelOnZeroSize(t *testing.T) {
	ob, _ := newOB(t)
	ob.ApplySnapshot(snap(0))
	ob.ApplyTick(bidTick(1, "50000", "1.5"))
	ob.ApplyTick(bidTick(2, "50000", "0"))
	bp, bs, _, _ := ob.BestQuote()
	assert.Empty(t, bp, "bid level should be removed")
	assert.Empty(t, bs)
}

func TestApplyTick_ZeroSizeUnknownLevel_NoOp(t *testing.T) {
	ob, _ := newOB(t)
	ob.ApplySnapshot(snap(0))
	// Remove a price level that was never added — should not panic or create level.
	ob.ApplyTick(bidTick(1, "49000", "0"))
	bp, _, _, _ := ob.BestQuote()
	assert.Empty(t, bp, "unknown level removal should not create a level")
}

func TestApplyTick_TradeIgnored(t *testing.T) {
	ob, _ := newOB(t)
	ob.ApplySnapshot(snap(0))
	ob.ApplyTick(tradeTick(1, "50000", "0.1")) // level==0
	bp, _, _, _ := ob.BestQuote()
	assert.Empty(t, bp, "trade tick must not affect book")
}

func TestApplySnapshot_ResetsBook(t *testing.T) {
	ob, _ := newOB(t)
	ob.ApplySnapshot(snap(0))
	ob.ApplyTick(bidTick(1, "50000", "2.0"))
	ob.ApplyTick(askTick(2, "50100", "1.0"))

	// Second snapshot should wipe the book and emit snapshot_superseded gap.
	ob.ApplySnapshot(snap(10))
	bp, _, ap, _ := ob.BestQuote()
	assert.Empty(t, bp, "bids should be cleared by new snapshot")
	assert.Empty(t, ap, "asks should be cleared by new snapshot")
}

func TestApplySnapshot_SecondSnapshot_EmitsGap(t *testing.T) {
	ob, gaps := newOB(t)
	ob.ApplySnapshot(snap(0))
	ob.ApplySnapshot(snap(10)) // second snapshot
	require.Len(t, *gaps, 1)
	assert.Equal(t, "snapshot_superseded", (*gaps)[0].GapCause)
}

// ─── BestQuote ────────────────────────────────────────────────────────────────

func TestBestQuote_EmptyBook(t *testing.T) {
	ob, _ := newOB(t)
	ob.ApplySnapshot(snap(0))
	bp, bs, ap, as_ := ob.BestQuote()
	assert.Empty(t, bp)
	assert.Empty(t, bs)
	assert.Empty(t, ap)
	assert.Empty(t, as_)
}

func TestBestQuote_AfterUpdates(t *testing.T) {
	ob, _ := newOB(t)
	ob.ApplySnapshot(snap(0))
	ob.ApplyTick(bidTick(1, "49900", "1.0"))
	ob.ApplyTick(bidTick(2, "50000", "2.0")) // higher bid = best
	ob.ApplyTick(askTick(3, "50200", "3.0"))
	ob.ApplyTick(askTick(4, "50100", "1.5")) // lower ask = best

	bp, bs, ap, as_ := ob.BestQuote()
	assert.Equal(t, "50000", bp, "best bid should be highest bid price")
	assert.Equal(t, "2.0", bs)
	assert.Equal(t, "50100", ap, "best ask should be lowest ask price")
	assert.Equal(t, "1.5", as_)
}

func TestBestQuote_OneSideEmpty(t *testing.T) {
	ob, _ := newOB(t)
	ob.ApplySnapshot(snap(0))
	ob.ApplyTick(bidTick(1, "50000", "1.0"))
	// No asks added.
	bp, bs, ap, as_ := ob.BestQuote()
	assert.Empty(t, bp, "one empty side returns all empty")
	assert.Empty(t, bs)
	assert.Empty(t, ap)
	assert.Empty(t, as_)
}

// ─── TopNDepth ────────────────────────────────────────────────────────────────

func TestTopNDepth_FewerLevelsThanN(t *testing.T) {
	ob, _ := newOB(t)
	ob.ApplySnapshot(snap(0))
	ob.ApplyTick(bidTick(1, "50000", "1.0"))
	// Request top 5 bids but only 1 exists.
	depth := ob.TopNDepth(5, "bid")
	assert.InDelta(t, 1.0, depth, 0.0001)
}

func TestTopNDepth_BidsSortedDescending(t *testing.T) {
	ob, _ := newOB(t)
	ob.ApplySnapshot(snap(0))
	ob.ApplyTick(bidTick(1, "49900", "3.0"))
	ob.ApplyTick(bidTick(2, "50000", "2.0"))
	ob.ApplyTick(bidTick(3, "49800", "1.0"))
	// Top 2 bids: 50000 (2.0) + 49900 (3.0) = 5.0
	depth := ob.TopNDepth(2, "bid")
	assert.InDelta(t, 5.0, depth, 0.0001)
}

func TestTopNDepth_AsksSortedAscending(t *testing.T) {
	ob, _ := newOB(t)
	ob.ApplySnapshot(snap(0))
	ob.ApplyTick(askTick(1, "50200", "3.0"))
	ob.ApplyTick(askTick(2, "50100", "2.0"))
	ob.ApplyTick(askTick(3, "50300", "1.0"))
	// Top 2 asks: 50100 (2.0) + 50200 (3.0) = 5.0
	depth := ob.TopNDepth(2, "ask")
	assert.InDelta(t, 5.0, depth, 0.0001)
}

// ─── Cold-start buffer ────────────────────────────────────────────────────────

func TestColdStart_BufferReplay(t *testing.T) {
	ob, _ := newOB(t)
	// Pre-snapshot deltas buffered.
	ob.ApplyTick(bidTick(5, "50000", "1.0"))
	ob.ApplyTick(bidTick(6, "50100", "2.0"))
	ob.ApplyTick(askTick(6, "50200", "1.0")) // need ask side for BestQuote

	// Snapshot at seq=4: all buffered deltas have seq > 4, so all replayed.
	ob.ApplySnapshot(snap(4))

	bp, _, _, _ := ob.BestQuote()
	assert.Equal(t, "50100", bp, "buffered deltas seq>snap should be replayed")
}

func TestColdStart_BufferReplay_OnlyAfterSnap(t *testing.T) {
	ob, _ := newOB(t)
	ob.ApplyTick(bidTick(3, "49000", "1.0")) // seq <= snap, should be discarded
	ob.ApplyTick(bidTick(7, "50000", "2.0")) // seq > snap, replayed
	ob.ApplyTick(askTick(7, "50100", "1.0")) // ask side for BestQuote

	ob.ApplySnapshot(snap(5))

	bp, _, _, _ := ob.BestQuote()
	assert.Equal(t, "50000", bp, "only deltas with seq > snapSeq replayed")
}

func TestColdStart_BufferOverflow_EmitsGap(t *testing.T) {
	ob, gaps := newOB(t) // maxColdBuf = 10
	for i := 0; i < 10; i++ {
		ob.ApplyTick(bidTick(int64(i+1), "50000", "1.0"))
	}
	require.Len(t, *gaps, 1)
	assert.Equal(t, "cold_start_buffer_overflow", (*gaps)[0].GapCause)
}

func TestColdStart_SeqReset_EmitsGap(t *testing.T) {
	ob, gaps := newOB(t)
	ob.ApplyTick(bidTick(1000, "50000", "1.0")) // maxColdSeq = 1000

	// Snapshot with seq=1 (much less than 1000/2=500) → seq reset.
	ob.ApplySnapshot(snap(1))

	require.Len(t, *gaps, 1)
	assert.Equal(t, "seq_reset", (*gaps)[0].GapCause)
}

func TestColdStart_SeqReset_ReturnsToBuffering(t *testing.T) {
	ob, gaps := newOB(t)
	ob.ApplyTick(bidTick(1000, "50000", "1.0")) // maxColdSeq = 1000

	ob.ApplySnapshot(snap(1)) // seq reset detected → snapshotSeen must be false again
	require.Len(t, *gaps, 1)

	// After seq_reset, OB should buffer new ticks (cold-start mode).
	ob.ApplyTick(bidTick(1001, "48000", "1.5"))
	ob.ApplyTick(askTick(1002, "49000", "0.5"))
	ob.ApplySnapshot(snap(1000)) // fresh valid snapshot

	bp, _, _, _ := ob.BestQuote()
	assert.Equal(t, "48000", bp, "after seq_reset OB should be in cold-start mode and replay new buffer")
}

func TestTopNDepth_InvalidSide_ReturnsZero(t *testing.T) {
	ob, _ := newOB(t)
	ob.ApplySnapshot(snap(0))
	ob.ApplyTick(bidTick(1, "50000", "1.0"))
	ob.ApplyTick(askTick(2, "50100", "1.0"))
	depth := ob.TopNDepth(5, "invalid")
	assert.Equal(t, 0.0, depth)
}

// ─── AllBids / AllAsks ────────────────────────────────────────────────────────

func TestAllBids_ReturnsCopy(t *testing.T) {
	ob, _ := newOB(t)
	ob.ApplySnapshot(snap(0))
	ob.ApplyTick(bidTick(1, "50000", "1.0"))

	bids := ob.AllBids()
	bids["50000"] = "999" // mutate the returned copy

	// Internal state must be unchanged.
	bids2 := ob.AllBids()
	assert.Equal(t, "1.0", bids2["50000"], "AllBids must return a copy; mutation must not affect OB state")
}

func TestAllAsks_ReturnsCopy(t *testing.T) {
	ob, _ := newOB(t)
	ob.ApplySnapshot(snap(0))
	ob.ApplyTick(askTick(1, "50100", "2.0"))

	asks := ob.AllAsks()
	asks["50100"] = "999"

	asks2 := ob.AllAsks()
	assert.Equal(t, "2.0", asks2["50100"], "AllAsks must return a copy; mutation must not affect OB state")
}

func TestAllBids_EmptyAfterGap(t *testing.T) {
	ob, _ := newOB(t)
	ob.ApplySnapshot(snap(0))
	ob.ApplyTick(bidTick(1, "50000", "1.0"))
	ob.ApplyGap(orderbook.GapMarker{GapCause: "external_disconnect"})
	assert.Empty(t, ob.AllBids(), "AllBids must be empty after gap reset")
}

func TestApplyGap_ResetsBook(t *testing.T) {
	ob, _ := newOB(t)
	ob.ApplySnapshot(snap(0))
	ob.ApplyTick(bidTick(1, "50000", "2.0"))

	ob.ApplyGap(orderbook.GapMarker{GapCause: "external_disconnect"})

	// Book should be reset and back to cold-start state.
	bp, _, _, _ := ob.BestQuote()
	assert.Empty(t, bp)

	// New snapshot should work cleanly.
	ob.ApplySnapshot(snap(2))
	ob.ApplyTick(bidTick(3, "49000", "1.5"))
	ob.ApplyTick(askTick(4, "49100", "0.5"))
	bp, bs, _, _ := ob.BestQuote()
	assert.Equal(t, "49000", bp)
	assert.Equal(t, "1.5", bs)
}

// ─── HasLevel ─────────────────────────────────────────────────────────────────

func TestOrderBook_HasLevel_KnownLevel(t *testing.T) {
	ob, _ := newOB(t)
	ob.ApplySnapshot(snap(0))
	ob.ApplyTick(bidTick(1, "50000", "1.5"))
	ob.ApplyTick(askTick(2, "50100", "0.8"))
	assert.True(t, ob.HasLevel("buy", "50000"), "known bid level must return true")
	assert.True(t, ob.HasLevel("sell", "50100"), "known ask level must return true")
}

func TestOrderBook_HasLevel_UnknownLevel(t *testing.T) {
	ob, _ := newOB(t)
	ob.ApplySnapshot(snap(0))
	ob.ApplyTick(bidTick(1, "50000", "1.5"))
	assert.False(t, ob.HasLevel("buy", "49999"), "unknown price → false")
	assert.False(t, ob.HasLevel("sell", "50100"), "no ask levels → false")
}

func TestOrderBook_HasLevel_FalseBeforeSnapshot(t *testing.T) {
	ob, _ := newOB(t)
	// Buffered delta before snapshot: book is not in warm state, bids map is empty
	ob.ApplyTick(bidTick(1, "50000", "1.5"))
	assert.False(t, ob.HasLevel("buy", "50000"), "pre-snapshot cold buffer: warm book is empty")
}

func TestOrderBook_HasLevel_FalseAfterGap(t *testing.T) {
	ob, _ := newOB(t)
	ob.ApplySnapshot(snap(0))
	ob.ApplyTick(bidTick(1, "50000", "1.5"))
	require.True(t, ob.HasLevel("buy", "50000"), "precondition: level exists before gap")
	ob.ApplyGap(orderbook.GapMarker{GapCause: "external_disconnect"})
	assert.False(t, ob.HasLevel("buy", "50000"), "HasLevel must return false after ApplyGap resets book")
}
