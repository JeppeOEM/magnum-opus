package metrics_test

import (
	"fmt"
	"strings"
	"testing"

	dto "github.com/prometheus/client_model/go"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/mrqdt/magnum-opus/aggregator/internal/metrics"
)

func TestRegistry_AllMetricsRegistered(t *testing.T) {
	r := prometheus.NewRegistry()
	reg := metrics.New(r)

	// Trigger series creation so Gather returns non-empty families.
	reg.PreInit([]metrics.SymbolKey{{Exchange: "test", Symbol: "SYM"}})
	// Observe one value so the histogram family appears.
	reg.QuestDBWriteLatencyMs.Observe(1.0)

	mfs, err := r.Gather()
	require.NoError(t, err)

	got := make(map[string]bool, len(mfs))
	for _, mf := range mfs {
		got[mf.GetName()] = true
	}

	want := []string{
		"aggregator_ticks_total",
		"aggregator_gap_total",
		"aggregator_feed_state",
		"aggregator_consumer_lag_ms",
		"aggregator_questdb_write_latency_ms",
	}
	for _, name := range want {
		assert.True(t, got[name], "metric %q must be registered and gatherable", name)
	}
}

func TestRegistry_PreInit_LabelsPresent(t *testing.T) {
	r := prometheus.NewRegistry()
	reg := metrics.New(r)

	reg.PreInit([]metrics.SymbolKey{
		{Exchange: "bybit", Symbol: "BTC-USDT"},
		{Exchange: "kucoin", Symbol: "ETH-USDT"},
	})

	mfs, err := r.Gather()
	require.NoError(t, err)

	byName := make(map[string]map[string]float64)
	for _, mf := range mfs {
		series := make(map[string]float64)
		for _, m := range mf.GetMetric() {
			key := labelsKey(m.GetLabel())
			switch {
			case m.GetGauge() != nil:
				series[key] = m.GetGauge().GetValue()
			case m.GetCounter() != nil:
				series[key] = m.GetCounter().GetValue()
			}
		}
		byName[mf.GetName()] = series
	}

	feedState, ok := byName["aggregator_feed_state"]
	require.True(t, ok, "aggregator_feed_state must be present after PreInit")
	bybitVal, found := feedState[`exchange="bybit",symbol="BTC-USDT"`]
	assert.True(t, found, "bybit/BTC-USDT feed_state series must exist")
	assert.Equal(t, 0.0, bybitVal, "feed_state must be 0 before any feed connects")

	ticksTotal, ok := byName["aggregator_ticks_total"]
	require.True(t, ok, "aggregator_ticks_total must be present after PreInit")
	_, found = ticksTotal[`exchange="kucoin",symbol="ETH-USDT"`]
	assert.True(t, found, "kucoin/ETH-USDT ticks_total series must exist")
}

func labelsKey(labels []*dto.LabelPair) string {
	var b strings.Builder
	for i, lp := range labels {
		if i > 0 {
			b.WriteString(",")
		}
		fmt.Fprintf(&b, "%s=%q", lp.GetName(), lp.GetValue())
	}
	return b.String()
}
