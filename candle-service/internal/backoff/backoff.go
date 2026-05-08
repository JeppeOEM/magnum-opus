// Package backoff provides exponential backoff with jitter.
// Pure: no IO, no goroutines, no time.Now().
package backoff

import (
	"math"
	"math/rand"
	"time"
)

// Backoff computes exponential backoff delays with ±jitter.
type Backoff struct {
	initial    time.Duration
	max        time.Duration
	multiplier float64
	jitter     float64 // fraction, e.g. 0.2 = ±20%
}

// New creates a Backoff.
// initial: delay for attempt 0.
// max: cap on computed delay (before jitter).
// multiplier: growth factor per attempt (e.g. 2.0).
// jitter: fraction of delay to add/subtract randomly (e.g. 0.2 for ±20%).
func New(initial, max time.Duration, multiplier, jitter float64) *Backoff {
	return &Backoff{
		initial:    initial,
		max:        max,
		multiplier: multiplier,
		jitter:     jitter,
	}
}

// Next returns the delay for the given attempt number (0-based).
// attempt=0 → initial delay; subsequent attempts grow exponentially, capped at max.
// Jitter is applied after capping: delay ± (delay * jitter * random[0,1]).
func (b *Backoff) Next(attempt int) time.Duration {
	base := float64(b.initial) * math.Pow(b.multiplier, float64(attempt))
	cap := float64(b.max)
	if base > cap {
		base = cap
	}
	// jitter: ± jitter fraction, uniformly distributed
	jitterRange := base * b.jitter
	jitter := (rand.Float64()*2 - 1) * jitterRange // [-jitterRange, +jitterRange]
	d := time.Duration(base + jitter)
	if d < 0 {
		d = 0
	}
	return d
}
