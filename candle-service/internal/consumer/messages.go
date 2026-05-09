package consumer

import "strconv"

// EventType identifies the kind of message read from a ticks stream.
type EventType int

const (
	EventUnknown  EventType = iota
	EventTick               // order book update or trade
	EventGap                // gap marker emitted by aggregator
	EventSnapshot           // snapshot arrival signal
)

// Tick represents a single tick event (trade or OB update) from the stream.
type Tick struct {
	Seq   int64
	TsMs  int64
	Price string // decimal string — never float64
	Size  string // decimal string — never float64
	Side  string // "buy" or "sell"
	Level int    // 0 = trade, >0 = OB level
}

// GapMarker is a gap event emitted by the aggregator for a detected discontinuity.
type GapMarker struct {
	SeqBefore int64
	SeqAfter  int64
	GapCause  string
	GapTsMs   int64
	Exchange  string
	Symbol    string
}

// SnapshotEvent signals that a fresh OB snapshot has arrived.
type SnapshotEvent struct {
	Seq  int64
	TsMs int64
}

// ParsedMessage is the discriminated-union result of parseMessage.
type ParsedMessage struct {
	Type     EventType
	Tick     *Tick
	Gap      *GapMarker
	Snapshot *SnapshotEvent
}

// parseMessage converts a raw Redis stream field map into a ParsedMessage.
// All field values in Redis streams are strings.
//
// Wire format (aggregator schema v1):
//
//	Tick:     type="tick",     event_type="update"|"trade", ts_exchange=<unix ms>
//	Gap:      type="gap"       (no event_type field)
//	Snapshot: type="snapshot"  (not written by aggregator; reserved for future use)
func parseMessage(fields map[string]any) ParsedMessage {
	msgType := getString(fields["type"])
	evtType := getString(fields["event_type"])

	switch msgType {
	case "tick":
		level := 1 // OB update
		if evtType == "trade" {
			level = 0
		}
		return ParsedMessage{
			Type: EventTick,
			Tick: &Tick{
				Seq:   parseInt64(fields["seq"]),
				TsMs:  parseInt64(fields["ts_exchange"]),
				Price: getString(fields["price"]),
				Size:  getString(fields["size"]),
				Side:  getString(fields["side"]),
				Level: level,
			},
		}
	case "gap":
		return ParsedMessage{
			Type: EventGap,
			Gap: &GapMarker{
				SeqBefore: parseInt64(fields["seq_before"]),
				SeqAfter:  parseInt64(fields["seq_after"]),
				GapCause:  getString(fields["gap_cause"]),
				GapTsMs:   parseInt64(fields["gap_ts"]),
				Exchange:  getString(fields["exchange"]),
				Symbol:    getString(fields["symbol"]),
			},
		}
	case "snapshot":
		return ParsedMessage{
			Type: EventSnapshot,
			Snapshot: &SnapshotEvent{
				Seq:  parseInt64(fields["seq"]),
				TsMs: parseInt64(fields["ts"]),
			},
		}
	default:
		return ParsedMessage{Type: EventUnknown}
	}
}

func getString(v any) string {
	s, _ := v.(string)
	return s
}

func parseInt64(v any) int64 {
	switch x := v.(type) {
	case string:
		n, _ := strconv.ParseInt(x, 10, 64)
		return n
	case int64:
		return x
	case float64:
		return int64(x)
	}
	return 0
}
