package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/redis/go-redis/v9"

	"github.com/mrqdt/magnum-opus/candle-service/internal/accumulator"
	"github.com/mrqdt/magnum-opus/candle-service/internal/backoff"
	"github.com/mrqdt/magnum-opus/candle-service/internal/blockwindow"
	"github.com/mrqdt/magnum-opus/candle-service/internal/cascade"
	"github.com/mrqdt/magnum-opus/candle-service/internal/config"
	"github.com/mrqdt/magnum-opus/candle-service/internal/consumer"
	"github.com/mrqdt/magnum-opus/candle-service/internal/features"
	"github.com/mrqdt/magnum-opus/candle-service/internal/health"
	"github.com/mrqdt/magnum-opus/candle-service/internal/metrics"
	"github.com/mrqdt/magnum-opus/candle-service/internal/migrator"
	"github.com/mrqdt/magnum-opus/candle-service/internal/orderbook"
	"github.com/mrqdt/magnum-opus/candle-service/internal/flusher"
	questdbwriter "github.com/mrqdt/magnum-opus/candle-service/internal/writer/questdb"
	heatmapwriter "github.com/mrqdt/magnum-opus/candle-service/internal/writer/heatmap"
	pubsubwriter "github.com/mrqdt/magnum-opus/candle-service/internal/writer/pubsub"
	rediswriter "github.com/mrqdt/magnum-opus/candle-service/internal/writer/redis"
)

// Injected at build time via -ldflags.
var (
	version   = "dev"
	gitSHA    = "unknown"
	buildTime = "unknown"
)

// wallClock implements accumulator.Clock using real time.
type wallClock struct{}

func (wallClock) Now() time.Time { return time.Now() }

// accWriter wraps an Accumulator + Writer + OrderBook and implements consumer.AccumulatorApplier
// and consumer.PartialPublisher. All calls come from the same consumer goroutine — not goroutine-safe.
type accWriter struct {
	acc                   *accumulator.Accumulator
	w                     *questdbwriter.Writer
	ob                    *orderbook.OrderBook
	btw                   *blockwindow.Window
	rdb                   *redis.Client
	exchange              string
	symbol                string
	ctx                   context.Context //nolint:containedctx
	openDepthSet          bool
	engine                *cascade.Engine
	cascadeFailureCounter func() // increments CascadeStateWriteFailureTotal{exchange,symbol}
	publisher             *rediswriter.Publisher
	candles1sPub          *pubsubwriter.Publisher // nil = disabled
	heatmapWriter         *heatmapwriter.Writer   // nil = disabled

	// CVD state — maintained across bar boundaries
	cumDelta     float64
	prevCumDelta float64
	prevClose    *float64
}

func (aw *accWriter) Apply(price, size string, isTrade bool, side string, tsMs int64,
	prevBid, prevBidSz, prevAsk, prevAskSz,
	currBid, currBidSz, currAsk, currAskSz string) {
	prev := features.BestQuote{BidPrice: prevBid, BidSize: prevBidSz, AskPrice: prevAsk, AskSize: prevAskSz}
	curr := features.BestQuote{BidPrice: currBid, BidSize: currBidSz, AskPrice: currAsk, AskSize: currAskSz}
	aw.acc.Apply(price, size, isTrade, side, tsMs, prev, curr)
	if !aw.openDepthSet && currBid != "" && currAsk != "" {
		aw.acc.SetOpenDepth(features.ComputeDepthSnapshot(aw.ob.AllBids(), aw.ob.AllAsks()))
		aw.openDepthSet = true
	}
	if isTrade {
		parsedSize, err := strconv.ParseFloat(size, 64)
		if err == nil && parsedSize > 0 && !math.IsInf(parsedSize, 0) {
			aw.btw.Add(parsedSize)
			if threshold, ok := aw.btw.Threshold(); ok && parsedSize > threshold {
				aw.acc.ApplyBlockTrade(side, parsedSize)
			}
		}
	}
}

func (aw *accWriter) Flush(isPartial bool) error {
	bids, asks := aw.ob.AllBids(), aw.ob.AllAsks()
	if len(bids) > 0 || len(asks) > 0 {
		aw.acc.SetCloseDepth(features.ComputeDepthSnapshot(bids, asks))
	}
	aw.acc.SeedFromLastKnown() // carry-forward for empty-second null rows
	tsSecMs := time.Now().Truncate(time.Second).UnixMilli()
	bar := aw.acc.CurrentBar(tsSecMs, isPartial)

	// CumDelta and CVDDivergence: only on full bar close with trade data.
	if !isPartial && bar.BuyVolume != nil && bar.SellVolume != nil {
		aw.cumDelta += *bar.BuyVolume - *bar.SellVolume
		cd := aw.cumDelta
		bar.CumDelta = &cd
		if bar.Close != nil && aw.prevClose != nil {
			cvdDiv := 0
			if *bar.Close > *aw.prevClose && aw.cumDelta < aw.prevCumDelta {
				cvdDiv = 1
			} else if *bar.Close < *aw.prevClose && aw.cumDelta > aw.prevCumDelta {
				cvdDiv = -1
			}
			bar.CVDDivergence = &cvdDiv
		}
		aw.prevCumDelta = aw.cumDelta
		aw.prevClose = bar.Close
		aw.persistCumDelta()
	}

	if err := aw.w.WriteBar(aw.ctx, bar); err != nil {
		return err
	}
	if !isPartial {
		// Only fold 1s bars into cascade on full bar close, not partial flushes.
		closedBars := aw.engine.Fold(bar, tsSecMs)
		aw.persistCascadeHashes(closedBars)
		if aw.publisher != nil {
			aw.publisher.PublishBars(aw.ctx, closedBars)      // step 4: candles: + candles:close:
			aw.publisher.PublishOBFeatures(aw.ctx, bar)       // step 5: ob_features:
		}
		if aw.candles1sPub != nil {
			aw.candles1sPub.Publish1sBar(aw.ctx, bar) //nolint:errcheck — fire-and-forget; internal method logs on error
		}
		if aw.heatmapWriter != nil {
			aw.heatmapWriter.WriteHeatmap(aw.ctx, bar.TsSecMs, bids, asks) //nolint:errcheck — fire-and-forget; internal method logs on error
		}
	}
	aw.acc.BarReset()
	aw.openDepthSet = false
	aw.persistBlockWindow()
	return nil
}

// persistBlockWindow writes the current blockwindow contents to Redis as a LIST.
// DEL + RPUSH atomically replaces the window; errors are logged and swallowed
// (persistence is best-effort — cold-start null period is acceptable).
func (aw *accWriter) persistBlockWindow() {
	sizes := aw.btw.Sizes()
	key := "candle:btw:" + aw.exchange + ":" + aw.symbol
	pipe := aw.rdb.Pipeline()
	pipe.Del(aw.ctx, key)
	if len(sizes) > 0 {
		args := make([]interface{}, len(sizes))
		for i, s := range sizes {
			args[i] = strconv.FormatFloat(s, 'f', -1, 64)
		}
		pipe.RPush(aw.ctx, key, args...)
	}
	if _, err := pipe.Exec(aw.ctx); err != nil {
		slog.WarnContext(aw.ctx, "block trade window persist failed",
			"exchange", aw.exchange, "symbol", aw.symbol, "error", err)
	}
}

// persistCumDelta writes the current cumDelta to Redis as a simple STRING key.
// Best-effort: errors are logged and swallowed (same pattern as persistBlockWindow).
func (aw *accWriter) persistCumDelta() {
	key := "candle:acc:" + aw.exchange + ":" + aw.symbol + ":cum_delta"
	val := strconv.FormatFloat(aw.cumDelta, 'f', -1, 64)
	if err := aw.rdb.Set(aw.ctx, key, val, 0).Err(); err != nil {
		slog.WarnContext(aw.ctx, "cum_delta persist failed",
			"exchange", aw.exchange, "symbol", aw.symbol, "error", err)
	}
}

// PublishCascadePartial implements consumer.PartialPublisher.
// Reads engine.CurrentBar for each TF and publishes partial bars to candles: streams.
// Called from the consumer goroutine — safe to call engine.CurrentBar().
func (aw *accWriter) PublishCascadePartial(ctx context.Context) {
	if aw.publisher == nil {
		return
	}
	for _, tf := range cascade.AllTFs {
		bar := aw.engine.CurrentBar(tf)
		if bar.OpenTs == 0 {
			continue
		}
		aw.publisher.PublishPartialBar(ctx, bar)
	}
}

// persistCascadeHashes writes all 7 TF accumulators to Redis HASHes after each 1s close.
// All 7 are written on every close (not just closed TFs) to keep HASH state current.
// Retries 3 times with exponential backoff; errors are logged and swallowed.
func (aw *accWriter) persistCascadeHashes(_ []cascade.Bar) {
	bo := backoff.New(50*time.Millisecond, 2*time.Second, 2.0, 0.2)
	for _, tf := range cascade.AllTFs {
		fields := aw.engine.ToHash(tf)
		key := "candle:acc:" + aw.exchange + ":" + aw.symbol + ":" + string(tf)
		var lastErr error
		for attempt := 0; attempt < 3; attempt++ {
			if err := aw.rdb.HSet(aw.ctx, key, fields).Err(); err == nil {
				lastErr = nil
				break
			} else {
				lastErr = err
				if attempt < 2 {
					time.Sleep(bo.Next(attempt))
				}
			}
		}
		if lastErr != nil {
			slog.ErrorContext(aw.ctx, "cascade state write failed",
				"exchange", aw.exchange, "symbol", aw.symbol, "tf", tf, "error", lastErr)
			if aw.cascadeFailureCounter != nil {
				aw.cascadeFailureCounter()
			}
		}
	}
}

func (aw *accWriter) ApplyOBEvent(kind consumer.OBEventKind, side string, parsedSize float64) {
	switch kind {
	case consumer.OBEventAdd:
		aw.acc.IncrementOBAdd(side, parsedSize)
	case consumer.OBEventCancel:
		aw.acc.IncrementOBCancel(side)
	case consumer.OBEventModify:
		aw.acc.IncrementOBModify()
	}
}

func (aw *accWriter) IncrementGap() {
	aw.acc.IncrementGap()
}

func (aw *accWriter) Reset() {
	aw.acc.Reset()
	aw.openDepthSet = false
	aw.cumDelta = 0
	aw.prevCumDelta = 0
	aw.prevClose = nil
}

// symbolEntry bundles per-symbol state needed for the main loop and shutdown.
type symbolEntry struct {
	exchange       string
	symbol         string
	aw             *accWriter
	w              *questdbwriter.Writer
	c              *consumer.Consumer
	barClose       chan consumer.BarCloseSig
	partialPublish chan consumer.PartialPublishSig
	lag            atomic.Int64
}

func main() {
	cfg := config.Load()

	logger := newLogger(cfg.Slot, cfg.LogLevel)
	slog.SetDefault(logger)

	logger.Info("candle service starting",
		"version", version,
		"git_sha", gitSHA,
		"build_time", buildTime,
	)

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()

	// Prometheus custom registry — never DefaultRegisterer.
	reg := prometheus.NewRegistry()

	// Build list of all (exchange, symbol) pairs.
	type pair struct{ exchange, symbol string }
	var pairs []pair
	for _, sym := range cfg.SymbolsKuCoin {
		pairs = append(pairs, pair{"kucoin", sym})
	}
	for _, sym := range cfg.SymbolsByBit {
		pairs = append(pairs, pair{"bybit", sym})
	}

	metricPairs := make([]metrics.ExchangeSymbol, len(pairs))
	for i, p := range pairs {
		metricPairs[i] = metrics.ExchangeSymbol{Exchange: p.exchange, Symbol: p.symbol}
	}
	m, err := metrics.Register(reg, metricPairs)
	if err != nil {
		logger.Error("metrics registration failed", "error", err)
		os.Exit(1)
	}

	// HTTP server is started after entries are built so the StateFunc has valid data.
	// This avoids a data race from swapping httpServer.Handler after ListenAndServe starts.
	var httpServer *http.Server

	migr := migrator.New(&http.Client{Timeout: 30 * time.Second}, cfg.QuestDBHTTPAddr, "migrations")
	if err := migr.Run(ctx); err != nil {
		if ctx.Err() != nil {
			logger.Info("migration aborted by shutdown signal")
		} else {
			logger.Error("migration failed", "error", err)
			os.Exit(1)
		}
	} else {
		logger.Info("migrations applied successfully")
	}

	rdb, err := connectRedis(ctx, cfg)
	if err != nil {
		logger.Error("redis connect failed", "error", err)
		os.Exit(1)
	}
	defer rdb.Close()

	// Start the daily Parquet flush goroutine when B2 credentials are configured.
	// m is already registered above; wire callbacks directly.
	if cfg.B2KeyID != "" && cfg.B2Bucket != "" {
		flushCfg := flusher.Config{
			B2KeyID:           cfg.B2KeyID,
			B2AppKey:          cfg.B2AppKey,
			B2Bucket:          cfg.B2Bucket,
			B2Endpoint:        cfg.B2Endpoint,
			FlushTimeUTC:      cfg.FlushTimeUTC,
			FlushDateOverride: cfg.FlushDateOverride,
			QuestDBHTTPAddr:   cfg.QuestDBHTTPAddr,
		}
		f := flusher.New(flushCfg, rdb, logger, wallClock{},
			m.FlushSuccessTotal.Inc,
			func() {
				m.FlushFailureTotal.Inc()
				m.RecordFlushFailure(time.Now().Unix())
			},
			m.FlushAlertFailureTotal.Inc,
		)
		go func() {
			if err := f.Run(ctx); err != nil && ctx.Err() == nil {
				logger.Error("flusher exited with error", "error", err)
			}
		}()
	} else {
		logger.Info("flush disabled: B2_KEY_ID or B2_BUCKET not set")
	}

	writerCfg := questdbwriter.WriterConfig{
		ILPAddr:          cfg.QuestDBILPAddr,
		HTTPAddr:         cfg.QuestDBHTTPAddr,
		FlushInterval:    time.Duration(cfg.QuestDBILPFlushMs) * time.Millisecond,
		WALProbeInterval: time.Duration(cfg.WALProbeIntervalS) * time.Second,
		WALBufferSize:    cfg.WALBufferSize,
	}

	var (
		wg      sync.WaitGroup
		entries []*symbolEntry
	)

	// Phase 1: build all symbol entries (no goroutines yet).
	for _, p := range pairs {
		ob := orderbook.New(p.exchange, p.symbol, cfg.ColdStartBufferSize,
			func(g orderbook.GapMarker) {
				logger.Warn("orderbook gap emitted",
					"exchange", p.exchange, "symbol", p.symbol,
					"gap_cause", g.GapCause)
			})
		acc := accumulator.New(p.exchange, p.symbol, wallClock{})

		w, err := questdbwriter.New(ctx, writerCfg, logger)
		if err != nil {
			logger.Error("questdb writer init failed",
				"exchange", p.exchange, "symbol", p.symbol, "error", err)
			os.Exit(1)
		}
		w.SetWALDropCounter(m.WALDropTotal.Inc)
		w.SetWriteLatencyObserver(m.QuestDBWriteLatencyMs.Observe)
		w.StartWALProbe(ctx)

		btw := blockwindow.New(cfg.BlockTradeWindow, cfg.BlockTradeMinSample)
		btwKey := "candle:btw:" + p.exchange + ":" + p.symbol
		if raw, err := rdb.LRange(ctx, btwKey, 0, -1).Result(); err == nil && len(raw) > 0 {
			sizes := make([]float64, 0, len(raw))
			for _, s := range raw {
				if f, err := strconv.ParseFloat(s, 64); err == nil {
					sizes = append(sizes, f)
				}
			}
			btw.Restore(sizes)
			logger.Info("block trade window restored", "exchange", p.exchange, "symbol", p.symbol, "entries", len(sizes))
		}

		eng := cascade.NewEngine(p.exchange, p.symbol, wallClock{})
		exchange, symbol := p.exchange, p.symbol

		pub := rediswriter.New(rdb, p.exchange, p.symbol,
			int64(cfg.CandleStreamMaxLen), int64(cfg.CandleCloseStreamMaxLen))
		pub.SetFailureCounter(func() {
			m.RedisPublishFailureTotal.WithLabelValues(exchange, symbol).Inc()
		})

		candles1sPub := pubsubwriter.New(pubsubwriter.NewRealClient(rdb), p.exchange, p.symbol)

		hw, err := heatmapwriter.New(ctx, cfg.QuestDBILPAddr, p.exchange, p.symbol)
		if err != nil {
			logger.Error("heatmap writer init failed",
				"exchange", p.exchange, "symbol", p.symbol, "error", err)
			os.Exit(1)
		}

		aw := &accWriter{
			acc: acc, w: w, ob: ob, btw: btw, rdb: rdb,
			exchange: p.exchange, symbol: p.symbol, ctx: ctx,
			engine: eng,
			cascadeFailureCounter: func() {
				m.CascadeStateWriteFailureTotal.WithLabelValues(exchange, symbol).Inc()
			},
			publisher:     pub,
			candles1sPub:  candles1sPub,
			heatmapWriter: hw,
		}
		barClose := make(chan consumer.BarCloseSig, 1)
		partialPublish := make(chan consumer.PartialPublishSig, 1)
		obAdapter := consumer.NewOBAdapter(ob)
		c := consumer.New(rdb, cfg.ConsumerGroup,
			cfg.Slot+"-"+p.exchange+"-"+p.symbol,
			p.exchange, p.symbol,
			obAdapter, aw, barClose, logger).
			WithPartialPublish(partialPublish, aw)
		if cfg.ShadowMode {
			c.WithShadowMode()
		}

		e := &symbolEntry{
			exchange:       p.exchange,
			symbol:         p.symbol,
			aw:             aw,
			w:              w,
			c:              c,
			barClose:       barClose,
			partialPublish: partialPublish,
		}
		entries = append(entries, e)
	}

	// Phase 2: cascade reconstruction — runs synchronously before any consumer goroutine starts.
	for _, e := range entries {
		eng := e.aw.engine
		for _, tf := range cascade.AllTFs {
			key := "candle:acc:" + e.exchange + ":" + e.symbol + ":" + string(tf)
			if fields, err := rdb.HGetAll(ctx, key).Result(); err == nil && len(fields) > 0 {
				if fields["open_ts"] != "" && fields["open_ts"] != "0" {
					eng.RestoreFromHash(tf, fields)
					logger.Info("cascade reconstruction: restored from hash",
						"exchange", e.exchange, "symbol", e.symbol, "tf", tf)
				} else {
					logger.Info("cascade reconstruction: no hash, starting empty",
						"exchange", e.exchange, "symbol", e.symbol, "tf", tf)
				}
			} else {
				logger.Info("cascade reconstruction: no hash, starting empty",
					"exchange", e.exchange, "symbol", e.symbol, "tf", tf)
			}
		}
	}

	// Phase 2b: completeness checks via QuestDB (best-effort — log and continue on error).
	for _, e := range entries {
		eng := e.aw.engine
		for _, tf := range cascade.AllTFs {
			bar := eng.CurrentBar(tf)
			if bar.OpenTs == 0 {
				continue
			}
			boundary := lastClosedBoundary(tf, time.Now())
			if bar.OpenTs >= boundary.UnixMilli() {
				continue
			}
			expected := (boundary.UnixMilli() - bar.OpenTs) / 1000
			count, err := querySnapshotCount(ctx, cfg.QuestDBHTTPAddr, e.exchange, e.symbol, bar.OpenTs, boundary.UnixMilli())
			if err != nil {
				logger.Error("cascade reconstruction: QuestDB check failed",
					"exchange", e.exchange, "symbol", e.symbol, "tf", tf, "error", err)
				continue
			}
			if count < int64(float64(expected)*0.95) {
				logger.Warn("cascade reconstruction incomplete",
					"exchange", e.exchange, "symbol", e.symbol, "tf", tf,
					"expected", expected, "actual", count)
			}
		}
	}

	// Phase 2c: restore cumDelta from Redis (best-effort — log and continue on error).
	for _, e := range entries {
		key := "candle:acc:" + e.exchange + ":" + e.symbol + ":cum_delta"
		if val, err := rdb.Get(ctx, key).Result(); err == nil {
			if v, err := strconv.ParseFloat(val, 64); err == nil {
				e.aw.cumDelta = v
				e.aw.prevCumDelta = v
				logger.Info("cum_delta restored", "exchange", e.exchange, "symbol", e.symbol, "value", v)
			}
		}
	}

	// Phase 3: start consumer goroutines now that reconstruction is complete.
	for _, e := range entries {
		wg.Add(1)
		go func(c *consumer.Consumer, e *symbolEntry) {
			defer wg.Done()
			if err := c.Run(ctx); err != nil && !errors.Is(err, context.Canceled) {
				logger.Error("consumer exited with error",
					"exchange", e.exchange, "symbol", e.symbol, "error", err)
			}
		}(e.c, e)
	}

	// promoted tracks whether POST /promote has been called.
	var promoted atomic.Bool

	// Build the real StateFunc now that entries are populated.
	stateFunc := func() health.HealthState {
		var maxLag int64
		for _, e := range entries {
			if l := e.lag.Load(); l > maxLag {
				maxLag = l
			}
		}
		walState := "ok"
		for _, e := range entries {
			if e.w.IsWALSuspended() {
				walState = "suspended"
				break
			}
		}
		shadowLag := int64(0)
		if cfg.ShadowMode && !promoted.Load() {
			lagCtx, lagCancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
			defer lagCancel()
			for _, e := range entries {
				if lag := e.c.ShadowLag(lagCtx); lag > shadowLag {
					shadowLag = lag
				}
			}
		}
		return health.HealthState{
			ConsumerLagMax:    maxLag,
			QuestDBWriteState: walState,
			ShadowLag:         shadowLag,
		}
	}

	// Build and start the HTTP server with the real StateFunc — no handler swapping.
	healthSrv := health.New(cfg.Slot, version, gitSHA, buildTime, stateFunc)
	healthSrv.WithMetrics(reg)
	if cfg.ShadowMode {
		promoteFn := func() {
			if promoted.Swap(true) {
				return // already promoted — idempotent no-op
			}
			// Only set the group cursor and clear shadowMode here.
			// acc.Reset, seenIDs clear, and XAutoClaimPending run in the consumer
			// goroutine after runShadow() exits — safe because the consumer goroutine
			// exclusively owns its accumulator and order book state.
			for _, e := range entries {
				if err := e.c.Promote(e.c.LastShadowID()); err != nil {
					logger.Error("promote failed", "exchange", e.exchange, "symbol", e.symbol, "error", err)
				}
			}
			logger.Info("promoted: switched from shadow XREAD to XREADGROUP", "slot", cfg.Slot)
		}
		healthSrv.WithPromotion(promoteFn)
	}
	httpServer = &http.Server{
		Addr:         ":" + cfg.ServicePort,
		Handler:      healthSrv.Handler(),
		ReadTimeout:  5 * time.Second,
		WriteTimeout: 5 * time.Second,
	}
	go func() {
		logger.Info("http server listening", "port", cfg.ServicePort)
		if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.Error("http server error", "error", err)
			stop()
		}
	}()

	// Shared 1-second ticker — fans out BarCloseSig to all consumer channels.
	if len(entries) > 0 {
		go func() {
			ticker := time.NewTicker(time.Second)
			defer ticker.Stop()
			for {
				select {
				case <-ctx.Done():
					return
				case <-ticker.C:
					for _, e := range entries {
						select {
						case e.barClose <- consumer.BarCloseSig{}:
						default:
							m.BarCloseDroppedTotal.WithLabelValues(e.exchange, e.symbol).Inc()
						}
					}
				}
			}
		}()
	}

	// Shared 250ms ticker — fans out PartialPublishSig to all consumer channels.
	// Dropped signals (consumer busy) are intentional; no counter needed.
	if len(entries) > 0 {
		go func() {
			ticker := time.NewTicker(time.Duration(cfg.CandlePartialPublishMs) * time.Millisecond)
			defer ticker.Stop()
			for {
				select {
				case <-ctx.Done():
					return
				case <-ticker.C:
					for _, e := range entries {
						select {
						case e.partialPublish <- consumer.PartialPublishSig{}:
						default:
						}
					}
				}
			}
		}()
	}

	// Lag polling goroutine — every 5 seconds, update each symbol's lag atomic and health gauges.
	if len(entries) > 0 {
		go func() {
			ticker := time.NewTicker(5 * time.Second)
			defer ticker.Stop()
			for {
				select {
				case <-ctx.Done():
					return
				case <-ticker.C:
					var maxLag int64
					walOK := true
					for _, e := range entries {
						lag := e.c.Lag(ctx)
						if lag < 0 {
							lag = 0
						}
						e.lag.Store(lag)
						m.ConsumerLag.WithLabelValues(e.exchange, e.symbol).Set(float64(lag))
						if lag > maxLag {
							maxLag = lag
						}
						if e.w.IsWALSuspended() {
							walOK = false
						}
					}
					lagHealth := 1.0
					if maxLag > 1000 {
						lagHealth = 0.0
					}
					m.Health.WithLabelValues("consumer_lag").Set(lagHealth)
					walHealth := 1.0
					if !walOK {
						walHealth = 0.0
					}
					m.Health.WithLabelValues("questdb").Set(walHealth)
					m.Health.WithLabelValues("flush").Set(m.FlushHealthy(900, time.Now().Unix()))
				}
			}
		}()
	}

	<-ctx.Done()

	logger.Info("shutdown: waiting for consumers to drain")
	wg.Wait()

	shutdownCtx, cancel := context.WithTimeout(context.Background(), cfg.ShutdownTimeout)
	defer cancel()

	// Flush each accumulator as a final partial bar.
	for _, e := range entries {
		tsSecMs := time.Now().Truncate(time.Second).UnixMilli()
		bar := e.aw.acc.CurrentBar(tsSecMs, true)
		if err := e.w.WriteBar(shutdownCtx, bar); err != nil {
			logger.Error("final flush failed", "exchange", e.exchange, "symbol", e.symbol, "error", err)
		}
		if err := e.w.Close(shutdownCtx); err != nil {
			logger.Error("writer close failed", "exchange", e.exchange, "symbol", e.symbol, "error", err)
		}
		if e.aw.heatmapWriter != nil {
			if err := e.aw.heatmapWriter.Close(shutdownCtx); err != nil {
				logger.Error("heatmap writer close failed", "exchange", e.exchange, "symbol", e.symbol, "error", err)
			}
		}
	}

	if err := httpServer.Shutdown(shutdownCtx); err != nil {
		logger.Error("http shutdown error", "error", err)
	}

	logger.Info("candle service stopped")
}

// querySnapshotCount returns the count of snapshot_1s rows for (exchange, symbol)
// in the half-open interval [fromMs, toMs). QuestDB timestamps are microseconds;
// fromMs and toMs are Unix milliseconds and are multiplied by 1000 before querying.
func querySnapshotCount(ctx context.Context, httpAddr, exchange, symbol string, fromMs, toMs int64) (int64, error) {
	query := fmt.Sprintf(
		"SELECT count() FROM snapshot_1s WHERE exchange='%s' AND symbol='%s' AND ts >= %d AND ts < %d",
		exchange, symbol, fromMs*1000, toMs*1000,
	)
	u := "http://" + httpAddr + "/exec?query=" + url.QueryEscape(query)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return 0, err
	}
	httpClient := &http.Client{Timeout: 10 * time.Second}
	resp, err := httpClient.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return 0, fmt.Errorf("QuestDB /exec returned %d", resp.StatusCode)
	}
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return 0, err
	}
	var result struct {
		Dataset [][]interface{} `json:"dataset"`
	}
	if err := json.Unmarshal(body, &result); err != nil {
		return 0, err
	}
	if len(result.Dataset) == 0 || len(result.Dataset[0]) == 0 {
		return 0, nil
	}
	// QuestDB returns count() as a float64 in JSON.
	v, ok := result.Dataset[0][0].(float64)
	if !ok {
		return 0, fmt.Errorf("unexpected count type: %T", result.Dataset[0][0])
	}
	return int64(v), nil
}

// lastClosedBoundary returns the most recent closed bar boundary at or before now for tf.
func lastClosedBoundary(tf cascade.TF, now time.Time) time.Time {
	t := now.UTC()
	switch tf {
	case cascade.TF1m:
		return t.Truncate(time.Minute)
	case cascade.TF5m:
		return t.Truncate(5 * time.Minute)
	case cascade.TF15m:
		return t.Truncate(15 * time.Minute)
	case cascade.TF1h:
		return t.Truncate(time.Hour)
	case cascade.TF4h:
		return t.Truncate(4 * time.Hour)
	case cascade.TF1d:
		return t.Truncate(24 * time.Hour)
	case cascade.TF1w:
		// Most recent Monday 00:00:00 UTC at or before now.
		daysBack := int(t.Weekday())
		if daysBack == 0 {
			daysBack = 7 // Sunday → go back 7 days to reach previous Monday
		}
		daysBack-- // Monday = 0 days back from Monday, 1 day back from Tuesday, etc.
		return time.Date(t.Year(), t.Month(), t.Day()-daysBack, 0, 0, 0, 0, time.UTC)
	}
	return time.Time{}
}

func connectRedis(ctx context.Context, cfg config.Config) (*redis.Client, error) {
	opts, err := redis.ParseURL(cfg.RedisURL)
	if err != nil {
		return nil, err
	}
	rdb := redis.NewClient(opts)
	if err := rdb.Ping(ctx).Err(); err != nil {
		rdb.Close()
		return nil, err
	}
	return rdb, nil
}

// newLogger builds a JSON slog.Logger that appends slot=<slot> to every record.
// Chain: slotHandler → redactHandler → JSONHandler.
func newLogger(slot, logLevel string) *slog.Logger {
	base := slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: parseLogLevel(logLevel)})
	redacted := &redactHandler{base: base}
	return slog.New(&slotHandler{base: redacted, slot: slot})
}

func parseLogLevel(level string) slog.Level {
	switch level {
	case "debug":
		return slog.LevelDebug
	case "warn":
		return slog.LevelWarn
	case "error":
		return slog.LevelError
	default:
		return slog.LevelInfo
	}
}

// slotHandler wraps a slog.Handler and appends slot to every log record.
type slotHandler struct {
	base slog.Handler
	slot string
}

func (h *slotHandler) Enabled(ctx context.Context, level slog.Level) bool {
	return h.base.Enabled(ctx, level)
}

func (h *slotHandler) Handle(ctx context.Context, r slog.Record) error {
	r.AddAttrs(slog.String("slot", h.slot))
	return h.base.Handle(ctx, r)
}

func (h *slotHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	return &slotHandler{base: h.base.WithAttrs(attrs), slot: h.slot}
}

func (h *slotHandler) WithGroup(name string) slog.Handler {
	return &slotHandler{base: h.base.WithGroup(name), slot: h.slot}
}

// sensitiveKeys: attribute keys containing any of these substrings are redacted.
var sensitiveKeys = []string{"password", "b2_access", "b2_secret", "secret_key", "b2_key_id", "b2_app_key"}

// redactHandler wraps a slog.Handler and redacts sensitive attribute values.
type redactHandler struct {
	base slog.Handler
}

func (h *redactHandler) Enabled(ctx context.Context, level slog.Level) bool {
	return h.base.Enabled(ctx, level)
}

func (h *redactHandler) Handle(ctx context.Context, r slog.Record) error {
	newRec := slog.NewRecord(r.Time, r.Level, r.Message, r.PC)
	r.Attrs(func(a slog.Attr) bool {
		newRec.AddAttrs(redactAttr(a))
		return true
	})
	return h.base.Handle(ctx, newRec)
}

func (h *redactHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	redacted := make([]slog.Attr, len(attrs))
	for i, a := range attrs {
		redacted[i] = redactAttr(a)
	}
	return &redactHandler{base: h.base.WithAttrs(redacted)}
}

func (h *redactHandler) WithGroup(name string) slog.Handler {
	return &redactHandler{base: h.base.WithGroup(name)}
}

func redactAttr(a slog.Attr) slog.Attr {
	a.Value = a.Value.Resolve()
	if a.Value.Kind() == slog.KindGroup {
		raw := a.Value.Group()
		redacted := make([]any, len(raw))
		for i, ga := range raw {
			redacted[i] = redactAttr(ga)
		}
		return slog.Group(a.Key, redacted...)
	}
	key := strings.ToLower(a.Key)
	for _, s := range sensitiveKeys {
		if strings.Contains(key, s) {
			return slog.String(a.Key, "[REDACTED]")
		}
	}
	// Partial redact for URL attributes that contain a password.
	if strings.Contains(key, "url") {
		v := a.Value.String()
		if u, err := url.Parse(v); err == nil && u.User != nil {
			if _, hasPwd := u.User.Password(); hasPwd {
				u.User = url.UserPassword(u.User.Username(), "[REDACTED]")
				return slog.String(a.Key, u.String())
			}
		}
	}
	return a
}
