//go:build l2

package questdb_test

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
	questdbwriter "github.com/mrqdt/magnum-opus/aggregator/internal/writer/questdb"
)

var (
	testTime = time.Date(2024, 1, 15, 12, 0, 0, 0, time.UTC)
	testSym  = symbol.Symbol("BTC-USDT")
	noSleep  = func(_ context.Context, _ time.Duration) error { return nil }
)

func newWriter(qdb *mock.FakeQuestDB, redis *mock.FakeRedis) *questdbwriter.Writer {
	clk := testutil.NewMockClock(testTime)
	// 1ms flush and WAL intervals so tests trigger them quickly; Start() called after WithSleep to avoid race.
	return questdbwriter.New(qdb, qdb, redis, clk,
		1*time.Millisecond, 1*time.Millisecond, 30*time.Second).WithSleep(noSleep).Start()
}

func makeTick(typ exchange.EventType) exchange.Tick {
	return exchange.Tick{
		Exchange:   "kucoin",
		Symbol:     testSym,
		Seq:        42,
		TsExchange: 1_700_000_000_000_000_000,
		TsLocal:    1_700_000_000_100_000_000,
		Side:       "bid",
		Price:      "29500.50",
		Size:       "0.01",
		Type:       typ,
	}
}

// TestWrite_Buffered verifies that Write buffers a row without immediately committing.
// AC1: tick buffered, sender goroutine owns ILP sender.
func TestWrite_Buffered(t *testing.T) {
	qdb := mock.NewFakeQuestDB()
	redis := mock.NewFakeRedis()
	clk := testutil.NewMockClock(testTime)
	// Very long flush interval so auto-flush does not fire during the test window.
	w := questdbwriter.New(qdb, qdb, redis, clk,
		1*time.Hour, 1*time.Hour, 30*time.Second).WithSleep(noSleep).Start()

	require.NoError(t, w.Write(context.Background(), makeTick(exchange.EventTypeUpdate)))

	// No flush yet — committed count is 0
	time.Sleep(20 * time.Millisecond)
	require.Equal(t, 0, qdb.CommittedCount(), "rows must not be committed before flush")

	// Close triggers final flush — row is now committed
	require.NoError(t, w.Close(context.Background()))
	require.Equal(t, 1, qdb.CommittedCount())
}

// TestFlush_SendsRows verifies that the flush timer causes buffered rows to be committed.
// AC2: 500ms flush timer fires → Flush called → rows committed.
func TestFlush_SendsRows(t *testing.T) {
	qdb := mock.NewFakeQuestDB()
	redis := mock.NewFakeRedis()
	w := newWriter(qdb, redis)
	defer w.Close(context.Background())

	require.NoError(t, w.Write(context.Background(), makeTick(exchange.EventTypeUpdate)))

	// Wait for at least one flush cycle (1ms interval + goroutine scheduling buffer)
	require.Eventually(t, func() bool {
		return qdb.CommittedCount() >= 1
	}, 500*time.Millisecond, 5*time.Millisecond)
}

// TestFlush_TradeTick verifies gap markers and trade events are buffered correctly.
func TestFlush_TradeTick(t *testing.T) {
	qdb := mock.NewFakeQuestDB()
	redis := mock.NewFakeRedis()
	w := newWriter(qdb, redis)
	defer w.Close(context.Background())

	tick := makeTick(exchange.EventTypeTrade)
	tick.Side = ""
	require.NoError(t, w.Write(context.Background(), tick))

	require.Eventually(t, func() bool {
		return qdb.CommittedCount() >= 1
	}, 500*time.Millisecond, 5*time.Millisecond)
}

// TestFlush_WALSuspension_AutoResumes verifies FR24: WAL auto-resume on flush failure.
// AC3: WAL suspension detected, RESUME WAL issued, metric callback fired, subsequent flush succeeds.
func TestFlush_WALSuspension_AutoResumes(t *testing.T) {
	qdb := mock.NewFakeQuestDB()
	redis := mock.NewFakeRedis()

	clk := testutil.NewMockClock(testTime)
	metricCalls := 0
	// maxFlushRetryDur=100ms so the retry loop has time to check WAL and retry
	w := questdbwriter.New(qdb, qdb, redis, clk,
		1*time.Millisecond, 1*time.Millisecond, 100*time.Millisecond).
		WithSleep(noSleep).
		WithWALSuspendedMetric(func() { metricCalls++ }).
		Start()
	defer w.Close(context.Background())

	qdb.SuspendWAL()
	require.NoError(t, w.Write(context.Background(), makeTick(exchange.EventTypeUpdate)))

	// With WAL suspended: flush fails → checkWAL called → metric fired → ResumeWAL → retry succeeds
	require.Eventually(t, func() bool {
		return qdb.CommittedCount() >= 1
	}, 500*time.Millisecond, 5*time.Millisecond)

	// WAL should be resumed
	suspended, err := qdb.IsWALSuspended(context.Background())
	require.NoError(t, err)
	require.False(t, suspended, "WAL must be resumed after auto-recovery")
	// AC3: metric callback fired at least once during suspension
	require.GreaterOrEqual(t, metricCalls, 1, "WAL suspension metric must fire at least once")
}

// TestFlush_Retry_ExhaustionContinues verifies NFR9: flush failure does not halt capture.
// AC4: after retry exhaustion, log ERROR and continue accepting new ticks.
func TestFlush_Retry_ExhaustionContinues(t *testing.T) {
	qdb := mock.NewFakeQuestDB()
	redis := mock.NewFakeRedis()

	clk := testutil.NewMockClock(testTime)
	// maxFlushRetryDur=0: exhaustion after first failure; WAL stays suspended
	w := questdbwriter.New(qdb, qdb, redis, clk,
		1*time.Millisecond, 1*time.Hour, 0).WithSleep(noSleep).Start()
	defer w.Close(context.Background())

	qdb.SuspendWAL()

	// Write first tick — will fail flush immediately (exhausted)
	require.NoError(t, w.Write(context.Background(), makeTick(exchange.EventTypeUpdate)))
	time.Sleep(50 * time.Millisecond)

	// Write a second tick — must succeed (capture not halted)
	require.NoError(t, w.Write(context.Background(), makeTick(exchange.EventTypeUpdate)))

	// Nothing committed (WAL still suspended, no retry)
	require.Equal(t, 0, qdb.CommittedCount())
}

// TestFlush_AuditTrail verifies flush-begin and flush-complete are published to Redis.
// AC6: flush audit trail in questdb:flush:pending stream.
func TestFlush_AuditTrail(t *testing.T) {
	qdb := mock.NewFakeQuestDB()
	redis := mock.NewFakeRedis()
	w := newWriter(qdb, redis)
	defer w.Close(context.Background())

	require.NoError(t, w.Write(context.Background(), makeTick(exchange.EventTypeUpdate)))

	// Wait for flush to complete
	require.Eventually(t, func() bool {
		entries := redis.Entries("questdb:flush:pending")
		if len(entries) < 2 {
			return false
		}
		// Check for both flush-begin and flush-complete
		hasBegin, hasComplete := false, false
		for _, e := range entries {
			switch e.Fields["type"] {
			case "flush-begin":
				hasBegin = true
			case "flush-complete":
				hasComplete = true
			}
		}
		return hasBegin && hasComplete
	}, 500*time.Millisecond, 5*time.Millisecond)

	entries := redis.Entries("questdb:flush:pending")
	// First entry must be flush-begin with correct row_count
	var beginEntry mock.FakeEntry
	for _, e := range entries {
		if e.Fields["type"] == "flush-begin" {
			beginEntry = e
			break
		}
	}
	require.Equal(t, "1", beginEntry.Fields["row_count"])
}

// TestClose_FlushesRemaining verifies that Close triggers a final flush.
// AC1+2: Close drains channel and flushes before returning.
func TestClose_FlushesRemaining(t *testing.T) {
	qdb := mock.NewFakeQuestDB()
	redis := mock.NewFakeRedis()
	// Very long flush interval so auto-flush doesn't fire during the test
	clk := testutil.NewMockClock(testTime)
	w := questdbwriter.New(qdb, qdb, redis, clk,
		1*time.Hour, 1*time.Hour, 30*time.Second).WithSleep(noSleep).Start()

	require.NoError(t, w.Write(context.Background(), makeTick(exchange.EventTypeUpdate)))

	// Close must flush the pending row
	require.NoError(t, w.Close(context.Background()))
	require.Equal(t, 1, qdb.CommittedCount())
}

// TestWriteGap_Buffered verifies gap markers are buffered and committed on flush.
func TestWriteGap_Buffered(t *testing.T) {
	qdb := mock.NewFakeQuestDB()
	redis := mock.NewFakeRedis()
	w := newWriter(qdb, redis)
	defer w.Close(context.Background())

	gap := gapdetector.GapEvent{
		Cause:     gapdetector.CauseExternalDisconnect,
		SeqBefore: 100,
		SeqAfter:  105,
		Timestamp: testTime,
	}
	require.NoError(t, w.WriteGap(context.Background(), "kucoin", testSym, gap))

	require.Eventually(t, func() bool {
		return qdb.CommittedCount() >= 1
	}, 500*time.Millisecond, 5*time.Millisecond)
}

// TestFlush_DanglingBegin verifies AC6: flush-begin appears without flush-complete when flush exhausts.
// A monitoring alert should fire if questdb:flush:pending has a begin with no matching complete.
func TestFlush_DanglingBegin(t *testing.T) {
	qdb := mock.NewFakeQuestDB()
	redis := mock.NewFakeRedis()
	clk := testutil.NewMockClock(testTime)

	// WAL always suspended + maxFlushRetryDur=0: flush-begin written, flush exhausts, no flush-complete.
	w := questdbwriter.New(qdb, qdb, redis, clk,
		1*time.Millisecond, 1*time.Hour, 0).WithSleep(noSleep).Start()
	defer w.Close(context.Background())

	qdb.SuspendWAL()
	require.NoError(t, w.Write(context.Background(), makeTick(exchange.EventTypeUpdate)))

	// Wait for flush attempt
	require.Eventually(t, func() bool {
		entries := redis.Entries("questdb:flush:pending")
		for _, e := range entries {
			if e.Fields["type"] == "flush-begin" {
				return true
			}
		}
		return false
	}, 500*time.Millisecond, 5*time.Millisecond)

	// Verify flush-begin present but no flush-complete (dangling begin — detectable by monitoring)
	entries := redis.Entries("questdb:flush:pending")
	hasBegin, hasComplete := false, false
	for _, e := range entries {
		switch e.Fields["type"] {
		case "flush-begin":
			hasBegin = true
		case "flush-complete":
			hasComplete = true
		}
	}
	require.True(t, hasBegin, "flush-begin must be written before flush attempt")
	require.False(t, hasComplete, "flush-complete must NOT appear when flush exhausts retries")
}

// TestWALAccepted_NotCommitted verifies the accepted-but-not-committed semantics.
// AC2 note: flush returning nil does not guarantee rows are queryable.
func TestWALAccepted_NotCommitted(t *testing.T) {
	// Test: rows are accepted by sender.Write but CommittedCount stays 0 until Flush succeeds.
	qdb2 := mock.NewFakeQuestDB()
	redis2 := mock.NewFakeRedis()
	clk := testutil.NewMockClock(testTime)
	w := questdbwriter.New(qdb2, qdb2, redis2, clk,
		1*time.Hour, 1*time.Hour, 30*time.Second).WithSleep(noSleep).Start()

	require.NoError(t, w.Write(context.Background(), makeTick(exchange.EventTypeUpdate)))
	time.Sleep(20 * time.Millisecond)

	// Row buffered in sender (accepted) but not committed (no flush yet)
	require.Equal(t, 0, qdb2.CommittedCount(), "not committed before flush")
	require.Equal(t, 1, qdb2.RowCount(), "accepted by sender.Write")

	require.NoError(t, w.Close(context.Background()))
	require.Equal(t, 1, qdb2.CommittedCount(), "committed after Close flushes")
}
