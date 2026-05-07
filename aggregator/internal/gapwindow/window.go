// Package gapwindow implements a rolling 24-hour gap counter using 24×1-hour buckets.
// Only internal_* gap causes are counted (external disconnects are normal operation).
package gapwindow

import (
	"sync"
	"time"

	"github.com/mrqdt/magnum-opus/aggregator/internal/gapdetector"
)

type bucket struct {
	hour  int64 // floor(unix_seconds / 3600)
	count int
}

// Window is a goroutine-safe rolling 24-hour gap counter.
type Window struct {
	mu      sync.Mutex
	buckets [24]bucket
}

// GapRecord represents a single gap event for bulk pre-population.
type GapRecord struct {
	Cause string
	At    time.Time
}

// New returns an empty Window.
func New() *Window { return &Window{} }

// Add records a gap event. Only internal_* causes increment the counter; external causes are ignored.
// If the slot already holds a more recent hour (from a newer event), the older event is skipped so
// that the most-recent-wins invariant is maintained regardless of insertion order.
func (w *Window) Add(cause string, at time.Time) {
	if !gapdetector.Cause(cause).Internal() {
		return
	}
	h := at.Unix() / 3600
	slot := int(h%24+24) % 24 // handle potential negative modulo on old 32-bit platforms
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.buckets[slot].hour > h {
		// Slot holds a more recent hour — skip older event to avoid evicting live data.
		return
	}
	if w.buckets[slot].hour != h {
		w.buckets[slot] = bucket{hour: h, count: 0}
	}
	w.buckets[slot].count++
}

// Count returns the number of internal_* gaps recorded in the 24-hour window ending at now.
// The window covers hours [floor(now/3600)-23, floor(now/3600)] inclusive (24 hourly buckets).
func (w *Window) Count(now time.Time) int {
	cutoff := now.Unix()/3600 - 23
	w.mu.Lock()
	defer w.mu.Unlock()
	total := 0
	for _, b := range w.buckets {
		if b.count > 0 && b.hour >= cutoff {
			total += b.count
		}
	}
	return total
}

// Populate pre-populates the window from a slice of pre-fetched records (e.g. from Redis on startup).
// Calls Add for each record; thread-safe.
func (w *Window) Populate(records []GapRecord) {
	for _, r := range records {
		w.Add(r.Cause, r.At)
	}
}
