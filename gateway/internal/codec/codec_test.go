package codec

import (
	"encoding/binary"
	"encoding/json"
	"math"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func mustJSON(v any) []byte {
	b, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	return b
}

func TestEncodeBinary_TypeByte(t *testing.T) {
	payload := mustJSON(map[string]any{
		"ts_ns": int64(1_700_000_000_000_000_000),
		"bids":  [][]string{{"29500.0", "1.5"}},
		"asks":  [][]string{{"29501.0", "0.5"}},
	})
	b, err := EncodeBinary(payload)
	require.NoError(t, err)
	require.GreaterOrEqual(t, len(b), 1)
	assert.Equal(t, byte(0x01), b[0], "first byte must be MSG_SNAPSHOT = 0x01")
}

func TestEncodeBinary_LevelCounts(t *testing.T) {
	payload := mustJSON(map[string]any{
		"ts_ns": int64(0),
		"bids":  [][]string{{"100.0", "1.0"}, {"99.0", "2.0"}},
		"asks":  [][]string{{"101.0", "0.5"}, {"102.0", "0.3"}, {"103.0", "0.1"}},
	})
	b, err := EncodeBinary(payload)
	require.NoError(t, err)
	require.GreaterOrEqual(t, len(b), 13)

	bidCount := binary.LittleEndian.Uint16(b[9:11])
	askCount := binary.LittleEndian.Uint16(b[11:13])
	assert.Equal(t, uint16(2), bidCount, "bidCount should be 2")
	assert.Equal(t, uint16(3), askCount, "askCount should be 3")

	// total size: 13 + (2+3)*12 = 73
	assert.Equal(t, 13+(2+3)*12, len(b))
}

func TestEncodeBinary_EmptyBook(t *testing.T) {
	payload := mustJSON(map[string]any{
		"ts_ns": int64(42),
		"bids":  [][]string{},
		"asks":  [][]string{},
	})
	b, err := EncodeBinary(payload)
	require.NoError(t, err)
	assert.Equal(t, 13, len(b), "header only (13 bytes) for empty book")
	assert.Equal(t, uint16(0), binary.LittleEndian.Uint16(b[9:11]))
	assert.Equal(t, uint16(0), binary.LittleEndian.Uint16(b[11:13]))
}

func TestEncodeBinary_TsNsEncoding(t *testing.T) {
	tsNs := int64(1_234_567_890_123_456_789)
	payload := mustJSON(map[string]any{
		"ts_ns": tsNs,
		"bids":  [][]string{},
		"asks":  [][]string{},
	})
	b, err := EncodeBinary(payload)
	require.NoError(t, err)
	require.GreaterOrEqual(t, len(b), 9)

	// bytes 1-8: tsNs as float64 big-endian
	bits := binary.BigEndian.Uint64(b[1:9])
	decoded := math.Float64frombits(bits)
	assert.InDelta(t, float64(tsNs), decoded, 1.0, "ts_ns should round-trip through float64")
}

func TestInjectType_Candles1s(t *testing.T) {
	payload := mustJSON(map[string]any{
		"ts_ns": int64(123),
		"close": 29500.0,
		"ofi":   1.23,
	})
	out, err := InjectType(payload, "candles1s")
	require.NoError(t, err)

	var m map[string]any
	require.NoError(t, json.Unmarshal(out, &m))
	assert.Equal(t, "candles1s", m["type"])
	assert.InDelta(t, float64(29500), m["close"], 1e-9, "original fields preserved")
	assert.InDelta(t, 1.23, m["ofi"], 1e-9, "original fields preserved")
}

func TestInjectType_PreservesExistingFields(t *testing.T) {
	payload := mustJSON(map[string]any{
		"exchange": "kucoin",
		"symbol":   "BTC-USDT",
		"ts_ns":    int64(999),
	})
	out, err := InjectType(payload, "candles1s")
	require.NoError(t, err)

	var m map[string]any
	require.NoError(t, json.Unmarshal(out, &m))
	assert.Equal(t, "candles1s", m["type"])
	assert.Equal(t, "kucoin", m["exchange"])
	assert.Equal(t, "BTC-USDT", m["symbol"])
}
