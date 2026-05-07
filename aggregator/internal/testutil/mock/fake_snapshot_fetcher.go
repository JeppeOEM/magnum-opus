//go:build l2

package mock

import (
	"context"
	"fmt"
	"sync"
	"time"

	"github.com/mrqdt/magnum-opus/aggregator/internal/coordinator"
	"github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
)

// FakeSnapshotFetcher is a configurable snapshot fetcher for L2 tests.
// Results are keyed by "exchange:symbol" string.
type FakeSnapshotFetcher struct {
	mu        sync.Mutex
	results   map[string]coordinator.SnapshotResult
	callCount int
	once      sync.Once
	calledCh  chan struct{}
	// FailFirst, if > 0, causes FetchSnapshot to return an error for the first N calls.
	FailFirst int
}

// NewFakeSnapshotFetcher creates a fake fetcher with the given per-(exchange,symbol) results.
// Key format: "exchange:symbol", e.g. "kucoin:BTC-USDT".
func NewFakeSnapshotFetcher(results map[string]coordinator.SnapshotResult) *FakeSnapshotFetcher {
	return &FakeSnapshotFetcher{
		results:  results,
		calledCh: make(chan struct{}),
	}
}

// FetchSnapshot returns the configured result for the given (exchange, symbol) pair.
// Errors for the first FailFirst calls (if set), then succeeds.
func (f *FakeSnapshotFetcher) FetchSnapshot(_ context.Context, exch string, sym symbol.Symbol) (coordinator.SnapshotResult, error) {
	f.mu.Lock()
	f.callCount++
	count := f.callCount
	fail := f.FailFirst
	f.mu.Unlock()

	f.once.Do(func() { close(f.calledCh) })

	if fail > 0 && count <= fail {
		return coordinator.SnapshotResult{}, fmt.Errorf("fake: injected failure (call %d of %d)", count, fail)
	}

	f.mu.Lock()
	result, ok := f.results[exch+":"+string(sym)]
	f.mu.Unlock()
	if !ok {
		return coordinator.SnapshotResult{}, fmt.Errorf("fake: no result configured for %s:%s", exch, sym)
	}
	return result, nil
}

// CallCount returns the total number of FetchSnapshot calls received.
func (f *FakeSnapshotFetcher) CallCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.callCount
}

// WaitCalled blocks until FetchSnapshot has been called at least once or timeout elapses.
// Returns true if called within the timeout.
func (f *FakeSnapshotFetcher) WaitCalled(timeout time.Duration) bool {
	select {
	case <-f.calledCh:
		return true
	case <-time.After(timeout):
		return false
	}
}
