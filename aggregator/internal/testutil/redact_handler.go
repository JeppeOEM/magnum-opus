package testutil

import (
	"context"
	"log/slog"
	"regexp"
)

// credentialPatterns matches common API key/secret formats for redaction in test logs.
// These patterns are intentionally broad to catch test credential values.
var credentialPatterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)(api[_-]?key|api[_-]?secret|passphrase|token|bearer)[^\s]*`),
}

// RedactingHandler is a lightweight slog.Handler wrapper that redacts
// strings matching credential patterns from all log output.
// Active in Epics 1–3 test binaries before Story 4.3 wires the production handler.
type RedactingHandler struct {
	inner slog.Handler
}

// NewRedactingHandler wraps h, redacting credential patterns from all output.
func NewRedactingHandler(h slog.Handler) *RedactingHandler {
	return &RedactingHandler{inner: h}
}

func (r *RedactingHandler) Enabled(ctx context.Context, level slog.Level) bool {
	return r.inner.Enabled(ctx, level)
}

func (r *RedactingHandler) Handle(ctx context.Context, rec slog.Record) error {
	safe := slog.NewRecord(rec.Time, rec.Level, r.redact(rec.Message), rec.PC)
	rec.Attrs(func(a slog.Attr) bool {
		safe.AddAttrs(r.redactAttr(a))
		return true
	})
	return r.inner.Handle(ctx, safe)
}

func (r *RedactingHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	safe := make([]slog.Attr, len(attrs))
	for i, a := range attrs {
		safe[i] = r.redactAttr(a)
	}
	return &RedactingHandler{inner: r.inner.WithAttrs(safe)}
}

func (r *RedactingHandler) WithGroup(name string) slog.Handler {
	return &RedactingHandler{inner: r.inner.WithGroup(name)}
}

func (r *RedactingHandler) redact(s string) string {
	for _, re := range credentialPatterns {
		s = re.ReplaceAllString(s, "[REDACTED]")
	}
	return s
}

func (r *RedactingHandler) redactAttr(a slog.Attr) slog.Attr {
	if a.Value.Kind() == slog.KindString {
		return slog.String(a.Key, r.redact(a.Value.String()))
	}
	return a
}
