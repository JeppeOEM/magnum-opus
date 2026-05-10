package coordinator_test

import (
	"context"
	"sync"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/mrqdt/magnum-opus/aggregator/internal/coordinator"
	"github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
	"github.com/mrqdt/magnum-opus/aggregator/internal/gapdetector"
	"github.com/mrqdt/magnum-opus/aggregator/internal/orderbook"
	"github.com/mrqdt/magnum-opus/aggregator/internal/reconnect"
	"github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
	clockutil "github.com/mrqdt/magnum-opus/aggregator/internal/testutil"
)

// ── Minimal fakes (L1-safe, no Redis/QuestDB) ─────────────────────────────────

type fakeStream struct{}

func (f *fakeStream) Write(_ context.Context, _ exchange.Tick) error           { return nil }
func (f *fakeStream) WriteSnapshot(_ context.Context, _ string, _ symbol.Symbol, _ uint64, _ int64) error {
	return nil
}
func (f *fakeStream) WriteGap(_ context.Context, _ string, _ symbol.Symbol, _ gapdetector.GapEvent) error {
	return nil
}

type fakeILP struct{}

func (f *fakeILP) Write(_ context.Context, _ exchange.Tick) error                                  { return nil }
func (f *fakeILP) WriteGap(_ context.Context, _ string, _ symbol.Symbol, _ gapdetector.GapEvent) error { return nil }
func (f *fakeILP) Close(_ context.Context) error                                                   { return nil }

type fakeOBPublisher struct {
	mu    sync.Mutex
	calls []exchange.Tick
}

func (f *fakeOBPublisher) Publish(_ context.Context, _ string, _ symbol.Symbol, _ orderbook.Snapshot, tick exchange.Tick) error {
	f.mu.Lock()
	f.calls = append(f.calls, tick)
	f.mu.Unlock()
	return nil
}

func (f *fakeOBPublisher) CallCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.calls)
}

func (f *fakeOBPublisher) LastTick() (exchange.Tick, bool) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.calls) == 0 {
		return exchange.Tick{}, false
	}
	return f.calls[len(f.calls)-1], true
}

func newLiveWorkerL1(pub coordinator.OBPublisher) (*coordinator.Worker, chan exchange.Tick, chan coordinator.SnapshotRequest) {
	deltas := make(chan exchange.Tick, 10)
	snaps := make(chan coordinator.SnapshotRequest, 1)
	clk := clockutil.NewMockClock(time.Date(2024, 1, 1, 0, 0, 0, 0, time.UTC))

	recon := reconnect.New()
	recon.GoLive()

	w := coordinator.NewWorker(
		"kucoin", symbol.Symbol("BTCUSDT"), deltas,
		&fakeStream{}, &fakeILP{}, snaps,
		orderbook.New(), recon, clk, nil,
	).WithPub(pub)

	return w, deltas, snaps
}

func sendAndWait(t *testing.T, deltas chan exchange.Tick, tick exchange.Tick, pub *fakeOBPublisher, wantCalls int) {
	t.Helper()
	before := pub.CallCount()
	deltas <- tick
	deadline := time.Now().Add(200 * time.Millisecond)
	for time.Now().Before(deadline) {
		if pub.CallCount() >= before+wantCalls {
			return
		}
		time.Sleep(2 * time.Millisecond)
	}
	// only assert if we expected calls
	if wantCalls > 0 {
		t.Errorf("OBPublisher not called within deadline: got %d calls, want >= %d", pub.CallCount(), before+wantCalls)
	}
}

func TestWorker_PubCalled_OnEventTypeUpdate(t *testing.T) {
	pub := &fakeOBPublisher{}
	w, deltas, _ := newLiveWorkerL1(pub)

	go w.Run(t.Context())

	tick := exchange.Tick{
		Exchange: "kucoin", Symbol: symbol.Symbol("BTCUSDT"),
		Seq: 1, TsLocal: 1_000, Side: "bid", Price: "29500", Size: "1.0",
		Type: exchange.EventTypeUpdate,
	}
	sendAndWait(t, deltas, tick, pub, 1)
	require.Equal(t, 1, pub.CallCount())
	last, ok := pub.LastTick()
	require.True(t, ok)
	assert.Equal(t, exchange.EventTypeUpdate, last.Type)
}

func TestWorker_PubNotCalled_OnEventTypeTrade(t *testing.T) {
	pub := &fakeOBPublisher{}
	w, deltas, _ := newLiveWorkerL1(pub)

	go w.Run(t.Context())

	tick := exchange.Tick{
		Exchange: "kucoin", Symbol: symbol.Symbol("BTCUSDT"),
		Seq: 1, TsLocal: 1_000, Side: "", Price: "29500", Size: "1.0",
		Type: exchange.EventTypeTrade,
	}
	deltas <- tick
	// Give the worker time to process
	time.Sleep(50 * time.Millisecond)
	assert.Equal(t, 0, pub.CallCount())
}

func TestWorker_NilPub_NoPanic(t *testing.T) {
	// Worker with nil pub must not panic on EventTypeUpdate
	w, deltas, _ := newLiveWorkerL1(nil)

	go w.Run(t.Context())

	tick := exchange.Tick{
		Exchange: "kucoin", Symbol: symbol.Symbol("BTCUSDT"),
		Seq: 1, TsLocal: 1_000, Side: "bid", Price: "29500", Size: "1.0",
		Type: exchange.EventTypeUpdate,
	}
	deltas <- tick
	time.Sleep(50 * time.Millisecond)
	// No panic = pass
}
