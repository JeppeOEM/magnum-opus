//go:build l2

package redis_test

import (
	"context"
	"testing"

	"github.com/alicebob/miniredis/v2"
	goredis "github.com/redis/go-redis/v9"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/mrqdt/magnum-opus/candle-service/internal/accumulator"
	"github.com/mrqdt/magnum-opus/candle-service/internal/cascade"
	rediswriter "github.com/mrqdt/magnum-opus/candle-service/internal/writer/redis"
)

func setupMiniredis(t *testing.T) (*goredis.Client, *miniredis.Miniredis) {
	t.Helper()
	mr := miniredis.RunT(t)
	rdb := goredis.NewClient(&goredis.Options{Addr: mr.Addr()})
	t.Cleanup(func() { rdb.Close() })
	return rdb, mr
}

func makeBar(tf cascade.TF, openTs int64, isComplete bool) cascade.Bar {
	open := 50000.0
	high := 51000.0
	low := 49000.0
	cls := 50500.0
	return cascade.Bar{
		Exchange:   "kucoin",
		Symbol:     "BTC-USDT",
		TF:         tf,
		OpenTs:     openTs,
		Open:       &open,
		High:       &high,
		Low:        &low,
		Close:      &cls,
		Volume:     1.5,
		QuoteVol:   75000.0,
		TradeCount: 42,
		BarCount:   60,
		GapCount:   0,
		IsComplete: isComplete,
	}
}

func TestPublishBars_ClosedBar(t *testing.T) {
	rdb, _ := setupMiniredis(t)
	ctx := context.Background()
	pub := rediswriter.New(rdb, "kucoin", "BTC-USDT", 10000, 500)

	bar := makeBar(cascade.TF1m, 1_000_000, true)
	pub.PublishBars(ctx, []cascade.Bar{bar})

	// Both streams should have one entry.
	partialLen, err := rdb.XLen(ctx, "candles:kucoin:BTC-USDT:1m").Result()
	require.NoError(t, err)
	assert.Equal(t, int64(1), partialLen)

	closeLen, err := rdb.XLen(ctx, "candles:close:kucoin:BTC-USDT:1m").Result()
	require.NoError(t, err)
	assert.Equal(t, int64(1), closeLen)

	// Verify is_complete field in partial+close stream.
	msgs, err := rdb.XRange(ctx, "candles:kucoin:BTC-USDT:1m", "-", "+").Result()
	require.NoError(t, err)
	require.Len(t, msgs, 1)
	assert.Equal(t, "true", msgs[0].Values["is_complete"])
	assert.Equal(t, "1000000", msgs[0].Values["ts"])
	assert.Equal(t, "kucoin", msgs[0].Values["exchange"])
	assert.Equal(t, "BTC-USDT", msgs[0].Values["symbol"])
	assert.Equal(t, "1m", msgs[0].Values["tf"])
	assert.Equal(t, "50000", msgs[0].Values["open"])
	assert.Equal(t, "51000", msgs[0].Values["high"])
	assert.Equal(t, "49000", msgs[0].Values["low"])
	assert.Equal(t, "50500", msgs[0].Values["close"])
	assert.Equal(t, "1.5", msgs[0].Values["volume"])
	assert.Equal(t, "75000", msgs[0].Values["quote_vol"])
	assert.Equal(t, "42", msgs[0].Values["trade_count"])
	assert.Equal(t, "60", msgs[0].Values["bar_count"])
	assert.Equal(t, "0", msgs[0].Values["gap_count"])

	// is_complete in close stream.
	closeMsgs, err := rdb.XRange(ctx, "candles:close:kucoin:BTC-USDT:1m", "-", "+").Result()
	require.NoError(t, err)
	require.Len(t, closeMsgs, 1)
	assert.Equal(t, "true", closeMsgs[0].Values["is_complete"])
}

func TestPublishBars_NilOHLC(t *testing.T) {
	rdb, _ := setupMiniredis(t)
	ctx := context.Background()
	pub := rediswriter.New(rdb, "kucoin", "BTC-USDT", 10000, 500)

	// Bar with nil OHLC (no-trade second).
	bar := cascade.Bar{
		Exchange:   "kucoin",
		Symbol:     "BTC-USDT",
		TF:         cascade.TF1m,
		OpenTs:     2_000_000,
		Open:       nil,
		High:       nil,
		Low:        nil,
		Close:      nil,
		Volume:     0,
		QuoteVol:   0,
		TradeCount: 0,
		BarCount:   1,
		GapCount:   0,
		IsComplete: true,
	}
	pub.PublishBars(ctx, []cascade.Bar{bar})

	msgs, err := rdb.XRange(ctx, "candles:kucoin:BTC-USDT:1m", "-", "+").Result()
	require.NoError(t, err)
	require.Len(t, msgs, 1)
	// nil fields must serialize to "" not "<nil>" or "0"
	assert.Equal(t, "", msgs[0].Values["open"])
	assert.Equal(t, "", msgs[0].Values["high"])
	assert.Equal(t, "", msgs[0].Values["low"])
	assert.Equal(t, "", msgs[0].Values["close"])
}

func TestPublishPartialBar_OnlyPartialStream(t *testing.T) {
	rdb, _ := setupMiniredis(t)
	ctx := context.Background()
	pub := rediswriter.New(rdb, "kucoin", "BTC-USDT", 10000, 500)

	bar := makeBar(cascade.TF5m, 3_000_000, false)
	pub.PublishPartialBar(ctx, bar)

	// Partial+close stream has one entry.
	partialLen, err := rdb.XLen(ctx, "candles:kucoin:BTC-USDT:5m").Result()
	require.NoError(t, err)
	assert.Equal(t, int64(1), partialLen)

	// Verify is_complete is "false".
	msgs, err := rdb.XRange(ctx, "candles:kucoin:BTC-USDT:5m", "-", "+").Result()
	require.NoError(t, err)
	require.Len(t, msgs, 1)
	assert.Equal(t, "false", msgs[0].Values["is_complete"])

	// Close-only stream must NOT exist.
	exists, err := rdb.Exists(ctx, "candles:close:kucoin:BTC-USDT:5m").Result()
	require.NoError(t, err)
	assert.Equal(t, int64(0), exists, "candles:close: stream must not be created for partial bars")
}

func TestPublishPartialBar_ZeroOpenTs(t *testing.T) {
	rdb, _ := setupMiniredis(t)
	ctx := context.Background()
	pub := rediswriter.New(rdb, "kucoin", "BTC-USDT", 10000, 500)

	bar := cascade.Bar{
		Exchange: "kucoin",
		Symbol:   "BTC-USDT",
		TF:       cascade.TF1m,
		OpenTs:   0, // not seeded
	}
	pub.PublishPartialBar(ctx, bar)

	// No stream should exist.
	exists, err := rdb.Exists(ctx, "candles:kucoin:BTC-USDT:1m").Result()
	require.NoError(t, err)
	assert.Equal(t, int64(0), exists, "should not XADD when OpenTs == 0")
}

func TestPublishBars_MAXLEN(t *testing.T) {
	rdb, _ := setupMiniredis(t)
	ctx := context.Background()
	// maxLen=2, closeMaxLen=1
	pub := rediswriter.New(rdb, "kucoin", "BTC-USDT", 2, 1)

	for i := 0; i < 5; i++ {
		bar := makeBar(cascade.TF1m, int64(1_000_000+i*60_000), true)
		pub.PublishBars(ctx, []cascade.Bar{bar})
	}

	// miniredis respects MAXLEN trimming.
	partialLen, err := rdb.XLen(ctx, "candles:kucoin:BTC-USDT:1m").Result()
	require.NoError(t, err)
	// Approximate MAXLEN may leave 1 extra entry; strict upper bound = maxLen + 1.
	assert.LessOrEqual(t, partialLen, int64(3), "stream length should be ≤ maxLen+1")

	closeLen, err := rdb.XLen(ctx, "candles:close:kucoin:BTC-USDT:1m").Result()
	require.NoError(t, err)
	assert.LessOrEqual(t, closeLen, int64(2), "close stream length should be ≤ closeMaxLen+1")
}

func TestPublishBars_FailureCounter(t *testing.T) {
	rdb, mr := setupMiniredis(t)
	ctx := context.Background()
	pub := rediswriter.New(rdb, "kucoin", "BTC-USDT", 10000, 500)

	var counterCalls int
	pub.SetFailureCounter(func() { counterCalls++ })

	// Stop miniredis to force XADD failures.
	mr.Close()

	bar := makeBar(cascade.TF1m, 1_000_000, true)
	pub.PublishBars(ctx, []cascade.Bar{bar})

	// Each closed bar triggers 2 XADDs (partial+close streams) so counter >= 1.
	assert.GreaterOrEqual(t, counterCalls, 1, "failure counter should be called on XADD error")
}

func TestPublishBars_MultipleTimeframes(t *testing.T) {
	rdb, _ := setupMiniredis(t)
	ctx := context.Background()
	pub := rediswriter.New(rdb, "bybit", "BTCUSDT", 10000, 500)

	bars := []cascade.Bar{
		makeBar(cascade.TF1m, 1_000_000, true),
		makeBar(cascade.TF1h, 1_000_000, true),
		makeBar(cascade.TF1w, 1_000_000, true),
	}
	pub.PublishBars(ctx, bars)

	for _, tf := range []cascade.TF{cascade.TF1m, cascade.TF1h, cascade.TF1w} {
		l, err := rdb.XLen(ctx, "candles:bybit:BTCUSDT:"+string(tf)).Result()
		require.NoError(t, err)
		assert.Equal(t, int64(1), l, "tf=%s partial stream should have 1 entry", tf)

		l2, err := rdb.XLen(ctx, "candles:close:bybit:BTCUSDT:"+string(tf)).Result()
		require.NoError(t, err)
		assert.Equal(t, int64(1), l2, "tf=%s close stream should have 1 entry", tf)
	}

	// No weekly alias format (candles:1w:...).
	exists, err := rdb.Exists(ctx, "candles:1w:bybit:BTCUSDT").Result()
	require.NoError(t, err)
	assert.Equal(t, int64(0), exists, "no weekly alias key must be created")
}

// makeOBBar builds an accumulator.Bar with known OB close fields for ob_features tests.
func makeOBBar(tsMs int64) accumulator.Bar {
	bid := 50000.0
	ask := 50001.0
	bidL1 := 1.5
	askL1 := 2.0
	bidL2 := 3.0
	askL2 := 4.0
	bidTop10 := 10.0
	askTop10 := 12.0
	bidTotal := 20.0
	askTotal := 22.0
	ofi := 0.5
	return accumulator.Bar{
		TsSecMs:         tsMs,
		Exchange:        "kucoin",
		Symbol:          "BTC-USDT",
		BestBid:         &bid,
		BestAsk:         &ask,
		BidDepthL1Close: &bidL1,
		AskDepthL1Close: &askL1,
		BidDepthL2Close: &bidL2,
		AskDepthL2Close: &askL2,
		BidDepthTop10Close: &bidTop10,
		AskDepthTop10Close: &askTop10,
		BidDepthTotalClose: &bidTotal,
		AskDepthTotalClose: &askTotal,
		OFI:             &ofi,
		OFIL1:           &ofi,
	}
}

func TestPublishOBFeatures_AllFields(t *testing.T) {
	rdb, _ := setupMiniredis(t)
	ctx := context.Background()
	pub := rediswriter.New(rdb, "kucoin", "BTC-USDT", 10000, 500)

	bar := makeOBBar(1_000_000)
	pub.PublishOBFeatures(ctx, bar)

	l, err := rdb.XLen(ctx, "ob_features:kucoin:BTC-USDT").Result()
	require.NoError(t, err)
	assert.Equal(t, int64(1), l)

	msgs, err := rdb.XRange(ctx, "ob_features:kucoin:BTC-USDT", "-", "+").Result()
	require.NoError(t, err)
	require.Len(t, msgs, 1)

	v := msgs[0].Values
	assert.Equal(t, "1000000", v["ts"])
	assert.Equal(t, "kucoin", v["exchange"])
	assert.Equal(t, "BTC-USDT", v["symbol"])
	assert.Equal(t, "50000", v["best_bid"])
	assert.Equal(t, "50001", v["best_ask"])
	assert.Equal(t, "1.5", v["bid_depth_l1"])
	assert.Equal(t, "2", v["ask_depth_l1"])
	assert.Equal(t, "3", v["bid_depth_l2"])
	assert.Equal(t, "4", v["ask_depth_l2"])
	assert.Equal(t, "10", v["bid_depth_top10"])
	assert.Equal(t, "12", v["ask_depth_top10"])
	assert.Equal(t, "20", v["bid_depth_total"])
	assert.Equal(t, "22", v["ask_depth_total"])
	assert.Equal(t, "0.5", v["ofi"])
	assert.Equal(t, "0.5", v["ofi_l1"])
}

func TestPublishOBFeatures_NilFields(t *testing.T) {
	rdb, _ := setupMiniredis(t)
	ctx := context.Background()
	pub := rediswriter.New(rdb, "kucoin", "BTC-USDT", 10000, 500)

	// Bar with all pointer fields nil (no OB data this second).
	bar := accumulator.Bar{
		TsSecMs:  2_000_000,
		Exchange: "kucoin",
		Symbol:   "BTC-USDT",
	}
	pub.PublishOBFeatures(ctx, bar)

	msgs, err := rdb.XRange(ctx, "ob_features:kucoin:BTC-USDT", "-", "+").Result()
	require.NoError(t, err)
	require.Len(t, msgs, 1)

	v := msgs[0].Values
	// nil fields must serialize to "" not "<nil>"
	assert.Equal(t, "", v["best_bid"])
	assert.Equal(t, "", v["best_ask"])
	assert.Equal(t, "", v["bid_depth_l1"])
	assert.Equal(t, "", v["ask_depth_l1"])
	assert.Equal(t, "", v["ofi"])
	assert.Equal(t, "", v["ofi_l1"])
	// ts, exchange, symbol are always non-nil
	assert.Equal(t, "2000000", v["ts"])
}

func TestPublishOBFeatures_StreamKey(t *testing.T) {
	rdb, _ := setupMiniredis(t)
	ctx := context.Background()
	pub := rediswriter.New(rdb, "bybit", "BTCUSDT", 10000, 500)

	bar := makeOBBar(3_000_000)
	bar.Exchange = "bybit"
	bar.Symbol = "BTCUSDT"
	pub.PublishOBFeatures(ctx, bar)

	// Correct key must exist.
	l, err := rdb.XLen(ctx, "ob_features:bybit:BTCUSDT").Result()
	require.NoError(t, err)
	assert.Equal(t, int64(1), l)

	// No TF-suffixed or close-variant keys.
	exists, err := rdb.Exists(ctx, "ob_features:bybit:BTCUSDT:1s").Result()
	require.NoError(t, err)
	assert.Equal(t, int64(0), exists, "no TF-suffixed key must exist")

	exists, err = rdb.Exists(ctx, "ob_features:close:bybit:BTCUSDT").Result()
	require.NoError(t, err)
	assert.Equal(t, int64(0), exists, "no ob_features:close: variant must exist")
}

func TestPublishOBFeatures_FailureCounter(t *testing.T) {
	rdb, mr := setupMiniredis(t)
	ctx := context.Background()
	pub := rediswriter.New(rdb, "kucoin", "BTC-USDT", 10000, 500)

	var counterCalls int
	pub.SetFailureCounter(func() { counterCalls++ })

	// Stop miniredis to force XADD failure.
	mr.Close()

	bar := makeOBBar(4_000_000)
	pub.PublishOBFeatures(ctx, bar)

	assert.GreaterOrEqual(t, counterCalls, 1, "failure counter should be called on XADD error")
}

func TestPublishOBFeatures_MAXLEN(t *testing.T) {
	rdb, _ := setupMiniredis(t)
	ctx := context.Background()
	pub := rediswriter.New(rdb, "kucoin", "BTC-USDT", 2, 500)

	for i := 0; i < 5; i++ {
		bar := makeOBBar(int64(1_000_000 + i*1_000))
		pub.PublishOBFeatures(ctx, bar)
	}

	l, err := rdb.XLen(ctx, "ob_features:kucoin:BTC-USDT").Result()
	require.NoError(t, err)
	assert.LessOrEqual(t, l, int64(3), "ob_features stream length should be ≤ maxLen+1")
}
