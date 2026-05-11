package features

import (
	"encoding/json"
	"math"
	"sort"
	"strconv"
)

// AuctionSignals holds per-bar auction microstructure signals derived from the footprint map.
type AuctionSignals struct {
	SinglePrintCount      int
	SinglePrintLevelsJSON string // JSON array of price strings sorted ascending; "[]" if none
	UnfinishedTop         bool
	UnfinishedBottom      bool
}

// ComputeAuctionSignals computes auction signals from a bar's footprint map.
// Empty footprint → zero-value AuctionSignals (count=0, JSON="[]", booleans false).
func ComputeAuctionSignals(footprint map[string]FootprintCell, high, low float64) AuctionSignals {
	if len(footprint) == 0 {
		return AuctionSignals{SinglePrintLevelsJSON: "[]"}
	}

	type entry struct {
		key    string
		price  float64
		buyVol float64
		selVol float64
	}

	entries := make([]entry, 0, len(footprint))
	totalVol := 0.0
	for k, cell := range footprint {
		p, err := strconv.ParseFloat(k, 64)
		if err != nil {
			continue
		}
		entries = append(entries, entry{key: k, price: p, buyVol: cell.BuyVol, selVol: cell.SellVol})
		totalVol += cell.BuyVol + cell.SellVol
	}

	n := len(entries)
	if n == 0 {
		return AuctionSignals{SinglePrintLevelsJSON: "[]"}
	}

	meanVol := totalVol / float64(n)

	sort.Slice(entries, func(i, j int) bool { return entries[i].price < entries[j].price })

	var singlePrintKeys []string
	var unfinishedTop, unfinishedBottom bool

	for _, e := range entries {
		levelVol := e.buyVol + e.selVol
		if levelVol < 0.10*meanVol && e.price >= low && e.price <= high {
			singlePrintKeys = append(singlePrintKeys, e.key)
		}
		if math.Abs(e.price-high) < 1e-9 && e.selVol == 0 {
			unfinishedTop = true
		}
		if math.Abs(e.price-low) < 1e-9 && e.buyVol == 0 {
			unfinishedBottom = true
		}
	}

	levelsJSON := "[]"
	if len(singlePrintKeys) > 0 {
		if b, err := json.Marshal(singlePrintKeys); err == nil {
			levelsJSON = string(b)
		}
	}

	return AuctionSignals{
		SinglePrintCount:      len(singlePrintKeys),
		SinglePrintLevelsJSON: levelsJSON,
		UnfinishedTop:         unfinishedTop,
		UnfinishedBottom:      unfinishedBottom,
	}
}

// DetectAbsorption returns true when trade activity shows price absorption:
// heavy directional volume (>60% or <40% buy ratio) with minimal price movement.
// Returns false when totalVol == 0 to prevent divide-by-zero.
func DetectAbsorption(buyVol, totalVol, open, close float64, tradeCount int) bool {
	if totalVol == 0 || close == 0 {
		return false
	}
	if tradeCount < 5 {
		return false
	}
	ratio := buyVol / totalVol
	if ratio <= 0.6 && ratio >= 0.4 {
		return false
	}
	return math.Abs(close-open)/close < 0.0005
}
