package orderbook_test

import (
	"testing"

	"github.com/mrqdt/magnum-opus/aggregator/internal/orderbook"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func bid(seq uint64, price, size string) orderbook.Delta {
	return orderbook.Delta{Seq: seq, Side: orderbook.SideBid, Price: price, Size: size}
}

func ask(seq uint64, price, size string) orderbook.Delta {
	return orderbook.Delta{Seq: seq, Side: orderbook.SideAsk, Price: price, Size: size}
}

// TestApply_AddAndUpdate covers the normal forward-sequence case.
func TestApply_AddAndUpdate(t *testing.T) {
	ob := orderbook.New()

	ob.Apply(bid(1, "29500.00", "1.5"))
	ob.Apply(bid(2, "29400.00", "2.0"))
	ob.Apply(ask(3, "29600.00", "0.5"))

	snap := ob.Snapshot()
	assert.Equal(t, uint64(3), snap.Seq)
	assert.Equal(t, "1.5", snap.Bids["29500.00"])
	assert.Equal(t, "2.0", snap.Bids["29400.00"])
	assert.Equal(t, "0.5", snap.Asks["29600.00"])
}

// TestApply_RemoveLevel verifies size "0" deletes the price level.
func TestApply_RemoveLevel(t *testing.T) {
	ob := orderbook.New()

	ob.Apply(bid(1, "29500.00", "1.5"))
	ob.Apply(bid(2, "29500.00", "0")) // remove

	snap := ob.Snapshot()
	_, exists := snap.Bids["29500.00"]
	assert.False(t, exists, "price level with size 0 must be removed")
}

// TestApply_Duplicate verifies exact-duplicate seq is silently discarded.
// This is a distinct test row from the out-of-order case per the AC.
func TestApply_Duplicate(t *testing.T) {
	ob := orderbook.New()

	ob.Apply(bid(5, "29500.00", "1.0"))
	before := ob.Snapshot()

	// Exact duplicate — seq == lastSeq.
	ob.Apply(bid(5, "29500.00", "9.9"))

	after := ob.Snapshot()
	assert.Equal(t, before.Bids, after.Bids, "duplicate delta must not modify book state")
	assert.Equal(t, uint64(5), after.Seq)
}

// TestApply_OutOfOrder verifies seq < lastSeq is silently discarded.
func TestApply_OutOfOrder(t *testing.T) {
	ob := orderbook.New()

	ob.Apply(bid(10, "29500.00", "1.0"))
	before := ob.Snapshot()

	// Out-of-order: seq < lastSeq.
	ob.Apply(bid(7, "29500.00", "9.9"))

	after := ob.Snapshot()
	assert.Equal(t, before.Bids, after.Bids, "out-of-order delta must not modify book state")
	assert.Equal(t, uint64(10), after.Seq)
}

// TestApply_SequenceAdvance verifies lastSeq advances monotonically.
func TestApply_SequenceAdvance(t *testing.T) {
	ob := orderbook.New()

	for i := uint64(1); i <= 100; i++ {
		ob.Apply(bid(i, "100.00", "1.0"))
		assert.Equal(t, i, ob.LastSeq())
	}
}

// TestSnapshot_IsCopy verifies the returned snapshot is a value copy.
func TestSnapshot_IsCopy(t *testing.T) {
	ob := orderbook.New()
	ob.Apply(bid(1, "29500.00", "1.0"))

	snap := ob.Snapshot()

	// Mutate the snapshot — must not affect the book.
	snap.Bids["29500.00"] = "999.0"
	snap.Bids["FAKE"] = "1.0"

	snap2 := ob.Snapshot()
	assert.Equal(t, "1.0", snap2.Bids["29500.00"], "book must not be affected by snapshot mutation")
	_, fakeExists := snap2.Bids["FAKE"]
	assert.False(t, fakeExists)
}

// TestApplySnapshot replaces book state from a REST snapshot.
func TestApplySnapshot(t *testing.T) {
	ob := orderbook.New()
	ob.Apply(bid(1, "old-price", "9.9"))

	bids := map[string]string{"29500.00": "2.0"}
	asks := map[string]string{"29600.00": "1.0"}
	ob.ApplySnapshot(50, bids, asks)

	snap := ob.Snapshot()
	require.Equal(t, uint64(50), snap.Seq)
	assert.Equal(t, "2.0", snap.Bids["29500.00"])
	assert.Equal(t, "1.0", snap.Asks["29600.00"])
	_, oldExists := snap.Bids["old-price"]
	assert.False(t, oldExists, "snapshot must replace, not merge, old state")
}

// TestReset clears all state.
func TestReset(t *testing.T) {
	ob := orderbook.New()
	ob.Apply(bid(5, "29500.00", "1.0"))
	ob.Reset()

	snap := ob.Snapshot()
	assert.Equal(t, uint64(0), snap.Seq)
	assert.Empty(t, snap.Bids)
	assert.Empty(t, snap.Asks)
}

// TestZeroSeqDiscarded verifies seq=0 on an empty book is treated as valid first message.
// LastSeq starts at 0, so a delta with seq=0 is a duplicate and must be discarded.
func TestZeroSeqDiscarded(t *testing.T) {
	ob := orderbook.New()
	ob.Apply(bid(0, "29500.00", "1.0"))

	snap := ob.Snapshot()
	_, exists := snap.Bids["29500.00"]
	assert.False(t, exists, "seq=0 delta must be discarded (lastSeq starts at 0)")
}

// TestMultipleSides verifies bid and ask sides are tracked independently.
func TestMultipleSides(t *testing.T) {
	ob := orderbook.New()

	ob.Apply(bid(1, "100.00", "10.0"))
	ob.Apply(ask(2, "100.00", "5.0")) // same price, different side

	snap := ob.Snapshot()
	assert.Equal(t, "10.0", snap.Bids["100.00"])
	assert.Equal(t, "5.0", snap.Asks["100.00"])
}

// TestApply_EmptySizeRemoves verifies empty string size also removes the level.
func TestApply_EmptySizeRemoves(t *testing.T) {
	ob := orderbook.New()
	ob.Apply(bid(1, "29500.00", "1.5"))
	ob.Apply(bid(2, "29500.00", "")) // empty string = remove

	snap := ob.Snapshot()
	_, exists := snap.Bids["29500.00"]
	assert.False(t, exists)
}

// TestApply_InvalidSide_Discarded verifies that an unknown Side value is silently discarded.
func TestApply_InvalidSide_Discarded(t *testing.T) {
	ob := orderbook.New()
	ob.Apply(bid(1, "100.00", "1.0"))

	// Side=2 is not SideBid(0) or SideAsk(1) — must be discarded.
	invalid := orderbook.Delta{Seq: 2, Side: orderbook.Side(2), Price: "100.00", Size: "9.9"}
	ob.Apply(invalid)

	snap := ob.Snapshot()
	// lastSeq must not advance on a discarded delta.
	assert.Equal(t, uint64(1), snap.Seq, "invalid Side must not advance sequence")
	assert.Equal(t, "1.0", snap.Bids["100.00"], "invalid Side must not modify book state")
}

// TestApply_EmptyPrice_Discarded verifies that a delta with empty Price is silently discarded.
func TestApply_EmptyPrice_Discarded(t *testing.T) {
	ob := orderbook.New()
	ob.Apply(bid(1, "100.00", "1.0"))

	empty := orderbook.Delta{Seq: 2, Side: orderbook.SideBid, Price: "", Size: "5.0"}
	ob.Apply(empty)

	snap := ob.Snapshot()
	assert.Equal(t, uint64(1), snap.Seq, "empty Price must not advance sequence")
	_, exists := snap.Bids[""]
	assert.False(t, exists, "empty price key must not exist in book")
}
