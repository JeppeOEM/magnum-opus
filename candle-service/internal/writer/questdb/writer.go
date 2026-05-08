// Package questdb writes 1-second OHLCV bars to QuestDB snapshot_1s via ILP.
// Single ILP sender owned by the Writer — not goroutine-safe; caller (consumer
// goroutine) must not call WriteBar concurrently.
package questdb

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	qdb "github.com/questdb/go-questdb-client/v3"

	"github.com/mrqdt/magnum-opus/candle-service/internal/accumulator"
	"github.com/mrqdt/magnum-opus/candle-service/internal/backoff"
)

// WriterConfig holds configuration for the QuestDB writer.
type WriterConfig struct {
	ILPAddr          string        // TCP ILP address, e.g. "localhost:9009"
	HTTPAddr         string        // REST HTTP address, e.g. "localhost:9000"
	FlushInterval    time.Duration // ILP batch flush interval
	WALProbeInterval time.Duration // How often to probe WAL when stale
	WALBufferSize    int           // Max bars buffered during WAL suspension
}

// walEntry holds a bar queued during WAL suspension.
type walEntry struct {
	bar accumulator.Bar
}

// Writer writes bars to QuestDB snapshot_1s via ILP.
type Writer struct {
	cfg    WriterConfig
	sender qdb.LineSender
	logger *slog.Logger

	walSuspended atomic.Bool
	lastWrite    atomic.Value // stores time.Time

	walMu  sync.Mutex
	walBuf []walEntry // ring-like bounded buffer (slice, dropped from front on overflow)

	walDropTotal   func()         // increments candle_wal_drop_total; injected for testing
	writeLatencyMs func(float64)  // observes ILP write latency in ms; injected for testing
}

// New creates and connects a Writer.
// The caller must call Close() when done.
func New(ctx context.Context, cfg WriterConfig, logger *slog.Logger) (*Writer, error) {
	sender, err := qdb.NewLineSender(ctx, qdb.WithTcp(), qdb.WithAddress(cfg.ILPAddr))
	if err != nil {
		return nil, fmt.Errorf("questdb writer: connect ILP %s: %w", cfg.ILPAddr, err)
	}
	w := &Writer{
		cfg:            cfg,
		sender:         sender,
		logger:         logger,
		walDropTotal:   func() {},        // default no-op; overridden in cmd/candle with real counter
		writeLatencyMs: func(float64) {}, // default no-op; overridden in cmd/candle with real histogram
	}
	w.lastWrite.Store(time.Now())
	return w, nil
}

// SetWALDropCounter wires the Prometheus counter increment for WAL drops.
func (w *Writer) SetWALDropCounter(fn func()) {
	w.walDropTotal = fn
}

// SetWriteLatencyObserver wires the Prometheus histogram observer for ILP write latency.
func (w *Writer) SetWriteLatencyObserver(fn func(float64)) {
	w.writeLatencyMs = fn
}

// StartWALProbe launches the background WAL probe goroutine.
// It exits when ctx is cancelled.
func (w *Writer) StartWALProbe(ctx context.Context) {
	go w.runWALProbe(ctx)
}

// WriteBar writes a single bar to QuestDB.
// During WAL suspension the bar is queued to the WAL buffer instead.
func (w *Writer) WriteBar(ctx context.Context, bar accumulator.Bar) error {
	if w.walSuspended.Load() {
		w.enqueueWAL(bar)
		return nil
	}
	return w.writeWithRetry(ctx, bar)
}

// Flush flushes any buffered ILP rows to QuestDB.
func (w *Writer) Flush(ctx context.Context) error {
	return w.sender.Flush(ctx)
}

// Close flushes and closes the ILP sender gracefully.
func (w *Writer) Close(ctx context.Context) error {
	if err := w.sender.Flush(ctx); err != nil {
		w.logger.WarnContext(ctx, "questdb: flush on close failed", "error", err)
	}
	return w.sender.Close(ctx)
}

// writeWithRetry writes bar to ILP with exponential backoff retry.
func (w *Writer) writeWithRetry(ctx context.Context, bar accumulator.Bar) error {
	bo := backoff.New(time.Second, 30*time.Second, 2.0, 0.2)
	for attempt := 0; ; attempt++ {
		start := time.Now()
		if err := w.writeBar(ctx, bar); err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			delay := bo.Next(attempt)
			w.logger.WarnContext(ctx, "questdb: ILP write failed, retrying",
				"attempt", attempt, "delay", delay, "error", err)
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(delay):
			}
			continue
		}
		w.writeLatencyMs(float64(time.Since(start).Milliseconds()))
		w.lastWrite.Store(time.Now())
		return nil
	}
}

// writeBar writes a single bar row to the ILP sender and flushes.
func (w *Writer) writeBar(ctx context.Context, bar accumulator.Bar) error {
	row := w.sender.Table("snapshot_1s").
		Symbol("exchange", bar.Exchange).
		Symbol("symbol", bar.Symbol)

	// OHLCV — only written if there were trades
	if bar.Open != nil {
		row = row.Float64Column("open", *bar.Open)
	}
	if bar.High != nil {
		row = row.Float64Column("high", *bar.High)
	}
	if bar.Low != nil {
		row = row.Float64Column("low", *bar.Low)
	}
	if bar.Close != nil {
		row = row.Float64Column("close", *bar.Close)
	}
	if bar.Volume != nil {
		row = row.Float64Column("volume", *bar.Volume)
	}
	if bar.QuoteVolume != nil {
		row = row.Float64Column("quote_volume", *bar.QuoteVolume)
	}
	if bar.TWAP != nil {
		row = row.Float64Column("twap", *bar.TWAP)
	}
	row = row.Int64Column("trade_count", int64(bar.TradeCount))

	// OB best quotes
	if bar.BestBidOpen != nil {
		row = row.Float64Column("best_bid_open", *bar.BestBidOpen)
	}
	if bar.BestAskOpen != nil {
		row = row.Float64Column("best_ask_open", *bar.BestAskOpen)
	}
	if bar.BestBid != nil {
		row = row.Float64Column("best_bid", *bar.BestBid)
	}
	if bar.BestAsk != nil {
		row = row.Float64Column("best_ask", *bar.BestAsk)
	}

	// OB depth at open
	if bar.BidDepthL1Open != nil {
		row = row.Float64Column("bid_depth_l1_open", *bar.BidDepthL1Open)
	}
	if bar.AskDepthL1Open != nil {
		row = row.Float64Column("ask_depth_l1_open", *bar.AskDepthL1Open)
	}
	if bar.BidDepthL2Open != nil {
		row = row.Float64Column("bid_depth_l2_open", *bar.BidDepthL2Open)
	}
	if bar.AskDepthL2Open != nil {
		row = row.Float64Column("ask_depth_l2_open", *bar.AskDepthL2Open)
	}
	if bar.BidDepthTop10Open != nil {
		row = row.Float64Column("bid_depth_top10_open", *bar.BidDepthTop10Open)
	}
	if bar.AskDepthTop10Open != nil {
		row = row.Float64Column("ask_depth_top10_open", *bar.AskDepthTop10Open)
	}
	if bar.BidDepthTotalOpen != nil {
		row = row.Float64Column("bid_depth_total_open", *bar.BidDepthTotalOpen)
	}
	if bar.AskDepthTotalOpen != nil {
		row = row.Float64Column("ask_depth_total_open", *bar.AskDepthTotalOpen)
	}

	// OB depth at close
	if bar.BidDepthL1Close != nil {
		row = row.Float64Column("bid_depth_l1_close", *bar.BidDepthL1Close)
	}
	if bar.AskDepthL1Close != nil {
		row = row.Float64Column("ask_depth_l1_close", *bar.AskDepthL1Close)
	}
	if bar.BidDepthL2Close != nil {
		row = row.Float64Column("bid_depth_l2_close", *bar.BidDepthL2Close)
	}
	if bar.AskDepthL2Close != nil {
		row = row.Float64Column("ask_depth_l2_close", *bar.AskDepthL2Close)
	}
	if bar.BidDepthTop10Close != nil {
		row = row.Float64Column("bid_depth_top10_close", *bar.BidDepthTop10Close)
	}
	if bar.AskDepthTop10Close != nil {
		row = row.Float64Column("ask_depth_top10_close", *bar.AskDepthTop10Close)
	}
	if bar.BidDepthTotalClose != nil {
		row = row.Float64Column("bid_depth_total_close", *bar.BidDepthTotalClose)
	}
	if bar.AskDepthTotalClose != nil {
		row = row.Float64Column("ask_depth_total_close", *bar.AskDepthTotalClose)
	}

	// Book shape (close-only)
	if bar.WeightedBidPrice != nil {
		row = row.Float64Column("weighted_bid_price", *bar.WeightedBidPrice)
	}
	if bar.WeightedAskPrice != nil {
		row = row.Float64Column("weighted_ask_price", *bar.WeightedAskPrice)
	}

	// Market impact (close-only)
	if bar.DepthTo1PctBid != nil {
		row = row.Float64Column("depth_to_1pct_bid", *bar.DepthTo1PctBid)
	}
	if bar.DepthTo1PctAsk != nil {
		row = row.Float64Column("depth_to_1pct_ask", *bar.DepthTo1PctAsk)
	}

	// Mid-price path
	if bar.MidPriceOpen != nil {
		row = row.Float64Column("mid_price_open", *bar.MidPriceOpen)
	}
	if bar.MidPriceHigh != nil {
		row = row.Float64Column("mid_price_high", *bar.MidPriceHigh)
	}
	if bar.MidPriceLow != nil {
		row = row.Float64Column("mid_price_low", *bar.MidPriceLow)
	}
	if bar.VWMP != nil {
		row = row.Float64Column("vwmp", *bar.VWMP)
	}

	// Spread
	if bar.SpreadHigh != nil {
		row = row.Float64Column("spread_high", *bar.SpreadHigh)
	}
	if bar.SpreadLow != nil {
		row = row.Float64Column("spread_low", *bar.SpreadLow)
	}
	if bar.SpreadMean != nil {
		row = row.Float64Column("spread_mean", *bar.SpreadMean)
	}
	if bar.EffectiveSpread != nil {
		row = row.Float64Column("effective_spread", *bar.EffectiveSpread)
	}

	// Trade flow
	if bar.BuyVolume != nil {
		row = row.Float64Column("buy_volume", *bar.BuyVolume)
	}
	if bar.BuyCount != nil {
		row = row.Int64Column("buy_count", int64(*bar.BuyCount))
	}

	// Block trades
	if bar.BlockBuyVolume != nil {
		row = row.Float64Column("block_buy_volume", *bar.BlockBuyVolume)
	}
	if bar.BlockSellVolume != nil {
		row = row.Float64Column("block_sell_volume", *bar.BlockSellVolume)
	}
	if bar.LargeBidOrders != nil {
		row = row.Int64Column("large_bid_orders", int64(*bar.LargeBidOrders))
	}
	if bar.LargeAskOrders != nil {
		row = row.Int64Column("large_ask_orders", int64(*bar.LargeAskOrders))
	}

	// Trade distribution
	if bar.MaxTradeSize != nil {
		row = row.Float64Column("max_trade_size", *bar.MaxTradeSize)
	}
	if bar.FirstTradeOffsetMs != nil {
		row = row.Int64Column("first_trade_offset_ms", int64(*bar.FirstTradeOffsetMs))
	}
	if bar.LastTradeOffsetMs != nil {
		row = row.Int64Column("last_trade_offset_ms", int64(*bar.LastTradeOffsetMs))
	}
	if bar.TradeClustering != nil {
		row = row.Float64Column("trade_clustering", *bar.TradeClustering)
	}
	if bar.MaxConsecutiveRun != nil {
		row = row.Int64Column("max_consecutive_run", int64(*bar.MaxConsecutiveRun))
	}

	// Volatility
	if bar.RealizedVol != nil {
		row = row.Float64Column("realized_vol", *bar.RealizedVol)
	}
	if bar.RealizedSkewness != nil {
		row = row.Float64Column("realized_skewness", *bar.RealizedSkewness)
	}
	if bar.UptickCount != nil {
		row = row.Int64Column("uptick_count", int64(*bar.UptickCount))
	}
	if bar.DowntickCount != nil {
		row = row.Int64Column("downtick_count", int64(*bar.DowntickCount))
	}

	// OB activity
	if bar.BidOrderArrivals != nil {
		row = row.Int64Column("bid_order_arrivals", int64(*bar.BidOrderArrivals))
	}
	if bar.AskOrderArrivals != nil {
		row = row.Int64Column("ask_order_arrivals", int64(*bar.AskOrderArrivals))
	}
	if bar.BidCancelCount != nil {
		row = row.Int64Column("bid_cancel_count", int64(*bar.BidCancelCount))
	}
	if bar.AskCancelCount != nil {
		row = row.Int64Column("ask_cancel_count", int64(*bar.AskCancelCount))
	}
	if bar.OBModifyCount != nil {
		row = row.Int64Column("ob_modify_count", int64(*bar.OBModifyCount))
	}
	if bar.AvgBidOrderSize != nil {
		row = row.Float64Column("avg_bid_order_size", *bar.AvgBidOrderSize)
	}
	if bar.AvgAskOrderSize != nil {
		row = row.Float64Column("avg_ask_order_size", *bar.AvgAskOrderSize)
	}
	if bar.BestBidChanges != nil {
		row = row.Int64Column("best_bid_changes", int64(*bar.BestBidChanges))
	}
	if bar.BestAskChanges != nil {
		row = row.Int64Column("best_ask_changes", int64(*bar.BestAskChanges))
	}
	if bar.QuoteStuffRatio != nil {
		row = row.Float64Column("quote_stuff_ratio", *bar.QuoteStuffRatio)
	}

	// Trade microstructure
	if bar.TradeSignAutocorr != nil {
		row = row.Float64Column("trade_sign_autocorr", *bar.TradeSignAutocorr)
	}
	if bar.InterTradeIntervalStdMs != nil {
		row = row.Float64Column("inter_trade_interval_std_ms", *bar.InterTradeIntervalStdMs)
	}
	if bar.NumTradePriceLevels != nil {
		row = row.Int64Column("num_trade_price_levels", int64(*bar.NumTradePriceLevels))
	}

	// OFI — nil when no ticks received this second
	if bar.OFI != nil {
		row = row.Float64Column("ofi", *bar.OFI)
	}
	if bar.OFIL1 != nil {
		row = row.Float64Column("ofi_l1", *bar.OFIL1)
	}

	// Quality — always written
	row = row.BoolColumn("is_partial", bar.IsPartial).
		Int64Column("gap_count", int64(bar.GapCount)).
		Int64Column("bar_count", int64(bar.BarCount))

	if err := row.At(ctx, time.UnixMilli(bar.TsSecMs)); err != nil {
		return fmt.Errorf("questdb: row.At: %w", err)
	}
	return w.sender.Flush(ctx)
}

// enqueueWAL adds bar to the WAL buffer, dropping oldest if full.
func (w *Writer) enqueueWAL(bar accumulator.Bar) {
	w.walMu.Lock()
	defer w.walMu.Unlock()
	if len(w.walBuf) >= w.cfg.WALBufferSize {
		// Drop oldest
		w.walBuf = w.walBuf[1:]
		w.walDropTotal()
	}
	w.walBuf = append(w.walBuf, walEntry{bar: bar})
}

// drainWAL writes all buffered bars to QuestDB after WAL resume.
func (w *Writer) drainWAL(ctx context.Context) {
	w.walMu.Lock()
	buf := w.walBuf
	w.walBuf = nil
	w.walMu.Unlock()

	for _, e := range buf {
		if err := w.writeWithRetry(ctx, e.bar); err != nil {
			w.logger.ErrorContext(ctx, "questdb: WAL drain write failed", "error", err)
			return
		}
	}
}

// runWALProbe periodically checks for WAL suspension and auto-resumes.
func (w *Writer) runWALProbe(ctx context.Context) {
	ticker := time.NewTicker(w.cfg.WALProbeInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			last, _ := w.lastWrite.Load().(time.Time)
			if time.Since(last) > 10*time.Second {
				w.probeAndResume(ctx)
			}
		}
	}
}

// probeAndResume queries wal_tables() and resumes WAL if suspended.
func (w *Writer) probeAndResume(ctx context.Context) {
	suspended, err := w.isWALSuspended(ctx)
	if err != nil {
		w.logger.WarnContext(ctx, "questdb: WAL probe failed", "error", err)
		return
	}
	if !suspended {
		if w.walSuspended.Load() {
			w.walSuspended.Store(false)
			w.drainWAL(ctx)
		}
		return
	}

	w.walSuspended.Store(true)
	w.logger.WarnContext(ctx, "questdb: WAL suspended — issuing RESUME WAL")

	if err := w.resumeWAL(ctx); err != nil {
		w.logger.ErrorContext(ctx, "questdb: RESUME WAL failed", "error", err)
		return
	}
	w.walSuspended.Store(false)
	w.logger.InfoContext(ctx, "questdb: WAL resumed — draining buffer")
	w.drainWAL(ctx)
}

// isWALSuspended queries QuestDB REST API wal_tables() for snapshot_1s.
func (w *Writer) isWALSuspended(ctx context.Context) (bool, error) {
	q := url.QueryEscape("wal_tables()")
	addr := w.cfg.HTTPAddr
	if !strings.HasPrefix(addr, "http") {
		addr = "http://" + addr
	}
	reqURL := addr + "/exec?query=" + q

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, reqURL, nil)
	if err != nil {
		return false, err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return false, err
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return false, err
	}

	var result struct {
		Dataset [][]interface{} `json:"dataset"`
	}
	if err := json.Unmarshal(body, &result); err != nil {
		return false, fmt.Errorf("questdb: parse wal_tables response: %w", err)
	}

	for _, row := range result.Dataset {
		if len(row) < 2 {
			continue
		}
		name, _ := row[0].(string)
		if name != "snapshot_1s" {
			continue
		}
		suspended, _ := row[1].(bool)
		return suspended, nil
	}
	return false, nil
}

// ForceProbe triggers an immediate WAL probe check. Used in tests.
func (w *Writer) ForceProbe(ctx context.Context) {
	w.probeAndResume(ctx)
}

// SetWALSuspended directly sets the WAL suspended state. Used in tests.
func (w *Writer) SetWALSuspended(v bool) {
	w.walSuspended.Store(v)
}

// IsWALSuspended reports whether the WAL is currently detected as suspended.
func (w *Writer) IsWALSuspended() bool {
	return w.walSuspended.Load()
}

// resumeWAL issues ALTER TABLE snapshot_1s RESUME WAL via REST.
func (w *Writer) resumeWAL(ctx context.Context) error {
	q := url.QueryEscape("ALTER TABLE snapshot_1s RESUME WAL")
	addr := w.cfg.HTTPAddr
	if !strings.HasPrefix(addr, "http") {
		addr = "http://" + addr
	}
	reqURL := addr + "/exec?query=" + q

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, reqURL, nil)
	if err != nil {
		return err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("questdb: RESUME WAL HTTP %d: %s", resp.StatusCode, string(body))
	}
	return nil
}
