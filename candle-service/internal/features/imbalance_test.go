package features_test

import (
	"testing"

	"github.com/mrqdt/magnum-opus/candle-service/internal/features"
	"github.com/stretchr/testify/assert"
)

func imb(cells map[string]features.FootprintCell) features.ImbalanceSignals {
	return features.ComputeImbalance(cells)
}

func TestComputeImbalance_Empty(t *testing.T) {
	s := imb(nil)
	assert.Equal(t, 0, s.BuyCount)
	assert.Equal(t, 0, s.SellCount)
	assert.Equal(t, 0, s.StackBuy)
	assert.Equal(t, 0, s.StackSell)
	assert.InDelta(t, 0.0, s.Ratio, 1e-9)

	s = imb(map[string]features.FootprintCell{})
	assert.Equal(t, 0, s.BuyCount)
}

func TestComputeImbalance_AllBuyImbalanced(t *testing.T) {
	// 3 cells all buy-imbalanced: buyVol > 3×sellVol, sellVol > 0
	m := map[string]features.FootprintCell{
		"100": {BuyVol: 10, SellVol: 1},
		"200": {BuyVol: 12, SellVol: 2},
		"300": {BuyVol: 9, SellVol: 0.5},
	}
	s := imb(m)
	assert.Equal(t, 3, s.BuyCount)
	assert.Equal(t, 0, s.SellCount)
	assert.Equal(t, 3, s.StackBuy)
	assert.Equal(t, 0, s.StackSell)
	assert.InDelta(t, 1.0, s.Ratio, 1e-9)
}

func TestComputeImbalance_AllSellImbalanced(t *testing.T) {
	m := map[string]features.FootprintCell{
		"100": {BuyVol: 1, SellVol: 10},
		"200": {BuyVol: 2, SellVol: 12},
	}
	s := imb(m)
	assert.Equal(t, 0, s.BuyCount)
	assert.Equal(t, 2, s.SellCount)
	assert.Equal(t, 0, s.StackBuy)
	assert.Equal(t, 2, s.StackSell)
	assert.InDelta(t, -1.0, s.Ratio, 1e-9)
}

func TestComputeImbalance_Mixed_NonConsecutive(t *testing.T) {
	// Alternating: buy, sell, buy, sell (sorted by price: 100,200,300,400)
	m := map[string]features.FootprintCell{
		"100": {BuyVol: 10, SellVol: 1}, // buy-imb
		"200": {BuyVol: 1, SellVol: 10}, // sell-imb
		"300": {BuyVol: 10, SellVol: 1}, // buy-imb
		"400": {BuyVol: 1, SellVol: 10}, // sell-imb
	}
	s := imb(m)
	assert.Equal(t, 2, s.BuyCount)
	assert.Equal(t, 2, s.SellCount)
	assert.Equal(t, 1, s.StackBuy, "non-consecutive: max run = 1")
	assert.Equal(t, 1, s.StackSell, "non-consecutive: max run = 1")
	assert.InDelta(t, 0.0, s.Ratio, 1e-9) // (2-2)/4 = 0
}

func TestComputeImbalance_ConsecutiveStack(t *testing.T) {
	// Prices 100-600: buy-imb, buy-imb, buy-imb, neutral, buy-imb, buy-imb
	// Longest buy run = 3 (at 100,200,300)
	m := map[string]features.FootprintCell{
		"100": {BuyVol: 10, SellVol: 1}, // buy-imb
		"200": {BuyVol: 10, SellVol: 1}, // buy-imb
		"300": {BuyVol: 10, SellVol: 1}, // buy-imb
		"400": {BuyVol: 3, SellVol: 3},  // neutral
		"500": {BuyVol: 10, SellVol: 1}, // buy-imb
		"600": {BuyVol: 10, SellVol: 1}, // buy-imb
	}
	s := imb(m)
	assert.Equal(t, 5, s.BuyCount)
	assert.Equal(t, 3, s.StackBuy, "longest run is 3, not 2")
}

func TestComputeImbalance_RatioBounds(t *testing.T) {
	// 8 total levels: 3 buy-imbalanced, 1 sell-imbalanced, 4 neutral
	// Ratio = (3-1)/8 = 0.25
	m := map[string]features.FootprintCell{
		"100": {BuyVol: 10, SellVol: 1}, // buy-imb
		"200": {BuyVol: 10, SellVol: 1}, // buy-imb
		"300": {BuyVol: 10, SellVol: 1}, // buy-imb
		"400": {BuyVol: 1, SellVol: 10}, // sell-imb
		"500": {BuyVol: 3, SellVol: 3},  // neutral
		"600": {BuyVol: 3, SellVol: 3},  // neutral
		"700": {BuyVol: 3, SellVol: 3},  // neutral
		"800": {BuyVol: 3, SellVol: 3},  // neutral
	}
	s := imb(m)
	assert.Equal(t, 3, s.BuyCount)
	assert.Equal(t, 1, s.SellCount)
	assert.InDelta(t, 0.25, s.Ratio, 1e-9)
}

func TestComputeImbalance_ZeroOnOneSide_Excluded(t *testing.T) {
	// sellVol == 0: must NOT count as buy-imbalanced (one-sided is a different signal)
	// buyVol == 0: must NOT count as sell-imbalanced
	m := map[string]features.FootprintCell{
		"100": {BuyVol: 10, SellVol: 0},  // one-sided buy: excluded
		"200": {BuyVol: 0, SellVol: 10},  // one-sided sell: excluded
		"300": {BuyVol: 10, SellVol: 1},  // genuine buy-imbalance
	}
	s := imb(m)
	assert.Equal(t, 1, s.BuyCount, "only the cell with both sides counts")
	assert.Equal(t, 0, s.SellCount)
	assert.Equal(t, 1, s.StackBuy)
}
