package metrics_test

import (
	"testing"

	"github.com/mrqdt/magnum-opus/candle-service/internal/metrics"
	"github.com/prometheus/client_golang/prometheus"
	dto "github.com/prometheus/client_model/go"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestMetrics_Register_AllMetricsCreated(t *testing.T) {
	reg := prometheus.NewRegistry()
	pairs := []metrics.ExchangeSymbol{{"kucoin", "BTC-USDT"}}
	m, err := metrics.Register(reg, pairs)
	require.NoError(t, err)
	require.NotNil(t, m)

	mfs, err := reg.Gather()
	require.NoError(t, err)
	assert.NotEmpty(t, mfs)

	names := metricNames(mfs)
	for _, want := range []string{
		"candle_bars_total",
		"candle_consumer_lag",
		"candle_questdb_write_latency_ms",
		"candle_gap_count_total",
		"candle_flush_success_total",
		"candle_flush_failure_total",
		"candle_flush_alert_failure_total",
		"candle_bar_close_dropped_total",
		"candle_wal_drop_total",
		"candle_redis_publish_failure_total",
		"candle_cascade_state_write_failure_total",
		"candle_health",
	} {
		assert.True(t, names[want], "expected metric %q to be registered", want)
	}
}

func TestMetrics_PerSymbolInitialized(t *testing.T) {
	reg := prometheus.NewRegistry()
	pairs := []metrics.ExchangeSymbol{
		{"kucoin", "BTC-USDT"},
		{"bybit", "ETH-USDT"},
	}
	_, err := metrics.Register(reg, pairs)
	require.NoError(t, err)

	mfs, err := reg.Gather()
	require.NoError(t, err)

	byName := make(map[string]*dto.MetricFamily, len(mfs))
	for _, mf := range mfs {
		byName[mf.GetName()] = mf
	}

	perSymbol := []string{
		"candle_bars_total",
		"candle_consumer_lag",
		"candle_gap_count_total",
		"candle_bar_close_dropped_total",
		"candle_redis_publish_failure_total",
		"candle_cascade_state_write_failure_total",
	}
	for _, name := range perSymbol {
		mf, ok := byName[name]
		require.True(t, ok, "metric %q not present", name)
		assert.Len(t, mf.GetMetric(), 2, "metric %q should have 2 series (one per symbol)", name)
	}
}

func TestMetrics_DuplicateRegistration_ReturnsError(t *testing.T) {
	reg := prometheus.NewRegistry()
	pairs := []metrics.ExchangeSymbol{{"kucoin", "BTC-USDT"}}
	_, err := metrics.Register(reg, pairs)
	require.NoError(t, err)

	_, err = metrics.Register(reg, pairs)
	assert.Error(t, err, "duplicate registration must return error")
}

func TestMetrics_EmptyPairs_RegistersOK(t *testing.T) {
	reg := prometheus.NewRegistry()
	m, err := metrics.Register(reg, nil)
	require.NoError(t, err)
	require.NotNil(t, m)
	// No per-symbol series yet; global metrics should still be present
	mfs, err := reg.Gather()
	require.NoError(t, err)
	names := metricNames(mfs)
	assert.True(t, names["candle_wal_drop_total"])
}

// ── Health gauge tests ────────────────────────────────────────────────────────

func TestHealthPreInit(t *testing.T) {
	reg := prometheus.NewRegistry()
	_, err := metrics.Register(reg, []metrics.ExchangeSymbol{{"kucoin", "BTC-USDT"}})
	require.NoError(t, err)

	mfs, err := reg.Gather()
	require.NoError(t, err)

	health := healthByCondition(mfs)
	require.Len(t, health, 3, "all three conditions must be pre-initialized")
	assert.Equal(t, 1.0, health["consumer_lag"])
	assert.Equal(t, 1.0, health["questdb"])
	assert.Equal(t, 1.0, health["flush"])

	// Verify candle_health appears in all-metrics list
	names := metricNames(mfs)
	assert.True(t, names["candle_health"])
}

func TestConsumerLagCondition(t *testing.T) {
	reg := prometheus.NewRegistry()
	m, err := metrics.Register(reg, nil)
	require.NoError(t, err)

	// Simulate goroutine: lag high → 0
	m.Health.WithLabelValues("consumer_lag").Set(0)
	mfs, _ := reg.Gather()
	assert.Equal(t, 0.0, healthByCondition(mfs)["consumer_lag"])

	// Lag back to ok → 1
	m.Health.WithLabelValues("consumer_lag").Set(1)
	mfs, _ = reg.Gather()
	assert.Equal(t, 1.0, healthByCondition(mfs)["consumer_lag"])
}

func TestQuestDBCondition(t *testing.T) {
	reg := prometheus.NewRegistry()
	m, err := metrics.Register(reg, nil)
	require.NoError(t, err)

	// WAL suspended → 0
	m.Health.WithLabelValues("questdb").Set(0)
	mfs, _ := reg.Gather()
	assert.Equal(t, 0.0, healthByCondition(mfs)["questdb"])

	// WAL ok → 1
	m.Health.WithLabelValues("questdb").Set(1)
	mfs, _ = reg.Gather()
	assert.Equal(t, 1.0, healthByCondition(mfs)["questdb"])
}


func TestFlushCondition(t *testing.T) {
	reg := prometheus.NewRegistry()
	m, err := metrics.Register(reg, nil)
	require.NoError(t, err)

	// No failure yet → healthy
	assert.Equal(t, 1.0, m.FlushHealthy(900, 1000))

	// Record failure at t=500; window=900; now=1000 → cutoff=100; 500>=100 → unhealthy
	m.RecordFlushFailure(500)
	assert.Equal(t, 0.0, m.FlushHealthy(900, 1000))

	// Advance time past window: now=1401; cutoff=501; 500<501 → healthy
	assert.Equal(t, 1.0, m.FlushHealthy(900, 1401))

	// Boundary: now=1400; cutoff=500; 500>=500 → unhealthy (inclusive lower bound)
	assert.Equal(t, 0.0, m.FlushHealthy(900, 1400))
}

func healthByCondition(mfs []*dto.MetricFamily) map[string]float64 {
	out := make(map[string]float64)
	for _, mf := range mfs {
		if mf.GetName() != "candle_health" {
			continue
		}
		for _, metric := range mf.GetMetric() {
			for _, lp := range metric.GetLabel() {
				if lp.GetName() == "condition" {
					out[lp.GetValue()] = metric.GetGauge().GetValue()
				}
			}
		}
	}
	return out
}

func metricNames(mfs []*dto.MetricFamily) map[string]bool {
	out := make(map[string]bool, len(mfs))
	for _, mf := range mfs {
		out[mf.GetName()] = true
	}
	return out
}
