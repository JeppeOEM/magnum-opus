// White-box L1 tests for shadow mode (no build tag, no real Redis or QuestDB).
package consumer

import (
	"io"
	"log/slog"
	"testing"

	"github.com/redis/go-redis/v9"
	"github.com/stretchr/testify/assert"
)

// ── stubs ─────────────────────────────────────────────────────────────────────

type shadowStubBook struct{}

func (s *shadowStubBook) ApplyTick(_ Tick)             {}
func (s *shadowStubBook) ApplyGap(_ GapMarker)          {}
func (s *shadowStubBook) ApplySnapshot(_ SnapshotEvent) {}
func (s *shadowStubBook) BestQuote() (string, string, string, string) {
	return "", "", "", ""
}
func (s *shadowStubBook) HasLevel(_, _ string) bool { return false }

type shadowStubAcc struct {
	flushCalls []bool
	applyCalls int
}

func (s *shadowStubAcc) Apply(_, _ string, _ bool, _ string, _ int64,
	_, _, _, _, _, _, _, _ string) {
	s.applyCalls++
}
func (s *shadowStubAcc) ApplyOBEvent(_ OBEventKind, _ string, _ float64) {}
func (s *shadowStubAcc) Flush(isPartial bool) error {
	s.flushCalls = append(s.flushCalls, isPartial)
	return nil
}
func (s *shadowStubAcc) IncrementGap() {}
func (s *shadowStubAcc) Reset()        {}

// discardLogger returns a slog.Logger that discards all output.
func discardLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

// ── TestShadowModeNoFlush ─────────────────────────────────────────────────────

// TestShadowModeNoFlush verifies that handleShadowMessage never calls Flush
// regardless of the message type.
func TestShadowModeNoFlush(t *testing.T) {
	acc := &shadowStubAcc{}
	c := &Consumer{
		streamKey: "ticks:kucoin:BTC-USDT",
		book:      &shadowStubBook{},
		acc:       acc,
		logger:    discardLogger(),
	}
	c.shadowMode.Store(true)
	c.shadowMu.Lock()
	c.lastShadowID = "0-0"
	c.shadowMu.Unlock()

	msg := redis.XMessage{
		ID: "1000-0",
		Values: map[string]interface{}{
			"event_type": "tick",
			"exchange":   "kucoin",
			"symbol":     "BTC-USDT",
			"side":       "sell",
			"price":      "60000",
			"size":       "1",
			"ts":         "1746000000000",
			"level":      "0",
		},
	}

	c.handleShadowMessage(msg)

	assert.Empty(t, acc.flushCalls, "shadow mode must never call Flush")
	assert.Equal(t, "1000-0", c.LastShadowID(), "lastShadowID must be updated")
	assert.Equal(t, 1, acc.applyCalls, "Apply must be called for trade ticks")
}

// ── TestShadowLagZeroWhenCaughtUp ────────────────────────────────────────────

// TestShadowLagZeroWhenCaughtUp verifies computeShadowLag returns 0 when the
// XRANGE result contains only the lastShadowID entry itself (stream tip).
func TestShadowLagZeroWhenCaughtUp(t *testing.T) {
	// At tip: XRANGE returns 1 entry (the lastShadowID itself) → lag = 0
	assert.Equal(t, int64(0), computeShadowLag(1))
}

func TestShadowLagPositiveWhenBehind(t *testing.T) {
	// 5 entries after lastShadowID + lastShadowID itself = 6 total → lag = 5
	assert.Equal(t, int64(5), computeShadowLag(6))
}

func TestShadowLagZeroWhenNoEntries(t *testing.T) {
	// Stream trimmed past lastShadowID: XRANGE returns 0 entries → clamp to 0
	assert.Equal(t, int64(0), computeShadowLag(0))
}
