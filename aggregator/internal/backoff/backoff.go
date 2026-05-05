// Package backoff implements exponential backoff as a pure function.
// No retry loops — the caller calls Wait(attempt, clock) and sleeps the returned duration.
// time.Now() and time.Sleep() are never called here.
package backoff

import (
	"math"
	"time"
)

// Clock abstracts time for deterministic testing.
type Clock interface {
	Now() time.Time
}

const (
	initialInterval = 1 * time.Second
	multiplier      = 2.0
	maxInterval     = 60 * time.Second
	jitterFraction  = 0.2 // ±20% jitter
)

// Duration returns the backoff duration for the given attempt number (0-based).
// Formula: min(initial * multiplier^attempt, max) ± 20% jitter.
//
// Jitter is seeded from clock.Now().UnixNano() to avoid thundering herd.
// The returned duration is always ≥ 0.
func Duration(attempt int, clock Clock) time.Duration {
	if clock == nil {
		panic("backoff: Duration called with nil clock")
	}
	if attempt < 0 {
		attempt = 0
	}
	base := float64(initialInterval) * math.Pow(multiplier, float64(attempt))
	if base > float64(maxInterval) {
		base = float64(maxInterval)
	}

	// Deterministic jitter seeded from nanosecond clock.
	// Range: [base*(1-jitter), base*(1+jitter)]
	seed := clock.Now().UnixNano()
	// Pseudo-random in [0,1) derived from seed — LCG single step.
	r := float64((uint64(seed)*6364136223846793005+1442695040888963407)>>11) / float64(1<<53)
	jitter := base * jitterFraction * (2*r - 1)

	d := time.Duration(base + jitter)
	if d < 0 {
		return 0
	}
	return d
}
