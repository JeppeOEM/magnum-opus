// Package blockwindow provides a rolling fixed-capacity window for trade sizes
// and a 99th-percentile threshold computation for block-trade classification.
package blockwindow

import (
	"math"
	"sort"
)

// Window maintains a rolling fixed-capacity slice of trade sizes and computes
// the 99th-percentile threshold (nearest-rank method).
// Not goroutine-safe — owned by a single consumer goroutine.
type Window struct {
	sizes      []float64
	windowSize int
	minSample  int
}

// New creates a Window capped at windowSize entries with minSample required
// before Threshold() returns a valid result.
// Panics if windowSize < 1 or minSample < 0.
func New(windowSize, minSample int) *Window {
	if windowSize < 1 {
		panic("blockwindow.New: windowSize must be >= 1")
	}
	if minSample < 0 {
		panic("blockwindow.New: minSample must be >= 0")
	}
	return &Window{
		sizes:      make([]float64, 0, windowSize),
		windowSize: windowSize,
		minSample:  minSample,
	}
}

// Add appends size to the window. If the window is at capacity, the oldest
// entry is discarded.
func (w *Window) Add(size float64) {
	if len(w.sizes) >= w.windowSize {
		// Discard oldest (index 0) by shifting left.
		copy(w.sizes, w.sizes[1:])
		w.sizes = w.sizes[:len(w.sizes)-1]
	}
	w.sizes = append(w.sizes, size)
}

// Threshold returns the 99th-percentile trade size (nearest-rank method) and
// whether enough samples are available. Returns (0, false) if fewer than
// minSample entries are present or the window is empty.
func (w *Window) Threshold() (float64, bool) {
	if len(w.sizes) == 0 {
		return 0, false
	}
	if len(w.sizes) < w.minSample {
		return 0, false
	}
	sorted := make([]float64, len(w.sizes))
	copy(sorted, w.sizes)
	sort.Float64s(sorted)
	idx := int(math.Ceil(0.99*float64(len(sorted)))) - 1
	if idx < 0 {
		idx = 0
	}
	if idx >= len(sorted) {
		idx = len(sorted) - 1
	}
	return sorted[idx], true
}

// Sizes returns a copy of the current window contents.
// The caller must not modify the returned slice.
func (w *Window) Sizes() []float64 {
	out := make([]float64, len(w.sizes))
	copy(out, w.sizes)
	return out
}

// Restore loads sizes into the window, replacing any existing contents.
// If len(sizes) > windowSize, only the last windowSize entries are kept.
func (w *Window) Restore(sizes []float64) {
	if len(sizes) > w.windowSize {
		sizes = sizes[len(sizes)-w.windowSize:]
	}
	w.sizes = make([]float64, len(sizes), w.windowSize)
	copy(w.sizes, sizes)
}
