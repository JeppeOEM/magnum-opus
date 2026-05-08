// Package accumulator maintains per-symbol 1-second OHLCV + OFI bar state.
// Pure: zero IO, no time.Now(), no goroutines. Clock injected for testability.
// Single-goroutine ownership — the consumer goroutine per symbol owns this.
package accumulator

import (
	"math"
	"sort"
	"strconv"
	"time"

	"github.com/mrqdt/magnum-opus/candle-service/internal/features"
)

// Clock allows deterministic time injection in L1 tests.
type Clock interface {
	Now() time.Time
}

// Bar is an immutable snapshot of a completed (or partial) 1-second bar.
// Pointer fields are nil when no data is available (written as null to QuestDB).
type Bar struct {
	TsSecMs    int64  // Unix ms of the second boundary (floor to 1000ms)
	Exchange   string
	Symbol     string

	// OHLCV — nil if no trade ticks in this second
	Open        *float64
	High        *float64
	Low         *float64
	Close       *float64
	Volume      *float64
	QuoteVolume *float64
	TradeCount  int
	TWAP        *float64 // nil if Volume==0

	// OB best quotes: open (first tick of second), close (last known)
	BestBidOpen *float64
	BestAskOpen *float64
	BestBid     *float64
	BestAsk     *float64

	// Mid-price path — nil if no valid OB quote this second
	MidPriceOpen *float64
	MidPriceHigh *float64
	MidPriceLow  *float64
	VWMP         *float64 // nil if no trade ticks

	// Spread — nil if no valid OB quote this second
	SpreadHigh     *float64
	SpreadLow      *float64
	SpreadMean     *float64
	EffectiveSpread *float64 // nil if no trade ticks

	// OB depth at open (first OB tick of second)
	BidDepthL1Open    *float64
	AskDepthL1Open    *float64
	BidDepthL2Open    *float64
	AskDepthL2Open    *float64
	BidDepthTop10Open *float64
	AskDepthTop10Open *float64
	BidDepthTotalOpen *float64
	AskDepthTotalOpen *float64

	// OB depth at close (bar-flush snapshot)
	BidDepthL1Close    *float64
	AskDepthL1Close    *float64
	BidDepthL2Close    *float64
	AskDepthL2Close    *float64
	BidDepthTop10Close *float64
	AskDepthTop10Close *float64
	BidDepthTotalClose *float64
	AskDepthTotalClose *float64

	// Book shape — close-only (bar-flush snapshot)
	WeightedBidPrice *float64
	WeightedAskPrice *float64

	// Market impact — close-only (bar-flush snapshot)
	DepthTo1PctBid *float64
	DepthTo1PctAsk *float64

	// Trade flow — nil when TradeCount==0
	BuyVolume *float64
	BuyCount  *int

	// Block trades — nil when no block trades occurred or threshold unavailable
	BlockBuyVolume  *float64
	BlockSellVolume *float64
	LargeBidOrders  *int
	LargeAskOrders  *int

	// Trade distribution — nil when TradeCount == 0 (TradeClustering also nil when < 2 trades)
	MaxTradeSize       *float64
	FirstTradeOffsetMs *int
	LastTradeOffsetMs  *int
	TradeClustering    *float64
	MaxConsecutiveRun  *int

	// Volatility — see field-level nil conditions
	RealizedVol      *float64 // nil when < 2 mid-price observations
	RealizedSkewness *float64 // nil when < 3 observations or zero variance
	UptickCount      *int     // nil when TradeCount == 0
	DowntickCount    *int     // nil when TradeCount == 0

	// OB activity — nil when no OB data this second
	BidOrderArrivals *int
	AskOrderArrivals *int
	BidCancelCount   *int
	AskCancelCount   *int
	OBModifyCount    *int
	AvgBidOrderSize  *float64 // nil if BidOrderArrivals == 0
	AvgAskOrderSize  *float64 // nil if AskOrderArrivals == 0
	BestBidChanges   *int     // nil if no ticks with valid quotes this second
	BestAskChanges   *int     // nil if no ticks with valid quotes this second
	QuoteStuffRatio  *float64 // nil if TradeCount == 0

	// Trade microstructure — nil conditions per field
	TradeSignAutocorr       *float64 // nil when TradeCount < 2 or all same sign
	InterTradeIntervalStdMs *float64 // nil when TradeCount < 2
	NumTradePriceLevels     *int     // nil when TradeCount == 0

	// OFI — nil when no ticks were received this second
	OFI   *float64
	OFIL1 *float64

	// Quality
	IsPartial bool
	GapCount  int
	BarCount  int
}

// Accumulator maintains per-symbol 1-second OHLCV + OFI bar state.
type Accumulator struct {
	exchange string
	symbol   string
	clk      Clock

	// OHLCV running state
	open, high, low, close float64
	volumeSum              float64
	quoteVolSum            float64
	tradeCount             int

	// TWAP: true time-weighted average price (Σ(price×Δt) / Σ(Δt))
	twapNumer      float64
	twapDenom      float64
	lastTradeTsMs  int64

	// OB best quote tracking (float64 for computation)
	bestBidOpen, bestAskOpen float64
	bestBid, bestAsk         float64
	hasOpenQuote             bool
	hasCloseQuote            bool

	// OFI running sum
	ofiSum  float64
	hasTicks bool // set true on first Apply() call this bar

	// trade flow
	buyVolume float64
	buyCount  int

	// lastKnown OB state — survives BarReset, cleared by Reset
	lastKnownBid   float64
	lastKnownAsk   float64
	hasLastKnownOB bool

	// mid-price per-bar state
	midPriceOpen float64
	midPriceHigh float64
	midPriceLow  float64
	hasMidOpen   bool
	hasMidHL     bool // initialized separately from hasSpread to decouple mid and spread tracking

	// spread per-bar state
	spreadHigh  float64
	spreadLow   float64
	spreadSum   float64
	spreadCount int
	hasSpread   bool

	// VWMP: Σ(mid * tradeSize) / Σ(tradeSize) — trade ticks only
	vwmpNumer float64
	vwmpDenom float64

	// effective spread: mean of EffectiveSpreadContrib across trade ticks
	effectiveSpreadSum   float64
	effectiveSpreadCount int

	// OB depth snapshot state — set by accWriter, not computed internally
	openDepth    features.DepthSnapshot
	closeDepth   features.DepthSnapshot
	hasOpenDepth bool
	hasCloseDepth bool

	// Block trade accumulators
	blockBuyVolume  float64
	blockSellVolume float64
	largeBidCount   int
	largeAskCount   int
	hasBlockData    bool

	// Trade distribution
	maxTradeSize        float64
	firstTradeTsMs      int64
	tradeTimestamps     []int64
	consecutiveRunLen   int
	consecutiveRunSide  string
	maxConsecutiveRun   int

	// Volatility — Welford online moments for mid-price log-returns
	nMidReturns    int
	midReturnMean  float64
	midReturnM2    float64
	midReturnM3    float64
	lastMidPrice   float64 // survives BarReset; cleared by Reset
	hasLastMid     bool    // survives BarReset; cleared by Reset
	uptickCount    int
	downtickCount  int

	// OB activity counters
	bidOrderArrivals int
	askOrderArrivals int
	bidCancelCount   int
	askCancelCount   int
	obModifyCount    int
	bidArrivalVolSum float64
	askArrivalVolSum float64
	bestBidChanges   int
	bestAskChanges   int
	hasOBActivity    bool // set true on any IncrementOBAdd/Cancel/Modify call
	hasQuoteActivity bool // set true once any tick with valid bid+ask is seen

	// Trade microstructure — online O(1) sign autocorrelation
	sumSigns         int
	sumSignPairs     int
	firstSign        int8
	lastSign         int8
	tradePriceLevels map[string]struct{} // distinct trade prices seen this bar

	// Quality
	gapCount int
	barCount int
}

// New creates an Accumulator for one (exchange, symbol).
func New(exchange, symbol string, clk Clock) *Accumulator {
	return &Accumulator{
		exchange:         exchange,
		symbol:           symbol,
		clk:              clk,
		tradePriceLevels: make(map[string]struct{}),
	}
}

// Apply records a single tick event.
// isTrade=true for trade ticks (Level==0); false for OB delta ticks (Level>0).
// prevQuote/currQuote are the L2 OB best quotes before and after the tick.
//
// OB delta ticks: update OFI and OB quote tracking only (no OHLCV update).
// Trade ticks: update OHLCV, OFI, and OB quote tracking.
func (a *Accumulator) Apply(price, size string, isTrade bool, side string, tsMs int64, prevQuote, currQuote features.BestQuote) {
	a.hasTicks = true

	// Update OFI for every tick (both trade and OB delta).
	a.ofiSum += features.OFIDelta(prevQuote, currQuote)

	var mid float64
	var hasMid bool

	// Update OB close quote and mid/spread state on every tick with valid quote.
	if currQuote.BidPrice != "" && currQuote.AskPrice != "" {
		bid, err1 := strconv.ParseFloat(currQuote.BidPrice, 64)
		ask, err2 := strconv.ParseFloat(currQuote.AskPrice, 64)
		if err1 == nil && err2 == nil {
			// Track best quote changes BEFORE updating a.bestBid/bestAsk.
			if a.hasQuoteActivity {
				if bid != a.bestBid {
					a.bestBidChanges++
				}
				if ask != a.bestAsk {
					a.bestAskChanges++
				}
			}
			a.hasQuoteActivity = true

			a.bestBid = bid
			a.bestAsk = ask
			a.hasCloseQuote = true
			if !a.hasOpenQuote {
				a.bestBidOpen = bid
				a.bestAskOpen = ask
				a.hasOpenQuote = true
			}

			mid = features.MidPrice(bid, ask)
			spread := features.Spread(bid, ask)
			hasMid = true

			if !a.hasMidOpen {
				a.midPriceOpen = mid
				a.hasMidOpen = true
			}
			if mid > a.midPriceHigh || !a.hasMidHL {
				a.midPriceHigh = mid
			}
			if mid < a.midPriceLow || !a.hasMidHL {
				a.midPriceLow = mid
			}
			a.hasMidHL = true
			if spread > a.spreadHigh || !a.hasSpread {
				a.spreadHigh = spread
			}
			if spread < a.spreadLow || !a.hasSpread {
				a.spreadLow = spread
			}
			a.spreadSum += spread
			a.spreadCount++
			a.hasSpread = true

			a.lastKnownBid = bid
			a.lastKnownAsk = ask
			a.hasLastKnownOB = true

			// Volatility: online Welford update for mid-price log-returns.
			// Uses exact Wikipedia/Terriberry algorithm: M3 updated BEFORE M2.
			if a.hasLastMid && a.lastMidPrice > 0 && mid > 0 {
				r := math.Log(mid / a.lastMidPrice)
				n1 := float64(a.nMidReturns)
				a.nMidReturns++
				n := float64(a.nMidReturns)
				delta := r - a.midReturnMean
				deltaN := delta / n
				term1 := delta * deltaN * n1
				a.midReturnMean += deltaN
				a.midReturnM3 += term1*deltaN*(n-2) - 3*deltaN*a.midReturnM2
				a.midReturnM2 += term1
			}
			a.lastMidPrice = mid
			a.hasLastMid = true
		}
	}

	if !isTrade {
		return
	}

	// Trade tick: update OHLCV.
	p, err := strconv.ParseFloat(price, 64)
	if err != nil {
		return
	}
	s, err := strconv.ParseFloat(size, 64)
	if err != nil {
		return
	}

	// TWAP accumulation: use price held since last trade, before updating close.
	if a.tradeCount > 0 {
		dt := tsMs - a.lastTradeTsMs
		if dt > 0 {
			a.twapNumer += a.close * float64(dt)
			a.twapDenom += float64(dt)
		}
	}
	a.lastTradeTsMs = tsMs

	// Uptick/downtick: compare new price to previous close BEFORE updating close.
	if a.tradeCount > 0 {
		if p > a.close {
			a.uptickCount++
		} else if p < a.close {
			a.downtickCount++
		}
	}

	if a.tradeCount == 0 {
		a.open = p
		a.high = p
		a.low = p
		a.firstTradeTsMs = tsMs
	} else {
		if p > a.high {
			a.high = p
		}
		if p < a.low {
			a.low = p
		}
	}
	a.close = p
	a.volumeSum += s
	a.quoteVolSum += p * s

	// Trade distribution tracking.
	if s > a.maxTradeSize {
		a.maxTradeSize = s
	}
	a.tradeTimestamps = append(a.tradeTimestamps, tsMs)
	if a.tradeCount == 0 {
		a.consecutiveRunSide = side
		a.consecutiveRunLen = 1
	} else if side == a.consecutiveRunSide {
		a.consecutiveRunLen++
	} else {
		a.consecutiveRunLen = 1
		a.consecutiveRunSide = side
	}
	if a.consecutiveRunLen > a.maxConsecutiveRun {
		a.maxConsecutiveRun = a.consecutiveRunLen
	}

	// Trade microstructure: sign autocorrelation and distinct price levels.
	currSign := int8(1)
	if side != "buy" {
		currSign = -1
	}
	if a.tradeCount == 0 {
		a.firstSign = currSign
	} else {
		a.sumSignPairs += int(a.lastSign) * int(currSign)
	}
	a.sumSigns += int(currSign)
	a.lastSign = currSign
	a.tradePriceLevels[price] = struct{}{}

	a.tradeCount++
	if side == "buy" {
		a.buyVolume += s
		a.buyCount++
	}

	// VWMP and effective spread — only when mid is available at trade time.
	if hasMid {
		a.vwmpNumer += mid * s
		a.vwmpDenom += s
		a.effectiveSpreadSum += features.EffectiveSpreadContrib(p, mid)
		a.effectiveSpreadCount++
	}
}

// ApplyBlockTrade records a block trade (trade that exceeded the rolling 99th-percentile
// size threshold). Called directly by accWriter after threshold classification; NOT on
// the AccumulatorApplier interface to avoid interface churn.
func (a *Accumulator) ApplyBlockTrade(side string, size float64) {
	if side == "buy" {
		a.blockBuyVolume += size
		a.largeBidCount++
	} else {
		a.blockSellVolume += size
		a.largeAskCount++
	}
	a.hasBlockData = true
}

// SetOpenDepth records the OB depth snapshot at the first valid OB tick of the bar.
// Called by accWriter.Apply() when the first currQuote is valid.
func (a *Accumulator) SetOpenDepth(d features.DepthSnapshot) {
	a.openDepth = d
	a.hasOpenDepth = true
}

// SetCloseDepth records the OB depth snapshot at bar-flush time.
// Called by accWriter.Flush() before CurrentBar().
func (a *Accumulator) SetCloseDepth(d features.DepthSnapshot) {
	a.closeDepth = d
	a.hasCloseDepth = true
}

// IncrementGap increments the gap_count for the current second.
func (a *Accumulator) IncrementGap() {
	a.gapCount++
}

// SeedFromLastKnown populates bestBidOpen/bestAskOpen from lastKnownBid/lastKnownAsk
// when the bar has no open quote yet. Called by accWriter.Flush() before CurrentBar()
// to ensure empty-second null rows still carry the last known OB state.
func (a *Accumulator) SeedFromLastKnown() {
	if !a.hasOpenQuote && a.hasLastKnownOB {
		a.bestBidOpen = a.lastKnownBid
		a.bestAskOpen = a.lastKnownAsk
		a.hasOpenQuote = true
		// Do NOT set hasCloseQuote — the close quote requires a tick this bar.
	}
}

// IncrementOBAdd records a new bid or ask price level (OBEventAdd).
func (a *Accumulator) IncrementOBAdd(side string, parsedSize float64) {
	if side == "buy" {
		a.bidOrderArrivals++
		a.bidArrivalVolSum += parsedSize
	} else {
		a.askOrderArrivals++
		a.askArrivalVolSum += parsedSize
	}
	a.hasOBActivity = true
}

// IncrementOBCancel records a cancelled bid or ask level (OBEventCancel).
func (a *Accumulator) IncrementOBCancel(side string) {
	if side == "buy" {
		a.bidCancelCount++
	} else {
		a.askCancelCount++
	}
	a.hasOBActivity = true
}

// IncrementOBModify records a modified price level (OBEventModify).
func (a *Accumulator) IncrementOBModify() {
	a.obModifyCount++
	a.hasOBActivity = true
}

// CurrentBar returns an immutable snapshot of the current bar state.
// tsSecMs is the Unix millisecond timestamp of the second boundary.
// isPartial marks whether this is a mid-second flush (not a full bar close).
func (a *Accumulator) CurrentBar(tsSecMs int64, isPartial bool) Bar {
	bar := Bar{
		TsSecMs:    tsSecMs,
		Exchange:   a.exchange,
		Symbol:     a.symbol,
		TradeCount: a.tradeCount,
		IsPartial:  isPartial,
		GapCount:   a.gapCount,
		BarCount:   a.barCount + 1, // this bar counts as 1
	}
	if a.hasTicks {
		bar.OFI = ptr(a.ofiSum)
		bar.OFIL1 = ptr(a.ofiSum)
	}

	if a.tradeCount > 0 {
		bar.Open = ptr(a.open)
		bar.High = ptr(a.high)
		bar.Low = ptr(a.low)
		bar.Close = ptr(a.close)
		bar.Volume = ptr(a.volumeSum)
		bar.QuoteVolume = ptr(a.quoteVolSum)
		// Finalize TWAP: add last trade's hold-time to bar end.
		barEndMs := tsSecMs + 1000
		dt := barEndMs - a.lastTradeTsMs
		if dt > 0 {
			finalNum := a.twapNumer + a.close*float64(dt)
			finalDen := a.twapDenom + float64(dt)
			bar.TWAP = ptr(finalNum / finalDen)
		} else if a.twapDenom > 0 {
			bar.TWAP = ptr(a.twapNumer / a.twapDenom)
		} else {
			// Single trade with tsMs >= barEndMs (exchange clock ahead): TWAP = that trade's price.
			bar.TWAP = ptr(a.close)
		}
		bar.BuyVolume = ptr(a.buyVolume)
		bar.BuyCount = ptrInt(a.buyCount)
	}

	if a.hasBlockData {
		bar.BlockBuyVolume = ptr(a.blockBuyVolume)
		bar.BlockSellVolume = ptr(a.blockSellVolume)
		bar.LargeBidOrders = ptrInt(a.largeBidCount)
		bar.LargeAskOrders = ptrInt(a.largeAskCount)
	}

	if a.hasOpenQuote {
		bar.BestBidOpen = ptr(a.bestBidOpen)
		bar.BestAskOpen = ptr(a.bestAskOpen)
	}
	if a.hasCloseQuote {
		bar.BestBid = ptr(a.bestBid)
		bar.BestAsk = ptr(a.bestAsk)
	}

	if a.hasMidOpen {
		bar.MidPriceOpen = ptr(a.midPriceOpen)
		bar.MidPriceHigh = ptr(a.midPriceHigh)
		bar.MidPriceLow = ptr(a.midPriceLow)
	}
	if a.vwmpDenom > 0 {
		bar.VWMP = ptr(a.vwmpNumer / a.vwmpDenom)
	}
	if a.hasSpread {
		bar.SpreadHigh = ptr(a.spreadHigh)
		bar.SpreadLow = ptr(a.spreadLow)
		bar.SpreadMean = ptr(a.spreadSum / float64(a.spreadCount))
	}
	if a.effectiveSpreadCount > 0 {
		bar.EffectiveSpread = ptr(a.effectiveSpreadSum / float64(a.effectiveSpreadCount))
	}

	if a.hasOpenDepth {
		bar.BidDepthL1Open = ptr(a.openDepth.BidL1)
		bar.AskDepthL1Open = ptr(a.openDepth.AskL1)
		bar.BidDepthL2Open = ptr(a.openDepth.BidL2)
		bar.AskDepthL2Open = ptr(a.openDepth.AskL2)
		bar.BidDepthTop10Open = ptr(a.openDepth.BidTop10)
		bar.AskDepthTop10Open = ptr(a.openDepth.AskTop10)
		bar.BidDepthTotalOpen = ptr(a.openDepth.BidTotal)
		bar.AskDepthTotalOpen = ptr(a.openDepth.AskTotal)
	}
	if a.hasCloseDepth {
		bar.BidDepthL1Close = ptr(a.closeDepth.BidL1)
		bar.AskDepthL1Close = ptr(a.closeDepth.AskL1)
		bar.BidDepthL2Close = ptr(a.closeDepth.BidL2)
		bar.AskDepthL2Close = ptr(a.closeDepth.AskL2)
		bar.BidDepthTop10Close = ptr(a.closeDepth.BidTop10)
		bar.AskDepthTop10Close = ptr(a.closeDepth.AskTop10)
		bar.BidDepthTotalClose = ptr(a.closeDepth.BidTotal)
		bar.AskDepthTotalClose = ptr(a.closeDepth.AskTotal)
		if a.closeDepth.HasBidVolume {
			bar.WeightedBidPrice = ptr(a.closeDepth.WeightedBidPrice)
			bar.DepthTo1PctBid = ptr(a.closeDepth.DepthTo1PctBid)
		}
		if a.closeDepth.HasAskVolume {
			bar.WeightedAskPrice = ptr(a.closeDepth.WeightedAskPrice)
			bar.DepthTo1PctAsk = ptr(a.closeDepth.DepthTo1PctAsk)
		}
	}

	// Trade distribution fields.
	if a.tradeCount > 0 {
		bar.MaxTradeSize = ptr(a.maxTradeSize)
		bar.FirstTradeOffsetMs = ptrInt(int(max64(0, a.firstTradeTsMs-tsSecMs)))
		bar.LastTradeOffsetMs = ptrInt(int(max64(0, a.lastTradeTsMs-tsSecMs)))
		bar.MaxConsecutiveRun = ptrInt(a.maxConsecutiveRun)
		bar.UptickCount = ptrInt(a.uptickCount)
		bar.DowntickCount = ptrInt(a.downtickCount)
		if a.tradeCount >= 2 {
			bar.TradeClustering = computeGini(a.tradeTimestamps)
		}
	}

	// Volatility fields.
	if a.nMidReturns >= 2 && a.midReturnM2 > 0 {
		variance := a.midReturnM2 / float64(a.nMidReturns-1)
		bar.RealizedVol = ptr(math.Sqrt(variance))
	}
	if a.nMidReturns >= 3 && a.midReturnM2 > 0 {
		skew := math.Sqrt(float64(a.nMidReturns)) * a.midReturnM3 / math.Pow(a.midReturnM2, 1.5)
		bar.RealizedSkewness = ptr(skew)
	}

	// OB activity fields: OB event counters only when OB events were received.
	// Gated on hasOBActivity only — trade-only bars must not write spurious 0s.
	if a.hasOBActivity {
		bar.BidOrderArrivals = ptrInt(a.bidOrderArrivals)
		bar.AskOrderArrivals = ptrInt(a.askOrderArrivals)
		bar.BidCancelCount = ptrInt(a.bidCancelCount)
		bar.AskCancelCount = ptrInt(a.askCancelCount)
		bar.OBModifyCount = ptrInt(a.obModifyCount)
	}
	if a.bidOrderArrivals > 0 {
		bar.AvgBidOrderSize = ptr(a.bidArrivalVolSum / float64(a.bidOrderArrivals))
	}
	if a.askOrderArrivals > 0 {
		bar.AvgAskOrderSize = ptr(a.askArrivalVolSum / float64(a.askOrderArrivals))
	}
	if a.hasQuoteActivity {
		bar.BestBidChanges = ptrInt(a.bestBidChanges)
		bar.BestAskChanges = ptrInt(a.bestAskChanges)
	}
	if a.tradeCount > 0 {
		arrivals := a.bidOrderArrivals + a.askOrderArrivals
		cancels := a.bidCancelCount + a.askCancelCount
		bar.QuoteStuffRatio = ptr(float64(arrivals+cancels) / float64(a.tradeCount))
	}

	// Trade microstructure fields.
	if a.tradeCount > 0 {
		bar.NumTradePriceLevels = ptrInt(len(a.tradePriceLevels))
	}
	if a.tradeCount >= 2 {
		n := len(a.tradeTimestamps)
		var sumI, sumI2 float64
		for i := 1; i < n; i++ {
			d := float64(a.tradeTimestamps[i] - a.tradeTimestamps[i-1])
			sumI += d
			sumI2 += d * d
		}
		nI := float64(n - 1)
		variance := sumI2/nI - (sumI/nI)*(sumI/nI)
		if variance > 0 {
			bar.InterTradeIntervalStdMs = ptr(math.Sqrt(variance))
		} else {
			bar.InterTradeIntervalStdMs = ptr(0.0)
		}

		nP := float64(a.tradeCount - 1)
		sumX := float64(a.sumSigns - int(a.lastSign))  // s_0..s_{n-2}
		sumY := float64(a.sumSigns - int(a.firstSign)) // s_1..s_{n-1}
		meanX := sumX / nP
		meanY := sumY / nP
		cov := float64(a.sumSignPairs)/nP - meanX*meanY
		varX := 1.0 - meanX*meanX // xi^2 = 1 for ±1 values
		varY := 1.0 - meanY*meanY
		if varX > 0 && varY > 0 {
			bar.TradeSignAutocorr = ptr(cov / math.Sqrt(varX*varY))
		}
	}

	return bar
}

// BarReset clears all per-bar state but preserves lastKnownBid/lastKnownAsk/hasLastKnownOB.
// Called by accWriter.Flush() on every bar boundary so OB carry-forward works across seconds.
func (a *Accumulator) BarReset() {
	a.open = 0
	a.high = 0
	a.low = 0
	a.close = 0
	a.volumeSum = 0
	a.quoteVolSum = 0
	a.tradeCount = 0
	a.twapNumer = 0
	a.twapDenom = 0
	a.lastTradeTsMs = 0
	a.bestBidOpen = 0
	a.bestAskOpen = 0
	a.bestBid = 0
	a.bestAsk = 0
	a.hasOpenQuote = false
	a.hasCloseQuote = false
	a.ofiSum = 0
	a.hasTicks = false
	a.midPriceOpen = 0
	a.hasMidOpen = false
	a.hasMidHL = false
	a.midPriceHigh = 0
	a.midPriceLow = 0
	a.hasSpread = false
	a.spreadHigh = 0
	a.spreadLow = 0
	a.spreadSum = 0
	a.spreadCount = 0
	a.vwmpNumer = 0
	a.vwmpDenom = 0
	a.effectiveSpreadSum = 0
	a.effectiveSpreadCount = 0
	a.buyVolume = 0
	a.buyCount = 0
	a.gapCount = 0
	a.openDepth = features.DepthSnapshot{}
	a.closeDepth = features.DepthSnapshot{}
	a.hasOpenDepth = false
	a.hasCloseDepth = false
	a.blockBuyVolume = 0
	a.blockSellVolume = 0
	a.largeBidCount = 0
	a.largeAskCount = 0
	a.hasBlockData = false
	// Trade distribution
	a.maxTradeSize = 0
	a.firstTradeTsMs = 0
	a.tradeTimestamps = a.tradeTimestamps[:0]
	a.consecutiveRunLen = 0
	a.consecutiveRunSide = ""
	a.maxConsecutiveRun = 0
	// Volatility (mid-price returns reset each bar; lastMidPrice/hasLastMid survive)
	a.nMidReturns = 0
	a.midReturnMean = 0
	a.midReturnM2 = 0
	a.midReturnM3 = 0
	a.uptickCount = 0
	a.downtickCount = 0
	// OB activity
	a.bidOrderArrivals = 0
	a.askOrderArrivals = 0
	a.bidCancelCount = 0
	a.askCancelCount = 0
	a.obModifyCount = 0
	a.bidArrivalVolSum = 0
	a.askArrivalVolSum = 0
	a.bestBidChanges = 0
	a.bestAskChanges = 0
	a.hasOBActivity = false
	a.hasQuoteActivity = false
	// Trade microstructure
	a.sumSigns = 0
	a.sumSignPairs = 0
	a.firstSign = 0
	a.lastSign = 0
	clear(a.tradePriceLevels)
	// lastKnownBid/lastKnownAsk/hasLastKnownOB intentionally NOT cleared
	// lastMidPrice/hasLastMid intentionally NOT cleared (carry-forward)
	a.barCount++
}

// Reset clears all bar state including lastKnownOB.
// MUST be called on every gap event and every snapshot event before feeding new ticks.
func (a *Accumulator) Reset() {
	a.BarReset()
	a.barCount = 0 // undo the BarReset increment — Reset means "start over"
	a.lastKnownBid = 0
	a.lastKnownAsk = 0
	a.hasLastKnownOB = false
	a.lastMidPrice = 0
	a.hasLastMid = false
	a.tradePriceLevels = make(map[string]struct{})
}

func ptr(f float64) *float64 { return &f }
func ptrInt(i int) *int      { return &i }

func max64(a, b int64) int64 {
	if a > b {
		return a
	}
	return b
}

// computeGini computes the Gini coefficient of inter-trade intervals from timestamps.
// Returns nil when < 2 timestamps, all intervals are 0, or the sum is 0.
func computeGini(timestamps []int64) *float64 {
	n := len(timestamps)
	if n < 2 {
		return nil
	}
	intervals := make([]float64, n-1)
	for i := 1; i < n; i++ {
		d := float64(timestamps[i] - timestamps[i-1])
		if d < 0 {
			d = 0 // clamp out-of-order tick timestamps
		}
		intervals[i-1] = d
	}
	sort.Float64s(intervals)
	m := len(intervals)
	sum := 0.0
	for _, v := range intervals {
		sum += v
	}
	if sum == 0 {
		return nil
	}
	// G = Σ((2i - n + 1) × x[i]) / (n × Σ(x[i])) with 0-indexed i, n = m
	// Equivalent to the standard 1-indexed formula Σ((2i - n - 1) × x[i]) shifted by 1.
	weighted := 0.0
	for i, v := range intervals {
		weighted += float64(2*i-m+1) * v
	}
	g := weighted / (float64(m) * sum)
	return ptr(g)
}
