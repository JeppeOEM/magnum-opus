package httpapi_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/mrqdt/magnum-opus/aggregator/internal/gapwindow"
	"github.com/mrqdt/magnum-opus/aggregator/internal/httpapi"
)

// fakeFeedStatus is a test double for FeedStatusReader.
type fakeFeedStatus struct {
	connected, total int
}

func (f *fakeFeedStatus) FeedCounts() (int, int) { return f.connected, f.total }

func testServer(t *testing.T, feeds httpapi.FeedStatusReader, win *gapwindow.Window, start time.Time) *httpapi.Server {
	t.Helper()
	reg := prometheus.NewRegistry()
	ver := httpapi.VersionInfo{Version: "0.0.1", GitSHA: "abc123", BuildTime: "2026-05-07T00:00:00Z"}
	return httpapi.New(":0", feeds, win, reg, start, ver)
}

func getJSON(t *testing.T, srv *httpapi.Server, path string) (int, map[string]any) {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, path, nil)
	rr := httptest.NewRecorder()
	srv.Handler().ServeHTTP(rr, req)
	var body map[string]any
	require.NoError(t, json.NewDecoder(rr.Body).Decode(&body), "response must be valid JSON")
	return rr.Code, body
}

func TestHealth_AllConnectedNoGaps(t *testing.T) {
	srv := testServer(t, &fakeFeedStatus{connected: 2, total: 2}, gapwindow.New(), time.Now())
	code, body := getJSON(t, srv, "/health")
	assert.Equal(t, http.StatusOK, code)
	assert.Equal(t, "ok", body["status"])
	assert.Equal(t, float64(2), body["connected_feeds"])
	assert.Equal(t, float64(0), body["gap_count_24h"])
}

func TestHealth_FeedDegraded(t *testing.T) {
	srv := testServer(t, &fakeFeedStatus{connected: 1, total: 2}, gapwindow.New(), time.Now())
	_, body := getJSON(t, srv, "/health")
	assert.Equal(t, "degraded", body["status"])
	assert.Equal(t, float64(1), body["connected_feeds"])
}

func TestHealth_InternalGapCritical(t *testing.T) {
	win := gapwindow.New()
	win.Add("internal_merge_error", time.Now().Add(-1*time.Hour))
	srv := testServer(t, &fakeFeedStatus{connected: 2, total: 2}, win, time.Now())
	_, body := getJSON(t, srv, "/health")
	assert.Equal(t, "critical", body["status"])
	assert.Equal(t, float64(1), body["gap_count_24h"])
}

func TestHealth_CriticalWinsDegraded(t *testing.T) {
	win := gapwindow.New()
	win.Add("internal_merge_error", time.Now().Add(-30*time.Minute))
	// connected < total AND internal gap: critical must win
	srv := testServer(t, &fakeFeedStatus{connected: 0, total: 2}, win, time.Now())
	_, body := getJSON(t, srv, "/health")
	assert.Equal(t, "critical", body["status"])
}

func TestHealth_UptimeIncreases(t *testing.T) {
	start := time.Now().Add(-10 * time.Second)
	srv := testServer(t, &fakeFeedStatus{connected: 1, total: 1}, gapwindow.New(), start)
	_, body := getJSON(t, srv, "/health")
	uptime, ok := body["uptime_seconds"].(float64)
	require.True(t, ok, "uptime_seconds must be numeric")
	assert.GreaterOrEqual(t, uptime, float64(10), "uptime must reflect elapsed time from start")
}

func TestHealth_ZeroFeeds_StatusOk(t *testing.T) {
	// zero/zero should not report degraded
	srv := testServer(t, &fakeFeedStatus{connected: 0, total: 0}, gapwindow.New(), time.Now())
	_, body := getJSON(t, srv, "/health")
	assert.Equal(t, "ok", body["status"])
}

func TestVersion_ReturnsInfo(t *testing.T) {
	reg := prometheus.NewRegistry()
	ver := httpapi.VersionInfo{Version: "1.2.3", GitSHA: "deadbeef", BuildTime: "2026-05-07T12:00:00Z"}
	srv := httpapi.New(":0", &fakeFeedStatus{}, gapwindow.New(), reg, time.Now(), ver)

	code, body := getJSON(t, srv, "/version")
	assert.Equal(t, http.StatusOK, code)
	assert.Equal(t, "1.2.3", body["version"])
	assert.Equal(t, "deadbeef", body["git_sha"])
	assert.Equal(t, "2026-05-07T12:00:00Z", body["build_time"])
	goVer, ok := body["go_version"].(string)
	require.True(t, ok, "go_version must be a string")
	assert.True(t, strings.HasPrefix(goVer, "go"), "go_version must start with 'go'")
}

func TestMetrics_PrometheusFormat(t *testing.T) {
	reg := prometheus.NewRegistry()
	// Register one metric so the response is non-empty
	c := prometheus.NewCounter(prometheus.CounterOpts{Name: "test_counter_total", Help: "test"})
	reg.MustRegister(c)
	c.Inc()

	srv := httpapi.New(":0", &fakeFeedStatus{}, gapwindow.New(), reg, time.Now(), httpapi.VersionInfo{})
	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	rr := httptest.NewRecorder()
	srv.Handler().ServeHTTP(rr, req)

	assert.Equal(t, http.StatusOK, rr.Code)
	ct := rr.Header().Get("Content-Type")
	assert.True(t, strings.Contains(ct, "text/plain"), "Content-Type must be text/plain, got: %s", ct)
	assert.Contains(t, rr.Body.String(), "test_counter_total")
}

func TestHealth_StartingStatus(t *testing.T) {
	// isReady returns false → status must be "starting"
	srv := testServer(t, &fakeFeedStatus{connected: 2, total: 2}, gapwindow.New(), time.Now())
	srv.WithReadyFn(func() bool { return false })
	_, body := getJSON(t, srv, "/health")
	assert.Equal(t, "starting", body["status"])
}

func TestHealth_StartingClearsOnReady(t *testing.T) {
	srv := testServer(t, &fakeFeedStatus{connected: 2, total: 2}, gapwindow.New(), time.Now())
	ready := false
	srv.WithReadyFn(func() bool { return ready })
	_, body := getJSON(t, srv, "/health")
	assert.Equal(t, "starting", body["status"])

	ready = true
	_, body = getJSON(t, srv, "/health")
	assert.Equal(t, "ok", body["status"])
}

func TestGathererFeedStatus_CountsFromPrometheus(t *testing.T) {
	reg := prometheus.NewRegistry()
	g := prometheus.NewGaugeVec(prometheus.GaugeOpts{
		Name: "aggregator_feed_state",
		Help: "test",
	}, []string{"exchange", "symbol"})
	reg.MustRegister(g)

	g.WithLabelValues("kucoin", "BTC-USDT").Set(1)
	g.WithLabelValues("bybit", "BTC-USDT").Set(0)
	g.WithLabelValues("kucoin", "ETH-USDT").Set(1)

	fs := httpapi.NewGathererFeedStatus(reg)
	connected, total := fs.FeedCounts()
	assert.Equal(t, 2, connected, "two feeds are live")
	assert.Equal(t, 3, total, "three feeds total")
}
