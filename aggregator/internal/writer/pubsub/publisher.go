// Package pubsub implements coordinator.OBPublisher by publishing full L2 orderbook
// snapshots to Redis pub/sub after every EventTypeUpdate tick.
// Interface ownership: coordinator.OBPublisher is defined in coordinator/interfaces.go.
package pubsub

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"sort"
	"strconv"

	goredis "github.com/redis/go-redis/v9"

	"github.com/mrqdt/magnum-opus/aggregator/internal/coordinator"
	"github.com/mrqdt/magnum-opus/aggregator/internal/exchange"
	"github.com/mrqdt/magnum-opus/aggregator/internal/orderbook"
	"github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
)

// compile-time check: Publisher satisfies coordinator.OBPublisher.
var _ coordinator.OBPublisher = (*Publisher)(nil)

const maxDepthLevels = 20

// PubSubClient is the narrow interface Publisher requires.
// *goredis.Client satisfies this via RealClient.
type PubSubClient interface {
	Publish(ctx context.Context, channel string, message any) error
}

// RealClient wraps *goredis.Client to satisfy PubSubClient.
type RealClient struct {
	c *goredis.Client
}

// NewRealClient wraps a go-redis v9 client.
func NewRealClient(c *goredis.Client) *RealClient { return &RealClient{c: c} }

// Publish calls go-redis Publish and returns any error.
func (r *RealClient) Publish(ctx context.Context, ch string, msg any) error {
	return r.c.Publish(ctx, ch, msg).Err()
}

// Publisher publishes full L2 orderbook snapshots to Redis pub/sub.
type Publisher struct {
	client PubSubClient
}

// New returns a Publisher using the given PubSubClient.
func New(client PubSubClient) *Publisher {
	return &Publisher{client: client}
}

// Publish encodes the current book snapshot as a DepthPayload JSON and publishes
// it to orderbook:{exch}:{sym}. Errors are returned; callers log at Warn and continue.
func (p *Publisher) Publish(ctx context.Context, exch string, sym symbol.Symbol, snap orderbook.Snapshot, tick exchange.Tick) error {
	bids := sortedLevels(snap.Bids, true)
	asks := sortedLevels(snap.Asks, false)

	midPrice, spread := midAndSpread(bids, asks)

	var intervalBid, intervalAsk float64
	if size, err := strconv.ParseFloat(tick.Size, 64); err == nil {
		switch tick.Side {
		case "bid":
			intervalBid = size
		case "ask":
			intervalAsk = size
		}
	}

	payload := map[string]any{
		"ts_ns":                  tick.TsLocal,
		"exchange":               exch,
		"symbol":                 sym.String(),
		"bids":                   bids,
		"asks":                   asks,
		"mid_price":              midPrice,
		"spread":                 spread,
		"interval_bid_volume":    intervalBid,
		"interval_ask_volume":    intervalAsk,
		"interval_total_volume":  intervalBid + intervalAsk,
		"update_count":           1,
	}

	b, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("pubsub: marshal failed: %w", err)
	}

	channel := fmt.Sprintf("orderbook:%s:%s", exch, sym.String())
	if err := p.client.Publish(ctx, channel, b); err != nil {
		if ctx.Err() == nil {
			slog.Warn("pubsub: publish failed", "exchange", exch, "symbol", sym.String(), "err", err)
		}
		return err
	}
	return nil
}

// priceLevel holds a parsed price alongside the original string pair for JSON output.
type priceLevel struct {
	price    float64
	priceStr string
	sizeStr  string
}

// sortedLevels extracts, sorts (descending if bid, ascending if ask), and caps
// the book levels at maxDepthLevels. Returns a [][]string ready for JSON.
func sortedLevels(m map[string]string, descending bool) [][]string {
	levels := make([]priceLevel, 0, len(m))
	for p, s := range m {
		if s == "0" || s == "" {
			continue
		}
		f, err := strconv.ParseFloat(p, 64)
		if err != nil {
			continue
		}
		levels = append(levels, priceLevel{f, p, s})
	}
	sort.Slice(levels, func(i, j int) bool {
		if descending {
			return levels[i].price > levels[j].price
		}
		return levels[i].price < levels[j].price
	})
	if len(levels) > maxDepthLevels {
		levels = levels[:maxDepthLevels]
	}
	out := make([][]string, len(levels))
	for i, l := range levels {
		out[i] = []string{l.priceStr, l.sizeStr}
	}
	return out
}

// midAndSpread computes mid-price and spread from already-sorted top-of-book levels.
// Returns 0.0 for both if either side is empty.
func midAndSpread(bids, asks [][]string) (mid, spread float64) {
	if len(bids) == 0 || len(asks) == 0 {
		return 0.0, 0.0
	}
	bestBid, errB := strconv.ParseFloat(bids[0][0], 64)
	bestAsk, errA := strconv.ParseFloat(asks[0][0], 64)
	if errB != nil || errA != nil {
		return 0.0, 0.0
	}
	return (bestBid + bestAsk) / 2, bestAsk - bestBid
}
