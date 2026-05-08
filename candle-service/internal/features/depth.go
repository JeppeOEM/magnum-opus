package features

import (
	"sort"
	"strconv"
)

// DepthSnapshot holds a point-in-time L2 OB depth snapshot used for bar open/close fields.
// Zero value is safe (all fields zero, HasBidVolume=HasAskVolume=false).
type DepthSnapshot struct {
	BidL1, AskL1       float64
	BidL2, AskL2       float64
	BidTop10, AskTop10 float64
	BidTotal, AskTotal float64

	WeightedBidPrice float64 // valid only when HasBidVolume=true
	WeightedAskPrice float64 // valid only when HasAskVolume=true

	// Market impact: cumulative volume within 1% of best bid/ask
	DepthTo1PctBid float64
	DepthTo1PctAsk float64

	HasBidVolume bool
	HasAskVolume bool
}

type priceLevel struct {
	price float64
	size  float64
}

// ComputeDepthSnapshot builds a DepthSnapshot from the raw bid/ask maps returned by
// ob.AllBids() / ob.AllAsks(). Levels with unparseable price or size are silently skipped.
// Returns a zero-value DepthSnapshot if both maps are empty.
func ComputeDepthSnapshot(bids, asks map[string]string) DepthSnapshot {
	var d DepthSnapshot
	bidLvl := parseLevels(bids)
	askLvl := parseLevels(asks)

	// Bids: best bid = highest price first
	sort.Slice(bidLvl, func(i, j int) bool { return bidLvl[i].price > bidLvl[j].price })
	// Asks: best ask = lowest price first
	sort.Slice(askLvl, func(i, j int) bool { return askLvl[i].price < askLvl[j].price })

	d.BidL1, d.BidL2, d.BidTop10, d.BidTotal, d.WeightedBidPrice = accumDepth(bidLvl)
	d.AskL1, d.AskL2, d.AskTop10, d.AskTotal, d.WeightedAskPrice = accumDepth(askLvl)
	d.HasBidVolume = d.BidTotal > 0
	d.HasAskVolume = d.AskTotal > 0
	if !d.HasBidVolume {
		d.WeightedBidPrice = 0
	}
	if !d.HasAskVolume {
		d.WeightedAskPrice = 0
	}

	// Market impact: volume within 1% of best quote
	if len(bidLvl) > 0 {
		d.DepthTo1PctBid = DepthTo1PctBid(bids, bidLvl[0].price)
	}
	if len(askLvl) > 0 {
		d.DepthTo1PctAsk = DepthTo1PctAsk(asks, askLvl[0].price)
	}
	return d
}

// DepthTo1PctBid returns the cumulative bid volume within 1% of bestBid.
// Sums all bid levels at prices in [bestBid*0.99, bestBid].
// Returns 0 if bids is empty or bestBid <= 0.
func DepthTo1PctBid(bids map[string]string, bestBid float64) float64 {
	if bestBid <= 0 {
		return 0
	}
	lo := bestBid * 0.99
	var total float64
	for priceStr, sizeStr := range bids {
		p, err1 := strconv.ParseFloat(priceStr, 64)
		s, err2 := strconv.ParseFloat(sizeStr, 64)
		if err1 != nil || err2 != nil || s <= 0 {
			continue
		}
		if p >= lo && p <= bestBid {
			total += s
		}
	}
	return total
}

// DepthTo1PctAsk returns the cumulative ask volume within 1% of bestAsk.
// Sums all ask levels at prices in [bestAsk, bestAsk*1.01].
// Returns 0 if asks is empty or bestAsk <= 0.
func DepthTo1PctAsk(asks map[string]string, bestAsk float64) float64 {
	if bestAsk <= 0 {
		return 0
	}
	hi := bestAsk * 1.01
	var total float64
	for priceStr, sizeStr := range asks {
		p, err1 := strconv.ParseFloat(priceStr, 64)
		s, err2 := strconv.ParseFloat(sizeStr, 64)
		if err1 != nil || err2 != nil || s <= 0 {
			continue
		}
		if p >= bestAsk && p <= hi {
			total += s
		}
	}
	return total
}

func parseLevels(m map[string]string) []priceLevel {
	levels := make([]priceLevel, 0, len(m))
	for priceStr, sizeStr := range m {
		p, err1 := strconv.ParseFloat(priceStr, 64)
		s, err2 := strconv.ParseFloat(sizeStr, 64)
		if err1 != nil || err2 != nil || s <= 0 {
			continue
		}
		levels = append(levels, priceLevel{price: p, size: s})
	}
	return levels
}

// accumDepth returns (l1, l2, top10, total, weightedPrice) for a sorted slice of levels.
// If fewer than N levels exist, lN caps at total (same behaviour as ob.TopNDepth).
func accumDepth(levels []priceLevel) (l1, l2, top10, total, weighted float64) {
	var priceVolumeSum float64
	for i, lv := range levels {
		total += lv.size
		priceVolumeSum += lv.price * lv.size
		switch i {
		case 0:
			l1 = total
		case 1:
			l2 = total
		case 9:
			top10 = total
		}
	}
	// Cap cumulative values to total when fewer levels exist than the tier boundary.
	n := len(levels)
	if n < 2 {
		l2 = total
	}
	if n < 10 {
		top10 = total
	}
	if total > 0 {
		weighted = priceVolumeSum / total
	}
	return
}
