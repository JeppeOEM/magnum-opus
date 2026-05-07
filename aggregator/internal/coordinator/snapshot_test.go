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

// TestSnapshotDispatcher_SuccessPath verifies the dispatcher calls the fetcher and the
// Worker receives the result (transitions to StateLive and starts writing ticks to Redis).
func TestSnapshotDispatcher_SuccessPath(t *testing.T) {
	sym := symbol.Symbol("BTC-USDT")

	fetcher := mock.NewFakeSnapshotFetcher(map[string]coordinator.SnapshotResult{
		"kucoin:BTC-USDT": {Seq: 0},
	})

	coord, fakeExch, fakeRedis, ctx, _ := newCoordinatorHarness(t, "kucoin", sym, fetcher)

	go coord.Run(ctx)

	// Dispatcher must call fetcher exactly once for the startup snapshot
	require.True(t, fetcher.WaitCalled(2*time.Second), "dispatcher did not call fetcher")
	require.Equal(t, 1, fetcher.CallCount())

	// Allow Worker to process the snapshot result and go Live
	time.Sleep(25 * time.Millisecond)

	// Confirm the Worker is live by sending a tick and observing it in Redis
	fakeExch.ticksCh <- exchange_tick("kucoin", sym, 1)
	require.Eventually(t, func() bool {
		return fakeRedis.Len("ticks:kucoin:BTC-USDT") >= 1
	}, time.Second, time.Millisecond, "Worker must write tick to Redis after snapshot")
}

// TestSnapshotDispatcher_RetryOnFetchFailure verifies the dispatcher retries when
// FetchSnapshot returns an error, and eventually delivers the result to the Worker.
func TestSnapshotDispatcher_RetryOnFetchFailure(t *testing.T) {
	sym := symbol.Symbol("BTC-USDT")

	fetcher := mock.NewFakeSnapshotFetcher(map[string]coordinator.SnapshotResult{
		"kucoin:BTC-USDT": {Seq: 0},
	})
	fetcher.FailFirst = 2 // fail first 2 calls, succeed on 3rd

	clk := testutil.NewMockClock(time.Date(2024, 1, 15, 12, 0, 0, 0, time.UTC))
	fakeRedis := mock.NewFakeRedis()
	fakeQDB := mock.NewFakeQuestDB()
	noSleep := func(_ context.Context, _ time.Duration) error { return nil }

	stream := rediswriter.New(fakeRedis, clk, 0).WithSleep(noSleep)
	ilp := questdbwriter.New(fakeQDB, fakeQDB, fakeRedis, clk,
		1*time.Millisecond, 1*time.Hour, 0).WithSleep(noSleep).Start()
	// coord.Shutdown() (called via cancel+t.Cleanup) closes ilp

	fakeExch := newFakeExchange("kucoin")
	coord := coordinator.New(
		[]coordinator.ExchangeConfig{{Adapter: fakeExch, Symbols: []symbol.Symbol{sym}}},
		stream, ilp, fetcher, clk,
	).WithSleep(noSleep) // noSleep skips backoff delays between retries

	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(func() {
		cancel()
		coord.Shutdown()
	})

	go coord.Run(ctx)

	// Dispatcher must retry to at least 3 calls (2 failures + 1 success)
	require.Eventually(t, func() bool {
		return fetcher.CallCount() >= 3
	}, 2*time.Second, time.Millisecond, "dispatcher must retry; expected ≥3 calls")

	// Allow Worker to process the successful snapshot result and go Live
	time.Sleep(25 * time.Millisecond)

	// Tick must flow — Worker is Live after the successful 3rd fetch
	fakeExch.ticksCh <- exchange_tick("kucoin", sym, 1)
	require.Eventually(t, func() bool {
		return fakeRedis.Len("ticks:kucoin:BTC-USDT") >= 1
	}, time.Second, time.Millisecond, "Worker must write tick after retry-succeeded snapshot")
}

// exchange_tick builds a minimal update tick for use in snapshot dispatcher tests.
func exchange_tick(exch string, sym symbol.Symbol, seq uint64) exchange.Tick {
	return exchange.Tick{
		Exchange: exch, Symbol: sym, Seq: seq,
		Type: exchange.EventTypeUpdate, Price: "100.00", Size: "1.0",
		TsExchange: time.Date(2024, 1, 15, 12, 0, 0, 0, time.UTC).UnixNano(),
		TsLocal:    time.Date(2024, 1, 15, 12, 0, 0, 0, time.UTC).UnixNano(),
	}
}
