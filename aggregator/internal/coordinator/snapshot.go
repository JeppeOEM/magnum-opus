package coordinator

import (
	"context"
	"log/slog"
	"sync"

	"github.com/mrqdt/magnum-opus/aggregator/internal/backoff"
)

// runSnapshotDispatcher reads SnapshotRequests from the shared snapReqs channel and
// dispatches each fetch in its own goroutine so multiple symbols are fetched in
// parallel. This eliminates the sequential stall during restart: all N symbols
// request snapshots nearly simultaneously and all N REST calls happen concurrently,
// cutting the post-restart gap from N×RTT to 1×RTT.
//
// Goroutine count is bounded by the number of active symbols (typically 2–10).
func (c *Coordinator) runSnapshotDispatcher(ctx context.Context) {
	var wg sync.WaitGroup
	for {
		select {
		case <-ctx.Done():
			wg.Wait()
			return
		case req := <-c.snapReqs:
			wg.Add(1)
			go func(r SnapshotRequest) {
				defer wg.Done()
				result, err := c.fetchWithRetry(ctx, r)
				if err != nil {
					// ctx cancelled during fetch — exit cleanly
					return
				}
				select {
				case r.ResultCh <- result:
				case <-ctx.Done():
				}
			}(req)
		}
	}
}

// fetchWithRetry calls FetchSnapshot with exponential backoff until success or ctx cancellation.
func (c *Coordinator) fetchWithRetry(ctx context.Context, req SnapshotRequest) (SnapshotResult, error) {
	for attempt := 0; ; attempt++ {
		result, err := c.fetcher.FetchSnapshot(ctx, req.Exchange, req.Symbol)
		if err == nil {
			return result, nil
		}
		if ctx.Err() != nil {
			return SnapshotResult{}, ctx.Err()
		}
		slog.Error("coordinator: snapshot fetch failed, retrying",
			"exchange", req.Exchange, "symbol", string(req.Symbol),
			"attempt", attempt+1, "err", err)
		d := backoff.Duration(attempt, c.clock)
		if sleepErr := c.sleepFn(ctx, d); sleepErr != nil {
			return SnapshotResult{}, sleepErr
		}
	}
}
