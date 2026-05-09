package consumer

import (
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// Tests use the actual aggregator wire format (schema v1):
//   type="tick"     + event_type="update"|"trade" + ts_exchange=<unix ms>
//   type="gap"      (no event_type)
//   type="snapshot" (reserved; not written by aggregator in production)

func TestParseMessage_Tick_OBUpdate(t *testing.T) {
	fields := map[string]any{
		"type":        "tick",
		"event_type":  "update",
		"seq":         "1234",
		"ts_exchange": "1700000000000",
		"ts_local":    "1700000000001",
		"price":       "42000.50",
		"size":        "0.5",
		"side":        "buy",
	}
	msg := parseMessage(fields)
	require.Equal(t, EventTick, msg.Type)
	require.NotNil(t, msg.Tick)
	assert.Equal(t, int64(1234), msg.Tick.Seq)
	assert.Equal(t, int64(1700000000000), msg.Tick.TsMs)
	assert.Equal(t, "42000.50", msg.Tick.Price)
	assert.Equal(t, "0.5", msg.Tick.Size)
	assert.Equal(t, "buy", msg.Tick.Side)
	assert.Equal(t, 1, msg.Tick.Level) // OB update → Level=1 (non-zero → isTrade=false)
	assert.Nil(t, msg.Gap)
	assert.Nil(t, msg.Snapshot)
}

func TestParseMessage_Tick_Trade(t *testing.T) {
	fields := map[string]any{
		"type":        "tick",
		"event_type":  "trade",
		"seq":         "5678",
		"ts_exchange": "1700000001000",
		"ts_local":    "1700000001001",
		"price":       "42100.00",
		"size":        "1.5",
		"side":        "sell",
	}
	msg := parseMessage(fields)
	require.Equal(t, EventTick, msg.Type)
	require.NotNil(t, msg.Tick)
	assert.Equal(t, int64(5678), msg.Tick.Seq)
	assert.Equal(t, int64(1700000001000), msg.Tick.TsMs)
	assert.Equal(t, "42100.00", msg.Tick.Price)
	assert.Equal(t, "1.5", msg.Tick.Size)
	assert.Equal(t, "sell", msg.Tick.Side)
	assert.Equal(t, 0, msg.Tick.Level) // trade → Level=0 (isTrade=true)
}

func TestParseMessage_Gap(t *testing.T) {
	fields := map[string]any{
		"type":       "gap",
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
		"type": "snapshot",
		"seq":  "999",
		"ts":   "1700000002000",
	}
	msg := parseMessage(fields)
	require.Equal(t, EventSnapshot, msg.Type)
	require.NotNil(t, msg.Snapshot)
	assert.Equal(t, int64(999), msg.Snapshot.Seq)
	assert.Equal(t, int64(1700000002000), msg.Snapshot.TsMs)
}

func TestParseMessage_Unknown(t *testing.T) {
	fields := map[string]any{
		"type": "heartbeat",
	}
	msg := parseMessage(fields)
	assert.Equal(t, EventUnknown, msg.Type)
	assert.Nil(t, msg.Tick)
	assert.Nil(t, msg.Gap)
	assert.Nil(t, msg.Snapshot)
}

func TestParseMessage_MissingType(t *testing.T) {
	msg := parseMessage(map[string]any{})
	assert.Equal(t, EventUnknown, msg.Type)
}

func TestParseMessage_ZeroVolumeTrade(t *testing.T) {
	fields := map[string]any{
		"type":        "tick",
		"event_type":  "trade",
		"seq":         "1",
		"ts_exchange": "100",
		"price":       "50000",
		"size":        "0",
		"side":        "buy",
	}
	msg := parseMessage(fields)
	require.Equal(t, EventTick, msg.Type)
	// size "0" with Level=0 signals a zero-volume trade — consumer discards it
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
