// Package features provides pure, stateless feature-computation functions.
// No IO, no goroutines, no time.Now(). All state lives in accumulator/.
package features

import (
	"math"
	"strconv"
)

// MidPrice computes the mid-price from the best bid and ask.
func MidPrice(bid, ask float64) float64 {
	return (bid + ask) / 2
}

// Spread computes the bid-ask spread.
func Spread(bid, ask float64) float64 {
	return ask - bid
}

// EffectiveSpreadContrib computes the per-trade contribution to effective spread
// using the formula 2 * |tradePrice - midPrice|. Caller accumulates these and
// takes the mean over trade ticks to get effective_spread for the bar.
func EffectiveSpreadContrib(tradePrice, midPrice float64) float64 {
	return 2 * math.Abs(tradePrice-midPrice)
}

// Microprice computes the depth-weighted fair value price.
// When total depth (bidL1+askL1) is zero, falls back to mid-price to avoid NaN.
func Microprice(bestBid, bestAsk, bidL1, askL1 float64) float64 {
	total := bidL1 + askL1
	if total == 0 {
		return MidPrice(bestBid, bestAsk)
	}
	return (bestAsk*bidL1 + bestBid*askL1) / total
}

// MicropriceMidDelta returns (microprice - mid) / mid × 10000 in basis points.
// Positive = microprice above mid (book weighted toward upward pressure).
// Returns 0 when mid is zero.
func MicropriceMidDelta(microprice, mid float64) float64 {
	if mid == 0 {
		return 0
	}
	return (microprice - mid) / mid * 10000.0
}

// BestQuote holds the L2 OB best bid/ask at a point in time.
type BestQuote struct {
	BidPrice string
	BidSize  string
	AskPrice string
	AskSize  string
}

// OFIDelta computes the per-tick order flow imbalance delta per Cont et al. (2014).
//
// Δ_bid = change in best-bid quantity if best-bid price is unchanged, else:
//   - if price raised: full new quantity (new level arrived)
//   - if price dropped: 0 (prior level displaced)
//
// Δ_ask = change in best-ask quantity if best-ask price is unchanged, else:
//   - if price lowered: full new quantity (new level arrived)
//   - if price rose: 0 (prior level displaced)
//
// Returns 0 if either quote has an empty BidPrice or AskPrice.
func OFIDelta(prev, curr BestQuote) float64 {
	if prev.BidPrice == "" || prev.AskPrice == "" ||
		curr.BidPrice == "" || curr.AskPrice == "" {
		return 0
	}
	prevBidP, _ := strconv.ParseFloat(prev.BidPrice, 64)
	currBidP, _ := strconv.ParseFloat(curr.BidPrice, 64)
	prevBidQ, _ := strconv.ParseFloat(prev.BidSize, 64)
	currBidQ, _ := strconv.ParseFloat(curr.BidSize, 64)
	prevAskP, _ := strconv.ParseFloat(prev.AskPrice, 64)
	currAskP, _ := strconv.ParseFloat(curr.AskPrice, 64)
	prevAskQ, _ := strconv.ParseFloat(prev.AskSize, 64)
	currAskQ, _ := strconv.ParseFloat(curr.AskSize, 64)

	var deltaBid float64
	switch {
	case currBidP == prevBidP:
		deltaBid = currBidQ - prevBidQ
	case currBidP > prevBidP:
		deltaBid = currBidQ // new best bid raised: add full new quantity
	// currBidP < prevBidP: prior level displaced, Δ_bid = 0
	}

	var deltaAsk float64
	switch {
	case currAskP == prevAskP:
		deltaAsk = currAskQ - prevAskQ
	case currAskP < prevAskP:
		deltaAsk = currAskQ // new best ask lowered: add full new quantity
	// currAskP > prevAskP: prior level displaced, Δ_ask = 0
	}

	return deltaBid - deltaAsk
}
