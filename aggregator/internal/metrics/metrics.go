// Package metrics defines all Prometheus metrics for the aggregator.
// It is a leaf package — it imports no other internal package.
package metrics

import "github.com/prometheus/client_golang/prometheus"

// SymbolKey identifies a (exchange, symbol) pair without importing internal/symbol.
type SymbolKey struct {
	Exchange string
	Symbol   string
}

// Registry holds all aggregator Prometheus metrics.
// Create with New; call PreInit before coordinator.Run().
type Registry struct {
	TicksTotal             *prometheus.CounterVec
	GapTotal               *prometheus.CounterVec
	FeedState              *prometheus.GaugeVec
	ConsumerLagMs          *prometheus.GaugeVec
	QuestDBWriteLatencyMs  prometheus.Histogram
}

// New creates all metrics and registers them with r.
// Always pass prometheus.NewRegistry() — never prometheus.DefaultRegisterer.
func New(r prometheus.Registerer) *Registry {
	reg := &Registry{
		TicksTotal: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "aggregator_ticks_total",
			Help: "Total ticks processed per exchange and symbol.",
		}, []string{"exchange", "symbol"}),

		GapTotal: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "aggregator_gap_total",
			Help: "Total gap events detected per exchange, symbol, and cause.",
		}, []string{"exchange", "symbol", "cause"}),

		FeedState: prometheus.NewGaugeVec(prometheus.GaugeOpts{
			Name: "aggregator_feed_state",
			Help: "Feed connectivity state: 1=connected and live, 0=disconnected or initializing.",
		}, []string{"exchange", "symbol"}),

		ConsumerLagMs: prometheus.NewGaugeVec(prometheus.GaugeOpts{
			Name: "aggregator_consumer_lag_ms",
			Help: "Redis Stream consumer group lag in milliseconds per exchange and symbol.",
		}, []string{"exchange", "symbol"}),

		QuestDBWriteLatencyMs: prometheus.NewHistogram(prometheus.HistogramOpts{
			Name:    "aggregator_questdb_write_latency_ms",
			Help:    "QuestDB ILP batch write latency in milliseconds.",
			Buckets: prometheus.DefBuckets,
		}),
	}
	r.MustRegister(
		reg.TicksTotal,
		reg.GapTotal,
		reg.FeedState,
		reg.ConsumerLagMs,
		reg.QuestDBWriteLatencyMs,
	)
	return reg
}

// gapCauses lists all valid gap cause label values.
// Pre-initializing all four ensures gap_total is always present in scrapes.
var gapCauses = []string{
	"internal_buffer_overflow",
	"internal_merge_error",
	"external_disconnect",
	"external_rate_limit",
}

// PreInit forces all per-symbol label series to exist at value 0 before any feed connects.
// This guarantees dashboards never show missing series for idle or disconnected feeds (NFR19).
// Call once after New, before coordinator.Run().
func (reg *Registry) PreInit(pairs []SymbolKey) {
	for _, p := range pairs {
		reg.TicksTotal.WithLabelValues(p.Exchange, p.Symbol)
		reg.FeedState.WithLabelValues(p.Exchange, p.Symbol)
		reg.ConsumerLagMs.WithLabelValues(p.Exchange, p.Symbol)
		for _, cause := range gapCauses {
			reg.GapTotal.WithLabelValues(p.Exchange, p.Symbol, cause)
		}
	}
}
