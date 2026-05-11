package features

// FootprintDeltaDivergence returns 1 (bearish) when price rose but net delta was negative,
// -1 (bullish) when price fell but net delta was positive, and 0 otherwise.
// Any nil argument returns 0.
func FootprintDeltaDivergence(open, close, buyVol, sellVol *float64) int {
	if open == nil || close == nil || buyVol == nil || sellVol == nil {
		return 0
	}
	netDelta := *buyVol - *sellVol
	if *close > *open && netDelta < 0 {
		return 1
	}
	if *close < *open && netDelta > 0 {
		return -1
	}
	return 0
}

// ComputeIceberg detects iceberg orders: the best-bid/ask level replenished after absorbing
// directional volume. Returns (iceBid, iceAsk, icePrice).
// icePrice is best_bid when iceBid; best_ask when iceAsk only; best_bid when both; nil when neither.
func ComputeIceberg(
	bidDepthClose, bidDepthOpen *float64,
	askDepthClose, askDepthOpen *float64,
	buyVol, sellVol *float64,
	bidArrivals, askArrivals *int,
	bestBid, bestAsk *float64,
) (iceBid bool, iceAsk bool, icePrice *float64) {
	if bidDepthClose != nil && bidDepthOpen != nil && *bidDepthOpen > 0 &&
		*bidDepthClose >= *bidDepthOpen*0.8 &&
		buyVol != nil && *buyVol > 0 &&
		bidArrivals != nil && *bidArrivals > 0 {
		iceBid = true
	}
	if askDepthClose != nil && askDepthOpen != nil && *askDepthOpen > 0 &&
		*askDepthClose >= *askDepthOpen*0.8 &&
		sellVol != nil && *sellVol > 0 &&
		askArrivals != nil && *askArrivals > 0 {
		iceAsk = true
	}
	if iceBid && bestBid != nil {
		v := *bestBid
		icePrice = &v
	} else if iceAsk && bestAsk != nil {
		v := *bestAsk
		icePrice = &v
	}
	return
}
