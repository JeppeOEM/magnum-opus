package consumer

import "github.com/mrqdt/magnum-opus/candle-service/internal/orderbook"

// OBAdapter wraps *orderbook.OrderBook to satisfy the BookApplier interface.
// It handles type conversion between consumer.{Tick,SnapshotEvent,GapMarker}
// and orderbook.{Tick,SnapshotEvent,GapMarker}.
type OBAdapter struct {
	ob *orderbook.OrderBook
}

// NewOBAdapter creates an OBAdapter wrapping ob.
func NewOBAdapter(ob *orderbook.OrderBook) *OBAdapter {
	return &OBAdapter{ob: ob}
}

func (a *OBAdapter) ApplyTick(t Tick) {
	a.ob.ApplyTick(orderbook.Tick{
		Seq:   t.Seq,
		TsMs:  t.TsMs,
		Price: t.Price,
		Size:  t.Size,
		Side:  t.Side,
		Level: t.Level,
	})
}

func (a *OBAdapter) ApplySnapshot(s SnapshotEvent) {
	a.ob.ApplySnapshot(orderbook.SnapshotEvent{
		Seq:  s.Seq,
		TsMs: s.TsMs,
	})
}

func (a *OBAdapter) ApplyGap(g GapMarker) {
	a.ob.ApplyGap(orderbook.GapMarker{
		SeqBefore: g.SeqBefore,
		SeqAfter:  g.SeqAfter,
		GapCause:  g.GapCause,
		GapTsMs:   g.GapTsMs,
		Exchange:  g.Exchange,
		Symbol:    g.Symbol,
	})
}

func (a *OBAdapter) BestQuote() (bidPrice, bidSize, askPrice, askSize string) {
	return a.ob.BestQuote()
}

func (a *OBAdapter) HasLevel(side, price string) bool {
	return a.ob.HasLevel(side, price)
}
