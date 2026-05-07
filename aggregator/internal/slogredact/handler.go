// Package slogredact provides a slog.Handler wrapper that redacts sensitive attribute values.
// Any attribute whose key (case-insensitive) contains a configured substring has its value
// replaced with "[REDACTED]". This provides defense-in-depth beyond config.Credential.LogValue().
package slogredact

import (
	"context"
	"log/slog"
	"strings"
)

// DefaultSensitive is the default set of key substrings that trigger redaction.
var DefaultSensitive = []string{"key", "secret", "password", "passphrase", "token"}

// Handler wraps another slog.Handler and redacts sensitive attribute values.
type Handler struct {
	inner     slog.Handler
	sensitive []string // lowercase substrings; attr keys containing any of these are redacted
}

// New wraps inner with redaction for the given sensitive key substrings.
// Pass nil for sensitive to use DefaultSensitive.
func New(inner slog.Handler, sensitive []string) *Handler {
	s := sensitive
	if s == nil {
		s = DefaultSensitive
	}
	return &Handler{inner: inner, sensitive: s}
}

// Enabled implements slog.Handler.
func (h *Handler) Enabled(ctx context.Context, level slog.Level) bool {
	return h.inner.Enabled(ctx, level)
}

// Handle implements slog.Handler. It clones the record, redacting sensitive attributes.
func (h *Handler) Handle(ctx context.Context, r slog.Record) error {
	r2 := slog.NewRecord(r.Time, r.Level, r.Message, r.PC)
	r.Attrs(func(a slog.Attr) bool {
		r2.AddAttrs(h.redactAttr(a))
		return true
	})
	return h.inner.Handle(ctx, r2)
}

// WithAttrs implements slog.Handler. Redacts sensitive attrs before delegating.
func (h *Handler) WithAttrs(attrs []slog.Attr) slog.Handler {
	redacted := make([]slog.Attr, len(attrs))
	for i, a := range attrs {
		redacted[i] = h.redactAttr(a)
	}
	return &Handler{inner: h.inner.WithAttrs(redacted), sensitive: h.sensitive}
}

// WithGroup implements slog.Handler.
func (h *Handler) WithGroup(name string) slog.Handler {
	return &Handler{inner: h.inner.WithGroup(name), sensitive: h.sensitive}
}

func (h *Handler) redactAttr(a slog.Attr) slog.Attr {
	lower := strings.ToLower(a.Key)
	for _, s := range h.sensitive {
		if strings.Contains(lower, s) {
			return slog.Attr{Key: a.Key, Value: slog.StringValue("[REDACTED]")}
		}
	}
	// Resolve LogValuer (e.g. config.Credential returns "[REDACTED]" via LogValue()).
	a.Value = a.Value.Resolve()
	return a
}
