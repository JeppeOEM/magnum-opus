// Package questdb implements coordinator.ILPWriter by batching raw tick writes
// to QuestDB via the ILP TCP protocol (port 9009).
// Interface ownership: coordinator.ILPWriter is defined in coordinator/interfaces.go — not here.
package questdb

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"strconv"
	"time"

	qdb "github.com/questdb/go-questdb-client/v3"

	"github.com/mrqdt/magnum-opus/aggregator/internal/backoff"
	"github.com/mrqdt/magnum-opus/aggregator/internal/coordinator"
	"github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
	"github.com/mrqdt/magnum-opus/aggregator/internal/gapdetector"
	"github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
)

// compile-time check: Writer satisfies coordinator.ILPWriter.
var _ coordinator.ILPWriter = (*Writer)(nil)

// ILPSender abstracts the QuestDB ILP TCP connection.
// FakeQuestDB (testutil/mock) and RealSender both satisfy this via structural typing.
type ILPSender interface {
	Write(ctx context.Context, table string, fields map[string]any) error
	Flush(ctx context.Context) error
	Close(ctx context.Context) error
}

// WALChecker polls for and resumes QuestDB WAL suspension.
// FakeQuestDB satisfies this via structural typing.
type WALChecker interface {
	IsWALSuspended(ctx context.Context) (bool, error)
	ResumeWAL(ctx context.Context) error
}

// FlushAuditor publishes flush audit events to Redis.
// FakeRedis and RealClient (writer/redis) both satisfy this via structural typing.
type FlushAuditor interface {
	XAdd(ctx context.Context, stream string, fields map[string]any, maxLen int64) (string, error)
}

const (
	rawTicksTable    = "raw_ticks"
	flushAuditStream = "questdb:flush:pending"
	flushAuditMaxLen = int64(10000)
)

type rowMsg struct {
	fields map[string]any
}

// Writer batches tick events for QuestDB ILP writes in configurable flush windows.
// A single goroutine owns the ILP sender — not goroutine-safe at the sender level.
type Writer struct {
	sender           ILPSender
	wal              WALChecker
	auditor          FlushAuditor
	clock            backoff.Clock
	flushInterval    time.Duration
	walInterval      time.Duration
	maxFlushRetryDur time.Duration
	sleepFn          func(ctx context.Context, d time.Duration) error
	// onWALSuspended is called each time WAL suspension is detected (AC3).
	// Wire a Prometheus gauge increment here in Epic 4. Set via WithWALSuspendedMetric before Start().
	onWALSuspended func()
	rows           chan rowMsg
	stop           chan struct{} // closed by Close() to signal the sender goroutine
	done           chan struct{} // closed by sender goroutine when it exits
	// pendingCount tracks rows accepted by sender since last successful flush — sender goroutine only, no mutex.
	pendingCount int
}

// New returns a configured Writer. Call Start() to begin the sender goroutine after all configuration.
// Production values: flushInterval=500ms, walInterval=30s, maxFlushRetryDur=30s.
func New(
	sender ILPSender,
	wal WALChecker,
	auditor FlushAuditor,
	clock backoff.Clock,
	flushInterval, walInterval, maxFlushRetryDur time.Duration,
) *Writer {
	return &Writer{
		sender:           sender,
		wal:              wal,
		auditor:          auditor,
		clock:            clock,
		flushInterval:    flushInterval,
		walInterval:      walInterval,
		maxFlushRetryDur: maxFlushRetryDur,
		sleepFn:          sleepWithContext,
		rows:             make(chan rowMsg, 1000),
		stop:             make(chan struct{}),
		done:             make(chan struct{}),
	}
}

// Start begins the sender goroutine. Must be called after all configuration (WithSleep, WithWALSuspendedMetric).
// Returns w for chaining.
func (w *Writer) Start() *Writer {
	go w.run()
	return w
}

// WithSleep overrides the sleep function. Use only in tests to avoid real delays. Call before Start().
func (w *Writer) WithSleep(fn func(ctx context.Context, d time.Duration) error) *Writer {
	w.sleepFn = fn
	return w
}

// WithWALSuspendedMetric sets a callback invoked each time WAL suspension is detected (AC3).
// Wire a Prometheus gauge increment here in Epic 4. Call before Start().
func (w *Writer) WithWALSuspendedMetric(fn func()) *Writer {
	w.onWALSuspended = fn
	return w
}

// Write buffers a tick for the next flush cycle. Does not block on QuestDB acknowledgement.
func (w *Writer) Write(ctx context.Context, tick exchange.Tick) error {
	fields := map[string]any{
		"exchange":    tick.Exchange,
		"symbol":      tick.Symbol.String(),
		"seq":         int64(tick.Seq),
		"ts_exchange": tick.TsExchange,
		"ts_local":    tick.TsLocal,
		"side":        tick.Side,
		"price":       tick.Price,
		"size":        tick.Size,
		"event_type":  tick.Type.String(),
		"is_gap":      false,
		"gap_cause":   "",
	}
	select {
	case w.rows <- rowMsg{fields: fields}:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

// WriteGap buffers a gap marker row for the next flush cycle.
func (w *Writer) WriteGap(ctx context.Context, exch string, sym symbol.Symbol, gap gapdetector.GapEvent) error {
	fields := map[string]any{
		"exchange":    exch,
		"symbol":      sym.String(),
		"seq":         int64(gap.SeqAfter),
		"ts_exchange": gap.Timestamp.UnixNano(),
		"ts_local":    gap.Timestamp.UnixNano(),
		"side":        "",
		"price":       "0",
		"size":        "0",
		"event_type":  "gap",
		"is_gap":      true,
		"gap_cause":   string(gap.Cause),
	}
	select {
	case w.rows <- rowMsg{fields: fields}:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

// Close signals the sender goroutine to drain and flush, then waits for it to finish.
func (w *Writer) Close(ctx context.Context) error {
	close(w.stop)
	select {
	case <-w.done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (w *Writer) run() {
	defer close(w.done)
	flushTicker := time.NewTicker(w.flushInterval)
	walTicker := time.NewTicker(w.walInterval)
	defer flushTicker.Stop()
	defer walTicker.Stop()

	for {
		select {
		case <-w.stop:
			w.drainAndClose()
			return
		case msg := <-w.rows:
			if err := w.sender.Write(context.Background(), rawTicksTable, msg.fields); err != nil {
				slog.Error("questdb: ILP write failed", "err", err)
			} else {
				w.pendingCount++
			}
		case <-flushTicker.C:
			if w.pendingCount > 0 {
				w.doFlush(context.Background())
			}
		case <-walTicker.C:
			w.checkWAL(context.Background())
		}
	}
}

func (w *Writer) drainAndClose() {
	for {
		select {
		case msg := <-w.rows:
			if err := w.sender.Write(context.Background(), rawTicksTable, msg.fields); err != nil {
				slog.Error("questdb: ILP write failed during drain", "err", err)
			} else {
				w.pendingCount++
			}
		default:
			if w.pendingCount > 0 {
				w.doFlush(context.Background())
			}
			_ = w.sender.Close(context.Background())
			return
		}
	}
}

func (w *Writer) doFlush(ctx context.Context) {
	count := w.pendingCount
	// pendingCount is NOT reset here — only reset after a successful Flush so audit row_count stays accurate.

	_, _ = w.auditor.XAdd(ctx, flushAuditStream, map[string]any{
		"type":      "flush-begin",
		"row_count": strconv.Itoa(count),
		"ts":        strconv.FormatInt(w.clock.Now().UnixMilli(), 10),
	}, flushAuditMaxLen)

	deadline := w.clock.Now().Add(w.maxFlushRetryDur)
	for attempt := 0; ; attempt++ {
		err := w.sender.Flush(ctx)
		if err == nil {
			w.pendingCount = 0 // reset only on success; dangling flush-begin remains if exhausted
			break
		}
		if !w.clock.Now().Before(deadline) {
			slog.Error("questdb: flush retry exhausted", "attempt", attempt, "rows", count, "err", err)
			return
		}
		w.checkWAL(ctx)
		_ = w.sleepFn(ctx, backoff.Duration(attempt, w.clock))
	}

	_, _ = w.auditor.XAdd(ctx, flushAuditStream, map[string]any{
		"type": "flush-complete",
		"ts":   strconv.FormatInt(w.clock.Now().UnixMilli(), 10),
	}, flushAuditMaxLen)
}

func (w *Writer) checkWAL(ctx context.Context) {
	suspended, err := w.wal.IsWALSuspended(ctx)
	if err != nil {
		slog.Warn("questdb: WAL check failed", "err", err)
		return
	}
	if !suspended {
		return
	}
	// AC3: fire metric callback so Epic 4 can increment a Prometheus gauge.
	if w.onWALSuspended != nil {
		w.onWALSuspended()
	}
	slog.Warn("questdb: WAL suspended, issuing RESUME WAL")
	if err := w.wal.ResumeWAL(ctx); err != nil {
		slog.Warn("questdb: RESUME WAL failed", "err", err)
		return
	}
	slog.Warn("questdb: WAL resumed successfully")
}

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

// RealSender wraps qdb.LineSender to satisfy ILPSender.
// The ILP sender is NOT goroutine-safe — only the Writer's sender goroutine calls it.
type RealSender struct {
	sender qdb.LineSender
}

// NewRealSender creates a TCP ILP sender connected to the given address (e.g. "localhost:9009").
// Auto-flush is disabled — the Writer controls flush timing.
func NewRealSender(ctx context.Context, addr string) (*RealSender, error) {
	s, err := qdb.NewLineSender(ctx,
		qdb.WithTcp(),
		qdb.WithAddress(addr),
		qdb.WithAutoFlushDisabled(),
	)
	if err != nil {
		return nil, err
	}
	return &RealSender{sender: s}, nil
}

// Write builds and buffers one raw_ticks ILP row.
func (r *RealSender) Write(ctx context.Context, _ string, fields map[string]any) error {
	s := r.sender.Table(rawTicksTable)
	for _, col := range []string{"exchange", "symbol", "side", "event_type"} {
		if v, ok := fields[col].(string); ok && v != "" {
			s = s.Symbol(col, v)
		}
	}
	if v, ok := fields["gap_cause"].(string); ok && v != "" {
		s = s.Symbol("gap_cause", v)
	}
	if seq, ok := fields["seq"].(int64); ok {
		s = s.Int64Column("seq", seq)
	}
	if tsNano, ok := fields["ts_local"].(int64); ok {
		s = s.TimestampColumn("ts_local", time.Unix(0, tsNano).UTC())
	}
	for _, col := range []string{"price", "size"} {
		if v, ok := fields[col].(string); ok {
			s = s.StringColumn(col, v)
		}
	}
	if isGap, ok := fields["is_gap"].(bool); ok {
		s = s.BoolColumn("is_gap", isGap)
	}
	tsNano, _ := fields["ts_exchange"].(int64)
	return s.At(ctx, time.Unix(0, tsNano).UTC())
}

// Flush sends all buffered rows to QuestDB.
func (r *RealSender) Flush(ctx context.Context) error { return r.sender.Flush(ctx) }

// Close flushes and releases the ILP connection.
func (r *RealSender) Close(ctx context.Context) error { return r.sender.Close(ctx) }

// RealWALChecker implements WALChecker via QuestDB HTTP REST API (port 9000).
type RealWALChecker struct {
	httpAddr string
	client   *http.Client
}

// NewRealWALChecker returns a WALChecker for the given QuestDB HTTP address (e.g. "localhost:9000").
func NewRealWALChecker(httpAddr string) *RealWALChecker {
	return &RealWALChecker{httpAddr: httpAddr, client: &http.Client{Timeout: 5 * time.Second}}
}

// IsWALSuspended queries wal_tables() and returns true if raw_ticks is suspended.
func (r *RealWALChecker) IsWALSuspended(ctx context.Context) (bool, error) {
	// WHERE suspended = true filters to only suspended tables; count > 0 means raw_ticks is suspended.
	query := "SELECT name FROM wal_tables() WHERE name = 'raw_ticks' AND suspended = true"
	u := fmt.Sprintf("http://%s/exec?query=%s", r.httpAddr, url.QueryEscape(query))
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return false, err
	}
	resp, err := r.client.Do(req)
	if err != nil {
		return false, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return false, err
	}
	var result struct {
		Count int `json:"count"`
	}
	if err := json.Unmarshal(body, &result); err != nil {
		return false, fmt.Errorf("questdb wal_tables parse: %w", err)
	}
	return result.Count > 0, nil
}

// ResumeWAL issues ALTER TABLE raw_ticks RESUME WAL via HTTP.
func (r *RealWALChecker) ResumeWAL(ctx context.Context) error {
	u := fmt.Sprintf("http://%s/exec?query=%s", r.httpAddr,
		url.QueryEscape("ALTER TABLE raw_ticks RESUME WAL"))
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return err
	}
	resp, err := r.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("questdb RESUME WAL: HTTP %d", resp.StatusCode)
	}
	return nil
}

// ApplySchema applies the raw_ticks CREATE TABLE DDL via QuestDB HTTP REST.
// questdbHTTPAddr e.g. "localhost:9000". Idempotent (IF NOT EXISTS).
// Called once on service startup; not used in L2 tests.
func ApplySchema(ctx context.Context, questdbHTTPAddr string) error {
	ddl := `CREATE TABLE IF NOT EXISTS raw_ticks (
		exchange    SYMBOL,
		symbol      SYMBOL,
		seq         LONG,
		ts_exchange TIMESTAMP,
		ts_local    TIMESTAMP,
		side        SYMBOL,
		price       STRING,
		size        STRING,
		event_type  SYMBOL,
		is_gap      BOOLEAN,
		gap_cause   SYMBOL
	) TIMESTAMP(ts_exchange) PARTITION BY DAY WAL`
	u := fmt.Sprintf("http://%s/exec?query=%s", questdbHTTPAddr, url.QueryEscape(ddl))
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return err
	}
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("questdb ApplySchema: HTTP %d", resp.StatusCode)
	}
	return nil
}
