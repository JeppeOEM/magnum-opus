package blockwindow_test

import (
	"testing"

	"github.com/mrqdt/magnum-opus/candle-service/internal/blockwindow"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestWindow_ThresholdBelowMinSample(t *testing.T) {
	w := blockwindow.New(100, 5)
	w.Add(1.0)
	w.Add(2.0)
	_, ok := w.Threshold()
	assert.False(t, ok, "should return false when fewer than minSample entries")
}

func TestWindow_ThresholdAtMinSample(t *testing.T) {
	w := blockwindow.New(100, 3)
	w.Add(10.0)
	w.Add(20.0)
	w.Add(30.0)
	thresh, ok := w.Threshold()
	require.True(t, ok)
	// sorted: [10, 20, 30]; idx = ceil(0.99*3)-1 = ceil(2.97)-1 = 3-1 = 2 → 30.0
	assert.InDelta(t, 30.0, thresh, 1e-9)
}

func TestWindow_Threshold99thPercentile(t *testing.T) {
	// 100 elements: 1..100; 99th pct = ceil(0.99*100)-1 = 99-1 = 98 (0-indexed) → value 99
	w := blockwindow.New(200, 10)
	for i := 1; i <= 100; i++ {
		w.Add(float64(i))
	}
	thresh, ok := w.Threshold()
	require.True(t, ok)
	assert.InDelta(t, 99.0, thresh, 1e-9)
}

func TestWindow_CapacityDiscardsOldest(t *testing.T) {
	w := blockwindow.New(3, 1)
	w.Add(1.0)
	w.Add(2.0)
	w.Add(3.0)
	w.Add(4.0) // should evict 1.0
	sizes := w.Sizes()
	assert.Equal(t, []float64{2.0, 3.0, 4.0}, sizes)
}

func TestWindow_SizesReturnsCopy(t *testing.T) {
	w := blockwindow.New(10, 1)
	w.Add(5.0)
	s := w.Sizes()
	s[0] = 999.0
	thresh, ok := w.Threshold()
	require.True(t, ok)
	assert.InDelta(t, 5.0, thresh, 1e-9, "modifying returned slice must not affect window")
}

func TestWindow_RestoreThenThreshold(t *testing.T) {
	w := blockwindow.New(10, 4)
	w.Restore([]float64{5.0, 10.0, 15.0, 20.0})
	thresh, ok := w.Threshold()
	require.True(t, ok)
	// sorted: [5, 10, 15, 20]; idx = ceil(0.99*4)-1 = ceil(3.96)-1 = 4-1 = 3 → 20.0
	assert.InDelta(t, 20.0, thresh, 1e-9)
}

func TestWindow_RestoreCapClamp(t *testing.T) {
	w := blockwindow.New(3, 1)
	w.Restore([]float64{1.0, 2.0, 3.0, 4.0, 5.0}) // 5 entries but cap is 3
	sizes := w.Sizes()
	assert.Equal(t, []float64{3.0, 4.0, 5.0}, sizes, "restore should keep only last windowSize entries")
}

func TestWindow_ThresholdEmptyWindow(t *testing.T) {
	w := blockwindow.New(100, 1)
	_, ok := w.Threshold()
	assert.False(t, ok, "empty window must return false")
}

func TestWindow_AddBeyondCapacity(t *testing.T) {
	w := blockwindow.New(2, 1)
	w.Add(1.0)
	w.Add(2.0)
	w.Add(3.0) // evicts 1.0
	w.Add(4.0) // evicts 2.0
	sizes := w.Sizes()
	assert.Equal(t, []float64{3.0, 4.0}, sizes)
}

func TestWindow_ThresholdStrictBoundary(t *testing.T) {
	// Threshold returns the 99th pct value; a trade exactly equal to threshold
	// must NOT be classified as a block trade (accWriter uses strict >).
	// This test verifies Threshold returns the boundary value correctly.
	w := blockwindow.New(10, 3)
	w.Add(1.0)
	w.Add(2.0)
	w.Add(10.0) // this is the 99th pct
	thresh, ok := w.Threshold()
	require.True(t, ok)
	assert.InDelta(t, 10.0, thresh, 1e-9)
	// A trade exactly at threshold (10.0) must NOT satisfy parsedSize > threshold.
	assert.False(t, 10.0 > thresh, "equal-to-threshold trade must not be a block trade")
	// A trade above threshold must satisfy the condition.
	assert.True(t, 10.001 > thresh, "trade above threshold must be classified as block")
}

func TestWindow_NewPanicsOnZeroWindowSize(t *testing.T) {
	assert.Panics(t, func() { blockwindow.New(0, 0) }, "windowSize=0 must panic")
	assert.Panics(t, func() { blockwindow.New(-1, 0) }, "negative windowSize must panic")
	assert.Panics(t, func() { blockwindow.New(10, -1) }, "negative minSample must panic")
}

func TestWindow_ThresholdMinSampleZeroEmptyWindow(t *testing.T) {
	// minSample=0 means any non-empty window returns a threshold.
	// An empty window must still return false.
	w := blockwindow.New(10, 0)
	_, ok := w.Threshold()
	assert.False(t, ok, "empty window with minSample=0 must return false")
	w.Add(5.0)
	thresh, ok := w.Threshold()
	require.True(t, ok, "non-empty window with minSample=0 must return true")
	assert.InDelta(t, 5.0, thresh, 1e-9)
}
