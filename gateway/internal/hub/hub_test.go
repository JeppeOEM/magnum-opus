package hub

import (
	"encoding/json"
	"testing"

	"github.com/coder/websocket"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// fakeSender implements Sender for unit tests — captures all sent messages.
type fakeSender struct {
	messages []message
}

func (f *fakeSender) Send(typ websocket.MessageType, data []byte) {
	f.messages = append(f.messages, message{typ: typ, data: data})
}

func mustJSON(v any) []byte {
	b, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	return b
}

func TestRoute_Orderbook_BinaryFrame(t *testing.T) {
	h := NewHub()
	fs := &fakeSender{}
	h.RegisterSender(fs)
	h.Subscribe(fs, "BTC-USDT")

	payload := mustJSON(map[string]any{
		"ts_ns": int64(1_000_000),
		"bids":  [][]string{{"100.0", "1.0"}},
		"asks":  [][]string{{"101.0", "0.5"}},
	})
	h.Route("orderbook:kucoin:BTC-USDT", payload)

	require.Len(t, fs.messages, 1)
	assert.Equal(t, websocket.MessageBinary, fs.messages[0].typ)
	assert.Equal(t, byte(0x01), fs.messages[0].data[0], "first byte must be 0x01 MSG_SNAPSHOT")
}

func TestRoute_Candles1s_TextFrame(t *testing.T) {
	h := NewHub()
	fs := &fakeSender{}
	h.RegisterSender(fs)
	h.Subscribe(fs, "BTC-USDT")

	payload := mustJSON(map[string]any{
		"ts_ns": int64(2_000_000),
		"close": 100.5,
		"ofi":   0.75,
	})
	h.Route("candles1s:kucoin:BTC-USDT", payload)

	require.Len(t, fs.messages, 1)
	assert.Equal(t, websocket.MessageText, fs.messages[0].typ)

	var m map[string]any
	require.NoError(t, json.Unmarshal(fs.messages[0].data, &m))
	assert.Equal(t, "candles1s", m["type"])
	assert.InDelta(t, 100.5, m["close"], 1e-9)
}

func TestSubscribeOnConnect_SendsLastSnapshot(t *testing.T) {
	h := NewHub()

	// First client — used to populate lastSnap cache
	first := &fakeSender{}
	h.RegisterSender(first)
	h.Subscribe(first, "BTC-USDT")

	payload := mustJSON(map[string]any{
		"ts_ns": int64(5_000_000),
		"bids":  [][]string{{"200.0", "1.0"}},
		"asks":  [][]string{{"201.0", "0.5"}},
	})
	h.Route("orderbook:kucoin:BTC-USDT", payload)
	require.Len(t, first.messages, 1) // first client received route

	// New client subscribes — should immediately receive the cached snapshot
	newClient := &fakeSender{}
	h.RegisterSender(newClient)
	h.Subscribe(newClient, "BTC-USDT")

	require.Len(t, newClient.messages, 1, "new subscriber should receive cached snapshot")
	assert.Equal(t, websocket.MessageBinary, newClient.messages[0].typ)
	assert.Equal(t, byte(0x01), newClient.messages[0].data[0])
}

func TestRoute_DoesNotSendToUnsubscribedSymbol(t *testing.T) {
	h := NewHub()
	fs := &fakeSender{}
	h.RegisterSender(fs)
	h.Subscribe(fs, "ETH-USDT") // subscribed to ETH, not BTC

	payload := mustJSON(map[string]any{
		"ts_ns": int64(1_000_000),
		"bids":  [][]string{{"100.0", "1.0"}},
		"asks":  [][]string{{"101.0", "0.5"}},
	})
	h.Route("orderbook:kucoin:BTC-USDT", payload)

	assert.Empty(t, fs.messages, "should not receive messages for unsubscribed symbol")
}

func TestUnsubscribe_StopsReceivingMessages(t *testing.T) {
	h := NewHub()
	fs := &fakeSender{}
	h.RegisterSender(fs)
	h.Subscribe(fs, "BTC-USDT")
	h.Unsubscribe(fs, "BTC-USDT")

	payload := mustJSON(map[string]any{
		"ts_ns": int64(1_000_000),
		"bids":  [][]string{{"100.0", "1.0"}},
		"asks":  [][]string{{"101.0", "0.5"}},
	})
	h.Route("orderbook:kucoin:BTC-USDT", payload)

	assert.Empty(t, fs.messages, "unsubscribed client should receive no messages")
}
