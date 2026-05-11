package accumulator_test

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/mrqdt/magnum-opus/candle-service/internal/accumulator"
	"github.com/mrqdt/magnum-opus/candle-service/internal/features"
	"github.com/mrqdt/magnum-opus/candle-service/internal/testutil"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

var epoch = time.Unix(1_700_000_000, 0)

func newAcc(t *testing.T) (*accumulator.Accumulator, *testutil.MockClock) {
	t.Helper()
	clk := testutil.NewMockClock(epoch)
	return accumulator.New("kucoin", "BTC-USDT", clk), clk
}

var noQ = features.BestQuote{}

func bq(bp, bs, ap, as_ string) features.BestQuote {
	return features.BestQuote{BidPrice: bp, BidSize: bs, AskPrice: ap, AskSize: as_}
}

func TestAccumulator_Apply_TradeUpdatesOHLCV(t *testing.T) {
	acc, _ := newAcc(t)
	q0 := bq("100", "1", "101", "1")
	q1 := bq("105", "1", "106", "1")
	q2 := bq("98", "3", "99", "1")
	acc.Apply("100", "2.0", true, "buy", 0, noQ, q0)
	acc.Apply("105", "1.0", true, "buy", 0, q0, q1)
	acc.Apply("98", "3.0", true, "buy", 0, q1, q2)

	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.Open)
	assert.InDelta(t, 100.0, *bar.Open, 1e-9)
	assert.InDelta(t, 105.0, *bar.High, 1e-9)
	assert.InDelta(t, 98.0, *bar.Low, 1e-9)
	assert.InDelta(t, 98.0, *bar.Close, 1e-9)
	assert.InDelta(t, 6.0, *bar.Volume, 1e-9) // 2+1+3
	assert.Equal(t, 3, bar.TradeCount)
}

func TestAccumulator_Apply_OBDeltaDoesNotUpdateOHLCV(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	acc.Apply("99000", "5.0", false, "", 0, noQ, q) // OB delta — must not update OHLCV
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.Open, "OB delta must not update OHLCV")
	assert.Equal(t, 0, bar.TradeCount)
}

func TestAccumulator_Apply_OBQuoteOpenCapture(t *testing.T) {
	acc, _ := newAcc(t)
	q1 := bq("100", "2", "101", "3")
	q2 := bq("100", "4", "101", "3")
	acc.Apply("0", "0", false, "", 0, noQ, q1) // first tick → captures open quote
	acc.Apply("0", "0", false, "", 0, q1, q2)  // second tick → updates close only

	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.BestBidOpen)
	assert.InDelta(t, 100.0, *bar.BestBidOpen, 1e-9, "open quote from first tick")
	assert.InDelta(t, 101.0, *bar.BestAskOpen, 1e-9)
	assert.InDelta(t, 100.0, *bar.BestBid, 1e-9, "close quote from last tick")
}

func TestAccumulator_Apply_TWAP_SingleTrade(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	// Single trade at tsMs=500 inside a second that starts at tsMs=0.
	// TWAP = price (only one trade; held entire remaining bar duration).
	tsSecMs := int64(0)
	acc.Apply("200", "3", true, "buy", 500, noQ, q)
	bar := acc.CurrentBar(tsSecMs, false)
	require.NotNil(t, bar.TWAP)
	assert.InDelta(t, 200.0, *bar.TWAP, 1e-9, "single-trade TWAP equals that trade's price")
}

func TestAccumulator_Apply_TWAP_TimeWeighted(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	// Two trades: price=100 at t=0, price=200 at t=500 (in a bar starting at t=0).
	// price 100 held from t=0 to t=500: Δt=500; price 200 held from t=500 to t=1000: Δt=500.
	// TWAP = (100*500 + 200*500) / (500+500) = 150.
	// VWAP would be volume-weighted, so if both have equal size → also 150. Use unequal sizes.
	// price 100 held 500ms, price 200 held 500ms → TWAP=150 regardless of trade sizes.
	tsSecMs := int64(0)
	acc.Apply("100", "10", true, "buy", 0, noQ, q) // large size trade at t=0
	acc.Apply("200", "1", true, "buy", 500, q, q)   // small size trade at t=500
	bar := acc.CurrentBar(tsSecMs, false)
	require.NotNil(t, bar.TWAP)
	// VWAP would be (100*10+200*1)/11≈109.09; TWAP should be 150.
	assert.InDelta(t, 150.0, *bar.TWAP, 1e-9, "TWAP is time-weighted not volume-weighted")
}

func TestAccumulator_Apply_TWAP_NilNoTrades(t *testing.T) {
	acc, _ := newAcc(t)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.TWAP, "TWAP nil when no trades")
}

func TestAccumulator_CurrentBar_ZeroTrades_NilFields(t *testing.T) {
	acc, _ := newAcc(t)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.Open)
	assert.Nil(t, bar.High)
	assert.Nil(t, bar.Low)
	assert.Nil(t, bar.Close)
	assert.Nil(t, bar.Volume)
	assert.Nil(t, bar.QuoteVolume)
	assert.Nil(t, bar.TWAP)
	assert.Equal(t, 0, bar.TradeCount)
}

func TestAccumulator_IncrementGap(t *testing.T) {
	acc, _ := newAcc(t)
	acc.IncrementGap()
	acc.IncrementGap()
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Equal(t, 2, bar.GapCount)
}

func TestAccumulator_Reset_ClearsAll(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	acc.Apply("100", "1", true, "buy", 0, noQ, q)
	acc.IncrementGap()
	acc.Reset()

	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.Open)
	assert.Equal(t, 0, bar.TradeCount)
	assert.Equal(t, 0, bar.GapCount)
	assert.Nil(t, bar.OFI, "OFI must be nil after Reset with no ticks")
	assert.Nil(t, bar.BestBidOpen)
	assert.Nil(t, bar.BestBid)
}

func TestAccumulator_Apply_OFIDelta_Accumulated(t *testing.T) {
	acc, _ := newAcc(t)
	// Two OB deltas: bid qty increases by 1 each → OFI delta = +1 each
	q0 := bq("100", "1", "101", "1")
	q1 := bq("100", "2", "101", "1")
	q2 := bq("100", "3", "101", "1")
	acc.Apply("0", "0", false, "", 0, q0, q1) // Δ_bid=+1, Δ_ask=0 → OFI=1
	acc.Apply("0", "0", false, "", 0, q1, q2) // Δ_bid=+1, Δ_ask=0 → OFI=1
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.OFI)
	assert.InDelta(t, 2.0, *bar.OFI, 1e-9)
}

func TestAccumulator_Apply_QuoteVolume(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	// quote_volume = sum(price * size): 100*2 + 110*3 = 530
	acc.Apply("100", "2", true, "buy", 0, noQ, q)
	acc.Apply("110", "3", true, "buy", 0, q, q)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.QuoteVolume)
	assert.InDelta(t, 530.0, *bar.QuoteVolume, 1e-9)
}

func TestAccumulator_IsPartial_Flag(t *testing.T) {
	acc, _ := newAcc(t)
	assert.True(t, acc.CurrentBar(epoch.UnixMilli(), true).IsPartial)
	assert.False(t, acc.CurrentBar(epoch.UnixMilli(), false).IsPartial)
}

// ── BarReset / Reset ──────────────────────────────────────────────────────────

func TestAccumulator_BarReset_ClearsPerBarState(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	acc.Apply("0", "0", false, "", 0, noQ, q)
	acc.Apply("100", "1", true, "buy", 0, q, q)
	acc.IncrementGap()
	acc.BarReset()

	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.BestBidOpen, "open quote cleared by BarReset")
	assert.Nil(t, bar.BestBid, "close quote cleared by BarReset")
	assert.Nil(t, bar.MidPriceOpen, "mid-price cleared by BarReset")
	assert.Nil(t, bar.Open, "OHLCV cleared by BarReset")
	assert.Equal(t, 0, bar.GapCount, "gapCount cleared by BarReset")
}

func TestAccumulator_BarReset_NextBarStartsFresh(t *testing.T) {
	acc, _ := newAcc(t)
	q1 := bq("100", "1", "102", "1") // mid=101
	q2 := bq("200", "1", "204", "1") // mid=202
	acc.Apply("0", "0", false, "", 0, noQ, q1)
	acc.BarReset()
	// After BarReset, next bar starts with fresh mid state
	acc.Apply("0", "0", false, "", 0, noQ, q2)

	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.MidPriceOpen)
	assert.InDelta(t, 202.0, *bar.MidPriceOpen, 1e-9, "open mid in new bar should be from q2, not q1")
}

func TestAccumulator_Reset_ClearsAllState(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	acc.Apply("0", "0", false, "", 0, noQ, q)
	acc.Reset()

	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.BestBidOpen)
	assert.Nil(t, bar.MidPriceOpen)
	assert.Equal(t, 0, bar.GapCount)
}

func TestAccumulator_BarReset_ClearsGapCount(t *testing.T) {
	acc, _ := newAcc(t)
	acc.IncrementGap()
	acc.IncrementGap()
	acc.BarReset()

	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Equal(t, 0, bar.GapCount)
}

// ── Mid-price tracking ────────────────────────────────────────────────────────

func TestAccumulator_MidPrice_SetOnFirstTick(t *testing.T) {
	acc, _ := newAcc(t)
	q1 := bq("100", "1", "102", "1") // mid=101
	acc.Apply("0", "0", false, "", 0, noQ, q1)

	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.MidPriceOpen)
	assert.InDelta(t, 101.0, *bar.MidPriceOpen, 1e-9)
}

func TestAccumulator_MidPrice_HighLow(t *testing.T) {
	acc, _ := newAcc(t)
	q1 := bq("100", "1", "102", "1") // mid=101
	q2 := bq("104", "1", "108", "1") // mid=106
	q3 := bq("98", "1", "100", "1")  // mid=99
	acc.Apply("0", "0", false, "", 0, noQ, q1)
	acc.Apply("0", "0", false, "", 0, q1, q2)
	acc.Apply("0", "0", false, "", 0, q2, q3)

	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.MidPriceHigh)
	require.NotNil(t, bar.MidPriceLow)
	assert.InDelta(t, 106.0, *bar.MidPriceHigh, 1e-9)
	assert.InDelta(t, 99.0, *bar.MidPriceLow, 1e-9)
	assert.InDelta(t, 101.0, *bar.MidPriceOpen, 1e-9, "open stays at first tick")
}

func TestAccumulator_MidPrice_NilWhenNoOBTick(t *testing.T) {
	acc, _ := newAcc(t)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.MidPriceOpen)
	assert.Nil(t, bar.MidPriceHigh)
	assert.Nil(t, bar.MidPriceLow)
}

// ── VWMP ──────────────────────────────────────────────────────────────────────

func TestAccumulator_VWMP_NilWhenTradeBeforeOBTick(t *testing.T) {
	// Trade arrives before any valid OB quote — VWMP is nil because mid is unavailable.
	acc, _ := newAcc(t)
	acc.Apply("100", "1", true, "buy", 0, noQ, noQ) // no OB quote → hasMid=false
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Equal(t, 1, bar.TradeCount, "trade was recorded")
	assert.Nil(t, bar.VWMP, "VWMP nil when trade precedes first OB tick")
	assert.Nil(t, bar.EffectiveSpread, "EffectiveSpread nil when trade precedes first OB tick")
}

func TestAccumulator_VWMP_NilWhenNoTrades(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "102", "1")
	acc.Apply("0", "0", false, "", 0, noQ, q) // OB-only tick
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.VWMP)
}

func TestAccumulator_VWMP_SingleTrade(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "102", "1") // mid=101
	// Trade at 101.5, size 2 → VWMP = (101 * 2) / 2 = 101
	acc.Apply("101.5", "2", true, "buy", 0, noQ, q)

	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.VWMP)
	assert.InDelta(t, 101.0, *bar.VWMP, 1e-9)
}

func TestAccumulator_VWMP_MultipleTrades(t *testing.T) {
	acc, _ := newAcc(t)
	q1 := bq("100", "1", "102", "1") // mid=101
	q2 := bq("104", "1", "106", "1") // mid=105
	// trade1: mid=101, size=2; trade2: mid=105, size=3
	// VWMP = (101*2 + 105*3) / (2+3) = (202+315)/5 = 517/5 = 103.4
	acc.Apply("101.0", "2", true, "buy", 0, noQ, q1)
	acc.Apply("105.0", "3", true, "buy", 0, q1, q2)

	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.VWMP)
	assert.InDelta(t, 103.4, *bar.VWMP, 1e-9)
}

// ── Spread tracking ───────────────────────────────────────────────────────────

func TestAccumulator_Spread_NilWhenNoOBTick(t *testing.T) {
	acc, _ := newAcc(t)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.SpreadHigh)
	assert.Nil(t, bar.SpreadLow)
	assert.Nil(t, bar.SpreadMean)
}

func TestAccumulator_Spread_SingleTick(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "102", "1") // spread=2
	acc.Apply("0", "0", false, "", 0, noQ, q)

	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.SpreadHigh)
	require.NotNil(t, bar.SpreadLow)
	require.NotNil(t, bar.SpreadMean)
	assert.InDelta(t, 2.0, *bar.SpreadHigh, 1e-9)
	assert.InDelta(t, 2.0, *bar.SpreadLow, 1e-9)
	assert.InDelta(t, 2.0, *bar.SpreadMean, 1e-9)
}

func TestAccumulator_Spread_MultipleTicks(t *testing.T) {
	acc, _ := newAcc(t)
	q1 := bq("100", "1", "104", "1") // spread=4
	q2 := bq("100", "1", "101", "1") // spread=1
	q3 := bq("100", "1", "103", "1") // spread=3
	acc.Apply("0", "0", false, "", 0, noQ, q1)
	acc.Apply("0", "0", false, "", 0, q1, q2)
	acc.Apply("0", "0", false, "", 0, q2, q3)

	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.SpreadHigh)
	assert.InDelta(t, 4.0, *bar.SpreadHigh, 1e-9)
	assert.InDelta(t, 1.0, *bar.SpreadLow, 1e-9)
	// mean = (4+1+3)/3 = 2.666...
	assert.InDelta(t, 8.0/3.0, *bar.SpreadMean, 1e-9)
}

// ── Effective spread ─────────────────────────────────────────────────────────

func TestAccumulator_EffectiveSpread_NilWhenNoTrades(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "102", "1")
	acc.Apply("0", "0", false, "", 0, noQ, q) // OB-only tick
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.EffectiveSpread)
}

func TestAccumulator_EffectiveSpread_TradeContrib(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "102", "1") // mid=101
	// trade at 101.5: contrib = 2*|101.5-101| = 1.0
	acc.Apply("101.5", "1", true, "buy", 0, noQ, q)

	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.EffectiveSpread)
	assert.InDelta(t, 1.0, *bar.EffectiveSpread, 1e-9)
}

// ── Trade flow (buy_volume / buy_count) ──────────────────────────────────────

func TestAccumulator_BuyVolume_AllBuy(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	acc.Apply("100", "2", true, "buy", 0, noQ, q)
	acc.Apply("110", "3", true, "buy", 0, q, q)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.BuyVolume)
	require.NotNil(t, bar.BuyCount)
	assert.InDelta(t, 5.0, *bar.BuyVolume, 1e-9, "buy_volume == volume when all trades are buys")
	assert.Equal(t, 2, *bar.BuyCount)
}

func TestAccumulator_BuyVolume_AllSell(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	acc.Apply("100", "2", true, "sell", 0, noQ, q)
	acc.Apply("110", "3", true, "sell", 0, q, q)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.BuyVolume)
	assert.InDelta(t, 0.0, *bar.BuyVolume, 1e-9, "buy_volume == 0 when all trades are sells")
	assert.Equal(t, 0, *bar.BuyCount)
}

func TestAccumulator_BuyVolume_Mixed(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	acc.Apply("100", "2", true, "buy", 0, noQ, q)
	acc.Apply("110", "3", true, "sell", 0, q, q)
	acc.Apply("105", "1", true, "buy", 0, q, q)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.BuyVolume)
	assert.InDelta(t, 3.0, *bar.BuyVolume, 1e-9, "buy_volume = 2+1 from buy trades only")
	assert.Equal(t, 2, *bar.BuyCount)
}

func TestAccumulator_BuyVolume_OBDeltaIgnored(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	acc.Apply("0", "0", false, "buy", 0, noQ, q) // side ignored for OB deltas
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.BuyVolume, "nil when TradeCount==0")
	assert.Nil(t, bar.BuyCount, "nil when TradeCount==0")
}

func TestAccumulator_BuyVolume_NilWhenNoTrades(t *testing.T) {
	acc, _ := newAcc(t)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.BuyVolume)
	assert.Nil(t, bar.BuyCount)
}

func TestAccumulator_BarReset_ClearsBuyState(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	acc.Apply("100", "2", true, "buy", 0, noQ, q)
	acc.BarReset()
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.BuyVolume, "buy state cleared by BarReset")
	assert.Nil(t, bar.BuyCount, "buy state cleared by BarReset")
}

// ── OB Depth snapshot ─────────────────────────────────────────────────────────

func TestAccumulator_SetOpenDepth_PopulatesOpenFields(t *testing.T) {
	acc, _ := newAcc(t)
	d := features.DepthSnapshot{
		BidL1: 1.0, AskL1: 2.0,
		BidL2: 3.0, AskL2: 4.0,
		BidTop10: 7.0, AskTop10: 8.0,
		BidTotal: 9.0, AskTotal: 10.0,
		HasBidVolume: true, HasAskVolume: true,
	}
	acc.SetOpenDepth(d)

	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.BidDepthL1Open)
	assert.InDelta(t, 1.0, *bar.BidDepthL1Open, 1e-9)
	assert.InDelta(t, 2.0, *bar.AskDepthL1Open, 1e-9)
	assert.InDelta(t, 3.0, *bar.BidDepthL2Open, 1e-9)
	assert.InDelta(t, 4.0, *bar.AskDepthL2Open, 1e-9)
	assert.InDelta(t, 7.0, *bar.BidDepthTop10Open, 1e-9)
	assert.InDelta(t, 8.0, *bar.AskDepthTop10Open, 1e-9)
	assert.InDelta(t, 9.0, *bar.BidDepthTotalOpen, 1e-9)
	assert.InDelta(t, 10.0, *bar.AskDepthTotalOpen, 1e-9)
	// WeightedBidPrice comes from close depth, not open
	assert.Nil(t, bar.WeightedBidPrice)
}

func TestAccumulator_SetCloseDepth_PopulatesCloseAndWeightedFields(t *testing.T) {
	acc, _ := newAcc(t)
	d := features.DepthSnapshot{
		BidL1: 2.5, AskL1: 1.5,
		BidL2: 5.0, AskL2: 3.0,
		BidTop10: 10.0, AskTop10: 7.5,
		BidTotal: 10.0, AskTotal: 7.5,
		WeightedBidPrice: 99.5, WeightedAskPrice: 100.5,
		HasBidVolume: true, HasAskVolume: true,
	}
	acc.SetCloseDepth(d)

	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.BidDepthL1Close)
	assert.InDelta(t, 2.5, *bar.BidDepthL1Close, 1e-9)
	assert.InDelta(t, 7.5, *bar.AskDepthTotalClose, 1e-9)
	require.NotNil(t, bar.WeightedBidPrice)
	assert.InDelta(t, 99.5, *bar.WeightedBidPrice, 1e-9)
	assert.InDelta(t, 100.5, *bar.WeightedAskPrice, 1e-9)
}

func TestAccumulator_DepthFields_NilWhenNotSet(t *testing.T) {
	acc, _ := newAcc(t)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.BidDepthL1Open)
	assert.Nil(t, bar.BidDepthL1Close)
	assert.Nil(t, bar.WeightedBidPrice)
}

func TestAccumulator_WeightedPrice_NilWhenNoVolume(t *testing.T) {
	acc, _ := newAcc(t)
	d := features.DepthSnapshot{
		BidTotal: 0, AskTotal: 0,
		HasBidVolume: false, HasAskVolume: false,
	}
	acc.SetCloseDepth(d)

	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.NotNil(t, bar.BidDepthTotalClose, "close depth set, so fields present even if zero")
	assert.Nil(t, bar.WeightedBidPrice, "nil when no bid volume")
	assert.Nil(t, bar.WeightedAskPrice, "nil when no ask volume")
	assert.Nil(t, bar.DepthTo1PctBid, "nil when no bid volume")
	assert.Nil(t, bar.DepthTo1PctAsk, "nil when no ask volume")
}

func TestAccumulator_DepthTo1Pct_NilWhenNotSet(t *testing.T) {
	acc, _ := newAcc(t)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.DepthTo1PctBid, "nil when hasCloseDepth=false")
	assert.Nil(t, bar.DepthTo1PctAsk, "nil when hasCloseDepth=false")
}

func TestAccumulator_SetCloseDepth_PopulatesDepthTo1Pct(t *testing.T) {
	acc, _ := newAcc(t)
	d := features.DepthSnapshot{
		DepthTo1PctBid: 12.5,
		DepthTo1PctAsk: 8.0,
		HasBidVolume:   true,
		HasAskVolume:   true,
	}
	acc.SetCloseDepth(d)

	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.DepthTo1PctBid)
	require.NotNil(t, bar.DepthTo1PctAsk)
	assert.InDelta(t, 12.5, *bar.DepthTo1PctBid, 1e-9)
	assert.InDelta(t, 8.0, *bar.DepthTo1PctAsk, 1e-9)
}

func TestAccumulator_BarReset_ClearsDepthState(t *testing.T) {
	acc, _ := newAcc(t)
	d := features.DepthSnapshot{BidL1: 5.0, BidTotal: 5.0, HasBidVolume: true}
	acc.SetOpenDepth(d)
	acc.SetCloseDepth(d)
	acc.BarReset()

	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.BidDepthL1Open, "open depth cleared by BarReset")
	assert.Nil(t, bar.BidDepthL1Close, "close depth cleared by BarReset")
}

func TestAccumulator_EffectiveSpread_MeanOverTrades(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "102", "1") // mid=101
	// trade1 at 101.5: contrib=1.0; trade2 at 102.0: contrib=2*|102-101|=2.0
	// mean = (1.0+2.0)/2 = 1.5
	acc.Apply("101.5", "1", true, "buy", 0, noQ, q)
	acc.Apply("102.0", "1", true, "buy", 0, q, q)

	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.EffectiveSpread)
	assert.InDelta(t, 1.5, *bar.EffectiveSpread, 1e-9)
}

// ─── Block Trade Tests ─────────────────────────────────────────────────────

func TestAccumulator_ApplyBlockTrade_BuyAccumulates(t *testing.T) {
	acc, _ := newAcc(t)
	acc.ApplyBlockTrade("buy", 5.5)
	acc.ApplyBlockTrade("buy", 3.2)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.BlockBuyVolume)
	assert.InDelta(t, 8.7, *bar.BlockBuyVolume, 1e-9)
	require.NotNil(t, bar.LargeBidOrders)
	assert.Equal(t, 2, *bar.LargeBidOrders)
}

func TestAccumulator_ApplyBlockTrade_SellAccumulates(t *testing.T) {
	acc, _ := newAcc(t)
	acc.ApplyBlockTrade("sell", 7.1)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.BlockSellVolume)
	assert.InDelta(t, 7.1, *bar.BlockSellVolume, 1e-9)
	require.NotNil(t, bar.LargeAskOrders)
	assert.Equal(t, 1, *bar.LargeAskOrders)
}

func TestAccumulator_ApplyBlockTrade_MixedSides(t *testing.T) {
	acc, _ := newAcc(t)
	acc.ApplyBlockTrade("buy", 4.0)
	acc.ApplyBlockTrade("sell", 6.0)
	acc.ApplyBlockTrade("buy", 2.0)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.BlockBuyVolume)
	assert.InDelta(t, 6.0, *bar.BlockBuyVolume, 1e-9)
	require.NotNil(t, bar.BlockSellVolume)
	assert.InDelta(t, 6.0, *bar.BlockSellVolume, 1e-9)
	require.NotNil(t, bar.LargeBidOrders)
	assert.Equal(t, 2, *bar.LargeBidOrders)
	require.NotNil(t, bar.LargeAskOrders)
	assert.Equal(t, 1, *bar.LargeAskOrders)
}

func TestAccumulator_ApplyBlockTrade_NilWhenNoBlockTrades(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	acc.Apply("100", "1", true, "buy", 0, noQ, q)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.BlockBuyVolume, "nil when no block trades")
	assert.Nil(t, bar.BlockSellVolume, "nil when no block trades")
	assert.Nil(t, bar.LargeBidOrders, "nil when no block trades")
	assert.Nil(t, bar.LargeAskOrders, "nil when no block trades")
}

func TestAccumulator_ApplyBlockTrade_ClearedByBarReset(t *testing.T) {
	acc, _ := newAcc(t)
	acc.ApplyBlockTrade("buy", 9.0)
	acc.BarReset()
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.BlockBuyVolume, "block state must be cleared by BarReset")
	assert.Nil(t, bar.LargeBidOrders, "block state must be cleared by BarReset")
}

// ─── Trade Distribution Tests ──────────────────────────────────────────────

func TestAccumulator_MaxTradeSize(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	acc.Apply("100", "3.5", true, "buy", 1000, noQ, q)
	acc.Apply("101", "7.2", true, "buy", 1100, q, q)
	acc.Apply("102", "2.0", true, "sell", 1200, q, q)
	bar := acc.CurrentBar(1000, false)
	require.NotNil(t, bar.MaxTradeSize)
	assert.InDelta(t, 7.2, *bar.MaxTradeSize, 1e-9)
}

func TestAccumulator_MaxTradeSize_NilNoTrades(t *testing.T) {
	acc, _ := newAcc(t)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.MaxTradeSize)
}

func TestAccumulator_FirstLastTradeOffset(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	tsSecMs := int64(1000000)
	acc.Apply("100", "1", true, "buy", tsSecMs+100, noQ, q)
	acc.Apply("101", "2", true, "sell", tsSecMs+750, q, q)
	bar := acc.CurrentBar(tsSecMs, false)
	require.NotNil(t, bar.FirstTradeOffsetMs)
	require.NotNil(t, bar.LastTradeOffsetMs)
	assert.Equal(t, 100, *bar.FirstTradeOffsetMs)
	assert.Equal(t, 750, *bar.LastTradeOffsetMs)
}

func TestAccumulator_MaxConsecutiveRun_AllBuy(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	acc.Apply("100", "1", true, "buy", 1000, noQ, q)
	acc.Apply("101", "1", true, "buy", 1100, q, q)
	acc.Apply("102", "1", true, "buy", 1200, q, q)
	bar := acc.CurrentBar(1000, false)
	require.NotNil(t, bar.MaxConsecutiveRun)
	assert.Equal(t, 3, *bar.MaxConsecutiveRun)
}

func TestAccumulator_MaxConsecutiveRun_Mixed(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	// buy,sell,buy,buy → runs: 1,1,2 → max=2
	acc.Apply("100", "1", true, "buy", 1000, noQ, q)
	acc.Apply("101", "1", true, "sell", 1100, q, q)
	acc.Apply("102", "1", true, "buy", 1200, q, q)
	acc.Apply("103", "1", true, "buy", 1300, q, q)
	bar := acc.CurrentBar(1000, false)
	require.NotNil(t, bar.MaxConsecutiveRun)
	assert.Equal(t, 2, *bar.MaxConsecutiveRun, "max run is 2 (last 2 buys)")
}

func TestAccumulator_MaxConsecutiveRun_Alternating(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	// buy,sell,buy,sell → runs: all 1 → max=1
	acc.Apply("100", "1", true, "buy", 1000, noQ, q)
	acc.Apply("101", "1", true, "sell", 1100, q, q)
	acc.Apply("102", "1", true, "buy", 1200, q, q)
	acc.Apply("103", "1", true, "sell", 1300, q, q)
	bar := acc.CurrentBar(1000, false)
	require.NotNil(t, bar.MaxConsecutiveRun)
	assert.Equal(t, 1, *bar.MaxConsecutiveRun, "alternating sides all have run=1")
}

func TestAccumulator_TradeClustering_NilSingleTrade(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	acc.Apply("100", "1", true, "buy", 1000, noQ, q)
	bar := acc.CurrentBar(1000, false)
	assert.Nil(t, bar.TradeClustering, "nil for single trade")
}

func TestAccumulator_TradeClustering_UniformIntervals(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	// 3 trades at 0,500,1000 → intervals=[500,500] → uniform → Gini=0
	acc.Apply("100", "1", true, "buy", 0, noQ, q)
	acc.Apply("101", "1", true, "buy", 500, q, q)
	acc.Apply("102", "1", true, "buy", 1000, q, q)
	bar := acc.CurrentBar(0, false)
	require.NotNil(t, bar.TradeClustering)
	assert.InDelta(t, 0.0, *bar.TradeClustering, 1e-9, "uniform intervals → Gini≈0")
}

func TestAccumulator_TradeClustering_ClusteredIntervals(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	// 3 trades at 0,1,1000 → intervals=[1,999] → highly clustered → Gini close to 1
	acc.Apply("100", "1", true, "buy", 0, noQ, q)
	acc.Apply("101", "1", true, "buy", 1, q, q)
	acc.Apply("102", "1", true, "buy", 1000, q, q)
	bar := acc.CurrentBar(0, false)
	require.NotNil(t, bar.TradeClustering)
	// intervals=[1,999]: sorted; Gini = ((2*0-2+1)*1 + (2*1-2+1)*999) / (2*1000)
	// = ((-1)*1 + (1)*999) / 2000 = (-1+999)/2000 = 998/2000 = 0.499
	assert.InDelta(t, 0.499, *bar.TradeClustering, 1e-3)
}

// ─── Volatility Tests ──────────────────────────────────────────────────────

func TestAccumulator_RealizedVol_NilBelow2Observations(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "102", "1") // mid=101
	acc.Apply("0", "0", false, "", 0, noQ, q) // first tick sets lastMidPrice; no return yet
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.RealizedVol, "nil when only 1 mid observation (need ≥2 returns)")
}

func TestAccumulator_RealizedVol_ComputedWith2Returns(t *testing.T) {
	acc, _ := newAcc(t)
	// mid1=100, mid2=110, mid3=90 → returns: ln(1.1), ln(90/110) — different returns ensure M2>0
	q1 := bq("99", "1", "101", "1")  // mid=100
	q2 := bq("109", "1", "111", "1") // mid=110
	q3 := bq("89", "1", "91", "1")   // mid=90
	acc.Apply("0", "0", false, "", 0, noQ, q1)
	acc.Apply("0", "0", false, "", 0, q1, q2)
	acc.Apply("0", "0", false, "", 0, q2, q3)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.RealizedVol, "should have vol with 2 returns")
	assert.True(t, *bar.RealizedVol > 0, "realized vol must be positive")
}

func TestAccumulator_RealizedSkewness_NilBelow3Observations(t *testing.T) {
	acc, _ := newAcc(t)
	q1 := bq("99", "1", "101", "1")
	q2 := bq("109", "1", "111", "1")
	acc.Apply("0", "0", false, "", 0, noQ, q1)
	acc.Apply("0", "0", false, "", 0, q1, q2)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.RealizedSkewness, "nil when only 1 return (need ≥3 for skewness)")
}

func TestAccumulator_RealizedSkewness_NilZeroVariance(t *testing.T) {
	acc, _ := newAcc(t)
	// Same mid across all ticks → zero variance → skewness nil
	q := bq("100", "1", "102", "1") // mid=101
	acc.Apply("0", "0", false, "", 0, noQ, q)
	acc.Apply("0", "0", false, "", 0, q, q)
	acc.Apply("0", "0", false, "", 0, q, q)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.RealizedSkewness, "nil when zero variance (all same mid)")
}

func TestAccumulator_UptickDowntickCount(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "102", "1")
	// Trades: 100→105 (up), 105→103 (down), 103→103 (zero, neither)
	acc.Apply("100", "1", true, "buy", 1000, noQ, q)
	acc.Apply("105", "1", true, "buy", 1100, q, q)
	acc.Apply("103", "1", true, "sell", 1200, q, q)
	acc.Apply("103", "1", true, "sell", 1300, q, q)
	bar := acc.CurrentBar(1000, false)
	require.NotNil(t, bar.UptickCount)
	require.NotNil(t, bar.DowntickCount)
	assert.Equal(t, 1, *bar.UptickCount, "one uptick: 100→105")
	assert.Equal(t, 1, *bar.DowntickCount, "one downtick: 105→103")
}

func TestAccumulator_UptickDowntick_NilNoTrades(t *testing.T) {
	acc, _ := newAcc(t)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.UptickCount)
	assert.Nil(t, bar.DowntickCount)
}

func TestAccumulator_VolatilityState_ClearedByBarReset(t *testing.T) {
	acc, _ := newAcc(t)
	q1 := bq("99", "1", "101", "1")
	q2 := bq("109", "1", "111", "1")
	q3 := bq("120", "1", "122", "1")
	acc.Apply("0", "0", false, "", 0, noQ, q1)
	acc.Apply("0", "0", false, "", 0, q1, q2)
	acc.Apply("0", "0", false, "", 0, q2, q3)
	acc.BarReset()
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.RealizedVol, "realized vol cleared by BarReset")
}

func TestAccumulator_LastMidPrice_SurvivesBarReset(t *testing.T) {
	acc, _ := newAcc(t)
	q1 := bq("99", "1", "101", "1")  // mid=100
	q2 := bq("119", "1", "121", "1") // mid=120
	acc.Apply("0", "0", false, "", 0, noQ, q1) // sets lastMidPrice=100
	acc.BarReset()
	// After BarReset, lastMidPrice=100 survives; one Apply with q2 gives 1 return → vol nil
	acc.Apply("0", "0", false, "", 0, noQ, q2) // r=ln(120/100); nMidReturns=1
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	// nMidReturns=1 → still need ≥2 for vol
	assert.Nil(t, bar.RealizedVol, "only 1 return in new bar → vol still nil")
}

func TestAccumulator_LastMidPrice_ClearedByReset(t *testing.T) {
	acc, _ := newAcc(t)
	q1 := bq("99", "1", "101", "1")  // mid=100
	q2 := bq("109", "1", "111", "1") // mid=110
	q3 := bq("119", "1", "121", "1") // mid=120
	acc.Apply("0", "0", false, "", 0, noQ, q1)
	acc.Reset() // clears lastMidPrice
	acc.Apply("0", "0", false, "", 0, noQ, q2)
	acc.Apply("0", "0", false, "", 0, q2, q3)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	// After Reset, new bar starts fresh — only q2→q3 return counted
	assert.Nil(t, bar.RealizedVol, "only 1 return after Reset → vol nil")
}

func TestAccumulator_TradeClustering_ClearedByBarReset(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	acc.Apply("100", "1", true, "buy", 1000, noQ, q)
	acc.Apply("101", "1", true, "buy", 1500, q, q)
	acc.Apply("102", "1", true, "buy", 1800, q, q)
	acc.BarReset()
	// After BarReset, single trade in new bar must not see old timestamps
	acc.Apply("103", "1", true, "buy", 2000, noQ, q)
	bar := acc.CurrentBar(2000, false)
	assert.Nil(t, bar.TradeClustering, "single trade after BarReset → clustering nil")
}

func TestAccumulator_UptickDowntick_ClearedByBarReset(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	acc.Apply("100", "1", true, "buy", 1000, noQ, q)
	acc.Apply("105", "1", true, "buy", 1100, q, q) // uptick
	acc.Apply("103", "1", true, "sell", 1200, q, q) // downtick
	acc.BarReset()
	// After BarReset, new bar starts with zero up/down counts
	acc.Apply("200", "1", true, "buy", 2000, noQ, q)
	bar := acc.CurrentBar(2000, false)
	require.NotNil(t, bar.UptickCount)
	assert.Equal(t, 0, *bar.UptickCount, "uptick cleared by BarReset")
	require.NotNil(t, bar.DowntickCount)
	assert.Equal(t, 0, *bar.DowntickCount, "downtick cleared by BarReset")
}

// --- OB Activity tests ---

func TestAccumulator_OBActivity_IncrementOBAdd(t *testing.T) {
	acc, _ := newAcc(t)
	acc.IncrementOBAdd("buy", 2.5)
	acc.IncrementOBAdd("buy", 1.5)
	acc.IncrementOBAdd("sell", 3.0)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.BidOrderArrivals)
	assert.Equal(t, 2, *bar.BidOrderArrivals)
	require.NotNil(t, bar.AskOrderArrivals)
	assert.Equal(t, 1, *bar.AskOrderArrivals)
	require.NotNil(t, bar.AvgBidOrderSize)
	assert.InDelta(t, 2.0, *bar.AvgBidOrderSize, 1e-9) // (2.5+1.5)/2
	require.NotNil(t, bar.AvgAskOrderSize)
	assert.InDelta(t, 3.0, *bar.AvgAskOrderSize, 1e-9)
}

func TestAccumulator_OBActivity_AvgOrderSize_NilWhenNoArrivals(t *testing.T) {
	acc, _ := newAcc(t)
	acc.IncrementOBAdd("buy", 5.0)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.AvgBidOrderSize)
	assert.Nil(t, bar.AvgAskOrderSize, "no ask arrivals → AvgAskOrderSize must be nil")
}

func TestAccumulator_OBActivity_IncrementOBCancel(t *testing.T) {
	acc, _ := newAcc(t)
	acc.IncrementOBCancel("buy")
	acc.IncrementOBCancel("buy")
	acc.IncrementOBCancel("sell")
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.BidCancelCount)
	assert.Equal(t, 2, *bar.BidCancelCount)
	require.NotNil(t, bar.AskCancelCount)
	assert.Equal(t, 1, *bar.AskCancelCount)
}

func TestAccumulator_OBActivity_IncrementOBModify(t *testing.T) {
	acc, _ := newAcc(t)
	acc.IncrementOBModify()
	acc.IncrementOBModify()
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.OBModifyCount)
	assert.Equal(t, 2, *bar.OBModifyCount)
}

func TestAccumulator_OBActivity_NilWhenNoActivity(t *testing.T) {
	acc, _ := newAcc(t)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.BidOrderArrivals, "no OB activity → nil")
	assert.Nil(t, bar.BestBidChanges, "no quote activity → nil")
	assert.Nil(t, bar.QuoteStuffRatio, "no trades → nil")
}

func TestAccumulator_BestBidChanges_SingleTick_Zero(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	acc.Apply("100", "1", false, "buy", 0, noQ, q)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.BestBidChanges)
	assert.Equal(t, 0, *bar.BestBidChanges, "first tick: no prior quote, so 0 changes")
	require.NotNil(t, bar.BestAskChanges)
	assert.Equal(t, 0, *bar.BestAskChanges)
}

func TestAccumulator_BestBidChanges_IncrementOnChange(t *testing.T) {
	acc, _ := newAcc(t)
	q0 := bq("100", "1", "101", "1")
	q1 := bq("102", "1", "103", "1") // both bid and ask changed
	q2 := bq("102", "2", "103", "2") // same bid/ask price, size changed — NO price change
	acc.Apply("100", "1", false, "buy", 0, noQ, q0)
	acc.Apply("100", "1", false, "buy", 0, q0, q1)
	acc.Apply("100", "1", false, "buy", 0, q1, q2)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.BestBidChanges)
	assert.Equal(t, 1, *bar.BestBidChanges, "one bid price change: 100→102")
	require.NotNil(t, bar.BestAskChanges)
	assert.Equal(t, 1, *bar.BestAskChanges, "one ask price change: 101→103")
}

func TestAccumulator_QuoteStuffRatio_NilWhenNoTrades(t *testing.T) {
	acc, _ := newAcc(t)
	acc.IncrementOBAdd("buy", 1.0)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.QuoteStuffRatio, "trade_count==0 → nil")
}

func TestAccumulator_QuoteStuffRatio_Correct(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	acc.Apply("100", "2", true, "buy", 0, noQ, q) // 1 trade
	acc.IncrementOBAdd("buy", 1.0)                // 1 arrival
	acc.IncrementOBAdd("sell", 1.0)               // 1 arrival
	acc.IncrementOBCancel("buy")                  // 1 cancel
	// ratio = (arrivals+cancels)/trades = (2+1)/1 = 3.0
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.QuoteStuffRatio)
	assert.InDelta(t, 3.0, *bar.QuoteStuffRatio, 1e-9)
}

func TestAccumulator_OBActivity_ClearedByBarReset(t *testing.T) {
	acc, _ := newAcc(t)
	acc.IncrementOBAdd("buy", 5.0)
	acc.IncrementOBCancel("sell")
	acc.IncrementOBModify()
	acc.BarReset()
	// After BarReset, OB activity state is cleared. Add a fresh OB event so
	// hasOBActivity=true and the counters appear as 0 (cleared).
	acc.IncrementOBAdd("sell", 3.0)
	acc.BarReset()
	// Now in a bar with zero OB activity — all OB activity fields must be nil.
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.BidOrderArrivals, "OB arrivals nil when hasOBActivity=false")
	assert.Nil(t, bar.AskCancelCount, "OB cancel nil when hasOBActivity=false")
	assert.Nil(t, bar.OBModifyCount, "OB modify nil when hasOBActivity=false")
}

func TestAccumulator_OBActivity_ZeroCountsWhenOBActivity(t *testing.T) {
	acc, _ := newAcc(t)
	// One bid arrival only — ask arrivals/cancels/modifies should be 0 but non-nil.
	acc.IncrementOBAdd("buy", 7.0)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.BidOrderArrivals)
	assert.Equal(t, 1, *bar.BidOrderArrivals)
	require.NotNil(t, bar.AskOrderArrivals)
	assert.Equal(t, 0, *bar.AskOrderArrivals, "ask arrivals 0 but non-nil when hasOBActivity")
	require.NotNil(t, bar.OBModifyCount)
	assert.Equal(t, 0, *bar.OBModifyCount, "modify count 0 but non-nil when hasOBActivity")
}

// --- Trade Microstructure tests ---

func TestAccumulator_NumTradePriceLevels_SamePrice(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	acc.Apply("100", "1", true, "buy", 0, noQ, q)
	acc.Apply("100", "2", true, "sell", 0, q, q)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.NumTradePriceLevels)
	assert.Equal(t, 1, *bar.NumTradePriceLevels)
}

func TestAccumulator_NumTradePriceLevels_DistinctPrices(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	acc.Apply("100", "1", true, "buy", 100, noQ, q)
	acc.Apply("200", "1", true, "sell", 200, q, q)
	acc.Apply("300", "1", true, "buy", 300, q, q)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.NumTradePriceLevels)
	assert.Equal(t, 3, *bar.NumTradePriceLevels)
}

func TestAccumulator_NumTradePriceLevels_NilNoTrades(t *testing.T) {
	acc, _ := newAcc(t)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.NumTradePriceLevels)
}

func TestAccumulator_InterTradeIntervalStdMs_NilSingleTrade(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	acc.Apply("100", "1", true, "buy", 1000, noQ, q)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.InterTradeIntervalStdMs)
}

func TestAccumulator_InterTradeIntervalStdMs_ZeroForEqualIntervals(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	acc.Apply("100", "1", true, "buy", 1000, noQ, q)
	acc.Apply("100", "1", true, "buy", 1010, q, q)
	acc.Apply("100", "1", true, "buy", 1020, q, q)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.InterTradeIntervalStdMs)
	assert.InDelta(t, 0.0, *bar.InterTradeIntervalStdMs, 1e-9)
}

func TestAccumulator_InterTradeIntervalStdMs_KnownSeries(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	// intervals: 10, 30 → mean=20, variance=((10-20)²+(30-20)²)/2=100 → std=10
	acc.Apply("100", "1", true, "buy", 1000, noQ, q)
	acc.Apply("100", "1", true, "buy", 1010, q, q)
	acc.Apply("100", "1", true, "buy", 1040, q, q)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.InterTradeIntervalStdMs)
	assert.InDelta(t, 10.0, *bar.InterTradeIntervalStdMs, 1e-9)
}

func TestAccumulator_TradeSignAutocorr_NilSingleTrade(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	acc.Apply("100", "1", true, "buy", 0, noQ, q)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.TradeSignAutocorr)
}

func TestAccumulator_TradeSignAutocorr_NilAllSameSide(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	acc.Apply("100", "1", true, "buy", 0, noQ, q)
	acc.Apply("100", "1", true, "buy", 0, q, q)
	acc.Apply("100", "1", true, "buy", 0, q, q)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.TradeSignAutocorr, "all same sign → degenerate → nil")
}

func TestAccumulator_TradeSignAutocorr_BuySellBuy(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	// [buy, sell, buy] → signs = [+1, -1, +1]
	// pairs = [+1*-1, -1*+1] = [-1, -1] → sumSignPairs = -2
	// sumSigns = +1, firstSign = +1, lastSign = +1
	// nP = 2, sumX = sumSigns - lastSign = 1-1 = 0, sumY = sumSigns - firstSign = 1-1 = 0
	// meanX = 0, meanY = 0
	// cov = sumSignPairs/nP - 0 = -2/2 = -1
	// varX = 1 - 0 = 1, varY = 1 - 0 = 1
	// autocorr = -1 / sqrt(1*1) = -1
	acc.Apply("100", "1", true, "buy", 100, noQ, q)
	acc.Apply("100", "1", true, "sell", 200, q, q)
	acc.Apply("100", "1", true, "buy", 300, q, q)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.TradeSignAutocorr)
	assert.InDelta(t, -1.0, *bar.TradeSignAutocorr, 1e-9)
}

func TestAccumulator_Microstructure_ClearedByBarReset(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	acc.Apply("100", "1", true, "buy", 100, noQ, q)
	acc.Apply("200", "1", true, "sell", 200, q, q)
	acc.BarReset()
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.NumTradePriceLevels, "cleared by BarReset")
	assert.Nil(t, bar.TradeSignAutocorr, "cleared by BarReset")
	assert.Nil(t, bar.InterTradeIntervalStdMs, "cleared by BarReset")
}

// --- Quality fields tests (Story 7-5) ---

func TestAccumulator_BarCount_ThreeConsecutiveBars(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	// Bar 1
	acc.Apply("100", "1", true, "buy", 0, noQ, q)
	bar1 := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Equal(t, 1, bar1.BarCount)
	acc.BarReset()
	// Bar 2
	acc.Apply("100", "1", true, "buy", 0, noQ, q)
	bar2 := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Equal(t, 2, bar2.BarCount)
	acc.BarReset()
	// Bar 3
	acc.Apply("100", "1", true, "buy", 0, noQ, q)
	bar3 := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Equal(t, 3, bar3.BarCount)
}

func TestAccumulator_BarCount_ResetAfterReset(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	acc.Apply("100", "1", true, "buy", 0, noQ, q)
	acc.BarReset()
	acc.Apply("100", "1", true, "buy", 0, noQ, q)
	acc.BarReset()
	// Now reset (gap/snapshot)
	acc.Reset()
	// First bar after reset must be bar_count=1
	acc.Apply("100", "1", true, "buy", 0, noQ, q)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Equal(t, 1, bar.BarCount, "Reset() must restart bar_count at 1")
}

func TestAccumulator_OFI_NilForEmptyBar(t *testing.T) {
	acc, _ := newAcc(t)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.OFI, "OFI must be nil when no Apply() calls")
	assert.Nil(t, bar.OFIL1, "OFIL1 must be nil when no Apply() calls")
}

func TestAccumulator_OFI_NonNilForOBDeltaBar(t *testing.T) {
	acc, _ := newAcc(t)
	q0 := bq("100", "1", "101", "1")
	q1 := bq("100", "2", "101", "1")
	acc.Apply("0", "0", false, "", 0, q0, q1) // OB delta only
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.OFI, "OFI must be non-nil when OB delta tick received")
	assert.InDelta(t, 1.0, *bar.OFI, 1e-9)
}

func TestAccumulator_OFI_NonNilForTradeBar(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	acc.Apply("100", "1", true, "buy", 0, noQ, q)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.OFI, "OFI must be non-nil when trade tick received")
}

func TestAccumulator_SeedFromLastKnown_PopulatesOpenQuote(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "5", "101", "3")
	// First bar: apply a tick to establish lastKnownBid/Ask
	acc.Apply("100", "1", true, "buy", 0, noQ, q)
	acc.BarReset()
	// Second bar: no ticks; SeedFromLastKnown should fill BestBidOpen
	acc.SeedFromLastKnown()
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.BestBidOpen)
	assert.InDelta(t, 100.0, *bar.BestBidOpen, 1e-9)
	require.NotNil(t, bar.BestAskOpen)
	assert.InDelta(t, 101.0, *bar.BestAskOpen, 1e-9)
}

func TestAccumulator_SeedFromLastKnown_NoopWhenOpenQuoteAlreadySet(t *testing.T) {
	acc, _ := newAcc(t)
	q0 := bq("100", "1", "101", "1")
	q1 := bq("200", "1", "201", "1")
	acc.Apply("100", "1", false, "buy", 0, noQ, q0) // sets hasOpenQuote
	acc.SeedFromLastKnown()                          // should NOT overwrite open quote
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.BestBidOpen)
	assert.InDelta(t, 100.0, *bar.BestBidOpen, 1e-9, "open quote must not be overwritten")
	_ = q1
}

func TestAccumulator_SeedFromLastKnown_NoopWhenNoLastKnownOB(t *testing.T) {
	acc, _ := newAcc(t)
	// Fresh accumulator: no lastKnownOB
	acc.SeedFromLastKnown()
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.BestBidOpen, "no lastKnownOB → BestBidOpen still nil")
}

func TestAccumulator_EmptyBar_CloseQuoteNil(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("100", "1", "101", "1")
	// First bar: establishes lastKnownBid/Ask
	acc.Apply("100", "1", true, "buy", 0, noQ, q)
	acc.BarReset()
	// Second bar: empty
	acc.SeedFromLastKnown()
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.BestBid, "empty bar: close quote must be nil")
	assert.Nil(t, bar.OFI, "empty bar: OFI must be nil")
	assert.Nil(t, bar.BestBidChanges, "empty bar: no hasQuoteActivity → BestBidChanges nil")
}

// ── Footprint tests (Story 19.1) ─────────────────────────────────────────────

func TestAccumulator_Footprint_BuyTickAccumulates(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("67000", "1", "67001", "1")
	acc.Apply("67000.5", "0.1", true, "buy", 0, noQ, q)

	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.FootprintJSON)
	var m map[string]map[string]float64
	require.NoError(t, json.Unmarshal([]byte(*bar.FootprintJSON), &m))
	cell, ok := m["67000.5"]
	require.True(t, ok, "price key must be present")
	assert.InDelta(t, 0.1, cell["b"], 1e-9, "buy volume")
	assert.InDelta(t, 0.0, cell["s"], 1e-9, "sell volume must be zero")
}

func TestAccumulator_Footprint_SellTickAccumulates(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("67000", "1", "67001", "1")
	acc.Apply("67000.5", "0.2", true, "sell", 0, noQ, q)

	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.FootprintJSON)
	var m map[string]map[string]float64
	require.NoError(t, json.Unmarshal([]byte(*bar.FootprintJSON), &m))
	cell, ok := m["67000.5"]
	require.True(t, ok)
	assert.InDelta(t, 0.0, cell["b"], 1e-9, "buy volume must be zero")
	assert.InDelta(t, 0.2, cell["s"], 1e-9, "sell volume")
}

func TestAccumulator_Footprint_BothSidesAtSamePrice(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("67000", "1", "67001", "1")
	acc.Apply("67000.5", "0.3", true, "buy", 0, noQ, q)
	acc.Apply("67000.5", "0.7", true, "sell", 0, noQ, q)

	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.FootprintJSON)
	var m map[string]map[string]float64
	require.NoError(t, json.Unmarshal([]byte(*bar.FootprintJSON), &m))
	cell := m["67000.5"]
	assert.InDelta(t, 0.3, cell["b"], 1e-9, "buy volume accumulates")
	assert.InDelta(t, 0.7, cell["s"], 1e-9, "sell volume accumulates")
}

func TestAccumulator_Footprint_MultiplePriceLevels(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("67000", "1", "67001", "1")
	acc.Apply("67000.5", "1.0", true, "buy", 0, noQ, q)
	acc.Apply("67001.0", "2.0", true, "sell", 0, noQ, q)

	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.FootprintJSON)
	var m map[string]map[string]float64
	require.NoError(t, json.Unmarshal([]byte(*bar.FootprintJSON), &m))
	assert.Len(t, m, 2, "two distinct price levels")
	assert.InDelta(t, 1.0, m["67000.5"]["b"], 1e-9)
	assert.InDelta(t, 2.0, m["67001.0"]["s"], 1e-9)
}

func TestAccumulator_Footprint_BarResetClearsMap(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("67000", "1", "67001", "1")
	// Bar 1: buy 1.0 at 67000.5
	acc.Apply("67000.5", "1.0", true, "buy", 0, noQ, q)
	acc.BarReset()

	// Bar 2: buy 0.3 at a different price — must see only bar-2 data in FootprintJSON
	acc.Apply("68000.0", "0.3", true, "buy", 0, noQ, q)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.FootprintJSON, "bar after reset: FootprintJSON must not be nil when trades present")
	var m map[string]map[string]float64
	require.NoError(t, json.Unmarshal([]byte(*bar.FootprintJSON), &m))
	assert.Len(t, m, 1, "only the post-reset trade must appear — pre-reset data must be gone")
	assert.Contains(t, m, "68000.0", "only bar-2 price must be present")
	assert.NotContains(t, m, "67000.5", "bar-1 price must be absent after reset")
}

func TestAccumulator_Footprint_ZeroTradeBar_NilFields(t *testing.T) {
	acc, _ := newAcc(t)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.FootprintJSON, "zero-trade bar: FootprintJSON must be nil")
	assert.Nil(t, bar.SellVolume, "zero-trade bar: SellVolume must be nil")
	assert.Nil(t, bar.BuyVolume, "zero-trade bar: BuyVolume must be nil")
}

func TestAccumulator_Footprint_SellVolumeEqualsVolumeMinusBuyVolume(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("67000", "1", "67001", "1")
	acc.Apply("67000.5", "3.0", true, "buy", 0, noQ, q)
	acc.Apply("67000.5", "7.0", true, "sell", 0, noQ, q)

	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.Volume)
	require.NotNil(t, bar.BuyVolume)
	require.NotNil(t, bar.SellVolume)
	assert.InDelta(t, 10.0, *bar.Volume, 1e-9, "total volume")
	assert.InDelta(t, 3.0, *bar.BuyVolume, 1e-9, "buy volume")
	assert.InDelta(t, 7.0, *bar.SellVolume, 1e-9, "sell volume = volume - buy_volume")
}

// ── Value Area integration tests (Story 19.2) ────────────────────────────────

func TestAccumulator_ValueArea_FieldsSetOnBar(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("67000", "1", "67001", "1")
	// Two price levels so value area is well-defined.
	acc.Apply("67000.0", "3.0", true, "buy", 0, noQ, q)
	acc.Apply("67001.0", "7.0", true, "sell", 0, noQ, q)

	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar.POCPrice, "POCPrice must be non-nil when trades present")
	require.NotNil(t, bar.ValueAreaHigh, "ValueAreaHigh must be non-nil when trades present")
	require.NotNil(t, bar.ValueAreaLow, "ValueAreaLow must be non-nil when trades present")
	require.NotNil(t, bar.POCVolume, "POCVolume must be non-nil when trades present")
	// POC should be 67001.0 (vol=7, higher than 67000.0 vol=3)
	assert.InDelta(t, 67001.0, *bar.POCPrice, 1e-9, "poc at highest-volume price")
	assert.InDelta(t, 7.0, *bar.POCVolume, 1e-9, "poc volume")
}

func TestAccumulator_ValueArea_NilOnZeroTradeBar(t *testing.T) {
	acc, _ := newAcc(t)
	bar := acc.CurrentBar(epoch.UnixMilli(), false)
	assert.Nil(t, bar.POCPrice, "zero-trade bar: POCPrice must be nil")
	assert.Nil(t, bar.ValueAreaHigh, "zero-trade bar: ValueAreaHigh must be nil")
	assert.Nil(t, bar.ValueAreaLow, "zero-trade bar: ValueAreaLow must be nil")
	assert.Nil(t, bar.POCVolume, "zero-trade bar: POCVolume must be nil")
}

func TestAccumulator_ValueArea_ClearedByBarReset(t *testing.T) {
	acc, _ := newAcc(t)
	q := bq("67000", "1", "67001", "1")
	acc.Apply("67000.0", "5.0", true, "buy", 0, noQ, q)

	bar1 := acc.CurrentBar(epoch.UnixMilli(), false)
	require.NotNil(t, bar1.POCPrice, "bar1 must have POCPrice")

	acc.BarReset()

	// After reset with no new trades, value area fields must be nil.
	bar2 := acc.CurrentBar(epoch.UnixMilli()+1000, false)
	assert.Nil(t, bar2.POCPrice, "POCPrice must be nil after BarReset with no trades")
	assert.Nil(t, bar2.ValueAreaHigh, "ValueAreaHigh must be nil after BarReset with no trades")
	assert.Nil(t, bar2.ValueAreaLow, "ValueAreaLow must be nil after BarReset with no trades")
	assert.Nil(t, bar2.POCVolume, "POCVolume must be nil after BarReset with no trades")
}
