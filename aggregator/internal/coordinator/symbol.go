package coordinator

import (
	"context"
	"log/slog"
	"runtime/debug"
	"time"

	"github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
	"github.com/mrqdt/magnum-opus/aggregator/internal/gapdetector"
	"github.com/mrqdt/magnum-opus/aggregator/internal/metrics"
	"github.com/mrqdt/magnum-opus/aggregator/internal/orderbook"
	"github.com/mrqdt/magnum-opus/aggregator/internal/reconnect"
	"github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
)

// SnapshotResult carries a completed REST snapshot from the dispatcher (story 3.5).
type SnapshotResult struct {
	Seq  uint64
	Bids map[string]string
	Asks map[string]string
}

// SnapshotRequest is sent to the snapshot dispatcher when a NeedsSnapshot signal fires.
// The dispatcher fetches the REST snapshot and sends the result to ResultCh.
type SnapshotRequest struct {
	Exchange string
	Symbol   symbol.Symbol
	Signal   *reconnect.NeedsSnapshot
	// ResultCh is a per-worker buffered channel (cap=1). The dispatcher must send exactly
	// one result; sending twice blocks the dispatcher goroutine permanently.
	ResultCh chan SnapshotResult
}

// Worker is a per-symbol goroutine that wires the order book, gap detector,
// reconnect state machine, and writers into a complete processing pipeline.
// Not safe for concurrent use — it owns a single goroutine.
type Worker struct {
	exch         string
	sym          symbol.Symbol
	deltas       <-chan exchange.Tick
	stream       StreamWriter
	ilp          ILPWriter
	snapRequests chan<- SnapshotRequest
	resultCh     chan SnapshotResult
	book         *orderbook.OrderBook
	recon        *reconnect.Machine
	clock        gapdetector.Clock
	lastSeq      uint64 // per-delta seq (fallback gap detection for exchanges without msg-level seq)
	lastMsgSeqEnd uint64 // message-level sequenceEnd (KuCoin-style gap detection)
	metrics      *metrics.Registry // nil disables metric emission (safe for tests)
}

// NewWorker constructs a Worker. Call Run(ctx) in a goroutine to start processing.
// Pass met=nil to disable metric emission (safe for existing L2 tests).
func NewWorker(
	exch string,
	sym symbol.Symbol,
	deltas <-chan exchange.Tick,
	stream StreamWriter,
	ilp ILPWriter,
	snapRequests chan<- SnapshotRequest,
	book *orderbook.OrderBook,
	recon *reconnect.Machine,
	clock gapdetector.Clock,
	met *metrics.Registry,
) *Worker {
	return &Worker{
		exch:         exch,
		sym:          sym,
		deltas:       deltas,
		stream:       stream,
		ilp:          ilp,
		snapRequests: snapRequests,
		resultCh:     make(chan SnapshotResult, 1),
		book:         book,
		recon:        recon,
		clock:        clock,
		metrics:      met,
	}
}

// Run starts the processing loop with panic recovery at this level only (AC4).
// Panics inside loop() or helpers are caught here, logged, and cause the loop to re-enter.
func (w *Worker) Run(ctx context.Context) {
	for {
		panicked := false
		func() {
			defer func() {
				if r := recover(); r != nil {
					panicked = true
					slog.Error("coordinator: panic in symbol goroutine",
						"exchange", w.exch, "symbol", string(w.sym),
						"panic", r, "stack", string(debug.Stack()))
					// SeqAfter = lastSeq+1 yields seq_gap=0 (unknown gap, not negative)
					gap := gapdetector.GapEvent{
						Cause:     gapdetector.CauseInternalMergeError,
						SeqBefore: w.lastSeq,
						SeqAfter:  w.lastSeq + 1,
						Timestamp: w.clock.Now(),
					}
					_ = w.stream.WriteGap(ctx, w.exch, w.sym, gap)
					_ = w.ilp.WriteGap(ctx, w.exch, w.sym, gap)
					if w.metrics != nil {
						w.metrics.GapTotal.WithLabelValues(w.exch, string(w.sym), string(gapdetector.CauseInternalMergeError)).Inc()
						w.metrics.RecordGap(w.exch, string(w.sym), w.clock.Now().Unix())
					}
					// Reset so the first live tick after recovery doesn't trigger a spurious
					// external-disconnect gap against the stale pre-panic lastSeq.
					w.lastSeq = 0
					w.lastMsgSeqEnd = 0
					// Drain any stale snapshot result that arrived while the panic was in flight.
					select {
					case <-w.resultCh:
					default:
					}
					ns := w.recon.NeedsSnapshotNow("panic")
					_ = w.requestSnapshot(ctx, ns)
				}
			}()
			w.loop(ctx)
		}()
		// Re-enter loop only after a panic recovery (and only if ctx is still live)
		if !panicked || ctx.Err() != nil {
			return
		}
	}
}

func (w *Worker) loop(ctx context.Context) {
	if w.recon.State() == reconnect.StateInitial {
		ns := w.recon.NeedsSnapshotNow("initial")
		if err := w.requestSnapshot(ctx, ns); err != nil {
			return
		}
	}
	for {
		select {
		case <-ctx.Done():
			// Best-effort gap marker when shutdown fires while a snapshot fetch is in-flight.
			// Uses a background context (root ctx is already cancelled).
			if w.recon.State() == reconnect.StateBuffering {
				shutCtx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
				gap := gapdetector.GapEvent{
					Cause:     gapdetector.CauseExternalDisconnect,
					SeqBefore: w.lastSeq,
					SeqAfter:  w.lastSeq + 1,
					Timestamp: w.clock.Now(),
				}
				_ = w.stream.WriteGap(shutCtx, w.exch, w.sym, gap)
				_ = w.ilp.WriteGap(shutCtx, w.exch, w.sym, gap)
				cancel()
			}
			return
		case tick := <-w.deltas:
			w.handleTick(ctx, tick)
		case result := <-w.resultCh:
			w.handleSnapshot(ctx, result)
		}
	}
}

func (w *Worker) handleTick(ctx context.Context, tick exchange.Tick) {
	// While buffering, only feed seq numbers — no IO yet (AC1)
	if w.recon.State() == reconnect.StateBuffering {
		w.recon.Feed(tick.Seq)
		return
	}

	// Gap detection in Live state (AC1).
	// If the exchange provides message-level sequence numbers (MsgSeqEnd != 0), use those —
	// per-level sequences within one message are non-consecutive by design (KuCoin assigns
	// a global sequence to every internal event; only a subset appear in L2 changes).
	// MsgSeqStart is only set on the first delta of each message, so the check fires once
	// per message rather than once per delta.
	if tick.MsgSeqEnd != 0 {
		if tick.MsgSeqStart > 0 && w.lastMsgSeqEnd > 0 {
			if gap := gapdetector.Detect(w.lastMsgSeqEnd, tick.MsgSeqStart, gapdetector.CauseExternalDisconnect, w.clock); gap != nil {
				slog.Warn("coordinator: gap detected", "exchange", w.exch, "symbol", string(w.sym),
					"cause", gap.Cause, "seq_before", gap.SeqBefore, "seq_after", gap.SeqAfter)
				if err := w.stream.WriteGap(ctx, w.exch, w.sym, *gap); err != nil && ctx.Err() == nil {
					slog.Error("coordinator: WriteGap (stream) failed", "err", err)
				}
				if err := w.ilp.WriteGap(ctx, w.exch, w.sym, *gap); err != nil && ctx.Err() == nil {
					slog.Error("coordinator: WriteGap (ilp) failed", "err", err)
				}
				if w.metrics != nil {
					w.metrics.GapTotal.WithLabelValues(w.exch, string(w.sym), string(gap.Cause)).Inc()
					w.metrics.RecordGap(w.exch, string(w.sym), w.clock.Now().Unix())
				}
			}
		}
		w.lastMsgSeqEnd = tick.MsgSeqEnd
	} else {
		// Fallback: per-delta gap detection for exchanges without message-level seq (e.g. Bybit).
		if w.lastSeq > 0 {
			if gap := gapdetector.Detect(w.lastSeq, tick.Seq, gapdetector.CauseExternalDisconnect, w.clock); gap != nil {
				slog.Warn("coordinator: gap detected", "exchange", w.exch, "symbol", string(w.sym),
					"cause", gap.Cause, "seq_before", gap.SeqBefore, "seq_after", gap.SeqAfter)
				if err := w.stream.WriteGap(ctx, w.exch, w.sym, *gap); err != nil && ctx.Err() == nil {
					slog.Error("coordinator: WriteGap (stream) failed", "err", err)
				}
				if err := w.ilp.WriteGap(ctx, w.exch, w.sym, *gap); err != nil && ctx.Err() == nil {
					slog.Error("coordinator: WriteGap (ilp) failed", "err", err)
				}
				if w.metrics != nil {
					w.metrics.GapTotal.WithLabelValues(w.exch, string(w.sym), string(gap.Cause)).Inc()
					w.metrics.RecordGap(w.exch, string(w.sym), w.clock.Now().Unix())
				}
			}
		}
		w.lastSeq = tick.Seq
	}

	// Apply update events to the order book (pure, no IO) (AC1)
	if tick.Type == exchange.EventTypeUpdate {
		w.book.Apply(orderbook.Delta{
			Seq:        tick.Seq,
			Side:       parseSide(tick.Side),
			Price:      tick.Price,
			Size:       tick.Size,
			TsExchange: tick.TsExchange,
		})
	}

	// Count tick as processed regardless of write outcome — metric tracks pipeline throughput,
	// not Redis acknowledgement. QuestDB always receives the tick even if Redis is down.
	if w.metrics != nil {
		w.metrics.TicksTotal.WithLabelValues(w.exch, string(w.sym)).Inc()
	}
	// Write to Redis — ctx propagates; error logged, not propagated (AC2, NFR9)
	if err := w.stream.Write(ctx, tick); err != nil && ctx.Err() == nil {
		slog.Error("coordinator: stream.Write failed", "err", err)
	}
	// Write to QuestDB — buffered, non-blocking; error logged, not propagated (AC2, NFR9)
	if err := w.ilp.Write(ctx, tick); err != nil && ctx.Err() == nil {
		slog.Error("coordinator: ilp.Write failed", "err", err)
	}
}

func (w *Worker) handleSnapshot(ctx context.Context, result SnapshotResult) {
	// Guard: only process snapshots when actually waiting for one.
	// A stale result arriving in StateLive would silently corrupt the live book.
	if w.recon.State() != reconnect.StateBuffering {
		slog.Warn("coordinator: unexpected snapshot result discarded",
			"exchange", w.exch, "symbol", string(w.sym))
		return
	}
	w.book.ApplySnapshot(result.Seq, result.Bids, result.Asks)
	replay, ns := w.recon.MergeSnapshot(result.Seq)
	if ns != nil {
		// Stale snapshot (merge_error) — emit audit marker then request a new one.
		slog.Warn("coordinator: stale snapshot, requesting new",
			"exchange", w.exch, "symbol", string(w.sym))
		gap := gapdetector.GapEvent{
			Cause:     gapdetector.CauseInternalMergeError,
			SeqBefore: w.lastSeq,
			SeqAfter:  w.lastSeq + 1,
			Timestamp: w.clock.Now(),
		}
		_ = w.stream.WriteGap(ctx, w.exch, w.sym, gap)
		_ = w.ilp.WriteGap(ctx, w.exch, w.sym, gap)
		if w.metrics != nil {
			w.metrics.GapTotal.WithLabelValues(w.exch, string(w.sym), string(gapdetector.CauseInternalMergeError)).Inc()
			w.metrics.RecordGap(w.exch, string(w.sym), w.clock.Now().Unix())
		}
		if err := w.requestSnapshot(ctx, ns); err != nil {
			slog.Error("coordinator: stale snapshot re-request failed",
				"err", err, "exchange", w.exch, "symbol", string(w.sym))
		}
		return
	}
	// replay contains seq numbers of buffered deltas newer than the snapshot.
	// Full payloads are not stored — the book is seeded from the REST snapshot and
	// live deltas fill subsequent updates. See Dev Notes: Replay Buffer Limitation.
	_ = replay
	// Anchor sequence trackers at the snapshot so the first live message is gap-checked from here.
	w.lastSeq = result.Seq
	w.lastMsgSeqEnd = result.Seq
	w.recon.GoLive()
	// Signal the candle service to exit cold-start and replay its buffered deltas.
	if err := w.stream.WriteSnapshot(ctx, w.exch, w.sym, result.Seq, w.clock.Now().UnixMilli()); err != nil && ctx.Err() == nil {
		slog.Error("coordinator: WriteSnapshot failed — candle service stays in cold-start",
			"exchange", w.exch, "symbol", string(w.sym), "err", err)
	}
	if w.metrics != nil {
		w.metrics.FeedState.WithLabelValues(w.exch, string(w.sym)).Set(1)
	}
	slog.Info("coordinator: book live", "exchange", w.exch, "symbol", string(w.sym))
}

func (w *Worker) requestSnapshot(ctx context.Context, ns *reconnect.NeedsSnapshot) error {
	req := SnapshotRequest{
		Exchange: w.exch,
		Symbol:   w.sym,
		Signal:   ns,
		ResultCh: w.resultCh,
	}
	select {
	case w.snapRequests <- req:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func parseSide(s string) orderbook.Side {
	if s == "ask" {
		return orderbook.SideAsk
	}
	return orderbook.SideBid
}
