package features_test

import (
	"testing"

	"github.com/mrqdt/magnum-opus/candle-service/internal/features"
	"github.com/stretchr/testify/assert"
)

func q(bidP, bidSz, askP, askSz string) features.BestQuote {
	return features.BestQuote{BidPrice: bidP, BidSize: bidSz, AskPrice: askP, AskSize: askSz}
}

func TestOFIDelta_EmptyQuote_ReturnsZero(t *testing.T) {
	assert.Equal(t, 0.0, features.OFIDelta(features.BestQuote{}, q("100", "1", "101", "1")))
	assert.Equal(t, 0.0, features.OFIDelta(q("100", "1", "101", "1"), features.BestQuote{}))
}

func TestOFIDelta_UnchangedPrices_QuantityDiff(t *testing.T) {
	// Bid quantity increased by 1, ask unchanged → delta = +1
	prev := q("100", "2", "101", "3")
	curr := q("100", "3", "101", "3")
	assert.InDelta(t, 1.0, features.OFIDelta(prev, curr), 1e-9)
}

func TestOFIDelta_UnchangedPrices_BothIncrease(t *testing.T) {
	// Bid +2, ask +1 → delta = +2 - +1 = +1
	prev := q("100", "1", "101", "1")
	curr := q("100", "3", "101", "2")
	assert.InDelta(t, 1.0, features.OFIDelta(prev, curr), 1e-9)
}

func TestOFIDelta_BidPriceRaised_UsesFullNewQty(t *testing.T) {
	// New best bid at higher price: Δ_bid = currBidQ (full new quantity)
	prev := q("99", "5", "101", "3")
	curr := q("100", "2", "101", "3")
	// deltaBid = 2 (currBidQ), deltaAsk = 0 → OFI = 2
	assert.InDelta(t, 2.0, features.OFIDelta(prev, curr), 1e-9)
}

func TestOFIDelta_BidPriceDropped_ZeroDeltaBid(t *testing.T) {
	// Best bid dropped (prior level displaced): Δ_bid = 0
	prev := q("100", "5", "101", "3")
	curr := q("99", "2", "101", "3")
	// deltaBid = 0, deltaAsk = 0 → OFI = 0
	assert.InDelta(t, 0.0, features.OFIDelta(prev, curr), 1e-9)
}

func TestOFIDelta_AskPriceLowered_UsesFullNewQty(t *testing.T) {
	// New best ask at lower price: Δ_ask = currAskQ
	prev := q("100", "2", "102", "4")
	curr := q("100", "2", "101", "3")
	// deltaBid = 0, deltaAsk = 3 → OFI = -3
	assert.InDelta(t, -3.0, features.OFIDelta(prev, curr), 1e-9)
}

func TestOFIDelta_AskPriceRaised_ZeroDeltaAsk(t *testing.T) {
	// Best ask rose (prior level displaced): Δ_ask = 0
	prev := q("100", "2", "101", "3")
	curr := q("100", "2", "102", "4")
	// deltaBid = 0, deltaAsk = 0 → OFI = 0
	assert.InDelta(t, 0.0, features.OFIDelta(prev, curr), 1e-9)
}

func TestOFIDelta_BothSidesChange(t *testing.T) {
	// Bid raised (+3 new qty), ask lowered (+2 new qty) → OFI = 3 - 2 = 1
	prev := q("99", "5", "102", "4")
	curr := q("100", "3", "101", "2")
	assert.InDelta(t, 1.0, features.OFIDelta(prev, curr), 1e-9)
}

func TestMidPrice(t *testing.T) {
	assert.InDelta(t, 100.5, features.MidPrice(100.0, 101.0), 1e-9)
	assert.InDelta(t, 50000.25, features.MidPrice(50000.0, 50000.5), 1e-9)
	assert.InDelta(t, 0.0, features.MidPrice(0.0, 0.0), 1e-9)
}

func TestSpread(t *testing.T) {
	assert.InDelta(t, 1.0, features.Spread(100.0, 101.0), 1e-9)
	assert.InDelta(t, 0.5, features.Spread(50000.0, 50000.5), 1e-9)
	assert.InDelta(t, 0.0, features.Spread(100.0, 100.0), 1e-9)
}

func TestEffectiveSpreadContrib(t *testing.T) {
	// 2 * |100.8 - 100.5| = 2 * 0.3 = 0.6
	assert.InDelta(t, 0.6, features.EffectiveSpreadContrib(100.8, 100.5), 1e-9)
	// Symmetric: buy below mid same magnitude
	assert.InDelta(t, 0.6, features.EffectiveSpreadContrib(100.2, 100.5), 1e-9)
	// Trade exactly at mid → 0
	assert.InDelta(t, 0.0, features.EffectiveSpreadContrib(100.0, 100.0), 1e-9)
}
