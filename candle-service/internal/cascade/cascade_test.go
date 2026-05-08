package cascade_test

import (
	"testing"
	"time"

	"github.com/mrqdt/magnum-opus/candle-service/internal/accumulator"
	"github.com/mrqdt/magnum-opus/candle-service/internal/cascade"
)

func fp(v float64) *float64 { return &v }

func msOf(t time.Time) int64 { return t.UnixMilli() }

// newBar builds a minimal accumulator.Bar for testing.
func newBar(tsSecMs int64, open, high, low, close_ float64, vol, quoteVol float64, tradeCount, gapCount int) accumulator.Bar {
	return accumulator.Bar{
		TsSecMs:     tsSecMs,
		Open:        fp(open),
		High:        fp(high),
		Low:         fp(low),
		Close:       fp(close_),
		Volume:      fp(vol),
		QuoteVolume: fp(quoteVol),
		TradeCount:  tradeCount,
		GapCount:    gapCount,
	}
}

// nilBar builds a bar with nil OHLCV (no-trade second).
func nilBar(tsSecMs int64) accumulator.Bar {
	return accumulator.Bar{TsSecMs: tsSecMs}
}

func TestIsBarClose(t *testing.T) {
	// Sunday May 3, 2026 23:59:59 UTC — next second is Monday May 4 00:00:00 UTC (1w closes)
	sunday235959 := msOf(time.Date(2026, 5, 3, 23, 59, 59, 0, time.UTC))
	// Tuesday May 5, 2026 00:00:00 UTC — first second of a new bar, nothing closes
	tuesday000000 := msOf(time.Date(2026, 5, 5, 0, 0, 0, 0, time.UTC))

	tests := []struct {
		name    string
		tsSecMs int64
		want    map[cascade.TF]bool
	}{
		{
			name:    "00:00:59 epoch — 1m only",
			tsSecMs: 59_000,
			want: map[cascade.TF]bool{
				cascade.TF1m: true, cascade.TF5m: false, cascade.TF15m: false,
				cascade.TF1h: false, cascade.TF4h: false, cascade.TF1d: false, cascade.TF1w: false,
			},
		},
		{
			name:    "00:04:59 epoch — 1m and 5m",
			tsSecMs: 299_000,
			want: map[cascade.TF]bool{
				cascade.TF1m: true, cascade.TF5m: true, cascade.TF15m: false,
				cascade.TF1h: false, cascade.TF4h: false, cascade.TF1d: false, cascade.TF1w: false,
			},
		},
		{
			name:    "00:14:59 epoch — 1m, 5m, 15m",
			tsSecMs: 899_000,
			want: map[cascade.TF]bool{
				cascade.TF1m: true, cascade.TF5m: true, cascade.TF15m: true,
				cascade.TF1h: false, cascade.TF4h: false, cascade.TF1d: false, cascade.TF1w: false,
			},
		},
		{
			name:    "00:59:59 epoch — 1m, 5m, 15m, 1h",
			tsSecMs: 3_599_000,
			want: map[cascade.TF]bool{
				cascade.TF1m: true, cascade.TF5m: true, cascade.TF15m: true,
				cascade.TF1h: true, cascade.TF4h: false, cascade.TF1d: false, cascade.TF1w: false,
			},
		},
		{
			name:    "03:59:59 epoch — 1m, 5m, 15m, 1h, 4h",
			tsSecMs: 14_399_000,
			want: map[cascade.TF]bool{
				cascade.TF1m: true, cascade.TF5m: true, cascade.TF15m: true,
				cascade.TF1h: true, cascade.TF4h: true, cascade.TF1d: false, cascade.TF1w: false,
			},
		},
		{
			name:    "23:59:59 epoch Thu — 1m..1d, not 1w",
			tsSecMs: 86_399_000,
			want: map[cascade.TF]bool{
				cascade.TF1m: true, cascade.TF5m: true, cascade.TF15m: true,
				cascade.TF1h: true, cascade.TF4h: true, cascade.TF1d: true, cascade.TF1w: false,
			},
		},
		{
			name:    "2026-05-03 23:59:59 UTC Sunday — all TFs including 1w",
			tsSecMs: sunday235959,
			want: map[cascade.TF]bool{
				cascade.TF1m: true, cascade.TF5m: true, cascade.TF15m: true,
				cascade.TF1h: true, cascade.TF4h: true, cascade.TF1d: true, cascade.TF1w: true,
			},
		},
		{
			name:    "2026-05-05 00:00:00 UTC Tuesday — no closes",
			tsSecMs: tuesday000000,
			want: map[cascade.TF]bool{
				cascade.TF1m: false, cascade.TF5m: false, cascade.TF15m: false,
				cascade.TF1h: false, cascade.TF4h: false, cascade.TF1d: false, cascade.TF1w: false,
			},
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			for _, tf := range cascade.AllTFs {
				got := cascade.IsBarClose(tc.tsSecMs, tf)
				if got != tc.want[tf] {
					t.Errorf("IsBarClose(%d, %s) = %v, want %v", tc.tsSecMs, tf, got, tc.want[tf])
				}
			}
		})
	}
}

func TestFoldNilRules(t *testing.T) {
	eng := cascade.NewEngine("ex", "SYM", nil)
	baseMs := int64(3_600_000) // 01:00:00 UTC epoch — inside a 1h bar

	t.Run("nil OHLC bar advances volume and barCount only", func(t *testing.T) {
		eng2 := cascade.NewEngine("ex", "SYM", nil)
		tradeBar := newBar(baseMs, 100, 110, 90, 105, 50, 5000, 3, 0)
		eng2.Fold(tradeBar, baseMs)

		nilB := nilBar(baseMs + 1000)
		eng2.Fold(nilB, baseMs+1000)

		bar := eng2.CurrentBar(cascade.TF1m)
		if bar.Open == nil || *bar.Open != 100 {
			t.Errorf("Open should be 100, got %v", bar.Open)
		}
		if bar.High == nil || *bar.High != 110 {
			t.Errorf("High should be 110, got %v", bar.High)
		}
		if bar.Low == nil || *bar.Low != 90 {
			t.Errorf("Low should be 90, got %v", bar.Low)
		}
		if bar.Close == nil || *bar.Close != 105 {
			t.Errorf("Close should carry last known (105), got %v", bar.Close)
		}
		if bar.Volume != 50 {
			t.Errorf("Volume should be 50 (nil bar adds 0), got %v", bar.Volume)
		}
		if bar.BarCount != 2 {
			t.Errorf("BarCount should be 2 (always increments), got %d", bar.BarCount)
		}
		if bar.TradeCount != 3 {
			t.Errorf("TradeCount should be 3, got %d", bar.TradeCount)
		}
	})

	t.Run("barCount always increments even for nil bars", func(t *testing.T) {
		eng3 := cascade.NewEngine("ex", "SYM", nil)
		for i := 0; i < 5; i++ {
			eng3.Fold(nilBar(baseMs+int64(i)*1000), baseMs+int64(i)*1000)
		}
		bar := eng3.CurrentBar(cascade.TF1m)
		if bar.BarCount != 5 {
			t.Errorf("BarCount should be 5, got %d", bar.BarCount)
		}
		if bar.Open != nil {
			t.Errorf("Open should be nil for all-nil bars, got %v", bar.Open)
		}
	})

	t.Run("high tracks maximum, low tracks minimum", func(t *testing.T) {
		eng4 := cascade.NewEngine("ex", "SYM", nil)
		eng4.Fold(newBar(baseMs, 100, 120, 95, 110, 10, 1000, 1, 0), baseMs)
		eng4.Fold(newBar(baseMs+1000, 110, 115, 85, 112, 10, 1000, 1, 0), baseMs+1000)
		bar := eng4.CurrentBar(cascade.TF1m)
		if bar.High == nil || *bar.High != 120 {
			t.Errorf("High should be max(120,115)=120, got %v", bar.High)
		}
		if bar.Low == nil || *bar.Low != 85 {
			t.Errorf("Low should be min(95,85)=85, got %v", bar.Low)
		}
		if bar.Close == nil || *bar.Close != 112 {
			t.Errorf("Close should be last value (112), got %v", bar.Close)
		}
	})

	_ = eng
}

func TestFoldBoundary1m(t *testing.T) {
	eng := cascade.NewEngine("kucoin", "BTC-USDT", nil)

	// Fold 59 bars inside a 1m window (00:01:00 to 00:01:58 UTC epoch).
	baseMs := int64(60_000) // 00:01:00 UTC
	for i := 0; i < 59; i++ {
		ts := baseMs + int64(i)*1000
		bar := newBar(ts, 100+float64(i), 110+float64(i), 90+float64(i), 105+float64(i), 10, 1000, 1, 0)
		closed := eng.Fold(bar, ts)
		if len(closed) != 0 {
			t.Fatalf("expected no closes before boundary, got %d at i=%d", len(closed), i)
		}
	}

	// Fold bar 60 — tsSecMs = 00:01:59, which closes the 1m bar.
	ts59 := baseMs + 59_000 // 00:01:59 UTC
	bar60 := newBar(ts59, 159, 169, 149, 164, 10, 1000, 1, 0)
	closed := eng.Fold(bar60, ts59)

	// Expect exactly 1 close: the 1m bar.
	var found1m *cascade.Bar
	for i := range closed {
		if closed[i].TF == cascade.TF1m {
			found1m = &closed[i]
		}
	}
	if found1m == nil {
		t.Fatal("expected 1m close in Fold result, got none")
	}
	if !found1m.IsComplete {
		t.Error("closed bar should have IsComplete=true")
	}
	if found1m.BarCount != 60 {
		t.Errorf("closed 1m bar BarCount = %d, want 60", found1m.BarCount)
	}
	if found1m.Open == nil || *found1m.Open != 100 {
		t.Errorf("closed 1m bar Open = %v, want 100", found1m.Open)
	}

	// After close, 1m accumulator is reset with openTs = ts59 + 1000.
	current := eng.CurrentBar(cascade.TF1m)
	if current.BarCount != 0 {
		t.Errorf("CurrentBar(1m).BarCount = %d after reset, want 0", current.BarCount)
	}
	if current.Open != nil {
		t.Errorf("CurrentBar(1m).Open should be nil after reset, got %v", current.Open)
	}
	wantOpenTs := ts59 + 1000
	if current.OpenTs != wantOpenTs {
		t.Errorf("CurrentBar(1m).OpenTs = %d, want %d", current.OpenTs, wantOpenTs)
	}
}

func TestFoldBoundary1w(t *testing.T) {
	eng := cascade.NewEngine("bybit", "ETH-USDT", nil)

	// Sunday May 3, 2026 23:59:59 UTC — next second is Monday May 4 00:00:00 UTC.
	sunday235959 := msOf(time.Date(2026, 5, 3, 23, 59, 59, 0, time.UTC))
	bar := newBar(sunday235959, 2000, 2100, 1900, 2050, 100, 200000, 50, 0)
	closed := eng.Fold(bar, sunday235959)

	var found1w, found1d bool
	for _, b := range closed {
		if b.TF == cascade.TF1w {
			found1w = true
			if !b.IsComplete {
				t.Error("1w bar should be complete")
			}
		}
		if b.TF == cascade.TF1d {
			found1d = true
		}
	}
	if !found1w {
		t.Error("expected 1w close at Sunday 23:59:59, got none")
	}
	if !found1d {
		t.Error("expected 1d close at Sunday 23:59:59, got none")
	}

	// After 1w close, accumulator is reset with openTs = sunday235959 + 1000.
	current := eng.CurrentBar(cascade.TF1w)
	wantOpenTs := sunday235959 + 1000
	if current.OpenTs != wantOpenTs {
		t.Errorf("CurrentBar(TF1w).OpenTs = %d after close, want %d", current.OpenTs, wantOpenTs)
	}
	if current.BarCount != 0 {
		t.Errorf("CurrentBar(TF1w).BarCount = %d after close, want 0", current.BarCount)
	}
}

func TestHashRoundTrip(t *testing.T) {
	eng := cascade.NewEngine("kucoin", "BTC-USDT", nil)

	// Fold a few bars to populate state.
	baseMs := int64(3_600_000)
	eng.Fold(newBar(baseMs, 30000, 31000, 29000, 30500, 5.5, 165000, 12, 1), baseMs)
	eng.Fold(newBar(baseMs+1000, 30500, 32000, 30000, 31500, 7.25, 220000, 18, 0), baseMs+1000)

	for _, tf := range cascade.AllTFs {
		fields := eng.ToHash(tf)

		// Verify all 10 keys are present.
		required := []string{"open_ts", "open", "high", "low", "close", "volume", "quote_vol", "trade_count", "bar_count", "gap_count"}
		for _, k := range required {
			if _, ok := fields[k]; !ok {
				t.Errorf("ToHash(%s) missing key %q", tf, k)
			}
		}

		// Restore into a fresh engine and compare.
		eng2 := cascade.NewEngine("kucoin", "BTC-USDT", nil)
		eng2.RestoreFromHash(tf, fields)

		orig := eng.CurrentBar(tf)
		restored := eng2.CurrentBar(tf)

		if orig.OpenTs != restored.OpenTs {
			t.Errorf("tf=%s: OpenTs orig=%d restored=%d", tf, orig.OpenTs, restored.OpenTs)
		}
		if orig.Volume != restored.Volume {
			t.Errorf("tf=%s: Volume orig=%v restored=%v", tf, orig.Volume, restored.Volume)
		}
		if orig.QuoteVol != restored.QuoteVol {
			t.Errorf("tf=%s: QuoteVol orig=%v restored=%v", tf, orig.QuoteVol, restored.QuoteVol)
		}
		if orig.BarCount != restored.BarCount {
			t.Errorf("tf=%s: BarCount orig=%d restored=%d", tf, orig.BarCount, restored.BarCount)
		}
		if orig.GapCount != restored.GapCount {
			t.Errorf("tf=%s: GapCount orig=%d restored=%d", tf, orig.GapCount, restored.GapCount)
		}
		if orig.TradeCount != restored.TradeCount {
			t.Errorf("tf=%s: TradeCount orig=%d restored=%d", tf, orig.TradeCount, restored.TradeCount)
		}

		floatEq := func(name string, a, b *float64) {
			if a == nil && b == nil {
				return
			}
			if a == nil || b == nil {
				t.Errorf("tf=%s: %s: one nil orig=%v restored=%v", tf, name, a, b)
				return
			}
			if *a != *b {
				t.Errorf("tf=%s: %s orig=%v restored=%v", tf, name, *a, *b)
			}
		}
		floatEq("Open", orig.Open, restored.Open)
		floatEq("High", orig.High, restored.High)
		floatEq("Low", orig.Low, restored.Low)
		floatEq("Close", orig.Close, restored.Close)
	}
}

// TestFoldTableDriven is a table-driven test covering the required fold scenarios from AC 6.
func TestFoldTableDriven(t *testing.T) {
	baseMs := int64(3_600_000) // 01:00:00 UTC — well inside a 1h bar

	tests := []struct {
		name       string
		bars       []accumulator.Bar // bars to fold in order
		checkTF    cascade.TF
		wantOpen   *float64
		wantClose  *float64
		wantVol    float64
		wantBar    int
		wantTrade  int
	}{
		{
			name:      "single non-nil bar populates all OHLCV fields",
			bars:      []accumulator.Bar{newBar(baseMs, 100, 110, 90, 105, 50, 5000, 3, 0)},
			checkTF:   cascade.TF1m,
			wantOpen:  fp(100),
			wantClose: fp(105),
			wantVol:   50,
			wantBar:   1,
			wantTrade: 3,
		},
		{
			name: "nil OHLC bar: volume unchanged, barCount increments",
			bars: []accumulator.Bar{
				newBar(baseMs, 100, 110, 90, 105, 50, 5000, 3, 0),
				nilBar(baseMs + 1000),
			},
			checkTF:   cascade.TF1m,
			wantOpen:  fp(100),
			wantClose: fp(105), // close carries last known
			wantVol:   50,      // nil volume adds 0
			wantBar:   2,       // barCount always +1
			wantTrade: 3,
		},
		{
			name: "two bars: bar1 has close, bar2 nil — close carries",
			bars: []accumulator.Bar{
				newBar(baseMs, 200, 210, 190, 205, 20, 2000, 5, 0),
				nilBar(baseMs + 1000),
			},
			checkTF:   cascade.TF5m,
			wantOpen:  fp(200),
			wantClose: fp(205), // carried from bar1
			wantVol:   20,
			wantBar:   2,
			wantTrade: 5,
		},
		{
			name: "two bars: bar1 nil, bar2 has trades — open set from bar2",
			bars: []accumulator.Bar{
				nilBar(baseMs),
				newBar(baseMs+1000, 300, 320, 280, 310, 30, 9000, 8, 0),
			},
			checkTF:   cascade.TF1m,
			wantOpen:  fp(300), // first non-nil open
			wantClose: fp(310),
			wantVol:   30,
			wantBar:   2,
			wantTrade: 8,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			eng := cascade.NewEngine("ex", "SYM", nil)
			for _, b := range tc.bars {
				eng.Fold(b, b.TsSecMs)
			}
			bar := eng.CurrentBar(tc.checkTF)

			if tc.wantOpen == nil {
				if bar.Open != nil {
					t.Errorf("Open: want nil, got %v", *bar.Open)
				}
			} else if bar.Open == nil || *bar.Open != *tc.wantOpen {
				t.Errorf("Open: want %v, got %v", *tc.wantOpen, bar.Open)
			}

			if tc.wantClose == nil {
				if bar.Close != nil {
					t.Errorf("Close: want nil, got %v", *bar.Close)
				}
			} else if bar.Close == nil || *bar.Close != *tc.wantClose {
				t.Errorf("Close: want %v, got %v", *tc.wantClose, bar.Close)
			}

			if bar.Volume != tc.wantVol {
				t.Errorf("Volume: want %v, got %v", tc.wantVol, bar.Volume)
			}
			if bar.BarCount != tc.wantBar {
				t.Errorf("BarCount: want %d, got %d", tc.wantBar, bar.BarCount)
			}
			if bar.TradeCount != tc.wantTrade {
				t.Errorf("TradeCount: want %d, got %d", tc.wantTrade, bar.TradeCount)
			}
		})
	}
}

func TestRestoreFromHashUnknownKeysIgnored(t *testing.T) {
	eng := cascade.NewEngine("ex", "SYM", nil)
	fields := map[string]string{
		"open_ts":     "1000",
		"open":        "100.5",
		"high":        "110.0",
		"low":         "99.0",
		"close":       "105.0",
		"volume":      "50.0",
		"quote_vol":   "5000.0",
		"trade_count": "10",
		"bar_count":   "5",
		"gap_count":   "0",
		"future_field": "should be ignored",
	}
	eng.RestoreFromHash(cascade.TF1m, fields)
	bar := eng.CurrentBar(cascade.TF1m)
	if bar.OpenTs != 1000 {
		t.Errorf("OpenTs = %d, want 1000", bar.OpenTs)
	}
	if bar.BarCount != 5 {
		t.Errorf("BarCount = %d, want 5", bar.BarCount)
	}
}

func TestRestoreFromHashEmptyFieldsTreatedAsZeroNil(t *testing.T) {
	eng := cascade.NewEngine("ex", "SYM", nil)
	fields := map[string]string{
		"open_ts":     "5000",
		"open":        "",
		"high":        "",
		"low":         "",
		"close":       "",
		"volume":      "0",
		"quote_vol":   "0",
		"trade_count": "0",
		"bar_count":   "3",
		"gap_count":   "0",
	}
	eng.RestoreFromHash(cascade.TF1h, fields)
	bar := eng.CurrentBar(cascade.TF1h)
	if bar.Open != nil {
		t.Errorf("Open should be nil for empty string, got %v", bar.Open)
	}
	if bar.BarCount != 3 {
		t.Errorf("BarCount = %d, want 3", bar.BarCount)
	}
}
