// Package heatmap writes per-second top-20 orderbook levels to QuestDB orderbook_heatmap via ILP.
// Each WriteHeatmap call produces up to 20 rows (one per level index, pairing bid and ask).
// Fire-and-forget: errors are logged at WARN and never propagate to the flush pipeline.
package heatmap

import (
	"context"
	"fmt"
	"log/slog"
	"math"
	"sort"
	"strconv"
	"time"

	qdb "github.com/questdb/go-questdb-client/v3"
)

// Writer writes heatmap rows to QuestDB orderbook_heatmap via ILP.
// One instance per (exchange, symbol). Not goroutine-safe; call from single consumer goroutine.
type Writer struct {
	sender   qdb.LineSender
	exchange string
	symbol   string
}

// New creates a Writer connecting to QuestDB ILP via TCP.
func New(ctx context.Context, ilpAddr, exchange, symbol string) (*Writer, error) {
	sender, err := qdb.NewLineSender(ctx, qdb.WithTcp(), qdb.WithAddress(ilpAddr))
	if err != nil {
		return nil, fmt.Errorf("heatmap writer: connect ILP %s: %w", ilpAddr, err)
	}
	return &Writer{sender: sender, exchange: exchange, symbol: symbol}, nil
}

// NewWithSender creates a Writer using an injected sender (for tests).
func NewWithSender(sender qdb.LineSender, exchange, symbol string) *Writer {
	return &Writer{sender: sender, exchange: exchange, symbol: symbol}
}

// WriteHeatmap writes up to 20 ILP rows to orderbook_heatmap.
// Level i pairs bid rank i (sorted desc by price) and ask rank i (sorted asc by price).
// Rows where both sides are absent are skipped; one-sided rows use 0.0 for the absent side.
// Returns the first error encountered; caller logs at WARN and discards (fire-and-forget).
func (w *Writer) WriteHeatmap(ctx context.Context, tsSecMs int64, bids, asks map[string]string) error {
	sortedBids := sortedLevels(bids, true)
	sortedAsks := sortedLevels(asks, false)

	ts := time.UnixMilli(tsSecMs)
	maxLevels := len(sortedBids)
	if len(sortedAsks) > maxLevels {
		maxLevels = len(sortedAsks)
	}
	if maxLevels > 20 {
		maxLevels = 20
	}
	if maxLevels == 0 {
		return nil
	}

	for i := 0; i < maxLevels; i++ {
		var bidPrice, bidSize, askPrice, askSize float64
		if i < len(sortedBids) {
			bidPrice = sortedBids[i].price
			bidSize = sortedBids[i].size
		}
		if i < len(sortedAsks) {
			askPrice = sortedAsks[i].price
			askSize = sortedAsks[i].size
		}

		if err := w.sender.
			Table("orderbook_heatmap").
			Symbol("exchange", w.exchange).
			Symbol("symbol", w.symbol).
			Int64Column("level", int64(i+1)).
			Float64Column("bid_price", bidPrice).
			Float64Column("bid_size", bidSize).
			Float64Column("ask_price", askPrice).
			Float64Column("ask_size", askSize).
			At(ctx, ts); err != nil {
			if ctx.Err() == nil {
				slog.WarnContext(ctx, "heatmap: ilp write failed",
					"exchange", w.exchange, "symbol", w.symbol, "level", i+1, "error", err)
			}
			_ = w.sender.Flush(ctx) // flush buffered rows before returning to avoid poisoning next call
			return err
		}
	}

	if err := w.sender.Flush(ctx); err != nil {
		if ctx.Err() == nil {
			slog.WarnContext(ctx, "heatmap: ilp flush failed",
				"exchange", w.exchange, "symbol", w.symbol, "error", err)
		}
		return err
	}
	return nil
}

// Close closes the underlying ILP sender.
func (w *Writer) Close(ctx context.Context) error {
	return w.sender.Close(ctx)
}

// levelEntry holds a parsed price level for sorting.
type levelEntry struct {
	price float64
	size  float64
}

// sortedLevels converts a price→size map to a slice sorted by price.
// descending=true for bids (best bid first); false for asks (best ask first).
// Excludes levels with size "0" or "" and skips unparseable price keys.
func sortedLevels(m map[string]string, descending bool) []levelEntry {
	levels := make([]levelEntry, 0, len(m))
	for priceStr, sizeStr := range m {
		if sizeStr == "0" || sizeStr == "" {
			continue
		}
		p, err := strconv.ParseFloat(priceStr, 64)
		if err != nil || math.IsNaN(p) || math.IsInf(p, 0) {
			continue
		}
		s, err := strconv.ParseFloat(sizeStr, 64)
		if err != nil {
			continue
		}
		levels = append(levels, levelEntry{price: p, size: s})
	}
	sort.Slice(levels, func(i, j int) bool {
		if descending {
			return levels[i].price > levels[j].price
		}
		return levels[i].price < levels[j].price
	})
	return levels
}
