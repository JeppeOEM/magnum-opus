package coordinator

import (
	"context"
	"log/slog"
	"sync"
	"time"

	"github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
	"github.com/mrqdt/magnum-opus/aggregator/internal/gapdetector"
	"github.com/mrqdt/magnum-opus/aggregator/internal/metrics"
	"github.com/mrqdt/magnum-opus/aggregator/internal/orderbook"
	"github.com/mrqdt/magnum-opus/aggregator/internal/reconnect"
	"github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
)

// SnapshotFetcher fetches a REST order book snapshot for the given exchange and symbol.
// Defined in coordinator (the consumer); implementations live in exchange/{kucoin,bybit}/.
type SnapshotFetcher interface {
	FetchSnapshot(ctx context.Context, exch string, sym symbol.Symbol) (SnapshotResult, error)
}

// ExchangeConfig pairs an exchange adapter with its configured symbols.
type ExchangeConfig struct {
	Adapter exchange.Exchange
	Symbols []symbol.Symbol
}

// Coordinator manages the lifecycle of all per-symbol workers and the snapshot dispatcher.
// Create with New, call Run(ctx) in a goroutine, then call Shutdown() after ctx is cancelled.
type Coordinator struct {
	exchanges []ExchangeConfig
	stream    StreamWriter
	ilp       ILPWriter
	fetcher   SnapshotFetcher
	clock     gapdetector.Clock

	entries      []symbolEntry
	snapReqs     chan SnapshotRequest
	wg           sync.WaitGroup
	shutdownOnce sync.Once
	sleepFn      func(ctx context.Context, d time.Duration) error
	metricsReg   *metrics.Registry // nil if WithMetrics was not called
}

type symbolEntry struct {
	exchange string
	sym      symbol.Symbol
	deltas   chan exchange.Tick
	worker   *Worker
}

const deltaChanCap = 256 // per-symbol tick buffer; absorbs bursts without blocking fanout

// New constructs a Coordinator. Creates one Worker and one deltas channel per (exchange, symbol).
// snapReqs channel capacity = total symbols + 16 to avoid backpressure on request send.
func New(
	exchanges []ExchangeConfig,
	stream StreamWriter,
	ilp ILPWriter,
	fetcher SnapshotFetcher,
	clock gapdetector.Clock,
) *Coordinator {
	totalSymbols := 0
	for _, ec := range exchanges {
		totalSymbols += len(ec.Symbols)
	}
	snapReqs := make(chan SnapshotRequest, totalSymbols+16)

	entries := make([]symbolEntry, 0, totalSymbols)
	for _, ec := range exchanges {
		for _, sym := range ec.Symbols {
			deltas := make(chan exchange.Tick, deltaChanCap)
			w := NewWorker(
				ec.Adapter.Name(), sym, deltas,
				stream, ilp, snapReqs,
				orderbook.New(), reconnect.New(), clock,
				nil, // metrics wired via WithMetrics after construction
			)
			entries = append(entries, symbolEntry{
				exchange: ec.Adapter.Name(),
				sym:      sym,
				deltas:   deltas,
				worker:   w,
			})
		}
	}
	return &Coordinator{
		exchanges: exchanges,
		stream:    stream,
		ilp:       ilp,
		fetcher:   fetcher,
		clock:     clock,
		entries:   entries,
		snapReqs:  snapReqs,
		sleepFn:   sleepWithContext,
	}
}

// WithSleep overrides the backoff sleep function. Used in tests to skip real delays.
func (c *Coordinator) WithSleep(fn func(ctx context.Context, d time.Duration) error) *Coordinator {
	c.sleepFn = fn
	return c
}

// WithMetrics enables Prometheus metric emission. Call before Run().
// Pre-initializes all per-symbol label series so they appear in scrapes immediately (NFR19).
func (c *Coordinator) WithMetrics(reg *metrics.Registry) *Coordinator {
	c.metricsReg = reg
	pairs := make([]metrics.SymbolKey, 0, len(c.entries))
	for _, e := range c.entries {
		pairs = append(pairs, metrics.SymbolKey{Exchange: e.exchange, Symbol: string(e.sym)})
	}
	reg.PreInit(pairs)
	for i := range c.entries {
		c.entries[i].worker.metrics = reg
	}
	return c
}

// Run starts the snapshot dispatcher, per-exchange fanout/signal-drain goroutines, and
// per-symbol Worker goroutines. All are tracked in the WaitGroup.
// Returns when ctx is cancelled. Call Shutdown() after Run returns.
func (c *Coordinator) Run(ctx context.Context) {
	c.wg.Add(1)
	go func() {
		defer c.wg.Done()
		c.runSnapshotDispatcher(ctx)
	}()

	for _, ec := range c.exchanges {
		symMap := make(map[symbol.Symbol]chan exchange.Tick, len(ec.Symbols))
		for _, e := range c.entries {
			if e.exchange == ec.Adapter.Name() {
				symMap[e.sym] = e.deltas
			}
		}

		adapter := ec.Adapter

		c.wg.Add(1)
		go func() {
			defer c.wg.Done()
			c.runTickFanout(ctx, adapter, symMap)
		}()

		c.wg.Add(1)
		go func() {
			defer c.wg.Done()
			c.runSignalDrain(ctx, adapter)
		}()

		for _, e := range c.entries {
			if e.exchange != ec.Adapter.Name() {
				continue
			}
			w := e.worker
			c.wg.Add(1)
			go func() {
				defer c.wg.Done()
				w.Run(ctx)
			}()
		}
	}
}

// Shutdown waits for all goroutines to exit, then flushes the QuestDB ILP buffer.
// Must be called after ctx is cancelled. Safe to call multiple times.
func (c *Coordinator) Shutdown() {
	c.shutdownOnce.Do(func() {
		c.wg.Wait()
		shutCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := c.ilp.Close(shutCtx); err != nil {
			slog.Error("coordinator: ILP writer close failed during shutdown", "err", err)
		}
	})
}

// runTickFanout routes ticks from the exchange's shared Ticks() channel to per-symbol delta
// channels. Non-blocking send: drops the tick if the symbol channel is full (backpressure
// isolation — the resulting seq gap triggers requestSnapshot in the Worker).
func (c *Coordinator) runTickFanout(ctx context.Context, exch exchange.Exchange, symMap map[symbol.Symbol]chan exchange.Tick) {
	ticksCh := exch.Ticks()
	for {
		select {
		case <-ctx.Done():
			return
		case tick, ok := <-ticksCh:
			if !ok {
				return
			}
			if ch, found := symMap[tick.Symbol]; found {
				select {
				case ch <- tick:
				default:
					slog.Warn("coordinator: tick dropped — symbol channel full",
						"exchange", exch.Name(), "symbol", string(tick.Symbol), "seq", tick.Seq)
				}
			}
		}
	}
}

// runSignalDrain reads exchange lifecycle signals and logs them.
// Explicit signal routing to Workers is deferred — seq-gap detection in handleTick
// already drives the reconnect flow when ticks resume after a disconnect.
func (c *Coordinator) runSignalDrain(ctx context.Context, exch exchange.Exchange) {
	sigsCh := exch.Signals()
	for {
		select {
		case <-ctx.Done():
			return
		case sig, ok := <-sigsCh:
			if !ok {
				return
			}
			if sig.Type == exchange.SignalNeedsSnapshot {
				slog.Info("coordinator: exchange signalled NeedsSnapshot (gap detection handles reconnect)",
					"exchange", exch.Name(), "symbol", string(sig.Symbol), "reason", sig.Reason)
			}
		}
	}
}

func sleepWithContext(ctx context.Context, d time.Duration) error {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-t.C:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}
