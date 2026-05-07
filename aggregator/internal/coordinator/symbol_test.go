//go:build l2

package coordinator_test

import (
	"context"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/testutil"
	"github.com/stretchr/testify/require"

	"github.com/mrqdt/magnum-opus/aggregator/internal/coordinator"
	"github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
	"github.com/mrqdt/magnum-opus/aggregator/internal/gapdetector"
	"github.com/mrqdt/magnum-opus/aggregator/internal/metrics"
	"github.com/mrqdt/magnum-opus/aggregator/internal/orderbook"
	"github.com/mrqdt/magnum-opus/aggregator/internal/reconnect"
	"github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
	clockutil "github.com/mrqdt/magnum-opus/aggregator/internal/testutil"
	"github.com/mrqdt/magnum-opus/aggregator/internal/testutil/mock"
	questdbwriter "github.com/mrqdt/magnum-opus/aggregator/internal/writer/questdb"
	rediswriter "github.com/mrqdt/magnum-opus/aggregator/internal/writer/redis"
)

var testSym = symbol.Symbol("BTC-USDT")

var testClock = time.Date(2024, 1, 15, 12, 0, 0, 0, time.UTC)

var noSleep = func(_ context.Context, _ time.Duration) error { return nil }

func makeTick(seq uint64) exchange.Tick {
	return exchange.Tick{
		Exchange:   "kucoin",
		Symbol:     testSym,
		Seq:        seq,
		TsExchange: 1_700_000_000_000_000_000,
		TsLocal:    1_700_000_000_100_000_000,
		Side:       "bid",
		Price:      "29500.50",
		Size:       "0.01",
		Type:       exchange.EventTypeUpdate,
	}
}

// newLiveWorker builds a Worker already in Live state (bypasses snapshot phase).
// Uses maxRetryDur=0 for the stream writer so Redis failures return immediately
// (frozen MockClock would otherwise cause infinite retries with any positive duration).
func newLiveWorker(
	t *testing.T,
	redis *mock.FakeRedis,
	qdb *mock.FakeQuestDB,
) (*coordinator.Worker, chan exchange.Tick, chan coordinator.SnapshotRequest, context.CancelFunc) {
	t.Helper()
	deltas := make(chan exchange.Tick, 10)
	snaps := make(chan coordinator.SnapshotRequest, 1)
	clk := clockutil.NewMockClock(testClock)

	// maxRetryDur=0: failures are immediate (no retry). The frozen clock would cause
	// infinite retries with any positive maxRetryDur.
	stream := rediswriter.New(redis, clk, 0).WithSleep(noSleep)
	ilp := questdbwriter.New(qdb, qdb, redis, clk,
		1*time.Millisecond, 1*time.Hour, 30*time.Second).WithSleep(noSleep).Start()

	book := orderbook.New()
	recon := reconnect.New()
	recon.GoLive()

	w := coordinator.NewWorker("kucoin", testSym, deltas, stream, ilp, snaps, book, recon, clk, nil)

	ctx, cancel := context.WithCancel(context.Background())
	go w.Run(ctx)

	t.Cleanup(func() {
		cancel()
		ilp.Close(context.Background())
	})
	return w, deltas, snaps, cancel
}

// TestWorker_NormalTickFlow: tick in Live state is written to both Redis and QuestDB (AC1, AC2).
func TestWorker_NormalTickFlow(t *testing.T) {
	redis := mock.NewFakeRedis()
	qdb := mock.NewFakeQuestDB()
	_, deltas, _, _ := newLiveWorker(t, redis, qdb)

	deltas <- makeTick(1)

	require.Eventually(t, func() bool {
		return len(redis.Entries("ticks:kucoin:BTC-USDT")) >= 1
	}, 500*time.Millisecond, 5*time.Millisecond, "tick must be written to Redis")

	require.Eventually(t, func() bool {
		return qdb.CommittedCount() >= 1
	}, 500*time.Millisecond, 5*time.Millisecond, "tick must be committed to QuestDB")
}

// TestWorker_GapDetected_EmitsMarker: seq jump triggers gap markers in both writers (AC1).
func TestWorker_GapDetected_EmitsMarker(t *testing.T) {
	redis := mock.NewFakeRedis()
	qdb := mock.NewFakeQuestDB()
	_, deltas, _, _ := newLiveWorker(t, redis, qdb)

	deltas <- makeTick(1)
	// Wait for seq=1 to establish lastSeq
	require.Eventually(t, func() bool {
		return len(redis.Entries("ticks:kucoin:BTC-USDT")) >= 1
	}, 500*time.Millisecond, 5*time.Millisecond)

	// seq=3 with lastSeq=1: gap (expected 2, got 3)
	deltas <- makeTick(3)

	// Gap marker written to gaps:log (AC1 Redis path)
	require.Eventually(t, func() bool {
		return len(redis.Entries("gaps:log")) >= 1
	}, 500*time.Millisecond, 5*time.Millisecond, "gap marker must be written to gaps:log")

	// Tick after gap also written (in-band gap + tick = ticks stream has ≥3 entries)
	require.Eventually(t, func() bool {
		return len(redis.Entries("ticks:kucoin:BTC-USDT")) >= 3
	}, 500*time.Millisecond, 5*time.Millisecond, "tick after gap must be written to stream")

	// Verify ordering: gap marker (entries[1]) precedes the tick that triggered it (entries[2])
	entries := redis.Entries("ticks:kucoin:BTC-USDT")
	require.Equal(t, "gap", entries[1].Fields["type"], "gap marker must appear before tick 3 in stream")
	require.Equal(t, "external_disconnect", entries[1].Fields["gap_cause"])
	require.Equal(t, "tick", entries[2].Fields["type"])

	// AC1: gap marker also written to QuestDB (ILP path)
	require.Eventually(t, func() bool {
		return qdb.CommittedCount() >= 2 // gap marker row + tick3 row
	}, 500*time.Millisecond, 5*time.Millisecond, "gap marker must be committed to QuestDB")
}

// TestWorker_RedisWriteFailure_ContinuesCapture: Redis failure does not halt capture (AC2, NFR9).
// Uses a separate FakeRedis for QuestDB audit so failure injection only affects stream writes.
func TestWorker_RedisWriteFailure_ContinuesCapture(t *testing.T) {
	redisStream := mock.NewFakeRedis()
	redisAudit := mock.NewFakeRedis() // questdb audit traffic: unaffected by stream failures
	qdb := mock.NewFakeQuestDB()
	snaps := make(chan coordinator.SnapshotRequest, 1)
	deltas := make(chan exchange.Tick, 10)
	clk := clockutil.NewMockClock(testClock)

	// maxRetryDur=0: Redis failure returns immediately (no retry with frozen clock).
	stream := rediswriter.New(redisStream, clk, 0).WithSleep(noSleep)
	ilp := questdbwriter.New(qdb, qdb, redisAudit, clk,
		1*time.Millisecond, 1*time.Hour, 30*time.Second).WithSleep(noSleep).Start()

	book := orderbook.New()
	recon := reconnect.New()
	recon.GoLive()

	w := coordinator.NewWorker("kucoin", testSym, deltas, stream, ilp, snaps, book, recon, clk, nil)
	ctx, cancel := context.WithCancel(context.Background())
	go w.Run(ctx)
	t.Cleanup(func() {
		cancel()
		ilp.Close(context.Background())
	})

	// Tick 1 — Redis succeeds (FailAfter=1 allows first call through)
	redisStream.FailAfter = 1
	deltas <- makeTick(1)
	require.Eventually(t, func() bool {
		return len(redisStream.Entries("ticks:kucoin:BTC-USDT")) >= 1
	}, 500*time.Millisecond, 5*time.Millisecond, "tick 1 must reach Redis")

	// Tick 2 — Redis write fails; worker must continue, QuestDB must still get both ticks
	deltas <- makeTick(2)
	require.Eventually(t, func() bool {
		return qdb.CommittedCount() >= 2
	}, 500*time.Millisecond, 5*time.Millisecond, "QuestDB must receive both ticks despite Redis failure")
}

// panicStream panics on the first Write call, then delegates to the real writer.
type panicStream struct {
	real   coordinator.StreamWriter
	panics int
}

func (p *panicStream) Write(ctx context.Context, tick exchange.Tick) error {
	if p.panics > 0 {
		p.panics--
		panic("injected panic for test")
	}
	return p.real.Write(ctx, tick)
}

func (p *panicStream) WriteGap(ctx context.Context, exch string, sym symbol.Symbol, gap gapdetector.GapEvent) error {
	return p.real.WriteGap(ctx, exch, sym, gap)
}

// TestWorker_PanicRecovery: panic inside handleTick is recovered; goroutine re-enters loop (AC4).
func TestWorker_PanicRecovery(t *testing.T) {
	redis := mock.NewFakeRedis()
	qdb := mock.NewFakeQuestDB()
	snaps := make(chan coordinator.SnapshotRequest, 2)
	deltas := make(chan exchange.Tick, 10)
	clk := clockutil.NewMockClock(testClock)

	realStream := rediswriter.New(redis, clk, 0).WithSleep(noSleep)
	panicS := &panicStream{real: realStream, panics: 1}
	ilp := questdbwriter.New(qdb, qdb, redis, clk,
		1*time.Millisecond, 1*time.Hour, 30*time.Second).WithSleep(noSleep).Start()

	book := orderbook.New()
	recon := reconnect.New()
	recon.GoLive()

	w := coordinator.NewWorker("kucoin", testSym, deltas, panicS, ilp, snaps, book, recon, clk, nil)
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(func() {
		cancel()
		ilp.Close(context.Background())
	})
	go w.Run(ctx)

	// Tick 1 triggers panic inside panicStream.Write
	deltas <- makeTick(1)

	// After panic recovery, a SnapshotRequest must be sent with reason="panic" (AC4)
	var req coordinator.SnapshotRequest
	require.Eventually(t, func() bool {
		select {
		case req = <-snaps:
			return true
		default:
			return false
		}
	}, 500*time.Millisecond, 5*time.Millisecond, "SnapshotRequest must be sent after panic")
	require.Equal(t, "panic", req.Signal.Reason)

	// AC4: CauseInternalMergeError gap marker must be written to both writers
	require.Eventually(t, func() bool {
		for _, e := range redis.Entries("gaps:log") {
			if e.Fields["gap_cause"] == "internal_merge_error" {
				return true
			}
		}
		return false
	}, 500*time.Millisecond, 5*time.Millisecond, "panic must emit internal_merge_error gap marker to gaps:log")

	// Resolve the snapshot so worker can go live again
	req.ResultCh <- coordinator.SnapshotResult{Seq: 1}

	// Tick 2 — worker must process it normally after recovery
	deltas <- makeTick(2)
	require.Eventually(t, func() bool {
		return len(redis.Entries("ticks:kucoin:BTC-USDT")) >= 1
	}, 500*time.Millisecond, 5*time.Millisecond, "worker must write ticks after panic recovery")
}

// TestWorker_CtxCancel_StateBuffering_EmitsGapMarker: when ctx is cancelled while Worker is in
// StateBuffering (snapshot in-flight), a best-effort external_disconnect gap marker is emitted (AC4).
func TestWorker_CtxCancel_StateBuffering_EmitsGapMarker(t *testing.T) {
	redis := mock.NewFakeRedis()
	qdb := mock.NewFakeQuestDB()
	deltas := make(chan exchange.Tick, 10)
	snaps := make(chan coordinator.SnapshotRequest, 1)
	clk := clockutil.NewMockClock(testClock)

	stream := rediswriter.New(redis, clk, 0).WithSleep(noSleep)
	ilp := questdbwriter.New(qdb, qdb, redis, clk,
		1*time.Millisecond, 1*time.Hour, 30*time.Second).WithSleep(noSleep).Start()
	t.Cleanup(func() { ilp.Close(context.Background()) })

	book := orderbook.New()
	recon := reconnect.New()
	// Leave recon in StateInitial → Worker enters StateBuffering immediately on first loop()

	w := coordinator.NewWorker("kucoin", testSym, deltas, stream, ilp, snaps, book, recon, clk, nil)

	ctx, cancel := context.WithCancel(context.Background())
	runDone := make(chan struct{})
	go func() {
		defer close(runDone)
		w.Run(ctx)
	}()

	// Wait for the Worker to enter StateBuffering (it sends a SnapshotRequest immediately)
	require.Eventually(t, func() bool {
		select {
		case <-snaps:
			return true
		default:
			return false
		}
	}, 500*time.Millisecond, time.Millisecond, "Worker must request snapshot (enter StateBuffering)")

	// Cancel while snapshot is still in-flight (no result sent to resultCh)
	cancel()

	select {
	case <-runDone:
	case <-time.After(time.Second):
		t.Fatal("Run() did not return within 1s after ctx cancellation")
	}

	// Gap marker must have been written to gaps:log
	require.Eventually(t, func() bool {
		for _, e := range redis.Entries("gaps:log") {
			if e.Fields["gap_cause"] == "external_disconnect" {
				return true
			}
		}
		return false
	}, 500*time.Millisecond, time.Millisecond, "shutdown gap marker must be written to gaps:log")
}

// TestWorker_CtxCancellation: cancelling ctx causes Run() to return within 500ms (AC5).
func TestWorker_CtxCancellation(t *testing.T) {
	redis := mock.NewFakeRedis()
	qdb := mock.NewFakeQuestDB()
	deltas := make(chan exchange.Tick, 10)
	snaps := make(chan coordinator.SnapshotRequest, 1)
	clk := clockutil.NewMockClock(testClock)

	stream := rediswriter.New(redis, clk, 0).WithSleep(noSleep)
	ilp := questdbwriter.New(qdb, qdb, redis, clk,
		1*time.Millisecond, 1*time.Hour, 30*time.Second).WithSleep(noSleep).Start()
	t.Cleanup(func() { ilp.Close(context.Background()) })

	book := orderbook.New()
	recon := reconnect.New()
	recon.GoLive()

	w := coordinator.NewWorker("kucoin", testSym, deltas, stream, ilp, snaps, book, recon, clk, nil)

	ctx, cancel := context.WithCancel(context.Background())
	runDone := make(chan struct{})
	go func() {
		defer close(runDone)
		w.Run(ctx)
	}()

	cancel()

	select {
	case <-runDone:
		// Run() returned promptly after ctx cancellation
	case <-time.After(500 * time.Millisecond):
		t.Fatal("Run() did not return within 500ms after ctx cancellation")
	}
}

// TestWorker_GapTotal_InternalMergeError: internal_merge_error increments on panic recovery (AC3).
func TestWorker_GapTotal_InternalMergeError(t *testing.T) {
	prom := prometheus.NewRegistry()
	reg := metrics.New(prom)

	redis := mock.NewFakeRedis()
	qdb := mock.NewFakeQuestDB()
	deltas := make(chan exchange.Tick, 10)
	snaps := make(chan coordinator.SnapshotRequest, 2)
	clk := clockutil.NewMockClock(testClock)

	realStream := rediswriter.New(redis, clk, 0).WithSleep(noSleep)
	ps := &panicStream{real: realStream, panics: 1}

	ilp := questdbwriter.New(qdb, qdb, redis, clk,
		1*time.Millisecond, 1*time.Hour, 30*time.Second).WithSleep(noSleep).Start()

	book := orderbook.New()
	recon := reconnect.New()
	recon.GoLive()

	w := coordinator.NewWorker("kucoin", testSym, deltas, ps, ilp, snaps, book, recon, clk, reg)
	ctx, cancel := context.WithCancel(context.Background())
	go w.Run(ctx)
	t.Cleanup(func() {
		cancel()
		ilp.Close(context.Background())
	})

	// The first tick triggers a panic in panicStream.Write; Run() recovers, emits
	// CauseInternalMergeError gap marker, increments the counter, then requests a snapshot.
	deltas <- makeTick(1)

	// Wait for the gap marker — guarantees the metric has been incremented before assertion.
	require.Eventually(t, func() bool {
		for _, e := range redis.Entries("gaps:log") {
			if e.Fields["gap_cause"] == "internal_merge_error" {
				return true
			}
		}
		return false
	}, time.Second, 5*time.Millisecond, "internal_merge_error gap marker must be written to gaps:log")

	internalGap := testutil.ToFloat64(reg.GapTotal.WithLabelValues("kucoin", "BTC-USDT", "internal_merge_error"))
	require.Equal(t, float64(1), internalGap, "internal_merge_error must be 1 after panic recovery")
}

// TestWorker_GapTotal_IncrementedOnGap: gap_total counter increments when a sequence gap is
// detected. Also verifies TicksTotal increments on successful writes and that unrelated
// causes remain at zero. (AC3 — L2 test exercising metric increment without HTTP wiring)
func TestWorker_GapTotal_IncrementedOnGap(t *testing.T) {
	prom := prometheus.NewRegistry()
	reg := metrics.New(prom)

	redis := mock.NewFakeRedis()
	qdb := mock.NewFakeQuestDB()
	deltas := make(chan exchange.Tick, 10)
	snaps := make(chan coordinator.SnapshotRequest, 1)
	clk := clockutil.NewMockClock(testClock)

	stream := rediswriter.New(redis, clk, 0).WithSleep(noSleep)
	ilp := questdbwriter.New(qdb, qdb, redis, clk,
		1*time.Millisecond, 1*time.Hour, 30*time.Second).WithSleep(noSleep).Start()

	book := orderbook.New()
	recon := reconnect.New()
	recon.GoLive()

	w := coordinator.NewWorker("kucoin", testSym, deltas, stream, ilp, snaps, book, recon, clk, reg)
	ctx, cancel := context.WithCancel(context.Background())
	go w.Run(ctx)
	t.Cleanup(func() {
		cancel()
		ilp.Close(context.Background())
	})

	// Send seq=1 to establish lastSeq
	deltas <- makeTick(1)
	require.Eventually(t, func() bool {
		return len(redis.Entries("ticks:kucoin:BTC-USDT")) >= 1
	}, 500*time.Millisecond, 5*time.Millisecond, "seq=1 tick must be written")

	// Send seq=5 (gap: expected 2, got 5) — triggers gap_total increment
	deltas <- makeTick(5)
	require.Eventually(t, func() bool {
		return len(redis.Entries("gaps:log")) >= 1
	}, 500*time.Millisecond, 5*time.Millisecond, "gap marker must be written before checking counter")

	// external_disconnect counter must be 1
	externalGap := testutil.ToFloat64(reg.GapTotal.WithLabelValues("kucoin", "BTC-USDT", "external_disconnect"))
	require.Equal(t, float64(1), externalGap, "external_disconnect gap_total must be 1")

	// internal_merge_error must stay at 0 (no panic, no stale snapshot)
	internalGap := testutil.ToFloat64(reg.GapTotal.WithLabelValues("kucoin", "BTC-USDT", "internal_merge_error"))
	require.Equal(t, float64(0), internalGap, "internal_merge_error gap_total must remain 0")

	// TicksTotal must reflect successful writes (seq=1 and seq=5 both written)
	ticks := testutil.ToFloat64(reg.TicksTotal.WithLabelValues("kucoin", "BTC-USDT"))
	require.GreaterOrEqual(t, ticks, float64(2), "ticks_total must be >= 2 after two successful writes")
}
