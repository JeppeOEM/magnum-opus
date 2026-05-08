// Package cascade implements the multi-timeframe OHLCV cascade engine.
// Pure: zero IO, no time.Now(), no goroutines. Clock injected for testability.
// Single-goroutine ownership — the accWriter goroutine per symbol owns this.
package cascade

import (
	"strconv"
	"time"

	"github.com/mrqdt/magnum-opus/candle-service/internal/accumulator"
)

// Clock allows deterministic time injection in L1 tests.
type Clock interface {
	Now() time.Time
}

// TF is a timeframe identifier.
type TF string

const (
	TF1m  TF = "1m"
	TF5m  TF = "5m"
	TF15m TF = "15m"
	TF1h  TF = "1h"
	TF4h  TF = "4h"
	TF1d  TF = "1d"
	TF1w  TF = "1w"
)

// AllTFs lists all timeframes in ascending duration order.
var AllTFs = []TF{TF1m, TF5m, TF15m, TF1h, TF4h, TF1d, TF1w}

// Bar is an immutable snapshot of a cascade bar (complete or in-progress).
type Bar struct {
	Exchange   string
	Symbol     string
	TF         TF
	OpenTs     int64    // Unix ms of first 1s bar; 0 = uninitialised
	Open       *float64
	High       *float64
	Low        *float64
	Close      *float64
	Volume     float64
	QuoteVol   float64
	TradeCount int
	BarCount   int
	GapCount   int
	IsComplete bool
}

// cascadeAcc holds the rolling accumulation state for one timeframe.
type cascadeAcc struct {
	openTs     int64
	open       *float64
	high       *float64
	low        *float64
	closeVal   *float64
	volume     float64
	quoteVol   float64
	tradeCount int
	barCount   int
	gapCount   int
}

func (a *cascadeAcc) fold(bar accumulator.Bar) {
	if a.openTs == 0 {
		a.openTs = bar.TsSecMs
	}
	if a.open == nil && bar.Open != nil {
		v := *bar.Open
		a.open = &v
	}
	if bar.High != nil {
		if a.high == nil || *bar.High > *a.high {
			v := *bar.High
			a.high = &v
		}
	}
	if bar.Low != nil {
		if a.low == nil || *bar.Low < *a.low {
			v := *bar.Low
			a.low = &v
		}
	}
	if bar.Close != nil {
		v := *bar.Close
		a.closeVal = &v
	}
	if bar.Volume != nil {
		a.volume += *bar.Volume
	}
	if bar.QuoteVolume != nil {
		a.quoteVol += *bar.QuoteVolume
	}
	a.tradeCount += bar.TradeCount
	a.barCount++
	a.gapCount += bar.GapCount
}

func (a *cascadeAcc) reset(openTs int64) {
	*a = cascadeAcc{openTs: openTs}
}

func (a *cascadeAcc) toBar(exchange, symbol string, tf TF, isComplete bool) Bar {
	b := Bar{
		Exchange:   exchange,
		Symbol:     symbol,
		TF:         tf,
		OpenTs:     a.openTs,
		Volume:     a.volume,
		QuoteVol:   a.quoteVol,
		TradeCount: a.tradeCount,
		BarCount:   a.barCount,
		GapCount:   a.gapCount,
		IsComplete: isComplete,
	}
	if a.open != nil {
		v := *a.open
		b.Open = &v
	}
	if a.high != nil {
		v := *a.high
		b.High = &v
	}
	if a.low != nil {
		v := *a.low
		b.Low = &v
	}
	if a.closeVal != nil {
		v := *a.closeVal
		b.Close = &v
	}
	return b
}

// IsBarClose returns true when tsSecMs is the last second of a TF bar —
// i.e., when the NEXT second begins a new bar.
func IsBarClose(tsSecMs int64, tf TF) bool {
	curr := time.Unix(tsSecMs/1000, 0).UTC()
	next := curr.Add(time.Second)
	switch tf {
	case TF1m:
		return next.Truncate(time.Minute).After(curr.Truncate(time.Minute))
	case TF5m:
		return next.Truncate(5 * time.Minute).After(curr.Truncate(5 * time.Minute))
	case TF15m:
		return next.Truncate(15 * time.Minute).After(curr.Truncate(15 * time.Minute))
	case TF1h:
		return next.Truncate(time.Hour).After(curr.Truncate(time.Hour))
	case TF4h:
		return next.Truncate(4 * time.Hour).After(curr.Truncate(4 * time.Hour))
	case TF1d:
		return next.Truncate(24 * time.Hour).After(curr.Truncate(24 * time.Hour))
	case TF1w:
		// time.Truncate cannot compute Monday boundaries; use explicit weekday check.
		return next.Weekday() == time.Monday && next.Hour() == 0 && next.Minute() == 0 && next.Second() == 0
	}
	return false
}

// Engine manages cascade accumulators for all 7 timeframes.
type Engine struct {
	exchange string
	symbol   string
	clk      Clock
	accs     map[TF]*cascadeAcc
}

// NewEngine creates an Engine with empty accumulators for all TFs.
func NewEngine(exchange, symbol string, clk Clock) *Engine {
	accs := make(map[TF]*cascadeAcc, len(AllTFs))
	for _, tf := range AllTFs {
		accs[tf] = &cascadeAcc{}
	}
	return &Engine{exchange: exchange, symbol: symbol, clk: clk, accs: accs}
}

// Fold folds a closed 1s bar into all TF cascade accumulators.
// Returns the list of TF Bars whose boundary closed at tsSecMs.
func (e *Engine) Fold(bar accumulator.Bar, tsSecMs int64) []Bar {
	var closed []Bar
	for _, tf := range AllTFs {
		acc := e.accs[tf]
		acc.fold(bar)
		if IsBarClose(tsSecMs, tf) {
			closed = append(closed, acc.toBar(e.exchange, e.symbol, tf, true))
			acc.reset(tsSecMs + 1000)
		}
	}
	return closed
}

// CurrentBar returns a read-only snapshot of the in-progress cascade bar.
// Returns a zero Bar (OpenTs == 0) if no data has been accumulated yet.
func (e *Engine) CurrentBar(tf TF) Bar {
	return e.accs[tf].toBar(e.exchange, e.symbol, tf, false)
}

// RestoreFromHash restores a TF accumulator from a Redis HASH field map.
// Absent or unparseable fields are silently treated as zero/nil.
func (e *Engine) RestoreFromHash(tf TF, fields map[string]string) {
	a := e.accs[tf]
	*a = cascadeAcc{}
	if v, ok := fields["open_ts"]; ok {
		a.openTs, _ = strconv.ParseInt(v, 10, 64)
	}
	a.open = parseFloatPtr(fields["open"])
	a.high = parseFloatPtr(fields["high"])
	a.low = parseFloatPtr(fields["low"])
	a.closeVal = parseFloatPtr(fields["close"])
	if v, ok := fields["volume"]; ok {
		a.volume, _ = strconv.ParseFloat(v, 64)
	}
	if v, ok := fields["quote_vol"]; ok {
		a.quoteVol, _ = strconv.ParseFloat(v, 64)
	}
	if v, ok := fields["trade_count"]; ok {
		a.tradeCount, _ = strconv.Atoi(v)
	}
	if v, ok := fields["bar_count"]; ok {
		a.barCount, _ = strconv.Atoi(v)
	}
	if v, ok := fields["gap_count"]; ok {
		a.gapCount, _ = strconv.Atoi(v)
	}
}

// ToHash serialises the current TF accumulator to a Redis HASH field map.
// All 10 fields are always written (missing fields on read = zero/default).
func (e *Engine) ToHash(tf TF) map[string]string {
	a := e.accs[tf]
	m := make(map[string]string, 10)
	m["open_ts"] = strconv.FormatInt(a.openTs, 10)
	m["open"] = floatOrEmpty(a.open)
	m["high"] = floatOrEmpty(a.high)
	m["low"] = floatOrEmpty(a.low)
	m["close"] = floatOrEmpty(a.closeVal)
	m["volume"] = strconv.FormatFloat(a.volume, 'f', -1, 64)
	m["quote_vol"] = strconv.FormatFloat(a.quoteVol, 'f', -1, 64)
	m["trade_count"] = strconv.Itoa(a.tradeCount)
	m["bar_count"] = strconv.Itoa(a.barCount)
	m["gap_count"] = strconv.Itoa(a.gapCount)
	return m
}

func floatOrEmpty(f *float64) string {
	if f == nil {
		return ""
	}
	return strconv.FormatFloat(*f, 'f', -1, 64)
}

func parseFloatPtr(s string) *float64 {
	if s == "" {
		return nil
	}
	v, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return nil
	}
	return &v
}
