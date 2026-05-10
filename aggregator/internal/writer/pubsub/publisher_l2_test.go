//go:build l2

package pubsub_test

import (
	"encoding/json"
	"testing"
	"time"

	goredis "github.com/redis/go-redis/v9"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
	"github.com/mrqdt/magnum-opus/aggregator/internal/orderbook"
	"github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
	"github.com/mrqdt/magnum-opus/aggregator/internal/writer/pubsub"
)

func TestPublisher_RealRedis(t *testing.T) {
	client := goredis.NewClient(&goredis.Options{Addr: "localhost:6379"})
	t.Cleanup(func() { client.Close() })

	ctx := t.Context()
	if err := client.Ping(ctx).Err(); err != nil {
		t.Skipf("Redis not available: %v", err)
	}

	// Subscribe before publishing so we don't miss the message.
	sub := client.PSubscribe(ctx, "orderbook:*")
	t.Cleanup(func() { sub.Close() })

	p := pubsub.New(pubsub.NewRealClient(client))

	snap := orderbook.Snapshot{
		Bids: map[string]string{"29500": "1.5", "29499": "2.0"},
		Asks: map[string]string{"29501": "0.8", "29502": "3.0"},
	}
	tick := exchange.Tick{
		Exchange: "kucoin",
		Symbol:   symbol.Symbol("BTCUSDT"),
		TsLocal:  1_700_000_000_000_000_000,
		Side:     "bid",
		Size:     "1.5",
		Type:     exchange.EventTypeUpdate,
	}

	require.NoError(t, p.Publish(ctx, "kucoin", symbol.Symbol("BTCUSDT"), snap, tick))

	// Assert message arrives within 100ms.
	msgCh := sub.Channel()
	select {
	case msg := <-msgCh:
		assert.Equal(t, "orderbook:kucoin:BTCUSDT", msg.Channel)

		var payload map[string]any
		require.NoError(t, json.Unmarshal([]byte(msg.Payload), &payload))
		assert.Contains(t, payload, "ts_ns")
		assert.Contains(t, payload, "bids")
		assert.Contains(t, payload, "asks")
		assert.Equal(t, "kucoin", payload["exchange"])
		assert.Equal(t, "BTCUSDT", payload["symbol"])
	case <-time.After(100 * time.Millisecond):
		t.Fatal("no pub/sub message received within 100ms")
	}
}
