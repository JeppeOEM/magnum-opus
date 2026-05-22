package main

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	goredis "github.com/redis/go-redis/v9"
	"github.com/prometheus/client_golang/prometheus"

	"github.com/mrqdt/magnum-opus/aggregator/internal/config"
	"github.com/mrqdt/magnum-opus/aggregator/internal/coordinator"
	"github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
	bybitexch "github.com/mrqdt/magnum-opus/aggregator/internal/exchange/bybit"
	kucoinexch "github.com/mrqdt/magnum-opus/aggregator/internal/exchange/kucoin"
	"github.com/mrqdt/magnum-opus/aggregator/internal/gapwindow"
	"github.com/mrqdt/magnum-opus/aggregator/internal/httpapi"
	"github.com/mrqdt/magnum-opus/aggregator/internal/metrics"
	"github.com/mrqdt/magnum-opus/aggregator/internal/slogredact"
	"github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
	rediswriter "github.com/mrqdt/magnum-opus/aggregator/internal/writer/redis"
	questdbwriter "github.com/mrqdt/magnum-opus/aggregator/internal/writer/questdb"
	pubsubwriter "github.com/mrqdt/magnum-opus/aggregator/internal/writer/pubsub"
)

// Build-time vars injected via -ldflags.
var (
	version   = "dev"
	gitSHA    = "unknown"
	buildTime = "unknown"
)

// dynamicSymbolsPrefix is the Redis key prefix for dynamically-added symbols.
// Key pattern: config:symbols:{exchange}  (Redis SET of raw symbol strings)
// These are loaded at startup and merged with config.yaml so additions survive restarts.
const dynamicSymbolsPrefix = "config:symbols:"

// symbolManager implements httpapi.SymbolManager.
// AddSymbol persists to Redis so new symbols survive a service restart.
type symbolManager struct {
	coord *coordinator.Coordinator
	rdb   *goredis.Client
}

func (m *symbolManager) AddSymbol(ctx context.Context, exch, sym string) error {
	if err := m.coord.AddSymbol(ctx, exch, sym); err != nil {
		return err
	}
	// Best-effort Redis persist — symbol is live even if this fails.
	if err := m.rdb.SAdd(ctx, dynamicSymbolsPrefix+exch, sym).Err(); err != nil {
		slog.Warn("aggregator: symbol live but Redis persist failed — won't survive restart",
			"exchange", exch, "symbol", sym, "err", err)
	}
	return nil
}

func (m *symbolManager) ListSymbols() []httpapi.SymbolEntry {
	tracked := m.coord.ListSymbols()
	entries := make([]httpapi.SymbolEntry, len(tracked))
	for i, t := range tracked {
		entries[i] = httpapi.SymbolEntry{Exchange: t.Exchange, Symbol: t.Symbol}
	}
	return entries
}

// mergeUnique appends strings from extra to base, skipping duplicates.
func mergeUnique(base, extra []string) []string {
	seen := make(map[string]bool, len(base))
	for _, s := range base {
		seen[s] = true
	}
	for _, s := range extra {
		if !seen[s] {
			base = append(base, s)
			seen[s] = true
		}
	}
	return base
}

// realClock wraps time.Now() for all components that require a Clock interface.
type realClock struct{}

func (realClock) Now() time.Time { return time.Now() }

// multiSnapshotFetcher dispatches FetchSnapshot to the correct exchange fetcher by name.
type multiSnapshotFetcher struct {
	fetchers map[string]coordinator.SnapshotFetcher
}

func (m *multiSnapshotFetcher) FetchSnapshot(ctx context.Context, exch string, sym symbol.Symbol) (coordinator.SnapshotResult, error) {
	if f, ok := m.fetchers[exch]; ok {
		return f.FetchSnapshot(ctx, exch, sym)
	}
	return coordinator.SnapshotResult{}, fmt.Errorf("no snapshot fetcher for exchange %q", exch)
}

func main() {
	// ── 1. Load config ────────────────────────────────────────────────────────
	cfg, err := config.Load()
	if err != nil {
		// slog not yet initialized — write to stderr directly
		fmt.Fprintf(os.Stderr, "aggregator: config error: %v\n", err)
		os.Exit(1)
	}

	// ── 2. Initialize credential-sanitizing logger (ARC9: FIRST action) ───────
	level := parseLogLevel(cfg.Service.LogLevel)
	inner := slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: level})
	slog.SetDefault(slog.New(slogredact.New(inner, nil)))

	slog.Info("aggregator starting",
		"version", version,
		"git_sha", gitSHA,
		"build_time", buildTime,
	)

	// ── 3. Root context with signal handling ──────────────────────────────────
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()

	clk := realClock{}
	startTime := clk.Now()

	// ── 4. Prometheus metrics ─────────────────────────────────────────────────
	promReg := prometheus.NewRegistry()
	metricsReg := metrics.New(promReg)

	// ── 5. Redis stream writer ────────────────────────────────────────────────
	redisClient := goredis.NewClient(&goredis.Options{
		Addr:     cfg.Redis.Addr,
		Password: cfg.Redis.Password.Value(),
	})
	realRedis := rediswriter.NewRealClient(redisClient)
	streamWriter := rediswriter.New(realRedis, clk, 10*time.Second)
	pubsubWriter := pubsubwriter.New(pubsubwriter.NewRealClient(redisClient))

	// ── 6. QuestDB ILP writer ─────────────────────────────────────────────────
	ilpSender, err := questdbwriter.NewRealSender(ctx, cfg.QuestDB.ILPAddr)
	if err != nil {
		slog.Error("aggregator: failed to connect to QuestDB ILP", "err", err, "addr", cfg.QuestDB.ILPAddr)
		os.Exit(1)
	}
	walChecker := questdbwriter.NewRealWALChecker(cfg.QuestDB.HTTPAddr)
	ilpWriter := questdbwriter.New(
		ilpSender, walChecker, realRedis, clk,
		1*time.Millisecond, 1*time.Hour, 30*time.Second,
	).Start()

	// ── 7. Merge dynamic symbols from Redis (survive restarts) ───────────────
	// Symbols added via POST /symbols are stored in Redis SETs so they are
	// reloaded here on every start — config.yaml stays as the static baseline.
	for _, exch := range []string{"kucoin", "bybit"} {
		dynamic, err := redisClient.SMembers(ctx, dynamicSymbolsPrefix+exch).Result()
		if err != nil {
			slog.Warn("aggregator: could not load dynamic symbols from Redis",
				"exchange", exch, "err", err)
			continue
		}
		if len(dynamic) == 0 {
			continue
		}
		switch exch {
		case "kucoin":
			cfg.Symbols.KuCoin = mergeUnique(cfg.Symbols.KuCoin, dynamic)
		case "bybit":
			cfg.Symbols.Bybit = mergeUnique(cfg.Symbols.Bybit, dynamic)
		}
		slog.Info("aggregator: loaded dynamic symbols from Redis",
			"exchange", exch, "symbols", dynamic)
	}

	// ── 8. Exchange adapters (only for configured symbols) ───────────────────
	kuCoinSyms := normalizeSymbols("kucoin", cfg.Symbols.KuCoin)
	bybitSyms := normalizeSymbols("bybit", cfg.Symbols.Bybit)

	var exchConfigs []coordinator.ExchangeConfig
	fetchers := map[string]coordinator.SnapshotFetcher{}

	if len(cfg.Symbols.KuCoin) > 0 {
		mode := "private"
		if cfg.KuCoin.Public {
			mode = "public"
		}
		slog.Info("aggregator: kucoin enabled", "mode", mode, "symbols", cfg.Symbols.KuCoin)
		kucoinAdapter := kucoinexch.New(cfg.KuCoin, http.DefaultClient, clk)
		if err := kucoinAdapter.Connect(ctx); err != nil {
			slog.Error("aggregator: kucoin connect failed", "err", err)
			os.Exit(1)
		}
		if err := kucoinAdapter.Subscribe(cfg.Symbols.KuCoin, []exchange.FeedType{exchange.FeedTypeOrderBook, exchange.FeedTypeTrade}); err != nil {
			slog.Error("aggregator: kucoin subscribe failed", "err", err, "symbols", cfg.Symbols.KuCoin)
			os.Exit(1)
		}
		exchConfigs = append(exchConfigs, coordinator.ExchangeConfig{Adapter: kucoinAdapter, Symbols: kuCoinSyms})
		fetchers["kucoin"] = kucoinexch.NewOrderBookFetcher(kucoinAdapter)
	}

	if len(cfg.Symbols.Bybit) > 0 {
		slog.Info("aggregator: bybit enabled", "symbols", cfg.Symbols.Bybit)
		bybitAdapter := bybitexch.New(clk)
		if err := bybitAdapter.Connect(ctx); err != nil {
			slog.Error("aggregator: bybit connect failed", "err", err)
			os.Exit(1)
		}
		if err := bybitAdapter.Subscribe(cfg.Symbols.Bybit, []exchange.FeedType{exchange.FeedTypeOrderBook, exchange.FeedTypeTrade}); err != nil {
			slog.Error("aggregator: bybit subscribe failed", "err", err, "symbols", cfg.Symbols.Bybit)
			os.Exit(1)
		}
		exchConfigs = append(exchConfigs, coordinator.ExchangeConfig{Adapter: bybitAdapter, Symbols: bybitSyms})
		fetchers["bybit"] = bybitexch.NewSnapshotFetcher(http.DefaultClient)
	}

	if len(exchConfigs) == 0 {
		slog.Error("aggregator: no exchanges configured — set KUCOIN_SYMBOLS and/or BYBIT_SYMBOLS")
		os.Exit(1)
	}

	// ── 9. Coordinator ────────────────────────────────────────────────────────
	fetcher := &multiSnapshotFetcher{fetchers: fetchers}

	coord := coordinator.New(
		exchConfigs,
		streamWriter, ilpWriter, fetcher, clk,
	).WithMetrics(metricsReg).WithOBPublisher(pubsubWriter)

	// ── 10. HTTP server + startup gate ────────────────────────────────────────
	gapWin := gapwindow.New()
	feedStatus := httpapi.NewGathererFeedStatus(promReg)

	symMgr := &symbolManager{coord: coord, rdb: redisClient}

	var startupReady atomic.Bool
	httpSrv := httpapi.New(
		cfg.Service.HTTPAddr,
		feedStatus, gapWin, promReg, startTime,
		httpapi.VersionInfo{Version: version, GitSHA: gitSHA, BuildTime: buildTime},
	).WithReadyFn(startupReady.Load).WithSymbolManager(symMgr)

	// ── 10. Start coordinator and HTTP server ─────────────────────────────────
	coord.Run(ctx)

	// ── 11. Health-update goroutine (AC4) ─────────────────────────────────────
	go func() {
		ticker := time.NewTicker(5 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				metricsReg.Health.WithLabelValues("feeds").Set(computeMinFeedState(promReg))
				metricsReg.Health.WithLabelValues("gaps").Set(metricsReg.GapHealthy(300, time.Now().Unix()))
			}
		}
	}()

	go func() {
		if err := httpSrv.Start(ctx); err != nil {
			slog.Error("aggregator: HTTP server error", "err", err)
		}
	}()
	slog.Info("aggregator: HTTP server started", "addr", cfg.Service.HTTPAddr)

	// ── 12. Startup gate: wait for all feeds to go live ───────────────────────
	// runStartupGate returns nil on success or SIGTERM (clean exit). Non-nil = timeout (exit 1).
	totalFeeds := len(kuCoinSyms) + len(bybitSyms)
	if err := runStartupGate(ctx, feedStatus, promReg, totalFeeds,
		time.Duration(cfg.Service.StartupTimeoutSec)*time.Second,
		&startupReady); err != nil {
		slog.Error("aggregator: startup gate failed", "err", err)
		coord.Shutdown()
		os.Exit(1)
	}

	// ── 13. Wait for shutdown signal ──────────────────────────────────────────
	<-ctx.Done()
	slog.Info("aggregator: shutdown signal received")
	stop() // release signal resources

	coord.Shutdown()
	slog.Info("aggregator: shutdown complete")
}

// runStartupGate polls until all feeds are live or the timeout elapses.
// On success, sets startupReady to true. On timeout, returns an error listing
// unconfirmed symbols (read from aggregator_feed_state metrics).
func runStartupGate(
	ctx context.Context,
	feedStatus *httpapi.GathererFeedStatus,
	gatherer prometheus.Gatherer,
	totalFeeds int,
	timeout time.Duration,
	ready *atomic.Bool,
) error {
	if totalFeeds == 0 {
		ready.Store(true)
		return nil
	}
	deadline := time.NewTimer(timeout)
	defer deadline.Stop()
	tick := time.NewTicker(time.Second)
	defer tick.Stop()

	for {
		select {
		case <-ctx.Done():
			// SIGTERM during startup: treat as clean exit, not a startup failure (FR33).
			return nil
		case <-deadline.C:
			// Report which symbols are still disconnected.
			unconfirmed := gatherUnconfirmedSymbols(gatherer)
			return fmt.Errorf("feeds not ready after %v; unconfirmed: %s", timeout, strings.Join(unconfirmed, ", "))
		case <-tick.C:
			connected, total := feedStatus.FeedCounts()
			if total > 0 && connected == total {
				ready.Store(true)
				slog.Info("aggregator: all feeds confirmed", "feeds", total)
				return nil
			}
		}
	}
}

// computeMinFeedState returns min(aggregator_feed_state) across all label series.
// Returns 1.0 if no series exist (no feeds configured yet).
func computeMinFeedState(g prometheus.Gatherer) float64 {
	mfs, _ := g.Gather()
	min := 1.0
	for _, mf := range mfs {
		if mf.GetName() != "aggregator_feed_state" {
			continue
		}
		for _, m := range mf.GetMetric() {
			if v := m.GetGauge().GetValue(); v < min {
				min = v
			}
		}
	}
	return min
}

// gatherUnconfirmedSymbols returns "{exchange}/{symbol}" strings for feeds with feed_state=0.
func gatherUnconfirmedSymbols(g prometheus.Gatherer) []string {
	mfs, _ := g.Gather()
	var out []string
	for _, mf := range mfs {
		if mf.GetName() != "aggregator_feed_state" {
			continue
		}
		for _, m := range mf.GetMetric() {
			if m.GetGauge().GetValue() != 1 {
				var exch, sym string
				for _, lp := range m.GetLabel() {
					switch lp.GetName() {
					case "exchange":
						exch = lp.GetValue()
					case "symbol":
						sym = lp.GetValue()
					}
				}
				out = append(out, exch+"/"+sym)
			}
		}
	}
	return out
}

// normalizeSymbols converts raw exchange-specific symbols to canonical Symbol values.
func normalizeSymbols(exch string, raw []string) []symbol.Symbol {
	syms := make([]symbol.Symbol, len(raw))
	for i, r := range raw {
		syms[i] = symbol.Normalize(exch, r)
	}
	return syms
}

// parseLogLevel maps a string to slog.Level; defaults to Info on unknown values.
func parseLogLevel(s string) slog.Level {
	switch strings.ToLower(s) {
	case "debug":
		return slog.LevelDebug
	case "warn", "warning":
		return slog.LevelWarn
	case "error":
		return slog.LevelError
	default:
		return slog.LevelInfo
	}
}
