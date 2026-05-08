package health_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/mrqdt/magnum-opus/candle-service/internal/health"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// noopState returns a zero HealthState (lag=0, walState="ok").
func noopState() health.HealthState { return health.HealthState{QuestDBWriteState: "ok"} }

func newTestServer(state health.StateFunc) *health.Server {
	if state == nil {
		state = noopState
	}
	return health.New("blue", "dev", "abc123", "2026-01-01", state)
}

func TestHealth_ReturnsOK(t *testing.T) {
	srv := newTestServer(nil)
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/health", nil)

	srv.Handler().ServeHTTP(rec, req)

	assert.Equal(t, http.StatusOK, rec.Code)

	var resp health.Response
	require.NoError(t, json.NewDecoder(rec.Body).Decode(&resp))
	assert.Equal(t, "ok", resp.Status)
	assert.Equal(t, "blue", resp.Slot)
	assert.Equal(t, "dev", resp.Version)
	assert.Equal(t, int64(0), resp.ShadowLag)
	assert.Equal(t, int64(0), resp.ConsumerLagMax)
	assert.Equal(t, "ok", resp.QuestDBWriteState)
}

func TestHealth_GreenSlot(t *testing.T) {
	srv := health.New("green", "abc123", "sha1", "2026-05-01", noopState)
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/health", nil)

	srv.Handler().ServeHTTP(rec, req)

	var resp health.Response
	require.NoError(t, json.NewDecoder(rec.Body).Decode(&resp))
	assert.Equal(t, "green", resp.Slot)
	assert.Equal(t, "abc123", resp.Version)
}

func TestHealth_Degraded_HighLag(t *testing.T) {
	srv := newTestServer(func() health.HealthState {
		return health.HealthState{ConsumerLagMax: 5000, QuestDBWriteState: "ok"}
	})
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	srv.Handler().ServeHTTP(rec, req)

	var resp health.Response
	require.NoError(t, json.NewDecoder(rec.Body).Decode(&resp))
	assert.Equal(t, "degraded", resp.Status)
	assert.Equal(t, int64(5000), resp.ConsumerLagMax)
}

func TestHealth_Critical_VeryHighLag(t *testing.T) {
	srv := newTestServer(func() health.HealthState {
		return health.HealthState{ConsumerLagMax: 15000, QuestDBWriteState: "ok"}
	})
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	srv.Handler().ServeHTTP(rec, req)

	var resp health.Response
	require.NoError(t, json.NewDecoder(rec.Body).Decode(&resp))
	assert.Equal(t, "critical", resp.Status)
}

func TestHealth_Degraded_WALSuspended(t *testing.T) {
	srv := newTestServer(func() health.HealthState {
		return health.HealthState{ConsumerLagMax: 0, QuestDBWriteState: "suspended"}
	})
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	srv.Handler().ServeHTTP(rec, req)

	var resp health.Response
	require.NoError(t, json.NewDecoder(rec.Body).Decode(&resp))
	assert.Equal(t, "degraded", resp.Status)
	assert.Equal(t, "suspended", resp.QuestDBWriteState)
}

func TestVersion_ReturnsAllFields(t *testing.T) {
	srv := health.New("blue", "v1.2.3", "deadbeef", "2026-05-08", noopState)
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/version", nil)

	srv.Handler().ServeHTTP(rec, req)

	assert.Equal(t, http.StatusOK, rec.Code)
	var body map[string]string
	require.NoError(t, json.NewDecoder(rec.Body).Decode(&body))
	assert.Equal(t, "v1.2.3", body["version"])
	assert.Equal(t, "deadbeef", body["git_sha"])
	assert.Equal(t, "2026-05-08", body["build_time"])
	assert.NotEmpty(t, body["go_version"], "go_version must be populated")
	assert.True(t, strings.HasPrefix(body["go_version"], "go"), "go_version should start with 'go'")
}

func TestHealth_WithMetrics_Returns200(t *testing.T) {
	srv := newTestServer(nil)
	reg := prometheus.NewRegistry()
	srv.WithMetrics(reg)

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	srv.Handler().ServeHTTP(rec, req)

	assert.Equal(t, http.StatusOK, rec.Code)
	assert.Contains(t, rec.Header().Get("Content-Type"), "text/plain")
}
