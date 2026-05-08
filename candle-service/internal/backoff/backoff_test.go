package backoff_test

import (
	"testing"
	"time"

	"github.com/mrqdt/magnum-opus/candle-service/internal/backoff"
	"github.com/stretchr/testify/assert"
)

func TestBackoff_Attempt0_ReturnsInitialPlusJitter(t *testing.T) {
	b := backoff.New(time.Second, 30*time.Second, 2.0, 0.2)
	// Run many times to account for randomness; all should be within ±20% of 1s.
	for range 100 {
		d := b.Next(0)
		assert.GreaterOrEqual(t, d, 800*time.Millisecond, "attempt 0 must be >= 0.8s")
		assert.LessOrEqual(t, d, 1200*time.Millisecond, "attempt 0 must be <= 1.2s")
	}
}

func TestBackoff_MidRange_GrowsExponentially(t *testing.T) {
	b := backoff.New(time.Second, 30*time.Second, 2.0, 0.0) // no jitter for determinism
	assert.Equal(t, time.Second, b.Next(0))
	assert.Equal(t, 2*time.Second, b.Next(1))
	assert.Equal(t, 4*time.Second, b.Next(2))
	assert.Equal(t, 8*time.Second, b.Next(3))
}

func TestBackoff_Cap_ClampedAtMax(t *testing.T) {
	b := backoff.New(time.Second, 30*time.Second, 2.0, 0.0) // no jitter
	// attempt 5: 1*2^5 = 32s > 30s cap
	assert.Equal(t, 30*time.Second, b.Next(5))
	assert.Equal(t, 30*time.Second, b.Next(100))
}

func TestBackoff_Jitter_WithinBounds(t *testing.T) {
	b := backoff.New(time.Second, 30*time.Second, 2.0, 0.2)
	for range 200 {
		d := b.Next(2) // base = 4s, ±20% = [3.2s, 4.8s]
		assert.GreaterOrEqual(t, d, 3200*time.Millisecond)
		assert.LessOrEqual(t, d, 4800*time.Millisecond)
	}
}

func TestBackoff_ZeroJitter_Deterministic(t *testing.T) {
	b := backoff.New(time.Second, 30*time.Second, 2.0, 0.0)
	assert.Equal(t, b.Next(3), b.Next(3))
}
