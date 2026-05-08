package features_test

import (
	"strconv"
	"testing"

	"github.com/mrqdt/magnum-opus/candle-service/internal/features"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// makeBids/makeAsks helpers for test readability.
func makeBids(pricesSizes ...string) map[string]string {
	return makeBook(pricesSizes...)
}
func makeAsks(pricesSizes ...string) map[string]string {
	return makeBook(pricesSizes...)
}
func makeBook(pricesSizes ...string) map[string]string {
	m := make(map[string]string, len(pricesSizes)/2)
	for i := 0; i+1 < len(pricesSizes); i += 2 {
		m[pricesSizes[i]] = pricesSizes[i+1]
	}
	return m
}

func TestComputeDepthSnapshot_EmptyMaps(t *testing.T) {
	d := features.ComputeDepthSnapshot(nil, nil)
	assert.False(t, d.HasBidVolume)
	assert.False(t, d.HasAskVolume)
	assert.InDelta(t, 0.0, d.BidTotal, 1e-9)
	assert.InDelta(t, 0.0, d.AskTotal, 1e-9)
	assert.InDelta(t, 0.0, d.WeightedBidPrice, 1e-9)
}

func TestComputeDepthSnapshot_SingleLevel(t *testing.T) {
	// One bid level at 100.0, size 5.0
	bids := makeBids("100.0", "5.0")
	asks := makeAsks("101.0", "3.0")

	d := features.ComputeDepthSnapshot(bids, asks)

	require.True(t, d.HasBidVolume)
	require.True(t, d.HasAskVolume)
	// L1 = total for single level
	assert.InDelta(t, 5.0, d.BidL1, 1e-9)
	assert.InDelta(t, 5.0, d.BidL2, 1e-9, "L2 caps at total for <2 levels")
	assert.InDelta(t, 5.0, d.BidTop10, 1e-9, "Top10 caps at total for <10 levels")
	assert.InDelta(t, 5.0, d.BidTotal, 1e-9)
	assert.InDelta(t, 100.0, d.WeightedBidPrice, 1e-9, "single level: weighted = level price")

	assert.InDelta(t, 3.0, d.AskL1, 1e-9)
	assert.InDelta(t, 3.0, d.AskTotal, 1e-9)
	assert.InDelta(t, 101.0, d.WeightedAskPrice, 1e-9)
}

func TestComputeDepthSnapshot_MultipleLevels(t *testing.T) {
	// 5 bid levels: 100(1), 99(2), 98(3), 97(4), 96(5) — best bid first
	bids := makeBids("100", "1", "99", "2", "98", "3", "97", "4", "96", "5")
	// 3 ask levels: 101(1.5), 102(2.5), 103(3.5)
	asks := makeAsks("101", "1.5", "102", "2.5", "103", "3.5")

	d := features.ComputeDepthSnapshot(bids, asks)

	require.True(t, d.HasBidVolume)
	require.True(t, d.HasAskVolume)

	// Bids sorted descending: [100=1, 99=2, 98=3, 97=4, 96=5]
	assert.InDelta(t, 1.0, d.BidL1, 1e-9)
	assert.InDelta(t, 3.0, d.BidL2, 1e-9)   // 1+2
	assert.InDelta(t, 15.0, d.BidTop10, 1e-9, "Top10 caps at total for <10 levels")
	assert.InDelta(t, 15.0, d.BidTotal, 1e-9) // 1+2+3+4+5
	// Weighted bid = (100*1 + 99*2 + 98*3 + 97*4 + 96*5) / 15
	//              = (100 + 198 + 294 + 388 + 480) / 15 = 1460/15 ≈ 97.333
	assert.InDelta(t, 1460.0/15.0, d.WeightedBidPrice, 1e-9)

	// Asks sorted ascending: [101=1.5, 102=2.5, 103=3.5]
	assert.InDelta(t, 1.5, d.AskL1, 1e-9)
	assert.InDelta(t, 4.0, d.AskL2, 1e-9)  // 1.5+2.5
	assert.InDelta(t, 7.5, d.AskTop10, 1e-9)
	assert.InDelta(t, 7.5, d.AskTotal, 1e-9)
	// Weighted ask = (101*1.5 + 102*2.5 + 103*3.5) / 7.5
	//             = (151.5 + 255 + 360.5) / 7.5 = 767/7.5 ≈ 102.267
	assert.InDelta(t, 767.0/7.5, d.WeightedAskPrice, 1e-9)
}

func TestComputeDepthSnapshot_OneSideEmpty(t *testing.T) {
	bids := makeBids("100", "2")
	d := features.ComputeDepthSnapshot(bids, nil)

	assert.True(t, d.HasBidVolume)
	assert.False(t, d.HasAskVolume)
	assert.InDelta(t, 2.0, d.BidTotal, 1e-9)
	assert.InDelta(t, 0.0, d.AskTotal, 1e-9)
	assert.InDelta(t, 0.0, d.WeightedAskPrice, 1e-9, "zero when no ask volume")
}

func TestComputeDepthSnapshot_Top10Cap(t *testing.T) {
	// Build exactly 12 bid levels; Top10 should equal sum of top 10
	bids := make(map[string]string)
	for i := 100; i >= 89; i-- { // 12 levels: 100,99,...,89
		bids[strconv.Itoa(i)] = "1.0"
	}

	d := features.ComputeDepthSnapshot(bids, nil)

	assert.InDelta(t, 1.0, d.BidL1, 1e-9)
	assert.InDelta(t, 2.0, d.BidL2, 1e-9)
	assert.InDelta(t, 10.0, d.BidTop10, 1e-9, "Top10 sums exactly 10 best levels")
	assert.InDelta(t, 12.0, d.BidTotal, 1e-9)
}

func TestComputeDepthSnapshot_SkipsZeroSize(t *testing.T) {
	// OB may still have zero-size levels in flight; they should be skipped.
	bids := makeBids("100", "0", "99", "5")
	d := features.ComputeDepthSnapshot(bids, nil)

	// Level 100 size=0 skipped; only 99 = 5
	assert.InDelta(t, 5.0, d.BidL1, 1e-9)
	assert.InDelta(t, 5.0, d.BidTotal, 1e-9)
}

// ── DepthTo1PctBid ────────────────────────────────────────────────────────────

func TestDepthTo1PctBid_OnlyWithinThreshold(t *testing.T) {
	// bestBid=100; range=[99,100]; levels at 100(in), 99.5(in), 99(in), 98.5(out), 98(out)
	// Level at 101 is above bestBid and must be excluded (crossed/corrupt book guard)
	bids := makeBids("101", "9", "100", "2", "99.5", "1", "99", "3", "98.5", "4", "98", "5")
	result := features.DepthTo1PctBid(bids, 100.0)
	// 101 > 100 → exclude; 100 in [99,100] → include (2); 99.5 in [99,100] → include (1);
	// 99 in [99,100] → include (3); 98.5 < 99 → exclude; 98 < 99 → exclude
	assert.InDelta(t, 6.0, result, 1e-9)
}

func TestDepthTo1PctBid_EmptyAndNil(t *testing.T) {
	assert.InDelta(t, 0.0, features.DepthTo1PctBid(nil, 100.0), 1e-9)
	assert.InDelta(t, 0.0, features.DepthTo1PctBid(map[string]string{}, 100.0), 1e-9)
}

func TestDepthTo1PctBid_ZeroBestBid(t *testing.T) {
	bids := makeBids("100", "5")
	assert.InDelta(t, 0.0, features.DepthTo1PctBid(bids, 0.0), 1e-9)
}

// ── DepthTo1PctAsk ────────────────────────────────────────────────────────────

func TestDepthTo1PctAsk_OnlyWithinThreshold(t *testing.T) {
	// bestAsk=100; range=[100,101]; levels at 100(in), 100.5(in), 101(in), 101.1(out), 102(out)
	// Level at 99 is below bestAsk and must be excluded (crossed/corrupt book guard)
	asks := makeAsks("99", "9", "100", "2", "100.5", "1", "101", "3", "101.1", "4", "102", "5")
	result := features.DepthTo1PctAsk(asks, 100.0)
	// 99 < 100 → exclude; 100 in [100,101] → include (2); 100.5 in [100,101] → include (1);
	// 101 in [100,101] → include (3); 101.1 > 101 → exclude; 102 > 101 → exclude
	assert.InDelta(t, 6.0, result, 1e-9)
}

func TestDepthTo1PctAsk_EmptyAndNil(t *testing.T) {
	assert.InDelta(t, 0.0, features.DepthTo1PctAsk(nil, 100.0), 1e-9)
	assert.InDelta(t, 0.0, features.DepthTo1PctAsk(map[string]string{}, 100.0), 1e-9)
}

func TestDepthTo1PctAsk_ZeroBestAsk(t *testing.T) {
	asks := makeAsks("100", "5")
	assert.InDelta(t, 0.0, features.DepthTo1PctAsk(asks, 0.0), 1e-9)
}

// ── ComputeDepthSnapshot 1pct fields ─────────────────────────────────────────

func TestComputeDepthSnapshot_Populates1PctFields(t *testing.T) {
	// bestBid=100; threshold=99 → levels 100(5) and 99.5(3) included; 98(2) excluded
	bids := makeBids("100", "5", "99.5", "3", "98", "2")
	// bestAsk=101; threshold=102.01 → levels 101(4) and 102(6) included; 103(1) excluded
	asks := makeAsks("101", "4", "102", "6", "103", "1")

	d := features.ComputeDepthSnapshot(bids, asks)

	assert.InDelta(t, 8.0, d.DepthTo1PctBid, 1e-9, "5+3 within 1% of bestBid=100")
	assert.InDelta(t, 10.0, d.DepthTo1PctAsk, 1e-9, "4+6 within 1% of bestAsk=101")
}

func TestComputeDepthSnapshot_1PctZeroWhenEmpty(t *testing.T) {
	d := features.ComputeDepthSnapshot(nil, nil)
	assert.InDelta(t, 0.0, d.DepthTo1PctBid, 1e-9)
	assert.InDelta(t, 0.0, d.DepthTo1PctAsk, 1e-9)
}
