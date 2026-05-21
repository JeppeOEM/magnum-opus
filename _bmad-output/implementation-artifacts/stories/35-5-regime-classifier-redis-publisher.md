---
id: 35-5
title: RegimeClassifier — 5-regime Redis publisher
epic: 35
status: ready-for-dev
---

# Story 35-5: RegimeClassifier — 5-Regime Redis Publisher

## Context

The ML inference engine needs to know the current market regime to select the right model. This story adds a `Classifier` in `internal/regime/` that reads the 1-minute close candle stream from Redis, applies threshold heuristics to classify one of 5 regimes, and publishes to a Redis key with 3-minute TTL.

5 regimes:
- `THIN_BOOK` — spread unusually wide (liquidity withdrawn)
- `HIGH_VOL` — realized volatility above threshold
- `TRENDING_UP` / `TRENDING_DOWN` — directional price movement
- `RANGING` — default; mean reversion is most reliable here

Classification runs every `CANDLE_REGIME_INTERVAL_S` seconds (default 60). The struct itself is pure (no goroutines, no IO) — the goroutine lives in `cmd/candle/main.go`.

## What to build

### `candle-service/internal/regime/classifier.go`

```go
package regime

import (
    "context"
    "math"
    "strconv"
    "time"

    goredis "github.com/redis/go-redis/v9"
    "log/slog"
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
    TrendThreshold float64 // |5-bar price change / mid| > this → TRENDING (default 0.001)
    SpreadMult     float64 // spread_mean > SpreadMult × median spread → THIN_BOOK (default 2.0)
    WindowBars     int     // number of 1m bars to read (default 10)
    TTLSeconds     int     // Redis key TTL (default 180)
}

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
// Pure struct — no goroutines, no IO in methods other than Classify/Publish.
type Classifier struct {
    rdb      goredis.Cmdable
    exchange string
    symbol   string
    cfg      Config
}

func New(rdb goredis.Cmdable, exchange, symbol string, cfg Config) *Classifier {
    return &Classifier{rdb: rdb, exchange: exchange, symbol: symbol, cfg: cfg}
}

type bar1m struct {
    close      float64
    spreadMean float64
    realizedVol float64
}

// Classify reads recent 1m bars from Redis, returns the current regime string.
func (c *Classifier) Classify(ctx context.Context) string {
    key := "candles:close:" + c.exchange + ":" + c.symbol + ":1m"
    msgs, err := c.rdb.XRevRangeN(ctx, key, "+", "-", int64(c.cfg.WindowBars)).Result()
    if err != nil || len(msgs) < 5 {
        return RegimeRanging // insufficient data — safe default
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

    // THIN_BOOK: latest spread > SpreadMult × median spread
    medianSpread := median(spreadSlice(bars))
    if medianSpread > 0 && latest.spreadMean > c.cfg.SpreadMult*medianSpread {
        return RegimeThinBook
    }

    // HIGH_VOL: latest realized_vol above threshold
    if latest.realizedVol > c.cfg.VolThreshold {
        return RegimeHighVol
    }

    // TRENDING: absolute 5-bar price change / latest close > threshold
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

func median(vals []float64) float64 {
    if len(vals) == 0 { return 0 }
    sorted := make([]float64, len(vals))
    copy(sorted, vals)
    // simple insertion sort for small slices
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
```

### `candle-service/internal/regime/classifier_test.go`

```go
package regime_test

func TestClassify_ThinBook(t *testing.T) {
    // latest spread = 10×median → THIN_BOOK
}
func TestClassify_HighVol(t *testing.T) {
    // latest realized_vol > threshold → HIGH_VOL
}
func TestClassify_TrendingUp(t *testing.T) {
    // 5-bar price rise > threshold → TRENDING_UP
}
func TestClassify_TrendingDown(t *testing.T) {
    // 5-bar price drop → TRENDING_DOWN
}
func TestClassify_Ranging_Default(t *testing.T) {
    // nothing triggered → RANGING
}
func TestClassify_InsufficientData_Ranging(t *testing.T) {
    // fewer than 5 bars in stream → RANGING
}
```

Use a Redis mock (or miniredis) to populate the stream and verify each outcome.

### `candle-service/cmd/candle/main.go` — start regime goroutine

After the candle flusher is started, add:

```go
// Regime classifier — goroutine in cmd/ (not in internal/)
regimeCfg := regime.DefaultConfig()
// override from env if set
if v := os.Getenv("CANDLE_REGIME_INTERVAL_S"); v != "" {
    if n, err := strconv.Atoi(v); err == nil && n > 0 {
        intervalS = n
    }
}
for _, sym := range symbols {
    exchange, symbol := sym.exchange, sym.symbol
    go func() {
        ticker := time.NewTicker(time.Duration(intervalS) * time.Second)
        defer ticker.Stop()
        clf := regime.New(rdb, exchange, symbol, regimeCfg)
        for {
            select {
            case <-ticker.C:
                r := clf.Classify(ctx)
                if err := clf.Publish(ctx, r); err != nil {
                    slog.WarnContext(ctx, "regime publish failed",
                        "exchange", exchange, "symbol", symbol, "error", err)
                }
            case <-ctx.Done():
                return
            }
        }
    }()
}
```

### Config additions to candle-service (env vars)

```
CANDLE_REGIME_INTERVAL_S=60          # how often to classify (default 60)
CANDLE_REGIME_VOL_THRESHOLD=0.002    # realized_vol threshold for HIGH_VOL
CANDLE_REGIME_TREND_THRESHOLD=0.001  # 5-bar change threshold for TRENDING
```

## Acceptance Criteria

1. `Classifier.Classify()` returns `THIN_BOOK` when latest spread > 2× median spread.
2. Returns `HIGH_VOL` when latest realized_vol > threshold (default 0.002).
3. Returns `TRENDING_UP` / `TRENDING_DOWN` when 5-bar price change > threshold (default 0.001).
4. Returns `RANGING` when none of the above conditions are met.
5. Returns `RANGING` when fewer than 5 valid 1m bars are available (safe fallback).
6. `Classifier.Publish()` writes to `regime:{exchange}:{symbol}` as a Redis SET with 180s TTL.
7. `Classifier` struct has no goroutines, no `time.Now()` calls in `internal/regime/`.
8. A goroutine started from `cmd/candle/main.go` runs the classify-and-publish loop.
9. All 6 unit tests pass (mock Redis stream).
10. `CANDLE_REGIME_INTERVAL_S` env var overrides the default 60-second interval.

## Dev Notes

- `candles:close:{exchange}:{symbol}:1m` is the existing Redis stream written by the cascade flusher — `spread_mean` and `realized_vol` are already in it.
- `XRevRangeN` reads newest-first — `bars[0]` is the latest bar.
- THIN_BOOK check uses ratio vs median (not absolute threshold) — adapts to different instruments automatically.
- THIN_BOOK is checked first (before HIGH_VOL) so a dislocated book doesn't get misclassified as HIGH_VOL.
- The goroutine in `cmd/` is fine per CLAUDE.md — the "no goroutines" rule is specifically for `internal/orderbook`.
