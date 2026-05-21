package regime_test

import (
	"context"
	"fmt"
	"testing"

	"github.com/alicebob/miniredis/v2"
	goredis "github.com/redis/go-redis/v9"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/mrqdt/magnum-opus/candle-service/internal/regime"
)

const (
	testExchange = "bybit"
	testSymbol   = "BTCUSDT"
	streamKey    = "candles:close:" + testExchange + ":" + testSymbol + ":1m"
)

func setupMiniredis(t *testing.T) (*goredis.Client, *miniredis.Miniredis) {
	t.Helper()
	mr := miniredis.RunT(t)
	rdb := goredis.NewClient(&goredis.Options{Addr: mr.Addr()})
	t.Cleanup(func() { rdb.Close() })
	return rdb, mr
}

// addBar pushes one 1m bar into the stream. close, spread, vol are string values.
func addBar(t *testing.T, rdb *goredis.Client, closePrice, spreadMean, realizedVol string) {
	t.Helper()
	ctx := context.Background()
	err := rdb.XAdd(ctx, &goredis.XAddArgs{
		Stream: streamKey,
		Values: map[string]any{
			"close":        closePrice,
			"spread_mean":  spreadMean,
			"realized_vol": realizedVol,
		},
	}).Err()
	require.NoError(t, err)
}

// addBars pushes n identical bars and one different final bar.
func addUniformBars(t *testing.T, rdb *goredis.Client, n int, close, spread, vol string) {
	t.Helper()
	for i := 0; i < n; i++ {
		addBar(t, rdb, close, spread, vol)
	}
}

func newClassifier(rdb *goredis.Client) *regime.Classifier {
	return regime.New(rdb, testExchange, testSymbol, regime.DefaultConfig())
}

// ── Tests ──────────────────────────────────────────────────────────────────

func TestClassify_InsufficientData_Ranging(t *testing.T) {
	rdb, _ := setupMiniredis(t)
	ctx := context.Background()
	// Only 3 bars — fewer than the minimum 5 required
	addUniformBars(t, rdb, 3, "50000", "1.0", "0.001")
	result := newClassifier(rdb).Classify(ctx)
	assert.Equal(t, regime.RegimeRanging, result)
}

func TestClassify_ThinBook(t *testing.T) {
	rdb, _ := setupMiniredis(t)
	ctx := context.Background()
	// 9 bars with spread=1.0, then 1 latest bar with spread=30.0 (>2× median=1.0)
	// XRevRangeN returns newest first, so add the latest bar last in XADD order
	addUniformBars(t, rdb, 9, "50000", "1.0", "0.001")
	addBar(t, rdb, "50000", "30.0", "0.001") // latest bar (added last = highest ID)
	result := newClassifier(rdb).Classify(ctx)
	assert.Equal(t, regime.RegimeThinBook, result, "spread 30 >> 2×median(1) → THIN_BOOK")
}

func TestClassify_HighVol(t *testing.T) {
	rdb, _ := setupMiniredis(t)
	ctx := context.Background()
	// Normal spread, but latest realized_vol > 0.002
	addUniformBars(t, rdb, 9, "50000", "1.0", "0.001")
	addBar(t, rdb, "50000", "1.0", "0.005") // latest: vol=0.005 > threshold
	result := newClassifier(rdb).Classify(ctx)
	assert.Equal(t, regime.RegimeHighVol, result, "vol 0.005 > 0.002 threshold → HIGH_VOL")
}

func TestClassify_TrendingUp(t *testing.T) {
	rdb, _ := setupMiniredis(t)
	ctx := context.Background()
	// 5 bars: oldest close=50000, then rise to 50100 (+0.2% > 0.1% threshold)
	// XRevRangeN returns newest first; latest = bars[0], oldest-of-5 = bars[4]
	prices := []float64{50000, 50025, 50050, 50075, 50100}
	for _, p := range prices {
		addBar(t, rdb, fmt.Sprintf("%.2f", p), "1.0", "0.001")
	}
	result := newClassifier(rdb).Classify(ctx)
	assert.Equal(t, regime.RegimeTrendingUp, result, "price rose 0.2% → TRENDING_UP")
}

func TestClassify_TrendingDown(t *testing.T) {
	rdb, _ := setupMiniredis(t)
	ctx := context.Background()
	prices := []float64{50100, 50075, 50050, 50025, 50000}
	for _, p := range prices {
		addBar(t, rdb, fmt.Sprintf("%.2f", p), "1.0", "0.001")
	}
	result := newClassifier(rdb).Classify(ctx)
	assert.Equal(t, regime.RegimeTrendingDown, result, "price fell 0.2% → TRENDING_DOWN")
}

func TestClassify_Ranging_Default(t *testing.T) {
	rdb, _ := setupMiniredis(t)
	ctx := context.Background()
	// All conditions below thresholds — should default to RANGING
	addUniformBars(t, rdb, 10, "50000", "1.0", "0.001")
	result := newClassifier(rdb).Classify(ctx)
	assert.Equal(t, regime.RegimeRanging, result, "nothing triggered → RANGING")
}

func TestClassify_Publish_SetsKeyWithTTL(t *testing.T) {
	rdb, mr := setupMiniredis(t)
	ctx := context.Background()
	clf := regime.New(rdb, testExchange, testSymbol, regime.DefaultConfig())
	err := clf.Publish(ctx, regime.RegimeRanging)
	require.NoError(t, err)
	key := "regime:" + testExchange + ":" + testSymbol
	val, err := rdb.Get(ctx, key).Result()
	require.NoError(t, err)
	assert.Equal(t, regime.RegimeRanging, val)
	ttl := mr.TTL(key)
	assert.Positive(t, ttl, "key must have a TTL set")
}
