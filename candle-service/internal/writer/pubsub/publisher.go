// Package pubsub publishes 1-second candle features to Redis pub/sub after each bar close.
// Fire-and-forget: errors are logged at WARN and never block the flush pipeline.
package pubsub

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"

	goredis "github.com/redis/go-redis/v9"

	"github.com/mrqdt/magnum-opus/candle-service/internal/accumulator"
)

// PubSubClient is the minimal Redis interface required by Publisher.
type PubSubClient interface {
	Publish(ctx context.Context, channel string, message any) error
}

// RealClient wraps *goredis.Client to satisfy PubSubClient.
type RealClient struct{ c *goredis.Client }

// NewRealClient creates a RealClient backed by the given go-redis client.
func NewRealClient(c *goredis.Client) *RealClient { return &RealClient{c: c} }

func (r *RealClient) Publish(ctx context.Context, ch string, msg any) error {
	return r.c.Publish(ctx, ch, msg).Err()
}

// Publisher publishes 1s candle bars to the candles1s:{exchange}:{symbol} Redis pub/sub channel.
type Publisher struct {
	client   PubSubClient
	exchange string
	symbol   string
	channel  string
}

// New creates a Publisher for one (exchange, symbol) pair.
func New(client PubSubClient, exchange, symbol string) *Publisher {
	return &Publisher{
		client:   client,
		exchange: exchange,
		symbol:   symbol,
		channel:  fmt.Sprintf("candles1s:%s:%s", exchange, symbol),
	}
}

// Publish1sBar encodes and publishes the 1s bar features to Redis pub/sub.
// On error: logs at WARN and returns the error. The caller swallows it (fire-and-forget).
func (p *Publisher) Publish1sBar(ctx context.Context, bar accumulator.Bar) error {
	payload := map[string]any{
		"ts_ns":           bar.TsSecMs * 1_000_000,
		"exchange":        bar.Exchange,
		"symbol":          bar.Symbol,
		"open":            derefF(bar.Open),
		"high":            derefF(bar.High),
		"low":             derefF(bar.Low),
		"close":           derefF(bar.Close),
		"volume":          derefF(bar.Volume),
		"ofi":             derefF(bar.OFI),
		"ofi_l1":          derefF(bar.OFIL1),
		"spread":          derefF(bar.SpreadMean),
		"bid_depth_l1":    derefF(bar.BidDepthL1Close),
		"ask_depth_l1":    derefF(bar.AskDepthL1Close),
		"bid_depth_top10": derefF(bar.BidDepthTop10Close),
		"ask_depth_top10": derefF(bar.AskDepthTop10Close),
		"realized_vol":    derefF(bar.RealizedVol),
	}
	if bar.BuyVolume != nil {
		payload["buy_volume"] = *bar.BuyVolume
		payload["sell_volume"] = derefF(bar.SellVolume)
	}
	if bar.FootprintJSON != nil {
		payload["footprint_json"] = *bar.FootprintJSON
	}
	if bar.POCPrice != nil && bar.ValueAreaHigh != nil && bar.ValueAreaLow != nil && bar.POCVolume != nil {
		payload["poc_price"] = *bar.POCPrice
		payload["value_area_high"] = *bar.ValueAreaHigh
		payload["value_area_low"] = *bar.ValueAreaLow
		payload["poc_volume"] = *bar.POCVolume
	}
	if bar.ImbalanceBuyCount != nil && bar.ImbalanceSellCount != nil && bar.ImbalanceStackBuy != nil && bar.ImbalanceStackSell != nil && bar.ImbalanceRatio != nil {
		payload["imbalance_buy_count"] = *bar.ImbalanceBuyCount
		payload["imbalance_sell_count"] = *bar.ImbalanceSellCount
		payload["imbalance_stack_buy"] = *bar.ImbalanceStackBuy
		payload["imbalance_stack_sell"] = *bar.ImbalanceStackSell
		payload["imbalance_ratio"] = *bar.ImbalanceRatio
	}
	if bar.SinglePrintCount != nil {
		payload["single_print_count"] = *bar.SinglePrintCount
		payload["single_print_levels_json"] = *bar.SinglePrintLevelsJSON
	}
	if bar.UnfinishedTop != nil && *bar.UnfinishedTop {
		payload["unfinished_top"] = "true"
	}
	if bar.UnfinishedBottom != nil && *bar.UnfinishedBottom {
		payload["unfinished_bottom"] = "true"
	}
	if bar.AbsorptionDetected != nil && *bar.AbsorptionDetected {
		payload["absorption_detected"] = "true"
	}
	if bar.FootprintDeltaDivergence != nil {
		payload["footprint_delta_divergence"] = *bar.FootprintDeltaDivergence
	}
	if bar.CumDelta != nil {
		payload["cum_delta"] = *bar.CumDelta
	}
	if bar.CVDDivergence != nil {
		payload["cvd_divergence"] = *bar.CVDDivergence
	}
	if bar.IcebergBidDetected != nil && *bar.IcebergBidDetected {
		payload["iceberg_bid_detected"] = "true"
	}
	if bar.IcebergAskDetected != nil && *bar.IcebergAskDetected {
		payload["iceberg_ask_detected"] = "true"
	}
	if bar.IcebergPrice != nil {
		payload["iceberg_price"] = *bar.IcebergPrice
	}
	b, err := json.Marshal(payload)
	if err != nil {
		if ctx.Err() == nil {
			slog.WarnContext(ctx, "candles1s pubsub: marshal failed",
				"exchange", p.exchange, "symbol", p.symbol, "err", err)
		}
		return err
	}
	if err := p.client.Publish(ctx, p.channel, b); err != nil {
		if ctx.Err() == nil {
			slog.WarnContext(ctx, "candles1s pubsub: publish failed",
				"exchange", p.exchange, "symbol", p.symbol, "err", err)
		}
		return err
	}
	return nil
}

func derefF(p *float64) float64 {
	if p == nil {
		return 0
	}
	return *p
}
