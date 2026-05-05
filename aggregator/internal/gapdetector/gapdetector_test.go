package gapdetector_test

import (
	"testing"
	"time"

	"github.com/mrqdt/magnum-opus/aggregator/internal/gapdetector"
	"github.com/mrqdt/magnum-opus/aggregator/internal/testutil"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// mockClock is a test-local Clock that returns a fixed time.
type mockClock struct{ t time.Time }

func (m *mockClock) Now() time.Time { return m.t }

func fixedClock(t time.Time) gapdetector.Clock { return &mockClock{t: t} }

var epoch = time.Date(2026, 5, 5, 12, 0, 0, 0, time.UTC)

// TestNoGap_Contiguous verifies that contiguous sequence numbers produce nil.
func TestNoGap_Contiguous(t *testing.T) {
	result := gapdetector.Detect(100, 101, gapdetector.CauseExternalDisconnect, fixedClock(epoch))
	assert.Nil(t, result, "contiguous sequence must produce no gap")
}

// TestNoGap_FirstMessage: prev=0 → next=1 is contiguous (cold start).
func TestNoGap_FirstMessage(t *testing.T) {
	result := gapdetector.Detect(0, 1, gapdetector.CauseExternalDisconnect, fixedClock(epoch))
	assert.Nil(t, result)
}

// TestGap_ExternalDisconnect verifies the disconnect cause path.
func TestGap_ExternalDisconnect(t *testing.T) {
	ts := epoch
	result := gapdetector.Detect(100, 105, gapdetector.CauseExternalDisconnect, fixedClock(ts))
	require.NotNil(t, result)
	assert.Equal(t, gapdetector.CauseExternalDisconnect, result.Cause)
	assert.Equal(t, uint64(100), result.SeqBefore)
	assert.Equal(t, uint64(105), result.SeqAfter)
	assert.Equal(t, ts, result.Timestamp)
	assert.False(t, result.Cause.Internal())
}

// TestGap_ExternalRateLimit verifies the rate-limit cause path.
func TestGap_ExternalRateLimit(t *testing.T) {
	result := gapdetector.Detect(200, 250, gapdetector.CauseExternalRateLimit, fixedClock(epoch))
	require.NotNil(t, result)
	assert.Equal(t, gapdetector.CauseExternalRateLimit, result.Cause)
	assert.False(t, result.Cause.Internal())
}

// TestGap_InternalBufferOverflow verifies the buffer overflow cause path.
func TestGap_InternalBufferOverflow(t *testing.T) {
	result := gapdetector.Detect(300, 350, gapdetector.CauseInternalBufferOverflow, fixedClock(epoch))
	require.NotNil(t, result)
	assert.Equal(t, gapdetector.CauseInternalBufferOverflow, result.Cause)
	assert.True(t, result.Cause.Internal(), "internal_buffer_overflow must be internal")
}

// TestGap_InternalMergeError verifies the merge error cause path.
func TestGap_InternalMergeError(t *testing.T) {
	result := gapdetector.Detect(400, 410, gapdetector.CauseInternalMergeError, fixedClock(epoch))
	require.NotNil(t, result)
	assert.Equal(t, gapdetector.CauseInternalMergeError, result.Cause)
	assert.True(t, result.Cause.Internal(), "internal_merge_error must be internal")
}

// TestAllFourCauses verifies each cause produces a GapEvent — no fifth cause exists.
func TestAllFourCauses(t *testing.T) {
	causes := []struct {
		cause    gapdetector.Cause
		internal bool
	}{
		{gapdetector.CauseInternalBufferOverflow, true},
		{gapdetector.CauseInternalMergeError, true},
		{gapdetector.CauseExternalDisconnect, false},
		{gapdetector.CauseExternalRateLimit, false},
	}

	for _, tc := range causes {
		t.Run(string(tc.cause), func(t *testing.T) {
			result := gapdetector.Detect(1, 3, tc.cause, fixedClock(epoch))
			require.NotNil(t, result)
			assert.Equal(t, tc.cause, result.Cause)
			assert.Equal(t, tc.internal, result.Cause.Internal())
		})
	}
}

// TestClock_Injected verifies the timestamp comes from the injected clock, not time.Now().
func TestClock_Injected(t *testing.T) {
	specificTime := time.Date(2030, 1, 15, 8, 30, 0, 0, time.UTC)
	result := gapdetector.Detect(10, 20, gapdetector.CauseExternalDisconnect, fixedClock(specificTime))
	require.NotNil(t, result)
	assert.Equal(t, specificTime, result.Timestamp)
}

// TestGap_AdjacentSkip: prev+2 (one missing message).
func TestGap_AdjacentSkip(t *testing.T) {
	result := gapdetector.Detect(99, 101, gapdetector.CauseExternalDisconnect, fixedClock(epoch))
	require.NotNil(t, result)
	assert.Equal(t, uint64(99), result.SeqBefore)
	assert.Equal(t, uint64(101), result.SeqAfter)
}

// TestCause_Internal_ExternalValues verifies the exhaustive mapping.
func TestCause_Internal_ExternalValues(t *testing.T) {
	assert.True(t, gapdetector.CauseInternalBufferOverflow.Internal())
	assert.True(t, gapdetector.CauseInternalMergeError.Internal())
	assert.False(t, gapdetector.CauseExternalDisconnect.Internal())
	assert.False(t, gapdetector.CauseExternalRateLimit.Internal())
}

// TestAllFourCauses_ViaBuilder exercises all four causes through GapScenarioBuilder,
// satisfying the AC requirement that each cause has a corresponding L1 test using the builder.
func TestAllFourCauses_ViaBuilder(t *testing.T) {
	scenarios := testutil.NewGapScenarioBuilder(epoch).
		AddGap("internal_buffer_overflow", 1, 10, gapdetector.CauseInternalBufferOverflow).
		AddGap("internal_merge_error", 20, 30, gapdetector.CauseInternalMergeError).
		AddGap("external_disconnect", 40, 50, gapdetector.CauseExternalDisconnect).
		AddGap("external_rate_limit", 60, 70, gapdetector.CauseExternalRateLimit).
		AddNoGap("contiguous_no_gap", 80, 81, gapdetector.CauseExternalDisconnect).
		Build()

	for _, sc := range scenarios {
		t.Run(sc.Name, func(t *testing.T) {
			result := gapdetector.Detect(sc.Prev, sc.Next, sc.Cause, fixedClock(sc.ClockTime))
			if sc.WantGap {
				require.NotNil(t, result)
				assert.Equal(t, sc.Cause, result.Cause)
				assert.Equal(t, sc.Prev, result.SeqBefore)
				assert.Equal(t, sc.Next, result.SeqAfter)
				assert.Equal(t, sc.ClockTime, result.Timestamp)
			} else {
				assert.Nil(t, result)
			}
		})
	}
}

// TestDetect_NilClock_Panics verifies the nil clock guard.
func TestDetect_NilClock_Panics(t *testing.T) {
	assert.Panics(t, func() {
		gapdetector.Detect(1, 5, gapdetector.CauseExternalDisconnect, nil)
	})
}
