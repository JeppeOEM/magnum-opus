package features

import (
	"sort"
	"strconv"
)

// ImbalanceSignals holds per-bar imbalance metrics derived from the footprint map.
// BuyCount/SellCount count cells meeting the 3× threshold; StackBuy/StackSell are
// the longest consecutive runs; Ratio is normalised to [−1, 1].
type ImbalanceSignals struct {
	BuyCount  int
	SellCount int
	StackBuy  int
	StackSell int
	Ratio     float64
}

// ComputeImbalance derives imbalance signals from a per-bar footprint map.
//
// Buy-imbalanced cell: buyVol > 3×sellVol AND sellVol > 0.
// Sell-imbalanced cell: sellVol > 3×buyVol AND buyVol > 0.
// Cells with zero on either side are excluded from imbalance counts.
//
// Consecutive stack is measured on price-sorted positions, not numeric distance.
// Returns zero-value ImbalanceSignals when footprint is empty.
func ComputeImbalance(footprint map[string]FootprintCell) ImbalanceSignals {
	if len(footprint) == 0 {
		return ImbalanceSignals{}
	}

	// Parse and sort price keys numerically.
	priceStrs := make([]string, 0, len(footprint))
	parsed := make(map[string]float64, len(footprint))
	for k := range footprint {
		p, err := strconv.ParseFloat(k, 64)
		if err != nil {
			continue
		}
		priceStrs = append(priceStrs, k)
		parsed[k] = p
	}
	if len(priceStrs) == 0 {
		return ImbalanceSignals{}
	}
	sort.Slice(priceStrs, func(i, j int) bool {
		return parsed[priceStrs[i]] < parsed[priceStrs[j]]
	})

	n := len(priceStrs)
	buyImb := make([]bool, n)
	sellImb := make([]bool, n)
	buyCount, sellCount := 0, 0

	for i, k := range priceStrs {
		cell := footprint[k]
		if cell.SellVol > 0 && cell.BuyVol > 3*cell.SellVol {
			buyImb[i] = true
			buyCount++
		}
		if cell.BuyVol > 0 && cell.SellVol > 3*cell.BuyVol {
			sellImb[i] = true
			sellCount++
		}
	}

	ratio := float64(buyCount-sellCount) / float64(n)

	return ImbalanceSignals{
		BuyCount:  buyCount,
		SellCount: sellCount,
		StackBuy:  longestRun(buyImb),
		StackSell: longestRun(sellImb),
		Ratio:     ratio,
	}
}

func longestRun(flags []bool) int {
	maxLen, cur := 0, 0
	for _, f := range flags {
		if f {
			cur++
			if cur > maxLen {
				maxLen = cur
			}
		} else {
			cur = 0
		}
	}
	return maxLen
}
