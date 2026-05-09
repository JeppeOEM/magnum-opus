// Package metrics defines all named Prometheus metric vars for the candle service.
// Zero internal imports — only stdlib and prometheus client. Never use DefaultRegisterer.
package metrics

import (
	"fmt"
	"sync"

	"github.com/prometheus/client_golang/prometheus"
)

// ExchangeSymbol identifies one (exchange, symbol) trading pair.
type ExchangeSymbol struct {
	Exchange string
	Symbol   string
}

// Metrics holds all named Prometheus vars. Obtain via Register.
type Metrics struct {
	BarsTotal                     *prometheus.CounterVec
	ConsumerLag                   *prometheus.GaugeVec
	QuestDBWriteLatencyMs         prometheus.Histogram
	GapCountTotal                 *prometheus.CounterVec
	FlushSuccessTotal             prometheus.Counter
	FlushFailureTotal             prometheus.Counter
	FlushAlertFailureTotal        prometheus.Counter
	BarCloseDroppedTotal          *prometheus.CounterVec
	WALDropTotal                  prometheus.Counter
	RedisPublishFailureTotal      *prometheus.CounterVec
	CascadeStateWriteFailureTotal *prometheus.CounterVec
	Health                        *prometheus.GaugeVec

	flushFailureMu      sync.Mutex
	lastFlushFailureUnix int64 // Unix seconds; 0 = never failed
}

// Register creates all metrics, registers them with reg, and initializes all
// per-symbol series to 0 for every pair in pairs.
// Returns an error if any metric fails to register (e.g., duplicate name).
func Register(reg prometheus.Registerer, pairs []ExchangeSymbol) (*Metrics, error) {
	m := &Metrics{
		BarsTotal: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "candle_bars_total",
			Help: "Total bars written per (exchange, symbol).",
		}, []string{"exchange", "symbol"}),

		ConsumerLag: prometheus.NewGaugeVec(prometheus.GaugeOpts{
			Name: "candle_consumer_lag",
			Help: "Current Redis Stream pending message count per (exchange, symbol).",
		}, []string{"exchange", "symbol"}),

		QuestDBWriteLatencyMs: prometheus.NewHistogram(prometheus.HistogramOpts{
			Name:    "candle_questdb_write_latency_ms",
			Help:    "Latency of QuestDB ILP write operations in milliseconds.",
			Buckets: []float64{1, 5, 10, 25, 50, 100, 250, 500, 1000},
		}),

		GapCountTotal: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "candle_gap_count_total",
			Help: "Total gap events per (exchange, symbol).",
		}, []string{"exchange", "symbol"}),

		FlushSuccessTotal: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "candle_flush_success_total",
			Help: "Total successful Parquet flush operations.",
		}),

		FlushFailureTotal: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "candle_flush_failure_total",
			Help: "Total failed Parquet flush operations.",
		}),

		FlushAlertFailureTotal: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "candle_flush_alert_failure_total",
			Help: "Total failures writing flush failure alerts to Redis.",
		}),

		BarCloseDroppedTotal: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "candle_bar_close_dropped_total",
			Help: "Bar close signals dropped because the consumer channel was full.",
		}, []string{"exchange", "symbol"}),

		WALDropTotal: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "candle_wal_drop_total",
			Help: "Bars dropped from the WAL buffer during QuestDB suspension.",
		}),

		RedisPublishFailureTotal: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "candle_redis_publish_failure_total",
			Help: "Total XADD failures on candle/ob_features streams.",
		}, []string{"exchange", "symbol"}),

		CascadeStateWriteFailureTotal: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "candle_cascade_state_write_failure_total",
			Help: "Total cascade accumulator Redis write failures.",
		}, []string{"exchange", "symbol"}),

		Health: prometheus.NewGaugeVec(prometheus.GaugeOpts{
			Name: "candle_health",
			Help: "Service health per condition: 1=ok, 0=failing.",
		}, []string{"condition"}),
	}

	collectors := []prometheus.Collector{
		m.BarsTotal,
		m.ConsumerLag,
		m.QuestDBWriteLatencyMs,
		m.GapCountTotal,
		m.FlushSuccessTotal,
		m.FlushFailureTotal,
		m.FlushAlertFailureTotal,
		m.BarCloseDroppedTotal,
		m.WALDropTotal,
		m.RedisPublishFailureTotal,
		m.CascadeStateWriteFailureTotal,
		m.Health,
	}
	for _, c := range collectors {
		if err := reg.Register(c); err != nil {
			return nil, fmt.Errorf("metrics: register: %w", err)
		}
	}

	// Initialize all per-symbol series to 0 so configured-but-silent symbols
	// appear in /metrics output from the first scrape.
	for _, p := range pairs {
		m.BarsTotal.WithLabelValues(p.Exchange, p.Symbol)
		m.ConsumerLag.WithLabelValues(p.Exchange, p.Symbol).Set(0)
		m.GapCountTotal.WithLabelValues(p.Exchange, p.Symbol)
		m.BarCloseDroppedTotal.WithLabelValues(p.Exchange, p.Symbol)
		m.RedisPublishFailureTotal.WithLabelValues(p.Exchange, p.Symbol)
		m.CascadeStateWriteFailureTotal.WithLabelValues(p.Exchange, p.Symbol)
	}
	// Health starts healthy — lag polling goroutine updates every 5s once running.
	m.Health.WithLabelValues("consumer_lag").Set(1)
	m.Health.WithLabelValues("questdb").Set(1)
	m.Health.WithLabelValues("flush").Set(1)

	return m, nil
}

// RecordFlushFailure records that a flush failure occurred at nowUnix.
// nowUnix is time.Now().Unix() from the caller (time.Now() is banned in internal/).
func (m *Metrics) RecordFlushFailure(nowUnix int64) {
	m.flushFailureMu.Lock()
	m.lastFlushFailureUnix = nowUnix
	m.flushFailureMu.Unlock()
}

// FlushHealthy returns 1.0 if no flush failure occurred within windowSecs, 0.0 otherwise.
func (m *Metrics) FlushHealthy(windowSecs, nowUnix int64) float64 {
	m.flushFailureMu.Lock()
	t := m.lastFlushFailureUnix
	m.flushFailureMu.Unlock()
	if t == 0 {
		return 1.0 // never failed
	}
	if nowUnix-t <= windowSecs {
		return 0.0
	}
	return 1.0
}
