// Package codec encodes DepthPayload JSON to the binary format expected by the
// frontend WebSocket client (frontend/src/ws.ts) and injects type fields into
// candles1s JSON payloads.
package codec

import (
	"encoding/binary"
	"encoding/json"
	"errors"
	"log/slog"
	"math"
	"strconv"
)

const msgSnapshot = 0x01

// depthPayload holds the fields we need from the DepthPayload JSON published
// by the aggregator to orderbook:{exchange}:{symbol}.
type depthPayload struct {
	TsNs int64      `json:"ts_ns"`
	Bids [][]string `json:"bids"`
	Asks [][]string `json:"asks"`
}

type levelEntry struct {
	price float64
	size  float64
}

// EncodeBinary decodes a DepthPayload JSON and produces a binary frame matching
// the decode logic in frontend/src/ws.ts:
//
//	byte  0:      0x01 (MSG_SNAPSHOT)
//	bytes 1-8:    ts_ns as float64 big-endian
//	bytes 9-10:   bidCount as uint16 little-endian
//	bytes 11-12:  askCount as uint16 little-endian
//	per level:    price float64 BE (8 bytes) + size float32 BE (4 bytes)
//	              bids first, then asks
func EncodeBinary(payload []byte) ([]byte, error) {
	var dp depthPayload
	if err := json.Unmarshal(payload, &dp); err != nil {
		return nil, err
	}

	bids := parseLevels(dp.Bids)
	asks := parseLevels(dp.Asks)

	if len(bids) > 0xFFFF || len(asks) > 0xFFFF {
		return nil, errors.New("level count exceeds uint16 max")
	}

	// 13 byte header + 12 bytes per level
	buf := make([]byte, 13+(len(bids)+len(asks))*12)

	buf[0] = msgSnapshot
	binary.BigEndian.PutUint64(buf[1:9], math.Float64bits(float64(dp.TsNs)))
	binary.LittleEndian.PutUint16(buf[9:11], uint16(len(bids)))
	binary.LittleEndian.PutUint16(buf[11:13], uint16(len(asks)))

	off := 13
	for _, lv := range append(bids, asks...) {
		binary.BigEndian.PutUint64(buf[off:off+8], math.Float64bits(lv.price))
		binary.BigEndian.PutUint32(buf[off+8:off+12], math.Float32bits(float32(lv.size)))
		off += 12
	}

	return buf, nil
}

// InjectType injects a "type" field into a JSON object payload.
// Used to tag candles1s messages before forwarding as WebSocket text frames.
func InjectType(payload []byte, msgType string) ([]byte, error) {
	var m map[string]any
	if err := json.Unmarshal(payload, &m); err != nil {
		return nil, err
	}
	m["type"] = msgType
	return json.Marshal(m)
}

// parseLevels converts a [][]string (each inner = [price_str, size_str]) to
// []levelEntry. Unparseable pairs are silently skipped.
func parseLevels(raw [][]string) []levelEntry {
	out := make([]levelEntry, 0, len(raw))
	for _, pair := range raw {
		if len(pair) < 2 {
			slog.Warn("gateway: parseLevels skipping malformed pair", "len", len(pair))
			continue
		}
		p, errP := strconv.ParseFloat(pair[0], 64)
		s, errS := strconv.ParseFloat(pair[1], 64)
		if errP != nil || errS != nil {
			slog.Warn("gateway: parseLevels skipping unparseable pair", "pair", pair)
			continue
		}
		out = append(out, levelEntry{price: p, size: s})
	}
	return out
}
