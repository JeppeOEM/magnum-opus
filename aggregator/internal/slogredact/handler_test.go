package slogredact_test

import (
	"bytes"
	"context"
	"log/slog"
	"strings"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/mrqdt/magnum-opus/aggregator/internal/slogredact"
)

func newLogger(buf *bytes.Buffer, sensitive []string) *slog.Logger {
	inner := slog.NewTextHandler(buf, &slog.HandlerOptions{Level: slog.LevelDebug})
	return slog.New(slogredact.New(inner, sensitive))
}

func TestHandler_RedactsAPIKey(t *testing.T) {
	var buf bytes.Buffer
	log := newLogger(&buf, nil)
	log.Info("connecting", "api_key", "sk-live-ABCDEF123456")
	out := buf.String()
	assert.Contains(t, out, "[REDACTED]", "api_key value must be redacted")
	assert.NotContains(t, out, "ABCDEF123456", "raw key must not appear in log")
}

func TestHandler_RedactsSecret(t *testing.T) {
	var buf bytes.Buffer
	log := newLogger(&buf, nil)
	log.Info("auth", "api_secret", "supersecretvalue")
	out := buf.String()
	assert.Contains(t, out, "[REDACTED]")
	assert.NotContains(t, out, "supersecretvalue")
}

func TestHandler_RedactsPassword(t *testing.T) {
	var buf bytes.Buffer
	log := newLogger(&buf, nil)
	log.Info("db", "password", "hunter2")
	assert.Contains(t, buf.String(), "[REDACTED]")
	assert.NotContains(t, buf.String(), "hunter2")
}

func TestHandler_PassesThroughNonSensitive(t *testing.T) {
	var buf bytes.Buffer
	log := newLogger(&buf, nil)
	log.Info("tick", "exchange", "kucoin", "symbol", "BTC-USDT")
	out := buf.String()
	assert.Contains(t, out, "kucoin", "non-sensitive value must pass through")
	assert.Contains(t, out, "BTC-USDT")
}

func TestHandler_RedactsWithAttrs(t *testing.T) {
	var buf bytes.Buffer
	inner := slog.NewTextHandler(&buf, nil)
	h := slogredact.New(inner, nil)
	// WithAttrs should also redact
	child := slog.New(h.WithAttrs([]slog.Attr{slog.String("api_key", "leaked-value")}))
	child.Info("test")
	out := buf.String()
	assert.Contains(t, out, "[REDACTED]")
	assert.NotContains(t, out, "leaked-value")
}

// fakeCredential mimics config.Credential's LogValue() — implements slog.LogValuer.
type fakeCredential struct{ raw string }

func (c fakeCredential) LogValue() slog.Value { return slog.StringValue("[REDACTED]") }

func TestHandler_RespectsLogValuer(t *testing.T) {
	var buf bytes.Buffer
	// Use custom sensitive list that does NOT match "cred" — relies solely on LogValue()
	log := newLogger(&buf, []string{"xxx-no-match"})
	cred := fakeCredential{raw: "supersecret123"}
	log.Info("auth", "credential", cred)
	out := buf.String()
	// Key "credential" doesn't match "xxx-no-match", but LogValue() returns "[REDACTED]"
	assert.Contains(t, out, "[REDACTED]", "LogValuer must be resolved to redacted value")
	assert.NotContains(t, out, "supersecret123")
}

func TestHandler_EnabledDelegates(t *testing.T) {
	inner := slog.NewTextHandler(&bytes.Buffer{}, &slog.HandlerOptions{Level: slog.LevelWarn})
	h := slogredact.New(inner, nil)
	assert.False(t, h.Enabled(context.Background(), slog.LevelDebug))
	assert.True(t, h.Enabled(context.Background(), slog.LevelWarn))
}

func TestHandler_WithGroup(t *testing.T) {
	var buf bytes.Buffer
	inner := slog.NewTextHandler(&buf, nil)
	h := slogredact.New(inner, nil)
	log := slog.New(h.WithGroup("auth"))
	log.Info("test", "api_key", "leaked")
	out := buf.String()
	require.True(t, strings.Contains(out, "[REDACTED]"), "key within group must be redacted: %s", out)
	assert.NotContains(t, out, "leaked")
}
