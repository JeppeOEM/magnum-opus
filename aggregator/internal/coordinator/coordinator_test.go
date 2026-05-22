//go:build l2

package coordinator_test

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	"github.com/mrqdt/magnum-opus/aggregator/internal/coordinator"
	"github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
	"github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
	"github.com/mrqdt/magnum-opus/aggregator/internal/testutil"
	"github.com/mrqdt/magnum-opus/aggregator/internal/testutil/mock"
	questdbwriter "github.com/mrqdt/magnum-opus/aggregator/internal/writer/questdb"
	rediswriter "github.com/mrqdt/magnum-opus/aggregator/internal/writer/redis"
)

// fakeExchange satisfies exchange.Exchange for coordinator L2 tests.
type fakeExchange struct {
	name    string
	ticksCh chan exchange.Tick
	sigsCh  chan exchange.Signal
}

func newFakeExchange(name string) *fakeExchange {
	return &fakeExchange{
		name:    name,
		ticksCh: make(chan exchange.Tick, 256),
		sigsCh:  make(chan exchange.Signal, 16),
	}
}

func (f *fakeExchange) Name() string                                              { return f.name }
func (f *fakeExchange) Connect(_ context.Context) error                           { return nil }
func (f *fakeExchange) Subscribe(_ []string, _ []exchange.FeedType) error         { return nil }
func (f *fakeExchange) AddSymbols(_ []string, _ []exchange.FeedType) error        { return nil }
func (f *fakeExchange) Ticks() <-chan exchange.Tick                                { return f.ticksCh }
func (f *fakeExchange) Signals() <-chan exchange.Signal                            { return f.sigsCh }
func (f *fakeExchange) Close() error                                               { return nil }

// newCoordinatorHarness builds a coordinator with one fake exchange and one symbol.
// Returns the coordinator, fake exchange, and a cancel func. Caller must call coord.Shutdown()
// and ilp.Close() in cleanup.
func newCoordinatorHarness(
	t *testing.T,
	exchName string,
	sym symbol.Symbol,
	fetcher coordinator.SnapshotFetcher,
) (coord *coordinator.Coordinator, fakeExch *fakeExchange, fakeRedis *mock.FakeRedis, ctx context.Context, cancel context.CancelFunc) {
	t.Helper()
	clk := testutil.NewMockClock(time.Date(2024, 1, 15, 12, 0, 0, 0, time.UTC))
	fakeRedis = mock.NewFakeRedis()
	fakeQDB := mock.NewFakeQuestDB()
	noSleep := func(_ context.Context, _ time.Duration) error { return nil }

	stream := rediswriter.New(fakeRedis, clk, 0).WithSleep(noSleep)
	ilp := questdbwriter.New(fakeQDB, fakeQDB, fakeRedis, clk,
		1*time.Millisecond, 1*time.Hour, 0).WithSleep(noSleep).Start()

	fakeExch = newFakeExchange(exchName)
	coord = coordinator.New(
		[]coordinator.ExchangeConfig{{Adapter: fakeExch, Symbols: []symbol.Symbol{sym}}},
		stream, ilp, fetcher, clk,
	).WithSleep(noSleep)

	ctx, cancel = context.WithCancel(context.Background())

	// coord.Shutdown() calls ilp.Close() internally — do not close ilp separately.
	t.Cleanup(func() {
		cancel()
		coord.Shutdown()
	})

	return coord, fakeExch, fakeRedis, ctx, cancel
}

// TestCoordinator_E2E_TickFlowGapSnapshot exercises the full pipeline:
// startup NeedsSnapshot → GoLive → ticks written → gap detected → gap marker in Redis.
func TestCoordinator_E2E_TickFlowGapSnapshot(t *testing.T) {
	sym := symbol.Symbol("BTC-USDT")
	streamKey := "ticks:kucoin:BTC-USDT"

	fetcher := mock.NewFakeSnapshotFetcher(map[string]coordinator.SnapshotResult{
		"kucoin:BTC-USDT": {Seq: 0, Bids: map[string]string{}, Asks: map[string]string{}},
	})

	coord, fakeExch, fakeRedis, ctx, _ := newCoordinatorHarness(t, "kucoin", sym, fetcher)

	go coord.Run(ctx)

	// Wait for the startup snapshot to be fetched (Worker → StateBuffering → snapReqs → dispatcher → fetcher)
	require.True(t, fetcher.WaitCalled(2*time.Second), "startup snapshot not fetched within 2s")

	// Allow Worker goroutine to receive from resultCh and transition to StateLive.
	// resultCh is buffered(1) and dispatcher sends immediately; Worker processes on next select.
	// 25ms >> 1 goroutine context switch on any modern scheduler.
	time.Sleep(25 * time.Millisecond)

	makeTick := func(seq uint64) exchange.Tick {
		return exchange.Tick{
			Exchange: "kucoin", Symbol: sym, Seq: seq,
			Type: exchange.EventTypeUpdate, Price: "100.00", Size: "1.0",
			TsExchange: time.Date(2024, 1, 15, 12, 0, 0, 0, time.UTC).UnixNano(),
			TsLocal:    time.Date(2024, 1, 15, 12, 0, 0, 0, time.UTC).UnixNano(),
		}
	}

	// Send ticks in Live state
	fakeExch.ticksCh <- makeTick(1)
	fakeExch.ticksCh <- makeTick(2)

	require.Eventually(t, func() bool {
		return fakeRedis.Len(streamKey) >= 2
	}, time.Second, time.Millisecond, "ticks 1 and 2 must be written to Redis")

	// Send tick with gap (seq=5, skips 3 and 4) — gap marker must precede tick 5
	fakeExch.ticksCh <- makeTick(5)

	// 4 entries total: tick1, tick2, gap-marker, tick5
	require.Eventually(t, func() bool {
		return fakeRedis.Len(streamKey) >= 4
	}, time.Second, time.Millisecond, "gap marker + tick5 must be written to Redis")

	entries := fakeRedis.Entries(streamKey)
	gapCount := 0
	for _, e := range entries {
		if e.Fields["type"] == "gap" {
			gapCount++
			require.Equal(t, "external_disconnect", e.Fields["gap_cause"])
			require.Equal(t, "2", e.Fields["seq_before"])
			require.Equal(t, "5", e.Fields["seq_after"])
		}
	}
	require.Equal(t, 1, gapCount, "exactly 1 gap marker expected (no duplicates)")
}

// TestCoordinator_E2E_NoGapDuplicate_OnWriteRetry verifies AC5: when a tick write fails
// internally and is retried successfully, the subsequent gap detection emits exactly one
// gap marker (no duplicates introduced by the retry mechanism).
func TestCoordinator_E2E_NoGapDuplicate_OnWriteRetry(t *testing.T) {
	sym := symbol.Symbol("BTC-USDT")
	streamKey := "ticks:kucoin:BTC-USDT"
	clk := testutil.NewMockClock(time.Date(2024, 1, 15, 12, 0, 0, 0, time.UTC))

	// Separate FakeRedis per writer so FailFirst only affects tick stream writes.
	fakeRedisStream := mock.NewFakeRedis()
	fakeRedisStream.FailFirst = 1 // first tick write fails; retry (noSleep + 10ms budget) succeeds
	fakeRedisQDB := mock.NewFakeRedis()
	fakeQDB := mock.NewFakeQuestDB()
	noSleep := func(_ context.Context, _ time.Duration) error { return nil }

	// maxRetryDur=10ms: MockClock doesn't advance, so deadline=now+10ms is always in the future,
	// allowing one retry before the injected failure clears.
	stream := rediswriter.New(fakeRedisStream, clk, 10*time.Millisecond).WithSleep(noSleep)
	ilp := questdbwriter.New(fakeQDB, fakeQDB, fakeRedisQDB, clk,
		1*time.Millisecond, 1*time.Hour, 0).WithSleep(noSleep).Start()

	fetcher := mock.NewFakeSnapshotFetcher(map[string]coordinator.SnapshotResult{
		"kucoin:BTC-USDT": {Seq: 0},
	})
	fakeExch := newFakeExchange("kucoin")
	coord := coordinator.New(
		[]coordinator.ExchangeConfig{{Adapter: fakeExch, Symbols: []symbol.Symbol{sym}}},
		stream, ilp, fetcher, clk,
	).WithSleep(noSleep)

	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(func() {
		cancel()
		coord.Shutdown()
	})

	go coord.Run(ctx)
	require.True(t, fetcher.WaitCalled(2*time.Second))
	time.Sleep(25 * time.Millisecond) // allow Worker to transition to StateLive

	makeTick := func(seq uint64) exchange.Tick {
		return exchange.Tick{
			Exchange: "kucoin", Symbol: sym, Seq: seq,
			Type: exchange.EventTypeUpdate, Price: "100.00", Size: "1.0",
			TsExchange: clk.Now().UnixNano(), TsLocal: clk.Now().UnixNano(),
		}
	}

	// Tick 1: first XAdd fails (FailFirst=1), retried, succeeds — no best-effort gap emitted.
	fakeExch.ticksCh <- makeTick(1)
	require.Eventually(t, func() bool {
		return fakeRedisStream.Len(streamKey) >= 1
	}, time.Second, time.Millisecond, "tick 1 must appear in Redis after retry")

	// Tick 4 with seq gap (2,3 missing): exactly one gap marker must be emitted.
	fakeExch.ticksCh <- makeTick(4)
	require.Eventually(t, func() bool {
		return fakeRedisStream.Len(streamKey) >= 3 // tick1, gap(1→4), tick4
	}, time.Second, time.Millisecond, "gap marker + tick4 must be written")

	gapCount := 0
	for _, e := range fakeRedisStream.Entries(streamKey) {
		if e.Fields["type"] == "gap" {
			gapCount++
		}
	}
	require.Equal(t, 1, gapCount, "exactly 1 gap marker: write retry must not produce duplicates")
}

// TestCoordinator_Shutdown_ExitsCleanly verifies Shutdown() returns promptly after ctx cancel.
func TestCoordinator_Shutdown_ExitsCleanly(t *testing.T) {
	sym := symbol.Symbol("ETH-USDT")
	fetcher := mock.NewFakeSnapshotFetcher(map[string]coordinator.SnapshotResult{
		"kucoin:ETH-USDT": {Seq: 0},
	})

	// Build harness but manage ctx ourselves (cleanup runs cancel+Shutdown)
	clk := testutil.NewMockClock(time.Date(2024, 1, 15, 12, 0, 0, 0, time.UTC))
	fakeRedis := mock.NewFakeRedis()
	fakeQDB := mock.NewFakeQuestDB()
	noSleep := func(_ context.Context, _ time.Duration) error { return nil }
	stream := rediswriter.New(fakeRedis, clk, 0).WithSleep(noSleep)
	ilp := questdbwriter.New(fakeQDB, fakeQDB, fakeRedis, clk,
		1*time.Millisecond, 1*time.Hour, 0).WithSleep(noSleep).Start()
	// coord.Shutdown() closes ilp — no separate cleanup needed

	fakeExch := newFakeExchange("kucoin")
	coord := coordinator.New(
		[]coordinator.ExchangeConfig{{Adapter: fakeExch, Symbols: []symbol.Symbol{sym}}},
		stream, ilp, fetcher, clk,
	).WithSleep(noSleep)

	ctx, cancel := context.WithCancel(context.Background())

	go coord.Run(ctx)
	require.True(t, fetcher.WaitCalled(2*time.Second), "startup snapshot not fetched")

	cancel()

	done := make(chan struct{})
	go func() {
		coord.Shutdown()
		close(done)
	}()

	select {
	case <-done:
		// Shutdown completed cleanly
	case <-time.After(3 * time.Second):
		t.Fatal("Shutdown did not complete within 3s after ctx cancel")
	}
}
