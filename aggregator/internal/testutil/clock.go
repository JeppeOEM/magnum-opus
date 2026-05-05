// Package testutil provides pure L1-safe test helpers shared across packages.
// No build tags — usable in any test without the l2 tag.
// Must not import github.com/redis/go-redis or github.com/questdb.
package testutil

import (
	"sync"
	"time"
)

// ClockAdvancer wraps a MockClock and advances it by a fixed step on each Tick call.
// Useful for driving time-dependent logic in table-driven tests without manual Advance calls.
type ClockAdvancer struct {
	Clock *MockClock
	Step  time.Duration
}

// NewClockAdvancer returns a ClockAdvancer that advances clock by step on each Tick.
func NewClockAdvancer(clock *MockClock, step time.Duration) *ClockAdvancer {
	return &ClockAdvancer{Clock: clock, Step: step}
}

// Tick advances the clock by one step and returns the new time.
func (a *ClockAdvancer) Tick() time.Time {
	a.Clock.Advance(a.Step)
	return a.Clock.Now()
}

// MockClock is a thread-safe, manually-advanceable clock for L1 tests.
type MockClock struct {
	mu  sync.Mutex
	now time.Time
}

// NewMockClock returns a MockClock set to the given initial time.
func NewMockClock(initial time.Time) *MockClock {
	return &MockClock{now: initial}
}

// Now returns the current mock time.
func (c *MockClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.now
}

// Advance moves the clock forward by d.
func (c *MockClock) Advance(d time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.now = c.now.Add(d)
}

// Set sets the clock to an absolute time.
func (c *MockClock) Set(t time.Time) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.now = t
}
