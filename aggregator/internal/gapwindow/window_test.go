package gapwindow_test

import (
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/mrqdt/magnum-opus/aggregator/internal/gapwindow"
)

var (
	epoch = time.Date(2024, 6, 1, 12, 0, 0, 0, time.UTC) // reference "now" for tests
	hour  = time.Hour
)

func TestWindow_CountWithin24h(t *testing.T) {
	w := gapwindow.New()
	w.Add("internal_merge_error", epoch.Add(-1*hour))
	assert.Equal(t, 1, w.Count(epoch))
}

func TestWindow_ExcludesOlderThan24h(t *testing.T) {
	w := gapwindow.New()
	w.Add("internal_merge_error", epoch.Add(-25*hour))
	assert.Equal(t, 0, w.Count(epoch))
}

func TestWindow_ExternalGapNotCounted(t *testing.T) {
	w := gapwindow.New()
	w.Add("external_disconnect", epoch)
	w.Add("external_rate_limit", epoch)
	assert.Equal(t, 0, w.Count(epoch))
}

func TestWindow_MultipleGaps(t *testing.T) {
	w := gapwindow.New()
	// 3 gaps in same hour
	w.Add("internal_merge_error", epoch)
	w.Add("internal_merge_error", epoch.Add(10*time.Minute))
	w.Add("internal_buffer_overflow", epoch.Add(30*time.Minute))
	// 1 gap in previous hour
	w.Add("internal_merge_error", epoch.Add(-1*hour))
	assert.Equal(t, 4, w.Count(epoch))
}

func TestWindow_Populate(t *testing.T) {
	w := gapwindow.New()
	records := []gapwindow.GapRecord{
		{Cause: "internal_merge_error", At: epoch.Add(-1 * hour)},      // included
		{Cause: "internal_buffer_overflow", At: epoch.Add(-2 * hour)},  // included
		{Cause: "external_disconnect", At: epoch.Add(-3 * hour)},       // excluded (external)
		{Cause: "internal_merge_error", At: epoch.Add(-25 * hour)},     // excluded (too old)
	}
	w.Populate(records)
	require.Equal(t, 2, w.Count(epoch))
}

func TestWindow_BucketEviction(t *testing.T) {
	w := gapwindow.New()
	// Add gap in hour slot H
	early := epoch
	w.Add("internal_merge_error", early)
	assert.Equal(t, 1, w.Count(early.Add(1*time.Minute)))

	// Advance 24 hours: same slot H now holds hour H+24
	later := early.Add(24 * hour)
	w.Add("internal_merge_error", later) // evicts the old bucket in the same slot
	// Now count from "later" — old gap is >24h ago, should not appear
	assert.Equal(t, 1, w.Count(later), "old evicted bucket must not be counted")
}
