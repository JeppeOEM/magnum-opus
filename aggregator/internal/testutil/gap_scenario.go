package testutil

import (
	"time"

	"github.com/mrqdt/magnum-opus/aggregator/internal/gapdetector"
)

// GapScenario describes a test case for gap detection.
type GapScenario struct {
	Name      string
	Prev      uint64
	Next      uint64
	Cause     gapdetector.Cause
	ClockTime time.Time
	// WantGap is true if a GapEvent is expected.
	WantGap bool
}

// GapScenarioBuilder builds sequences of GapScenario for table-driven tests.
type GapScenarioBuilder struct {
	scenarios []GapScenario
	clock     time.Time
}

// NewGapScenarioBuilder returns a builder with the given base clock time.
func NewGapScenarioBuilder(baseTime time.Time) *GapScenarioBuilder {
	return &GapScenarioBuilder{clock: baseTime}
}

// AddNoGap adds a scenario where no gap is expected (contiguous sequence).
func (b *GapScenarioBuilder) AddNoGap(name string, prev, next uint64, cause gapdetector.Cause) *GapScenarioBuilder {
	b.scenarios = append(b.scenarios, GapScenario{
		Name: name, Prev: prev, Next: next, Cause: cause,
		ClockTime: b.clock, WantGap: false,
	})
	return b
}

// AddGap adds a scenario where a gap is expected.
func (b *GapScenarioBuilder) AddGap(name string, prev, next uint64, cause gapdetector.Cause) *GapScenarioBuilder {
	b.scenarios = append(b.scenarios, GapScenario{
		Name: name, Prev: prev, Next: next, Cause: cause,
		ClockTime: b.clock, WantGap: true,
	})
	return b
}

// AdvanceClock advances the builder's internal clock for subsequent scenarios.
func (b *GapScenarioBuilder) AdvanceClock(d time.Duration) *GapScenarioBuilder {
	b.clock = b.clock.Add(d)
	return b
}

// Build returns the collected scenarios.
func (b *GapScenarioBuilder) Build() []GapScenario { return b.scenarios }
