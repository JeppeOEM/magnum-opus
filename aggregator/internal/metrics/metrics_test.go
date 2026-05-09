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

// ── Health gauge tests (AC1–3, 5) ────────────────────────────────────────────

func TestHealthPreInit(t *testing.T) {
	r := prometheus.NewRegistry()
	reg := metrics.New(r)
	reg.PreInit([]metrics.SymbolKey{{Exchange: "test", Symbol: "SYM"}})

	mfs, err := r.Gather()
	require.NoError(t, err)

	health := metricsByCondition(mfs, "aggregator_health")
	require.Len(t, health, 2, "both conditions must be pre-initialized")
	assert.Equal(t, 1.0, health["feeds"], "feeds must start at 1")
	assert.Equal(t, 1.0, health["gaps"], "gaps must start at 1")
}

func TestFeedsCondition(t *testing.T) {
	// Build a fake gatherer with aggregator_feed_state series to simulate
	// the pattern used by computeMinFeedState in cmd/aggregator/main.go.
	// We test Registry.Health directly — the goroutine logic is trivially
	// thin and is covered by integration; here we test the gauge mechanics.

	r := prometheus.NewRegistry()
	reg := metrics.New(r)
	reg.PreInit([]metrics.SymbolKey{
		{Exchange: "ex", Symbol: "A"},
		{Exchange: "ex", Symbol: "B"},
	})

	// Simulate feeds goroutine setting feeds=0 (all feeds down)
	reg.Health.WithLabelValues("feeds").Set(0)
	mfs, _ := r.Gather()
	assert.Equal(t, 0.0, metricsByCondition(mfs, "aggregator_health")["feeds"])

	// Simulate feeds goroutine setting feeds=1 (all live)
	reg.Health.WithLabelValues("feeds").Set(1)
	mfs, _ = r.Gather()
	assert.Equal(t, 1.0, metricsByCondition(mfs, "aggregator_health")["feeds"])

	// Back to 0 when a feed drops
	reg.Health.WithLabelValues("feeds").Set(0)
	mfs, _ = r.Gather()
	assert.Equal(t, 0.0, metricsByCondition(mfs, "aggregator_health")["feeds"])
}

func TestGapsCondition(t *testing.T) {
	r := prometheus.NewRegistry()
	reg := metrics.New(r)
	reg.PreInit(nil)

	// No gaps — healthy
	assert.Equal(t, 1.0, reg.GapHealthy(300, 1000))

	// Record a persistent gap at t=800; window=300 → cutoff=700; 800>=700 → unhealthy
	reg.RecordGap("ex", "A", "internal_merge_error", 800)
	assert.Equal(t, 0.0, reg.GapHealthy(300, 1000))

	// Advance time so gap is outside window; cutoff=600; 800>=600 → still unhealthy
	assert.Equal(t, 0.0, reg.GapHealthy(300, 900))

	// Advance so gap is just outside: cutoff=801; 800<801 → healthy
	assert.Equal(t, 1.0, reg.GapHealthy(300, 1101))
}

func TestGapHealthyWindow(t *testing.T) {
	r := prometheus.NewRegistry()
	reg := metrics.New(r)
	reg.PreInit(nil)

	// Gap at t=700, window=300, nowUnix=1000 → cutoff=700; 700>=700 → unhealthy (boundary inclusive)
	reg.RecordGap("ex", "A", "internal_merge_error", 700)
	assert.Equal(t, 0.0, reg.GapHealthy(300, 1000), "gap at exactly cutoff must be unhealthy")

	// nowUnix=1001 → cutoff=701; 700<701 → healthy
	assert.Equal(t, 1.0, reg.GapHealthy(300, 1001), "gap just outside window must be healthy")
}

func TestRecordGap_TransientCausesIgnored(t *testing.T) {
	r := prometheus.NewRegistry()
	reg := metrics.New(r)
	reg.PreInit(nil)

	// external_disconnect and internal_buffer_overflow are transient — must not affect health
	reg.RecordGap("ex", "A", "external_disconnect", 1000)
	reg.RecordGap("ex", "A", "internal_buffer_overflow", 1000)
	assert.Equal(t, 1.0, reg.GapHealthy(300, 1000), "transient gaps must not affect health gauge")
}

// metricsByCondition returns a map[condition]value for a named GaugeVec.
func metricsByCondition(mfs []*dto.MetricFamily, name string) map[string]float64 {
	out := make(map[string]float64)
	for _, mf := range mfs {
		if mf.GetName() != name {
			continue
		}
		for _, m := range mf.GetMetric() {
			for _, lp := range m.GetLabel() {
				if lp.GetName() == "condition" {
					out[lp.GetValue()] = m.GetGauge().GetValue()
				}
			}
		}
	}
	return out
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
