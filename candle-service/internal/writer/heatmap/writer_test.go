package heatmap

import (
	"context"
	"fmt"
	"math/big"
	"testing"
	"time"

	qdb "github.com/questdb/go-questdb-client/v3"
)

// heatmapRow captures the data written for a single ILP row.
type heatmapRow struct {
	level    int64
	bidPrice float64
	bidSize  float64
	askPrice float64
	askSize  float64
}

// fakeLineSender implements qdb.LineSender, recording heatmap rows.
type fakeLineSender struct {
	rows    []heatmapRow
	current heatmapRow
	flushed int
}

func (f *fakeLineSender) Table(_ string) qdb.LineSender                           { return f }
func (f *fakeLineSender) Symbol(_, _ string) qdb.LineSender                      { return f }
func (f *fakeLineSender) StringColumn(_, _ string) qdb.LineSender                { return f }
func (f *fakeLineSender) BoolColumn(_ string, _ bool) qdb.LineSender             { return f }
func (f *fakeLineSender) Long256Column(_ string, _ *big.Int) qdb.LineSender      { return f }
func (f *fakeLineSender) TimestampColumn(_ string, _ time.Time) qdb.LineSender   { return f }
func (f *fakeLineSender) AtNow(_ context.Context) error                           { return nil }
func (f *fakeLineSender) Close(_ context.Context) error                           { return nil }

func (f *fakeLineSender) Int64Column(name string, val int64) qdb.LineSender {
	if name == "level" {
		f.current.level = val
	}
	return f
}

func (f *fakeLineSender) Float64Column(name string, val float64) qdb.LineSender {
	switch name {
	case "bid_price":
		f.current.bidPrice = val
	case "bid_size":
		f.current.bidSize = val
	case "ask_price":
		f.current.askPrice = val
	case "ask_size":
		f.current.askSize = val
	}
	return f
}

func (f *fakeLineSender) At(_ context.Context, _ time.Time) error {
	f.rows = append(f.rows, f.current)
	f.current = heatmapRow{}
	return nil
}

func (f *fakeLineSender) Flush(_ context.Context) error { f.flushed++; return nil }

func itoa(i int) string { return fmt.Sprintf("%d", i) }

func TestHeatmapWriter_LevelOrder(t *testing.T) {
	fake := &fakeLineSender{}
	w := NewWithSender(fake, "kucoin", "BTCUSDT")
	bids := map[string]string{"100": "1", "102": "2", "101": "3"}
	asks := map[string]string{"103": "4", "105": "5", "104": "6"}
	if err := w.WriteHeatmap(context.Background(), 1000, bids, asks); err != nil {
		t.Fatal(err)
	}
	if len(fake.rows) != 3 {
		t.Fatalf("want 3 rows, got %d", len(fake.rows))
	}
	// Level 1: best bid = 102, best ask = 103
	if fake.rows[0].level != 1 || fake.rows[0].bidPrice != 102 || fake.rows[0].askPrice != 103 {
		t.Errorf("level 1: bid=%v ask=%v, want bid=102 ask=103", fake.rows[0].bidPrice, fake.rows[0].askPrice)
	}
	// Level 2: bid=101, ask=104
	if fake.rows[1].bidPrice != 101 || fake.rows[1].askPrice != 104 {
		t.Errorf("level 2: bid=%v ask=%v, want bid=101 ask=104", fake.rows[1].bidPrice, fake.rows[1].askPrice)
	}
	// Level 3: bid=100, ask=105
	if fake.rows[2].bidPrice != 100 || fake.rows[2].askPrice != 105 {
		t.Errorf("level 3: bid=%v ask=%v, want bid=100 ask=105", fake.rows[2].bidPrice, fake.rows[2].askPrice)
	}
}

func TestHeatmapWriter_ThinBook_BidOnly(t *testing.T) {
	fake := &fakeLineSender{}
	w := NewWithSender(fake, "kucoin", "BTCUSDT")
	bids := map[string]string{"100": "1", "101": "2", "102": "3"}
	asks := map[string]string{}
	if err := w.WriteHeatmap(context.Background(), 1000, bids, asks); err != nil {
		t.Fatal(err)
	}
	if len(fake.rows) != 3 {
		t.Fatalf("want 3 rows, got %d", len(fake.rows))
	}
	for i, row := range fake.rows {
		if row.askPrice != 0.0 || row.askSize != 0.0 {
			t.Errorf("row %d: ask fields should be 0.0, got price=%v size=%v", i+1, row.askPrice, row.askSize)
		}
	}
}

func TestHeatmapWriter_ThinBook_AskOnly(t *testing.T) {
	fake := &fakeLineSender{}
	w := NewWithSender(fake, "kucoin", "BTCUSDT")
	bids := map[string]string{}
	asks := map[string]string{"100": "1", "101": "2", "102": "3"}
	if err := w.WriteHeatmap(context.Background(), 1000, bids, asks); err != nil {
		t.Fatal(err)
	}
	if len(fake.rows) != 3 {
		t.Fatalf("want 3 rows, got %d", len(fake.rows))
	}
	for i, row := range fake.rows {
		if row.bidPrice != 0.0 || row.bidSize != 0.0 {
			t.Errorf("row %d: bid fields should be 0.0, got price=%v size=%v", i+1, row.bidPrice, row.bidSize)
		}
	}
}

func TestHeatmapWriter_Cap20(t *testing.T) {
	fake := &fakeLineSender{}
	w := NewWithSender(fake, "kucoin", "BTCUSDT")
	bids := make(map[string]string, 25)
	asks := make(map[string]string, 25)
	for i := 1; i <= 25; i++ {
		bids[itoa(i)] = "1"
		asks[itoa(i+100)] = "1"
	}
	if err := w.WriteHeatmap(context.Background(), 1000, bids, asks); err != nil {
		t.Fatal(err)
	}
	if len(fake.rows) != 20 {
		t.Errorf("want 20 rows (capped), got %d", len(fake.rows))
	}
}

func TestHeatmapWriter_EmptyBook(t *testing.T) {
	fake := &fakeLineSender{}
	w := NewWithSender(fake, "kucoin", "BTCUSDT")
	if err := w.WriteHeatmap(context.Background(), 1000, map[string]string{}, map[string]string{}); err != nil {
		t.Fatal(err)
	}
	if len(fake.rows) != 0 {
		t.Errorf("want 0 rows, got %d", len(fake.rows))
	}
	if fake.flushed != 0 {
		t.Errorf("want 0 flush calls for empty book, got %d", fake.flushed)
	}
}

func TestHeatmapWriter_ExcludesZeroSize(t *testing.T) {
	fake := &fakeLineSender{}
	w := NewWithSender(fake, "kucoin", "BTCUSDT")
	bids := map[string]string{"100": "1", "101": "0", "102": "2"} // 101 has size "0"
	asks := map[string]string{"103": "1"}
	if err := w.WriteHeatmap(context.Background(), 1000, bids, asks); err != nil {
		t.Fatal(err)
	}
	// 2 valid bid levels (100 and 102) + 1 ask level → 2 rows
	if len(fake.rows) != 2 {
		t.Errorf("want 2 rows (zero-size excluded), got %d", len(fake.rows))
	}
	// Best bid should be 102 (not 101 which was excluded)
	if fake.rows[0].bidPrice != 102 {
		t.Errorf("level 1 bid = %v, want 102", fake.rows[0].bidPrice)
	}
}
