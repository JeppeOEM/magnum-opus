package features

import (
	"sort"
	"strconv"
)

// FootprintCell holds per-price buy and sell volume within a single 1-second bar.
type FootprintCell struct {
	BuyVol  float64
	SellVol float64
}

// ComputeValueArea computes the Point of Control (POC) and 70% Value Area from a
// per-bar footprint map. Price-string keys are parsed numerically for comparison.
//
// Returns ok=false when footprint is empty or all levels have zero volume.
// Single-level footprints return pocPrice==vahPrice==valPrice with ok=true.
//
// Tie-break rule: when two levels share the same volume, the higher price wins
// (for POC selection and for expansion side selection).
func ComputeValueArea(footprint map[string]FootprintCell) (pocPrice, vahPrice, valPrice, pocVolume float64, ok bool) {
	if len(footprint) == 0 {
		return 0, 0, 0, 0, false
	}

	type level struct {
		price float64
		vol   float64
	}

	levels := make([]level, 0, len(footprint))
	totalVol := 0.0
	for priceStr, cell := range footprint {
		p, err := strconv.ParseFloat(priceStr, 64)
		if err != nil {
			continue
		}
		v := cell.BuyVol + cell.SellVol
		levels = append(levels, level{p, v})
		totalVol += v
	}

	if len(levels) == 0 || totalVol == 0 {
		return 0, 0, 0, 0, false
	}

	// Sort ascending by price.
	sort.Slice(levels, func(i, j int) bool { return levels[i].price < levels[j].price })

	// Find POC: max volume; ties → higher price (i.e. later index in sorted slice).
	pocIdx := 0
	for i, l := range levels {
		if l.vol > levels[pocIdx].vol || (l.vol == levels[pocIdx].vol && l.price > levels[pocIdx].price) {
			pocIdx = i
		}
	}

	if len(levels) == 1 {
		p := levels[0].price
		return p, p, p, levels[0].vol, true
	}

	// Expand value area outward from POC until ≥ 70% of total volume is covered.
	target := totalVol * 0.70
	accumulated := levels[pocIdx].vol
	lo := pocIdx
	hi := pocIdx

	for accumulated < target && (lo > 0 || hi < len(levels)-1) {
		canLo := lo > 0
		canHi := hi < len(levels)-1

		expandHi := false
		switch {
		case canHi && canLo:
			// Prefer higher price side on tie (>= so tie → expand upward).
			expandHi = levels[hi+1].vol >= levels[lo-1].vol
		case canHi:
			expandHi = true
		}

		if expandHi {
			hi++
			accumulated += levels[hi].vol
		} else {
			lo--
			accumulated += levels[lo].vol
		}
	}

	return levels[pocIdx].price, levels[hi].price, levels[lo].price, levels[pocIdx].vol, true
}
