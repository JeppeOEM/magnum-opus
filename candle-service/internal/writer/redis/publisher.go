// Package redis publishes cascade OHLCV bars and OB feature snapshots to Redis Streams.
// Pure: no time.Now(), no goroutines, no init(). Single-goroutine ownership per symbol.
package redis

import (
	"context"
	"log/slog"
	"strconv"

	goredis "github.com/redis/go-redis/v9"

	"github.com/mrqdt/magnum-opus/candle-service/internal/accumulator"
	"github.com/mrqdt/magnum-opus/candle-service/internal/cascade"
)

// Publisher writes cascade bars to two Redis stream families:
//
//	candles:{exchange}:{symbol}:{tf}       — partial + closed bars (MAXLEN maxLen)
//	candles:close:{exchange}:{symbol}:{tf} — closed bars only      (MAXLEN closeMaxLen)
type Publisher struct {
	rdb          goredis.Cmdable
	exchange     string
	symbol       string
	maxLen       int64
	closeMaxLen  int64
	failureFn    func() // increments candle_redis_publish_failure_total; may be nil
}

// New creates a Publisher for one (exchange, symbol).
// maxLen is the approximate MAXLEN for the partial+close stream.
// closeMaxLen is the approximate MAXLEN for the close-only stream.
func New(rdb *goredis.Client, exchange, symbol string, maxLen, closeMaxLen int64) *Publisher {
	return &Publisher{
		rdb:         rdb,
		exchange:    exchange,
		symbol:      symbol,
		maxLen:      maxLen,
		closeMaxLen: closeMaxLen,
	}
}

// SetFailureCounter injects a callback invoked once per XADD failure.
// The callback should increment candle_redis_publish_failure_total{exchange,symbol}.
func (p *Publisher) SetFailureCounter(fn func()) {
	p.failureFn = fn
}

// PublishBars publishes each closed bar to both the partial+close stream and the
// close-only stream. On XADD failure: log ERROR, call failureFn, continue.
func (p *Publisher) PublishBars(ctx context.Context, bars []cascade.Bar) {
	for _, bar := range bars {
		fields := barFields(bar, true)
		partialKey := "candles:" + p.exchange + ":" + p.symbol + ":" + string(bar.TF)
		closeKey := "candles:close:" + p.exchange + ":" + p.symbol + ":" + string(bar.TF)

		if err := p.xadd(ctx, partialKey, p.maxLen, fields); err != nil {
			slog.ErrorContext(ctx, "candle stream xadd failed",
				"exchange", p.exchange, "symbol", p.symbol, "tf", bar.TF,
				"stream", partialKey, "error", err)
			if p.failureFn != nil {
				p.failureFn()
			}
		}
		if err := p.xadd(ctx, closeKey, p.closeMaxLen, fields); err != nil {
			slog.ErrorContext(ctx, "candle close stream xadd failed",
				"exchange", p.exchange, "symbol", p.symbol, "tf", bar.TF,
				"stream", closeKey, "error", err)
			if p.failureFn != nil {
				p.failureFn()
			}
		}
	}
}

// PublishPartialBar publishes an in-progress cascade bar to the partial+close stream only.
// Silently skips if bar.OpenTs == 0 (accumulator not yet seeded).
// Never writes to candles:close: stream — partial bars are excluded from close-only subscribers.
func (p *Publisher) PublishPartialBar(ctx context.Context, bar cascade.Bar) {
	if bar.OpenTs == 0 {
		return
	}
	fields := barFields(bar, false)
	key := "candles:" + p.exchange + ":" + p.symbol + ":" + string(bar.TF)
	if err := p.xadd(ctx, key, p.maxLen, fields); err != nil {
		slog.ErrorContext(ctx, "candle partial stream xadd failed",
			"exchange", p.exchange, "symbol", p.symbol, "tf", bar.TF,
			"stream", key, "error", err)
		if p.failureFn != nil {
			p.failureFn()
		}
	}
}

func (p *Publisher) xadd(ctx context.Context, stream string, maxLen int64, fields map[string]any) error {
	_, err := p.rdb.XAdd(ctx, &goredis.XAddArgs{
		Stream: stream,
		MaxLen: maxLen,
		Approx: true,
		Values: fields,
	}).Result()
	return err
}

// barFields serialises a cascade.Bar to the Redis stream field map.
// isComplete controls the is_complete field value ("true"/"false").
func barFields(bar cascade.Bar, isComplete bool) map[string]any {
	isCompleteStr := "false"
	if isComplete {
		isCompleteStr = "true"
	}
	m := map[string]any{
		"ts":          strconv.FormatInt(bar.OpenTs, 10),
		"exchange":    bar.Exchange,
		"symbol":      bar.Symbol,
		"tf":          string(bar.TF),
		"open":        floatOrEmpty(bar.Open),
		"high":        floatOrEmpty(bar.High),
		"low":         floatOrEmpty(bar.Low),
		"close":       floatOrEmpty(bar.Close),
		"volume":      strconv.FormatFloat(bar.Volume, 'f', -1, 64),
		"quote_vol":   strconv.FormatFloat(bar.QuoteVol, 'f', -1, 64),
		"trade_count": strconv.Itoa(bar.TradeCount),
		"bar_count":   strconv.Itoa(bar.BarCount),
		"gap_count":   strconv.Itoa(bar.GapCount),
		"is_complete": isCompleteStr,
	}
	if bar.TradeCount > 0 {
		m["buy_volume"] = strconv.FormatFloat(bar.BuyVolume, 'f', -1, 64)
		m["sell_volume"] = strconv.FormatFloat(bar.SellVolume, 'f', -1, 64)
	}
	return m
}

func floatOrEmpty(f *float64) string {
	if f == nil {
		return ""
	}
	return strconv.FormatFloat(*f, 'f', -1, 64)
}

// PublishOBFeatures publishes a 1-second OB feature snapshot to ob_features:{exchange}:{symbol}.
// Published on each 1s bar close (not on 250ms partial cadence).
// bar must be the closed accumulator.Bar from acc.CurrentBar() — called before acc.BarReset().
func (p *Publisher) PublishOBFeatures(ctx context.Context, bar accumulator.Bar) {
	fields := obFields(bar)
	key := "ob_features:" + p.exchange + ":" + p.symbol
	if err := p.xadd(ctx, key, p.maxLen, fields); err != nil {
		slog.ErrorContext(ctx, "ob features stream xadd failed",
			"exchange", p.exchange, "symbol", p.symbol, "error", err)
		if p.failureFn != nil {
			p.failureFn()
		}
	}
}

// obFields serialises an accumulator.Bar to the ob_features Redis stream field map.
func obFields(bar accumulator.Bar) map[string]any {
	return map[string]any{
		"ts":              strconv.FormatInt(bar.TsSecMs, 10),
		"exchange":        bar.Exchange,
		"symbol":          bar.Symbol,
		"best_bid":        floatOrEmpty(bar.BestBid),
		"best_ask":        floatOrEmpty(bar.BestAsk),
		"bid_depth_l1":    floatOrEmpty(bar.BidDepthL1Close),
		"ask_depth_l1":    floatOrEmpty(bar.AskDepthL1Close),
		"bid_depth_l2":    floatOrEmpty(bar.BidDepthL2Close),
		"ask_depth_l2":    floatOrEmpty(bar.AskDepthL2Close),
		"bid_depth_top10": floatOrEmpty(bar.BidDepthTop10Close),
		"ask_depth_top10": floatOrEmpty(bar.AskDepthTop10Close),
		"bid_depth_total": floatOrEmpty(bar.BidDepthTotalClose),
		"ask_depth_total": floatOrEmpty(bar.AskDepthTotalClose),
		"ofi":             floatOrEmpty(bar.OFI),
		"ofi_l1":          floatOrEmpty(bar.OFIL1),
	}
}
