package pubsub_test

import (
	"context"
	"encoding/json"
	"fmt"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
	"github.com/mrqdt/magnum-opus/aggregator/internal/orderbook"
	"github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
	"github.com/mrqdt/magnum-opus/aggregator/internal/writer/pubsub"
)

// fakePubSubClient captures Publish calls for assertion.
type fakePubSubClient struct {
	Calls []publishCall
	Err   error
}

type publishCall struct {
	Channel string
	Payload []byte
}

func (f *fakePubSubClient) Publish(_ context.Context, ch string, msg any) error {
	var payload []byte
	switch v := msg.(type) {
	case []byte:
		payload = v
	case string:
		payload = []byte(v)
	default:
		payload = []byte(fmt.Sprint(v))
	}
	f.Calls = append(f.Calls, publishCall{Channel: ch, Payload: payload})
	return f.Err
}

func makeSnap(bids, asks map[string]string) orderbook.Snapshot {
	return orderbook.Snapshot{Bids: bids, Asks: asks}
}

func makeTick(side, size string) exchange.Tick {
	return exchange.Tick{
		Exchange: "kucoin",
		Symbol:   symbol.Symbol("BTCUSDT"),
		TsLocal:  1_700_000_000_000_000_000,
		Side:     side,
		Size:     size,
		Type:     exchange.EventTypeUpdate,
	}
}

func decode(t *testing.T, calls []publishCall) map[string]any {
	t.Helper()
	require.Len(t, calls, 1)
	var m map[string]any
	require.NoError(t, json.Unmarshal(calls[0].Payload, &m))
	return m
}

func TestPublisher_ChannelName(t *testing.T) {
	fake := &fakePubSubClient{}
	p := pubsub.New(fake)
	snap := makeSnap(map[string]string{"29500": "1.0"}, map[string]string{"29501": "1.0"})
	err := p.Publish(context.Background(), "kucoin", symbol.Symbol("BTCUSDT"), snap, makeTick("bid", "1.0"))
	require.NoError(t, err)
	assert.Equal(t, "orderbook:kucoin:BTCUSDT", fake.Calls[0].Channel)
}

func TestPublisher_BidsSortedDesc(t *testing.T) {
	fake := &fakePubSubClient{}
	p := pubsub.New(fake)
	bids := map[string]string{"29498": "1.0", "29500": "2.0", "29499": "3.0"}
	snap := makeSnap(bids, map[string]string{"29501": "1.0"})
	require.NoError(t, p.Publish(context.Background(), "kucoin", symbol.Symbol("BTCUSDT"), snap, makeTick("bid", "1.0")))
	m := decode(t, fake.Calls)
	bidsOut := m["bids"].([]any)
	require.Len(t, bidsOut, 3)
	assert.Equal(t, "29500", bidsOut[0].([]any)[0])
	assert.Equal(t, "29499", bidsOut[1].([]any)[0])
	assert.Equal(t, "29498", bidsOut[2].([]any)[0])
}

func TestPublisher_AsksSortedAsc(t *testing.T) {
	fake := &fakePubSubClient{}
	p := pubsub.New(fake)
	asks := map[string]string{"29502": "1.0", "29500": "2.0", "29501": "3.0"}
	snap := makeSnap(map[string]string{"29499": "1.0"}, asks)
	require.NoError(t, p.Publish(context.Background(), "kucoin", symbol.Symbol("BTCUSDT"), snap, makeTick("ask", "1.0")))
	m := decode(t, fake.Calls)
	asksOut := m["asks"].([]any)
	require.Len(t, asksOut, 3)
	assert.Equal(t, "29500", asksOut[0].([]any)[0])
	assert.Equal(t, "29501", asksOut[1].([]any)[0])
	assert.Equal(t, "29502", asksOut[2].([]any)[0])
}

func TestPublisher_Top20Cap(t *testing.T) {
	fake := &fakePubSubClient{}
	p := pubsub.New(fake)
	bids := make(map[string]string, 25)
	for i := range 25 {
		bids[fmt.Sprintf("%d", 29500+i)] = "1.0"
	}
	snap := makeSnap(bids, map[string]string{"29600": "1.0"})
	require.NoError(t, p.Publish(context.Background(), "kucoin", symbol.Symbol("BTCUSDT"), snap, makeTick("bid", "1.0")))
	m := decode(t, fake.Calls)
	assert.Len(t, m["bids"].([]any), 20)
}

func TestPublisher_MidPriceAndSpread(t *testing.T) {
	fake := &fakePubSubClient{}
	p := pubsub.New(fake)
	snap := makeSnap(map[string]string{"29500": "1.0"}, map[string]string{"29502": "1.0"})
	require.NoError(t, p.Publish(context.Background(), "kucoin", symbol.Symbol("BTCUSDT"), snap, makeTick("bid", "1.0")))
	m := decode(t, fake.Calls)
	assert.InDelta(t, 29501.0, m["mid_price"].(float64), 0.001)
	assert.InDelta(t, 2.0, m["spread"].(float64), 0.001)
}

func TestPublisher_IntervalVolumes_Bid(t *testing.T) {
	fake := &fakePubSubClient{}
	p := pubsub.New(fake)
	snap := makeSnap(map[string]string{"29500": "1.0"}, map[string]string{"29501": "1.0"})
	require.NoError(t, p.Publish(context.Background(), "kucoin", symbol.Symbol("BTCUSDT"), snap, makeTick("bid", "1.5")))
	m := decode(t, fake.Calls)
	assert.InDelta(t, 1.5, m["interval_bid_volume"].(float64), 0.001)
	assert.InDelta(t, 0.0, m["interval_ask_volume"].(float64), 0.001)
	assert.InDelta(t, 1.5, m["interval_total_volume"].(float64), 0.001)
}

func TestPublisher_IntervalVolumes_Ask(t *testing.T) {
	fake := &fakePubSubClient{}
	p := pubsub.New(fake)
	snap := makeSnap(map[string]string{"29500": "1.0"}, map[string]string{"29501": "1.0"})
	require.NoError(t, p.Publish(context.Background(), "kucoin", symbol.Symbol("BTCUSDT"), snap, makeTick("ask", "2.0")))
	m := decode(t, fake.Calls)
	assert.InDelta(t, 0.0, m["interval_bid_volume"].(float64), 0.001)
	assert.InDelta(t, 2.0, m["interval_ask_volume"].(float64), 0.001)
	assert.InDelta(t, 2.0, m["interval_total_volume"].(float64), 0.001)
}

func TestPublisher_EmptyBook(t *testing.T) {
	fake := &fakePubSubClient{}
	p := pubsub.New(fake)
	snap := makeSnap(map[string]string{}, map[string]string{})
	require.NoError(t, p.Publish(context.Background(), "kucoin", symbol.Symbol("BTCUSDT"), snap, makeTick("bid", "0.0")))
	m := decode(t, fake.Calls)
	assert.Empty(t, m["bids"].([]any))
	assert.Empty(t, m["asks"].([]any))
	assert.InDelta(t, 0.0, m["mid_price"].(float64), 0.001)
	assert.InDelta(t, 0.0, m["spread"].(float64), 0.001)
}

func TestPublisher_ZeroSizeLevelExcluded(t *testing.T) {
	fake := &fakePubSubClient{}
	p := pubsub.New(fake)
	// "0"-sized entry must be excluded; only the valid level appears in output.
	bids := map[string]string{"29500": "1.0", "29499": "0"}
	snap := makeSnap(bids, map[string]string{"29501": "1.0"})
	require.NoError(t, p.Publish(context.Background(), "kucoin", symbol.Symbol("BTCUSDT"), snap, makeTick("bid", "1.0")))
	m := decode(t, fake.Calls)
	bidsOut := m["bids"].([]any)
	require.Len(t, bidsOut, 1, "zero-size level must be excluded")
	assert.Equal(t, "29500", bidsOut[0].([]any)[0])
}

func TestPublisher_ClientError(t *testing.T) {
	fake := &fakePubSubClient{Err: fmt.Errorf("redis unavailable")}
	p := pubsub.New(fake)
	snap := makeSnap(map[string]string{"29500": "1.0"}, map[string]string{"29501": "1.0"})
	err := p.Publish(context.Background(), "kucoin", symbol.Symbol("BTCUSDT"), snap, makeTick("bid", "1.0"))
	assert.ErrorContains(t, err, "redis unavailable")
}
