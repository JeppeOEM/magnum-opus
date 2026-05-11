package features

import (
	"testing"
)

func TestComputeAuctionSignals_EmptyFootprint(t *testing.T) {
	sigs := ComputeAuctionSignals(nil, 100.0, 99.0)
	if sigs.SinglePrintCount != 0 {
		t.Errorf("want 0 got %d", sigs.SinglePrintCount)
	}
	if sigs.SinglePrintLevelsJSON != "[]" {
		t.Errorf("want [] got %s", sigs.SinglePrintLevelsJSON)
	}
	if sigs.UnfinishedTop || sigs.UnfinishedBottom {
		t.Error("want both false")
	}
}

func TestComputeAuctionSignals_EmptyMap(t *testing.T) {
	sigs := ComputeAuctionSignals(map[string]FootprintCell{}, 100.0, 99.0)
	if sigs.SinglePrintCount != 0 || sigs.SinglePrintLevelsJSON != "[]" {
		t.Errorf("empty map: want 0/[], got %d/%s", sigs.SinglePrintCount, sigs.SinglePrintLevelsJSON)
	}
}

func TestComputeAuctionSignals_SinglePrintDetection(t *testing.T) {
	// mean vol = (1000 + 50) / 2 = 525; 10% threshold = 52.5
	// level "99.0" has vol 50 < 52.5 → single print
	// level "100.0" has vol 1000 → not single print
	fp := map[string]FootprintCell{
		"99.0":  {BuyVol: 30, SellVol: 20},  // vol=50 < 52.5 — single print
		"100.0": {BuyVol: 600, SellVol: 400}, // vol=1000 — not single print
	}
	sigs := ComputeAuctionSignals(fp, 100.0, 99.0)
	if sigs.SinglePrintCount != 1 {
		t.Errorf("want SinglePrintCount=1 got %d", sigs.SinglePrintCount)
	}
	if sigs.SinglePrintLevelsJSON == "[]" {
		t.Error("want non-empty levels JSON")
	}
}

func TestComputeAuctionSignals_UnfinishedTop(t *testing.T) {
	fp := map[string]FootprintCell{
		"100.0": {BuyVol: 50, SellVol: 0},  // high, sellVol==0 → unfinished top
		"99.0":  {BuyVol: 20, SellVol: 30},
	}
	sigs := ComputeAuctionSignals(fp, 100.0, 99.0)
	if !sigs.UnfinishedTop {
		t.Error("want UnfinishedTop=true")
	}
	if sigs.UnfinishedBottom {
		t.Error("want UnfinishedBottom=false")
	}
}

func TestComputeAuctionSignals_UnfinishedBottom(t *testing.T) {
	fp := map[string]FootprintCell{
		"99.0":  {BuyVol: 0, SellVol: 50},  // low, buyVol==0 → unfinished bottom
		"100.0": {BuyVol: 20, SellVol: 30},
	}
	sigs := ComputeAuctionSignals(fp, 100.0, 99.0)
	if !sigs.UnfinishedBottom {
		t.Error("want UnfinishedBottom=true")
	}
	if sigs.UnfinishedTop {
		t.Error("want UnfinishedTop=false")
	}
}

func TestComputeAuctionSignals_HighLowNotInFootprint(t *testing.T) {
	fp := map[string]FootprintCell{
		"99.5": {BuyVol: 50, SellVol: 0},
	}
	// high=100.0 and low=99.0 are not in the footprint
	sigs := ComputeAuctionSignals(fp, 100.0, 99.0)
	if sigs.UnfinishedTop || sigs.UnfinishedBottom {
		t.Error("want both false when high/low not in footprint")
	}
}

// --- DetectAbsorption tests ---

func TestDetectAbsorption_ZeroTotalVol(t *testing.T) {
	if DetectAbsorption(0, 0, 100.0, 100.0, 10) {
		t.Error("want false for totalVol==0")
	}
}

func TestDetectAbsorption_TradeCountBelow5(t *testing.T) {
	// buyRatio=0.65 (skewed), tiny price move — but tradeCount=4
	if DetectAbsorption(65, 100, 100.0, 100.04, 4) {
		t.Error("want false for tradeCount=4")
	}
}

func TestDetectAbsorption_SkewJustBelow(t *testing.T) {
	// ratio=0.59 — not above 0.6 and not below 0.4 → neutral band, should be false
	if DetectAbsorption(59, 100, 100.0, 100.04, 5) {
		t.Error("want false for ratio=0.59")
	}
}

func TestDetectAbsorption_BuySkewTrue(t *testing.T) {
	// ratio=0.61 > 0.6, tradeCount=5, price moved 0.04% < 0.05%
	if !DetectAbsorption(61, 100, 100.0, 100.04, 5) {
		t.Error("want true for ratio=0.61, count=5, tiny move")
	}
}

func TestDetectAbsorption_SellSkewTrue(t *testing.T) {
	// ratio=0.35 < 0.4, tradeCount=5, price moved 0.04%
	if !DetectAbsorption(35, 100, 100.0, 100.04, 5) {
		t.Error("want true for ratio=0.35, count=5, tiny move")
	}
}

func TestDetectAbsorption_PriceMoveTooBig(t *testing.T) {
	// ratio=0.65 but price moved 0.06% > 0.05%
	if DetectAbsorption(65, 100, 100.0, 100.06, 5) {
		t.Error("want false for price move > 0.05%")
	}
}
