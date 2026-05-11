//go:build l2

package pubsub

import (
	"encoding/json"
	"testing"
	"time"

	goredis "github.com/redis/go-redis/v9"

	"github.com/mrqdt/magnum-opus/candle-service/internal/accumulator"
)

func TestPublisher_RealRedis(t *testing.T) {
	ctx := t.Context()
	rdb := goredis.NewClient(&goredis.Options{Addr: "localhost:6379"})
	defer rdb.Close()
	if err := rdb.Ping(ctx).Err(); err != nil {
		t.Skipf("Redis unavailable: %v", err)
	}

	pubsub := rdb.PSubscribe(ctx, "candles1s:*")
	defer pubsub.Close()
	// Drain the subscription confirmation message.
	if _, err := pubsub.Receive(ctx); err != nil {
		t.Fatalf("subscribe: %v", err)
	}

	p := New(NewRealClient(rdb), "kucoin", "BTCUSDT")
	bar := accumulator.Bar{
		TsSecMs:  1_700_000_000_000,
		Exchange: "kucoin",
		Symbol:   "BTCUSDT",
		Open:     ptr(42000.0),
		OFI:      ptr(1.2),
	}
	if err := p.Publish1sBar(ctx, bar); err != nil {
		t.Fatalf("Publish1sBar: %v", err)
	}

	msgCh := pubsub.Channel()
	select {
	case msg := <-msgCh:
		if msg.Channel != "candles1s:kucoin:BTCUSDT" {
			t.Errorf("channel = %q, want candles1s:kucoin:BTCUSDT", msg.Channel)
		}
		var got map[string]any
		if err := json.Unmarshal([]byte(msg.Payload), &got); err != nil {
			t.Fatalf("unmarshal payload: %v", err)
		}
		for _, field := range []string{"ts_ns", "open", "ofi"} {
			if _, ok := got[field]; !ok {
				t.Errorf("payload missing field %q", field)
			}
		}
	case <-time.After(100 * time.Millisecond):
		t.Fatal("no message received within 100ms")
	}
}
