// Package redis implements coordinator.StreamWriter by publishing to Redis Streams.
// Interface ownership: coordinator.StreamWriter is defined in coordinator/interfaces.go — not here.
package redis

import (
	"context"
	"fmt"
	"maps"
	"strconv"
	"time"

	goredis "github.com/redis/go-redis/v9"

	"github.com/mrqdt/magnum-opus/aggregator/internal/backoff"
	"github.com/mrqdt/magnum-opus/aggregator/internal/coordinator"
	"github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
	"github.com/mrqdt/magnum-opus/aggregator/internal/gapdetector"
	"github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
)

// compile-time check: Writer satisfies coordinator.StreamWriter.
var _ coordinator.StreamWriter = (*Writer)(nil)

const maxStreamLen = 50000

// RedisClient is the narrow interface Writer requires.
// FakeRedis in testutil/mock satisfies this via structural typing.
// RealClient wraps *goredis.Client for production.
type RedisClient interface {
	XAdd(ctx context.Context, stream string, fields map[string]any, maxLen int64) (string, error)
}

// RealClient wraps *goredis.Client to satisfy RedisClient.
type RealClient struct {
	c *goredis.Client
}

// NewRealClient wraps a go-redis v9 client.
func NewRealClient(c *goredis.Client) *RealClient { return &RealClient{c: c} }

// XAdd calls go-redis XAdd with approximate MAXLEN trimming.
func (r *RealClient) XAdd(ctx context.Context, stream string, fields map[string]any, maxLen int64) (string, error) {
	cmd := r.c.XAdd(ctx, &goredis.XAddArgs{
		Stream: stream,
		MaxLen: maxLen,
		Approx: true,
		Values: fields,
	})
	return cmd.Result()
}

// Writer publishes normalized tick events and gap markers to Redis Streams.
// It satisfies coordinator.StreamWriter. Configure via New.
type Writer struct {
	client      RedisClient
	clock       backoff.Clock
	maxRetryDur time.Duration
	// sleepFn sleeps for d, returning ctx.Err() if the context is cancelled first.
	// Defaults to sleepWithContext; override in tests to skip real delays.
	sleepFn func(ctx context.Context, d time.Duration) error
}

// New returns a Writer. maxRetryDur caps total retry time per write (10*time.Second in production).
func New(client RedisClient, clock backoff.Clock, maxRetryDur time.Duration) *Writer {
	return &Writer{client: client, clock: clock, maxRetryDur: maxRetryDur, sleepFn: sleepWithContext}
}

// WithSleep overrides the sleep function. Use only in tests to avoid real delays.
func (w *Writer) WithSleep(fn func(ctx context.Context, d time.Duration) error) *Writer {
	w.sleepFn = fn
	return w
}

// sleepWithContext sleeps for d, returning ctx.Err() if the context is cancelled.
// Using a timer+select ensures ctx cancellation interrupts the sleep immediately.
func sleepWithContext(ctx context.Context, d time.Duration) error {
	if d <= 0 {
		return nil
	}
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

// Write publishes a normalized tick event to ticks:{exchange}:{symbol}.
// On persistent failure it emits a best-effort gap marker and returns the error.
func (w *Writer) Write(ctx context.Context, tick exchange.Tick) error {
	stream := fmt.Sprintf("ticks:%s:%s", tick.Exchange, tick.Symbol.String())
	fields := map[string]any{
		"type":        "tick",
		"exchange":    tick.Exchange,
		"symbol":      tick.Symbol.String(),
		"seq":         strconv.FormatUint(tick.Seq, 10),
		"ts_exchange": strconv.FormatInt(tick.TsExchange/1_000_000, 10),
		"ts_local":    strconv.FormatInt(tick.TsLocal/1_000_000, 10),
		"side":        tick.Side,
		"price":       tick.Price,
		"size":        tick.Size,
		"event_type":  tick.Type.String(),
	}
	if err := w.xaddWithRetry(ctx, stream, fields); err != nil {
		// Emit a best-effort gap marker so the Candle Service detects the hole.
		_ = w.WriteGap(ctx, tick.Exchange, tick.Symbol, gapdetector.GapEvent{
			Cause:     gapdetector.CauseExternalDisconnect,
			SeqBefore: tick.Seq,
			SeqAfter:  tick.Seq,
			Timestamp: w.clock.Now(),
		})
		return err
	}
	return nil
}

// WriteSnapshot publishes a snapshot signal to ticks:{exchange}:{symbol}.
// Best-effort: no retry, no gap fallback. A failure delays cold-start exit on the candle
// service but does not cause data loss — the next reconnect will trigger another snapshot.
func (w *Writer) WriteSnapshot(ctx context.Context, exch string, sym symbol.Symbol, seq uint64, tsMs int64) error {
	stream := fmt.Sprintf("ticks:%s:%s", exch, sym.String())
	fields := map[string]any{
		"type":     "snapshot",
		"exchange": exch,
		"symbol":   sym.String(),
		"seq":      strconv.FormatUint(seq, 10),
		"ts":       strconv.FormatInt(tsMs, 10),
	}
	_, err := w.client.XAdd(ctx, stream, fields, maxStreamLen)
	return err
}

// WriteGap publishes an in-band gap marker to ticks:{exchange}:{symbol} and to gaps:log.
func (w *Writer) WriteGap(ctx context.Context, exch string, sym symbol.Symbol, gap gapdetector.GapEvent) error {
	inband := map[string]any{
		"type":       "gap",
		"exchange":   exch,
		"symbol":     sym.String(),
		"gap_ts":     strconv.FormatInt(gap.Timestamp.UnixMilli(), 10),
		"gap_cause":  string(gap.Cause),
		"seq_before": strconv.FormatUint(gap.SeqBefore, 10),
		"seq_after":  strconv.FormatUint(gap.SeqAfter, 10),
	}
	tickStream := fmt.Sprintf("ticks:%s:%s", exch, sym.String())
	if err := w.xaddWithRetry(ctx, tickStream, inband); err != nil {
		return err
	}

	gapsLog := make(map[string]any, len(inband)+1)
	maps.Copy(gapsLog, inband)
	gapsLog["seq_gap"] = strconv.FormatInt(int64(gap.SeqAfter)-int64(gap.SeqBefore)-1, 10)
	return w.xaddWithRetry(ctx, "gaps:log", gapsLog)
}

// xaddWithRetry attempts XAdd with exponential backoff until maxRetryDur elapses or ctx is cancelled.
func (w *Writer) xaddWithRetry(ctx context.Context, stream string, fields map[string]any) error {
	deadline := w.clock.Now().Add(w.maxRetryDur)
	for attempt := 0; ; attempt++ {
		_, err := w.client.XAdd(ctx, stream, fields, maxStreamLen)
		if err == nil {
			return nil
		}
		if !w.clock.Now().Before(deadline) {
			return err
		}
		if sleepErr := w.sleepFn(ctx, backoff.Duration(attempt, w.clock)); sleepErr != nil {
			return sleepErr
		}
	}
}
