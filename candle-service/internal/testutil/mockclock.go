// Package testutil provides test helpers shared across packages.
package testutil

import (
	"sync"
	"time"
)

// MockClock is a deterministic clock for L1 tests.
// It satisfies accumulator.Clock and cascade.Clock via structural typing.
type MockClock struct {
	mu  sync.Mutex
	now time.Time
}

// NewMockClock returns a MockClock set to t.
func NewMockClock(t time.Time) *MockClock {
	return &MockClock{now: t}
}

// Set advances (or rewinds) the clock to t.
func (c *MockClock) Set(t time.Time) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.now = t
}

// Now returns the current mock time.
func (c *MockClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.now
}
