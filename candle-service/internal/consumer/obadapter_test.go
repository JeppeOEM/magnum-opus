package consumer_test

import (
	"testing"

	"github.com/mrqdt/magnum-opus/candle-service/internal/consumer"
	"github.com/mrqdt/magnum-opus/candle-service/internal/orderbook"
	"github.com/stretchr/testify/assert"
)

func TestOBAdapter_ApplyTick_ConvertFields(t *testing.T) {
	gaps := &[]orderbook.GapMarker{}
	ob := orderbook.New("kucoin", "BTC-USDT", 10, func(g orderbook.GapMarker) {
		*gaps = append(*gaps, g)
	})
	adapter := consumer.NewOBAdapter(ob)

	// Apply snapshot first so book is ready.
	adapter.ApplySnapshot(consumer.SnapshotEvent{Seq: 0, TsMs: 1000})
	// Apply a bid tick.
	adapter.ApplyTick(consumer.Tick{Seq: 1, TsMs: 1001, Price: "50000", Size: "1.5", Side: "buy", Level: 1})
	// Apply an ask tick.
	adapter.ApplyTick(consumer.Tick{Seq: 2, TsMs: 1002, Price: "50100", Size: "0.5", Side: "sell", Level: 1})

	bid, bidSz, ask, askSz := adapter.BestQuote()
	assert.Equal(t, "50000", bid)
	assert.Equal(t, "1.5", bidSz)
	assert.Equal(t, "50100", ask)
	assert.Equal(t, "0.5", askSz)
}

func TestOBAdapter_ApplySnapshot_ResetsBook(t *testing.T) {
	ob := orderbook.New("kucoin", "BTC-USDT", 10, func(orderbook.GapMarker) {})
	adapter := consumer.NewOBAdapter(ob)

	adapter.ApplySnapshot(consumer.SnapshotEvent{Seq: 0})
	adapter.ApplyTick(consumer.Tick{Seq: 1, Price: "50000", Size: "1.5", Side: "buy", Level: 1})
	adapter.ApplyTick(consumer.Tick{Seq: 2, Price: "50100", Size: "0.5", Side: "sell", Level: 1})
	// Second snapshot should reset book.
	adapter.ApplySnapshot(consumer.SnapshotEvent{Seq: 10})

	bid, _, _, _ := adapter.BestQuote()
	assert.Empty(t, bid, "book should be empty after snapshot reset")
}

func TestOBAdapter_ApplyGap_ResetsBook(t *testing.T) {
	ob := orderbook.New("kucoin", "BTC-USDT", 10, func(orderbook.GapMarker) {})
	adapter := consumer.NewOBAdapter(ob)

	adapter.ApplySnapshot(consumer.SnapshotEvent{Seq: 0})
	adapter.ApplyTick(consumer.Tick{Seq: 1, Price: "50000", Size: "1.5", Side: "buy", Level: 1})
	adapter.ApplyGap(consumer.GapMarker{GapCause: "external_disconnect"})

	bid, _, _, _ := adapter.BestQuote()
	assert.Empty(t, bid, "book should be empty after gap reset")
}

func TestOBAdapter_BestQuote_EmptyBook_ReturnsEmpty(t *testing.T) {
	ob := orderbook.New("kucoin", "BTC-USDT", 10, func(orderbook.GapMarker) {})
	adapter := consumer.NewOBAdapter(ob)
	bid, bidSz, ask, askSz := adapter.BestQuote()
	assert.Empty(t, bid)
	assert.Empty(t, bidSz)
	assert.Empty(t, ask)
	assert.Empty(t, askSz)
}
