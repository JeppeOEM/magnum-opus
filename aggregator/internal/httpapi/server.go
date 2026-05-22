// Package httpapi exposes /health, /version, /metrics, and /symbols HTTP endpoints.
// No authentication required on any endpoint (NFR17).
// All state reads are in-memory; no IO is performed per-request (NFR20).
package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"runtime"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/mrqdt/magnum-opus/aggregator/internal/gapwindow"
)

// SymbolEntry is the JSON representation of one tracked feed.
type SymbolEntry struct {
	Exchange string `json:"exchange"`
	Symbol   string `json:"symbol"`
}

// SymbolManager is implemented by the composition root and injected via WithSymbolManager.
// AddSymbol must be safe to call concurrently from multiple HTTP handler goroutines.
type SymbolManager interface {
	AddSymbol(ctx context.Context, exchange, symbol string) error
	ListSymbols() []SymbolEntry
}

// VersionInfo carries build-time version metadata injected by the composition root.
// GoVersion is always sourced from runtime.Version() — not injected here.
type VersionInfo struct {
	Version   string
	GitSHA    string
	BuildTime string
}

// FeedStatusReader provides current connected/total feed counts.
// Implementations must be goroutine-safe and perform no IO.
type FeedStatusReader interface {
	FeedCounts() (connected, total int)
}

// GathererFeedStatus implements FeedStatusReader by reading aggregator_feed_state from a Gatherer.
// Gather() is an in-memory operation; no network or file IO is performed.
type GathererFeedStatus struct {
	g prometheus.Gatherer
}

// NewGathererFeedStatus wraps a prometheus.Gatherer for use as a FeedStatusReader.
func NewGathererFeedStatus(g prometheus.Gatherer) *GathererFeedStatus {
	return &GathererFeedStatus{g: g}
}

func (f *GathererFeedStatus) FeedCounts() (connected, total int) {
	mfs, _ := f.g.Gather()
	for _, mf := range mfs {
		if mf.GetName() != "aggregator_feed_state" {
			continue
		}
		for _, m := range mf.GetMetric() {
			total++
			if m.GetGauge().GetValue() == 1 {
				connected++
			}
		}
	}
	return
}

// Server holds the HTTP mux and dependencies for the operational endpoints.
type Server struct {
	mux       *http.ServeMux
	addr      string
	feeds     FeedStatusReader
	gapWin    *gapwindow.Window
	startTime time.Time
	ver       VersionInfo
	isReady   func() bool      // nil = always ready; non-nil = returns false during startup phase
	nowFn     func() time.Time // injected for testability; default time.Now
	mgr       SymbolManager    // nil if WithSymbolManager was not called
}

// New constructs a Server with /health, /version, and /metrics registered on an internal mux.
// The gatherer is used for /metrics; no auth on any endpoint (NFR17).
func New(
	addr string,
	feeds FeedStatusReader,
	gapWin *gapwindow.Window,
	gatherer prometheus.Gatherer,
	startTime time.Time,
	ver VersionInfo,
) *Server {
	s := &Server{
		mux:       http.NewServeMux(),
		addr:      addr,
		feeds:     feeds,
		gapWin:    gapWin,
		startTime: startTime,
		ver:       ver,
		nowFn:     time.Now,
	}
	s.mux.HandleFunc("/health", s.handleHealth)
	s.mux.HandleFunc("/version", s.handleVersion)
	s.mux.Handle("/metrics", promhttp.HandlerFor(gatherer, promhttp.HandlerOpts{}))
	return s
}

// WithReadyFn sets a function that returns true once the startup gate is cleared.
// Until it returns true, /health reports status="starting". Pass nil to disable.
func (s *Server) WithReadyFn(fn func() bool) *Server {
	s.isReady = fn
	return s
}

// WithSymbolManager wires a SymbolManager and registers the /symbols endpoints:
//
//	POST /symbols  — add a new symbol to the running service
//	GET  /symbols  — list all currently tracked (exchange, symbol) pairs
func (s *Server) WithSymbolManager(mgr SymbolManager) *Server {
	s.mgr = mgr
	s.mux.HandleFunc("POST /symbols", s.handleAddSymbol)
	s.mux.HandleFunc("GET /symbols", s.handleListSymbols)
	return s
}

// Handler returns the HTTP handler for testing without starting a real listener.
func (s *Server) Handler() http.Handler { return s.mux }

// Start calls ListenAndServe on the configured address. It returns nil on graceful shutdown
// (ctx cancellation) and a non-nil error only for unexpected listener failures.
func (s *Server) Start(ctx context.Context) error {
	srv := &http.Server{Addr: s.addr, Handler: s.mux}
	go func() {
		<-ctx.Done()
		shutCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = srv.Shutdown(shutCtx)
	}()
	if err := srv.ListenAndServe(); !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}

type healthResponse struct {
	Status         string `json:"status"`
	ConnectedFeeds int    `json:"connected_feeds"`
	GapCount24h    int    `json:"gap_count_24h"`
	UptimeSeconds  int64  `json:"uptime_seconds"`
}

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	now := s.nowFn()
	connected, total := s.feeds.FeedCounts()
	gapCount := s.gapWin.Count(now)

	status := "ok"
	if s.isReady != nil && !s.isReady() {
		status = "starting"
	} else {
		if connected < total {
			status = "degraded"
		}
		if gapCount > 0 {
			status = "critical"
		}
	}

	resp := healthResponse{
		Status:         status,
		ConnectedFeeds: connected,
		GapCount24h:    gapCount,
		UptimeSeconds:  int64(now.Sub(s.startTime).Seconds()),
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(resp)
}

type versionResponse struct {
	Version   string `json:"version"`
	GitSHA    string `json:"git_sha"`
	BuildTime string `json:"build_time"`
	GoVersion string `json:"go_version"`
}

func (s *Server) handleVersion(w http.ResponseWriter, _ *http.Request) {
	resp := versionResponse{
		Version:   s.ver.Version,
		GitSHA:    s.ver.GitSHA,
		BuildTime: s.ver.BuildTime,
		GoVersion: runtime.Version(),
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(resp)
}

// handleAddSymbol handles POST /symbols.
//
// Request body:
//
//	{"exchange": "kucoin", "symbol": "XRP-USDT"}
//
// Success (201 Created):
//
//	{"status": "ok", "exchange": "kucoin", "symbol": "XRP-USDT"}
//
// Errors return 400 with {"error": "..."}.
func (s *Server) handleAddSymbol(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Exchange string `json:"exchange"`
		Symbol   string `json:"symbol"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSONError(w, "invalid JSON body", http.StatusBadRequest)
		return
	}
	if req.Exchange == "" || req.Symbol == "" {
		writeJSONError(w, `"exchange" and "symbol" are required`, http.StatusBadRequest)
		return
	}

	if err := s.mgr.AddSymbol(r.Context(), req.Exchange, req.Symbol); err != nil {
		writeJSONError(w, err.Error(), http.StatusBadRequest)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(map[string]string{
		"status":   "ok",
		"exchange": req.Exchange,
		"symbol":   req.Symbol,
	})
}

// handleListSymbols handles GET /symbols.
//
// Response (200 OK):
//
//	{"symbols": [{"exchange": "kucoin", "symbol": "BTC-USDT"}, ...]}
func (s *Server) handleListSymbols(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"symbols": s.mgr.ListSymbols(),
	})
}

func writeJSONError(w http.ResponseWriter, msg string, code int) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": msg})
}
