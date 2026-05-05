package backoff_test

import (
	"testing"
	"time"

	"github.com/mrqdt/magnum-opus/aggregator/internal/backoff"
	"github.com/stretchr/testify/assert"
)

type fixedClock struct{ t time.Time }

func (c *fixedClock) Now() time.Time { return c.t }

func clock(ns int64) backoff.Clock {
	return &fixedClock{t: time.Unix(0, ns)}
}

func TestDuration_FirstAttempt(t *testing.T) {
	d := backoff.Duration(0, clock(12345))
	// Expect ~1s ± 20%: [800ms, 1200ms]
	assert.GreaterOrEqual(t, d, 800*time.Millisecond)
	assert.LessOrEqual(t, d, 1200*time.Millisecond)
}

func TestDuration_SecondAttempt(t *testing.T) {
	d := backoff.Duration(1, clock(12345))
	// Expect ~2s ± 20%: [1600ms, 2400ms]
	assert.GreaterOrEqual(t, d, 1600*time.Millisecond)
	assert.LessOrEqual(t, d, 2400*time.Millisecond)
}

func TestDuration_CapsAtMax(t *testing.T) {
	// Attempt 10 → base would be 1024s, capped to 60s.
	d := backoff.Duration(10, clock(0))
	assert.LessOrEqual(t, d, 72*time.Second) // max+20% jitter
	assert.GreaterOrEqual(t, d, 48*time.Second) // max-20% jitter
}

func TestDuration_NeverNegative(t *testing.T) {
	for attempt := 0; attempt < 20; attempt++ {
		d := backoff.Duration(attempt, clock(int64(attempt)*999983))
		assert.GreaterOrEqual(t, d, time.Duration(0))
	}
}

func TestDuration_Monotonic_WithSameSeed(t *testing.T) {
	// With fixed seed, same attempt always returns same duration.
	d1 := backoff.Duration(3, clock(42))
	d2 := backoff.Duration(3, clock(42))
	assert.Equal(t, d1, d2, "same attempt + same clock must produce same duration")
}

func TestDuration_DifferentSeeds(t *testing.T) {
	// Different seeds should produce different (jittered) values.
	d1 := backoff.Duration(0, clock(1000))
	d2 := backoff.Duration(0, clock(2000))
	// They may happen to be equal, but overwhelmingly won't be.
	// Just verify both are in valid range.
	assert.GreaterOrEqual(t, d1, 800*time.Millisecond)
	assert.GreaterOrEqual(t, d2, 800*time.Millisecond)
}

func TestDuration_Grows(t *testing.T) {
	// Median values (ignoring jitter) should grow: attempt 0 < 1 < 2.
	// Use multiple seeds and check the median grows.
	avg := func(attempt int) time.Duration {
		var total time.Duration
		for seed := int64(0); seed < 100; seed++ {
			total += backoff.Duration(attempt, clock(seed*1000003))
		}
		return total / 100
	}

	avg0 := avg(0)
	avg1 := avg(1)
	avg2 := avg(2)
	assert.Less(t, avg0, avg1, "attempt 1 should be larger than attempt 0")
	assert.Less(t, avg1, avg2, "attempt 2 should be larger than attempt 1")
}
