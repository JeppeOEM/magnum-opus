package consumer

import (
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestParseMessage_Tick(t *testing.T) {
	fields := map[string]any{
		"event_type": "tick",
		"seq":        "1234",
		"ts":         "1700000000000",
		"price":      "42000.50",
		"size":       "0.5",
		"side":       "buy",
		"level":      "0",
	}
	msg := parseMessage(fields)
	require.Equal(t, EventTick, msg.Type)
	require.NotNil(t, msg.Tick)
	assert.Equal(t, int64(1234), msg.Tick.Seq)
	assert.Equal(t, int64(1700000000000), msg.Tick.TsMs)
	assert.Equal(t, "42000.50", msg.Tick.Price)
	assert.Equal(t, "0.5", msg.Tick.Size)
	assert.Equal(t, "buy", msg.Tick.Side)
	assert.Equal(t, 0, msg.Tick.Level)
	assert.Nil(t, msg.Gap)
	assert.Nil(t, msg.Snapshot)
}

func TestParseMessage_Tick_OBLevel(t *testing.T) {
	fields := map[string]any{
		"event_type": "tick",
		"seq":        "5",
		"ts":         "100",
		"price":      "100.0",
		"size":       "1.0",
		"side":       "sell",
		"level":      "3",
	}
	msg := parseMessage(fields)
	require.Equal(t, EventTick, msg.Type)
	assert.Equal(t, 3, msg.Tick.Level)
}

func TestParseMessage_Gap(t *testing.T) {
	fields := map[string]any{
		"event_type": "gap",
		"seq_before": "100",
		"seq_after":  "200",
		"gap_cause":  "external_disconnect",
		"gap_ts":     "1700000001000",
		"exchange":   "kucoin",
		"symbol":     "BTC-USDT",
	}
	msg := parseMessage(fields)
	require.Equal(t, EventGap, msg.Type)
	require.NotNil(t, msg.Gap)
	assert.Equal(t, int64(100), msg.Gap.SeqBefore)
	assert.Equal(t, int64(200), msg.Gap.SeqAfter)
	assert.Equal(t, "external_disconnect", msg.Gap.GapCause)
	assert.Equal(t, int64(1700000001000), msg.Gap.GapTsMs)
	assert.Equal(t, "kucoin", msg.Gap.Exchange)
	assert.Equal(t, "BTC-USDT", msg.Gap.Symbol)
}

func TestParseMessage_Snapshot(t *testing.T) {
	fields := map[string]any{
		"event_type": "snapshot",
		"seq":        "999",
		"ts":         "1700000002000",
	}
	msg := parseMessage(fields)
	require.Equal(t, EventSnapshot, msg.Type)
	require.NotNil(t, msg.Snapshot)
	assert.Equal(t, int64(999), msg.Snapshot.Seq)
	assert.Equal(t, int64(1700000002000), msg.Snapshot.TsMs)
}

func TestParseMessage_Unknown(t *testing.T) {
	fields := map[string]any{
		"event_type": "heartbeat",
	}
	msg := parseMessage(fields)
	assert.Equal(t, EventUnknown, msg.Type)
	assert.Nil(t, msg.Tick)
	assert.Nil(t, msg.Gap)
	assert.Nil(t, msg.Snapshot)
}

func TestParseMessage_MissingEventType(t *testing.T) {
	msg := parseMessage(map[string]any{})
	assert.Equal(t, EventUnknown, msg.Type)
}

func TestParseMessage_ZeroVolumeTrade(t *testing.T) {
	fields := map[string]any{
		"event_type": "tick",
		"seq":        "1",
		"ts":         "100",
		"price":      "50000",
		"size":       "0",
		"side":       "buy",
		"level":      "0",
	}
	msg := parseMessage(fields)
	require.Equal(t, EventTick, msg.Type)
	// size "0" with level 0 signals a zero-volume trade — consumer discards it
	assert.Equal(t, "0", msg.Tick.Size)
	assert.Equal(t, 0, msg.Tick.Level)
}

func TestGapDedupKey_Unique(t *testing.T) {
	g1 := GapMarker{SeqBefore: 1, SeqAfter: 2, GapCause: "external_disconnect"}
	g2 := GapMarker{SeqBefore: 1, SeqAfter: 2, GapCause: "feed_restart"}
	g3 := GapMarker{SeqBefore: 1, SeqAfter: 3, GapCause: "external_disconnect"}
	assert.NotEqual(t, gapDedupKey(g1), gapDedupKey(g2))
	assert.NotEqual(t, gapDedupKey(g1), gapDedupKey(g3))
}

func TestGapDedupKey_Deterministic(t *testing.T) {
	g := GapMarker{SeqBefore: 100, SeqAfter: 200, GapCause: "seq_discontinuity"}
	assert.Equal(t, gapDedupKey(g), gapDedupKey(g))
}
