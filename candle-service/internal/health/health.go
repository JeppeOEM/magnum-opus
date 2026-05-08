package health

import (
	"encoding/json"
	"net/http"
	"runtime"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// HealthState is provided by the service at each /health request.
type HealthState struct {
	ConsumerLagMax    int64
	QuestDBWriteState string // "ok" or "suspended"
	ShadowLag         int64  // 0 after blue-green promotion
}

// StateFunc returns the current health state. Must return quickly (< 1ms).
type StateFunc func() HealthState

// Response is the JSON body returned by GET /health.
type Response struct {
	Status            string `json:"status"`
	Slot              string `json:"slot"`
	Version           string `json:"version"`
	ShadowLag         int64  `json:"shadow_lag"`
	ConsumerLagMax    int64  `json:"consumer_lag_max"`
	QuestDBWriteState string `json:"questdb_write_state"`
}

// Server serves /health, /version, and optionally /metrics over HTTP.
type Server struct {
	slot      string
	version   string
	gitSHA    string
	buildTime string
	state     StateFunc
	mux       *http.ServeMux
}

// New creates a Server. slot, version, gitSHA, buildTime are embedded in responses.
// state provides live health data; use a func returning zero HealthState for stubs.
func New(slot, version, gitSHA, buildTime string, state StateFunc) *Server {
	s := &Server{
		slot:      slot,
		version:   version,
		gitSHA:    gitSHA,
		buildTime: buildTime,
		state:     state,
		mux:       http.NewServeMux(),
	}
	s.mux.HandleFunc("/health", s.handleHealth)
	s.mux.HandleFunc("/version", s.handleVersion)
	return s
}

// WithMetrics wires the /metrics endpoint using the given Gatherer.
// Must be called before the HTTP server starts.
func (s *Server) WithMetrics(g prometheus.Gatherer) {
	s.mux.Handle("/metrics", promhttp.HandlerFor(g, promhttp.HandlerOpts{}))
}

// Handler returns the HTTP handler for use with http.Server.
func (s *Server) Handler() http.Handler { return s.mux }

func (s *Server) handleHealth(w http.ResponseWriter, _ *http.Request) {
	hs := s.state()
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(Response{
		Status:            deriveStatus(hs.ConsumerLagMax, hs.QuestDBWriteState),
		Slot:              s.slot,
		Version:           s.version,
		ShadowLag:         hs.ShadowLag,
		ConsumerLagMax:    hs.ConsumerLagMax,
		QuestDBWriteState: hs.QuestDBWriteState,
	})
}

func (s *Server) handleVersion(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]string{
		"version":    s.version,
		"git_sha":    s.gitSHA,
		"build_time": s.buildTime,
		"go_version": runtime.Version(),
	})
}

// deriveStatus converts lag and WAL state to a health status string.
func deriveStatus(lagMax int64, walState string) string {
	if lagMax > 10_000 {
		return "critical"
	}
	if lagMax > 1_000 || walState == "suspended" {
		return "degraded"
	}
	return "ok"
}
