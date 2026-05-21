// Package regime classifies market conditions into one of 5 regimes from
// recent 1-minute candle data. Pure struct — no goroutines, no time.Now().
// The classify-and-publish goroutine lives in cmd/candle/main.go.
package regime

import (
	"context"
	"math"
	"strconv"
	"time"

	goredis "github.com/redis/go-redis/v9"
)

const (
	RegimeThinBook     = "THIN_BOOK"
	RegimeHighVol      = "HIGH_VOL"
	RegimeTrendingUp   = "TRENDING_UP"
	RegimeTrendingDown = "TRENDING_DOWN"
	RegimeRanging      = "RANGING"
)

// Config holds tunable thresholds for regime classification.
type Config struct {
	VolThreshold   float64 // realized_vol > this → HIGH_VOL (default 0.002)
	TrendThreshold float64 // |5-bar price change / price| > this → TRENDING (default 0.001)
	SpreadMult     float64 // spread_mean > SpreadMult × median spread → THIN_BOOK (default 2.0)
	WindowBars     int     // number of 1m bars to read (default 10)
	TTLSeconds     int     // Redis key TTL (default 180)
}

// DefaultConfig returns the default regime classification thresholds.
func DefaultConfig() Config {
	return Config{
		VolThreshold:   0.002,
		TrendThreshold: 0.001,
		SpreadMult:     2.0,
		WindowBars:     10,
		TTLSeconds:     180,
	}
}

// Classifier reads the candles:close:{exchange}:{symbol}:1m Redis stream,
// classifies a regime, and publishes to regime:{exchange}:{symbol}.
// Pure struct — no goroutines, no IO outside of Classify/Publish.
type Classifier struct {
	rdb      goredis.Cmdable
	exchange string
	symbol   string
	cfg      Config
}

// New creates a Classifier for one (exchange, symbol) pair.
func New(rdb goredis.Cmdable, exchange, symbol string, cfg Config) *Classifier {
	return &Classifier{rdb: rdb, exchange: exchange, symbol: symbol, cfg: cfg}
}

type bar1m struct {
	close       float64
	spreadMean  float64
	realizedVol float64
}

// Classify reads recent 1m bars from Redis and returns the current regime string.
// Returns RegimeRanging when fewer than 5 valid bars are available (safe default).
func (c *Classifier) Classify(ctx context.Context) string {
	key := "candles:close:" + c.exchange + ":" + c.symbol + ":1m"
	msgs, err := c.rdb.XRevRangeN(ctx, key, "+", "-", int64(c.cfg.WindowBars)).Result()
	if err != nil || len(msgs) < 5 {
		return RegimeRanging
	}

	bars := make([]bar1m, 0, len(msgs))
	for _, msg := range msgs {
		b := bar1m{}
		if v, ok := msg.Values["close"].(string); ok {
			b.close, _ = strconv.ParseFloat(v, 64)
		}
		if v, ok := msg.Values["spread_mean"].(string); ok {
			b.spreadMean, _ = strconv.ParseFloat(v, 64)
		}
		if v, ok := msg.Values["realized_vol"].(string); ok {
			b.realizedVol, _ = strconv.ParseFloat(v, 64)
		}
		if b.close > 0 {
			bars = append(bars, b)
		}
	}
	if len(bars) < 5 {
		return RegimeRanging
	}

	latest := bars[0]

	// THIN_BOOK: latest spread > SpreadMult × median spread (checked first —
	// a dislocated book must not be misclassified as HIGH_VOL).
	medianSpread := median(spreadSlice(bars))
	if medianSpread > 0 && latest.spreadMean > c.cfg.SpreadMult*medianSpread {
		return RegimeThinBook
	}

	// HIGH_VOL: latest realized_vol above threshold.
	if latest.realizedVol > c.cfg.VolThreshold {
		return RegimeHighVol
	}

	// TRENDING: absolute 5-bar price change / oldest close > threshold.
	oldest := bars[min(4, len(bars)-1)]
	if oldest.close > 0 {
		change := (latest.close - oldest.close) / oldest.close
		if math.Abs(change) > c.cfg.TrendThreshold {
			if change > 0 {
				return RegimeTrendingUp
			}
			return RegimeTrendingDown
		}
	}

	return RegimeRanging
}

// Publish writes the regime string to regime:{exchange}:{symbol} with TTL.
func (c *Classifier) Publish(ctx context.Context, regime string) error {
	key := "regime:" + c.exchange + ":" + c.symbol
	return c.rdb.Set(ctx, key, regime, time.Duration(c.cfg.TTLSeconds)*time.Second).Err()
}

func spreadSlice(bars []bar1m) []float64 {
	s := make([]float64, len(bars))
	for i, b := range bars {
		s[i] = b.spreadMean
	}
	return s
}

// median returns the median of vals using insertion sort (fast for small slices).
func median(vals []float64) float64 {
	if len(vals) == 0 {
		return 0
	}
	sorted := make([]float64, len(vals))
	copy(sorted, vals)
	for i := 1; i < len(sorted); i++ {
		for j := i; j > 0 && sorted[j] < sorted[j-1]; j-- {
			sorted[j], sorted[j-1] = sorted[j-1], sorted[j]
		}
	}
	n := len(sorted)
	if n%2 == 0 {
		return (sorted[n/2-1] + sorted[n/2]) / 2
	}
	return sorted[n/2]
}
