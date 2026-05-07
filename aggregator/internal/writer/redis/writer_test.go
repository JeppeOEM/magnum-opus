//go:build l2

package redis_test

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	"github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
	"github.com/mrqdt/magnum-opus/aggregator/internal/gapdetector"
	"github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
	"github.com/mrqdt/magnum-opus/aggregator/internal/testutil"
	"github.com/mrqdt/magnum-opus/aggregator/internal/testutil/mock"
	rediswriter "github.com/mrqdt/magnum-opus/aggregator/internal/writer/redis"
)

var (
	testTime = time.Date(2024, 1, 15, 12, 0, 0, 0, time.UTC)
	testSym  = symbol.Symbol("BTC-USDT")
	noSleep  = func(_ context.Context, _ time.Duration) error { return nil }
)

func newWriter(fake *mock.FakeRedis) *rediswriter.Writer {
	clk := testutil.NewMockClock(testTime)
	return rediswriter.New(fake, clk, 10*time.Second).WithSleep(noSleep)
}

func makeTick(typ exchange.EventType) exchange.Tick {
	return exchange.Tick{
		Exchange:   "kucoin",
		Symbol:     testSym,
		Seq:        42,
		TsExchange: 1_700_000_000_000_000_000, // nanoseconds
		TsLocal:    1_700_000_000_100_000_000, // nanoseconds
		Side:       "bid",
		Price:      "29500.50",
		Size:       "0.01",
		Type:       typ,
	}
}

func TestWrite_UpdateTick(t *testing.T) {
	fake := mock.NewFakeRedis()
	w := newWriter(fake)
	tick := makeTick(exchange.EventTypeUpdate)

	require.NoError(t, w.Write(context.Background(), tick))

	entries := fake.Entries("ticks:kucoin:BTC-USDT")
	require.Len(t, entries, 1)
	f := entries[0].Fields
	require.Equal(t, "tick", f["type"])
	require.Equal(t, "kucoin", f["exchange"])
	require.Equal(t, "BTC-USDT", f["symbol"])
	require.Equal(t, "42", f["seq"])
	require.Equal(t, "1700000000000", f["ts_exchange"]) // ns ÷ 1_000_000
	require.Equal(t, "1700000000100", f["ts_local"])
	require.Equal(t, "bid", f["side"])
	require.Equal(t, "29500.50", f["price"])
	require.Equal(t, "0.01", f["size"])
	require.Equal(t, "update", f["event_type"])
}

func TestWrite_TradeTick(t *testing.T) {
	fake := mock.NewFakeRedis()
	w := newWriter(fake)
	tick := makeTick(exchange.EventTypeTrade)
	tick.Side = "" // trades have no side

	require.NoError(t, w.Write(context.Background(), tick))

	entries := fake.Entries("ticks:kucoin:BTC-USDT")
	require.Len(t, entries, 1)
	require.Equal(t, "trade", entries[0].Fields["event_type"])
	require.Equal(t, "", entries[0].Fields["side"])
}

func TestWrite_GapMarker(t *testing.T) {
	fake := mock.NewFakeRedis()
	w := newWriter(fake)
	gap := gapdetector.GapEvent{
		Cause:     gapdetector.CauseExternalDisconnect,
		SeqBefore: 100,
		SeqAfter:  105,
		Timestamp: testTime,
	}

	require.NoError(t, w.WriteGap(context.Background(), "kucoin", testSym, gap))

	// In-band gap marker written to ticks stream.
	tickEntries := fake.Entries("ticks:kucoin:BTC-USDT")
	require.Len(t, tickEntries, 1)
	tf := tickEntries[0].Fields
	require.Equal(t, "gap", tf["type"])
	require.Equal(t, "external_disconnect", tf["gap_cause"])
	require.Equal(t, "100", tf["seq_before"])
	require.Equal(t, "105", tf["seq_after"])
	_, hasSeqGap := tf["seq_gap"]
	require.False(t, hasSeqGap, "seq_gap must not appear in ticks stream entry")

	// gaps:log entry with seq_gap derived field.
	gapEntries := fake.Entries("gaps:log")
	require.Len(t, gapEntries, 1)
	gf := gapEntries[0].Fields
	require.Equal(t, "gap", gf["type"])
	require.Equal(t, "4", gf["seq_gap"]) // 105 - 100 - 1
}

func TestWrite_RetryOnFailure(t *testing.T) {
	fake := mock.NewFakeRedis()
	fake.FailFirst = 1 // first XAdd call fails, subsequent succeed
	clk := testutil.NewMockClock(testTime)
	clk.Advance(0) // ensure deadline > now by maxRetryDur
	w := rediswriter.New(fake, clk, 10*time.Second).WithSleep(noSleep)

	tick := makeTick(exchange.EventTypeUpdate)
	require.NoError(t, w.Write(context.Background(), tick))
	require.Equal(t, 1, fake.Len("ticks:kucoin:BTC-USDT"))
}

func TestWrite_ExhaustedRetry_EmitsGap(t *testing.T) {
	// alwaysFailRedis always fails, so the gap marker write is also attempted (and fails).
	// This test verifies the gap marker is ATTEMPTED after tick write exhaustion (NFR9).
	// It cannot verify the gap was stored — the fake always fails — but proves WriteGap was called.
	alwaysFail := &alwaysFailRedis{}
	clk := testutil.NewMockClock(testTime)
	// maxRetryDur=0: deadline == clock.Now(), so !clock.Now().Before(deadline) is true immediately.
	w := rediswriter.New(alwaysFail, clk, 0).WithSleep(noSleep)

	tick := makeTick(exchange.EventTypeUpdate)
	err := w.Write(context.Background(), tick)
	require.Error(t, err)
	// calls > 1: tick write attempt (call 1) + gap marker ticks-stream attempt (call 2).
	require.Greater(t, alwaysFail.calls, 1, "expected gap marker attempt after tick write failure")
}

// alwaysFailRedis is a minimal RedisClient that always returns an error.
type alwaysFailRedis struct{ calls int }

func (a *alwaysFailRedis) XAdd(_ context.Context, _ string, _ map[string]any, _ int64) (string, error) {
	a.calls++
	return "", errAlwaysFail
}

type alwaysFailError string

func (e alwaysFailError) Error() string { return string(e) }

const errAlwaysFail alwaysFailError = "always fail"

func TestXREADGROUP_CompatibleEntries(t *testing.T) {
	fake := mock.NewFakeRedis()
	stream := "ticks:kucoin:BTC-USDT"

	require.NoError(t, fake.CreateGroup(stream, "candle-service"))

	w := newWriter(fake)
	require.NoError(t, w.Write(context.Background(), makeTick(exchange.EventTypeUpdate)))

	entries, err := fake.XREADGROUP("candle-service", "consumer-1", stream)
	require.NoError(t, err)
	require.Len(t, entries, 1)
	require.Equal(t, "tick", entries[0].Fields["type"])
	require.Equal(t, "BTC-USDT", entries[0].Fields["symbol"])

	require.NoError(t, fake.XACK(stream, "candle-service", entries[0].ID))

	// Second XREADGROUP returns nothing — position advanced past delivered entry.
	entries2, err := fake.XREADGROUP("candle-service", "consumer-1", stream)
	require.NoError(t, err)
	require.Empty(t, entries2)
}

func TestWriteGap_DuplicateOnRetry(t *testing.T) {
	// On retry after a Redis failure during WriteGap, the gap marker may be written twice
	// to the ticks stream (duplicate). This is acceptable — Candle Service deduplicates.
	fake := mock.NewFakeRedis()
	fake.FailFirst = 1 // first XAdd (ticks stream) fails; retry succeeds; gaps:log also succeeds
	clk := testutil.NewMockClock(testTime)
	w := rediswriter.New(fake, clk, 10*time.Second).WithSleep(noSleep)

	gap := gapdetector.GapEvent{
		Cause:     gapdetector.CauseInternalMergeError,
		SeqBefore: 10,
		SeqAfter:  20,
		Timestamp: testTime,
	}
	// Retry on the ticks stream write produces an extra entry (the duplicate).
	require.NoError(t, w.WriteGap(context.Background(), "kucoin", testSym, gap))

	require.GreaterOrEqual(t, fake.Len("ticks:kucoin:BTC-USDT"), 1)
	require.Equal(t, 1, fake.Len("gaps:log"))

	entries := fake.Entries("gaps:log")
	require.Equal(t, "internal_merge_error", entries[0].Fields["gap_cause"])
}

func TestWriteGap_GapsLogWriteFailure(t *testing.T) {
	// AC7 third scenario: tick write succeeds, then gaps:log write fails.
	// FailAfter=1: call 1 (ticks stream in-band write) succeeds, call 2 (gaps:log) fails.
	fake := mock.NewFakeRedis()
	fake.FailAfter = 1
	clk := testutil.NewMockClock(testTime)
	// maxRetryDur=0: gaps:log failure exhausts retries immediately, WriteGap returns error.
	w := rediswriter.New(fake, clk, 0).WithSleep(noSleep)

	gap := gapdetector.GapEvent{
		Cause:     gapdetector.CauseExternalRateLimit,
		SeqBefore: 50,
		SeqAfter:  60,
		Timestamp: testTime,
	}
	err := w.WriteGap(context.Background(), "kucoin", testSym, gap)
	require.Error(t, err)

	// In-band ticks stream entry was written before gaps:log failed.
	require.Equal(t, 1, fake.Len("ticks:kucoin:BTC-USDT"))
	// gaps:log was not written — write failed.
	require.Equal(t, 0, fake.Len("gaps:log"))
}
