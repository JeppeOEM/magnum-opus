package features

import (
	"testing"
)

func fp(v float64) *float64 { return &v }
func ip(v int) *int         { return &v }

func TestFootprintDeltaDivergence_BearishDivergence(t *testing.T) {
	// close > open (up candle), net delta negative (sellers dominated)
	got := FootprintDeltaDivergence(fp(100), fp(101), fp(30), fp(70))
	if got != 1 {
		t.Errorf("want 1 (bearish) got %d", got)
	}
}

func TestFootprintDeltaDivergence_BullishDivergence(t *testing.T) {
	// close < open (down candle), net delta positive (buyers dominated)
	got := FootprintDeltaDivergence(fp(101), fp(100), fp(70), fp(30))
	if got != -1 {
		t.Errorf("want -1 (bullish) got %d", got)
	}
}

func TestFootprintDeltaDivergence_NoDivergenceUpBuyDominant(t *testing.T) {
	// close > open, buyers dominated — confirmed move, no divergence
	got := FootprintDeltaDivergence(fp(100), fp(101), fp(70), fp(30))
	if got != 0 {
		t.Errorf("want 0 got %d", got)
	}
}

func TestFootprintDeltaDivergence_NoDivergenceFlat(t *testing.T) {
	// close == open
	got := FootprintDeltaDivergence(fp(100), fp(100), fp(50), fp(50))
	if got != 0 {
		t.Errorf("want 0 got %d", got)
	}
}

func TestFootprintDeltaDivergence_NilArgs(t *testing.T) {
	if FootprintDeltaDivergence(nil, fp(101), fp(30), fp(70)) != 0 {
		t.Error("nil open → want 0")
	}
	if FootprintDeltaDivergence(fp(100), nil, fp(30), fp(70)) != 0 {
		t.Error("nil close → want 0")
	}
	if FootprintDeltaDivergence(fp(100), fp(101), nil, fp(70)) != 0 {
		t.Error("nil buyVol → want 0")
	}
	if FootprintDeltaDivergence(fp(100), fp(101), fp(30), nil) != 0 {
		t.Error("nil sellVol → want 0")
	}
}

// --- ComputeIceberg tests ---

func TestComputeIceberg_BidIceberg(t *testing.T) {
	// bidDepthClose = 0.80 * bidDepthOpen (exact boundary → true)
	iceBid, iceAsk, icePrice := ComputeIceberg(
		fp(80), fp(100), // bid close=80, open=100 → 0.80×
		fp(100), fp(100), // ask not triggered (equal, no sell vol)
		fp(10), fp(0), // buyVol=10 > 0, sellVol=0
		ip(1), ip(0), // bidArrivals=1, askArrivals=0
		fp(67000), fp(67001),
	)
	if !iceBid {
		t.Error("want iceBid=true at 0.80×")
	}
	if iceAsk {
		t.Error("want iceAsk=false")
	}
	if icePrice == nil || *icePrice != 67000 {
		t.Errorf("want icePrice=67000 got %v", icePrice)
	}
}

func TestComputeIceberg_BidNotIceberg_BelowThreshold(t *testing.T) {
	// bidDepthClose = 0.79 * bidDepthOpen → just below threshold
	iceBid, _, _ := ComputeIceberg(
		fp(79), fp(100),
		fp(100), fp(100),
		fp(10), fp(0),
		ip(1), ip(0),
		fp(67000), fp(67001),
	)
	if iceBid {
		t.Error("want iceBid=false at 0.79×")
	}
}

func TestComputeIceberg_AskIceberg(t *testing.T) {
	iceBid, iceAsk, icePrice := ComputeIceberg(
		fp(100), fp(100), // bid not triggered
		fp(80), fp(100), // ask close=80, open=100 → 0.80×
		fp(0), fp(10), // buyVol=0, sellVol=10 > 0
		ip(0), ip(1), // bidArrivals=0, askArrivals=1
		fp(67000), fp(67001),
	)
	if iceBid {
		t.Error("want iceBid=false")
	}
	if !iceAsk {
		t.Error("want iceAsk=true")
	}
	if icePrice == nil || *icePrice != 67001 {
		t.Errorf("want icePrice=67001 (ask) got %v", icePrice)
	}
}

func TestComputeIceberg_BothDetected_UsesBidPrice(t *testing.T) {
	iceBid, iceAsk, icePrice := ComputeIceberg(
		fp(80), fp(100),
		fp(80), fp(100),
		fp(10), fp(10),
		ip(1), ip(1),
		fp(67000), fp(67001),
	)
	if !iceBid || !iceAsk {
		t.Error("want both detected")
	}
	if icePrice == nil || *icePrice != 67000 {
		t.Errorf("want icePrice=67000 (bid takes priority) got %v", icePrice)
	}
}

func TestComputeIceberg_NeitherDetected(t *testing.T) {
	iceBid, iceAsk, icePrice := ComputeIceberg(
		nil, nil, nil, nil,
		nil, nil,
		nil, nil,
		nil, nil,
	)
	if iceBid || iceAsk || icePrice != nil {
		t.Error("want all nil/false when inputs are nil")
	}
}
