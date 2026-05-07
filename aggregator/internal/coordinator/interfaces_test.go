// Package coordinator_test provides compile-time and L1 structural checks for the
// coordinator package. These tests verify the interface contract that Stories 3.2
// and 3.3 implement against.
package coordinator_test

import (
	"testing"

	"github.com/mrqdt/magnum-opus/aggregator/internal/coordinator"
	"github.com/mrqdt/magnum-opus/aggregator/internal/gapdetector"
)

// TestInterfacesExist verifies that StreamWriter and ILPWriter are exported types
// following the noun/noun+er naming convention (no I-prefix).
// AC1: "interface names follow noun/noun+er pattern"
func TestInterfacesExist(t *testing.T) {
	// Compile-time assertion: if either interface is missing or renamed, build fails.
	var _ coordinator.StreamWriter
	var _ coordinator.ILPWriter
}

// TestGapCauseValuesMatchSchema verifies that the four gapdetector.Cause string
// values exactly match the gap_cause values documented in the Redis Stream schema.
// These are downstream contracts — changing any value breaks the Candle Service.
// AC2: "gap_cause accepts exactly four values"
func TestGapCauseValuesMatchSchema(t *testing.T) {
	cases := []struct {
		cause gapdetector.Cause
		want  string
	}{
		{gapdetector.CauseInternalBufferOverflow, "internal_buffer_overflow"},
		{gapdetector.CauseInternalMergeError, "internal_merge_error"},
		{gapdetector.CauseExternalDisconnect, "external_disconnect"},
		{gapdetector.CauseExternalRateLimit, "external_rate_limit"},
	}
	for _, c := range cases {
		t.Run(c.want, func(t *testing.T) {
			if got := string(c.cause); got != c.want {
				t.Errorf("Cause string = %q, want %q", got, c.want)
			}
		})
	}
}

// TestGapCauseInternalClassification verifies the Internal() helper correctly
// distinguishes service-bug causes from exchange-behaviour causes.
// This guards the coordinator's log-level policy (ERROR for internal, WARN for external).
func TestGapCauseInternalClassification(t *testing.T) {
	cases := []struct {
		cause    gapdetector.Cause
		internal bool
	}{
		{gapdetector.CauseInternalBufferOverflow, true},
		{gapdetector.CauseInternalMergeError, true},
		{gapdetector.CauseExternalDisconnect, false},
		{gapdetector.CauseExternalRateLimit, false},
	}
	for _, c := range cases {
		t.Run(string(c.cause), func(t *testing.T) {
			if got := c.cause.Internal(); got != c.internal {
				t.Errorf("Cause(%q).Internal() = %v, want %v", c.cause, got, c.internal)
			}
		})
	}
}
