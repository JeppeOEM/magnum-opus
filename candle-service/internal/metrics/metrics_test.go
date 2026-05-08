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

func metricNames(mfs []*dto.MetricFamily) map[string]bool {
	out := make(map[string]bool, len(mfs))
	for _, mf := range mfs {
		out[mf.GetName()] = true
	}
	return out
}
