package gapdetector

// Detect checks whether there is a sequence gap between prev and next.
//
// Returns nil if next == prev+1 (contiguous, no gap).
// Returns a GapEvent with the given cause if next != prev+1.
//
// The cause must be determined by the caller based on context:
//   - Use CauseExternalDisconnect when transport fires a reconnect event
//   - Use CauseExternalRateLimit when exchange sends a rate-limit signal
//   - Use CauseInternalBufferOverflow when the delta buffer is full
//   - Use CauseInternalMergeError when a merge state machine error occurs
func Detect(prev, next uint64, cause Cause, clock Clock) *GapEvent {
	if clock == nil {
		panic("gapdetector: Detect called with nil clock")
	}
	if next == prev+1 {
		return nil
	}
	return &GapEvent{
		Cause:     cause,
		SeqBefore: prev,
		SeqAfter:  next,
		Timestamp: clock.Now(),
	}
}
