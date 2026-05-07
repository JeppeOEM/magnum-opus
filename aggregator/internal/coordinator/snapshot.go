package coordinator

import (
	"context"
	"log/slog"

	"github.com/mrqdt/magnum-opus/aggregator/internal/backoff"
)

// runSnapshotDispatcher reads SnapshotRequests from the shared snapReqs channel,
// fetches the REST snapshot via the configured SnapshotFetcher (with retry), and
// delivers the result to the Worker's resultCh. Processes one request at a time —
// serialized dispatching is acceptable at ≤200 symbols scale.
func (c *Coordinator) runSnapshotDispatcher(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		case req := <-c.snapReqs:
			result, err := c.fetchWithRetry(ctx, req)
			if err != nil {
				// ctx cancelled during fetch — exit cleanly
				return
			}
			select {
			case req.ResultCh <- result:
			case <-ctx.Done():
				return
			}
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
