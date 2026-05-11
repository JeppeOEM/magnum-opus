package features_test

import (
	"testing"

	"github.com/mrqdt/magnum-opus/candle-service/internal/features"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func fp(cells map[string]features.FootprintCell) map[string]features.FootprintCell { return cells }

func TestComputeValueArea_Empty(t *testing.T) {
	_, _, _, _, ok := features.ComputeValueArea(fp(nil))
	assert.False(t, ok, "empty footprint must return ok=false")

	_, _, _, _, ok = features.ComputeValueArea(fp(map[string]features.FootprintCell{}))
	assert.False(t, ok, "empty map must return ok=false")
}

func TestComputeValueArea_SingleLevel(t *testing.T) {
	m := fp(map[string]features.FootprintCell{
		"100.0": {BuyVol: 1.0, SellVol: 2.0},
	})
	poc, vah, val, pocVol, ok := features.ComputeValueArea(m)
	require.True(t, ok)
	assert.InDelta(t, 100.0, poc, 1e-9, "poc")
	assert.InDelta(t, 100.0, vah, 1e-9, "vah == poc on single level")
	assert.InDelta(t, 100.0, val, 1e-9, "val == poc on single level")
	assert.InDelta(t, 3.0, pocVol, 1e-9, "poc volume = buyVol+sellVol")
}

func TestComputeValueArea_TwoLevels_HigherVolIsPoC(t *testing.T) {
	// Level 100: vol=3; level 200: vol=7. POC=200. Total=10. Target=7.
	// POC already covers 7/10 = 70%. Value area = {200} only.
	m := fp(map[string]features.FootprintCell{
		"100": {BuyVol: 1.0, SellVol: 2.0}, // vol=3
		"200": {BuyVol: 4.0, SellVol: 3.0}, // vol=7
	})
	poc, vah, val, pocVol, ok := features.ComputeValueArea(m)
	require.True(t, ok)
	assert.InDelta(t, 200.0, poc, 1e-9)
	assert.InDelta(t, 7.0, pocVol, 1e-9)
	// POC alone >= 70%: vah == val == poc
	assert.InDelta(t, 200.0, vah, 1e-9)
	assert.InDelta(t, 200.0, val, 1e-9)
}

func TestComputeValueArea_TwoLevels_NeedsBothForValueArea(t *testing.T) {
	// Level 100: vol=4; level 200: vol=6. Total=10. Target=7.
	// POC=200 (vol=6, < 70%). Expand to include 100 → accumulated=10 >= 7.
	m := fp(map[string]features.FootprintCell{
		"100": {BuyVol: 2.0, SellVol: 2.0}, // vol=4
		"200": {BuyVol: 3.0, SellVol: 3.0}, // vol=6
	})
	poc, vah, val, pocVol, ok := features.ComputeValueArea(m)
	require.True(t, ok)
	assert.InDelta(t, 200.0, poc, 1e-9)
	assert.InDelta(t, 6.0, pocVol, 1e-9)
	assert.InDelta(t, 200.0, vah, 1e-9)
	assert.InDelta(t, 100.0, val, 1e-9)
}

func TestComputeValueArea_StandardDistribution(t *testing.T) {
	// 5 levels. POC at 300 with vol=10. Total=30. Target=21.
	// Sorted: 100(5), 200(6), 300(10), 400(6), 500(3)
	// Start: accumulated=10 @ idx=2. lo=2,hi=2.
	// Step 1: left vol=6, right vol=6. Tie → expand right (hi). hi=3, acc=16.
	// Step 2: left vol=6, right vol=3. left > right → expand left. lo=1, acc=22 >= 21. Stop.
	// VA = [200..400]. vah=400, val=200.
	m := fp(map[string]features.FootprintCell{
		"100": {BuyVol: 2.5, SellVol: 2.5}, // vol=5
		"200": {BuyVol: 3.0, SellVol: 3.0}, // vol=6
		"300": {BuyVol: 5.0, SellVol: 5.0}, // vol=10
		"400": {BuyVol: 3.0, SellVol: 3.0}, // vol=6
		"500": {BuyVol: 1.5, SellVol: 1.5}, // vol=3
	})
	poc, vah, val, pocVol, ok := features.ComputeValueArea(m)
	require.True(t, ok)
	assert.InDelta(t, 300.0, poc, 1e-9)
	assert.InDelta(t, 10.0, pocVol, 1e-9)
	assert.InDelta(t, 400.0, vah, 1e-9)
	assert.InDelta(t, 200.0, val, 1e-9)
}

func TestComputeValueArea_TieBreak_HigherPriceWinsPOC(t *testing.T) {
	// Two levels with equal volume. Higher price wins as POC.
	m := fp(map[string]features.FootprintCell{
		"100": {BuyVol: 5.0, SellVol: 0.0}, // vol=5
		"200": {BuyVol: 5.0, SellVol: 0.0}, // vol=5
	})
	poc, _, _, pocVol, ok := features.ComputeValueArea(m)
	require.True(t, ok)
	assert.InDelta(t, 200.0, poc, 1e-9, "higher price wins tie")
	assert.InDelta(t, 5.0, pocVol, 1e-9)
}

func TestComputeValueArea_UniformDistribution(t *testing.T) {
	// All 4 levels have vol=5. Total=20. Target=14. POC=highest price (400).
	// hi=3(poc), lo=3. expand: only lo available. lo=2, acc=10. lo=1, acc=15 >= 14. Stop.
	// vah=400, val=200.
	m := fp(map[string]features.FootprintCell{
		"100": {BuyVol: 2.5, SellVol: 2.5},
		"200": {BuyVol: 2.5, SellVol: 2.5},
		"300": {BuyVol: 2.5, SellVol: 2.5},
		"400": {BuyVol: 2.5, SellVol: 2.5},
	})
	poc, vah, val, pocVol, ok := features.ComputeValueArea(m)
	require.True(t, ok)
	assert.InDelta(t, 400.0, poc, 1e-9, "highest price wins uniform tie")
	assert.InDelta(t, 5.0, pocVol, 1e-9)
	assert.InDelta(t, 400.0, vah, 1e-9)
	assert.InDelta(t, 200.0, val, 1e-9)
}

func TestComputeValueArea_70PctBoundaryPrecision(t *testing.T) {
	// 3 levels: 100(vol=10), 200(vol=10), 300(vol=10). Total=30. Target=21.
	// POC: tie at all three → highest price = 300, pocIdx=2.
	// hi=2, lo=2, acc=10. Only lo available (hi is already at end).
	// Expand lo: lo=1, acc=20 < 21. Expand lo: lo=0, acc=30 >= 21. Stop.
	// vah=300, val=100.
	m := fp(map[string]features.FootprintCell{
		"100": {BuyVol: 5.0, SellVol: 5.0},
		"200": {BuyVol: 5.0, SellVol: 5.0},
		"300": {BuyVol: 5.0, SellVol: 5.0},
	})
	poc, vah, val, pocVol, ok := features.ComputeValueArea(m)
	require.True(t, ok)
	assert.InDelta(t, 300.0, poc, 1e-9)
	assert.InDelta(t, 10.0, pocVol, 1e-9)
	assert.InDelta(t, 300.0, vah, 1e-9)
	assert.InDelta(t, 100.0, val, 1e-9)
}

func TestComputeValueArea_AllZeroVolume(t *testing.T) {
	m := fp(map[string]features.FootprintCell{
		"100": {BuyVol: 0.0, SellVol: 0.0},
		"200": {BuyVol: 0.0, SellVol: 0.0},
	})
	_, _, _, _, ok := features.ComputeValueArea(m)
	assert.False(t, ok, "all-zero volume footprint must return ok=false")
}

func TestComputeValueArea_TieExpansionPrefersHigherPrice(t *testing.T) {
	// POC at 200. Adjacent levels have equal vol → should expand upward (to 300) first.
	// 100(3), 200(6), 300(3). Total=12. Target=8.4.
	// POC=200(vol=6), acc=6. lo=1,hi=1.
	// Both available: left=3, right=3. Tie → expand hi. hi=2, acc=9 >= 8.4. Stop.
	// vah=300, val=200.
	m := fp(map[string]features.FootprintCell{
		"100": {BuyVol: 1.5, SellVol: 1.5}, // vol=3
		"200": {BuyVol: 3.0, SellVol: 3.0}, // vol=6
		"300": {BuyVol: 1.5, SellVol: 1.5}, // vol=3
	})
	poc, vah, val, pocVol, ok := features.ComputeValueArea(m)
	require.True(t, ok)
	assert.InDelta(t, 200.0, poc, 1e-9)
	assert.InDelta(t, 6.0, pocVol, 1e-9)
	assert.InDelta(t, 300.0, vah, 1e-9, "tie in expansion → prefer higher price side")
	assert.InDelta(t, 200.0, val, 1e-9)
}
