package coordinator

import (
	"context"
	"fmt"
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

// TrackedSymbol describes a currently active feed returned by ListSymbols.
type TrackedSymbol struct {
	Exchange string
	Symbol   string
}

// fanoutAdd is sent on a per-exchange channel to register a new symbol with
// the runTickFanout goroutine. The fanout goroutine is the sole writer of its
// symMap, so this channel-based handoff avoids any lock in the hot tick path.
type fanoutAdd struct {
	sym symbol.Symbol
	ch  chan exchange.Tick
}

// Coordinator manages the lifecycle of all per-symbol workers and the snapshot dispatcher.
// Create with New, call Run(ctx) in a goroutine, then call Shutdown() after ctx is cancelled.
type Coordinator struct {
	exchanges []ExchangeConfig
	stream    StreamWriter
	ilp       ILPWriter
	fetcher   SnapshotFetcher
	clock     gapdetector.Clock

	entriesMu    sync.Mutex
	entries      []symbolEntry
	snapReqs     chan SnapshotRequest
	wg           sync.WaitGroup
	shutdownOnce sync.Once
	sleepFn      func(ctx context.Context, d time.Duration) error
	metricsReg   *metrics.Registry // nil if WithMetrics was not called

	// Dynamic symbol addition — populated by Run().
	exchangeAddChs map[string]chan fanoutAdd // keyed by exchange name, cap 64
	pub            OBPublisher              // stored for Workers created after startup
	runCtx         context.Context          // stored so AddSymbol can launch goroutines
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

// WithOBPublisher sets an OBPublisher on every worker. Call before Run().
// Workers with a non-nil publisher emit a full L2 book snapshot to Redis pub/sub
// after every EventTypeUpdate tick.
// Also stored so Workers created dynamically via AddSymbol receive the publisher.
func (c *Coordinator) WithOBPublisher(pub OBPublisher) *Coordinator {
	c.pub = pub
	for i := range c.entries {
		c.entries[i].worker.WithPub(pub)
	}
	return c
}

// Run starts the snapshot dispatcher, per-exchange fanout/signal-drain goroutines, and
// per-symbol Worker goroutines. All are tracked in the WaitGroup.
// Returns immediately after launching goroutines; call Shutdown() once ctx is cancelled.
// Must be called before AddSymbol.
func (c *Coordinator) Run(ctx context.Context) {
	c.runCtx = ctx
	c.exchangeAddChs = make(map[string]chan fanoutAdd, len(c.exchanges))

	c.wg.Add(1)
	go func() {
		defer c.wg.Done()
		c.runSnapshotDispatcher(ctx)
	}()

	for _, ec := range c.exchanges {
		// Buffered so AddSymbol never blocks the HTTP handler under normal load.
		addCh := make(chan fanoutAdd, 64)
		c.exchangeAddChs[ec.Adapter.Name()] = addCh

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
			c.runTickFanout(ctx, adapter, symMap, addCh)
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
//
// addCh carries fanoutAdd messages from AddSymbol. This goroutine is the sole
// owner of symMap — no lock is needed because all writes go through addCh.
func (c *Coordinator) runTickFanout(ctx context.Context, exch exchange.Exchange, symMap map[symbol.Symbol]chan exchange.Tick, addCh <-chan fanoutAdd) {
	ticksCh := exch.Ticks()
	for {
		select {
		case <-ctx.Done():
			return
		case add := <-addCh:
			symMap[add.sym] = add.ch
			slog.Info("coordinator: fanout registered new symbol",
				"exchange", exch.Name(), "symbol", string(add.sym))
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

// AddSymbol subscribes to a new symbol on a running exchange adapter and wires it
// into the live coordinator pipeline — no restart required.
//
// Ordering guarantees:
//  1. Exchange subscription sent first — if the exchange rejects the symbol (bad name,
//     rate limit) nothing internal changes and the error is returned immediately.
//  2. Worker and delta channel created and registered with fanout goroutine.
//  3. Worker goroutine launched using the coordinator's lifetime context.
//
// The new symbol persists across reconnects because AddSymbols appends to the
// adapter's internal rawSyms list. For persistence across service restarts, callers
// (e.g. main's HTTP handler) must also write to Redis or config.yaml.
func (c *Coordinator) AddSymbol(ctx context.Context, exchName string, rawSym string) error {
	if c.runCtx == nil {
		return fmt.Errorf("coordinator: Run() must be called before AddSymbol")
	}

	// 1. Find the exchange adapter.
	var ec *ExchangeConfig
	for i := range c.exchanges {
		if c.exchanges[i].Adapter.Name() == exchName {
			ec = &c.exchanges[i]
			break
		}
	}
	if ec == nil {
		return fmt.Errorf("coordinator: exchange %q not configured", exchName)
	}

	// 2. Normalize symbol and check for duplicates.
	sym := symbol.Normalize(exchName, rawSym)

	c.entriesMu.Lock()
	for _, e := range c.entries {
		if e.exchange == exchName && e.sym == sym {
			c.entriesMu.Unlock()
			return fmt.Errorf("coordinator: %s already tracked on %s", rawSym, exchName)
		}
	}
	c.entriesMu.Unlock()

	// 3. Subscribe on the exchange adapter first — fail fast before changing state.
	if err := ec.Adapter.AddSymbols(
		[]string{rawSym},
		[]exchange.FeedType{exchange.FeedTypeOrderBook, exchange.FeedTypeTrade},
	); err != nil {
		return fmt.Errorf("coordinator: exchange subscribe failed: %w", err)
	}

	// 4. Build worker and delta channel.
	deltas := make(chan exchange.Tick, deltaChanCap)
	w := NewWorker(
		exchName, sym, deltas,
		c.stream, c.ilp, c.snapReqs,
		orderbook.New(), reconnect.New(), c.clock,
		c.metricsReg,
	)
	if c.pub != nil {
		w.WithPub(c.pub)
	}
	if c.metricsReg != nil {
		c.metricsReg.PreInit([]metrics.SymbolKey{{Exchange: exchName, Symbol: string(sym)}})
	}

	// 5. Append entry (brief critical section — no IO inside lock).
	c.entriesMu.Lock()
	c.entries = append(c.entries, symbolEntry{
		exchange: exchName, sym: sym, deltas: deltas, worker: w,
	})
	c.entriesMu.Unlock()

	// 6. Register with the fanout goroutine via buffered channel.
	// The fanout goroutine is the sole owner of symMap — this handoff is lock-free
	// in the hot tick path. Blocks only if 64 symbols are being added simultaneously.
	addCh, ok := c.exchangeAddChs[exchName]
	if !ok {
		return fmt.Errorf("coordinator: no fanout channel for exchange %q", exchName)
	}
	select {
	case addCh <- fanoutAdd{sym: sym, ch: deltas}:
	case <-ctx.Done():
		return ctx.Err()
	}

	// 7. Start worker goroutine using coordinator lifetime context.
	c.wg.Add(1)
	go func() {
		defer c.wg.Done()
		w.Run(c.runCtx)
	}()

	slog.Info("coordinator: symbol added dynamically",
		"exchange", exchName, "symbol", string(sym))
	return nil
}

// ListSymbols returns all currently tracked (exchange, symbol) pairs.
// Safe to call concurrently with AddSymbol.
func (c *Coordinator) ListSymbols() []TrackedSymbol {
	c.entriesMu.Lock()
	defer c.entriesMu.Unlock()
	out := make([]TrackedSymbol, len(c.entries))
	for i, e := range c.entries {
		out[i] = TrackedSymbol{Exchange: e.exchange, Symbol: string(e.sym)}
	}
	return out
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
