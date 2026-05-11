package pubsub

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"testing"

	"github.com/mrqdt/magnum-opus/candle-service/internal/accumulator"
)

type fakePubSubClient struct {
	calls []struct {
		Channel string
		Payload []byte
	}
	err error
}

func (f *fakePubSubClient) Publish(_ context.Context, ch string, msg any) error {
	var b []byte
	switch v := msg.(type) {
	case []byte:
		b = v
	case string:
		b = []byte(v)
	default:
		b = []byte(fmt.Sprint(v))
	}
	f.calls = append(f.calls, struct {
		Channel string
		Payload []byte
	}{ch, b})
	return f.err
}

func ptr(f float64) *float64 { return &f }

func TestPublisher_ChannelName(t *testing.T) {
	fake := &fakePubSubClient{}
	p := New(fake, "kucoin", "BTCUSDT")
	if err := p.Publish1sBar(context.Background(), accumulator.Bar{Exchange: "kucoin", Symbol: "BTCUSDT"}); err != nil {
		t.Fatal(err)
	}
	if len(fake.calls) != 1 {
		t.Fatalf("expected 1 publish call, got %d", len(fake.calls))
	}
	if fake.calls[0].Channel != "candles1s:kucoin:BTCUSDT" {
		t.Errorf("channel = %q, want candles1s:kucoin:BTCUSDT", fake.calls[0].Channel)
	}
}

func TestPublisher_PayloadFields(t *testing.T) {
	fake := &fakePubSubClient{}
	p := New(fake, "kucoin", "BTCUSDT")
	bar := accumulator.Bar{
		TsSecMs:         1_700_000_000_000,
		Exchange:        "kucoin",
		Symbol:          "BTCUSDT",
		Open:            ptr(100.0),
		High:            ptr(101.0),
		Low:             ptr(99.0),
		Close:           ptr(100.5),
		Volume:          ptr(500.0),
		OFI:             ptr(1.5),
		OFIL1:           ptr(0.8),
		SpreadMean:      ptr(0.1),
		BidDepthL1Close: ptr(200.0),
		AskDepthL1Close: ptr(180.0),
		BidDepthTop10Close: ptr(2000.0),
		AskDepthTop10Close: ptr(1800.0),
		RealizedVol:     ptr(0.002),
	}
	if err := p.Publish1sBar(context.Background(), bar); err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(fake.calls[0].Payload, &got); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	checks := map[string]float64{
		"open": 100.0, "ofi": 1.5, "spread": 0.1,
		"bid_depth_l1": 200.0, "realized_vol": 0.002,
	}
	for field, want := range checks {
		v, ok := got[field].(float64)
		if !ok {
			t.Errorf("field %s missing or wrong type", field)
			continue
		}
		if v != want {
			t.Errorf("field %s = %v, want %v", field, v, want)
		}
	}
}

func TestPublisher_NilFieldsZero(t *testing.T) {
	fake := &fakePubSubClient{}
	p := New(fake, "bybit", "ETHUSDT")
	bar := accumulator.Bar{Exchange: "bybit", Symbol: "ETHUSDT"} // all pointers nil
	if err := p.Publish1sBar(context.Background(), bar); err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(fake.calls[0].Payload, &got); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	nilFields := []string{"open", "high", "low", "close", "volume", "ofi", "ofi_l1",
		"spread", "bid_depth_l1", "ask_depth_l1", "bid_depth_top10", "ask_depth_top10", "realized_vol"}
	for _, f := range nilFields {
		v, ok := got[f]
		if !ok {
			t.Errorf("field %s missing from payload", f)
			continue
		}
		if v.(float64) != 0.0 {
			t.Errorf("field %s = %v, want 0.0", f, v)
		}
	}
}

func TestPublisher_TsNs(t *testing.T) {
	fake := &fakePubSubClient{}
	p := New(fake, "kucoin", "BTCUSDT")
	bar := accumulator.Bar{TsSecMs: 1_700_000_000_000, Exchange: "kucoin", Symbol: "BTCUSDT"}
	if err := p.Publish1sBar(context.Background(), bar); err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(fake.calls[0].Payload, &got); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	// JSON numbers decode as float64; 1_700_000_000_000_000_000 fits in float64 exactly at this magnitude
	tsNs := int64(got["ts_ns"].(float64))
	want := int64(1_700_000_000_000_000_000)
	if tsNs != want {
		t.Errorf("ts_ns = %d, want %d", tsNs, want)
	}
}

func TestPublisher_ValueAreaFields_Present(t *testing.T) {
	fake := &fakePubSubClient{}
	p := New(fake, "kucoin", "BTCUSDT")
	bar := accumulator.Bar{
		Exchange:      "kucoin",
		Symbol:        "BTCUSDT",
		BuyVolume:     ptr(3.0),
		SellVolume:    ptr(7.0),
		POCPrice:      ptr(67000.5),
		ValueAreaHigh: ptr(67100.0),
		ValueAreaLow:  ptr(66900.0),
		POCVolume:     ptr(5.0),
	}
	if err := p.Publish1sBar(context.Background(), bar); err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(fake.calls[0].Payload, &got); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	checks := map[string]float64{
		"poc_price":      67000.5,
		"value_area_high": 67100.0,
		"value_area_low":  66900.0,
		"poc_volume":      5.0,
	}
	for field, want := range checks {
		v, ok := got[field].(float64)
		if !ok {
			t.Errorf("field %s missing or wrong type (got %v)", field, got[field])
			continue
		}
		if v != want {
			t.Errorf("field %s = %v, want %v", field, v, want)
		}
	}
}

func TestPublisher_ValueAreaFields_AbsentWhenNoPOC(t *testing.T) {
	fake := &fakePubSubClient{}
	p := New(fake, "kucoin", "BTCUSDT")
	bar := accumulator.Bar{Exchange: "kucoin", Symbol: "BTCUSDT"} // POCPrice == nil
	if err := p.Publish1sBar(context.Background(), bar); err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(fake.calls[0].Payload, &got); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	for _, field := range []string{"poc_price", "value_area_high", "value_area_low", "poc_volume"} {
		if _, exists := got[field]; exists {
			t.Errorf("field %s must be absent from zero-trade bar payload", field)
		}
	}
}

func TestPublisher_ClientError(t *testing.T) {
	wantErr := errors.New("redis down")
	fake := &fakePubSubClient{err: wantErr}
	p := New(fake, "kucoin", "BTCUSDT")
	err := p.Publish1sBar(context.Background(), accumulator.Bar{Exchange: "kucoin", Symbol: "BTCUSDT"})
	if !errors.Is(err, wantErr) {
		t.Errorf("err = %v, want %v", err, wantErr)
	}
}
